"""Run an RMarkdown script through Rscript, with parameters passed as one JSON file."""
from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QObject, QProcess, Signal

CREATE_NO_WINDOW = 0x08000000

# No spaces/quotes in the expression so it survives any OS quoting.
R_RENDER_EXPR = (
    "rmarkdown::render(commandArgs(TRUE)[1],"
    "params=list(params_file=commandArgs(TRUE)[2]),"
    "output_dir=commandArgs(TRUE)[3],output_file=commandArgs(TRUE)[4],knit_root_dir=commandArgs(TRUE)[3],"
    "envir=new.env())"
)
R_MISSING_EXPR = "cat(setdiff(commandArgs(TRUE),rownames(installed.packages())),sep=',')"


def missing_r_packages(rscript: str, packages: list[str]) -> list[str]:
    """Return the R packages from `packages` that are not installed."""
    if not packages:
        return []
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    r = subprocess.run([rscript, "-e", R_MISSING_EXPR, *packages],
                       capture_output=True, text=True, timeout=120, creationflags=flags)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "Rscript failed")
    return [p for p in r.stdout.strip().split(",") if p]


class RScriptRunner(QObject):
    output = Signal(str)
    finished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def cancel(self) -> None:
        """Stop the running process (and its children on Windows)."""
        p = self._proc
        if p is None or p.state() == QProcess.ProcessState.NotRunning:
            return
        if sys.platform == "win32":
            try:
                subprocess.run(["taskkill", "/PID", str(p.processId()), "/T", "/F"],
                               capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
            except Exception:  # noqa: BLE001
                pass
        p.kill()

    def wait(self, msecs: int = 5000) -> bool:
        return self._proc is None or self._proc.waitForFinished(msecs)

    def start(self, rscript: str, rmd: str, params_file: str, out_dir: str,
              report_name: str = "report.html") -> None:
        self.start_args(rscript, ["-e", R_RENDER_EXPR, rmd, params_file, out_dir, report_name])

    def start_args(self, rscript: str, args: list[str]) -> None:
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if sys.platform == "win32":  # no console flash
            try:
                p.setCreateProcessArgumentsModifier(
                    lambda a: setattr(a, "flags", a.flags | CREATE_NO_WINDOW))
            except Exception:  # noqa: BLE001
                pass
        p.readyReadStandardOutput.connect(
            lambda: self.output.emit(bytes(p.readAllStandardOutput()).decode("utf-8", "replace")))
        p.finished.connect(
            lambda code, status: self.finished.emit(
                -1 if status == QProcess.ExitStatus.CrashExit else code))

        def on_error(err):
            self.output.emit(f"Process error: {err}")
            if err == QProcess.ProcessError.FailedToStart:
                self.finished.emit(-1)

        p.errorOccurred.connect(on_error)
        self._proc = p
        p.start(rscript, args)
