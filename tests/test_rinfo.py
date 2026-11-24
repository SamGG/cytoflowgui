"""R detection order in find_rscript(), without running R."""
import pytest

from cytoflowgui.services import rinfo
from cytoflowgui.services.settings import settings


@pytest.fixture
def fake_r(tmp_path, monkeypatch):
    """Three fake Rscript files and no R found on PATH or in the install locations."""
    files = {}
    for name in ("prefs", "env", "path"):
        f = tmp_path / name / "Rscript.exe"
        f.parent.mkdir()
        f.write_text("")
        files[name] = str(f)
    monkeypatch.setattr(rinfo.shutil, "which", lambda _: None)
    monkeypatch.setattr(rinfo, "_windows_candidates", lambda: iter(()))
    monkeypatch.setattr(rinfo, "UNIX_CANDIDATES", [])
    return files


def _set_pref(value: str) -> None:
    s = settings()
    s.setValue("R/rscript", value)
    s.sync()


def test_nothing_found(fake_r):
    assert rinfo.find_rscript() is None


def test_env_var_is_used(fake_r, monkeypatch):
    monkeypatch.setenv("RSCRIPT_PATH", fake_r["env"])
    assert rinfo.find_rscript() == fake_r["env"]


def test_env_var_quotes_and_spaces_are_stripped(fake_r, monkeypatch):
    monkeypatch.setenv("RSCRIPT_PATH", f'  "{fake_r["env"]}"  ')
    assert rinfo.find_rscript() == fake_r["env"]


def test_preferences_win_over_env_var(fake_r, monkeypatch):
    _set_pref(fake_r["prefs"])
    monkeypatch.setenv("RSCRIPT_PATH", fake_r["env"])
    assert rinfo.find_rscript() == fake_r["prefs"]


def test_invalid_preference_falls_back_to_env_var(fake_r, monkeypatch, tmp_path):
    _set_pref(str(tmp_path / "missing" / "Rscript.exe"))
    monkeypatch.setenv("RSCRIPT_PATH", fake_r["env"])
    assert rinfo.find_rscript() == fake_r["env"]


def test_env_var_wins_over_path(fake_r, monkeypatch):
    monkeypatch.setattr(rinfo.shutil, "which", lambda _: fake_r["path"])
    monkeypatch.setenv("RSCRIPT_PATH", fake_r["env"])
    assert rinfo.find_rscript() == fake_r["env"]


@pytest.mark.parametrize("bad", ["", "   ", "missing", "folder"])
def test_invalid_env_var_falls_back_to_path(fake_r, monkeypatch, tmp_path, bad):
    value = {"missing": str(tmp_path / "nope" / "Rscript.exe"), "folder": str(tmp_path)}.get(bad, bad)
    monkeypatch.setattr(rinfo.shutil, "which", lambda _: fake_r["path"])
    monkeypatch.setenv("RSCRIPT_PATH", value)
    assert rinfo.find_rscript() == fake_r["path"]
