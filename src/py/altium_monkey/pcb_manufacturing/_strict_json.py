"""Strict JSON framing before generated manufacturing-contract decoding.

msgspec 0.21.1 accepts the last occurrence of a duplicate object member. The
generated model decoder is therefore sufficient only for already-governed
bytes. Untrusted wire bytes pass through this model-independent preflight.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class StrictJsonDiagnostic(Exception):
    """Language-neutral strict JSON rejection."""

    code: str
    path: str


@dataclass(frozen=True)
class _ObjectPairs:
    pairs: tuple[tuple[str, object], ...]


_MIN_INT64 = -(1 << 63)
_MAX_INT64 = (1 << 63) - 1


def preflight_strict_json(raw: bytes) -> None:
    """Reject duplicate members, non-JSON numbers, and invalid strings."""

    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=lambda pairs: _ObjectPairs(tuple(pairs)),
            parse_constant=_reject_non_json_number,
        )
    except UnicodeDecodeError as error:
        raise StrictJsonDiagnostic(
            "PCB_MANUFACTURING_JSON_STRING_INVALID", "$"
        ) from error
    _materialize(parsed, "$")


def _materialize(value: object, path: str) -> object:
    if isinstance(value, _ObjectPairs):
        result: dict[str, object] = {}
        for key, item in value.pairs:
            if _contains_surrogate(key):
                raise StrictJsonDiagnostic(
                    "PCB_MANUFACTURING_JSON_STRING_INVALID", path
                )
            child_path = f"{path}.{key}"
            if key in result:
                raise StrictJsonDiagnostic(
                    "PCB_MANUFACTURING_JSON_DUPLICATE_KEY", child_path
                )
            result[key] = _materialize(item, child_path)
        return result
    if isinstance(value, list):
        return [
            _materialize(item, f"{path}.{index}") for index, item in enumerate(value)
        ]
    if isinstance(value, str) and _contains_surrogate(value):
        raise StrictJsonDiagnostic("PCB_MANUFACTURING_JSON_STRING_INVALID", path)
    if isinstance(value, int) and not isinstance(value, bool):
        if not _MIN_INT64 <= value <= _MAX_INT64:
            raise StrictJsonDiagnostic(
                "PCB_MANUFACTURING_JSON_INTEGER_OUT_OF_RANGE", path
            )
    if isinstance(value, float) and not math.isfinite(value):
        raise StrictJsonDiagnostic("PCB_MANUFACTURING_JSON_NUMBER_INVALID", path)
    return value


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _reject_non_json_number(_token: str) -> object:
    raise StrictJsonDiagnostic("PCB_MANUFACTURING_JSON_NUMBER_INVALID", "$")
