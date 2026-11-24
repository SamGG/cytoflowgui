"""Steps of a workspace: allocation, sidecars (step.json) and the steps.tsv index.

Each step is a folder `<id>_<label>` directly inside the workspace. Its `step.json` is the
source of truth (written atomically, never locked by Excel). `steps.tsv` is only an index
generated from the sidecars: UTF-8 with BOM, tab separated, rewritten atomically.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from . import stepid
from .workspace import MARKER_DIR, Workspace, pid_alive

SIDECAR = "step.json"
SCHEMA = 1
COLUMNS = ["id", "label", "plugin", "plugin_version", "comment", "inputs", "status", "started_at",
           "exit_code", "finished_at", "html_report", "data_folder", "report_folder",
           "app_version", "pid", "rerun_of"]
RUNNING, OK, FAILED, CANCELLED = "running", "ok", "failed", "cancelled"
INTERRUPTED, MISSING, DELETED, UNKNOWN = "interrupted", "missing", "deleted", "unknown"
TOMBSTONES = "tombstones.json"      # in .cytoflow: steps whose folder is gone (deleted or missing)
DATA_DIR, REPORTS_DIR = "data", "reports"


class StepError(Exception):
    pass


def iso_utc(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def local_time(text: str) -> str:
    """ISO UTC timestamp -> local time for display."""
    when = parse_iso(text)
    return when.astimezone().strftime("%Y-%m-%d %H:%M:%S") if when else ""


def duration_text(started: str, finished: str = "") -> str:
    a = parse_iso(started)
    b = parse_iso(finished) if finished else datetime.now(timezone.utc)
    if not a or not b:
        return ""
    s = max(0, int((b - a).total_seconds()))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s" if m else f"{s}s"


def _cell(value, guard: bool = False) -> str:
    """One TSV cell: no tab or line break; text that Excel would read as a formula is neutralised."""
    text = "" if value is None else str(value)
    text = text.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return "'" + text if guard and text[:1] in ("=", "+", "-", "@") else text


@dataclass
class Step:
    id: str
    folder: str                   # folder name inside the workspace (never changed after creation)
    plugin: str
    plugin_version: str
    label: str                    # the user's label, else the plugin id
    comment: str = ""
    inputs: list[str] = field(default_factory=list)       # ids of the steps used as input
    status: str = RUNNING
    started_at: str = ""
    exit_code: int | None = None
    finished_at: str = ""
    html_report: str = ""         # paths below are relative to the workspace, with '/'
    data_folder: str = ""         # FCS, RDS... to continue the analysis
    report_folder: str = ""       # text, XLSX... end products
    app_version: str = ""
    pid: int | None = None
    host: str = ""
    rerun_of: str = ""
    deleted_at: str = ""
    input_files: list[dict] = field(default_factory=list)   # what each input parameter pointed to
    params: dict = field(default_factory=dict)            # the plugin parameters, for a later re-run

    def row(self) -> dict[str, str]:
        return {
            "id": self.id, "label": _cell(self.label, True), "plugin": self.plugin,
            "plugin_version": self.plugin_version, "comment": _cell(self.comment, True),
            "inputs": ";".join(self.inputs), "status": self.status, "started_at": self.started_at,
            "exit_code": _cell(self.exit_code), "finished_at": self.finished_at,
            "html_report": self.html_report, "data_folder": self.data_folder,
            "report_folder": self.report_folder, "app_version": self.app_version,
            "pid": _cell(self.pid), "rerun_of": self.rerun_of,
        }

    def to_json(self) -> dict:
        return {"schema": SCHEMA, **asdict(self)}

    @classmethod
    def from_json(cls, data: dict) -> "Step":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def _write_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
    os.replace(tmp, path)                  # fails with PermissionError if another program has it open


def _non_empty(folder: Path) -> bool:
    return folder.is_dir() and any(folder.iterdir())


class StepRegistry:
    """All the steps of one workspace (kept in memory, rebuilt from the sidecars)."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.steps: dict[str, Step] = {}
        self.dirty = False                 # steps.tsv is out of date
        self.load()

    # ---- reading
    def _tomb_path(self) -> Path:
        return self.ws.marker_dir / TOMBSTONES

    def _read_tombs(self) -> dict:
        try:
            return json.loads(self._tomb_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_tomb(self, step: Step) -> None:
        tombs = self._read_tombs()
        tombs[step.id] = step.to_json()
        _write_atomic(self._tomb_path(), json.dumps(tombs, indent=2, ensure_ascii=False))

    def load(self) -> None:
        self.steps.clear()
        self._disk_ids: set[str] = set()
        for child in sorted(self.ws.root.iterdir()):
            sid = stepid.parse_folder_name(child.name) if child.is_dir() else None
            if sid:
                self._disk_ids.add(sid)
            sidecar = child / SIDECAR
            if not sid or not sidecar.is_file():
                continue
            try:
                step = Step.from_json(json.loads(sidecar.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            step.folder = child.name
            self.steps[step.id] = step
        for sid, data in self._read_tombs().items():       # deleted or missing steps
            if sid not in self.steps:
                step = Step.from_json(data)
                step.folder = ""
                self.steps[sid] = step
        self._adopt_missing_from_tsv()

    def _adopt_missing_from_tsv(self) -> None:
        """A step listed in steps.tsv whose folder has vanished is 'missing' (kept as a tombstone)."""
        try:
            lines = self.ws.registry_path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return
        if len(lines) < 2:
            return
        head = lines[0].split("\t")
        for line in lines[1:]:
            row = dict(zip(head, line.split("\t")))
            sid = row.get("id", "")
            if not stepid.is_valid(sid) or sid in self.steps or sid in self._disk_ids:
                continue
            code = row.get("exit_code", "")
            step = Step(id=sid, folder="", plugin=row.get("plugin", ""), plugin_version=row.get("plugin_version", ""),
                        label=row.get("label", sid), comment=row.get("comment", ""),
                        inputs=[i for i in row.get("inputs", "").split(";") if i], status=MISSING,
                        started_at=row.get("started_at", ""), finished_at=row.get("finished_at", ""),
                        exit_code=int(code) if code.lstrip("-").isdigit() else None,
                        app_version=row.get("app_version", ""), rerun_of=row.get("rerun_of", ""))
            self.steps[sid] = step
            self._save_tomb(step)
            self.dirty = True

    def step_dir(self, step: Step) -> Path:
        return self.ws.root / step.folder

    def _taken_ids(self) -> set[str]:
        ids = set(self.steps)
        ids.update(i for c in self.ws.root.iterdir()
                   if c.is_dir() and (i := stepid.parse_folder_name(c.name)))
        return ids

    # ---- creating and updating
    def create(self, plugin_id: str, plugin_version: str, label: str = "", comment: str = "",
               params: dict | None = None, rerun_of: str = "", inputs: list[str] | None = None,
               input_files: list[dict] | None = None) -> Step:
        """Allocate an ID, create the step folder and write its sidecar (status: running)."""
        when = datetime.now(timezone.utc)
        sid, taken = stepid.from_datetime(when), self._taken_ids()
        for _ in range(500):
            if sid in taken:
                sid = stepid.next_id(sid)
                continue
            folder = stepid.folder_name(sid, label, plugin_id)
            try:
                (self.ws.root / folder).mkdir()               # atomic claim of the name
            except FileExistsError:
                sid = stepid.next_id(sid)
                continue
            except OSError as e:
                raise StepError(f"Cannot create the step folder: {e}") from e
            break
        else:
            raise StepError("No free step ID found")
        step = Step(id=sid, folder=folder, plugin=plugin_id, plugin_version=plugin_version,
                    label=(label or "").strip() or plugin_id, comment=(comment or "").strip(),
                    started_at=iso_utc(when), app_version=__version__, pid=os.getpid(),
                    host=socket.gethostname(), rerun_of=rerun_of, params=params or {},
                    inputs=list(inputs or []), input_files=list(input_files or []))
        self.save(step)
        return step

    def save(self, step: Step) -> None:
        """Write the sidecar (atomic) and mark the TSV index as out of date."""
        if step.folder:
            _write_atomic(self.step_dir(step) / SIDECAR,
                          json.dumps(step.to_json(), indent=2, ensure_ascii=False))
        else:
            self._save_tomb(step)
        self.steps[step.id] = step
        self.dirty = True

    def _detect_outputs(self, step: Step) -> None:
        d = self.step_dir(step)
        report = d / f"{step.plugin}_report.html"
        step.html_report = f"{step.folder}/{report.name}" if report.is_file() else ""
        step.data_folder = f"{step.folder}/{DATA_DIR}" if _non_empty(d / DATA_DIR) else ""
        step.report_folder = f"{step.folder}/{REPORTS_DIR}" if _non_empty(d / REPORTS_DIR) else ""

    def finish(self, step: Step, exit_code: int, status: str) -> Step:
        """Close a step: exit code, end time and the outputs found by convention."""
        step.exit_code, step.status = exit_code, status
        step.finished_at, step.pid = iso_utc(), None
        self._detect_outputs(step)
        self.save(step)
        return step

    # ---- reconcile, repair, delete
    def reconcile(self, active: set[str] | frozenset = frozenset()) -> dict[str, list[str]]:
        """Steps still 'running' that no process owns (crash, power loss) become 'interrupted'."""
        report = {"interrupted": [], "missing": [s.id for s in self.steps.values() if s.status == MISSING]}
        me = socket.gethostname()
        for s in list(self.steps.values()):
            if s.status != RUNNING or s.id in active or not s.folder:
                continue
            if s.host == me and s.pid and s.pid != os.getpid() and pid_alive(s.pid):
                continue                                   # still alive in another process
            s.status, s.finished_at, s.pid = INTERRUPTED, iso_utc(), None
            self._detect_outputs(s)
            self.save(s)
            report["interrupted"].append(s.id)
        return report

    def repair(self, active: set[str] | frozenset = frozenset()) -> dict[str, list[str]]:
        """Rebuild from the sidecars: adopt step folders without a readable sidecar, close dead runs,
        report inconsistent lineage. The caller rewrites steps.tsv."""
        self.load()
        adopted = []
        for child in sorted(self.ws.root.iterdir()):
            sid = stepid.parse_folder_name(child.name) if child.is_dir() else None
            if not sid or sid in self.steps:
                continue
            side = child / SIDECAR
            if side.is_file():                             # unreadable: keep a copy, never destroy it
                shutil.copy2(side, child / (SIDECAR + ".bak"))
            suffix = child.name[len(sid) + 1:]
            step = Step(id=sid, folder=child.name, plugin=suffix, plugin_version="", label=suffix or sid,
                        status=UNKNOWN, started_at=iso_utc(stepid.to_datetime(sid)))
            self._detect_outputs(step)
            self.save(step)
            adopted.append(sid)
        report = self.reconcile(active)
        report["adopted"] = adopted
        report["lineage_issues"] = [f"{s.id} lists {p} as input" for s in self.steps.values()
                                    for p in s.inputs if p >= s.id or p not in self.steps]
        self.dirty = True
        return report

    def delete(self, sid: str) -> None:
        """Send the step folder to the Recycle Bin (or .cytoflow/trash) and keep a tombstone row."""
        step = self.steps[sid]
        if step.status == RUNNING:
            raise StepError("A running step cannot be deleted")
        d = self.step_dir(step)
        if step.folder and d.is_dir():
            try:
                from send2trash import send2trash
                send2trash(str(d))
            except Exception:  # noqa: BLE001  (module missing or no trash on this system)
                trash = self.ws.root / MARKER_DIR / "trash"
                trash.mkdir(exist_ok=True)
                shutil.move(str(d), str(trash / step.folder))
        step.status, step.deleted_at, step.folder = DELETED, iso_utc(), ""
        step.pid, step.html_report, step.data_folder, step.report_folder = None, "", "", ""
        self.save(step)

    # ---- lineage
    def parents(self, sid: str) -> list[str]:
        step = self.steps.get(sid)
        return [p for p in (step.inputs if step else []) if p in self.steps]

    def children(self, sid: str) -> list[str]:
        return sorted(s.id for s in self.steps.values() if sid in s.inputs)

    def _walk(self, sid: str, nxt) -> list[str]:
        seen, todo = [], list(nxt(sid))
        while todo:
            cur = todo.pop(0)
            if cur not in seen and cur != sid:
                seen.append(cur)
                todo.extend(nxt(cur))
        return sorted(seen)

    def ancestors(self, sid: str) -> list[str]:
        return self._walk(sid, self.parents)

    def descendants(self, sid: str) -> list[str]:
        return self._walk(sid, self.children)

    def set_comment(self, sid: str, text: str) -> None:
        step = self.steps[sid]
        step.comment = text.strip()
        self.save(step)

    # ---- the TSV index
    def write_tsv(self) -> None:
        """Rewrite steps.tsv (UTF-8 with BOM). Raises OSError if the file cannot be replaced."""
        lines = ["\t".join(COLUMNS)]
        for step in sorted(self.steps.values(), key=lambda s: s.id):
            row = step.row()
            lines.append("\t".join(row[c] for c in COLUMNS))
        _write_atomic(self.ws.registry_path, "\n".join(lines) + "\n", encoding="utf-8-sig")
        self.dirty = False
