from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from loom.adapters import build_adapters
from loom.config import load_config
from loom.model import ProvenancePointer
from loom.pipeline.verifier import EvidenceVerifier
from loom.store import LoomStore


def _store(config_path: str) -> tuple[LoomStore, object]:
    cfg = load_config(config_path)
    return LoomStore(cfg.paths.db_path), cfg


def _selected_adapters(cfg, source: str | None):
    adapters = build_adapters(cfg)
    if not adapters:
        raise SystemExit("no [[sources]] configured; add at least one to the config")
    if source is None:
        return adapters
    if source not in adapters:
        raise SystemExit(f"unknown source {source!r}; configured: {sorted(adapters)}")
    return {source: adapters[source]}


def cmd_init(args: argparse.Namespace) -> int:
    store, cfg = _store(args.config)
    cfg.paths.data_dir.mkdir(parents=True, exist_ok=True)
    store.init()
    print(f"initialized {store.db_path}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    store, cfg = _store(args.config)
    store.init()

    runs = []
    total_inserted = 0
    total_duplicates = 0
    for name, adapter in _selected_adapters(cfg, args.source).items():
        cursor_before = {} if args.full else store.latest_cursor(name)
        # Run rows carry only a summary; the full per-file cursor lives in
        # adapter_cursor so frequent scheduled runs don't bloat ingest_run.
        run_id = store.start_run(
            name,
            {"_summary": True, "files_tracked": len(cursor_before), "full": bool(args.full)},
        )

        records = adapter.scan(cursor_before)
        if args.limit_files is not None:
            allowed = set(str(path) for path in adapter.source_paths()[: args.limit_files])
            records = (
                record for record in records
                if record.provenance_pointer.source_path in allowed
            )

        stats = store.insert_evidence(run_id, records)
        stats.update(adapter.stats())
        cursor_after = adapter.next_cursor()
        store.finish_run(
            run_id,
            cursor_after={"_summary": True, "files_tracked": len(cursor_after)},
            stats=stats,
        )
        # A limited ingest is a smoke-test/dev operation; do not mark the whole
        # corpus as scanned or future incremental runs would skip untouched files.
        if args.limit_files is None:
            store.set_adapter_cursor(name, cursor_after)
        total_inserted += stats.get("inserted", 0)
        total_duplicates += stats.get("duplicates", 0)
        runs.append({"run_id": run_id, "adapter": name, **stats})
    print(
        json.dumps(
            {"inserted": total_inserted, "duplicates": total_duplicates, "runs": runs},
            sort_keys=True,
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store, _cfg = _store(args.config)
    store.init()
    print(json.dumps(store.counts(), sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    store, cfg = _store(args.config)
    store.init()
    verifier = EvidenceVerifier(build_adapters(cfg))
    rows = store.sample_evidence(args.sample, random=not args.oldest)
    results = [
        verifier.verify_pointer(row["evidence_id"], ProvenancePointer.from_json(row["provenance_json"]))
        for row in rows
    ]
    failed = [result for result in results if not result.ok]
    print(
        json.dumps(
            {
                "checked": len(results),
                "failed": len(failed),
                "failures": [result.__dict__ for result in failed[:10]],
            },
            sort_keys=True,
        )
    )
    return 1 if failed else 0


def cmd_embed(args: argparse.Namespace) -> int:
    from loom.pipeline.embedder import embed_missing

    store, cfg = _store(args.config)
    store.init()
    result = embed_missing(store, cfg.embedding, limit=args.limit)
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_cluster(args: argparse.Namespace) -> int:
    from loom.pipeline.cluster import cluster_embeddings

    store, cfg = _store(args.config)
    store.init()
    result = cluster_embeddings(store, cfg.clustering, model=args.model or cfg.embedding.model)
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_name(args: argparse.Namespace) -> int:
    from loom.pipeline.namer import name_pending_clusters

    store, cfg = _store(args.config)
    store.init()
    result = name_pending_clusters(store, cfg.llm, limit=args.limit)
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    store, _cfg = _store(args.config)
    store.init()
    ranked = store.rank_concepts()
    print(json.dumps({"ranked": ranked}, sort_keys=True))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from loom.api.server import create_app

    cfg = load_config(args.config)
    uvicorn.run(create_app(args.config), host=cfg.server.host, port=cfg.server.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loom")
    parser.add_argument("--config", default=str(Path.cwd() / "loom.toml"))
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.set_defaults(func=cmd_init)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--source", help="ingest only this configured source (default: all)")
    ingest.add_argument("--full", action="store_true")
    ingest.add_argument("--limit-files", type=int)
    ingest.set_defaults(func=cmd_ingest)

    verify = sub.add_parser("verify")
    verify.add_argument("--sample", type=int, default=20)
    verify.add_argument("--oldest", action="store_true", help="verify oldest rows instead of random rows")
    verify.set_defaults(func=cmd_verify)

    embed = sub.add_parser("embed")
    embed.add_argument("--limit", type=int)
    embed.set_defaults(func=cmd_embed)

    cluster = sub.add_parser("cluster")
    cluster.add_argument("--model")
    cluster.set_defaults(func=cmd_cluster)

    name = sub.add_parser("name")
    name.add_argument("--limit", type=int)
    name.set_defaults(func=cmd_name)

    rank = sub.add_parser("rank")
    rank.set_defaults(func=cmd_rank)

    serve = sub.add_parser("serve")
    serve.set_defaults(func=cmd_serve)

    status = sub.add_parser("status")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
