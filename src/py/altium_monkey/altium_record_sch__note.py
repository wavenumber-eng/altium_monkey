"""Schematic record model for SchRecordType.NOTE."""

from typing import TYPE_CHECKING, Any

from .altium_record_sch__text_frame import AltiumSchTextFrame
from .altium_record_types import SchRecordType
from .altium_serializer import AltiumSerializer, CaseMode, Fields

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext


class AltiumSchNote(AltiumSchTextFrame):
    """
    NOTE record.

    Note/comment annotation with author and collapse state.
    Inherits from TEXT_FRAME.
    """

    def __init__(self) -> None:
        super().__init__()
        self.author: str = "Author"
        self.collapsed: bool = False
        self.show_border = True  # Notes always have visible border

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.NOTE

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
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()

        self.author, _ = s.read_str(record, Fields.AUTHOR, default="Author")
        self.collapsed, _ = s.read_bool(record, Fields.COLLAPSED, default=False)

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        s = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record

        s.write_str(record, Fields.AUTHOR, self.author, raw)
        if self.collapsed:
            s.write_bool(record, Fields.COLLAPSED, self.collapsed, raw)

        return record

    def __repr__(self) -> str:
        return f"<AltiumSchNote by '{self.author}' collapsed={self.collapsed}>"

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if self.is_hidden:
            return None

        x1, y1 = ctx.transform_coord_precise(self.location)
        x2, y2 = ctx.transform_coord_precise(self.corner)
        frame_x = min(float(x1), float(x2))
        frame_y = min(float(y1), float(y2))
        frame_width = abs(float(x2) - float(x1))
        frame_height = abs(float(y2) - float(y1))
        if frame_width == 0 or frame_height == 0:
            return None

        dog_ear_size = 10.0
        if self.collapsed:
            frame_width = dog_ear_size
            frame_height = dog_ear_size

        def parse_color(
            color_int: int | None, default: tuple[int, int, int]
        ) -> tuple[int, int, int]:
            if color_int is None:
                return default
            r = color_int & 0xFF
            g = (color_int >> 8) & 0xFF
            b = (color_int >> 16) & 0xFF
            return (r, g, b)

        def color_split(
            c1: tuple[int, int, int], c2: tuple[int, int, int], scale: float
        ) -> tuple[int, int, int]:
            r = min(c1[0], c2[0]) + int(round(abs(c1[0] - c2[0]) * scale))
            g = min(c1[1], c2[1]) + int(round(abs(c1[1] - c2[1]) * scale))
            b = min(c1[2], c2[2]) + int(round(abs(c1[2] - c2[2]) * scale))
            return (r, g, b)

        def modify_color(
            percent: int, color: tuple[int, int, int], bg: tuple[int, int, int]
        ) -> tuple[int, int, int]:
            r = color[0] + (bg[0] - color[0]) * percent // 100
            g = color[1] + (bg[1] - color[1]) * percent // 100
            b = color[2] + (bg[2] - color[2]) * percent // 100
            return (r, g, b)

        def rgb_to_win32(rgb: tuple[int, int, int]) -> int:
            return rgb[0] | (rgb[1] << 8) | (rgb[2] << 16)

        def build_note_polygon(
            x: float, y: float, w: float, h: float, ear: float
        ) -> list[tuple[float, float]]:
            return [
                (x, y + h),
                (x, y),
                (x + w, y),
                (x + w, y + h - ear),
                (x + w - ear, y + h),
            ]

        def geometry_points(points: list[tuple[float, float]]) -> list[list[float]]:
            return [
                list(
                    svg_coord_to_geometry(
                        px,
                        py,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                    )
                )
                for px, py in points
            ]

        border_rgb = parse_color(self.color, (0, 0, 0))
        fill_rgb = parse_color(self.area_color, (255, 255, 255))
        doc_bg_rgb = parse_color(
            getattr(ctx, "sheet_area_color", None), (255, 255, 255)
        )
        shadow_base_rgb = (0, 0, 0)
        note_collapse_rgb = (255, 0, 0)
        shadow_base = color_split(border_rgb, shadow_base_rgb, 0.2)
        shadow_stroke_rgb = modify_color(75, shadow_base, doc_bg_rgb)
        shadow_fill_base = color_split(fill_rgb, shadow_base_rgb, 0.2)
        shadow_fill_rgb = modify_color(85, shadow_fill_base, doc_bg_rgb)
        if self.collapsed:
            dog_ear_fill_rgb = fill_rgb
            dog_ear_stroke_rgb = border_rgb
        else:
            dog_ear_stroke_rgb = modify_color(
                75, color_split(border_rgb, shadow_base_rgb, 0.2), doc_bg_rgb
            )
            dog_ear_fill_rgb = modify_color(
                75, color_split(fill_rgb, shadow_base_rgb, 0.8), doc_bg_rgb
            )
        collapse_fill_rgb = color_split(fill_rgb, note_collapse_rgb, 0.8)
        collapse_stroke_rgb = color_split(border_rgb, note_collapse_rgb, 0.2)

        operations: list[SchGeometryOp] = []
        shadow_points = geometry_points(
            build_note_polygon(
                frame_x + 2.0, frame_y + 2.0, frame_width, frame_height, dog_ear_size
            )
        )
        operations.append(
            SchGeometryOp.polygons(
                [shadow_points],
                brush=make_solid_brush(rgb_to_win32(shadow_fill_rgb), alpha=125),
            )
        )
        operations.append(
            SchGeometryOp.polygons(
                [shadow_points],
                pen=make_pen(rgb_to_win32(shadow_stroke_rgb), width=0),
            )
        )

        body_points = geometry_points(
            build_note_polygon(
                frame_x, frame_y, frame_width, frame_height, dog_ear_size
            )
        )
        operations.append(
            SchGeometryOp.polygons(
                [body_points],
                brush=make_solid_brush(
                    int(self.area_color) if self.area_color is not None else 0xFFFFFF
                ),
            )
        )
        operations.append(
            SchGeometryOp.polygons(
                [body_points],
                pen=make_pen(int(self.color) if self.color is not None else 0, width=0),
            )
        )

        if not self.collapsed and self.text:
            margin_svg = self.text_margin_mils * ctx.scale
            text_area_x = frame_x + margin_svg
            text_area_y = frame_y + margin_svg
            text_area_width = frame_width - 2 * margin_svg
            text_area_height = frame_height - 2 * margin_svg
            operations.extend(
                self._build_text_geometry_ops(
                    ctx,
                    text_area_x=text_area_x,
                    text_area_y=text_area_y,
                    text_area_width=text_area_width,
                    clip_x=text_area_x,
                    clip_y=text_area_y,
                    clip_width=text_area_width,
                    clip_height=text_area_height,
                    units_per_px=units_per_px,
                )
            )

        ear_x = frame_x + frame_width - dog_ear_size
        ear_y = frame_y + frame_height - dog_ear_size
        dog_ear_points = geometry_points(
            [
                (ear_x, ear_y),
                (ear_x, frame_y + frame_height),
                (frame_x + frame_width, ear_y),
            ]
        )
        operations.append(
            SchGeometryOp.polygons(
                [dog_ear_points],
                brush=make_solid_brush(rgb_to_win32(dog_ear_fill_rgb)),
            )
        )
        operations.append(
            SchGeometryOp.polygons(
                [dog_ear_points],
                pen=make_pen(rgb_to_win32(dog_ear_stroke_rgb), width=0),
            )
        )

        ind_left = frame_x + 1.0
        ind_right = frame_x + 5.0
        ind_center = frame_x + 3.0
        if not self.collapsed:
            collapse_points = geometry_points(
                [
                    (ind_left, frame_y + 5.0),
                    (ind_right, frame_y + 5.0),
                    (ind_center, frame_y + 2.0),
                ]
            )
        else:
            collapse_points = geometry_points(
                [
                    (ind_left, frame_y + 1.0),
                    (ind_right, frame_y + 1.0),
                    (ind_center, frame_y + 4.0),
                ]
            )
        operations.append(
            SchGeometryOp.polygons(
                [collapse_points],
                brush=make_solid_brush(rgb_to_win32(collapse_fill_rgb)),
            )
        )
        operations.append(
            SchGeometryOp.polygons(
                [collapse_points],
                pen=make_pen(rgb_to_win32(collapse_stroke_rgb), width=0),
            )
        )

        left = min(float(self.location.x), float(self.corner.x))
        right = max(float(self.location.x), float(self.corner.x))
        bottom = min(float(self.location.y), float(self.corner.y))
        top = max(float(self.location.y), float(self.corner.y))
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="note",
            object_id="eNote",
            bounds=SchGeometryBounds(
                left=int(round(left)),
                top=int(round(top)),
                right=int(round(right)),
                bottom=int(round(bottom)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _render_note_text(
        self,
        ctx: "SchSvgRenderContext",
        frame_x: float,
        frame_y: float,
        frame_width: float,
        frame_height: float,
    ) -> list[str]:
        """
        Render note text content.

        Note text uses a fixed 5-pixel margin from the frame edge (not configurable).
        This is different from TextFrame which uses text_margin + line_width.

        Args:
            ctx: Render context
            frame_x, frame_y: Top-left corner of frame in SVG coords
            frame_width, frame_height: Frame dimensions in SVG pixels

        Returns:
            List of SVG element strings for text
        """
        from .altium_sch_svg_renderer import color_to_hex, svg_text_or_poly

        elements = []

        # Get font info
        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            self.font_id
        )

        # Font size for SVG display (native mode truncates to int)
        font_size = ctx.get_baseline_font_size(font_size_px)

        # Replace ~1 line separators with newlines and substitute parameters
        text = self.text.replace("~1", "\n")
        text = ctx.substitute_parameters(text)

        # Get text color
        text_color = (
            color_to_hex(self.text_color) if self.text_color is not None else "#000000"
        )

        # Note margin: just text_margin (not line_width + text_margin like TextFrame).
        # Notes use only text_margin regardless of border thickness.
        # The pentagon shape already accounts for the border visually.
        # Uses text_margin_mils, which combines base and fractional parts.
        margin_svg = self.text_margin_mils * ctx.scale

        text_area_x = frame_x + margin_svg
        text_area_y = frame_y + margin_svg
        text_area_width = frame_width - 2 * margin_svg
        text_area_height = frame_height - 2 * margin_svg

        # ClipPath dimensions (same as text area)
        clip_x = text_area_x
        clip_y = text_area_y
        clip_width = text_area_width
        clip_height = text_area_height

        # Word wrap text if enabled
        lines = self._wrap_text_to_lines(
            text, text_area_width, font_name, font_size_px, is_bold, is_italic
        )

        # Use non-truncated transformed size for width calculations.
        font_size_for_width = ctx.get_font_size_for_width(self.font_id)

        # Strip trailing empty line
        if lines and lines[-1] == "":
            lines.pop()

        # Match TextFrame/native pipeline:
        # - baseline uses rendered font size
        # - line spacing uses text-cell height from original pt size
        line_height = int(ctx.get_font_line_height(self.font_id))
        baseline_offset = int(font_size_px)
        current_y = text_area_y + baseline_offset

        # Font attributes
        font_weight = "bold" if is_bold else None
        font_style = "italic" if is_italic else None
        text_decoration = "underline" if is_underline else None

        last_non_empty_line_index = max(
            (index for index, line in enumerate(lines) if line != ""),
            default=-1,
        )

        for index, line in enumerate(lines):
            has_trailing_layout_space = line.endswith((" ", "\t"))
            include_rsb = (
                line != ""
                and not has_trailing_layout_space
                and index != last_non_empty_line_index
            )
            line_width_px = self._measure_aligned_line_width_px(
                line,
                font_name,
                font_size_for_width,
                is_bold=is_bold,
                is_italic=is_italic,
                include_rsb=include_rsb,
            )
            line_x = self._get_aligned_line_x(
                text_area_x,
                text_area_width,
                line_width_px,
            )

            if self.clip_to_rect:
                # Create clipPath group
                clip_id = ctx.next_clip_rect_id()
                text_elem = svg_text_or_poly(
                    ctx,
                    line_x,
                    current_y,
                    line,
                    font_size=font_size,
                    font_family=font_name,
                    fill=text_color,
                    text_decoration=text_decoration,
                    font_weight=font_weight,
                    font_style=font_style,
                    clip_path=f"url(#{clip_id})",
                    poly_target_advance=line_width_px,
                )
                elements.append(
                    f'<g> <clipPath id="{clip_id}"> '
                    f'<rect x="{clip_x}" y="{clip_y}" width="{clip_width}" height="{clip_height}"/>'
                    f"</clipPath>\n"
                    f"  {text_elem}\n</g>"
                )
            else:
                # No clipping
                elements.append(
                    svg_text_or_poly(
                        ctx,
                        line_x,
                        current_y,
                        line,
                        font_size=font_size,
                        font_family=font_name,
                        fill=text_color,
                        text_decoration=text_decoration,
                        font_weight=font_weight,
                        font_style=font_style,
                        poly_target_advance=line_width_px,
                    )
                )

            current_y += line_height

        return elements
