"""Update dialog (notify + confirm) and background workers."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from .registry import discover
from .services import updater
from .services.settings import plugins_dir


class FuncWorker(QThread):
    """Runs fn(log) in a thread. fn may call log('text') to report progress."""
    progress = Signal(str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn(self.progress.emit))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


def start_check(parent=None) -> FuncWorker:
    return FuncWorker(lambda log: updater.check_updates(discover(plugins_dir())), parent)


def _label(it: updater.UpdateItem) -> str:
    if it.kind == "app":
        return f"{it.name} {it.current} → {it.new}   (restart required)"
    if it.kind == "plugin-new":
        return f"{it.name} {it.new}   (new tool)"
    return f"{it.name} {it.current} → {it.new}"


class UpdateDialog(QDialog):
    def __init__(self, parent=None, result: updater.UpdateResult | None = None):
        super().__init__(parent)
        self.setWindowTitle("Updates")
        self.resize(640, 460)
        self.installed_any = False
        self.restart: tuple[Path, str] | None = None   # (wheel, version) to apply after exit
        self._worker: FuncWorker | None = None

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.items = QListWidget()
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.hide()
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(130)
        self.go = QPushButton("Update")
        self.go.setDefault(True)
        self.go.setEnabled(False)
        self.close_btn = QPushButton("Later")
        self.go.clicked.connect(self._apply)
        self.close_btn.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.close_btn)
        row.addWidget(self.go)
        lay = QVBoxLayout(self)
        for w in (self.status, self.items, self.bar, self.log):
            lay.addWidget(w)
        lay.addLayout(row)

        if result is None:
            self._check()
        else:
            self._show(result)

    # ---- checking
    def _check(self):
        self.status.setText("Checking for updates…")
        self.bar.show()
        self._worker = start_check(self)
        self._worker.result.connect(self._show)
        self._worker.failed.connect(lambda e: self._show(updater.UpdateResult(errors=[e])))
        self._worker.start()

    def _show(self, res: updater.UpdateResult):
        self.bar.hide()
        self.items.clear()
        for it in res.items:
            li = QListWidgetItem(_label(it) + (f"  — {it.blocked}" if it.blocked else ""))
            li.setData(Qt.ItemDataRole.UserRole, it)
            li.setToolTip(it.notes)
            if it.blocked:
                li.setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                li.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                li.setCheckState(Qt.CheckState.Checked)
            self.items.addItem(li)
        for e in res.errors:
            self.log.appendPlainText(f"⚠ {e}")
        usable = any(not i.blocked for i in res.items)
        if usable:
            self.status.setText("Updates are available. Choose what to install:")
        elif res.items:
            self.status.setText("Updates exist but cannot be installed right now.")
        elif res.errors:
            self.status.setText("Could not check for updates (are you offline?).")
        else:
            self.status.setText("Everything is up to date.")
        self.go.setEnabled(usable)

    # ---- applying
    def _chosen(self) -> list[updater.UpdateItem]:
        out = []
        for i in range(self.items.count()):
            li = self.items.item(i)
            if li.checkState() == Qt.CheckState.Checked and li.flags() & Qt.ItemFlag.ItemIsEnabled:
                out.append(li.data(Qt.ItemDataRole.UserRole))
        return out

    def _apply(self):
        chosen = self._chosen()
        if not chosen:
            return
        self.go.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.items.setEnabled(False)
        self.bar.show()

        def work(log):
            out, wheel, app = [], None, None
            for it in chosen:
                if it.kind == "app":
                    app = it
                    continue
                try:
                    m = updater.install_plugin(it, log)
                    out.append((it, True, f"installed {m.version}"))
                except Exception as e:  # noqa: BLE001
                    out.append((it, False, str(e)))
            if app:
                try:
                    wheel = updater.prepare_app_update(app, log)
                    out.append((app, True, "downloaded and verified"))
                except Exception as e:  # noqa: BLE001
                    out.append((app, False, str(e)))
            return out, wheel, app

        self._worker = FuncWorker(work, self)
        self._worker.progress.connect(self.log.appendPlainText)
        self._worker.result.connect(self._applied)
        self._worker.failed.connect(lambda e: self._applied(([], None, None), e))
        self._worker.start()

    def _applied(self, res, error: str | None = None):
        out, wheel, app = res
        self.bar.hide()
        self.close_btn.setText("Close")
        self.close_btn.setEnabled(True)
        if error:
            self.log.appendPlainText(f"✖ {error}")
        for it, ok, msg in out:
            self.log.appendPlainText(f"{'✔' if ok else '✖'} {it.name}: {msg}")
            if ok and it.kind != "app":
                self.installed_any = True
        if wheel and app:
            QMessageBox.information(
                self, "Restart",
                "cytoflowgui will now close, finish updating and restart by itself.")
            self.restart = (wheel, app.new)
            self.accept()
            return
        failed = [x for x in out if not x[1]]
        self.status.setText("Some updates failed. See the log below." if failed or error
                            else "Done.")

    def reject(self):
        if self._worker and self._worker.isRunning():
            return
        super().reject()

    def done(self, r):
        if self._worker is not None:
            self._worker.wait()
        super().done(r)
