"""
Parse PCB text primitive records.
"""

import html
import logging
import struct
from typing import TYPE_CHECKING

from .altium_record_types import PcbGraphicalObject, PcbLayer, PcbRecordType

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext

log = logging.getLogger(__name__)


def _mm_to_svg_x(ctx: "PcbSvgRenderContext", x_mm: float) -> float:
    return ctx.x_to_svg(x_mm / 0.0254)


def _mm_to_svg_y(ctx: "PcbSvgRenderContext", y_mm: float) -> float:
    return ctx.y_to_svg(y_mm / 0.0254)


class AltiumPcbText(PcbGraphicalObject):
    """
    PCB text primitive record.

    Represents a text label on PCB.

    Attributes:
        x: X position (internal units, 10k/mil)
        y: Y position (internal units)
        height: Text height (internal units)
        rotation: Rotation angle (degrees, 0 deg = horizontal)
        stroke_width: Line width for stroke fonts (internal units)
        layer: Layer number (1=TOP, 32=BOTTOM, etc.)
        component_index: Links to component (uint16, 0xFFFF=unlinked)
        is_mirrored: True if text is mirrored
        is_comment: True if this is a component comment/value
        is_designator: True if this is a component designator
        text_content: Actual text string (resolved from WideStrings6/Data)
        widestring_index: Index into WideStrings6 table (uint32)
        stroke_font_type: Font type for stroke fonts (uint16, 1=Default,
            2=Sans Serif, 3=Serif; 0 accepted only as compatibility fallback)
    """

    def __init__(self) -> None:
        super().__init__()
        # --- Shared header (offsets 0-12, same layout as Track/Fill/Arc) ---
        # Offset 0: layer (from base class)
        # Offset 1: flags1 (bit2=~locked, bit3=user_routed)
        self.user_routed: bool = True
        # Offset 2: flags2 (always 0 for text, no keepout mode)
        # Offset 3-4: net_index (from base class, always 0xFFFF for text)
        # Offset 5-6: polygon_index (always 0xFFFF, text never in polygons)
        self.polygon_index: int = 0xFFFF
        # Offset 7-8: component_index (from base class)
        # Offset 9-12: union_index (0xFFFFFFFF = none)
        self.union_index: int = 0xFFFFFFFF

        # --- Geometry fields (offsets 13-41) ---
        self.height: int = 0  # Internal units (10k/mil)
        self.rotation: float = 0.0  # Degrees
        self.stroke_width: int = 0  # Internal units
        self.is_mirrored: bool = False
        self.is_comment: bool = False
        self.is_designator: bool = False

        # --- Font/text fields (offsets 42-118) ---
        self._byte_42: int = 0  # Always 0 in corpus (possibly charset: 0=ANSI)
        self.text_content: str = ""  # Resolved from WideStrings6
        self.widestring_index: int | None = None
        self.stroke_font_type: int = 1
        self.font_type: int = 0  # 0=stroke, 1+=TrueType
        self.is_bold: bool = False
        self.is_italic: bool = False
        self.font_name: str = ""  # TrueType font name (64-byte UTF-16LE)
        self.is_inverted: bool = False
        self.margin_border_width: int = 0  # Internal units

        # --- Fields beyond widestring_index (offsets 119+) ---
        # Offsets 119-122: TEXT-specific union_index (int32)
        # Unlike Track/Fill/Arc where header offset 9-12 holds the real union_index,
        # TEXT always stores 0xFFFFFFFF at offset 9-12 and puts the actual union_index here.
        # Confirmed by 17,476 JSON UNIONINDEX matches across 522K texts in 744 files.
        self.text_union_index: int = 0
        self.use_inverted_rectangle: bool = False
        self.textbox_rect_width: int = 0  # Internal units
        self.textbox_rect_height: int = 0  # Internal units
        self.textbox_rect_justification: int = 3  # LEFT_BOTTOM default
        self.text_offset_width: int = 0  # Internal units (TEXTBORDEROFFSET)
        self.is_frame: bool = False
        self.is_offset_border: bool = (
            False  # Text border spacing mode (0=margin, 1=offset)
        )

        # --- Frame tail block (offsets 230-251) ---
        # 232-239: reserved sentinels (always INT32_MIN)
        # 240: is_justification_valid
        # 241: advance_snapping
        # 242-243: padding (always 0)
        # 244-247: snap_point_x (int32, equals text X when advance_snapping=FALSE)
        # 248-251: snap_point_y (int32, equals text Y when advance_snapping=FALSE)
        self.is_justification_valid: bool = False
        self.advance_snapping: bool = False
        self.snap_point_x: int = -2147483648  # INT32_MIN sentinel = "not set"
        self.snap_point_y: int = -2147483648  # INT32_MIN sentinel = "not set"

        # Barcode block (93 bytes, always present in extended format)
        # Layout confirmed by record format binary layout notes ATEXT6:
        #   bc[0:8]   full_width, full_height (2x int32)
        #   bc[8:16]  x_margin, y_margin (2x int32)
        #   bc[16:20] min_width (int32)
        #   bc[20]    barcode_kind (0=Code39, 1=Code128)
        #   bc[21]    barcode_render_mode (0=ByMinWidth, 1=ByFullWidth)
        #   bc[22]    barcode_inverted (bool)
        #   bc[23]    font_type/TEXTKIND (overwrites offset-43 value: 0=stroke, 1=TT, 2=barcode)
        #   bc[24:88] barcode_font_name (64-byte UTF-16LE)
        #   bc[88]    barcode_show_text (bool)
        #   bc[89:93] layer_v7 (int32, V7 layer ID)
        self.barcode_full_width: int = 0  # Internal units
        self.barcode_full_height: int = 0  # Internal units
        self.barcode_x_margin: int = 0  # Internal units
        self.barcode_y_margin: int = 0  # Internal units
        self.barcode_min_width: int = 0  # Internal units
        self.barcode_kind: int = 0  # 0=Code39, 1=Code128
        self.barcode_render_mode: int = 0  # 0=ByMinWidth, 1=ByFullWidth
        self.barcode_inverted: bool = False
        self.barcode_show_text: bool = False
        self.barcode_font_name: str = ""  # 64-byte UTF-16LE font name
        self.barcode_layer_v7: int = 0  # V7 layer ID at barcode block bc[89:93]

        # Raw byte preservation for non-semantic padding (Altium doesn't zero-pad
        # after null-terminated strings; residual memory remains in file).
        self._font_name_raw: bytes | None = None  # 64 bytes at content[46:110]
        self._barcode_font_name_raw: bytes | None = None  # 64 bytes at bc[24:88]
        self._subrecord2_raw: bytes | None = None  # Original SR2 bytes for round-trip
        self._flags1_raw: int = 0x04  # Full raw flags1 byte (bit2=~locked default)
        self._font_type_offset43: int | None = (
            None  # Original font_type at offset 43 (before bc[23] overwrite)
        )

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB text record discriminator.
        """
        return PcbRecordType.TEXT

    @staticmethod
    def _read_subrecord_payload(data: bytes, cursor: int) -> tuple[bytes, int, int]:
        if len(data) < cursor + 4:
            raise ValueError("Data too short for SubRecord length")
        subrecord_len = struct.unpack("<I", data[cursor : cursor + 4])[0]
        cursor += 4
        if len(data) < cursor + subrecord_len:
            raise ValueError(
                f"SubRecord truncated: expected {subrecord_len} bytes, got {len(data) - cursor}"
            )
        return (
            data[cursor : cursor + subrecord_len],
            cursor + subrecord_len,
            subrecord_len,
        )

    def _parse_shared_header_and_geometry(self, content: bytes) -> int:
        pos = 0
        self.layer = content[pos]
        pos += 1

        flags1 = content[pos]
        self._flags1_raw = flags1
        self.is_locked = (flags1 & 0x04) == 0
        self.is_polygon_outline = (flags1 & 0x02) != 0
        self.user_routed = (flags1 & 0x08) != 0
        pos += 1

        self.is_keepout = (content[pos] & 0x02) != 0
        pos += 1

        net_raw = struct.unpack("<H", content[pos : pos + 2])[0]
        self.net_index = None if net_raw == 0xFFFF else net_raw
        pos += 2

        self.polygon_index = struct.unpack("<H", content[pos : pos + 2])[0]
        pos += 2

        self.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.component_index == 0xFFFF:
            self.component_index = None
        pos += 2

        self.union_index = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        self.x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.y = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        self.height = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4
        self.stroke_font_type = struct.unpack("<H", content[pos : pos + 2])[0]
        pos += 2
        self.rotation = struct.unpack("<d", content[pos : pos + 8])[0]
        pos += 8
        self.is_mirrored = content[pos] != 0
        pos += 1
        self.stroke_width = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4
        self.is_comment = content[pos] != 0
        pos += 1
        self.is_designator = content[pos] != 0
        pos += 1
        return pos

    def _parse_extended_text_fields(self, content: bytes, pos: int) -> int:
        if pos < len(content):
            self._byte_42 = content[pos]
            pos += 1
        if pos < len(content):
            self.font_type = content[pos]
            self._font_type_offset43 = content[pos]
            pos += 1
        if pos < len(content):
            self.is_bold = content[pos] != 0
            pos += 1
        if pos < len(content):
            self.is_italic = content[pos] != 0
            pos += 1
        if pos + 64 <= len(content):
            font_name_bytes = content[pos : pos + 64]
            self._font_name_raw = bytes(font_name_bytes)
            try:
                self.font_name = font_name_bytes.decode("utf-16-le").split("\x00")[0]
            except Exception:
                self.font_name = ""
            pos += 64
        else:
            pos = len(content)

        if pos < len(content):
            self.is_inverted = content[pos] != 0
            pos += 1
        if pos + 4 <= len(content):
            self.margin_border_width = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4
        if pos + 4 <= len(content):
            self.widestring_index = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4
        if pos + 4 <= len(content):
            self.text_union_index = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
        if pos < len(content):
            self.use_inverted_rectangle = content[pos] != 0
            pos += 1
        if pos + 4 <= len(content):
            self.textbox_rect_width = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
        if pos + 4 <= len(content):
            self.textbox_rect_height = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
        if pos < len(content):
            self.textbox_rect_justification = content[pos]
            pos += 1
        if pos + 4 <= len(content):
            self.text_offset_width = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
        return pos

    def _parse_barcode_block_if_present(self, content: bytes, pos: int) -> int:
        if len(content) - pos < 93:
            return pos

        bc = content[pos : pos + 93]
        self.barcode_full_width = struct.unpack("<i", bc[0:4])[0]
        self.barcode_full_height = struct.unpack("<i", bc[4:8])[0]
        self.barcode_x_margin = struct.unpack("<i", bc[8:12])[0]
        self.barcode_y_margin = struct.unpack("<i", bc[12:16])[0]
        self.barcode_min_width = struct.unpack("<i", bc[16:20])[0]
        self.barcode_kind = bc[20]
        self.barcode_render_mode = bc[21]
        self.barcode_inverted = bc[22] != 0
        self.font_type = bc[23]
        try:
            self.barcode_font_name = bc[24:88].decode("utf-16-le").split("\x00")[0]
        except Exception:
            self.barcode_font_name = ""
        self.barcode_show_text = bc[88] != 0
        self.barcode_layer_v7 = struct.unpack("<i", bc[89:93])[0]
        self._barcode_font_name_raw = bytes(bc[24:88])
        return pos + 93

    def _parse_tail_fields(self, content: bytes, pos: int) -> None:
        remaining = len(content) - pos
        if remaining >= 2:
            self.is_frame = content[pos] != 0
            pos += 1
            self.is_offset_border = content[pos] != 0
            pos += 1
        else:
            self.is_frame = (
                self.textbox_rect_height != 0 and self.textbox_rect_width != 0
            )

        if len(content) - pos < 20:
            return

        pos += 8
        self.is_justification_valid = content[pos] != 0
        pos += 1
        self.advance_snapping = content[pos] != 0
        pos += 1
        pos += 2
        self.snap_point_x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.snap_point_y = struct.unpack("<i", content[pos : pos + 4])[0]

    def _parse_subrecord2(self, data: bytes, cursor: int) -> int:
        self._subrecord2_pascal = False
        if cursor + 4 > len(data):
            return cursor

        subrecord2_len = struct.unpack("<I", data[cursor : cursor + 4])[0]
        cursor += 4
        if cursor + subrecord2_len > len(data):
            return cursor + subrecord2_len

        subrecord2_content = data[cursor : cursor + subrecord2_len]
        self._subrecord2_raw = bytes(subrecord2_content)
        cursor += subrecord2_len
        if subrecord2_len >= 1 and subrecord2_content[0] == subrecord2_len - 1:
            self._subrecord2_pascal = True
            text_bytes = subrecord2_content[1:]
        else:
            text_bytes = subrecord2_content
        if not self.text_content and text_bytes:
            self.text_content = text_bytes.decode("ascii", errors="replace").rstrip(
                "\x00"
            )
        return cursor

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse TEXT record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short

        Note:
            This parses the binary structure but does NOT resolve text_content.
            Call resolve_text_content() after parsing with the WideStrings6 table.
        """
        if len(data) < offset + 1:
            raise ValueError("Data too short for TEXT record")

        cursor = offset
        type_byte = data[cursor]
        if type_byte != PcbRecordType.TEXT:
            raise ValueError(
                f"Invalid TEXT type byte: 0x{type_byte:02X} "
                f"(expected 0x{int(PcbRecordType.TEXT):02X})"
            )
        cursor += 1

        content, cursor, subrecord_len = self._read_subrecord_payload(data, cursor)
        self._original_sr1_len: int = subrecord_len
        self._raw_sr1_content: bytes = bytes(content)

        if len(content) < 42:
            log.warning(
                f"TEXT SubRecord shorter than expected: {len(content)} bytes (expected >=42)"
            )

        pos = self._parse_shared_header_and_geometry(content)
        if subrecord_len < 123:
            self._raw_binary = data[offset:cursor]
            self._raw_binary_signature = self._state_signature()
            return cursor - offset

        pos = self._parse_extended_text_fields(content, pos)
        pos = self._parse_barcode_block_if_present(content, pos)
        self._parse_tail_fields(content, pos)
        cursor = self._parse_subrecord2(data, cursor)

        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()
        return cursor - offset

    def resolve_text_content(self, widestrings_table: dict[int, str]) -> None:
        """
        Resolve text content from WideStrings6 table.

        Args:
            widestrings_table: Dict mapping index -> string
        """
        if (
            self.widestring_index is not None
            and self.widestring_index in widestrings_table
        ):
            self.text_content = widestrings_table[self.widestring_index]

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of semantically known TEXT fields.
        """
        return (
            int(self.layer),
            bool(self.is_locked),
            bool(self.user_routed),
            int(self._flags1_raw),
            bool(self.is_keepout),
            int(self.net_index) if self.net_index is not None else 0xFFFF,
            int(self.polygon_index),
            int(self.component_index) if self.component_index is not None else 0xFFFF,
            int(self.union_index),
            int(self.x),
            int(self.y),
            int(self.height),
            int(self.stroke_font_type),
            float(self.rotation),
            bool(self.is_mirrored),
            int(self.stroke_width),
            bool(self.is_comment),
            bool(self.is_designator),
            int(self._byte_42),
            int(self.font_type),
            bool(self.is_bold),
            bool(self.is_italic),
            str(self.font_name),
            bool(self.is_inverted),
            int(self.margin_border_width),
            int(self.widestring_index) if self.widestring_index is not None else None,
            int(self.text_union_index),
            str(self.text_content),
            bool(self.use_inverted_rectangle),
            int(self.textbox_rect_width),
            int(self.textbox_rect_height),
            int(self.textbox_rect_justification),
            int(self.text_offset_width),
            bool(self.is_frame),
            bool(self.is_offset_border),
            int(self.snap_point_x),
            int(self.snap_point_y),
            bool(self.is_justification_valid),
            bool(self.advance_snapping),
            int(self.barcode_full_width),
            int(self.barcode_full_height),
            int(self.barcode_x_margin),
            int(self.barcode_y_margin),
            int(self.barcode_min_width),
            int(self.barcode_kind),
            int(self.barcode_render_mode),
            bool(self.barcode_inverted),
            str(self.barcode_font_name),
            bool(self.barcode_show_text),
            int(self.barcode_layer_v7),
            self._subrecord2_raw,
            self._font_type_offset43,
        )

    @staticmethod
    def _encode_utf16le_fixed(text: str, size_bytes: int) -> bytes:
        """
        Encode a UTF-16LE null-terminated string into a fixed-size field.
        """
        raw = (text or "").encode("utf-16-le")
        truncated = raw[: max(0, size_bytes - 2)]
        # Keep UTF-16 code unit alignment when truncating.
        if len(truncated) % 2 != 0:
            truncated = truncated[:-1]
        padded = truncated + b"\x00\x00"
        if len(padded) < size_bytes:
            padded += b"\x00" * (size_bytes - len(padded))
        return padded[:size_bytes]

    @staticmethod
    def _encode_font_name_with_padding(
        text: str, raw_original: bytes | None, size_bytes: int = 64
    ) -> bytes:
        """
        Encode font name preserving original padding bytes after null terminator.

                Altium does not zero-pad font name fields - residual memory remains after
                the null-terminated string.  To achieve byte-identical round-trip, start
                from the original raw bytes and overlay the new string portion only.

                When text is empty and raw_original is available, return raw_original
                unchanged - the field is semantically unused (e.g. stroke font) and
                the raw bytes may contain residual data that must be preserved.
        """
        if not text and raw_original is not None and len(raw_original) == size_bytes:
            return bytes(raw_original)

        encoded = (text or "").encode("utf-16-le")
        # Truncate to fit within field (leave room for null terminator)
        if len(encoded) > size_bytes - 2:
            encoded = encoded[: size_bytes - 2]
            if len(encoded) % 2 != 0:
                encoded = encoded[:-1]

        if raw_original is not None and len(raw_original) == size_bytes:
            result = bytearray(raw_original)
        else:
            result = bytearray(size_bytes)
        # Write encoded string + null terminator
        result[: len(encoded)] = encoded
        result[len(encoded) : len(encoded) + 2] = b"\x00\x00"
        return bytes(result[:size_bytes])

    def _initialize_serialization_content(self, target_len: int) -> bytearray:
        raw_sr1 = getattr(self, "_raw_sr1_content", None)
        if raw_sr1 is not None and len(raw_sr1) == target_len:
            return bytearray(raw_sr1)

        content = bytearray(target_len)
        raw = self._raw_binary
        if raw is None or len(raw) < 5 or raw[0] != 0x05:
            return content

        try:
            subrecord_len = struct.unpack("<I", raw[1:5])[0]
            if subrecord_len >= target_len and len(raw) >= 5 + subrecord_len:
                content[:] = raw[5 : 5 + target_len]
        except Exception:
            pass
        return content

    def _serialize_shared_header(self, content: bytearray) -> None:
        content[0] = max(0, min(255, int(self.layer)))
        flags1 = self._flags1_raw & ~0x0E
        if self.is_polygon_outline:
            flags1 |= 0x02
        if not self.is_locked:
            flags1 |= 0x04
        if self.user_routed:
            flags1 |= 0x08
        content[1] = flags1
        content[2] = 0x02 if self.is_keepout else 0x00
        net_val = (
            0xFFFF
            if self.net_index is None
            else max(0, min(0xFFFF, int(self.net_index)))
        )
        struct.pack_into("<H", content, 3, net_val)
        struct.pack_into("<H", content, 5, max(0, min(0xFFFF, int(self.polygon_index))))
        struct.pack_into(
            "<H",
            content,
            7,
            0xFFFF
            if self.component_index is None
            else max(0, min(0xFFFF, int(self.component_index))),
        )
        struct.pack_into("<I", content, 9, int(self.union_index) & 0xFFFFFFFF)

    def _serialize_geometry_block(self, content: bytearray) -> None:
        struct.pack_into("<i", content, 13, int(self.x))
        struct.pack_into("<i", content, 17, int(self.y))
        struct.pack_into("<I", content, 21, max(0, int(self.height)))
        struct.pack_into(
            "<H", content, 25, max(0, min(0xFFFF, int(self.stroke_font_type)))
        )
        struct.pack_into("<d", content, 27, float(self.rotation))
        content[35] = 1 if self.is_mirrored else 0
        struct.pack_into("<I", content, 36, max(0, int(self.stroke_width)))
        content[40] = 1 if self.is_comment else 0
        content[41] = 1 if self.is_designator else 0

    def _serialize_text_fields(self, content: bytearray) -> None:
        content[42] = max(0, min(255, int(self._byte_42)))
        font_type_43 = (
            self._font_type_offset43
            if self._font_type_offset43 is not None
            else self.font_type
        )
        content[43] = max(0, min(255, int(font_type_43)))
        content[44] = 1 if self.is_bold else 0
        content[45] = 1 if self.is_italic else 0
        content[46:110] = self._encode_font_name_with_padding(
            self.font_name, self._font_name_raw
        )
        content[110] = 1 if self.is_inverted else 0
        struct.pack_into("<I", content, 111, max(0, int(self.margin_border_width)))
        widestring_index = (
            0 if self.widestring_index is None else max(0, int(self.widestring_index))
        )
        struct.pack_into("<I", content, 115, widestring_index)
        struct.pack_into("<i", content, 119, int(self.text_union_index))
        content[123] = 1 if self.use_inverted_rectangle else 0
        struct.pack_into("<i", content, 124, int(self.textbox_rect_width))
        struct.pack_into("<i", content, 128, int(self.textbox_rect_height))
        content[132] = max(0, min(255, int(self.textbox_rect_justification)))
        struct.pack_into("<i", content, 133, int(self.text_offset_width))

    def _serialize_barcode_block(self, content: bytearray, target_len: int) -> None:
        if target_len < 230:
            return
        struct.pack_into("<i", content, 137, int(self.barcode_full_width))
        struct.pack_into("<i", content, 141, int(self.barcode_full_height))
        struct.pack_into("<i", content, 145, int(self.barcode_x_margin))
        struct.pack_into("<i", content, 149, int(self.barcode_y_margin))
        struct.pack_into("<i", content, 153, int(self.barcode_min_width))
        content[157] = max(0, min(255, int(self.barcode_kind)))
        content[158] = max(0, min(255, int(self.barcode_render_mode)))
        content[159] = 1 if self.barcode_inverted else 0
        content[160] = max(0, min(255, int(self.font_type)))
        content[161:225] = self._encode_font_name_with_padding(
            self.barcode_font_name, self._barcode_font_name_raw
        )
        content[225] = 1 if self.barcode_show_text else 0
        struct.pack_into("<i", content, 226, int(self.barcode_layer_v7))

    def _serialize_tail_blocks(self, content: bytearray, target_len: int) -> None:
        if target_len >= 232:
            content[230] = 1 if self.is_frame else 0
            content[231] = 1 if self.is_offset_border else 0
        if target_len >= 240:
            if content[232:236] == b"\x00\x00\x00\x00":
                struct.pack_into("<i", content, 232, -2147483648)
            if content[236:240] == b"\x00\x00\x00\x00":
                struct.pack_into("<i", content, 236, -2147483648)
        if target_len <= 240:
            return
        content[240] = 1 if self.is_justification_valid else 0
        content[241] = 1 if self.advance_snapping else 0
        struct.pack_into("<i", content, 244, int(self.snap_point_x))
        struct.pack_into("<i", content, 248, int(self.snap_point_y))

    def _build_subrecord2_bytes(self) -> bytes:
        if self._subrecord2_raw is not None:
            return self._subrecord2_raw

        subrecord2_text = self.text_content or ""
        if getattr(self, "_subrecord2_pascal", False):
            str_bytes = subrecord2_text.encode("ascii", errors="replace")
            return bytes([len(str_bytes)]) + str_bytes
        return subrecord2_text.encode("utf-8", errors="replace") + b"\x00"

    def serialize_to_binary(self) -> bytes:
        """
        Serialize TEXT record to binary format.

        Returns:
            Binary data ready to write to stream

        Strategy:
            Reuse raw binary only when semantic fields are unchanged.
            Otherwise serialize deterministic extended-format content.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        target_len = getattr(self, "_original_sr1_len", 252)
        content = self._initialize_serialization_content(target_len)
        self._serialize_shared_header(content)
        self._serialize_geometry_block(content)
        self._serialize_text_fields(content)
        self._serialize_barcode_block(content, target_len)
        self._serialize_tail_blocks(content, target_len)
        subrecord2_bytes = self._build_subrecord2_bytes()

        record = bytearray()
        record.append(0x05)
        record.extend(struct.pack("<I", len(content)))
        record.extend(content)
        record.extend(struct.pack("<I", len(subrecord2_bytes)))
        record.extend(subrecord2_bytes)

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    @property
    def height_mils(self) -> float:
        """
        Get text height in mils.
        """
        return self._from_internal_units(self.height)

    @property
    def stroke_width_mils(self) -> float:
        """
        Get stroke width in mils.
        """
        return self._from_internal_units(self.stroke_width)

    @property
    def effective_justification(self) -> int:
        """
        Get effective justification for rendering.

                When is_justification_valid is False, the binary stores 5 (CENTER_CENTER)
                which is wrong - use LEFT_BOTTOM (3) as fallback per record format source.
        """
        if self.is_justification_valid:
            return self.textbox_rect_justification
        return 3  # LEFT_BOTTOM

    @property
    def textbox_rect_width_mils(self) -> float:
        """
        Get textbox rect width in mils.
        """
        return self._from_internal_units(self.textbox_rect_width)

    @property
    def textbox_rect_height_mils(self) -> float:
        """
        Get textbox rect height in mils.
        """
        return self._from_internal_units(self.textbox_rect_height)

    @property
    def barcode_full_width_mils(self) -> float:
        """
        Get barcode full width in mils.
        """
        return self._from_internal_units(self.barcode_full_width)

    @property
    def barcode_full_height_mils(self) -> float:
        """
        Get barcode full height in mils.
        """
        return self._from_internal_units(self.barcode_full_height)

    @property
    def barcode_min_width_mils(self) -> float:
        """
        Get barcode minimum bar width in mils.
        """
        return self._from_internal_units(self.barcode_min_width)

    def _resolve_svg_color(
        self,
        ctx: "PcbSvgRenderContext",
        stroke: str | None,
    ) -> str:
        if stroke is not None:
            return stroke
        try:
            return ctx.layer_color(PcbLayer(int(self.layer)))
        except ValueError:
            return "#808080"

    def _svg_metadata_attrs(
        self,
        ctx: "PcbSvgRenderContext",
        *,
        include_metadata: bool,
    ) -> list[str]:
        if not include_metadata:
            return []

        meta_attrs = [
            'data-primitive="text"',
            f'data-font-type="{self.font_type}"',
        ]
        meta_attrs.extend(ctx.layer_metadata_attrs(int(self.layer)))
        meta_attrs.extend(
            ctx.relationship_metadata_attrs(
                component_index=self.component_index,
            )
        )
        if self.is_designator:
            meta_attrs.append('data-text-role="designator"')
        elif self.is_comment:
            meta_attrs.append('data-text-role="comment"')
        else:
            meta_attrs.append('data-text-role="free"')
        return meta_attrs

    def _render_polygon_text_result(
        self,
        result: object,
        *,
        ctx: "PcbSvgRenderContext",
        color: str,
        meta_attrs: list[str],
    ) -> list[str]:
        from .altium_text_to_polygon import TextPolygonResult

        if not isinstance(result, TextPolygonResult):
            return []

        def _append_contour(
            parts: list[str],
            contour: list[tuple[float, float]],
        ) -> bool:
            points = contour
            if len(points) >= 2 and points[0] == points[-1]:
                points = points[:-1]
            if len(points) < 3:
                return False
            first = points[0]
            parts.append(
                f"M {ctx.fmt(_mm_to_svg_x(ctx, first[0]))} {ctx.fmt(_mm_to_svg_y(ctx, first[1]))}"
            )
            for px, py in points[1:]:
                parts.append(
                    f"L {ctx.fmt(_mm_to_svg_x(ctx, px))} {ctx.fmt(_mm_to_svg_y(ctx, py))}"
                )
            parts.append("Z")
            return True

        if self.is_inverted:
            parts: list[str] = []
            for char_polys in result.characters:
                for poly in char_polys:
                    _append_contour(parts, poly.outline)
                    for hole in poly.holes:
                        _append_contour(parts, hole)
            if parts:
                attrs = [
                    f'd="{" ".join(parts)}"',
                    f'fill="{html.escape(color)}"',
                    'fill-rule="evenodd"',
                    'stroke="none"',
                ]
                attrs.extend(meta_attrs)
                return [f"<path {' '.join(attrs)}/>"]

        elements: list[str] = []
        for char_polys in result.characters:
            for poly in char_polys:
                if not poly.outline:
                    continue
                parts: list[str] = []
                if not _append_contour(parts, poly.outline):
                    continue
                for hole in poly.holes:
                    _append_contour(parts, hole)
                attrs = [
                    f'd="{" ".join(parts)}"',
                    f'fill="{html.escape(color)}"',
                    'fill-rule="evenodd"',
                    'stroke="none"',
                ]
                attrs.extend(meta_attrs)
                elements.append(f"<path {' '.join(attrs)}/>")
        return elements

    def _render_stroke_text_result(
        self,
        result: object,
        *,
        ctx: "PcbSvgRenderContext",
        color: str,
        meta_attrs: list[str],
    ) -> list[str]:
        from .altium_text_to_polygon import StrokeTextResult

        if not isinstance(result, StrokeTextResult):
            return []

        elements = []
        stroke_width = max(float(result.stroke_width_mm), 0.001)
        for x1_mm, y1_mm, x2_mm, y2_mm in result.lines:
            attrs = [
                f'x1="{ctx.fmt(_mm_to_svg_x(ctx, x1_mm))}"',
                f'y1="{ctx.fmt(_mm_to_svg_y(ctx, y1_mm))}"',
                f'x2="{ctx.fmt(_mm_to_svg_x(ctx, x2_mm))}"',
                f'y2="{ctx.fmt(_mm_to_svg_y(ctx, y2_mm))}"',
                f'stroke="{html.escape(color)}"',
                f'stroke-width="{ctx.fmt(stroke_width)}"',
                'stroke-linecap="round"',
                'fill="none"',
            ]
            attrs.extend(meta_attrs)
            elements.append(f"<line {' '.join(attrs)}/>")
        return elements

    def _fallback_text_geometry_box(
        self,
        *,
        ctx: "PcbSvgRenderContext",
        color: str,
        resolved_text: str,
        meta_attrs: list[str],
    ) -> list[str]:
        x = ctx.x_to_svg(self.x_mils)
        y = ctx.y_to_svg(self.y_mils)
        height_mm = max(self.height_mils * 0.0254, 0.05)
        width_mm = max(len(resolved_text) * height_mm * 0.6, height_mm * 0.5)
        y_top = y - height_mm * 0.8
        y_bottom = y + height_mm * 0.2
        path_d = (
            f"M {ctx.fmt(x)} {ctx.fmt(y_top)} "
            f"L {ctx.fmt(x + width_mm)} {ctx.fmt(y_top)} "
            f"L {ctx.fmt(x + width_mm)} {ctx.fmt(y_bottom)} "
            f"L {ctx.fmt(x)} {ctx.fmt(y_bottom)} Z"
        )
        attrs = [
            f'd="{path_d}"',
            'fill="none"',
            f'stroke="{html.escape(color)}"',
            f'stroke-width="{ctx.fmt(max(height_mm * 0.05, 0.02))}"',
            'stroke-dasharray="0.2 0.1"',
        ]
        attrs.extend(meta_attrs)
        attrs.append('data-text-fallback="geometry-box"')
        return [f"<path {' '.join(attrs)}/>"]

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | None = None,
        text_as_polygons: bool = True,
        truetype_renderer: object | None = None,
        stroke_renderer: object | None = None,
        barcode_renderer: object | None = None,
        font_resolver: object | None = None,
    ) -> list[str]:
        """
        Render PCB text as geometry (polygon contours or stroke segments).
        """
        if ctx is None:
            return []
        if for_layer is not None and int(self.layer) != for_layer.value:
            return []
        raw_text = self.text_content or ""
        resolved_text = (
            ctx.substitute_special_strings(raw_text)
            if hasattr(ctx, "substitute_special_strings")
            else raw_text
        )
        if not resolved_text or not resolved_text.strip():
            return []
        allow_fallback = bool(
            getattr(
                getattr(ctx, "options", None), "allow_text_geometry_fallback", False
            )
        )
        color = self._resolve_svg_color(ctx, stroke)
        meta_attrs = self._svg_metadata_attrs(ctx, include_metadata=include_metadata)

        try:
            from .altium_text_to_polygon import (
                render_pcb_text,
            )

            result = render_pcb_text(
                self,
                truetype_renderer=truetype_renderer,
                stroke_renderer=stroke_renderer,
                barcode_renderer=barcode_renderer,
                font_resolver=font_resolver,
                text_override=resolved_text,
            )
            if result is not None:
                polygon_elements = self._render_polygon_text_result(
                    result,
                    ctx=ctx,
                    color=color,
                    meta_attrs=meta_attrs,
                )
                if polygon_elements:
                    return polygon_elements
                stroke_elements = self._render_stroke_text_result(
                    result,
                    ctx=ctx,
                    color=color,
                    meta_attrs=meta_attrs,
                )
                if stroke_elements:
                    return stroke_elements
        except Exception as exc:
            if not allow_fallback:
                raise RuntimeError(
                    f"Failed to render PCB text geometry for layer {self.layer} text={resolved_text[:48]!r}"
                ) from exc
            log.exception(
                "Failed to render text geometry for layer %s text=%r; using fallback box",
                self.layer,
                resolved_text[:48],
            )

        if not allow_fallback:
            raise RuntimeError(
                f"PCB text geometry renderer produced no output for layer {self.layer} text={resolved_text[:48]!r}"
            )

        return self._fallback_text_geometry_box(
            ctx=ctx,
            color=color,
            resolved_text=resolved_text,
            meta_attrs=meta_attrs,
        )

    def __repr__(self) -> str:
        text_preview = (
            self.text_content[:20] + "..."
            if len(self.text_content) > 20
            else self.text_content
        )
        return (
            f"<AltiumPcbText layer={self.layer} "
            f"pos=({self.x_mils:.2f}, {self.y_mils:.2f}) "
            f"height={self.height_mils:.2f}mil "
            f"text='{text_preview}'>"
        )
