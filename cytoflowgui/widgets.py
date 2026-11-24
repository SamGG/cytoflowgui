"""Reusable widgets, available to plugins."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .services.settings import plugins_dir, settings
from .theme import DEFAULT_THEME, THEMES, apply_theme


class FilePicker(QWidget):
    """Line edit + Browse. The dialog opens in the field's folder if set, else in start_dir()."""
    changed = Signal(str)

    def __init__(self, filter_: str = "All files (*)", start_dir: Callable[[], str] | None = None,
                 parent=None):
        super().__init__(parent)
        self.filter_, self.start_dir = filter_, start_dir
        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse)
        self.row = QHBoxLayout()
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.addWidget(self.edit, 1)
        self.row.addWidget(btn)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(2)
        self._outer.addLayout(self.row)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.path()))

    def path(self) -> str:
        return self.edit.text().strip()

    def _initial_dir(self) -> str:
        if self.path() and Path(self.path()).parent.is_dir():
            return str(Path(self.path()).parent)
        if self.start_dir:
            d = self.start_dir()
            if d and Path(d).is_dir():
                return d
        return str(Path.home())

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select file", self._initial_dir(), self.filter_)
        if f:
            self.edit.setText(f)
            self.changed.emit(f)


class DirPicker(FilePicker):
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select folder", self.path() or self._initial_dir())
        if d:
            self.edit.setText(d)
            self.changed.emit(d)


class HelpDialog(QDialog):
    def __init__(self, html: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Help")
        self.setModal(True)
        self.resize(640, 480)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setHtml(html)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(view)
        lay.addLayout(row)


class LogDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 420)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay = QVBoxLayout(self)
        lay.addWidget(self.text)

    def append(self, s: str):
        self.text.appendPlainText(s.rstrip("\n"))


class PreferencesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(560)
        self.rscript = FilePicker("Rscript (Rscript*);;All files (*)")
        self.rscript.edit.setText(str(settings().value("R/rscript", "") or ""))
        self.rscript.edit.setPlaceholderText("empty = detect automatically")
        self.theme = QComboBox()
        for key, label in THEMES.items():
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(str(settings().value("ui/theme", DEFAULT_THEME)))))
        form = QFormLayout()
        form.addRow("Rscript executable:", self.rscript)
        form.addRow("Appearance:", self.theme)
        form.addRow("Plugins folder:", QLabel(str(plugins_dir())))
        ok, cancel = QPushButton("OK"), QPushButton("Cancel")
        ok.setDefault(True)
        ok.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(row)

    def _save(self):
        s = settings()
        s.setValue("R/rscript", self.rscript.path())
        s.setValue("ui/theme", self.theme.currentData())
        apply_theme(self.theme.currentData())
        s.sync()
        self.accept()


class MarkerSelector(QWidget):
    """Multi-select list of markers with Copy / Paste buttons.

    Copy puts the selected markers on the clipboard, separated by tabs. Paste replaces the selection with
    the markers found in the clipboard (separated by tabs, new lines, commas or semicolons; matched
    case-insensitively on marker or channel name). Reusable by any plugin.
    """
    changed = Signal()
    CHANNEL_ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, parent=None, rows: int = 6):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)  # click toggles
        self.list.setMinimumHeight(self.list.fontMetrics().height() * rows + 12)
        self.copy_btn = QPushButton("Copy")
        self.paste_btn = QPushButton("Paste")
        self.copy_btn.setToolTip("Copy the selected markers to the clipboard (separated by tabs)")
        self.paste_btn.setToolTip("Replace the selection with the markers in the clipboard "
                                  "(separated by tabs; new lines, commas and semicolons are accepted too)")
        self.info = QLabel()
        self.info.setStyleSheet("color: gray")

        buttons = QVBoxLayout()
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.paste_btn)
        buttons.addStretch(1)
        top = QHBoxLayout()
        top.addWidget(self.list, 1)
        top.addLayout(buttons)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.info)

        self.copy_btn.clicked.connect(self.copy)
        self.paste_btn.clicked.connect(self.paste)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self._update_info()

    # ---- content
    def set_markers(self, items: Sequence[str | tuple[str, str]], keep_selection: bool = True) -> None:
        """items: marker names, or (marker, channel) pairs. Keeps the selection of markers still present."""
        keep = set(self.selected()) if keep_selection else set()
        self.list.blockSignals(True)
        self.list.clear()
        for it in items:
            marker, channel = (it, "") if isinstance(it, str) else it
            li = QListWidgetItem(marker)
            li.setData(self.CHANNEL_ROLE, channel)
            if channel and channel != marker:
                li.setToolTip(f"Channel: {channel}")
            self.list.addItem(li)
            li.setSelected(marker in keep)
        self.list.blockSignals(False)
        self._on_selection()

    def markers(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def selected(self) -> list[str]:
        return [li.text() for li in self._selected_items()]

    def selected_pairs(self) -> list[tuple[str, str]]:
        return [(li.text(), li.data(self.CHANNEL_ROLE) or li.text()) for li in self._selected_items()]

    def set_selected(self, names: Sequence[str]) -> list[str]:
        """Select the given markers; returns the names that were not found."""
        lookup = {}
        for i in range(self.list.count()):
            li = self.list.item(i)
            lookup.setdefault(li.text().lower(), li)
            ch = li.data(self.CHANNEL_ROLE)
            if ch:
                lookup.setdefault(ch.lower(), li)
        found, missing = [], []
        for n in names:
            (found if n.lower() in lookup else missing).append(lookup.get(n.lower()) or n)
        self.list.clearSelection()
        for li in found:
            li.setSelected(True)
        if found:
            self.list.scrollToItem(found[0])
        return missing

    def _selected_items(self):
        return [li for i in range(self.list.count()) if (li := self.list.item(i)).isSelected()]

    # ---- clipboard
    def copy(self) -> None:
        sel = self.selected()
        if sel:
            QGuiApplication.clipboard().setText("\t".join(sel))
            self.info.setText(f"{len(sel)} marker(s) copied")
            self.info.setToolTip("")

    def paste(self) -> None:
        names = [n.strip() for n in re.split(r"[\r\n\t,;]+", QGuiApplication.clipboard().text())]
        names = list(dict.fromkeys(n for n in names if n))
        if not names:
            self.info.setText("The clipboard holds no marker")
            return
        missing = self.set_selected(names)
        self._update_info()
        if missing:
            self.info.setText(f"{len(self.selected())} selected, {len(missing)} not found")
            self.info.setToolTip("Not found: " + ", ".join(missing))

    def _on_selection(self) -> None:
        self._update_info()
        self.changed.emit()

    def _update_info(self) -> None:
        n, total = len(self._selected_items()), self.list.count()
        self.info.setText(f"{n} of {total} selected" if total else "Load an FCS file to list its markers")
        self.info.setToolTip("")
        self.copy_btn.setEnabled(n > 0)
        self.paste_btn.setEnabled(total > 0)
