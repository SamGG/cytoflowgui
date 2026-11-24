"""The plugins shipped next to the application must be usable by this application version."""
from pathlib import Path

import pytest

from cytoflowgui.manifest import read_manifest

PLUGINS = Path(__file__).resolve().parents[2] / "cytoflowplugins" / "plugins"


@pytest.mark.skipif(not PLUGINS.is_dir(), reason="plugins repository not next to the application")
def test_shipped_plugins_are_valid_for_this_version():
    folders = [p for p in PLUGINS.iterdir() if p.is_dir()]
    assert folders
    for folder in folders:
        m = read_manifest(folder)
        assert m.error is None, f"{folder.name}: {m.error}"
        if m.id == "split_fcs":
            assert [i.param for i in m.inputs] == ["fcs_file", "file_map_file", "cluster_map_file"]
