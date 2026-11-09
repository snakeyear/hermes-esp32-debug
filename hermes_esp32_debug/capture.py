"""Capture a bounded slice of serial output from a board. Never writes to the device
(except toggling the RTS/DTR modem lines when a reset is explicitly requested)."""

from __future__ import annotations

import time
from typing import Callable, Optional

MAX_SECONDS = 60.0
MAX_LINES = 2000
MAX_BYTES = 256 * 1024
READ_TIMEOUT = 0.2  # seconds per read call; keeps the loop responsive


class PortBusyError(Exception):
    pass


class PortNotFoundError(Exception):
    pass


def classify_open_error(exc: BaseException) -> str:
    msg = str(exc).lower()
    if any(s in msg for s in ("access is denied", "permissionerror", "resource busy", "device or resource busy",
                               "being used by another", "errno 16", "could not exclusively lock")):
        return "port_busy"
    if any(s in msg for s in ("filenotfounderror", "no such file", "cannot find the file", "does not exist",
                               "the system cannot find")):
        return "port_not_found"
    return "open_failed"


def reset_via_rts(ser, sleep: Callable[[float], None] = time.sleep) -> None:
    """Classic auto-reset: pull EN low through RTS while keeping IO0 (DTR) high,
    so the chip restarts into the application, not the bootloader."""
    ser.dtr = False
    ser.rts = True
    sleep(0.12)
    ser.rts = False
    sleep(0.05)


def open_port(port: str, baud: int, serial_module=None):
    if serial_module is None:
        import serial as serial_module  # type: ignore
    ser = serial_module.Serial()
    ser.port = port
    ser.baudrate = int(baud)
    ser.timeout = READ_TIMEOUT
    ser.write_timeout = 1.0
    # Keep both lines de-asserted on open so opening does not reset / enter bootloader
    # on boards with the standard auto-reset circuit.
    ser.dtr = False
    ser.rts = False
    try:
        if hasattr(ser, "exclusive"):
            try:
                ser.exclusive = True  # POSIX only; ignored on Windows (opens are exclusive anyway)
            except Exception:
                pass
        ser.open()
    except Exception as exc:  # serial.SerialException, OSError, ValueError
        kind = classify_open_error(exc)
        if kind == "port_busy":
            raise PortBusyError(str(exc)) from exc
        if kind == "port_not_found":
            raise PortNotFoundError(str(exc)) from exc
        raise
    return ser


def capture(
    port: str,
    *,
    baud: int = 115200,
    seconds: float = 10.0,
    max_lines: int = 300,
    reset: bool = False,
    stop_on_panic: bool = True,
    serial_module=None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Read lines for up to ``seconds`` / ``max_lines``. Always returns; always closes the port."""
    seconds = max(0.5, min(float(seconds), MAX_SECONDS))
    max_lines = max(1, min(int(max_lines), MAX_LINES))
    ser = open_port(port, baud, serial_module)
    lines: list[str] = []
    total = 0
    stop_reason = "time_limit"
    panic_seen_at: Optional[float] = None
    buf = b""
    try:
        if reset:
            reset_via_rts(ser, sleep)
        else:
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
        deadline = clock() + seconds
        while True:
            now = clock()
            if now >= deadline:
                stop_reason = "time_limit"
                break
            if panic_seen_at is not None and now - panic_seen_at > 1.5:
                stop_reason = "panic_captured"
                break
            chunk = ser.read(max(1, min(4096, getattr(ser, "in_waiting", 0) or 1)))
            if not chunk:
                continue
            total += len(chunk)
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", errors="replace").rstrip("\r")
                lines.append(text)
                if stop_on_panic and panic_seen_at is None and (
                    "Backtrace:" in text or "MEPC" in text or "abort() was called" in text
                ):
                    panic_seen_at = clock()
                if len(lines) >= max_lines:
                    break
            if len(lines) >= max_lines:
                stop_reason = "line_limit"
                break
            if total >= MAX_BYTES:
                stop_reason = "byte_limit"
                break
        if buf and len(lines) < max_lines:
            lines.append(buf.decode("utf-8", errors="replace").rstrip("\r"))
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return {"lines": lines, "bytes": total, "stop_reason": stop_reason, "seconds_limit": seconds}
