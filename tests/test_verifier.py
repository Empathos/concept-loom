from __future__ import annotations

from pathlib import Path
import unittest

from loom.adapters.jsonl_transcripts import JsonlTranscriptsAdapter, JsonlTranscriptsConfig
from loom.adapters.markdown_folder import MarkdownFolderAdapter, MarkdownFolderConfig
from loom.pipeline.verifier import EvidenceVerifier

FIXTURES = Path(__file__).parent / "fixtures"


class EvidenceVerifierTest(unittest.TestCase):
    def _adapters(self) -> dict:
        chats = JsonlTranscriptsAdapter(
            JsonlTranscriptsConfig(
                name="chats",
                glob=str(FIXTURES / "sessions" / "*.jsonl"),
                skip_roles=("system",),
            )
        )
        notes = MarkdownFolderAdapter(
            MarkdownFolderConfig(
                name="notes",
                root=FIXTURES / "notes",
                include=("**/*.md",),
                max_chunk_chars=200,
                min_text_chars=10,
            )
        )
        return {chats.name: chats, notes.name: notes}

    def test_verifies_live_source_spans_for_all_adapters(self) -> None:
        adapters = self._adapters()
        verifier = EvidenceVerifier(adapters)
        checked = 0
        for adapter in adapters.values():
            for record in adapter.scan():
                result = verifier.verify_pointer(checked, record.provenance_pointer)
                self.assertTrue(result.ok, f"{adapter.name}: {result.reason}")
                checked += 1
        self.assertGreaterEqual(checked, 4)

    def test_missing_adapter_fails_verification(self) -> None:
        adapters = self._adapters()
        record = next(iter(adapters["chats"].scan()))
        verifier = EvidenceVerifier({})
        result = verifier.verify_pointer(1, record.provenance_pointer)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
