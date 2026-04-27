"""Schematic record model for SchRecordType.NET_LABEL."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import (
    SchGraphicalObject,
    SchRecordType,
    TextJustification,
    TextOrientation,
    color_to_hex,
)
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_sch_record_helpers import rotate_point_about_origin
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_svg_renderer import SchSvgRenderContext
from .altium_text_metrics import (
    get_baseline_offset,
    measure_text_height,
    measure_text_width,
)


class AltiumSchNetLabel(SingleFontBindableRecordMixin, SchGraphicalObject):
    """
    Net label record.

    Labels attached to wires/nets to assign net names.
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        self._use_pascal_case: bool = True
        self.text: str = ""
        self.font_id: int = 1
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.justification: TextJustification = TextJustification.BOTTOM_LEFT
        self.is_mirrored: bool = False
        self._used_utf8_text: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.NET_LABEL

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse from a record.

                Args:
                   record: Source record dictionary
                    font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record)
        self._font_manager = font_manager
        self._public_font_spec = None

        # Detect case mode for round-trip fidelity
        self._use_pascal_case = "Text" in record or "FontID" in record

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()
        r = self._record

        self.text, _, self._used_utf8_text = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT,
            default="",
        )
        # Use read_font_id for translation support
        self.font_id, _ = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=1
        )
        orient_val, _ = s.read_int(record, Fields.ORIENTATION, default=0)
        self.orientation = TextOrientation(orient_val)
        justify_val, _ = s.read_int(record, Fields.JUSTIFICATION, default=0)
        self.justification = TextJustification(justify_val)
        self.is_mirrored, _ = s.read_bool(record, Fields.IS_MIRRORED, default=False)

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()

        # Determine case mode
        mode = (
            CaseMode.PASCALCASE
            if getattr(self, "_use_pascal_case", False)
            else CaseMode.UPPERCASE
        )
        s = AltiumSerializer(mode)
        raw = self._raw_record

        write_dynamic_string_field(
            s,
            record,
            Fields.TEXT,
            self.text,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_text,
            was_present=True,
        )
        s.write_int(record, Fields.FONT_ID, self.font_id, raw)
        if self.orientation.value != 0:
            s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)
        else:
            s.remove_field(record, Fields.ORIENTATION)
        if self.justification.value != 0:
            s.write_int(record, Fields.JUSTIFICATION, self.justification.value, raw)
        else:
            s.remove_field(record, Fields.JUSTIFICATION)
        if self.is_mirrored:
            s.write_bool(record, Fields.IS_MIRRORED, self.is_mirrored, raw)
        else:
            s.remove_field(record, Fields.IS_MIRRORED)

        return record

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for this net label.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_font_payload,
            make_pen,
            make_solid_brush,
            make_text_with_overline_operations,
            split_overline_text,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if getattr(self, "is_hidden", False) or not self.text:
            return None

        display_text = ctx.substitute_parameters(self.text)
        if not display_text:
            return None

        baseline_x, baseline_y = ctx.transform_coord_precise(self.location)
        baseline_x, baseline_y = round(baseline_x, 3), round(baseline_y, 3)

        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            self.font_id
        )
        line_height = ctx.get_font_line_height(self.font_id)
        clean_text, _ = split_overline_text(display_text)
        if not clean_text:
            return None

        baseline_x, baseline_y, text_width, rotation_deg = self._compute_text_layout(
            ctx,
            clean_text,
            font_name,
            font_size_px,
            line_height,
            is_bold,
            is_italic,
            baseline_x,
            baseline_y,
        )
        text_height = measure_text_height(
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
            use_altium_algorithm=False,
        )
        min_x, max_x, min_y, max_y = self._compute_text_bounds(
            baseline_x,
            baseline_y,
            text_width,
            text_height,
            font_size_px,
            rotation_deg,
        )

        font_spec = (
            ctx.font_manager.get_font_info(self.font_id) if ctx.font_manager else None
        )
        font_payload = make_font_payload(
            name=str(font_spec.get("name", font_name)) if font_spec else str(font_name),
            size_px=font_size_px,
            units_per_px=units_per_px,
            rotation=rotation_deg,
            underline=bool(font_spec.get("underline", is_underline))
            if font_spec
            else bool(is_underline),
            italic=bool(font_spec.get("italic", is_italic))
            if font_spec
            else bool(is_italic),
            bold=bool(font_spec.get("bold", is_bold)) if font_spec else bool(is_bold),
            strikeout=bool(font_spec.get("strikeout", False)) if font_spec else False,
        )
        fill_color_raw = self._resolve_fill_color(ctx)

        operations = make_text_with_overline_operations(
            text=display_text,
            baseline_x_px=baseline_x,
            baseline_y_px=baseline_y,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            font_payload=font_payload,
            font_size_px=font_size_px,
            font_name=font_name,
            bold=is_bold,
            italic=is_italic,
            brush_color_raw=fill_color_raw,
            rotation_deg=rotation_deg,
            units_per_px=units_per_px,
        )
        connection_point = svg_coord_to_geometry(
            *ctx.transform_coord_precise(self.location),
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )

        junction_ops, junction_bounds = self._build_junction_overlay(
            ctx,
            units_per_px,
            SchGeometryOp,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
        )
        if junction_ops:
            operations.extend(junction_ops)
            junction_min_x, junction_max_x, junction_min_y, junction_max_y = (
                junction_bounds
            )
            min_x = min(min_x, junction_min_x)
            max_x = max(max_x, junction_max_x)
            min_y = min(min_y, junction_min_y)
            max_y = max(max_y, junction_max_y)

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="netlabel",
            object_id="eNetLabel",
            bounds=SchGeometryBounds(
                left=math.floor(min_x * 100000),
                top=math.floor((float(ctx.sheet_height or 0.0) - min_y) * 100000),
                right=math.ceil(max_x * 100000),
                bottom=math.ceil((float(ctx.sheet_height or 0.0) - max_y) * 100000),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
            extras={
                "connection_points": [
                    {
                        "id": "net-label-hotspot",
                        "kind": "connection",
                        "role": "ratsnest-anchor",
                        "point": [connection_point[0], connection_point[1]],
                        "source_kind": "net_label_hotspot",
                    }
                ]
            },
        )

    def _compute_text_layout(
        self,
        ctx: "SchSvgRenderContext",
        clean_text: str,
        font_name: str,
        font_size_px: float,
        line_height: float,
        is_bold: bool,
        is_italic: bool,
        baseline_x: float,
        baseline_y: float,
    ) -> tuple[float, float, float, float]:
        text_width = measure_text_width(
            clean_text,
            ctx.get_font_size_for_width(self.font_id),
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        h_offset, v_offset = self._compute_alignment_offsets(
            text_width,
            line_height,
            font_size_px,
            font_name,
        )
        return self._apply_orientation_offsets(
            baseline_x,
            baseline_y,
            h_offset,
            v_offset,
            text_width,
        )

    def _compute_alignment_offsets(
        self,
        text_width: float,
        line_height: float,
        font_size_px: float,
        font_name: str,
    ) -> tuple[float, float]:
        justification = self.justification.value
        h_align = justification % 3
        v_align = justification // 3
        baseline_offset = get_baseline_offset(font_size_px, font_name)

        if h_align == 1:
            h_offset = text_width / 2.0
        elif h_align == 2:
            h_offset = text_width
        else:
            h_offset = 0.0

        if v_align == 0:
            v_offset = baseline_offset
        elif v_align == 1:
            v_offset = -(line_height / 2.0 - baseline_offset)
        elif v_align == 2:
            v_offset = -(line_height - baseline_offset)
        else:
            v_offset = 0.0

        return h_offset, v_offset

    def _apply_orientation_offsets(
        self,
        baseline_x: float,
        baseline_y: float,
        h_offset: float,
        v_offset: float,
        text_width: float,
    ) -> tuple[float, float, float, float]:
        angle = self.orientation.value * 90
        if angle == 0:
            baseline_x -= h_offset
            baseline_y -= v_offset
        elif angle == 90:
            baseline_y += h_offset
            baseline_x -= v_offset
        elif angle == 180:
            baseline_x += h_offset
            baseline_y += v_offset
        elif angle == 270:
            baseline_y -= h_offset
            baseline_x += v_offset
        return baseline_x, baseline_y, text_width, float(-angle)

    def _compute_text_bounds(
        self,
        baseline_x: float,
        baseline_y: float,
        text_width: float,
        text_height: float,
        font_size_px: float,
        rotation_deg: float,
    ) -> tuple[float, float, float, float]:
        baseline_font_size = float(int(font_size_px))
        theta = math.radians(rotation_deg)
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)

        def rotate_point(px: float, py: float) -> tuple[float, float]:
            return rotate_point_about_origin(
                px,
                py,
                origin_x=baseline_x,
                origin_y=baseline_y,
                cos_theta=cos_theta,
                sin_theta=sin_theta,
            )

        unrotated_top = baseline_y - baseline_font_size
        corners = [
            rotate_point(baseline_x, unrotated_top),
            rotate_point(baseline_x + text_width, unrotated_top),
            rotate_point(baseline_x + text_width, unrotated_top + text_height),
            rotate_point(baseline_x, unrotated_top + text_height),
        ]
        return (
            min(point[0] for point in corners),
            max(point[0] for point in corners),
            min(point[1] for point in corners),
            max(point[1] for point in corners),
        )

    def _resolve_fill_color(self, ctx: "SchSvgRenderContext") -> int:
        fill_color_raw = int(self.color) if self.color is not None else 0
        wire_mask_state = ctx.get_connected_wire_mask_state(
            self.location.x, self.location.y
        )
        should_mask = (
            ctx.is_under_compile_mask(self.location.x, self.location.y)
            if wire_mask_state is None
            else wire_mask_state
        )
        if not should_mask:
            return fill_color_raw

        masked_hex = ctx.get_masked_color(color_to_hex(fill_color_raw)).lstrip("#")
        return (
            int(masked_hex[0:2], 16)
            | (int(masked_hex[2:4], 16) << 8)
            | (int(masked_hex[4:6], 16) << 16)
        )

    def _build_junction_overlay(
        self,
        ctx: "SchSvgRenderContext",
        units_per_px: int,
        geometry_op_cls: Any,
        make_pen: Any,
        make_solid_brush: Any,
        svg_coord_to_geometry: Any,
    ) -> tuple[list[Any], tuple[float, float, float, float] | None]:
        if not (
            getattr(ctx, "native_svg_export", False)
            and (self.location.x, self.location.y) in ctx.connection_points
        ):
            return [], None

        junction_x, junction_y = ctx.transform_point(self.location.x, self.location.y)
        junction_x1, junction_y1 = svg_coord_to_geometry(
            junction_x - 2 * ctx.scale,
            junction_y - 2 * ctx.scale,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        junction_x2, junction_y2 = svg_coord_to_geometry(
            junction_x + 2 * ctx.scale,
            junction_y + 2 * ctx.scale,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        junction_color_raw = 0x000000
        ops = [
            geometry_op_cls.rounded_rectangle(
                x1=junction_x1,
                y1=junction_y1,
                x2=junction_x2,
                y2=junction_y2,
                corner_x_radius=2 * ctx.scale * units_per_px,
                corner_y_radius=2 * ctx.scale * units_per_px,
                brush=make_solid_brush(junction_color_raw),
            ),
            geometry_op_cls.rounded_rectangle(
                x1=junction_x1,
                y1=junction_y1,
                x2=junction_x2,
                y2=junction_y2,
                corner_x_radius=2 * ctx.scale * units_per_px,
                corner_y_radius=2 * ctx.scale * units_per_px,
                pen=make_pen(junction_color_raw, width=0),
            ),
        ]
        return ops, (
            junction_x - 2 * ctx.scale,
            junction_x + 2 * ctx.scale,
            junction_y - 2 * ctx.scale,
            junction_y + 2 * ctx.scale,
        )
