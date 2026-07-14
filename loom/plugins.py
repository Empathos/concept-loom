from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

PLUGIN_PREFIX = "plugin:"


def is_plugin_spec(value: str) -> bool:
    return value.startswith(PLUGIN_PREFIX)


def load_plugin(spec: str) -> Any:
    """Resolve a "plugin:module.path:attribute" reference to the attribute.

    Lets adapters and LLM providers live outside this package — e.g. private
    integrations that should never enter a public checkout. Directories
    listed under [plugins] paths in the config are added to sys.path before
    resolution.
    """
    if not is_plugin_spec(spec):
        raise ValueError(f"not a plugin spec: {spec!r}")
    target = spec[len(PLUGIN_PREFIX):]
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        raise ValueError(
            f"plugin spec must look like 'plugin:module.path:Attribute', got {spec!r}"
        )
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attribute)
    except AttributeError:
        raise ValueError(f"module {module_name!r} has no attribute {attribute!r}") from None


def add_plugin_paths(paths: tuple[Path, ...]) -> None:
    for path in paths:
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
