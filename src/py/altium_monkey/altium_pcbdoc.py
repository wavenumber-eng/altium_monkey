"""
Parse and round-trip Altium PcbDoc board documents.
"""

import copy
import logging
import math
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence
import zlib

from .altium_api_markers import public_api
from .altium_board import AltiumBoard, AltiumBoardOutline
from .altium_component_kind import parse_component_kind
from .altium_embedded_files import (
    EmbeddedFont,
    EmbeddedModel,
    parse_embedded_fonts,
    sanitize_embedded_asset_name,
)
from .altium_pcb_component import AltiumPcbComponent
from .altium_pcb_dimension import AltiumPcbDimension, parse_dimensions6_stream
from .altium_pcb_extended_primitive_information import (
    AltiumPcbExtendedPrimitiveInformation,
    parse_extended_primitive_information_stream,
)
from .altium_pcb_step_bounds import compute_step_model_bounds_mils
from .altium_pcb_custom_shapes import (
    AltiumPcbCustomShapeRecord,
    attach_custom_pad_shape,
    build_pcblib_custom_pad_extended_info,
    build_pcblib_custom_pad_region_properties,
    find_best_pad_region,
    parse_custom_shapes_stream,
    resolve_pcbdoc_custom_pad_shapes,
    serialize_custom_shapes_stream,
)
from .altium_pcb_rule import AltiumPcbRule
from .altium_record_pcb__board_region import AltiumPcbBoardRegion
from .altium_record_pcb__arc import AltiumPcbArc
from .altium_record_pcb__fill import AltiumPcbFill
from .altium_record_pcb__model import AltiumPcbModel
from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_pcb__region import AltiumPcbRegion
from .altium_record_pcb__text import AltiumPcbText
from .altium_record_pcb__track import AltiumPcbTrack
from .altium_record_pcb__polygon import AltiumPcbPolygon
from .altium_record_pcb__net import AltiumPcbNet
from .altium_record_pcb__netclass import AltiumPcbNetClass
from .altium_record_types import PcbLayer
from .altium_record_pcb__via import AltiumPcbVia
from .altium_record_pcb__shapebased_region import (
    AltiumPcbShapeBasedRegion,
    PcbExtendedVertex,
)
from .altium_pcb_enums import (
    PadShape,
    PcbBarcodeKind,
    PcbBarcodeRenderMode,
    PcbBodyProjection,
    PcbRegionKind,
    PcbTextJustification,
    PcbTextKind,
)
from .altium_record_pcb__component_body import AltiumPcbComponentBody
from .altium_pcbdoc_layers import (
    _clear_raw_cache,
    _clamp_i32,
    _flip_layer,
    _sync_pad_saved_layer_state,
    _sync_saved_layer_id,
)
from .altium_pcb_embedded_model_compose import (
    collect_pcbdoc_embedded_model_entries,
    copy_footprint_with_models_into_builder,
)
from .altium_pcbdoc_builder_text import PCB_TEXT_BARCODE_MARGIN_MILS
from .altium_utilities import (
    decode_byte_array,
    encode_altium_record,
    get_records_in_section,
    parse_byte_record,
    parse_texts6_designators,
    parse_widestrings6,
)
from .altium_ole import AltiumOleFile, AltiumOleWriter

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .altium_board import BoardOutlineVertex
    from .altium_pcb_surface import PCB_SurfaceRole, PCB_SurfaceSide
    from .altium_pcb_svg_renderer import PcbSvgRenderOptions
    from .altium_pcblib import AltiumPcbLib

PcbDocPointMils = Sequence[float]
PcbDocBoundsMils = Sequence[float]


def _coerce_pcbdoc_point_mils(
    point: PcbDocPointMils,
    name: str,
) -> tuple[float, float]:
    """
    Normalize a public PCB point argument into an `(x_mils, y_mils)` tuple.
    """
    if len(point) != 2:
        raise ValueError(f"{name} must contain exactly two mil values")
    return float(point[0]), float(point[1])


def _coerce_pcbdoc_bounds_mils(
    bounds: PcbDocBoundsMils, name: str
) -> tuple[float, float, float, float]:
    """
    Normalize public PCB rectangular bounds into `(left, bottom, right, top)`.
    """
    if len(bounds) != 4:
        raise ValueError(f"{name} must contain exactly four mil values")
    left_mils, bottom_mils, right_mils, top_mils = (
        float(bounds[0]),
        float(bounds[1]),
        float(bounds[2]),
        float(bounds[3]),
    )
    if right_mils <= left_mils or top_mils <= bottom_mils:
        raise ValueError(
            f"{name} must be ordered as left, bottom, right, top with positive size"
        )
    return left_mils, bottom_mils, right_mils, top_mils


def _pcbdoc_mils_to_internal(value_mils: float) -> int:
    return int(round(float(value_mils) * 10000.0))


def _pcbdoc_ascii_identifier(text: str) -> str:
    return ",".join(str(ord(ch)) for ch in str(text))


# Footprint Extraction Helpers: PcbDoc -> PcbLib coordinate transform


def _reverse_transform_point(
    bx: float,
    by: float,
    cx: float,
    cy: float,
    rotation_deg: float,
    flipped: bool,
) -> tuple[float, float]:
    """
    Convert board-absolute coordinate to footprint-local coordinate.

    Reverses Altium's placement chain: local -> mirror_Y (if flip) -> rotate -> translate.
    PcbLib stores footprints in top-side orientation; bottom-side placement mirrors Y.
    For exact 90-degree multiples, uses integer arithmetic to avoid rounding.
    """
    dx = bx - cx
    dy = by - cy

    angle = rotation_deg % 360
    if angle == 0.0:
        lx, ly = dx, dy
    elif angle == 90.0:
        lx, ly = dy, -dx
    elif angle == 180.0:
        lx, ly = -dx, -dy
    elif angle == 270.0:
        lx, ly = -dy, dx
    else:
        rad = math.radians(angle)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)
        lx = dx * cos_r + dy * sin_r
        ly = -dx * sin_r + dy * cos_r

    if flipped:
        ly = -ly

    return lx, ly


def _reverse_transform_angle(
    board_angle: float,
    rotation_deg: float,
    flipped: bool,
) -> float:
    """
    Convert board-absolute angle to footprint-local angle (degrees).

    Non-flipped: local = board - comp_rotation
    Flipped (mirror_Y): local = -(board - comp_rotation)  (Y-mirror negates angle)
    """
    local = board_angle - rotation_deg
    if flipped:
        local = -local
    return local % 360.0


def _reverse_transform_angle_preserve_identity(
    board_angle: float,
    rotation_deg: float,
    flipped: bool,
) -> float:
    """
    Reverse-transform a 2D angle while preserving identity as 360.0.
    """
    local = _reverse_transform_angle(board_angle, rotation_deg, flipped)
    return _normalize_rotation_degrees(local, preserve_identity_360=True)


def _reverse_transform_angle_rad(
    board_angle_rad: float,
    rotation_deg: float,
    flipped: bool,
) -> float:
    """
    Same as _reverse_transform_angle but for angles in radians.
    """
    rot_rad = math.radians(rotation_deg)
    local = board_angle_rad - rot_rad
    if flipped:
        local = -local
    return local % (2 * math.pi)


def _normalize_rotation_degrees(
    angle: float, *, preserve_identity_360: bool = False
) -> float:
    """
    Normalize a degree rotation into [0, 360], snapping near-integers.
    """
    normalized = float(angle) % 360.0
    nearest_int = round(normalized)
    if math.isclose(normalized, nearest_int, abs_tol=1e-6):
        normalized = float(nearest_int % 360)
    if math.isclose(normalized, 0.0, abs_tol=1e-6):
        return 360.0 if preserve_identity_360 else 0.0
    return normalized


def _reverse_transform_model_z_rotation(
    board_angle: float,
    rotation_deg: float,
    flipped: bool,
    *,
    preserve_identity_360: bool,
) -> float:
    """
    Convert board-space 3D model Z rotation to footprint-local library space.

        Altium does not mirror STEP/body Z rotation like a 2D primitive angle when a
        component is placed on the bottom side. Empirically, the library-local model
        Z rotation is:
          - top placement:    board_model_z - component_rotation
          - bottom placement: board_model_z + component_rotation
    """
    local = board_angle + rotation_deg if flipped else board_angle - rotation_deg
    return _normalize_rotation_degrees(
        local, preserve_identity_360=preserve_identity_360
    )


def _source_angle_uses_identity_360(angle: float) -> bool:
    """
    True when a source angle encodes identity as 360 rather than 0.
    """
    normalized = float(angle) % 360.0
    return math.isclose(normalized, 0.0, abs_tol=1e-6) and not math.isclose(
        float(angle), 0.0, abs_tol=1e-6
    )


def _normalize_component_body_model_z_rotation(
    board_angle: float, rotation_deg: float, flipped: bool
) -> float:
    """
    Normalize a component-body model Z rotation into footprint-local space.
    """
    local = (
        float(board_angle) + float(rotation_deg)
        if flipped
        else float(board_angle) - float(rotation_deg)
    )
    preserve_identity_360 = math.isclose(local % 360.0, 0.0, abs_tol=1e-6) and (
        flipped
        or not math.isclose(float(board_angle), 0.0, abs_tol=1e-6)
        or not math.isclose(float(rotation_deg), 0.0, abs_tol=1e-6)
    )
    return _reverse_transform_model_z_rotation(
        board_angle,
        rotation_deg,
        flipped,
        preserve_identity_360=preserve_identity_360,
    )


def _normalize_generic_texture_rotation(
    board_angle: float, rotation_deg: float, flipped: bool
) -> float:
    """
    Normalize generic/extruded-body texture rotation into footprint-local space.

        Observed Altium behavior differs from STEP-backed model rotation:
          - generic body texture rotation generally normalizes via
            `board_texture_rotation - component_rotation`
          - when a bottom-side source already encodes identity as 360, Altium
            preserves that identity instead of remapping it through the mirror
          - when that normalization resolves to identity, Altium prefers 360
            if either the source texture rotation or the component placement was
            non-zero
    """
    if flipped and _source_angle_uses_identity_360(board_angle):
        return _normalize_rotation_degrees(
            board_angle,
            preserve_identity_360=True,
        )

    local = float(board_angle) - float(rotation_deg)
    preserve_identity_360 = math.isclose(local % 360.0, 0.0, abs_tol=1e-6) and (
        not math.isclose(float(board_angle), 0.0, abs_tol=1e-6)
        or not math.isclose(float(rotation_deg), 0.0, abs_tol=1e-6)
    )
    return _normalize_rotation_degrees(
        local, preserve_identity_360=preserve_identity_360
    )


def _component_body_uses_model_placement(body: AltiumPcbComponentBody) -> bool:
    """
    True when the body references an embedded/STEP-style model placement.

        Generic/extruded 3D bodies (MODELTYPE=0 with no model name/source) behave
        differently from STEP-backed bodies during Altium's board -> PcbLib
        extraction. In particular, their MODELID / MODEL.CHECKSUM values appear to
        be regenerated cache-like fields rather than stable semantic identifiers, so
        we should key generic-body equivalence off the normalized body geometry and
        presentation fields instead of those IDs.
    """
    model_id = str(getattr(body, "model_id", "") or "").strip()
    if not model_id:
        return False
    model_type = int(getattr(body, "model_type", 0) or 0)
    model_name = str(getattr(body, "model_name", "") or "").replace("\x00", "").strip()
    model_source = (
        str(getattr(body, "model_source", "") or "").replace("\x00", "").strip()
    )
    return model_type in {1, 2, 3} or bool(model_name) or bool(model_source)


def _extract_transform_track(
    track: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a Track from board-absolute to footprint-local.
    """
    sx, sy = _reverse_transform_point(
        track.start_x, track.start_y, cx, cy, rot, flipped
    )
    ex, ey = _reverse_transform_point(track.end_x, track.end_y, cx, cy, rot, flipped)
    track.start_x = _clamp_i32(sx)
    track.start_y = _clamp_i32(sy)
    track.end_x = _clamp_i32(ex)
    track.end_y = _clamp_i32(ey)
    if flipped:
        track.layer = _flip_layer(track.layer, layer_flip_map)
    _sync_saved_layer_id(
        track, "v7_layer_id", flipped=flipped, v7_layer_flip_map=v7_layer_flip_map
    )
    track.component_index = None
    track.net_index = 0xFFFF
    _clear_raw_cache(track)


def _extract_transform_pad(
    pad: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a Pad from board-absolute to footprint-local.
    """
    px, py = _reverse_transform_point(pad.x, pad.y, cx, cy, rot, flipped)
    pad.x = _clamp_i32(px)
    pad.y = _clamp_i32(py)
    pad.rotation = _reverse_transform_angle(pad.rotation, rot, flipped)
    if flipped:
        pad.layer = _flip_layer(pad.layer, layer_flip_map)
    _sync_pad_saved_layer_state(
        pad, flipped=flipped, v7_layer_flip_map=v7_layer_flip_map
    )
    pad.component_index = None
    pad.net_index = 0xFFFF
    _clear_raw_cache(pad)


def _extract_transform_arc(
    arc: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform an Arc from board-absolute to footprint-local.
    """
    cx2, cy2 = _reverse_transform_point(
        arc.center_x, arc.center_y, cx, cy, rot, flipped
    )
    arc.center_x = _clamp_i32(cx2)
    arc.center_y = _clamp_i32(cy2)
    arc.start_angle = _reverse_transform_angle(arc.start_angle, rot, flipped)
    arc.end_angle = _reverse_transform_angle(arc.end_angle, rot, flipped)
    if flipped:
        # Mirroring reverses arc direction - swap start/end
        arc.start_angle, arc.end_angle = arc.end_angle, arc.start_angle
        arc.layer = _flip_layer(arc.layer, layer_flip_map)
    _sync_saved_layer_id(
        arc, "v7_layer_id", flipped=flipped, v7_layer_flip_map=v7_layer_flip_map
    )
    arc.component_index = None
    arc.net_index = 0xFFFF
    _clear_raw_cache(arc)


def _extract_transform_via(
    via: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a Via from board-absolute to footprint-local.
    """
    vx, vy = _reverse_transform_point(via.x, via.y, cx, cy, rot, flipped)
    via.x = _clamp_i32(vx)
    via.y = _clamp_i32(vy)
    via.component_index = None
    via.net_index = 0xFFFF
    _clear_raw_cache(via)


def _extract_transform_text(
    text: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a Text from board-absolute to footprint-local.
    """
    tx, ty = _reverse_transform_point(text.x, text.y, cx, cy, rot, flipped)
    text.x = _clamp_i32(tx)
    text.y = _clamp_i32(ty)
    text.rotation = _reverse_transform_angle(text.rotation, rot, flipped)
    if flipped:
        text.layer = _flip_layer(text.layer, layer_flip_map)
    _sync_saved_layer_id(
        text, "barcode_layer_v7", flipped=flipped, v7_layer_flip_map=v7_layer_flip_map
    )
    text.component_index = None
    if hasattr(text, "net_index"):
        text.net_index = 0xFFFF
    _clear_raw_cache(text)


def _extract_transform_fill(
    fill: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a Fill from board-absolute to footprint-local.
    """
    f1x, f1y = _reverse_transform_point(fill.pos1_x, fill.pos1_y, cx, cy, rot, flipped)
    f2x, f2y = _reverse_transform_point(fill.pos2_x, fill.pos2_y, cx, cy, rot, flipped)
    fill.pos1_x = _clamp_i32(f1x)
    fill.pos1_y = _clamp_i32(f1y)
    fill.pos2_x = _clamp_i32(f2x)
    fill.pos2_y = _clamp_i32(f2y)
    fill.rotation = _reverse_transform_angle(fill.rotation, rot, flipped)
    if flipped:
        fill.layer = _flip_layer(fill.layer, layer_flip_map)
    _sync_saved_layer_id(
        fill, "v7_layer_id", flipped=flipped, v7_layer_flip_map=v7_layer_flip_map
    )
    fill.component_index = None
    fill.net_index = 0xFFFF
    _clear_raw_cache(fill)


def _extract_transform_region(
    region: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a Region (outline + hole vertices) to footprint-local.
    """
    if hasattr(region, "outline_vertices") and region.outline_vertices:
        for v in region.outline_vertices:
            vx, vy = _reverse_transform_point(v.x_raw, v.y_raw, cx, cy, rot, flipped)
            v.x_raw = float(vx)
            v.y_raw = float(vy)
    if hasattr(region, "hole_vertices") and region.hole_vertices:
        for hole in region.hole_vertices:
            for v in hole:
                vx, vy = _reverse_transform_point(
                    v.x_raw, v.y_raw, cx, cy, rot, flipped
                )
                v.x_raw = float(vx)
                v.y_raw = float(vy)
    if flipped:
        region.layer = _flip_layer(region.layer, layer_flip_map)
    region.component_index = None
    if hasattr(region, "net_index"):
        region.net_index = 0xFFFF
    _clear_raw_cache(region)


def _extract_transform_shapebased(
    sbr: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a ShapeBasedRegion or ComponentBody to footprint-local.
    """
    if hasattr(sbr, "outline") and sbr.outline:
        for v in sbr.outline:
            vx, vy = _reverse_transform_point(v.x, v.y, cx, cy, rot, flipped)
            v.x = _clamp_i32(vx)
            v.y = _clamp_i32(vy)
            if v.is_round:
                cvx, cvy = _reverse_transform_point(
                    v.center_x, v.center_y, cx, cy, rot, flipped
                )
                v.center_x = _clamp_i32(cvx)
                v.center_y = _clamp_i32(cvy)
                # ShapeBasedRegion contour angles are stored in degrees, not radians.
                v.start_angle = _reverse_transform_angle(v.start_angle, rot, flipped)
                v.end_angle = _reverse_transform_angle(v.end_angle, rot, flipped)
                if flipped:
                    v.start_angle, v.end_angle = v.end_angle, v.start_angle
    if hasattr(sbr, "holes") and sbr.holes:
        for hole in sbr.holes:
            for v in hole:
                vx, vy = _reverse_transform_point(v.x, v.y, cx, cy, rot, flipped)
                v.x = float(vx)
                v.y = float(vy)
    if flipped:
        sbr.layer = _flip_layer(sbr.layer, layer_flip_map)
    sbr.component_index = None
    _clear_raw_cache(sbr)


def _extract_transform_component_body(
    body: Any,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Reverse-transform a ComponentBody (inherits from ShapeBasedRegion).
    """
    _extract_transform_shapebased(body, cx, cy, rot, flipped, layer_flip_map)
    if (
        hasattr(body, "body_projection")
        and body.body_projection == PcbBodyProjection.BOTTOM
    ):
        body.body_projection = PcbBodyProjection.TOP
    # Also transform 2D model placement offset
    if hasattr(body, "model_2d_x") and hasattr(body, "model_2d_y"):
        mx, my = _reverse_transform_point(
            body.model_2d_x, body.model_2d_y, cx, cy, rot, flipped
        )
        body.model_2d_x = _clamp_i32(mx)
        body.model_2d_y = _clamp_i32(my)
    # The body-local 2D/3D model rotations in the extracted footprint should be
    # library-space, not board-placement-space. Altium normalizes 2D identity
    # rotation to 0.0 (not 360.0) in extracted PcbLib bodies, while 3D Z
    # rotation follows the footprint-local normalization rules below.
    if hasattr(body, "model_2d_rotation"):
        body.model_2d_rotation = _normalize_rotation_degrees(
            float(body.model_2d_rotation),
            preserve_identity_360=False,
        )
    if getattr(body, "model_id", "") and hasattr(body, "model_3d_rotz"):
        source_model_3d_rotz = float(body.model_3d_rotz)
        if _component_body_uses_model_placement(body):
            normalized_model_3d_rotz = _normalize_component_body_model_z_rotation(
                source_model_3d_rotz,
                rot,
                flipped,
            )
            if (
                flipped
                and math.isclose(float(rot) % 360.0, 180.0, abs_tol=1e-6)
                and math.isclose(
                    float(getattr(body, "model_3d_rotx", 0.0)) % 360.0,
                    90.0,
                    abs_tol=1e-6,
                )
                and math.isclose(source_model_3d_rotz % 360.0, 180.0, abs_tol=1e-6)
                and math.isclose(normalized_model_3d_rotz % 360.0, 0.0, abs_tol=1e-6)
            ):
                normalized_model_3d_rotz = 0.0
            body.model_3d_rotz = normalized_model_3d_rotz
        else:
            body.model_3d_rotz = source_model_3d_rotz
    if hasattr(body, "model_checksum"):
        body.model_checksum = int(body.model_checksum) & 0xFFFFFFFF
    model_name = str(getattr(body, "model_name", "") or "").replace("\x00", "").strip()
    model_source = (
        str(getattr(body, "model_source", "") or "").replace("\x00", "").strip()
    )
    if hasattr(body, "texture_rotation") and (not model_name and not model_source):
        body.model_name = ""
        body.model_source = ""
        body.texture_rotation = _normalize_generic_texture_rotation(
            float(body.texture_rotation),
            rot,
            flipped,
        )


def _localize_custom_pad_contract(
    footprint: Any,
    source_pad: Any,
    local_pad: Any,
    *,
    local_pad_index: int,
    cx: float,
    cy: float,
    rot: float,
    flipped: bool,
    layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Promote a PcbDoc custom pad into the footprint-local PcbLib contract.
    """
    source_custom = getattr(source_pad, "custom_shape", None)
    if source_custom is None:
        return

    for source_layer_shape in source_custom.iter_layer_shapes():
        source_region = getattr(source_layer_shape, "region", None)
        if source_region is None:
            continue

        source_layer_id = int(
            getattr(source_layer_shape, "layer", getattr(source_region, "layer", 0))
            or 0
        )
        local_layer_id = (
            _flip_layer(source_layer_id, layer_flip_map) if flipped else source_layer_id
        )
        if local_layer_id not in {
            PcbLayer.TOP.value,
            PcbLayer.BOTTOM.value,
            PcbLayer.TOP_PASTE.value,
            PcbLayer.BOTTOM_PASTE.value,
            PcbLayer.TOP_SOLDER.value,
            PcbLayer.BOTTOM_SOLDER.value,
        }:
            continue
        local_region = find_best_pad_region(
            list(getattr(footprint, "regions", []) or []),
            local_pad,
            target_layer=local_layer_id,
        )
        if local_region is None:
            import copy

            local_region = copy.deepcopy(source_region)
            _extract_transform_region(
                local_region, cx, cy, rot, flipped, layer_flip_map
            )
            footprint.regions.append(local_region)
            footprint._record_order.append(local_region)

        source_shape_region = getattr(source_layer_shape, "shape_region", None)
        local_shape_region = None
        if source_shape_region is not None:
            import copy

            local_shape_region = copy.deepcopy(source_shape_region)
            _extract_transform_shapebased(
                local_shape_region, cx, cy, rot, flipped, layer_flip_map
            )

        include_pad_index = local_layer_id in {
            PcbLayer.TOP.value,
            PcbLayer.BOTTOM.value,
        }
        should_emit_contour = False
        if local_shape_region is not None:
            should_emit_contour = include_pad_index or any(
                bool(getattr(vertex, "is_round", False))
                for vertex in (getattr(local_shape_region, "outline", None) or [])
            )

        if local_shape_region is not None and should_emit_contour:
            local_region.properties = build_pcblib_custom_pad_region_properties(
                region=local_region,
                shape_region=local_shape_region,
                pad_index=local_pad_index + 1,
                include_pad_index=include_pad_index,
            )
        elif include_pad_index:
            local_region.properties = {
                str(key): str(value).replace("\x00", "")
                for key, value in (
                    getattr(local_region, "properties", {}) or {}
                ).items()
            }
            local_region.properties["PADINDEX"] = str(local_pad_index + 1)
        else:
            local_region.properties = {
                str(key): str(value).replace("\x00", "")
                for key, value in (
                    getattr(local_region, "properties", {}) or {}
                ).items()
            }
        try:
            local_region.properties["V7_LAYER"] = PcbLayer(
                int(getattr(local_region, "layer", local_layer_id) or local_layer_id)
            ).to_json_name()
        except (TypeError, ValueError):
            pass
        local_region.is_shapebased = True
        _clear_raw_cache(local_region)

        attach_custom_pad_shape(
            local_pad,
            source="pcbdoc_stream",
            region=local_region,
            shape_region=local_shape_region,
            record=getattr(source_layer_shape, "source_record", None),
            pad_index=local_pad_index,
            shape_kind=getattr(source_layer_shape, "shape_kind", 10),
            layer=int(getattr(local_region, "layer", local_layer_id) or local_layer_id),
        )

        if include_pad_index:
            primitive_index = footprint._record_order.index(local_region)
            has_explicit_paste_region = any(
                getattr(layer_shape, "region", None) is not None
                and getattr(layer_shape, "layer", None)
                in {
                    PcbLayer.TOP_PASTE.value,
                    PcbLayer.BOTTOM_PASTE.value,
                }
                for layer_shape in source_custom.iter_layer_shapes()
            )
            footprint.extended_primitive_information.append(
                build_pcblib_custom_pad_extended_info(
                    primitive_index=primitive_index,
                    pad=local_pad,
                    layer=int(
                        getattr(local_region, "layer", local_layer_id) or local_layer_id
                    ),
                    has_explicit_paste_region=has_explicit_paste_region,
                )
            )


def _region_pad_index_zero_based(region: Any) -> int | None:
    props = getattr(region, "properties", {}) or {}
    pad_index = props.get("PADINDEX")
    if pad_index is None:
        return None
    try:
        parsed = int(float(str(pad_index).replace("\x00", "").strip()))
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed - 1


def _should_skip_custom_pad_region_copy(
    region: Any,
    pads: list[Any],
    component_index: int,
) -> bool:
    pad_index = _region_pad_index_zero_based(region)
    if pad_index is None or not (0 <= pad_index < len(pads)):
        return False

    source_pad = pads[pad_index]
    if getattr(source_pad, "component_index", None) != component_index:
        return False

    custom_shape = getattr(source_pad, "custom_shape", None)
    if custom_shape is None:
        return False

    desired_layers = {
        int(layer_shape.layer)
        for layer_shape in custom_shape.iter_layer_shapes()
        if getattr(layer_shape, "region", None) is not None
        and getattr(layer_shape, "layer", None) is not None
        and int(layer_shape.layer)
        in {
            PcbLayer.TOP.value,
            PcbLayer.BOTTOM.value,
            PcbLayer.TOP_PASTE.value,
            PcbLayer.BOTTOM_PASTE.value,
            PcbLayer.TOP_SOLDER.value,
            PcbLayer.BOTTOM_SOLDER.value,
        }
    }
    if not desired_layers:
        return False
    return int(getattr(region, "layer", 0) or 0) not in desired_layers


def _build_pcblib_widestrings(texts: list[Any], pcbdoc_table: dict[int, str]) -> bytes:
    """
    Build PcbLib WideStrings stream from text primitives.

    Format: [uint32 length][pipe-delimited |ENCODEDTEXT{N}=b1,b2,...|]
    """
    entries = []
    for text in texts:
        idx = getattr(text, "widestring_index", None)
        content = getattr(text, "text_content", None)
        if content is None and idx is not None:
            content = pcbdoc_table.get(idx, "")
        if content and idx is not None:
            byte_csv = ",".join(str(ord(c)) for c in content)
            entries.append(f"ENCODEDTEXT{idx}={byte_csv}")

    if not entries:
        return struct.pack("<I", 0)

    props_str = "|" + "|".join(entries) + "|"
    props_bytes = props_str.encode("ascii")
    return struct.pack("<I", len(props_bytes)) + props_bytes


def _build_footprint_parameters(name: str) -> bytes:
    """
    Build minimal PcbLib Parameters stream for a footprint.
    """
    props_str = f"|PATTERN={name}|"
    props_bytes = props_str.encode("ascii")
    return struct.pack("<I", len(props_bytes)) + props_bytes


@public_api
class AltiumPcbDoc:
    """
    Altium PcbDoc file parser.

    Parses complete PcbDoc files including both text-based component placement
    and binary primitive geometry (pads, tracks, arcs, texts).

    Attributes:
        filepath: Path to .PcbDoc file

        # Text-based data (Components6, Nets6, Board6, Classes6, Polygons6)
        components: List of AltiumPcbComponent (component placements)
        board: AltiumBoard (board origin and metadata)
        nets: List of AltiumPcbNet (net definitions with properties)
        net_classes: List of AltiumPcbNetClass (net class groupings)
        polygons: List of AltiumPcbPolygon (polygon pour definitions)

        # Binary primitive geometry (Pads6, Tracks6, Arcs6, Texts6)
        pads: List of AltiumPcbPad records
        tracks: List of AltiumPcbTrack records
        arcs: List of AltiumPcbArc records
        texts: List of AltiumPcbText records
        fills: List of AltiumPcbFill records
        regions: List of AltiumPcbRegion records
        shapebased_regions: List of AltiumPcbShapeBasedRegion (rendered polygon geometry)
        models: List of AltiumPcbModel records

        # Embedded files
        embedded_fonts: List of EmbeddedFont (embedded TrueType fonts)
        embedded_models: List of EmbeddedModel (embedded 3D models)

        # Raw streams for passthrough
        _raw_streams: Dict of stream_name -> bytes

        # Access components
        for comp in pcbdoc.components:
            print(f"{comp.designator}: {comp.footprint}")

        # Access geometry
        for pad in pcbdoc.pads:
            print(f"Pad {pad.designator}")

        # Round-trip
        pcbdoc.save("output.PcbDoc")

    """

    def __init__(self, filepath: Path | str | None = None) -> None:
        """
        Create an AltiumPcbDoc.

        The constructor creates an empty in-memory object and stores `filepath`
        metadata only. Use `AltiumPcbDoc.from_file(...)` to parse an existing
        binary document.

        Args:
            filepath: Optional source or destination `.PcbDoc` path metadata.
                If omitted, creates an empty PcbDoc with no associated path.
        """
        self.filepath: Path | None = Path(filepath) if filepath is not None else None

        # Text-based data
        self.components: list[AltiumPcbComponent] = []
        self.board: AltiumBoard | None = None
        self.nets: list[
            AltiumPcbNet
        ] = []  # Changed from list[str] to list[AltiumPcbNet]
        self.net_classes: list[AltiumPcbNetClass] = []
        self.polygons: list[AltiumPcbPolygon] = []
        self.rules: list[AltiumPcbRule] = []
        self.dimensions: list[AltiumPcbDimension] = []
        self.extended_primitive_information: list[
            AltiumPcbExtendedPrimitiveInformation
        ] = []
        self.custom_shapes: list[AltiumPcbCustomShapeRecord] = []

        # Binary primitive geometry
        self.pads: list[AltiumPcbPad] = []
        self.vias: list[AltiumPcbVia] = []
        self.tracks: list[AltiumPcbTrack] = []
        self.arcs: list[AltiumPcbArc] = []
        self.texts: list[AltiumPcbText] = []
        self.fills: list[AltiumPcbFill] = []
        self.regions: list[AltiumPcbRegion] = []
        self.board_regions: list[AltiumPcbBoardRegion] = []
        self.shapebased_regions: list[AltiumPcbShapeBasedRegion] = []
        self.component_bodies: list[AltiumPcbComponentBody] = []
        self.shapebased_component_bodies: list[AltiumPcbComponentBody] = []
        self.models: list[AltiumPcbModel] = []
        self._models_stream_source: str | None = None
        self.widestrings_table: dict[int, str] = {}

        # Embedded files
        self.embedded_fonts: list[EmbeddedFont] = []
        self.embedded_models: list[EmbeddedModel] = []

        # Raw streams for passthrough
        # CRITICAL: Stores raw binary for ALL streams (both parsed and unparsed)
        # to ensure complete round-trip preservation
        self._raw_streams: dict[str, bytes] = {}
        self.raw_custom_shapes_header: bytes | None = None
        self.raw_custom_shapes_data: bytes | None = None
        self._authoring_builder: Any | None = None

    def _profile_for_authoring_builder(self) -> object:
        from .altium_pcbdoc_builder import PcbDocBuildProfile

        if self.filepath is not None and self.filepath.exists():
            return PcbDocBuildProfile.from_pcbdoc(self.filepath)
        if self._raw_streams:
            return PcbDocBuildProfile.from_raw_streams(self._raw_streams)
        return PcbDocBuildProfile.default()

    def _ensure_authoring_builder(self) -> Any:
        if self._authoring_builder is None:
            from .altium_pcbdoc_builder import PcbDocBuilder

            self._authoring_builder = PcbDocBuilder(
                profile=self._profile_for_authoring_builder()
            )
        self._mirror_authoring_builder_state()
        return self._authoring_builder

    def _mirror_authoring_builder_state(self) -> None:
        builder = self._authoring_builder
        if builder is None:
            return
        self.components = builder.components
        self.nets = builder.nets
        self.arcs = builder.arcs
        self.tracks = builder.tracks
        self.fills = builder.fills
        self.texts = builder.texts
        self.pads = builder.pads
        self.regions = builder.regions
        self.shapebased_regions = builder.shapebased_regions
        self.vias = builder.vias
        self.models = builder.models
        self.component_bodies = builder.component_bodies
        self.shapebased_component_bodies = builder.shapebased_component_bodies
        self.board = builder.board_data.top_level_board

    def _sync_from_saved_authoring_file(
        self,
        filepath: Path,
        *,
        verbose: bool,
    ) -> None:
        builder = self._authoring_builder
        parsed = self.from_file(filepath, verbose=verbose)
        self.__dict__.update(parsed.__dict__)
        self._authoring_builder = builder
        self._mirror_authoring_builder_state()

    def set_outline_vertices_mils(
        self,
        vertices_mils: Sequence[tuple[float, float]],
    ) -> None:
        """
        Replace the board outline with explicit polygon vertices in mils.

        Args:
            vertices_mils: Ordered board-outline vertices as `(x_mils, y_mils)`
                tuples. The outline is closed automatically when written.
        """
        self._ensure_authoring_builder().set_outline_vertices_mils(vertices_mils)
        self._mirror_authoring_builder_state()

    def set_board_outline(self, outline: AltiumBoardOutline) -> None:
        """
        Replace the board outline with line/arc outline geometry in mils.

        Args:
            outline: `AltiumBoardOutline` instance containing ordered outline
                vertices and optional cutouts.
        """
        self._ensure_authoring_builder().set_board_outline(outline)
        self._mirror_authoring_builder_state()

    def set_outline_rectangle_mils(
        self,
        left_mils: float,
        bottom_mils: float,
        right_mils: float,
        top_mils: float,
    ) -> None:
        """
        Set the board outline to an axis-aligned rectangle in mils.

        Args:
            left_mils: Left X coordinate in mils.
            bottom_mils: Bottom Y coordinate in mils.
            right_mils: Right X coordinate in mils.
            top_mils: Top Y coordinate in mils.
        """
        self._ensure_authoring_builder().set_outline_rectangle_mils(
            left_mils,
            bottom_mils,
            right_mils,
            top_mils,
        )
        self._mirror_authoring_builder_state()

    def set_outline_circle_mils(
        self,
        *,
        center_mils: PcbDocPointMils,
        diameter_mils: float,
        vertex_count: int = 64,
    ) -> None:
        """
        Approximate a circular board outline with a polygon in mils.

        Args:
            center_mils: Circle center as `(x_mils, y_mils)`.
            diameter_mils: Circle diameter in mils.
            vertex_count: Number of polygon vertices used for the approximation.
        """
        center = _coerce_pcbdoc_point_mils(center_mils, "center_mils")
        self._ensure_authoring_builder().set_outline_circle_mils(
            center_mils=center,
            diameter_mils=diameter_mils,
            vertex_count=vertex_count,
        )
        self._mirror_authoring_builder_state()

    def set_origin_mils(self, x_mils: float, y_mils: float) -> None:
        """
        Set the board origin in mils.

        Args:
            x_mils: Board origin X coordinate in mils.
            y_mils: Board origin Y coordinate in mils.
        """
        self._ensure_authoring_builder().set_origin_mils(x_mils, y_mils)
        self._mirror_authoring_builder_state()

    def set_origin_to_outline_lower_left(self) -> None:
        """
        Set the board origin to the lower-left bounds of the current outline.

        Raises:
            ValueError: If the board has no outline to anchor against.
        """
        self._ensure_authoring_builder().set_origin_to_outline_lower_left()
        self._mirror_authoring_builder_state()

    def set_layer_stack_template(self, template: object) -> None:
        """
        Apply a named or explicit layer-stack template to this board.

        Args:
            template: Layer-stack template object or supported template name for
                the underlying PCB authoring builder.
        """
        self._ensure_authoring_builder().set_layer_stack_template(template)
        self._mirror_authoring_builder_state()

    def add_net(
        self,
        name: str,
        *,
        preferred_width_mils: float = 10.0,
    ) -> AltiumPcbNet:
        """
        Add or return a net by name.

        Args:
            name: Net name. Matching is case-insensitive after trimming.
            preferred_width_mils: Preferred routing width stored for new nets in
                mils.

        Returns:
            The existing or newly authored `AltiumPcbNet` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_net(name, preferred_width_mils=preferred_width_mils)
        self._mirror_authoring_builder_state()
        normalized = name.strip().upper()
        for net in self.nets:
            if net.name.strip().upper() == normalized:
                return net
        raise RuntimeError(f"Failed to add net: {name}")

    def add_embedded_model(
        self,
        *,
        name: str,
        model_data: bytes,
        model_id: str | None = None,
        rotation_x_degrees: float = 0.0,
        rotation_y_degrees: float = 0.0,
        rotation_z_degrees: float = 0.0,
        z_offset_mils: float = 0.0,
        checksum: int | None = None,
        model_source: str = "Undefined",
        data_is_compressed: bool = False,
    ) -> AltiumPcbModel:
        """
        Add an embedded 3D model payload to this board.

        When `checksum` is omitted, the checksum is computed with Altium's
        native byte-weighted model checksum algorithm. Pass `checksum` only
        when preserving source metadata exactly during a copy workflow.

        Args:
            name: Model filename stored in `Models/Data`, commonly `.step` or
                `.stp`.
            model_data: Model payload bytes. Pass uncompressed bytes by default,
                or zlib-compressed `Models/<n>` payload bytes when
                `data_is_compressed=True`.
            model_id: Optional model GUID. A new GUID is generated when omitted.
            rotation_x_degrees: Default model X-axis rotation in degrees.
            rotation_y_degrees: Default model Y-axis rotation in degrees.
            rotation_z_degrees: Default model Z-axis rotation in degrees.
            z_offset_mils: Default model Z offset in mils.
            checksum: Optional native checksum override.
            model_source: Altium model source string.
            data_is_compressed: True when `model_data` is already compressed.

        Returns:
            The authored embedded model metadata object.
        """
        builder = self._ensure_authoring_builder()
        model = builder.add_embedded_model(
            name=name,
            model_data=model_data,
            model_id=model_id,
            rotation_x_degrees=rotation_x_degrees,
            rotation_y_degrees=rotation_y_degrees,
            rotation_z_degrees=rotation_z_degrees,
            z_offset_mil=z_offset_mils,
            checksum=checksum,
            model_source=model_source,
            data_is_compressed=data_is_compressed,
        )
        self._mirror_authoring_builder_state()
        return model

    def add_component_body(
        self,
        *,
        outline_points_mils: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.MECHANICAL_1,
        overall_height_mils: float,
        standoff_height_mils: float = 0.5,
        cavity_height_mils: float = 0.0,
        body_projection: PcbBodyProjection = PcbBodyProjection.TOP,
        model: AltiumPcbModel | None = None,
        model_2d_mils: PcbDocPointMils = (0.0, 0.0),
        model_2d_rotation_degrees: float = 0.0,
        model_3d_rotx_degrees: float | None = None,
        model_3d_roty_degrees: float | None = None,
        model_3d_rotz_degrees: float | None = None,
        model_3d_dz_mils: float | None = None,
        model_checksum: int | None = None,
        identifier: str | None = None,
        name: str = " ",
        body_color_3d: int = 0x808080,
        body_opacity_3d: float = 1.0,
        model_type: int = 1,
        model_source: str | None = None,
    ) -> AltiumPcbComponentBody:
        """
        Add a free board-level 3D component body using mil-unit geometry.

        This low-level public helper maps directly to Altium's component-body
        record. Use `add_extruded_3d_body(...)` for a generic extruded solid and
        `add_embedded_3d_model(...)` for a STEP-backed body.

        Args:
            outline_points_mils: Board-local 2D projection polygon vertices in
                mils.
            layer: `PcbLayer` or native layer id that owns the 2D projection.
            overall_height_mils: Top Z height of the body in mils.
            standoff_height_mils: Bottom Z height of the body in mils.
            cavity_height_mils: Native cavity height field in mils.
            body_projection: `PcbBodyProjection` side/projection mode.
            model: Optional embedded or linked `AltiumPcbModel`.
            model_2d_mils: 2D model placement point as `(x_mils, y_mils)`.
            model_2d_rotation_degrees: 2D model placement rotation in degrees.
            model_3d_rotx_degrees: Optional 3D model X-axis rotation override.
            model_3d_roty_degrees: Optional 3D model Y-axis rotation override.
            model_3d_rotz_degrees: Optional 3D model Z-axis rotation override.
            model_3d_dz_mils: Optional 3D model Z offset override in mils.
            model_checksum: Optional native model checksum override.
            identifier: Optional native body identifier.
            name: Body name shown by Altium.
            body_color_3d: Native Win32 color integer for generic 3D bodies.
            body_opacity_3d: Body opacity from 0.0 to 1.0.
            model_type: Native model type. Use 0 for extruded bodies and 1 for
                STEP-backed bodies.
            model_source: Optional native model source string override.

        Returns:
            The authored `AltiumPcbComponentBody` record.
        """
        if len(outline_points_mils) < 3:
            raise ValueError("Component body outline requires at least 3 points")

        layer_id = int(layer)
        model_2d_x_mils, model_2d_y_mils = _coerce_pcbdoc_point_mils(
            model_2d_mils, "model_2d_mils"
        )

        body = AltiumPcbComponentBody()
        body.layer = layer_id
        body.net_index = None
        body.polygon_index = 0xFFFF
        body.component_index = None
        body.hole_count = 0
        body.is_locked = False
        body.is_keepout = False
        body.kind = PcbRegionKind.COPPER
        body.is_shapebased = False
        body.subpoly_index = -1
        body.union_index = 0
        body.standoff_height = _pcbdoc_mils_to_internal(standoff_height_mils)
        body.overall_height = _pcbdoc_mils_to_internal(overall_height_mils)
        body.cavity_height = _pcbdoc_mils_to_internal(cavity_height_mils)
        body.body_projection = body_projection
        body.body_color_3d = int(body_color_3d)
        body.body_opacity_3d = float(body_opacity_3d)
        body.identifier = identifier or _pcbdoc_ascii_identifier(name)
        body.texture = ""
        body.texture_center_x = 0
        body.texture_center_y = 0
        body.texture_size_x = 0
        body.texture_size_y = 0
        body.texture_rotation = 0.0
        body.arc_resolution = 0.5
        body.v7_layer = PcbLayer(layer_id).to_json_name()
        body.name = name
        body._geometry_variant = (False, False)
        body.model_type = int(model_type)

        outline: list[PcbExtendedVertex] = []
        for x_mils, y_mils in outline_points_mils:
            vertex = PcbExtendedVertex()
            vertex.is_round = False
            vertex.x = _pcbdoc_mils_to_internal(x_mils)
            vertex.y = _pcbdoc_mils_to_internal(y_mils)
            vertex.center_x = vertex.x
            vertex.center_y = vertex.y
            vertex.radius = 0
            vertex.start_angle = 0.0
            vertex.end_angle = 0.0
            outline.append(vertex)
        body.outline = outline
        body.holes = []

        if model is not None:
            body.model_id = str(model.id)
            body.model_checksum = (
                int(model.checksum) if model_checksum is None else int(model_checksum)
            )
            body.model_is_embedded = bool(model.is_embedded)
            body.model_name = str(model.name)
            body.model_2d_x = _pcbdoc_mils_to_internal(model_2d_x_mils)
            body.model_2d_y = _pcbdoc_mils_to_internal(model_2d_y_mils)
            body.model_2d_rotation = float(model_2d_rotation_degrees)
            body.model_3d_rotx = float(
                model.rotation_x
                if model_3d_rotx_degrees is None
                else model_3d_rotx_degrees
            )
            body.model_3d_roty = float(
                model.rotation_y
                if model_3d_roty_degrees is None
                else model_3d_roty_degrees
            )
            body.model_3d_rotz = float(
                model.rotation_z
                if model_3d_rotz_degrees is None
                else model_3d_rotz_degrees
            )
            body.model_3d_dz = (
                int(round(model.z_offset))
                if model_3d_dz_mils is None
                else _pcbdoc_mils_to_internal(model_3d_dz_mils)
            )
            body.model_source = (
                model.model_source if model_source is None else model_source
            )

        builder = self._ensure_authoring_builder()
        builder.add_component_body(body)
        self._mirror_authoring_builder_state()
        return self.component_bodies[-1]

    def add_component_body_rectangle(
        self,
        *,
        left_mils: float,
        bottom_mils: float,
        right_mils: float,
        top_mils: float,
        **kwargs: object,
    ) -> AltiumPcbComponentBody:
        """
        Add a rectangular free 3D component body using mil-unit bounds.

        Args:
            left_mils: Left X coordinate of the projection rectangle in mils.
            bottom_mils: Bottom Y coordinate of the projection rectangle in mils.
            right_mils: Right X coordinate of the projection rectangle in mils.
            top_mils: Top Y coordinate of the projection rectangle in mils.
            **kwargs: Additional arguments accepted by `add_component_body(...)`,
                such as `overall_height_mils`, `standoff_height_mils`, `model`,
                `body_projection`, and model transform overrides.

        Returns:
            The authored `AltiumPcbComponentBody` record.
        """
        return self.add_component_body(
            outline_points_mils=[
                (left_mils, bottom_mils),
                (right_mils, bottom_mils),
                (right_mils, top_mils),
                (left_mils, top_mils),
            ],
            **kwargs,
        )

    def add_extruded_3d_body(
        self,
        *,
        outline_points_mils: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.MECHANICAL_1,
        overall_height_mils: float,
        standoff_height_mils: float = 0.0,
        side: PcbBodyProjection = PcbBodyProjection.TOP,
        name: str = "Extruded 3D Body",
        identifier: str | None = None,
        body_color_3d: int = 0x808080,
        opacity: float = 1.0,
    ) -> AltiumPcbComponentBody:
        """
        Add a free generic extruded 3D body to this board.

        Args:
            outline_points_mils: Polygon vertices for the 2D projection in mils.
            layer: Mechanical `PcbLayer` or native layer id that owns the 3D
                body projection.
            overall_height_mils: Top Z of the extruded body in mils.
            standoff_height_mils: Bottom Z of the extruded body in mils.
            side: `PcbBodyProjection` board side/projection for the body.
            name: Body name shown in Altium.
            identifier: Optional native identifier override.
            body_color_3d: Native Win32 color integer for the extruded body.
            opacity: 3D body opacity from 0.0 to 1.0.

        Returns:
            The authored `AltiumPcbComponentBody` record.
        """
        if not 0.0 <= float(opacity) <= 1.0:
            raise ValueError("opacity must be between 0.0 and 1.0")
        body = self.add_component_body(
            outline_points_mils=outline_points_mils,
            layer=layer,
            overall_height_mils=overall_height_mils,
            standoff_height_mils=standoff_height_mils,
            body_projection=side,
            identifier=identifier,
            name=name,
            body_color_3d=body_color_3d,
            body_opacity_3d=opacity,
            model_type=0,
        )
        body.model_extruded_min_z = _pcbdoc_mils_to_internal(standoff_height_mils)
        body.model_extruded_max_z = _pcbdoc_mils_to_internal(overall_height_mils)
        self._ensure_authoring_builder().component_bodies[
            -1
        ].model_extruded_min_z = body.model_extruded_min_z
        self._ensure_authoring_builder().component_bodies[
            -1
        ].model_extruded_max_z = body.model_extruded_max_z
        self._ensure_authoring_builder().shapebased_component_bodies[
            -1
        ].model_extruded_min_z = body.model_extruded_min_z
        self._ensure_authoring_builder().shapebased_component_bodies[
            -1
        ].model_extruded_max_z = body.model_extruded_max_z
        self._mirror_authoring_builder_state()
        return self.component_bodies[-1]

    def add_embedded_3d_model(
        self,
        model: AltiumPcbModel,
        *,
        overall_height_mils: float | None = None,
        bounds_mils: PcbDocBoundsMils | None = None,
        projection_outline_mils: Sequence[PcbDocPointMils] | None = None,
        layer: int | PcbLayer = PcbLayer.MECHANICAL_1,
        side: PcbBodyProjection = PcbBodyProjection.TOP,
        location_mils: PcbDocPointMils = (0.0, 0.0),
        rotation_x_degrees: float | None = None,
        rotation_y_degrees: float | None = None,
        rotation_z_degrees: float | None = None,
        standoff_height_mils: float | None = None,
        identifier: str | None = None,
        name: str = " ",
        opacity: float = 1.0,
    ) -> AltiumPcbComponentBody:
        """
        Place an embedded PCB 3D model using Altium 3D Body dialog concepts.

        If neither `bounds_mils` nor `projection_outline_mils` is supplied, the
        rectangular projection is inferred from the embedded STEP payload using
        OCCT. `location_mils` and rotation arguments are applied to inferred
        projection geometry. Explicit projection geometry is written as supplied.

        Args:
            model: `AltiumPcbModel` returned by `add_embedded_model(...)`.
            overall_height_mils: Optional Overall Height in mils. When omitted,
                the height is inferred from STEP bounds.
            bounds_mils: Optional rectangular projection as `(left_mils,
                bottom_mils, right_mils, top_mils)`.
            projection_outline_mils: Optional non-rectangular projection polygon
                vertices in mils.
            layer: `PcbLayer` or native layer id that owns the projection.
            side: `PcbBodyProjection` side/projection mode.
            location_mils: Model XY placement point in mils.
            rotation_x_degrees: Optional X-axis rotation override in degrees.
            rotation_y_degrees: Optional Y-axis rotation override in degrees.
            rotation_z_degrees: Optional Z-axis rotation override in degrees.
            standoff_height_mils: Optional Z offset/standoff override in mils.
            identifier: Optional native body identifier.
            name: Body name shown by Altium.
            opacity: Body opacity from 0.0 to 1.0.

        Returns:
            The authored `AltiumPcbComponentBody` record.

        Raises:
            ValueError: If both `bounds_mils` and `projection_outline_mils` are
                supplied, if projection inference is requested without embedded
                STEP bytes, or if `opacity` is outside 0.0 through 1.0.
        """
        if not 0.0 <= float(opacity) <= 1.0:
            raise ValueError("opacity must be between 0.0 and 1.0")
        if bounds_mils is not None and projection_outline_mils is not None:
            raise ValueError(
                "Pass exactly one of bounds_mils or projection_outline_mils"
            )

        location_x_mils, location_y_mils = _coerce_pcbdoc_point_mils(
            location_mils, "location_mils"
        )
        resolved_rotation_x_degrees = float(
            model.rotation_x if rotation_x_degrees is None else rotation_x_degrees
        )
        resolved_rotation_y_degrees = float(
            model.rotation_y if rotation_y_degrees is None else rotation_y_degrees
        )
        resolved_rotation_z_degrees = float(
            model.rotation_z if rotation_z_degrees is None else rotation_z_degrees
        )
        resolved_model_z_offset_mils = (
            float(model.z_offset) / 10000.0
            if standoff_height_mils is None
            else float(standoff_height_mils)
        )

        inferred_body_standoff_mils = 0.0
        needs_inferred_bounds = bounds_mils is None and projection_outline_mils is None
        needs_inferred_height = overall_height_mils is None
        if needs_inferred_bounds or needs_inferred_height:
            model_payload = getattr(model, "embedded_data", None)
            if not model_payload:
                raise ValueError(
                    "Cannot infer STEP model bounds/height because the model "
                    "does not carry uncompressed embedded STEP bytes. Pass "
                    "bounds_mils/projection_outline_mils and overall_height_mils "
                    "explicitly, or use a model returned by add_embedded_model(...)."
                )
            inferred = compute_step_model_bounds_mils(
                bytes(model_payload),
                filename_hint=str(getattr(model, "name", "") or "model.step"),
                rotation_x_degrees=resolved_rotation_x_degrees,
                rotation_y_degrees=resolved_rotation_y_degrees,
                rotation_z_degrees=resolved_rotation_z_degrees,
                location_mils=(location_x_mils, location_y_mils),
                z_offset_mils=resolved_model_z_offset_mils,
            )
            if needs_inferred_bounds:
                bounds_mils = inferred.bounds_mils
            if needs_inferred_height:
                overall_height_mils = inferred.overall_height_mils
            inferred_body_standoff_mils = inferred.min_z_mils

        assert overall_height_mils is not None

        if bounds_mils is not None:
            left_mils, bottom_mils, right_mils, top_mils = _coerce_pcbdoc_bounds_mils(
                bounds_mils, "bounds_mils"
            )
            outline_points_mils = [
                (left_mils, bottom_mils),
                (right_mils, bottom_mils),
                (right_mils, top_mils),
                (left_mils, top_mils),
            ]
        else:
            assert projection_outline_mils is not None
            outline_points_mils = [
                _coerce_pcbdoc_point_mils(point, "projection_outline_mils vertex")
                for point in projection_outline_mils
            ]
            if len(outline_points_mils) < 3:
                raise ValueError("projection_outline_mils requires at least 3 points")

        return self.add_component_body(
            outline_points_mils=outline_points_mils,
            layer=layer,
            overall_height_mils=overall_height_mils,
            standoff_height_mils=inferred_body_standoff_mils,
            body_projection=side,
            model=model,
            model_2d_mils=(location_x_mils, location_y_mils),
            model_2d_rotation_degrees=0.0,
            model_3d_rotx_degrees=resolved_rotation_x_degrees,
            model_3d_roty_degrees=resolved_rotation_y_degrees,
            model_3d_rotz_degrees=resolved_rotation_z_degrees,
            model_3d_dz_mils=resolved_model_z_offset_mils,
            identifier=identifier,
            name=name,
            body_opacity_3d=opacity,
        )

    def add_component(
        self,
        *,
        designator: str,
        footprint: str,
        position_mils: PcbDocPointMils,
        layer: str | PcbLayer | int = "TOP",
        rotation_degrees: float = 0.0,
        source_footprint_library: str | Path = "",
        name_on: bool = True,
        comment_on: bool = False,
        name_auto_position: int = 1,
        comment_auto_position: int = 3,
        description: str = "",
        parameters: dict[str, str] | None = None,
        unique_id: str | None = None,
    ) -> AltiumPcbComponent:
        """
        Add a component placement record without placing footprint primitives.

        Use `add_component_from_pcblib(...)` for the common workflow where the
        footprint pads, tracks, text, and models should be placed with the
        component.

        Args:
            designator: Component designator, for example `"U1"` or `"R10"`.
            footprint: Footprint pattern name stored on the component.
            position_mils: Component origin as `(x_mils, y_mils)`.
            layer: Placement layer/side, commonly `PcbLayer.TOP`,
                `PcbLayer.BOTTOM`, `"TOP"`, or `"BOTTOM"`.
            rotation_degrees: Component rotation in degrees.
            source_footprint_library: Optional source PcbLib path/name metadata.
            name_on: Whether the designator text is visible.
            comment_on: Whether the comment/value text is visible.
            name_auto_position: Native designator auto-position code.
            comment_auto_position: Native comment auto-position code.
            description: Optional component description.
            parameters: Optional component parameter dictionary.
            unique_id: Optional native component unique ID. Generated when
                omitted by the underlying authoring flow.

        Returns:
            The authored `AltiumPcbComponent` placement record.
        """
        builder = self._ensure_authoring_builder()
        component_index = builder.add_component(
            designator=designator,
            footprint=footprint,
            position_mils=_coerce_pcbdoc_point_mils(position_mils, "position_mils"),
            layer=layer,
            rotation_degrees=rotation_degrees,
            source_footprint_library=str(source_footprint_library),
            name_on=name_on,
            comment_on=comment_on,
            name_auto_position=name_auto_position,
            comment_auto_position=comment_auto_position,
            description=description,
            parameters=parameters,
            unique_id=unique_id,
        )
        self._mirror_authoring_builder_state()
        return self.components[component_index]

    def add_component_from_pcblib(
        self,
        footprint: object,
        *,
        designator: str,
        position_mils: PcbDocPointMils,
        layer: str | PcbLayer | int = "TOP",
        rotation_degrees: float = 0.0,
        source_footprint_library: str | Path = "",
        comment_text: str | None = None,
        comment_visible: bool = False,
        component_parameters: dict[str, str] | None = None,
        pad_nets: dict[str, str] | None = None,
        source_pcblib: object | None = None,
    ) -> AltiumPcbComponent:
        """
        Place a PcbLib footprint and return the live component record.

        Args:
            footprint: `AltiumPcbFootprint` to place.
            designator: Component designator.
            position_mils: Component origin as `(x_mils, y_mils)`.
            layer: Placement side/layer, commonly `"TOP"` or `"BOTTOM"`.
            rotation_degrees: Component rotation in degrees.
            source_footprint_library: Optional source library path string.
            comment_text: Optional comment/value text to place.
            comment_visible: Whether the placed comment text is visible.
            component_parameters: Optional component parameter dictionary.
            pad_nets: Optional mapping from pad designator to net name.
            source_pcblib: Optional source library object for embedded model copy.

        Returns:
            The authored `AltiumPcbComponent` placement record.
        """
        if not source_footprint_library and source_pcblib is not None:
            library_path = getattr(source_pcblib, "filepath", None)
            if library_path:
                source_footprint_library = str(library_path)

        builder = self._ensure_authoring_builder()
        component_index = builder.place_footprint(
            footprint,
            designator=designator,
            position_mils=_coerce_pcbdoc_point_mils(position_mils, "position_mils"),
            layer=layer,
            rotation_degrees=rotation_degrees,
            source_footprint_library=str(source_footprint_library),
            comment_text=comment_text,
            comment_visible=comment_visible,
            component_parameters=component_parameters,
            pad_nets=pad_nets,
            source_pcblib=source_pcblib,
        )
        self._mirror_authoring_builder_state()
        return self.components[component_index]

    def add_track(
        self,
        start_mils: PcbDocPointMils,
        end_mils: PcbDocPointMils,
        *,
        width_mils: float,
        layer: int | str | PcbLayer = "Top Layer",
        net: str | None = None,
    ) -> AltiumPcbTrack:
        """
        Add a track segment using mil-unit endpoints and width.

        Args:
            start_mils: Track start as `(x_mils, y_mils)`.
            end_mils: Track end as `(x_mils, y_mils)`.
            width_mils: Track width in mils.
            layer: `PcbLayer`, native layer id, or supported layer name.
            net: Optional net name. The net is created if needed.

        Returns:
            The authored `AltiumPcbTrack` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_track(
            _coerce_pcbdoc_point_mils(start_mils, "start_mils"),
            _coerce_pcbdoc_point_mils(end_mils, "end_mils"),
            width_mils=width_mils,
            layer=layer,
            net=net,
        )
        self._mirror_authoring_builder_state()
        return self.tracks[-1]

    def add_arc(
        self,
        *,
        center_mils: PcbDocPointMils,
        radius_mils: float,
        start_angle_degrees: float,
        end_angle_degrees: float,
        width_mils: float,
        layer: int | str | PcbLayer = "Top Layer",
        net: str | None = None,
    ) -> AltiumPcbArc:
        """
        Add a circular arc using mil units and degree angles.

        Args:
            center_mils: Arc center as `(x_mils, y_mils)`.
            radius_mils: Arc radius in mils.
            start_angle_degrees: Start angle in degrees.
            end_angle_degrees: End angle in degrees.
            width_mils: Arc stroke width in mils.
            layer: `PcbLayer`, native layer id, or supported layer name.
            net: Optional net name. The net is created if needed.

        Returns:
            The authored `AltiumPcbArc` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_arc(
            center_mils=_coerce_pcbdoc_point_mils(center_mils, "center_mils"),
            radius_mils=radius_mils,
            start_angle=start_angle_degrees,
            end_angle=end_angle_degrees,
            width_mils=width_mils,
            layer=layer,
            net=net,
        )
        self._mirror_authoring_builder_state()
        return self.arcs[-1]

    def add_fill(
        self,
        corner1_mils: PcbDocPointMils,
        corner2_mils: PcbDocPointMils,
        *,
        rotation_degrees: float = 0.0,
        layer: int | str | PcbLayer = "Top Layer",
        net: str | None = None,
    ) -> AltiumPcbFill:
        """
        Add a rectangular fill using opposite mil-unit corners.

        Args:
            corner1_mils: First fill corner as `(x_mils, y_mils)`.
            corner2_mils: Opposite fill corner as `(x_mils, y_mils)`.
            rotation_degrees: Fill rotation in degrees.
            layer: `PcbLayer`, native layer id, or supported layer name.
            net: Optional net name. The net is created if needed.

        Returns:
            The authored `AltiumPcbFill` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_fill(
            _coerce_pcbdoc_point_mils(corner1_mils, "corner1_mils"),
            _coerce_pcbdoc_point_mils(corner2_mils, "corner2_mils"),
            rotation_degrees=rotation_degrees,
            layer=layer,
            net=net,
        )
        self._mirror_authoring_builder_state()
        return self.fills[-1]

    def add_text(
        self,
        *,
        text: str,
        position_mils: PcbDocPointMils,
        height_mils: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        rotation_degrees: float = 0.0,
        stroke_width_mils: float = 10.0,
        font_kind: str | PcbTextKind = PcbTextKind.STROKE,
        font_name: str = "Arial",
        bold: bool = False,
        italic: bool = False,
        is_comment: bool = False,
        is_designator: bool = False,
        is_mirrored: bool = False,
        is_inverted: bool = False,
        inverted_margin_mils: float = 0.0,
        use_inverted_rectangle: bool = False,
        inverted_rectangle_size_mils: tuple[float, float] | None = None,
        is_frame: bool = False,
        frame_size_mils: tuple[float, float] | None = None,
        text_justification: int | PcbTextJustification | None = None,
        barcode_kind: int | PcbBarcodeKind = PcbBarcodeKind.CODE_39,
        barcode_render_mode: int | PcbBarcodeRenderMode = (
            PcbBarcodeRenderMode.BY_FULL_WIDTH
        ),
        barcode_full_size_mils: tuple[float, float] | None = None,
        barcode_margin_mils: tuple[float, float] = (
            PCB_TEXT_BARCODE_MARGIN_MILS,
            PCB_TEXT_BARCODE_MARGIN_MILS,
        ),
        barcode_min_width_mils: float = 0.0,
        barcode_show_text: bool = True,
        barcode_inverted: bool = True,
    ) -> AltiumPcbText:
        """
        Add PCB text using mil units.

        `font_kind` accepts `"stroke"`, `"truetype"`, or `"barcode"`.
        Barcode sizing and inverted-text margins are also in mils.
        `is_frame=True` creates Altium multiline text and requires
        `frame_size_mils=(width_mils, height_mils)`.

        Args:
            text: Text content. Use CRLF (`\\r\\n`) line breaks for text
                frames.
            position_mils: Text anchor position as `(x_mils, y_mils)`.
            height_mils: Text height in mils.
            layer: `PcbLayer` or native layer id.
            rotation_degrees: Text rotation in degrees.
            stroke_width_mils: Stroke font line width in mils.
            font_kind: `PcbTextKind` or equivalent string.
            font_name: TrueType or stroke font family name.
            bold: Enable bold style for TrueType text.
            italic: Enable italic style for TrueType text.
            is_comment: Mark as component comment/value text.
            is_designator: Mark as component designator text.
            is_mirrored: Mirror text geometry.
            is_inverted: Enable inverted text rendering.
            inverted_margin_mils: Inverted text margin in mils.
            use_inverted_rectangle: Use an explicit inverted rectangle instead
                of deriving the box from text extents.
            inverted_rectangle_size_mils: Optional inverted rectangle
                `(width_mils, height_mils)`.
            is_frame: Create multiline text-frame text.
            frame_size_mils: Text-frame `(width_mils, height_mils)`.
            text_justification: Optional `PcbTextJustification` for framed or
                inverted text.
            barcode_kind: `PcbBarcodeKind` symbology for barcode text.
            barcode_render_mode: `PcbBarcodeRenderMode` sizing mode.
            barcode_full_size_mils: Barcode full `(width_mils, height_mils)`.
            barcode_margin_mils: Barcode margin `(x_mils, y_mils)`.
            barcode_min_width_mils: Minimum barcode bar width in mils.
            barcode_show_text: Show human-readable barcode text.
            barcode_inverted: Render inverted barcode foreground/background.

        Returns:
            The authored `AltiumPcbText` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_text(
            text=text,
            position_mils=_coerce_pcbdoc_point_mils(position_mils, "position_mils"),
            height_mils=height_mils,
            layer=layer,
            rotation_degrees=rotation_degrees,
            stroke_width_mils=stroke_width_mils,
            font_kind=font_kind,
            font_name=font_name,
            bold=bold,
            italic=italic,
            is_comment=is_comment,
            is_designator=is_designator,
            is_mirrored=is_mirrored,
            is_inverted=is_inverted,
            inverted_margin_mils=inverted_margin_mils,
            use_inverted_rectangle=use_inverted_rectangle,
            inverted_rectangle_size_mils=inverted_rectangle_size_mils,
            is_frame=is_frame,
            frame_size_mils=frame_size_mils,
            text_justification=text_justification,
            barcode_kind=barcode_kind,
            barcode_render_mode=barcode_render_mode,
            barcode_full_size_mils=barcode_full_size_mils,
            barcode_margin_mils=barcode_margin_mils,
            barcode_min_width_mils=barcode_min_width_mils,
            barcode_show_text=barcode_show_text,
            barcode_inverted=barcode_inverted,
        )
        self._mirror_authoring_builder_state()
        return self.texts[-1]

    def add_pad(
        self,
        *,
        designator: str,
        position_mils: PcbDocPointMils,
        width_mils: float,
        height_mils: float,
        layer: int | PcbLayer = PcbLayer.TOP,
        shape: int | PadShape = PadShape.RECTANGLE,
        rotation_degrees: float = 0.0,
        hole_size_mils: float = 0.0,
        plated: bool | None = None,
        net: str | None = None,
        corner_radius_percent: int | None = None,
        slot_length_mils: float = 0.0,
        slot_rotation_degrees: float = 0.0,
        solder_mask_expansion_mils: float | None = None,
        paste_mask_expansion_mils: float | None = None,
    ) -> AltiumPcbPad:
        """
        Add a pad using mil-unit center and size.

        `shape=PadShape.ROUNDED_RECTANGLE` writes Altium's native
        alternate-shape encoding; use `corner_radius_percent` to override the
        default 50% corner radius. `slot_length_mils` creates a slotted hole
        and requires `hole_size_mils`.

        `solder_mask_expansion_mils` and `paste_mask_expansion_mils` set manual
        mask expansion values when provided. Pass `0.0` for explicit zero
        expansion.

        Args:
            designator: Pad designator text, for example `"1"`.
            position_mils: Pad center as `(x_mils, y_mils)`.
            width_mils: Pad X size in mils.
            height_mils: Pad Y size in mils.
            layer: `PcbLayer` or native layer id. Use `PcbLayer.MULTI_LAYER`
                for through-hole pads.
            shape: `PadShape` or native pad shape id.
            rotation_degrees: Pad rotation in degrees.
            hole_size_mils: Drill diameter in mils. Use 0 for SMT pads.
            plated: Optional plated-through flag. Defaults to the native layer
                convention when omitted.
            net: Optional net name. The net is created if needed.
            corner_radius_percent: Rounded-rectangle corner radius percentage.
            slot_length_mils: Optional total slot length in mils.
            slot_rotation_degrees: Slot rotation in degrees.
            solder_mask_expansion_mils: Optional manual solder-mask expansion
                in mils.
            paste_mask_expansion_mils: Optional manual paste-mask expansion in
                mils.

        Returns:
            The authored `AltiumPcbPad` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_pad(
            designator=designator,
            position_mils=_coerce_pcbdoc_point_mils(position_mils, "position_mils"),
            width_mils=width_mils,
            height_mils=height_mils,
            layer=layer,
            shape=int(shape),
            rotation_degrees=rotation_degrees,
            hole_size_mils=hole_size_mils,
            plated=plated,
            net=net,
            corner_radius_percent=corner_radius_percent,
            slot_length_mils=slot_length_mils,
            slot_rotation_degrees=slot_rotation_degrees,
            solder_mask_expansion_mils=solder_mask_expansion_mils,
            paste_mask_expansion_mils=paste_mask_expansion_mils,
        )
        self._mirror_authoring_builder_state()
        return self.pads[-1]

    def add_via(
        self,
        *,
        position_mils: PcbDocPointMils,
        diameter_mils: float,
        hole_size_mils: float,
        layer_start: int | PcbLayer = PcbLayer.TOP,
        layer_end: int | PcbLayer = PcbLayer.BOTTOM,
        net: str | None = None,
    ) -> AltiumPcbVia:
        """
        Add a via using mil-unit center, diameter, and hole size.

        Args:
            position_mils: Via center as `(x_mils, y_mils)`.
            diameter_mils: Via pad diameter in mils.
            hole_size_mils: Via drill diameter in mils.
            layer_start: Start layer as `PcbLayer` or native layer id.
            layer_end: End layer as `PcbLayer` or native layer id.
            net: Optional net name. The net is created if needed.

        Returns:
            The authored `AltiumPcbVia` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_via(
            position_mils=_coerce_pcbdoc_point_mils(position_mils, "position_mils"),
            diameter_mils=diameter_mils,
            hole_size_mils=hole_size_mils,
            layer_start=layer_start,
            layer_end=layer_end,
            net=net,
        )
        self._mirror_authoring_builder_state()
        return self.vias[-1]

    def add_region(
        self,
        *,
        outline_points_mils: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.TOP,
        hole_points_mils: list[list[tuple[float, float]]] | None = None,
        is_keepout: bool = False,
        keepout_restrictions: int = 0,
        net: str | None = None,
    ) -> AltiumPcbRegion:
        """
        Add a region polygon using mil-unit vertices.

        Args:
            outline_points_mils: Outer polygon vertices in mils.
            layer: `PcbLayer` or native layer id.
            hole_points_mils: Optional list of hole polygons in mils.
            is_keepout: Mark the region as a keepout.
            keepout_restrictions: Native keepout restriction bitmask.
            net: Optional net name. The net is created if needed.

        Returns:
            The authored `AltiumPcbRegion` record.
        """
        builder = self._ensure_authoring_builder()
        builder.add_region(
            outline_points_mils=outline_points_mils,
            layer=layer,
            hole_points_mils=hole_points_mils,
            is_keepout=is_keepout,
            keepout_restrictions=keepout_restrictions,
            net=net,
        )
        self._mirror_authoring_builder_state()
        return self.regions[-1]

    @classmethod
    def from_file(
        cls, filepath: Path, verbose: bool = False, parse_geometry: bool = True
    ) -> "AltiumPcbDoc":
        """
        Parse a PcbDoc file.

        Args:
            filepath: Path to .PcbDoc file
            verbose: If True, print detailed parsing info
            parse_geometry: Reserved compatibility flag. Geometry parsing is
                currently always enabled.

        Returns:
            AltiumPcbDoc instance

        Raises:
            FileNotFoundError: If file doesn't exist
            Exception: If parsing fails
        """
        filepath = Path(filepath).resolve()

        if not filepath.exists():
            raise FileNotFoundError(f"PcbDoc file not found: {filepath}")

        if verbose:
            log.info(f"Parsing PcbDoc: {filepath.name}")

        pcbdoc = cls()
        pcbdoc.filepath = filepath
        pcbdoc._parse(verbose=verbose)

        if verbose:
            log.info(
                f"Parsed {len(pcbdoc.components)} components, {len(pcbdoc.nets)} nets"
            )

        return pcbdoc

    def _parse(self, verbose: bool = False) -> None:
        """
        Parse PcbDoc file streams.

        Args:
            verbose: If True, print parsing progress
        """
        ole = AltiumOleFile(str(self.filepath))

        try:
            string_table, designator_map = self._parse_text_lookup_tables(
                ole, verbose=verbose
            )
            self._parse_board_metadata(ole, verbose=verbose)
            parameter_map = self._parse_component_parameter_map(ole, verbose=verbose)
            self._parse_component_and_text_record_streams(
                ole,
                designator_map=designator_map,
                parameter_map=parameter_map,
                verbose=verbose,
            )
            self._parse_additional_metadata_streams(ole, verbose=verbose)
            self._parse_binary_primitive_streams(
                ole, string_table=string_table, verbose=verbose
            )
            self._finalize_parsed_board_geometry(verbose=verbose)

            resolve_pcbdoc_custom_pad_shapes(self)
            if verbose:
                log.info("  Storing unparsed streams for passthrough...")
            self._store_raw_streams(ole, verbose=verbose)

        finally:
            ole.close()

    def _parse_text_lookup_tables(
        self,
        ole: AltiumOleFile,
        *,
        verbose: bool,
    ) -> tuple[dict[int, str], dict[int, str]]:
        """
        Parse lookup tables used by binary text/designator streams.
        """
        if verbose:
            log.info("  Parsing WideStrings6/Data...")
        string_table = parse_widestrings6(ole, verbose=verbose)
        self.widestrings_table = dict(string_table)
        if verbose:
            log.info(f"    Loaded {len(string_table)} strings")

        if verbose:
            log.info("  Parsing Texts6/Data...")
        designator_map = parse_texts6_designators(ole, string_table, verbose=verbose)
        if verbose:
            log.info(f"    Found designators for {len(designator_map)} components")
        return dict(string_table), designator_map

    def _parse_board_metadata(self, ole: AltiumOleFile, *, verbose: bool) -> None:
        """
        Parse board-level metadata from Board6/Data.
        """
        if not ole.exists(["Board6", "Data"]):
            return
        if verbose:
            log.info("  Parsing Board6/Data...")
        board_records = get_records_in_section(ole, "Board6/Data")
        if not board_records:
            return
        self.board = AltiumBoard.from_record(board_records[0])
        if verbose:
            log.info(
                f"    Board origin: ({self.board.origin_x}, {self.board.origin_y}) mils"
            )

    def _parse_component_parameter_map(
        self,
        ole: AltiumOleFile,
        *,
        verbose: bool,
    ) -> dict[str, dict[str, str]]:
        """
        Parse PrimitiveParameters/Data into a UNIQUEID-keyed parameter map.
        """
        parameter_map: dict[str, dict[str, str]] = {}
        if not ole.exists(["PrimitiveParameters", "Data"]):
            return parameter_map
        if verbose:
            log.info("  Parsing PrimitiveParameters/Data...")
        try:
            param_records = get_records_in_section(ole, "PrimitiveParameters/Data")
            current_uid: str | None = None
            for record in param_records:
                if "PRIMITIVEID" in record:
                    current_uid = record["PRIMITIVEID"]
                    if "COUNT" in record and current_uid not in parameter_map:
                        parameter_map[current_uid] = {}
                    continue
                if "NAME" in record and "VALUE" in record and current_uid:
                    parameter_map.setdefault(current_uid, {})[record["NAME"]] = record[
                        "VALUE"
                    ]
            if verbose:
                log.info(f"    Found parameters for {len(parameter_map)} components")
        except Exception as exc:
            if verbose:
                log.warning(f"    Error parsing PrimitiveParameters/Data: {exc}")
        return parameter_map

    def _parse_components_stream(
        self,
        ole: AltiumOleFile,
        *,
        designator_map: dict[int, str],
        parameter_map: dict[str, dict[str, str]],
        verbose: bool,
    ) -> None:
        """
        Parse Components6/Data into component objects.
        """
        if not ole.exists(["Components6", "Data"]):
            return
        if verbose:
            log.info("  Parsing Components6/Data...")
        component_records = get_records_in_section(ole, "Components6/Data")
        for index, record in enumerate(component_records):
            unique_id = record.get("UNIQUEID", "")
            self.components.append(
                AltiumPcbComponent(
                    designator=designator_map.get(
                        index, record.get("SOURCEDESIGNATOR", "")
                    ),
                    footprint=record.get("PATTERN", ""),
                    layer=record.get("LAYER", ""),
                    x=record.get("X", ""),
                    y=record.get("Y", ""),
                    rotation=record.get("ROTATION", ""),
                    unique_id=unique_id,
                    description=record.get("SOURCEDESCRIPTION", ""),
                    parameters=parameter_map.get(unique_id, {}),
                    raw_record=record,
                    component_kind=parse_component_kind(record),
                )
            )
        if verbose:
            log.info(f"    Found {len(self.components)} components")

    def _parse_optional_record_collection(
        self,
        ole: AltiumOleFile,
        *,
        section_name: str,
        label: str,
        record_factory: Any,
        target: list[Any],
        verbose: bool,
    ) -> None:
        """
        Parse a text-record collection stream into a target list.
        """
        stream_parts = section_name.split("/")
        if not ole.exists(stream_parts):
            return
        if verbose:
            log.info(f"  Parsing {section_name}...")
        try:
            for record in get_records_in_section(ole, section_name):
                target.append(record_factory(record))
            if verbose:
                log.info(f"    Found {len(target)} {label}")
        except Exception as exc:
            if verbose:
                log.warning(f"    Error parsing {section_name}: {exc}")

    def _parse_rules_stream(self, ole: AltiumOleFile, *, verbose: bool) -> None:
        """
        Parse Rules6/Data into PCB rule objects.
        """
        if not ole.exists(["Rules6", "Data"]):
            return
        if verbose:
            log.info("  Parsing Rules6/Data...")
        try:
            rules_data = ole.openstream(["Rules6", "Data"])
            for index, (record, leader, payload) in enumerate(
                self._iter_rules6_records(rules_data)
            ):
                self.rules.append(
                    AltiumPcbRule.from_record(
                        record,
                        index=index,
                        record_leader=leader,
                        record_payload=payload,
                    )
                )
            if verbose:
                log.info(f"    Found {len(self.rules)} rules")
        except Exception as exc:
            if verbose:
                log.warning(f"    Error parsing Rules6/Data: {exc}")

    def _parse_dimensions_stream(self, ole: AltiumOleFile, *, verbose: bool) -> None:
        """
        Parse Dimensions6/Data into PCB dimension objects.
        """
        if not ole.exists(["Dimensions6", "Data"]):
            return
        if verbose:
            log.info("  Parsing Dimensions6/Data...")
        try:
            dimensions_data = ole.openstream(["Dimensions6", "Data"])
            self.dimensions = parse_dimensions6_stream(dimensions_data)
            if verbose:
                log.info(f"    Found {len(self.dimensions)} dimensions")
        except Exception as exc:
            if verbose:
                log.warning(f"    Error parsing Dimensions6/Data: {exc}")

    def _parse_extended_primitive_information_stream(
        self,
        ole: AltiumOleFile,
        *,
        verbose: bool,
    ) -> None:
        """
        Parse ExtendedPrimitiveInformation/Data.
        """
        if not ole.exists(["ExtendedPrimitiveInformation", "Data"]):
            return
        if verbose:
            log.info("  Parsing ExtendedPrimitiveInformation/Data...")
        try:
            extended_info_data = ole.openstream(
                ["ExtendedPrimitiveInformation", "Data"]
            )
            self.extended_primitive_information = (
                parse_extended_primitive_information_stream(extended_info_data)
            )
            if verbose:
                log.info(
                    "    Found %d extended primitive information records",
                    len(self.extended_primitive_information),
                )
        except Exception as exc:
            if verbose:
                log.warning(
                    f"    Error parsing ExtendedPrimitiveInformation/Data: {exc}"
                )

    def _parse_custom_shapes_streams(
        self, ole: AltiumOleFile, *, verbose: bool
    ) -> None:
        """
        Parse CustomShapes header/data streams when present.
        """
        if ole.exists(["CustomShapes", "Header"]):
            self.raw_custom_shapes_header = ole.openstream(["CustomShapes", "Header"])

        if not ole.exists(["CustomShapes", "Data"]):
            return
        if verbose:
            log.info("  Parsing CustomShapes/Data...")
        try:
            self.raw_custom_shapes_data = ole.openstream(["CustomShapes", "Data"])
            self.custom_shapes = parse_custom_shapes_stream(self.raw_custom_shapes_data)
            if verbose:
                log.info("    Found %d custom shape record(s)", len(self.custom_shapes))
        except Exception as exc:
            if verbose:
                log.warning(f"    Error parsing CustomShapes/Data: {exc}")

    def _parse_component_and_text_record_streams(
        self,
        ole: AltiumOleFile,
        *,
        designator_map: dict[int, str],
        parameter_map: dict[str, dict[str, str]],
        verbose: bool,
    ) -> None:
        """
        Parse text-based component, net, class, and polygon streams.
        """
        self._parse_components_stream(
            ole,
            designator_map=designator_map,
            parameter_map=parameter_map,
            verbose=verbose,
        )
        self._parse_optional_record_collection(
            ole,
            section_name="Nets6/Data",
            label="nets",
            record_factory=AltiumPcbNet.from_record,
            target=self.nets,
            verbose=verbose,
        )
        self._parse_optional_record_collection(
            ole,
            section_name="Classes6/Data",
            label="net classes",
            record_factory=AltiumPcbNetClass.from_record,
            target=self.net_classes,
            verbose=verbose,
        )
        self._parse_optional_record_collection(
            ole,
            section_name="Polygons6/Data",
            label="polygon pours",
            record_factory=AltiumPcbPolygon.from_record,
            target=self.polygons,
            verbose=verbose,
        )

    def _parse_additional_metadata_streams(
        self, ole: AltiumOleFile, *, verbose: bool
    ) -> None:
        """
        Parse additional metadata/configuration streams that are not primitive binaries.
        """
        self._parse_rules_stream(ole, verbose=verbose)
        self._parse_dimensions_stream(ole, verbose=verbose)
        self._parse_extended_primitive_information_stream(ole, verbose=verbose)
        self._parse_custom_shapes_streams(ole, verbose=verbose)

    def _parse_binary_primitive_streams(
        self,
        ole: AltiumOleFile,
        *,
        string_table: dict[int, str],
        verbose: bool,
    ) -> None:
        """
        Parse binary primitive and embedded-resource streams.
        """
        stream_parsers = (
            (
                "Tracks6/Data",
                "  Parsing Tracks6/Data (binary)...",
                lambda: self._parse_tracks6(ole, verbose=verbose),
            ),
            (
                "Arcs6/Data",
                "  Parsing Arcs6/Data (binary)...",
                lambda: self._parse_arcs6(ole, verbose=verbose),
            ),
            (
                "Pads6/Data",
                "  Parsing Pads6/Data (binary)...",
                lambda: self._parse_pads6(ole, verbose=verbose),
            ),
            (
                "Vias6/Data",
                "  Parsing Vias6/Data (binary)...",
                lambda: self._parse_vias6(ole, verbose=verbose),
            ),
            (
                "Texts6/Data",
                "  Parsing Texts6/Data (binary)...",
                lambda: self._parse_texts6(ole, string_table, verbose=verbose),
            ),
            (
                "Fills6/Data",
                "  Parsing Fills6/Data (binary)...",
                lambda: self._parse_fills6(ole, verbose=verbose),
            ),
            (
                "Regions6/Data",
                "  Parsing Regions6/Data (binary)...",
                lambda: self._parse_regions6(ole, verbose=verbose),
            ),
            (
                "BoardRegions/Data",
                "  Parsing BoardRegions/Data (binary board regions)...",
                lambda: self._parse_boardregions6(ole, verbose=verbose),
            ),
            (
                "ShapeBasedRegions6/Data",
                "  Parsing ShapeBasedRegions6/Data (binary rendered geometry)...",
                lambda: self._parse_shapebasedregions6(ole, verbose=verbose),
            ),
            (
                "ComponentBodies6/Data",
                "  Parsing ComponentBodies6/Data (3D extruded shapes)...",
                lambda: self._parse_componentbodies6(ole, verbose=verbose),
            ),
            (
                "ShapeBasedComponentBodies6/Data",
                "  Parsing ShapeBasedComponentBodies6/Data (rendered 3D shapes)...",
                lambda: self._parse_shapebasedcomponentbodies6(ole, verbose=verbose),
            ),
        )
        for section_name, message, parser in stream_parsers:
            if not ole.exists(section_name.split("/")):
                continue
            if verbose:
                log.info(message)
            parser()

        if ole.exists(["ModelsNoEmbed", "Data"]) or ole.exists(["Models", "Data"]):
            if verbose:
                log.info("  Checking ModelsNoEmbed/Data and Models/Data...")
            self._parse_models(ole, verbose=verbose)

        if ole.exists(["EmbeddedFonts6", "Data"]):
            if verbose:
                log.info("  Parsing EmbeddedFonts6/Data...")
            self._parse_embedded_fonts(ole, verbose=verbose)

    def _finalize_parsed_board_geometry(self, *, verbose: bool) -> None:
        """
        Finalize board outline fallback/cutout state after primitive parsing.
        """
        if self.board is None:
            return
        if self.board.outline and self.board.outline.vertex_count > 0:
            if verbose:
                log.info(
                    f"  Board outline: {self.board.outline.vertex_count} vertices from Board6/Data"
                )
        else:
            if verbose:
                log.info("  Board outline: falling back to keepout tracks/arcs...")
            self.board.extract_outline_from_primitives(
                self.tracks, self.regions, self.arcs
            )
        if self.board.outline is None:
            return
        self.board.outline.cutouts = self._collect_board_outline_cutouts()
        if verbose and self.board.outline.cutouts:
            log.info(
                f"  Board outline cutouts: {len(self.board.outline.cutouts)} "
                "(from ShapeBasedRegions6/Regions6)"
            )

    @staticmethod
    def _iter_rules6_records(raw: bytes) -> list[tuple[dict[str, str], bytes, bytes]]:
        """
        Parse native Rules6/Data records: 2-byte leader + 4-byte length + text.
        """
        records: list[tuple[dict[str, str], bytes, bytes]] = []
        pos = 0
        while pos + 6 <= len(raw):
            leader = raw[pos : pos + 2]
            length = int.from_bytes(raw[pos + 2 : pos + 6], byteorder="little")
            pos += 6
            if length <= 0 or pos + length > len(raw):
                break
            raw_payload = raw[pos : pos + length]
            record_bytes = raw_payload
            pos += length
            if record_bytes and record_bytes[-1] == 0:
                record_bytes = record_bytes[:-1]
            fields: dict[str, str] = {}
            for pair in parse_byte_record(record_bytes):
                text = decode_byte_array(pair)
                if "=" not in text:
                    continue
                key, value = text.split("=", 1)
                key_text = str(key or "").strip().upper()
                if key_text:
                    fields[key_text] = value
            if "RULEKIND" in fields:
                records.append((fields, leader, raw_payload))
        return records

    @staticmethod
    def _parse_rules6_records(raw: bytes) -> list[dict[str, str]]:
        return [record for record, _, _ in AltiumPcbDoc._iter_rules6_records(raw)]

    @staticmethod
    def _is_board_cutout_region(region: object) -> bool:
        """
        Return True when a REGION/ShapeBasedRegion represents a board cutout.
        """
        props = dict(getattr(region, "properties", {}) or {})
        is_prop = str(props.get("ISBOARDCUTOUT", "")).strip("\x00").upper() == "TRUE"
        is_flag = bool(getattr(region, "is_board_cutout", False))
        kind = getattr(region, "kind", None)

        kind_value: int | None = None
        try:
            if kind is not None:
                kind_value = int(getattr(kind, "value", kind))
        except (TypeError, ValueError):
            kind_value = None

        is_legacy_region_cutout = (
            isinstance(region, AltiumPcbRegion) and kind_value == 3
        )
        is_shapebased_cutout = (
            isinstance(region, AltiumPcbShapeBasedRegion) and kind_value == 1
        )
        return is_prop or is_flag or is_legacy_region_cutout or is_shapebased_cutout

    @staticmethod
    def _region_cutout_vertices(region: object) -> list["BoardOutlineVertex"]:
        """
        Project REGION/ShapeBasedRegion cutout geometry into board-outline vertices.
        """
        from .altium_board import BoardOutlineVertex

        if isinstance(region, AltiumPcbRegion):
            return [
                BoardOutlineVertex(x_mils=v.x_mils, y_mils=v.y_mils)
                for v in (region.outline_vertices or [])
            ]

        if isinstance(region, AltiumPcbShapeBasedRegion):
            outline = list(region.outline or [])
            if len(outline) >= 2:
                first = outline[0]
                last = outline[-1]
                if math.isclose(
                    first.x_mils, last.x_mils, abs_tol=1e-6
                ) and math.isclose(first.y_mils, last.y_mils, abs_tol=1e-6):
                    outline = outline[:-1]

            result: list[BoardOutlineVertex] = []
            for vertex in outline:
                radius_mils = float(getattr(vertex, "radius_mils", 0.0) or 0.0)
                result.append(
                    BoardOutlineVertex(
                        x_mils=float(vertex.x_mils),
                        y_mils=float(vertex.y_mils),
                        is_arc=bool(
                            getattr(vertex, "is_round", False) and radius_mils > 0.0
                        ),
                        center_x_mils=float(
                            getattr(vertex, "center_x_mils", 0.0) or 0.0
                        ),
                        center_y_mils=float(
                            getattr(vertex, "center_y_mils", 0.0) or 0.0
                        ),
                        radius_mils=radius_mils,
                        start_angle_deg=float(getattr(vertex, "start_angle", 0.0)),
                        end_angle_deg=float(getattr(vertex, "end_angle", 0.0)),
                    )
                )
            return result

        return []

    def _collect_board_outline_cutouts(self) -> list[list["BoardOutlineVertex"]]:
        """
        Collect board cutout contours, preferring ShapeBasedRegions arc geometry.
        """
        shapebased_cutouts: list[list["BoardOutlineVertex"]] = []
        for region in self.shapebased_regions:
            if not self._is_board_cutout_region(region):
                continue
            vertices = self._region_cutout_vertices(region)
            if vertices:
                shapebased_cutouts.append(vertices)

        if shapebased_cutouts:
            return shapebased_cutouts

        region_cutouts: list[list["BoardOutlineVertex"]] = []
        for region in self.regions:
            if not self._is_board_cutout_region(region):
                continue
            vertices = self._region_cutout_vertices(region)
            if vertices:
                region_cutouts.append(vertices)
        return region_cutouts

    def _parse_tracks6(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse Tracks6/Data stream (binary TRACK records).

        Args:
            ole: Open OLE file
            verbose: Enable verbose output
        """
        data = ole.openstream(["Tracks6", "Data"])

        offset = 0
        track_count = 0

        while offset < len(data) - 1:
            # Check for TRACK type byte (0x04)
            if data[offset] == 0x04:
                try:
                    track = AltiumPcbTrack()
                    bytes_consumed = track.parse_from_binary(data, offset)
                    self.tracks.append(track)
                    offset += bytes_consumed
                    track_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(f"Failed to parse TRACK at offset {offset}: {e}")
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {track_count} tracks")

    def _parse_arcs6(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse Arcs6/Data stream (binary ARC records).
        """
        data = ole.openstream(["Arcs6", "Data"])
        offset = 0
        arc_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x01:  # ARC type
                try:
                    arc = AltiumPcbArc()
                    bytes_consumed = arc.parse_from_binary(data, offset)
                    self.arcs.append(arc)
                    offset += bytes_consumed
                    arc_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(f"Failed to parse ARC at offset {offset}: {e}")
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {arc_count} arcs")

    def _parse_pads6(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse Pads6/Data stream (binary PAD records).
        """
        data = ole.openstream(["Pads6", "Data"])
        offset = 0
        pad_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x02:  # PAD type
                try:
                    pad = AltiumPcbPad()
                    bytes_consumed = pad.parse_from_binary(data, offset)
                    self.pads.append(pad)
                    offset += bytes_consumed
                    pad_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(f"Failed to parse PAD at offset {offset}: {e}")
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {pad_count} pads")

    def _parse_vias6(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse Vias6/Data stream (binary VIA records).
        """
        data = ole.openstream(["Vias6", "Data"])
        offset = 0
        via_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x03:  # VIA type
                try:
                    via = AltiumPcbVia()
                    bytes_consumed = via.parse_from_binary(data, offset)
                    self.vias.append(via)
                    offset += bytes_consumed
                    via_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(f"Failed to parse VIA at offset {offset}: {e}")
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {via_count} vias")

    def _parse_shapebasedregions6(
        self, ole: AltiumOleFile, verbose: bool = False
    ) -> None:
        """
        Parse ShapeBasedRegions6/Data stream (binary REGION records with rendered geometry).
        """
        data = ole.openstream(["ShapeBasedRegions6", "Data"])
        offset = 0
        region_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x0B:  # REGION type
                try:
                    region = AltiumPcbShapeBasedRegion()
                    bytes_consumed = region.parse_from_binary(data, offset)
                    self.shapebased_regions.append(region)
                    offset += bytes_consumed
                    region_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(
                            f"Failed to parse ShapeBasedRegion at offset {offset}: {e}"
                        )
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {region_count} shape-based regions")

    def _parse_componentbodies6(
        self, ole: AltiumOleFile, verbose: bool = False
    ) -> None:
        """
        Parse ComponentBodies6/Data stream (3D extruded component bodies).
        """
        data = ole.openstream(["ComponentBodies6", "Data"])
        offset = 0
        body_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x0C:  # COMPONENT_BODY type
                try:
                    body = AltiumPcbComponentBody()
                    body._force_extended_vertices = False
                    bytes_consumed = body.parse_from_binary(data, offset)
                    self.component_bodies.append(body)
                    offset += bytes_consumed
                    body_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(
                            f"Failed to parse ComponentBody at offset {offset}: {e}"
                        )
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {body_count} component bodies")

    def _parse_shapebasedcomponentbodies6(
        self, ole: AltiumOleFile, verbose: bool = False
    ) -> None:
        """
        Parse ShapeBasedComponentBodies6/Data stream (rendered 3D component bodies).
        """
        data = ole.openstream(["ShapeBasedComponentBodies6", "Data"])
        offset = 0
        body_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x0C:  # COMPONENT_BODY type
                try:
                    body = AltiumPcbComponentBody()
                    body._force_extended_vertices = True
                    bytes_consumed = body.parse_from_binary(data, offset)
                    self.shapebased_component_bodies.append(body)
                    offset += bytes_consumed
                    body_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(
                            f"Failed to parse ShapeBasedComponentBody at offset {offset}: {e}"
                        )
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {body_count} shape-based component bodies")

    def _parse_texts6(
        self, ole: AltiumOleFile, string_table: dict[int, str], verbose: bool = False
    ) -> None:
        """
        Parse Texts6/Data stream (binary TEXT records).
        """
        data = ole.openstream(["Texts6", "Data"])
        offset = 0
        text_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x05:  # TEXT type
                try:
                    text = AltiumPcbText()
                    bytes_consumed = text.parse_from_binary(data, offset)
                    text.resolve_text_content(string_table)
                    # Signature must reflect resolved text_content (WideStrings6),
                    # otherwise unchanged parsed TEXT records appear dirty and are
                    # needlessly reserialized.
                    text._raw_binary_signature = text._state_signature()
                    self.texts.append(text)
                    offset += bytes_consumed
                    text_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(f"Failed to parse TEXT at offset {offset}: {e}")
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {text_count} texts")

    def _parse_fills6(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse Fills6/Data stream (binary FILL records).
        """
        try:
            data = ole.openstream(["Fills6", "Data"])
        except Exception:
            if verbose:
                log.info("    No Fills6/Data stream found")
            return

        offset = 0
        fill_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x06:  # FILL type
                try:
                    fill = AltiumPcbFill()
                    bytes_consumed = fill.parse_from_binary(data, offset)
                    self.fills.append(fill)
                    offset += bytes_consumed
                    fill_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(f"Failed to parse FILL at offset {offset}: {e}")
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {fill_count} fills")

    def _parse_regions6(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse Regions6/Data stream (binary REGION records).
        """
        try:
            data = ole.openstream(["Regions6", "Data"])
        except Exception:
            if verbose:
                log.info("    No Regions6/Data stream found")
            return

        offset = 0
        region_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x0B:  # REGION type
                try:
                    region = AltiumPcbRegion()
                    bytes_consumed = region.parse_from_binary(data, offset)
                    self.regions.append(region)
                    offset += bytes_consumed
                    region_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(f"Failed to parse REGION at offset {offset}: {e}")
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {region_count} regions")

    def _parse_boardregions6(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse BoardRegions/Data stream (binary BoardRegion records).
        """
        try:
            data = ole.openstream(["BoardRegions", "Data"])
        except Exception:
            if verbose:
                log.info("    No BoardRegions/Data stream found")
            return

        offset = 0
        board_region_count = 0

        while offset < len(data) - 1:
            if data[offset] == 0x0B:
                try:
                    board_region = AltiumPcbBoardRegion()
                    bytes_consumed = board_region.parse_from_binary(data, offset)
                    self.board_regions.append(board_region)
                    offset += bytes_consumed
                    board_region_count += 1
                except Exception as e:
                    if verbose:
                        log.warning(
                            f"Failed to parse BoardRegion at offset {offset}: {e}"
                        )
                    offset += 1
            else:
                offset += 1

        if verbose:
            log.info(f"    Parsed {board_region_count} board regions")

    def _parse_models(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse model stream (length-prefixed MODEL property records).

        Stream preference:
        1. ModelsNoEmbed/Data (full list: embedded + linked models)
        2. Models/Data (legacy/embedded subset)
        """
        self.models = []
        self._models_stream_source = None

        selected_stream_name: str | None = None
        selected_data: bytes | None = None

        stream_candidates = [
            ("ModelsNoEmbed/Data", ["ModelsNoEmbed", "Data"]),
            ("Models/Data", ["Models", "Data"]),
        ]
        for stream_name, stream_path in stream_candidates:
            try:
                data = ole.openstream(stream_path)
            except Exception:
                continue

            if data:
                selected_stream_name = stream_name
                selected_data = data
                break

            # Keep first empty stream as fallback if no non-empty stream exists.
            if selected_data is None:
                selected_stream_name = stream_name
                selected_data = data

        if selected_data is None or selected_stream_name is None:
            if verbose:
                log.info("    No model metadata stream found")
            return

        self._models_stream_source = selected_stream_name
        offset = 0
        model_count = 0
        while offset + 4 <= len(selected_data):
            try:
                model = AltiumPcbModel()
                bytes_consumed = model.parse_from_binary(selected_data, offset)
                if bytes_consumed <= 0:
                    break
                self.models.append(model)
                offset += bytes_consumed
                model_count += 1
            except Exception as ex:
                if verbose:
                    log.warning(f"Failed to parse MODEL at offset {offset}: {ex}")
                break

        if verbose:
            log.info(f"    Parsed {model_count} models from {selected_stream_name}")

    def _parse_embedded_fonts(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Parse EmbeddedFonts6/Data stream to extract embedded fonts.

        Args:
            ole: Open OLE file
            verbose: Enable verbose output
        """
        try:
            data = ole.openstream(["EmbeddedFonts6", "Data"])
            self.embedded_fonts = parse_embedded_fonts(data)

            if verbose:
                for font in self.embedded_fonts:
                    log.info(f"    Found embedded font: {font.filename}")
        except Exception as e:
            if verbose:
                log.warning(f"Failed to parse embedded fonts: {e}")

    def _store_raw_streams(self, ole: AltiumOleFile, verbose: bool = False) -> None:
        """
        Store ALL streams as raw binary for complete round-trip preservation.

        CRITICAL: We store raw binary for EVERY stream (both parsed and unparsed)
        because our OOP serialization is not yet complete. This ensures that:
        1. Files open correctly in Altium (all required streams present)
        2. Round-trip preserves everything byte-for-byte
        3. We can incrementally add OOP serialization per-stream

        Once we have complete OOP serialization for a stream, we can exclude it
        from this passthrough and use serialize methods instead.

        Args:
            ole: Open OLE file
            verbose: Enable verbose output
        """
        # Store EVERYTHING - both parsed and unparsed streams
        # This ensures complete preservation until OOP serialization is ready
        for path in ole.listdir():
            if not path:
                continue
            stream_name = "/".join(path)
            try:
                self._raw_streams[stream_name] = ole.openstream(path)
                if verbose:
                    log.debug(
                        f"    Stored raw stream: {stream_name} ({len(self._raw_streams[stream_name])} bytes)"
                    )
            except Exception as e:
                if verbose:
                    log.warning(f"Could not read stream {stream_name}: {e}")

    def save(self, filepath: Path | str, verbose: bool = False) -> None:
        """
        Save to binary PcbDoc format.

        This is the canonical public write path for PcbDoc files.

        Args:
            filepath: Destination `.PcbDoc` path.
            verbose: Enable progress logging during serialization.
        """
        output_path = Path(filepath)
        if self._authoring_builder is not None:
            self._authoring_builder.save(output_path)
            self._sync_from_saved_authoring_file(output_path, verbose=verbose)
            return
        self._to_file_impl(output_path, verbose=verbose)

    def _write_passthrough_streams(
        self, writer: AltiumOleWriter, *, verbose: bool
    ) -> None:
        """
        Write untouched OLE streams back into the output document.
        """
        for stream_name, data in self._raw_streams.items():
            writer.add_stream(stream_name, data)
            if verbose:
                log.info(f"  Wrote passthrough stream: {stream_name}")

    def _write_basic_primitive_streams(
        self, writer: AltiumOleWriter, *, verbose: bool
    ) -> None:
        """
        Write the primary primitive data streams.
        """
        if self.tracks:
            if verbose:
                log.info(f"  Writing Tracks6/Data ({len(self.tracks)} tracks)...")
            writer.add_stream("Tracks6/Data", self._serialize_tracks())

        if self.arcs:
            if verbose:
                log.info(f"  Writing Arcs6/Data ({len(self.arcs)} arcs)...")
            writer.add_stream("Arcs6/Data", self._serialize_arcs())

        if self.pads:
            if verbose:
                log.info(f"  Writing Pads6/Data ({len(self.pads)} pads)...")
            writer.add_stream("Pads6/Data", self._serialize_pads())

        if self.vias:
            if verbose:
                log.info(f"  Writing Vias6/Data ({len(self.vias)} vias)...")
            writer.add_stream("Vias6/Data", self._serialize_vias())

        if self.texts:
            if verbose:
                log.info(f"  Writing Texts6/Data ({len(self.texts)} texts)...")
            writer.add_stream("WideStrings6/Data", self._serialize_widestrings6())
            writer.add_stream("Texts6/Data", self._serialize_texts())

        if self.fills:
            if verbose:
                log.info(f"  Writing Fills6/Data ({len(self.fills)} fills)...")
            writer.add_stream("Fills6/Data", self._serialize_fills())

        if self.regions:
            if verbose:
                log.info(f"  Writing Regions6/Data ({len(self.regions)} regions)...")
            writer.add_stream("Regions6/Data", self._serialize_regions())

        if self.board_regions:
            if verbose:
                log.info(
                    f"  Writing BoardRegions/Data ({len(self.board_regions)} board regions)..."
                )
            writer.add_stream("BoardRegions/Data", self._serialize_board_regions())

    def _write_shapebased_region_stream(
        self, writer: AltiumOleWriter, *, verbose: bool
    ) -> None:
        """
        Write shape-based regions when OOP serialization is safe to use.
        """
        if not self.shapebased_regions:
            return
        if verbose:
            log.info(
                f"  Writing ShapeBasedRegions6/Data ({len(self.shapebased_regions)} shape-based regions)..."
            )
        shapebased_regions_data = self._serialize_shapebased_regions()
        raw_stream_key = "ShapeBasedRegions6/Data"
        raw_stream = self._raw_streams.get(raw_stream_key)
        if raw_stream is not None and len(shapebased_regions_data) != len(raw_stream):
            log.debug(
                f"  SBR stream size mismatch ({len(shapebased_regions_data)} vs "
                f"{len(raw_stream)}), using raw stream passthrough"
            )
            return
        writer.add_stream(raw_stream_key, shapebased_regions_data)

    def _write_component_body_streams(
        self, writer: AltiumOleWriter, *, verbose: bool
    ) -> None:
        """
        Write component body streams.
        """
        if self.component_bodies:
            if verbose:
                log.info(
                    f"  Writing ComponentBodies6/Data ({len(self.component_bodies)} component bodies)..."
                )
            writer.add_stream(
                "ComponentBodies6/Data", self._serialize_component_bodies()
            )

        if self.shapebased_component_bodies:
            if verbose:
                log.info(
                    "  Writing ShapeBasedComponentBodies6/Data (%d shape-based component bodies)...",
                    len(self.shapebased_component_bodies),
                )
            writer.add_stream(
                "ShapeBasedComponentBodies6/Data",
                self._serialize_shapebased_component_bodies(),
            )

    def _write_model_streams(self, writer: AltiumOleWriter, *, verbose: bool) -> None:
        """
        Write embedded and linked model metadata streams.
        """
        if not self.models:
            return
        if verbose:
            log.info(
                f"  Writing model metadata streams ({len(self.models)} model records)..."
            )

        models_data_all = self._serialize_models(self.models)
        embedded_models = [model for model in self.models if model.is_embedded]
        models_data_embedded = self._serialize_models(embedded_models)
        write_models_no_embed = (
            self._models_stream_source == "ModelsNoEmbed/Data"
            or "ModelsNoEmbed/Data" in self._raw_streams
            or len(embedded_models) != len(self.models)
        )
        if write_models_no_embed:
            writer.add_stream("ModelsNoEmbed/Data", models_data_all)
            writer.add_stream("Models/Data", models_data_embedded)
            return
        writer.add_stream("Models/Data", models_data_all)

    def _write_support_streams(self, writer: AltiumOleWriter, *, verbose: bool) -> None:
        """
        Write remaining parsed support streams.
        """
        if self.rules:
            if verbose:
                log.info(f"  Writing Rules6/Data ({len(self.rules)} rules)...")
            writer.add_stream("Rules6/Data", self._serialize_rules())

        if self.dimensions:
            if verbose:
                log.info(
                    f"  Writing Dimensions6/Data ({len(self.dimensions)} dimensions)..."
                )
            writer.add_stream("Dimensions6/Data", self._serialize_dimensions())

        if self.extended_primitive_information:
            if verbose:
                log.info(
                    "  Writing ExtendedPrimitiveInformation/Data (%d records)...",
                    len(self.extended_primitive_information),
                )
            writer.add_stream(
                "ExtendedPrimitiveInformation/Data",
                self._serialize_extended_primitive_information(),
            )

        if self.custom_shapes:
            if verbose:
                log.info(
                    "  Writing CustomShapes/Data (%d records)...",
                    len(self.custom_shapes),
                )
            writer.add_stream(
                "CustomShapes/Header",
                len(self.custom_shapes).to_bytes(4, byteorder="little"),
            )
            writer.add_stream("CustomShapes/Data", self._serialize_custom_shapes())

    def _to_file_impl(self, filepath: Path, verbose: bool = False) -> None:
        """
        Internal write implementation.
        """
        filepath = Path(filepath).resolve()

        if verbose:
            log.info(f"Writing PcbDoc: {filepath.name}")

        # Create OLE writer
        writer = AltiumOleWriter()

        self._write_passthrough_streams(writer, verbose=verbose)
        self._write_basic_primitive_streams(writer, verbose=verbose)
        self._write_shapebased_region_stream(writer, verbose=verbose)
        self._write_component_body_streams(writer, verbose=verbose)
        self._write_model_streams(writer, verbose=verbose)
        self._write_support_streams(writer, verbose=verbose)

        # Write to file
        writer.write(str(filepath))

        if verbose:
            log.info(f"Successfully wrote {filepath.name}")

    def compute_board_centroid_mils(self) -> tuple[float, float] | None:
        """
        Compute a stable board centroid in mils from board-outline geometry.

        Uses polygon centroid (shoelace) when possible and falls back to the
        board-outline bounding-box center for degenerate outlines.

        Returns:
            `(x_mils, y_mils)` centroid in board coordinates, or `None` when
            the document has no board outline.
        """
        outline = getattr(self.board, "outline", None) if self.board else None
        vertices = list(getattr(outline, "vertices", []) or [])
        if not vertices:
            return None

        points = [(float(v.x_mils), float(v.y_mils)) for v in vertices]
        if len(points) < 3:
            if outline is None:
                return None
            min_x, min_y, max_x, max_y = outline.bounding_box
            return ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)

        if points[0] != points[-1]:
            points.append(points[0])

        area2 = 0.0
        cx_acc = 0.0
        cy_acc = 0.0
        for idx in range(len(points) - 1):
            x0, y0 = points[idx]
            x1, y1 = points[idx + 1]
            cross = x0 * y1 - x1 * y0
            area2 += cross
            cx_acc += (x0 + x1) * cross
            cy_acc += (y0 + y1) * cross

        if math.isclose(area2, 0.0, abs_tol=1e-9):
            if outline is None:
                return None
            min_x, min_y, max_x, max_y = outline.bounding_box
            return ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)

        centroid_x = cx_acc / (3.0 * area2)
        centroid_y = cy_acc / (3.0 * area2)
        return (centroid_x, centroid_y)

    def compute_board_centroid_relative_to_origin_mils(
        self,
    ) -> tuple[float, float] | None:
        """
        Compute board centroid offset from board datum/origin in mils.

        Returns:
            `(x_mils, y_mils)` centroid relative to the board origin, or `None`
            when the document has no board outline.
        """
        centroid = self.compute_board_centroid_mils()
        if centroid is None:
            return None
        origin_x = float(getattr(self.board, "origin_x", 0.0)) if self.board else 0.0
        origin_y = float(getattr(self.board, "origin_y", 0.0)) if self.board else 0.0
        return (centroid[0] - origin_x, centroid[1] - origin_y)

    def to_svg(
        self,
        options: "PcbSvgRenderOptions | None" = None,
        project_parameters: dict[str, str] | None = None,
    ) -> str:
        """
        Render this PCB document to a single composed SVG.

        Args:
            options: `PcbSvgRenderOptions` controlling visible layers, colors,
                scale, enrichment metadata, and board-outline rendering.
            project_parameters: Optional project-level parameters used for PCB
                text token substitution.

        Returns:
            SVG document text.
        """
        from .altium_pcb_svg_renderer import PcbSvgRenderer

        renderer = PcbSvgRenderer(options=options)
        return renderer.render_board(self, project_parameters=project_parameters)

    def to_layer_svgs(
        self,
        options: "PcbSvgRenderOptions | None" = None,
        project_parameters: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Render this PCB document to one SVG per visible layer.

        Args:
            options: `PcbSvgRenderOptions` controlling visible layers, colors,
                scale, enrichment metadata, and board-outline rendering.
            project_parameters: Optional project-level parameters used for PCB
                text token substitution.

        Returns:
            Dict mapping layer name to SVG document text.
        """
        from .altium_pcb_svg_renderer import PcbSvgRenderer

        renderer = PcbSvgRenderer(options=options)
        return renderer.render_layers(self, project_parameters=project_parameters)

    def to_surface_svg(
        self,
        side: "PCB_SurfaceSide",
        *,
        role_order: list["PCB_SurfaceRole"]
        | tuple["PCB_SurfaceRole", ...]
        | None = None,
        role_colors: dict["PCB_SurfaceRole", str] | None = None,
        options: "PcbSvgRenderOptions | None" = None,
        project_parameters: dict[str, str] | None = None,
        include_missing_roles: bool = True,
        mirror_bottom_view: bool = True,
    ) -> str:
        """
        Render this PCB document to a composed SVG for one board surface.

        Args:
            side: `PCB_SurfaceSide` surface side to render.
            role_order: Ordered logical surface roles to include.
            role_colors: Optional per-role color overrides.
            options: Base `PcbSvgRenderOptions`.
            project_parameters: Optional project-level parameters used for PCB
                text token substitution.
            include_missing_roles: Append default roles not explicitly listed
                in `role_order`.
            mirror_bottom_view: Mirror bottom-side output in SVG space.

        Returns:
            SVG document text.
        """
        from dataclasses import replace

        from .altium_pcb_surface import (
            pcb_surface_layer,
            pcb_surface_layers,
            PCB_SurfaceSide,
        )
        from .altium_pcb_svg_renderer import PcbSvgRenderer, PcbSvgRenderOptions

        layer_order = pcb_surface_layers(
            side,
            role_order=role_order,
            include_missing_roles=include_missing_roles,
        )

        layer_colors: dict[PcbLayer, str] = {}
        if role_colors:
            for role, color in role_colors.items():
                layer_colors[pcb_surface_layer(side, role)] = color

        base_options = options or PcbSvgRenderOptions()
        render_options = replace(
            base_options,
            visible_layers=set(layer_order),
            layer_render_order=layer_order,
            layer_colors={
                **dict(getattr(base_options, "layer_colors", {}) or {}),
                **layer_colors,
            },
            mirror_x=(
                bool(mirror_bottom_view) if side == PCB_SurfaceSide.BOTTOM else False
            ),
        )

        renderer = PcbSvgRenderer(options=render_options)
        return renderer.render_board(self, project_parameters=project_parameters)

    def to_board_outline_svg(
        self,
        options: "PcbSvgRenderOptions | None" = None,
        project_parameters: dict[str, str] | None = None,
    ) -> str:
        """
        Render this PCB document to an SVG containing only board-outline geometry.

        Args:
            options: `PcbSvgRenderOptions` controlling scale, colors, and
                enrichment metadata.
            project_parameters: Optional project-level parameters used for PCB
                text token substitution.

        Returns:
            SVG document text.
        """
        from .altium_pcb_svg_renderer import PcbSvgRenderer

        renderer = PcbSvgRenderer(options=options)
        return renderer.render_board_outline_only(
            self, project_parameters=project_parameters
        )

    def _build_footprint_group_index(
        self,
    ) -> tuple[
        list[tuple[tuple[str, str], str, int]], dict[tuple[str, str], list[int]], int
    ]:
        """
        Group components by extracted footprint identity while preserving board order.
        """
        group_order: list[tuple[tuple[str, str], str, int]] = []
        groups: dict[tuple[str, str], list[int]] = {}
        counts: dict[str, int] = {}
        unique_patterns: dict[str, None] = {}
        for idx, comp in enumerate(self.components):
            if not comp.footprint:
                continue
            pattern = comp.footprint
            unique_patterns.setdefault(pattern, None)
            source_footprint_library = str(
                getattr(comp, "source_footprint_library", "") or ""
            )
            group_key = (pattern, source_footprint_library)
            if group_key not in groups:
                occurrence = counts.get(pattern, 0) + 1
                counts[pattern] = occurrence
                emitted_name = pattern if occurrence == 1 else f"{pattern}_{occurrence}"
                groups[group_key] = []
                group_order.append((group_key, emitted_name, occurrence))
            groups[group_key].append(idx)
        return group_order, groups, len(unique_patterns)

    @staticmethod
    def _component_transform_params(comp: Any) -> tuple[int, int, float, bool]:
        """
        Resolve component-local extraction transform parameters.
        """
        cx = int(float(str(comp.x).strip("mil")) * 10000)
        cy = int(float(str(comp.y).strip("mil")) * 10000)
        rotation = comp.get_rotation_degrees()
        flipped = comp.get_layer_normalized() == "bottom"
        return cx, cy, rotation, flipped

    @staticmethod
    def _build_extracted_footprint(fp_name: str) -> Any:
        """
        Create an extracted footprint container with a stable OLE storage name.
        """
        from .altium_pcblib import AltiumPcbFootprint, _sanitize_ole_name

        footprint = AltiumPcbFootprint(fp_name)
        ole_name = _sanitize_ole_name(fp_name)
        if len(ole_name) > 31:
            ole_name = ole_name[:31]
        footprint._ole_storage_name = ole_name
        return footprint

    def _primitive_extraction_sources(self) -> list[tuple[str, list[Any], Any]]:
        """
        Return primitive sources used during footprint extraction.
        """
        return [
            ("pads", self.pads, _extract_transform_pad),
            ("tracks", self.tracks, _extract_transform_track),
            ("arcs", self.arcs, _extract_transform_arc),
            ("vias", self.vias, _extract_transform_via),
            ("texts", self.texts, _extract_transform_text),
            ("fills", self.fills, _extract_transform_fill),
            ("regions", self.regions, _extract_transform_region),
        ]

    def _copy_component_primitives_to_footprint(
        self,
        footprint: Any,
        *,
        comp_idx: int,
        cx: int,
        cy: int,
        rotation: float,
        flipped: bool,
        layer_flip_map: dict[int, int],
        v7_layer_flip_map: dict[int, int],
    ) -> None:
        """
        Deep-copy and localize standard primitive streams for a component.
        """
        for attr_name, prim_list, transform_fn in self._primitive_extraction_sources():
            for prim in prim_list:
                if prim.component_index != comp_idx:
                    continue
                if attr_name == "texts" and (
                    getattr(prim, "is_comment", False)
                    or getattr(prim, "is_designator", False)
                ):
                    continue
                if attr_name == "regions" and _should_skip_custom_pad_region_copy(
                    prim,
                    self.pads,
                    comp_idx,
                ):
                    continue
                primitive_copy = copy.deepcopy(prim)
                transform_fn(
                    primitive_copy,
                    cx,
                    cy,
                    rotation,
                    flipped,
                    layer_flip_map,
                    v7_layer_flip_map,
                )
                getattr(footprint, attr_name).append(primitive_copy)
                footprint._record_order.append(primitive_copy)

    def _append_shapebased_region_fallback(
        self,
        footprint: Any,
        *,
        comp_idx: int,
        cx: int,
        cy: int,
        rotation: float,
        flipped: bool,
        layer_flip_map: dict[int, int],
    ) -> None:
        """
        Use shape-based regions only when a component has no classic regions.
        """
        if footprint.regions:
            return
        for prim in self.shapebased_regions:
            if prim.component_index != comp_idx:
                continue
            primitive_copy = copy.deepcopy(prim)
            _extract_transform_shapebased(
                primitive_copy, cx, cy, rotation, flipped, layer_flip_map
            )
            footprint.regions.append(primitive_copy)
            footprint._record_order.append(primitive_copy)

    def _localize_footprint_custom_pads(
        self,
        footprint: Any,
        *,
        comp_idx: int,
        cx: int,
        cy: int,
        rotation: float,
        flipped: bool,
        layer_flip_map: dict[int, int],
    ) -> None:
        """
        Rebuild localized custom-pad contracts for extracted footprint pads.
        """
        source_component_pads = [
            prim for prim in self.pads if prim.component_index == comp_idx
        ]
        for local_pad_index, (source_pad, local_pad) in enumerate(
            zip(source_component_pads, footprint.pads)
        ):
            local_pad.custom_shape = None
            _localize_custom_pad_contract(
                footprint,
                source_pad,
                local_pad,
                local_pad_index=local_pad_index,
                cx=cx,
                cy=cy,
                rot=rotation,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
            )

    def _append_component_body_fallback(
        self,
        footprint: Any,
        *,
        comp_idx: int,
        cx: int,
        cy: int,
        rotation: float,
        flipped: bool,
        layer_flip_map: dict[int, int],
    ) -> None:
        """
        Prefer standard component bodies, falling back to shape-based bodies.
        """
        standard_bodies = [
            body for body in self.component_bodies if body.component_index == comp_idx
        ]
        fallback_bodies: list[Any] = []
        if not standard_bodies:
            fallback_bodies = [
                body
                for body in self.shapebased_component_bodies
                if body.component_index == comp_idx
            ]
        for prim in standard_bodies or fallback_bodies:
            primitive_copy = copy.deepcopy(prim)
            _extract_transform_component_body(
                primitive_copy,
                cx,
                cy,
                rotation,
                flipped,
                layer_flip_map,
            )
            footprint.component_bodies.append(primitive_copy)
            footprint._record_order.append(primitive_copy)

    @staticmethod
    def _warn_on_far_footprint_primitives(footprint: Any, fp_name: str) -> None:
        """
        Warn about extracted primitives that remain far from the local footprint origin.
        """
        warn_dist_mils = 10000
        far_prims: list[tuple[str, float, float]] = []
        for prim in footprint._record_order:
            px = py = 0.0
            if hasattr(prim, "x") and hasattr(prim, "y"):
                px, py = abs(prim.x) / 10000.0, abs(prim.y) / 10000.0
            elif hasattr(prim, "start_x") and hasattr(prim, "start_y"):
                px = abs(prim.start_x) / 10000.0
                py = abs(prim.start_y) / 10000.0
            if px > warn_dist_mils or py > warn_dist_mils:
                far_prims.append((type(prim).__name__, px, py))
        if not far_prims:
            return
        log.warning(
            f"Footprint '{fp_name}': {len(far_prims)} primitive(s) far from origin "
            f"(possible hidden objects): {far_prims[0][0]} at "
            f"({far_prims[0][1]:.0f}, {far_prims[0][2]:.0f}) mil"
            + (f" (+{len(far_prims) - 1} more)" if len(far_prims) > 1 else "")
        )

    def _finalize_extracted_footprint_metadata(
        self,
        footprint: Any,
        *,
        fp_name: str,
        comp: Any,
        occurrence: int,
    ) -> None:
        """
        Populate extracted footprint metadata streams and parameters.
        """
        if footprint.texts:
            footprint.raw_widestrings = _build_pcblib_widestrings(
                footprint.texts,
                self.widestrings_table,
            )
        footprint.parameters.update(
            {
                "PATTERN": fp_name,
                "HEIGHT": (
                    str(getattr(comp, "height", "0mil") or "0mil")
                    if occurrence == 1
                    else "0mil"
                ),
                "DESCRIPTION": (
                    str(getattr(comp, "footprint_description", "") or "")
                    if occurrence == 1
                    else ""
                ),
                "ITEMGUID": "",
                "REVISIONGUID": "",
            }
        )
        footprint.raw_parameters = _build_footprint_parameters(fp_name)

    @staticmethod
    def _log_extracted_footprint_summary(footprint: Any) -> None:
        """
        Log a short primitive-count summary for an extracted footprint.
        """
        total = sum(
            len(getattr(footprint, attr))
            for attr in [
                "pads",
                "tracks",
                "arcs",
                "vias",
                "texts",
                "fills",
                "regions",
                "component_bodies",
            ]
        )
        log.info(
            f"    {total} primitives ({len(footprint.pads)} pads, "
            f"{len(footprint.tracks)} tracks, {len(footprint.arcs)} arcs, "
            f"{len(footprint.texts)} texts, {len(footprint.regions)} regions, "
            f"{len(footprint.component_bodies)} bodies)"
        )

    def _extract_footprints(self, verbose: bool = False) -> list:
        """
        Extract footprints from PcbDoc, reversing component placement transforms.

        Returns a list of AltiumPcbFootprint objects with footprint-local coordinates.
        Logs warnings for primitives found far from origin (possible hidden objects).

        Args:
            verbose: Enable verbose logging

        Returns:
            List of AltiumPcbFootprint objects
        """
        if verbose:
            log.info(
                f"Extracting footprints from {self.filepath.name if self.filepath else 'PcbDoc'}"
            )

        fp_group_order, fp_groups, unique_pattern_count = (
            self._build_footprint_group_index()
        )

        if verbose:
            log.info(
                "Found %d extracted footprint groups across %d unique patterns from %d components",
                len(fp_group_order),
                unique_pattern_count,
                len(self.components),
            )

        layer_flip_map = dict(getattr(self.board, "component_layer_flip_map", {}) or {})
        v7_layer_flip_map = dict(
            getattr(self.board, "component_v7_layer_flip_map", {}) or {}
        )

        footprints = []
        for group_key, fp_name, occurrence in fp_group_order:
            comp_indices = fp_groups[group_key]
            comp_idx = comp_indices[0]
            comp = self.components[comp_idx]

            cx, cy, rotation, flipped = self._component_transform_params(comp)

            if verbose:
                side = "BOTTOM" if flipped else "TOP"
                suffix_note = f" instance#{occurrence}" if occurrence > 1 else ""
                log.info(
                    f"  {fp_name}: comp={comp.designator} at ({cx / 10000:.1f}, {cy / 10000:.1f}) "
                    f"rot={rotation:.0f} {side}{suffix_note}"
                )

            fp = self._build_extracted_footprint(fp_name)
            self._copy_component_primitives_to_footprint(
                fp,
                comp_idx=comp_idx,
                cx=cx,
                cy=cy,
                rotation=rotation,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                v7_layer_flip_map=v7_layer_flip_map,
            )
            self._append_shapebased_region_fallback(
                fp,
                comp_idx=comp_idx,
                cx=cx,
                cy=cy,
                rotation=rotation,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
            )
            self._localize_footprint_custom_pads(
                fp,
                comp_idx=comp_idx,
                cx=cx,
                cy=cy,
                rotation=rotation,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
            )
            self._append_component_body_fallback(
                fp,
                comp_idx=comp_idx,
                cx=cx,
                cy=cy,
                rotation=rotation,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
            )
            self._warn_on_far_footprint_primitives(fp, fp_name)
            self._finalize_extracted_footprint_metadata(
                fp,
                fp_name=fp_name,
                comp=comp,
                occurrence=occurrence,
            )
            footprints.append(fp)

            if verbose:
                self._log_extracted_footprint_summary(fp)

        return footprints

    def _get_embedded_model_entries(self) -> list[tuple[AltiumPcbModel, bytes]]:
        return collect_pcbdoc_embedded_model_entries(self._raw_streams, self.models)

    @staticmethod
    def _sanitize_embedded_asset_name(name: str, fallback: str) -> str:
        """
        Sanitize embedded asset names for stable filesystem extraction.
        """
        return sanitize_embedded_asset_name(name, fallback)

    def get_embedded_model_entries(self) -> list[tuple[AltiumPcbModel, bytes]]:
        """
        Return embedded model metadata plus compressed payload bytes.

        Returns:
            List of `(model, compressed_payload)` tuples. The payload is the
            zlib-compressed bytes stored in the native `Models/<n>` streams.
        """
        return self._get_embedded_model_entries()

    def extract_embedded_fonts(
        self, output_dir: Path, verbose: bool = False
    ) -> list[Path]:
        """
        Extract embedded PCB TrueType fonts to `output_dir`.

        Files are written as `<index:03d>__<font filename>.ttf` after decompression.

        Args:
            output_dir: Directory where extracted font files will be written.
            verbose: Enable progress logging.

        Returns:
            Paths to the font files written.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for index, font in enumerate(self.embedded_fonts):
            filename = self._sanitize_embedded_asset_name(
                font.filename, f"font_{index:03d}.ttf"
            )
            output_path = output_dir / f"{index:03d}__{filename}"
            font.save_to_file(output_path)
            if output_path.exists():
                written.append(output_path)
                if verbose:
                    log.info("Extracted embedded font: %s", output_path.name)
        return written

    def extract_embedded_models(
        self, output_dir: Path, verbose: bool = False
    ) -> list[Path]:
        """
        Extract embedded 3D model payloads to `output_dir`.

        Files are written as `<index:03d>__<model filename>` after zlib decompression.

        Args:
            output_dir: Directory where extracted model payloads will be written.
            verbose: Enable progress logging and decompression warnings.

        Returns:
            Paths to the model files written.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for index, (model, compressed_payload) in enumerate(
            self._get_embedded_model_entries()
        ):
            filename = self._sanitize_embedded_asset_name(
                str(getattr(model, "name", "") or ""),
                f"model_{index:03d}.bin",
            )
            output_path = output_dir / f"{index:03d}__{filename}"
            try:
                payload = zlib.decompress(compressed_payload)
            except Exception:
                payload = b""
            if not payload:
                if verbose:
                    log.warning(
                        "Failed to decompress embedded model payload: %s", filename
                    )
                continue
            output_path.write_bytes(payload)
            written.append(output_path)
            if verbose:
                log.info("Extracted embedded model: %s", output_path.name)
        return written

    def _build_pcblib_with_builder(self, footprints: list) -> "AltiumPcbLib":
        from .altium_pcblib_builder import PcbLibBuilder

        builder = PcbLibBuilder()
        model_entries = self._get_embedded_model_entries()
        seen_model_signatures: set[tuple] = set()
        for footprint in footprints:
            copy_footprint_with_models_into_builder(
                builder,
                footprint,
                model_entries,
                seen_model_signatures=seen_model_signatures,
                height=footprint.parameters.get("HEIGHT", "0mil"),
                description=footprint.parameters.get("DESCRIPTION", ""),
                item_guid=footprint.parameters.get("ITEMGUID", ""),
                revision_guid=footprint.parameters.get("REVISIONGUID", ""),
                copy_footprint=False,
            )

        return builder.build()

    def extract_pcblib(
        self, output_path: Path | None = None, verbose: bool = False
    ) -> "AltiumPcbLib":
        """
        Extract footprints from PcbDoc as an AltiumPcbLib object.

        Reverses Altium's component placement transform (translate + rotate + flip)
        to recover footprint-local coordinates from board-absolute coordinates.
        Returns an AltiumPcbLib containing one footprint per component
        instance. Duplicate PATTERN names are suffixed `_2`, `_3`, ... to match
        Altium's combined-library extraction behavior.

        If `output_path` is provided, also writes the combined PcbLib file as a
        convenience. Use the returned object's `save(...)` and `split(...)` for
        more control.

        Args:
            output_path: Optional path to write a combined `.PcbLib` file.
            verbose: Enable progress logging.

        Returns:
            `AltiumPcbLib` object with extracted footprints.
        """
        footprints = self._extract_footprints(verbose=verbose)
        pcblib = self._build_pcblib_with_builder(footprints)

        if output_path is not None:
            pcblib.save(output_path)
            if verbose:
                log.info(
                    f"Wrote {output_path.name} with {len(pcblib.footprints)} footprints"
                )

        return pcblib

    def _serialize_tracks(self) -> bytes:
        """
        Serialize all TRACK records to binary format.
        """
        data = bytearray()
        for track in self.tracks:
            track_bytes = track.serialize_to_binary()
            data.extend(track_bytes)
        return bytes(data)

    def _serialize_arcs(self) -> bytes:
        """
        Serialize all ARC records to binary format.
        """
        data = bytearray()
        for arc in self.arcs:
            arc_bytes = arc.serialize_to_binary()
            data.extend(arc_bytes)
        return bytes(data)

    def _serialize_pads(self) -> bytes:
        """
        Serialize all PAD records to binary format.
        """
        data = bytearray()
        for pad in self.pads:
            pad_bytes = pad.serialize_to_binary()
            data.extend(pad_bytes)
        return bytes(data)

    def _serialize_vias(self) -> bytes:
        """
        Serialize all VIA records to binary format.
        """
        data = bytearray()
        for via in self.vias:
            via_bytes = via.serialize_to_binary()
            data.extend(via_bytes)
        return bytes(data)

    def _allocate_missing_widestring_indices(self, table: dict[int, str]) -> None:
        """
        Assign deterministic WideStrings indices for text records missing one.
        """
        next_index = max(table.keys(), default=0) + 1
        for text in self.texts:
            if text.widestring_index is not None:
                continue
            while next_index in table:
                next_index += 1
            text.widestring_index = next_index
            next_index += 1

    def _serialize_widestrings6(self) -> bytes:
        """
        Serialize WideStrings6/Data from stored table plus current TEXT contents.

        This enables deterministic text round-trip for mutated/new TEXT records.
        """
        table = dict(self.widestrings_table)
        self._allocate_missing_widestring_indices(table)

        for text in self.texts:
            if text.widestring_index is None:
                continue
            table[int(text.widestring_index)] = text.text_content or ""

        self.widestrings_table = dict(table)
        out = bytearray()
        for index in sorted(table.keys()):
            value = table[index]
            out.extend(struct.pack("<I", int(index)))
            if value == "":
                # Empty WideStrings entries are encoded with length<=2 and no payload.
                out.extend(struct.pack("<I", 2))
                continue
            payload = value.encode("utf-16le", errors="replace") + b"\x00\x00"
            out.extend(struct.pack("<I", len(payload)))
            out.extend(payload)
        return bytes(out)

    def _serialize_texts(self) -> bytes:
        """
        Serialize all TEXT records to binary format.
        """
        data = bytearray()
        for text in self.texts:
            text_bytes = text.serialize_to_binary()
            data.extend(text_bytes)
        return bytes(data)

    def _serialize_fills(self) -> bytes:
        """
        Serialize all FILL records to binary format.
        """
        data = bytearray()
        for fill in self.fills:
            fill_bytes = fill.serialize_to_binary()
            data.extend(fill_bytes)
        return bytes(data)

    def _serialize_regions(self) -> bytes:
        """
        Serialize all REGION records to binary format.
        """
        data = bytearray()
        for region in self.regions:
            region_bytes = region.serialize_to_binary()
            data.extend(region_bytes)
        return bytes(data)

    def _serialize_board_regions(self) -> bytes:
        """
        Serialize all BoardRegion records to binary format.
        """
        data = bytearray()
        for board_region in self.board_regions:
            data.extend(board_region.serialize_to_binary())
        return bytes(data)

    def _serialize_shapebased_regions(self) -> bytes:
        """
        Serialize all shape-based REGION records to binary format.
        """
        data = bytearray()
        for region in self.shapebased_regions:
            region_bytes = region.serialize_to_binary()
            data.extend(region_bytes)
        return bytes(data)

    def _serialize_component_bodies(self) -> bytes:
        """
        Serialize all COMPONENT_BODY records to binary format.
        """
        data = bytearray()
        for body in self.component_bodies:
            body_bytes = body.serialize_to_binary()
            data.extend(body_bytes)
        return bytes(data)

    def _serialize_shapebased_component_bodies(self) -> bytes:
        """
        Serialize all shape-based COMPONENT_BODY records to binary format.
        """
        data = bytearray()
        for body in self.shapebased_component_bodies:
            body_bytes = body.serialize_to_binary()
            data.extend(body_bytes)
        return bytes(data)

    def _serialize_models(self, models: list[AltiumPcbModel] | None = None) -> bytes:
        """
        Serialize MODEL property records to stream-ready bytes.
        """
        records = self.models if models is None else models
        data = bytearray()
        for model in records:
            model_bytes = model.serialize_to_binary()
            data.extend(model_bytes)
        return bytes(data)

    def _serialize_rules(self) -> bytes:
        """
        Serialize typed Rules6/Data records back to native leader+length records.
        """
        data = bytearray()
        for rule in self.rules:
            data.extend(rule.record_leader or b"\x00\x00")
            if getattr(rule, "can_passthrough_raw_payload", lambda: False)():
                payload = bytes(getattr(rule, "raw_record_payload", b""))
                data.extend(len(payload).to_bytes(4, byteorder="little"))
                data.extend(payload)
                continue
            encoded = encode_altium_record(rule.to_record())
            data.extend(encoded[:4])
            data.extend(encoded[4:])
        return bytes(data)

    def _serialize_dimensions(self) -> bytes:
        """
        Serialize typed Dimensions6/Data records back to native type+leader+length+payload.
        """
        data = bytearray()
        for dimension in self.dimensions:
            data.append(int(getattr(dimension, "record_type", 0)) & 0xFF)
            data.append(int(getattr(dimension, "record_leader", 0)) & 0xFF)
            payload = dimension.serialize_record_payload()
            data.extend(len(payload).to_bytes(4, byteorder="little"))
            data.extend(payload)
        return bytes(data)

    def _serialize_extended_primitive_information(self) -> bytes:
        """
        Serialize typed ExtendedPrimitiveInformation/Data records.
        """
        data = bytearray()
        for item in self.extended_primitive_information:
            data.extend(item.serialize_record())
        return bytes(data)

    def _serialize_custom_shapes(self) -> bytes:
        """
        Serialize typed CustomShapes/Data records.
        """
        return serialize_custom_shapes_stream(self.custom_shapes)

    @staticmethod
    def _records_require_serialize(records: list[object]) -> bool:
        """
        Return True when at least one record cannot be safely passthrough-preserved.

        A record is considered safe for passthrough if it has raw bytes and an unchanged
        state signature.
        """
        for record in records:
            raw_binary = getattr(record, "_raw_binary", None)
            raw_sig = getattr(record, "_raw_binary_signature", None)
            state_sig_fn = getattr(record, "_state_signature", None)
            if raw_binary is None or raw_sig is None or not callable(state_sig_fn):
                return True
            try:
                if raw_sig != state_sig_fn():
                    return True
            except Exception:
                return True
        return False

    def get_component_primitives(self, component_index: int) -> dict[str, list]:
        """
        Get all primitives belonging to a component by index.

        Args:
            component_index: Zero-based index into `components`.

        Returns:
            Dict with primitive-family keys such as `pads`, `tracks`, `arcs`,
            `fills`, `texts`, `regions`, `board_regions`, and
            `component_bodies`. Each value is a list of matching primitive
            objects. Vias and polygons are excluded because they do not carry a
            component index.
        """
        result: dict[str, list] = {
            "pads": [],
            "tracks": [],
            "arcs": [],
            "fills": [],
            "texts": [],
            "regions": [],
            "board_regions": [],
            "component_bodies": [],
        }
        for pad in self.pads:
            if pad.component_index == component_index:
                result["pads"].append(pad)
        for track in self.tracks:
            if track.component_index == component_index:
                result["tracks"].append(track)
        for arc in self.arcs:
            if arc.component_index == component_index:
                result["arcs"].append(arc)
        for fill in self.fills:
            if fill.component_index == component_index:
                result["fills"].append(fill)
        for text in self.texts:
            if text.component_index == component_index:
                result["texts"].append(text)
        for region in self.regions:
            if region.component_index == component_index:
                result["regions"].append(region)
        for board_region in self.board_regions:
            if board_region.component_index == component_index:
                result["board_regions"].append(board_region)
        for body in self.component_bodies:
            if body.component_index == component_index:
                result["component_bodies"].append(body)
        return result

    def get_net_primitives(self, net_index: int) -> dict[str, list]:
        """
        Get all primitives assigned to a net by index.

        Args:
            net_index: Zero-based index into `nets`.

        Returns:
            Dict with primitive-family keys such as `pads`, `tracks`, `arcs`,
            `vias`, `fills`, `regions`, and `polygons`. Each value is a list of
            matching primitive objects. Text records are excluded because they
            do not carry a net index.
        """
        result: dict[str, list] = {
            "pads": [],
            "tracks": [],
            "arcs": [],
            "vias": [],
            "fills": [],
            "regions": [],
            "polygons": [],
        }
        for pad in self.pads:
            if pad.net_index == net_index:
                result["pads"].append(pad)
        for track in self.tracks:
            if track.net_index == net_index:
                result["tracks"].append(track)
        for arc in self.arcs:
            if arc.net_index == net_index:
                result["arcs"].append(arc)
        for via in self.vias:
            if via.net_index == net_index:
                result["vias"].append(via)
        for fill in self.fills:
            if fill.net_index == net_index:
                result["fills"].append(fill)
        for region in self.regions:
            if region.net_index == net_index:
                result["regions"].append(region)
        for polygon in self.polygons:
            if polygon.net == net_index:
                result["polygons"].append(polygon)
        return result

    def get_unique_footprints(self) -> set[str]:
        """
        Get unique footprint names used in the design.

        Returns:
            Set of unique footprint names from component `PATTERN` fields.
        """
        footprints = set()
        for comp in self.components:
            if comp.footprint:
                footprints.add(comp.footprint)
        return footprints

    def get_components_by_footprint(self, footprint: str) -> list[AltiumPcbComponent]:
        """
        Get all components using a specific footprint.

        Args:
            footprint: Exact component footprint pattern name to search for.

        Returns:
            List of `AltiumPcbComponent` instances using this footprint.
        """
        return [comp for comp in self.components if comp.footprint == footprint]

    def __str__(self) -> str:
        """
        String representation.
        """
        return f"AltiumPcbDoc({self.filepath.name}, {len(self.components)} components, {len(self.get_unique_footprints())} unique footprints)"

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        return f"AltiumPcbDoc(filepath={self.filepath}, components={len(self.components)}, nets={len(self.nets)})"


def get_footprints_from_pcbdoc(pcbdoc_path: Path, verbose: bool = False) -> set[str]:
    """
    Extract unique footprint names from a PcbDoc file.

    Convenience function for quick footprint extraction.

    Args:
        pcbdoc_path: Path to .PcbDoc file
        verbose: If True, print parsing progress

    Returns:
        Set of unique footprint names
    """
    pcbdoc = AltiumPcbDoc.from_file(pcbdoc_path, verbose=verbose)
    return pcbdoc.get_unique_footprints()


def find_pcblib_files(
    footprint_name: str, search_dirs: list[Path], verbose: bool = False
) -> list[Path]:
    """
    Find PcbLib files matching a footprint name.

    Searches for exact match (case-insensitive) in search directories.

    Args:
        footprint_name: Name of footprint to find
        search_dirs: List of directories to search
        verbose: If True, print search progress

    Returns:
        List of matching PcbLib file paths (empty if not found)
    """
    matches = []

    for search_dir in search_dirs:
        if not search_dir.exists():
            if verbose:
                log.warning(f"  Search directory not found: {search_dir}")
            continue

        # Look for exact match: footprint_name.PcbLib
        exact_match = search_dir / f"{footprint_name}.PcbLib"
        if exact_match.exists():
            matches.append(exact_match)
            continue

        # Look for case-insensitive match
        for pcblib_file in search_dir.glob("*.PcbLib"):
            if pcblib_file.stem.lower() == footprint_name.lower():
                matches.append(pcblib_file)
                break

    return matches


def extract_footprints_from_pcbdoc(
    pcbdoc_path: Path,
    output_dir: Path,
    search_dirs: list[Path],
    copy_files: bool = True,
    verbose: bool = True,
) -> dict[str, Path | None]:
    """
    Extract footprints from a PcbDoc file by copying matching PcbLib files.

    Args:
        pcbdoc_path: Path to .PcbDoc file
        output_dir: Directory to copy footprint files to
        search_dirs: List of directories to search for PcbLib files
        copy_files: If True, copy files. If False, just report matches.
        verbose: If True, print detailed progress

    Returns:
        Dict mapping footprint name -> PcbLib file path (None if not found)

        for fp, path in results.items():
            if path:
                log.info(f"Found: {fp}")
            else:
                log.info(f"Missing: {fp}")
    """
    import shutil

    if verbose:
        log.info("")
        log.info("=" * 80)
        log.info(f"Extracting Footprints: {pcbdoc_path.name}")
        log.info("=" * 80)
        log.info("")

    # Get unique footprints
    if verbose:
        log.info("Parsing PcbDoc file...")
    footprints = get_footprints_from_pcbdoc(pcbdoc_path, verbose=verbose)

    if verbose:
        log.info(f"Found {len(footprints)} unique footprints")
        log.info("")
        log.info("Searching for PcbLib files...")
        log.info("")

    # Find matching PcbLib files
    results = {}
    for footprint in sorted(footprints):
        matches = find_pcblib_files(footprint, search_dirs, verbose=False)

        if matches:
            results[footprint] = matches[0]
            if verbose:
                log.info(f"  [FOUND] {footprint}")
                log.info(f"          {matches[0]}")

            if copy_files:
                # Copy to output directory
                output_dir.mkdir(parents=True, exist_ok=True)
                dest = output_dir / matches[0].name
                shutil.copy2(matches[0], dest)
                if verbose:
                    log.info(f"          Copied to: {dest}")
        else:
            results[footprint] = None
            if verbose:
                log.warning(f"  [MISSING] {footprint}")

    # Summary
    if verbose:
        log.info("")
        log.info("=" * 80)
        log.info("Summary")
        log.info("=" * 80)
        found_count = sum(1 for v in results.values() if v is not None)
        missing_count = len(results) - found_count
        log.info(f"  Found: {found_count}/{len(results)} footprints")
        if missing_count > 0:
            log.warning(f"  Missing: {missing_count}/{len(results)} footprints")

    return results
