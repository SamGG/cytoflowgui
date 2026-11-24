"""Standalone helper (no cytoflowgui imports): wait for the app to exit, pip-install the new
wheel into the same Python environment, record the result, relaunch the app."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WIN = sys.platform == "win32"


def wait_exit(pid: int, timeout: int = 120) -> None:
    if WIN:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
        if h:
            k.WaitForSingleObject(h, timeout * 1000)
            k.CloseHandle(h)
        return
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.3)


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("pid", "wheel", "version", "result", "log"):
        ap.add_argument(f"--{a}", required=True)
    a = ap.parse_args()

    wait_exit(int(a.pid))
    time.sleep(1)
    Path(a.log).parent.mkdir(parents=True, exist_ok=True)
    with open(a.log, "a", encoding="utf-8") as lf:
        lf.write(f"\n=== {time.ctime()}  update to {a.version}\n")
        lf.flush()
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                            "--disable-pip-version-check", a.wheel],
                           stdout=lf, stderr=subprocess.STDOUT,
                           creationflags=0x08000000 if WIN else 0)
    Path(a.result).write_text(json.dumps(
        {"ok": r.returncode == 0, "version": a.version, "log": a.log}), encoding="utf-8")

    kw = {"creationflags": 0x00000008 | 0x00000200} if WIN else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-m", "cytoflowgui"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


if __name__ == "__main__":
    main()
