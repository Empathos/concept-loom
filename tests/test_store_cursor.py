from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from loom.cli import main as cli_main
from loom.store import LoomStore


FIXTURE = Path(__file__).parent / "fixtures" / "sessions"


class AdapterCursorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = LoomStore(self.root / "test.db")
        self.store.init()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_set_and_get_roundtrip(self) -> None:
        cursor = {"/some/file.jsonl": {"mtime": 1.5, "size": 10}}
        self.store.set_adapter_cursor("chats", cursor)
        self.assertEqual(self.store.latest_cursor("chats"), cursor)
        updated = {"/some/file.jsonl": {"mtime": 2.5, "size": 20}}
        self.store.set_adapter_cursor("chats", updated)
        self.assertEqual(self.store.latest_cursor("chats"), updated)

    def test_missing_cursor_is_empty(self) -> None:
        self.assertEqual(self.store.latest_cursor("chats"), {})

    def test_legacy_run_cursor_fallback(self) -> None:
        legacy = {"/old/file.jsonl": {"mtime": 1.0, "size": 5}}
        run_id = self.store.start_run("chats", legacy)
        self.store.finish_run(run_id, cursor_after=legacy, stats={})
        self.assertEqual(self.store.latest_cursor("chats"), legacy)

    def test_adapter_cursor_wins_over_legacy(self) -> None:
        legacy = {"/old/file.jsonl": {"mtime": 1.0, "size": 5}}
        run_id = self.store.start_run("chats", legacy)
        self.store.finish_run(run_id, cursor_after=legacy, stats={})
        current = {"/new/file.jsonl": {"mtime": 2.0, "size": 9}}
        self.store.set_adapter_cursor("chats", current)
        self.assertEqual(self.store.latest_cursor("chats"), current)

    def test_fallback_skips_summary_rows(self) -> None:
        legacy = {"/old/file.jsonl": {"mtime": 1.0, "size": 5}}
        run_id = self.store.start_run("chats", legacy)
        self.store.finish_run(run_id, cursor_after=legacy, stats={})
        run_id = self.store.start_run("chats", {"_summary": True, "files_tracked": 1})
        self.store.finish_run(
            run_id, cursor_after={"_summary": True, "files_tracked": 1}, stats={}
        )
        self.assertEqual(self.store.latest_cursor("chats"), legacy)


class IngestCursorCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config_path = self.root / "loom.toml"
        self.config_path.write_text(
            "\n".join(
                [
                    "[paths]",
                    'data_dir = "data"',
                    'db_path = "data/test.db"',
                    "",
                    "[[sources]]",
                    'name = "chats"',
                    'type = "jsonl_transcripts"',
                    f'glob = "{FIXTURE}/*.jsonl"',
                    'skip_roles = ["system"]',
                ]
            ),
            encoding="utf-8",
        )
        self.store = LoomStore(self.root / "data" / "test.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_stats(self, run_id: int) -> dict:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT cursor_before, cursor_after, stats_json FROM ingest_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return {
            "cursor_before": json.loads(row["cursor_before"]),
            "cursor_after": json.loads(row["cursor_after"]),
            "stats": json.loads(row["stats_json"]),
        }

    def test_ingest_persists_cursor_outside_run_rows(self) -> None:
        self.assertEqual(cli_main(["--config", str(self.config_path), "ingest"]), 0)
        first = self._run_stats(1)
        self.assertGreater(first["stats"]["inserted"], 0)
        self.assertTrue(first["cursor_before"]["_summary"])
        self.assertTrue(first["cursor_after"]["_summary"])

        cursor = self.store.latest_cursor("chats")
        self.assertEqual(len(cursor), first["cursor_after"]["files_tracked"])
        for state in cursor.values():
            self.assertIn("mtime", state)
            self.assertIn("size", state)

        # Unchanged corpus: the incremental run must skip every file.
        self.assertEqual(cli_main(["--config", str(self.config_path), "ingest"]), 0)
        second = self._run_stats(2)
        self.assertEqual(second["stats"]["inserted"], 0)
        self.assertEqual(second["stats"]["files_scanned"], 0)
        self.assertEqual(
            second["stats"]["files_skipped_unchanged"], first["stats"]["files_seen"]
        )

    def test_limited_ingest_does_not_advance_cursor(self) -> None:
        self.assertEqual(
            cli_main(["--config", str(self.config_path), "ingest", "--limit-files", "0"]), 0
        )
        self.assertEqual(self.store.latest_cursor("chats"), {})


if __name__ == "__main__":
    unittest.main()
