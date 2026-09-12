"""Portable Windows-ACP adaptation for compact schematic strings."""

from __future__ import annotations


_UNDEFINED_CP1252_C1_BYTES = frozenset({0x81, 0x8D, 0x8F, 0x90, 0x9D})


def encode_compact_acp(value: str) -> bytes:
    """Encode fixed CP1252 while preserving governed undefined C1 controls."""
    encoded = bytearray()
    for character in value:
        codepoint = ord(character)
        if codepoint in _UNDEFINED_CP1252_C1_BYTES:
            encoded.append(codepoint)
        else:
            encoded.extend(character.encode("cp1252", errors="replace"))
    return bytes(encoded)


def decode_compact_acp(value: bytes) -> str:
    """Decode fixed CP1252 while preserving governed undefined C1 controls."""
    return "".join(
        chr(byte)
        if byte in _UNDEFINED_CP1252_C1_BYTES
        else bytes((byte,)).decode("cp1252")
        for byte in value
    )
