"""Shared helpers for PCB text-backed stream encoding and simple tokens."""

from __future__ import annotations

import struct


def build_length_prefixed_ascii(body: str) -> bytes:
    """Encode an ASCII body as `[uint32 len][payload]`."""
    body_bytes = body.encode("latin-1")
    return struct.pack("<I", len(body_bytes)) + body_bytes


def extract_length_prefixed_ascii(data: bytes) -> str:
    """Decode an ASCII `[uint32 len][payload]` stream body."""
    if len(data) < 4:
        raise ValueError("Invalid length-prefixed stream")
    length = struct.unpack("<I", data[:4])[0]
    if len(data) < 4 + length:
        raise ValueError("Invalid length-prefixed stream")
    return data[4 : 4 + length].decode("latin-1").rstrip("\x00")


def count_length_prefixed_records(data: bytes | None) -> int:
    """Count packed `[uint32 len][payload]` records in a stream."""
    if not data:
        return 0

    count = 0
    offset = 0
    while offset + 4 <= len(data):
        record_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if offset + record_len > len(data):
            break
        offset += record_len
        count += 1
    return count


class PcbKeyValueTextEntryMixin:
    """Shared `raw` text entry accessors for PCB builder segment records."""

    raw: str

    @property
    def key(self) -> str | None:
        if "=" not in self.raw:
            return None
        return self.raw.split("=", 1)[0]

    @property
    def value(self) -> str | None:
        if "=" not in self.raw:
            return None
        return self.raw.split("=", 1)[1]


def format_mil_value(value_mils: float) -> str:
    """Format a mil-valued float using Altium's compact text form."""
    return f"{format(float(value_mils), 'g')}mil"


def format_bool_text(value: bool) -> str:
    """Format a boolean using Altium's `TRUE` / `FALSE` tokens."""
    return "TRUE" if value else "FALSE"


def parse_altium_bool_token(value: object) -> bool | None:
    """Parse common Altium truthy and falsy text tokens."""
    token = str(value or "").strip().upper()
    if not token:
        return None
    if token in {"TRUE", "T", "1"}:
        return True
    if token in {"FALSE", "F", "0"}:
        return False
    return None


def parse_altium_int_token(value: object) -> int | None:
    """Parse a text token as an integer when possible."""
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return int(token)
    except (TypeError, ValueError):
        return None
