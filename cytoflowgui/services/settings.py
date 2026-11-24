"""Per-user settings and folders (never overwritten by app updates)."""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir
from PySide6.QtCore import QSettings

APP_NAME = "cytoflowgui"


def data_dir() -> Path:
    d = Path(user_data_dir(APP_NAME, appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def plugins_dir() -> Path:
    """Plugins folder. CYTOFLOW_PLUGINS_DIR overrides it (handy for development)."""
    env = os.environ.get("CYTOFLOW_PLUGINS_DIR")
    d = Path(env) if env else data_dir() / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings() -> QSettings:
    return QSettings(str(data_dir() / "cytoflowgui.ini"), QSettings.Format.IniFormat)
