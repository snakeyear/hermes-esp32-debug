"""Tool handlers. Each returns a JSON string and never raises."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import backtrace as bt
from . import capture as cap
from . import chipinfo, ports, toolchain
from .redact import redact

logger = logging.getLogger(__name__)

SettingsGetter = Callable[[str, Any], Any]


def _no_settings(key: str, default: Any = None) -> Any:
    return default


def _dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _err(kind: str, message: str, **extra) -> str:
    return _dump({"ok": False, "error": kind, "message": message, **extra})


def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _serial_missing() -> Optional[str]:
    try:
        import serial  # noqa: F401
        from serial.tools import list_ports  # noqa: F401
        return None
    except ImportError:
        return _err("pyserial_missing", "pyserial is not installed in Hermes' Python environment. "
                    "Re-enable the plugin so Hermes installs its declared dependency (pyserial).")


def make_handlers(get_setting: SettingsGetter = _no_settings, *, serial_module=None, list_ports_module=None,
                  runner=None) -> dict[str, Callable[..., str]]:
    """Build handlers bound to a settings getter (``ctx.get_config``) and optional test doubles."""

    def setting(key: str, default: Any) -> Any:
        try:
            v = get_setting(key, default)
            return default if v in (None, "") else v
        except Exception:
            return default

    def esp32_list_ports(args: dict, **kwargs) -> str:
        try:
            if list_ports_module is None and (msg := _serial_missing()):
                return msg
            rows = ports.list_ports(list_ports_module)
            if _bool((args or {}).get("esp_only"), False):
                rows = [r for r in rows if r["esp_like"]]
            return _dump({"ok": True, "count": len(rows), "ports": rows,
                          "esp_like_ports": [r["port"] for r in rows if r["esp_like"]]})
        except Exception as exc:
            logger.exception("esp32_list_ports failed")
            return _err("list_failed", f"Could not enumerate serial ports: {exc}")

    def esp32_chip_info(args: dict, **kwargs) -> str:
        try:
            args = args or {}
            port = str(args.get("port") or "").strip()
            if not port:
                return _err("bad_args", "port is required (see esp32_list_ports).")
            if not _bool(args.get("confirm_reset"), False):
                return _err("reset_not_confirmed",
                            "esp32_chip_info resets the board. Ask the user, then call again with confirm_reset=true.")
            baud = _int(args.get("baud"), _int(setting("default_baud", 115200), 115200))
            python = setting("esptool_python", None)
            timeout = float(_int(setting("esptool_timeout", 45), 45))
            kw = {"runner": runner} if runner else {}
            result = chipinfo.chip_info(port, python=python, esptool_path=setting("esptool_path", None),
                                        baud=baud, timeout=timeout, **kw)
            if "output_tail" in result and _bool(setting("redact", True), True):
                result["output_tail"] = redact(result["output_tail"])
            result["port"] = port
            return _dump(result)
        except Exception as exc:
            logger.exception("esp32_chip_info failed")
            return _err("chip_info_failed", str(exc))

    def esp32_serial_capture(args: dict, **kwargs) -> str:
        try:
            args = args or {}
            port = str(args.get("port") or "").strip()
            if not port:
                return _err("bad_args", "port is required (see esp32_list_ports).")
            if serial_module is None and (msg := _serial_missing()):
                return msg
            baud = _int(args.get("baud"), _int(setting("default_baud", 115200), 115200))
            seconds = float(args.get("seconds") or 10)
            max_lines = _int(args.get("max_lines"), 300)
            try:
                res = cap.capture(port, baud=baud, seconds=seconds, max_lines=max_lines,
                                  reset=_bool(args.get("reset"), False),
                                  stop_on_panic=_bool(args.get("stop_on_panic"), True),
                                  serial_module=serial_module)
            except cap.PortBusyError as exc:
                return _err("port_busy", f"{port} is open in another program (Arduino Serial Monitor, idf.py monitor, "
                            f"PuTTY, esptool). Close it and retry. ({exc})", port=port)
            except cap.PortNotFoundError as exc:
                return _err("port_not_found", f"{port} does not exist. Run esp32_list_ports. ({exc})", port=port)
            text = "\n".join(res["lines"])
            redacted = _bool(setting("redact", True), True)
            if redacted:
                text = redact(text)
            info = bt.parse_panic(text)
            return _dump({
                "ok": True, "port": port, "baud": baud, "line_count": len(res["lines"]), "bytes": res["bytes"],
                "stop_reason": res["stop_reason"], "redacted": redacted,
                "panic_detected": bool(info.backtrace or info.reason or info.abort_pc),
                "panic_summary": {k: v for k, v in info.as_dict().items() if k in (
                    "reason", "core", "chip_hint", "elf_sha256_prefix", "reset_reasons", "panic_count")},
                "log": text,
                "next_step": ("Panic found: call esp32_decode_backtrace with this log and the build's .elf."
                              if (info.backtrace or info.abort_pc or "MEPC" in info.registers) else None),
            })
        except Exception as exc:
            logger.exception("esp32_serial_capture failed")
            return _err("capture_failed", f"Serial capture failed: {exc}")

    def esp32_decode_backtrace(args: dict, **kwargs) -> str:
        try:
            args = args or {}
            log_text = str(args.get("log_text") or "")
            elf_path = str(args.get("elf_path") or "").strip()
            if not log_text.strip():
                return _err("bad_args", "log_text is required.")
            if not elf_path:
                return _err("bad_args", "elf_path is required (the .elf of the exact build on the board).")
            info = bt.parse_panic(log_text, which="first" if args.get("which_panic") == "first" else "last")
            addresses = info.addresses()
            if not addresses:
                return _err("no_backtrace", "No panic backtrace or MEPC/RA/PC address found in log_text.",
                            panic=info.as_dict())
            ok, msg, elf_sha = bt.check_elf(elf_path)
            if not ok:
                return _err("bad_elf", msg)
            chip = toolchain.normalize_chip(args.get("chip")) or toolchain.normalize_chip(info.chip_hint) \
                or toolchain.normalize_chip(setting("default_chip", None))
            arch = toolchain.arch_for_chip(chip) or info.arch_hint
            dirs = setting("toolchain_dirs", []) or []
            if isinstance(dirs, str):
                dirs = [d.strip() for d in dirs.split(";") if d.strip()]
            explicit = args.get("addr2line_path") or setting("addr2line_path", None)
            a2l, searched = toolchain.find_addr2line(chip, arch, explicit, dirs)
            if not a2l:
                if explicit:
                    return _err("addr2line_rejected",
                                "addr2line_path must be an existing Espressif addr2line "
                                "(xtensa-*-elf-addr2line or riscv32-esp-elf-addr2line).", searched=searched)
                return _err("addr2line_not_found",
                            "Espressif addr2line not found. Install the ESP32 Arduino core / PlatformIO / ESP-IDF, "
                            "or set the plugin setting 'addr2line_path' or 'toolchain_dirs'.",
                            chip=chip, arch=arch, searched=searched[:20], addresses=addresses)
            frames, error = bt.run_addr2line(a2l, elf_path, addresses)
            if error:
                return _err("addr2line_failed", error, addr2line=a2l)
            warnings: list[str] = []
            sha_match = None
            if info.elf_sha256_prefix and elf_sha:
                sha_match = elf_sha.startswith(info.elf_sha256_prefix)
                if not sha_match:
                    warnings.append(f"ELF mismatch: log reports ELF SHA256 {info.elf_sha256_prefix}…, this ELF is "
                                    f"{elf_sha[:len(info.elf_sha256_prefix)]}…. File:line results are unreliable; "
                                    "use the .elf from the build that is flashed.")
            elif not info.elf_sha256_prefix:
                warnings.append("Log has no 'ELF file SHA256' line, so the ELF could not be matched to the flashed build.")
            if all(f["file"] is None for f in frames):
                warnings.append("No address resolved to a source line: wrong ELF, wrong chip toolchain, or a build without debug info (-g).")
            include_source = _bool(args.get("include_source"), True)
            for f in frames:
                f["location"] = (f"{f['file']}:{f['line']}" if f["file"] and f["line"] else None)
                f["in_project"] = bool(f["file"]) and not _is_framework_path(f["file"])
                if include_source and f["file"] and f["line"]:
                    snip = bt.source_snippet(f["file"], f["line"])
                    if snip:
                        f["source"] = snip["lines"]
            first_user = next((f for f in frames if f["in_project"] and f["location"]), None)
            out = {
                "ok": True,
                "chip": chip, "arch": arch, "addr2line": a2l, "elf": elf_path,
                "elf_sha256_match": sha_match,
                "panic": {**info.as_dict(), "exception": bt.describe_exception(info)},
                "frames": frames,
                "first_project_frame": first_user and {k: first_user[k] for k in ("function", "location")},
                "hints": bt.hints(info),
                "warnings": warnings,
            }
            if _bool(setting("redact", True), True):
                return redact(_dump(out), ips=False)
            return _dump(out)
        except Exception as exc:
            logger.exception("esp32_decode_backtrace failed")
            return _err("decode_failed", f"Backtrace decode failed: {exc}")

    return {
        "esp32_list_ports": esp32_list_ports,
        "esp32_chip_info": esp32_chip_info,
        "esp32_serial_capture": esp32_serial_capture,
        "esp32_decode_backtrace": esp32_decode_backtrace,
    }


_FRAMEWORK_MARKERS = ("/esp-idf/", "\\esp-idf\\", "/components/freertos", "\\components\\freertos",
                      "/framework-arduinoespressif32", "\\framework-arduinoespressif32",
                      "/packages/esp32/hardware/", "\\packages\\esp32\\hardware\\", "/arduino15/", "\\arduino15\\",
                      "/esp32-arduino-lib-builder/", "/xtensa-esp-elf/", "/riscv32-esp-elf/", "/newlib/",
                      "/builds/idf/", "/home/runner/work/")


def _is_framework_path(path: str) -> bool:
    low = path.replace("\\\\", "\\").lower()
    return any(m in low for m in _FRAMEWORK_MARKERS)
