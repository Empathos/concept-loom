from __future__ import annotations

import unittest

from loom.model.records import ProvenancePointer, text_sha256


class ProvenancePointerTest(unittest.TestCase):
    def test_requires_honest_granularity(self) -> None:
        with self.assertRaises(ValueError):
            ProvenancePointer(
                source_system="x",
                source_class="raw_transcript",
                source_path="/tmp/x",
                source_id=None,
                session_id=None,
                message_id=None,
                parent_message_id=None,
                timestamp=None,
                span={"kind": "char", "start": 0, "end": 1},
                granularity="pretend_precise",
                content_sha256=text_sha256("x"),
                adapter_version="test/1",
            )


if __name__ == "__main__":
    unittest.main()
