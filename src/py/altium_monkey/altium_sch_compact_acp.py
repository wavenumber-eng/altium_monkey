"""Portable Windows-ACP adaptation for compact schematic strings."""

from __future__ import annotations

from .altium_text_codec import decode_altium_ansi, encode_altium_ansi_lossy


def encode_compact_acp(value: str) -> bytes:
    """Encode fixed CP1252 while preserving governed undefined C1 controls."""
    return encode_altium_ansi_lossy(value)


def decode_compact_acp(value: bytes) -> str:
    """Decode fixed CP1252 while preserving governed undefined C1 controls."""
    return decode_altium_ansi(value)
