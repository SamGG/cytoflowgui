"""The 'Create flowBunch' plugin of the plugins repository: its form and its script must agree."""
import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2] / "cytoflowplugins" / "plugins" / "flowbunch_create"

pytestmark = pytest.mark.skipif(not PLUGIN.is_dir(), reason="the plugins repository is not next to this one")


def _load():
    spec = importlib.util.spec_from_file_location("fbgui_cytometers", PLUGIN / "gui.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cytometers_of_the_form_are_the_ones_the_script_accepts():
    """The script validates the cytometer, and fb_initiate of cytoElements accepts only these."""
    mod = _load()
    rmd = (PLUGIN / "script.Rmd").read_text(encoding="utf-8")
    listed = re.search(r"cfg\$cytometer %in% c\(([^)]*)\)", rmd)
    assert listed, "the script no longer validates the cytometer"
    accepted = set(re.findall(r'"([^"]+)"', listed.group(1)))
    assert {key for _, key in mod.CYTOMETERS} == accepted == {"flow", "spectral", "mass", "transformed"}


def test_old_steps_saved_with_none_are_restored_as_transformed(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from cytoflowgui.api import ToolContext
    from cytoflowgui.manifest import read_manifest
    app = QApplication.instance() or QApplication([])
    mod = _load()
    tool = mod.FlowBunchCreateTool(read_manifest(PLUGIN), ToolContext(lambda: None, None))
    assert tool.cytometer.findData("none") < 0
    tool.apply_parameters({"cytometer": "none"})
    assert tool.cytometer.currentData() == "transformed"
    tool.apply_parameters({"cytometer": "flow"})
    assert tool.cytometer.currentData() == "flow"
    tool.apply_parameters({})                                   # an unknown value keeps the current one
    assert tool.cytometer.currentData() == "flow"
    app.processEvents()
