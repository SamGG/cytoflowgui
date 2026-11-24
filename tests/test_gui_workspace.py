import json
import socket

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from cytoflowgui import workspace_ui as UI
from cytoflowgui.main import MainWindow
from cytoflowgui.services import workspace as W


@pytest.fixture(scope="session")
def app():
    import gc

    qapp = QApplication.instance() or QApplication([])
    yield qapp
    for widget in QApplication.topLevelWidgets():       # destroy windows before the application
        widget.close()
        widget.deleteLater()
    qapp.processEvents()
    gc.collect()


def test_no_workspace_disables_tools(app):
    w = MainWindow(startup=False)
    assert not w.tools_menu.isEnabled() and not w.close_act.isEnabled()
    assert w.windowTitle() == "cytoflowgui" and "No workspace" in w.ws_btn.text()


def test_open_empty_folder_creates_it(app, tmp_path, monkeypatch):
    (tmp_path / "ws").mkdir()
    monkeypatch.setattr(UI, "_ask_create_here", lambda *a: True)
    ws = UI.open_path(None, tmp_path / "ws")
    assert ws and W.Workspace.is_workspace(tmp_path / "ws")
    ws.close()


def test_non_workspace_folder_is_refused_unless_confirmed(app, tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("x")
    asked = []
    monkeypatch.setattr(UI, "_ask_create_here", lambda p, path, empty: asked.append(empty) or False)
    assert UI.open_path(None, tmp_path / "docs") is None and asked == [False]   # not empty -> warned
    assert not W.Workspace.is_workspace(tmp_path / "docs")
    assert UI.open_path(None, tmp_path / "docs", interactive=False) is None   # silent mode never asks


def test_main_window_state_and_switching(app, tmp_path):
    a, b = W.Workspace.create(tmp_path / "A", name="Project A"), W.Workspace.create(tmp_path / "B")
    w = MainWindow(startup=False)
    w.set_workspace(W.Workspace.open(tmp_path / "A"))
    assert w.windowTitle() == "Project A — cytoflowgui" and str(tmp_path / "A") in w.ws_btn.text()
    assert w.tools_menu.isEnabled() and w.close_act.isEnabled()
    assert (tmp_path / "A" / ".cytoflow" / "lock.json").exists()
    w.set_workspace(W.Workspace.open(tmp_path / "B"))                       # A released, B locked
    assert not (tmp_path / "A" / ".cytoflow" / "lock.json").exists()
    assert (tmp_path / "B" / ".cytoflow" / "lock.json").exists()
    w.close_workspace()
    assert not (tmp_path / "B" / ".cytoflow" / "lock.json").exists() and not w.tools_menu.isEnabled()


def test_read_only_is_shown(app, tmp_path):
    W.Workspace.create(tmp_path / "A")
    w = MainWindow(startup=False)
    w.set_workspace(W.Workspace.open(tmp_path / "A", read_only=True))
    assert "[read-only]" in w.windowTitle() and "[read-only]" in w.ws_btn.text()


def test_locked_workspace_asks_and_opens_read_only(app, tmp_path, monkeypatch):
    W.Workspace.create(tmp_path / "A")
    info = W.LockInfo("OTHER-PC", 1, "alice", "2026-11-23T10:00:00Z", "0.5.0")
    (tmp_path / "A" / ".cytoflow" / "lock.json").write_text(json.dumps(info.__dict__))
    monkeypatch.setattr(UI, "_ask_locked", lambda p, i, remote: "readonly" if remote else "cancel")
    ws = UI.open_path(None, tmp_path / "A")
    assert ws and ws.read_only
    assert UI.open_path(None, tmp_path / "A", interactive=False) is None


def test_recent_menu_and_chooser(app, tmp_path):
    W.Workspace.create(tmp_path / "A", name="Project A")
    W.remember_workspace(tmp_path / "A")
    gone = tmp_path / "gone"
    gone.mkdir(); W.remember_workspace(gone); gone.rmdir()
    w = MainWindow(startup=False)
    w._fill_recent_menu()
    texts = [(a.text(), a.isEnabled()) for a in w.recent_menu.actions() if a.text()]
    assert texts[0][1] is False and "gone" in texts[0][0] and texts[1][1] is True
    dlg = UI.WorkspaceChooser()
    assert dlg.list.count() == 2 and not dlg.open_btn.isEnabled()           # first row = missing folder
    dlg.list.setCurrentRow(1)
    assert dlg.open_btn.isEnabled()
    dlg.open_btn.click()
    assert dlg.action == "open" and dlg.path == str(tmp_path / "A")


def test_new_workspace_dialog_validation(app, tmp_path):
    (tmp_path / "full").mkdir(); (tmp_path / "full" / "x").write_text("1")
    d = UI.NewWorkspaceDialog()
    d.parent_dir.edit.setText(str(tmp_path))
    assert not d.ok_btn.isEnabled()                                         # no name yet
    d.name.setText("bad:name"); assert not d.ok_btn.isEnabled()
    d.name.setText("full"); assert not d.ok_btn.isEnabled() and "not empty" in d.preview.text()
    d.name.setText("fresh"); assert d.ok_btn.isEnabled() and d.path() == tmp_path / "fresh"
