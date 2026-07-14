from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import datetime
from pathlib import Path
import re
from typing import Any

from loom.adapters.base import Adapter, file_state
from loom.config import SourceConfig
from loom.model import NormalizedRecord, ProvenancePointer
from loom.model.records import clean_text, text_sha256


_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


@dataclass(frozen=True)
class MarkdownFolderConfig:
    name: str
    root: Path
    include: tuple[str, ...] = ("**/*.md", "**/*.txt")
    max_chunk_chars: int = 2000
    min_text_chars: int = 40

    @classmethod
    def from_source(cls, source: SourceConfig, config_root: Path) -> "MarkdownFolderConfig":
        options = source.options
        root = options.get("root")
        if not root:
            raise ValueError(f"markdown_folder source {source.name!r} needs a root")
        root_path = Path(root).expanduser()
        if not root_path.is_absolute():
            root_path = config_root / root_path
        return cls(
            name=source.name,
            root=root_path,
            include=tuple(options.get("include", ("**/*.md", "**/*.txt"))),
            max_chunk_chars=int(options.get("max_chunk_chars", 2000)),
            min_text_chars=int(options.get("min_text_chars", 40)),
        )


def chunk_spans(text: str, max_chars: int) -> list[tuple[int, int]]:
    """Split text into paragraph-aligned (start, end) character spans.

    Consecutive paragraphs are packed into one span until adding the next
    would exceed max_chars; a single oversized paragraph stays whole so every
    span is an exact slice of the source text (the provenance contract).
    """
    paragraphs: list[tuple[int, int]] = []
    cursor = 0
    for separator in _PARAGRAPH_BREAK.finditer(text):
        if separator.start() > cursor:
            paragraphs.append((cursor, separator.start()))
        cursor = separator.end()
    if cursor < len(text):
        paragraphs.append((cursor, len(text)))

    spans: list[tuple[int, int]] = []
    for start, end in paragraphs:
        if spans and (end - spans[-1][0]) <= max_chars:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return [(start, end) for start, end in spans if text[start:end].strip()]


class MarkdownFolderAdapter(Adapter):
    """Ingest a folder of markdown/plain-text notes as chunked evidence.

    Chunks carry exact character spans into the source file, so evidence can
    be re-verified against the live file at any time. Chunks from the same
    file share a session_id (the file's relative path), which is what links
    concepts that co-occur within a document.
    """

    version = "markdown_folder/1"
    source_class = "document"

    def __init__(self, config: MarkdownFolderConfig):
        self.config = config
        self.name = config.name
        self._cursor: dict[str, Any] = {}
        self._stats: dict[str, int] = {}

    def source_paths(self) -> list[Path]:
        paths: set[Path] = set()
        for pattern in self.config.include:
            paths.update(p for p in self.config.root.glob(pattern) if p.is_file())
        return sorted(paths)

    def scan(self, cursor: dict[str, Any] | None = None) -> Iterator[NormalizedRecord]:
        previous = cursor or {}
        next_cursor: dict[str, Any] = {}
        self._stats = {
            "files_seen": 0,
            "files_scanned": 0,
            "files_skipped_unchanged": 0,
            "records_emitted": 0,
            "short_text_skipped": 0,
        }
        for path in self.source_paths():
            self._stats["files_seen"] += 1
            path_key = str(path)
            state = file_state(path)
            next_cursor[path_key] = state
            old = previous.get(path_key)
            if old and old.get("mtime") == state["mtime"] and old.get("size") == state["size"]:
                self._stats["files_skipped_unchanged"] += 1
                continue
            self._stats["files_scanned"] += 1
            yield from self._records_from_file(path)
        self._cursor = next_cursor

    def next_cursor(self) -> dict[str, Any]:
        return self._cursor

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def _read(self, path: Path) -> str:
        return path.read_bytes().decode("utf-8", errors="replace")

    def _relpath(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.config.root))
        except ValueError:
            return str(path)

    def _records_from_file(self, path: Path) -> Iterator[NormalizedRecord]:
        raw = self._read(path)
        relpath = self._relpath(path)
        timestamp = (
            datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)
            .isoformat()
        )
        for index, (start, end) in enumerate(chunk_spans(raw, self.config.max_chunk_chars)):
            text = clean_text(raw[start:end])
            if len(text.strip()) < self.config.min_text_chars:
                self._stats["short_text_skipped"] += 1
                continue
            pointer = ProvenancePointer(
                source_system=self.name,
                source_class=self.source_class,
                source_path=str(path),
                source_id=relpath,
                session_id=relpath,
                message_id=None,
                parent_message_id=None,
                timestamp=timestamp,
                span={"kind": "char", "start": start, "end": end},
                granularity="exact_span",
                content_sha256=text_sha256(text),
                adapter_version=self.version,
                transform_chain=("adapter/markdown_folder/1",),
            )
            yield NormalizedRecord(
                source_id=relpath,
                source_type="document_chunk",
                source_class=self.source_class,
                timestamp=timestamp,
                text=text,
                metadata={"relpath": relpath, "chunk_index": index, "n_chars": end - start},
                provenance_pointer=pointer,
            )
            self._stats["records_emitted"] += 1

    def read_span(self, pointer: ProvenancePointer) -> str:
        if pointer.source_system != self.name:
            raise ValueError(
                f"pointer source_system {pointer.source_system!r} is not {self.name!r}"
            )
        span = pointer.span
        if span.get("kind") != "char":
            raise ValueError(f"unsupported span kind for markdown_folder: {span.get('kind')}")
        raw = self._read(Path(pointer.source_path))
        return clean_text(raw[int(span.get("start", 0)) : int(span.get("end", len(raw)))])
