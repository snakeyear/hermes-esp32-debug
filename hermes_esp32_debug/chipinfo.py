"""Read chip type, MAC and flash size by running esptool as a separate process.

esptool is GPL-2.0-or-later. This module never imports it: it runs
``<python> -m esptool ...`` with :mod:`subprocess` and parses the text output,
which keeps this plugin MIT-licensed. Only read-only esptool commands are used
(``flash-id``, falling back to ``chip-id``); the code path to anything that
writes, erases or changes eFuses does not exist.
"""

from __future__ import annotations

import re
import subprocess
import sys
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
    "esptool_missing": "esptool is not installed for the configured Python. Install it there (pip install esptool) "
                       "or set the plugin setting 'esptool_python' to a Python that has it.",
    "port_busy": "The port is open in another program (Arduino Serial Monitor, idf.py monitor, PuTTY, another agent). Close it and retry.",
    "port_unavailable": "The port is busy or does not exist. Close other serial monitors and check esp32_list_ports.",
    "port_not_found": "The port does not exist. Run esp32_list_ports and use one of the listed ports.",
    "no_sync": "esptool could not talk to the ROM bootloader. Hold BOOT, tap RESET (EN), release BOOT, then retry; "
               "also try a lower baud or a data-capable USB cable.",
}


def build_command(python: str, port: str, command: str, baud: int = 115200) -> list[str]:
    if command not in READ_ONLY_COMMANDS:
        raise ValueError(f"refusing non read-only esptool command: {command!r}")
    return [python, "-m", "esptool", "--port", port, "--baud", str(int(baud)), command]


def run_esptool(python: Optional[str], port: str, command: str, baud: int, timeout: float,
                runner=subprocess.run) -> tuple[int, str]:
    cmd = build_command(python or sys.executable, port, command, baud)
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout, check=False,
                      stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return -1, partial + f"\n[esp32-debug] esptool timed out after {timeout:.0f}s"
    except FileNotFoundError:
        return -2, f"[esp32-debug] Python interpreter not found: {cmd[0]}"
    except OSError as exc:
        return -2, f"[esp32-debug] could not start esptool: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def chip_info(port: str, *, python: Optional[str] = None, baud: int = 115200, timeout: float = 45.0,
              runner=subprocess.run) -> dict:
    """Run ``flash-id`` (prints chip, features, crystal, MAC and flash size in one reset);
    fall back to ``chip-id`` (and the esptool v4 spelling) only when needed."""
    attempts: list[dict] = []
    merged: dict = {}
    for command in ("flash-id", "chip-id"):
        rc, out = run_esptool(python, port, command, baud, timeout, runner)
        if rc != 0 and "invalid choice" in out.lower():  # esptool v4 used underscores
            legacy = command.replace("-", "_")
            cmd = [python or sys.executable, "-m", "esptool", "--port", port, "--baud", str(int(baud)), legacy]
            try:
                proc = runner(cmd, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
                rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
            except (subprocess.TimeoutExpired, OSError) as exc:
                rc, out = -1, str(exc)
        attempts.append({"command": command, "returncode": rc})
        parsed = parse_esptool_output(out)
        for k, v in parsed.items():
            if v and not merged.get(k):
                merged[k] = v
        if rc != 0:
            kind = classify_error(out) or ("timeout" if rc == -1 else "esptool_error")
            return {
                "ok": False,
                "error": kind,
                "help": ERROR_HELP.get(kind, "esptool failed; see output_tail."),
                "attempts": attempts,
                "partial": {k: v for k, v in merged.items() if v},
                "output_tail": out.strip()[-1500:],
            }
        if merged.get("chip") and merged.get("mac") and merged.get("flash_size"):
            break
    return {"ok": True, **{k: v for k, v in merged.items() if v not in (None, [])}, "attempts": attempts,
            "note": "The board was reset into the ROM bootloader and hard-reset afterwards; any running session on it restarted."}
