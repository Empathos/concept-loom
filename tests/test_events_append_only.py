from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from loom.adapters.jsonl_transcripts import JsonlTranscriptsAdapter, JsonlTranscriptsConfig
from loom.store import LoomStore

FIXTURE = Path(__file__).parent / "fixtures" / "sessions"


class AppendOnlyStoreTest(unittest.TestCase):
    def test_evidence_rejects_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LoomStore(Path(tmp) / "loom.db")
            store.init()
            adapter = JsonlTranscriptsAdapter(
                JsonlTranscriptsConfig(name="chats", glob=str(FIXTURE / "sample.jsonl"))
            )
            run_id = store.start_run(adapter.name)
            store.insert_evidence(run_id, [next(adapter.scan())])
            with store.connect() as conn:
                with self.assertRaisesRegex(Exception, "append-only"):
                    conn.execute("UPDATE evidence SET text = 'changed' WHERE evidence_id = 1")
                with self.assertRaisesRegex(Exception, "append-only"):
                    conn.execute("DELETE FROM evidence WHERE evidence_id = 1")

    def test_dead_update_fts_trigger_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LoomStore(Path(tmp) / "loom.db")
            store.init()
            with store.connect() as conn:
                trigger_names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    )
                }
            self.assertNotIn("evidence_au", trigger_names)


if __name__ == "__main__":
    unittest.main()
