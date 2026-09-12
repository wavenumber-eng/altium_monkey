"""Schematic record model for SchRecordType.SIGNAL_HARNESS."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_sch__bus import AltiumSchBus
from .altium_record_sch__wire import wire_like_junction_geometry_ops
from .altium_record_types import LineWidth, SchRecordType
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from ._sch_managed_defaults import HARNESS_SIGNAL_COLOR


class AltiumSchSignalHarness(AltiumSchBus):
    """
    SIGNAL_HARNESS record.

    Signal harness bundle between connectors.
    Inherits from BUS with thicker line width.
    """

    def __init__(self) -> None:
        super().__init__()
        self.line_width = LineWidth.MEDIUM  # Harnesses are thicker
        self.color = HARNESS_SIGNAL_COLOR
        self._has_unique_id: bool = False
        self._used_utf8_unique_id: bool = False
        self._used_utf8_assigned_interface: bool = False
        self._used_utf8_assigned_interface_signal: bool = False
        self._source_unique_id: str = str(self.unique_id or "")

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: object | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        view = self._record
        (
            self.assigned_interface,
            _,
            self._used_utf8_assigned_interface,
        ) = read_dynamic_string_field(
            serializer,
            record,
            view,
            Fields.ASSIGNED_INTERFACE,
            default="",
        )
        (
            self.assigned_interface_signal,
            _,
            self._used_utf8_assigned_interface_signal,
        ) = read_dynamic_string_field(
            serializer,
            record,
            view,
            Fields.ASSIGNED_INTERFACE_SIGNAL,
            default="",
        )
        unique_id, self._has_unique_id, self._used_utf8_unique_id = (
            read_dynamic_string_field(
                serializer,
                record,
                view,
                "UniqueID",
                default="",
            )
        )
        self.unique_id = unique_id or None
        if not self._has_color:
            self.color = 0
        if not self._has_underline_color:
            self.underline_color = 0
        self._capture_graphical_source_state()
        self._source_underline_color = self.underline_color
        self._source_assigned_interface = self.assigned_interface
        self._source_assigned_interface_signal = self.assigned_interface_signal
        self._source_unique_id = str(self.unique_id or "")

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.SIGNAL_HARNESS

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record
        if self._used_utf8_assigned_interface:
            self._restore_raw_fallback(record, raw, "AssignedInterface")
        if self._used_utf8_assigned_interface_signal:
            self._restore_raw_fallback(record, raw, "AssignedInterfaceSignal")
        write_dynamic_string_field(
            serializer,
            record,
            Fields.ASSIGNED_INTERFACE,
            self.assigned_interface,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_assigned_interface,
            was_present=bool(
                raw
                and any(
                    key.lower() in {"assignedinterface", "%utf8%assignedinterface"}
                    for key in raw
                )
            ),
            force=self.assigned_interface != self._source_assigned_interface,
        )
        write_dynamic_string_field(
            serializer,
            record,
            Fields.ASSIGNED_INTERFACE_SIGNAL,
            self.assigned_interface_signal,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_assigned_interface_signal,
            was_present=bool(
                raw
                and any(
                    key.lower()
                    in {
                        "assignedinterfacesignal",
                        "%utf8%assignedinterfacesignal",
                    }
                    for key in raw
                )
            ),
            force=(
                self.assigned_interface_signal != self._source_assigned_interface_signal
            ),
        )
        unique_id = str(self.unique_id or "")
        if self._used_utf8_unique_id and raw is not None:
            for key, value in raw.items():
                if key.lower() == "uniqueid":
                    record[key] = value
                    break
        write_dynamic_string_field(
            serializer,
            record,
            "UniqueID",
            unique_id,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_unique_id,
            was_present=self._has_unique_id,
            force=unique_id != self._source_unique_id,
        )
        # Signal harness has no family Location, but its data-object OwnerIndex
        # remains part of the managed ownership chain.
        record.pop("LOCATION.X", None)
        record.pop("Location.X", None)
        record.pop("LOCATION.Y", None)
        record.pop("Location.Y", None)
        if raw is None:
            return self._authored_managed_order(record)
        return record

    @staticmethod
    def _restore_raw_fallback(
        record: dict[str, object],
        raw: dict[str, object] | None,
        field_name: str,
    ) -> None:
        if raw is None:
            return
        for key, value in raw.items():
            if key.lower() == field_name.lower():
                record[key] = value
                return

    def _detect_case_mode(self) -> CaseMode:
        return CaseMode.PASCALCASE if self._use_pascal_case else CaseMode.UPPERCASE

    @staticmethod
    def _authored_managed_order(record: dict[str, object]) -> dict[str, object]:
        family_order = (
            "LineWidth",
            "Color",
            "UnderlineColor",
            "LocationCount",
            "ExtraLocationCount",
            "UniqueID",
            "%UTF8%UniqueID",
            "AssignedInterface",
            "%UTF8%AssignedInterface",
            "AssignedInterfaceSignal",
            "%UTF8%AssignedInterfaceSignal",
        )
        family_names = {name.lower(): name for name in family_order}
        point_prefixes = ("x", "y", "ex", "ey")
        family_values: dict[str, tuple[str, object]] = {}
        point_values: list[tuple[str, object]] = []
        result: dict[str, object] = {}
        for key, value in record.items():
            normalized = key.lower()
            family_name = family_names.get(normalized)
            is_point = normalized.startswith(point_prefixes) and any(
                character.isdigit() for character in normalized
            )
            if family_name is not None:
                family_values[family_name] = (key, value)
            elif is_point:
                point_values.append((key, value))
            else:
                result[key] = value
        for family_name in family_order[:5]:
            if family_name in family_values:
                key, value = family_values[family_name]
                result[key] = value
        for key, value in point_values:
            result[key] = value
        for family_name in family_order[5:]:
            if family_name in family_values:
                key, value = family_values[family_name]
                result[key] = value
        return result

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
        kind: str = "signalharness",
        object_id: str = "eSignalHarness",
        default_color_raw: int = 0xE8C59F,
        stroke_width_mils_override: float | None = None,
        junction_color_raw: int = 0x800000,
        junction_size_px: float = 10.0,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for a signal harness.
        """
        import math

        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if len(self.points) < 2:
            return None

        DEFAULT_HARNESS_BLUE = int(default_color_raw)
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
            width=_geometry_item_length(background_width_px, units_per_px=units_per_px),
        )
        hash_pen = make_pen(
            foreground_color_raw,
            width=_geometry_item_length(hash_width_px, units_per_px=units_per_px),
        )
        center_pen = make_pen(
            foreground_color_raw,
            width=_geometry_item_length(center_width_px, units_per_px=units_per_px),
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
                size_px=junction_size_px,
                color_raw=junction_color_raw,
            )
        )

        inflate = max(float(stroke_width_mils_override or background_width_px), 1.0)
        min_x = min(point[0] for point in raw_points) - inflate
        max_x = max(point[0] for point in raw_points) + inflate
        min_y = min(point[1] for point in raw_points) - inflate
        max_y = max(point[1] for point in raw_points) + inflate

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind=kind,
            object_id=object_id,
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
