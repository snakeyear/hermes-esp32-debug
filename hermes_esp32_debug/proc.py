"""Isolated subprocess launch and the plugin's Hermes data directory.

esptool and addr2line are never given Hermes's inherited environment or the
caller's current working directory. ``python -m esptool`` is launched with
``-P`` (Python 3.11+; this plugin requires >=3.11) so a project-local
``esptool.py`` cannot shadow the real module.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

PLUGIN_NAME = "esp32-debug"

# Same ceiling as serial capture (capture.MAX_BYTES): a model-supplied log_text
# must not be larger than a capture the plugin itself would keep.
LOG_TEXT_MAX_BYTES = 256 * 1024

_ENV_UNIX = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE")
_ENV_WINDOWS = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
    "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE",
    "TEMP", "TMP",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "PATHEXT", "COMSPEC",
)


def isolated_env() -> dict[str, str]:
    """Explicit, minimal environment for a child process. Never ``os.environ``."""
    keys = _ENV_WINDOWS if os.name == "nt" else _ENV_UNIX
    env: dict[str, str] = {}
    for key in keys:
        val = os.environ.get(key)
        if val:
            env[key] = val
    if os.name == "nt":
        if "HOME" not in env and "USERPROFILE" in env:
            env["HOME"] = env["USERPROFILE"]
        # Python on Windows needs SYSTEMROOT to start; keep a last-resort default.
        if "SYSTEMROOT" not in env:
            windir = env.get("WINDIR") or r"C:\Windows"
            env["SYSTEMROOT"] = windir
    else:
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("LANG", "C.UTF-8")
    return env


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _hermes_home() -> Path:
    """Resolve Hermes home without importing Hermes (tests run standalone).

    Prefer the official helpers when they are on the path, then ``HERMES_HOME``,
    then the same platform default Hermes itself uses.
    """
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        pass
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(os.path.expandvars(raw)))
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"



def _official_plugin_data_dir() -> Optional[Path]:
    """Use Hermes's own helper when it is importable (running inside the agent)."""
    try:
        from plugins.plugin_storage import plugin_data_dir as official  # type: ignore
        return Path(official(PLUGIN_NAME))
    except Exception:
        return None


def plugin_data_dir(*, create: bool = True) -> Path:
    """``<hermes home>/plugin-data/esp32-debug``, or ``ESP32_DEBUG_CACHE_DIR``.

    Matches Hermes v0.23's ``plugins.plugin_storage.plugin_data_dir`` layout.
    The env override is for tests and for an operator who wants a different folder.
    """
    override = os.environ.get("ESP32_DEBUG_CACHE_DIR", "").strip()
    if override:
        root = Path(override)
    else:
        official = _official_plugin_data_dir()
        root = official if official is not None else (_hermes_home() / "plugin-data" / PLUGIN_NAME)
    if create:
        root.mkdir(parents=True, exist_ok=True)
        _chmod_best_effort(root, 0o700)
    return root


def python_module_cmd(python: str, module: str) -> list[str]:
    """``<python> -P -m <module>``. ``-P`` is 3.11+ (safe path; no cwd / PYTHONPATH)."""
    return [python, "-P", "-m", module]


def python_script_cmd(python: str, script: str) -> list[str]:
    return [python, "-P", script]


def run_isolated(cmd: list[str], *, timeout: float, cwd: Optional[Path | str] = None,
                 runner=subprocess.run):
    """Run ``cmd`` with a neutral cwd, a minimal env, and stdin discarded."""
    work = Path(cwd) if cwd else plugin_data_dir(create=True)
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError:
        work = plugin_data_dir(create=True)
    return runner(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
        cwd=str(work),
        env=isolated_env(),
    )
