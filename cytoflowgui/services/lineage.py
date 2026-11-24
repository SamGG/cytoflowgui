"""Input files of a step: where they come from (which step, or outside the workspace)."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..manifest import Manifest
from . import stepid
from .steps import DATA_DIR, OK, REPORTS_DIR, RUNNING, Step, StepRegistry

KIND_EXTENSIONS = {"fcs": (".fcs",), "rds": (".rds",),
                   "table": (".xlsx", ".xlsm", ".csv", ".tsv", ".txt"), "any": ()}


@dataclass
class InputRef:
    param: str
    path: str                     # absolute, as resolved when the step was started
    lineage: bool = False
    inside: bool = False          # inside the workspace folder
    rel: str = ""                 # path relative to the workspace, with '/', when inside
    step: str = ""                # id of the step that produced it ("" if none)
    orphan: bool = False          # in a folder named like a step but unknown to the registry
    exists: bool = True
    size: int | None = None       # fingerprint of an external file
    mtime: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def resolve_input(ws_root: Path, registry: StepRegistry | None, param: str, path: str,
                  lineage: bool = False) -> InputRef:
    p = Path(path)
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()
    ref = InputRef(param, str(p), lineage, exists=p.exists())
    try:
        rel = p.relative_to(Path(ws_root).resolve())
    except ValueError:                                   # outside the workspace
        if p.is_file():
            st = p.stat()
            ref.size = st.st_size
            ref.mtime = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ref
    ref.inside, ref.rel = True, rel.as_posix()
    sid = stepid.parse_folder_name(rel.parts[0]) if rel.parts else None
    if sid:
        if registry is not None and sid in registry.steps:
            ref.step = sid
        else:
            ref.orphan = True
    return ref


def resolve_inputs(ws_root: Path, registry: StepRegistry | None, manifest: Manifest,
                   params: dict) -> list[InputRef]:
    refs = []
    for spec in manifest.inputs:
        value = params.get(spec.param)
        if isinstance(value, str) and value.strip():
            refs.append(resolve_input(ws_root, registry, spec.param, value.strip(), spec.lineage))
    return refs


def describe(ref: InputRef, registry: StepRegistry | None) -> tuple[str, str]:
    """(level, text) for the hint under an input field. Levels: info, warn."""
    if not ref.exists:
        return "warn", "File not found"
    if ref.step and registry is not None and ref.step in registry.steps:
        st = registry.steps[ref.step]
        text = f"From step {st.id} ({st.label})"
        if st.status == RUNNING:
            return "warn", f"{text}, which is still running"
        if st.status != OK:
            return "warn", f"{text}, which {st.status}"
        return "info", text
    if ref.orphan:
        return "warn", "In a step folder that has no registry entry (orphan)"
    if ref.inside:
        return "info", "In the workspace, not produced by a step"
    return "info", "External file (outside the workspace)"


def matches_kind(name: str, kind: str) -> bool:
    exts = KIND_EXTENSIONS.get(kind, ())
    return not exts or name.lower().endswith(exts)


def list_outputs(registry: StepRegistry, step: Step, kind: str) -> list[str]:
    """Files of a step (in data/ and reports/) matching the kind, relative to the step folder."""
    base = registry.step_dir(step)
    found = []
    for sub in (DATA_DIR, REPORTS_DIR):
        d = base / sub
        if d.is_dir():
            found += [f.relative_to(base).as_posix() for f in d.rglob("*")
                      if f.is_file() and matches_kind(f.name, kind)]
    return sorted(found)


def list_output_folders(registry: StepRegistry, step: Step, kind: str) -> list[str]:
    """Folders of a step (inside data/ and reports/) that directly hold files of the kind,
    relative to the step folder, e.g. ['data', 'data/cluster_1']."""
    base = registry.step_dir(step)
    found = []
    for sub in (DATA_DIR, REPORTS_DIR):
        for dirpath, _dirs, files in os.walk(base / sub):
            if any(matches_kind(f, kind) for f in files):
                found.append(Path(dirpath).relative_to(base).as_posix())
    return sorted(found)


def steps_with_outputs(registry: StepRegistry, kind: str, folder: bool = False) -> list[Step]:
    """Successful steps that have at least one matching file (or folder of such files), newest first."""
    lister = list_output_folders if folder else list_outputs
    out = [s for s in registry.steps.values() if s.status == OK and lister(registry, s, kind)]
    return sorted(out, key=lambda s: s.id, reverse=True)
