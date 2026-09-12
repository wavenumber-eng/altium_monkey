"""Schematic record model for SchRecordType.ELLIPTICAL_ARC."""

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_sch__arc import AltiumSchArc, _normalize_native_arc_angle
from .altium_record_types import LineWidth, SchRecordType
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    _coord_scalar_to_native_units,
    SecondaryRadiusMilsMixin,
    detect_case_mode_method_from_uppercase_fields,
)
from .altium_sch_svg_renderer import LINE_WIDTH_MILS, SchSvgRenderContext


class AltiumSchEllipticalArc(SecondaryRadiusMilsMixin, AltiumSchArc):
    """
    ELLIPTICAL_ARC record.

    An arc on an ellipse (non-circular arc).

    Public code should use ``location_mils``, ``radius_mils``, and
    ``secondary_radius_mils`` for geometry updates. The raw radius fields are
    internal coord-style storage fields kept for serializer fidelity.
    """

    def __init__(self) -> None:
        super().__init__()
        self.secondary_radius: int = 10  # Radius for minor axis (mils)
        self.secondary_radius_frac: int = 0
        self._has_secondary_radius: bool = False
        self._has_secondary_radius_base: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.ELLIPTICAL_ARC

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading
        s = AltiumSerializer()
        # Imported files leave a missing SecondaryRadius field at 0. Do not
        # inherit the new-object default secondary radius during parse.
        (
            self.secondary_radius,
            self.secondary_radius_frac,
            self._has_secondary_radius_base,
        ) = s.read_coord(record, Fields.SECONDARY_RADIUS.canonical)
        _, has_secondary_radius_frac = s.read_int(
            record, Fields.SECONDARY_RADIUS_FRAC, default=0
        )
        self._has_secondary_radius = (
            self._has_secondary_radius_base or has_secondary_radius_frac
        )

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        self._serialize_managed_family_coord(
            record,
            s,
            Fields.SECONDARY_RADIUS.canonical,
            "",
            self.secondary_radius,
            self.secondary_radius_frac,
        )

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
                "StartAngle",
                "EndAngle",
                "Color",
                "UniqueID",
            ),
        )

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this elliptical arc.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_arc_angles,
            _geometry_item_length,
            make_pen,
            svg_coord_to_geometry,
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
        source_start_angle = _normalize_native_arc_angle(float(self.start_angle))
        source_end_angle = _normalize_native_arc_angle(
            0.0 if not self._has_end_angle else float(self.end_angle)
        )
        missing_end_angle_quarter = not self._has_end_angle and math.isclose(
            float(self.start_angle), 270.0
        )

        operations: list[SchGeometryOp] = []
        if renderable:
            center_x, center_y = svg_coord_to_geometry(
                cx,
                cy,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            geometry_start_angle, geometry_end_angle = _geometry_item_arc_angles(
                source_start_angle,
                source_end_angle,
                rotation=ctx.rotation,
                mirror_x=ctx.mirror,
            )
            operations.append(
                SchGeometryOp.arc(
                    center_x=center_x,
                    center_y=center_y,
                    width=_geometry_item_length(
                        rx * 2.0,
                        units_per_px=units_per_px,
                    ),
                    height=_geometry_item_length(
                        ry * 2.0,
                        units_per_px=units_per_px,
                    ),
                    start_angle=geometry_start_angle,
                    end_angle=geometry_end_angle,
                    pen=make_pen(
                        int(self.color) if self.color is not None else 0,
                        width=0
                        if self.line_width == LineWidth.SMALLEST
                        else _geometry_item_length(
                            LINE_WIDTH_MILS.get(self.line_width, 1.0)
                            * ctx.get_stroke_scale(),
                            units_per_px=units_per_px,
                        ),
                        line_join="pljRound",
                    ),
                )
            )

        center_x_mils = float(self.location.x)
        center_y_mils = float(self.location.y)
        if renderable:
            left_inflate_x = 2.0 if missing_end_angle_quarter else radius_units + 2.0
            right_inflate_x = radius_units + 2.0
            inflate_y = secondary_radius_units + 2.0
            bounds = SchGeometryBounds(
                left=int(round((center_x_mils - left_inflate_x) * 100000)),
                top=int(round((center_y_mils + inflate_y) * 100000)),
                right=int(round((center_x_mils + right_inflate_x) * 100000)),
                bottom=int(round((center_y_mils - inflate_y) * 100000)),
            )
        else:
            # Native Altium suppresses degenerate elliptical arcs where either axis is zero.
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
            kind="ellipticalarc",
            object_id="eEllipticalArc",
            bounds=bounds,
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<AltiumSchEllipticalArc at=({self.location.x}, {self.location.y}) "
            f"r1={self.radius} r2={self.secondary_radius}>"
        )
