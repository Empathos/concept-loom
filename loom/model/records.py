from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any


VALID_GRANULARITIES = {"exact_span", "message", "file", "unknown"}
VALID_SPAN_KINDS = {"char", "byte", "line", "message", "file"}


def clean_text(text: str) -> str:
    return text.encode("utf-8", "replace").decode("utf-8")


def text_sha256(text: str) -> str:
    return sha256(clean_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProvenancePointer:
    source_system: str
    source_class: str
    source_path: str
    source_id: str | None
    session_id: str | None
    message_id: str | None
    parent_message_id: str | None
    timestamp: str | None
    span: dict[str, Any]
    granularity: str
    content_sha256: str
    adapter_version: str
    transform_chain: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.granularity not in VALID_GRANULARITIES:
            raise ValueError(f"invalid granularity: {self.granularity}")
        span_kind = self.span.get("kind")
        if span_kind not in VALID_SPAN_KINDS:
            raise ValueError(f"invalid span kind: {span_kind}")
        if not self.content_sha256:
            raise ValueError("content_sha256 is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "source_class": self.source_class,
            "source_path": self.source_path,
            "source_id": self.source_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "parent_message_id": self.parent_message_id,
            "timestamp": self.timestamp,
            "span": self.span,
            "granularity": self.granularity,
            "content_sha256": self.content_sha256,
            "adapter_version": self.adapter_version,
            "transform_chain": list(self.transform_chain),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> "ProvenancePointer":
        data = json.loads(value)
        data["transform_chain"] = tuple(data.get("transform_chain", []))
        return cls(**data)


@dataclass(frozen=True)
class NormalizedRecord:
    source_id: str | None
    source_type: str
    source_class: str
    timestamp: str | None
    text: str
    metadata: dict[str, Any]
    provenance_pointer: ProvenancePointer

    def __post_init__(self) -> None:
        if self.text != clean_text(self.text):
            raise ValueError("record text must be valid UTF-8 text before storage")
        if self.provenance_pointer.content_sha256 != text_sha256(self.text):
            raise ValueError("record text does not match provenance content_sha256")
