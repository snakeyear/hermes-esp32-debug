import json
import subprocess
import sys

import pytest

from hermes_esp32_debug import chipinfo
from hermes_esp32_debug.tools import make_handlers

ESPTOOL5_FLASH_ID = """esptool v5.1.0
Connected to ESP32-S3 on COM5:
Chip type:          ESP32-S3 (QFN56) (revision v0.2)
Features:           Wi-Fi, BT 5 (LE), Dual Core + LP Core, 240MHz, Embedded PSRAM 8MB (AP_3v3)
Crystal frequency:  40MHz
USB mode:           USB-Serial/JTAG
MAC:                34:85:18:aa:bb:cc

Stub flasher running.

Flash Memory Information:
=========================
Manufacturer: c8
Device: 4018
Detected flash size: 16MB
Flash voltage set by eFuse: 3.3V

Hard resetting via RTS pin...
"""

ESPTOOL4_FLASH_ID = """esptool.py v4.7.0
Serial port COM7
Connecting....
Detecting chip type... ESP32
Chip is ESP32-D0WD-V3 (revision v3.0)
Features: WiFi, BT, Dual Core, 240MHz, VRef calibration in efuse, Coding Scheme None
Crystal is 40MHz
MAC: 24:6f:28:11:22:33
Uploading stub...
Manufacturer: 20
Device: 4016
Detected flash size: 4MB
Hard resetting via RTS pin...
"""


def test_parse_v5():
    p = chipinfo.parse_esptool_output(ESPTOOL5_FLASH_ID)
    assert p["chip"] == "ESP32-S3"
    assert p["chip_description"].startswith("ESP32-S3 (QFN56)")
    assert p["mac"] == "34:85:18:aa:bb:cc"
    assert p["flash_size"] == "16MB"
    assert p["crystal"] == "40MHz"
    assert p["usb_mode"] == "USB-Serial/JTAG"
    assert "Wi-Fi" in p["features"]
    assert p["esptool_version"] == "5.1.0"


def test_parse_v4():
    p = chipinfo.parse_esptool_output(ESPTOOL4_FLASH_ID)
    assert p["chip"] == "ESP32"
    assert p["chip_description"] == "ESP32-D0WD-V3 (revision v3.0)"
    assert p["mac"] == "24:6f:28:11:22:33"
    assert p["flash_size"] == "4MB"
    assert p["crystal"] == "40MHz"


def test_command_allow_list():
    cmd = chipinfo.build_command("py", "COM5", "flash-id")
    assert cmd == ["py", "-m", "esptool", "--port", "COM5", "--baud", "115200", "flash-id"]
    for bad in ("write-flash", "erase-flash", "write_flash", "burn-efuse", "write-mem", "erase-region"):
        with pytest.raises(ValueError):
            chipinfo.build_command("py", "COM5", bad)


class Runner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        item = self.outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        rc, out = item
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")


def test_chip_info_single_reset_when_flash_id_has_everything():
    r = Runner([(0, ESPTOOL5_FLASH_ID)])
    res = chipinfo.chip_info("COM5", python="C:/Python312/python.exe", runner=r)
    assert res["ok"] and res["mac"] == "34:85:18:aa:bb:cc" and res["flash_size"] == "16MB"
    assert len(r.calls) == 1
    cmd, kw = r.calls[0]
    assert cmd[:3] == ["C:/Python312/python.exe", "-m", "esptool"]
    assert "timeout" in kw and kw["timeout"] > 0
    assert "import esptool" not in open(chipinfo.__file__, encoding="utf-8").read()


def test_chip_info_defaults_to_sys_executable():
    r = Runner([(0, ESPTOOL5_FLASH_ID)])
    chipinfo.chip_info("COM5", runner=r)
    assert r.calls[0][0][0] == sys.executable


def test_chip_info_v4_fallback_to_underscore():
    r = Runner([(2, "esptool.py: error: argument operation: invalid choice: 'flash-id'"), (0, ESPTOOL4_FLASH_ID)])
    res = chipinfo.chip_info("COM7", runner=r)
    assert res["ok"] and res["chip"] == "ESP32"
    assert r.calls[1][0][-1] == "flash_id"


@pytest.mark.parametrize("out,kind", [
    ("A fatal error occurred: Could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5)", "port_busy"),
    ("A fatal error occurred: Could not open port 'COM42': FileNotFoundError(2, 'The system cannot find the file specified.')", "port_not_found"),
    ("A fatal error occurred: Failed to connect to ESP32-S3: No serial data received.", "no_sync"),
    ("/usr/bin/python3: No module named esptool", "esptool_missing"),
])
def test_chip_info_errors(out, kind):
    res = chipinfo.chip_info("COM5", runner=Runner([(2, out)]))
    assert res["ok"] is False and res["error"] == kind and res["help"]


def test_chip_info_timeout():
    res = chipinfo.chip_info("COM5", runner=Runner([subprocess.TimeoutExpired(["x"], 45)]))
    assert res["ok"] is False and res["error"] == "timeout"


def test_handler_requires_confirm_reset():
    r = Runner([(0, ESPTOOL5_FLASH_ID)])
    h = make_handlers(runner=r)
    out = json.loads(h["esp32_chip_info"]({"port": "COM5"}))
    assert out["error"] == "reset_not_confirmed" and r.calls == []
    out = json.loads(h["esp32_chip_info"]({"port": "COM5", "confirm_reset": True}))
    assert out["ok"] and out["port"] == "COM5"


def test_handler_uses_configured_python():
    r = Runner([(0, ESPTOOL5_FLASH_ID)])
    settings = {"esptool_python": "C:/tools/py/python.exe", "esptool_timeout": 30}
    h = make_handlers(lambda k, d=None: settings.get(k, d), runner=r)
    json.loads(h["esp32_chip_info"]({"port": "COM5", "confirm_reset": True}))
    cmd, kw = r.calls[0]
    assert cmd[0] == "C:/tools/py/python.exe" and kw["timeout"] == 30


def test_real_esptool5_wrapped_messages():
    nf = ("A fatal error occurred: Could not open /dev/ttyNOPE, the port is busy or doesn't exist.\n"
          "([Errno 2] could not open port /dev/ttyNOPE: [Errno 2] No such file or directory: '/dev/ttyNOPE')")
    busy = ("A fatal error occurred: Could not open COM5, the port is busy or doesn't exist.\n"
            "(could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5))")
    assert chipinfo.classify_error(nf) == "port_not_found"
    assert chipinfo.classify_error(busy) == "port_busy"
    assert chipinfo.classify_error("Could not open COM5, the port is busy or doesn't exist.") == "port_unavailable"
