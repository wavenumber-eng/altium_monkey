"""
Shared schematic display-mode filtering helpers.
"""

from __future__ import annotations


_MISSING_OWNER_PART_DISPLAY_MODE = object()


def record_belongs_to_display_mode(record: object, display_mode: int | None) -> bool:
    """
    Return whether a schematic child record belongs to the selected display mode.
    """
    if display_mode is None:
        return True

    record_mode = getattr(
        record,
        "owner_part_display_mode",
        _MISSING_OWNER_PART_DISPLAY_MODE,
    )
    if record_mode is _MISSING_OWNER_PART_DISPLAY_MODE:
        return True
    if record_mode is None:
        record_mode = 0
    return int(record_mode) == int(display_mode)
