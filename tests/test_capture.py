import json
import time

import pytest

from hermes_esp32_debug import capture as cap
from hermes_esp32_debug.tools import make_handlers


def test_capture_reads_lines_and_stops_after_panic(fake_serial_module, fixture_text):
    data = fixture_text("s3_loadprohibited_bootloop.log").encode()
    mod = fake_serial_module(data)
    res = cap.capture("COM5", seconds=5, max_lines=500, serial_module=mod)
    assert any("Guru Meditation" in l for l in res["lines"])
    ser = mod.Serial.instances[0]
    assert ser.closed and ser.dtr is False
    assert ser.rts_history == [False]  # opened with RTS de-asserted, never pulsed
    assert res["stop_reason"] in ("panic_captured", "time_limit")


def test_capture_line_limit(fake_serial_module):
    mod = fake_serial_module(b"".join(b"line %d\r\n" % i for i in range(100)))
    res = cap.capture("COM5", seconds=5, max_lines=10, serial_module=mod)
    assert len(res["lines"]) == 10 and res["stop_reason"] == "line_limit"
    assert res["lines"][0] == "line 0"


def test_capture_never_hangs_on_silent_port(fake_serial_module):
    mod = fake_serial_module(b"")
    t = [0.0]

    def clock():
        t[0] += 0.25
        return t[0]

    res = cap.capture("COM5", seconds=2, serial_module=mod, clock=clock)
    assert res["lines"] == [] and res["stop_reason"] == "time_limit"


def test_capture_clamps_limits(fake_serial_module):
    mod = fake_serial_module(b"x\n")
    res = cap.capture("COM5", seconds=9999, max_lines=10**9, serial_module=mod,
                      clock=iter([0, 0, 1e9, 1e9, 1e9]).__next__)
    assert res["seconds_limit"] == cap.MAX_SECONDS


def test_reset_via_rts_pulses_en_not_boot(fake_serial_module):
    mod = fake_serial_module(b"boot\n")
    cap.capture("COM5", seconds=1, reset=True, serial_module=mod, sleep=lambda s: None,
                clock=iter([0, 0, 0.1, 5, 5, 5]).__next__)
    ser = mod.Serial.instances[0]
    assert ser.rts_history == [False, True, False]
    assert ser.dtr is False


@pytest.mark.parametrize("msg,kind", [
    ("could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5)", "port_busy"),
    ("[Errno 16] could not open port /dev/ttyACM0: [Errno 16] Device or resource busy", "port_busy"),
    ("could not open port 'COM42': FileNotFoundError(2, 'The system cannot find the file specified.', None, 2)", "port_not_found"),
])
def test_open_errors_are_classified(fake_serial_module, msg, kind):
    mod = fake_serial_module(open_error=OSError(msg))
    h = make_handlers(serial_module=mod)
    out = json.loads(h["esp32_serial_capture"]({"port": "COM5", "seconds": 1}))
    assert out["ok"] is False and out["error"] == kind


def test_handler_redacts_and_detects_panic(fake_serial_module, fixture_text):
    mod = fake_serial_module(fixture_text("s3_loadprohibited_bootloop.log").encode())
    h = make_handlers(serial_module=mod)
    out = json.loads(h["esp32_serial_capture"]({"port": "COM5", "seconds": 3}))
    assert out["ok"] and out["panic_detected"] and out["redacted"]
    log = out["log"]
    for secret in ("MyHomeAP-5G", "sk_live_FAKE0123456789abcdef", "hunter2-fake", "192.168.1.57"):
        assert secret not in log
    assert "Backtrace: 0x42003b5b:0x3fcebf40" in log
    assert out["panic_summary"]["reason"] == "LoadProhibited"
    assert "esp32_decode_backtrace" in out["next_step"]


def test_handler_redaction_can_be_disabled(fake_serial_module):
    mod = fake_serial_module(b"I (1) wifi:connected with LabAP, aid = 1\n")
    h = make_handlers(lambda k, d=None: False if k == "redact" else d, serial_module=mod)
    out = json.loads(h["esp32_serial_capture"]({"port": "COM5", "seconds": 1}))
    assert "LabAP" in out["log"] and out["redacted"] is False


def test_handler_requires_port():
    out = json.loads(make_handlers()["esp32_serial_capture"]({}))
    assert out["error"] == "bad_args"
