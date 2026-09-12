"""Strict private helpers for SchDoc container framing and ownership."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .altium_serializer import Fields, _read_param_boolean
from .altium_sch_auxiliary_codec import (
    SchAuxiliaryEntry,
    SchAuxiliaryReadLimits,
    SchAuxiliaryStreamError,
    decode_auxiliary_stream,
)
from .altium_schlib_container import (
    SchLibContainerError,
    _SchLibBudget,
    _SchLibReadLimits,
    _casefold_value,
    _count_field_pairs,
    _parse_i32,
    _parse_instruction_stream,
    _read_record_frame,
    _snapshot_container,
)

SCHDOC_V5_HEADER = "Protel for Windows - Schematic Capture Binary File Version 5.0"
_MODELED_ROOT_STREAMS = (
    "FileHeader",
    "Additional",
    "Storage",
    "ObjectDefinitions",
    "HarnessConnectionPointConnector",
)
_ADDITIONAL_RECORD_IDS = frozenset({138, 215, 216, 217, 218, 220, 221, 225})
_OBJECT_DEFINITION_RECORD_IDS = frozenset({7, 13, 129})

SchDocContainerError = SchLibContainerError
_SchDocBudget = _SchLibBudget
_SchDocReadLimits = _SchLibReadLimits


@dataclass(frozen=True, slots=True)
class _SchDocWarehouse:
    header: dict[str, object]
    records: tuple[dict[str, object], ...]
    stored_weight: int | None

    @property
    def weight_is_stale(self) -> bool:
        if self.stored_weight is None:
            return bool(self.records)
        return self.stored_weight != len(self.records)


def _parse_warehouse(
    data: bytes,
    stream: str,
    budget: _SchDocBudget,
    *,
    weight_required: bool,
) -> _SchDocWarehouse:
    framed = _parse_instruction_stream(
        data,
        stream,
        budget,
        allow_equal_duplicates=True,
        allow_terminal_nul_padding=True,
        always_unique_fields=frozenset({"weight"}),
    )
    if not framed or framed[0].get("__BINARY_RECORD__"):
        raise SchDocContainerError(
            "malformed",
            f"{stream} must begin with an ASCII contextual header",
            stream=stream,
        )
    header = framed[0]
    instruction = _casefold_value(header, "RECORD")
    if (
        instruction is not None
        and _parse_i32(instruction, "RECORD", stream=stream) != 0
    ):
        raise SchDocContainerError(
            "malformed", f"{stream} header RECORD must be zero", stream=stream
        )
    if _casefold_value(header, "HEADER") != SCHDOC_V5_HEADER:
        raise SchDocContainerError(
            "unsupported", f"{stream} is not the SchDoc V5 profile", stream=stream
        )
    records = tuple(framed[1:])
    weight = _parse_header_weight(header, stream)
    if weight is None and (weight_required or records):
        raise SchDocContainerError(
            "malformed", f"{stream} is missing Weight", stream=stream
        )
    return _SchDocWarehouse(header, records, weight)


def _parse_header_weight(header: dict[str, object], stream: str) -> int | None:
    value = _casefold_value(header, "Weight")
    if value is None:
        return None
    weight = _parse_i32(value, "Weight", stream=stream)
    if weight < 0:
        raise SchDocContainerError(
            "malformed", f"{stream} Weight is negative", stream=stream
        )
    return weight


def _header_value(header: dict[str, object], field: str) -> object | None:
    return _casefold_value(header, field)


def _root_stream_path(streams: dict[str, bytes], name: str) -> str | None:
    matches = [
        path
        for path in streams
        if "/" not in path and path.casefold() == name.casefold()
    ]
    if len(matches) > 1:
        raise SchDocContainerError(
            "duplicate", f"modeled stream {name} collides case-insensitively"
        )
    return matches[0] if matches else None


def _root_stream(streams: dict[str, bytes], name: str) -> bytes | None:
    path = _root_stream_path(streams, name)
    return streams[path] if path is not None else None


def _validate_modeled_root_paths(
    streams: dict[str, bytes], storages: tuple[str, ...]
) -> None:
    modeled = {name.casefold() for name in _MODELED_ROOT_STREAMS}
    for storage in storages:
        if "/" not in storage and storage.casefold() in modeled:
            raise SchDocContainerError(
                "malformed",
                f"modeled SchDoc path {storage} must be a stream, not a storage",
            )
    for name in _MODELED_ROOT_STREAMS:
        _root_stream(streams, name)


def _validate_fileheader_records(records: tuple[dict[str, object], ...]) -> None:
    sheet_positions = [
        index
        for index, record in enumerate(records)
        if _record_id(record, "FileHeader", index) == 31
    ]
    if sheet_positions != [0]:
        raise SchDocContainerError(
            "malformed",
            "FileHeader must contain exactly one Sheet as its first object",
            stream="FileHeader",
        )


def _validate_stream_record_families(
    fileheader: tuple[dict[str, object], ...],
    additional: tuple[dict[str, object], ...],
    definitions: tuple[dict[str, object], ...],
) -> None:
    for index, record in enumerate(fileheader):
        record_id = _record_id(record, "FileHeader", index)
        if record_id in _ADDITIONAL_RECORD_IDS or record_id == 129:
            raise SchDocContainerError(
                "malformed",
                f"FileHeader RECORD {record_id} belongs to another warehouse",
                stream="FileHeader",
                record_index=index,
            )
    _validate_allowed_record_ids(additional, "Additional", _ADDITIONAL_RECORD_IDS)
    _validate_allowed_record_ids(
        definitions, "ObjectDefinitions", _OBJECT_DEFINITION_RECORD_IDS
    )


def _validate_allowed_record_ids(
    records: tuple[dict[str, object], ...],
    stream: str,
    allowed: frozenset[int],
) -> None:
    for index, record in enumerate(records):
        record_id = _record_id(record, stream, index)
        if record_id not in allowed:
            raise SchDocContainerError(
                "malformed",
                f"{stream} does not allow RECORD {record_id}",
                stream=stream,
                record_index=index,
            )


def _record_id(record: dict[str, object], stream: str, index: int) -> int:
    if record.get("__BINARY_RECORD__"):
        raise SchDocContainerError(
            "unsupported",
            f"{stream} does not allow binary object records",
            stream=stream,
            record_index=index,
        )
    value = record.get("RECORD")
    if value is None:
        raise SchDocContainerError(
            "malformed",
            "object record is missing RECORD",
            stream=stream,
            record_index=index,
        )
    try:
        return _parse_i32(value, "RECORD", stream=stream)
    except SchDocContainerError as exc:
        if exc.record_index is None:
            exc.record_index = index
        raise


def _record_owner_index(
    record: dict[str, object],
    stream: str,
    index: int,
    *,
    default: int = 0,
) -> int:
    value = _casefold_value(record, "OwnerIndex")
    if value is None:
        return default
    try:
        return _parse_i32(value, "OwnerIndex", stream=stream)
    except SchDocContainerError as exc:
        if exc.record_index is None:
            exc.record_index = index
        raise


def _record_uses_additional_owner(record: dict[str, object]) -> bool:
    return _read_param_boolean(record, Fields.OWNER_INDEX_ADDITIONAL_LIST)


def _storage_frame_count(
    data: bytes,
    cursor: int,
    budget: _SchDocBudget,
) -> int:
    count = 0
    while cursor < len(data):
        mode, _, _, cursor, record_offset = _read_record_frame(
            data, cursor, "Storage", budget.limits
        )
        if mode != 1:
            raise SchDocContainerError(
                "malformed",
                "Storage payload records must use binary framing",
                stream="Storage",
                byte_offset=record_offset,
            )
        count += 1
        if count > budget.limits.max_records_per_stream - 1:
            raise SchDocContainerError(
                "limit", "Storage record count exceeds the reviewed limit"
            )
        if count > budget.remaining_records:
            raise SchDocContainerError(
                "limit", "Storage exceeds the remaining document record budget"
            )
    return count


def _storage_weight(pairs: list[bytes]) -> tuple[int | None, int | None]:
    indexes = [
        index
        for index, pair in enumerate(pairs)
        if pair.partition(b"=")[0].lower() == b"weight"
    ]
    if len(indexes) > 1:
        raise SchDocContainerError(
            "duplicate", "duplicate Storage Weight", stream="Storage"
        )
    if not indexes:
        return None, None
    index = indexes[0]
    _, separator, value = pairs[index].partition(b"=")
    if not separator or not value.isascii() or not value.isdigit():
        raise SchDocContainerError(
            "malformed", "Storage Weight must be a nonnegative signed i32"
        )
    weight = int(value)
    if weight > (1 << 31) - 1:
        raise SchDocContainerError(
            "malformed", "Storage Weight must be a nonnegative signed i32"
        )
    return index, weight


def _preflight_storage_header(
    data: bytes,
    budget: _SchDocBudget,
) -> tuple[bytes, int, int]:
    mode, payload, _, end, record_offset = _read_record_frame(
        data, 0, "Storage", budget.limits
    )
    if mode != 0:
        raise SchDocContainerError(
            "malformed", "Storage must begin with an ASCII header", stream="Storage"
        )
    header_pairs = _count_field_pairs(payload, "Storage", record_offset)
    budget.consume_record("Storage", 1)
    budget.consume_field_pairs("Storage", header_pairs, header_pairs)
    return payload, end, header_pairs


def _normalize_storage_header(
    data: bytes,
    budget: _SchDocBudget,
    payload: bytes,
    end: int,
) -> tuple[bytes, int]:
    pairs = payload[:-1].split(b"|")
    weight_index, weight = _storage_weight(pairs)
    physical_count = _storage_frame_count(data, end, budget)
    if weight is None and physical_count:
        raise SchDocContainerError(
            "malformed", "nonempty Storage is missing Weight", stream="Storage"
        )
    if weight_index is None:
        return data, physical_count
    pairs[weight_index] = b"Weight=" + str(physical_count).encode("ascii")
    normalized = b"|".join(pairs) + b"\0"
    return struct.pack("<I", len(normalized)) + normalized + data[end:], physical_count


def _parse_storage(
    data: bytes | None,
    budget: _SchDocBudget,
) -> tuple[SchAuxiliaryEntry, ...]:
    if data is None:
        return ()
    payload, header_end, _ = _preflight_storage_header(data, budget)
    normalized, physical_count = _normalize_storage_header(
        data, budget, payload, header_end
    )
    try:
        entries = decode_auxiliary_stream(
            normalized,
            expected_header="Icon storage",
            limits=_auxiliary_limits(budget),
        )
    except SchAuxiliaryStreamError as exc:
        raise SchDocContainerError(
            exc.kind,
            exc.reason,
            stream="Storage",
            byte_offset=exc.offset,
        ) from exc
    if len(entries) != physical_count:
        raise SchDocContainerError(
            "malformed", "Storage physical record count changed during decoding"
        )
    total_records = physical_count + 1
    if total_records > budget.limits.max_records_per_stream:
        raise SchDocContainerError(
            "limit",
            f"records_per_stream limit exceeded by {total_records}",
            stream="Storage",
        )
    budget.consume_stream("Storage", len(entries), 0)
    budget.consume_decompressed("Storage", tuple(len(entry.data) for entry in entries))
    return tuple(entries)


def _storage_weight_is_stale(data: bytes, budget: _SchDocBudget) -> bool:
    payload, end, _ = _preflight_storage_header(data, budget)
    _, weight = _storage_weight(payload[:-1].split(b"|"))
    physical_count = _storage_frame_count(data, end, budget)
    return weight is not None and weight != physical_count


def _auxiliary_limits(budget: _SchDocBudget) -> SchAuxiliaryReadLimits:
    limits = budget.limits
    return SchAuxiliaryReadLimits(
        max_stream_bytes=limits.max_stream_bytes,
        max_records_per_stream=min(
            limits.max_records_per_stream, budget.remaining_records
        ),
        max_record_bytes=limits.max_record_bytes,
        max_compressed_blob_bytes=limits.max_stream_bytes,
        max_decompressed_blob_bytes=min(
            limits.max_decompressed_blob_bytes,
            budget.remaining_decompressed_bytes,
        ),
        max_total_decompressed_blob_bytes=budget.remaining_decompressed_bytes,
    )


__all__ = [
    "SCHDOC_V5_HEADER",
    "SchDocContainerError",
    "_SchDocBudget",
    "_SchDocReadLimits",
    "_SchDocWarehouse",
    "_parse_storage",
    "_storage_weight_is_stale",
    "_validate_modeled_root_paths",
    "_parse_warehouse",
    "_header_value",
    "_record_id",
    "_record_owner_index",
    "_record_uses_additional_owner",
    "_root_stream",
    "_root_stream_path",
    "_snapshot_container",
    "_validate_fileheader_records",
    "_validate_stream_record_families",
]
