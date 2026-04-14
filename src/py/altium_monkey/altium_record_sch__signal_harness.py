"""Schematic record model for SchRecordType.SIGNAL_HARNESS."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_sch__bus import AltiumSchBus
from .altium_record_sch__wire import wire_like_junction_geometry_ops
from .altium_record_types import LineWidth, SchRecordType


class AltiumSchSignalHarness(AltiumSchBus):
    """
    SIGNAL_HARNESS record.

    Signal harness bundle between connectors.
    Inherits from BUS with thicker line width.
    """

    def __init__(self) -> None:
        super().__init__()
        self.line_width = LineWidth.MEDIUM  # Harnesses are thicker

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.SIGNAL_HARNESS

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        # Signal harness shouldn't have OWNERINDEX or LOCATION.X/Y
        # (verified from harness_example.SchDoc)
        record.pop("OWNERINDEX", None)
        record.pop("OwnerIndex", None)
        record.pop("LOCATION.X", None)
        record.pop("Location.X", None)
        record.pop("LOCATION.Y", None)
        record.pop("Location.Y", None)
        return record

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for a signal harness.
        """
        import math

        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if len(self.points) < 2:
            return None

        DEFAULT_HARNESS_BLUE = 0xE8C59F
        line_width_map = {0: 2, 1: 3, 2: 5, 3: 7}

        def lighten_color(color_int: int) -> int:
            r = color_int & 0xFF
            g = (color_int >> 8) & 0xFF
            b = (color_int >> 16) & 0xFF
            # Native SignalHarnessDrawGraphObject applies stronger lightening
            # only for low-intensity colors.
            offset = 0x5A if (r + g + b) / 3 < 100 else 0x1E
            r = min(r + offset, 0xFF)
            g = min(g + offset, 0xFF)
            b = min(b + offset, 0xFF)
            return r | (g << 8) | (b << 16)

        svg_points = [
            tuple(float(v) for v in ctx.transform_coord_precise(point))
            for point in self.points
        ]
        geometry_points = [
            svg_coord_to_geometry(
                x,
                y,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for x, y in svg_points
        ]
        raw_points = [(float(point.x), float(point.y)) for point in self.points]

        effective_color = int(self.color) if self.color is not None else 0
        if effective_color == DEFAULT_HARNESS_BLUE:
            background_color_raw = 0xFFE3BD
            foreground_color_raw = DEFAULT_HARNESS_BLUE
        else:
            background_color_raw = lighten_color(effective_color)
            foreground_color_raw = effective_color

        background_width_px = line_width_map.get(
            self.line_width.value
            if hasattr(self.line_width, "value")
            else self.line_width,
            5,
        )
        hash_width_px = 1.5
        center_width_px = 1.0

        background_pen = make_pen(
            background_color_raw,
            width=int(round(background_width_px * units_per_px)),
        )
        hash_pen = make_pen(
            foreground_color_raw,
            width=int(round(hash_width_px * units_per_px)),
        )
        center_pen = make_pen(
            foreground_color_raw,
            width=int(round(center_width_px * units_per_px)),
        )

        operations = []

        for segment_index, ((x1, y1), (x2, y2)) in enumerate(
            zip(svg_points, svg_points[1:], strict=False)
        ):
            raw_start = self.points[segment_index]
            raw_end = self.points[segment_index + 1]
            operations.append(
                SchGeometryOp.lines(
                    [
                        list(geometry_points[segment_index]),
                        list(geometry_points[segment_index + 1]),
                    ],
                    pen=background_pen,
                )
            )
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length <= 0:
                operations.append(
                    SchGeometryOp.lines(
                        [
                            list(geometry_points[segment_index]),
                            list(geometry_points[segment_index + 1]),
                        ],
                        pen=center_pen,
                    )
                )
                continue

            vertical_hash_x = background_width_px / 2.0 - 0.75
            horizontal_hash_y = background_width_px / 2.0 - 0.75
            hash_spacing = 10.0
            hash_start_offset = 2.0
            hash_length = 4.0

            hash_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
            if raw_start.x == raw_end.x:
                x_raw = float(raw_start.x)
                y_low = min(float(raw_start.y), float(raw_end.y))
                y_high = max(float(raw_start.y), float(raw_end.y))
                t = hash_start_offset
                while t + hash_length < (y_high - y_low):
                    hash_segments.append(
                        (
                            ctx.transform_point(x_raw + vertical_hash_x, y_low + t),
                            ctx.transform_point(
                                x_raw - vertical_hash_x, y_low + t + hash_length
                            ),
                        )
                    )
                    t += hash_spacing
            elif raw_start.y == raw_end.y:
                x_low = min(float(raw_start.x), float(raw_end.x))
                x_high = max(float(raw_start.x), float(raw_end.x))
                y_raw = float(raw_start.y)
                t = hash_start_offset
                while t + hash_length < (x_high - x_low):
                    hash_segments.append(
                        (
                            ctx.transform_point(x_low + t, y_raw + horizontal_hash_y),
                            ctx.transform_point(
                                x_low + t + hash_length, y_raw - horizontal_hash_y
                            ),
                        )
                    )
                    t += hash_spacing
            else:
                ux, uy = dx / length, dy / length
                hash_positions: list[float] = []
                t = 4.0
                while t < length:
                    hash_positions.append(t)
                    t += hash_spacing
                for t in hash_positions:
                    if abs(dx) > abs(dy):
                        hx = min(x1, x2) + t
                        hy = y1
                        hash_start = (hx - 2, hy - horizontal_hash_y)
                        hash_end = (hx + 2, hy + horizontal_hash_y)
                    else:
                        hx = x1 + ux * t
                        hy = y1 + uy * t
                        hash_start = (hx + vertical_hash_x, hy + 2)
                        hash_end = (hx - vertical_hash_x, hy - 2)
                    hash_segments.append((hash_start, hash_end))

            for hash_start, hash_end in hash_segments:
                operations.append(
                    SchGeometryOp.lines(
                        [
                            list(
                                svg_coord_to_geometry(
                                    hash_start[0],
                                    hash_start[1],
                                    sheet_height_px=float(ctx.sheet_height or 0.0),
                                    units_per_px=units_per_px,
                                )
                            ),
                            list(
                                svg_coord_to_geometry(
                                    hash_end[0],
                                    hash_end[1],
                                    sheet_height_px=float(ctx.sheet_height or 0.0),
                                    units_per_px=units_per_px,
                                )
                            ),
                        ],
                        pen=hash_pen,
                    )
                )

            operations.append(
                SchGeometryOp.lines(
                    [
                        list(geometry_points[segment_index]),
                        list(geometry_points[segment_index + 1]),
                    ],
                    pen=center_pen,
                )
            )

        if len(geometry_points) >= 2:
            endpoint = geometry_points[-1]
            operations.append(
                SchGeometryOp.lines([endpoint, endpoint], pen=background_pen)
            )
            operations.append(SchGeometryOp.lines([endpoint, endpoint], pen=center_pen))

        operations.extend(
            wire_like_junction_geometry_ops(
                [geometry_points[0]],
                source_points=[(self.points[0].x, self.points[0].y)],
                connection_points=ctx.harness_junction_points,
                units_per_px=units_per_px,
                size_px=10.0,
                color_raw=0x800000,
            )
        )

        inflate = max(float(background_width_px), 1.0)
        min_x = min(point[0] for point in raw_points) - inflate
        max_x = max(point[0] for point in raw_points) + inflate
        min_y = min(point[1] for point in raw_points) - inflate
        max_y = max(point[1] for point in raw_points) + inflate

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="signalharness",
            object_id="eSignalHarness",
            bounds=SchGeometryBounds(
                left=int(round(min_x * 100000)),
                top=int(round(max_y * 100000)),
                right=int(round(max_x * 100000)),
                bottom=int(round(min_y * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        vertex_count = len(self.points) if hasattr(self, "points") else 0
        return f"<AltiumSchSignalHarness vertices={vertex_count}>"
