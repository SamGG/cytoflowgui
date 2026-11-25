"""? > Plugins > Create a plugin: asks a few questions and writes the skeleton of a new plugin
into the plugins folder (the work is done by services/scaffold.py)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout,
)

from .registry import discover
from .services import scaffold
from .services.scaffold import PluginSpec, ScaffoldError
from .services.settings import plugins_dir

INPUT_LABELS = {
    "none": "No input file",
    "fcs_file": "One FCS file",
    "fcs_dir": "A folder of FCS files",
    "rds": "An RDS file",
    "table": "A table (CSV, TSV, XLSX)",
}
DEFAULT_CATEGORY = "Custom"


class NewPluginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create a plugin")
        self.setMinimumWidth(520)
        self.folder = plugins_dir()
        manifests = discover(self.folder)
        self.taken_ids = {m.id for m in manifests}

        self.name = QLineEdit()
        self.name.setPlaceholderText("shown in the Tools menu, e.g. Compensation check")
        self.id = QLineEdit()
        self.id.setPlaceholderText("folder and file name, e.g. compensation_check")
        self._id_touched = False
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(sorted({m.category for m in manifests if m.error is None}, key=str.lower))
        self.category.setCurrentText(DEFAULT_CATEGORY)
        self.tooltip = QLineEdit()
        self.tooltip.setPlaceholderText("one line, shown when pointing at the tool")
        self.input = QComboBox()
        for key, label in INPUT_LABELS.items():
            self.input.addItem(label, key)

        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet("color: gray")
        where = QLabel(f"Created in: {self.folder}")
        where.setWordWrap(True)
        where.setStyleSheet("color: gray")
        notes = [where]
        if scaffold.in_git_checkout(self.folder):
            git = QLabel("This plugins folder is inside a git checkout (development): the new plugin "
                         "will show up there as an untracked folder.")
            git.setWordWrap(True)
            notes.append(git)

        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("Id:", self.id)
        form.addRow("Category:", self.category)
        form.addRow("Description:", self.tooltip)
        form.addRow("Input:", self.input)
        self.ok, cancel = QPushButton("Create"), QPushButton("Cancel")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.problem)
        for n in notes:
            lay.addWidget(n)
        lay.addLayout(row)

        self.name.textChanged.connect(self._name_changed)
        self.id.textEdited.connect(self._id_edited)
        for w in (self.name, self.id, self.tooltip):
            w.textChanged.connect(self._check)
        self.category.currentTextChanged.connect(self._check)
        self._check()

    def spec(self) -> PluginSpec:
        return PluginSpec(self.id.text().strip(), self.name.text().strip(), self.category.currentText().strip(),
                          self.tooltip.text().strip(), self.input.currentData())

    def _name_changed(self, text: str):
        if not self._id_touched:                       # the id follows the name until it is typed
            self.id.setText(scaffold.suggest_id(text))

    def _id_edited(self, _):
        self._id_touched = True

    def _check(self, *_):
        problem = scaffold.check_spec(self.spec(), self.folder, self.taken_ids)
        self.problem.setText(problem or "")
        self.ok.setEnabled(problem is None)


def create_plugin_flow(parent=None) -> Path | None:
    """Ask, then write the plugin. Returns its folder, or None when cancelled or refused."""
    dlg = NewPluginDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    try:
        folder = scaffold.create_plugin(dlg.spec(), dlg.folder, dlg.taken_ids)
    except ScaffoldError as e:
        QMessageBox.critical(parent, "Create a plugin", str(e))
        return None
    return folder
