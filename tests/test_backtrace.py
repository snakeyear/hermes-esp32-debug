import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hermes_esp32_debug import backtrace as bt
from hermes_esp32_debug import toolchain
from hermes_esp32_debug.tools import make_handlers

FIX = Path(__file__).parent / "fixtures"


# ---------------- parsing ----------------

def test_parse_s3_loadprohibited(fixture_text):
    info = bt.parse_panic(fixture_text("s3_loadprohibited_bootloop.log"))
    assert info.chip_hint == "esp32s3" and info.arch_hint == "xtensa"
    assert info.reason == "LoadProhibited" and info.core == 1
    assert info.registers["PC"] == "0x42003b5e"
    assert info.registers["EXCVADDR"] == "0x00000008"
    assert info.backtrace == ["0x42003b5b", "0x42003c07", "0x42006f3e", "0x4037a9a6"]
    assert info.elf_sha256_prefix == "5a1c2e9f0b7d4c33"
    assert bt.describe_exception(info) == "EXCCAUSE 28 = LoadProhibited"
    assert info.addresses()[0] == "0x42003b5e"
    assert any("NULL" in h for h in bt.hints(info))
    assert info.reset_reasons.count("RTC_SW_CPU_RST") == 2


def test_parse_abort_with_esp_error_check(fixture_text):
    info = bt.parse_panic(fixture_text("esp32_idf_abort.log"))
    assert info.abort_pc == "0x40086c2b" and info.core == 0
    assert "ESP_ERR_NO_MEM" in info.assert_text
    assert len(info.backtrace) == 7
    assert any("Assertion failed" in h for h in bt.hints(info))


def test_parse_stack_overflow_corrupted(fixture_text):
    info = bt.parse_panic(fixture_text("esp32_stack_overflow_corrupted.log"))
    assert info.stack_overflow_task == "tts_task"
    assert info.backtrace_corrupted
    hs = " ".join(bt.hints(info))
    assert "Stack overflow" in hs and "CORRUPTED" in hs


def test_parse_riscv(fixture_text):
    info = bt.parse_panic(fixture_text("c3_riscv_store_fault.log"))
    assert info.chip_hint == "esp32c3" and info.arch_hint == "riscv"
    assert info.registers["MEPC"] == "0x42005f32" and info.registers["RA"] == "0x42005f2c"
    assert bt.describe_exception(info) == "MCAUSE 7 = Store access fault"
    assert info.addresses() == ["0x42005f32", "0x42005f2c"]  # SP/GP (0x3fc...) are not code
    assert any("NULL" in h for h in bt.hints(info))


def test_parse_watchdog_and_brownout(fixture_text):
    wdt = bt.parse_panic(fixture_text("s3_task_wdt.log"))
    assert wdt.watchdog and len(wdt.backtrace) == 3
    assert any("Watchdog" in h for h in bt.hints(wdt))
    bo = bt.parse_panic(fixture_text("brownout.log"))
    assert bo.brownout and bo.addresses() == []
    assert any("Brownout" in h for h in bt.hints(bo))


def test_parse_first_vs_last():
    log = ("Guru Meditation Error: Core  0 panic'ed (LoadProhibited).\nBacktrace: 0x400d0001:0x3ffb0000\n"
           "Rebooting...\nGuru Meditation Error: Core  1 panic'ed (StoreProhibited).\nBacktrace: 0x400d0002:0x3ffb0000\n")
    assert bt.parse_panic(log).backtrace == ["0x400d0002"]
    assert bt.parse_panic(log, which="first").backtrace == ["0x400d0001"]
    assert bt.parse_panic(log).panic_count == 2


def test_parse_addr2line_output():
    frames = bt.parse_addr2line_output((FIX / "addr2line_s3.txt").read_text())
    assert len(frames) == 4
    assert frames[0]["function"] == "Face::draw(Avatar*)"
    assert frames[0]["file"].endswith("face.cpp") and frames[0]["line"] == 57
    assert frames[1]["inlined_by"][0]["function"] == "loop()"
    unknown = bt.parse_addr2line_output("0x40000000: ?? ??:0\n")
    assert unknown[0]["function"] is None and unknown[0]["file"] is None


# ---------------- toolchain discovery ----------------

def test_candidate_names():
    assert toolchain.candidate_names("esp32s3")[0] == "xtensa-esp32s3-elf-addr2line"
    assert "xtensa-esp-elf-addr2line" in toolchain.candidate_names("ESP32-S3")
    assert toolchain.candidate_names("esp32c3") == ["riscv32-esp-elf-addr2line"]
    assert toolchain.arch_for_chip("esp32c6") == "riscv"
    assert toolchain.normalize_chip("ESP32-S3 (QFN56)") == "esp32s3"


@pytest.mark.parametrize("layout", [
    "Arduino15/packages/esp32/tools/esp-x32/2405/bin",
    "Arduino15/packages/esp32/tools/xtensa-esp32s3-elf-gcc/esp-2021r2-patch5-8.4.0/bin",
])
def test_find_addr2line_arduino(tmp_path, monkeypatch, layout):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(toolchain, "_home", lambda: tmp_path / "nohome")
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    d = tmp_path / layout
    d.mkdir(parents=True)
    exe = d / "xtensa-esp32s3-elf-addr2line.exe"
    exe.write_text("")
    found, searched = toolchain.find_addr2line("esp32s3")
    assert found == str(exe)


def test_find_addr2line_platformio_and_espressif(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "_home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    monkeypatch.delenv("IDF_TOOLS_PATH", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "linux")
    rv = tmp_path / ".espressif/tools/riscv32-esp-elf/esp-13.2.0_20240530/riscv32-esp-elf/bin"
    rv.mkdir(parents=True)
    (rv / "riscv32-esp-elf-addr2line").write_text("")
    pio = tmp_path / ".platformio/packages/toolchain-xtensa-esp32s3/bin"
    pio.mkdir(parents=True)
    (pio / "xtensa-esp32s3-elf-addr2line").write_text("")
    assert toolchain.find_addr2line("esp32c3")[0].endswith("riscv32-esp-elf-addr2line")
    assert "toolchain-xtensa-esp32s3" in toolchain.find_addr2line("esp32s3")[0]


def test_explicit_path_must_be_espressif_addr2line(tmp_path):
    evil = tmp_path / "calc.exe"
    evil.write_text("")
    assert toolchain.find_addr2line("esp32s3", explicit=str(evil))[0] is None
    ok = tmp_path / "xtensa-esp32s3-elf-addr2line.exe"
    ok.write_text("")
    assert toolchain.find_addr2line("esp32s3", explicit=str(ok))[0] == str(ok)


# ---------------- end-to-end decode with a fake addr2line ----------------

ADDR_MAP = {
    "0x42003b5e": "Face::draw(Avatar*) at {src}/face.cpp:57",
    "0x42003b5b": "Face::draw(Avatar*) at {src}/face.cpp:57",
    "0x42003c07": "Avatar::update() at {src}/avatar.cpp:112\n (inlined by) loop() at {src}/main.cpp:88",
    "0x42006f3e": "loopTask(void*) at C:/Users/dev/AppData/Local/Arduino15/packages/esp32/hardware/esp32/3.0.7/cores/esp32/main.cpp:74",
    "0x4037a9a6": "vPortTaskWrapper at /home/runner/work/esp32-arduino-lib-builder/esp-idf/components/freertos/port.c:139",
}


def _make_project(tmp_path):
    src = tmp_path / "StackChanAssistant" / "src"
    src.mkdir(parents=True)
    lines = [f"// line {i}" for i in range(1, 70)]
    lines[56] = "  int w = avatar->face->width;  // line 57"
    (src / "face.cpp").write_text("\n".join(lines) + "\n")
    elf = tmp_path / "firmware.elf"
    elf.write_bytes(b"\x7fELF" + b"\x01\x01\x01" + b"\x00" * 200)
    sha = hashlib.sha256(elf.read_bytes()).hexdigest()
    return src, elf, sha


def _fake_addr2line(tmp_path, src):
    bindir = tmp_path / "tc" / "bin"
    bindir.mkdir(parents=True)
    mapping = {k: v.format(src=src.as_posix()) for k, v in ADDR_MAP.items()}
    script = bindir / "xtensa-esp32s3-elf-addr2line"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        m = {mapping!r}
        args = sys.argv[1:]
        assert args[0] == "-pfiaC" and args[1] == "-e", args
        for a in args[3:]:
            print(a + ": " + m.get(a, "?? ??:0"))
        """))
    script.chmod(0o755)
    return script


@pytest.mark.skipif(os.name == "nt", reason="shebang fake binary is POSIX-only; the mocked test covers Windows")
def test_decode_end_to_end_with_fake_addr2line(tmp_path, fixture_text):
    src, elf, sha = _make_project(tmp_path)
    a2l = _fake_addr2line(tmp_path, src)
    log = fixture_text("s3_loadprohibited_bootloop.log").replace("5a1c2e9f0b7d4c33", sha[:16])
    h = make_handlers(lambda k, d=None: [str(a2l.parent.parent)] if k == "toolchain_dirs" else d)
    out = json.loads(h["esp32_decode_backtrace"]({"log_text": log, "elf_path": str(elf)}))
    assert out["ok"], out
    assert out["chip"] == "esp32s3" and out["arch"] == "xtensa"
    assert out["elf_sha256_match"] is True
    assert out["first_project_frame"]["function"] == "Face::draw(Avatar*)"
    assert out["first_project_frame"]["location"].endswith("face.cpp:57")
    f0 = out["frames"][0]
    assert any(l.startswith(">   57 |") and "avatar->face->width" in l for l in f0["source"])
    framework = [f for f in out["frames"] if "freertos" in (f["file"] or "")][0]
    assert framework["in_project"] is False
    assert out["panic"]["exception"] == "EXCCAUSE 28 = LoadProhibited"
    assert any("NULL" in x for x in out["hints"])
    assert out["warnings"] == []


def test_decode_with_mocked_subprocess_and_sha_mismatch(tmp_path, fixture_text, monkeypatch):
    src, elf, _sha = _make_project(tmp_path)
    exe = tmp_path / "xtensa-esp32s3-elf-addr2line.exe"
    exe.write_text("")
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        body = "\n".join(a + ": " + ADDR_MAP.get(a, "?? ??:0").format(src="C:/proj/src") for a in cmd[4:])
        return subprocess.CompletedProcess(cmd, 0, stdout=body + "\n", stderr="")

    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    h = make_handlers()
    out = json.loads(h["esp32_decode_backtrace"]({
        "log_text": fixture_text("s3_loadprohibited_bootloop.log"), "elf_path": str(elf),
        "addr2line_path": str(exe)}))
    assert out["ok"] and out["elf_sha256_match"] is False
    assert any("ELF mismatch" in w for w in out["warnings"])
    cmd, kw = calls[0]
    assert cmd[:4] == [str(exe), "-pfiaC", "-e", str(elf)] and kw["timeout"] > 0
    assert out["first_project_frame"]["location"] == "C:\\proj\\src\\face.cpp:57"


def test_decode_errors(tmp_path, fixture_text):
    h = make_handlers()
    assert json.loads(h["esp32_decode_backtrace"]({"log_text": "hello", "elf_path": "x.elf"}))["error"] == "no_backtrace"
    log = fixture_text("s3_loadprohibited_bootloop.log")
    assert json.loads(h["esp32_decode_backtrace"]({"log_text": log, "elf_path": str(tmp_path / "missing.elf")}))["error"] == "bad_elf"
    binfile = tmp_path / "firmware.bin"
    binfile.write_bytes(b"\xe9\x05\x02\x2f" + b"\x00" * 10)
    out = json.loads(h["esp32_decode_backtrace"]({"log_text": log, "elf_path": str(binfile)}))
    assert out["error"] == "bad_elf" and ".bin" in out["message"]
    elf = tmp_path / "a.elf"
    elf.write_bytes(b"\x7fELF" + b"\x00" * 20)
    out = json.loads(h["esp32_decode_backtrace"]({"log_text": log, "elf_path": str(elf),
                                                  "addr2line_path": str(tmp_path / "notepad.exe")}))
    assert out["error"] == "addr2line_rejected"


def test_decode_addr2line_not_found(tmp_path, fixture_text, monkeypatch):
    monkeypatch.setattr(toolchain, "_home", lambda: tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("IDF_TOOLS_PATH", raising=False)
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: None)
    elf = tmp_path / "a.elf"
    elf.write_bytes(b"\x7fELF" + b"\x00" * 20)
    out = json.loads(make_handlers()["esp32_decode_backtrace"]({
        "log_text": fixture_text("c3_riscv_store_fault.log"), "elf_path": str(elf)}))
    assert out["error"] == "addr2line_not_found" and out["arch"] == "riscv"
    assert out["addresses"] == ["0x42005f32", "0x42005f2c"]


def test_addr2line_timeout(monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(bt.subprocess, "run", boom)
    frames, err = bt.run_addr2line("xtensa-esp32s3-elf-addr2line", "a.elf", ["0x42000000"], timeout=1)
    assert frames == [] and "timed out" in err
