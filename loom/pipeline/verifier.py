from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import shutil
import subprocess

from loom.adapters.base import Adapter
from loom.model import ProvenancePointer


@dataclass(frozen=True)
class VerificationResult:
    evidence_id: int
    ok: bool
    reason: str


def verify_text(text: str, digest: str) -> bool:
    return sha256(text.encode("utf-8")).hexdigest() == digest


class EvidenceVerifier:
    def __init__(self, adapters: dict[str, Adapter]):
        self.adapters = adapters

    def verify_pointer(self, evidence_id: int, pointer: ProvenancePointer) -> VerificationResult:
        adapter = self.adapters.get(pointer.source_system)
        if adapter is None:
            return VerificationResult(evidence_id, False, f"no adapter for {pointer.source_system}")

        try:
            live_text = adapter.read_span(pointer)
        except Exception as exc:  # noqa: BLE001 - surfaced in verification report.
            return VerificationResult(evidence_id, False, f"read_span failed: {exc}")

        if not verify_text(live_text, pointer.content_sha256):
            return VerificationResult(evidence_id, False, "sha256 mismatch")

        rg = shutil.which("rg")
        probe = adapter.lexical_probe(pointer, live_text) if live_text else None
        if rg and probe:
            completed = subprocess.run(
                [rg, "-F", "--max-count", "1", probe, pointer.source_path],
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            if completed.returncode not in (0,):
                return VerificationResult(evidence_id, False, "rg distinctive substring check failed")

        return VerificationResult(evidence_id, True, "verified")
