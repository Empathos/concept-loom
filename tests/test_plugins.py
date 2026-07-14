from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from loom.adapters import build_adapters
from loom.config import LLMConfig, load_config
from loom.llm.client import call_json
from loom.plugins import add_plugin_paths, load_plugin

FIXTURES = Path(__file__).parent / "fixtures"

PLUGIN_MODULE = textwrap.dedent(
    '''
    from loom.adapters.markdown_folder import MarkdownFolderAdapter, MarkdownFolderConfig


    class ShoutingNotesAdapter(MarkdownFolderAdapter):
        """A trivial third-party adapter: markdown_folder with a marker."""

        version = "shouting_notes/1"

        @classmethod
        def from_source(cls, source, config_root):
            return cls(MarkdownFolderConfig.from_source(source, config_root))


    def fake_call_json(cfg, *, session_key, prompt):
        return {"provider": "external", "session_key": session_key}
    '''
)


@pytest.fixture()
def plugin_dir(tmp_path):
    (tmp_path / "my_loom_plugins.py").write_text(PLUGIN_MODULE, encoding="utf-8")
    return tmp_path


def test_plugin_adapter_loads_from_config(tmp_path, plugin_dir):
    config_path = tmp_path / "loom.toml"
    config_path.write_text(
        "\n".join(
            [
                "[plugins]",
                f'paths = ["{plugin_dir}"]',
                "",
                "[[sources]]",
                'name = "notes"',
                'type = "plugin:my_loom_plugins:ShoutingNotesAdapter"',
                f'root = "{FIXTURES / "notes"}"',
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    adapters = build_adapters(cfg)
    assert adapters["notes"].version == "shouting_notes/1"
    assert len(list(adapters["notes"].scan())) >= 1


def test_plugin_llm_provider_dispatch(plugin_dir):
    add_plugin_paths((plugin_dir,))
    cfg = LLMConfig(provider="plugin:my_loom_plugins:fake_call_json")
    result = call_json(cfg, session_key="agent:loom:namer-x", prompt="p")
    assert result == {"provider": "external", "session_key": "agent:loom:namer-x"}


def test_malformed_plugin_specs_are_rejected(plugin_dir):
    add_plugin_paths((plugin_dir,))
    with pytest.raises(ValueError):
        load_plugin("plugin:my_loom_plugins")
    with pytest.raises(ValueError):
        load_plugin("plugin:my_loom_plugins:missing_attr")
    with pytest.raises(ValueError):
        load_plugin("markdown_folder")
