"""Workspace = a folder holding all the steps of a project.

It is recognised by a hidden marker folder `.cytoflow` that contains `workspace.json`
(identity) and `lock.json` (written while an application window has the workspace open, so
that a second window is warned). The list of recent workspaces is kept in the user settings.
"""
from __future__ import annotations

import getpass
import json
import os
import socket
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .settings import settings

MARKER_DIR = ".cytoflow"
WORKSPACE_FILE = "workspace.json"
LOCK_FILE = "lock.json"
REGISTRY_FILE = "steps.tsv"
SCHEMA = 1
MAX_RECENT = 10


class WorkspaceError(Exception):
    pass


@dataclass
class LockInfo:
    host: str
    pid: int
    user: str
    started_at: str
    app_version: str


class WorkspaceLocked(WorkspaceError):
    """The workspace is open elsewhere. `remote` is True when the holder is on another computer,
    where we cannot tell whether it is still running."""

    def __init__(self, info: LockInfo, remote: bool):
        super().__init__(f"Workspace in use by {info.user}@{info.host} (process {info.pid})")
        self.info, self.remote = info, remote


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pid_alive(pid: int) -> bool:
    """True if a process with this id is running on this computer."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5          # access denied: it exists
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259            # STILL_ACTIVE
    try:
        os.kill(pid, 0)                                  # never use this on Windows (it kills)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _hide(path: Path) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)   # FILE_ATTRIBUTE_HIDDEN
        except Exception:  # noqa: BLE001
            pass


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class Workspace:
    def __init__(self, root: Path, name: str, created_at: str, read_only: bool = False):
        self.root, self.name, self.created_at, self.read_only = root, name, created_at, read_only
        self._has_lock = False

    # ---- paths
    @property
    def marker_dir(self) -> Path:
        return self.root / MARKER_DIR

    @property
    def registry_path(self) -> Path:
        return self.root / REGISTRY_FILE

    # ---- identity
    @staticmethod
    def is_workspace(path: str | Path) -> bool:
        return (Path(path) / MARKER_DIR / WORKSPACE_FILE).is_file()

    @staticmethod
    def peek_name(path: str | Path) -> str:
        """Display name of a workspace folder, without opening it."""
        try:
            data = json.loads((Path(path) / MARKER_DIR / WORKSPACE_FILE).read_text(encoding="utf-8"))
            return str(data.get("name") or Path(path).name)
        except Exception:  # noqa: BLE001
            return Path(path).name

    @classmethod
    def create(cls, path: str | Path, name: str | None = None) -> "Workspace":
        """Turn a folder (existing or not) into a workspace. Not locked yet: call open() next."""
        root = Path(path)
        if cls.is_workspace(root):
            raise WorkspaceError("This folder is already a workspace")
        root.mkdir(parents=True, exist_ok=True)
        marker = root / MARKER_DIR
        marker.mkdir(exist_ok=True)
        _hide(marker)
        _write_json_atomic(marker / WORKSPACE_FILE, {
            "schema": SCHEMA, "name": name or root.name,
            "created_at": _now_iso(), "app_version": __version__})
        return cls._read(root)

    @classmethod
    def _read(cls, root: Path, read_only: bool = False) -> "Workspace":
        try:
            data = json.loads((root / MARKER_DIR / WORKSPACE_FILE).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            raise WorkspaceError(f"Not a valid workspace ({e})") from e
        if int(data.get("schema", 0)) > SCHEMA:
            raise WorkspaceError("This workspace was created by a newer version of the application")
        return cls(root.resolve(), str(data.get("name") or root.name),
                   str(data.get("created_at", "")), read_only)

    # ---- opening and locking
    @classmethod
    def open(cls, path: str | Path, read_only: bool = False, take_over: bool = False) -> "Workspace":
        root = Path(path)
        if not cls.is_workspace(root):
            raise WorkspaceError("This folder is not a workspace")
        ws = cls._read(root, read_only)
        if not read_only:
            ws._acquire(take_over)
        return ws

    @staticmethod
    def _me() -> LockInfo:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001
            user = "?"
        return LockInfo(socket.gethostname(), os.getpid(), user, _now_iso(), __version__)

    def _lock_path(self) -> Path:
        return self.marker_dir / LOCK_FILE

    def read_lock(self) -> LockInfo | None:
        try:
            return LockInfo(**json.loads(self._lock_path().read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return None

    def _acquire(self, take_over: bool) -> None:
        held = self.read_lock()
        if held and not take_over:
            if held.host == socket.gethostname():
                if held.pid != os.getpid() and pid_alive(held.pid):
                    raise WorkspaceLocked(held, remote=False)
                # our own process, or a dead one (crash): the lock is stale, take it
            else:
                raise WorkspaceLocked(held, remote=True)
        _write_json_atomic(self._lock_path(), asdict(self._me()))
        self._has_lock = True

    def close(self) -> None:
        """Release the lock (only if it is ours)."""
        if not self._has_lock:
            return
        held = self.read_lock()
        if held and held.host == socket.gethostname() and held.pid == os.getpid():
            try:
                self._lock_path().unlink()
            except OSError:
                pass
        self._has_lock = False


# ---------------------------------------------------------------- recent workspaces
def _load_list(key: str) -> list[str]:
    try:
        data = json.loads(str(settings().value(key, "[]") or "[]"))
        return [str(p) for p in data] if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save_list(key: str, items: list[str]) -> None:
    s = settings()
    s.setValue(key, json.dumps(items))
    s.sync()


def recent_workspaces() -> list[str]:
    return _load_list("workspace/recent")


def remember_workspace(path: str | Path) -> None:
    p = str(Path(path).resolve())
    items = [x for x in recent_workspaces() if not _same(x, p)]
    _save_list("workspace/recent", [p, *items][:MAX_RECENT])
    s = settings()
    s.setValue("workspace/last", p)
    s.sync()


def forget_workspace(path: str | Path) -> None:
    _save_list("workspace/recent", [x for x in recent_workspaces() if not _same(x, path)])


def clear_recent() -> None:
    _save_list("workspace/recent", [])


def last_workspace() -> str | None:
    p = str(settings().value("workspace/last", "") or "")
    return p or None
