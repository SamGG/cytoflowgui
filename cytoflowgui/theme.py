"""Application appearance: light (default) or follow the system."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEMES = {"light": "Light", "system": "Follow system (may be dark)"}
DEFAULT_THEME = "light"
_original_style: list[str] = []


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
