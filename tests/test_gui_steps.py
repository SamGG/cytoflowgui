import json

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QDialog

from cytoflowgui import input_ui
from cytoflowgui.api import ToolContext, ToolPlugin
from cytoflowgui.manifest import read_manifest
from cytoflowgui.services import steps
from cytoflowgui.services.runmanager import RunManager
from cytoflowgui.services.workspace import Workspace
from cytoflowgui.steps_panel import StepsPanel

from helpers import make_plugin, make_step

INPUTS = '''
[[inputs]]
param = "fcs_file"
kind = "fcs"
lineage = true

[[inputs]]
param = "map_file"
kind = "table"
'''


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()


@pytest.fixture
def env(qapp, tmp_path):
    ws = Workspace.open(Workspace.create(tmp_path / "ws").root)
    rm = RunManager()
    rm.set_workspace(ws)
    manifest = read_manifest(make_plugin(tmp_path / "pl", inputs=INPUTS))
    yield ws, rm, manifest, tmp_path
    rm.set_workspace(None)


class Demo(ToolPlugin):
    def build_ui(self):
        self.fcs = self.input_picker("fcs_file", "FCS (*.fcs)")
        self.map = self.input_picker("map_file")
        self.form.addRow("FCS:", self.fcs)
        self.form.addRow("Map:", self.map)

    def collect_parameters(self):
        return {"fcs_file": self.fcs.path(), "map_file": self.map.path()}


def tool(env):
    ws, rm, manifest, _ = env
    return Demo(manifest, ToolContext(lambda: ws, rm))


# ---------------------------------------------------------------- input picker
def test_hint_follows_the_path(env):
    ws, rm, _, tmp = env
    t = tool(env)
    a = make_step(rm.registry, "clustering", {"all.fcs": "x"})
    t.fcs.edit.setText(str(rm.registry.step_dir(a) / "data" / "all.fcs"))
    assert t.fcs.hint.text() == f"From step {a.id} (clustering)"
    (tmp / "raw.fcs").write_text("1")
    t.fcs.edit.setText(str(tmp / "raw.fcs"))
    assert t.fcs.hint.text() == "External file (outside the workspace)"
    t.fcs.edit.setText(str(tmp / "missing.fcs"))
    assert t.fcs.hint.text().startswith("⚠") and "not found" in t.fcs.hint.text()
    t.fcs.edit.setText("")
    assert t.fcs.hint.text() == ""


def test_only_lineage_inputs_get_the_step_button(env):
    t = tool(env)
    assert len(t.fcs.findChildren(input_ui.QPushButton)) == 2 and len(t.map.findChildren(input_ui.QPushButton)) == 1
    with pytest.raises(ValueError):
        t.input_picker("not_declared")


def test_from_step_dialog_and_start_folder(env, monkeypatch):
    ws, rm, _, tmp = env
    a = make_step(rm.registry, "a", {"one.fcs": "1"})
    b = make_step(rm.registry, "b", {"two.fcs": "2", "notes.txt": "n"})
    make_step(rm.registry, "bad", {"z.fcs": "z"}, status=steps.FAILED)
    t = tool(env)
    dlg = input_ui.StepOutputDialog(rm.registry, "fcs")
    assert [s.id for s in dlg.steps] == [b.id, a.id]                              # newest first, no failed step
    assert [dlg.file_list.item(i).text() for i in range(dlg.file_list.count())] == ["data/two.fcs"]
    dlg.step_list.setCurrentRow(1)
    assert dlg.file_list.item(0).text() == "data/one.fcs"
    dlg.use_btn.click()
    assert dlg.chosen == rm.registry.step_dir(a) / "data" / "one.fcs"
    # the tool field: choose through the dialog, and 'use as input' for a given step
    def fake_exec(self):                                                          # the user picks b's file
        self.chosen = rm.registry.step_dir(b) / "data" / "two.fcs"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(input_ui.StepOutputDialog, "exec", fake_exec)
    assert t.use_step_as_input(b.id)
    assert t.fcs.path().endswith("two.fcs") and "From step" in t.fcs.hint.text()
    # start folder: current path, else the tool's remembered folder, else the latest step's data folder
    t.fcs.edit.setText("")
    assert t.fcs._initial_dir() == str(rm.registry.step_dir(b) / "data")
    t.fcs.start_dir = lambda: str(tmp)
    assert t.fcs._initial_dir() == str(tmp)
    t.fcs.edit.setText(str(tmp / "pl" / "x.fcs"))
    assert t.fcs._initial_dir() == str(tmp / "pl")


def test_drop_a_file_and_a_step_folder(env, monkeypatch):
    ws, rm, _, tmp = env
    a = make_step(rm.registry, "a", {"one.fcs": "1"})
    (tmp / "raw.fcs").write_text("1")
    t = tool(env)

    def drop(path):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        t.fcs.dropEvent(QDropEvent(QPointF(1, 1), Qt.DropAction.CopyAction, mime,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    drop(tmp / "raw.fcs")
    assert t.fcs.path() == str(tmp / "raw.fcs")
    opened = []
    monkeypatch.setattr(t.fcs, "choose_from_step", lambda sid=None: opened.append(sid))
    drop(rm.registry.step_dir(a))                                                  # a step folder
    drop(tmp)                                                                      # not a step: ignored
    assert opened == [a.id]


def test_run_warns_about_a_failed_parent(env):
    ws, rm, _, _ = env
    bad = make_step(rm.registry, "bad", {"z.fcs": "z"}, status=steps.FAILED)
    t = tool(env)
    t.fcs.edit.setText(str(rm.registry.step_dir(bad) / "data" / "z.fcs"))
    assert t._input_warnings() and "failed" in t._input_warnings()[0]


# ---------------------------------------------------------------- steps panel
def build_history(rm):
    a = make_step(rm.registry, "clustering", {"all.fcs": "x"}, plugin="cluster")
    b = make_step(rm.registry, "split T", {"t.fcs": "x"}, inputs=[a.id])
    c = make_step(rm.registry, "split B", {"b.fcs": "x"}, inputs=[a.id], status=steps.FAILED)
    d = make_step(rm.registry, "report", inputs=[b.id, c.id])
    rm.registry.set_comment(b.id, "T cells only")
    return a, b, c, d


def column(panel, col):
    return [panel.tree.topLevelItem(i).text(col) for i in range(panel.tree.topLevelItemCount())]


def test_panel_lists_newest_first_and_filters(env):
    ws, rm, _, _ = env
    a, b, c, d = build_history(rm)
    panel = StepsPanel(rm)
    panel.refresh()
    assert column(panel, 0) == [d.id, c.id, b.id, a.id]
    panel.status_box.setCurrentText("failed")
    assert column(panel, 0) == [c.id]
    panel.status_box.setCurrentIndex(0)
    panel.plugin_box.setCurrentText("cluster")
    assert column(panel, 0) == [a.id]
    panel.plugin_box.setCurrentIndex(0)
    panel.search.setText("t cells")                                               # searches comments too
    assert column(panel, 0) == [b.id]
    panel.search.setText(c.id.lower())
    assert column(panel, 0) == [c.id]


def test_panel_tree_and_lineage_focus(env):
    ws, rm, _, _ = env
    a, b, c, d = build_history(rm)
    panel = StepsPanel(rm)
    panel.tree_check.setChecked(True)
    assert column(panel, 0) == [a.id]                                               # the only root
    root = panel.tree.topLevelItem(0)
    assert [root.child(i).text(0) for i in range(root.childCount())] == [c.id, b.id]   # newest first
    items = {i.text(0): i for i in panel._all_items()}
    assert items[d.id].parent() is items[b.id]                                      # under its first parent
    assert items[d.id].text(7) == f"{b.id}, {c.id}"                                 # all its inputs are listed
    panel.set_focus(b.id)                                                           # ancestors and descendants of b
    panel.tree_check.setChecked(False)
    assert sorted(column(panel, 0)) == sorted([a.id, b.id, d.id]) and not panel.banner_row.isHidden()
    panel.set_focus(None)
    assert len(column(panel, 0)) == 4


def test_panel_details_comment_and_links(env):
    ws, rm, _, _ = env
    a, b, c, d = build_history(rm)
    panel = StepsPanel(rm)
    panel.refresh()
    panel.select_step(b.id)
    assert b.id in panel.d_title.text() and panel.comment.text() == "T cells only"
    assert panel.folder_btn.isEnabled() and panel.data_btn.isEnabled() and not panel.cancel_btn.isEnabled()
    panel.comment.setText("changed in the panel")
    panel.comment.editingFinished.emit()
    assert rm.registry.steps[b.id].comment == "changed in the panel"
    side = json.loads((rm.registry.step_dir(b) / "step.json").read_text(encoding="utf-8"))
    assert side["comment"] == "changed in the panel" and side["label"] == "split T"     # the label is fixed
    assert column(panel, 6)[column(panel, 0).index(b.id)] == "changed in the panel"
    panel.select_step(d.id)
    panel.search.setText("nothing matches this")
    panel._link(f"step:{a.id}")                                                         # a link clears the filters
    assert panel._current == a.id and panel.search.text() == ""


def test_panel_read_only_blocks_comment(env, tmp_path):
    ws, rm, _, _ = env
    a = make_step(rm.registry, "a")
    ro = RunManager()
    ro.set_workspace(Workspace.open(ws.root, read_only=True))
    panel = StepsPanel(ro)
    panel.select_step(a.id)
    assert panel.comment.isReadOnly()
    ro.set_comment(a.id, "x")
    assert ro.registry.steps[a.id].comment == ""


def test_running_step_shows_elapsed_time(env):
    ws, rm, _, _ = env
    s = rm.registry.create("demo", "1.0.0", "live")
    panel = StepsPanel(rm)
    panel.refresh()
    assert column(panel, 3) == ["running"]
    panel._tick()
    assert column(panel, 5)[0].endswith("s")
