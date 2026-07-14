from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class PathsConfig:
    data_dir: Path
    db_path: Path


@dataclass(frozen=True)
class SourceConfig:
    """One configured ingest source: a named instance of an adapter type.

    `name` becomes the `source_system` recorded on every evidence row from
    this source, so renaming a source orphans provenance verification for
    rows already ingested under the old name.
    """

    name: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingConfig:
    model: str
    batch_size: int
    device: str
    max_seq_length: int


@dataclass(frozen=True)
class ClusteringConfig:
    min_cluster_size: int
    min_samples: int
    reduced_dimensions: int


@dataclass(frozen=True)
class LLMConfig:
    # "anthropic" uses the official Anthropic SDK; "openai" speaks the
    # OpenAI-compatible chat completions protocol, which also covers local
    # servers such as Ollama, LM Studio, and vLLM via base_url.
    provider: str = "anthropic"
    model: str = "claude-opus-4-8"
    base_url: str | None = None
    # Environment variable holding the API key. Defaults per provider:
    # ANTHROPIC_API_KEY / OPENAI_API_KEY. Local OpenAI-compatible servers
    # usually need no key; an unset variable is only an error when the
    # request is rejected for it.
    api_key_env: str | None = None
    max_tokens: int = 1024
    timeout: int = 120
    max_llm_calls_per_run: int = 200


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int


@dataclass(frozen=True)
class LoomConfig:
    root: Path
    paths: PathsConfig
    sources: tuple[SourceConfig, ...]
    embedding: EmbeddingConfig
    clustering: ClusteringConfig
    llm: LLMConfig
    server: ServerConfig
    plugin_paths: tuple[Path, ...] = ()


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def load_config(path: str | Path = "loom.toml") -> LoomConfig:
    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    root = config_path.parent

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    paths = data.get("paths", {})
    embedding = data.get("embedding", {})
    clustering = data.get("clustering", {})
    llm = data.get("llm", {})
    server = data.get("server", {})

    plugin_paths = tuple(
        _resolve(root, entry) for entry in data.get("plugins", {}).get("paths", [])
    )
    if plugin_paths:
        # Applied at load time so plugin adapters/providers referenced by
        # [[sources]] or [llm] resolve no matter which entry point runs.
        from loom.plugins import add_plugin_paths

        add_plugin_paths(plugin_paths)

    sources: list[SourceConfig] = []
    for entry in data.get("sources", []):
        options = {k: v for k, v in entry.items() if k not in {"name", "type"}}
        name = entry.get("name")
        adapter_type = entry.get("type")
        if not name or not adapter_type:
            raise ValueError(f"[[sources]] entries need both name and type: {entry!r}")
        sources.append(SourceConfig(name=name, type=adapter_type, options=options))
    names = [source.name for source in sources]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate source names in config: {names!r}")

    return LoomConfig(
        root=root,
        paths=PathsConfig(
            data_dir=_resolve(root, paths.get("data_dir", "data")),
            db_path=_resolve(root, paths.get("db_path", "data/loom.db")),
        ),
        sources=tuple(sources),
        embedding=EmbeddingConfig(
            model=embedding.get("model", "BAAI/bge-m3"),
            batch_size=int(embedding.get("batch_size", 8)),
            device=embedding.get("device", "auto"),
            max_seq_length=int(embedding.get("max_seq_length", 512)),
        ),
        clustering=ClusteringConfig(
            min_cluster_size=int(clustering.get("min_cluster_size", 6)),
            min_samples=int(clustering.get("min_samples", 3)),
            reduced_dimensions=int(clustering.get("reduced_dimensions", 50)),
        ),
        llm=LLMConfig(
            provider=llm.get("provider", "anthropic"),
            model=llm.get("model", "claude-opus-4-8"),
            base_url=llm.get("base_url"),
            api_key_env=llm.get("api_key_env"),
            max_tokens=int(llm.get("max_tokens", 1024)),
            timeout=int(llm.get("timeout", 120)),
            max_llm_calls_per_run=int(llm.get("max_llm_calls_per_run", 200)),
        ),
        server=ServerConfig(
            host=server.get("host", "127.0.0.1"),
            port=int(server.get("port", 8901)),
        ),
        plugin_paths=plugin_paths,
    )
