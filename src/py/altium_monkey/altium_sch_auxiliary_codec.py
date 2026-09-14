"""Encode and decode framed schematic auxiliary streams."""

from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from .altium_sch_compact_acp import decode_compact_acp, encode_compact_acp


AuxiliaryErrorKind = Literal[
    "decode", "decompression", "limit", "malformed", "truncated"
]


class SchAuxiliaryStreamError(ValueError):
    """Structured failure for a malformed or resource-exhausting auxiliary stream."""

    def __init__(self, kind: AuxiliaryErrorKind, offset: int, reason: str) -> None:
        self.kind = kind
        self.offset = offset
        self.reason = reason
        super().__init__(f"{kind} auxiliary stream error at offset {offset}: {reason}")


@dataclass(frozen=True)
class SchAuxiliaryReadLimits:
    """Reviewed bounds applied while decoding untrusted auxiliary streams."""

    max_stream_bytes: int = 67_108_864
    max_records_per_stream: int = 1_000_000
    max_record_bytes: int = 16_777_215
    max_compressed_blob_bytes: int = 67_108_864
    max_decompressed_blob_bytes: int = 268_435_456
    max_total_decompressed_blob_bytes: int = 536_870_912

    def __post_init__(self) -> None:
        values = (
            ("max_stream_bytes", self.max_stream_bytes, 67_108_864),
            ("max_records_per_stream", self.max_records_per_stream, 1_000_000),
            ("max_record_bytes", self.max_record_bytes, 16_777_215),
            (
                "max_compressed_blob_bytes",
                self.max_compressed_blob_bytes,
                67_108_864,
            ),
            (
                "max_decompressed_blob_bytes",
                self.max_decompressed_blob_bytes,
                268_435_456,
            ),
            (
                "max_total_decompressed_blob_bytes",
                self.max_total_decompressed_blob_bytes,
                536_870_912,
            ),
        )
        for field_name, value, reviewed_maximum in values:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _error(
                    "limit",
                    0,
                    f"{field_name} must be a nonnegative integer",
                )
            if value > reviewed_maximum:
                raise _error(
                    "limit",
                    0,
                    f"{field_name} {value} exceeds reviewed maximum {reviewed_maximum}",
                )


@dataclass(frozen=True)
class SchAuxiliaryEntry:
    """One decoded name and compressed payload record."""

    name: str
    data: bytes
    compressed_data: bytes
    binary_header: bytes
    offset: int


@dataclass(frozen=True)
class _ManagedAuxiliaryReadResult:
    """Internal result for the bounded Altium-managed reader profile."""

    entries: tuple[SchAuxiliaryEntry, ...]
    declared_weight: int
    header_terminated: bool
    unread_suffix: bytes
    diagnostics: tuple[str, ...]


def _error(
    kind: AuxiliaryErrorKind, offset: int, reason: str
) -> SchAuxiliaryStreamError:
    return SchAuxiliaryStreamError(kind, offset, reason)


def _require_available(data: bytes, offset: int, size: int, field: str) -> None:
    if offset + size > len(data):
        available = max(len(data) - offset, 0)
        raise _error(
            "truncated",
            offset,
            f"{field} requires {size} bytes but only {available} remain",
        )


def _read_u32(data: bytes, offset: int, field: str) -> int:
    _require_available(data, offset, 4, field)
    return struct.unpack_from("<I", data, offset)[0]


def _decode_header(
    data: bytes, expected_header: str, limits: SchAuxiliaryReadLimits
) -> tuple[int, int]:
    header_length = _read_u32(data, 0, "header length")
    if header_length > limits.max_record_bytes:
        raise _error(
            "limit",
            0,
            f"header length {header_length} exceeds {limits.max_record_bytes}",
        )
    _require_available(data, 4, header_length, "header payload")
    payload = data[4 : 4 + header_length]
    if not payload or payload[-1] != 0:
        raise _error("malformed", 4, "header is not null terminated")
    try:
        text = payload[:-1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise _error("decode", 4 + exc.start, "header is not ASCII") from exc

    pairs = _parse_header_pairs(text)
    if pairs.pop("HEADER", None) != expected_header:
        raise _error("malformed", 4, f"expected HEADER={expected_header}")
    weight_text = pairs.pop("Weight", "0")
    if pairs:
        raise _error("malformed", 4, "unexpected auxiliary header fields")
    weight = _parse_weight(weight_text, limits)
    return 4 + header_length, weight


def _parse_header_pairs(text: str) -> dict[str, str]:
    fields = text.split("|")
    if not fields or fields[0] != "":
        raise _error("malformed", 4, "header must start with '|'")
    pairs: dict[str, str] = {}
    for field in fields[1:]:
        key, separator, value = field.partition("=")
        if not separator or not key or key in pairs:
            raise _error("malformed", 4, f"invalid header field {field!r}")
        pairs[key] = value
    return pairs


def _parse_weight(weight_text: str, limits: SchAuxiliaryReadLimits) -> int:
    if not weight_text.isascii() or not weight_text.isdigit():
        raise _error("malformed", 4, "Weight must be an unsigned decimal integer")
    normalized = weight_text.lstrip("0") or "0"
    maximum = str(limits.max_records_per_stream)
    if len(normalized) > len(maximum) or (
        len(normalized) == len(maximum) and normalized > maximum
    ):
        raise _error(
            "limit",
            4,
            f"record count exceeds {limits.max_records_per_stream}",
        )
    return int(normalized)


def _decompress_zlib(
    compressed: bytes,
    *,
    offset: int,
    limits: SchAuxiliaryReadLimits,
    total_before: int,
) -> bytes:
    if len(compressed) > limits.max_compressed_blob_bytes:
        raise _error(
            "limit",
            offset,
            f"compressed payload exceeds {limits.max_compressed_blob_bytes} bytes",
        )
    decoder = zlib.decompressobj()
    try:
        output = decoder.decompress(compressed, limits.max_decompressed_blob_bytes + 1)
    except zlib.error as exc:
        raise _error("decompression", offset, str(exc)) from exc
    if len(output) > limits.max_decompressed_blob_bytes or decoder.unconsumed_tail:
        raise _error(
            "limit",
            offset,
            f"decompressed payload exceeds {limits.max_decompressed_blob_bytes} bytes",
        )
    if not decoder.eof:
        raise _error("decompression", offset, "truncated zlib stream")
    if decoder.unused_data:
        raise _error("malformed", offset, "trailing bytes after zlib stream")
    total = total_before + len(output)
    if total > limits.max_total_decompressed_blob_bytes:
        raise _error(
            "limit",
            offset,
            f"aggregate decompressed payload exceeds {limits.max_total_decompressed_blob_bytes} bytes",
        )
    return output


def decode_auxiliary_stream(
    data: bytes,
    *,
    expected_header: str,
    limits: SchAuxiliaryReadLimits | None = None,
) -> tuple[SchAuxiliaryEntry, ...]:
    """Decode one complete auxiliary stream without accepting partial data."""
    active_limits = limits or SchAuxiliaryReadLimits()
    if len(data) > active_limits.max_stream_bytes:
        raise _error(
            "limit",
            0,
            f"stream length {len(data)} exceeds {active_limits.max_stream_bytes}",
        )
    cursor, weight = _decode_header(data, expected_header, active_limits)
    entries: list[SchAuxiliaryEntry] = []
    names: set[str] = set()
    total_decompressed = 0
    for record_index in range(weight):
        entry, cursor = _decode_entry(
            data,
            cursor,
            record_index,
            active_limits,
            total_decompressed,
        )
        name = entry.name
        if name in names:
            raise _error(
                "malformed", entry.offset, f"duplicate auxiliary name {name!r}"
            )
        names.add(name)
        total_decompressed += len(entry.data)
        entries.append(entry)
    if cursor != len(data):
        raise _error("malformed", cursor, "trailing bytes after declared records")
    return tuple(entries)


def _decode_managed_auxiliary_stream(
    data: bytes,
    *,
    expected_header: str,
    limits: SchAuxiliaryReadLimits | None = None,
) -> _ManagedAuxiliaryReadResult:
    """Decode the row prefix selected by Altium's managed SchLib reader."""
    active_limits = limits or SchAuxiliaryReadLimits()
    if len(data) > active_limits.max_stream_bytes:
        raise _error(
            "limit",
            0,
            f"stream length {len(data)} exceeds {active_limits.max_stream_bytes}",
        )
    cursor, weight, terminated, diagnostics = _decode_managed_header(
        data, expected_header, active_limits
    )
    selected_count = max(weight, 0)
    if selected_count > active_limits.max_records_per_stream:
        raise _error(
            "limit",
            4,
            f"record count exceeds {active_limits.max_records_per_stream}",
        )

    entries: list[SchAuxiliaryEntry] = []
    total_decompressed = 0
    for record_index in range(selected_count):
        entry, cursor = _decode_entry(
            data,
            cursor,
            record_index,
            active_limits,
            total_decompressed,
        )
        total_decompressed += len(entry.data)
        entries.append(entry)

    unread_suffix = data[cursor:]
    if unread_suffix:
        diagnostics.append("unread_suffix")
    return _ManagedAuxiliaryReadResult(
        entries=tuple(entries),
        declared_weight=weight,
        header_terminated=terminated,
        unread_suffix=unread_suffix,
        diagnostics=tuple(diagnostics),
    )


def _first_managed_storage_entries(
    entries: tuple[SchAuxiliaryEntry, ...],
) -> tuple[SchAuxiliaryEntry, ...]:
    """Retain the first selected entry for each portable lowercased name."""
    selected: list[SchAuxiliaryEntry] = []
    names: set[str] = set()
    for entry in entries:
        folded = entry.name.lower()
        if folded in names:
            continue
        names.add(folded)
        selected.append(entry)
    return tuple(selected)


def _decode_managed_header(
    data: bytes,
    expected_header: str,
    limits: SchAuxiliaryReadLimits,
) -> tuple[int, int, bool, list[str]]:
    header_value = _read_u32(data, 0, "header length")
    mode = header_value >> 24
    if mode != 0:
        raise _error("malformed", 0, "auxiliary header is not text mode 0")
    header_length = header_value & 0x00FF_FFFF
    if header_length > limits.max_record_bytes:
        raise _error(
            "limit",
            0,
            f"header length {header_length} exceeds {limits.max_record_bytes}",
        )
    _require_available(data, 4, header_length, "header payload")
    payload = data[4 : 4 + header_length]
    terminated = bool(payload) and payload[-1] == 0
    diagnostics = [] if terminated else ["header_not_null_terminated"]

    # The managed reader reserves the final declared byte as its terminator even
    # when that byte is nonzero. A missing NUL therefore truncates the final
    # Weight digit instead of exposing the physical count.
    semantic_payload = payload[:-1] if payload else b""
    text = decode_compact_acp(semantic_payload)
    pairs, header_diagnostics = _parse_managed_header_pairs(text)
    diagnostics.extend(header_diagnostics)

    header = pairs.get("header")
    if header is None:
        diagnostics.append("missing_header")
    elif header != expected_header:
        diagnostics.append("header_name_mismatch")
    weight = _managed_weight(pairs, diagnostics)
    return 4 + header_length, weight, terminated, diagnostics


def _managed_weight(pairs: Mapping[str, str], diagnostics: list[str]) -> int:
    weight_text = pairs.get("weight")
    if weight_text is None:
        diagnostics.append("missing_weight")
        return 0
    weight = _try_parse_managed_i32(weight_text)
    if weight is None:
        diagnostics.append("invalid_weight")
        return 0
    if weight < 0:
        diagnostics.append("negative_weight")
    return weight


def _parse_managed_header_pairs(text: str) -> tuple[dict[str, str], list[str]]:
    pairs: dict[str, str] = {}
    diagnostics: list[str] = []
    for field in text.split("|"):
        if not field:
            continue
        key, separator, value = field.partition("=")
        key = key.strip()
        if not separator or not key:
            diagnostics.append("malformed_header_field")
            continue
        normalized_key = key.casefold()
        if normalized_key in pairs:
            diagnostics.append("duplicate_header_field")
            continue
        pairs[normalized_key] = value.strip()
    if set(pairs) - {"header", "weight"}:
        diagnostics.append("extra_header_fields")
    return pairs, diagnostics


_MANAGED_I32_PATTERN = re.compile(r"[+-]?[0-9]+", re.ASCII)
_MANAGED_NUMERIC_WHITESPACE = " \t\r\n\v\f"


def _try_parse_managed_i32(value: str) -> int | None:
    """Return the invariant subset of managed ``Int32.TryParse`` semantics."""
    text = value.strip(_MANAGED_NUMERIC_WHITESPACE)
    if not _MANAGED_I32_PATTERN.fullmatch(text):
        return None
    parsed = int(text)
    if parsed < -(1 << 31) or parsed > (1 << 31) - 1:
        return None
    return parsed


def _decode_entry(
    data: bytes,
    record_offset: int,
    record_index: int,
    limits: SchAuxiliaryReadLimits,
    total_decompressed: int,
) -> tuple[SchAuxiliaryEntry, int]:
    binary_header, cursor, record_end = _decode_record_frame(
        data, record_offset, record_index, limits
    )
    if data[cursor] != 0xD0:
        raise _error("malformed", cursor, "binary record marker is not 0xD0")
    cursor += 1
    name_length = data[cursor]
    cursor += 1
    if cursor + name_length + 4 > record_end:
        raise _error("truncated", cursor, "name exceeds the framed record body")
    name = decode_compact_acp(data[cursor : cursor + name_length])
    cursor += name_length
    compressed_length = _read_u32(data, cursor, "compressed payload length")
    cursor += 4
    if cursor + compressed_length != record_end:
        raise _error(
            "malformed", cursor, "compressed length does not match record body"
        )
    compressed = data[cursor:record_end]
    payload = _decompress_zlib(
        compressed,
        offset=cursor,
        limits=limits,
        total_before=total_decompressed,
    )
    return (
        SchAuxiliaryEntry(
            name=name,
            data=payload,
            compressed_data=compressed,
            binary_header=binary_header,
            offset=record_offset,
        ),
        record_end,
    )


def _decode_record_frame(
    data: bytes,
    record_offset: int,
    record_index: int,
    limits: SchAuxiliaryReadLimits,
) -> tuple[bytes, int, int]:
    header_value = _read_u32(data, record_offset, "binary record header")
    mode = header_value >> 24
    if mode != 0x01:
        raise _error(
            "malformed",
            record_offset,
            f"record {record_index} has binary mode 0x{mode:02X}, expected 0x01",
        )
    record_length = header_value & 0x00FF_FFFF
    if record_length > limits.max_record_bytes:
        raise _error(
            "limit",
            record_offset,
            f"record length {record_length} exceeds {limits.max_record_bytes}",
        )
    cursor = record_offset + 4
    _require_available(data, cursor, record_length, "binary record body")
    if record_length < 6:
        raise _error("malformed", record_offset, "binary record body is too short")
    return data[record_offset:cursor], cursor, cursor + record_length


def _encode_name(name: str, offset: int) -> bytes:
    encoded = encode_compact_acp(name)
    if len(encoded) > 0xFF:
        raise _error(
            "limit",
            offset,
            f"ACP name length {len(encoded)} exceeds the one-byte limit",
        )
    return encoded


def _usable_raw_compressed(
    name: str,
    payload: bytes,
    raw_compressed: Mapping[str, bytes] | None,
) -> bytes | None:
    if raw_compressed is None:
        return None
    compressed = raw_compressed.get(name)
    if compressed is None:
        return None
    try:
        decoded = _decompress_zlib(
            compressed,
            offset=0,
            limits=SchAuxiliaryReadLimits(),
            total_before=0,
        )
        if decoded == payload:
            return compressed
    except SchAuxiliaryStreamError:
        pass
    return None


def encode_auxiliary_stream(
    header: str,
    entries: Iterable[tuple[str, bytes]],
    *,
    raw_compressed: Mapping[str, bytes] | None = None,
) -> bytes:
    """Encode an ordered auxiliary stream using managed binary framing."""
    if not header or not header.isascii() or "|" in header or "=" in header:
        raise _error("malformed", 0, "header must be a simple ASCII name")
    prepared = _prepare_entries(entries)
    header_text = f"|HEADER={header}"
    if prepared:
        header_text += f"|Weight={len(prepared)}"
    header_payload = header_text.encode("ascii") + b"\x00"
    output = bytearray(struct.pack("<I", len(header_payload)))
    output.extend(header_payload)
    for encoded_name, name, payload in prepared:
        compressed = _usable_raw_compressed(name, payload, raw_compressed)
        if compressed is None:
            compressed = zlib.compress(payload)
        record_length = 1 + 1 + len(encoded_name) + 4 + len(compressed)
        if record_length > 0x00FF_FFFF:
            raise _error("limit", len(output), "binary record exceeds 24-bit framing")
        output.extend(struct.pack("<I", 0x0100_0000 | record_length))
        output.append(0xD0)
        output.append(len(encoded_name))
        output.extend(encoded_name)
        output.extend(struct.pack("<I", len(compressed)))
        output.extend(compressed)
    return bytes(output)


def _prepare_entries(
    entries: Iterable[tuple[str, bytes]],
) -> list[tuple[bytes, str, bytes]]:
    prepared: list[tuple[bytes, str, bytes]] = []
    encoded_names: set[bytes] = set()
    for name, payload in entries:
        encoded = _encode_name(name, 0)
        if encoded in encoded_names:
            raise _error("malformed", 0, "duplicate encoded auxiliary name")
        encoded_names.add(encoded)
        prepared.append((encoded, name, payload))
    return prepared
