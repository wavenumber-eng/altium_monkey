"""Schematic record model for SchRecordType.TEXT_FRAME."""

import math
from typing import Any

from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
    TextOrientation,
)
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    RectangularBoundsMilsMixin,
    detect_case_mode_method_from_uppercase_fields,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SchSvgRenderContext,
)


def _decode_altium_multiline_text(value: str) -> str:
    """
    Decode Altium text-frame multiline escapes to normal Python text.

    Native V5 stores text-frame and note content with:
    - ``~1`` for newline
    - ``~2`` for ``|``
    - ``~~`` for a literal ``~``
    """
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    i = 0
    while i < len(normalized):
        char = normalized[i]
        if char == "~" and i + 1 < len(normalized):
            next_char = normalized[i + 1]
            if next_char == "1":
                result.append("\n")
                i += 2
                continue
            if next_char == "2":
                result.append("|")
                i += 2
                continue
            if next_char == "~":
                result.append("~")
                i += 2
                continue
        result.append(char)
        i += 1
    return "".join(result)


def _encode_altium_multiline_text(value: str) -> str:
    """
    Encode normal Python multiline text to Altium V5 text-frame storage form.
    """
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("~", "~~").replace("\n", "~1").replace("|", "~2")


class AltiumSchTextFrame(
    RectangularBoundsMilsMixin,
    SingleFontBindableRecordMixin,
    SchGraphicalObject,
):
    """
    Text frame/box record.

    Multi-line text with border, word wrapping, and clipping.

    Attributes:
        text: Text content (lines separated by ~1; ~1~1 = empty line)
        font_id: Font reference ID
        corner: Bottom-right corner coordinate
        alignment: Horizontal alignment (0=Left, 1=Center, 2=Right)
        word_wrap: Enable automatic word wrapping
        clip_to_rect: Clip text at frame boundaries
        show_border: Draw border rectangle
        is_solid: Fill background with area_color
        line_width: Border line width (LineWidth enum)
        text_margin: Margin from frame edges (internal units)
        text_margin_frac: Fractional part of margin
        text_color: Text color (Win32 BGR), None for default black
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_single_font_binding()
        self.text: str = ""
        self.font_id: int = 1
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.is_mirrored: bool = False
        self.is_hidden: bool = False

        # Corner coordinate (opposite of location)
        self.corner = CoordPoint()

        # Text frame specific fields (defaults from native SVG testing)
        # Alignment: 0=Center, 1=Left, 2=Right (opposite of typical convention!)
        self.alignment: int = 0
        self.word_wrap: bool = False  # Default: False (per native SVG testing)
        self.clip_to_rect: bool = False  # Default: False (per native SVG testing)
        self.show_border: bool = False  # Default: False
        self.is_solid: bool = False  # Default: False
        self.line_width: LineWidth = LineWidth.SMALLEST  # Border line width
        # TextMargin is in mils (TextMargin=5 -> 5 mil margin)
        self.text_margin: int = 0  # Default: 0 (not 5!)
        self.text_margin_frac: int = 0  # Fractional part
        self.text_color: int | None = None  # Text color (separate from border color)

        # Track which fields were present for round-trip fidelity
        self._has_corner_x: bool = False
        self._has_corner_y: bool = False
        self._has_alignment: bool = False
        self._has_word_wrap: bool = False
        self._has_clip_to_rect: bool = False
        self._has_line_width: bool = False
        self._has_text_color: bool = False
        self._used_utf8_text: bool = False  # True if original used %UTF8%Text key

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.TEXT_FRAME

    @property
    def text_margin_mils(self) -> float:
        """
        Text margin in mils, combining base and fractional parts.

                Formula: text_margin + text_margin_frac / 100000.0

                Single source of truth for the combined margin value.
        """
        return self.text_margin + self.text_margin_frac / 100000.0

    @text_margin_mils.setter
    def text_margin_mils(self, value: float) -> None:
        """
        Set text margin from mils value, decomposing to base + frac.
        """
        internal = int(round(value * 100000))
        self.text_margin = internal // 100000
        self.text_margin_frac = internal % 100000

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        """
        Parse text frame from record.

                Args:
                   record: Source record dictionary
                    font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record)
        self._font_manager = font_manager
        self._public_font_spec = None

        # Use serializer for field reading
        s = AltiumSerializer()

        # Text field - prefer %UTF8%Text for Unicode, fallback to Text, while
        # still normalizing Altium's pipe-escape sequences in either source.
        r = self._record  # Case-insensitive view
        self.text, _, self._used_utf8_text = read_dynamic_string_field(
            s,
            record,
            r,
            Fields.TEXT,
            default="",
        )
        self.text = _decode_altium_multiline_text(self.text)

        # Use read_font_id for translation support
        self.font_id, _ = s.read_font_id(
            record, Fields.FONT_ID, font_manager, default=1
        )
        orient_val, _ = s.read_int(record, Fields.ORIENTATION, default=0)
        self.orientation = TextOrientation(orient_val)
        self.is_mirrored, _ = s.read_bool(record, Fields.IS_MIRRORED, default=False)
        self.is_hidden, _ = s.read_bool(record, Fields.IS_HIDDEN, default=False)

        # Parse corner coordinates with presence tracking
        corner_x, corner_x_frac, self._has_corner_x = s.read_coord(
            record, "Corner", "X"
        )
        corner_y, corner_y_frac, self._has_corner_y = s.read_coord(
            record, "Corner", "Y"
        )
        self.corner = CoordPoint(corner_x, corner_y, corner_x_frac, corner_y_frac)

        # Text frame specific fields
        self.alignment, self._has_alignment = s.read_int(
            record, Fields.ALIGNMENT, default=0
        )
        # Default to False when fields are missing (per native SVG testing)
        self.word_wrap, self._has_word_wrap = s.read_bool(
            record, Fields.WORD_WRAP, default=False
        )
        self.clip_to_rect, self._has_clip_to_rect = s.read_bool(
            record, Fields.CLIP_TO_RECT, default=False
        )
        self.show_border, _ = s.read_bool(record, Fields.SHOW_BORDER, default=False)
        self.is_solid, _ = s.read_bool(record, Fields.IS_SOLID, default=False)
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

        # Text margin with fractional part (default 0, not 5!)
        self.text_margin, _ = s.read_int(record, Fields.TEXT_MARGIN, default=0)
        self.text_margin_frac, _ = s.read_int(
            record, Fields.TEXT_MARGIN_FRAC, default=0
        )

        # Text color (separate from border color)
        self.text_color, self._has_text_color = s.read_color(
            record, Fields.TEXT_COLOR, default=None
        )

    def serialize_to_record(self) -> dict[str, Any]:
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record
        encoded_text = _encode_altium_multiline_text(self.text)

        # Text content - preserve %UTF8%Text key for Unicode content
        if self._used_utf8_text:
            record["%UTF8%Text"] = encoded_text
        else:
            s.write_str(record, Fields.TEXT, encoded_text, raw)
        s.write_int(record, Fields.FONT_ID, self.font_id, raw)

        # Corner coordinates
        if self._has_corner_x or self.corner.x != 0:
            s.write_coord(record, "Corner", "X", self.corner.x, self.corner.x_frac, raw)
        if self._has_corner_y or self.corner.y != 0:
            s.write_coord(record, "Corner", "Y", self.corner.y, self.corner.y_frac, raw)

        # Text frame fields
        if self._has_alignment or self.alignment != 0:
            s.write_int(record, Fields.ALIGNMENT, self.alignment, raw)

        if self._has_word_wrap or self.word_wrap:
            s.write_bool(record, Fields.WORD_WRAP, self.word_wrap, raw)

        if self._has_clip_to_rect or self.clip_to_rect:
            s.write_bool(record, Fields.CLIP_TO_RECT, self.clip_to_rect, raw)

        s.write_bool(record, Fields.SHOW_BORDER, self.show_border, raw)
        s.write_bool(record, Fields.IS_SOLID, self.is_solid, raw)

        if self._has_line_width or self.line_width != LineWidth.SMALLEST:
            s.write_int(record, Fields.LINE_WIDTH, self.line_width.value, raw)

        # Text margin
        s.write_int(record, Fields.TEXT_MARGIN, self.text_margin, raw)
        if self.text_margin_frac:
            s.write_int(record, Fields.TEXT_MARGIN_FRAC, self.text_margin_frac, raw)

        # Text color (if set)
        if self.text_color is not None:
            s.write_color(record, Fields.TEXT_COLOR, self.text_color, raw, force=True)

        # Orientation (if non-default)
        if self.orientation != TextOrientation.DEGREES_0:
            s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)

        if self.is_mirrored:
            s.write_bool(record, Fields.IS_MIRRORED, self.is_mirrored, raw)
        if self.is_hidden:
            s.write_bool(record, Fields.IS_HIDDEN, self.is_hidden, raw)

        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def _wrap_text_to_lines(
        self,
        text: str,
        max_width_px: float,
        font_name: str,
        font_size_px: float,
        is_bold: bool = False,
        is_italic: bool = False,
    ) -> list[str]:
        """
        Split text into lines using GDI+-compatible word wrapping.

        Algorithm matches Altium's GetSubstringByWidth:
        1. If entire text fits within max_width -> return as-is
        2. Character-by-character accumulation until exceeds max_width
        3. Backtrack to last space/tab for word boundary
        4. Fallback to character-level break if no word boundary

        Args:
            text: Text to wrap (already with ~1 replaced by newlines)
            max_width_px: Maximum line width in pixels
            font_name: Font family name
            font_size_px: Font size in pixels
            is_bold: Whether font is bold
            is_italic: Whether font is italic

        Returns:
            List of text lines
        """
        from .altium_text_metrics import measure_text_width

        if not self.word_wrap:
            # No wrapping: split only on explicit line breaks
            return text.split("\n")

        lines = []
        for paragraph in text.split("\n"):
            if not paragraph:
                lines.append("")
                continue

            # Word wrap algorithm (matches Altium's GetSubstringByWidth)
            remaining = paragraph
            while remaining:
                # Fast path: entire text fits
                text_width = measure_text_width(
                    remaining, font_size_px, font_name, bold=is_bold, italic=is_italic
                )
                if text_width <= max_width_px:
                    lines.append(remaining)
                    break

                # Character-by-character until overflow
                fit_text = ""
                for char in remaining:
                    test = fit_text + char
                    test_width = measure_text_width(
                        test, font_size_px, font_name, bold=is_bold, italic=is_italic
                    )
                    if test_width > max_width_px:
                        # Overshoot - use accumulated text before this char
                        break
                    fit_text = test

                if not fit_text:
                    # Can't fit even one char - force at least one
                    fit_text = remaining[0]

                # Backtrack to word boundary if not at end of remaining
                if (
                    len(fit_text) < len(remaining)
                    and remaining[len(fit_text)] not in " \t"
                ):
                    # Not at word boundary - find last space/tab
                    for j in range(len(fit_text) - 1, -1, -1):
                        if fit_text[j] in " \t":
                            fit_text = fit_text[: j + 1]
                            break

                lines.append(fit_text)
                remaining = remaining[len(fit_text) :]

        return lines

    def _measure_aligned_line_width_px(
        self,
        line: str,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
        include_rsb: bool,
    ) -> float:
        """
        Measure frame text width for horizontal alignment.

        Native TextFrame/Note alignment behaves like a bounding-string width,
        not the label/pin positioning width used elsewhere. Empirically this
        matches including the last glyph's RSB in the width used for center and
        right alignment.
        """
        from .altium_text_metrics import measure_text_width

        return measure_text_width(
            line,
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
            include_rsb=include_rsb,
        )

    def _get_aligned_line_x(
        self,
        text_area_x: float,
        text_area_width: float,
        line_width_px: float,
    ) -> float:
        """
        Match native TextFrame/Note horizontal alignment.

        Alignment values in SchDoc text frames are:
        - `1`: left
        - `2`: right
        - `0`: center/default
        """
        if self.alignment == 1:
            return text_area_x

        if self.alignment == 2:
            return text_area_x + text_area_width - line_width_px

        return text_area_x + (text_area_width - line_width_px) / 2

    def _get_geometry_pen_width(self, units_per_px: int) -> int:
        if self.line_width == LineWidth.SMALLEST:
            return 0
        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        return int(round(stroke_width_mils * units_per_px))

    def _measure_layout_advance_px(
        self,
        text: str,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
        include_rsb: bool,
        use_altium_algorithm: bool,
    ) -> float:
        from .altium_text_metrics import measure_text_width

        if not text:
            return 0.0

        try:
            from .altium_ttf_metrics import get_font, get_font_path
        except ImportError:
            from altium_ttf_metrics import get_font, get_font_path

        font_path = None
        candidate_names: list[str] = []
        if is_bold and is_italic:
            candidate_names.extend(
                [f"{font_name} Bold Italic", f"{font_name} Italic Bold", font_name]
            )
        elif is_bold:
            candidate_names.extend([f"{font_name} Bold", font_name])
        elif is_italic:
            candidate_names.extend([f"{font_name} Italic", font_name])
        else:
            candidate_names.append(font_name)

        for candidate in candidate_names:
            font_path = get_font_path(candidate)
            if font_path:
                break

        if not font_path:
            return measure_text_width(
                text,
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
                include_rsb=include_rsb,
            )

        font = get_font(font_path)
        raw_advance = font._get_raw_advance_sum(text)
        if use_altium_algorithm:
            factor = font.get_factor()
            measure_size = 100.0 * factor
            width = raw_advance * (measure_size / font.units_per_em)
            if not include_rsb:
                last_glyph_id = font.cmap.get(ord(text[-1]), 0)
                width -= font.get_rsb(last_glyph_id) * measure_size
            font_size_pt = (
                font_size_px / factor if factor > 0 else font_size_px * (9.0 / 8.0)
            )
            return width * (font_size_pt / 100.0)

        width = raw_advance * (font_size_px / font.units_per_em)
        if not include_rsb:
            last_glyph_id = font.cmap.get(ord(text[-1]), 0)
            width -= font.get_rsb(last_glyph_id) * font_size_px
        return width

    def _measure_trailing_layout_cursor_px(
        self,
        line: str,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
        use_altium_algorithm: bool,
    ) -> float:
        visible_prefix = line.rstrip(" \t")
        trailing_layout = line[len(visible_prefix) :]
        if not trailing_layout:
            return self._measure_layout_advance_px(
                line,
                font_name,
                font_size_px,
                is_bold=is_bold,
                is_italic=is_italic,
                include_rsb=True,
                use_altium_algorithm=use_altium_algorithm,
            )

        cursor_px = self._measure_layout_advance_px(
            visible_prefix,
            font_name,
            font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
            include_rsb=True,
            use_altium_algorithm=use_altium_algorithm,
        )
        tab_stop_px = font_size_px * 2.0
        for char in trailing_layout:
            if char == "\t" and tab_stop_px > 0.0:
                cursor_px = (math.floor(cursor_px / tab_stop_px) + 1.0) * tab_stop_px
                continue
            cursor_px += self._measure_layout_advance_px(
                char,
                font_name,
                font_size_px,
                is_bold=is_bold,
                is_italic=is_italic,
                include_rsb=True,
                use_altium_algorithm=use_altium_algorithm,
            )
        return cursor_px

    def _wrapped_text_frame_lines(
        self,
        ctx: SchSvgRenderContext,
        *,
        text_area_width: float,
    ) -> tuple[str, float, bool, bool, bool, list[str], bool]:
        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            self.font_id
        )
        text = self.text.replace("~1", "\n")
        text = ctx.substitute_parameters(text)
        lines = self._wrap_text_to_lines(
            text,
            text_area_width,
            font_name,
            font_size_px,
            is_bold,
            is_italic,
        )
        had_trailing_empty_line = bool(lines and lines[-1] == "")
        if had_trailing_empty_line:
            lines.pop()
        return (
            font_name,
            font_size_px,
            is_bold,
            is_italic,
            is_underline,
            lines,
            had_trailing_empty_line,
        )

    def _text_frame_clip_geometry(
        self,
        *,
        clip_x: float,
        clip_y: float,
        clip_width: float,
        clip_height: float,
        sheet_height_px: float,
        units_per_px: int,
    ) -> tuple[int, int, int, int] | None:
        from .altium_sch_geometry_oracle import svg_coord_to_geometry

        if not self.clip_to_rect:
            return None

        clip_x1, clip_y1 = svg_coord_to_geometry(
            clip_x,
            clip_y,
            sheet_height_px=sheet_height_px,
            units_per_px=units_per_px,
        )
        clip_x2, clip_y2 = svg_coord_to_geometry(
            clip_x + clip_width,
            clip_y + clip_height,
            sheet_height_px=sheet_height_px,
            units_per_px=units_per_px,
        )
        return clip_x1, clip_y1, clip_x2, clip_y2

    def _text_frame_line_state(
        self,
        *,
        index: int,
        line: str,
        lines: list[str],
        had_trailing_empty_line: bool,
        last_non_empty_line_index: int,
    ) -> tuple[str | None, bool, str, bool, bool, str]:
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        has_trailing_layout_space = line.endswith((" ", "\t"))
        trailing_layout = (
            line[len(line.rstrip(" \t")) :] if has_trailing_layout_space else ""
        )
        split_token_continuation = (
            line != ""
            and next_line not in {"", None}
            and not has_trailing_layout_space
            and not str(next_line).startswith((" ", "\t"))
        )
        has_explicit_break_after_line = next_line is not None or (
            had_trailing_empty_line and index == len(lines) - 1
        )
        include_rsb = (
            line != ""
            and not split_token_continuation
            and not has_trailing_layout_space
            and (index != last_non_empty_line_index or has_explicit_break_after_line)
        )
        payload_text = self._text_frame_payload_text(
            index=index,
            line=line,
            next_line=next_line,
            lines=lines,
            had_trailing_empty_line=had_trailing_empty_line,
            split_token_continuation=split_token_continuation,
            has_trailing_layout_space=has_trailing_layout_space,
        )
        return (
            next_line,
            has_trailing_layout_space,
            trailing_layout,
            split_token_continuation,
            include_rsb,
            payload_text,
        )

    def _text_frame_payload_text(
        self,
        *,
        index: int,
        line: str,
        next_line: str | None,
        lines: list[str],
        had_trailing_empty_line: bool,
        split_token_continuation: bool,
        has_trailing_layout_space: bool,
    ) -> str:
        if line == "":
            return "\r"
        if line.isspace():
            return line + "\r"
        if split_token_continuation:
            return line
        if index == len(lines) - 1 and not had_trailing_empty_line:
            return line
        if has_trailing_layout_space and next_line not in {"", None}:
            return line
        return line + "\r"

    def _append_text_frame_string_op(
        self,
        operations: list[Any],
        *,
        clip_geometry: tuple[int, int, int, int] | None,
        geometry_x: int,
        geometry_y: int,
        payload_text: str,
        font_payload: Any,
        text_brush: Any,
    ) -> None:
        from .altium_sch_geometry_oracle import SchGeometryOp

        if clip_geometry is not None:
            clip_x1, clip_y1, clip_x2, clip_y2 = clip_geometry
            operations.append(
                SchGeometryOp.push_clip(
                    x1=clip_x1,
                    y1=clip_y1,
                    x2=clip_x2,
                    y2=clip_y2,
                )
            )
        operations.append(
            SchGeometryOp.string(
                x=geometry_x,
                y=geometry_y,
                text=payload_text,
                font=font_payload,
                brush=text_brush,
            )
        )
        if clip_geometry is not None:
            operations.append(SchGeometryOp.pop_clip())

    def _build_text_geometry_ops(
        self,
        ctx: SchSvgRenderContext,
        *,
        text_area_x: float,
        text_area_y: float,
        text_area_width: float,
        clip_x: float,
        clip_y: float,
        clip_width: float,
        clip_height: float,
        units_per_px: int,
    ) -> list[Any]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_font_payload,
            make_solid_brush,
            svg_coord_to_geometry,
        )

        if not self.text:
            return []

        (
            font_name,
            font_size_px,
            is_bold,
            is_italic,
            is_underline,
            lines,
            had_trailing_empty_line,
        ) = self._wrapped_text_frame_lines(
            ctx,
            text_area_width=text_area_width,
        )
        font_size_for_width = ctx.get_font_size_for_width(self.font_id)
        line_height = int(ctx.get_font_line_height(self.font_id))
        baseline_offset = int(font_size_px)
        current_y = text_area_y + baseline_offset
        sheet_height_px = float(ctx.sheet_height or 0.0)
        text_color_raw = int(self.text_color) if self.text_color is not None else 0
        font_payload = make_font_payload(
            name=font_name,
            size_px=font_size_px,
            units_per_px=units_per_px,
            rotation=0.0,
            underline=is_underline,
            italic=is_italic,
            bold=is_bold,
            strikeout=False,
        )
        text_brush = make_solid_brush(text_color_raw)
        last_non_empty_line_index = max(
            (index for index, line in enumerate(lines) if line != ""),
            default=-1,
        )
        clip_geometry = self._text_frame_clip_geometry(
            clip_x=clip_x,
            clip_y=clip_y,
            clip_width=clip_width,
            clip_height=clip_height,
            sheet_height_px=sheet_height_px,
            units_per_px=units_per_px,
        )

        operations: list[SchGeometryOp] = []
        for index, line in enumerate(lines):
            (
                next_line,
                has_trailing_layout_space,
                trailing_layout,
                split_token_continuation,
                include_rsb,
                payload_text,
            ) = self._text_frame_line_state(
                index=index,
                line=line,
                lines=lines,
                had_trailing_empty_line=had_trailing_empty_line,
                last_non_empty_line_index=last_non_empty_line_index,
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
            geometry_x, geometry_y = svg_coord_to_geometry(
                line_x,
                current_y - baseline_offset,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            self._append_text_frame_string_op(
                operations,
                clip_geometry=clip_geometry,
                geometry_x=geometry_x,
                geometry_y=geometry_y,
                payload_text=payload_text,
                font_payload=font_payload,
                text_brush=text_brush,
            )
            if (
                getattr(ctx, "native_svg_export", False)
                and has_trailing_layout_space
                and "\t" in trailing_layout
                and next_line not in {"", None}
            ):
                trailing_cursor_x = line_x + self._measure_trailing_layout_cursor_px(
                    line,
                    font_name,
                    font_size_px,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    use_altium_algorithm=True,
                )
                trailing_geometry_x, _ = svg_coord_to_geometry(
                    trailing_cursor_x,
                    current_y - baseline_offset,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                self._append_text_frame_string_op(
                    operations,
                    clip_geometry=clip_geometry,
                    geometry_x=trailing_geometry_x,
                    geometry_y=geometry_y,
                    payload_text="\r",
                    font_payload=font_payload,
                    text_brush=text_brush,
                )
            current_y += line_height

        return operations

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> Any:
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

        geo_left, geo_top = svg_coord_to_geometry(
            frame_x,
            frame_y,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        geo_right, geo_bottom = svg_coord_to_geometry(
            frame_x + frame_width,
            frame_y + frame_height,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )

        operations: list[SchGeometryOp] = []
        if self.is_solid:
            fill_color_raw = (
                int(self.area_color) if self.area_color is not None else 0xFFFFFF
            )
            operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=geo_left,
                    y1=geo_top,
                    x2=geo_right,
                    y2=geo_bottom,
                    brush=make_solid_brush(fill_color_raw),
                )
            )
            if not self.show_border:
                # Native TextFrameDrawGraphObject temporarily swaps Color=AreaColor
                # and routes solid/no-border frames through RectangleDrawGraphObject,
                # which emits an area-colored outline in addition to the fill.
                operations.append(
                    SchGeometryOp.rounded_rectangle(
                        x1=geo_left,
                        y1=geo_top,
                        x2=geo_right,
                        y2=geo_bottom,
                        pen=make_pen(
                            fill_color_raw,
                            width=self._get_geometry_pen_width(units_per_px),
                            line_join="pljMiter",
                        ),
                    )
                )

        if self.show_border:
            stroke_color_raw = int(self.color) if self.color is not None else 0
            operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=geo_left,
                    y1=geo_top,
                    x2=geo_right,
                    y2=geo_bottom,
                    pen=make_pen(
                        stroke_color_raw,
                        width=self._get_geometry_pen_width(units_per_px),
                        line_join="pljMiter",
                    ),
                )
            )

        border_width = 0.0
        if self.line_width != LineWidth.SMALLEST:
            border_width = (
                LINE_WIDTH_MILS.get(self.line_width, 1.0) * ctx.get_stroke_scale()
            )
        margin_svg = self.text_margin_mils * ctx.scale + border_width
        text_area_x = frame_x + margin_svg
        text_area_y = frame_y + margin_svg
        text_area_width = frame_width - 2 * margin_svg
        text_area_height = frame_height - 2 * margin_svg
        if margin_svg > 0:
            clip_x = text_area_x
            clip_y = text_area_y
            clip_width = text_area_width
            clip_height = text_area_height
        else:
            clip_x = frame_x
            clip_y = frame_y + 0.0001
            clip_width = frame_width - 0.00011
            clip_height = frame_height - 0.0001

        operations.extend(
            self._build_text_geometry_ops(
                ctx,
                text_area_x=text_area_x,
                text_area_y=text_area_y,
                text_area_width=text_area_width,
                clip_x=clip_x,
                clip_y=clip_y,
                clip_width=clip_width,
                clip_height=clip_height,
                units_per_px=units_per_px,
            )
        )

        left = min(float(self.location.x), float(self.corner.x))
        right = max(float(self.location.x), float(self.corner.x))
        bottom = min(float(self.location.y), float(self.corner.y))
        top = max(float(self.location.y), float(self.corner.y))
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="textframe",
            object_id="eTextFrame",
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


# =============================================================================
# Graphical Primitives
# =============================================================================
