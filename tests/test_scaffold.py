import importlib.util

import pytest

from cytoflowgui import API_VERSION, __version__
from cytoflowgui.manifest import read_manifest
from cytoflowgui.services import scaffold
from cytoflowgui.services.scaffold import INPUT_KINDS, PluginSpec, ScaffoldError


def spec(**kw):
    base = dict(id="my_tool", name="My tool", category="Demo", tooltip="Does a thing.")
    base.update(kw)
    return PluginSpec(**base)


def test_names():
    assert scaffold.class_name("my_tool") == "MyToolTool"
    assert scaffold.suggest_id("My new  Tool!") == "my_new_tool"
    assert scaffold.suggest_id("3D plot") == "p_3d_plot"


@pytest.mark.parametrize("bad", ["", "a", "My_tool", "1abc", "a-b", "a b", "x" * 41, "con", "com1"])
def test_bad_ids_are_refused(tmp_path, bad):
    assert scaffold.check_spec(spec(id=bad), tmp_path)


def test_other_problems(tmp_path):
    assert scaffold.check_spec(spec(), tmp_path) is None
    assert scaffold.check_spec(spec(name="  "), tmp_path)
    assert scaffold.check_spec(spec(tooltip="two\nlines"), tmp_path)
    assert scaffold.check_spec(spec(input_kind="video"), tmp_path)
    assert scaffold.check_spec(spec(), tmp_path, taken_ids={"my_tool"})
    (tmp_path / "my_tool").mkdir()
    assert "already exists" in scaffold.check_spec(spec(), tmp_path)


@pytest.mark.parametrize("kind", list(INPUT_KINDS))
def test_generated_plugin_is_valid(tmp_path, kind):
    folder = scaffold.create_plugin(spec(input_kind=kind), tmp_path / "plugins")
    assert sorted(p.name for p in folder.iterdir()) == ["gui.py", "help.html", "plugin.toml", "script.Rmd"]
    assert [p.name for p in folder.parent.iterdir()] == ["my_tool"]            # no temporary folder left
    for f in folder.iterdir():
        assert "{{" not in f.read_text("utf-8"), f.name
    m = read_manifest(folder)
    assert m.error is None and m.api_version == API_VERSION and m.min_app == __version__
    assert m.entry == "gui.py:MyToolTool" and m.name == "My tool" and m.category == "Demo"
    expected = INPUT_KINDS[kind]
    assert [i.param for i in m.inputs] == ([expected["param"]] if expected else [])
    if expected:
        assert m.inputs[0].kind == expected["kind"] and m.inputs[0].folder == (expected["folder"] == "true")
        assert m.inputs[0].lineage
    rmd = (folder / "script.Rmd").read_text("utf-8")
    if expected:
        assert "cfg$" + expected["param"] in rmd
    else:
        assert "input_steps" not in rmd and "NULL," in rmd
    assert "source(file.path(cfg$helpers_dir" in rmd and "footer.Rmd" in rmd
    compile((folder / "gui.py").read_text("utf-8"), "gui.py", "exec")


def test_special_characters_are_escaped(tmp_path):
    s = spec(name='Quote " and \\ back', tooltip='Say "hi" <b> & more')
    folder = scaffold.create_plugin(s, tmp_path)
    m = read_manifest(folder)
    assert m.error is None and m.name == s.name and m.tooltip == s.tooltip
    compile((folder / "gui.py").read_text("utf-8"), "gui.py", "exec")
    assert "&lt;b&gt; &amp; more" in (folder / "help.html").read_text("utf-8")


def test_never_overwrites(tmp_path):
    folder = scaffold.create_plugin(spec(), tmp_path)
    (folder / "gui.py").write_text("mine", encoding="utf-8")
    with pytest.raises(ScaffoldError):
        scaffold.create_plugin(spec(), tmp_path)
    assert (folder / "gui.py").read_text("utf-8") == "mine"
    assert [p.name for p in tmp_path.iterdir()] == ["my_tool"]


def test_failure_leaves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "FILES", {"gui.py.tmpl": "gui.py", "missing.tmpl": "x"})
    with pytest.raises(ScaffoldError):
        scaffold.create_plugin(spec(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_template_errors_are_reported():
    values, blocks = {"a": "1"}, {"input": True}
    assert scaffold.render("x {{a}}\n", values, blocks, ".py") == "x 1\n"
    with pytest.raises(ScaffoldError):
        scaffold.render("{{nope}}\n", values, blocks, ".py")
    with pytest.raises(ScaffoldError):
        scaffold.render("{{#other}}\n", values, blocks, ".py")
    with pytest.raises(ScaffoldError):
        scaffold.render("{{#input}}\nx\n", values, blocks, ".py")
    assert scaffold.render("{{#input}}\nA\n{{/input}}\n{{^input}}\nB\n{{/input}}\n", values, blocks, ".py") == "A\n"
    assert scaffold.render("{{#input}}\nA\n{{/input}}\n{{^input}}\nB\n{{/input}}\n", values, {"input": False}, ".py") == "B\n"


def test_in_git_checkout(tmp_path):
    assert not scaffold.in_git_checkout(tmp_path)
    (tmp_path / ".git").mkdir()
    assert scaffold.in_git_checkout(tmp_path / "plugins")


@pytest.mark.parametrize("kind", ["none", "fcs_dir"])
def test_generated_gui_works(tmp_path, kind):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from cytoflowgui.api import ToolContext
    app = QApplication.instance() or QApplication([])
    folder = scaffold.create_plugin(spec(input_kind=kind), tmp_path / "plugins")
    sp = importlib.util.spec_from_file_location("generated_gui", folder / "gui.py")
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    tool = mod.MyToolTool(read_manifest(folder), ToolContext(lambda: None, None))
    assert tool.supports_rerun
    assert tool.validate()                                        # nothing entered yet
    data = tmp_path / "fcs"
    data.mkdir()
    params = {"example_text": "hello", "example_flag": True}
    if kind != "none":
        params["fcs_dir"] = str(data.resolve())
    assert tool.apply_parameters(params) == []
    assert tool.validate() is None
    assert tool.collect_parameters() == params
    app.processEvents()
