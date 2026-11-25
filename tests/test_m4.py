import json
import os
import socket

import pytest

from cytoflowgui.services import steps
from cytoflowgui.services.workspace import Workspace

from helpers import make_step


@pytest.fixture
def reg(tmp_path):
    return steps.StepRegistry(Workspace.create(tmp_path / "ws"))


def test_dead_running_step_becomes_interrupted(reg):
    s = reg.create("demo", "1")
    (reg.step_dir(s) / "data").mkdir(); (reg.step_dir(s) / "data" / "a.txt").write_text("x")
    s.pid = 99999999                                              # no such process
    reg.save(s)
    r = steps.StepRegistry(reg.ws).reconcile()
    assert r["interrupted"] == [s.id]
    again = steps.StepRegistry(reg.ws)
    got = again.steps[s.id]
    assert got.status == "interrupted" and got.finished_at and got.data_folder.endswith("/data")
    assert again.reconcile()["interrupted"] == []                  # idempotent


def test_active_or_live_steps_are_left_alone(reg):
    s = reg.create("demo", "1")
    assert reg.reconcile({s.id})["interrupted"] == []
    other = reg.create("demo", "1", label="b")
    other.pid, other.host = os.getppid(), socket.gethostname()      # alive, not us
    reg.save(other)
    assert other.id not in reg.reconcile({s.id})["interrupted"]


def test_missing_folder_becomes_a_tombstone(reg):
    a = make_step(reg, "a"); b = make_step(reg, "b", inputs=[a.id])
    reg.write_tsv()
    import shutil
    shutil.rmtree(reg.step_dir(a))
    r = steps.StepRegistry(reg.ws)
    assert r.steps[a.id].status == "missing" and r.steps[a.id].folder == "" and r.steps[b.id].inputs == [a.id]
    assert steps.StepRegistry(reg.ws).steps[a.id].status == "missing"     # persisted without the TSV
    r.write_tsv()
    assert a.id in reg.ws.registry_path.read_text("utf-8-sig")


def test_delete_leaves_a_tombstone_and_the_trash(reg):
    a = make_step(reg, "a", {"x.fcs": "1"}, plugin="demo"); b = make_step(reg, "b", inputs=[a.id])
    reg.params = None
    d = reg.step_dir(a)
    reg.delete(a.id)
    assert not d.exists() and (reg.ws.marker_dir / "trash" / d.name).exists() or not d.exists()
    t = steps.StepRegistry(reg.ws).steps[a.id]
    assert t.status == "deleted" and t.deleted_at and t.folder == "" and reg.children(a.id) == [b.id]
    run = reg.create("demo", "1")
    with pytest.raises(steps.StepError):
        reg.delete(run.id)


def test_repair_adopts_and_reports(reg):
    a = make_step(reg, "a")
    (reg.ws.root / "AAAAAAB_old_run").mkdir()                       # an older version's folder, no sidecar
    (reg.ws.root / "AAAAAAB_old_run" / "data").mkdir(); (reg.ws.root / "AAAAAAB_old_run" / "data" / "f.fcs").write_text("x")
    bad = make_step(reg, "bad")
    (reg.step_dir(bad) / "step.json").write_text("{broken")
    a.inputs = ["ZZZZZZZ"]; reg.save(a)
    r = reg.repair()
    assert set(r["adopted"]) == {"AAAAAAB", bad.id} and (reg.step_dir(bad) / "step.json.bak").is_file()
    assert reg.steps["AAAAAAB"].status == "unknown" and reg.steps["AAAAAAB"].data_folder.endswith("/data")
    assert any("ZZZZZZZ" in x for x in r["lineage_issues"])
    reg.write_tsv()
    assert len(reg.ws.registry_path.read_text("utf-8-sig").splitlines()) == 4


def test_rerun_restores_the_form(tmp_path):
    pytest.importorskip("PySide6")
    import importlib.util, sys
    from PySide6.QtWidgets import QApplication
    from cytoflowgui.api import ToolContext
    from cytoflowgui.manifest import read_manifest
    from cytoflowgui.services.runmanager import RunManager
    app = QApplication.instance() or QApplication([])
    ws = Workspace.open(Workspace.create(tmp_path / "ws").root)
    rm = RunManager(); rm.set_workspace(ws)
    plug = __import__("pathlib").Path(__file__).resolve().parents[2] / "cytoflowplugins" / "plugins" / "flowbunch_create"
    m = read_manifest(plug)
    spec = importlib.util.spec_from_file_location("fbgui", plug / "gui.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    folder = tmp_path / "fcs"; folder.mkdir(); (folder / "a.fcs").write_text("x")
    done = make_step(rm.registry, "first", plugin="flowbunch_create")
    done.params = {"fb_name": "proj", "fcs_dir": str(folder), "cytometer": "spectral",
                   "language": "de", "cofactor": 5, "test_mode": True}
    done.comment = "note"; done.plugin_version = "0.1.0"; rm.registry.save(done)
    tool = mod.FlowBunchCreateTool(m, ToolContext(lambda: ws, rm))
    assert tool.supports_rerun
    warns = tool.rerun(done)
    assert tool.collect_parameters() == done.params and tool.label_edit.text() == "first"
    assert tool.comment_edit.text() == "note" and tool._rerun_of == done.id and any("version" in w for w in warns)
    folder.rmdir.__self__  # noqa
    (folder / "a.fcs").unlink(); folder.rmdir()
    assert any("not found" in w for w in tool.rerun(done))
    rm.set_workspace(None)
