"""
Shared JSON apply helpers for schematic document and library models.
"""

import base64
import binascii
import json
import math
import zlib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from .altium_record_types import SchRecordType
from .altium_sch_json_object_types import (
    SCH_JSON_OBJECT_TYPE_TO_RECORD_TYPE,
    SchJsonObjectType,
)

_MAX_BASE64_INPUT_BYTES = 100_663_296
_MAX_BASE64_DECODED_BYTES = 67_108_864
_BASE64_ALPHABET = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
_JSON_STRUCTURAL_KEYS = frozenset(
    {
        "record",
        "binarydata",
        "_binary_record_type",
        "__binary_record__",
        "__binary_data__",
    }
)
_JsonContainer = dict[str, object] | list[object]


class _SupportsJsonLimits(Protocol):
    @property
    def max_json_input_bytes(self) -> int: ...

    @property
    def max_json_recursion_depth(self) -> int: ...

    @property
    def max_total_field_pairs_per_document(self) -> int: ...


@dataclass(slots=True)
class JsonBlobBudget:
    """Track aggregate decompressed bytes across one JSON transaction."""

    limit: int
    consumed: int = 0

    def consume(self, size: int, *, context: str) -> None:
        total = self.consumed + size
        if total > self.limit:
            raise ValueError(f"{context}.BinaryData exceeds the aggregate blob limit")
        self.consumed = total


class _SupportsJsonApply(Protocol):
    @staticmethod
    def _load_json_source(source: Path | str | dict) -> dict: ...

    def _update_from_json(self, data: dict) -> None: ...

    def _commit_json_update(self, staged: object) -> None: ...

    def _validate_json_staged(self) -> None: ...


class JsonApplyMixin:
    """
    Shared ``apply_json()`` behavior for binary-backed JSON update surfaces.
    """

    def apply_json(self: _SupportsJsonApply, source: Path | str | dict) -> None:
        """
        Transactionally mutate this object from a JSON payload.
        """
        data = self._load_json_source(source)
        staged = deepcopy(self)
        staged._update_from_json(data)
        staged._validate_json_staged()
        self._commit_json_update(staged)


def load_bounded_json_source(
    source: Path | str | dict[str, object],
    *,
    limits: _SupportsJsonLimits,
) -> dict[str, object]:
    """Load one JSON object under the reviewed byte, depth, and node budgets."""
    if isinstance(source, dict):
        data: object = source
        _validate_json_tree(data, limits=limits)
        _measure_json_value(data, limits.max_json_input_bytes)
    else:
        source_path = Path(source)
        declared_size = source_path.stat().st_size
        if declared_size > limits.max_json_input_bytes:
            raise ValueError("JSON input exceeds the byte limit")
        with source_path.open("rb") as handle:
            payload = handle.read(limits.max_json_input_bytes + 1)
        if len(payload) > limits.max_json_input_bytes:
            raise ValueError("JSON input exceeds the byte limit")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("JSON input is not valid UTF-8") from exc
        try:
            data = json.loads(text, object_pairs_hook=_strict_json_object)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON syntax at line {exc.lineno} column {exc.colno}"
            ) from exc
        except RecursionError as exc:
            raise ValueError("JSON input exceeds the recursion depth limit") from exc
        _validate_json_tree(data, limits=limits)
    if not isinstance(data, dict):
        raise ValueError("Invalid JSON format: root must be an object")
    return cast(dict[str, object], data)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    folded: dict[str, str] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON object contains duplicate key {key!r}")
        normalized = key.casefold()
        prior = folded.get(normalized)
        if prior is not None:
            raise ValueError(
                f"JSON object keys {prior!r} and {key!r} collide case-insensitively"
            )
        result[key] = value
        folded[normalized] = key
    return result


def _json_container_children(value: _JsonContainer) -> list[object]:
    if isinstance(value, dict):
        _validate_json_mapping_keys(value)
        return list(value.values())
    return value


def _validate_json_scalar(value: object) -> None:
    if not isinstance(value, str | int | float | bool) and value is not None:
        raise ValueError("JSON input contains a non-JSON value")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON input contains a non-finite number")


def _enter_json_container(
    value: _JsonContainer,
    depth: int,
    active: set[int],
    limits: _SupportsJsonLimits,
    remaining_items: int,
) -> tuple[list[object], int]:
    identity = id(value)
    if identity in active:
        raise ValueError("JSON input contains a recursive container")
    if depth > limits.max_json_recursion_depth:
        raise ValueError("JSON input exceeds the recursion depth limit")
    child_count = (
        dict.__len__(value) if isinstance(value, dict) else list.__len__(value)
    )
    if child_count > remaining_items:
        raise ValueError("JSON input exceeds the aggregate item limit")
    if type(value) not in {dict, list}:
        raise ValueError("JSON input contains a non-JSON container")
    active.add(identity)
    return _json_container_children(value), child_count


def _validate_json_tree(value: object, *, limits: _SupportsJsonLimits) -> None:
    active: set[int] = set()
    aggregate = 0
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    while stack:
        current, depth, leaving = stack.pop()
        if not isinstance(current, dict | list):
            _validate_json_scalar(current)
            continue
        if leaving:
            active.remove(id(current))
            continue
        remaining_items = limits.max_total_field_pairs_per_document - aggregate
        children, child_count = _enter_json_container(
            current,
            depth,
            active,
            limits,
            remaining_items,
        )
        aggregate += child_count
        stack.append((current, depth, True))
        stack.extend((child, depth + 1, False) for child in reversed(children))


def _validate_json_mapping_keys(value: Mapping[str, object]) -> None:
    folded: dict[str, str] = {}
    for key in value:
        if not isinstance(key, str):
            raise ValueError("JSON object keys must be strings")
        normalized = key.casefold()
        prior = folded.get(normalized)
        if prior is not None and prior != key:
            raise ValueError(
                f"JSON object keys {prior!r} and {key!r} collide case-insensitively"
            )
        folded[normalized] = key


def _measure_json_value(value: object, limit: int) -> None:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    size = 0
    try:
        for chunk in encoder.iterencode(value):
            size += len(chunk.encode("utf-8"))
            if size > limit:
                raise ValueError("JSON input exceeds the byte limit")
    except (RecursionError, ValueError) as exc:
        if (
            isinstance(exc, ValueError)
            and str(exc) == "JSON input exceeds the byte limit"
        ):
            raise
        raise ValueError("JSON input cannot be serialized exactly") from exc


def json_object_rows(
    value: object, *, context: str, max_rows: int | None = None
) -> tuple[Mapping[str, object], ...]:
    """Validate and return an indexed JSON object list."""
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list")
    if max_rows is not None and len(value) > max_rows:
        raise ValueError(f"{context} exceeds the reviewed record limit")
    rows: list[Mapping[str, object]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ValueError(f"{context}[{index}] must be an object")
        object_index = row.get("ObjectIndex")
        if (
            isinstance(object_index, bool)
            or not isinstance(object_index, int)
            or object_index != index
        ):
            raise ValueError(f"{context}[{index}] has an invalid ObjectIndex")
        rows.append(row)
    return tuple(rows)


def _json_casefold_value(
    value: Mapping[str, object], key: str, default: object = None
) -> object:
    """Return one already collision-checked JSON field by Unicode casefold."""
    folded = key.casefold()
    return next(
        (item for name, item in value.items() if name.casefold() == folded), default
    )


def json_record_from_object(
    row: Mapping[str, object],
    *,
    context: str,
    max_binary_bytes: int,
    blob_budget: JsonBlobBudget | None = None,
) -> dict[str, object]:
    """Convert one normalized schematic JSON object into a strict record."""
    record_type = _json_record_type(row.get("ObjectType"), context)

    if "BinaryData" in row:
        return _binary_json_record(
            row, record_type.value, context, max_binary_bytes, blob_budget
        )

    return _text_json_record(row, record_type.value, context)


def _json_record_type(value: object, context: str) -> SchRecordType:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} has an invalid ObjectType")
    try:
        object_type = SchJsonObjectType(value)
    except ValueError:
        object_type = None
    record_type = (
        SCH_JSON_OBJECT_TYPE_TO_RECORD_TYPE.get(object_type)
        if object_type is not None
        else None
    )
    if record_type is None:
        raise ValueError(f"{context} has unknown ObjectType {value!r}")
    return record_type


def _binary_json_record(
    row: Mapping[str, object],
    record_type: int,
    context: str,
    max_binary_bytes: int,
    blob_budget: JsonBlobBudget | None,
) -> dict[str, object]:
    allowed = {"ObjectType", "ObjectIndex", "BinaryData", "_binary_record_type"}
    unexpected = set(row).difference(allowed)
    if unexpected:
        field = min(unexpected)
        raise ValueError(f"{context}.{field} is not allowed on a binary row")
    annotation = row.get("_binary_record_type")
    if "_binary_record_type" in row and (
        isinstance(annotation, bool)
        or not isinstance(annotation, int)
        or annotation != record_type
    ):
        raise ValueError(
            f"{context} binary record annotation does not match ObjectType"
        )
    binary_data = _decode_binary_json_payload(
        row.get("BinaryData"),
        context=context,
        max_binary_bytes=max_binary_bytes,
    )
    if blob_budget is not None:
        blob_budget.consume(len(binary_data), context=context)
    if not binary_data or binary_data[0] != record_type:
        raise ValueError(f"{context} binary record type does not match ObjectType")
    return {
        "RECORD": record_type,
        "__BINARY_RECORD__": True,
        "__BINARY_DATA__": binary_data,
    }


def _text_json_record(
    row: Mapping[str, object], record_type: int, context: str
) -> dict[str, object]:
    record: dict[str, object] = {"RECORD": str(record_type)}
    for key, value in row.items():
        if key in {"ObjectType", "ObjectIndex"}:
            continue
        normalized = key.casefold()
        if normalized in _JSON_STRUCTURAL_KEYS or normalized.startswith("__binary_"):
            raise ValueError(f"{context}.{key} is a reserved structural field")
        record[key] = _json_record_value(value, f"{context}.{key}")
    return record


def _json_record_value(value: object, context: str) -> str:
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{context} must be a finite JSON number")
    if isinstance(value, str | int | float):
        return str(value)
    raise ValueError(f"{context} has an unsupported JSON value")


def _decode_binary_json_payload(
    value: object,
    *,
    context: str,
    max_binary_bytes: int,
) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context}.BinaryData must be a non-empty base64 string")
    compressed = _decode_base64_json_payload(value, context, max_binary_bytes)
    return _decompress_json_payload(compressed, context, max_binary_bytes)


def _decode_base64_json_payload(
    value: str, context: str, max_binary_bytes: int
) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{context}.BinaryData is not ASCII base64") from exc
    if len(encoded) > _MAX_BASE64_INPUT_BYTES:
        raise ValueError(f"{context}.BinaryData exceeds the encoded size limit")
    compressed_limit = _compressed_size_limit(max_binary_bytes)
    normalized = bytes(byte for byte in encoded if byte in _BASE64_ALPHABET)
    effective_length = len(normalized)
    if effective_length > ((compressed_limit + 2) // 3) * 4:
        raise ValueError(f"{context}.BinaryData exceeds the compressed size limit")
    try:
        compressed = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{context}.BinaryData is not valid base64") from exc
    if base64.b64encode(compressed) != normalized:
        raise ValueError(f"{context}.BinaryData has noncanonical padding or bits")
    if len(compressed) > compressed_limit:
        raise ValueError(f"{context}.BinaryData exceeds the compressed size limit")
    return compressed


def _compressed_size_limit(max_binary_bytes: int) -> int:
    zlib_bound = (
        max_binary_bytes
        + (max_binary_bytes >> 12)
        + (max_binary_bytes >> 14)
        + (max_binary_bytes >> 25)
        + 13
    )
    return min(_MAX_BASE64_DECODED_BYTES, zlib_bound)


def _decompress_json_payload(
    compressed: bytes, context: str, max_binary_bytes: int
) -> bytes:
    decoder = zlib.decompressobj()
    try:
        payload = decoder.decompress(compressed, max_binary_bytes + 1)
        if len(payload) <= max_binary_bytes and not decoder.unconsumed_tail:
            payload += decoder.flush(max_binary_bytes + 1 - len(payload))
    except zlib.error as exc:
        raise ValueError(f"{context}.BinaryData is not valid zlib data") from exc
    if len(payload) > max_binary_bytes or decoder.unconsumed_tail:
        raise ValueError(f"{context}.BinaryData exceeds the decompressed size limit")
    if not decoder.eof:
        raise ValueError(f"{context}.BinaryData contains a truncated zlib stream")
    return payload
