"""Workspace chooser, 'new workspace' dialog and the open/create flows."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .services.settings import settings
from .services.workspace import (
    LockInfo, Workspace, WorkspaceError, WorkspaceLocked, forget_workspace, last_workspace,
    recent_workspaces, remember_workspace,
)
from .widgets import DirPicker

_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _valid_folder_name(name: str) -> bool:
    return bool(name) and not _BAD_NAME.search(name) and name == name.strip() and not name.endswith(".")


# ---------------------------------------------------------------- dialogs
class NewWorkspaceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New workspace")
        self.setMinimumWidth(560)
        self.parent_dir = DirPicker()
        default = str(settings().value("workspace/last_parent", "") or "")
        self.parent_dir.edit.setText(default or str(Path.home() / "Documents"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("folder name of the workspace")
        self.preview = QLabel()
        self.preview.setStyleSheet("color: gray")
        self.preview.setWordWrap(True)
        self.ok_btn, cancel = QPushButton("Create"), QPushButton("Cancel")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Parent folder:", self.parent_dir)
        form.addRow("Workspace name:", self.name)
        form.addRow("", self.preview)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.ok_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(row)
        self.parent_dir.edit.textChanged.connect(self._update)
        self.name.textChanged.connect(self._update)
        self._update()

    def path(self) -> Path:
        return Path(self.parent_dir.path()) / self.name.text().strip()

    def _problem(self) -> str | None:
        name = self.name.text().strip()
        if not name:
            return "Enter a name."
        if not _valid_folder_name(name):
            return "This name cannot be used as a folder name."
        if not Path(self.parent_dir.path()).is_dir():
            return "The parent folder does not exist."
        p = self.path()
        if p.exists() and (not p.is_dir() or any(p.iterdir())):
            return "This folder already exists and is not empty."
        return None

    def _update(self):
        problem = self._problem()
        self.preview.setText(problem or f"The workspace will be created in {self.path()}")
        self.preview.setStyleSheet(f"color: {'#c62828' if problem and self.name.text() else 'gray'}")
        self.ok_btn.setEnabled(problem is None)


class WorkspaceChooser(QDialog):
    """Startup / switch dialog. After exec(): `action` is 'open', 'new', 'browse' or None."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open a workspace")
        self.resize(620, 420)
        self.action: str | None = None
        self.path: str | None = None

        intro = QLabel("A workspace is a folder holding all the steps (analyses) of a project.\n"
                       "Open a recent one, or open or create another.")
        self.list = QListWidget()
        for p in recent_workspaces():
            exists = Path(p).is_dir()
            name = Workspace.peek_name(p) if exists else Path(p).name
            li = QListWidgetItem(f"{name}\n{p}" + ("" if exists else "\n(folder not found)"))
            li.setData(Qt.ItemDataRole.UserRole, p)
            if not exists:
                li.setFlags(Qt.ItemFlag.ItemIsSelectable)
                li.setForeground(Qt.GlobalColor.gray)
            self.list.addItem(li)
        if self.list.count():
            self.list.setCurrentRow(0)

        self.open_btn = QPushButton("Open")
        new_btn, browse_btn = QPushButton("New workspace…"), QPushButton("Open folder…")
        self.remove_btn = QPushButton("Remove from list")
        cancel = QPushButton("Cancel")
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(self._open_selected)
        new_btn.clicked.connect(lambda: self._done("new"))
        browse_btn.clicked.connect(lambda: self._done("browse"))
        self.remove_btn.clicked.connect(self._remove)
        cancel.clicked.connect(self.reject)
        self.list.itemDoubleClicked.connect(lambda _: self._open_selected())
        self.list.currentRowChanged.connect(self._sync)

        side = QVBoxLayout()
        for b in (self.open_btn, self.remove_btn, new_btn, browse_btn):
            side.addWidget(b)
        side.addStretch(1)
        side.addWidget(cancel)
        mid = QHBoxLayout()
        mid.addWidget(self.list, 1)
        mid.addLayout(side)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(mid)
        self._sync()

    def _selected(self) -> str | None:
        li = self.list.currentItem()
        return li.data(Qt.ItemDataRole.UserRole) if li else None

    def _sync(self, *_):
        p = self._selected()
        self.open_btn.setEnabled(bool(p) and Path(p).is_dir())
        self.remove_btn.setEnabled(bool(p))

    def _done(self, action: str, path: str | None = None):
        self.action, self.path = action, path
        self.accept()

    def _open_selected(self):
        p = self._selected()
        if p and Path(p).is_dir():
            self._done("open", p)

    def _remove(self):
        p = self._selected()
        if p:
            forget_workspace(p)
            self.list.takeItem(self.list.currentRow())
            self._sync()


# ---------------------------------------------------------------- questions (small functions: easy to test)
def _ask_create_here(parent, path: Path, empty: bool) -> bool:
    if empty:
        text = f"Create a workspace in this empty folder?\n\n{path}"
        default = QMessageBox.StandardButton.Yes
    else:
        text = (f"This folder is not a workspace and already contains files:\n\n{path}\n\n"
                "Use it as a workspace anyway? Steps will be added next to the existing files.")
        default = QMessageBox.StandardButton.No
    yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
    return QMessageBox.question(parent, "Workspace", text, yes | no, default) == yes


def _ask_locked(parent, info: LockInfo, remote: bool) -> str:
    """Returns 'readonly', 'takeover' or 'cancel'."""
    where = (f"on another computer ({info.host})" if remote
             else f"in another cytoflowgui window (process {info.pid})")
    text = (f"This workspace is already open {where}, by {info.user} since {info.started_at}.\n\n"
            "Opening it in two places at once could corrupt its registry.")
    box = QMessageBox(QMessageBox.Icon.Warning, "Workspace in use", text, parent=parent)
    ro = box.addButton("Open read-only", QMessageBox.ButtonRole.AcceptRole)
    take = box.addButton("Take over", QMessageBox.ButtonRole.DestructiveRole) if remote else None
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(ro)
    box.exec()
    clicked = box.clickedButton()
    return "readonly" if clicked is ro else "takeover" if (take and clicked is take) else "cancel"


# ---------------------------------------------------------------- flows
def open_path(parent, path: str | Path, interactive: bool = True) -> Workspace | None:
    """Open (or initialise) the workspace at `path`. With interactive=False nothing is ever asked:
    None is returned whenever a question would be needed."""
    path = Path(path)
    if not path.is_dir():
        if interactive:
            QMessageBox.warning(parent, "Workspace", f"Folder not found:\n{path}")
            forget_workspace(path)
        return None
    try:
        if not Workspace.is_workspace(path):
            if not interactive:
                return None
            empty = not any(path.iterdir())
            if not _ask_create_here(parent, path, empty):
                return None
            Workspace.create(path)
        try:
            ws = Workspace.open(path)
        except WorkspaceLocked as lock:
            if not interactive:
                return None
            choice = _ask_locked(parent, lock.info, lock.remote)
            if choice == "cancel":
                return None
            ws = Workspace.open(path, read_only=choice == "readonly", take_over=choice == "takeover")
    except (WorkspaceError, OSError) as e:
        if interactive:
            QMessageBox.critical(parent, "Workspace", f"Cannot open the workspace:\n{e}")
        return None
    remember_workspace(ws.root)
    return ws


def new_workspace_flow(parent) -> Workspace | None:
    dlg = NewWorkspaceDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    path = dlg.path()
    try:
        Workspace.create(path, name=path.name)
    except (WorkspaceError, OSError) as e:
        QMessageBox.critical(parent, "Workspace", f"Cannot create the workspace:\n{e}")
        return None
    s = settings()
    s.setValue("workspace/last_parent", str(path.parent))
    s.sync()
    return open_path(parent, path)


def browse_flow(parent) -> Workspace | None:
    start = last_workspace() or str(Path.home())
    folder = QFileDialog.getExistingDirectory(parent, "Open a workspace folder", start)
    return open_path(parent, folder) if folder else None


def choose_workspace(parent) -> Workspace | None:
    """Show the chooser until a workspace is opened or the user cancels."""
    while True:
        dlg = WorkspaceChooser(parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        ws = (open_path(parent, dlg.path) if dlg.action == "open"
              else new_workspace_flow(parent) if dlg.action == "new"
              else browse_flow(parent))
        if ws:
            return ws


def reopen_last(parent) -> Workspace | None:
    """Silently reopen the last workspace, if that needs no question."""
    p = last_workspace()
    return open_path(parent, p, interactive=False) if p else None
