from pathlib import Path

from loom.adapters.base import Adapter
from loom.adapters.jsonl_transcripts import JsonlTranscriptsAdapter, JsonlTranscriptsConfig
from loom.adapters.markdown_folder import MarkdownFolderAdapter, MarkdownFolderConfig
from loom.config import LoomConfig, SourceConfig

ADAPTER_TYPES = {
    "markdown_folder": (MarkdownFolderAdapter, MarkdownFolderConfig),
    "jsonl_transcripts": (JsonlTranscriptsAdapter, JsonlTranscriptsConfig),
}


def build_adapter(source: SourceConfig, config_root: Path) -> Adapter:
    try:
        adapter_cls, config_cls = ADAPTER_TYPES[source.type]
    except KeyError:
        raise ValueError(
            f"unknown adapter type {source.type!r} for source {source.name!r}; "
            f"known types: {sorted(ADAPTER_TYPES)}"
        ) from None
    return adapter_cls(config_cls.from_source(source, config_root))


def build_adapters(cfg: LoomConfig) -> dict[str, Adapter]:
    """Instantiate every configured source, keyed by source name.

    Relative paths in source options resolve against the config file's
    directory, so a checked-in config works from any working directory.
    """
    return {source.name: build_adapter(source, cfg.root) for source in cfg.sources}


__all__ = [
    "ADAPTER_TYPES",
    "Adapter",
    "JsonlTranscriptsAdapter",
    "JsonlTranscriptsConfig",
    "MarkdownFolderAdapter",
    "MarkdownFolderConfig",
    "build_adapter",
    "build_adapters",
]
