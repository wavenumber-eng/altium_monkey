"""Schematic record model for SchRecordType.NOTE."""

from typing import TYPE_CHECKING

from .altium_record_sch__text_frame import AltiumSchTextFrame
from ._sch_managed_defaults import LONG_TEXT_COLOR, NOTE_BORDER_COLOR, NOTE_FILL_COLOR
from .altium_record_types import LineWidth, SchRecordType
from .altium_sch_record_helpers import _RecordFields
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)

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
        self.color = NOTE_BORDER_COLOR
        self.area_color = NOTE_FILL_COLOR
        self.text_color = LONG_TEXT_COLOR
        self._init_family_dynamic_unique_id()
        self.author: str = "Author"
        self.collapsed: bool = False
        self.text = "Type @ to refer to a designator"
        self.alignment = 1
        self.word_wrap = True
        self.clip_to_rect = True
        self.show_border = True
        self.is_solid = True
        self.text_margin = 5
        self.text_margin_frac = 0
        self._has_author: bool = False
        self._used_utf8_author: bool = False
        self._has_collapsed: bool = False
        self._source_author = self.author
        self._source_collapsed = self.collapsed
        self._capture_graphical_source_state()
        self._capture_text_frame_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.NOTE

    def parse_from_record(
        self,
        record: _RecordFields,
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
        self._parse_family_dynamic_unique_id(s, record)

        # ImportNote reads these fields but deliberately normalizes the object
        # state to the managed note invariants.
        self.line_width = LineWidth.SMALLEST
        self.is_solid = True
        self.show_border = True
        if not self._has_text_margin:
            self.text_margin = 5
            self.text_margin_frac = 0
        if self.color is None:
            self.color = 0
        if self.area_color is None:
            self.area_color = 0
        if self.text_color is None:
            self.text_color = 0

        self.author, self._has_author, self._used_utf8_author = (
            read_dynamic_string_field(
                s,
                record,
                self._record,
                Fields.AUTHOR,
                default="",
            )
        )
        self.collapsed, self._has_collapsed = s.read_bool(
            record, Fields.COLLAPSED, default=False
        )
        self._source_author = self.author
        self._source_collapsed = self.collapsed
        self._capture_text_frame_source_state()

    def serialize_to_record(self) -> _RecordFields:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        s = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record
        self._serialize_managed_family_color(
            record, s, Fields.COLOR.canonical, int(self.color or 0)
        )
        self._serialize_managed_family_color(
            record, s, Fields.AREA_COLOR.canonical, int(self.area_color or 0)
        )
        self._serialize_managed_family_color(
            record, s, Fields.TEXT_COLOR.canonical, int(self.text_color or 0)
        )
        self._serialize_note_fields(record, s, raw)
        self._remove_absent_note_colors(record, s, raw)
        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Corner.X",
                "Corner.X_Frac",
                "Corner.Y",
                "Corner.Y_Frac",
                "LineWidth",
                "Color",
                "AreaColor",
                "TextColor",
                "FontID",
                "IsSolid",
                "ShowBorder",
                "Alignment",
                "WordWrap",
                "ClipToRect",
                "Text",
                "TextMargin",
                "TextMargin_Frac",
                "Collapsed",
                "Author",
                "UniqueID",
            ),
        )

    def _serialize_note_fields(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self.author != self._source_author and not self.author:
            self._remove_fields_case_insensitively(record, ["Author", "%UTF8%Author"])
        else:
            write_dynamic_string_field(
                serializer,
                record,
                Fields.AUTHOR,
                self.author,
                raw_record=raw_record,
                used_utf8_sidecar=self._used_utf8_author,
                was_present=self._has_author,
                force=self.author != self._source_author,
            )
        self._serialize_managed_family_bool(
            record, serializer, Fields.COLLAPSED.canonical, self.collapsed
        )

    def _remove_absent_note_colors(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        for field, value, source_value in (
            (Fields.COLOR, self.color, 0),
            (Fields.AREA_COLOR, self.area_color, 0),
        ):
            _, was_present = field.find_in_record(raw_record or {})
            if raw_record is not None and not was_present and value == source_value:
                serializer.remove_field(record, field)

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
            def blend_channel(value: int, background: int) -> int:
                delta = (background - value) * percent
                quotient = delta // 100 if delta >= 0 else -((-delta) // 100)
                return value + quotient

            r = blend_channel(color[0], bg[0])
            g = blend_channel(color[1], bg[1])
            b = blend_channel(color[2], bg[2])
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
        pen_width = self._get_geometry_pen_width(units_per_px)

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
                pen=make_pen(
                    rgb_to_win32(shadow_stroke_rgb),
                    alpha=125,
                    width=pen_width,
                ),
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
                pen=make_pen(
                    int(self.color) if self.color is not None else 0,
                    width=pen_width,
                ),
            )
        )

        if not self.collapsed and self.text:
            from .altium_sch_svg_renderer import LINE_WIDTH_MILS

            border_width = 0.0
            if self.line_width != LineWidth.SMALLEST:
                border_width = (
                    LINE_WIDTH_MILS.get(self.line_width, 1.0) * ctx.get_stroke_scale()
                )
            margin_svg = self._text_margin_record_units * ctx.scale + border_width
            (
                text_area_x,
                text_area_y,
                text_area_width,
                text_area_height,
            ) = self._get_note_text_rect(
                frame_x,
                frame_y,
                frame_width,
                frame_height,
                margin_svg,
            )
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
                pen=make_pen(rgb_to_win32(dog_ear_stroke_rgb), width=pen_width),
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
                pen=make_pen(rgb_to_win32(collapse_stroke_rgb), width=pen_width),
            )
        )

        left = min(float(self.location.x), float(self.corner.x))
        top = max(float(self.location.y), float(self.corner.y))
        if self.collapsed:
            right = left + dog_ear_size
            bottom = top - dog_ear_size
        else:
            right = max(float(self.location.x), float(self.corner.x))
            bottom = min(float(self.location.y), float(self.corner.y))
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

    @staticmethod
    def _get_note_text_rect(
        frame_x: float,
        frame_y: float,
        frame_width: float,
        frame_height: float,
        margin: float,
    ) -> tuple[float, float, float, float]:
        return (
            min(frame_x + margin, frame_x + frame_width / 2),
            min(frame_y + margin, frame_y + frame_height / 2),
            max(0.0, frame_width - 2 * margin),
            max(0.0, frame_height - 2 * margin),
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

        Note text uses the record's split-coordinate text margin from the frame edge.

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

        from .altium_sch_svg_renderer import LINE_WIDTH_MILS

        border_width = 0.0
        if self.line_width != LineWidth.SMALLEST:
            border_width = (
                LINE_WIDTH_MILS.get(self.line_width, 1.0) * ctx.get_stroke_scale()
            )
        margin_svg = self._text_margin_record_units * ctx.scale + border_width

        (
            text_area_x,
            text_area_y,
            text_area_width,
            text_area_height,
        ) = self._get_note_text_rect(
            frame_x,
            frame_y,
            frame_width,
            frame_height,
            margin_svg,
        )

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
