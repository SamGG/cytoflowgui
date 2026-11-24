"""Tests must never touch the real per-user settings or data folder."""
from cytoflowgui.services import settings


def test_data_dir_is_isolated(tmp_path):
    d = settings.data_dir().resolve()
    assert d.is_relative_to(tmp_path.resolve()), f"data_dir() leaks outside the test: {d}"
    ini = settings.settings().fileName()
    assert ini.replace("\\", "/").lower().startswith(tmp_path.resolve().as_posix().lower())
