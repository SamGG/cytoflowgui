"""Log window of a running step."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .services.runmanager import LOG_FILE, RunManager
from .services.steps import Step

STATUS_COLORS = {"running": "gray", "ok": "#2e7d32", "failed": "#c62828", "cancelled": "#b26a00"}


class RunLogDialog(QDialog):
    """Non-modal: live output of one step, with Cancel, and shortcuts to its folder and report."""

    def __init__(self, step: Step, workspace_root: Path, runs: RunManager, parent=None):
        super().__init__(parent)
        self.step, self.root, self.runs = step, Path(workspace_root), runs
        self.setWindowTitle(f"{step.id} — {step.label}")
        self.resize(780, 440)
        self.status = QLabel()
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.cancel_btn = QPushButton("Cancel step")
        self.folder_btn = QPushButton("Open step folder")
        self.report_btn = QPushButton("Open report")
        close = QPushButton("Close")
        self.cancel_btn.clicked.connect(lambda: runs.cancel(step.id))
        self.folder_btn.clicked.connect(self._open_folder)
        self.report_btn.clicked.connect(self._open_report)
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(self.cancel_btn)
        row.addWidget(self.folder_btn)
        row.addWidget(self.report_btn)
        row.addStretch(1)
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.text, 1)
        lay.addLayout(row)
        self._refresh(step)

    def append(self, text: str) -> None:
        self.text.appendPlainText(text.rstrip("\n"))

    def on_finished(self, step: Step) -> None:
        self._refresh(step)

    def _refresh(self, step: Step) -> None:
        self.step = step
        color = STATUS_COLORS.get(step.status, "gray")
        code = "" if step.exit_code is None else f" (exit code {step.exit_code})"
        self.status.setText(f"<b style='color:{color}'>{step.status}</b>{code} — "
                            f"started {step.started_at}, log: {step.folder}/{LOG_FILE}")
        self.cancel_btn.setEnabled(step.status == "running")
        self.report_btn.setEnabled(bool(step.html_report))

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root / self.step.folder)))

    def _open_report(self):
        if self.step.html_report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root / self.step.html_report)))
