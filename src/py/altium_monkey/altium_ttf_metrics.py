"""
TrueType font metrics and measurement helpers.

This module parses font files directly and exposes reusable helpers for width,
height, baseline, and point-to-pixel conversion. Font lookup and host-specific
resolution live in the shared resolver layer rather than in this module.
"""

import logging
import struct
from functools import lru_cache
from pathlib import Path

from .altium_font_resolver import resolve_font_with_style

log = logging.getLogger(__name__)


class TrueTypeFont:
    """
    Parse a font file and expose glyph metrics used by the text helpers.
    """

    def __init__(self, font_path: str | Path) -> None:
        """
        Load and parse a TrueType font file.

        Args:
            font_path: Path to .ttf file

        Raises:
            ValueError: If file is not a valid TrueType font
            FileNotFoundError: If font file doesn't exist
        """
        self.path = Path(font_path)
        self._data = self.path.read_bytes()

        # Parse the font table directory (maps table tags to file offsets)
        self._tables = self._parse_table_directory()

        # Parse required tables in order of dependency
        self.units_per_em = self._parse_head()
        hhea_asc, hhea_desc, hhea_gap, self.num_hmetrics = self._parse_hhea()

        # IMPORTANT: GDI+ uses OS/2 table metrics, not hhea!
        # This is critical for fonts like Calibri where values differ.
        self.cap_height = 0  # Default; overwritten by _parse_os2() if OS/2 v2+
        self.typo_ascender = 0  # Default; overwritten by _parse_os2()
        self.typo_descender = 0  # Default; overwritten by _parse_os2()
        self.use_typo_metrics = False  # Default; overwritten by _parse_os2()
        os2_metrics = self._parse_os2()
        if os2_metrics:
            # Use Windows-compatible metrics (usWinAscent, usWinDescent)
            self.ascender, self.descender, self.line_gap = os2_metrics
            # When USE_TYPO_METRICS is set (bit 7 of fsSelection), GDI+
            # reports sTypoAscender/sTypoDescender instead of
            # usWinAscent/usWinDescent for all cell metrics.  Variable
            # fonts like Bahnschrift set this flag.
            if self.use_typo_metrics and self.typo_ascender > 0:
                self.ascender = self.typo_ascender
                self.descender = self.typo_descender
        else:
            # Fall back to hhea (rare - most fonts have OS/2)
            self.ascender = hhea_asc
            self.descender = abs(hhea_desc)  # Make positive
            self.line_gap = hhea_gap

        # Build character-to-glyph mapping and get advance widths
        self.cmap = self._parse_cmap()
        self.advances, self.lsbs = self._parse_hmtx()

        # Parse glyph bounding boxes for accurate RSB calculation
        self.glyph_widths = self._parse_glyph_bounds()

    def _parse_table_directory(self) -> dict[str, tuple[int, int]]:
        """
        Parse the table directory to get table offsets and lengths.
        """
        # Offset table header
        # uint32 sfntVersion
        # uint16 numTables
        # uint16 searchRange
        # uint16 entrySelector
        # uint16 rangeShift

        sfnt_version = struct.unpack(">I", self._data[0:4])[0]
        num_tables = struct.unpack(">H", self._data[4:6])[0]

        # Check for valid TrueType
        if sfnt_version not in (0x00010000, 0x74727565):  # 1.0 or 'true'
            # Could be OpenType with CFF (0x4F54544F = 'OTTO')
            if sfnt_version == 0x4F54544F:
                pass  # OpenType with PostScript outlines - still works
            else:
                raise ValueError(f"Not a TrueType font: {self.path}")

        tables = {}
        offset = 12  # After header

        for _ in range(num_tables):
            # Table record: tag (4), checksum (4), offset (4), length (4)
            tag = self._data[offset : offset + 4].decode("ascii")
            tbl_offset = struct.unpack(">I", self._data[offset + 8 : offset + 12])[0]
            tbl_length = struct.unpack(">I", self._data[offset + 12 : offset + 16])[0]
            tables[tag] = (tbl_offset, tbl_length)
            offset += 16

        return tables

    def _get_table(self, tag: str) -> bytes:
        """
        Get raw table data by tag.
        """
        if tag not in self._tables:
            raise ValueError(f"Table '{tag}' not found in font")
        offset, length = self._tables[tag]
        return self._data[offset : offset + length]

    def _parse_head(self) -> int:
        """
        Parse 'head' table to get unitsPerEm.
        """
        head = self._get_table("head")
        # unitsPerEm is at offset 18 (uint16)
        units_per_em = struct.unpack(">H", head[18:20])[0]
        return units_per_em

    def _parse_os2(self) -> tuple[int, int, int] | None:
        """
        Parse 'OS/2' table for Windows-compatible metrics.

        GDI+ uses usWinAscent/usWinDescent for text metrics, not hhea values.
        These provide the actual metrics Windows applications see.

        Returns:
            (usWinAscent, usWinDescent, sTypoLineGap) or None if table missing
        """
        if "OS/2" not in self._tables:
            return None

        os2 = self._get_table("OS/2")

        # OS/2 table layout (version 1+):
        # offset 68: sTypoAscender (int16)
        # offset 70: sTypoDescender (int16)
        # offset 72: sTypoLineGap (int16)
        # offset 74: usWinAscent (uint16) - used by GDI+
        # offset 76: usWinDescent (uint16) - used by GDI+
        # offset 86: sxHeight (int16) - OS/2 v2+
        # offset 88: sCapHeight (int16) - OS/2 v2+

        # Need at least 78 bytes for these fields
        if len(os2) < 78:
            return None

        # GDI+ uses usWinAscent/usWinDescent
        win_ascent = struct.unpack(">H", os2[74:76])[0]
        win_descent = struct.unpack(">H", os2[76:78])[0]

        # sTypoAscender/Descender - design intent metrics, used by barcode text
        self.typo_ascender = struct.unpack(">h", os2[68:70])[0]
        self.typo_descender = abs(struct.unpack(">h", os2[70:72])[0])

        # fsSelection (offset 62, uint16) - bit 7 = USE_TYPO_METRICS
        # When set, GDI+/.NET uses sTypoAscender/sTypoDescender instead of
        # usWinAscent/usWinDescent for cell height.  Variable fonts like
        # Bahnschrift set this flag.
        if len(os2) >= 64:
            fs_selection = struct.unpack(">H", os2[62:64])[0]
            self.use_typo_metrics = bool(fs_selection & (1 << 7))

        # Use sTypoLineGap for line spacing
        typo_line_gap = struct.unpack(">h", os2[72:74])[0]

        # Parse sCapHeight (OS/2 v2+, offset 88) for PCB multiline text spacing
        if len(os2) >= 90:
            self.cap_height = struct.unpack(">h", os2[88:90])[0]
        else:
            self.cap_height = 0

        return win_ascent, win_descent, typo_line_gap

    def _parse_hhea(self) -> tuple[int, int, int, int]:
        """
        Parse 'hhea' table to get vertical metrics and numberOfHMetrics.

        Returns:
            (ascender, descender, lineGap, numberOfHMetrics)

        Note:
            ascender/descender are in font design units (typically 2048 for TrueType).
            descender is typically negative.
        """
        hhea = self._get_table("hhea")

        # hhea table layout:
        # offset 0: version (fixed 32-bit)
        # offset 4: ascender (int16) - typographic ascent
        # offset 6: descender (int16) - typographic descent (usually negative)
        # offset 8: lineGap (int16) - typographic line gap
        # ...
        # offset 34: numberOfHMetrics (uint16)

        ascender = struct.unpack(">h", hhea[4:6])[0]  # signed
        descender = struct.unpack(">h", hhea[6:8])[0]  # signed (negative)
        line_gap = struct.unpack(">h", hhea[8:10])[0]  # signed
        num_hmetrics = struct.unpack(">H", hhea[34:36])[0]

        return ascender, descender, line_gap, num_hmetrics

    def _parse_cmap(self) -> dict[int, int]:
        """
        Parse 'cmap' table to build character to glyph ID mapping.

        Focuses on format 4/12 subtables and falls back to the Windows
        Symbol encoding when a Unicode cmap is not present.
        """
        cmap = self._get_table("cmap")

        # cmap header
        num_subtables = struct.unpack(">H", cmap[2:4])[0]

        # Find best subtable.
        #
        # Preference order:
        #   1. Windows Unicode BMP/full Unicode (platform 3, enc 1/10)
        #   2. Unicode platform subtables (platform 0)
        #   3. Windows Symbol (platform 3, enc 0)
        #
        # Windows Symbol fonts such as symbol.ttf often omit a Unicode cmap and
        # instead expose glyphs through the private-use range U+F000..U+F0FF.
        # We normalize that later so ASCII lookups still work for Altium's
        # legacy board text records.
        best_offset = None
        best_platform_id = None
        best_encoding_id = None
        best_rank = None

        offset = 4
        for _ in range(num_subtables):
            platform_id = struct.unpack(">H", cmap[offset : offset + 2])[0]
            encoding_id = struct.unpack(">H", cmap[offset + 2 : offset + 4])[0]
            subtable_offset = struct.unpack(">I", cmap[offset + 4 : offset + 8])[0]

            rank = None
            if platform_id == 3 and encoding_id == 1:
                rank = 0
            elif platform_id == 3 and encoding_id == 10:
                rank = 1
            elif platform_id == 0 and encoding_id in (3, 4):
                rank = 2
            elif platform_id == 0:
                rank = 3
            elif platform_id == 3 and encoding_id == 0:
                rank = 4

            if rank is not None and (best_rank is None or rank < best_rank):
                best_rank = rank
                best_offset = subtable_offset
                best_platform_id = platform_id
                best_encoding_id = encoding_id
                if rank == 0:
                    break

            offset += 8

        if best_offset is None:
            log.warning("No suitable cmap subtable found")
            return {}

        char_to_glyph = self._parse_cmap_subtable(cmap, best_offset)

        if best_platform_id == 3 and best_encoding_id == 0:
            return self._normalize_windows_symbol_cmap(char_to_glyph)

        return char_to_glyph

    @staticmethod
    def _normalize_windows_symbol_cmap(char_to_glyph: dict[int, int]) -> dict[int, int]:
        """
        Expose Windows Symbol cmap entries through their low-byte aliases.

                The Windows Symbol encoding commonly maps printable characters through
                U+F000..U+F0FF rather than the ASCII codepoints carried in Altium text
                records. Mirror those entries onto 0x00..0xFF so metric lookups for
                strings like ``"pl"`` or ``"-1"`` work without special-case callers.
        """
        normalized = dict(char_to_glyph)
        for char_code, glyph_id in list(char_to_glyph.items()):
            if 0xF000 <= char_code <= 0xF0FF and glyph_id:
                normalized.setdefault(char_code & 0x00FF, glyph_id)
        return normalized

    def _parse_cmap_subtable(self, cmap: bytes, offset: int) -> dict[int, int]:
        """
        Parse a cmap subtable (format 4 or 12).
        """
        format_type = struct.unpack(">H", cmap[offset : offset + 2])[0]

        if format_type == 4:
            return self._parse_cmap_format4(cmap, offset)
        elif format_type == 12:
            return self._parse_cmap_format12(cmap, offset)
        else:
            log.warning(f"Unsupported cmap format: {format_type}")
            return {}

    def _parse_cmap_format4(self, cmap: bytes, offset: int) -> dict[int, int]:
        """
        Parse cmap format 4 (segment mapping to delta values).

        This is the most common format for BMP characters.
        """
        # Format 4 header
        # uint16 format (already read)
        # uint16 length
        # uint16 language
        # uint16 segCountX2
        # uint16 searchRange
        # uint16 entrySelector
        # uint16 rangeShift

        seg_count_x2 = struct.unpack(">H", cmap[offset + 6 : offset + 8])[0]
        seg_count = seg_count_x2 // 2

        # Arrays start at offset + 14
        arr_offset = offset + 14

        # endCode[segCount]
        end_codes = []
        for i in range(seg_count):
            end_codes.append(
                struct.unpack(">H", cmap[arr_offset + i * 2 : arr_offset + i * 2 + 2])[
                    0
                ]
            )

        arr_offset += seg_count * 2 + 2  # +2 for reservedPad

        # startCode[segCount]
        start_codes = []
        for i in range(seg_count):
            start_codes.append(
                struct.unpack(">H", cmap[arr_offset + i * 2 : arr_offset + i * 2 + 2])[
                    0
                ]
            )

        arr_offset += seg_count * 2

        # idDelta[segCount]
        id_deltas = []
        for i in range(seg_count):
            id_deltas.append(
                struct.unpack(">h", cmap[arr_offset + i * 2 : arr_offset + i * 2 + 2])[
                    0
                ]
            )  # signed!

        arr_offset += seg_count * 2

        # idRangeOffset[segCount]
        id_range_offsets = []
        id_range_offset_pos = arr_offset  # Save position for glyph index calculation
        for i in range(seg_count):
            id_range_offsets.append(
                struct.unpack(">H", cmap[arr_offset + i * 2 : arr_offset + i * 2 + 2])[
                    0
                ]
            )

        arr_offset += seg_count * 2

        # glyphIdArray follows (variable length)

        # Build mapping
        char_to_glyph = {}

        for seg_idx in range(seg_count):
            start = start_codes[seg_idx]
            end = end_codes[seg_idx]
            delta = id_deltas[seg_idx]
            range_offset = id_range_offsets[seg_idx]

            if start == 0xFFFF:
                continue

            for char_code in range(start, end + 1):
                if range_offset == 0:
                    glyph_id = (char_code + delta) & 0xFFFF
                else:
                    # Complex case: use glyphIdArray
                    glyph_idx_offset = (
                        id_range_offset_pos
                        + seg_idx * 2
                        + range_offset
                        + (char_code - start) * 2
                    )
                    glyph_id = struct.unpack(
                        ">H", cmap[glyph_idx_offset : glyph_idx_offset + 2]
                    )[0]
                    if glyph_id != 0:
                        glyph_id = (glyph_id + delta) & 0xFFFF

                char_to_glyph[char_code] = glyph_id

        return char_to_glyph

    def _parse_cmap_format12(self, cmap: bytes, offset: int) -> dict[int, int]:
        """
        Parse cmap format 12 (segmented coverage for 32-bit characters).
        """
        # Format 12 header
        # uint16 format
        # uint16 reserved
        # uint32 length
        # uint32 language
        # uint32 numGroups

        num_groups = struct.unpack(">I", cmap[offset + 12 : offset + 16])[0]

        char_to_glyph = {}
        arr_offset = offset + 16

        for _ in range(num_groups):
            start_char = struct.unpack(">I", cmap[arr_offset : arr_offset + 4])[0]
            end_char = struct.unpack(">I", cmap[arr_offset + 4 : arr_offset + 8])[0]
            start_glyph = struct.unpack(">I", cmap[arr_offset + 8 : arr_offset + 12])[0]

            for i, char_code in enumerate(range(start_char, end_char + 1)):
                char_to_glyph[char_code] = start_glyph + i

            arr_offset += 12

        return char_to_glyph

    def _parse_hmtx(self) -> tuple[list[int], list[int]]:
        """
        Parse 'hmtx' table to get advance widths and left side bearings.

        Returns:
            (advances, lsbs) - Lists where index is glyph ID
        """
        hmtx = self._get_table("hmtx")

        # hmtx contains:
        # - longHorMetric[numberOfHMetrics] - each is advanceWidth (uint16) + lsb (int16)
        # - leftSideBearing[numGlyphs - numberOfHMetrics] - for remaining glyphs

        advances = []
        lsbs = []

        # Read full metrics (advance + lsb pairs)
        offset = 0
        for _ in range(self.num_hmetrics):
            advance = struct.unpack(">H", hmtx[offset : offset + 2])[0]
            lsb = struct.unpack(">h", hmtx[offset + 2 : offset + 4])[0]  # signed!
            advances.append(advance)
            lsbs.append(lsb)
            offset += 4

        # Remaining glyphs use the last advance width but have their own LSB
        # Read these from maxp table to get total glyph count
        try:
            maxp = self._get_table("maxp")
            num_glyphs = struct.unpack(">H", maxp[4:6])[0]

            # Get last advance width (used for all remaining glyphs)
            last_advance = advances[-1] if advances else 0

            # Read remaining LSBs (each is int16, 2 bytes)
            remaining_glyphs = num_glyphs - self.num_hmetrics
            for _ in range(remaining_glyphs):
                lsb = struct.unpack(">h", hmtx[offset : offset + 2])[0]
                advances.append(last_advance)
                lsbs.append(lsb)
                offset += 2
        except Exception:
            # If maxp table unavailable, we have partial LSB data (fallback in get_rsb())
            pass

        return advances, lsbs

    def _parse_glyph_bounds(self) -> list[int]:
        """
        Parse glyph bounding boxes from loca + glyf tables.

        This is needed for accurate RSB calculation:
            RSB = advance - LSB - glyph_width
            where glyph_width = xMax - xMin

        Returns:
            List of glyph widths (xMax - xMin) indexed by glyph ID
        """
        # Check if required tables exist (some fonts may not have glyf)
        if "loca" not in self._tables or "glyf" not in self._tables:
            log.debug("No loca/glyf tables - using fallback RSB")
            return []

        if "maxp" not in self._tables:
            log.debug("No maxp table - using fallback RSB")
            return []

        try:
            # 1. Get numGlyphs from maxp table
            maxp = self._get_table("maxp")
            num_glyphs = struct.unpack(">H", maxp[4:6])[0]

            # 2. Get loca format from head table (offset 50-51)
            # 0 = short format (offset/2 as uint16)
            # 1 = long format (offset as uint32)
            head = self._get_table("head")
            loc_format = struct.unpack(">h", head[50:52])[0]

            # 3. Parse loca table to get glyph offsets
            loca = self._get_table("loca")
            offsets = []

            for i in range(num_glyphs + 1):  # +1 for end sentinel
                if loc_format == 0:  # Short format: offset/2 stored as uint16
                    off = struct.unpack(">H", loca[i * 2 : i * 2 + 2])[0] * 2
                else:  # Long format: offset stored as uint32
                    off = struct.unpack(">I", loca[i * 4 : i * 4 + 4])[0]
                offsets.append(off)

            # 4. Parse glyf table headers to get bounding boxes
            glyf = self._get_table("glyf")
            glyph_widths = []

            for i in range(num_glyphs):
                start = offsets[i]
                end = offsets[i + 1]

                if start == end:
                    # Empty glyph (e.g., space character)
                    glyph_widths.append(0)
                else:
                    # Glyph header: numberOfContours(2), xMin(2), yMin(2), xMax(2), yMax(2)
                    # We only need xMin and xMax for width calculation
                    xMin = struct.unpack(">h", glyf[start + 2 : start + 4])[0]
                    xMax = struct.unpack(">h", glyf[start + 6 : start + 8])[0]
                    glyph_widths.append(xMax - xMin)

            return glyph_widths

        except Exception as e:
            log.debug("Error parsing glyph bounds: %s", e)
            return []

    def get_advance(self, glyph_id: int) -> int:
        """
        Get advance width for a glyph ID in font design units.
        """
        if glyph_id < len(self.advances):
            return self.advances[glyph_id]
        elif self.advances:
            # Use last advance width for monospace trailing glyphs
            return self.advances[-1]
        return 0

    def get_ascent(self, font_size_px: float) -> float:
        """
        Get font ascent in pixels (height above baseline).

        Args:
            font_size_px: Font size in pixels

        Returns:
            Ascent in pixels
        """
        scale = font_size_px / self.units_per_em
        return self.ascender * scale

    def get_descent(self, font_size_px: float) -> float:
        """
        Get font descent in pixels (height below baseline).

        Args:
            font_size_px: Font size in pixels

        Returns:
            Descent in pixels (positive value)
        """
        scale = font_size_px / self.units_per_em
        # descender is already positive (from OS/2 usWinDescent or abs(hhea))
        return self.descender * scale

    def get_line_height(self, font_size_px: float) -> float:
        """
        Get recommended line height in pixels.

        Args:
            font_size_px: Font size in pixels

        Returns:
            Line height in pixels (ascent + descent + lineGap)
        """
        scale = font_size_px / self.units_per_em
        # ascender and descender are both positive now
        return (self.ascender + self.descender + self.line_gap) * scale

    def get_pcb_line_spacing(self, height_mm: float) -> float:
        """
        Get Altium PCB multiline text line spacing in mm.

        Altium PCB text uses a custom line spacing that does NOT match GDI+
        multiline layout (which gives ratios >= 1.0). The empirical formula is:

            spacing = height_mm * cap_height * 1.2 / cell

        Where:
            - height_mm: Altium text Height field (= cell height in mm)
            - cap_height: OS/2 sCapHeight (design units)
            - cell: usWinAscent + usWinDescent (design units)
            - 1.2: Multiplier (120% of rendered cap height)

        Validated against 55 measured data points (5 fonts x 11 heights)
        from IPC-2581 reference output. Max error: 0.78% (Calibri h=20mil).

        Args:
            height_mm: Altium text Height in mm (cell height, not em size)

        Returns:
            Line spacing in mm (distance between baselines of consecutive lines)
        """
        cell = self.ascender + self.descender
        if cell == 0 or self.cap_height == 0:
            # Fallback: use height itself (ratio 1.0)
            return height_mm
        return height_mm * self.cap_height * 1.2 / cell

    def get_baseline_offset(self, font_size_px: float) -> float:
        """
        Get baseline offset for SVG rendering.

        This is the descent value - used to position text correctly
        when SVG uses the baseline as the Y coordinate.

        Args:
            font_size_px: Font size in pixels

        Returns:
            Baseline offset in pixels
        """
        return self.get_descent(font_size_px)

    def measure_text(
        self, text: str, font_size_px: float, include_rsb: bool = True
    ) -> float:
        """
        Measure text width using the loaded font metrics.
        """
        # Match the measurement path by ignoring trailing whitespace.
        text = text.rstrip()

        if not text:
            return 0.0

        # Scale from design units to pixels.
        scale = font_size_px / self.units_per_em

        # Sum advance widths for all characters.
        total_advance = 0
        for char in text:
            glyph_id = self.cmap.get(ord(char), 0)
            total_advance += self.get_advance(glyph_id)

        width = total_advance * scale

        # Optionally exclude the trailing side bearing.
        if not include_rsb:
            last_glyph_id = self.cmap.get(ord(text[-1]), 0)
            rsb_em = self.get_rsb(last_glyph_id)
            width -= rsb_em * font_size_px

        return width

    def get_factor(self) -> float:
        """
        Return the font-specific point-to-pixel scaling factor.
        """
        return self.units_per_em / (self.ascender + self.descender)

    def _get_raw_advance_sum(self, text: str) -> int:
        """
        Get sum of raw advance widths in design units.

        Args:
            text: String to measure (should already be stripped)

        Returns:
            Total advance width in design units
        """
        total = 0
        for char in text:
            glyph_id = self.cmap.get(ord(char), 0)
            total += self.get_advance(glyph_id)
        return total

    def measure_text_altium(
        self, text: str, font_size_px: float, include_rsb: bool = True
    ) -> float:
        """
        Measure text width using the rendering-oriented scaling path.
        """
        text = text.rstrip()
        if not text:
            return 0.0

        # Use the font-specific scale factor.
        factor = self.get_factor()

        # Measure at a normalized size, then scale back to the target size.
        measure_size = 100.0 * factor

        # Convert the accumulated advances to the normalized measurement size.
        raw_advance = self._get_raw_advance_sum(text)
        width_at_measure_size = raw_advance * (measure_size / self.units_per_em)

        # Optionally exclude the trailing side bearing.
        if not include_rsb:
            last_char = text[-1]
            glyph_id = self.cmap.get(ord(last_char), 0)
            rsb = self.get_rsb(glyph_id)
            rsb_at_measure_size = rsb * measure_size
            width_at_measure_size -= rsb_at_measure_size

        # Convert the target pixel size back to points with the same font factor.
        font_size_pt = (
            font_size_px / factor if factor > 0 else font_size_px * (9.0 / 8.0)
        )

        # Scale from 100pt to target PT size
        return width_at_measure_size * (font_size_pt / 100.0)

    def get_rsb(self, glyph_id: int) -> float:
        """
        Get Right Side Bearing for a glyph in em units (0-1 range).

        RSB = advance_width - LSB - glyph_width
            = advance_width - LSB - (xMax - xMin)

        Args:
            glyph_id: Glyph ID

        Returns:
            RSB in em units (can be negative for some characters like 'A')
        """
        # Check if we have the required data
        if not self.glyph_widths or glyph_id >= len(self.glyph_widths):
            # Fallback to approximation if glyph bounds not available
            return 0.035

        if glyph_id >= len(self.lsbs):
            return 0.035

        # Calculate actual RSB
        advance = self.get_advance(glyph_id)
        lsb = self.lsbs[glyph_id]
        glyph_width = self.glyph_widths[glyph_id]

        # RSB in design units
        rsb_units = advance - lsb - glyph_width

        # Convert to em units (0-1 range)
        return rsb_units / self.units_per_em


# =============================================================================
# Module-Level Convenience Functions
# =============================================================================


# Font cache to avoid re-parsing the same font file multiple times.
# LRU cache with 32 slots should be plenty for typical schematic rendering
# where only a few fonts (Arial, Times New Roman, etc.) are used.
@lru_cache(maxsize=32)
def get_font(font_path: str) -> TrueTypeFont:
    """
    Return a cached ``TrueTypeFont`` instance for a font file.
    """
    return TrueTypeFont(font_path)


def measure_text(text: str, font_path: str, font_size_px: float) -> float:
    """
    Measure text width using a cached font instance.
    """
    font = get_font(font_path)
    return font.measure_text(text, font_size_px)


def measure_text_altium(
    text: str, font_path: str, font_size_px: float, include_rsb: bool = True
) -> float:
    """
    Measure text width with the rendering-oriented scaling path.
    """
    font = get_font(font_path)
    return font.measure_text_altium(text, font_size_px, include_rsb)


def get_font_path(font_name: str) -> str | None:
    """
    Resolve a font family name to an on-disk font file.

    Args:
        font_name: Font family name (e.g., "Arial")

    Returns:
        Full path to .ttf file, or None if not found
    """
    resolution = resolve_font_with_style(font_name)
    return None if resolution.path is None else str(resolution.path)


def get_font_path_with_style(
    font_name: str, bold: bool = False, italic: bool = False
) -> str | None:
    """
    Get the system font path for a font name with style variations.

    Tries to find the exact style match first, then falls back to base font.

    Args:
        font_name: Font family name (e.g., "Arial", "Arial Black")
        bold: Whether to look for bold variant
        italic: Whether to look for italic variant

    Returns:
        Full path to .ttf file, or None if not found
    """
    resolution = resolve_font_with_style(font_name, bold=bold, italic=italic)
    return None if resolution.path is None else str(resolution.path)


# Cache for resolved font factors.
_font_factor_cache: dict[str, float] = {}


def get_font_factor(font_name: str, bold: bool = False, italic: bool = False) -> float:
    """
    Return the point-to-pixel factor for the resolved font.
    """
    font_path = get_font_path_with_style(font_name, bold, italic)
    cache_key = font_path or f"missing|{font_name}|{bold}|{italic}"
    if cache_key in _font_factor_cache:
        return _font_factor_cache[cache_key]

    if not font_path:
        # Fallback when the font cannot be resolved.
        log.debug(f"Font '{font_name}' not found, using default factor 8/9")
        _font_factor_cache[cache_key] = 8.0 / 9.0
        return 8.0 / 9.0

    try:
        # Load the resolved font and compute its factor.
        font = get_font(font_path)
        factor = font.get_factor()
        _font_factor_cache[cache_key] = factor
        return factor
    except Exception as e:
        log.warning(f"Error getting font factor for '{font_name}': {e}")
        _font_factor_cache[cache_key] = 8.0 / 9.0
        return 8.0 / 9.0


def pt_to_px(
    pt_size: float, font_name: str = "Arial", bold: bool = False, italic: bool = False
) -> float:
    """
    Convert a point size to pixels using the resolved font factor.
    """
    factor = get_font_factor(font_name, bold, italic)
    return max(1.0, pt_size * factor)
