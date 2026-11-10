"""Security fixes requested on the catalog PR (addr2line setting, isolated exec, redact ReDoS)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
import time

from hermes_esp32_debug import chipinfo, proc, schemas
from hermes_esp32_debug import backtrace as bt
from hermes_esp32_debug.proc import LOG_TEXT_MAX_BYTES, isolated_env
from hermes_esp32_debug.redact import redact
from hermes_esp32_debug.tools import default_cache_dir, make_handlers
from test_chipinfo import ESPTOOL5_FLASH_ID, Runner


def test_schema_has_no_addr2line_path():
    props = schemas.DECODE_BACKTRACE["parameters"]["properties"]
    assert "addr2line_path" not in props
    assert "elf_path" in props and "log_text" in props


def test_addr2line_path_in_args_is_ignored(tmp_path, fixture_text, monkeypatch):
    """A model-supplied path must not be executed, even if it looks like Espressif addr2line."""
    elf = tmp_path / "a.elf"
    elf.write_bytes(b"\x7fELF" + b"\x00" * 20)
    planted = tmp_path / "xtensa-esp32s3-elf-addr2line.exe"
    planted.write_text("")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="0x42003b5e: ?? ??:0\n", stderr="")

    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    monkeypatch.setattr("hermes_esp32_debug.toolchain._home", lambda: tmp_path / "nohome")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nolocal"))
    monkeypatch.delenv("IDF_TOOLS_PATH", raising=False)
    monkeypatch.setattr("hermes_esp32_debug.toolchain.shutil.which", lambda name: None)
    monkeypatch.setattr("hermes_esp32_debug.toolchain.search_roots",
                        lambda extra_dirs=(): [Path(d).expanduser() for d in extra_dirs if d])
    # no setting — args must not fill in the gap
    h = make_handlers()
    out = json.loads(h["esp32_decode_backtrace"]({
        "log_text": fixture_text("s3_loadprohibited_bootloop.log"),
        "elf_path": str(elf),
        "addr2line_path": str(planted),
    }))
    assert out["error"] == "addr2line_not_found"
    assert calls == []

    # setting is honoured; a different path in args is still ignored
    setting_exe = tmp_path / "xtensa-esp-elf-addr2line.exe"
    setting_exe.write_text("")
    h = make_handlers(lambda k, d=None: str(setting_exe) if k == "addr2line_path" else d)
    out = json.loads(h["esp32_decode_backtrace"]({
        "log_text": fixture_text("s3_loadprohibited_bootloop.log"),
        "elf_path": str(elf),
        "addr2line_path": str(planted),
    }))
    assert out["ok"], out
    assert calls and calls[0][0] == str(setting_exe)
    assert str(planted) not in calls[0]


def test_esptool_python_uses_safe_path_flag_neutral_cwd_and_minimal_env(tmp_path, monkeypatch):
    monkeypatch.setattr(chipinfo, "autodetect_esptool", lambda: None)
    r = Runner([(0, ESPTOOL5_FLASH_ID)])
    work = tmp_path / "plugin-data"
    work.mkdir()
    res = chipinfo.chip_info("COM5", python="C:/Python312/python.exe", runner=r, cwd=work)
    assert res["ok"]
    cmd, kw = r.calls[0]
    assert cmd[:4] == ["C:/Python312/python.exe", "-P", "-m", "esptool"]
    assert kw["cwd"] == str(work)
    env = kw["env"]
    assert env is not os.environ and "PYTHONPATH" not in env
    assert "PATH" in env
    if os.name == "nt":
        assert "SYSTEMROOT" in env and "TEMP" in env


def test_isolated_env_drops_secrets_and_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/tmp/evil")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    env = isolated_env()
    assert "PYTHONPATH" not in env
    assert "OPENAI_API_KEY" not in env
    assert "HTTP_PROXY" not in env
    assert "PATH" in env


def test_addr2line_subprocess_uses_cwd_and_minimal_env(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="0x42000000: ?? ??:0\n", stderr="")

    monkeypatch.setattr(bt.subprocess, "run", fake_run)
    work = tmp_path / "data"
    work.mkdir()
    frames, err = bt.run_addr2line("xtensa-esp32s3-elf-addr2line", "a.elf", ["0x42000000"], cwd=work)
    assert err is None and frames
    cmd, kw = calls[0]
    assert kw["cwd"] == str(work)
    assert "PYTHONPATH" not in kw["env"]
    assert kw["env"] is not os.environ


def test_url_userinfo_still_masks_credentials():
    s = redact("mqtt://user:fakepw@broker")
    assert "fakepw" not in s and "<redacted>@" in s


def test_url_userinfo_not_quadratic_on_256kib_dotted_line():
    line = "a" + ("." * (256 * 1024))
    t0 = time.perf_counter()
    out = redact(line)
    elapsed = time.perf_counter() - t0
    assert out  # redaction must finish
    assert elapsed < 1.0, f"redact took {elapsed:.3f}s on a 256 KiB dotted line"


def test_log_text_over_capture_cap_is_rejected(tmp_path):
    elf = tmp_path / "a.elf"
    elf.write_bytes(b"\x7fELF" + b"\x00" * 20)
    huge = "x" * (LOG_TEXT_MAX_BYTES + 1)
    out = json.loads(make_handlers(cache_dir=tmp_path)["esp32_decode_backtrace"]({
        "log_text": huge, "elf_path": str(elf),
    }))
    assert out["error"] == "log_text_too_long"
    assert out["max_bytes"] == LOG_TEXT_MAX_BYTES


def test_last_capture_lives_in_plugin_data_dir_with_owner_mode(fake_serial_module, tmp_path, monkeypatch):
    monkeypatch.delenv("ESP32_DEBUG_CACHE_DIR", raising=False)
    home = tmp_path / "hermes-home"
    monkeypatch.setattr(proc, "_hermes_home", lambda: home)
    monkeypatch.setattr(proc, "_official_plugin_data_dir", lambda: None)
    data = proc.plugin_data_dir(create=True)
    assert data == home / "plugin-data" / "esp32-debug"
    assert default_cache_dir() == data
    assert data != Path(tempfile.gettempdir()) / "hermes-esp32-debug"

    h = make_handlers(serial_module=fake_serial_module(b"Guru Meditation Error: Core 1 panic'ed (LoadProhibited)\n"),
                      cache_dir=data)
    out = json.loads(h["esp32_serial_capture"]({"port": "COM7", "seconds": 1}))
    assert out["ok"]
    path = data / "last_capture.json"
    assert path.is_file()
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name != "nt":
        assert mode == 0o600
    # On Windows chmod(0600) is best-effort (DACLs are not POSIX modes); the file still exists.
    assert mode != 0
