"""Schematic record model for SchRecordType.ELLIPSE."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
)
from ._sch_managed_defaults import GRAPHICAL_BORDER_COLOR, GRAPHICAL_FILL_COLOR
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    _coord_scalar_to_native_units,
    detect_case_mode_method_from_dotted_uppercase_fields,
    PrimaryRadiusMilsMixin,
    SecondaryRadiusMilsMixin,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SEMI_TRANSPARENT_ALPHA,
    SchSvgRenderContext,
)


class AltiumSchEllipse(
    PrimaryRadiusMilsMixin, SecondaryRadiusMilsMixin, SchGraphicalObject
):
    """
    Ellipse/circle record.

    Ellipse defined by center (location) and radii.

    Public code should use ``location_mils``, ``radius_mils``, and
    ``secondary_radius_mils`` for geometry updates. The raw radius fields are
    internal coord-style storage fields kept for serializer fidelity.
    """

    def __init__(self) -> None:
        super().__init__()
        self.color = GRAPHICAL_BORDER_COLOR
        self.area_color = GRAPHICAL_FILL_COLOR
        self.radius: int = 20  # X radius (base integer part)
        self.radius_frac: int = 0  # X radius fractional part (/100000)
        self.secondary_radius: int = 10  # Y radius (base integer part)
        self.secondary_radius_frac: int = 0  # Y radius fractional part (/100000)
        self.line_width: LineWidth = LineWidth.SMALLEST
        self.is_solid: bool = True
        self.transparent: bool = (
            False  # Unchecked checkboxes don't appear in Altium records
        )
        # Track which fields were present (used for conditional serialization)
        self._has_radius: bool = False  # Any radius field was present (base or frac)
        self._has_secondary_radius: bool = (
            False  # Any secondary radius field was present
        )
        self._has_radius_base: bool = False  # Base Radius value was explicitly present
        self._has_secondary_radius_base: bool = (
            False  # Base SecondaryRadius was present
        )
        self._has_line_width: bool = False
        self._has_is_solid: bool = False
        self._has_transparent: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.ELLIPSE

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

        # Parse radius fields with presence tracking
        # CRITICAL: Default must be 0, not 10. When only Radius_Frac is present,
        # the base radius is implicitly 0 (e.g., Radius_Frac=78700 -> radius=0.787)
        self.radius, self.radius_frac, self._has_radius_base = s.read_coord(
            record, Fields.RADIUS.canonical
        )
        _, has_radius_frac = s.read_int(record, Fields.RADIUS_FRAC, default=0)
        self._has_radius = self._has_radius_base or has_radius_frac

        # Parse secondary radius fields (same default=0 logic)
        (
            self.secondary_radius,
            self.secondary_radius_frac,
            self._has_secondary_radius_base,
        ) = s.read_coord(record, Fields.SECONDARY_RADIUS.canonical)
        _, has_sec_radius_frac = s.read_int(
            record, Fields.SECONDARY_RADIUS_FRAC, default=0
        )
        self._has_secondary_radius = (
            self._has_secondary_radius_base or has_sec_radius_frac
        )

        # Parse line width
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

        # Sparse import overwrites the authored fill default.
        self.is_solid, self._has_is_solid = s.read_bool(
            record, Fields.IS_SOLID, default=False
        )
        self.transparent, self._has_transparent = s.read_bool(
            record, Fields.TRANSPARENT, default=False
        )
        self._apply_imported_color_defaults(area_color=True)

        # CRITICAL: Handle missing AreaColor field - DON'T set defaults here
        # Leave area_color as-is from parent class parsing

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)

        # Note: Always output Location.X/Y to match native export (no omission when 0)

        # Radius/Radius_Frac handling:
        # - Only output base Radius if source explicitly had it OR value is non-zero
        # - Always output Radius_Frac if non-zero (even with zero base)
        self._serialize_managed_family_coord(
            record, s, Fields.RADIUS.canonical, "", self.radius, self.radius_frac
        )

        # Same logic for SecondaryRadius
        self._serialize_managed_family_coord(
            record,
            s,
            Fields.SECONDARY_RADIUS.canonical,
            "",
            self.secondary_radius,
            self.secondary_radius_frac,
        )

        # Only export LineWidth if it was present in source OR has non-default value
        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )

        # Only export IsSolid if it was present in source OR is True
        self._serialize_managed_family_bool(
            record, s, Fields.IS_SOLID.canonical, self.is_solid
        )

        # Transparent field: Export if it was present in source OR is True
        self._serialize_managed_family_bool(
            record, s, Fields.TRANSPARENT.canonical, self.transparent
        )

        # Note: Ellipse does NOT serialize LineStyle or LineStyleExt even if
        # stale fields were present in a parsed raw record.
        s.remove_field(record, Fields.LINE_STYLE)
        s.remove_field(record, Fields.LINE_STYLE_EXT)

        # Remove OwnerIndex for SchLib-only context (owner_index == 0 or None)
        if self.owner_index == 0 or self.owner_index is None:
            s.remove_field(record, Fields.OWNER_INDEX)

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
                "SecondaryRadius",
                "SecondaryRadius_Frac",
                "LineWidth",
                "Color",
                "AreaColor",
                "IsSolid",
                "Transparent",
                "UniqueID",
            ),
        )

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this ellipse.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_rounded_rectangle_operation,
            make_pen,
            make_solid_brush,
            wrap_record_operations,
        )

        cx, cy = ctx.transform_coord_precise(self.location)
        radius_units = _coord_scalar_to_native_units(self.radius, self.radius_frac)
        secondary_radius_units = _coord_scalar_to_native_units(
            self.secondary_radius,
            self.secondary_radius_frac,
        )
        rx = radius_units * ctx.scale
        ry = secondary_radius_units * ctx.scale
        renderable = radius_units > 0.0 and secondary_radius_units > 0.0

        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        pen_width = (
            0
            if self.line_width == LineWidth.SMALLEST
            else _geometry_item_length(
                stroke_width_mils * ctx.get_stroke_scale(),
                units_per_px=units_per_px,
            )
        )

        operations: list[SchGeometryOp] = []
        stroke_color_raw = (
            int(ctx.line_color_override)
            if ctx.line_color_override is not None
            else int(self.color)
            if self.color is not None
            else 0
        )

        if renderable and self.is_solid:
            fill_color_raw = (
                int(ctx.area_color_override)
                if ctx.area_color_override is not None
                else int(self.area_color)
                if self.area_color is not None
                else stroke_color_raw
            )
            operations.append(
                make_rounded_rectangle_operation(
                    x1_px=cx - rx,
                    y1_px=cy - ry,
                    x2_px=cx + rx,
                    y2_px=cy + ry,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                    corner_x_radius_px=rx,
                    corner_y_radius_px=ry,
                    brush=make_solid_brush(
                        fill_color_raw,
                        alpha=SEMI_TRANSPARENT_ALPHA if self.transparent else 0xFF,
                    ),
                )
            )

        if renderable:
            operations.append(
                make_rounded_rectangle_operation(
                    x1_px=cx - rx,
                    y1_px=cy - ry,
                    x2_px=cx + rx,
                    y2_px=cy + ry,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                    corner_x_radius_px=rx,
                    corner_y_radius_px=ry,
                    pen=make_pen(
                        stroke_color_raw,
                        width=pen_width,
                        line_join="pljRound",
                    ),
                )
            )

        center_x_mils = float(self.location.x)
        center_y_mils = float(self.location.y)
        if renderable:
            inflate_x = radius_units + stroke_width_mils + 2.0
            inflate_y = secondary_radius_units + stroke_width_mils + 2.0
            bounds = SchGeometryBounds(
                left=int(round((center_x_mils - inflate_x) * 100000)),
                top=int(round((center_y_mils + inflate_y) * 100000)),
                right=int(round((center_x_mils + inflate_x) * 100000)),
                bottom=int(round((center_y_mils - inflate_y) * 100000)),
            )
        else:
            # Native Altium suppresses degenerate ellipses where either axis is zero.
            bounds = SchGeometryBounds(
                left=int(round(center_x_mils * 100000)),
                top=int(round(center_y_mils * 100000)),
                right=int(round(center_x_mils * 100000)),
                bottom=int(round(center_y_mils * 100000)),
            )
        unique_id = str(self.unique_id or "")

        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="ellipse",
            object_id="eEllipse",
            bounds=bounds,
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
