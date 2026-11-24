import json
import os

import pytest

from cytoflowgui.services import stepid, steps
from cytoflowgui.services.workspace import Workspace


@pytest.fixture
def reg(tmp_path):
    return steps.StepRegistry(Workspace.create(tmp_path / "ws"))


def test_create_makes_folder_and_sidecar(reg):
    s = reg.create("split_fcs", "0.6.0", label="T cells", comment="first try", params={"a": 1})
    assert stepid.is_valid(s.id) and s.folder == f"{s.id}_T_cells" and s.label == "T cells"
    assert (reg.ws.root / s.folder).is_dir()
    side = json.loads((reg.ws.root / s.folder / "step.json").read_text(encoding="utf-8"))
    assert side["status"] == "running" and side["params"] == {"a": 1} and side["pid"] == os.getpid()
    assert reg.dirty


def test_default_label_is_the_plugin_id(reg):
    s = reg.create("split_fcs", "0.6.0")
    assert s.label == "split_fcs" and s.folder == f"{s.id}_split_fcs"


def test_ids_are_unique_even_in_the_same_second(reg, monkeypatch):
    monkeypatch.setattr(stepid, "from_datetime", lambda when: "AAAAAAA")
    ids = [reg.create("p", "1").id for _ in range(3)]
    assert ids == ["AAAAAAA", "AAAAAAB", "AAAAAAC"]


def test_existing_folder_with_the_same_id_is_never_reused(reg, monkeypatch):
    (reg.ws.root / "AAAAAAA_old_name").mkdir()                     # no sidecar, e.g. made by hand
    monkeypatch.setattr(stepid, "from_datetime", lambda when: "AAAAAAA")
    assert reg.create("p", "1").id == "AAAAAAB"


def test_finish_detects_outputs_by_convention(reg):
    s = reg.create("split_fcs", "0.6.0")
    d = reg.step_dir(s)
    (d / "data").mkdir(); (d / "data" / "a.fcs").write_text("x")
    (d / "reports").mkdir()                                         # empty: not reported
    (d / "split_fcs_report.html").write_text("<html/>")
    reg.finish(s, 0, steps.OK)
    assert (s.status, s.exit_code, s.pid) == ("ok", 0, None) and s.finished_at.endswith("Z")
    assert s.html_report == f"{s.folder}/split_fcs_report.html"
    assert s.data_folder == f"{s.folder}/data" and s.report_folder == ""


def test_failed_step_without_outputs(reg):
    s = reg.finish(reg.create("p", "1"), 1, steps.FAILED)
    assert s.status == "failed" and s.html_report == s.data_folder == s.report_folder == ""


def test_tsv_format(reg):
    a = reg.create("p", "1", label="first", comment="a\tb\nc")
    b = reg.create("p", "1", label="second", comment="=SUM(A1)")
    reg.finish(a, 0, steps.OK)
    reg.write_tsv()
    raw = reg.ws.registry_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                           # UTF-8 BOM
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0].split("\t") == steps.COLUMNS and len(lines) == 3
    rows = [dict(zip(steps.COLUMNS, ln.split("\t"))) for ln in lines[1:]]
    assert [r["id"] for r in rows] == sorted([a.id, b.id])           # chronological
    by_id = {r["id"]: r for r in rows}
    assert by_id[a.id]["comment"] == "a b c" and by_id[a.id]["exit_code"] == "0"
    assert by_id[b.id]["comment"] == "'=SUM(A1)" and by_id[b.id]["status"] == "running"
    assert not reg.dirty


def test_locked_tsv_loses_nothing(reg, monkeypatch):
    s = reg.create("p", "1")
    real = os.replace

    def locked(src, dst):
        if str(dst).endswith("steps.tsv"):
            raise PermissionError("open in Excel")
        return real(src, dst)

    monkeypatch.setattr(steps.os, "replace", locked)
    with pytest.raises(OSError):
        reg.write_tsv()
    assert reg.dirty and (reg.step_dir(s) / "step.json").is_file()   # the sidecar is safe
    monkeypatch.setattr(steps.os, "replace", real)
    reg.write_tsv()
    assert not reg.dirty and s.id in reg.ws.registry_path.read_text(encoding="utf-8-sig")


def test_load_rebuilds_from_sidecars(tmp_path):
    ws = Workspace.create(tmp_path / "ws")
    r1 = steps.StepRegistry(ws)
    s = r1.finish(r1.create("p", "1", label="x", comment="hello"), 0, steps.OK)
    side = r1.step_dir(s) / "step.json"
    data = json.loads(side.read_text(encoding="utf-8")); data["future_field"] = 1
    side.write_text(json.dumps(data), encoding="utf-8")              # a newer version added a key
    (ws.root / "notes").mkdir()                                      # ordinary folders are ignored
    r2 = steps.StepRegistry(ws)
    assert list(r2.steps) == [s.id] and r2.steps[s.id].comment == "hello"
    new_name = ws.root / f"{s.id}_renamed_by_hand"
    r1.step_dir(s).rename(new_name)                                  # folder renamed: the ID still finds it
    assert steps.StepRegistry(ws).steps[s.id].folder == new_name.name
