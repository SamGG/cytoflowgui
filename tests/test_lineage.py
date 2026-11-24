import json
import shutil
import time

import pytest

from cytoflowgui import manifest as M
from cytoflowgui.manifest import read_manifest
from cytoflowgui.services import lineage, steps
from cytoflowgui.services.workspace import Workspace

from helpers import make_plugin, make_step


@pytest.fixture
def reg(tmp_path):
    return steps.StepRegistry(Workspace.create(tmp_path / "ws"))


# ---------------------------------------------------------------- manifest
INPUTS = '''
[[inputs]]
param = "fcs_file"
kind = "FCS"
lineage = true
label = "Input"

[[inputs]]
param = "map_file"
kind = "table"
'''


def test_manifest_inputs(tmp_path):
    m = read_manifest(make_plugin(tmp_path, inputs=INPUTS))
    assert m.error is None and [(i.param, i.kind, i.lineage) for i in m.inputs] == [
        ("fcs_file", "fcs", True), ("map_file", "table", False)]
    assert read_manifest(make_plugin(tmp_path, "b", '[[inputs]]\nkind = "fcs"\n')).error
    assert "kind must be" in read_manifest(make_plugin(tmp_path, "c", '[[inputs]]\nparam = "x"\nkind = "jpg"\n')).error
    assert read_manifest(make_plugin(tmp_path, "d")).inputs == []


# ---------------------------------------------------------------- resolving
def test_external_file_gets_a_fingerprint(reg, tmp_path):
    f = tmp_path / "outside" / "a.fcs"
    f.parent.mkdir()
    f.write_text("12345")
    ref = lineage.resolve_input(reg.ws.root, reg, "fcs_file", str(f), True)
    assert not ref.inside and ref.step == "" and ref.size == 5 and ref.mtime.endswith("Z")
    assert lineage.describe(ref, reg) == ("info", "External file (outside the workspace)")


def test_file_inside_a_step(reg):
    s = make_step(reg, "clustering", {"all.fcs": "x"})
    ref = lineage.resolve_input(reg.ws.root, reg, "fcs_file", str(reg.step_dir(s) / "data" / "all.fcs"), True)
    assert ref.inside and ref.step == s.id and ref.rel == f"{s.folder}/data/all.fcs" and not ref.orphan
    assert lineage.describe(ref, reg) == ("info", f"From step {s.id} (clustering)")


def test_step_that_failed_or_runs_gives_a_warning(reg):
    bad = make_step(reg, "bad", {"a.fcs": "x"}, status=steps.FAILED)
    ref = lineage.resolve_input(reg.ws.root, reg, "p", str(reg.step_dir(bad) / "data" / "a.fcs"))
    assert lineage.describe(ref, reg) == ("warn", f"From step {bad.id} (bad), which failed")
    run = reg.create("demo", "1")
    (reg.step_dir(run) / "data").mkdir()
    (reg.step_dir(run) / "data" / "b.fcs").write_text("x")
    ref = lineage.resolve_input(reg.ws.root, reg, "p", str(reg.step_dir(run) / "data" / "b.fcs"))
    assert "still running" in lineage.describe(ref, reg)[1]


def test_orphan_workspace_file_and_missing(reg):
    (reg.ws.root / "AAAAAAA_old").mkdir()
    (reg.ws.root / "AAAAAAA_old" / "x.fcs").write_text("x")
    (reg.ws.root / "notes").mkdir()
    (reg.ws.root / "notes" / "y.fcs").write_text("y")
    orphan = lineage.resolve_input(reg.ws.root, reg, "p", str(reg.ws.root / "AAAAAAA_old" / "x.fcs"))
    assert orphan.orphan and orphan.step == "" and lineage.describe(orphan, reg)[0] == "warn"
    plain = lineage.resolve_input(reg.ws.root, reg, "p", str(reg.ws.root / "notes" / "y.fcs"))
    assert plain.inside and not plain.orphan and lineage.describe(plain, reg)[1].startswith("In the workspace")
    gone = lineage.resolve_input(reg.ws.root, reg, "p", str(reg.ws.root / "notes" / "nope.fcs"))
    assert lineage.describe(gone, reg) == ("warn", "File not found")


# ---------------------------------------------------------------- outputs and graph
def test_outputs_by_kind(reg):
    a = make_step(reg, "a", {"x.fcs": "1", "sub/y.fcs": "2", "t.rds": "3", "reports/m.xlsx": "4"})
    make_step(reg, "failed", {"z.fcs": "5"}, status=steps.FAILED)
    assert lineage.list_outputs(reg, a, "fcs") == ["data/sub/y.fcs", "data/x.fcs"]
    assert lineage.list_outputs(reg, a, "rds") == ["data/t.rds"]
    assert lineage.list_outputs(reg, a, "table") == ["reports/m.xlsx"]
    assert len(lineage.list_outputs(reg, a, "any")) == 4
    assert [s.id for s in lineage.steps_with_outputs(reg, "fcs")] == [a.id]       # failed steps are left out


def test_graph(reg):
    a = make_step(reg, "a"); b = make_step(reg, "b", inputs=[a.id]); c = make_step(reg, "c", inputs=[b.id])
    d = make_step(reg, "d", inputs=[a.id, c.id])
    assert reg.parents(d.id) == [a.id, c.id] and reg.children(a.id) == sorted([b.id, d.id])
    assert reg.ancestors(d.id) == sorted([a.id, b.id, c.id]) and reg.descendants(a.id) == sorted([b.id, c.id, d.id])
    a.inputs = [d.id]                                   # a corrupted cycle must not hang
    assert d.id not in reg.descendants(d.id) and set(reg.ancestors(d.id)) == {a.id, b.id, c.id}


# ---------------------------------------------------------------- real runs
RSCRIPT = shutil.which("Rscript")
RMD = '---\ntitle: "t"\nparams:\n  params_file: ""\n---\n\n```{r}\ncfg <- jsonlite::read_json(params$params_file, simplifyVector = TRUE)\n' \
      'dir.create(cfg$data_dir, recursive = TRUE)\nwriteLines("x", file.path(cfg$data_dir, "out.txt"))\n' \
      'dir.create(cfg$reports_dir, recursive = TRUE)\nwriteLines("y", file.path(cfg$reports_dir, "side.txt"))\n```\n'


@pytest.mark.skipif(not RSCRIPT, reason="R is not installed")
def test_a_second_step_records_its_inputs(tmp_path):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication
    from cytoflowgui.services.runmanager import RunManager
    QApplication.instance() or QApplication([])
    m = read_manifest(make_plugin(tmp_path / "pl", inputs=INPUTS, rmd=RMD))
    ws = Workspace.create(tmp_path / "ws")
    rm = RunManager()
    rm.set_workspace(Workspace.open(tmp_path / "ws"))
    done = []
    rm.run_finished.connect(done.append)

    def wait(n):
        end = time.time() + 120
        while len(done) < n and time.time() < end:
            QCoreApplication.processEvents()
            time.sleep(0.05)
        assert len(done) == n

    outside = tmp_path / "raw.fcs"
    outside.write_text("abc")
    first = rm.start(RSCRIPT, m, {"fcs_file": str(outside)})
    wait(1)
    f1 = rm.registry.steps[first.id]
    assert f1.status == "ok" and f1.inputs == [] and f1.input_files[0]["size"] == 3 and not f1.input_files[0]["inside"]

    out = rm.registry.step_dir(f1) / "data" / "out.txt"
    side = rm.registry.step_dir(f1) / "reports" / "side.txt"
    second = rm.start(RSCRIPT, m, {"fcs_file": str(out), "map_file": str(side)})
    wait(2)
    s2 = rm.registry.steps[second.id]
    assert s2.status == "ok" and s2.inputs == [first.id]                         # one parent, listed once
    assert [(f["param"], f["step"], f["rel"]) for f in s2.input_files] == [
        ("fcs_file", first.id, f"{f1.folder}/data/out.txt"), ("map_file", first.id, f"{f1.folder}/reports/side.txt")]
    cfg = json.loads((rm.registry.step_dir(s2) / "run_parameters.json").read_text(encoding="utf-8"))
    assert cfg["input_step_ids"] == [first.id] and cfg["input_steps"] == {"fcs_file": first.id, "map_file": first.id}
    side_json = json.loads((rm.registry.step_dir(s2) / "step.json").read_text(encoding="utf-8"))
    assert side_json["inputs"] == [first.id] and side_json["input_files"][0]["lineage"] is True
    rm.flush_now()
    rows = [dict(zip(steps.COLUMNS, ln.split("\t"))) for ln in rm.registry.ws.registry_path.read_text("utf-8-sig").splitlines()[1:]]
    assert [r["inputs"] for r in rows] == ["", first.id]
    rm.shutdown()
