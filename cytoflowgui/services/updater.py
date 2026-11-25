"""Update logic (no GUI): check GitHub-hosted indexes, download, verify, install.

Two small JSON files drive everything:
  plugins index  {"schema":1,"plugins":[{id,name,version,api_version,min_app,url,sha256,notes}]}
  app index      {"schema":1,"version","url" (a .whl),"sha256","notes","min_python"}
Downloads are verified against the SHA-256 given in the index before anything is installed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from packaging.version import Version

from .. import API_VERSION, __version__
from ..manifest import Manifest, read_manifest
from .settings import data_dir, plugins_dir, settings

GITHUB_ORG = "samgg"
DEFAULT_PLUGINS_INDEX = f"https://raw.githubusercontent.com/{GITHUB_ORG}/cytoflowplugins/main/index.json"
DEFAULT_APP_INDEX = f"https://github.com/{GITHUB_ORG}/cytoflowgui/releases/latest/download/app-index.json"
ALLOWED_HOSTS = {"github.com", "raw.githubusercontent.com"}
USER_PLUGIN_MSG = "a plugin you created has this id: rename or remove it to receive this one"


class UpdateError(Exception):
    pass


@dataclass
class UpdateItem:
    kind: str                 # "app" | "plugin-new" | "plugin-update"
    id: str
    name: str
    current: str | None
    new: str
    url: str
    sha256: str
    origin: str               # index URL this item came from
    notes: str = ""
    blocked: str | None = None  # reason it cannot be installed now


@dataclass
class UpdateResult:
    items: list[UpdateItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ network
def _cfg(env: str, key: str, default: str) -> str:
    return os.environ.get(env) or str(settings().value(key, "") or "") or default


def _request(url: str) -> Request:
    return Request(url, headers={"User-Agent": f"cytoflowgui/{__version__}"})


def _check_url(url: str, origin: str) -> None:
    u, o = urlparse(url), urlparse(origin)
    if u.scheme == "https" and (u.hostname in ALLOWED_HOSTS or u.hostname == o.hostname):
        return
    if u.scheme == "file" and o.scheme == "file":     # local testing only
        return
    raise UpdateError(f"Untrusted download URL: {url}")


def fetch_json(url: str, timeout: int = 15) -> dict:
    with urlopen(_request(url), timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if data.get("schema") != 1:
        raise UpdateError("Unsupported index format")
    return data


def download(url: str, dest: Path, sha256: str, origin: str) -> Path:
    _check_url(url, origin)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    with urlopen(_request(url), timeout=60) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 16):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest().lower() != sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("Checksum mismatch: the download is corrupted or was tampered with")
    os.replace(part, dest)
    return dest


# ------------------------------------------------------------------ checking
def is_installed_package() -> bool:
    """False when running from a source checkout (development)."""
    parts = Path(__file__).resolve().parts
    return "site-packages" in parts or "dist-packages" in parts


def check_updates(installed: list[Manifest]) -> UpdateResult:
    res = UpdateResult()
    by_id = {m.id: m for m in installed}

    pidx = _cfg("CYTOFLOW_PLUGINS_INDEX", "update/plugins_index", DEFAULT_PLUGINS_INDEX)
    try:
        for p in fetch_json(pidx).get("plugins", []):
            try:
                cur = by_id.get(p["id"])
                new = p["version"]
                if cur and Version(new) <= Version(cur.version):
                    continue
                item = UpdateItem(
                    kind="plugin-update" if cur else "plugin-new", id=p["id"],
                    name=p.get("name", p["id"]), current=cur.version if cur else None, new=new,
                    url=p["url"], sha256=p["sha256"], origin=pidx, notes=p.get("notes", ""))
                if cur and cur.user:
                    item.blocked = USER_PLUGIN_MSG
                elif Version(p.get("min_app", "0")) > Version(__version__):
                    item.blocked = f"requires cytoflowgui >= {p['min_app']}"
                elif int(p.get("api_version", API_VERSION)) != API_VERSION:
                    item.blocked = "incompatible plugin API"
                res.items.append(item)
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"Bad plugin entry in index: {e}")
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"Could not read the plugins index: {e}")

    aidx = _cfg("CYTOFLOW_APP_INDEX", "update/app_index", DEFAULT_APP_INDEX)
    try:
        a = fetch_json(aidx)
        if Version(a["version"]) > Version(__version__):
            item = UpdateItem(kind="app", id="cytoflowgui", name="cytoflowgui", current=__version__,
                              new=a["version"], url=a["url"], sha256=a["sha256"], origin=aidx,
                              notes=a.get("notes", ""))
            py = tuple(sys.version_info[:3])
            if not is_installed_package():
                item.blocked = "development mode: update the source folder instead"
            elif Version(a.get("min_python", "0")) > Version(".".join(map(str, py))):
                item.blocked = f"requires Python >= {a['min_python']}"
            elif not item.url.endswith(".whl"):
                item.blocked = "invalid package"
            res.items.insert(0, item)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"Could not read the application index: {e}")
    return res


# ------------------------------------------------------------------ plugins
def _safe_extract(zip_path: Path, out: Path) -> None:
    out.mkdir(parents=True)
    root = out.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (out / info.filename).resolve()
            if target != root and root not in target.parents:
                raise UpdateError("Unsafe path in archive")
        z.extractall(out)


def _plugin_root(staging: Path) -> Path:
    if (staging / "plugin.toml").is_file():
        return staging
    dirs = [d for d in staging.iterdir() if d.is_dir() and d.name != "__MACOSX"]
    if len(dirs) == 1 and (dirs[0] / "plugin.toml").is_file():
        return dirs[0]
    raise UpdateError("Archive does not contain a plugin (plugin.toml not found)")


def _in_git_repo(path: Path) -> bool:
    return any((p / ".git").exists() for p in [path.resolve(), *path.resolve().parents])


def install_plugin(item: UpdateItem, log=lambda s: None) -> Manifest:
    """Download, verify, validate, then atomically swap in. The previous version is kept in backups/."""
    if item.blocked:
        raise UpdateError(item.blocked)
    pdir = plugins_dir()
    if _in_git_repo(pdir):
        raise UpdateError("The plugins folder is inside a git checkout (development); left untouched")
    if (pdir / item.id / "plugin.toml").is_file() and read_manifest(pdir / item.id).user:
        raise UpdateError(USER_PLUGIN_MSG)                 # whatever the index said

    cache = data_dir() / "cache"
    log(f"Downloading {item.name} {item.new}…")
    zpath = download(item.url, cache / f"{item.id}-{item.new}.zip", item.sha256, item.origin)

    staging = cache / "staging" / item.id
    shutil.rmtree(staging, ignore_errors=True)
    try:
        _safe_extract(zpath, staging)
        root = _plugin_root(staging)
        m = read_manifest(root)
        if m.id != item.id or Version(m.version) != Version(item.new):
            raise UpdateError("The archive does not match the index (id or version)")
        if m.error:
            raise UpdateError(f"Downloaded plugin is not usable: {m.error}")

        target = pdir / item.id
        backup = data_dir() / "backups" / item.id
        shutil.rmtree(backup, ignore_errors=True)          # keep only the previous version
        backup.parent.mkdir(parents=True, exist_ok=True)
        had_old = target.exists()
        if had_old:
            shutil.move(str(target), str(backup))
        try:
            shutil.move(str(root), str(target))
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            if had_old:
                shutil.move(str(backup), str(target))     # roll back
            raise
        log(f"Installed {item.name} {item.new}")
        return read_manifest(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ------------------------------------------------------------------ application
def prepare_app_update(item: UpdateItem, log=lambda s: None) -> Path:
    if item.blocked:
        raise UpdateError(item.blocked)
    log(f"Downloading cytoflowgui {item.new}…")
    name = Path(urlparse(item.url).path).name       # must stay a valid wheel file name for pip
    return download(item.url, data_dir() / "cache" / name, item.sha256, item.origin)


def schedule_app_update(wheel: Path, version: str) -> None:
    """Start a detached helper that waits for this process to exit, pip-installs the wheel,
    then relaunches the app. The caller must quit the application right after."""
    helper = data_dir() / "cache" / "apply_update.py"
    shutil.copy2(Path(__file__).with_name("apply_update.py"), helper)
    args = [sys.executable, str(helper), "--pid", str(os.getpid()), "--wheel", str(wheel),
            "--version", version, "--result", str(data_dir() / "last_update.json"),
            "--log", str(data_dir() / "logs" / "update.log")]
    kw: dict = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kw)
