"""
Shared schematic display-mode filtering helpers.
"""

from __future__ import annotations

from typing import Any, cast


_MISSING_OWNER_PART_DISPLAY_MODE = object()


def _raw_pin_record(value: object) -> object:
    return getattr(value, "pin", value)


def component_shows_hidden_pins(component: object) -> bool:
    record = getattr(component, "record", component)
    return bool(getattr(record, "show_hidden_pins", False))


def pin_has_managed_hidden_net(value: object) -> bool:
    pin = _raw_pin_record(value)
    return getattr(pin, "hidden_net_name", None) is not None


def pin_is_managed_part_member(value: object) -> bool:
    pin = _raw_pin_record(value)
    return not bool(getattr(pin, "is_hidden", False)) or pin_has_managed_hidden_net(pin)


def pin_is_runtime_hidden(value: object, component: object | None) -> bool:
    pin = _raw_pin_record(value)
    return bool(getattr(pin, "is_hidden", False)) and (
        component is None or not component_shows_hidden_pins(component)
    )


def _pin_owner_part_id_for_component_view(value: object) -> int:
    """Resolve Altium's omitted SchDoc pin part ID as the common part."""
    pin = _raw_pin_record(value)
    owner_part_id = getattr(pin, "owner_part_id", None)
    if owner_part_id is None:
        return 0
    resolved = int(owner_part_id)
    raw_record = getattr(pin, "_raw_record", None)
    if resolved != -1 or not isinstance(raw_record, dict):
        return resolved
    if any(str(name).casefold() == "ownerpartid" for name in raw_record):
        return resolved
    return 0


def pin_belongs_to_component_view(value: object, component: object) -> bool:
    pin = _raw_pin_record(value)
    record = getattr(component, "record", component)
    resolved_owner_part_id = _pin_owner_part_id_for_component_view(pin)
    current_part_value = getattr(record, "current_part_id", 1)
    current_part_id = 1 if current_part_value is None else int(current_part_value)
    if resolved_owner_part_id not in (0, current_part_id):
        return False
    display_mode_count = int(getattr(record, "display_mode_count", 0) or 0)
    if display_mode_count <= 1:
        return True
    owner_display_mode = getattr(pin, "owner_part_display_mode", None)
    resolved_owner_display_mode = (
        0 if owner_display_mode is None else int(owner_display_mode)
    )
    return resolved_owner_display_mode == int(getattr(record, "display_mode", 0) or 0)


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
    return int(cast(Any, record_mode)) == int(display_mode)
