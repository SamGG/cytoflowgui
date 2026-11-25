"""Creates the skeleton of a new plugin from the templates shipped with the application
(templates/plugin/*.tmpl). No widget imports: the dialog of the UI only calls check_spec() and
create_plugin().

Template syntax (see "Plugin templates" in CLAUDE.md):
  {{key}}                          replaced by the value, escaped for the type of the file
  {{#input}} ... {{/input}}        lines kept only when the plugin has an input
  {{^input}} ... {{/input}}        lines kept only when the plugin has none
The lines holding a block marker are dropped. An unknown key or block is an error, so that a
template that drifts from this module fails the tests instead of producing a broken plugin.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import API_VERSION, __version__

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "plugin"
# template file -> file written in the plugin folder
FILES = {
    "plugin.toml.tmpl": "plugin.toml", "gui.py.tmpl": "gui.py",
    "script.Rmd.tmpl": "script.Rmd", "help.html.tmpl": "help.html",
}
ID_RE = re.compile(r"[a-z][a-z0-9_]{1,39}")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                    *(f"lpt{i}" for i in range(1, 10))}
MAX_TEXT = 300

# input kind chosen in the dialog -> values of the {{input_*}} placeholders; None = no input
INPUT_KINDS: dict[str, dict | None] = {
    "none": None,
    "fcs_file": dict(param="fcs_file", kind="fcs", folder="false", label="FCS file",
                     filter="FCS files (*.fcs);;All files (*)", what="FCS file"),
    "fcs_dir": dict(param="fcs_dir", kind="fcs", folder="true", label="FCS folder",
                    filter="All files (*)", what="folder of FCS files"),
    "rds": dict(param="rds_file", kind="rds", folder="false", label="RDS file",
                filter="RDS files (*.rds);;All files (*)", what="RDS file"),
    "table": dict(param="table_file", kind="table", folder="false", label="Table file",
                  filter="Tables (*.csv *.tsv *.txt *.xlsx);;All files (*)", what="table file"),
}


class ScaffoldError(Exception):
    pass


@dataclass
class PluginSpec:
    id: str
    name: str
    category: str
    tooltip: str
    input_kind: str = "none"          # a key of INPUT_KINDS


def class_name(plugin_id: str) -> str:
    """'my_tool' -> 'MyToolTool'."""
    return "".join(part.capitalize() for part in plugin_id.split("_") if part) + "Tool"


def suggest_id(name: str) -> str:
    """A valid id from a display name: 'My new tool' -> 'my_new_tool'."""
    text = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if text and not text[0].isalpha():
        text = "p_" + text
    return text[:40].rstrip("_")


def in_git_checkout(path: Path) -> bool:
    """True when the folder is inside a git checkout (a development plugins folder)."""
    p = path.resolve()
    return any((q / ".git").exists() for q in [p, *p.parents])


def check_spec(spec: PluginSpec, plugins_dir: Path, taken_ids=()) -> str | None:
    """The first problem found in the answers, or None. `taken_ids`: ids of the plugins already
    known (installed under another folder name, for instance)."""
    if not ID_RE.fullmatch(spec.id):
        return "The id must be 2 to 40 characters: lowercase letters, digits and _, starting with a letter."
    if spec.id in WINDOWS_RESERVED:
        return f"'{spec.id}' is a reserved name on Windows."
    if spec.id in set(taken_ids) or (plugins_dir / spec.id).exists():
        return f"A plugin with the id '{spec.id}' already exists."
    for label, value in (("name", spec.name), ("category", spec.category), ("tooltip", spec.tooltip)):
        value = value.strip()
        if not value:
            return f"The {label} is required."
        if len(value) > MAX_TEXT or any(ord(c) < 32 for c in value):
            return f"The {label} must be one line of at most {MAX_TEXT} characters."
    if spec.input_kind not in INPUT_KINDS:
        return f"Unknown input kind: {spec.input_kind}"
    return None


# ---- rendering ---------------------------------------------------------------------------
_BLOCK = re.compile(r"\{\{([#^/])(\w+)\}\}")
_KEY = re.compile(r"\{\{(\w+)\}\}")


def _values(spec: PluginSpec) -> dict[str, str]:
    values = {
        "id": spec.id, "name": spec.name.strip(), "category": spec.category.strip(),
        "tooltip": spec.tooltip.strip(), "class_name": class_name(spec.id),
        "api_version": str(API_VERSION), "app_version": __version__,
    }
    inp = INPUT_KINDS[spec.input_kind]
    for key in ("param", "kind", "folder", "label", "filter", "what"):
        values[f"input_{key}"] = inp[key] if inp else ""
    return values


def _escape(value: str, suffix: str) -> str:
    if suffix == ".html":
        return html.escape(value, quote=False)
    return json.dumps(value, ensure_ascii=False)[1:-1]            # inside the quotes of the template


def render(template: str, values: dict[str, str], blocks: dict[str, bool], suffix: str) -> str:
    """Keep or drop the block lines, then replace the keys. `suffix`: type of the output file."""
    out, stack = [], []                                          # stack: is each open block kept
    for line in template.splitlines(keepends=True):
        marks = _BLOCK.findall(line)
        if marks:
            for sign, name in marks:
                if name not in blocks:
                    raise ScaffoldError(f"Unknown block in a template: {name}")
                if sign == "/":
                    if not stack:
                        raise ScaffoldError(f"Block closed but not open: {name}")
                    stack.pop()
                else:
                    stack.append(blocks[name] == (sign == "#"))
            continue
        if all(stack):
            out.append(line)
    if stack:
        raise ScaffoldError("A block of a template is not closed")

    def sub(m: re.Match) -> str:
        if m.group(1) not in values:
            raise ScaffoldError(f"Unknown placeholder in a template: {m.group(1)}")
        return _escape(values[m.group(1)], suffix)
    return _KEY.sub(sub, "".join(out))


def create_plugin(spec: PluginSpec, plugins_dir: Path, taken_ids=()) -> Path:
    """Write <plugins_dir>/<id>/ and return it. Never overwrites: the folder is built aside, then
    renamed (a rename onto an existing folder fails)."""
    problem = check_spec(spec, plugins_dir, taken_ids)
    if problem:
        raise ScaffoldError(problem)
    values, blocks = _values(spec), {"input": INPUT_KINDS[spec.input_kind] is not None}
    plugins_dir.mkdir(parents=True, exist_ok=True)
    tmp = plugins_dir / f".{spec.id}.new-{uuid.uuid4().hex[:8]}"          # dot: ignored by discover()
    final = plugins_dir / spec.id
    try:
        tmp.mkdir()
        for src, dst in FILES.items():
            text = (TEMPLATES_DIR / src).read_text(encoding="utf-8")
            (tmp / dst).write_text(render(text, values, blocks, Path(dst).suffix), encoding="utf-8")
        os.rename(tmp, final)
    except (OSError, ScaffoldError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if isinstance(e, ScaffoldError):
            raise
        raise ScaffoldError(f"Cannot create the plugin: {e}") from e
    return final
