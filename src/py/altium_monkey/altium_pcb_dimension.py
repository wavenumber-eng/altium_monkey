"""
Typed PCB dimension model for Dimensions6/Data.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from .altium_pcb_property_helpers import (
    clean_pcb_property_text as _clean_text,
    parse_pcb_int_token as _parse_int_token,
    parse_pcb_mils_token_as_internal as _parse_mils_token_as_internal,
    parse_pcb_property_payload,
    PcbPropertyRecordMixin,
    set_pcb_text_property,
)
from .altium_utilities import encode_altium_record


def _parse_bool_token(value: object) -> bool | None:
    text = _clean_text(value).upper()
    if not text:
        return None
    if text in {"TRUE", "T", "1", "YES"}:
        return True
    if text in {"FALSE", "F", "0", "NO"}:
        return False
    return None


def _format_bool_token(value: bool | None) -> str:
    if value is None:
        return ""
    return "TRUE" if value else "FALSE"


def _parse_float_token(value: object) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _decode_univ_chars(raw_value: object) -> str:
    text = _clean_text(raw_value)
    if not text:
        return ""
    chars: list[str] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            code = int(token)
        except (TypeError, ValueError):
            continue
        if 0 <= code <= 0x10FFFF:
            chars.append(chr(code))
    return "".join(chars)


@dataclass
class AltiumPcbDimensionReference:
    """
    Typed REFERENCE{N}POINT* payload.
    """

    anchor: str = ""
    object_id: str = ""
    object_string: str = ""
    primitive_ref: str = ""
    point_x_token: str = ""
    point_y_token: str = ""

    @property
    def point_x(self) -> int | None:
        return _parse_mils_token_as_internal(self.point_x_token)

    @property
    def point_y(self) -> int | None:
        return _parse_mils_token_as_internal(self.point_y_token)


@dataclass
class AltiumPcbDimensionTextPoint:
    """
    Typed TEXT{N}* placement payload.
    """

    index: int = 0
    x_token: str = ""
    y_token: str = ""
    angle_token: str = ""
    mirror_token: str = ""

    @property
    def x(self) -> int | None:
        return _parse_mils_token_as_internal(self.x_token)

    @property
    def y(self) -> int | None:
        return _parse_mils_token_as_internal(self.y_token)

    @property
    def angle_deg(self) -> float | None:
        return _parse_float_token(self.angle_token)

    @property
    def mirrored(self) -> bool | None:
        return _parse_bool_token(self.mirror_token)


class AltiumPcbDimension(PcbPropertyRecordMixin):
    """
    Typed Dimensions6 record with raw-payload-preserving roundtrip.
    """

    KIND_NAMES = {
        0: "unknown",
        1: "linear",
        2: "angular",
        3: "radial",
        4: "leader",
        5: "datum",
        6: "baseline",
        7: "center",
        8: "unknown_2",
        9: "linear_diameter",
        10: "radial_diameter",
    }

    def __init__(self) -> None:
        self.record_type: int = 0
        self.record_leader: int = 0
        self.properties: dict[str, str] = {}

        self.selection: bool | None = None
        self.layer_token: str = ""
        self.layer_v7_token: str = ""
        self.dimension_layer_token: str = ""
        self.dimension_layer_v7_token: str = ""
        self.locked: bool | None = None
        self.polygon_outline: bool | None = None
        self.user_routed: bool | None = None
        self.keepout: bool | None = None
        self.union_index: int | None = None
        self.primitive_lock: bool | None = None
        self.dimension_locked: bool | None = None
        self.object_id: int | None = None
        self.dimension_kind: int = 0
        self.drc_error: bool | None = None
        self.vindex_for_save: int | None = None

        self.lx_token: str = ""
        self.ly_token: str = ""
        self.hx_token: str = ""
        self.hy_token: str = ""
        self.x1_token: str = ""
        self.y1_token: str = ""
        self.x2_token: str = ""
        self.y2_token: str = ""

        self.text_x_token: str = ""
        self.text_y_token: str = ""
        self.height_token: str = ""
        self.angle_token: str = ""
        self.line_width_token: str = ""
        self.text_height_token: str = ""
        self.text_width_token: str = ""
        self.text_line_width_token: str = ""
        self.text_precision: int | None = None
        self.text_gap_token: str = ""
        self.arrow_size_token: str = ""
        self.arrow_line_width_token: str = ""
        self.arrow_length_token: str = ""
        self.arrow_position: str = ""
        self.extension_offset_token: str = ""
        self.extension_line_width_token: str = ""
        self.extension_pick_gap_token: str = ""
        self.angle_step_token: str = ""

        self.font: str = ""
        self.font_name: str = ""
        self.style: str = ""
        self.text_position_mode: str = ""
        self.text_format: str = ""
        self.text_dimension_unit: str = ""
        self.text_value_token: str = ""
        self.text_token: str = ""
        self.use_ttf_fonts: bool | None = None
        self.bold: bool | None = None
        self.italic: bool | None = None
        self.unicode_marker: str = ""
        self.text_prefix_token: str = ""
        self.text_prefix_univ_token: str = ""
        self.text_suffix_token: str = ""
        self.text_suffix_univ_token: str = ""
        self.unicode_text_prefix: str = ""
        self.unicode_text_suffix: str = ""

        self.references: list[AltiumPcbDimensionReference] = []
        self.text_points: list[AltiumPcbDimensionTextPoint] = []

        self.raw_record_payload: bytes | None = None
        self._properties_raw_signature: tuple | None = None
        self._typed_signature_at_parse: tuple | None = None

    @classmethod
    def from_stream_record(
        cls,
        *,
        record_type: int,
        record_leader: int,
        record_payload: bytes,
    ) -> "AltiumPcbDimension":
        item = cls()
        item.record_type = int(record_type)
        item.record_leader = int(record_leader)
        item.raw_record_payload = bytes(record_payload)
        item.properties = parse_pcb_property_payload(record_payload)
        item._load_typed_fields_from_properties()
        item._properties_raw_signature = item._properties_field_signature()
        item._typed_signature_at_parse = item._typed_signature()
        return item

    @property
    def kind_name(self) -> str:
        kind_value = int(self.dimension_kind or 0) or int(self.record_type or 0)
        return self.KIND_NAMES.get(kind_value, "unknown")

    @property
    def x1(self) -> int | None:
        return _parse_mils_token_as_internal(self.x1_token)

    @property
    def y1(self) -> int | None:
        return _parse_mils_token_as_internal(self.y1_token)

    @property
    def x2(self) -> int | None:
        return _parse_mils_token_as_internal(self.x2_token)

    @property
    def y2(self) -> int | None:
        return _parse_mils_token_as_internal(self.y2_token)

    @property
    def text_x(self) -> int | None:
        return _parse_mils_token_as_internal(self.text_x_token)

    @property
    def text_y(self) -> int | None:
        return _parse_mils_token_as_internal(self.text_y_token)

    @property
    def height(self) -> int | None:
        return _parse_mils_token_as_internal(self.height_token)

    @property
    def angle_deg(self) -> float | None:
        return _parse_float_token(self.angle_token)

    @property
    def line_width(self) -> int | None:
        return _parse_mils_token_as_internal(self.line_width_token)

    @property
    def text_height(self) -> int | None:
        return _parse_mils_token_as_internal(self.text_height_token)

    @property
    def text_width(self) -> int | None:
        return _parse_mils_token_as_internal(self.text_width_token)

    @property
    def text_line_width(self) -> int | None:
        return _parse_mils_token_as_internal(self.text_line_width_token)

    @property
    def text_gap(self) -> int | None:
        return _parse_mils_token_as_internal(self.text_gap_token)

    @property
    def arrow_size(self) -> int | None:
        return _parse_mils_token_as_internal(self.arrow_size_token)

    @property
    def arrow_line_width(self) -> int | None:
        return _parse_mils_token_as_internal(self.arrow_line_width_token)

    @property
    def arrow_length(self) -> int | None:
        return _parse_mils_token_as_internal(self.arrow_length_token)

    @property
    def extension_offset(self) -> int | None:
        return _parse_mils_token_as_internal(self.extension_offset_token)

    @property
    def extension_line_width(self) -> int | None:
        return _parse_mils_token_as_internal(self.extension_line_width_token)

    @property
    def extension_pick_gap(self) -> int | None:
        return _parse_mils_token_as_internal(self.extension_pick_gap_token)

    @property
    def angle_step(self) -> float | None:
        return _parse_float_token(self.angle_step_token)

    @property
    def text_value(self) -> float | None:
        return _parse_float_token(self.text_value_token)

    @property
    def decoded_text_prefix(self) -> str:
        return _decode_univ_chars(self.text_prefix_univ_token) or self.text_prefix_token

    @property
    def decoded_text_suffix(self) -> str:
        return _decode_univ_chars(self.text_suffix_univ_token) or self.text_suffix_token

    def primary_text_point(self) -> AltiumPcbDimensionTextPoint | None:
        if self.text_points:
            for point in self.text_points:
                if point.index == 1:
                    return point
            return self.text_points[0]
        if self.text_x_token or self.text_y_token:
            return AltiumPcbDimensionTextPoint(
                index=0, x_token=self.text_x_token, y_token=self.text_y_token
            )
        return None

    def measured_value_mm(self) -> float | None:
        kind = self.kind_name
        if kind == "linear":
            if len(self.references) >= 2:
                x0 = self.references[0].point_x
                y0 = self.references[0].point_y
                x1 = self.references[1].point_x
                y1 = self.references[1].point_y
                if None not in (x0, y0, x1, y1):
                    return math.hypot(float(x1 - x0), float(y1 - y0)) / 10000.0 * 0.0254
            if None not in (self.x1, self.y1, self.x2, self.y2):
                return (
                    math.hypot(float(self.x2 - self.x1), float(self.y2 - self.y1))
                    / 10000.0
                    * 0.0254
                )
            return None
        if kind in {"radial", "radial_diameter"}:
            if self.references:
                ref = self.references[0]
                if None not in (ref.point_x, ref.point_y, self.x1, self.y1):
                    radius_mm = (
                        math.hypot(
                            float(self.x1 - ref.point_x), float(self.y1 - ref.point_y)
                        )
                        / 10000.0
                        * 0.0254
                    )
                    if kind == "radial_diameter":
                        return radius_mm * 2.0
                    return radius_mm
        return None

    def formatted_text(self) -> str:
        if self.text_token:
            return self.text_token
        if self.text_value is not None:
            value = self.text_value_token or format(self.text_value, "g")
            return f"{self.decoded_text_prefix}{value}{self.decoded_text_suffix}"
        measured = self.measured_value_mm()
        if measured is not None and self.kind_name in {
            "linear",
            "radial",
            "radial_diameter",
        }:
            precision = (
                2
                if self.text_precision is None
                else max(0, min(6, int(self.text_precision)))
            )
            return f"{self.decoded_text_prefix}{measured:.{precision}f}{self.decoded_text_suffix}"
        if self.text_format:
            return self.text_format
        return f"{self.decoded_text_prefix}{self.decoded_text_suffix}".strip()

    def can_passthrough_raw_payload(self) -> bool:
        return (
            self.raw_record_payload is not None
            and self._properties_raw_signature is not None
            and self._properties_raw_signature == self._properties_field_signature()
        )

    def serialize_record_payload(self) -> bytes:
        if self._typed_signature_at_parse != self._typed_signature():
            self._sync_typed_fields_to_properties()
        if self.can_passthrough_raw_payload() and self.raw_record_payload is not None:
            return bytes(self.raw_record_payload)
        encoded = encode_altium_record(self.properties)
        return encoded[4:]

    def _load_typed_fields_from_properties(self) -> None:
        props = self.properties or {}
        self.selection = _parse_bool_token(props.get("SELECTION", ""))
        self.layer_token = _clean_text(props.get("LAYER", ""))
        self.layer_v7_token = _clean_text(props.get("LAYER_V7", ""))
        self.dimension_layer_token = _clean_text(props.get("DIMENSIONLAYER", ""))
        self.dimension_layer_v7_token = _clean_text(props.get("DIMENSIONLAYER_V7", ""))
        self.locked = _parse_bool_token(props.get("LOCKED", ""))
        self.polygon_outline = _parse_bool_token(props.get("POLYGONOUTLINE", ""))
        self.user_routed = _parse_bool_token(props.get("USERROUTED", ""))
        self.keepout = _parse_bool_token(props.get("KEEPOUT", ""))
        self.union_index = _parse_int_token(props.get("UNIONINDEX", ""))
        self.primitive_lock = _parse_bool_token(props.get("PRIMITIVELOCK", ""))
        self.dimension_locked = _parse_bool_token(props.get("DIMENSIONLOCKED", ""))
        self.object_id = _parse_int_token(props.get("OBJECTID", ""))
        self.dimension_kind = int(
            _parse_int_token(props.get("DIMENSIONKIND", "")) or self.record_type or 0
        )
        self.drc_error = _parse_bool_token(props.get("DRCERROR", ""))
        self.vindex_for_save = _parse_int_token(props.get("VINDEXFORSAVE", ""))

        self.lx_token = _clean_text(props.get("LX", ""))
        self.ly_token = _clean_text(props.get("LY", ""))
        self.hx_token = _clean_text(props.get("HX", ""))
        self.hy_token = _clean_text(props.get("HY", ""))
        self.x1_token = _clean_text(props.get("X1", ""))
        self.y1_token = _clean_text(props.get("Y1", ""))
        self.x2_token = _clean_text(props.get("X2", ""))
        self.y2_token = _clean_text(props.get("Y2", ""))
        self.text_x_token = _clean_text(props.get("TEXTX", ""))
        self.text_y_token = _clean_text(props.get("TEXTY", ""))
        self.height_token = _clean_text(props.get("HEIGHT", ""))
        self.angle_token = _clean_text(props.get("ANGLE", ""))
        self.line_width_token = _clean_text(props.get("LINEWIDTH", ""))
        self.text_height_token = _clean_text(props.get("TEXTHEIGHT", ""))
        self.text_width_token = _clean_text(props.get("TEXTWIDTH", ""))
        self.text_line_width_token = _clean_text(props.get("TEXTLINEWIDTH", ""))
        self.text_precision = _parse_int_token(props.get("TEXTPRECISION", ""))
        self.text_gap_token = _clean_text(props.get("TEXTGAP", ""))
        self.arrow_size_token = _clean_text(props.get("ARROWSIZE", ""))
        self.arrow_line_width_token = _clean_text(props.get("ARROWLINEWIDTH", ""))
        self.arrow_length_token = _clean_text(props.get("ARROWLENGTH", ""))
        self.arrow_position = _clean_text(props.get("ARROWPOSITION", ""))
        self.extension_offset_token = _clean_text(props.get("EXTENSIONOFFSET", ""))
        self.extension_line_width_token = _clean_text(
            props.get("EXTENSIONLINEWIDTH", "")
        )
        self.extension_pick_gap_token = _clean_text(props.get("EXTENSIONPICKGAP", ""))
        self.angle_step_token = _clean_text(props.get("ANGLESTEP", ""))

        self.font = _clean_text(props.get("FONT", ""))
        self.font_name = _clean_text(props.get("FONTNAME", ""))
        self.style = _clean_text(props.get("STYLE", ""))
        self.text_position_mode = _clean_text(props.get("TEXTPOSITION", ""))
        self.text_format = _clean_text(props.get("TEXTFORMAT", ""))
        self.text_dimension_unit = _clean_text(props.get("TEXTDIMENSIONUNIT", ""))
        self.text_value_token = _clean_text(props.get("TEXTVALUE", ""))
        self.text_token = _clean_text(props.get("TEXT", ""))
        self.use_ttf_fonts = _parse_bool_token(props.get("USETTFONTS", ""))
        self.bold = _parse_bool_token(props.get("BOLD", ""))
        self.italic = _parse_bool_token(props.get("ITALIC", ""))
        self.unicode_marker = _clean_text(props.get("UNICODE", ""))
        self.text_prefix_token = _clean_text(props.get("TEXTPREFIX", ""))
        self.text_prefix_univ_token = _clean_text(
            props.get("TEXTPREFIXUNIV", "") or props.get("UNICODE__TEXTPREFIX", "")
        )
        self.text_suffix_token = _clean_text(props.get("TEXTSUFFIX", ""))
        self.text_suffix_univ_token = _clean_text(
            props.get("TEXTSUFFIXUNIV", "") or props.get("UNICODE__TEXTSUFFIX", "")
        )
        self.unicode_text_prefix = _clean_text(props.get("UNICODE__TEXTPREFIX", ""))
        self.unicode_text_suffix = _clean_text(props.get("UNICODE__TEXTSUFFIX", ""))

        self.references = []
        count = max(0, int(_parse_int_token(props.get("REFERENCES_COUNT", "0")) or 0))
        for index in range(count):
            self.references.append(
                AltiumPcbDimensionReference(
                    anchor=_clean_text(props.get(f"REFERENCE{index}ANCHOR", "")),
                    object_id=_clean_text(props.get(f"REFERENCE{index}OBJECTID", "")),
                    object_string=_clean_text(
                        props.get(f"REFERENCE{index}OBJECTSTRING", "")
                    ),
                    primitive_ref=_clean_text(props.get(f"REFERENCE{index}PRIM", "")),
                    point_x_token=_clean_text(props.get(f"REFERENCE{index}POINTX", "")),
                    point_y_token=_clean_text(props.get(f"REFERENCE{index}POINTY", "")),
                )
            )

        self.text_points = []
        for index in range(1, 64):
            x_key = f"TEXT{index}X"
            y_key = f"TEXT{index}Y"
            if x_key not in props and y_key not in props:
                break
            self.text_points.append(
                AltiumPcbDimensionTextPoint(
                    index=index,
                    x_token=_clean_text(props.get(x_key, "")),
                    y_token=_clean_text(props.get(y_key, "")),
                    angle_token=_clean_text(props.get(f"TEXT{index}ANGLE", "")),
                    mirror_token=_clean_text(props.get(f"TEXT{index}MIRROR", "")),
                )
            )

    def _sync_typed_fields_to_properties(self) -> None:
        props = dict(self.properties or {})

        def _set_bool(
            key: str, value: bool | None, *, keep_if_present: bool = True
        ) -> None:
            if value is None and not (keep_if_present and key in props):
                props.pop(key, None)
                return
            props[key] = _format_bool_token(value)

        def _set_int(
            key: str, value: int | None, *, keep_if_present: bool = True
        ) -> None:
            if value is None and not (keep_if_present and key in props):
                props.pop(key, None)
                return
            props[key] = "" if value is None else str(int(value))

        _set_bool("SELECTION", self.selection)
        set_pcb_text_property(props, "LAYER", self.layer_token)
        set_pcb_text_property(props, "LAYER_V7", self.layer_v7_token)
        set_pcb_text_property(props, "DIMENSIONLAYER", self.dimension_layer_token)
        set_pcb_text_property(props, "DIMENSIONLAYER_V7", self.dimension_layer_v7_token)
        _set_bool("LOCKED", self.locked)
        _set_bool("POLYGONOUTLINE", self.polygon_outline)
        _set_bool("USERROUTED", self.user_routed)
        _set_bool("KEEPOUT", self.keepout)
        _set_int("UNIONINDEX", self.union_index)
        _set_bool("PRIMITIVELOCK", self.primitive_lock)
        _set_bool("DIMENSIONLOCKED", self.dimension_locked)
        _set_int("OBJECTID", self.object_id)
        _set_int("DIMENSIONKIND", self.dimension_kind)
        _set_bool("DRCERROR", self.drc_error)
        _set_int("VINDEXFORSAVE", self.vindex_for_save)

        for key, value in (
            ("LX", self.lx_token),
            ("LY", self.ly_token),
            ("HX", self.hx_token),
            ("HY", self.hy_token),
            ("X1", self.x1_token),
            ("Y1", self.y1_token),
            ("X2", self.x2_token),
            ("Y2", self.y2_token),
            ("TEXTX", self.text_x_token),
            ("TEXTY", self.text_y_token),
            ("HEIGHT", self.height_token),
            ("ANGLE", self.angle_token),
            ("LINEWIDTH", self.line_width_token),
            ("TEXTHEIGHT", self.text_height_token),
            ("TEXTWIDTH", self.text_width_token),
            ("TEXTLINEWIDTH", self.text_line_width_token),
            ("TEXTGAP", self.text_gap_token),
            ("ARROWSIZE", self.arrow_size_token),
            ("ARROWLINEWIDTH", self.arrow_line_width_token),
            ("ARROWLENGTH", self.arrow_length_token),
            ("ARROWPOSITION", self.arrow_position),
            ("EXTENSIONOFFSET", self.extension_offset_token),
            ("EXTENSIONLINEWIDTH", self.extension_line_width_token),
            ("EXTENSIONPICKGAP", self.extension_pick_gap_token),
            ("ANGLESTEP", self.angle_step_token),
            ("FONT", self.font),
            ("FONTNAME", self.font_name),
            ("STYLE", self.style),
            ("TEXTPOSITION", self.text_position_mode),
            ("TEXTFORMAT", self.text_format),
            ("TEXTDIMENSIONUNIT", self.text_dimension_unit),
            ("TEXTVALUE", self.text_value_token),
            ("TEXT", self.text_token),
            ("UNICODE", self.unicode_marker),
            ("TEXTPREFIX", self.text_prefix_token),
            ("TEXTPREFIXUNIV", self.text_prefix_univ_token),
            ("TEXTSUFFIX", self.text_suffix_token),
            ("TEXTSUFFIXUNIV", self.text_suffix_univ_token),
            ("UNICODE__TEXTPREFIX", self.unicode_text_prefix),
            ("UNICODE__TEXTSUFFIX", self.unicode_text_suffix),
        ):
            set_pcb_text_property(props, key, value)

        _set_int("TEXTPRECISION", self.text_precision)
        _set_bool("USETTFONTS", self.use_ttf_fonts)
        _set_bool("BOLD", self.bold)
        _set_bool("ITALIC", self.italic)

        for key in list(props):
            if key.startswith("REFERENCE") and any(
                key.endswith(suffix)
                for suffix in (
                    "ANCHOR",
                    "OBJECTID",
                    "OBJECTSTRING",
                    "PRIM",
                    "POINTX",
                    "POINTY",
                )
            ):
                props.pop(key, None)
        _set_int("REFERENCES_COUNT", len(self.references))
        for index, ref in enumerate(self.references):
            set_pcb_text_property(props, f"REFERENCE{index}ANCHOR", ref.anchor)
            set_pcb_text_property(props, f"REFERENCE{index}OBJECTID", ref.object_id)
            set_pcb_text_property(
                props, f"REFERENCE{index}OBJECTSTRING", ref.object_string
            )
            set_pcb_text_property(props, f"REFERENCE{index}PRIM", ref.primitive_ref)
            set_pcb_text_property(props, f"REFERENCE{index}POINTX", ref.point_x_token)
            set_pcb_text_property(props, f"REFERENCE{index}POINTY", ref.point_y_token)

        for key in list(props):
            if key.startswith("TEXT") and any(
                key.endswith(suffix) for suffix in ("X", "Y", "ANGLE", "MIRROR")
            ):
                tail = key[4:]
                if tail and tail[0].isdigit():
                    props.pop(key, None)
        for point in self.text_points:
            if point.index <= 0:
                continue
            set_pcb_text_property(props, f"TEXT{point.index}X", point.x_token)
            set_pcb_text_property(props, f"TEXT{point.index}Y", point.y_token)
            set_pcb_text_property(props, f"TEXT{point.index}ANGLE", point.angle_token)
            set_pcb_text_property(props, f"TEXT{point.index}MIRROR", point.mirror_token)

        self.properties = props

    def _typed_signature(self) -> tuple:
        return (
            self.record_type,
            self.record_leader,
            self.selection,
            self.layer_token,
            self.layer_v7_token,
            self.dimension_layer_token,
            self.dimension_layer_v7_token,
            self.locked,
            self.polygon_outline,
            self.user_routed,
            self.keepout,
            self.union_index,
            self.primitive_lock,
            self.dimension_locked,
            self.object_id,
            self.dimension_kind,
            self.drc_error,
            self.vindex_for_save,
            self.lx_token,
            self.ly_token,
            self.hx_token,
            self.hy_token,
            self.x1_token,
            self.y1_token,
            self.x2_token,
            self.y2_token,
            self.text_x_token,
            self.text_y_token,
            self.height_token,
            self.angle_token,
            self.line_width_token,
            self.text_height_token,
            self.text_width_token,
            self.text_line_width_token,
            self.text_precision,
            self.text_gap_token,
            self.arrow_size_token,
            self.arrow_line_width_token,
            self.arrow_length_token,
            self.arrow_position,
            self.extension_offset_token,
            self.extension_line_width_token,
            self.extension_pick_gap_token,
            self.angle_step_token,
            self.font,
            self.font_name,
            self.style,
            self.text_position_mode,
            self.text_format,
            self.text_dimension_unit,
            self.text_value_token,
            self.text_token,
            self.use_ttf_fonts,
            self.bold,
            self.italic,
            self.unicode_marker,
            self.text_prefix_token,
            self.text_prefix_univ_token,
            self.text_suffix_token,
            self.text_suffix_univ_token,
            self.unicode_text_prefix,
            self.unicode_text_suffix,
            tuple(
                (
                    item.anchor,
                    item.object_id,
                    item.object_string,
                    item.primitive_ref,
                    item.point_x_token,
                    item.point_y_token,
                )
                for item in self.references
            ),
            tuple(
                (
                    item.index,
                    item.x_token,
                    item.y_token,
                    item.angle_token,
                    item.mirror_token,
                )
                for item in self.text_points
            ),
        )

    def _properties_field_signature(self) -> tuple:
        return tuple(
            (str(key), str(value)) for key, value in (self.properties or {}).items()
        )

    def _properties_signature(self) -> tuple:
        return self._properties_field_signature()


def iter_dimensions6_records(raw: bytes) -> Iterable[tuple[int, int, bytes]]:
    """
    Yield `(record_type, leader, payload)` tuples from a Dimensions6/Data stream.
    """
    pos = 0
    total = len(raw or b"")
    while pos + 6 <= total:
        record_type = int(raw[pos])
        leader = int(raw[pos + 1])
        record_len = int.from_bytes(raw[pos + 2 : pos + 6], "little")
        pos += 6
        if record_len <= 0 or pos + record_len > total:
            break
        payload = raw[pos : pos + record_len]
        pos += record_len
        yield (record_type, leader, payload)


def parse_dimensions6_stream(raw: bytes) -> list[AltiumPcbDimension]:
    """
    Parse a Dimensions6/Data stream into typed dimension records.
    """
    return [
        AltiumPcbDimension.from_stream_record(
            record_type=record_type,
            record_leader=leader,
            record_payload=payload,
        )
        for record_type, leader, payload in iter_dimensions6_records(raw or b"")
    ]
