"""Schematic record model for SchRecordType.ARC."""

import struct
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
from ._sch_managed_defaults import GRAPHICAL_BORDER_COLOR
from .altium_serializer import AltiumSerializer, Fields, format_param_n3
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
    normalized = struct.unpack("<f", struct.pack("<f", float(angle)))[0]
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
    radius = cast(Any, IntField(default=10))
    radius_frac = cast(Any, IntField(default=0))

    def __init__(self) -> None:
        super().__init__()
        if self.record_type in (SchRecordType.ARC, SchRecordType.ELLIPTICAL_ARC):
            self.color = GRAPHICAL_BORDER_COLOR
            self._apply_nonpersisted_area_color_default()
        self.radius = 10  # Descriptor handles type enforcement
        self.radius_frac = 0
        self.start_angle: float = 30.0  # Degrees - floats are allowed
        self.end_angle: float = 330.0  # Degrees - floats are allowed
        self.line_width: LineWidth = LineWidth.SMALL
        # Track which fields were present
        self._has_radius: bool = False
        self._has_start_angle: bool = False
        self._has_end_angle: bool = False
        self._has_line_width: bool = False
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
        radius_val, radius_frac_val, has_radius_base = s.read_coord(
            record, Fields.RADIUS.canonical
        )
        _, has_radius_frac = s.read_int(record, Fields.RADIUS_FRAC, default=0)
        self._has_radius = has_radius_base or has_radius_frac
        self.radius = radius_val  # Descriptor handles type enforcement
        self.radius_frac = radius_frac_val

        # Parse line width
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)
        self._apply_imported_color_defaults(area_color=False)
        if self.record_type in (SchRecordType.ARC, SchRecordType.ELLIPTICAL_ARC):
            self._apply_nonpersisted_area_color_default()

        # Angles need special handling to preserve original string format (e.g., '360.000')
        start_str, self._has_start_angle = s.read_str(
            record, Fields.START_ANGLE, default=""
        )
        end_str, self._has_end_angle = s.read_str(record, Fields.END_ANGLE, default="")
        self._start_angle_str = start_str if start_str else None
        self._end_angle_str = end_str if end_str else None

        self.start_angle = float(start_str) if start_str else 0.0
        self.end_angle = float(end_str) if end_str else 0.0

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)

        # Write radius field - skip if zero (Altium omits Radius=0).
        radius = cast(int, self.radius)
        radius_frac = cast(int, self.radius_frac)

        # Native serialization omits only zero whole radii, so any nonzero
        # whole (including the new-object default of 10) must be written or
        # authored values such as 100.0 mils would reparse as 0.
        self._serialize_managed_family_coord(
            record, s, Fields.RADIUS.canonical, "", radius, radius_frac
        )

        # For angles, use original string format if available and unchanged
        # Write if: was present in original OR value is non-default
        self._serialize_managed_angle(
            record,
            s,
            Fields.START_ANGLE.canonical,
            self.start_angle,
            self._start_angle_str,
        )
        self._serialize_managed_angle(
            record, s, Fields.END_ANGLE.canonical, self.end_angle, self._end_angle_str
        )

        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )

        s.remove_field(record, Fields.IS_SOLID)
        s.remove_field(record, Fields.TRANSPARENT)
        s.remove_field(record, Fields.LINE_STYLE)
        s.remove_field(record, Fields.LINE_STYLE_EXT)
        if self._raw_record is None or self._area_color_dirty:
            self._remove_fields_case_insensitively(record, ["AreaColor", "AREACOLOR"])

        self._move_geometry_identity_to_end_if_needed(record)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Radius",
                "Radius_Frac",
                "LineWidth",
                "StartAngle",
                "EndAngle",
                "Color",
                "UniqueID",
            ),
        )

    def _serialize_managed_angle(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        field: str,
        value: float,
        source_spelling: str | None,
    ) -> None:
        source = float(source_spelling) if source_spelling else 0.0
        if self._raw_record is not None and value == source:
            return
        serializer.remove_field(record, field)
        if value != 0.0:
            serializer.write_str(
                record, field, format_param_n3(value), None, force=True
            )

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
            _geometry_item_arc_angles,
            _geometry_item_length,
            make_rounded_rectangle_operation,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        cx, cy = ctx.transform_coord_precise(self.location)
        radius_units = _coord_scalar_to_native_units(self.radius, self.radius_frac)
        radius_px = radius_units * ctx.scale
        pen = make_pen(
            int(self.color) if self.color is not None else 0,
            width=0
            if self.line_width == LineWidth.SMALLEST
            else _geometry_item_length(
                LINE_WIDTH_MILS.get(self.line_width, 1.0) * ctx.get_stroke_scale(),
                units_per_px=units_per_px,
            ),
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
        geometry_start_angle, geometry_end_angle = _geometry_item_arc_angles(
            start_angle,
            end_angle,
            rotation=ctx.rotation,
            mirror_x=ctx.mirror,
        )
        if draws_native_circle:
            if is_zero_radius:
                operations.append(
                    make_rounded_rectangle_operation(
                        x1_px=cx,
                        y1_px=cy,
                        x2_px=cx,
                        y2_px=cy,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                        pen=pen,
                    )
                )
            else:
                operations.append(
                    make_rounded_rectangle_operation(
                        x1_px=cx - radius_px,
                        y1_px=cy - radius_px,
                        x2_px=cx + radius_px,
                        y2_px=cy + radius_px,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                        corner_x_radius_px=radius_px,
                        corner_y_radius_px=radius_px,
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
            diameter_units = _geometry_item_length(
                radius_px * 2.0,
                units_per_px=units_per_px,
            )
            operations.append(
                SchGeometryOp.arc(
                    center_x=center_x,
                    center_y=center_y,
                    width=diameter_units,
                    height=diameter_units,
                    start_angle=geometry_start_angle,
                    end_angle=geometry_end_angle,
                    pen=pen,
                )
            )

        inflate = float(radius_units) + 2.0
        center_x_mils = float(self.location.x)
        center_y_mils = float(self.location.y)
        unique_id = str(self.unique_id or "")

        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="arc",
            object_id="eArc",
            bounds=SchGeometryBounds(
                left=int(round((center_x_mils - inflate) * 100000)),
                top=int(round((center_y_mils + inflate) * 100000)),
                right=int(round((center_x_mils + inflate) * 100000)),
                bottom=int(round((center_y_mils - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
