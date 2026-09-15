"""Altium-compatible Windows-1252 text conversion."""

from __future__ import annotations


_MANAGED_C1_BYTES = frozenset({0x81, 0x8D, 0x8F, 0x90, 0x9D})
_CP1252_TO_UNICODE = str.maketrans(
    {
        0x80: 0x20AC,
        0x82: 0x201A,
        0x83: 0x0192,
        0x84: 0x201E,
        0x85: 0x2026,
        0x86: 0x2020,
        0x87: 0x2021,
        0x88: 0x02C6,
        0x89: 0x2030,
        0x8A: 0x0160,
        0x8B: 0x2039,
        0x8C: 0x0152,
        0x8E: 0x017D,
        0x91: 0x2018,
        0x92: 0x2019,
        0x93: 0x201C,
        0x94: 0x201D,
        0x95: 0x2022,
        0x96: 0x2013,
        0x97: 0x2014,
        0x98: 0x02DC,
        0x99: 0x2122,
        0x9A: 0x0161,
        0x9B: 0x203A,
        0x9C: 0x0153,
        0x9E: 0x017E,
        0x9F: 0x0178,
    }
)


def decode_altium_ansi(value: bytes) -> str:
    """Decode code page 1252 with the byte-total .NET compatibility mapping."""

    return value.decode("latin1").translate(_CP1252_TO_UNICODE)


def encode_altium_ansi_lossy(value: str) -> bytes:
    """Encode Altium ANSI exactly for C1 compatibility and replace other gaps."""

    try:
        return value.encode("cp1252")
    except UnicodeEncodeError:
        pass

    encoded = bytearray()
    for character in value:
        codepoint = ord(character)
        if codepoint in _MANAGED_C1_BYTES:
            encoded.append(codepoint)
        else:
            encoded.extend(character.encode("cp1252", errors="replace"))
    return bytes(encoded)
