"""Plugin discovery and loading."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .api import ToolPlugin
from .manifest import Manifest, read_manifest


def discover(plugins_dir: Path) -> list[Manifest]:
    found = [read_manifest(p) for p in sorted(plugins_dir.iterdir())
             if p.is_dir() and not p.name.startswith((".", "_"))]
    return sorted(found, key=lambda m: (m.category.lower(), m.name.lower()))


def load_tool(m: Manifest, ctx=None, parent=None) -> ToolPlugin:
    """Import the plugin GUI lazily and instantiate it."""
    module_file, _, cls_name = m.entry.partition(":")
    modname = f"cytoflow_plugin_{m.id}"
    spec = importlib.util.spec_from_file_location(modname, m.dir / module_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    cls = getattr(mod, cls_name)
    if not (isinstance(cls, type) and issubclass(cls, ToolPlugin)):
        raise TypeError(f"{m.entry} is not a ToolPlugin subclass")
    return cls(m, ctx, parent)
