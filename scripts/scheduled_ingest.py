#!/usr/bin/env python3
"""Guarded scheduled ingest+embed tick for Concept Loom.

Runs `loom ingest` and, if the inserted-row count is within the sanity
threshold, `loom embed --limit N`. A spike above the threshold trips a
circuit breaker: the embed stage is skipped and the script exits 2 so the
calling cron agent can alert a human instead of proceeding. Evidence is
append-only and deduplicated, so a tripped breaker leaves already-inserted
rows in place — the breaker exists to catch structural anomalies (glob or
cursor changes, new agent directories) before burning embed compute, not to
roll back the ingest.

Cluster/name/rank are deliberately NOT run here: clustering is a full-corpus
rebuild that wipes naming state, so re-clustering belongs in a supervised run,
not on a timer.

Exit codes: 0 ok or skipped (lock held), 1 stage error, 2 breaker tripped.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
from pathlib import Path
import subprocess
import sys

LOOM_DIR = Path(__file__).resolve().parent.parent


def run_stage(stage: str, args: list[str]) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "loom.cli", *args],
        cwd=LOOM_DIR,
        capture_output=True,
        text=True,
        timeout=4 * 3600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"loom {stage} exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"no JSON stats in {stage} output: {proc.stdout.strip()[-500:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(LOOM_DIR / "loom.toml"))
    parser.add_argument("--max-inserted", type=int, default=5000,
                        help="breaker threshold; normal ticks insert hundreds")
    parser.add_argument("--embed-limit", type=int, default=5000,
                        help="max rows embedded per tick; backlog drains across ticks")
    parser.add_argument("--log", default=str(LOOM_DIR / "data" / "scheduled-ingest.log"))
    args = parser.parse_args()

    entry: dict = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    lock_path = log_path.with_suffix(".lock")
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("RESULT: SKIPPED-LOCKED (previous tick still running)")
        return 0

    def finish(result: str, code: int) -> int:
        entry["result"] = result
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        print(f"RESULT: {result}")
        print(json.dumps(entry, sort_keys=True))
        return code

    try:
        ingest_stats = run_stage("ingest", ["--config", args.config, "ingest"])
    except Exception as exc:
        entry["error"] = str(exc)
        return finish("ERROR-INGEST", 1)
    entry["ingest"] = ingest_stats

    inserted = int(ingest_stats.get("inserted", 0))
    if inserted > args.max_inserted:
        entry["breaker"] = {"inserted": inserted, "max_inserted": args.max_inserted}
        return finish("BREAKER-TRIPPED", 2)

    try:
        embed_stats = run_stage(
            "embed", ["--config", args.config, "embed", "--limit", str(args.embed_limit)]
        )
    except Exception as exc:
        entry["error"] = str(exc)
        return finish("ERROR-EMBED", 1)
    entry["embed"] = embed_stats

    return finish("OK", 0)


if __name__ == "__main__":
    sys.exit(main())
