"""List serial ports and flag the ones that look like an ESP32 board."""

from __future__ import annotations

from typing import Optional

# (vid, pid or None for "any") -> human label
ESP_USB_IDS: list[tuple[int, Optional[int], str]] = [
    (0x303A, None, "Espressif native USB (USB-Serial-JTAG / TinyUSB CDC)"),
    (0x10C4, 0xEA60, "Silicon Labs CP210x USB-UART"),
    (0x1A86, None, "WCH CH34x / CH9102 USB-UART"),
    (0x0403, 0x6001, "FTDI FT232R USB-UART"),
    (0x0403, 0x6010, "FTDI FT2232 (ESP-Prog / ESP-WROVER-KIT)"),
    (0x0403, 0x6015, "FTDI FT231X USB-UART"),
]
STRONG_VIDS = {0x303A}  # only Espressif's own VID is a near-certain match


def classify(vid: Optional[int], pid: Optional[int]) -> tuple[bool, Optional[str], str]:
    """Return ``(esp_like, bridge_label, confidence)``."""
    if vid is None:
        return False, None, "none"
    for v, p, label in ESP_USB_IDS:
        if vid == v and (p is None or pid == p):
            return True, label, "high" if vid in STRONG_VIDS else "medium"
    return False, None, "none"


def _hex(v: Optional[int]) -> Optional[str]:
    return None if v is None else f"{v:04x}"


def list_ports(list_ports_module=None) -> list[dict]:
    """Enumerate ports via ``serial.tools.list_ports.comports()``. Opens nothing."""
    if list_ports_module is None:
        from serial.tools import list_ports as list_ports_module  # type: ignore
    rows: list[dict] = []
    for p in list_ports_module.comports():
        vid = getattr(p, "vid", None)
        pid = getattr(p, "pid", None)
        esp_like, label, conf = classify(vid, pid)
        rows.append({
            "port": p.device,
            "description": getattr(p, "description", None),
            "vid": _hex(vid),
            "pid": _hex(pid),
            "vid_pid": f"{_hex(vid)}:{_hex(pid)}" if vid is not None else None,
            "serial_number": getattr(p, "serial_number", None),
            "manufacturer": getattr(p, "manufacturer", None),
            "hwid": getattr(p, "hwid", None),
            "esp_like": esp_like,
            "usb_bridge": label,
            "confidence": conf,
        })
    rows.sort(key=lambda r: (not r["esp_like"], r["confidence"] != "high", str(r["port"])))
    return rows
