"""Application appearance: light (default) or follow the system."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenuBar, QStatusBar, QWidget

THEMES = {"light": "Light", "system": "Follow system (may be dark)"}
DEFAULT_THEME = "light"
MENU_BAR_QSS = "QMenuBar { border-bottom: 1px solid palette(mid); }"
STATUS_BAR_QSS = "QStatusBar { border-top: 1px solid palette(mid); }"
_original_style: list[str] = []


class _StyleSheetStyler(QObject):
    """Keeps a stylesheet that uses palette() colours up to date. Qt resolves those colours only
    when the stylesheet is set, so it is set again after each change of the application palette
    (theme switch, system light/dark change). The widget itself is not notified, the application is."""

    def __init__(self, widget: QWidget, qss: str):
        super().__init__(widget)               # destroyed with the widget, which removes the filter
        self._widget, self._qss = widget, qss
        self._apply()
        QApplication.instance().installEventFilter(self)

    def _apply(self) -> None:
        self._widget.setStyleSheet("")         # setting the same text again would not re-resolve
        self._widget.setStyleSheet(self._qss)

    def eventFilter(self, obj, event) -> bool:
        if obj is QApplication.instance() and event.type() == QEvent.Type.ApplicationPaletteChange:
            QTimer.singleShot(0, self, self._apply)   # after the new palette is in place
        return False


def style_menu_bar(bar: QMenuBar) -> None:
    """Draw a line under the menu bar, following the theme."""
    _StyleSheetStyler(bar, MENU_BAR_QSS)


def style_status_bar(bar: QStatusBar) -> None:
    """Draw a line above the status bar, following the theme."""
    _StyleSheetStyler(bar, STATUS_BAR_QSS)


def _light_palette() -> QPalette:
    p, c, R, G = QPalette(), QColor, QPalette.ColorRole, QPalette.ColorGroup
    for role, col in {
        R.Window: "#f0f0f0", R.WindowText: "#000000", R.Base: "#ffffff", R.AlternateBase: "#f7f7f7",
        R.ToolTipBase: "#ffffdc", R.ToolTipText: "#000000", R.Text: "#000000", R.Button: "#f0f0f0",
        R.ButtonText: "#000000", R.BrightText: "#ff0000", R.Highlight: "#308cc6",
        R.HighlightedText: "#ffffff", R.Link: "#0b61a4", R.PlaceholderText: "#808080",
    }.items():
        p.setColor(role, c(col))
    for role in (R.Text, R.ButtonText, R.WindowText):
        p.setColor(G.Disabled, role, c("#a0a0a0"))
    return p


def _apply_fallback(mode: str) -> None:
    """Qt < 6.8: no colour-scheme API, so use the Fusion style with an explicit palette."""
    app = QApplication.instance()
    if not _original_style:
        _original_style.append(app.style().name())
    if mode == "light":
        app.setStyle("Fusion")
        app.setPalette(_light_palette())
    else:
        app.setStyle(_original_style[0])
        app.setPalette(app.style().standardPalette())


def apply_theme(mode: str) -> None:
    hints = QApplication.instance().styleHints()
    if hasattr(hints, "setColorScheme"):                  # Qt >= 6.8
        if mode == "light":
            hints.setColorScheme(Qt.ColorScheme.Light)
        elif hasattr(hints, "unsetColorScheme"):
            hints.unsetColorScheme()
        else:
            hints.setColorScheme(Qt.ColorScheme.Unknown)
    else:
        _apply_fallback(mode)
