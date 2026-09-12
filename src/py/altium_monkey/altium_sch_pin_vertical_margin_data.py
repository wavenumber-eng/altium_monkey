"""Decode and encode the private SchLib pin vertical-margin stream."""

from __future__ import annotations

import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from .altium_sch_auxiliary_codec import (
    SchAuxiliaryReadLimits,
    _error,
    decode_auxiliary_stream,
    encode_auxiliary_stream,
)
from .altium_sch_enums import PinItemMode
from ._sch_managed_numeric import split_coord_toward_zero as _split_coord_toward_zero

if TYPE_CHECKING:
    from .altium_record_sch__pin import AltiumSchPin, PinTextSettings


_HEADER: Final = "PinVerticalMarginData"
_DESIGNATOR_KEY: Final = "PINDESIGNATORVERTICALMARGIN"
_NAME_KEY: Final = "PINNAMEVERTICALMARGIN"
_MAX_ENTRY_BYTES: Final = 152
_I32_MIN: Final = -(1 << 31)
_I32_MAX: Final = (1 << 31) - 1
_INDEX_RE: Final = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_I32_RE: Final = re.compile(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z")


@dataclass(frozen=True, slots=True)
class _PinVerticalMargins:
    designator: int | None = None
    name: int | None = None


@dataclass(frozen=True, slots=True)
class _PinVerticalMarginEntry:
    pin_index: int
    margins: _PinVerticalMargins
    payload: bytes
    compressed_data: bytes


def _decode_pin_vertical_margin_stream(
    data: bytes,
    *,
    limits: SchAuxiliaryReadLimits | None = None,
) -> tuple[_PinVerticalMarginEntry, ...]:
    active_limits = _entry_bounded_limits(limits or SchAuxiliaryReadLimits())
    decoded = decode_auxiliary_stream(
        data,
        expected_header=_HEADER,
        limits=active_limits,
    )
    _require_positive_weight(data, len(decoded))
    return tuple(
        _PinVerticalMarginEntry(
            pin_index=_parse_pin_index(entry.name, entry.offset),
            margins=_decode_entry_payload(entry.data, entry.offset),
            payload=entry.data,
            compressed_data=entry.compressed_data,
        )
        for entry in decoded
    )


def _require_positive_weight(data: bytes, decoded_count: int) -> None:
    header_length = struct.unpack_from("<I", data)[0]
    header = data[4 : 4 + header_length].decode("ascii")
    weight_text: str | None = None
    for field in header.removesuffix("\x00").split("|"):
        if field.startswith("Weight="):
            weight_text = field.removeprefix("Weight=")
            break
    if weight_text is None or not weight_text.isdecimal():
        raise _error("malformed", 4, "Weight must be present as unsigned decimal")
    normalized = weight_text.lstrip("0") or "0"
    if decoded_count <= 0 or normalized != str(decoded_count):
        raise _error(
            "malformed",
            4,
            "present vertical-margin stream must have positive exact Weight",
        )


def _entry_bounded_limits(limits: SchAuxiliaryReadLimits) -> SchAuxiliaryReadLimits:
    return SchAuxiliaryReadLimits(
        max_stream_bytes=limits.max_stream_bytes,
        max_records_per_stream=limits.max_records_per_stream,
        max_record_bytes=limits.max_record_bytes,
        max_compressed_blob_bytes=limits.max_compressed_blob_bytes,
        max_decompressed_blob_bytes=min(
            limits.max_decompressed_blob_bytes,
            _MAX_ENTRY_BYTES,
        ),
        max_total_decompressed_blob_bytes=limits.max_total_decompressed_blob_bytes,
    )


def _parse_pin_index(name: str, offset: int) -> int:
    if _INDEX_RE.fullmatch(name) is None:
        raise _error("malformed", offset, "pin index is not canonical decimal")
    value = int(name)
    if value > _I32_MAX:
        raise _error("malformed", offset, "pin index exceeds signed i32")
    return value


def _decode_entry_payload(data: bytes, offset: int) -> _PinVerticalMargins:
    if len(data) > _MAX_ENTRY_BYTES:
        raise _error(
            "limit",
            offset,
            f"vertical-margin payload exceeds {_MAX_ENTRY_BYTES} bytes",
        )
    if len(data) < 4:
        raise _error("truncated", offset, "missing UTF-16 byte length")
    byte_length = struct.unpack_from("<i", data)[0]
    if byte_length < 0:
        raise _error("malformed", offset, "UTF-16 byte length is negative")
    if byte_length != len(data) - 4:
        raise _error("malformed", offset, "UTF-16 byte length does not match payload")
    if byte_length % 2 != 0:
        raise _error("malformed", offset, "UTF-16 byte length must be even")
    try:
        parameters = data[4:].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise _error("decode", offset + 4 + exc.start, "invalid UTF-16LE") from exc
    if "\x00" in parameters:
        raise _error("malformed", offset, "parameter text contains NUL")
    return _parse_parameters(parameters, offset)


def _parse_parameters(parameters: str, offset: int) -> _PinVerticalMargins:
    if not parameters.isascii() or not parameters.startswith("|"):
        raise _error("malformed", offset, "parameter text must start with ASCII '|'")
    fields = parameters[1:].split("|")
    if not fields or any(not field for field in fields):
        raise _error("malformed", offset, "parameter text contains an empty field")
    values: dict[str, int] = {}
    for field in fields:
        key, separator, value = field.partition("=")
        folded = key.casefold()
        if not separator or folded not in {
            _DESIGNATOR_KEY.casefold(),
            _NAME_KEY.casefold(),
        }:
            raise _error("malformed", offset, f"unknown parameter {key!r}")
        if folded in values:
            raise _error("malformed", offset, f"duplicate parameter {key!r}")
        values[folded] = _parse_i32(value, offset)
    return _PinVerticalMargins(
        designator=values.get(_DESIGNATOR_KEY.casefold()),
        name=values.get(_NAME_KEY.casefold()),
    )


def _parse_i32(value: str, offset: int) -> int:
    if _I32_RE.fullmatch(value) is None:
        raise _error("malformed", offset, "margin is not canonical signed decimal")
    parsed = int(value)
    if not _I32_MIN <= parsed <= _I32_MAX:
        raise _error("malformed", offset, "margin exceeds signed i32")
    return parsed


def _encode_pin_vertical_margin_stream(
    pins: Sequence[AltiumSchPin],
    *,
    original_stream: bytes | None = None,
) -> bytes | None:
    original_entries = _raw_entries_by_name(original_stream)
    prepared: list[tuple[str, bytes]] = []
    raw_compressed: dict[str, bytes] = {}
    for pin_index, pin in enumerate(pins):
        margins = _margins_from_pin(pin)
        if margins.designator is None and margins.name is None:
            continue
        name = str(pin_index)
        original = original_entries.get(name)
        if original is not None and original.margins == margins:
            prepared.append((name, original.payload))
            raw_compressed[name] = original.compressed_data
        else:
            prepared.append((name, _encode_entry_payload(margins)))
    if not prepared:
        return None
    return encode_auxiliary_stream(
        _HEADER,
        prepared,
        raw_compressed=raw_compressed or None,
    )


def _raw_entries_by_name(
    original_stream: bytes | None,
) -> dict[str, _PinVerticalMarginEntry]:
    if original_stream is None:
        return {}
    entries = _decode_pin_vertical_margin_stream(original_stream)
    return {str(entry.pin_index): entry for entry in entries}


def _margins_from_pin(pin: AltiumSchPin) -> _PinVerticalMargins:
    return _PinVerticalMargins(
        designator=_margin_from_settings(pin.designator_settings),
        name=_margin_from_settings(pin.name_settings),
    )


def _margin_from_settings(settings: PinTextSettings) -> int | None:
    if settings.position_mode != PinItemMode.CUSTOM:
        return None
    whole = _checked_authored_part(
        settings.position_vertical_margin,
        "vertical-margin whole part",
    )
    fraction = _checked_authored_part(
        settings.position_vertical_margin_frac,
        "vertical-margin fractional part",
    )
    value = whole * 100_000 + fraction
    if not _I32_MIN <= value <= _I32_MAX:
        raise _error(
            "malformed", 0, "authored vertical-margin value exceeds signed i32"
        )
    return value


def _checked_authored_part(value: int | None, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error("malformed", 0, f"authored {field} must be an integer")
    if not _I32_MIN <= value <= _I32_MAX:
        raise _error("malformed", 0, f"authored {field} exceeds signed i32")
    return value


def _encode_entry_payload(margins: _PinVerticalMargins) -> bytes:
    fields: list[str] = []
    if margins.designator is not None:
        fields.append(f"{_DESIGNATOR_KEY}={margins.designator}")
    if margins.name is not None:
        fields.append(f"{_NAME_KEY}={margins.name}")
    if not fields:
        raise _error("malformed", 0, "vertical-margin entry has no fields")
    encoded = ("|" + "|".join(fields)).encode("utf-16-le")
    payload = struct.pack("<i", len(encoded)) + encoded
    if len(payload) > _MAX_ENTRY_BYTES:
        raise _error(
            "limit",
            0,
            f"vertical-margin payload exceeds {_MAX_ENTRY_BYTES} bytes",
        )
    return payload


def _apply_pin_vertical_margins(
    pins: Sequence[AltiumSchPin],
    entries: Sequence[_PinVerticalMarginEntry],
) -> None:
    staged_names = [replace(pin.name_settings) for pin in pins]
    staged_designators = [replace(pin.designator_settings) for pin in pins]
    changed: set[int] = set()
    for entry in entries:
        if entry.pin_index >= len(pins):
            continue
        changed.add(entry.pin_index)
        if entry.margins.designator is not None:
            _apply_margin(
                staged_designators[entry.pin_index],
                entry.margins.designator,
            )
        if entry.margins.name is not None:
            _apply_margin(staged_names[entry.pin_index], entry.margins.name)
    for pin_index in changed:
        pins[pin_index].name_settings = staged_names[pin_index]
        pins[pin_index].designator_settings = staged_designators[pin_index]


def _apply_margin(settings: PinTextSettings, value: int) -> None:
    whole, fraction = _split_coord_toward_zero(value)
    settings.position_mode = PinItemMode.CUSTOM
    settings.position_vertical_margin = whole
    settings.position_vertical_margin_frac = fraction
