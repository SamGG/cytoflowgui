import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Settings and data folder in a temporary place, never the real ones."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "xdg"))
    # On Windows, platformdirs asks the OS (not LOCALAPPDATA) unless this override is set
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", str(tmp_path / "xdg"))
    monkeypatch.setenv("CYTOFLOW_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.delenv("RSCRIPT_PATH", raising=False)    # set by the IDE terminal; tests opt in
