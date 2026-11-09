import json

from hermes_esp32_debug import ports
from hermes_esp32_debug.tools import make_handlers


def test_classify():
    assert ports.classify(0x303A, 0x1001)[:2] == (True, "Espressif native USB (USB-Serial-JTAG / TinyUSB CDC)")
    assert ports.classify(0x303A, 0x1001)[2] == "high"
    assert ports.classify(0x10C4, 0xEA60)[0] is True
    assert ports.classify(0x10C4, 0x0001)[0] is False
    assert ports.classify(0x1A86, 0x7523)[0] is True
    assert ports.classify(None, None) == (False, None, "none")


def test_list_ports_sorted_and_flagged(fake_list_ports):
    rows = ports.list_ports(fake_list_ports)
    assert rows[0]["port"] == "COM5" and rows[0]["confidence"] == "high"
    assert rows[0]["vid_pid"] == "303a:1001"
    esp = [r["port"] for r in rows if r["esp_like"]]
    assert set(esp) == {"COM5", "COM7", "COM9"}
    com1 = next(r for r in rows if r["port"] == "COM1")
    assert com1["vid"] is None and com1["esp_like"] is False


def test_handler_esp_only(fake_list_ports):
    h = make_handlers(list_ports_module=fake_list_ports)
    out = json.loads(h["esp32_list_ports"]({"esp_only": True}))
    assert out["ok"] and out["count"] == 3
    assert out["esp_like_ports"][0] == "COM5"


def test_handler_never_raises():
    class Boom:
        def comports(self):
            raise RuntimeError("driver exploded")
    out = json.loads(make_handlers(list_ports_module=Boom())["esp32_list_ports"]({}))
    assert out["ok"] is False and out["error"] == "list_failed"
