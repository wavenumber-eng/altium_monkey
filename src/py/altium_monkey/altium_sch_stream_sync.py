"""Private save-time helpers for local schematic stream synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

_I32_MAX = (1 << 31) - 1
_I32_MIN = -(1 << 31)
_MAX_RECORDS_PER_STREAM = 1_000_000
_MAX_TOTAL_RECORDS_PER_DOCUMENT = 1_000_000


class _IndexDispositionKind(Enum):
    ASSIGN = "assign"
    PRESERVE = "preserve"
    OMIT = "omit"


@dataclass(frozen=True)
class _IndexDisposition:
    kind: _IndexDispositionKind
    owner_position: int | None = None

    @classmethod
    def assign(cls, owner_position: int) -> _IndexDisposition:
        return cls(_IndexDispositionKind.ASSIGN, owner_position)

    @classmethod
    def preserve(cls) -> _IndexDisposition:
        return cls(_IndexDispositionKind.PRESERVE)

    @classmethod
    def omit(cls) -> _IndexDisposition:
        return cls(_IndexDispositionKind.OMIT)


@dataclass(frozen=True)
class _IndexPlanEntry:
    record_token: int
    current_value: int
    raw_present: bool
    disposition: _IndexDisposition


@dataclass(frozen=True)
class _IndexUpdate:
    record_token: int
    value: int


def _require_plain_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    return value


def _require_i32(name: str, value: int) -> int:
    checked = _require_plain_int(name, value)
    if checked < _I32_MIN or checked > _I32_MAX:
        raise ValueError(f"{name} must fit signed 32-bit storage")
    return checked


def _require_reviewed_limit(name: str, value: int, reviewed_maximum: int) -> int:
    checked = _require_plain_int(name, value)
    if checked < 0 or checked > reviewed_maximum:
        raise ValueError(
            f"{name} must be between 0 and reviewed maximum {reviewed_maximum}"
        )
    return checked


def _derive_stream_weight(
    staged_record_count: int,
    *,
    max_records: int = _MAX_RECORDS_PER_STREAM,
) -> int:
    """Return the checked Weight for an already-staged physical warehouse."""
    limit = _require_reviewed_limit("max_records", max_records, _MAX_RECORDS_PER_STREAM)
    count = _require_plain_int("staged_record_count", staged_record_count)
    if count < 0:
        raise ValueError("staged_record_count must be nonnegative")
    if count > limit:
        raise ValueError(f"staged record count {count} exceeds limit {limit}")
    return _require_i32("Weight", count)


def _derive_schlib_weight(
    serialized_data_counts: Sequence[int],
    *,
    max_total_records: int = _MAX_TOTAL_RECORDS_PER_DOCUMENT,
) -> int:
    """Return the checked library root plus staged symbol Data record counts."""
    limit = _require_reviewed_limit(
        "max_total_records",
        max_total_records,
        _MAX_TOTAL_RECORDS_PER_DOCUMENT,
    )
    if len(serialized_data_counts) > limit:
        raise ValueError(
            f"serialized Data count entries {len(serialized_data_counts)} "
            f"exceed limit {limit}"
        )
    total = 1
    if total > limit:
        raise ValueError(f"total record count {total} exceeds limit {limit}")
    for index, value in enumerate(serialized_data_counts):
        count = _require_plain_int(f"serialized_data_counts[{index}]", value)
        if count < 0:
            raise ValueError("serialized Data record counts must be nonnegative")
        total += count
        if total > limit:
            raise ValueError(f"total record count {total} exceeds limit {limit}")
        _require_i32("Weight", total)
    return total


def _validate_index_entry(
    entry: _IndexPlanEntry,
    seen_tokens: set[int],
    assigned_positions: set[int],
) -> tuple[int, int]:
    token = _require_plain_int("record_token", entry.record_token)
    if token in seen_tokens:
        raise ValueError(f"duplicate record token {token}")
    seen_tokens.add(token)
    current = _require_i32("current index", entry.current_value)
    if not isinstance(entry.raw_present, bool):
        raise TypeError("raw_present must be a bool")
    disposition = entry.disposition
    if not isinstance(disposition, _IndexDisposition):
        raise TypeError("disposition must be an _IndexDisposition")
    if not isinstance(disposition.kind, _IndexDispositionKind):
        raise ValueError("disposition kind is invalid")
    if disposition.kind is _IndexDispositionKind.ASSIGN:
        if disposition.owner_position is None:
            raise ValueError("Assign requires an owner position")
        desired = _require_i32("owner position", disposition.owner_position)
        if desired < 0:
            raise ValueError("owner position must be nonnegative")
        if desired in assigned_positions:
            raise ValueError(f"duplicate owner position {desired}")
        assigned_positions.add(desired)
    elif disposition.owner_position is not None:
        raise ValueError(
            f"{disposition.kind.value.title()} does not accept an owner position"
        )
    return token, current


def _index_update_for_entry(
    entry: _IndexPlanEntry,
    token: int,
    current: int,
    *,
    dirty: bool,
) -> _IndexUpdate | None:
    if not dirty or entry.disposition.kind is _IndexDispositionKind.PRESERVE:
        return None
    if entry.disposition.kind is _IndexDispositionKind.OMIT:
        return None if current == -2 else _IndexUpdate(token, -2)
    desired = entry.disposition.owner_position
    if desired is None:  # validated by _validate_index_entry
        raise AssertionError("validated Assign position is absent")
    if current == desired:
        return None
    return _IndexUpdate(token, desired)


def _plan_index_updates(
    entries: Sequence[_IndexPlanEntry],
    *,
    dirty: bool,
    max_records: int = _MAX_RECORDS_PER_STREAM,
) -> tuple[_IndexUpdate, ...]:
    """Validate one owner-relative plan completely before returning mutations."""
    limit = _require_reviewed_limit("max_records", max_records, _MAX_RECORDS_PER_STREAM)
    if len(entries) > limit:
        raise ValueError(f"index plan length {len(entries)} exceeds limit {limit}")

    seen_tokens: set[int] = set()
    assigned_positions: set[int] = set()
    updates: list[_IndexUpdate] = []
    for entry in entries:
        token, current = _validate_index_entry(entry, seen_tokens, assigned_positions)
        update = _index_update_for_entry(entry, token, current, dirty=dirty)
        if update is not None:
            updates.append(update)
    return tuple(updates)
