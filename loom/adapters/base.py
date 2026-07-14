from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loom.model import NormalizedRecord, ProvenancePointer
from loom.model.records import clean_text


class Adapter(ABC):
    name: str
    version: str
    source_class: str

    @abstractmethod
    def scan(self, cursor: dict[str, Any] | None = None) -> Iterator[NormalizedRecord]:
        raise NotImplementedError

    @abstractmethod
    def next_cursor(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def read_span(self, pointer: ProvenancePointer) -> str:
        raise NotImplementedError

    @abstractmethod
    def source_paths(self) -> list[Path]:
        """All files this adapter would consider, for smoke-test limits."""
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        return {}

    def lexical_probe(self, pointer: ProvenancePointer, live_text: str) -> str | None:
        """A substring expected verbatim in the raw source file.

        Used by the verifier as an independent single-line check alongside
        the sha256 comparison (ripgrep matches per line, so the probe must
        not span newlines). Return None to skip the lexical check when no
        such substring exists — e.g. extracted text that is JSON-escaped in
        the raw file.
        """
        for line in live_text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:120]
        return None


def extract_text(content: Any) -> tuple[str, dict[str, Any]]:
    """Pull human text out of a string-or-structured content value.

    Chat exports store message content as either a plain string or nested
    blocks (lists of {type, text} dicts, tool payloads, etc.). Collect every
    string reachable through common content keys and note whether anything
    non-textual was present.
    """
    metadata: dict[str, Any] = {
        "has_structured_content": False,
        "content_shape": type(content).__name__,
    }
    if isinstance(content, str):
        return content, metadata
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            if value.get("type") not in {None, "text"}:
                metadata["has_structured_content"] = True
            for key in ("text", "content", "input", "output", "message"):
                if key in value:
                    walk(value[key])
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif value is not None:
            metadata["has_structured_content"] = True

    walk(content)
    return clean_text("\n".join(part for part in parts if part)), metadata


def file_state(path: Path) -> dict[str, float | int]:
    stat = path.stat()
    return {"mtime": stat.st_mtime, "size": stat.st_size}
