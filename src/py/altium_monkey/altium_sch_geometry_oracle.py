"""
SCH IR payload helpers.

This module owns the Python-side schema constants for the
`x2.sch_onscreen_geometry_oracle.v1` payload used by the AD25 geometry oracle.

Within the Python renderer pipeline, this module is the schema-aligned SCH IR
layer, even though the historical API names still use "geometry".
"""

from __future__ import annotations

import copy
import json
import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, cast

import jsonschema_rs

from ._sch_geometry_ir_schema import SCH_GEOMETRY_IR_SCHEMA_JSON
from .altium_json_apply_helpers import (
    _measure_json_value,
    _strict_json_object,
    _validate_json_tree,
)
from .altium_record_types import color_to_hex


SCH_GEOMETRY_ORACLE_SCHEMA = "x2.sch_onscreen_geometry_oracle.v1"
SCH_GEOMETRY_IR_SCHEMA = SCH_GEOMETRY_ORACLE_SCHEMA
_GEOMETRY_IR_SCHEMA_DOCUMENT = cast(
    "dict[str, jsonschema_rs.JSONType]",
    json.loads(SCH_GEOMETRY_IR_SCHEMA_JSON),
)
_GEOMETRY_IR_VALIDATOR = jsonschema_rs.Draft202012Validator(
    _GEOMETRY_IR_SCHEMA_DOCUMENT
)


@dataclass(frozen=True, slots=True)
class _GeometryJsonLimits:
    max_json_input_bytes: int = 536_870_912
    max_json_recursion_depth: int = 128
    max_total_field_pairs_per_document: int = 64_000_000
    max_generated_string_bytes: int = 268_435_456


_GEOMETRY_JSON_LIMITS = _GeometryJsonLimits()
_MAX_GEOMETRY_RECORDS = 8_000_000
_MAX_GEOMETRY_OPERATIONS = 16_000_000
_MAX_POINTS_PER_OPERATION = 1_000_000
_MAX_TOTAL_POINTS = 16_000_000
_MAX_GEOMETRY_STACK_DEPTH = 1_024
_MAX_IMAGE_COUNT = 1_000_000
_JSON_INTEGER_MIN = -(2**63)
_JSON_INTEGER_MAX = 2**64 - 1
_STACK_OPERATION_DELTAS = {
    "gotPushTransform": ("transform", 1),
    "gotPopTransform": ("transform", -1),
    "gotPushClip": ("clip", 1),
    "gotPopClip": ("clip", -1),
    "gotBeginGroup": ("group", 1),
    "gotEndGroup": ("group", -1),
}


def _validate_geometry_string_bytes(payload: object, limit: int) -> None:
    total = 0
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            total += len(value.encode("utf-8"))
        elif isinstance(value, dict):
            total += sum(len(key.encode("utf-8")) for key in value)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        if total > limit:
            raise ValueError("schematic geometry IR exceeds the string byte limit")


def _validate_geometry_integer_domain(payload: object) -> None:
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, bool) or value is None or isinstance(value, str | float):
            continue
        if isinstance(value, int):
            if not _JSON_INTEGER_MIN <= value <= _JSON_INTEGER_MAX:
                raise ValueError(
                    "schematic geometry IR contains an out-of-range integer"
                )
            continue
        if isinstance(value, dict):
            stack.extend(value.values())
            continue
        if isinstance(value, list):
            stack.extend(value)


def _geometry_operation_point_count(operation: Mapping[str, object]) -> int:
    operation_type = operation["type"]
    if operation_type == "gotLines":
        return len(cast("list[object]", operation["points"]))
    if operation_type != "gotPolygons":
        return 0
    polygons = cast("list[Mapping[str, object]]", operation["polygons"])
    return sum(len(cast("list[object]", polygon["points"])) for polygon in polygons)


def _validate_polygon_indexes(operation: Mapping[str, object], path: str) -> None:
    if operation["type"] != "gotPolygons":
        return
    polygons = cast("list[Mapping[str, object]]", operation["polygons"])
    for index, polygon in enumerate(polygons):
        if polygon["index"] != index:
            raise ValueError(f"{path}.polygons[{index}].index must equal {index}")


def _update_geometry_stack(
    operation_type: object,
    depths: dict[str, int],
    path: str,
) -> None:
    stack_delta = _STACK_OPERATION_DELTAS.get(cast("str", operation_type))
    if stack_delta is None:
        return
    stack_name, delta = stack_delta
    depth = depths[stack_name] + delta
    if depth < 0:
        raise ValueError(f"{path} underflows the {stack_name} stack")
    if depth > _MAX_GEOMETRY_STACK_DEPTH:
        raise ValueError(f"{path} exceeds the {stack_name} stack limit")
    depths[stack_name] = depth


def _validate_geometry_record(
    record: Mapping[str, object],
    record_index: int,
) -> tuple[int, int, int, bool]:
    operations = cast("list[Mapping[str, object]]", record["operations"])
    path = f"records[{record_index}]"
    if record["operation_count"] != len(operations):
        raise ValueError(f"{path}.operation_count must equal the operations length")
    depths = {"transform": 0, "clip": 0, "group": 0}
    point_count = 0
    image_count = 0
    for index, operation in enumerate(operations):
        operation_path = f"{path}.operations[{index}]"
        if operation["index"] != index:
            raise ValueError(f"{operation_path}.index must equal {index}")
        _validate_polygon_indexes(operation, operation_path)
        operation_points = _geometry_operation_point_count(operation)
        if operation_points > _MAX_POINTS_PER_OPERATION:
            raise ValueError(f"{operation_path} exceeds the point limit")
        point_count += operation_points
        image_count += int(operation["type"] == "gotImage")
        _update_geometry_stack(operation["type"], depths, operation_path)
    unclosed = next((name for name, depth in depths.items() if depth), None)
    if unclosed is not None:
        raise ValueError(f"{path} ends with an unclosed {unclosed} stack")
    error = record.get("error")
    return (
        len(operations),
        point_count,
        image_count,
        isinstance(error, str) and bool(error),
    )


def _validate_geometry_semantics(payload: Mapping[str, object]) -> None:
    records = cast("list[Mapping[str, object]]", payload["records"])
    if len(records) > _MAX_GEOMETRY_RECORDS:
        raise ValueError("geometry IR exceeds the record limit")
    total_operations = 0
    total_points = 0
    image_count = 0
    failed_renders = 0
    for index, record in enumerate(records):
        operation_count, point_count, record_images, failed = _validate_geometry_record(
            record, index
        )
        total_operations += operation_count
        total_points += point_count
        image_count += record_images
        failed_renders += int(failed)
        if total_operations > _MAX_GEOMETRY_OPERATIONS:
            raise ValueError("geometry IR exceeds the operation limit")
        if total_points > _MAX_TOTAL_POINTS:
            raise ValueError("geometry IR exceeds the total point limit")
        if image_count > _MAX_IMAGE_COUNT:
            raise ValueError("geometry IR exceeds the image limit")
    if payload["total_operations"] != total_operations:
        raise ValueError("total_operations must equal the sum of record operations")
    if payload["failed_renders"] != failed_renders:
        raise ValueError("failed_renders must equal records with nonempty errors")


def validate_sch_geometry_ir_payload(payload: object) -> None:
    """Reject values outside the strict schematic geometry-IR v1 contract."""
    if not isinstance(payload, dict):
        raise ValueError("schematic geometry IR payload must be an object")
    try:
        _validate_json_tree(payload, limits=_GEOMETRY_JSON_LIMITS)
        _validate_geometry_integer_domain(payload)
        _validate_geometry_string_bytes(
            payload, _GEOMETRY_JSON_LIMITS.max_generated_string_bytes
        )
        _measure_json_value(payload, _GEOMETRY_JSON_LIMITS.max_json_input_bytes)
    except RecursionError as error:
        raise ValueError(
            "schematic geometry IR payload is too deeply nested"
        ) from error
    schema = payload.get("schema")
    if schema != SCH_GEOMETRY_IR_SCHEMA:
        raise ValueError(
            f"schematic geometry IR schema must be {SCH_GEOMETRY_IR_SCHEMA!r}, "
            f"got {schema!r}"
        )
    validation_error = next(
        iter(
            _GEOMETRY_IR_VALIDATOR.iter_errors(cast("jsonschema_rs.JSONType", payload))
        ),
        None,
    )
    if validation_error is not None:
        raise ValueError(
            f"schematic geometry IR payload is invalid: {validation_error}"
        )
    _validate_geometry_semantics(cast("Mapping[str, object]", payload))


def _load_geometry_json_file(path: str | Path) -> dict[str, object]:
    geometry_path = Path(path)
    declared_size = geometry_path.stat().st_size
    if declared_size > _GEOMETRY_JSON_LIMITS.max_json_input_bytes:
        raise ValueError("schematic geometry IR JSON exceeds the byte limit")
    with geometry_path.open("rb") as handle:
        payload = handle.read(_GEOMETRY_JSON_LIMITS.max_json_input_bytes + 1)
    if len(payload) > _GEOMETRY_JSON_LIMITS.max_json_input_bytes:
        raise ValueError("schematic geometry IR JSON exceeds the byte limit")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("schematic geometry IR JSON is not valid UTF-8") from error
    try:
        data: object = json.loads(text, object_pairs_hook=_strict_json_object)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid geometry IR JSON at line {error.lineno} column {error.colno}"
        ) from error
    except RecursionError as error:
        raise ValueError("schematic geometry IR JSON is too deeply nested") from error
    _validate_json_tree(data, limits=_GEOMETRY_JSON_LIMITS)
    if not isinstance(data, dict):
        raise ValueError("schematic geometry IR JSON root must be an object")
    return cast("dict[str, object]", data)


def _render_group_ids_for_sources(
    source_objects: Sequence[object],
) -> dict[int, str]:
    occupied = {
        str(getattr(source_object, "unique_id", "") or "")
        for source_object in source_objects
        if str(getattr(source_object, "unique_id", "") or "")
    }
    result: dict[int, str] = {}
    for position, source_object in enumerate(source_objects):
        if str(getattr(source_object, "unique_id", "") or ""):
            continue
        candidate = f"AMUID{position:08X}"
        suffix = 0
        while candidate in occupied:
            suffix += 1
            candidate = f"AMUID{position:08X}_{suffix}"
        occupied.add(candidate)
        result[id(source_object)] = candidate
    return result


def _source_render_group_identity(source_object: object, group_id: str) -> str:
    try:
        record_type = int(getattr(source_object, "record_type"))
    except (AttributeError, TypeError, ValueError):
        source_type = type(source_object)
        owner = f"{source_type.__module__}.{source_type.__qualname__}"
    else:
        owner = f"record:{record_type}"
    return f"{owner}\0{group_id}"


def _with_private_record_group_identity(
    record: "SchGeometryRecord",
    *,
    render_group_id: str,
    render_group_identity: str,
    render_source_id: int,
) -> "SchGeometryRecord":
    operations = list(record.operations)
    if (
        len(operations) >= 4
        and operations[3].kind_str() == SchGeometryOpKind.BEGIN_GROUP.value
        and operations[3].payload.get("parameters") == str(record.unique_id)
    ):
        operations[3] = replace(
            operations[3],
            render_group_id=render_group_id,
            render_group_identity=render_group_identity,
            render_source_id=render_source_id,
        )
    return replace(
        record,
        operations=operations,
        render_group_id=render_group_id,
        render_group_identity=render_group_identity,
        render_source_id=render_source_id,
    )


class SchIrRenderProfile(str, Enum):
    """
    High-level IR intent profiles.
    """

    ORACLE = "oracle"
    ONSCREEN = "onscreen"


def normalize_sch_ir_render_profile(
    profile: SchIrRenderProfile | str,
) -> SchIrRenderProfile:
    if isinstance(profile, SchIrRenderProfile):
        return profile
    normalized = str(profile or "").strip().lower()
    if normalized == SchIrRenderProfile.ORACLE.value:
        return SchIrRenderProfile.ORACLE
    if normalized == SchIrRenderProfile.ONSCREEN.value:
        return SchIrRenderProfile.ONSCREEN
    raise ValueError(f"Unknown schematic IR render profile: {profile!r}")


class SchGeometryOpKind(str, Enum):
    """
    Oracle-aligned schematic geometry operation kinds.
    """

    STRING = "gotString"
    LINES = "gotLines"
    ARC = "gotArc"
    ELLIPSE = "gotEllipse"
    ROUNDED_RECTANGLE = "gotRoundedRectangle"
    PUSH_TRANSFORM = "gotPushTransform"
    POP_TRANSFORM = "gotPopTransform"
    PUSH_CLIP = "gotPushClip"
    POP_CLIP = "gotPopClip"
    BEGIN_GROUP = "gotBeginGroup"
    END_GROUP = "gotEndGroup"
    IMAGE = "gotImage"
    POLYGONS = "gotPolygons"


def _signed_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _oracle_color_raw_from_win32(color_raw: int, *, alpha: int = 0xFF) -> int:
    """
    Convert parsed schematic Win32 color (0x00BBGGRR) to GeometryMaker color raw.

    The geometry oracle serializes colors as signed 32-bit integers whose low
    24 bits match the rendered RGB hex form.
    """
    rgb_hex = color_to_hex(int(color_raw))[1:]
    alpha_byte = max(0, min(255, int(alpha)))
    return _signed_int32((alpha_byte << 24) | int(rgb_hex, 16))


def _oracle_color_hex(color_raw: int) -> str:
    return f"#{(int(color_raw) & 0xFFFFFF):06X}"


def _geometry_item_f32(value: int | float) -> float:
    """Store one transformed GeometryMaker item field as a managed Single."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _narrow_pen_geometry(pen: dict[str, object]) -> dict[str, object]:
    narrowed = copy.deepcopy(pen)
    if "width" in narrowed:
        narrowed["width"] = _geometry_item_f32(cast("int | float", narrowed["width"]))
    if "dash_values" in narrowed:
        narrowed["dash_values"] = [
            _geometry_item_f32(value)
            for value in cast("list[int | float]", narrowed["dash_values"])
        ]
    return narrowed


def _narrow_font_geometry(font: dict[str, object]) -> dict[str, object]:
    narrowed = copy.deepcopy(font)
    if "size" in narrowed:
        narrowed["size"] = _geometry_item_f32(cast("int | float", narrowed["size"]))
    if "rotation" in narrowed:
        narrowed["rotation"] = _geometry_item_f32(
            cast("int | float", narrowed["rotation"])
        )
    return narrowed


def _geometry_units_scale(units_per_px: int) -> float:
    return _geometry_item_f32(float(units_per_px) / 100_000.0) * 100_000.0


def _geometry_item_length(value_px: int | float, *, units_per_px: int) -> float:
    return _geometry_item_f32(float(value_px) * _geometry_units_scale(units_per_px))


def _geometry_item_point(
    x: float, y: float, *, units_per_px: int
) -> tuple[float, float]:
    scale_correction = _geometry_units_scale(units_per_px) / float(units_per_px)
    return (
        _geometry_item_f32(float(x) * scale_correction),
        _geometry_item_f32(float(y) * scale_correction),
    )


def _geometry_item_arc_angles(
    start_angle: float,
    end_angle: float,
    *,
    rotation: int | float = 0,
    mirror_x: bool = False,
) -> tuple[float, float]:
    """Apply Scale/Rotate/InvertY arc-angle transforms in managed stack order."""

    def normalize(angle: float) -> float:
        if 0.0 <= angle <= 360.0:
            return angle
        remainder = angle % 360.0
        return 360.0 if remainder == 0.0 and angle > 0.0 else remainder

    transformed_start = float(start_angle)
    transformed_end = float(end_angle)
    if mirror_x and abs(transformed_end - transformed_start - 360.0) > (
        0.009999999776482582
    ):
        mirrored_start = normalize(-transformed_start + 180.0)
        mirrored_end = normalize(-transformed_end + 180.0)
        transformed_start, transformed_end = mirrored_end, mirrored_start
    transformed_start += float(rotation)
    transformed_end += float(rotation)
    return -transformed_start, -transformed_end


def _rounded_rectangle_axis(first: float, second: float) -> tuple[float, float, float]:
    center = _geometry_item_f32((float(first) + float(second)) / 2.0)
    half_extent = _geometry_item_f32(abs(float(second) - float(first)) / 2.0)
    return (
        _geometry_item_f32(center - half_extent),
        _geometry_item_f32(center + half_extent),
        half_extent,
    )


def make_rounded_rectangle_operation(
    *,
    x1_px: float,
    y1_px: float,
    x2_px: float,
    y2_px: float,
    sheet_height_px: float,
    units_per_px: int,
    source_rotation: int | float = 0,
    corner_x_radius_px: float = 0.0,
    corner_y_radius_px: float = 0.0,
    brush: dict[str, object] | None = None,
    pen: dict[str, object] | None = None,
) -> "SchGeometryOp":
    """Build the draw operation from managed rounded-item source fields."""
    center_x_px = (float(x1_px) + float(x2_px)) / 2.0
    center_y_px = (float(y1_px) + float(y2_px)) / 2.0
    half_width_px = abs(float(x2_px) - float(x1_px)) / 2.0
    half_height_px = abs(float(y2_px) - float(y1_px)) / 2.0
    if int(source_rotation) % 180:
        half_width_px, half_height_px = half_height_px, half_width_px
    center_x, center_y = svg_coord_to_geometry(
        center_x_px,
        center_y_px,
        sheet_height_px=sheet_height_px,
        units_per_px=units_per_px,
    )
    return SchGeometryOp.rounded_rectangle_from_item(
        center_x=center_x,
        center_y=center_y,
        half_width=_geometry_item_length(half_width_px, units_per_px=units_per_px),
        half_height=_geometry_item_length(half_height_px, units_per_px=units_per_px),
        corner_x_radius=_geometry_item_length(
            min(half_width_px, abs(float(corner_x_radius_px))),
            units_per_px=units_per_px,
        ),
        corner_y_radius=_geometry_item_length(
            min(half_height_px, abs(float(corner_y_radius_px))),
            units_per_px=units_per_px,
        ),
        brush=brush,
        pen=pen,
    )


def _geometry_clip_can_draw(x1: float, y1: float, x2: float, y2: float) -> bool:
    left, right = sorted((_geometry_item_f32(x1), _geometry_item_f32(x2)))
    top, bottom = sorted((_geometry_item_f32(y1), _geometry_item_f32(y2)))
    return abs(right - left) > 1.0 and abs(bottom - top) > 1.0


_GeometryRect = tuple[float, float, float, float]


def _geometry_item_rect(x1: float, y1: float, x2: float, y2: float) -> _GeometryRect:
    left, right = sorted((_geometry_item_f32(x1), _geometry_item_f32(x2)))
    top, bottom = sorted((_geometry_item_f32(y1), _geometry_item_f32(y2)))
    return left, top, right, bottom


def _geometry_rects_intersect(first: _GeometryRect, second: _GeometryRect) -> bool:
    return not (
        first[0] > second[2]
        or second[0] > first[2]
        or first[1] > second[3]
        or second[1] > first[3]
    )


def _geometry_rect_includes(large: _GeometryRect, small: _GeometryRect) -> bool:
    return (
        large[0] <= small[0]
        and small[2] <= large[2]
        and large[1] <= small[1]
        and small[3] <= large[3]
    )


def _geometry_f32_ieee_divide(numerator: float, denominator: float) -> float:
    numerator = _geometry_item_f32(numerator)
    denominator = _geometry_item_f32(denominator)
    if denominator != 0.0:
        return _geometry_item_f32(numerator / denominator)
    if numerator == 0.0:
        return math.nan
    sign = math.copysign(1.0, numerator) * math.copysign(1.0, denominator)
    return math.copysign(math.inf, sign)


def _geometry_image_crop(
    source: _GeometryRect,
    dest: _GeometryRect,
    target: _GeometryRect | None,
) -> tuple[_GeometryRect, _GeometryRect] | None:
    """Apply ImageGeometryCoords.TryGet's float32 viewport crop."""
    source = _geometry_item_rect(*source)
    dest = _geometry_item_rect(*dest)
    if target is None:
        return source, dest
    target = _geometry_item_rect(*target)
    if not _geometry_rects_intersect(target, dest):
        return None
    if _geometry_rect_includes(target, dest):
        return source, dest

    intersection = (
        max(target[0], dest[0]),
        max(target[1], dest[1]),
        min(target[2], dest[2]),
        min(target[3], dest[3]),
    )
    dest_width = _geometry_item_f32(dest[2] - dest[0])
    dest_height = _geometry_item_f32(dest[1] - dest[3])
    if dest_width == 0.0 or dest_height == 0.0:
        return None
    scale_x = _geometry_f32_ieee_divide(
        _geometry_item_f32(source[2] - source[0]),
        dest_width,
    )
    scale_y = _geometry_f32_ieee_divide(
        _geometry_item_f32(source[1] - source[3]),
        dest_height,
    )
    left_crop = _geometry_item_f32(
        _geometry_item_f32(dest[0] - intersection[0]) * scale_x
    )
    right_crop = _geometry_item_f32(
        _geometry_item_f32(dest[2] - intersection[2]) * scale_x
    )
    top_crop = _geometry_item_f32(
        _geometry_item_f32(dest[1] - intersection[1]) * scale_y
    )
    bottom_crop = _geometry_item_f32(
        _geometry_item_f32(dest[3] - intersection[3]) * scale_y
    )
    cropped_source = (
        _geometry_item_f32(source[0] - left_crop),
        _geometry_item_f32(source[1] - top_crop),
        _geometry_item_f32(source[2] - right_crop),
        _geometry_item_f32(source[3] - bottom_crop),
    )
    cropped_dest: _GeometryRect = (
        _geometry_item_f32(intersection[0]),
        _geometry_item_f32(intersection[1]),
        _geometry_item_f32(intersection[2]),
        _geometry_item_f32(intersection[3]),
    )
    return cropped_source, cropped_dest


def _geometry_rotation_matrix(
    rotation: float, origin_x: float, origin_y: float
) -> tuple[float, float, float, float, float, float]:
    rotation_single = _geometry_item_f32(rotation)
    radians = _geometry_item_f32(
        _geometry_item_f32(rotation_single / _geometry_item_f32(180.0))
        * _geometry_item_f32(math.pi)
    )
    radians = _geometry_item_f32(math.remainder(radians, _geometry_item_f32(math.tau)))
    rotation_epsilon = _geometry_item_f32(1.7453292e-5)
    half_pi = _geometry_item_f32(math.pi) / _geometry_item_f32(2.0)
    pi = _geometry_item_f32(math.pi)
    if -rotation_epsilon < radians < rotation_epsilon:
        cosine, sine = 1.0, 0.0
    elif half_pi - rotation_epsilon < radians < half_pi + rotation_epsilon:
        cosine, sine = 0.0, 1.0
    elif radians < -pi + rotation_epsilon or radians > pi - rotation_epsilon:
        cosine, sine = -1.0, 0.0
    elif -half_pi - rotation_epsilon < radians < -half_pi + rotation_epsilon:
        cosine, sine = 0.0, -1.0
    else:
        cosine = _geometry_item_f32(math.cos(radians))
        sine = _geometry_item_f32(math.sin(radians))
    origin_x = _geometry_item_f32(origin_x)
    origin_y = _geometry_item_f32(origin_y)
    one_minus_cosine = _geometry_item_f32(1.0 - cosine)
    translate_x = _geometry_item_f32(
        _geometry_item_f32(origin_x * one_minus_cosine)
        + _geometry_item_f32(origin_y * sine)
    )
    translate_y = _geometry_item_f32(
        _geometry_item_f32(origin_y * one_minus_cosine)
        - _geometry_item_f32(origin_x * sine)
    )
    return cosine, sine, -sine, cosine, translate_x, translate_y


def _geometry_inverse_transform_bounds(
    matrix: tuple[float, float, float, float, float, float],
    rect: _GeometryRect,
) -> _GeometryRect:
    m11, m12, m21, m22, m31, m32 = matrix
    determinant = m11 * m22 - m12 * m21
    if determinant == 0.0:
        return rect
    inverse = (
        m22 / determinant,
        -m12 / determinant,
        -m21 / determinant,
        m11 / determinant,
        (m21 * m32 - m31 * m22) / determinant,
        (m31 * m12 - m11 * m32) / determinant,
    )

    def transform(x: float, y: float) -> tuple[float, float]:
        return (
            x * inverse[0] + y * inverse[2] + inverse[4],
            x * inverse[1] + y * inverse[3] + inverse[5],
        )

    points = [
        transform(rect[0], rect[1]),
        transform(rect[2], rect[1]),
        transform(rect[0], rect[3]),
        transform(rect[2], rect[3]),
    ]
    return _geometry_item_rect(
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _make_image_operations(
    *,
    source: _GeometryRect,
    dest: _GeometryRect,
    alpha: float,
    draw_rect: _GeometryRect | None = None,
    rotation: float = 0.0,
) -> list["SchGeometryOp"]:
    """Export one ImageGeometryItem using its crop and rotation draw path."""
    cropped = _geometry_image_crop(source, dest, draw_rect)
    if cropped is None:
        return []
    cropped_source, cropped_dest = cropped
    if abs(float(rotation)) < 0.01:
        return [
            SchGeometryOp.image(
                dest_x1=cropped_dest[0],
                dest_y1=cropped_dest[1],
                dest_x2=cropped_dest[2],
                dest_y2=cropped_dest[3],
                source_x1=cropped_source[0],
                source_y1=cropped_source[1],
                source_x2=cropped_source[2],
                source_y2=cropped_source[3],
                alpha=alpha,
            )
        ]
    matrix = _geometry_rotation_matrix(rotation, cropped_dest[0], cropped_dest[1])
    image_dest = _geometry_inverse_transform_bounds(matrix, cropped_dest)
    return [
        SchGeometryOp.push_transform(matrix),
        SchGeometryOp.image(
            dest_x1=image_dest[0],
            dest_y1=image_dest[1],
            dest_x2=image_dest[2],
            dest_y2=image_dest[3],
            source_x1=cropped_source[0],
            source_y1=cropped_source[1],
            source_x2=cropped_source[2],
            source_y2=cropped_source[3],
            alpha=alpha,
        ),
        SchGeometryOp.pop_transform(),
    ]


def make_solid_brush(color_raw: int, *, alpha: int = 0xFF) -> dict[str, Any]:
    """
    Create a solid brush payload from a parsed schematic Win32 color.
    """
    geometry_color_raw = _oracle_color_raw_from_win32(color_raw, alpha=alpha)
    return {
        "brush_type": "gbtSolid",
        "color_raw": geometry_color_raw,
        "color_hex": _oracle_color_hex(geometry_color_raw),
        "color_to_raw": 0,
        "color_to_hex": "#000000",
        "from_x": 0,
        "from_y": 0,
        "to_x": 0,
        "to_y": 0,
        "pattern_width": 0,
        "pattern_height": 0,
    }


def make_pen(
    color_raw: int,
    *,
    alpha: int = 0xFF,
    width: float = 0,
    min_width: int = 1,
    line_join: str = "pljRound",
    dash_style: str = "pdsSolid",
    dash_values: list[float] | None = None,
) -> dict[str, Any]:
    """
    Create a pen payload from a parsed schematic Win32 color.
    """
    geometry_color_raw = _oracle_color_raw_from_win32(color_raw, alpha=alpha)
    width_value = _geometry_item_f32(width)
    if abs(width_value - round(width_value)) <= 1e-9:
        width_payload: int | float = int(round(width_value))
    else:
        width_payload = width_value

    return {
        "color_raw": geometry_color_raw,
        "color_hex": _oracle_color_hex(geometry_color_raw),
        "width": width_payload,
        "min_width": int(min_width),
        "line_join": str(line_join),
        "dash_style": str(dash_style),
        "dash_values": [_geometry_item_f32(value) for value in dash_values or []],
    }


def make_font_payload(
    *,
    name: str,
    size_px: float,
    units_per_px: int = 64,
    rotation: float = 0.0,
    underline: bool = False,
    italic: bool = False,
    bold: bool = False,
    strikeout: bool = False,
) -> dict[str, Any]:
    """
    Create an oracle-aligned font payload from screen-space font metrics.
    """
    return {
        "name": str(name),
        "size": _geometry_item_f32(
            float(size_px) * _geometry_units_scale(units_per_px)
        ),
        "rotation": _geometry_item_f32(rotation),
        "underline": bool(underline),
        "italic": bool(italic),
        "bold": bool(bold),
        "strikeout": bool(strikeout),
    }


def wrap_record_operations(
    unique_id: str | None,
    operations: list["SchGeometryOp"],
    *,
    units_per_px: int = 64,
    workspace_height_px: int = 1000,
    render_group_id: str | None = None,
    render_group_identity: str | None = None,
    render_source_id: int | None = None,
) -> list["SchGeometryOp"]:
    """
    Wrap record-local operations in the standard GeometryMaker group envelope.
    """
    return [
        SchGeometryOp.push_transform(
            [1, 0, 0, 1, 0, -(units_per_px * workspace_height_px)]
        ),
        SchGeometryOp.begin_group(),
        SchGeometryOp.begin_group("DocumentMainGroup"),
        SchGeometryOp.begin_group(
            unique_id,
            render_group_id=render_group_id,
            render_group_identity=render_group_identity,
            render_source_id=render_source_id,
        ),
        *operations,
        SchGeometryOp.end_group(),
        SchGeometryOp.end_group(),
        SchGeometryOp.end_group(),
        SchGeometryOp.pop_transform(),
    ]


def unwrap_record_operations(
    record_or_operations: "SchGeometryRecord | list[SchGeometryOp]",
    *,
    unique_id: str | None = None,
) -> list["SchGeometryOp"]:
    """
    Return record-local operations from a wrapped geometry record or raw ops list.
    """
    if isinstance(record_or_operations, list):
        return record_or_operations

    operations = list(getattr(record_or_operations, "operations", []) or [])
    if len(operations) < 8:
        return operations

    if (
        operations[0].kind_str() == SchGeometryOpKind.PUSH_TRANSFORM.value
        and operations[1].kind_str() == SchGeometryOpKind.BEGIN_GROUP.value
        and operations[2].kind_str() == SchGeometryOpKind.BEGIN_GROUP.value
        and operations[2].payload.get("parameters") == "DocumentMainGroup"
        and operations[-4].kind_str() == SchGeometryOpKind.END_GROUP.value
        and operations[-3].kind_str() == SchGeometryOpKind.END_GROUP.value
        and operations[-2].kind_str() == SchGeometryOpKind.END_GROUP.value
        and operations[-1].kind_str() == SchGeometryOpKind.POP_TRANSFORM.value
    ):
        wrapped_id = operations[3].payload.get("parameters")
        if unique_id is None or wrapped_id == unique_id:
            return operations[4:-4]

    return operations


def svg_coord_to_geometry(
    x_px: float,
    y_px: float,
    *,
    sheet_height_px: float,
    units_per_px: int = 64,
    workspace_height_px: int = 1000,
) -> tuple[float, float]:
    """
    Convert a screen-space SVG coordinate to stored GeometryItem units.
    """
    return _geometry_item_point(
        *_svg_coord_to_geometry_exact(
            x_px,
            y_px,
            sheet_height_px=sheet_height_px,
            units_per_px=units_per_px,
            workspace_height_px=workspace_height_px,
        ),
        units_per_px=units_per_px,
    )


def _svg_coord_to_geometry_exact(
    x_px: float,
    y_px: float,
    *,
    sheet_height_px: float,
    units_per_px: int = 64,
    workspace_height_px: int = 1000,
) -> tuple[float, float]:
    """Convert an SVG coordinate before managed zoom and item storage."""
    x_units = float(x_px) * int(units_per_px)
    y_units = (float(y_px) - float(sheet_height_px)) * int(units_per_px) + int(
        units_per_px
    ) * int(workspace_height_px)
    return (x_units, y_units)


def _apply_single_slash_negation(text: str, *, enabled: bool) -> str:
    """Expand a leading slash to managed whole-string negation markers."""
    if not enabled or not text.startswith("\\"):
        return text
    clean_units = [unit for unit in _utf16_code_units(text) if unit != ord("\\")]
    expanded = [item for unit in clean_units for item in (unit, ord("\\"))]
    return _text_from_utf16_code_units(expanded)


def _utf16_code_units(text: str) -> list[int]:
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    return [
        int.from_bytes(encoded[index : index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]


def _text_from_utf16_code_units(code_units: Sequence[int]) -> str:
    encoded = b"".join(code_unit.to_bytes(2, "little") for code_unit in code_units)
    return encoded.decode("utf-16-le", errors="surrogatepass")


def split_overline_text(
    text: str, *, single_slash_negation: bool = False
) -> tuple[str, list[int]]:
    """
    Return clean text plus character indexes that carry an overline.
    """
    clean_text, segments = _split_overline_text_details(
        text, single_slash_negation=single_slash_negation
    )
    return clean_text, [index for index, _include_rsb in segments]


def _split_overline_text_details(
    text: str, *, single_slash_negation: bool = False
) -> tuple[str, list[tuple[int, bool]]]:
    """Retain managed per-marker right-side-bearing measurement state."""
    text = _apply_single_slash_negation(text, enabled=single_slash_negation)
    clean_units: list[int] = []
    overline_segments: list[tuple[int, bool]] = []
    source_units = _utf16_code_units(text)

    for source_index, code_unit in enumerate(source_units):
        if code_unit == ord("\\"):
            if clean_units:
                overline_segments.append(
                    (len(clean_units) - 1, source_index < len(source_units) - 1)
                )
            continue
        clean_units.append(code_unit)

    return (_text_from_utf16_code_units(clean_units), overline_segments)


def make_text_with_overline_operations(
    *,
    text: str,
    baseline_x_px: float,
    baseline_y_px: float,
    sheet_height_px: float,
    font_payload: dict[str, Any],
    font_size_px: float,
    font_name: str,
    bold: bool = False,
    italic: bool = False,
    brush_color_raw: int = 0,
    pen_color_raw: int | None = None,
    rotation_deg: float = 0.0,
    units_per_px: int = 64,
    geometry_step_px: float | None = None,
    overline_pen_width: float | None = None,
    single_slash_negation: bool = False,
) -> list["SchGeometryOp"]:
    """
    Build oracle-aligned text operations, including individual overline segments.

    GeometryMaker stores text coordinates offset from the rendered baseline by the
    truncated baseline font size. Overline segments live on that same geometry Y.
    """
    from .altium_text_metrics import measure_text_width

    clean_text, overline_segments = _split_overline_text_details(
        text, single_slash_negation=single_slash_negation
    )
    if not clean_text:
        return []

    baseline_font_size = float(int(font_size_px))
    geometry_step = (
        float(geometry_step_px) if geometry_step_px is not None else baseline_font_size
    )
    theta = 0.017453292519943295 * float(rotation_deg)
    geometry_x_px = float(baseline_x_px) + math.sin(theta) * geometry_step
    geometry_y_px = float(baseline_y_px) - math.cos(theta) * geometry_step
    geometry_x, geometry_y = svg_coord_to_geometry(
        geometry_x_px,
        geometry_y_px,
        sheet_height_px=sheet_height_px,
        units_per_px=units_per_px,
    )
    operations: list[SchGeometryOp] = []
    if overline_segments:
        pen = make_pen(
            brush_color_raw if pen_color_raw is None else pen_color_raw,
            width=(
                _geometry_item_length(1.0, units_per_px=units_per_px)
                if overline_pen_width is None
                else overline_pen_width
            ),
        )
        cos_theta = math.cos(theta)
        sin_theta = math.sin(theta)
        clean_units = _utf16_code_units(clean_text)
        for char_idx, include_rsb in overline_segments:
            text_before = _text_from_utf16_code_units(clean_units[:char_idx])
            text_including = _text_from_utf16_code_units(clean_units[: char_idx + 1])
            width_before = (
                measure_text_width(
                    text_before,
                    font_size_px,
                    font_name,
                    bold=bold,
                    italic=italic,
                    include_rsb=True,
                )
                if text_before
                else 0.0
            )
            width_including = measure_text_width(
                text_including,
                font_size_px,
                font_name,
                bold=bold,
                italic=italic,
                include_rsb=include_rsb,
            )
            line_start = svg_coord_to_geometry(
                geometry_x_px + cos_theta * width_including,
                geometry_y_px + sin_theta * width_including,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            line_end = svg_coord_to_geometry(
                geometry_x_px + cos_theta * width_before,
                geometry_y_px + sin_theta * width_before,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            operations.append(SchGeometryOp.lines([line_start, line_end], pen=pen))

    operations.append(
        SchGeometryOp.string(
            x=geometry_x,
            y=geometry_y,
            text=clean_text,
            font=font_payload,
            brush=make_solid_brush(brush_color_raw),
        )
    )
    return operations


@dataclass(frozen=True)
class SchGeometryBounds:
    """
    Record bounds block from the geometry oracle payload.
    """

    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SchGeometryBounds | None:
        if not isinstance(data, dict):
            return None
        return cls(
            left=int(data.get("Left", 0)),
            top=int(data.get("Top", 0)),
            right=int(data.get("Right", 0)),
            bottom=int(data.get("Bottom", 0)),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "Left": int(self.left),
            "Top": int(self.top),
            "Right": int(self.right),
            "Bottom": int(self.bottom),
        }


@dataclass(frozen=True)
class SchGeometryOp:
    """
    One schematic geometry operation in oracle-aligned form.
    """

    kind: SchGeometryOpKind | str
    payload: dict[str, Any] = field(default_factory=dict)
    render_group_id: str | None = field(default=None, repr=False, compare=False)
    render_group_identity: str | None = field(default=None, repr=False, compare=False)
    render_source_id: int | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchGeometryOp:
        payload = copy.deepcopy(data)
        kind = str(payload.pop("type", "")).strip()
        payload.pop("index", None)
        return cls(kind=kind, payload=payload)

    @classmethod
    def push_transform(cls, matrix: list[float] | tuple[float, ...]) -> SchGeometryOp:
        return cls(
            kind=SchGeometryOpKind.PUSH_TRANSFORM,
            payload={"matrix": [_geometry_item_f32(value) for value in matrix]},
        )

    @classmethod
    def pop_transform(cls) -> SchGeometryOp:
        return cls(kind=SchGeometryOpKind.POP_TRANSFORM)

    @classmethod
    def begin_group(
        cls,
        parameters: str | None = "",
        *,
        render_group_id: str | None = None,
        render_group_identity: str | None = None,
        render_source_id: int | None = None,
    ) -> SchGeometryOp:
        return cls(
            kind=SchGeometryOpKind.BEGIN_GROUP,
            payload={"parameters": str(parameters)},
            render_group_id=render_group_id,
            render_group_identity=render_group_identity,
            render_source_id=render_source_id,
        )

    @classmethod
    def end_group(cls) -> SchGeometryOp:
        return cls(kind=SchGeometryOpKind.END_GROUP)

    @classmethod
    def push_clip(
        cls,
        *,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> "SchGeometryOp":
        left, right = sorted((float(x1), float(x2)))
        top, bottom = sorted((float(y1), float(y2)))
        return cls(
            kind=SchGeometryOpKind.PUSH_CLIP,
            payload={
                "x1": _geometry_item_f32(left),
                "y1": _geometry_item_f32(top),
                "x2": _geometry_item_f32(right),
                "y2": _geometry_item_f32(bottom),
            },
        )

    @classmethod
    def pop_clip(cls) -> "SchGeometryOp":
        return cls(kind=SchGeometryOpKind.POP_CLIP)

    @classmethod
    def rounded_rectangle(
        cls,
        *,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        corner_x_radius: float = 0,
        corner_y_radius: float = 0,
        brush: dict[str, Any] | None = None,
        pen: dict[str, Any] | None = None,
    ) -> SchGeometryOp:
        left, right, half_width = _rounded_rectangle_axis(x1, x2)
        top, bottom, half_height = _rounded_rectangle_axis(y1, y2)
        radius_x = _geometry_item_f32(min(abs(corner_x_radius), half_width))
        radius_y = _geometry_item_f32(min(abs(corner_y_radius), half_height))
        if brush is not None and pen is None:
            radius_y = radius_x
        payload: dict[str, Any] = {
            "x1": left,
            "y1": top,
            "x2": right,
            "y2": bottom,
            "corner_x_radius": radius_x,
            "corner_y_radius": radius_y,
        }
        if brush is not None:
            payload["brush"] = copy.deepcopy(brush)
        if pen is not None:
            payload["pen"] = _narrow_pen_geometry(pen)
        return cls(kind=SchGeometryOpKind.ROUNDED_RECTANGLE, payload=payload)

    @classmethod
    def rounded_rectangle_from_item(
        cls,
        *,
        center_x: float,
        center_y: float,
        half_width: float,
        half_height: float,
        corner_x_radius: float = 0,
        corner_y_radius: float = 0,
        brush: dict[str, object] | None = None,
        pen: dict[str, object] | None = None,
    ) -> SchGeometryOp:
        stored_center_x = _geometry_item_f32(center_x)
        stored_center_y = _geometry_item_f32(center_y)
        stored_half_width = _geometry_item_f32(abs(half_width))
        stored_half_height = _geometry_item_f32(abs(half_height))
        radius_x = _geometry_item_f32(abs(corner_x_radius))
        radius_y = _geometry_item_f32(abs(corner_y_radius))
        if brush is not None and pen is None:
            radius_y = radius_x
        payload: dict[str, Any] = {
            "x1": _geometry_item_f32(stored_center_x - stored_half_width),
            "y1": _geometry_item_f32(stored_center_y - stored_half_height),
            "x2": _geometry_item_f32(stored_center_x + stored_half_width),
            "y2": _geometry_item_f32(stored_center_y + stored_half_height),
            "corner_x_radius": radius_x,
            "corner_y_radius": radius_y,
        }
        if brush is not None:
            payload["brush"] = copy.deepcopy(brush)
        if pen is not None:
            payload["pen"] = _narrow_pen_geometry(pen)
        return cls(kind=SchGeometryOpKind.ROUNDED_RECTANGLE, payload=payload)

    @classmethod
    def lines(
        cls,
        points: Sequence[Sequence[float]],
        *,
        pen: dict[str, Any] | None = None,
    ) -> SchGeometryOp:
        payload: dict[str, Any] = {
            "points": [
                [_geometry_item_f32(point[0]), _geometry_item_f32(point[1])]
                for point in points
            ],
        }
        if pen is not None:
            payload["pen"] = _narrow_pen_geometry(pen)
        return cls(kind=SchGeometryOpKind.LINES, payload=payload)

    @classmethod
    def string(
        cls,
        *,
        x: float,
        y: float,
        text: str,
        font: dict[str, Any],
        brush: dict[str, Any] | None = None,
    ) -> SchGeometryOp:
        payload: dict[str, Any] = {
            "x": _geometry_item_f32(x),
            "y": _geometry_item_f32(y),
            "text": str(text),
            "font": _narrow_font_geometry(font),
        }
        if brush is not None:
            payload["brush"] = copy.deepcopy(brush)
        return cls(kind=SchGeometryOpKind.STRING, payload=payload)

    @classmethod
    def arc(
        cls,
        *,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        start_angle: float,
        end_angle: float,
        pen: dict[str, Any] | None = None,
    ) -> "SchGeometryOp":
        payload: dict[str, Any] = {
            "center_x": _geometry_item_f32(center_x),
            "center_y": _geometry_item_f32(center_y),
            "width": _geometry_item_f32(width),
            "height": _geometry_item_f32(height),
            "start_angle": _geometry_item_f32(start_angle),
            "end_angle": _geometry_item_f32(end_angle),
        }
        if pen is not None:
            payload["pen"] = _narrow_pen_geometry(pen)
        return cls(kind=SchGeometryOpKind.ARC, payload=payload)

    @classmethod
    def polygons(
        cls,
        polygons: Sequence[Sequence[Sequence[float]]],
        *,
        brush: dict[str, Any] | None = None,
        pen: dict[str, Any] | None = None,
    ) -> "SchGeometryOp":
        payload: dict[str, Any] = {
            "polygons": [
                {
                    "index": index,
                    "points": [
                        [
                            _geometry_item_f32(point[0]),
                            _geometry_item_f32(point[1]),
                        ]
                        for point in polygon
                    ],
                }
                for index, polygon in enumerate(polygons)
            ]
        }
        if brush is not None:
            payload["brush"] = copy.deepcopy(brush)
        if pen is not None:
            payload["pen"] = _narrow_pen_geometry(pen)
        return cls(kind=SchGeometryOpKind.POLYGONS, payload=payload)

    @classmethod
    def image(
        cls,
        *,
        dest_x1: float,
        dest_y1: float,
        dest_x2: float,
        dest_y2: float,
        source_x1: float = 0,
        source_y1: float = 0,
        source_x2: float,
        source_y2: float,
        alpha: float = 1.0,
    ) -> "SchGeometryOp":
        dest_left, dest_right = sorted((float(dest_x1), float(dest_x2)))
        dest_top, dest_bottom = sorted((float(dest_y1), float(dest_y2)))
        source_left, source_right = sorted((float(source_x1), float(source_x2)))
        source_top, source_bottom = sorted((float(source_y1), float(source_y2)))
        return cls(
            kind=SchGeometryOpKind.IMAGE,
            payload={
                "dest_x1": _geometry_item_f32(dest_left),
                "dest_y1": _geometry_item_f32(dest_top),
                "dest_x2": _geometry_item_f32(dest_right),
                "dest_y2": _geometry_item_f32(dest_bottom),
                "source_x1": _geometry_item_f32(source_left),
                "source_y1": _geometry_item_f32(source_top),
                "source_x2": _geometry_item_f32(source_right),
                "source_y2": _geometry_item_f32(source_bottom),
                "alpha": _geometry_item_f32(alpha),
            },
        )

    def kind_str(self) -> str:
        return (
            self.kind.value
            if isinstance(self.kind, SchGeometryOpKind)
            else str(self.kind)
        )

    def to_dict(self, *, index: int | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.kind_str()}
        if index is not None:
            data["index"] = int(index)
        data.update(copy.deepcopy(self.payload))
        return data


@dataclass(frozen=True)
class SchGeometryRecord:
    """
    One record entry from the schematic geometry document.
    """

    handle: str
    unique_id: str | None
    kind: str
    object_id: str
    bounds: SchGeometryBounds | None = None
    operations: list[SchGeometryOp] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
    source_object_index: int | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    render_group_id: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    render_group_identity: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    render_source_id: int | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchGeometryRecord:
        extras = copy.deepcopy(data)
        operations_data = extras.pop("operations", []) or []
        bounds = SchGeometryBounds.from_dict(extras.pop("bounds", None))
        extras.pop("operation_count", None)
        return cls(
            handle=str(extras.pop("handle", "")),
            unique_id=(
                None
                if (unique_id := extras.pop("unique_id", "")) is None
                else str(unique_id)
            ),
            kind=str(extras.pop("kind", "")),
            object_id=str(extras.pop("object_id", "")),
            bounds=bounds,
            operations=[
                SchGeometryOp.from_dict(op)
                for op in operations_data
                if isinstance(op, dict)
            ],
            extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "handle": self.handle,
            "unique_id": self.unique_id,
            "kind": self.kind,
            "object_id": self.object_id,
        }
        if self.bounds is not None:
            data["bounds"] = self.bounds.to_dict()
        data["operation_count"] = len(self.operations)
        data["operations"] = [
            op.to_dict(index=index) for index, op in enumerate(self.operations)
        ]
        data.update(copy.deepcopy(self.extras))
        return data


@dataclass(frozen=True)
class SchGeometryDocument:
    """
    Typed, oracle-aligned schematic geometry document.
    """

    records: list[SchGeometryRecord] = field(default_factory=list)
    source_path: str | None = None
    source_kind: str = "SCH"
    include_kinds: list[str] = field(default_factory=lambda: ["all"])
    generated_utc: str | None = None
    failed_renders: int = 0
    coordinate_space: dict[str, Any] | None = None
    canvas: dict[str, Any] | None = None
    document_id: str | None = None
    workspace_background_color: str | None = None
    export_provenance: dict[str, Any] | None = None
    render_hints: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchGeometryDocument:
        schema = str(data.get("schema", "")).strip()
        if schema != SCH_GEOMETRY_ORACLE_SCHEMA:
            raise ValueError(f"Unexpected geometry oracle schema: {schema!r}")

        extras = copy.deepcopy(data)
        extras.pop("schema", None)
        extras.pop("source_path", None)
        extras.pop("source_kind", None)
        extras.pop("include_kinds", None)
        extras.pop("generated_utc", None)
        extras.pop("total_operations", None)
        extras.pop("failed_renders", None)
        extras.pop("records", None)
        coordinate_space = extras.pop("coordinate_space", None)
        canvas = extras.pop("canvas", None)
        document_id = extras.pop("document_id", None)
        workspace_background_color = extras.pop("workspace_background_color", None)
        export_provenance = extras.pop("export_provenance", None)
        render_hints = extras.pop("render_hints", None)

        records = []
        for record in data.get("records", []):
            if isinstance(record, dict):
                records.append(SchGeometryRecord.from_dict(record))

        return cls(
            records=records,
            source_path=str(data.get("source_path"))
            if data.get("source_path") is not None
            else None,
            source_kind=str(data.get("source_kind", "SCH")),
            include_kinds=[
                str(kind)
                for kind in (
                    ["all"]
                    if data.get("include_kinds") is None
                    else data["include_kinds"]
                )
            ],
            generated_utc=str(data.get("generated_utc"))
            if data.get("generated_utc") is not None
            else None,
            failed_renders=int(data.get("failed_renders", 0) or 0),
            coordinate_space=copy.deepcopy(coordinate_space)
            if isinstance(coordinate_space, dict)
            else None,
            canvas=copy.deepcopy(canvas) if isinstance(canvas, dict) else None,
            document_id=str(document_id) if document_id is not None else None,
            workspace_background_color=str(workspace_background_color)
            if workspace_background_color is not None
            else None,
            export_provenance=copy.deepcopy(export_provenance)
            if isinstance(export_provenance, dict)
            else None,
            render_hints=copy.deepcopy(render_hints)
            if isinstance(render_hints, dict)
            else None,
            extras=extras,
        )

    @classmethod
    def from_validated_dict(cls, data: dict[str, object]) -> SchGeometryDocument:
        """Decode one strict v1 payload after structural and semantic validation."""
        validate_sch_geometry_ir_payload(data)
        decoded = cls.from_dict(cast("dict[str, Any]", data))
        explicit_nulls = {
            key: None
            for key in ("source_path", "generated_utc", "document_id")
            if key in data and data[key] is None
        }
        if not explicit_nulls:
            return decoded
        return replace(decoded, extras={**decoded.extras, **explicit_nulls})

    @classmethod
    def from_validated_file(cls, path: str | Path) -> SchGeometryDocument:
        """Load and decode strict v1 JSON under the reviewed host limits."""
        data = _load_geometry_json_file(path)
        return cls.from_validated_dict(data)

    @classmethod
    def from_file(cls, path: str | Path) -> SchGeometryDocument:
        return cls.from_dict(cast("dict[str, Any]", _load_geometry_json_file(path)))

    def to_dict(self) -> dict[str, Any]:
        total_operations = sum(len(record.operations) for record in self.records)
        failed_renders = sum(
            isinstance(error, str) and bool(error)
            for record in self.records
            if (error := record.extras.get("error")) is not None
        )
        serialized_records = [record.to_dict() for record in self.records]
        data: dict[str, Any] = {
            "schema": SCH_GEOMETRY_ORACLE_SCHEMA,
            "source_kind": self.source_kind,
            "include_kinds": [str(kind) for kind in self.include_kinds],
            "total_operations": total_operations,
            "failed_renders": failed_renders,
            "records": serialized_records,
        }
        if self.source_path is not None:
            data["source_path"] = self.source_path
        if self.generated_utc is not None:
            data["generated_utc"] = self.generated_utc
        if self.coordinate_space is not None:
            data["coordinate_space"] = copy.deepcopy(self.coordinate_space)
        if self.canvas is not None:
            data["canvas"] = copy.deepcopy(self.canvas)
        if self.document_id is not None:
            data["document_id"] = self.document_id
        if self.workspace_background_color is not None:
            data["workspace_background_color"] = self.workspace_background_color
        if self.export_provenance is not None:
            data["export_provenance"] = copy.deepcopy(self.export_provenance)
        if self.render_hints is not None:
            data["render_hints"] = copy.deepcopy(self.render_hints)
        data.update(copy.deepcopy(self.extras))
        # Extension data cannot replace the required document contract. Keep
        # nullable known fields in extras so strict decode/encode preserves an
        # explicit null, but always restore the non-nullable derived fields.
        data.update(
            {
                "schema": SCH_GEOMETRY_ORACLE_SCHEMA,
                "source_kind": self.source_kind,
                "include_kinds": [str(kind) for kind in self.include_kinds],
                "total_operations": total_operations,
                "failed_renders": failed_renders,
                "records": serialized_records,
            }
        )
        return data

    def to_normalized_dict(self, *, source_path: str | None = None) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("generated_utc", None)

        if source_path is None:
            data.pop("source_path", None)
        else:
            data["source_path"] = source_path.replace("\\", "/")

        include_kinds = data.get("include_kinds")
        if isinstance(include_kinds, list):
            data["include_kinds"] = [str(kind) for kind in include_kinds]

        records = data.get("records")
        if not isinstance(records, list):
            data["records"] = []
            data["total_operations"] = 0
            return data

        total_operations = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            operations = record.get("operations")
            if isinstance(operations, list):
                record["operation_count"] = len(operations)
                total_operations += len(operations)
        data["total_operations"] = total_operations
        return data

    def write_json(self, path: str | Path) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path

    def write_normalized_json(
        self,
        path: str | Path,
        *,
        source_path: str | None = None,
    ) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_normalized_dict(source_path=source_path)
        output_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path


class SchGeometryOracle(SchGeometryDocument):
    """
    Alias for the external oracle payload wrapper.
    """
