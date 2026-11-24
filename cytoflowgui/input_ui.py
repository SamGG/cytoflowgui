"""Input file widgets that know about the steps of the workspace."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from .manifest import InputSpec
from .services import lineage
from .services.steps import Step, StepRegistry, local_time
from .widgets import FilePicker

HINT_COLORS = {"info": "gray", "warn": "#b26a00"}


class StepOutputDialog(QDialog):
    """Pick a file among the outputs of the successful steps of the workspace."""

    def __init__(self, registry: StepRegistry | None, kind: str, parent=None, preselect: str | None = None,
                 folder: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Use the output of a step")
        self.resize(720, 420)
        self.registry, self.kind, self.folder = registry, kind, folder
        self.chosen: Path | None = None
        self.steps = list_steps = (lineage.steps_with_outputs(registry, kind, folder) if registry else [])
        self.step_list, self.file_list = QListWidget(), QListWidget()
        for s in list_steps:
            li = QListWidgetItem(f"{s.id}  {s.label}\n{s.plugin} — {local_time(s.started_at)}")
            li.setData(Qt.ItemDataRole.UserRole, s.id)
            self.step_list.addItem(li)
        self.note = QLabel("" if list_steps else "No successful step has matching files yet.")
        self.use_btn, cancel = QPushButton("Use this folder" if folder else "Use this file"), QPushButton("Cancel")
        self.use_btn.setDefault(True)
        self.use_btn.clicked.connect(self._accept_file)
        cancel.clicked.connect(self.reject)
        self.step_list.currentRowChanged.connect(self._fill_files)
        self.file_list.itemDoubleClicked.connect(lambda _: self._accept_file())
        self.file_list.currentRowChanged.connect(lambda _: self._sync())

        cols = QHBoxLayout()
        for title, w in (("Steps (newest first)", self.step_list),
                         ("Folders" if folder else "Files", self.file_list)):
            col = QVBoxLayout()
            col.addWidget(QLabel(f"<b>{title}</b>"))
            col.addWidget(w, 1)
            cols.addLayout(col)
        row = QHBoxLayout()
        row.addWidget(self.note, 1)
        row.addWidget(cancel)
        row.addWidget(self.use_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(cols, 1)
        lay.addLayout(row)

        rows = [i for i, s in enumerate(list_steps) if s.id == preselect]
        if list_steps:
            self.step_list.setCurrentRow(rows[0] if rows else 0)
        self._sync()

    def _fill_files(self, row: int):
        self.file_list.clear()
        if 0 <= row < len(self.steps):
            lister = lineage.list_output_folders if self.folder else lineage.list_outputs
            for rel in lister(self.registry, self.steps[row], self.kind):
                self.file_list.addItem(rel)
            if self.file_list.count():
                self.file_list.setCurrentRow(0)
        self._sync()

    def _sync(self):
        self.use_btn.setEnabled(self.file_list.currentRow() >= 0)

    def _accept_file(self):
        row, li = self.step_list.currentRow(), self.file_list.currentItem()
        if row >= 0 and li:
            self.chosen = self.registry.step_dir(self.steps[row]) / li.text()
            self.accept()


class InputPicker(FilePicker):
    """File field for an input declared in plugin.toml. Adds a hint telling where the file comes from
    (which step, or outside the workspace), a 'From step…' button for lineage inputs, and drag and drop."""

    def __init__(self, ctx, spec: InputSpec, filter_: str = "All files (*)", start_dir=None, parent=None):
        super().__init__(filter_, start_dir, parent)
        self.ctx, self.spec = ctx, spec
        self.setAcceptDrops(True)
        self.edit.setAcceptDrops(False)               # so that drops reach this widget
        if spec.lineage:
            btn = QPushButton("From step…")
            btn.setToolTip("Use a file produced by a previous step of the workspace")
            btn.clicked.connect(lambda: self.choose_from_step())
            self.row.addWidget(btn)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self._outer.addWidget(self.hint)
        self.edit.textChanged.connect(lambda _: self.refresh_hint())
        self.refresh_hint()

    # ---- helpers
    def _registry(self) -> StepRegistry | None:
        return self.ctx.registry if self.ctx else None

    def _workspace(self):
        return self.ctx.workspace if self.ctx else None

    def ref(self) -> lineage.InputRef | None:
        ws, path = self._workspace(), self.path()
        if not ws or not path:
            return None
        return lineage.resolve_input(ws.root, self._registry(), self.spec.param, path, self.spec.lineage)

    def refresh_hint(self) -> None:
        ref = self.ref()
        if ref is None:
            self.hint.setText("")
            return
        level, text = lineage.describe(ref, self._registry())
        self.hint.setText(("⚠ " if level == "warn" else "") + text)
        self.hint.setStyleSheet(f"color: {HINT_COLORS[level]}")

    def _browse(self):
        if not self.spec.folder:
            return super()._browse()
        d = QFileDialog.getExistingDirectory(self, "Select folder", self._initial_dir())
        if d:
            self.set_path(d)

    def _initial_dir(self) -> str:
        """Current folder, else the tool's remembered folder, else the latest step's output folder."""
        if self.spec.folder and self.path() and Path(self.path()).is_dir():
            return self.path()
        if self.path() and Path(self.path()).parent.is_dir():
            return str(Path(self.path()).parent)
        if self.start_dir:
            d = self.start_dir()
            if d and Path(d).is_dir():
                return d
        reg = self._registry()
        if reg and self.spec.lineage:
            for s in lineage.steps_with_outputs(reg, self.spec.kind, self.spec.folder):
                d = reg.step_dir(s) / "data"
                if d.is_dir():
                    return str(d)
        return str(Path.home())

    def set_path(self, path: str | Path) -> None:
        self.edit.setText(str(path))
        self.changed.emit(str(path))

    # ---- from a step
    def choose_from_step(self, step_id: str | None = None) -> bool:
        dlg = StepOutputDialog(self._registry(), self.spec.kind, self, preselect=step_id,
                               folder=self.spec.folder)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen:
            self.set_path(dlg.chosen)
            return True
        return False

    # ---- drag and drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not urls:
            return
        p = Path(urls[0])
        event.acceptProposedAction()
        if self.spec.folder:                          # a folder field takes a folder (or a file's folder)
            self.set_path(p if p.is_dir() else p.parent)
        elif p.is_file():
            self.set_path(p)
        elif p.is_dir():                              # a step folder: choose among its files
            ws = self._workspace()
            ref = lineage.resolve_input(ws.root, self._registry(), self.spec.param, str(p)) if ws else None
            if ref and ref.step:
                self.choose_from_step(ref.step)
