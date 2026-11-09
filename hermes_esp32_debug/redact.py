"""Redaction of secrets and network identifiers in captured serial logs.

Serial logs from Wi-Fi firmware routinely print the SSID, the PSK, cloud
tokens and IP addresses. Everything a tool returns to the model goes through
:func:`redact` first (unless the user turns it off in settings).
"""

from __future__ import annotations

import re

MASK = "<redacted>"

# key = value / key: value, where the key looks secret-ish.
_KV_KEYS = (
    r"bssid|ssid|password|passwd|pass|pwd|psk|wifi_pass|wpa_passphrase|token|access_token|"
    r"refresh_token|api[_-]?key|apikey|secret|client_secret|auth|authorization|bearer|"
    r"session|cookie|private[_-]?key|mqtt_pass|mqtt_user|username|user"
)
_KV_RE = re.compile(
    rf"(?i)\b(?P<key>{_KV_KEYS})(?P<sep>\s*[:=]\s*|\s+is\s+)(?P<val>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"',;]+)"
)
# ESP-IDF Wi-Fi driver: "wifi:connected with MyHomeAP, aid = 3, channel 6"
_WIFI_CONNECTED_RE = re.compile(r"(?i)(connected with |connecting to |ssid\s*[\"'])([^,\"'\r\n]+)")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_QUERY_RE = re.compile(r"(?i)([?&](?:token|key|sig|signature|access_token|apikey|api_key|auth|password)=)[^&\s]+")
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
# IPv4 (each octet 0-255). Hex addresses like 0x3fc9 never match (no dots).
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4_RE = re.compile(rf"(?<![\d.]){_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}(?![\d.])")
# IPv6 candidates: hex/colon tokens; _ipv6_sub decides (needs '::' or 8 groups, so MACs survive)
_IPV6_RE = re.compile(r"(?<![\w:])[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{0,4}){2,7}(?![\w:])")
_MAC_RE = re.compile(r"(?i)^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
# Long opaque strings (hex / base64 keys) of 32+ chars that are not addresses.
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{40,}\b")


def _ipv6_sub(match: re.Match) -> str:
    text = match.group(0)
    if _MAC_RE.match(text):
        return text  # MAC addresses (6 groups of 2) are kept; they identify the board
    if ("::" in text and any(c != ":" for c in text)) or text.count(":") == 7:
        return "<ip>"
    return text  # e.g. "12:34:56" timestamps


def redact(text: str, *, ips: bool = True) -> str:
    """Return ``text`` with secrets masked. Idempotent and never raises."""
    if not text:
        return text
    try:
        out = _URL_USERINFO_RE.sub(r"\1<redacted>@", text)
        out = _URL_QUERY_RE.sub(rf"\1{MASK}", out)
        out = _BEARER_RE.sub(rf"\1 {MASK}", out)
        out = _JWT_RE.sub(MASK, out)
        out = _KV_RE.sub(lambda m: f"{m.group('key')}{m.group('sep')}{MASK}", out)
        out = _WIFI_CONNECTED_RE.sub(lambda m: f"{m.group(1)}{MASK}", out)
        out = _LONG_HEX_RE.sub(lambda m: m.group(0) if _looks_like_sha_line(out, m) else MASK, out)
        if ips:
            out = _IPV4_RE.sub("<ip>", out)
            out = _IPV6_RE.sub(_ipv6_sub, out)
        return out
    except Exception:  # pragma: no cover - redaction must never break a tool
        return text


def _looks_like_sha_line(text: str, match: re.Match) -> bool:
    """Keep 'ELF file SHA256: <hex>' — it is a build fingerprint, not a secret."""
    start = max(0, match.start() - 24)
    return "sha256" in text[start:match.start()].lower()
