from __future__ import annotations

from pathlib import Path
import unittest

from loom.adapters.jsonl_transcripts import JsonlTranscriptsAdapter, JsonlTranscriptsConfig
from loom.adapters.markdown_folder import (
    MarkdownFolderAdapter,
    MarkdownFolderConfig,
    chunk_spans,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _jsonl_adapter(**overrides) -> JsonlTranscriptsAdapter:
    config = JsonlTranscriptsConfig(
        name="chats",
        glob=str(FIXTURES / "sessions" / "*.jsonl"),
        skip_roles=("system",),
        **overrides,
    )
    return JsonlTranscriptsAdapter(config)


class JsonlTranscriptsAdapterTest(unittest.TestCase):
    def test_scans_messages_and_skips_roles_and_non_messages(self) -> None:
        adapter = _jsonl_adapter()
        records = list(adapter.scan())
        stats = adapter.stats()
        # sample.jsonl: 1 session envelope + 3 messages (one system-role);
        # sample.trajectory.jsonl: 2 lines with no message.content at all.
        self.assertEqual(stats["files_seen"], 2)
        self.assertEqual(stats["role_skipped"], 1)
        self.assertEqual(stats["no_text_skipped"], 3)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].text, "Concept Loom should preserve provenance.")
        self.assertEqual(records[0].provenance_pointer.granularity, "exact_span")
        self.assertEqual(records[0].provenance_pointer.message_id, "msg-1")
        self.assertEqual(records[1].metadata["content_shape"], "list")
        self.assertFalse(records[1].metadata["has_structured_content"])

    def test_read_span_round_trips(self) -> None:
        adapter = _jsonl_adapter()
        for record in adapter.scan():
            self.assertEqual(adapter.read_span(record.provenance_pointer), record.text)

    def test_synthetic_line_ids_when_id_path_missing(self) -> None:
        adapter = _jsonl_adapter(id_path="nonexistent")
        records = list(adapter.scan())
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r.provenance_pointer.message_id.startswith("line-") for r in records))
        for record in records:
            self.assertEqual(adapter.read_span(record.provenance_pointer), record.text)
            # No stable raw-file anchor for synthetic ids: lexical check opts out.
            self.assertIsNone(adapter.lexical_probe(record.provenance_pointer, record.text))

    def test_unchanged_files_are_skipped_on_second_scan(self) -> None:
        adapter = _jsonl_adapter()
        list(adapter.scan())
        cursor = adapter.next_cursor()
        second = list(adapter.scan(cursor))
        self.assertEqual(second, [])
        self.assertEqual(adapter.stats()["files_skipped_unchanged"], 2)


class ChunkSpansTest(unittest.TestCase):
    def test_spans_are_exact_slices_and_respect_max_chars(self) -> None:
        text = "para one\n\npara two is a bit longer\n\n\npara three"
        spans = chunk_spans(text, max_chars=20)
        self.assertEqual([text[s:e] for s, e in spans],
                         ["para one", "para two is a bit longer", "para three"])
        packed = chunk_spans(text, max_chars=len(text))
        self.assertEqual(len(packed), 1)
        start, end = packed[0]
        self.assertEqual(text[start:end], text)

    def test_blank_only_spans_are_dropped(self) -> None:
        self.assertEqual(chunk_spans("\n\n   \n\n", max_chars=100), [])


class MarkdownFolderAdapterTest(unittest.TestCase):
    def _adapter(self) -> MarkdownFolderAdapter:
        config = MarkdownFolderConfig(
            name="notes",
            root=FIXTURES / "notes",
            include=("**/*.md",),
            max_chunk_chars=200,
            min_text_chars=10,
        )
        return MarkdownFolderAdapter(config)

    def test_chunks_carry_exact_char_spans(self) -> None:
        adapter = self._adapter()
        records = list(adapter.scan())
        self.assertGreaterEqual(len(records), 2)
        raw = (FIXTURES / "notes" / "garden.md").read_text(encoding="utf-8")
        for record in records:
            span = record.provenance_pointer.span
            self.assertEqual(record.text, raw[span["start"] : span["end"]])
            self.assertEqual(record.provenance_pointer.session_id, "garden.md")

    def test_read_span_round_trips(self) -> None:
        adapter = self._adapter()
        for record in adapter.scan():
            self.assertEqual(adapter.read_span(record.provenance_pointer), record.text)

    def test_lexical_probe_is_single_line(self) -> None:
        adapter = self._adapter()
        record = next(iter(adapter.scan()))
        probe = adapter.lexical_probe(record.provenance_pointer, record.text)
        self.assertIsNotNone(probe)
        self.assertNotIn("\n", probe)

    def test_unchanged_files_are_skipped_on_second_scan(self) -> None:
        adapter = self._adapter()
        list(adapter.scan())
        cursor = adapter.next_cursor()
        self.assertEqual(list(adapter.scan(cursor)), [])
        self.assertEqual(adapter.stats()["files_skipped_unchanged"], 1)


if __name__ == "__main__":
    unittest.main()
