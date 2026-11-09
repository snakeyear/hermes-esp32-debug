"""Locate the Espressif ``addr2line`` binary for a chip on Windows, macOS or Linux.

Search order:
1. an explicit path the user (or the call) supplied,
2. extra directories from the plugin settings (``toolchain_dirs``),
3. the usual install trees: Arduino IDE 2 / arduino-cli (``Arduino15/packages/esp32/tools``),
   PlatformIO (``~/.platformio/packages``) and ESP-IDF (``$IDF_TOOLS_PATH``,
   ``~/.espressif/tools``, ``C:\\Espressif\\tools``),
4. ``PATH``.

Only file names that look like an Espressif addr2line are accepted, so the
tool can never be pointed at an arbitrary executable.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

XTENSA_CHIPS = {"esp32", "esp32s2", "esp32s3"}
RISCV_CHIPS = {"esp32c2", "esp32c3", "esp32c5", "esp32c6", "esp32c61", "esp32h2", "esp32h4", "esp32p4"}
KNOWN_CHIPS = XTENSA_CHIPS | RISCV_CHIPS


def normalize_chip(chip: Optional[str]) -> Optional[str]:
    if not chip:
        return None
    c = chip.strip().lower().replace("-", "").replace("_", "")
    for known in sorted(KNOWN_CHIPS, key=len, reverse=True):
        if c.startswith(known):
            return known
    return None


def arch_for_chip(chip: Optional[str]) -> Optional[str]:
    c = normalize_chip(chip)
    if c in XTENSA_CHIPS:
        return "xtensa"
    if c in RISCV_CHIPS:
        return "riscv"
    return None


def candidate_names(chip: Optional[str], arch: Optional[str] = None) -> list[str]:
    """Executable base names to try, most specific first (without ``.exe``)."""
    c = normalize_chip(chip)
    a = arch or arch_for_chip(c)
    names: list[str] = []
    if a == "xtensa" or (a is None and c is None):
        if c in XTENSA_CHIPS:
            names.append(f"xtensa-{c}-elf-addr2line")
        names.append("xtensa-esp-elf-addr2line")  # unified toolchain (IDF >= 5.2, Arduino core 3.x)
        if c is None:
            names += [f"xtensa-{x}-elf-addr2line" for x in ("esp32s3", "esp32", "esp32s2")]
    if a == "riscv" or (a is None and c is None):
        names.append("riscv32-esp-elf-addr2line")
    return names


def is_espressif_addr2line(path: str | os.PathLike) -> bool:
    name = Path(path).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name.endswith("-addr2line") and (name.startswith("xtensa-") or name.startswith("riscv32-"))


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def search_roots(extra_dirs: Iterable[str] = ()) -> list[Path]:
    home = _home()
    local = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    roots = [Path(d).expanduser() for d in extra_dirs if d]
    roots += [
        Path(local) / "Arduino15" / "packages" / "esp32" / "tools",      # Windows Arduino IDE 2 / arduino-cli
        home / ".arduino15" / "packages" / "esp32" / "tools",             # Linux
        home / "Library" / "Arduino15" / "packages" / "esp32" / "tools",  # macOS
        home / ".platformio" / "packages",
        home / ".espressif" / "tools",
        Path("C:/Espressif/tools"),
    ]
    idf_tools = os.environ.get("IDF_TOOLS_PATH")
    if idf_tools:
        roots.insert(len(list(extra_dirs)), Path(idf_tools) / "tools")
    return roots


def _glob_in(root: Path, exe: str) -> list[Path]:
    if not root.is_dir():
        return []
    hits: list[str] = []
    # Layouts seen in the wild (depth 1..5 below the tools root):
    #   Arduino:    esp-x32/2405/bin/xtensa-esp32s3-elf-addr2line.exe
    #               xtensa-esp32s3-elf-gcc/esp-2021r2-patch5-8.4.0/bin/...
    #   PlatformIO: toolchain-xtensa-esp32s3/bin/...
    #   ESP-IDF:    xtensa-esp-elf/esp-13.2.0_20240530/xtensa-esp-elf/bin/...
    for depth in ("*", "*/*", "*/*/*", "*/*/*/*"):
        hits += glob.glob(str(root / depth / "bin" / exe))
    hits += glob.glob(str(root / "bin" / exe))
    # newest version directory first
    return [Path(h) for h in sorted(set(hits), reverse=True)]


def find_addr2line(
    chip: Optional[str] = None,
    arch: Optional[str] = None,
    explicit: Optional[str] = None,
    extra_dirs: Iterable[str] = (),
) -> tuple[Optional[str], list[str]]:
    """Return ``(path_or_None, searched_locations)``."""
    searched: list[str] = []
    if explicit:
        searched.append(explicit)
        p = Path(explicit).expanduser()
        if p.is_file() and is_espressif_addr2line(p):
            return str(p), searched
        return None, searched
    suffix = ".exe" if sys.platform.startswith("win") else ""
    names = candidate_names(chip, arch)
    extra = list(extra_dirs)
    for root in search_roots(extra):
        searched.append(str(root))
        for name in names:
            for exe in {name + suffix, name, name + ".exe"}:
                for hit in _glob_in(root, exe):
                    if hit.is_file():
                        return str(hit), searched
    for name in names:
        found = shutil.which(name)
        searched.append(f"PATH:{name}")
        if found:
            return found, searched
    return None, searched
