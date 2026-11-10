"""Regression tests for issues found on real hardware (M5Stack CoreS3 / StackChan, Windows, 2026-11-09)."""
import json
import subprocess

from hermes_esp32_debug import backtrace as bt
from hermes_esp32_debug import capture as cap
from hermes_esp32_debug import toolchain
from hermes_esp32_debug.tools import make_handlers


def test_reset_rewrites_dtr_after_each_rts_change(fake_serial_module):
    # Windows usbser.sys ignores an RTS change unless DTR is written as well.
    mod = fake_serial_module(b"boot\n")
    slept = []
    cap.capture("COM7", seconds=1, reset=True, serial_module=mod, sleep=slept.append,
                clock=iter([0, 0, 0.1, 5, 5, 5]).__next__)
    ser = mod.Serial.instances[0]
    assert ser.rts_history == [False, True, False]
    assert ser.dtr is False
    # open(dtr=False) + reset(dtr=False) + one re-write per RTS edge
    assert ser.dtr_writes >= 4
    assert slept[:2] == [0.2, 0.2]


def test_real_cores3_panic_parses(fixture_text):
    info = bt.parse_panic(fixture_text("s3_arduino_cores3_null_read.log"))
    assert info.reason == "LoadProhibited" and info.core == 1
    assert info.elf_sha256_prefix == "a9767c616"  # Arduino core: CONFIG_APP_RETRIEVE_LEN_ELF_SHA=9
    assert info.backtrace[0] == "0x42008ed8" and len(info.backtrace) == 7
    assert info.registers["EXCVADDR"] == "0x00000006"
    assert info.reset_reasons == ["RTC_SW_CPU_RST"]
    assert info.coredump_unavailable
    h = bt.hints(info)
    assert any("NULL" in x for x in h)
    assert any("coredump partition" in x for x in h)


def test_capture_stops_at_rebooting_and_saves_last_capture(fake_serial_module, fixture_text, tmp_path):
    data = fixture_text("s3_arduino_cores3_null_read.log").encode()
    mod = fake_serial_module(data)
    h = make_handlers(serial_module=mod, cache_dir=tmp_path)
    out = json.loads(h["esp32_serial_capture"]({"port": "COM7", "seconds": 5}))
    assert out["panic_detected"] and out["stop_reason"] == "panic_captured"
    assert "ELF file SHA256: a9767c616" in out["log"]
    assert "Rebooting..." in out["log"] and "crash demo build" not in out["log"]
    s = out["panic_summary"]
    assert s["exception"] == "EXCCAUSE 28 = LoadProhibited" and s["fault_address"] == "0x00000006"
    assert s["elf_sha256_prefix"] == "a9767c616"
    assert "log_text can be omitted" in out["next_step"]
    saved = json.loads((tmp_path / "last_capture.json").read_text(encoding="utf-8"))
    assert saved["port"] == "COM7" and "Guru Meditation" in saved["log"]


def test_silent_port_gets_hint(fake_serial_module, tmp_path):
    h = make_handlers(serial_module=fake_serial_module(b""), cache_dir=tmp_path)
    out = json.loads(h["esp32_serial_capture"]({"port": "COM7", "seconds": 0.5}))
    assert out["line_count"] == 0 and "USB CDC On Boot" in out["hint"]
    assert not (tmp_path / "last_capture.json").exists()


def test_decode_reuses_last_capture(fake_serial_module, fixture_text, tmp_path, monkeypatch):
    log = fixture_text("s3_arduino_cores3_null_read.log")
    elf = tmp_path / "StackChanAssistant-crashdemo.ino.elf"
    elf.write_bytes(b"\x7fELF" + b"\x00" * 64)
    sha = bt.check_elf(str(elf))[2]
    log = log.replace("a9767c616", sha[:9])
    exe = tmp_path / "xtensa-esp-elf-addr2line.exe"
    exe.write_text("")
    m = {"0x42008edb": "(anonymous namespace)::averageAccel((anonymous namespace)::ImuFrame const*) at C:/sk/crash_demo.cpp:28",
         "0x42008ed8": "(anonymous namespace)::averageAccel((anonymous namespace)::ImuFrame const*) at C:/sk/crash_demo.cpp:28",
         "0x42008efe": "(anonymous namespace)::processImuFrame() at C:/sk/crash_demo.cpp:35"}

    def fake_run(cmd, **kw):
        body = "\n".join(a + ": " + m.get(a, "?? ??:0") for a in cmd[4:])
        return subprocess.CompletedProcess(cmd, 0, stdout=body + "\n", stderr="")

    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    h = make_handlers(lambda k, d=None: str(exe) if k == "addr2line_path" else d,
                      serial_module=fake_serial_module(log.encode()), cache_dir=tmp_path)
    json.loads(h["esp32_serial_capture"]({"port": "COM7", "seconds": 5}))
    out = json.loads(h["esp32_decode_backtrace"]({"elf_path": str(elf)}))
    assert out["ok"], out
    assert out["log_source"].startswith("last_capture (COM7")
    assert out["elf_sha256_match"] is True
    assert out["first_project_frame"]["location"] == "C:\\sk\\crash_demo.cpp:28"
    out2 = json.loads(h["esp32_decode_backtrace"]({"log_text": "last", "elf_path": str(elf)}))
    assert out2["ok"] and out2["log_source"].startswith("last_capture")


def test_decode_without_log_and_without_cache(tmp_path):
    h = make_handlers(cache_dir=tmp_path / "empty")
    out = json.loads(h["esp32_decode_backtrace"]({"elf_path": "x.elf"}))
    assert out["error"] == "bad_args" and "no earlier" in out["message"]


def test_vendor_core_toolchain_found(tmp_path, monkeypatch):
    # Only the M5Stack board package is installed (no packages/esp32).
    bin_dir = tmp_path / "Arduino15" / "packages" / "m5stack" / "tools" / "esp-x32" / "2601" / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "xtensa-esp-elf-addr2line.exe"
    exe.write_text("")
    (bin_dir / "xtensa-esp-elf-addr2line").write_text("")
    monkeypatch.setattr(toolchain, "_home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("IDF_TOOLS_PATH", raising=False)
    found, searched = toolchain.find_addr2line("esp32s3")
    assert found and "m5stack" in found


def test_first_backtrace_entry_deduped_against_pc(fixture_text):
    info = bt.parse_panic(fixture_text("s3_arduino_cores3_null_read.log"))
    addrs = info.addresses()
    assert addrs[0] == "0x42008edb"          # exception PC
    assert "0x42008ed8" not in addrs         # same frame, printed as PC-3
    assert addrs[1] == "0x42008efe" and len(addrs) == 7


def test_windows_mixed_separators_normalised():
    frames = bt.parse_addr2line_output(
        "0x42008edb: averageAccel at C:\\GdriveM\\ESP32\\demo/crash_demo.cpp:25\n"
        "0x4037f1ad: vPortTaskWrapper at /home/runner/work/x/port.c:139\n")
    assert frames[0]["file"] == "C:\\GdriveM\\ESP32\\demo\\crash_demo.cpp" and frames[0]["line"] == 25
    assert frames[1]["file"] == "/home/runner/work/x/port.c"
