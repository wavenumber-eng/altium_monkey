"""
Shared helpers for SchLib pin auxiliary streams.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypedDict

from .altium_pintextdata_modifier import PinTextData, PinTextPosition
from .altium_sch_auxiliary_codec import encode_auxiliary_stream
from .altium_sch_enums import PinItemMode, PinTextAnchor, PinTextOrientation

if TYPE_CHECKING:
    from .altium_record_sch__pin import AltiumSchPin, PinTextSettings


class _PinTextContext(TypedDict):
    pintextdata_designator: str
    name_font_id: int
    designator_font_id: int
    name_color: int
    designator_color: int
    name_rotation: int
    designator_rotation: int
    name_ref_to_comp: bool
    des_ref_to_comp: bool
    name_needs_position: bool
    des_needs_position: bool
    name_margin_mils: float
    des_margin_mils: float
    name_has_custom_font: bool
    des_has_custom_font: bool


def build_pintextdata_stream_for_pins(
    pins: Sequence["AltiumSchPin"],
    *,
    resolve_font_id: Callable[["PinTextSettings"], int],
) -> bytes | None:
    """
    Build a SchLib ``PinTextData`` stream from live pin objects.

    This mirrors the current authoring-side binary layout used for generated
    libraries so new-object save paths and extracted-symbol save paths can share
    one implementation.
    """
    pins_with_pintextdata = [
        (pin_index, pin) for pin_index, pin in enumerate(pins) if pin.needs_pintextdata
    ]
    if not pins_with_pintextdata:
        return None

    entries: list[tuple[str, bytes]] = []

    # Altium keys each PinTextData record by the pin's full symbol-list index,
    # not by a dense index over only customized pins.
    for pin_index, pin in pins_with_pintextdata:
        ctx = _resolve_pintext_context(
            pin,
            pin_index,
            resolve_name_font_id=resolve_font_id,
            resolve_designator_font_id=resolve_font_id,
        )
        attrs = _build_pintext_attrs(ctx)
        entries.append((str(ctx["pintextdata_designator"]), attrs))

    return encode_auxiliary_stream("PinTextData", entries)


def build_pinfrac_stream_for_pins(pins: Sequence["AltiumSchPin"]) -> bytes | None:
    """
    Build a SchLib ``PinFrac`` stream from live pin objects.

    This uses the currently validated Python layout for sub-10-mil pin
    coordinate/length precision.
    """
    pins_with_frac: list[tuple[int, int, int, int, int]] = []
    for pin_idx, pin in enumerate(pins):
        x_frac = (
            pin.location.x_frac
            if pin.location.x_frac != 0
            else (pin.location_x_frac or 0)
        )
        y_frac = (
            pin.location.y_frac
            if pin.location.y_frac != 0
            else (pin.location_y_frac or 0)
        )
        length_frac = pin.pin_length_frac or 0
        owner_part_id = pin.owner_part_id if pin.owner_part_id is not None else 1

        if x_frac != 0 or y_frac != 0 or length_frac != 0:
            pins_with_frac.append((pin_idx, owner_part_id, x_frac, y_frac, length_frac))

    if not pins_with_frac:
        return None

    stream = bytearray()
    header = f"|HEADER=PinFrac|Weight={len(pins_with_frac)}\x00"
    header_bytes = header.encode("latin-1")
    stream.extend(struct.pack("<I", len(header_bytes)))
    stream.extend(header_bytes)

    for pin_idx, owner_part_id, x_frac, y_frac, length_frac in pins_with_frac:
        payload = struct.pack("<iii", x_frac, y_frac, length_frac)
        compressed = zlib.compress(payload)

        record = bytearray()
        record.append(owner_part_id & 0xFF)
        record.extend(struct.pack("<H", 0x01D0))
        record.append(pin_idx & 0xFF)
        record.extend(struct.pack("<I", len(compressed)))
        record.extend(compressed)

        rec_len = len(record)
        stream.extend(struct.pack("<I", rec_len)[:3])
        stream.extend(record)

    return bytes(stream)


def _resolve_pintext_context(
    pin: "AltiumSchPin",
    pin_index: int,
    *,
    resolve_name_font_id: Callable[["PinTextSettings"], int],
    resolve_designator_font_id: Callable[["PinTextSettings"], int],
) -> _PinTextContext:
    name_settings = pin.name_settings
    designator_settings = pin.designator_settings

    name_color = (
        name_settings.color
        if name_settings.font_mode == PinItemMode.CUSTOM
        else pin.color
    )
    designator_color = (
        designator_settings.color
        if designator_settings.font_mode == PinItemMode.CUSTOM
        else pin.color
    )
    name_needs_position = name_settings.position_mode == PinItemMode.CUSTOM
    des_needs_position = designator_settings.position_mode == PinItemMode.CUSTOM
    name_margin_mils = pin.name_margin_mils
    if name_margin_mils is None:
        name_margin_mils = 0.0 if name_needs_position else 50.0
    des_margin_mils = pin.designator_margin_mils
    if des_margin_mils is None:
        des_margin_mils = 0.0 if des_needs_position else 50.0

    return {
        "pintextdata_designator": str(pin_index),
        "name_font_id": resolve_name_font_id(name_settings),
        "designator_font_id": resolve_designator_font_id(designator_settings),
        "name_color": int(name_color),
        "designator_color": int(designator_color),
        "name_rotation": int(name_settings.rotation.value) * 90,
        "designator_rotation": int(designator_settings.rotation.value) * 90,
        "name_ref_to_comp": name_settings.rotation_anchor == PinTextAnchor.COMPONENT,
        "des_ref_to_comp": (
            designator_settings.rotation_anchor == PinTextAnchor.COMPONENT
        ),
        "name_needs_position": name_needs_position,
        "des_needs_position": des_needs_position,
        "name_margin_mils": float(name_margin_mils),
        "des_margin_mils": float(des_margin_mils),
        "name_has_custom_font": name_settings.font_mode == PinItemMode.CUSTOM,
        "des_has_custom_font": designator_settings.font_mode == PinItemMode.CUSTOM,
    }


def _build_pintext_attrs(ctx: _PinTextContext) -> bytes:
    name_position = None
    if ctx["name_needs_position"]:
        name_position = PinTextPosition(
            margin_mils=ctx["name_margin_mils"],
            orientation=PinTextOrientation(ctx["name_rotation"]),
            reference_to_component=ctx["name_ref_to_comp"],
        )
    designator_position = None
    if ctx["des_needs_position"]:
        designator_position = PinTextPosition(
            margin_mils=ctx["des_margin_mils"],
            orientation=PinTextOrientation(ctx["designator_rotation"]),
            reference_to_component=ctx["des_ref_to_comp"],
        )
    pin_text_data = PinTextData(
        format_type="AUTO",
        raw_data=bytearray(),
        name_font_id=ctx["name_font_id"] if ctx["name_has_custom_font"] else None,
        name_color=ctx["name_color"] if ctx["name_has_custom_font"] else None,
        designator_font_id=(
            ctx["designator_font_id"] if ctx["des_has_custom_font"] else None
        ),
        designator_color=(
            ctx["designator_color"] if ctx["des_has_custom_font"] else None
        ),
        position=name_position,
        name_position=name_position,
        designator_position=designator_position,
    )
    return pin_text_data.serialize()
