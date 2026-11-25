import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from cytoflowgui.services import scaffold
from cytoflowgui.services.settings import plugins_dir


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_dialog_follows_the_name_until_the_id_is_typed(app):
    from cytoflowgui.plugin_dialog import NewPluginDialog
    d = NewPluginDialog()
    assert not d.ok.isEnabled() and d.problem.text()
    d.name.setText("My new tool")
    assert d.id.text() == "my_new_tool"
    d.id.setText("custom_id")
    d.id.textEdited.emit("custom_id")                       # a user edit, not a program change
    d.name.setText("Another name")
    assert d.id.text() == "custom_id"


def test_dialog_checks_the_answers_and_builds_the_spec(app):
    from cytoflowgui.plugin_dialog import NewPluginDialog
    d = NewPluginDialog()
    d.name.setText("My tool")
    d.tooltip.setText("Does a thing.")
    assert d.ok.isEnabled() and d.problem.text() == ""
    d.input.setCurrentIndex(d.input.findData("fcs_dir"))
    assert d.spec() == scaffold.PluginSpec("my_tool", "My tool", "Custom", "Does a thing.", "fcs_dir")
    d.id.setText("Bad Id")
    assert not d.ok.isEnabled() and "id" in d.problem.text().lower()
    d.id.setText("my_tool")
    (plugins_dir() / "my_tool").mkdir()                     # taken after the dialog opened: refused at once
    d.tooltip.setText("Does a thing!")
    assert not d.ok.isEnabled() and "already exists" in d.problem.text()


def test_dialog_lists_the_existing_categories(app):
    from cytoflowgui.plugin_dialog import NewPluginDialog
    scaffold.create_plugin(scaffold.PluginSpec("first", "First", "Zeta", "t"), plugins_dir())
    d = NewPluginDialog()
    assert [d.category.itemText(i) for i in range(d.category.count())] == ["Zeta"]
    d.name.setText("Second")
    d.tooltip.setText("t")
    assert d.id.text() == "second" and d.ok.isEnabled()
    d.id.setText("first")
    assert not d.ok.isEnabled()


def test_plugins_submenu_in_the_help_menu(app):
    from cytoflowgui.main import MainWindow
    w = MainWindow(startup=False)
    assert [a.text() for a in w.menuBar().actions()] == ["&Workspace", "&Tools", "?"]
    help_menu = w.menuBar().actions()[-1].menu()
    plugins = next(a for a in help_menu.actions() if a.text() == "Plugins").menu()
    assert [a.text() for a in plugins.actions()] == ["Reload plugins", "Open plugins folder", "Create a plugin…"]
    assert [a.text() for a in help_menu.actions()] == [
        "Check for updates…", "Check setup…", "Preferences…", "Plugins", "About"]


def test_created_plugin_appears_in_the_tools_menu(app, monkeypatch):
    from cytoflowgui import main
    w = main.MainWindow(startup=False)
    folder = scaffold.create_plugin(scaffold.PluginSpec("demo_tool", "Demo tool", "Custom", "t"), plugins_dir())
    monkeypatch.setattr(main, "create_plugin_flow", lambda parent: folder)
    monkeypatch.setattr(main.QMessageBox, "exec", lambda self: 0)           # the information box
    w.create_plugin()
    sub = next(a for a in w.tools_menu.actions() if a.text() == "Custom").menu()
    assert [a.text() for a in sub.actions()] == ["Demo tool"] and sub.actions()[0].isEnabled()
