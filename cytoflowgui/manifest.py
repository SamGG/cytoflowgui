"""plugin.toml parsing and validation."""
from __future__ import annotations

import importlib.metadata as md
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from packaging.version import Version

from . import API_VERSION, __version__

INPUT_KINDS = ("fcs", "rds", "table", "any")
REQUIRED = ("id", "name", "category", "tooltip", "version", "api_version", "entry", "rmd")


@dataclass
class InputSpec:
    """An input file of a tool, declared in plugin.toml ([[inputs]]).
    kind: fcs | rds | table | any (for a folder: the kind of files it holds). folder: the parameter is a
    folder, not a file. lineage: the file is normally produced by another step, so the
    tool offers 'From step…' and warns when that step failed or is still running."""
    param: str
    kind: str = "any"
    lineage: bool = False
    label: str = ""
    folder: bool = False


@dataclass
class Manifest:
    dir: Path
    id: str
    name: str
    category: str = "Other"
    tooltip: str = ""
    version: str = "0.0.0"
    api_version: int = 0
    entry: str = ""          # "gui.py:ClassName"
    rmd: str = ""
    help: str = ""
    requires_r: list[str] = field(default_factory=list)      # CRAN
    requires_bioc: list[str] = field(default_factory=list)   # Bioconductor
    requires_python: list[str] = field(default_factory=list)
    min_app: str = "0.0.0"
    inputs: list[InputSpec] = field(default_factory=list)
    user: bool = False       # created by the user (Create a plugin): updates never replace it
    error: str | None = None  # set when the plugin cannot be used


def read_manifest(folder: Path) -> Manifest:
    m = Manifest(dir=folder, id=folder.name, name=folder.name)
    try:
        with open(folder / "plugin.toml", "rb") as f:
            d = tomllib.load(f)
    except Exception as e:  # noqa: BLE001
        m.error = f"Cannot read plugin.toml: {e}"
        return m

    missing = [k for k in REQUIRED if k not in d]
    if missing:
        m.error = f"plugin.toml lacks: {', '.join(missing)}"
        return m

    req = d.get("requires", {})
    m.id, m.name, m.category = d["id"], d["name"], d["category"]
    m.tooltip, m.version, m.api_version = d["tooltip"], d["version"], int(d["api_version"])
    m.entry, m.rmd, m.help = d["entry"], d["rmd"], d.get("help", "")
    m.requires_r = list(req.get("r", []))
    m.requires_bioc = list(req.get("bioc", []))
    m.requires_python = list(req.get("python", []))
    m.min_app = req.get("app", "0.0.0")
    m.user = d.get("user") is True
    for item in d.get("inputs", []):
        if "param" not in item:
            m.error = "An [[inputs]] entry lacks 'param'"
            return m
        spec = InputSpec(str(item["param"]), str(item.get("kind", "any")).lower(),
                         bool(item.get("lineage", False)), str(item.get("label", "")),
                         bool(item.get("folder", False)))
        if spec.kind not in INPUT_KINDS:
            m.error = f"Input '{spec.param}': kind must be one of {', '.join(INPUT_KINDS)}"
            return m
        m.inputs.append(spec)

    if m.api_version != API_VERSION:
        m.error = f"Plugin API {m.api_version} is not supported (application provides {API_VERSION})"
    elif Version(m.min_app) > Version(__version__):
        m.error = f"Requires cytoflowgui >= {m.min_app} (installed {__version__})"
    elif not (folder / m.entry.partition(":")[0]).is_file():
        m.error = f"GUI file not found: {m.entry}"
    elif not (folder / m.rmd).is_file():
        m.error = f"RMarkdown file not found: {m.rmd}"
    else:
        for pkg in m.requires_python:
            try:
                md.version(pkg)
            except md.PackageNotFoundError:
                m.error = f"Missing Python package: {pkg}"
                break
    return m
