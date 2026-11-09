import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Keep the last-capture cache out of the real temp dir."""
    monkeypatch.setenv("ESP32_DEBUG_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def fixture_text():
    def _read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return _read


class FakePortInfo:
    def __init__(self, device, vid=None, pid=None, description="n/a", manufacturer=None, serial_number=None):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.description = description
        self.manufacturer = manufacturer
        self.serial_number = serial_number
        self.hwid = f"USB VID:PID={vid:04X}:{pid:04X}" if vid is not None else "n/a"


class FakeListPorts:
    def __init__(self, ports):
        self._ports = ports

    def comports(self):
        return list(self._ports)


class FakeSerial:
    """Mimics pyserial.Serial enough for capture(): unopened ctor, attributes, open/read/close."""

    data: bytes = b""
    open_error: Exception | None = None
    instances: list = []

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.write_timeout = None
        self.dtr = None
        self.rts = None
        self.is_open = False
        self.closed = False
        self.rts_history = []
        self.dtr_writes = 0
        self._buf = bytearray(type(self).data)
        type(self).instances.append(self)

    def __setattr__(self, k, v):
        if k == "rts" and getattr(self, "rts_history", None) is not None:
            self.rts_history.append(v)
        if k == "dtr" and getattr(self, "dtr_writes", None) is not None:
            object.__setattr__(self, "dtr_writes", self.dtr_writes + 1)
        object.__setattr__(self, k, v)

    def open(self):
        if type(self).open_error:
            raise type(self).open_error
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self._buf)

    def reset_input_buffer(self):
        pass

    def read(self, n=1):
        chunk = bytes(self._buf[:min(n, 64)])
        del self._buf[:len(chunk)]
        return chunk

    def write(self, *_a, **_k):  # pragma: no cover - must never be called
        raise AssertionError("capture must never write to the device")

    def close(self):
        self.closed = True
        self.is_open = False


@pytest.fixture
def fake_serial_module():
    def make(data: bytes = b"", open_error: Exception | None = None):
        cls = type("FakeSerialX", (FakeSerial,), {"data": data, "open_error": open_error, "instances": []})
        mod = type("serialmod", (), {"Serial": cls})
        return mod
    return make


@pytest.fixture
def fake_list_ports():
    return FakeListPorts([
        FakePortInfo("COM3", 0x8087, 0x0AAA, "Intel(R) Active Management Technology - SOL (COM3)"),
        FakePortInfo("COM7", 0x10C4, 0xEA60, "Silicon Labs CP210x USB to UART Bridge (COM7)", "Silicon Labs"),
        FakePortInfo("COM5", 0x303A, 0x1001, "USB Serial Device (COM5)", "Espressif"),
        FakePortInfo("COM9", 0x1A86, 0x55D4, "USB-Enhanced-SERIAL CH9102 (COM9)", "wch.cn"),
        FakePortInfo("COM1", None, None, "Communications Port (COM1)"),
    ])
