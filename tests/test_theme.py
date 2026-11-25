"""The line under the menu bar follows the application palette (theme switch)."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMainWindow

from cytoflowgui.theme import MENU_BAR_QSS, STATUS_BAR_QSS, style_menu_bar, style_status_bar


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    saved = QPalette(app.palette())
    yield app
    app.setPalette(saved)
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()


def _line_colour(app, w) -> str:
    for _ in range(3):
        app.processEvents()
    bar = w.menuBar()
    return w.grab().toImage().pixelColor(bar.width() // 2, bar.geometry().bottom()).name()


def _set_mid(app, colour: str) -> None:
    p = QPalette(app.palette())
    p.setColor(QPalette.ColorRole.Mid, QColor(colour))
    app.setPalette(p)


def test_menu_bar_line_follows_palette(app):
    w = QMainWindow()
    w.menuBar().addMenu("&File")
    w.resize(300, 200)
    style_menu_bar(w.menuBar())
    w.show()
    assert w.menuBar().styleSheet() == MENU_BAR_QSS
    for colour in ("#ff0000", "#00ff00"):
        _set_mid(app, colour)
        assert _line_colour(app, w) == colour


def test_status_bar_line_follows_palette(app):
    w = QMainWindow()
    w.menuBar().addMenu("&File")
    w.statusBar().showMessage("ready")
    w.resize(300, 200)
    style_status_bar(w.statusBar())
    w.show()
    assert w.statusBar().styleSheet() == STATUS_BAR_QSS
    for colour in ("#ff0000", "#00ff00"):
        _set_mid(app, colour)
        for _ in range(3):
            app.processEvents()
        bar = w.statusBar()
        image = w.grab().toImage()
        assert image.pixelColor(bar.width() // 2, bar.geometry().top()).name() == colour


def test_main_window_bars_are_styled(app):
    from cytoflowgui.main import MainWindow

    w = MainWindow(startup=False)
    assert w.menuBar().styleSheet() == MENU_BAR_QSS
    assert w.statusBar().styleSheet() == STATUS_BAR_QSS
