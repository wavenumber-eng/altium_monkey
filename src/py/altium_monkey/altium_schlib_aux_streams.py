"""
Shared helpers for SchLib pin auxiliary streams.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from .altium_pintextdata_modifier import PinTextData
from .altium_sch_enums import PinItemMode, PinTextAnchor

if TYPE_CHECKING:
    from .altium_record_sch__pin import AltiumSchPin, PinTextSettings


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
    pins_with_pintextdata = [pin for pin in pins if pin.needs_pintextdata]
    if not pins_with_pintextdata:
        return None

    result = bytearray()
    header_text = f"|HEADER=PinTextData|Weight={len(pins_with_pintextdata)}"
    header_bytes = header_text.encode("iso-8859-1")
    result.extend(struct.pack("<I", len(header_bytes) + 1))
    result.extend(header_bytes)
    result.append(0x00)

    for pin_index, pin in enumerate(pins_with_pintextdata):
        ctx = _resolve_pintext_context(
            pin,
            pin_index,
            resolve_name_font_id=resolve_font_id,
            resolve_designator_font_id=resolve_font_id,
        )
        attrs = _build_pintext_attrs(ctx)
        _append_pintext_record(result, str(ctx["pintextdata_designator"]), attrs)

    return bytes(result)


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
) -> dict[str, object]:
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
        "name_has_customization": pin.needs_custom_name_pintextdata,
        "des_has_customization": pin.needs_custom_designator_pintextdata,
        "has_custom_font": (
            name_settings.font_mode == PinItemMode.CUSTOM
            or designator_settings.font_mode == PinItemMode.CUSTOM
        ),
    }


def _pintext_quadrant_flags(rotation: int, ref_to_comp: bool) -> int:
    flags = 0x01
    if ref_to_comp:
        flags |= 0x02
    flags |= ((rotation // 90) << 2) & 0x0C
    return flags


def _pintext_extended_flags(rotation: int, ref_to_comp: bool) -> int:
    flags = 0x11 | (0x02 if ref_to_comp else 0x00)
    if rotation == 90:
        flags |= 0x04
    elif rotation == 180:
        flags |= 0x08
    elif rotation == 270:
        flags |= 0x0C
    return flags


def _build_pintext_dual_position_attrs(
    *,
    name_margin_mils: float,
    des_margin_mils: float,
    name_ref_to_comp: bool,
    des_ref_to_comp: bool,
    name_rotation: int,
    designator_rotation: int,
) -> bytes:
    name_margin_dxp = round(name_margin_mils * 10000)
    des_margin_dxp = round(des_margin_mils * 10000)
    attrs = bytearray(10)
    attrs[0] = _pintext_quadrant_flags(name_rotation, name_ref_to_comp)
    struct.pack_into("<i", attrs, 1, name_margin_dxp)
    attrs[5] = _pintext_quadrant_flags(designator_rotation, des_ref_to_comp)
    struct.pack_into("<i", attrs, 6, des_margin_dxp)
    return bytes(attrs)


def _build_pintext_dual_custom_attrs(
    *,
    name_margin_mils: float,
    des_margin_mils: float,
    name_ref_to_comp: bool,
    des_ref_to_comp: bool,
    name_rotation: int,
    designator_rotation: int,
    name_font_id: int,
    designator_font_id: int,
    name_color: int,
    designator_color: int,
) -> bytes:
    name_margin_dxp = round(name_margin_mils * 10000)
    des_margin_dxp = round(des_margin_mils * 10000)
    attrs = bytearray(22)
    attrs[0] = _pintext_extended_flags(name_rotation, name_ref_to_comp)
    struct.pack_into("<i", attrs, 1, name_margin_dxp)
    struct.pack_into("<H", attrs, 5, name_font_id)
    struct.pack_into("<I", attrs, 7, name_color)
    attrs[11] = _pintext_extended_flags(designator_rotation, des_ref_to_comp)
    struct.pack_into("<i", attrs, 12, des_margin_dxp)
    struct.pack_into("<H", attrs, 16, designator_font_id)
    struct.pack_into("<I", attrs, 18, designator_color)
    return bytes(attrs)


def _build_pintext_designator_position_attrs(
    *,
    des_margin_mils: float,
    des_ref_to_comp: bool,
    designator_rotation: int,
    designator_font_id: int,
    designator_color: int,
    name_font_id: int,
    name_color: int,
    name_has_customization: bool,
) -> bytes:
    if name_has_customization:
        des_margin_dxp = round(des_margin_mils * 10000)
        attrs = bytearray(18)
        attrs[0] = 0x10
        struct.pack_into("<H", attrs, 1, name_font_id)
        struct.pack_into("<I", attrs, 3, name_color)
        attrs[7] = _pintext_extended_flags(designator_rotation, des_ref_to_comp)
        struct.pack_into("<i", attrs, 8, des_margin_dxp)
        struct.pack_into("<H", attrs, 12, designator_font_id)
        struct.pack_into("<I", attrs, 14, designator_color)
        return bytes(attrs)

    des_margin_int16 = round(des_margin_mils * 39.0625)
    attrs = bytearray(12)
    attrs[0] = 0x00
    attrs[1] = _pintext_extended_flags(designator_rotation, des_ref_to_comp)
    attrs[2] = 0x00 if designator_rotation == 90 else 0x80
    struct.pack_into("<H", attrs, 3, des_margin_int16)
    attrs[5] = 0x00
    attrs[6] = designator_font_id
    struct.pack_into("<I", attrs, 7, designator_color)
    attrs[11] = 0x00
    return bytes(attrs)


def _build_pintext_name_position_attrs(
    *,
    name_margin_mils: float,
    name_ref_to_comp: bool,
    name_rotation: int,
    name_font_id: int,
    name_color: int,
    designator_font_id: int,
    designator_color: int,
    des_has_customization: bool,
) -> bytes:
    if des_has_customization:
        name_margin_dxp = round(name_margin_mils * 10000)
        attrs = bytearray(18)
        attrs[0] = _pintext_extended_flags(name_rotation, name_ref_to_comp)
        struct.pack_into("<i", attrs, 1, name_margin_dxp)
        attrs[5] = name_font_id
        struct.pack_into("<I", attrs, 6, name_color)
        attrs[10] = 0x00
        attrs[11] = 0x10
        attrs[12] = designator_font_id
        struct.pack_into("<I", attrs, 13, designator_color)
        attrs[17] = 0x00
        return bytes(attrs)

    name_margin_int16 = round(name_margin_mils * 39.0625)
    attrs = bytearray(12)
    attrs[0] = _pintext_extended_flags(name_rotation, name_ref_to_comp)
    attrs[1] = 0x00
    struct.pack_into("<H", attrs, 2, name_margin_int16)
    attrs[4] = 0x00
    attrs[5] = name_font_id
    struct.pack_into("<I", attrs, 6, name_color)
    attrs[10] = 0x00
    attrs[11] = 0x00
    return bytes(attrs)


def _build_pintext_single_custom_attrs(
    *,
    is_name: bool,
    font_id: int,
    color: int,
) -> bytes:
    attrs = bytearray(8)
    if is_name:
        attrs[0] = 0x10
        attrs[1] = font_id
        struct.pack_into("<I", attrs, 2, color)
    else:
        attrs[0] = 0x00
        attrs[1] = 0x10
        attrs[2] = font_id
        struct.pack_into("<I", attrs, 3, color)
    attrs[6] = 0x00
    attrs[7] = 0x00
    return bytes(attrs)


def _build_pintext_attrs(ctx: dict[str, object]) -> bytes:
    name_needs_position = bool(ctx["name_needs_position"])
    des_needs_position = bool(ctx["des_needs_position"])
    has_custom_font = bool(ctx["has_custom_font"])
    name_has_customization = bool(ctx["name_has_customization"])
    des_has_customization = bool(ctx["des_has_customization"])
    name_rotation = int(ctx["name_rotation"])
    designator_rotation = int(ctx["designator_rotation"])
    name_ref_to_comp = bool(ctx["name_ref_to_comp"])
    des_ref_to_comp = bool(ctx["des_ref_to_comp"])
    name_font_id = int(ctx["name_font_id"])
    designator_font_id = int(ctx["designator_font_id"])
    name_color = int(ctx["name_color"])
    designator_color = int(ctx["designator_color"])
    name_margin_mils = float(ctx["name_margin_mils"])
    des_margin_mils = float(ctx["des_margin_mils"])

    if name_needs_position and des_needs_position and not has_custom_font:
        return _build_pintext_dual_position_attrs(
            name_margin_mils=name_margin_mils,
            des_margin_mils=des_margin_mils,
            name_ref_to_comp=name_ref_to_comp,
            des_ref_to_comp=des_ref_to_comp,
            name_rotation=name_rotation,
            designator_rotation=designator_rotation,
        )

    if name_needs_position and des_needs_position:
        return _build_pintext_dual_custom_attrs(
            name_margin_mils=name_margin_mils,
            des_margin_mils=des_margin_mils,
            name_ref_to_comp=name_ref_to_comp,
            des_ref_to_comp=des_ref_to_comp,
            name_rotation=name_rotation,
            designator_rotation=designator_rotation,
            name_font_id=name_font_id,
            designator_font_id=designator_font_id,
            name_color=name_color,
            designator_color=designator_color,
        )

    if des_needs_position and not name_needs_position:
        return _build_pintext_designator_position_attrs(
            des_margin_mils=des_margin_mils,
            des_ref_to_comp=des_ref_to_comp,
            designator_rotation=designator_rotation,
            designator_font_id=designator_font_id,
            designator_color=designator_color,
            name_font_id=name_font_id,
            name_color=name_color,
            name_has_customization=name_has_customization,
        )

    if name_needs_position and not des_needs_position:
        return _build_pintext_name_position_attrs(
            name_margin_mils=name_margin_mils,
            name_ref_to_comp=name_ref_to_comp,
            name_rotation=name_rotation,
            name_font_id=name_font_id,
            name_color=name_color,
            designator_font_id=designator_font_id,
            designator_color=designator_color,
            des_has_customization=des_has_customization,
        )

    if des_has_customization and not name_has_customization:
        return _build_pintext_single_custom_attrs(
            is_name=False,
            font_id=designator_font_id,
            color=designator_color,
        )

    if name_has_customization and not des_has_customization:
        return _build_pintext_single_custom_attrs(
            is_name=True,
            font_id=name_font_id,
            color=name_color,
        )

    pin_text_data = PinTextData.create_both_format(
        name_font_id=name_font_id,
        designator_font_id=designator_font_id,
        name_color=name_color,
        designator_color=designator_color,
    )
    return pin_text_data.serialize()


def _append_pintext_record(result: bytearray, designator: str, attrs: bytes) -> None:
    compressed = zlib.compress(attrs)
    designator_bytes = designator.encode("iso-8859-1")
    str_len = len(designator_bytes)
    record_len = 2 + str_len + 4 + len(compressed)
    result.extend(struct.pack("<I", record_len)[:3])
    result.append(0x01)
    result.append(0xD0)
    result.append(str_len)
    result.extend(designator_bytes)
    result.extend(struct.pack("<I", len(compressed)))
    result.extend(compressed)
