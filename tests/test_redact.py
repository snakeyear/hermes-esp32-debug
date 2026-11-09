from hermes_esp32_debug.redact import redact


def test_masks_common_secrets():
    s = redact("ssid=HomeNet password=\"p@ss w\" token: abcdef123456 api_key=XYZ")
    assert "HomeNet" not in s and "abcdef123456" not in s and "XYZ" not in s
    assert "ssid=<redacted>" in s


def test_masks_wifi_driver_line_and_bssid():
    s = redact("I (612) wifi:connected with MyHomeAP-5G, aid = 3, channel 6, bssid = 12:34:56:78:9a:bc")
    assert "MyHomeAP-5G" not in s and "12:34:56:78:9a:bc" not in s


def test_masks_ips_but_not_hex_addresses_or_timestamps():
    s = redact("gw: 192.168.1.1 Backtrace: 0x42003b5b:0x3fcebf40 at 12:34:56 v6 fe80::1ff:fe23:4567:890a")
    assert "192.168.1.1" not in s and "fe80::1ff" not in s
    assert "0x42003b5b:0x3fcebf40" in s and "12:34:56" in s


def test_masks_url_credentials_and_query_tokens():
    s = redact("mqtt://user:fakepw@broker https://x.example/api?token=abc123&x=1 Authorization: Bearer abcdefghijklmnop")
    assert "fakepw" not in s and "abc123" not in s and "abcdefghijklmnop" not in s


def test_keeps_elf_sha_and_mac_in_chip_output():
    s = redact("ELF file SHA256: 5a1c2e9f0b7d4c33aa5a1c2e9f0b7d4c33aa5a1c2e9f0b7d4c33\nMAC: 34:85:18:aa:bb:cc")
    assert "5a1c2e9f0b7d4c33" in s and "34:85:18:aa:bb:cc" in s


def test_idempotent_and_empty():
    assert redact("") == ""
    once = redact("password=abc 10.0.0.2")
    assert redact(once) == once
