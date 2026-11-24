"""Shared helpers for the tests that need steps without running R."""
from pathlib import Path

from cytoflowgui.manifest import read_manifest
from cytoflowgui.services import steps


def make_plugin(root: Path, plugin_id: str = "demo", inputs: str = "", rmd: str = "") -> Path:
    d = root / plugin_id
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(
        f'id = "{plugin_id}"\nname = "Demo"\ncategory = "Test"\ntooltip = "t"\nversion = "1.0.0"\n'
        f'api_version = 2\nentry = "gui.py:X"\nrmd = "script.Rmd"\n{inputs}', encoding="utf-8")
    (d / "gui.py").write_text("class X: pass\n")
    (d / "script.Rmd").write_text(rmd, encoding="utf-8")
    return d


def make_step(reg, label="", files=None, status=steps.OK, inputs=None, plugin="demo"):
    """A finished step with some files in data/ (or reports/ when the name starts with reports/)."""
    s = reg.create(plugin, "1.0.0", label, inputs=inputs or [])
    for name, text in (files or {}).items():
        f = reg.step_dir(s) / (name if name.startswith("reports/") else f"data/{name}")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return reg.finish(s, 0 if status == steps.OK else 1, status)
