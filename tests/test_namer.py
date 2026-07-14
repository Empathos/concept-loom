from __future__ import annotations

from loom.config import LLMConfig
from loom.llm.client import LLMTransportError
from loom.pipeline import namer


def _cfg() -> LLMConfig:
    return LLMConfig(timeout=300, max_llm_calls_per_run=200)


class FakeStore:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str]] = []
        self.finished: dict | None = None

    def pending_clusters(self, limit: int) -> list[dict]:
        return [{"cluster_id": f"cluster-{idx}", "size": 12} for idx in range(10)]

    def start_run(self, adapter: str, cursor_before: dict) -> int:
        return 42

    def cluster_sample(self, cluster_id: str, limit: int) -> list[dict]:
        return [
            {
                "evidence_id": 1001,
                "source_system": "test",
                "ts": "2026-07-08T00:00:00Z",
                "text": "sample evidence",
            }
        ]

    def mark_cluster(self, cluster_id: str, status: str) -> None:
        self.marked.append((cluster_id, status))

    def finish_run(self, run_id: int, *, cursor_after: dict, stats: dict, status: str = "completed") -> None:
        self.finished = {
            "run_id": run_id,
            "cursor_after": cursor_after,
            "stats": stats,
            "status": status,
        }


def test_name_pending_clusters_stops_after_repeated_transport_deferrals(monkeypatch):
    store = FakeStore()

    def fail_transport(*args, **kwargs):
        raise LLMTransportError("timeout")

    monkeypatch.setattr(namer, "call_json", fail_transport)

    result = namer.name_pending_clusters(store, _cfg())

    assert result == {
        "processed": 3,
        "named": 0,
        "failed": 3,
        "deferred": 3,
        "incoherent": 0,
        "stopped_early": "transport_deferred_threshold",
    }
    assert store.marked == [
        ("cluster-0", "naming_deferred"),
        ("cluster-1", "naming_deferred"),
        ("cluster-2", "naming_deferred"),
    ]
    assert store.finished == {
        "run_id": 42,
        "cursor_after": {"processed": 3},
        "stats": {
            "named": 0,
            "failed": 3,
            "deferred": 3,
            "incoherent": 0,
            "transport_errors": [
                {"cluster_id": "cluster-0", "error": "timeout"},
                {"cluster_id": "cluster-1", "error": "timeout"},
                {"cluster_id": "cluster-2", "error": "timeout"},
            ],
            "stopped_early": "transport_deferred_threshold",
        },
        "status": "completed",
    }
