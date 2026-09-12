"""Schematic record model for SchRecordType.TEXT_FRAME."""

import math
from typing import Any, cast

from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
    TextOrientation,
)
from ._sch_managed_defaults import BOX_BORDER_COLOR, BOX_FILL_COLOR, LONG_TEXT_COLOR
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    RectangularBoundsMilsMixin,
    _RecordFields,
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
        if self.record_type is SchRecordType.TEXT_FRAME:
            self.color = BOX_BORDER_COLOR
            self.area_color = BOX_FILL_COLOR
        self._init_single_font_binding()
        self.text: str = "Type @ to refer to a designator"
        self.font_id: int = 1
        self.orientation: TextOrientation = TextOrientation.DEGREES_0
        self.is_mirrored: bool = False
        self.is_hidden: bool = False

        # Corner coordinate (opposite of location)
        self.corner = CoordPoint(50, 50)

        # Authored defaults follow SchDataTextFrame.SetDefault. Sparse import
        # overwrites these independently below without materializing fields.
        self.alignment: int = 1
        self.word_wrap: bool = True
        self.clip_to_rect: bool = True
        self.show_border: bool = False  # Default: False
        self.is_solid: bool = True
        self.line_width: LineWidth = LineWidth.SMALLEST  # Border line width
        # Stored as split internal coordinates; use text_margin_mils for semantic access.
        self.text_margin: int = 0
        self.text_margin_frac: int = 5
        self.text_color: int | None = (
            LONG_TEXT_COLOR if self.record_type is SchRecordType.TEXT_FRAME else None
        )

        # Track which fields were present for round-trip fidelity
        self._has_corner_x: bool = False
        self._has_corner_y: bool = False
        self._has_alignment: bool = False
        self._has_word_wrap: bool = False
        self._has_clip_to_rect: bool = False
        self._has_show_border: bool = False
        self._has_is_solid: bool = False
        self._has_line_width: bool = False
        self._has_text_margin: bool = False
        self._has_text_margin_frac: bool = False
        self._has_text_color: bool = False
        self._used_utf8_text: bool = False  # True if original used %UTF8%Text key
        self._capture_text_frame_source_state()

    def _capture_text_frame_source_state(self) -> None:
        """Remember parsed semantic state so sparse mutations can be detected."""
        self._source_alignment = self.alignment
        self._source_word_wrap = self.word_wrap
        self._source_clip_to_rect = self.clip_to_rect
        self._source_show_border = self.show_border
        self._source_is_solid = self.is_solid
        self._source_line_width = self.line_width
        self._source_text_margin = self.text_margin
        self._source_text_margin_frac = self.text_margin_frac
        self._source_text_color = self.text_color
        self._source_text = self.text
        self._source_orientation = self.orientation
        self._source_is_mirrored = self.is_mirrored
        self._source_is_hidden = self.is_hidden

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.TEXT_FRAME

    @property
    def text_margin_mils(self) -> float:
        """
        Text margin in mils, combining base and fractional parts.

                Formula: text_margin * 10 + text_margin_frac / 10000.0

                Single source of truth for the combined margin value.
        """
        return self.text_margin * 10 + self.text_margin_frac / 10000.0

    @text_margin_mils.setter
    def text_margin_mils(self, value: float) -> None:
        """
        Set text margin from mils value, decomposing to base + frac.
        """
        if not math.isfinite(value):
            raise ValueError("text_margin_mils must be finite")
        internal = int(round(value * 10000))
        self.text_margin = internal // 100000
        self.text_margin_frac = internal % 100000

    @property
    def _text_margin_record_units(self) -> float:
        """Return the margin in the legacy coordinate units used by SVG geometry."""
        return self.text_margin + self.text_margin_frac / 100000.0

    def parse_from_record(
        self,
        record: _RecordFields,
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
        # TextFrame's V5 codec does not import these inherited compatibility
        # fields. Keep their raw spellings losslessly, but do not let stale
        # values alter the typed TextFrame state.
        self.orientation = TextOrientation.DEGREES_0
        self.is_mirrored = False
        self.is_hidden = False

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
        self.show_border, self._has_show_border = s.read_bool(
            record, Fields.SHOW_BORDER, default=False
        )
        self.is_solid, self._has_is_solid = s.read_bool(
            record, Fields.IS_SOLID, default=False
        )
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

        # Import_Coord_WithDefault supplies five internal units when absent.
        self.text_margin, _, self._has_text_margin = s.read_coord(
            record, Fields.TEXT_MARGIN.canonical
        )
        self.text_margin_frac, self._has_text_margin_frac = s.read_int(
            record, Fields.TEXT_MARGIN_FRAC, default=0
        )
        if not self._has_text_margin_frac:
            self.text_margin_frac = 5

        # Text color (separate from border color)
        self.text_color, self._has_text_color = s.read_color(
            record, Fields.TEXT_COLOR, default=None
        )
        self._apply_imported_color_defaults(area_color=True)
        if not self._has_text_color:
            self.text_color = 0
        self._capture_text_frame_source_state()

    def serialize_to_record(self) -> _RecordFields:
        self._ensure_bound_public_font_ready()
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record
        encoded_text = _encode_altium_multiline_text(self.text)
        self._serialize_text_frame_text(record, s, raw, encoded_text)
        self._serialize_text_frame_corners(record, s, raw)
        self._serialize_text_frame_layout(record, s, raw)
        self._serialize_text_frame_margin_and_color(record, s, raw)
        self._serialize_text_frame_orientation(record, s, raw)
        self._move_geometry_identity_to_end_if_needed(record)
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
                "UniqueID",
            ),
        )

    def _serialize_text_frame_text(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
        encoded_text: str,
    ) -> None:
        if raw_record is None or self.text != self._source_text:
            if not encoded_text:
                serializer.remove_field(record, Fields.TEXT)
                serializer.remove_field(record, "%UTF8%Text")
            elif self._used_utf8_text:
                serializer.remove_field(record, "%UTF8%Text")
                record["%UTF8%Text"] = encoded_text
            else:
                serializer.remove_field(record, Fields.TEXT)
                serializer.remove_field(record, "%UTF8%Text")
                serializer.write_str(
                    record, Fields.TEXT, encoded_text, None, force=True
                )
        self._serialize_managed_font_id(
            record,
            serializer,
            Fields.FONT_ID.canonical,
            self.font_id,
            self._get_fallback_font_manager(),
        )

    def _serialize_text_frame_corners(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        self._serialize_managed_family_coord(
            record, serializer, "Corner", "X", self.corner.x, self.corner.x_frac
        )
        self._serialize_managed_family_coord(
            record, serializer, "Corner", "Y", self.corner.y, self.corner.y_frac
        )

    def _serialize_text_frame_layout(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        self._serialize_text_frame_alignment(record, serializer, raw_record)
        self._serialize_text_frame_border(record, serializer, raw_record)

    def _serialize_text_frame_alignment(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self.record_type is SchRecordType.NOTE:
            if (
                (raw_record is None and self.alignment != 0)
                or self._has_alignment
                or self.alignment != self._source_alignment
            ):
                serializer.write_int(
                    record,
                    Fields.ALIGNMENT,
                    self.alignment,
                    raw_record,
                    force=raw_record is None
                    or self.alignment != self._source_alignment,
                )
            for field, value, source, present in (
                (
                    Fields.WORD_WRAP,
                    self.word_wrap,
                    self._source_word_wrap,
                    self._has_word_wrap,
                ),
                (
                    Fields.CLIP_TO_RECT,
                    self.clip_to_rect,
                    self._source_clip_to_rect,
                    self._has_clip_to_rect,
                ),
            ):
                if (raw_record is None and value) or present or value != source:
                    serializer.write_bool(
                        record,
                        field,
                        value,
                        raw_record,
                        force=raw_record is None or value != source,
                    )
            return
        self._serialize_managed_family_int(
            record, serializer, Fields.ALIGNMENT.canonical, self.alignment
        )
        self._serialize_managed_family_bool(
            record, serializer, Fields.WORD_WRAP.canonical, self.word_wrap
        )
        self._serialize_managed_family_bool(
            record, serializer, Fields.CLIP_TO_RECT.canonical, self.clip_to_rect
        )

    def _serialize_text_frame_border(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if self.record_type is SchRecordType.NOTE:
            for field, value, source, present in (
                (
                    Fields.SHOW_BORDER,
                    self.show_border,
                    self._source_show_border,
                    self._has_show_border,
                ),
                (
                    Fields.IS_SOLID,
                    self.is_solid,
                    self._source_is_solid,
                    self._has_is_solid,
                ),
            ):
                if (raw_record is None and value) or present or value != source:
                    serializer.write_bool(
                        record,
                        field,
                        value,
                        raw_record,
                        force=raw_record is None or value != source,
                    )
            if (
                (raw_record is None and self.line_width.value != 0)
                or self._has_line_width
                or self.line_width != self._source_line_width
            ):
                serializer.write_int(
                    record,
                    Fields.LINE_WIDTH,
                    self.line_width.value,
                    raw_record,
                    force=raw_record is None
                    or self.line_width != self._source_line_width,
                )
            return
        self._serialize_managed_family_bool(
            record, serializer, Fields.SHOW_BORDER.canonical, self.show_border
        )
        self._serialize_managed_family_bool(
            record, serializer, Fields.IS_SOLID.canonical, self.is_solid
        )
        self._serialize_managed_family_int(
            record, serializer, Fields.LINE_WIDTH.canonical, self.line_width.value
        )

    def _serialize_text_frame_margin_and_color(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        self._serialize_text_frame_margin(record, serializer, raw_record)
        self._serialize_text_frame_color(record, serializer, raw_record)

    def _serialize_text_frame_margin(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        changed = (
            self.text_margin != self._source_text_margin
            or self.text_margin_frac != self._source_text_margin_frac
        )
        if raw_record is not None and not changed:
            return

        serializer.remove_field(record, Fields.TEXT_MARGIN)
        serializer.remove_field(record, Fields.TEXT_MARGIN_FRAC)
        serializer.write_coord(
            record,
            Fields.TEXT_MARGIN.canonical,
            "",
            self.text_margin,
            self.text_margin_frac,
            raw_record,
            force=True,
        )

    def _serialize_text_frame_color(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        if raw_record is not None and self.text_color == self._source_text_color:
            return
        serializer.remove_field(record, Fields.TEXT_COLOR)
        if self.text_color not in (None, 0):
            serializer.write_color(
                record, Fields.TEXT_COLOR, self.text_color, None, force=True
            )

    def _serialize_text_frame_orientation(
        self,
        record: _RecordFields,
        serializer: AltiumSerializer,
        raw_record: _RecordFields | None,
    ) -> None:
        del serializer
        if raw_record is None:
            self._remove_fields_case_insensitively(
                record,
                [
                    "Orientation",
                    "IsMirrored",
                    "IsHidden",
                    "LineStyle",
                    "LineStyleExt",
                    "Transparent",
                ],
            )

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
        paragraphs = text.split("\n")
        if text.endswith("\n"):
            paragraphs.pop()
        if not self.word_wrap:
            return paragraphs

        lines: list[str] = []
        for paragraph in paragraphs:
            remaining = self._text_to_utf16_units(paragraph)
            while remaining:
                length = self._substring_utf16_length_by_width(
                    remaining,
                    max_width_px,
                    font_name,
                    font_size_px,
                    is_bold=is_bold,
                    is_italic=is_italic,
                )
                if length == 0:
                    forced = [remaining[0]]
                    length = 1
                    if len(remaining) > 1 and remaining[1] in {ord(" "), ord("\t")}:
                        forced.append(remaining[0])
                        length = 2
                    lines.append(self._utf16_units_to_text(forced))
                else:
                    lines.append(self._utf16_units_to_text(remaining[:length]))
                remaining = remaining[length:]
        return lines

    @staticmethod
    def _text_to_utf16_units(text: str) -> list[int]:
        encoded = text.encode("utf-16-le", errors="surrogatepass")
        return [
            encoded[index] | (encoded[index + 1] << 8)
            for index in range(0, len(encoded), 2)
        ]

    @staticmethod
    def _utf16_units_to_text(units: list[int]) -> str:
        encoded = bytearray(len(units) * 2)
        for index, unit in enumerate(units):
            encoded[index * 2] = unit & 0xFF
            encoded[index * 2 + 1] = unit >> 8
        return encoded.decode("utf-16-le", errors="surrogatepass")

    def _substring_utf16_length_by_width(
        self,
        text: list[int],
        max_width_px: float,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
    ) -> int:
        length = self._initial_utf16_fit_length(
            text,
            max_width_px,
            font_name,
            font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
        )
        if length < len(text) and self._is_layout_unit(text[length]):
            length += 1
        if length in {0, len(text)} or self._is_layout_unit(text[length]):
            return length
        return self._backtrack_utf16_word_boundary(text, length)

    def _initial_utf16_fit_length(
        self,
        text: list[int],
        max_width_px: float,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
    ) -> int:
        if (
            self._measure_tabbed_units_width_px(
                text,
                font_name,
                font_size_px,
                is_bold=is_bold,
                is_italic=is_italic,
            )
            <= max_width_px
        ):
            return len(text)
        length = 1
        if (
            self._measure_tabbed_units_width_px(
                text[:length],
                font_name,
                font_size_px,
                is_bold=is_bold,
                is_italic=is_italic,
            )
            < max_width_px
        ):
            while length < len(text):
                length += 1
                width = self._measure_tabbed_units_width_px(
                    text[:length],
                    font_name,
                    font_size_px,
                    is_bold=is_bold,
                    is_italic=is_italic,
                )
                if width >= max_width_px:
                    if width > max_width_px:
                        length -= 1
                    break
        return length

    @staticmethod
    def _backtrack_utf16_word_boundary(text: list[int], length: int) -> int:
        boundary = length - 1
        while boundary > 0 and not AltiumSchTextFrame._is_layout_unit(text[boundary]):
            boundary -= 1
        return length if boundary == 0 else boundary + 1

    @staticmethod
    def _is_layout_unit(unit: int) -> bool:
        return unit in {ord(" "), ord("\t")}

    def _measure_tabbed_units_width_px(
        self,
        units: list[int],
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
    ) -> float:
        return self._measure_tabbed_line_width_px(
            self._utf16_units_to_text(units),
            font_name,
            font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
        )

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

    def _measure_tabbed_line_width_px(
        self,
        line: str,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
        include_rsb: bool = True,
    ) -> float:
        tab_width = self._managed_tab_width_internal(
            font_name,
            font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
        )
        if tab_width == 0:
            tab_width = 4_000_000
        width = 0
        remaining = line
        while remaining:
            word, separator, remaining = remaining.partition("\t")
            width += int(
                self._measure_aligned_line_width_px(
                    word.replace("\r", ""),
                    font_name,
                    font_size_px,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    include_rsb=include_rsb,
                )
                * 100_000
            )
            if separator:
                width = (
                    width + tab_width
                    if width % tab_width == 0
                    else math.ceil(width / tab_width) * tab_width
                )
        return width / 100_000

    def _managed_tab_width_internal(
        self,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
    ) -> int:
        alphabet_width = int(
            self._measure_aligned_line_width_px(
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                font_name,
                font_size_px,
                is_bold=is_bold,
                is_italic=is_italic,
                include_rsb=True,
            )
            * 100_000
        )
        return int(alphabet_width * 8 / 52)

    def _managed_tab_runs(
        self,
        text: str,
        start_x: float,
        font_name: str,
        font_size_px: float,
        *,
        is_bold: bool,
        is_italic: bool,
    ) -> list[tuple[str, float]]:
        if not text:
            return []
        tab_width = self._managed_tab_width_internal(
            font_name,
            font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
        )
        runs: list[tuple[str, float]] = []
        cursor = 0
        remaining = text
        while True:
            word, separator, remaining = remaining.partition("\t")
            runs.append((word, start_x + cursor / 100_000))
            if not separator or not remaining:
                return runs
            cursor += int(
                self._measure_aligned_line_width_px(
                    word.replace("\r", ""),
                    font_name,
                    font_size_px,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    include_rsb=True,
                )
                * 100_000
            )
            cursor = (
                cursor + tab_width
                if tab_width == 0 or cursor % tab_width == 0
                else math.ceil(cursor / tab_width) * tab_width
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

    def _get_geometry_pen_width(self, units_per_px: int) -> float:
        from .altium_sch_geometry_oracle import _geometry_item_length

        if self.line_width == LineWidth.SMALLEST:
            return 0
        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        return _geometry_item_length(stroke_width_mils, units_per_px=units_per_px)

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

        from .altium_ttf_metrics import get_font, get_font_path

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
        text = self._restore_managed_line_endings(text)
        lines = self._wrap_text_to_lines(
            text,
            text_area_width,
            font_name,
            font_size_px,
            is_bold,
            is_italic,
        )
        had_trailing_empty_line = False
        return (
            font_name,
            font_size_px,
            is_bold,
            is_italic,
            is_underline,
            lines,
            had_trailing_empty_line,
        )

    @staticmethod
    def _restore_managed_line_endings(text: str) -> str:
        restored: list[str] = []
        previous = ""
        for character in text:
            if character == "\n" and previous != "\r":
                restored.append("\r")
            restored.append(character)
            previous = character
        return "".join(restored)

    def _text_frame_clip_geometry(
        self,
        *,
        clip_x: float,
        clip_y: float,
        clip_width: float,
        clip_height: float,
        sheet_height_px: float,
        units_per_px: int,
    ) -> tuple[float, float, float, float] | None:
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
        layout_line = line.removesuffix("\r")
        layout_next_line = (
            next_line.removesuffix("\r") if next_line is not None else None
        )
        has_trailing_layout_space = layout_line.endswith((" ", "\t"))
        trailing_layout = (
            layout_line[len(layout_line.rstrip(" \t")) :]
            if has_trailing_layout_space
            else ""
        )
        split_token_continuation = (
            layout_line != ""
            and layout_next_line not in {"", None}
            and not has_trailing_layout_space
            and not str(layout_next_line).startswith((" ", "\t"))
        )
        has_explicit_break_after_line = next_line is not None or (
            had_trailing_empty_line and index == len(lines) - 1
        )
        include_rsb = (
            layout_line != ""
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
        if line.endswith("\r"):
            return line
        if line == "":
            return "\r"
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
        clip_geometry: tuple[float, float, float, float] | None,
        geometry_x: float,
        geometry_y: float,
        payload_text: str,
        font_payload: Any,
        text_brush: Any,
    ) -> None:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            _geometry_clip_can_draw,
        )

        if clip_geometry is not None:
            clip_x1, clip_y1, clip_x2, clip_y2 = clip_geometry
            if not _geometry_clip_can_draw(clip_x1, clip_y1, clip_x2, clip_y2):
                return
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
            (
                index
                for index, line in enumerate(lines)
                if line.removesuffix("\r") != ""
            ),
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
            (*_, include_rsb, payload_text) = self._text_frame_line_state(
                index=index,
                line=line,
                lines=lines,
                had_trailing_empty_line=had_trailing_empty_line,
                last_non_empty_line_index=last_non_empty_line_index,
            )
            line_width_px = (
                0.0
                if self.alignment == 1
                else self._measure_tabbed_line_width_px(
                    line,
                    font_name,
                    font_size_for_width,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    include_rsb=include_rsb,
                )
            )
            line_x = self._get_aligned_line_x(
                text_area_x,
                text_area_width,
                line_width_px,
            )
            _, geometry_y = svg_coord_to_geometry(
                line_x,
                current_y - baseline_offset,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            for run_text, run_x in self._managed_tab_runs(
                payload_text,
                line_x,
                font_name,
                font_size_for_width,
                is_bold=is_bold,
                is_italic=is_italic,
            ):
                run_geometry_x, _ = svg_coord_to_geometry(
                    run_x,
                    current_y - baseline_offset,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                self._append_text_frame_string_op(
                    operations,
                    clip_geometry=clip_geometry,
                    geometry_x=run_geometry_x,
                    geometry_y=geometry_y,
                    payload_text=run_text,
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
            make_rounded_rectangle_operation,
            make_pen,
            make_solid_brush,
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

        operations: list[SchGeometryOp] = []
        if self.is_solid:
            fill_color_raw = (
                int(self.area_color) if self.area_color is not None else 0xFFFFFF
            )
            operations.append(
                make_rounded_rectangle_operation(
                    x1_px=frame_x,
                    y1_px=frame_y,
                    x2_px=frame_x + frame_width,
                    y2_px=frame_y + frame_height,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                    source_rotation=ctx.rotation,
                    brush=make_solid_brush(fill_color_raw),
                )
            )
            if not self.show_border:
                # Native TextFrameDrawGraphObject temporarily swaps Color=AreaColor
                # and routes solid/no-border frames through RectangleDrawGraphObject,
                # which emits an area-colored outline in addition to the fill.
                operations.append(
                    make_rounded_rectangle_operation(
                        x1_px=frame_x,
                        y1_px=frame_y,
                        x2_px=frame_x + frame_width,
                        y2_px=frame_y + frame_height,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                        source_rotation=ctx.rotation,
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
                make_rounded_rectangle_operation(
                    x1_px=frame_x,
                    y1_px=frame_y,
                    x2_px=frame_x + frame_width,
                    y2_px=frame_y + frame_height,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                    source_rotation=ctx.rotation,
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
        margin_svg = self._text_margin_record_units * ctx.scale + border_width
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
        unique_id = cast(str, self.unique_id)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=unique_id,
            kind="textframe",
            object_id="eTextFrame",
            bounds=SchGeometryBounds(
                left=int(round(left)),
                top=int(round(top)),
                right=int(round(right)),
                bottom=int(round(bottom)),
            ),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )


# =============================================================================
# Graphical Primitives
# =============================================================================
