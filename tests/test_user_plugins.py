import json

import pytest

from cytoflowgui.manifest import read_manifest
from cytoflowgui.services import scaffold, updater
from cytoflowgui.services.settings import plugins_dir

from helpers import make_plugin


def _index(tmp_path, monkeypatch, version="9.0.0", plugin_id="demo"):
    entry = {"id": plugin_id, "name": "Demo", "version": version, "api_version": 3, "min_app": "0.0.0",
             "url": (tmp_path / "demo.zip").as_uri(), "sha256": "0" * 64}
    f = tmp_path / "index.json"
    f.write_text(json.dumps({"schema": 1, "plugins": [entry]}), encoding="utf-8")
    monkeypatch.setenv("CYTOFLOW_PLUGINS_INDEX", f.as_uri())
    monkeypatch.setenv("CYTOFLOW_APP_INDEX", (tmp_path / "none.json").as_uri())     # unreadable: ignored
    return entry


def test_user_flag_is_read(tmp_path):
    assert read_manifest(make_plugin(tmp_path, "a", inputs="user = true\n")).user is True
    assert read_manifest(make_plugin(tmp_path, "b")).user is False
    assert read_manifest(make_plugin(tmp_path, "c", inputs='user = "yes"\n')).user is False


def test_scaffolded_plugins_are_marked_as_user_plugins(tmp_path):
    folder = scaffold.create_plugin(scaffold.PluginSpec("mine", "Mine", "Custom", "t"), tmp_path)
    m = read_manifest(folder)
    assert m.error is None and m.user is True


def test_an_update_never_targets_a_user_plugin(tmp_path, monkeypatch):
    _index(tmp_path, monkeypatch)
    mine = read_manifest(make_plugin(tmp_path / "p1", "demo", inputs="user = true\n"))
    res = updater.check_updates([mine])
    assert [(i.kind, i.blocked) for i in res.items] == [("plugin-update", updater.USER_PLUGIN_MSG)]
    official = read_manifest(make_plugin(tmp_path / "p2", "demo"))
    res = updater.check_updates([official])
    assert [(i.kind, i.blocked) for i in res.items] == [("plugin-update", None)]


def test_install_refuses_to_replace_a_user_plugin(tmp_path, monkeypatch):
    entry = _index(tmp_path, monkeypatch)
    mine = make_plugin(plugins_dir(), "demo", inputs="user = true\n")
    item = updater.UpdateItem(kind="plugin-update", id="demo", name="Demo", current="1.0.0", new=entry["version"],
                              url=entry["url"], sha256=entry["sha256"], origin=entry["url"])
    with pytest.raises(updater.UpdateError, match="you created"):
        updater.install_plugin(item)
    assert (mine / "plugin.toml").is_file() and not (updater.data_dir() / "backups" / "demo").exists()
