from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import glob as globlib
import json
from pathlib import Path
from typing import Any

from loom.adapters.base import Adapter, extract_text, file_state
from loom.config import SourceConfig
from loom.model import NormalizedRecord, ProvenancePointer
from loom.model.records import clean_text, text_sha256


_SYNTHETIC_ID_PREFIX = "line-"


def resolve_path(obj: Any, dotted: str | None) -> Any:
    """Follow a dotted key path ("message.content") into nested dicts."""
    if not dotted:
        return None
    value = obj
    for key in dotted.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


@dataclass(frozen=True)
class JsonlTranscriptsConfig:
    name: str
    glob: str
    text_path: str = "message.content"
    role_path: str = "message.role"
    id_path: str = "id"
    timestamp_path: str = "timestamp"
    session_id_path: str | None = None
    skip_roles: tuple[str, ...] = ()
    min_text_chars: int = 1

    @classmethod
    def from_source(cls, source: SourceConfig, config_root: Path) -> "JsonlTranscriptsConfig":
        options = source.options
        pattern = options.get("glob")
        if not pattern:
            raise ValueError(f"jsonl_transcripts source {source.name!r} needs a glob")
        pattern_path = Path(pattern).expanduser()
        if not pattern_path.is_absolute():
            pattern_path = config_root / pattern_path
        return cls(
            name=source.name,
            glob=str(pattern_path),
            text_path=options.get("text_path", "message.content"),
            role_path=options.get("role_path", "message.role"),
            id_path=options.get("id_path", "id"),
            timestamp_path=options.get("timestamp_path", "timestamp"),
            session_id_path=options.get("session_id_path"),
            skip_roles=tuple(options.get("skip_roles", ())),
            min_text_chars=int(options.get("min_text_chars", 1)),
        )


class JsonlTranscriptsAdapter(Adapter):
    """Ingest chat/session exports stored as JSON Lines.

    Field locations are configurable dotted paths, so one adapter covers most
    transcript formats (Claude Code sessions, chat exports, agent logs). Lines
    where text_path resolves to nothing are skipped, which transparently
    ignores non-message envelope lines. Records without an id at id_path get
    a synthetic "line-N" message id; provenance still verifies via the sha256
    check, but the ripgrep lexical probe is skipped for extracted text.
    """

    version = "jsonl_transcripts/1"
    source_class = "raw_transcript"

    def __init__(self, config: JsonlTranscriptsConfig):
        self.config = config
        self.name = config.name
        self._cursor: dict[str, Any] = {}
        self._stats: dict[str, int] = {}

    def source_paths(self) -> list[Path]:
        return sorted(Path(p) for p in globlib.glob(self.config.glob, recursive=True))

    def scan(self, cursor: dict[str, Any] | None = None) -> Iterator[NormalizedRecord]:
        previous = cursor or {}
        next_cursor: dict[str, Any] = {}
        self._stats = {
            "files_seen": 0,
            "files_scanned": 0,
            "files_skipped_unchanged": 0,
            "lines_seen": 0,
            "records_emitted": 0,
            "no_text_skipped": 0,
            "role_skipped": 0,
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

    def _iter_json_lines(self, path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield line_no, json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {path}:{line_no}: {exc}") from exc

    def _session_id(self, item: dict[str, Any], path: Path) -> str:
        if self.config.session_id_path:
            value = resolve_path(item, self.config.session_id_path)
            if value is not None:
                return str(value)
        return path.stem

    def _records_from_file(self, path: Path) -> Iterator[NormalizedRecord]:
        for line_no, item in self._iter_json_lines(path):
            self._stats["lines_seen"] += 1
            content = resolve_path(item, self.config.text_path)
            if content is None:
                self._stats["no_text_skipped"] += 1
                continue
            role = resolve_path(item, self.config.role_path)
            if role is not None and str(role) in self.config.skip_roles:
                self._stats["role_skipped"] += 1
                continue
            text, metadata = extract_text(content)
            text = clean_text(text)
            if len(text.strip()) < self.config.min_text_chars:
                self._stats["short_text_skipped"] += 1
                continue
            raw_id = resolve_path(item, self.config.id_path)
            message_id = str(raw_id) if raw_id is not None else f"{_SYNTHETIC_ID_PREFIX}{line_no}"
            timestamp_value = resolve_path(item, self.config.timestamp_path)
            timestamp = str(timestamp_value) if timestamp_value is not None else None
            session_id = self._session_id(item, path)
            pointer = ProvenancePointer(
                source_system=self.name,
                source_class=self.source_class,
                source_path=str(path),
                source_id=session_id,
                session_id=session_id,
                message_id=message_id,
                parent_message_id=None,
                timestamp=timestamp,
                span={"kind": "char", "start": 0, "end": len(text)},
                granularity="exact_span",
                content_sha256=text_sha256(text),
                adapter_version=self.version,
                transform_chain=("adapter/jsonl_transcripts/1",),
            )
            metadata.update({"role": role, "line_no": line_no})
            yield NormalizedRecord(
                source_id=session_id,
                source_type="transcript_message",
                source_class=self.source_class,
                timestamp=timestamp,
                text=text,
                metadata=metadata,
                provenance_pointer=pointer,
            )
            self._stats["records_emitted"] += 1

    def read_span(self, pointer: ProvenancePointer) -> str:
        if pointer.source_system != self.name:
            raise ValueError(
                f"pointer source_system {pointer.source_system!r} is not {self.name!r}"
            )
        target = pointer.message_id
        if not target:
            raise ValueError("jsonl_transcripts pointer requires message_id")
        for line_no, item in self._iter_json_lines(Path(pointer.source_path)):
            if target.startswith(_SYNTHETIC_ID_PREFIX):
                if line_no != int(target[len(_SYNTHETIC_ID_PREFIX):]):
                    continue
            else:
                raw_id = resolve_path(item, self.config.id_path)
                if raw_id is None or str(raw_id) != target:
                    continue
            content = resolve_path(item, self.config.text_path)
            text, _metadata = extract_text(content)
            text = clean_text(text)
            span = pointer.span
            if span.get("kind") == "char":
                return text[int(span.get("start", 0)) : int(span.get("end", len(text)))]
            if span.get("kind") == "message":
                return text
            raise ValueError(f"unsupported span kind for jsonl_transcripts: {span.get('kind')}")
        raise ValueError(f"message {target} not found in {pointer.source_path}")

    def lexical_probe(self, pointer: ProvenancePointer, live_text: str) -> str | None:
        message_id = pointer.message_id or ""
        if message_id and not message_id.startswith(_SYNTHETIC_ID_PREFIX):
            # A real id from the export is the stable raw-file anchor; the
            # extracted text may be JSON-escaped in the file.
            return message_id
        return None
