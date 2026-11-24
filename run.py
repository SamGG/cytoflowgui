"""Simple launcher for development: python run.py
Uses the plugins in ../cytoflowplugins/plugins if that folder exists."""
import os
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
plugins = here.parent / "cytoflowplugins" / "plugins"
if plugins.is_dir():
    os.environ.setdefault("CYTOFLOW_PLUGINS_DIR", str(plugins))

from cytoflowgui.main import main  # noqa: E402

main()
