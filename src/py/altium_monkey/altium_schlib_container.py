"""Strict private helpers for SchLib container framing and discovery."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Final, Protocol

from .altium_utilities import decode_byte_array, parse_byte_record
from .altium_serializer import sanitize_stream_name

SCHLIB_V5_HEADER: Final = (
    "Protel for Windows - Schematic Library Editor Binary File Version 5.0"
)


class SchLibContainerError(ValueError):
    """Structured failure while validating a SchLib container boundary."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        stream: str | None = None,
        record_index: int | None = None,
        byte_offset: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.stream = stream
        self.record_index = record_index
        self.byte_offset = byte_offset


@dataclass(frozen=True, slots=True)
class _SchLibReadLimits:
    max_container_bytes: int = 268_435_456
    max_stream_bytes: int = 67_108_864
    max_streams_per_container: int = 4_096
    max_ole_directory_entries: int = 8_192
    max_records_per_stream: int = 1_000_000
    max_total_records_per_document: int = 1_000_000
    max_record_bytes: int = 16_777_215
    max_field_pairs_per_record: int = 65_536
    max_indexed_items_per_record: int = 65_536
    max_total_field_pairs_per_stream: int = 1_500_000
    max_total_field_pairs_per_document: int = 3_000_000
    max_field_name_bytes: int = 4_096
    max_field_value_bytes: int = 16_777_215
    max_decompressed_blob_bytes: int = 268_435_456
    max_total_decompressed_blob_bytes: int = 536_870_912
    max_ownership_depth: int = 1_024
    max_json_input_bytes: int = 536_870_912
    max_json_recursion_depth: int = 128

    def validate(self) -> _SchLibReadLimits:
        defaults = type(self)()
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            reviewed = getattr(defaults, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SchLibContainerError(
                    "limit", f"{field_name} must be a nonnegative integer"
                )
            if value > reviewed:
                raise SchLibContainerError(
                    "limit",
                    f"{field_name} exceeds reviewed maximum {reviewed}",
                )
        return self


@dataclass(slots=True)
class _SchLibBudget:
    limits: _SchLibReadLimits
    total_records: int = 0
    total_field_pairs: int = 0
    total_decompressed_bytes: int = 0

    @property
    def remaining_records(self) -> int:
        return self.limits.max_total_records_per_document - self.total_records

    @property
    def remaining_decompressed_bytes(self) -> int:
        return (
            self.limits.max_total_decompressed_blob_bytes
            - self.total_decompressed_bytes
        )

    def consume_record(self, stream: str, stream_records: int) -> None:
        if stream_records > self.limits.max_records_per_stream:
            raise _limit_error("records_per_stream", stream_records, stream=stream)
        total = self.total_records + 1
        if total > self.limits.max_total_records_per_document:
            raise _limit_error("total_records_per_document", total)
        self.total_records = total

    def consume_field_pairs(
        self,
        stream: str,
        stream_field_pairs: int,
        record_field_pairs: int,
    ) -> None:
        if record_field_pairs > self.limits.max_field_pairs_per_record:
            raise _limit_error(
                "field_pairs_per_record", record_field_pairs, stream=stream
            )
        if stream_field_pairs > self.limits.max_total_field_pairs_per_stream:
            raise _limit_error(
                "field_pairs_per_stream", stream_field_pairs, stream=stream
            )
        total = self.total_field_pairs + record_field_pairs
        if total > self.limits.max_total_field_pairs_per_document:
            raise _limit_error("total_field_pairs_per_document", total)
        self.total_field_pairs = total

    def consume_stream(self, stream: str, records: int, field_pairs: int) -> None:
        if records > self.limits.max_records_per_stream:
            raise _limit_error("records_per_stream", records, stream=stream)
        if field_pairs > self.limits.max_total_field_pairs_per_stream:
            raise _limit_error("field_pairs_per_stream", field_pairs, stream=stream)
        total_records = self.total_records + records
        total_pairs = self.total_field_pairs + field_pairs
        if total_records > self.limits.max_total_records_per_document:
            raise _limit_error("total_records_per_document", total_records)
        if total_pairs > self.limits.max_total_field_pairs_per_document:
            raise _limit_error("total_field_pairs_per_document", total_pairs)
        self.total_records = total_records
        self.total_field_pairs = total_pairs

    def consume_decompressed(self, stream: str, blob_sizes: tuple[int, ...]) -> None:
        for size in blob_sizes:
            if size > self.limits.max_decompressed_blob_bytes:
                raise _limit_error("decompressed_blob_bytes", size, stream=stream)
        total = self.total_decompressed_bytes + sum(blob_sizes)
        if total > self.limits.max_total_decompressed_blob_bytes:
            raise _limit_error("total_decompressed_blob_bytes", total)
        self.total_decompressed_bytes = total


@dataclass(frozen=True, slots=True)
class _SectionKeys:
    by_libref: dict[str, str]


@dataclass(frozen=True, slots=True)
class _SymbolStorage:
    storage_key: str
    libref: str


@dataclass(frozen=True, slots=True)
class _SymbolPlan:
    entries: tuple[_SymbolStorage, ...]
    header_coherent: bool


class _OleReader(Protocol):
    def listdir(
        self, streams: bool = True, storages: bool = False
    ) -> list[list[str]]: ...

    def get_size(self, path: str | list[str]) -> int: ...

    def openstream(self, path: str | list[str]) -> bytes: ...


def _limit_error(
    resource: str,
    actual: int,
    *,
    stream: str | None = None,
    record_index: int | None = None,
    byte_offset: int | None = None,
) -> SchLibContainerError:
    return SchLibContainerError(
        "limit",
        f"{resource} limit exceeded by {actual}",
        stream=stream,
        record_index=record_index,
        byte_offset=byte_offset,
    )


def _parse_i32(value: object, field: str, *, stream: str) -> int:
    text = str(value)
    if re.fullmatch(r"[+-]?[0-9]+", text) is None:
        raise SchLibContainerError(
            "malformed", f"{field} is not an ASCII integer", stream=stream
        )
    parsed = int(text)
    if not -(1 << 31) <= parsed <= (1 << 31) - 1:
        raise SchLibContainerError(
            "overflow", f"{field} exceeds signed i32", stream=stream
        )
    return parsed


def _casefold_value(record: dict[str, object], field: str) -> object | None:
    target = field.casefold()
    matches = [value for key, value in record.items() if key.casefold() == target]
    if len(matches) > 1:
        raise SchLibContainerError("duplicate", f"duplicate field {field}")
    return matches[0] if matches else None


def _dynamic_value(record: dict[str, object], field: str, *, stream: str) -> str:
    return _folded_dynamic_value(_fold_record(record), field, stream=stream)


def _fold_record(record: dict[str, object]) -> dict[str, object]:
    return {key.casefold(): value for key, value in record.items()}


def _folded_dynamic_value(
    folded_record: dict[str, object], field: str, *, stream: str
) -> str:
    utf8_value = folded_record.get(f"%utf8%{field}".casefold())
    value = (
        utf8_value if utf8_value is not None else folded_record.get(field.casefold())
    )
    if value is None:
        raise SchLibContainerError(
            "malformed", f"missing dynamic field {field}", stream=stream
        )
    return str(value)


def _parse_instruction_stream(
    data: bytes,
    stream: str,
    budget: _SchLibBudget,
    *,
    allow_equal_duplicates: bool = False,
    allow_terminal_nul_padding: bool = False,
    always_unique_fields: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    limits = budget.limits
    if len(data) > limits.max_stream_bytes:
        raise _limit_error("stream_bytes", len(data), stream=stream)
    records: list[dict[str, object]] = []
    offset = 0
    field_pair_total = 0
    while offset < len(data):
        budget.consume_record(stream, len(records) + 1)
        mode, payload, encoded_length, offset, record_offset = _read_record_frame(
            data, offset, stream, limits
        )
        if mode == 1:
            records.append(
                {
                    "RECORD": payload[0],
                    "__BINARY_RECORD__": True,
                    "__BINARY_DATA__": payload,
                    "__ORIGINAL_LENGTH_BYTES__": encoded_length.to_bytes(4, "little"),
                }
            )
            continue
        pair_count = _count_field_pairs(
            payload,
            stream,
            record_offset,
            allow_terminal_nul_padding=allow_terminal_nul_padding,
        )
        field_pair_total += pair_count
        budget.consume_field_pairs(stream, field_pair_total, pair_count)
        result, pair_count = _decode_text_record(
            payload,
            stream,
            len(records),
            record_offset,
            limits,
            allow_equal_duplicates=allow_equal_duplicates,
            allow_terminal_nul_padding=allow_terminal_nul_padding,
            always_unique_fields=always_unique_fields,
        )
        records.append(result)
    return records


def _count_field_pairs(
    payload: bytes,
    stream: str,
    record_offset: int,
    *,
    allow_terminal_nul_padding: bool = False,
) -> int:
    record = _text_record_body(
        payload,
        stream,
        record_offset,
        allow_terminal_nul_padding=allow_terminal_nul_padding,
    )
    if record.startswith(b"|"):
        record = record[1:]
    if not record:
        return 0
    return record.count(b"|") + (0 if record.endswith(b"|") else 1)


def _text_record_body(
    payload: bytes,
    stream: str,
    record_offset: int,
    *,
    allow_terminal_nul_padding: bool,
) -> bytes:
    if not payload or payload[-1] != 0:
        raise SchLibContainerError(
            "malformed",
            "text record must end with a terminal NUL",
            stream=stream,
            byte_offset=record_offset,
        )
    body = payload.rstrip(b"\x00")
    terminal_count = len(payload) - len(body)
    if not allow_terminal_nul_padding and (b"\x00" in body or terminal_count != 1):
        raise SchLibContainerError(
            "malformed",
            "text record must contain exactly one terminal NUL",
            stream=stream,
            byte_offset=record_offset,
        )
    if b"\x00" in body:
        raise SchLibContainerError(
            "malformed",
            "text record contains invalid NUL padding",
            stream=stream,
            byte_offset=record_offset,
        )
    return body


def _read_record_frame(
    data: bytes,
    offset: int,
    stream: str,
    limits: _SchLibReadLimits,
) -> tuple[int, bytes, int, int, int]:
    record_offset = offset
    if len(data) - offset < 4:
        raise SchLibContainerError(
            "truncated", "truncated record length", stream=stream, byte_offset=offset
        )
    encoded_length = struct.unpack_from("<I", data, offset)[0]
    mode = encoded_length >> 24
    length = encoded_length & 0x00FF_FFFF
    if mode not in {0, 1}:
        raise SchLibContainerError(
            "malformed",
            f"unsupported record mode {mode}",
            stream=stream,
            byte_offset=record_offset,
        )
    if length == 0:
        raise SchLibContainerError(
            "malformed", "zero-length record", stream=stream, byte_offset=record_offset
        )
    if length > limits.max_record_bytes:
        raise _limit_error(
            "record_bytes", length, stream=stream, byte_offset=record_offset
        )
    payload_offset = offset + 4
    end = payload_offset + length
    if end > len(data):
        raise SchLibContainerError(
            "truncated",
            "record payload exceeds stream",
            stream=stream,
            byte_offset=record_offset,
        )
    return mode, data[payload_offset:end], encoded_length, end, record_offset


def _decode_text_record(
    payload: bytes,
    stream: str,
    record_index: int,
    record_offset: int,
    limits: _SchLibReadLimits,
    *,
    allow_equal_duplicates: bool = False,
    allow_terminal_nul_padding: bool = False,
    always_unique_fields: frozenset[str] = frozenset(),
) -> tuple[dict[str, object], int]:
    body = _text_record_body(
        payload,
        stream,
        record_offset,
        allow_terminal_nul_padding=allow_terminal_nul_padding,
    )
    raw_pairs = parse_byte_record(body)
    if len(raw_pairs) > limits.max_field_pairs_per_record:
        raise _limit_error(
            "field_pairs_per_record",
            len(raw_pairs),
            stream=stream,
            record_index=record_index,
        )
    result: dict[str, object] = {}
    folded: dict[str, object] = {}
    empty_pair_count = 0
    for pair_index, raw_pair in enumerate(raw_pairs):
        if not raw_pair:
            key = f"UNHANDLED{empty_pair_count}"
            empty_pair_count += 1
            result[key] = ""
            folded[key.casefold()] = ""
            continue
        key, value = _decode_field_pair(
            raw_pair, stream, record_index, pair_index, limits
        )
        normalized = key.casefold()
        if normalized in folded:
            if (
                normalized in always_unique_fields
                or not allow_equal_duplicates
                or folded[normalized] != value
            ):
                raise SchLibContainerError(
                    "duplicate",
                    f"duplicate field {key}",
                    stream=stream,
                    record_index=record_index,
                )
            continue
        folded[normalized] = value
        result[key] = value
    return result, len(raw_pairs)


def _decode_field_pair(
    raw_pair: bytes,
    stream: str,
    record_index: int,
    pair_index: int,
    limits: _SchLibReadLimits,
) -> tuple[str, str]:
    raw_key, separator, raw_value = raw_pair.partition(b"=")
    if not separator or not raw_key:
        raise SchLibContainerError(
            "malformed",
            f"invalid field pair {pair_index}",
            stream=stream,
            record_index=record_index,
        )
    if len(raw_key) > limits.max_field_name_bytes:
        raise _limit_error(
            "field_name_bytes", len(raw_key), stream=stream, record_index=record_index
        )
    if len(raw_value) > limits.max_field_value_bytes:
        raise _limit_error(
            "field_value_bytes",
            len(raw_value),
            stream=stream,
            record_index=record_index,
        )
    try:
        decoded = decode_byte_array(
            raw_pair, context=f"{stream} record {record_index} pair {pair_index}"
        )
    except ValueError as exc:
        raise SchLibContainerError(
            "decode",
            f"field pair {pair_index} cannot be decoded",
            stream=stream,
            record_index=record_index,
        ) from exc
    key, value = decoded.split("=", 1)
    return key, value


def _snapshot_container(
    ole: _OleReader,
    container_size: int,
    limits: _SchLibReadLimits,
) -> tuple[dict[str, bytes], tuple[str, ...]]:
    limits.validate()
    if container_size > limits.max_container_bytes:
        raise _limit_error("container_bytes", container_size)
    stream_paths = ole.listdir(streams=True, storages=False)
    storage_paths = ole.listdir(streams=False, storages=True)
    directory_entries = 1 + len(stream_paths) + len(storage_paths)
    if directory_entries > limits.max_ole_directory_entries:
        raise _limit_error("ole_directory_entries", directory_entries)
    if len(stream_paths) > limits.max_streams_per_container:
        raise _limit_error("streams_per_container", len(stream_paths))
    sized_paths = _preflight_stream_sizes(ole, stream_paths, limits)
    streams: dict[str, bytes] = {}
    for path, declared_size in sized_paths:
        payload = ole.openstream(path)
        if len(payload) != declared_size:
            raise SchLibContainerError(
                "malformed",
                "stream payload length differs from its directory size",
                stream=path,
            )
        streams[path] = payload
    return streams, tuple("/".join(path) for path in storage_paths)


def _preflight_stream_sizes(
    ole: _OleReader,
    stream_paths: list[list[str]],
    limits: _SchLibReadLimits,
) -> list[tuple[str, int]]:
    sized_paths: list[tuple[str, int]] = []
    aggregate = 0
    for path in stream_paths:
        path_text = "/".join(path)
        size = ole.get_size(path)
        if size < 0:
            raise SchLibContainerError(
                "malformed", "stream has a negative declared size", stream=path_text
            )
        if size > limits.max_stream_bytes:
            raise _limit_error("stream_bytes", size, stream=path_text)
        aggregate += size
        if aggregate > limits.max_container_bytes:
            raise _limit_error("aggregate_stream_bytes", aggregate)
        sized_paths.append((path_text, size))
    return sized_paths


def _parse_file_header(data: bytes, budget: _SchLibBudget) -> dict[str, object]:
    records = _parse_instruction_stream(data, "FileHeader", budget)
    if len(records) != 1 or records[0].get("__BINARY_RECORD__"):
        raise SchLibContainerError(
            "malformed",
            "FileHeader must contain exactly one ASCII record",
            stream="FileHeader",
        )
    header = records[0]
    instruction = _casefold_value(header, "RECORD")
    if (
        instruction is not None
        and _parse_i32(instruction, "RECORD", stream="FileHeader") != 0
    ):
        raise SchLibContainerError(
            "malformed", "FileHeader RECORD must be zero", stream="FileHeader"
        )
    if _casefold_value(header, "HEADER") != SCHLIB_V5_HEADER:
        raise SchLibContainerError(
            "unsupported",
            "FileHeader is not the SchLib V5 profile",
            stream="FileHeader",
        )
    minor = _casefold_value(header, "MinorVersion")
    if minor is not None:
        _parse_i32(minor, "MinorVersion", stream="FileHeader")
    return header


def _parse_section_keys(
    data: bytes | None,
    budget: _SchLibBudget,
) -> _SectionKeys:
    if data is None:
        return _SectionKeys({})
    records = _parse_instruction_stream(data, "SectionKeys", budget)
    if len(records) != 1 or records[0].get("__BINARY_RECORD__"):
        raise SchLibContainerError(
            "malformed",
            "SectionKeys must contain exactly one ASCII record",
            stream="SectionKeys",
        )
    record = records[0]
    folded_record = _fold_record(record)
    count = _section_key_count(folded_record, budget.limits)
    by_libref = _section_key_entries(folded_record, count)
    _reject_trailing_section_keys(record, count)
    return _SectionKeys(by_libref)


def _section_key_count(record: dict[str, object], limits: _SchLibReadLimits) -> int:
    instruction = record.get("record")
    if (
        instruction is not None
        and _parse_i32(instruction, "RECORD", stream="SectionKeys") != 0
    ):
        raise SchLibContainerError(
            "malformed", "SectionKeys RECORD must be zero", stream="SectionKeys"
        )
    raw_count = record.get("keycount")
    if raw_count is None:
        raise SchLibContainerError(
            "malformed", "SectionKeys is missing KeyCount", stream="SectionKeys"
        )
    count = _parse_i32(raw_count, "KeyCount", stream="SectionKeys")
    if count < 0:
        raise SchLibContainerError(
            "malformed", "SectionKeys KeyCount is negative", stream="SectionKeys"
        )
    if count > limits.max_indexed_items_per_record:
        raise _limit_error("indexed_items_per_record", count, stream="SectionKeys")
    return count


def _section_key_entries(
    folded_record: dict[str, object], count: int
) -> dict[str, str]:
    by_libref: dict[str, str] = {}
    folded_keys: set[str] = set()
    for index in range(count):
        libref = _folded_dynamic_value(
            folded_record, f"LibRef{index}", stream="SectionKeys"
        )
        section_key = _folded_dynamic_value(
            folded_record, f"SectionKey{index}", stream="SectionKeys"
        )
        if not libref or libref in by_libref:
            raise SchLibContainerError(
                "duplicate",
                "SectionKeys has an empty or duplicate LibRef",
                stream="SectionKeys",
            )
        if not section_key or "/" in section_key or "\\" in section_key:
            raise SchLibContainerError(
                "malformed",
                "SectionKeys target is not a top-level storage key",
                stream="SectionKeys",
            )
        folded = section_key.casefold()
        if folded in folded_keys:
            raise SchLibContainerError(
                "duplicate",
                "SectionKeys targets collide case-insensitively",
                stream="SectionKeys",
            )
        folded_keys.add(folded)
        by_libref[libref] = section_key
    return by_libref


def _reject_trailing_section_keys(record: dict[str, object], count: int) -> None:
    indexed_pattern = re.compile(
        r"(?:%UTF8%)?(?:LibRef|SectionKey)([0-9]+)", re.IGNORECASE
    )
    for key in record:
        match = indexed_pattern.fullmatch(key)
        if match and int(match.group(1)) >= count:
            raise SchLibContainerError(
                "malformed",
                "SectionKeys contains trailing indexed fields",
                stream="SectionKeys",
            )


def _header_symbol_order(
    header: dict[str, object],
    section_keys: _SectionKeys,
    discovered: tuple[str, ...],
    available_storages: tuple[str, ...],
    available_streams: tuple[str, ...],
    *,
    max_indexed_items: int,
) -> _SymbolPlan:
    folded_header = _fold_record(header)
    _validate_header_indexed_fields(header, max_indexed_items)
    discovered_by_fold = _validate_discovered_storages(
        discovered, available_storages, available_streams, section_keys
    )
    header_entries = _mapped_header_entries(
        folded_header,
        section_keys,
        discovered_by_fold,
        max_indexed_items=max_indexed_items,
    )
    if _is_exact_permutation(header_entries, discovered_by_fold):
        return _SymbolPlan(header_entries or (), True)
    reverse_section_keys = {
        key.casefold(): libref for libref, key in section_keys.by_libref.items()
    }
    return _SymbolPlan(
        tuple(
            _SymbolStorage(name, reverse_section_keys.get(name.casefold(), name))
            for name in discovered
        ),
        False,
    )


def _validate_header_indexed_fields(
    header: dict[str, object], max_indexed_items: int
) -> None:
    raw_count = _casefold_value(header, "CompCount")
    if raw_count is None:
        indexed = next((key for key in header if _is_header_indexed_key(key)), None)
        if indexed is not None:
            raise SchLibContainerError(
                "malformed",
                f"indexed FileHeader field {indexed} has no CompCount",
                stream="FileHeader",
            )
        return
    count = _parse_i32(raw_count, "CompCount", stream="FileHeader")
    if count < 0:
        raise SchLibContainerError(
            "malformed", "CompCount is negative", stream="FileHeader"
        )
    if count > max_indexed_items:
        raise _limit_error("indexed_items_per_record", count, stream="FileHeader")
    alias_counts = _header_alias_counts(header, count, max_indexed_items)
    for raw_key in header:
        _validate_header_indexed_key(raw_key, count, alias_counts)


def _validate_header_indexed_key(
    raw_key: str, count: int, alias_counts: dict[int, int]
) -> None:
    key = re.sub(r"^%UTF8%", "", raw_key, flags=re.IGNORECASE)
    symbol_match = re.fullmatch(
        r"(?:LibRef|PartCount|CompDescr|AliasCount)([0-9]+)",
        key,
        re.IGNORECASE,
    )
    if symbol_match and int(symbol_match.group(1)) >= count:
        raise SchLibContainerError(
            "malformed",
            f"indexed FileHeader field {raw_key} exceeds CompCount",
            stream="FileHeader",
        )
    alias_match = re.fullmatch(r"Comp([0-9]+)Alias([0-9]+)", key, re.IGNORECASE)
    if alias_match and not _alias_index_is_declared(alias_match, alias_counts):
        raise SchLibContainerError(
            "malformed",
            f"indexed FileHeader alias {raw_key} exceeds AliasCount",
            stream="FileHeader",
        )


def _is_header_indexed_key(raw_key: str) -> bool:
    key = re.sub(r"^%UTF8%", "", raw_key, flags=re.IGNORECASE)
    return (
        re.fullmatch(
            r"(?:LibRef|PartCount|CompDescr|AliasCount)[0-9]+",
            key,
            re.IGNORECASE,
        )
        is not None
        or re.fullmatch(r"Comp[0-9]+Alias[0-9]+", key, re.IGNORECASE) is not None
    )


def _header_alias_counts(
    header: dict[str, object], count: int, max_indexed_items: int
) -> dict[int, int]:
    alias_counts: dict[int, int] = {}
    for key, raw_count in header.items():
        match = re.fullmatch(r"AliasCount([0-9]+)", key, re.IGNORECASE)
        if match is None:
            continue
        symbol_index = int(match.group(1))
        if symbol_index >= count:
            continue
        alias_count = _parse_i32(
            raw_count, f"AliasCount{symbol_index}", stream="FileHeader"
        )
        if alias_count < 0:
            raise SchLibContainerError(
                "malformed", "FileHeader AliasCount is negative", stream="FileHeader"
            )
        if alias_count > max_indexed_items:
            raise _limit_error(
                "indexed_items_per_record", alias_count, stream="FileHeader"
            )
        alias_counts[symbol_index] = alias_count
    return alias_counts


def _alias_index_is_declared(
    match: re.Match[str], alias_counts: dict[int, int]
) -> bool:
    symbol_index = int(match.group(1))
    alias_index = int(match.group(2))
    return symbol_index in alias_counts and alias_index < alias_counts[symbol_index]


def _validate_discovered_storages(
    discovered: tuple[str, ...],
    available_storages: tuple[str, ...],
    available_streams: tuple[str, ...],
    section_keys: _SectionKeys,
) -> dict[str, str]:
    discovered_by_fold = {name.casefold(): name for name in discovered}
    if len(discovered_by_fold) != len(discovered):
        raise SchLibContainerError(
            "duplicate", "symbol storage keys collide case-insensitively"
        )
    available_by_fold = {
        name.casefold(): name for name in available_storages if "/" not in name
    }
    stream_folds = {name.casefold() for name in available_streams}
    for target in section_keys.by_libref.values():
        folded = target.casefold()
        owns_known_stream = (
            f"{folded}/data" in stream_folds or f"{folded}/redirection" in stream_folds
        )
        if folded not in available_by_fold or not owns_known_stream:
            raise SchLibContainerError(
                "malformed",
                f"SectionKeys target {target!r} has no Data or Redirection stream",
            )
    return discovered_by_fold


def _mapped_header_entries(
    folded_header: dict[str, object],
    section_keys: _SectionKeys,
    discovered_by_fold: dict[str, str],
    *,
    max_indexed_items: int,
) -> tuple[_SymbolStorage, ...] | None:
    raw_count = folded_header.get("compcount")
    if raw_count is None:
        return None
    count = _parse_i32(raw_count, "CompCount", stream="FileHeader")
    if count < 0:
        raise SchLibContainerError(
            "malformed", "CompCount is negative", stream="FileHeader"
        )
    if count > max_indexed_items:
        raise _limit_error("indexed_items_per_record", count, stream="FileHeader")
    entries: list[_SymbolStorage] = []
    for index in range(count):
        entry = _mapped_header_entry(
            folded_header, index, section_keys, discovered_by_fold
        )
        if entry is None:
            return None
        entries.append(entry)
    return tuple(entries)


def _mapped_header_entry(
    folded_header: dict[str, object],
    index: int,
    section_keys: _SectionKeys,
    discovered_by_fold: dict[str, str],
) -> _SymbolStorage | None:
    try:
        libref = _folded_dynamic_value(
            folded_header, f"LibRef{index}", stream="FileHeader"
        )
    except SchLibContainerError:
        return None
    mapped = section_keys.by_libref.get(libref, libref)
    storage = discovered_by_fold.get(mapped.casefold())
    if storage is None and libref not in section_keys.by_libref:
        implicit = sanitize_stream_name(libref)
        storage = discovered_by_fold.get(implicit.casefold())
    if libref in section_keys.by_libref and storage is None:
        raise SchLibContainerError(
            "malformed",
            f"SectionKeys component target {mapped!r} has no Data stream",
            stream="SectionKeys",
        )
    return _SymbolStorage(storage, libref) if storage is not None else None


def _is_exact_permutation(
    entries: tuple[_SymbolStorage, ...] | None,
    discovered_by_fold: dict[str, str],
) -> bool:
    if entries is None or len(entries) != len(discovered_by_fold):
        return False
    return {entry.storage_key.casefold() for entry in entries} == set(
        discovered_by_fold
    )
