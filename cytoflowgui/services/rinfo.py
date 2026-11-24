"""Detect R and Pandoc, and check which required R packages are missing."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .runner import missing_r_packages
from .settings import settings

UNIX_CANDIDATES = ["/usr/local/bin/Rscript", "/opt/homebrew/bin/Rscript", "/usr/bin/Rscript",
                   "/Library/Frameworks/R.framework/Resources/bin/Rscript"]
BASE_R_PACKAGES = ["rmarkdown", "jsonlite"]   # always required by the core


def _flags() -> int:
    return 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW


def _version_key(p: Path) -> list[int]:           # .../R-4.4.1/bin/Rscript.exe
    return [int(x) for x in re.findall(r"\d+", p.parent.parent.name)]


def _windows_candidates():
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\R-core\R", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                yield Path(winreg.QueryValueEx(k, "InstallPath")[0]) / "bin" / "Rscript.exe"
        except OSError:
            pass
    roots = [os.environ.get("ProgramFiles"),
             os.environ.get("LOCALAPPDATA") and str(Path(os.environ["LOCALAPPDATA"]) / "Programs")]
    for root in filter(None, roots):
        yield from sorted(Path(root).glob("R/R-*/bin/Rscript.exe"), key=_version_key, reverse=True)


def find_rscript() -> str | None:
    """Preferences first, then PATH, then the usual install locations."""
    s = str(settings().value("R/rscript", "") or "")
    if s and Path(s).is_file():
        return s
    if (w := shutil.which("Rscript")):
        return w
    cands = _windows_candidates() if sys.platform == "win32" else (Path(p) for p in UNIX_CANDIDATES)
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def _run_r(rscript: str, expr: str) -> str:
    r = subprocess.run([rscript, "-e", expr], capture_output=True, text=True,
                       timeout=120, creationflags=_flags())
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "Rscript failed")
    return r.stdout.strip()


def pandoc_available(rscript: str, rmarkdown_ok: bool) -> bool | None:
    """True/False, or None when it cannot be verified yet (rmarkdown not installed)."""
    if shutil.which("pandoc"):
        return True
    if not rmarkdown_ok:
        return None
    try:
        return _run_r(rscript, "cat(rmarkdown::pandoc_available())").endswith("TRUE")
    except Exception:  # noqa: BLE001
        return False


@dataclass
class SetupStatus:
    rscript: str | None = None
    r_version: str | None = None
    missing_cran: list[str] = field(default_factory=list)
    missing_bioc: list[str] = field(default_factory=list)
    pandoc: bool | None = None
    error: str | None = None

    @property
    def complete(self) -> bool:
        return bool(self.r_version and not self.missing_cran and not self.missing_bioc
                    and self.pandoc is True)


def check_setup(cran: list[str], bioc: list[str]) -> SetupStatus:
    st = SetupStatus(rscript=find_rscript())
    if not st.rscript:
        return st
    try:
        st.r_version = _run_r(st.rscript, "cat(as.character(getRversion()))")
        missing = set(missing_r_packages(st.rscript, [*cran, *bioc]))
        st.missing_cran = [p for p in cran if p in missing]
        st.missing_bioc = [p for p in bioc if p in missing]
        st.pandoc = pandoc_available(st.rscript, "rmarkdown" not in missing)
    except Exception as e:  # noqa: BLE001
        st.error = str(e)
    return st
