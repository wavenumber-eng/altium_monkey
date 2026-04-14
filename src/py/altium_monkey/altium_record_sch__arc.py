"""Schematic record model for SchRecordType.ARC."""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    IntField,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
)
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    detect_case_mode_method_from_dotted_uppercase_fields,
    _coord_scalar_to_native_units,
    PrimaryRadiusMilsMixin,
)
from .altium_sch_svg_renderer import LINE_WIDTH_MILS, SchSvgRenderContext

ARC_FULL_TURN_DEGREES = 360.0


def _normalize_native_arc_angle(angle: float) -> float:
    """
    Normalize schematic arc angles for native SVG/IR rendering parity.

    Native export keeps exactly 360 as 360, folds values above 360 back by one
    or more turns, and folds negative values upward. Missing angle fields have
    distinct persisted-field semantics and are handled by the caller before
    this normalization step.
    """
    normalized = float(angle)
    while normalized > ARC_FULL_TURN_DEGREES:
        normalized -= ARC_FULL_TURN_DEGREES
    while normalized < 0.0:
        normalized += ARC_FULL_TURN_DEGREES
    return normalized


class AltiumSchArc(PrimaryRadiusMilsMixin, SchGraphicalObject):
    """
    Circular arc record.

    Arc defined by center, radius, start/end angles.

    Public code should use ``location_mils`` and ``radius_mils`` for geometry
    updates. The raw ``radius`` / ``radius_frac`` fields are internal
    coord-style storage fields kept for serializer fidelity.

    Rendering depends on angle field presence, not only numeric angle values:
    explicit ``EndAngle=90`` is a true quarter arc, while a missing persisted
    ``EndAngle`` is handled as native missing-field state during SVG/IR export.

    Radius storage fields enforce integer assignment, while ``start_angle`` and
    ``end_angle`` intentionally accept floats.
    """

    # Integer fields with type enforcement
    # New objects still follow Altium's default arc radius, but missing Radius
    # on import must remain 0 per the native SchDataArc importer.
    radius = IntField(default=10)
    radius_frac = IntField(default=0)

    def __init__(self) -> None:
        super().__init__()
        self.radius = 10  # Descriptor handles type enforcement
        self.radius_frac = 0
        self.start_angle: float = 0.0  # Degrees - floats are allowed
        self.end_angle: float = 90.0  # Degrees - floats are allowed
        self.line_width: LineWidth = LineWidth.SMALLEST
        # Track which fields were present
        self._has_radius: bool = False
        self._has_start_angle: bool = False
        self._has_end_angle: bool = False
        self._has_line_width: bool = False
        self._radius_mils_explicit: bool = False
        # Store original string format to preserve formatting (e.g., '360.000')
        self._start_angle_str: str | None = None
        self._end_angle_str: str | None = None

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.ARC

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse from a record.
        """
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()

        # Parse radius with presence tracking.
        # Arc radius uses the same coord whole/frac storage family as other
        # schematic geometry values. Imported files leave a missing Radius
        # field at 0. Do not fall back to the new-object default radius during
        # parse.
        radius_frac_val, has_radius_frac = s.read_int(
            record, Fields.RADIUS_FRAC, default=0
        )
        radius_val, self._has_radius = s.read_int(record, Fields.RADIUS, default=0)
        self.radius = radius_val  # Descriptor handles type enforcement
        self.radius_frac = radius_frac_val
        self._radius_mils_explicit = False

        # Parse line width
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

        # Angles need special handling to preserve original string format (e.g., '360.000')
        start_str, self._has_start_angle = s.read_str(
            record, Fields.START_ANGLE, default=""
        )
        end_str, self._has_end_angle = s.read_str(record, Fields.END_ANGLE, default="")
        self._start_angle_str = start_str if start_str else None
        self._end_angle_str = end_str if end_str else None

        self.start_angle = float(start_str) if start_str else 0.0
        self.end_angle = float(end_str) if end_str else 90.0

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Write radius field - skip if zero (Altium omits Radius=0).
        radius = cast(int, self.radius)
        radius_frac = cast(int, self.radius_frac)

        wrote_radius = (radius != 0 or self._has_radius) and (
            self._has_radius or radius != 10 or radius_frac != 0
        )
        if wrote_radius:
            s.write_int(record, Fields.RADIUS, radius, raw)
        else:
            s.remove_field(record, Fields.RADIUS)

        wrote_radius_frac = (
            self._radius_mils_explicit
            and radius_frac != 0
            and (radius != 0 or self._has_radius)
        )
        if wrote_radius_frac:
            s.write_int(record, Fields.RADIUS_FRAC, radius_frac, raw)
        else:
            s.remove_field(record, Fields.RADIUS_FRAC)

        # For angles, use original string format if available and unchanged
        # Write if: was present in original OR value is non-default
        if self._has_start_angle or self.start_angle != 0.0:
            angle_val = (
                self._start_angle_str
                if self._start_angle_str
                and float(self._start_angle_str) == self.start_angle
                else str(self.start_angle)
            )
            s.write_str(record, Fields.START_ANGLE, angle_val, raw)

        if self._has_end_angle or self.end_angle != 90.0:
            angle_val = (
                self._end_angle_str
                if self._end_angle_str and float(self._end_angle_str) == self.end_angle
                else str(self.end_angle)
            )
            s.write_str(record, Fields.END_ANGLE, angle_val, raw)

        if self._has_line_width or self.line_width != LineWidth.SMALLEST:
            s.write_int(record, Fields.LINE_WIDTH, self.line_width.value, raw)

        s.remove_field(record, Fields.IS_SOLID)
        s.remove_field(record, Fields.TRANSPARENT)
        s.remove_field(record, Fields.AREA_COLOR)
        s.remove_field(record, Fields.LINE_STYLE)
        s.remove_field(record, Fields.LINE_STYLE_EXT)

        return record

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for this arc.

        Native schematic export first resolves missing angle fields, then
        normalizes angles, then emits a full-circle helper plus an arc for
        normalized equality/full-turn cases. Non-circle explicit arcs such as
        ``0 -> 90`` must remain path arcs; this is common in component-child
        inductor coil graphics.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        cx, cy = ctx.transform_coord_precise(self.location)
        cx, cy = round(cx, 3), round(cy, 3)
        radius_units = _coord_scalar_to_native_units(self.radius, self.radius_frac)
        radius_px = round(radius_units * ctx.scale, 3)
        pen = make_pen(
            int(self.color) if self.color is not None else 0,
            width=0
            if self.line_width == LineWidth.SMALLEST
            else int(round(LINE_WIDTH_MILS.get(self.line_width, 1.0) * units_per_px)),
            line_join="pljRound",
        )

        start_angle = _normalize_native_arc_angle(
            float(self.start_angle) if self._has_start_angle else 0.0
        )
        end_angle = _normalize_native_arc_angle(
            float(self.end_angle) if self._has_end_angle else 0.0
        )
        draws_native_circle = (
            start_angle == end_angle
            or (start_angle == 0.0 and end_angle == ARC_FULL_TURN_DEGREES)
            or (end_angle == 0.0 and start_angle == ARC_FULL_TURN_DEGREES)
        )
        is_zero_radius = radius_px <= 0.0

        if is_zero_radius and not draws_native_circle:
            return None

        operations: list[SchGeometryOp] = []
        if draws_native_circle:
            if is_zero_radius:
                center_x, center_y = svg_coord_to_geometry(
                    cx,
                    cy,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                )
                operations.append(
                    SchGeometryOp.rounded_rectangle(
                        x1=center_x,
                        y1=center_y,
                        x2=center_x,
                        y2=center_y,
                        pen=pen,
                    )
                )
            else:
                left_x, top_y = svg_coord_to_geometry(
                    cx - radius_px,
                    cy - radius_px,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                )
                right_x, bottom_y = svg_coord_to_geometry(
                    cx + radius_px,
                    cy + radius_px,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                )
                radius_units = radius_px * units_per_px
                operations.append(
                    SchGeometryOp.rounded_rectangle(
                        x1=left_x,
                        y1=top_y,
                        x2=right_x,
                        y2=bottom_y,
                        corner_x_radius=radius_units,
                        corner_y_radius=radius_units,
                        pen=pen,
                    )
                )

        if not is_zero_radius:
            center_x, center_y = svg_coord_to_geometry(
                cx,
                cy,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            diameter_units = radius_px * 2.0 * units_per_px
            operations.append(
                SchGeometryOp.arc(
                    center_x=center_x,
                    center_y=center_y,
                    width=diameter_units,
                    height=diameter_units,
                    start_angle=-start_angle
                    if not draws_native_circle
                    or (start_angle == ARC_FULL_TURN_DEGREES and end_angle == 0.0)
                    else 0.0,
                    end_angle=-end_angle
                    if not draws_native_circle
                    or (start_angle == ARC_FULL_TURN_DEGREES and end_angle == 0.0)
                    else -ARC_FULL_TURN_DEGREES,
                    pen=pen,
                )
            )

        inflate = float(radius_units) + 2.0
        center_x_mils = float(self.location.x)
        center_y_mils = float(self.location.y)

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="arc",
            object_id="eArc",
            bounds=SchGeometryBounds(
                left=int(round((center_x_mils - inflate) * 100000)),
                top=int(round((center_y_mils + inflate) * 100000)),
                right=int(round((center_x_mils + inflate) * 100000)),
                bottom=int(round((center_y_mils - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
