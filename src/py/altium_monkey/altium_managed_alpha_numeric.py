"""AD26-compatible alphanumeric comparison for schematic hierarchy names."""

from __future__ import annotations

from bisect import bisect_right

from .altium_managed_alpha_numeric_lower import (
    MANAGED_LOWER_RANGES,
    MANAGED_LOWER_SINGLETONS,
)
from .altium_dotnet_ordinal import dotnet_utf16_units

_DECIMAL_DIGIT_STARTS = (
    0x0030,
    0x0660,
    0x06F0,
    0x07C0,
    0x0966,
    0x09E6,
    0x0A66,
    0x0AE6,
    0x0B66,
    0x0BE6,
    0x0C66,
    0x0CE6,
    0x0D66,
    0x0DE6,
    0x0E50,
    0x0ED0,
    0x0F20,
    0x1040,
    0x1090,
    0x17E0,
    0x1810,
    0x1946,
    0x19D0,
    0x1A80,
    0x1A90,
    0x1B50,
    0x1BB0,
    0x1C40,
    0x1C50,
    0xA620,
    0xA8D0,
    0xA900,
    0xA9D0,
    0xA9F0,
    0xAA50,
    0xABF0,
    0xFF10,
)
_MANAGED_LOWER_RANGE_STARTS = tuple(row[0] for row in MANAGED_LOWER_RANGES)
_MANAGED_LOWER_SINGLETON_MAP = dict(MANAGED_LOWER_SINGLETONS)


def _is_managed_decimal_digit(value: int) -> bool:
    return any(start <= value < start + 10 for start in _DECIMAL_DIGIT_STARTS)


def managed_designator_prefix(value: str) -> str:
    """Return the prefix before trailing .NET ``char.IsDigit`` units."""
    cut = len(value)
    for index in range(len(value) - 1, -1, -1):
        code_point = ord(value[index])
        if code_point > 0xFFFF or not _is_managed_decimal_digit(code_point):
            break
        cut = index
    return value[:cut]


def _managed_en_us_lower(value: int) -> int:
    singleton = _MANAGED_LOWER_SINGLETON_MAP.get(value)
    if singleton is not None:
        return singleton
    index = bisect_right(_MANAGED_LOWER_RANGE_STARTS, value) - 1
    if index >= 0:
        start, end, step, delta = MANAGED_LOWER_RANGES[index]
        if value <= end and (value - start) % step == 0:
            return value + delta
    return value


def managed_alpha_numeric_compare(left: str, right: str) -> int:
    """Compare like AD26 ``AlphaNumericComparatorFast.CompareStr`` on en-US."""

    left_units = dotnet_utf16_units(left)
    right_units = dotnet_utf16_units(right)
    left_index = 0
    right_index = 0
    case_result = 0
    while left_index < len(left_units) and right_index < len(right_units):
        ordering, next_left, next_right, case_ordering = _compare_at(
            left_units,
            right_units,
            left_index,
            right_index,
        )
        if ordering:
            return ordering
        if case_result == 0:
            case_result = case_ordering
        left_index = next_left
        right_index = next_right
    if left_index < len(left_units):
        return 1
    if right_index < len(right_units):
        return -1
    if case_result == 0:
        return len(left_units) - len(right_units)
    return case_result


def _compare_at(
    left: tuple[int, ...],
    right: tuple[int, ...],
    left_index: int,
    right_index: int,
) -> tuple[int, int, int, int]:
    left_unit = left[left_index]
    right_unit = right[right_index]
    left_digit = _is_managed_decimal_digit(left_unit)
    right_digit = _is_managed_decimal_digit(right_unit)
    if not left_digit and not right_digit:
        return _compare_text_units(left_unit, right_unit, left_index, right_index)
    if left_digit and right_digit:
        ordering, next_left, next_right = _compare_digit_runs(
            left,
            right,
            left_index,
            right_index,
        )
        return ordering, next_left, next_right, 0
    return (-1 if left_digit else 1), left_index, right_index, 0


def _compare_text_units(
    left: int,
    right: int,
    left_index: int,
    right_index: int,
) -> tuple[int, int, int, int]:
    if left == right:
        return 0, left_index + 1, right_index + 1, 0
    left_lower = _managed_en_us_lower(left)
    right_lower = _managed_en_us_lower(right)
    if left_lower != right_lower:
        return left_lower - right_lower, left_index, right_index, 0
    case_ordering = 1 if left != left_lower else -1
    return 0, left_index + 1, right_index + 1, case_ordering


def _compare_digit_runs(
    left: tuple[int, ...],
    right: tuple[int, ...],
    left_end: int,
    right_end: int,
) -> tuple[int, int, int]:
    left_start = left_end
    right_start = right_end
    left_end = _digit_run_end(left, left_end)
    right_end = _digit_run_end(right, right_end)
    left_start = _ascii_zero_end(left, left_start, left_end)
    right_start = _ascii_zero_end(right, right_start, right_end)
    length_result = (left_end - left_start) - (right_end - right_start)
    if length_result:
        return length_result, left_end, right_end
    left_digits = left[left_start:left_end]
    right_digits = right[right_start:right_end]
    if left_digits != right_digits:
        return (-1 if left_digits < right_digits else 1), left_end, right_end
    return 0, left_end, right_end


def _digit_run_end(values: tuple[int, ...], index: int) -> int:
    while index < len(values) and _is_managed_decimal_digit(values[index]):
        index += 1
    return index


def _ascii_zero_end(values: tuple[int, ...], index: int, end: int) -> int:
    while index < end and values[index] == 0x0030:
        index += 1
    return index
