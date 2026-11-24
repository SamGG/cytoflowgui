import json
import shutil
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from cytoflowgui import API_VERSION
from cytoflowgui.manifest import read_manifest
from cytoflowgui.services import steps
from cytoflowgui.services.runmanager import RunError, RunManager
from cytoflowgui.services.workspace import Workspace

RSCRIPT = shutil.which("Rscript")
pytestmark = pytest.mark.skipif(not RSCRIPT, reason="R is not installed")

RMD = '''---
title: "test"
params:
  params_file: ""
---

```{r}
cfg <- jsonlite::read_json(params$params_file, simplifyVector = TRUE)
cat("hello from ", cfg$step_id, "\\n", sep = "", file = stderr())
if (identical(cfg$mode, "fail")) stop("boom")
if (identical(cfg$mode, "sleep")) Sys.sleep(120)
if (identical(cfg$mode, "short")) Sys.sleep(3)
dir.create(cfg$data_dir, recursive = TRUE)
writeLines("x", file.path(cfg$data_dir, "a.txt"))
dir.create(cfg$reports_dir, recursive = TRUE)
writeLines("y", file.path(cfg$reports_dir, "r.txt"))
```
'''


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def setup(qapp, tmp_path):
    plugin = tmp_path / "plugins" / "demo"
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text(
        f'id = "demo"\nname = "Demo"\ncategory = "Test"\ntooltip = "t"\nversion = "1.0.0"\n'
        f'api_version = {API_VERSION}\nentry = "gui.py:X"\nrmd = "script.Rmd"\n', encoding="utf-8")
    (plugin / "gui.py").write_text("class X: pass\n")
    (plugin / "script.Rmd").write_text(RMD, encoding="utf-8")
    ws = Workspace.create(tmp_path / "ws")
    rm = RunManager()
    rm.set_workspace(Workspace.open(tmp_path / "ws"))
    finished, outputs = [], {}
    rm.run_finished.connect(finished.append)
    rm.run_output.connect(lambda sid, t: outputs.setdefault(sid, []).append(t))
    yield rm, read_manifest(plugin), finished, outputs
    rm.shutdown()


def wait_until(cond, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_successful_step(setup):
    rm, m, finished, outputs = setup
    step = rm.start(RSCRIPT, m, {"mode": "ok"}, label="My run", comment="note")
    assert [s.id for s in rm.running()] == [step.id]
    assert wait_until(lambda: finished)
    s = finished[0]
    d = rm.registry.step_dir(s)
    assert (s.status, s.exit_code) == ("ok", 0) and s.folder == f"{s.id}_My_run"
    assert s.html_report.endswith("demo_report.html") and (rm.registry.ws.root / s.html_report).is_file()
    assert s.data_folder == f"{s.folder}/data" and s.report_folder == f"{s.folder}/reports"
    assert f"hello from {s.id}" in (d / "console.log").read_text(encoding="utf-8")
    assert f"hello from {s.id}" in "".join(outputs[s.id])
    cfg = json.loads((d / "run_parameters.json").read_text(encoding="utf-8"))
    assert cfg["step_id"] == s.id and cfg["api_version"] == API_VERSION and cfg["mode"] == "ok"
    assert cfg["step_dir"] == str(d) and cfg["data_dir"] == str(d / "data")
    assert cfg["workspace_dir"] == str(rm.registry.ws.root) and cfg["input_step_ids"] == []
    rm.flush_now()
    row = dict(zip(steps.COLUMNS, rm.registry.ws.registry_path.read_text("utf-8-sig").splitlines()[1].split("\t")))
    assert row["status"] == "ok" and row["comment"] == "note" and row["pid"] == ""


def test_failing_step_is_recorded(setup):
    rm, m, finished, _ = setup
    rm.start(RSCRIPT, m, {"mode": "fail"})
    assert wait_until(lambda: finished)
    s = finished[0]
    assert s.status == "failed" and s.exit_code != 0 and s.html_report == ""
    assert "boom" in (rm.registry.step_dir(s) / "console.log").read_text(encoding="utf-8")
    assert (rm.registry.step_dir(s) / "step.json").is_file()


def test_cancel(setup):
    rm, m, finished, _ = setup
    step = rm.start(RSCRIPT, m, {"mode": "sleep"})
    time.sleep(1.5)
    t0 = time.time()
    rm.cancel(step.id)
    assert wait_until(lambda: finished, 30) and time.time() - t0 < 20
    assert finished[0].status == "cancelled" and not rm.running()


def test_two_steps_run_at_the_same_time(setup):
    rm, m, finished, _ = setup
    a = rm.start(RSCRIPT, m, {"mode": "short"}, label="a")
    b = rm.start(RSCRIPT, m, {"mode": "short"}, label="b")
    assert a.id != b.id and len(rm.running()) == 2
    assert wait_until(lambda: len(finished) == 2)
    assert {s.status for s in finished} == {"ok"}
    rm.flush_now()
    assert len(rm.registry.ws.registry_path.read_text("utf-8-sig").splitlines()) == 3


def test_read_only_or_missing_workspace_refuses(setup, tmp_path):
    rm, m, _, _ = setup
    rm.set_workspace(Workspace.open(tmp_path / "ws", read_only=True))
    with pytest.raises(RunError):
        rm.start(RSCRIPT, m, {})
    rm.set_workspace(None)
    with pytest.raises(RunError):
        rm.start(RSCRIPT, m, {})


def test_locked_index_retries_and_keeps_going(setup, monkeypatch):
    rm, m, finished, _ = setup
    states, real = [], steps.os.replace

    def locked(src, dst):
        if str(dst).endswith("steps.tsv"):
            raise PermissionError("open in Excel")
        return real(src, dst)

    rm.registry_state.connect(lambda ok, msg: states.append(ok))
    monkeypatch.setattr(steps.os, "replace", locked)
    rm.start(RSCRIPT, m, {"mode": "ok"})
    assert wait_until(lambda: finished and False in states)          # the step still completed
    assert finished[0].status == "ok" and rm.registry.dirty
    monkeypatch.setattr(steps.os, "replace", real)
    rm.flush_now()
    assert not rm.registry.dirty and rm.registry.ws.registry_path.is_file()
