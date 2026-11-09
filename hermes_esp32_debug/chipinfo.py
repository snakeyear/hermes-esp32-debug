"""Read chip type, MAC and flash size by running esptool as a separate process.

esptool is GPL-2.0-or-later. This module never imports it: it runs an esptool
launcher with :mod:`subprocess` and parses the text output, which keeps this
plugin MIT-licensed. Launcher resolution (first match wins):

1. setting ``esptool_path``: an explicit ``esptool`` / ``esptool.exe`` / ``esptool.py``,
2. setting ``esptool_python``: ``<python> -m esptool``,
3. auto-detect the standalone ``esptool.exe`` that Arduino installs under
   ``%LOCALAPPDATA%\\Arduino15\\packages\\*\\tools\\esptool_py\\<version>\\`` (highest version),
   then PlatformIO's ``~/.platformio/packages/tool-esptoolpy``,
4. Hermes' own Python: ``<sys.executable> -m esptool``. Only read-only esptool commands are used
(``flash-id``, falling back to ``chip-id``); the code path to anything that
writes, erases or changes eFuses does not exist.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

READ_ONLY_COMMANDS = ("flash-id", "chip-id")  # allow-list; nothing else is ever passed

_CHIP_TYPE_RE = re.compile(r"^(?:Chip type:\s*|Chip is\s+)(?P<v>.+?)\s*$", re.M)
_CONNECTED_RE = re.compile(r"Connected to (?P<chip>ESP[\w-]+) on")
_DETECT_RE = re.compile(r"Detecting chip type\.*\s*(?P<chip>ESP[\w-]+)")
_FEATURES_RE = re.compile(r"^Features:\s*(?P<v>.+?)\s*$", re.M)
_CRYSTAL_RE = re.compile(r"^Crystal(?: frequency)?(?: is)?:?\s*(?P<v>\d+\s*MHz)", re.M)
_USB_MODE_RE = re.compile(r"^USB mode:\s*(?P<v>.+?)\s*$", re.M)
_MAC_RE = re.compile(r"^(?:BASE )?MAC:\s*(?P<v>(?:[0-9a-fA-F]{2}:){5,7}[0-9a-fA-F]{2})", re.M)
_FLASH_SIZE_RE = re.compile(r"Detected flash size:\s*(?P<v>\S+)")
_FLASH_MFR_RE = re.compile(r"Manufacturer:\s*(?P<v>[0-9a-fA-F]+)")
_FLASH_DEV_RE = re.compile(r"Device:\s*(?P<v>[0-9a-fA-F]+)")
_CHIP_ID_RE = re.compile(r"Chip ID:\s*(?P<v>0x[0-9a-fA-F]+)")
_ESPTOOL_VER_RE = re.compile(r"esptool(?:\.py)? v(?P<v>[\d.]+\S*)")


def parse_esptool_output(out: str) -> dict:
    def g(rx: re.Pattern, grp: str = "v") -> Optional[str]:
        m = rx.search(out)
        return m.group(grp).strip() if m else None

    chip_desc = g(_CHIP_TYPE_RE)
    chip = None
    m = _CONNECTED_RE.search(out) or _DETECT_RE.search(out)
    if m:
        chip = m.group("chip")
    elif chip_desc:
        chip = chip_desc.split()[0]
    mac = None
    mac_m = list(_MAC_RE.finditer(out))
    if mac_m:
        # prefer the 6-byte BASE MAC when an EUI-64 is printed as well
        six = [x.group("v") for x in mac_m if x.group("v").count(":") == 5]
        mac = (six or [mac_m[0].group("v")])[0].lower()
    features = g(_FEATURES_RE)
    return {
        "chip": chip,
        "chip_description": chip_desc,
        "features": [f.strip() for f in features.split(",")] if features else [],
        "crystal": g(_CRYSTAL_RE),
        "usb_mode": g(_USB_MODE_RE),
        "mac": mac,
        "flash_size": g(_FLASH_SIZE_RE),
        "flash_manufacturer_id": g(_FLASH_MFR_RE),
        "flash_device_id": g(_FLASH_DEV_RE),
        "chip_id": g(_CHIP_ID_RE),
        "esptool_version": g(_ESPTOOL_VER_RE),
    }


def classify_error(out: str) -> Optional[str]:
    low = out.lower()
    if "no module named esptool" in low:
        return "esptool_missing"
    if "could not open" in low:
        # esptool 5 wraps pyserial's message: "Could not open COM5, the port is busy or doesn't exist.
        # (could not open port 'COM5': PermissionError(13, 'Access is denied.' ...))" -> inspect the cause.
        if any(s in low for s in ("filenotfounderror", "no such file", "cannot find the file", "does not exist")) \
                and "access is denied" not in low:
            return "port_not_found"
        if any(s in low for s in ("access is denied", "permissionerror", "resource busy", "errno 16")):
            return "port_busy"
        return "port_unavailable"
    if "failed to connect" in low or "no serial data received" in low or "wrong boot mode" in low:
        return "no_sync"
    return None


ERROR_HELP = {
    "esptool_missing": "No usable esptool found. Set the plugin setting 'esptool_path' to an esptool.exe (Arduino installs one under "
                       "%LOCALAPPDATA%\\Arduino15\\packages\\esp32\\tools\\esptool_py\\<ver>\\), or 'esptool_python' to a Python "
                       "that has esptool (pip install esptool).",
    "port_busy": "The port is open in another program (Arduino Serial Monitor, idf.py monitor, PuTTY, another agent). Close it and retry.",
    "port_unavailable": "The port is busy or does not exist. Close other serial monitors and check esp32_list_ports.",
    "port_not_found": "The port does not exist. Run esp32_list_ports and use one of the listed ports.",
    "no_sync": "esptool could not talk to the ROM bootloader. Hold BOOT, tap RESET (EN), release BOOT, then retry; "
               "also try a lower baud or a data-capable USB cable.",
}


ESPTOOL_NAMES = {"esptool", "esptool.exe", "esptool.py"}


class EsptoolPathError(ValueError):
    pass


def _version_key(text: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-_+]", text))


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _launcher_for_file(path: Path) -> list[str]:
    if path.name.lower().endswith(".py"):
        return [sys.executable, str(path)]
    return [str(path)]


def autodetect_esptool() -> Optional[Path]:
    """Find a standalone esptool installed by Arduino (highest version) or PlatformIO."""
    home = _home()
    local = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    arduino_roots = [
        Path(local) / "Arduino15" / "packages",
        home / ".arduino15" / "packages",
        home / "Library" / "Arduino15" / "packages",
    ]
    found: list[tuple[tuple, Path]] = []
    for root in arduino_roots:
        for name in ("esptool.exe", "esptool"):
            for hit in glob.glob(str(root / "*" / "tools" / "esptool_py" / "*" / name)):
                p = Path(hit)
                if p.is_file():
                    found.append((_version_key(p.parent.name), p))
    if found:
        found.sort(key=lambda t: t[0], reverse=True)
        return found[0][1]
    pio = home / ".platformio" / "packages" / "tool-esptoolpy"
    for name in ("esptool.exe", "esptool", "esptool.py"):
        p = pio / name
        if p.is_file():
            return p
    return None


def resolve_esptool(esptool_path: Optional[str] = None, esptool_python: Optional[str] = None) -> tuple[list[str], str]:
    """Return ``(launcher_argv, source)``. Raises :class:`EsptoolPathError` for a bad explicit path."""
    if esptool_path:
        p = Path(str(esptool_path)).expanduser()
        if p.name.lower() not in ESPTOOL_NAMES:
            raise EsptoolPathError(f"esptool_path must point to esptool, esptool.exe or esptool.py (got {p.name!r}).")
        if not p.is_file():
            raise EsptoolPathError(f"esptool_path does not exist: {p}")
        return _launcher_for_file(p), "esptool_path"
    if esptool_python:
        return [str(esptool_python), "-m", "esptool"], "esptool_python"
    auto = autodetect_esptool()
    if auto is not None:
        return _launcher_for_file(auto), "autodetect"
    return [sys.executable, "-m", "esptool"], "hermes_python"


def build_command(launcher, port: str, command: str, baud: int = 115200) -> list[str]:
    """``launcher`` is an argv prefix (list) or, for compatibility, a Python path (str)."""
    if command.replace("_", "-") not in READ_ONLY_COMMANDS:
        raise ValueError(f"refusing non read-only esptool command: {command!r}")
    prefix = [launcher, "-m", "esptool"] if isinstance(launcher, str) else list(launcher)
    return [*prefix, "--port", port, "--baud", str(int(baud)), command]


def run_esptool(launcher, port: str, command: str, baud: int, timeout: float,
                runner=subprocess.run) -> tuple[int, str]:
    cmd = build_command(launcher, port, command, baud)
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout, check=False,
                      stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return -1, partial + f"\n[esp32-debug] esptool timed out after {timeout:.0f}s"
    except FileNotFoundError:
        return -2, f"[esp32-debug] esptool launcher not found: {cmd[0]}"
    except OSError as exc:
        return -2, f"[esp32-debug] could not start esptool: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def chip_info(port: str, *, python: Optional[str] = None, esptool_path: Optional[str] = None,
              baud: int = 115200, timeout: float = 45.0, runner=subprocess.run) -> dict:
    """Run ``flash-id`` (prints chip, features, crystal, MAC and flash size in one reset);
    fall back to ``chip-id`` (and the esptool v4 underscore spelling) only when needed."""
    try:
        launcher, source = resolve_esptool(esptool_path, python)
    except EsptoolPathError as exc:
        return {"ok": False, "error": "esptool_path_rejected", "help": str(exc), "attempts": []}
    attempts: list[dict] = []
    merged: dict = {}
    for command in ("flash-id", "chip-id"):
        rc, out = run_esptool(launcher, port, command, baud, timeout, runner)
        if rc != 0 and "invalid choice" in out.lower():  # esptool v4 used underscores
            command = command.replace("-", "_")
            rc, out = run_esptool(launcher, port, command, baud, timeout, runner)
        attempts.append({"command": command, "returncode": rc})
        parsed = parse_esptool_output(out)
        for k, v in parsed.items():
            if v and not merged.get(k):
                merged[k] = v
        if rc != 0:
            kind = classify_error(out) or ("timeout" if rc == -1 else
                                           "esptool_missing" if rc == -2 else "esptool_error")
            return {
                "ok": False,
                "error": kind,
                "help": ERROR_HELP.get(kind, "esptool failed; see output_tail."),
                "esptool": {"launcher": launcher, "source": source},
                "attempts": attempts,
                "partial": {k: v for k, v in merged.items() if v},
                "output_tail": out.strip()[-1500:],
            }
        if merged.get("chip") and merged.get("mac") and merged.get("flash_size"):
            break
    return {"ok": True, **{k: v for k, v in merged.items() if v not in (None, [])},
            "esptool": {"launcher": launcher, "source": source}, "attempts": attempts,
            "note": "The board was reset into the ROM bootloader and hard-reset afterwards; any running session on it restarted."}
