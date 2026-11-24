import json
import os
import socket
import subprocess
import sys

import pytest

from cytoflowgui.services import workspace as W


def test_create_open_close(tmp_path):
    root = tmp_path / "proj"
    ws = W.Workspace.create(root, name="My project")
    assert W.Workspace.is_workspace(root) and ws.name == "My project"
    assert W.Workspace.peek_name(root) == "My project"
    with pytest.raises(W.WorkspaceError):
        W.Workspace.create(root)
    ws = W.Workspace.open(root)
    lock = json.loads((root / ".cytoflow" / "lock.json").read_text())
    assert lock["pid"] == os.getpid() and lock["host"] == socket.gethostname()
    ws.close()
    assert not (root / ".cytoflow" / "lock.json").exists()


def test_open_not_a_workspace(tmp_path):
    with pytest.raises(W.WorkspaceError):
        W.Workspace.open(tmp_path)


def test_live_lock_is_refused_then_readonly(tmp_path):
    W.Workspace.create(tmp_path / "p")
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        info = W.LockInfo(socket.gethostname(), other.pid, "bob", "2026-11-23T10:00:00Z", "0.5.0")
        (tmp_path / "p" / ".cytoflow" / "lock.json").write_text(json.dumps(info.__dict__))
        with pytest.raises(W.WorkspaceLocked) as e:
            W.Workspace.open(tmp_path / "p")
        assert not e.value.remote and e.value.info.pid == other.pid
        ro = W.Workspace.open(tmp_path / "p", read_only=True)       # allowed, takes no lock
        assert ro.read_only
        ro.close()
        assert json.loads((tmp_path / "p" / ".cytoflow" / "lock.json").read_text())["pid"] == other.pid
    finally:
        other.kill()


def test_stale_lock_of_a_dead_process_is_taken(tmp_path):
    W.Workspace.create(tmp_path / "p")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    info = W.LockInfo(socket.gethostname(), dead.pid, "bob", "2026-11-23T10:00:00Z", "0.5.0")
    (tmp_path / "p" / ".cytoflow" / "lock.json").write_text(json.dumps(info.__dict__))
    ws = W.Workspace.open(tmp_path / "p")
    assert ws.read_lock().pid == os.getpid()
    ws.close()


def test_lock_from_another_computer_needs_take_over(tmp_path):
    W.Workspace.create(tmp_path / "p")
    info = W.LockInfo("OTHER-PC", 1234, "alice", "2026-11-23T10:00:00Z", "0.5.0")
    (tmp_path / "p" / ".cytoflow" / "lock.json").write_text(json.dumps(info.__dict__))
    with pytest.raises(W.WorkspaceLocked) as e:
        W.Workspace.open(tmp_path / "p")
    assert e.value.remote
    ws = W.Workspace.open(tmp_path / "p", take_over=True)
    assert ws.read_lock().host == socket.gethostname()
    ws.close()


def test_close_does_not_remove_a_foreign_lock(tmp_path):
    ws = W.Workspace.create(tmp_path / "p")
    ws = W.Workspace.open(tmp_path / "p")
    (tmp_path / "p" / ".cytoflow" / "lock.json").write_text(
        json.dumps(W.LockInfo("OTHER-PC", 1, "x", "t", "v").__dict__))
    ws.close()
    assert (tmp_path / "p" / ".cytoflow" / "lock.json").exists()


def test_pid_alive():
    assert W.pid_alive(os.getpid()) and not W.pid_alive(0) and not W.pid_alive(-5)


def test_recent_list(tmp_path):
    dirs = [tmp_path / f"w{i}" for i in range(12)]
    for d in dirs:
        d.mkdir()
        W.remember_workspace(d)
    recent = W.recent_workspaces()
    assert len(recent) == W.MAX_RECENT and recent[0] == str(dirs[-1].resolve())
    W.remember_workspace(dirs[5])                                  # moves to the top, no duplicate
    assert W.recent_workspaces()[0] == str(dirs[5].resolve())
    assert len(set(W.recent_workspaces())) == len(W.recent_workspaces())
    assert W.last_workspace() == str(dirs[5].resolve())
    W.forget_workspace(dirs[5])
    assert str(dirs[5].resolve()) not in W.recent_workspaces()
    W.clear_recent()
    assert W.recent_workspaces() == []
