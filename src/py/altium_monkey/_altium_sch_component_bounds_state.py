"""Render-local component bounds filters; source records remain unchanged."""

from __future__ import annotations

from .altium_record_sch__component import AltiumSchComponent
from .altium_record_sch__parameter import AltiumSchImageParameter, AltiumSchParameter
from .altium_record_sch__pin import AltiumSchPin
from .altium_record_types import SchPrimitive
from .altium_serializer import AltiumSerializer


def _bounds_owner_part_id(record: SchPrimitive) -> int:
    if record.owner_part_id is not None:
        return record.owner_part_id
    # V5 ordinary graphical import defaults to zero; pins retain minus one.
    if isinstance(record, AltiumSchPin) or record._raw_record is None:
        return -1
    return 0


def _component_primitive_passes_bounds_filter(
    record: SchPrimitive, component: AltiumSchComponent
) -> bool:
    """Apply current-part filtering to one spatial child of this immediate owner."""
    if isinstance(record, AltiumSchImageParameter):
        return not record.is_hidden
    if isinstance(record, AltiumSchParameter):
        return component.show_hidden_fields or not record.is_hidden
    display_mode = record.owner_part_display_mode or 0
    if display_mode != component.display_mode:
        return False
    owner_part = _bounds_owner_part_id(record)
    part_matches = component.current_part_id in (-1, owner_part)
    if isinstance(record, AltiumSchPin):
        return (part_matches or owner_part == 0) and (
            component.show_hidden_pins or not record.is_hidden
        )
    return part_matches


def _bounds_schematic_block_flag(record: SchPrimitive) -> bool:
    typed = getattr(record, "is_schematic_block_object", None)
    if isinstance(typed, bool):
        return typed
    # The base record preserves this flag but does not expose a typed property.
    # Its parsed case-insensitive view avoids rescanning every field per query.
    if "IsSchematicBlockObject" not in record._record:
        return False
    flag, _ = AltiumSerializer().read_bool(
        record._record, "IsSchematicBlockObject", default=False
    )
    return flag


def _bounds_is_accessible(
    record: SchPrimitive,
    *,
    normalizing_component: AltiumSchComponent | None = None,
) -> bool:
    """Read saved/BOC state, or derive the component's spatial accessibility pass."""
    if _bounds_schematic_block_flag(record):
        return False
    return _bounds_data_is_accessible(
        record, normalizing_component=normalizing_component
    )


def _bounds_data_is_accessible(
    record: SchPrimitive,
    *,
    normalizing_component: AltiumSchComponent | None = None,
) -> bool:
    """Return data accessibility before the engine's schematic-block override."""
    if normalizing_component is None:
        return not record.is_not_accessible
    if isinstance(record, AltiumSchPin):
        return normalizing_component.pins_moveable
    return isinstance(record, AltiumSchParameter) and not isinstance(
        record, AltiumSchImageParameter
    )
