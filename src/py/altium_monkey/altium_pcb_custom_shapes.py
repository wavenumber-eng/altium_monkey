"""
Typed PCB custom-pad shape support.

This module covers two related layers:

1. `PcbDoc` `CustomShapes/Data` stream records, which currently identify pad
   primitive indices that carry custom pad geometry semantics.
2. Semantic custom-pad linking on top of the raw pad + region/arc primitives
   used by the existing parser model.

The current on-disk `PcbLib` fixtures do not carry a `CustomShapes/*` stream,
so `PcbLib` support is inferred from the stored anchor pad plus local contour
primitives. That preserves round-trip fidelity while giving downstream tools a
first-class semantic object to consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .altium_pcb_mask_paste_rules import (
    get_pad_mask_expansion_iu,
    get_pad_paste_expansion_iu,
)
from .altium_pcb_property_helpers import (
    clean_pcb_property_text as _clean_text,
    parse_pcb_int_token as _parse_int_token,
    parse_pcb_property_payload,
    PcbLengthPrefixedPropertyRecordMixin,
)
from .altium_record_types import PcbLayer

if TYPE_CHECKING:
    from .altium_pcb_extended_primitive_information import (
        AltiumPcbExtendedPrimitiveInformation,
    )


class AltiumPcbCustomShapeRecord(PcbLengthPrefixedPropertyRecordMixin):
    """
    Typed wrapper for one `CustomShapes/Data` record.
    """

    def __init__(self) -> None:
        self.properties: dict[str, str] = {}
        self.primitive_index: int | None = None
        self.raw_record_payload: bytes | None = None
        self._typed_signature_at_parse: tuple | None = None
        self._properties_raw_signature: tuple | None = None

    @classmethod
    def from_payload(cls, payload: bytes) -> "AltiumPcbCustomShapeRecord":
        item = cls()
        item.raw_record_payload = bytes(payload)
        item.properties = parse_pcb_property_payload(payload)
        item._load_typed_fields_from_properties()
        item._typed_signature_at_parse = item._typed_signature()
        item._properties_raw_signature = item._properties_signature()
        return item

    def _load_typed_fields_from_properties(self) -> None:
        props = self.properties or {}
        self.primitive_index = _parse_int_token(props.get("PRIMITIVEINDEX", ""))

    def _sync_typed_fields_to_properties(self) -> None:
        props = dict(self.properties or {})
        if self.primitive_index is None and "PRIMITIVEINDEX" not in props:
            props.pop("PRIMITIVEINDEX", None)
        else:
            props["PRIMITIVEINDEX"] = (
                "" if self.primitive_index is None else str(int(self.primitive_index))
            )
        self.properties = props

    def _typed_signature(self) -> tuple:
        return (self.primitive_index,)

    def _properties_signature(self) -> tuple:
        return tuple(
            (str(key), str(value)) for key, value in (self.properties or {}).items()
        )


def parse_custom_shapes_stream(raw: bytes) -> list[AltiumPcbCustomShapeRecord]:
    """
    Parse a `CustomShapes/Data` stream into typed records.
    """
    out: list[AltiumPcbCustomShapeRecord] = []
    pos = 0
    total = len(raw or b"")
    while pos + 4 <= total:
        record_len = int.from_bytes(raw[pos : pos + 4], byteorder="little")
        pos += 4
        if record_len <= 0 or pos + record_len > total:
            break
        payload = raw[pos : pos + record_len]
        pos += record_len
        out.append(AltiumPcbCustomShapeRecord.from_payload(payload))
    return out


@dataclass
class AltiumPcbCustomPadLayerShape:
    """
    One layer-specific custom-pad geometry attachment.
    """

    layer: int | None = None
    shape_kind: int | None = None
    source_record: AltiumPcbCustomShapeRecord | None = None
    region: object | None = None
    shape_region: object | None = None
    arcs: list[object] = field(default_factory=list)

    @property
    def geometry_primitives(self) -> list[object]:
        items: list[object] = []
        if self.region is not None:
            items.append(self.region)
        items.extend(self.arcs)
        return items


@dataclass
class AltiumPcbCustomPadShape:
    """
    First-class semantic custom-pad object attached to an anchor pad.
    """

    source: str
    anchor_pad_index: int | None = None
    layer_shapes: dict[int, AltiumPcbCustomPadLayerShape] = field(default_factory=dict)

    @staticmethod
    def _key_for_layer(layer: int | None) -> int:
        return int(layer) if layer is not None else -1

    @property
    def primary_layer_shape(self) -> AltiumPcbCustomPadLayerShape | None:
        if not self.layer_shapes:
            return None
        for layer_id in (
            PcbLayer.TOP.value,
            PcbLayer.BOTTOM.value,
            PcbLayer.TOP_PASTE.value,
            PcbLayer.BOTTOM_PASTE.value,
            PcbLayer.TOP_SOLDER.value,
            PcbLayer.BOTTOM_SOLDER.value,
        ):
            item = self.layer_shapes.get(self._key_for_layer(layer_id))
            if item is not None:
                return item
        first_key = sorted(self.layer_shapes)[0]
        return self.layer_shapes[first_key]

    @property
    def layer(self) -> int | None:
        item = self.primary_layer_shape
        return None if item is None else item.layer

    @property
    def shape_kind(self) -> int | None:
        item = self.primary_layer_shape
        return None if item is None else item.shape_kind

    @property
    def source_record(self) -> AltiumPcbCustomShapeRecord | None:
        item = self.primary_layer_shape
        return None if item is None else item.source_record

    @property
    def region(self) -> object | None:
        item = self.primary_layer_shape
        return None if item is None else item.region

    @property
    def shape_region(self) -> object | None:
        item = self.primary_layer_shape
        return None if item is None else item.shape_region

    @property
    def arcs(self) -> list[object]:
        item = self.primary_layer_shape
        return [] if item is None else list(item.arcs)

    @property
    def geometry_primitives(self) -> list[object]:
        items: list[object] = []
        for item in self.iter_layer_shapes():
            items.extend(item.geometry_primitives)
        return items

    def get_layer_shape(self, layer: int | None) -> AltiumPcbCustomPadLayerShape | None:
        return self.layer_shapes.get(self._key_for_layer(layer))

    def iter_layer_shapes(self) -> list[AltiumPcbCustomPadLayerShape]:
        return [self.layer_shapes[key] for key in sorted(self.layer_shapes)]

    def add_layer_shape(
        self,
        *,
        layer: int | None,
        shape_kind: int | None,
        source_record: AltiumPcbCustomShapeRecord | None,
        region: object | None,
        shape_region: object | None = None,
        arcs: list[object] | None = None,
    ) -> None:
        key = self._key_for_layer(layer)
        self.layer_shapes[key] = AltiumPcbCustomPadLayerShape(
            layer=layer,
            shape_kind=shape_kind,
            source_record=source_record,
            region=region,
            shape_region=shape_region,
            arcs=list(arcs or []),
        )


def serialize_custom_shapes_stream(records: list[AltiumPcbCustomShapeRecord]) -> bytes:
    """
    Serialize typed `CustomShapes/Data` records.
    """
    data = bytearray()
    for item in records:
        data.extend(item.serialize_record())
    return bytes(data)


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    if len(polygon) < 3:
        return False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _region_polygon_vertices_mils(region: object) -> list[tuple[float, float]]:
    verts = list(getattr(region, "outline_vertices", None) or [])
    if verts:
        return [(float(v.x_mils), float(v.y_mils)) for v in verts]

    outline = list(getattr(region, "outline", None) or [])
    if outline:
        if len(outline) >= 2:
            first = outline[0]
            last = outline[-1]
            if int(getattr(first, "x", 0)) == int(getattr(last, "x", 0)) and int(
                getattr(first, "y", 0)
            ) == int(getattr(last, "y", 0)):
                outline = outline[:-1]
        return [
            (
                float(getattr(v, "x", 0) or 0) / 10000.0,
                float(getattr(v, "y", 0) or 0) / 10000.0,
            )
            for v in outline
        ]

    return []


def _region_contains_pad_center(region: object, pad: object) -> bool:
    polygon = _region_polygon_vertices_mils(region)
    verts = polygon
    if len(verts) < 3:
        return False
    center_fn = getattr(pad, "pad_center_mils", None)
    if callable(center_fn):
        try:
            px, py = center_fn(getattr(pad, "layer", None))
        except Exception:
            px = float(getattr(pad, "x", 0) or 0) / 10000.0
            py = float(getattr(pad, "y", 0) or 0) / 10000.0
    else:
        px = float(getattr(pad, "x", 0) or 0) / 10000.0
        py = float(getattr(pad, "y", 0) or 0) / 10000.0
    return _point_in_polygon(px, py, polygon)


def _candidate_region_score(region: object) -> tuple[float, float]:
    verts = _region_polygon_vertices_mils(region)
    if not verts:
        return (float("inf"), float("inf"))
    xs = [float(x) for x, _y in verts]
    ys = [float(y) for _x, y in verts]
    area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    return (area, float(len(verts)))


def _iter_pad_regions(
    regions: list[object],
    pad: object,
    *,
    target_layer: int | None = None,
) -> list[object]:
    layer = getattr(pad, "layer", None) if target_layer is None else target_layer
    component_index = getattr(pad, "component_index", None)
    net_index = getattr(pad, "net_index", None)

    matches: list[object] = []
    for region in regions:
        if getattr(region, "layer", None) != layer:
            continue

        region_component = getattr(region, "component_index", None)
        if (
            component_index not in (None, 0xFFFF)
            and region_component != component_index
        ):
            continue

        region_net = getattr(region, "net_index", None)
        if net_index not in (None, 0xFFFF) and region_net not in (
            net_index,
            None,
            0xFFFF,
        ):
            continue

        if _region_contains_pad_center(region, pad):
            matches.append(region)

    return sorted(matches, key=_candidate_region_score)


def find_best_pad_region(
    regions: list[object],
    pad: object,
    *,
    target_layer: int | None = None,
) -> object | None:
    """
    Return the most likely region/shape-region attached to a pad.
    """
    matches = _iter_pad_regions(regions, pad, target_layer=target_layer)
    return matches[0] if matches else None


def _coerce_layer_id(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _supports_custom_pad_layer(
    pad: object,
    layer: int | None,
    *,
    include_inner_multilayer: bool = False,
) -> bool:
    if layer is None:
        return False

    source_layer = _coerce_layer_id(getattr(pad, "layer", None))
    if source_layer is None:
        return False

    if layer in {
        PcbLayer.TOP.value,
        PcbLayer.TOP_PASTE.value,
        PcbLayer.TOP_SOLDER.value,
    }:
        return source_layer in {PcbLayer.TOP.value, PcbLayer.MULTI_LAYER.value}

    if layer in {
        PcbLayer.BOTTOM.value,
        PcbLayer.BOTTOM_PASTE.value,
        PcbLayer.BOTTOM_SOLDER.value,
    }:
        return source_layer in {PcbLayer.BOTTOM.value, PcbLayer.MULTI_LAYER.value}

    if source_layer == PcbLayer.MULTI_LAYER.value and include_inner_multilayer:
        return 2 <= int(layer) <= 31

    return layer == source_layer


def _custom_layer_candidates_for_pad(pad: object) -> list[int]:
    source_layer = _coerce_layer_id(getattr(pad, "layer", None))
    if source_layer == PcbLayer.TOP.value:
        return [PcbLayer.TOP.value, PcbLayer.TOP_PASTE.value, PcbLayer.TOP_SOLDER.value]
    if source_layer == PcbLayer.BOTTOM.value:
        return [
            PcbLayer.BOTTOM.value,
            PcbLayer.BOTTOM_PASTE.value,
            PcbLayer.BOTTOM_SOLDER.value,
        ]
    if source_layer == PcbLayer.MULTI_LAYER.value:
        return [
            PcbLayer.TOP.value,
            PcbLayer.BOTTOM.value,
            PcbLayer.TOP_PASTE.value,
            PcbLayer.BOTTOM_PASTE.value,
            PcbLayer.TOP_SOLDER.value,
            PcbLayer.BOTTOM_SOLDER.value,
        ]
    return []


def _region_pad_index_zero_based(region: object) -> int | None:
    props = getattr(region, "properties", {}) or {}
    pad_index = _parse_int_token(props.get("PADINDEX"))
    if pad_index is None or pad_index <= 0:
        return None
    return int(pad_index) - 1


def _attach_explicit_padindex_regions(
    *,
    pads: list[object],
    regions: list[object],
    shape_regions: list[object] | None,
    source: str,
    include_inner_multilayer: bool = False,
) -> None:
    for region in regions:
        pad_index = _region_pad_index_zero_based(region)
        if pad_index is None or not (0 <= pad_index < len(pads)):
            continue
        pad = pads[pad_index]
        layer = _coerce_layer_id(getattr(region, "layer", None))
        if not _supports_custom_pad_layer(
            pad,
            layer,
            include_inner_multilayer=include_inner_multilayer,
        ):
            continue
        shape_region = None
        if shape_regions:
            shape_region = find_best_pad_region(shape_regions, pad, target_layer=layer)
        attach_custom_pad_shape(
            pad,
            source=source,
            region=region,
            shape_region=shape_region,
            pad_index=pad_index,
            layer=layer,
        )


def _attach_derived_layer_regions(
    *,
    pads: list[object],
    regions: list[object],
    shape_regions: list[object] | None,
    source: str,
) -> None:
    for pad_index, pad in enumerate(pads):
        custom_shape = getattr(pad, "custom_shape", None)
        if custom_shape is None:
            continue
        for layer in _custom_layer_candidates_for_pad(pad):
            if custom_shape.get_layer_shape(layer) is not None:
                continue
            region = find_best_pad_region(regions, pad, target_layer=layer)
            if region is None:
                continue
            shape_region = None
            if shape_regions:
                shape_region = find_best_pad_region(
                    shape_regions, pad, target_layer=layer
                )
            attach_custom_pad_shape(
                pad,
                source=source,
                region=region,
                shape_region=shape_region,
                pad_index=pad_index,
                layer=layer,
            )


def attach_custom_pad_shape(
    pad: object,
    *,
    source: str,
    region: object | None,
    shape_region: object | None = None,
    record: object | None = None,
    pad_index: int | None = None,
    shape_kind: int | None = 10,
    arcs: list[object] | None = None,
    layer: int | None = None,
) -> None:
    """
    Attach first-class custom-pad semantics to a pad.
    """
    if region is None:
        return
    layer_id = _coerce_layer_id(layer)
    if layer_id is None:
        layer_id = _coerce_layer_id(getattr(region, "layer", None))
    if not _supports_custom_pad_layer(pad, layer_id):
        return

    custom_shape = getattr(pad, "custom_shape", None)
    if not isinstance(custom_shape, AltiumPcbCustomPadShape):
        custom_shape = AltiumPcbCustomPadShape(
            source=source,
            anchor_pad_index=pad_index,
        )
        pad.custom_shape = custom_shape

    if pad_index is not None:
        custom_shape.anchor_pad_index = int(pad_index)
    custom_shape.add_layer_shape(
        layer=layer_id,
        shape_kind=shape_kind,
        source_record=record,
        region=region,
        shape_region=shape_region,
        arcs=list(arcs or []),
    )


def resolve_pcbdoc_custom_pad_shapes(pcbdoc: object) -> None:
    """
    Attach semantic custom-pad objects to parsed `PcbDoc` pads.
    """
    pads = list(getattr(pcbdoc, "pads", []) or [])
    regions = list(getattr(pcbdoc, "regions", []) or [])
    shape_regions = list(getattr(pcbdoc, "shapebased_regions", []) or [])
    for pad in pads:
        pad.custom_shape = None

    for record in list(getattr(pcbdoc, "custom_shapes", []) or []):
        pad_index = record.primitive_index
        if pad_index is None or not (0 <= pad_index < len(pads)):
            continue
        pad = pads[pad_index]
        matches = _iter_pad_regions(regions, pad)
        attach_custom_pad_shape(
            pad,
            source="pcbdoc_stream",
            region=(matches[0] if matches else None),
            shape_region=find_best_pad_region(shape_regions, pad),
            record=record,
            pad_index=pad_index,
        )
    _attach_explicit_padindex_regions(
        pads=pads,
        regions=regions,
        shape_regions=shape_regions,
        source="pcbdoc_region_padindex",
        include_inner_multilayer=True,
    )
    _attach_derived_layer_regions(
        pads=pads,
        regions=regions,
        shape_regions=shape_regions,
        source="pcbdoc_region_inferred",
    )


def _is_pcblib_custom_pad_anchor(pad: object) -> bool:
    layer = int(getattr(pad, "layer", 0) or 0)
    if layer not in {1, 32, 74}:
        return False
    if int(getattr(pad, "hole_size", 0) or 0) != 0:
        return False
    width = int(getattr(pad, "top_width", 0) or 0)
    height = int(getattr(pad, "top_height", 0) or 0)
    return 0 < width <= 10000 and 0 < height <= 10000


def resolve_pcblib_custom_pad_shapes(footprint: object) -> None:
    """
    Infer semantic custom-pad objects for a parsed `PcbLib` footprint.
    """
    pads = list(getattr(footprint, "pads", []) or [])
    regions = list(getattr(footprint, "regions", []) or [])
    for pad in pads:
        pad.custom_shape = None

    _attach_explicit_padindex_regions(
        pads=pads,
        regions=regions,
        shape_regions=None,
        source="pcblib_inferred",
    )
    _attach_derived_layer_regions(
        pads=pads,
        regions=regions,
        shape_regions=None,
        source="pcblib_inferred",
    )

    for pad_index, pad in enumerate(pads):
        if getattr(pad, "custom_shape", None) is not None:
            continue
        if not _is_pcblib_custom_pad_anchor(pad):
            continue
        matches = _iter_pad_regions(regions, pad)
        attach_custom_pad_shape(
            pad,
            source="pcblib_inferred",
            region=(matches[0] if matches else None),
            pad_index=pad_index,
        )


def _format_mil_token_from_internal(value_iu: int | float | None) -> str:
    value = float(value_iu or 0) / 10000.0
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return f"{int(rounded)}mil"
    return f"{value:.6f}".rstrip("0").rstrip(".") + "mil"


def _format_angle_token(value: float | int | None) -> str:
    text = f"{float(value or 0.0):.14E}"
    mantissa, exponent = text.split("E", 1)
    sign = exponent[0]
    digits = exponent[1:].zfill(4)
    return f"{mantissa}E{sign}{digits}"


def build_pcblib_custom_pad_region_properties(
    *,
    region: object,
    shape_region: object,
    pad_index: int,
    include_pad_index: bool = True,
) -> dict[str, str]:
    """
    Build the footprint-local custom-pad region property contract Altium expects.
    """
    props = {
        str(k): str(v).replace("\x00", "")
        for k, v in (getattr(region, "properties", {}) or {}).items()
    }
    try:
        props["V7_LAYER"] = PcbLayer(
            int(getattr(region, "layer", 0) or 0)
        ).to_json_name()
    except (TypeError, ValueError):
        pass
    if include_pad_index:
        props["PADINDEX"] = str(int(pad_index))
    else:
        props.pop("PADINDEX", None)

    outline = list(getattr(shape_region, "outline", None) or [])
    if len(outline) >= 2:
        first = outline[0]
        last = outline[-1]
        if int(getattr(first, "x", 0)) == int(getattr(last, "x", 0)) and int(
            getattr(first, "y", 0)
        ) == int(getattr(last, "y", 0)):
            outline = outline[:-1]

    props["MAINCONTOURVERTEXCOUNT"] = str(len(outline))
    for index, vertex in enumerate(outline):
        is_round = bool(getattr(vertex, "is_round", False))
        props[f"KIND{index}"] = "1" if is_round else "0"
        props[f"VX{index}"] = _format_mil_token_from_internal(getattr(vertex, "x", 0))
        props[f"VY{index}"] = _format_mil_token_from_internal(getattr(vertex, "y", 0))
        props[f"CX{index}"] = _format_mil_token_from_internal(
            getattr(vertex, "center_x", 0) if is_round else 0
        )
        props[f"CY{index}"] = _format_mil_token_from_internal(
            getattr(vertex, "center_y", 0) if is_round else 0
        )
        props[f"SA{index}"] = _format_angle_token(
            getattr(vertex, "start_angle", 0.0) if is_round else 0.0
        )
        props[f"EA{index}"] = _format_angle_token(
            getattr(vertex, "end_angle", 0.0) if is_round else 0.0
        )
        props[f"R{index}"] = _format_mil_token_from_internal(
            getattr(vertex, "radius", 0) if is_round else 0
        )

    return props


def build_pcblib_custom_pad_extended_info(
    *,
    primitive_index: int,
    pad: object,
    layer: int | None = None,
    has_explicit_paste_region: bool = False,
) -> "AltiumPcbExtendedPrimitiveInformation":
    """
    Build the footprint-local mask metadata record used by custom-pad regions.
    """
    from .altium_pcb_extended_primitive_information import (
        AltiumPcbExtendedPrimitiveInformation,
    )

    item = AltiumPcbExtendedPrimitiveInformation()
    item.primitive_index = int(primitive_index)
    item.primitive_object_id = "Region"
    item.info_type = "Mask"

    base_layer = PcbLayer.TOP
    try:
        layer_enum = PcbLayer(int(layer)) if layer is not None else None
    except (TypeError, ValueError):
        layer_enum = None
    if layer_enum is not None and layer_enum.is_bottom_side():
        base_layer = PcbLayer.BOTTOM
    elif _coerce_layer_id(getattr(pad, "layer", None)) == PcbLayer.BOTTOM.value:
        base_layer = PcbLayer.BOTTOM

    if int(getattr(pad, "hole_size", 0) or 0) > 0 or has_explicit_paste_region:
        item.paste_mask_expansion_mode = "None"
        item.paste_mask_expansion_manual_token = ""
    else:
        item.paste_mask_expansion_mode = "Manual"
        item.paste_mask_expansion_manual_token = _format_mil_token_from_internal(
            get_pad_paste_expansion_iu(pad)
        )

    item.solder_mask_expansion_mode = "Manual"
    item.solder_mask_expansion_manual_token = _format_mil_token_from_internal(
        get_pad_mask_expansion_iu(pad)
    )
    item._sync_typed_fields_to_properties()
    item._typed_signature_at_parse = item._typed_signature()
    item._properties_raw_signature = item._properties_signature()
    item.raw_record_payload = None
    return item
