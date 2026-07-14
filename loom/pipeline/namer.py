from __future__ import annotations

from hashlib import sha1
import json

from loom.config import LLMConfig
from loom.llm.client import LLMTransportError, call_json
from loom.llm.prompts import NAMER_PROMPT_V1
from loom.store import LoomStore


MAX_CONSECUTIVE_TRANSPORT_DEFERRALS = 3
MIN_CALLS_BEFORE_DEFERRED_RATIO_STOP = 5
MAX_TRANSPORT_DEFERRED_RATIO = 0.5


def name_pending_clusters(store: LoomStore, cfg: LLMConfig, *, limit: int | None = None) -> dict[str, int]:
    cap = cfg.max_llm_calls_per_run if limit is None else min(limit, cfg.max_llm_calls_per_run)
    clusters = store.pending_clusters(cap)
    run_id = store.start_run("name", {"limit": cap})
    processed = 0
    named = 0
    failed = 0
    deferred = 0
    incoherent = 0
    consecutive_transport_deferrals = 0
    stopped_early: str | None = None
    transport_errors: list[dict[str, str]] = []
    for cluster in clusters:
        processed += 1
        cluster_id = cluster["cluster_id"]
        sample = store.cluster_sample(cluster_id, 12)
        evidence = [
            {
                "index": idx,
                "evidence_id": row["evidence_id"],
                "source_system": row["source_system"],
                "timestamp": row["ts"],
                "text": row["text"][:700],
            }
            for idx, row in enumerate(sample)
        ]
        prompt = NAMER_PROMPT_V1 + "\nEvidence:\n" + json.dumps(evidence, ensure_ascii=False, indent=2)
        try:
            result = call_json(cfg, session_key=f"agent:loom:namer-{cluster_id}", prompt=prompt)
        except LLMTransportError as exc:
            failed += 1
            deferred += 1
            consecutive_transport_deferrals += 1
            transport_errors.append({"cluster_id": cluster_id, "error": str(exc)})
            store.mark_cluster(cluster_id, "naming_deferred")
            deferred_ratio = deferred / processed
            if (
                consecutive_transport_deferrals >= MAX_CONSECUTIVE_TRANSPORT_DEFERRALS
                or (
                    processed >= MIN_CALLS_BEFORE_DEFERRED_RATIO_STOP
                    and deferred_ratio > MAX_TRANSPORT_DEFERRED_RATIO
                )
            ):
                stopped_early = "transport_deferred_threshold"
                break
            continue
        except Exception:
            failed += 1
            consecutive_transport_deferrals = 0
            store.mark_cluster(cluster_id, "naming_failed")
            continue
        if not result.get("coherent", False):
            incoherent += 1
            consecutive_transport_deferrals = 0
            store.mark_cluster(cluster_id, "incoherent")
            continue
        title = str(result.get("title") or "").strip()
        summary = str(result.get("summary") or "").strip()
        concept_type = str(result.get("concept_type") or "theme").strip()
        if not title or not summary:
            failed += 1
            consecutive_transport_deferrals = 0
            store.mark_cluster(cluster_id, "naming_failed")
            continue
        consecutive_transport_deferrals = 0
        aliases = [str(alias) for alias in result.get("aliases", []) if str(alias).strip()]
        evidence_ids = [int(row["evidence_id"]) for row in sample]
        concept_id = "concept-" + sha1(f"{cluster_id}:{title}".encode("utf-8")).hexdigest()[:16]
        payload = {
            "cluster_id": cluster_id,
            "prompt_version": "NAMER_PROMPT_V1",
            "raw_model_reply": result,
            "sample_evidence_ids": evidence_ids,
        }
        store.create_concept_from_cluster(
            run_id=run_id,
            cluster_id=cluster_id,
            concept_id=concept_id,
            title=title,
            concept_type=concept_type,
            summary=summary,
            aliases=aliases,
            evidence_ids=evidence_ids,
            payload=payload,
        )
        named += 1
    stats = {"named": named, "failed": failed, "deferred": deferred, "incoherent": incoherent}
    if transport_errors:
        stats["transport_errors"] = transport_errors
    if stopped_early is not None:
        stats["stopped_early"] = stopped_early
    store.finish_run(
        run_id,
        cursor_after={"processed": processed},
        stats=stats,
    )
    result = {"processed": processed, "named": named, "failed": failed, "deferred": deferred, "incoherent": incoherent}
    if stopped_early is not None:
        result["stopped_early"] = stopped_early
    return result
