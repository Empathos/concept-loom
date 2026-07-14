from __future__ import annotations

import unittest


class Phase1ImportsTest(unittest.TestCase):
    def test_pipeline_imports(self) -> None:
        import loom.pipeline.cluster  # noqa: F401
        import loom.pipeline.embedder  # noqa: F401
        import loom.pipeline.namer  # noqa: F401
        import loom.api.server  # noqa: F401


if __name__ == "__main__":
    unittest.main()
