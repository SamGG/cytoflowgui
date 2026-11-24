"""Setup check / first-run wizard: R, R packages (CRAN + Bioconductor), Pandoc."""
from __future__ import annotations

import sys

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from .registry import discover
from .services.rinfo import BASE_R_PACKAGES, SetupStatus, check_setup
from .services.runner import RScriptRunner
from .services.settings import plugins_dir, settings

R_URL = ("https://cran.r-project.org/bin/windows/base/" if sys.platform == "win32"
         else "https://cran.r-project.org/")
PANDOC_URL = "https://pandoc.org/installing.html"

# Installs go to the user's own R library, so no administrator rights are needed.
_LIB = ("l<-Sys.getenv('R_LIBS_USER');dir.create(l,recursive=TRUE,showWarnings=FALSE);"
        ".libPaths(c(l,.libPaths()));")
_REPO = "repos='https://cloud.r-project.org'"
CRAN_EXPR = _LIB + f"install.packages(commandArgs(TRUE),lib=l,{_REPO})"
BIOC_EXPR = (_LIB + f"if(!requireNamespace('BiocManager',quietly=TRUE))"
             f"install.packages('BiocManager',lib=l,{_REPO});"
             "BiocManager::install(commandArgs(TRUE),lib=l,ask=FALSE,update=FALSE)")

COLORS = {"ok": "#2e7d32", "bad": "#c62828", "wait": "#757575", "unknown": "#757575"}
ICONS = {"ok": "✔", "bad": "✖", "wait": "…", "unknown": "–"}


def _dedupe(seq):
    return list(dict.fromkeys(seq))


class CheckWorker(QThread):
    done = Signal(object)

    def __init__(self, cran, bioc, parent=None):
        super().__init__(parent)
        self.cran, self.bioc = cran, bioc

    def run(self):
        self.done.emit(check_setup(self.cran, self.bioc))


def _hbox(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        lay.addWidget(x)
    return w


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Setup check")
        self.resize(720, 520)
        self._status: SetupStatus | None = None
        self._worker: CheckWorker | None = None
        self._queue: list[tuple[str, list[str]]] = []
        self.runner = RScriptRunner(self)
        self.runner.output.connect(self._append)
        self.runner.finished.connect(self._install_finished)

        self.browse_btn = QPushButton("Browse…")
        self.dl_r_btn = QPushButton("Download R…")
        self.install_btn = QPushButton("Install missing packages")
        self.dl_pandoc_btn = QPushButton("Download Pandoc…")
        self.recheck_btn = QPushButton("Re-check")
        self.close_btn = QPushButton("Close")
        self.browse_btn.clicked.connect(self._browse_r)
        self.dl_r_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(R_URL)))
        self.dl_pandoc_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(PANDOC_URL)))
        self.install_btn.clicked.connect(self._install)
        self.recheck_btn.clicked.connect(self._recheck)
        self.close_btn.clicked.connect(self.accept)

        self.rows: dict[str, tuple[QLabel, QLabel]] = {}
        grid = QGridLayout()
        for i, (key, title, buttons) in enumerate((
                ("r", "R", (self.browse_btn, self.dl_r_btn)),
                ("pkg", "R packages", (self.install_btn,)),
                ("pandoc", "Pandoc", (self.dl_pandoc_btn,)))):
            icon, text = QLabel("…"), QLabel()
            icon.setFixedWidth(20)
            text.setWordWrap(True)
            grid.addWidget(icon, i, 0)
            grid.addWidget(QLabel(f"<b>{title}</b>"), i, 1)
            grid.addWidget(text, i, 2)
            grid.addWidget(_hbox(*buttons), i, 3)
            self.rows[key] = (icon, text)
        grid.setColumnStretch(2, 1)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.recheck_btn)
        bottom.addWidget(self.close_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("cytoflowgui needs R, a few R packages and Pandoc to run its tools."))
        lay.addLayout(grid)
        lay.addWidget(self.log, 1)
        lay.addLayout(bottom)
        self._recheck()

    # ---- helpers
    def _set(self, key: str, state: str, msg: str):
        icon, text = self.rows[key]
        icon.setText(ICONS[state])
        icon.setStyleSheet(f"color:{COLORS[state]};font-weight:bold")
        text.setText(msg)

    def _append(self, s: str):
        self.log.appendPlainText(s.rstrip("\n"))

    def _busy(self, busy: bool):
        for b in (self.browse_btn, self.dl_r_btn, self.install_btn, self.dl_pandoc_btn,
                  self.recheck_btn, self.close_btn):
            b.setEnabled(not busy)

    def _required(self) -> tuple[list[str], list[str]]:
        cran, bioc = list(BASE_R_PACKAGES), []
        for m in discover(plugins_dir()):
            if m.error is None:
                cran += m.requires_r
                bioc += m.requires_bioc
        return _dedupe(cran), _dedupe(bioc)

    # ---- checking
    def _recheck(self):
        self._busy(True)
        for key in self.rows:
            self._set(key, "wait", "Checking…")
        self._worker = CheckWorker(*self._required(), parent=self)
        self._worker.done.connect(self._on_status)
        self._worker.start()

    def _on_status(self, st: SetupStatus):
        self._status = st
        self._busy(False)
        s = settings()
        if st.rscript and not str(s.value("R/rscript", "") or ""):
            s.setValue("R/rscript", st.rscript)
            s.sync()

        if not st.rscript:
            self._set("r", "bad", "R was not found. Install R, then click Re-check "
                                  "(or use Browse… to point to Rscript).")
            self._set("pkg", "unknown", "Requires R.")
            self._set("pandoc", "unknown", "Requires R.")
            self.install_btn.setEnabled(False)
            return
        if st.error or not st.r_version:
            self._set("r", "bad", f"R found at {st.rscript} but it failed to run: {st.error}")
            self._set("pkg", "unknown", "Could not be checked.")
            self._set("pandoc", "unknown", "Could not be checked.")
            self.install_btn.setEnabled(False)
            return

        self._set("r", "ok", f"R {st.r_version} — {st.rscript}")
        missing = st.missing_cran + st.missing_bioc
        if missing:
            parts = []
            if st.missing_cran:
                parts.append(f"{', '.join(st.missing_cran)} (CRAN)")
            if st.missing_bioc:
                parts.append(f"{', '.join(st.missing_bioc)} (Bioconductor)")
            self._set("pkg", "bad", "Missing: " + "; ".join(parts))
        else:
            self._set("pkg", "ok", "All required R packages are installed.")
        self.install_btn.setEnabled(bool(missing))

        if st.pandoc is True:
            self._set("pandoc", "ok", "Pandoc found.")
        elif st.pandoc is None:
            self._set("pandoc", "unknown", "Will be checked once the R packages are installed.")
        else:
            self._set("pandoc", "bad", "Pandoc was not found. It comes with RStudio, or can be "
                                       "installed separately. Click Re-check afterwards.")
        if st.complete:
            s.setValue("setup/completed", "1")
            s.sync()
            self._append("Setup complete.")

    def _browse_r(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select Rscript", "", "Rscript (Rscript*);;All files (*)")
        if f:
            s = settings()
            s.setValue("R/rscript", f)
            s.sync()
            self._recheck()

    # ---- installing
    def _install(self):
        st = self._status
        if not st or not st.rscript:
            return
        names = st.missing_cran + st.missing_bioc
        if QMessageBox.question(
                self, "Install R packages",
                "Install these R packages into your personal R library?\n\n" + ", ".join(names) +
                "\n\nThis needs an internet connection and may take several minutes."
        ) != QMessageBox.StandardButton.Yes:
            return
        self._queue = []
        if st.missing_cran:
            self._queue.append((CRAN_EXPR, st.missing_cran))
        if st.missing_bioc:                       # CRAN first: Bioconductor packages depend on it
            self._queue.append((BIOC_EXPR, st.missing_bioc))
        self._busy(True)
        self._next_install()

    def _next_install(self):
        if not self._queue:
            return self._recheck()
        expr, pkgs = self._queue.pop(0)
        self._append(f"\n> Installing: {', '.join(pkgs)}")
        self.runner.start_args(self._status.rscript, ["-e", expr, *pkgs])

    def _install_finished(self, code: int):
        if code != 0:
            self._append(f"Installation failed (exit code {code}).")
            self._queue.clear()
            return self._recheck()
        self._next_install()

    def done(self, r):
        if self._worker is not None:
            self._worker.wait()
        super().done(r)
