"""
Parse PCB pad primitive records.
"""

import html
import logging
import math
import struct
from typing import TYPE_CHECKING

from .altium_pcb_enums import PadShape
from .altium_pcb_mask_paste_rules import (
    get_pad_mask_expansion_iu,
    get_pad_paste_expansion_iu,
    has_pad_paste_opening,
    is_pad_solder_mask_only,
)
from .altium_record_types import PcbGraphicalObject, PcbLayer, PcbRecordType

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext


_MIL_TO_MM = 0.0254

log = logging.getLogger(__name__)


# SubRecord 6 alt_shape values that override the base shape from SubRecord 5.
# Binary format stores a combined shape+subkind encoding; value 9 = RoundedRectangle.
_ALT_SHAPE_OVERRIDE = {
    9: PadShape.ROUNDED_RECTANGLE,
}


class AltiumPcbPad(PcbGraphicalObject):
    """
    PCB pad primitive record.

    Represents a component pad on PCB.

    Attributes:
        designator: Pad number/name (e.g., "1", "2", "GND")
        x: X position (internal units, 10k/mil)
        y: Y position (internal units)
        width: Pad width on top layer (internal units)
        height: Pad height on top layer (internal units)
        hole_size: Drill hole diameter (0 for SMT)
        shape: Pad shape (1=CIRCLE, 2=RECT, 3=OCTAGONAL)
        rotation: Rotation angle (degrees)
        layer: Layer (74=MULTI_LAYER for through-hole, 1=TOP, 32=BOTTOM for SMT)
        component_index: Links to component (uint16, 0xFFFF=unlinked)
        net_index: Links to net (uint16, 0xFFFF=unlinked)
        is_plated: True if through-hole is plated
        top_width: Top layer width (internal units)
        top_height: Top layer height (internal units)
        mid_width: Mid layer width (internal units)
        mid_height: Mid layer height (internal units)
        bot_width: Bottom layer width (internal units)
        bot_height: Bottom layer height (internal units)
        top_shape: Top layer shape
        mid_shape: Mid layer shape
        bot_shape: Bottom layer shape
    """

    def __init__(self) -> None:
        super().__init__()
        self.designator: str = ""
        self.height: int = 0  # Internal units
        self.hole_size: int = 0  # Internal units (0 = SMT)
        self.shape: int = PadShape.CIRCLE
        self.rotation: float = 0.0  # Degrees
        self.is_plated: bool = False

        # Layer-specific geometry
        self.top_width: int = 0
        self.top_height: int = 0
        self.mid_width: int = 0
        self.mid_height: int = 0
        self.bot_width: int = 0
        self.bot_height: int = 0
        self.top_shape: int = PadShape.CIRCLE
        self.mid_shape: int = PadShape.CIRCLE
        self.bot_shape: int = PadShape.CIRCLE

        # Decoded flag booleans (from _flags uint16 at SubRecord 5 offset 1-2)
        self.is_tenting_top: bool = False
        self.is_tenting_bottom: bool = False
        self.is_test_fab_top: bool = False
        self.is_test_fab_bottom: bool = False
        self.is_assy_test_point_top: bool = False
        self.is_assy_test_point_bottom: bool = False
        self.pad_mode: int = 0  # 0=simple, 1=top-mid-bot, 2=full stack

        # Mask expansion (SubRecord 5 extended data, offset 85+)
        # Mode: 0=none, 1=rule, 2=manual (record format ALTIUM_MODE enum)
        self.pastemask_expansion_mode: int = 0
        self.soldermask_expansion_mode: int = 0
        self.pastemask_expansion_manual: int = 0  # Internal units (10k/mil)
        self.soldermask_expansion_manual: int = 0  # Internal units (10k/mil)
        self._has_mask_expansion: bool = False  # True if SubRecord 5 was long enough

        # Pad cache (SubRecord 5 offsets 63-85, shared with Via)
        # PlaneConnectionStyle: 0=NoConnect, 1=Relief, 2=DirectConnect
        self.plane_connection_style: int = 1  # Default: Relief
        self.cache_relief_conductor_width: int = 0
        self.cache_relief_entries: int = 4
        self.cache_relief_air_gap: int = 0
        self.cache_power_plane_relief_expansion: int = 0
        self.cache_power_plane_clearance: int = 0
        self._has_pad_cache: bool = False

        # SubRecord 6: SizeAndShapeByLayer (per-layer pad variations)
        # Hole shape: 0=Round, 1=Square, 2=Slot
        self.hole_shape: int = 0
        self.slot_size: int = 0  # Internal units (for slot holes)
        self.slot_rotation: float = 0.0  # Degrees (for slot holes)
        # Per-layer inner sizes (29 inner copper layers)
        self.inner_size_x: list[int] = []  # 29 entries, internal units
        self.inner_size_y: list[int] = []  # 29 entries, internal units
        self.inner_shape: list[int] = []  # 29 entries (PadShape enum)
        # Per-layer pad-center offsets (32 physical signal layers). The binary
        # field names are historically exposed as hole_offset_* in this code.
        self.hole_offset_x: list[int] = []  # 32 entries, internal units
        self.hole_offset_y: list[int] = []  # 32 entries, internal units
        # Per-layer alternative shapes and corner radii (32 layers)
        self.alt_shape: list[int] = []  # 32 entries (alt shape enum, 9=RoundRect)
        self.corner_radius: list[int] = []  # 32 entries (percentage, 0-100)
        # Optional extended full-stack entries (SubRecord 6 tail block, when present).
        # Tuple format: (layer_code, mode_flags, enabled, size_x_iu, size_y_iu, corner_pct)
        self.full_stack_layer_entries: list[tuple[int, int, int, int, int, int]] = []
        # Semantic custom-pad model attached by higher-level document/library parsers.
        self.custom_shape = None

        # Cache for unknown SubRecord data (for round-trip compatibility)
        self._subrecord2_data: bytes = b""
        self._subrecord3_data: bytes = b""
        self._subrecord4_data: bytes = b""
        self._subrecord5_extended_data: bytes = b""  # Data after offset 61
        self._subrecord5_extended_signature: tuple | None = None
        self._subrecord6_data: bytes = b""  # Raw SubRecord 6 for round-trip
        self._subrecord6_signature: tuple | None = None

        # Shared header fields (same layout as Track/Fill/Arc)
        self._flags: int = 0
        self.polygon_index: int = 0xFFFF  # Offset 5-6: always 0xFFFF for pads
        self.union_index: int = 0xFFFFFFFF  # Offset 9-12: 0xFFFFFFFF=none
        self.user_routed: bool = True  # flags1 bit3 (0x08)
        self._flags1_bit0: int = 0  # flags1 bit0 (preserved raw)

        # Cache validity/state bytes (offsets 94-100, 103-104)
        self._cache_byte_94: int = (
            0  # Offset 94: semantic uncertain (99.3% zero, also 249/207/255)
        )
        self._cache_padding_95: int = 0  # Offset 95: always 0
        self.cache_plane_connection_valid: int = 0  # Offset 96
        self.cache_relief_conductor_width_valid: int = 0  # Offset 97
        self.cache_relief_entries_valid: int = 0  # Offset 98
        self.cache_relief_air_gap_valid: int = 0  # Offset 99
        self.cache_power_plane_relief_expansion_valid: int = 0  # Offset 100
        self.cache_paste_mask_expansion_valid: int = 0  # Offset 103
        self.cache_solder_mask_expansion_valid: int = 0  # Offset 104
        self.layer_v7_save_id: int | None = (
            None  # Offset 114 in extended SubRecord 5 tail
        )

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB pad record discriminator.
        """
        return PcbRecordType.PAD

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse PAD record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short
        """
        if len(data) < offset + 1:
            raise ValueError("Data too short for PAD record")

        cursor = offset

        # Verify type byte
        type_byte = data[cursor]
        if type_byte != PcbRecordType.PAD:
            raise ValueError(
                f"Invalid PAD type byte: 0x{type_byte:02X} "
                f"(expected 0x{int(PcbRecordType.PAD):02X})"
            )
        cursor += 1

        # SubRecord 1: Designator (Pascal string)
        cursor += self._parse_subrecord_designator(data, cursor)

        # SubRecord 2: Unknown (preserve for round-trip)
        cursor += self._parse_and_store_subrecord(data, cursor, 2)

        # SubRecord 3: Unknown (preserve for round-trip)
        cursor += self._parse_and_store_subrecord(data, cursor, 3)

        # SubRecord 4: Unknown (preserve for round-trip)
        cursor += self._parse_and_store_subrecord(data, cursor, 4)

        # SubRecord 5: SizeAndShape (main geometry)
        cursor += self._parse_subrecord_size_and_shape(data, cursor)

        # SubRecord 6: SizeAndShapeByLayer (optional per-layer variations)
        if cursor < len(data):
            cursor += self._parse_subrecord_size_and_shape_by_layer(data, cursor)

        # Store raw binary for round-trip
        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()

        return cursor - offset

    def _parse_subrecord_designator(self, data: bytes, offset: int) -> int:
        """
        Parse SubRecord 1: Designator.
        """
        if len(data) < offset + 4:
            raise ValueError("Data too short for SubRecord 1")

        subrecord_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4

        if len(data) < offset + subrecord_len:
            raise ValueError(f"SubRecord 1 truncated: expected {subrecord_len} bytes")

        content = data[offset : offset + subrecord_len]

        # Parse Pascal string: first byte is length
        if len(content) >= 1:
            str_len = content[0]
            if str_len > 0 and len(content) >= str_len + 1:
                self.designator = content[1 : 1 + str_len].decode(
                    "utf-8", errors="replace"
                )

        return 4 + subrecord_len

    def _parse_and_store_subrecord(
        self, data: bytes, offset: int, subrecord_num: int
    ) -> int:
        """
        Parse and store a SubRecord's raw data for round-trip compatibility.
        """
        if len(data) < offset + 4:
            return 0  # End of data

        subrecord_len = struct.unpack("<I", data[offset : offset + 4])[0]

        if len(data) < offset + 4 + subrecord_len:
            return 0  # Truncated

        # Store the content (not including the length prefix)
        content = data[offset + 4 : offset + 4 + subrecord_len]

        if subrecord_num == 2:
            self._subrecord2_data = content
        elif subrecord_num == 3:
            self._subrecord3_data = content
        elif subrecord_num == 4:
            self._subrecord4_data = content

        return 4 + subrecord_len

    def _skip_subrecord(self, data: bytes, offset: int) -> int:
        """
        Skip a SubRecord (read length and skip content).
        """
        if len(data) < offset + 4:
            return 0  # End of data

        subrecord_len = struct.unpack("<I", data[offset : offset + 4])[0]
        return 4 + subrecord_len

    def _parse_subrecord_size_and_shape(self, data: bytes, offset: int) -> int:
        """
        Parse SubRecord 5: SizeAndShape (main geometry).
        """
        if len(data) < offset + 4:
            raise ValueError("Data too short for SubRecord 5")

        subrecord_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4

        if len(data) < offset + subrecord_len:
            raise ValueError(f"SubRecord 5 truncated: expected {subrecord_len} bytes")

        content = data[offset : offset + subrecord_len]

        if len(content) < 110:
            log.warning(
                f"SubRecord 5 shorter than expected: {len(content)} bytes (expected >=110)"
            )

        pos = 0

        # Layer (offset 0)
        self.layer = content[pos]
        pos += 1

        # Flags (offset 1-2) - capture for round-trip and decode
        self._flags = (
            struct.unpack("<H", content[pos : pos + 2])[0]
            if pos + 2 <= len(content)
            else 0
        )
        # Decode flag bits (from record format binary header layout APAD6 struct)
        flags1 = self._flags & 0xFF  # Low byte (offset 1)
        flags2 = (self._flags >> 8) & 0xFF  # High byte (offset 2)
        self._flags1_bit0 = flags1 & 0x01  # bit 0 (preserved)
        self.user_routed = (flags1 & 0x08) != 0  # bit 3 (shared with Track/Fill/Arc)
        self.is_tenting_top = (flags1 & 0x20) != 0  # bit 5
        self.is_tenting_bottom = (flags1 & 0x40) != 0  # bit 6
        self.is_test_fab_top = (flags1 & 0x10) != 0  # bit 4
        self.is_test_fab_bottom = (flags2 & 0x01) != 0  # bit 0 of high byte
        # Assembly test points (DFT metadata; does not imply copper suppression)
        # bit 7 (0x80): set on tagged assembly test pads
        self.is_assy_test_point_top = (flags1 & 0x80) != 0  # bit 7
        self.is_assy_test_point_bottom = (flags2 & 0x02) != 0  # bit 1 of high byte
        pos += 2

        # Net index (offset 3-4)
        self.net_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.net_index == 0xFFFF:
            self.net_index = None
        pos += 2

        # Polygon index (offset 5-6: always 0xFFFF for pads, shared header field)
        self.polygon_index = (
            struct.unpack("<H", content[pos : pos + 2])[0]
            if pos + 2 <= len(content)
            else 0xFFFF
        )
        pos += 2

        # Component index (offset 7-8) - key linkage
        self.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.component_index == 0xFFFF:
            self.component_index = None
        pos += 2

        # Union index (offset 9-12: 0xFFFFFFFF=none, shared header field)
        self.union_index = (
            struct.unpack("<I", content[pos : pos + 4])[0]
            if pos + 4 <= len(content)
            else 0xFFFFFFFF
        )
        pos += 4

        # Position (offset 13-20: 2x int32)
        self.x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.y = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Top width (offset 21-24)
        self.top_width = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Top height (offset 25-28)
        self.top_height = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Mid width (offset 29-32)
        self.mid_width = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Mid height (offset 33-36)
        self.mid_height = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Bot width (offset 37-40)
        self.bot_width = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Bot height (offset 41-44)
        self.bot_height = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Hole size (offset 45-48: uint32)
        self.hole_size = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        # Shapes (offset 49-51: 3 bytes)
        if pos + 3 <= len(content):
            self.top_shape = content[pos]
            pos += 1
            self.mid_shape = content[pos]
            pos += 1
            self.bot_shape = content[pos]
            pos += 1

        # Rotation (offset 52-59: double)
        if pos + 8 <= len(content):
            self.rotation = struct.unpack("<d", content[pos : pos + 8])[0]
            pos += 8

        # Plated (offset 60)
        if pos < len(content):
            self.is_plated = content[pos] != 0
            pos += 1

        # Skip 1 reserved byte (offset 61) and preserve on-disk compatibility.
        if pos < len(content):
            pos += 1

        # Pad mode (offset 62: uint8) - 0=simple, 1=top-mid-bot, 2=full stack
        if pos < len(content):
            self.pad_mode = content[pos]
            pos += 1

        # Parse pad-cache thermal relief + mask fields if SubRecord 5 is long enough.
        # Offsets 63-102 contain the full cache block (40 bytes).
        if pos + 40 <= len(content):
            # --- Pad-cache thermal relief block (offsets 63-85) ---
            pos += 4  # offset 63-66: reserved (always 0)
            self.plane_connection_style = content[pos]  # offset 67
            pos += 1
            self.cache_relief_conductor_width = struct.unpack(
                "<i", content[pos : pos + 4]
            )[0]  # offset 68-71
            pos += 4
            self.cache_relief_entries = struct.unpack("<H", content[pos : pos + 2])[
                0
            ]  # offset 72-73
            pos += 2
            self.cache_relief_air_gap = struct.unpack("<i", content[pos : pos + 4])[
                0
            ]  # offset 74-77
            pos += 4
            self.cache_power_plane_relief_expansion = struct.unpack(
                "<i", content[pos : pos + 4]
            )[0]  # offset 78-81
            pos += 4
            self.cache_power_plane_clearance = struct.unpack(
                "<i", content[pos : pos + 4]
            )[0]  # offset 82-85
            pos += 4
            self._has_pad_cache = True

            # --- Mask expansion values (offsets 86-93) ---
            self.pastemask_expansion_manual = struct.unpack(
                "<i", content[pos : pos + 4]
            )[0]  # offset 86-89
            pos += 4
            self.soldermask_expansion_manual = struct.unpack(
                "<i", content[pos : pos + 4]
            )[0]  # offset 90-93
            pos += 4

            # --- Cache validity bytes (offsets 94-100) ---
            self._cache_byte_94 = content[pos]  # offset 94
            pos += 1
            self._cache_padding_95 = content[pos]  # offset 95
            pos += 1
            self.cache_plane_connection_valid = content[pos]  # offset 96
            pos += 1
            self.cache_relief_conductor_width_valid = content[pos]  # offset 97
            pos += 1
            self.cache_relief_entries_valid = content[pos]  # offset 98
            pos += 1
            self.cache_relief_air_gap_valid = content[pos]  # offset 99
            pos += 1
            self.cache_power_plane_relief_expansion_valid = content[pos]  # offset 100
            pos += 1

            # --- Mask modes (offsets 101-102) ---
            self.pastemask_expansion_mode = content[pos]  # offset 101
            pos += 1
            self.soldermask_expansion_mode = content[pos]  # offset 102
            pos += 1
            self._has_mask_expansion = True

            # --- Post-mode cache validity (offsets 103-104) ---
            if pos + 2 <= len(content):
                self.cache_paste_mask_expansion_valid = content[pos]  # offset 103
                pos += 1
                self.cache_solder_mask_expansion_valid = content[pos]  # offset 104
                pos += 1

        if len(content) >= 118:
            self.layer_v7_save_id = struct.unpack("<I", content[114:118])[0]

        # Capture any extended data beyond offset 61 (for round-trip compatibility)
        # (includes unknown byte + pad_mode + mask fields + everything after)
        extended_start = 61
        if extended_start < len(content):
            self._subrecord5_extended_data = content[extended_start:]
            self._subrecord5_extended_signature = (
                self._subrecord5_extended_state_signature()
            )

        # Use top layer as default width/height/shape
        self.width = self.top_width
        self.height = self.top_height
        self.shape = self.top_shape

        return 4 + subrecord_len

    def _parse_subrecord_size_and_shape_by_layer(self, data: bytes, offset: int) -> int:
        """
        Parse SubRecord 6: SizeAndShapeByLayer (per-layer pad variations).

        Known lengths: 596, 628, 651 bytes.
        When >= 596 bytes, contains per-layer sizes, shapes, hole info, and corner radii.
        """
        if len(data) < offset + 4:
            return 0

        subrecord_len = struct.unpack("<I", data[offset : offset + 4])[0]
        if len(data) < offset + 4 + subrecord_len:
            return 0

        content = data[offset + 4 : offset + 4 + subrecord_len]
        self._subrecord6_data = content  # Store raw for round-trip

        if subrecord_len >= 596:
            pos = 0

            # Inner sizes X (29 layers, int32 each = 116 bytes)
            self.inner_size_x = []
            for _ in range(29):
                self.inner_size_x.append(struct.unpack("<i", content[pos : pos + 4])[0])
                pos += 4

            # Inner sizes Y (29 layers, int32 each = 116 bytes)
            self.inner_size_y = []
            for _ in range(29):
                self.inner_size_y.append(struct.unpack("<i", content[pos : pos + 4])[0])
                pos += 4

            # Inner shapes (29 layers, uint8 each = 29 bytes)
            self.inner_shape = list(content[pos : pos + 29])
            pos += 29

            # Skip 1 byte padding
            pos += 1

            # Hole shape (uint8): 0=Round, 1=Square, 2=Slot
            self.hole_shape = content[pos]
            pos += 1

            # Slot size (int32)
            self.slot_size = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4

            # Slot rotation (double)
            self.slot_rotation = struct.unpack("<d", content[pos : pos + 8])[0]
            pos += 8

            # Per-layer pad-center offsets X (32 layers, int32 each = 128 bytes)
            self.hole_offset_x = []
            for _ in range(32):
                self.hole_offset_x.append(
                    struct.unpack("<i", content[pos : pos + 4])[0]
                )
                pos += 4

            # Per-layer pad-center offsets Y (32 layers, int32 each = 128 bytes)
            self.hole_offset_y = []
            for _ in range(32):
                self.hole_offset_y.append(
                    struct.unpack("<i", content[pos : pos + 4])[0]
                )
                pos += 4

            # Skip 1 byte padding
            pos += 1

            # Alt shapes (32 layers, uint8 each = 32 bytes)
            self.alt_shape = list(content[pos : pos + 32])
            pos += 32

            # Corner radii (32 layers, uint8 each = 32 bytes)
            self.corner_radius = list(content[pos : pos + 32])
            pos += 32

            # Optional tail block used by full-stack pads. Observed format:
            # - 32 bytes reserved/legacy payload
            # - uint32 entry_count
            # - uint32 entry_stride (observed 15)
            # - entry_count * entry_stride bytes
            #   entry[0:2]   int16 layer code
            #   entry[2:4]   uint16 mode/flags
            #   entry[4]     uint8 enabled
            #   entry[5:9]   int32 size X (internal units)
            #   entry[9:13]  int32 size Y (internal units)
            #   entry[13:15] uint16 corner % / mode payload
            self._parse_subrecord6_full_stack_entries(content)

        elif subrecord_len != 0:
            log.debug(
                "Pad SubRecord 6 unexpected length: %d (expected 0 or >=596)",
                subrecord_len,
            )

        self._subrecord6_signature = self._subrecord6_state_signature()

        return 4 + subrecord_len

    def _parse_subrecord6_full_stack_entries(self, content: bytes) -> None:
        """
        Parse optional SubRecord 6 full-stack entry tail.
        """
        self.full_stack_layer_entries = []

        tail_offset = 596
        header_size = 40
        if len(content) < tail_offset + header_size:
            return

        count = struct.unpack("<I", content[tail_offset + 32 : tail_offset + 36])[0]
        stride = struct.unpack("<I", content[tail_offset + 36 : tail_offset + 40])[0]
        if count <= 0 or stride < 15:
            return

        data_start = tail_offset + header_size
        data_end = data_start + (count * stride)
        if data_end > len(content):
            return

        entries: list[tuple[int, int, int, int, int, int]] = []
        pos = data_start
        for _ in range(count):
            rec = content[pos : pos + stride]
            layer_code = struct.unpack("<h", rec[0:2])[0]
            mode_flags = struct.unpack("<H", rec[2:4])[0]
            enabled = rec[4]
            size_x = struct.unpack("<i", rec[5:9])[0]
            size_y = struct.unpack("<i", rec[9:13])[0]
            corner_pct = struct.unpack("<H", rec[13:15])[0]
            entries.append(
                (layer_code, mode_flags, enabled, size_x, size_y, corner_pct)
            )
            pos += stride

        self.full_stack_layer_entries = entries

    def _subrecord6_state_signature(self) -> tuple:
        """
        Return a stable signature for the typed SubRecord 6 state.
        """

        def _freeze(value: object) -> object:
            if isinstance(value, list):
                return tuple(_freeze(v) for v in value)
            if isinstance(value, tuple):
                return tuple(_freeze(v) for v in value)
            return value

        return (
            _freeze(self.inner_size_x),
            _freeze(self.inner_size_y),
            _freeze(self.inner_shape),
            int(self.hole_shape or 0),
            int(self.slot_size or 0),
            float(self.slot_rotation or 0.0),
            _freeze(self.hole_offset_x),
            _freeze(self.hole_offset_y),
            _freeze(self.alt_shape),
            _freeze(self.corner_radius),
            _freeze(self.full_stack_layer_entries),
        )

    def _state_signature(self) -> tuple:
        """
        Return a stable signature for known PAD serializer state.
        """

        def _freeze(value: object) -> object:
            if isinstance(value, list):
                return tuple(_freeze(v) for v in value)
            if isinstance(value, tuple):
                return tuple(_freeze(v) for v in value)
            if isinstance(value, dict):
                return tuple((k, _freeze(v)) for k, v in sorted(value.items()))
            if isinstance(value, bytearray):
                return bytes(value)
            return value

        items: list[tuple[str, object]] = []
        for key, value in sorted(self.__dict__.items()):
            if key in {
                "_raw_binary",
                "_raw_binary_signature",
                "_subrecord5_extended_signature",
                "_subrecord6_signature",
                "custom_shape",
            }:
                continue
            items.append((key, _freeze(value)))
        return tuple(items)

    def _subrecord5_extended_state_signature(self) -> tuple:
        """
        Return a stable signature for the typed SubRecord 5 tail state.
        """
        return (
            int(self.pad_mode or 0),
            int(self.plane_connection_style or 0),
            int(self.cache_relief_conductor_width or 0),
            int(self.cache_relief_entries or 0),
            int(self.cache_relief_air_gap or 0),
            int(self.cache_power_plane_relief_expansion or 0),
            int(self.cache_power_plane_clearance or 0),
            int(self.pastemask_expansion_manual or 0),
            int(self.soldermask_expansion_manual or 0),
            int(self._cache_byte_94 or 0),
            int(self._cache_padding_95 or 0),
            int(self.cache_plane_connection_valid or 0),
            int(self.cache_relief_conductor_width_valid or 0),
            int(self.cache_relief_entries_valid or 0),
            int(self.cache_relief_air_gap_valid or 0),
            int(self.cache_power_plane_relief_expansion_valid or 0),
            int(self.pastemask_expansion_mode or 0),
            int(self.soldermask_expansion_mode or 0),
            int(self.cache_paste_mask_expansion_valid or 0),
            int(self.cache_solder_mask_expansion_valid or 0),
            0 if self.layer_v7_save_id is None else int(self.layer_v7_save_id),
        )

    def _pack_flags(self) -> int:
        """
        Pack decoded flag booleans back into the shared PAD header flags.
        """
        flags = int(self._flags or 0)
        flags1 = flags & 0xFF
        flags2 = (flags >> 8) & 0xFF

        def _set(flag_byte: int, mask: int, enabled: bool) -> int:
            if enabled:
                return flag_byte | mask
            return flag_byte & (~mask & 0xFF)

        flags1 = _set(flags1, 0x08, bool(self.user_routed))
        flags1 = _set(flags1, 0x10, bool(self.is_test_fab_top))
        flags1 = _set(flags1, 0x20, bool(self.is_tenting_top))
        flags1 = _set(flags1, 0x40, bool(self.is_tenting_bottom))
        flags1 = _set(flags1, 0x80, bool(self.is_assy_test_point_top))
        flags2 = _set(flags2, 0x01, bool(self.is_test_fab_bottom))
        flags2 = _set(flags2, 0x02, bool(self.is_assy_test_point_bottom))

        if self._flags1_bit0:
            flags1 |= 0x01
        else:
            flags1 &= 0xFE

        return flags1 | (flags2 << 8)

    def _build_subrecord5_extended_data(self) -> bytes:
        """
        Synthesize the extended PAD tail (offset 61 onward).
        """
        current_sig = self._subrecord5_extended_state_signature()
        if (
            self._subrecord5_extended_data
            and self._subrecord5_extended_signature is not None
            and current_sig == self._subrecord5_extended_signature
        ):
            return self._subrecord5_extended_data

        if self._subrecord5_extended_data:
            ext_data = bytearray(self._subrecord5_extended_data)
        else:
            ext_data = bytearray(b"\x00" * 57)

        if len(ext_data) < 57:
            ext_data.extend(b"\x00" * (57 - len(ext_data)))

        if not self._subrecord5_extended_data:
            ext_data[0] = 0
        ext_data[1] = int(self.pad_mode) & 0xFF
        if not self._subrecord5_extended_data:
            struct.pack_into("<I", ext_data, 2, 0)
        ext_data[6] = int(self.plane_connection_style) & 0xFF
        struct.pack_into("<i", ext_data, 7, int(self.cache_relief_conductor_width or 0))
        struct.pack_into(
            "<H", ext_data, 11, int(self.cache_relief_entries or 0) & 0xFFFF
        )
        struct.pack_into("<i", ext_data, 13, int(self.cache_relief_air_gap or 0))
        struct.pack_into(
            "<i", ext_data, 17, int(self.cache_power_plane_relief_expansion or 0)
        )
        struct.pack_into("<i", ext_data, 21, int(self.cache_power_plane_clearance or 0))
        struct.pack_into("<i", ext_data, 25, int(self.pastemask_expansion_manual or 0))
        struct.pack_into("<i", ext_data, 29, int(self.soldermask_expansion_manual or 0))
        ext_data[33] = int(self._cache_byte_94) & 0xFF
        ext_data[34] = int(self._cache_padding_95) & 0xFF
        ext_data[35] = int(self.cache_plane_connection_valid) & 0xFF
        ext_data[36] = int(self.cache_relief_conductor_width_valid) & 0xFF
        ext_data[37] = int(self.cache_relief_entries_valid) & 0xFF
        ext_data[38] = int(self.cache_relief_air_gap_valid) & 0xFF
        ext_data[39] = int(self.cache_power_plane_relief_expansion_valid) & 0xFF
        ext_data[40] = int(self.pastemask_expansion_mode) & 0xFF
        ext_data[41] = int(self.soldermask_expansion_mode) & 0xFF
        ext_data[42] = int(self.cache_paste_mask_expansion_valid) & 0xFF
        ext_data[43] = int(self.cache_solder_mask_expansion_valid) & 0xFF
        struct.pack_into(
            "<I", ext_data, 53, int(self.layer_v7_save_id or 0) & 0xFFFFFFFF
        )
        built = bytes(ext_data)
        self._subrecord5_extended_data = built
        self._subrecord5_extended_signature = current_sig
        return built

    def _has_synthesized_subrecord6_fields(self) -> bool:
        return any(
            (
                self.inner_size_x,
                self.inner_size_y,
                self.inner_shape,
                self.hole_shape,
                self.slot_size,
                self.slot_rotation,
                self.hole_offset_x,
                self.hole_offset_y,
                self.alt_shape,
                self.corner_radius,
                self.full_stack_layer_entries,
            )
        )

    def _build_subrecord6_data(self) -> bytes:
        """
        Synthesize SubRecord 6 (SizeAndShapeByLayer) from typed fields.
        """
        if not self._has_synthesized_subrecord6_fields():
            return b""

        def _pad_list(values: list[int], count: int, default: int = 0) -> list[int]:
            out = list(values[:count])
            if len(out) < count:
                out.extend([default] * (count - len(out)))
            return out

        content = bytearray()
        inner_size_x = _pad_list(
            [int(v) for v in self.inner_size_x], 29, int(self.mid_width or 0)
        )
        inner_size_y = _pad_list(
            [int(v) for v in self.inner_size_y], 29, int(self.mid_height or 0)
        )
        inner_shape = _pad_list(
            [int(v) for v in self.inner_shape],
            29,
            int(self.mid_shape or PadShape.CIRCLE),
        )
        hole_offset_x = _pad_list([int(v) for v in self.hole_offset_x], 32, 0)
        hole_offset_y = _pad_list([int(v) for v in self.hole_offset_y], 32, 0)
        alt_shape = _pad_list([int(v) for v in self.alt_shape], 32, 0)
        corner_radius = _pad_list([int(v) for v in self.corner_radius], 32, 0)

        for value in inner_size_x:
            content.extend(struct.pack("<i", value))
        for value in inner_size_y:
            content.extend(struct.pack("<i", value))
        content.extend(bytes(v & 0xFF for v in inner_shape))
        content.append(0)
        content.append(int(self.hole_shape) & 0xFF)
        content.extend(struct.pack("<i", int(self.slot_size or 0)))
        content.extend(struct.pack("<d", float(self.slot_rotation or 0.0)))
        for value in hole_offset_x:
            content.extend(struct.pack("<i", value))
        for value in hole_offset_y:
            content.extend(struct.pack("<i", value))
        content.append(0)
        content.extend(bytes(v & 0xFF for v in alt_shape))
        content.extend(bytes(v & 0xFF for v in corner_radius))

        if self.full_stack_layer_entries:
            content.extend(b"\x00" * 32)
            stride = 15
            content.extend(struct.pack("<I", len(self.full_stack_layer_entries)))
            content.extend(struct.pack("<I", stride))
            for (
                layer_code,
                mode_flags,
                enabled,
                size_x,
                size_y,
                corner_pct,
            ) in self.full_stack_layer_entries:
                entry = bytearray()
                entry.extend(struct.pack("<h", int(layer_code)))
                entry.extend(struct.pack("<H", int(mode_flags) & 0xFFFF))
                entry.append(int(enabled) & 0xFF)
                entry.extend(struct.pack("<i", int(size_x)))
                entry.extend(struct.pack("<i", int(size_y)))
                entry.extend(struct.pack("<H", int(corner_pct) & 0xFFFF))
                content.extend(entry)

        built = bytes(content)
        self._subrecord6_data = built
        self._subrecord6_signature = self._subrecord6_state_signature()
        return built

    @property
    def semantic_top_shape(self) -> int:
        """
        Return the first-class semantic top shape, including custom pads.
        """
        if self.custom_shape is not None:
            return int(PadShape.CUSTOM)
        return int(self.effective_top_shape)

    def serialize_to_binary(self) -> bytes:
        """
        Serialize PAD record to binary format.

        Returns:
            Binary data ready to write to stream

        Strategy:
            PAD records are complex with 6 SubRecords.
            For round-trip, use stored raw binary if available.
            For from-scratch creation, build minimal required SubRecords.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        # Create from scratch
        record = bytearray()

        # Type byte (0x02 = PAD)
        record.append(0x02)

        # SubRecord 1: Designator (Pascal string)
        designator_bytes = self.designator.encode("utf-8")
        subrecord1_content = bytearray()
        subrecord1_content.append(len(designator_bytes))
        subrecord1_content.extend(designator_bytes)
        record.extend(struct.pack("<I", len(subrecord1_content)))
        record.extend(subrecord1_content)

        # SubRecord 2: Unknown (use captured data if available)
        if self._subrecord2_data:
            record.extend(struct.pack("<I", len(self._subrecord2_data)))
            record.extend(self._subrecord2_data)
        else:
            record.extend(struct.pack("<I", 0))

        # SubRecord 3: Unknown (use captured data if available)
        if self._subrecord3_data:
            record.extend(struct.pack("<I", len(self._subrecord3_data)))
            record.extend(self._subrecord3_data)
        else:
            record.extend(struct.pack("<I", 0))

        # SubRecord 4: Unknown (use captured data if available)
        if self._subrecord4_data:
            record.extend(struct.pack("<I", len(self._subrecord4_data)))
            record.extend(self._subrecord4_data)
        else:
            record.extend(struct.pack("<I", 0))

        # SubRecord 5: SizeAndShape (main geometry - 110 bytes minimum)
        subrecord5_content = bytearray()

        # Offset 0: Layer
        subrecord5_content.append(self.layer)

        # Offset 1-2: Flags (use captured value or 0 for new records)
        packed_flags = self._pack_flags()
        subrecord5_content.extend(struct.pack("<H", packed_flags))

        # Offset 3-4: Net index
        net = 0xFFFF if self.net_index is None else self.net_index
        subrecord5_content.extend(struct.pack("<H", net))

        # Offset 5-6: Polygon index (always 0xFFFF for pads)
        subrecord5_content.extend(struct.pack("<H", self.polygon_index))

        # Offset 7-8: Component index
        comp = 0xFFFF if self.component_index is None else self.component_index
        subrecord5_content.extend(struct.pack("<H", comp))

        # Offset 9-12: Union index (0xFFFFFFFF=none)
        subrecord5_content.extend(struct.pack("<I", self.union_index))

        # Offset 13-20: Position (X, Y as int32)
        subrecord5_content.extend(struct.pack("<i", self.x))
        subrecord5_content.extend(struct.pack("<i", self.y))

        # Offset 21-44: Layer-specific widths and heights (6 x int32)
        subrecord5_content.extend(struct.pack("<i", self.top_width))
        subrecord5_content.extend(struct.pack("<i", self.top_height))
        subrecord5_content.extend(struct.pack("<i", self.mid_width))
        subrecord5_content.extend(struct.pack("<i", self.mid_height))
        subrecord5_content.extend(struct.pack("<i", self.bot_width))
        subrecord5_content.extend(struct.pack("<i", self.bot_height))

        # Offset 45-48: Hole size (uint32)
        subrecord5_content.extend(struct.pack("<I", self.hole_size))

        # Offset 49-51: Shapes (3 bytes)
        subrecord5_content.append(self.top_shape)
        subrecord5_content.append(self.mid_shape)
        subrecord5_content.append(self.bot_shape)

        # Offset 52-59: Rotation (double)
        subrecord5_content.extend(struct.pack("<d", self.rotation))

        # Offset 60: Plated (1 byte)
        subrecord5_content.append(1 if self.is_plated else 0)

        # Append any extended data captured during parsing (for round-trip)
        ext_data = self._build_subrecord5_extended_data()
        if ext_data:
            subrecord5_content.extend(ext_data)

        # Write SubRecord 5
        record.extend(struct.pack("<I", len(subrecord5_content)))
        record.extend(subrecord5_content)

        # SubRecord 6: SizeAndShapeByLayer
        current_subrecord6_sig = self._subrecord6_state_signature()
        if (
            self._subrecord6_data
            and self._subrecord6_signature is not None
            and current_subrecord6_sig == self._subrecord6_signature
        ):
            subrecord6_data = self._subrecord6_data
        elif self._has_synthesized_subrecord6_fields():
            subrecord6_data = self._build_subrecord6_data()
        else:
            subrecord6_data = self._subrecord6_data
        if subrecord6_data:
            record.extend(struct.pack("<I", len(subrecord6_data)))
            record.extend(subrecord6_data)
        else:
            record.extend(struct.pack("<I", 0))

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    @property
    def corner_radius_percentage(self) -> int:
        """
        Get corner radius percentage for top layer (0 if not rounded rect).
        """
        if self.corner_radius and len(self.corner_radius) > 0:
            # Index 0 = top layer in the 32-layer array
            return self.corner_radius[0]
        return 0

    @property
    def effective_top_shape(self) -> int:
        """
        Get effective top layer shape, applying SubRecord 6 alt_shape overrides.

                SubRecord 5 stores the base shape (often ROUND even for rounded rect pads).
                SubRecord 6 alt_shape overrides this per-layer (e.g. value 9 = RoundedRect).
        """
        if self.alt_shape and len(self.alt_shape) > 0:
            override = _ALT_SHAPE_OVERRIDE.get(self.alt_shape[0])
            if override is not None:
                return override
        return self.top_shape

    @property
    def soldermask_expansion_mils(self) -> float:
        """
        Get soldermask expansion in mils (manual value).
        """
        return self._from_internal_units(self.soldermask_expansion_manual)

    @property
    def pastemask_expansion_mils(self) -> float:
        """
        Get pastemask expansion in mils (manual value).
        """
        return self._from_internal_units(self.pastemask_expansion_manual)

    @property
    def hole_size_mils(self) -> float:
        """
        Get hole size in mils.
        """
        return self._from_internal_units(self.hole_size)

    def _pad_offset_layer_index(
        self, layer: "PcbLayer | int | None" = None
    ) -> int | None:
        """
        Map a requested layer to the pad-offset array index (0-31).
        """
        offsets_x = self.hole_offset_x or []
        offsets_y = self.hole_offset_y or []
        count = min(len(offsets_x), len(offsets_y))
        if count <= 0:
            return None

        layer_id: int | None = None
        if layer is None:
            layer_id = None
        elif isinstance(layer, int):
            layer_id = layer
        else:
            layer_id = int(layer.value)

        if layer_id is not None and 1 <= layer_id <= count:
            return layer_id - 1

        if layer_id in (
            PcbLayer.TOP_OVERLAY.value,
            PcbLayer.TOP_PASTE.value,
            PcbLayer.TOP_SOLDER.value,
        ):
            return 0
        if layer_id in (
            PcbLayer.BOTTOM_OVERLAY.value,
            PcbLayer.BOTTOM_PASTE.value,
            PcbLayer.BOTTOM_SOLDER.value,
        ):
            return min(31, count - 1)

        return 0

    def pad_offset_internal_units(
        self, layer: "PcbLayer | int | None" = None
    ) -> tuple[int, int]:
        """
        Get pad-center offset (internal units) for a given layer.
        """
        offsets_x = self.hole_offset_x or []
        offsets_y = self.hole_offset_y or []
        if not offsets_x or not offsets_y:
            return (0, 0)

        idx = self._pad_offset_layer_index(layer)
        if idx is None:
            return (0, 0)
        off_x = int(offsets_x[idx])
        off_y = int(offsets_y[idx])
        return self._rotate_pad_offset_internal_units(off_x, off_y)

    def _rotate_pad_offset_internal_units(
        self, off_x: int, off_y: int
    ) -> tuple[int, int]:
        """
        Rotate pad offset vector into board coordinates using pad rotation.
        """
        rotation = float(self.rotation or 0.0) % 360.0
        if math.isclose(rotation, 0.0, abs_tol=1e-9):
            return (off_x, off_y)

        # Fast exact paths for orthogonal pads (the common castellated case).
        quarter_turns = round(rotation / 90.0)
        if math.isclose(rotation, quarter_turns * 90.0, abs_tol=1e-9):
            turn = quarter_turns % 4
            if turn == 1:
                return (-off_y, off_x)
            if turn == 2:
                return (-off_x, -off_y)
            if turn == 3:
                return (off_y, -off_x)
            return (off_x, off_y)

        angle = math.radians(rotation)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        rot_x = off_x * cos_a - off_y * sin_a
        rot_y = off_x * sin_a + off_y * cos_a
        return (int(round(rot_x)), int(round(rot_y)))

    def pad_center_internal_units(
        self, layer: "PcbLayer | int | None" = None
    ) -> tuple[int, int]:
        """
        Get copper pad center in internal units for a given layer.
        """
        off_x, off_y = self.pad_offset_internal_units(layer)
        return (int(self.x) + off_x, int(self.y) + off_y)

    def pad_center_mils(
        self, layer: "PcbLayer | int | None" = None
    ) -> tuple[float, float]:
        """
        Get copper pad center in mils for a given layer.
        """
        cx_iu, cy_iu = self.pad_center_internal_units(layer)
        return (self._from_internal_units(cx_iu), self._from_internal_units(cy_iu))

    def hole_center_internal_units(
        self, layer: "PcbLayer | int | None" = None
    ) -> tuple[int, int]:
        """
        Get drilled-hole center in internal units.

                Hole center is anchored at the base pad location regardless of
                per-layer copper pad offsets.
        """
        _ = layer
        return (int(self.x), int(self.y))

    def hole_center_mils(
        self, layer: "PcbLayer | int | None" = None
    ) -> tuple[float, float]:
        """
        Get drilled-hole center in mils.
        """
        cx_iu, cy_iu = self.hole_center_internal_units(layer)
        return (self._from_internal_units(cx_iu), self._from_internal_units(cy_iu))

    @property
    def is_through_hole(self) -> bool:
        """
        Check if this is a through-hole pad.
        """
        return self.hole_size > 0

    @property
    def is_smt(self) -> bool:
        """
        Check if this is an SMT pad.
        """
        return self.hole_size == 0

    def _source_layer(self) -> PcbLayer | None:
        """
        Resolve this pad's source layer enum.
        """
        try:
            return PcbLayer(int(self.layer))
        except ValueError:
            return None

    def _full_stack_layer_override(
        self,
        layer: "PcbLayer | int",
    ) -> tuple[int, int, int, int, int, int] | None:
        """
        Return the best explicit full-stack override entry for a layer.

                The optional SubRecord 6 tail stores explicit per-layer openings for
                some pads, especially older full-stack pads. Prefer entries with
                non-zero low mode-flag bits because those behave like real overrides.
                Fall back to a single matching entry when that is all we have.
        """
        layer_code = int(layer.value) if isinstance(layer, PcbLayer) else int(layer)
        if isinstance(layer, PcbLayer):
            if layer == PcbLayer.TOP_SOLDER:
                layer_code = 8
            elif layer == PcbLayer.BOTTOM_SOLDER:
                layer_code = 9
        entries = getattr(self, "full_stack_layer_entries", None) or []
        candidates: list[tuple[int, int, int, int, int, int]] = []
        for entry in entries:
            try:
                if int(entry[0]) != layer_code:
                    continue
                candidates.append(entry)
            except (TypeError, ValueError, IndexError):
                continue
        if not candidates:
            return None

        override = None
        for entry in candidates:
            try:
                mode_flags = int(entry[1] or 0)
            except (TypeError, ValueError, IndexError):
                continue
            if (mode_flags & 0xFF) != 0:
                override = entry
        if override is not None:
            return override
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _full_stack_layer_size(self, layer: "PcbLayer | int") -> tuple[int, int] | None:
        """
        Return explicit full-stack size override for a specific layer.
        """
        entry = self._full_stack_layer_override(layer)
        if entry is None:
            return None
        try:
            return (int(entry[3] or 0), int(entry[4] or 0))
        except (TypeError, ValueError, IndexError):
            return None

    @staticmethod
    def _side_base_layer(layer: PcbLayer) -> PcbLayer:
        """
        Map side-specific mask/paste layers to their copper-side geometry.
        """
        if layer.is_bottom_side():
            return PcbLayer.BOTTOM
        return PcbLayer.TOP

    def _should_render_on_layer(self, layer: PcbLayer) -> bool:
        """
        Check if this pad contributes geometry on the requested layer.
        """
        source_layer = self._source_layer()
        if source_layer is None:
            return False

        if self.is_through_hole:
            if layer.is_copper():
                return True
            if layer == PcbLayer.TOP_SOLDER:
                explicit = self._full_stack_layer_size(layer)
                if explicit is not None:
                    return explicit[0] > 0 and explicit[1] > 0
                return not self.is_tenting_top
            if layer == PcbLayer.BOTTOM_SOLDER:
                explicit = self._full_stack_layer_size(layer)
                if explicit is not None:
                    return explicit[0] > 0 and explicit[1] > 0
                return not self.is_tenting_bottom
            return False

        solder_only = is_pad_solder_mask_only(self)

        if source_layer == PcbLayer.MULTI_LAYER:
            if layer == PcbLayer.TOP_SOLDER:
                return not self.is_tenting_top
            if layer == PcbLayer.BOTTOM_SOLDER:
                return not self.is_tenting_bottom
            if layer.is_copper():
                return not solder_only
            return False

        if source_layer == PcbLayer.TOP:
            if layer == PcbLayer.TOP:
                return not solder_only
            if layer == PcbLayer.TOP_SOLDER:
                return True
            if layer == PcbLayer.TOP_PASTE:
                if solder_only:
                    return False
                width_iu, height_iu = self._layer_size(PcbLayer.TOP)
                return has_pad_paste_opening(self, width_iu, height_iu)
            return False

        if source_layer == PcbLayer.BOTTOM:
            if layer == PcbLayer.BOTTOM:
                return not solder_only
            if layer == PcbLayer.BOTTOM_SOLDER:
                return True
            if layer == PcbLayer.BOTTOM_PASTE:
                if solder_only:
                    return False
                width_iu, height_iu = self._layer_size(PcbLayer.BOTTOM)
                return has_pad_paste_opening(self, width_iu, height_iu)
            return False

        if source_layer.is_copper():
            return layer.value == source_layer.value

        return layer.value == source_layer.value

    def _should_force_svg_copper_render(self, layer: PcbLayer) -> bool:
        """
        Allow board-SVG copper rendering for flagged testpoint pads.

                Altium's IPC output can serialize some TC2030-style testpoint pads as
                solder-mask-only apertures while the board renderer still shows the
                underlying copper on the owning side. Keep that distinction localized
                to SVG instead of weakening the generic mask-only heuristic.
        """
        if not layer.is_copper() or self.is_through_hole:
            return False

        source_layer = self._source_layer()
        if source_layer != layer:
            return False

        if layer == PcbLayer.TOP:
            return bool(
                getattr(self, "is_assy_test_point_top", False)
                or getattr(self, "is_fab_test_point_top", False)
                or getattr(self, "is_test_fab_top", False)
            )
        if layer == PcbLayer.BOTTOM:
            return bool(
                getattr(self, "is_assy_test_point_bottom", False)
                or getattr(self, "is_fab_test_point_bottom", False)
                or getattr(self, "is_test_fab_bottom", False)
            )
        return False

    def _layer_size(self, layer: PcbLayer) -> tuple[int, int]:
        """
        Get pad width/height (internal units) for a specific layer.
        """
        if layer in (PcbLayer.TOP_SOLDER, PcbLayer.BOTTOM_SOLDER):
            explicit = self._full_stack_layer_size(layer)
            if explicit is not None:
                return explicit

        if layer in (
            PcbLayer.TOP_PASTE,
            PcbLayer.TOP_SOLDER,
            PcbLayer.BOTTOM_PASTE,
            PcbLayer.BOTTOM_SOLDER,
        ):
            layer = self._side_base_layer(layer)

        if layer == PcbLayer.TOP:
            w = int(self.top_width or self.width)
            h = int(self.top_height or self.height)
            return w, h

        if layer == PcbLayer.BOTTOM:
            w = int(self.bot_width or self.top_width or self.width)
            h = int(self.bot_height or self.top_height or self.height)
            return w, h

        if layer.is_copper() and 2 <= layer.value <= 31:
            idx = layer.value - 2
            if idx < len(self.inner_size_x) and idx < len(self.inner_size_y):
                w = int(
                    self.inner_size_x[idx]
                    or self.mid_width
                    or self.top_width
                    or self.width
                )
                h = int(
                    self.inner_size_y[idx]
                    or self.mid_height
                    or self.top_height
                    or self.height
                )
                return w, h
            w = int(self.mid_width or self.top_width or self.width)
            h = int(self.mid_height or self.top_height or self.height)
            return w, h

        w = int(self.top_width or self.width)
        h = int(self.top_height or self.height)
        return w, h

    def _layer_shape(self, layer: PcbLayer) -> int:
        """
        Get effective pad shape for the requested layer.
        """
        if layer in (
            PcbLayer.TOP_PASTE,
            PcbLayer.TOP_SOLDER,
            PcbLayer.BOTTOM_PASTE,
            PcbLayer.BOTTOM_SOLDER,
        ):
            layer = self._side_base_layer(layer)

        layer_idx = layer.value - 1
        if 0 <= layer_idx < len(self.alt_shape):
            override = _ALT_SHAPE_OVERRIDE.get(self.alt_shape[layer_idx])
            if override is not None:
                return int(override)

        if layer == PcbLayer.BOTTOM:
            return int(self.bot_shape or self.top_shape or PadShape.CIRCLE)
        if layer.is_copper() and 2 <= layer.value <= 31:
            idx = layer.value - 2
            if idx < len(self.inner_shape):
                return int(
                    self.inner_shape[idx]
                    or self.mid_shape
                    or self.top_shape
                    or PadShape.CIRCLE
                )
            return int(self.mid_shape or self.top_shape or PadShape.CIRCLE)
        return int(self.top_shape or PadShape.CIRCLE)

    def _layer_corner_radius_mils(
        self, layer: PcbLayer, width_mils: float, height_mils: float
    ) -> float:
        """
        Get corner radius in mils for rounded-rectangle pads.
        """
        pct = 0
        layer_idx = layer.value - 1
        if 0 <= layer_idx < len(self.corner_radius):
            pct = int(self.corner_radius[layer_idx])
        elif self.corner_radius_percentage > 0:
            pct = int(self.corner_radius_percentage)

        if pct <= 0:
            return 0.0

        return (pct / 100.0) * min(width_mils, height_mils) / 2.0

    def _octagon_points(
        self,
        cx: float,
        cy: float,
        half_w: float,
        half_h: float,
        rotation_deg: float,
    ) -> list[tuple[float, float]]:
        """
        Build 8-vertex octagon points around center.
        """
        chamfer = min(half_w, half_h) / 2.0
        local = [
            (half_w, -(half_h - chamfer)),
            (half_w, half_h - chamfer),
            (half_w - chamfer, half_h),
            (-(half_w - chamfer), half_h),
            (-half_w, half_h - chamfer),
            (-half_w, -(half_h - chamfer)),
            (-(half_w - chamfer), -half_h),
            (half_w - chamfer, -half_h),
        ]

        if abs(rotation_deg) < 1e-9:
            return [(cx + x, cy + y) for x, y in local]

        angle = math.radians(rotation_deg)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        points: list[tuple[float, float]] = []
        for x_local, y_local in local:
            rx = x_local * cos_a - y_local * sin_a
            ry = x_local * sin_a + y_local * cos_a
            points.append((cx + rx, cy + ry))
        return points

    def _hole_knockout_svg_elements(
        self,
        ctx: "PcbSvgRenderContext",
        layer: PcbLayer,
        *,
        include_metadata: bool = True,
        hole_color: str = "#FFFFFF",
        hole_opacity: float = 1.0,
        hole_outline: bool = False,
        hole_outline_width_mm: float = 0.10,
    ) -> list[str]:
        """
        Render pad drill knockout geometry (circle or slotted obround).
        """
        if self.hole_size <= 0 or not layer.is_copper():
            return []

        hole_diameter_mm = max(self.hole_size_mils * _MIL_TO_MM, 0.0)
        if hole_diameter_mm <= 0.0:
            return []

        hole_shape_code = int(self.hole_shape or 0)
        slot_size_mils = self._from_internal_units(int(self.slot_size or 0))
        slot_length_mm = max(slot_size_mils * _MIL_TO_MM, hole_diameter_mm)
        is_slot = hole_shape_code == 2 and slot_length_mm > hole_diameter_mm + 1e-9
        hole_kind = "slot" if is_slot else "round"
        hole_plating = "plated" if bool(self.is_plated) else "non-plated"
        hole_render = "stroke" if (hole_outline or is_slot) else "fill"

        hole_cx_mils, hole_cy_mils = self.hole_center_mils(layer)
        cx = ctx.x_to_svg(hole_cx_mils)
        cy = ctx.y_to_svg(hole_cy_mils)
        hole_attrs: list[str] = []
        if include_metadata:
            hole_attrs.extend(
                [
                    'data-primitive="pad-hole"',
                    f'data-hole-kind="{hole_kind}"',
                    f'data-hole-plating="{hole_plating}"',
                    f'data-hole-render="{hole_render}"',
                    'data-hole-owner="pad"',
                ]
            )
            if self.designator:
                escaped_designator = html.escape(self.designator)
                hole_attrs.append(f'data-pad-designator="{escaped_designator}"')
                hole_attrs.append(f'data-pad-number="{escaped_designator}"')
            hole_attrs.extend(ctx.layer_metadata_attrs(layer.value))
            hole_attrs.extend(
                ctx.relationship_metadata_attrs(
                    net_index=self.net_index,
                    component_index=self.component_index,
                )
            )
            hole_id_attr = ctx.primitive_id_attr(
                "pad",
                self,
                layer_id=layer.value,
                role="hole",
            )
            if hole_id_attr:
                hole_attrs.append(hole_id_attr)
        attr_suffix = f" {' '.join(hole_attrs)}" if hole_attrs else ""
        opacity = max(0.0, min(1.0, float(hole_opacity)))
        fill_opacity_attr = (
            f' fill-opacity="{ctx.fmt(opacity)}"' if opacity < 1.0 else ""
        )
        stroke_opacity_attr = (
            f' stroke-opacity="{ctx.fmt(opacity)}"' if opacity < 1.0 else ""
        )
        color = html.escape(hole_color)
        outline_w = max(0.01, min(float(hole_outline_width_mm), hole_diameter_mm))

        # Match IPC-2581 slot semantics: obround knockout driven by
        # hole_shape=slot and slot_size > hole_size.
        if is_slot:
            rotation_deg = -(
                float(self.slot_rotation or 0.0) + float(self.rotation or 0.0)
            )
            if hole_outline:
                x = cx - slot_length_mm * 0.5
                y = cy - hole_diameter_mm * 0.5
                attrs = [
                    f'x="{ctx.fmt(x)}"',
                    f'y="{ctx.fmt(y)}"',
                    f'width="{ctx.fmt(slot_length_mm)}"',
                    f'height="{ctx.fmt(hole_diameter_mm)}"',
                    f'rx="{ctx.fmt(hole_diameter_mm * 0.5)}"',
                    f'ry="{ctx.fmt(hole_diameter_mm * 0.5)}"',
                    'fill="none"',
                    f'stroke="{color}"',
                    f'stroke-width="{ctx.fmt(outline_w)}"',
                ]
                if opacity < 1.0:
                    attrs.append(f'stroke-opacity="{ctx.fmt(opacity)}"')
                if abs(rotation_deg) > 1e-9:
                    attrs.append(
                        f'transform="rotate({ctx.fmt(rotation_deg)} '
                        f'{ctx.fmt(cx)} {ctx.fmt(cy)})"'
                    )
                if hole_attrs:
                    attrs.extend(hole_attrs)
                return [f"<rect {' '.join(attrs)}/>"]

            half_straight_mm = (slot_length_mm - hole_diameter_mm) * 0.5
            angle = math.radians(rotation_deg)
            dx = half_straight_mm * math.cos(angle)
            dy = half_straight_mm * math.sin(angle)
            x1 = cx - dx
            y1 = cy - dy
            x2 = cx + dx
            y2 = cy + dy
            return [
                (
                    f'<path d="M {ctx.fmt(x1)} {ctx.fmt(y1)} '
                    f'L {ctx.fmt(x2)} {ctx.fmt(y2)}" '
                    f'stroke="{color}" stroke-width="{ctx.fmt(hole_diameter_mm)}" '
                    f'stroke-linecap="round" fill="none"{stroke_opacity_attr}{attr_suffix}/>'
                )
            ]

        hole_radius_mm = hole_diameter_mm * 0.5
        if hole_outline:
            return [
                (
                    f'<circle cx="{ctx.fmt(cx)}" cy="{ctx.fmt(cy)}" '
                    f'r="{ctx.fmt(hole_radius_mm)}" fill="none" stroke="{color}" '
                    f'stroke-width="{ctx.fmt(outline_w)}"{stroke_opacity_attr}{attr_suffix}/>'
                )
            ]
        return [
            (
                f'<circle cx="{ctx.fmt(cx)}" cy="{ctx.fmt(cy)}" '
                f'r="{ctx.fmt(hole_radius_mm)}" fill="{color}"{fill_opacity_attr}{attr_suffix}/>'
            )
        ]

    def _resolve_svg_target_layer(
        self,
        for_layer: PcbLayer | None,
    ) -> tuple[PcbLayer, PcbLayer] | None:
        source_layer = self._source_layer()
        layer = for_layer
        if layer is None:
            if source_layer is None:
                return None
            layer = source_layer
        return layer, source_layer

    def _custom_shape_svg_elements(
        self,
        ctx: "PcbSvgRenderContext",
        *,
        layer: PcbLayer,
        stroke: str | None,
        include_metadata: bool,
        render_holes: bool,
    ) -> list[str] | None:
        custom_layer_shape = None
        if self.custom_shape is not None:
            try:
                custom_layer_shape = self.custom_shape.get_layer_shape(int(layer.value))
            except Exception:
                custom_layer_shape = None
        if custom_layer_shape is None:
            return None

        if render_holes and self.hole_size > 0 and layer.is_copper():
            return self._hole_svg_elements(
                ctx,
                layer,
                stroke or ctx.layer_color(layer),
                include_metadata=include_metadata,
            )
        return []

    def _pad_svg_geometry_state(
        self,
        ctx: "PcbSvgRenderContext",
        *,
        layer: PcbLayer,
        source_layer: PcbLayer | None,
    ) -> dict[str, object] | None:
        use_side_expansion = False
        geometry_layer = layer
        expansion_iu = 0
        if layer.is_solder_mask() or layer.is_paste_mask():
            if self.is_through_hole or source_layer in {
                PcbLayer.TOP,
                PcbLayer.BOTTOM,
                PcbLayer.MULTI_LAYER,
            }:
                use_side_expansion = True
                geometry_layer = self._side_base_layer(layer)
                if layer.is_solder_mask():
                    expansion_iu = get_pad_mask_expansion_iu(self)
                else:
                    expansion_iu = get_pad_paste_expansion_iu(self)

        base_width_iu, base_height_iu = self._layer_size(geometry_layer)
        width_iu = base_width_iu + 2 * expansion_iu
        height_iu = base_height_iu + 2 * expansion_iu
        if width_iu <= 0 or height_iu <= 0:
            return None

        base_width_mils = self._from_internal_units(base_width_iu)
        base_height_mils = self._from_internal_units(base_height_iu)
        width_mils = self._from_internal_units(width_iu)
        height_mils = self._from_internal_units(height_iu)
        width_mm = width_mils * _MIL_TO_MM
        height_mm = height_mils * _MIL_TO_MM
        pad_cx_mils, pad_cy_mils = self.pad_center_mils(geometry_layer)

        return {
            "use_side_expansion": use_side_expansion,
            "geometry_layer": geometry_layer,
            "expansion_iu": expansion_iu,
            "base_width_mils": base_width_mils,
            "base_height_mils": base_height_mils,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "cx": ctx.x_to_svg(pad_cx_mils),
            "cy": ctx.y_to_svg(pad_cy_mils),
            "half_w": width_mm / 2.0,
            "half_h": height_mm / 2.0,
            "shape": self._layer_shape(geometry_layer),
            "rotation": -float(self.rotation or 0.0),
        }

    def _pad_svg_metadata_attrs(
        self,
        ctx: "PcbSvgRenderContext",
        *,
        layer: PcbLayer,
        include_metadata: bool,
    ) -> list[str]:
        if not include_metadata:
            return []

        meta_attrs = ['data-primitive="pad"']
        meta_attrs.extend(ctx.layer_metadata_attrs(layer.value))
        if self.designator:
            escaped_designator = html.escape(self.designator)
            meta_attrs.append(f'data-pad-designator="{escaped_designator}"')
            meta_attrs.append(f'data-pad-number="{escaped_designator}"')
        meta_attrs.extend(
            ctx.relationship_metadata_attrs(
                net_index=self.net_index,
                component_index=self.component_index,
            )
        )
        element_id_attr = ctx.primitive_id_attr(
            "pad",
            self,
            layer_id=layer.value,
            role="main",
        )
        if element_id_attr:
            meta_attrs.append(element_id_attr)
        return meta_attrs

    def _build_pad_svg_elements(
        self,
        ctx: "PcbSvgRenderContext",
        *,
        color: str,
        meta_attrs: list[str],
        geometry: dict[str, object],
    ) -> list[str]:
        cx = float(geometry["cx"])
        cy = float(geometry["cy"])
        half_w = float(geometry["half_w"])
        half_h = float(geometry["half_h"])
        width_mm = float(geometry["width_mm"])
        height_mm = float(geometry["height_mm"])
        shape = int(geometry["shape"])
        rotation = float(geometry["rotation"])
        geometry_layer = geometry["geometry_layer"]
        base_width_mils = float(geometry["base_width_mils"])
        base_height_mils = float(geometry["base_height_mils"])
        use_side_expansion = bool(geometry["use_side_expansion"])
        expansion_iu = int(geometry["expansion_iu"])

        if shape == PadShape.CIRCLE:
            transform = ""
            if abs(rotation) > 1e-9:
                transform = f' transform="rotate({ctx.fmt(rotation)} {ctx.fmt(cx)} {ctx.fmt(cy)})"'
            if abs(width_mm - height_mm) > 1e-9:
                radius_mm = min(width_mm, height_mm) * 0.5
                return [
                    f'<rect x="{ctx.fmt(cx - half_w)}" y="{ctx.fmt(cy - half_h)}" '
                    f'width="{ctx.fmt(width_mm)}" height="{ctx.fmt(height_mm)}" '
                    f'rx="{ctx.fmt(radius_mm)}" ry="{ctx.fmt(radius_mm)}" '
                    f'fill="{html.escape(color)}"{transform} {" ".join(meta_attrs)}/>'
                ]
            return [
                f'<ellipse cx="{ctx.fmt(cx)}" cy="{ctx.fmt(cy)}" '
                f'rx="{ctx.fmt(half_w)}" ry="{ctx.fmt(half_h)}" '
                f'fill="{html.escape(color)}"{transform} {" ".join(meta_attrs)}/>'
            ]

        if shape == PadShape.RECTANGLE:
            transform = ""
            if abs(rotation) > 1e-9:
                transform = f' transform="rotate({ctx.fmt(rotation)} {ctx.fmt(cx)} {ctx.fmt(cy)})"'
            return [
                f'<rect x="{ctx.fmt(cx - half_w)}" y="{ctx.fmt(cy - half_h)}" '
                f'width="{ctx.fmt(width_mm)}" height="{ctx.fmt(height_mm)}" '
                f'fill="{html.escape(color)}"{transform} {" ".join(meta_attrs)}/>'
            ]

        if shape == PadShape.OCTAGONAL:
            points = self._octagon_points(cx, cy, half_w, half_h, rotation)
            point_str = " ".join(f"{ctx.fmt(px)},{ctx.fmt(py)}" for px, py in points)
            return [
                f'<polygon points="{point_str}" fill="{html.escape(color)}" {" ".join(meta_attrs)}/>'
            ]

        if shape == PadShape.ROUNDED_RECTANGLE:
            radius_mils = self._layer_corner_radius_mils(
                geometry_layer,
                base_width_mils,
                base_height_mils,
            )
            if use_side_expansion and expansion_iu != 0:
                radius_mils = max(
                    radius_mils + self._from_internal_units(expansion_iu), 0.0
                )
            radius_mm = max(radius_mils * _MIL_TO_MM, 0.0)
            transform = ""
            if abs(rotation) > 1e-9:
                transform = f' transform="rotate({ctx.fmt(rotation)} {ctx.fmt(cx)} {ctx.fmt(cy)})"'
            return [
                f'<rect x="{ctx.fmt(cx - half_w)}" y="{ctx.fmt(cy - half_h)}" '
                f'width="{ctx.fmt(width_mm)}" height="{ctx.fmt(height_mm)}" '
                f'rx="{ctx.fmt(radius_mm)}" ry="{ctx.fmt(radius_mm)}" '
                f'fill="{html.escape(color)}"{transform} {" ".join(meta_attrs)}/>'
            ]

        return [
            f'<ellipse cx="{ctx.fmt(cx)}" cy="{ctx.fmt(cy)}" '
            f'rx="{ctx.fmt(half_w)}" ry="{ctx.fmt(half_h)}" '
            f'fill="{html.escape(color)}" {" ".join(meta_attrs)}/>'
        ]

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | None = None,
        render_holes: bool = True,
    ) -> list[str]:
        """
        Render pad geometry to SVG for a specific layer context.

        Args:
            ctx: PCB SVG render context.
            stroke: Optional fill/stroke color override.
            include_metadata: Include data-* metadata attributes.
            for_layer: Target render layer.
        """
        if ctx is None:
            return []

        layer_info = self._resolve_svg_target_layer(for_layer)
        if layer_info is None:
            return []
        layer, source_layer = layer_info

        if not self._should_render_on_layer(
            layer
        ) and not self._should_force_svg_copper_render(layer):
            return []

        custom_shape_elements = self._custom_shape_svg_elements(
            ctx,
            layer=layer,
            stroke=stroke,
            include_metadata=include_metadata,
            render_holes=render_holes,
        )
        if custom_shape_elements is not None:
            return custom_shape_elements

        geometry = self._pad_svg_geometry_state(
            ctx,
            layer=layer,
            source_layer=source_layer,
        )
        if geometry is None:
            return []

        color = stroke or ctx.layer_color(layer)
        meta_attrs = self._pad_svg_metadata_attrs(
            ctx,
            layer=layer,
            include_metadata=include_metadata,
        )
        elements = self._build_pad_svg_elements(
            ctx,
            color=color,
            meta_attrs=meta_attrs,
            geometry=geometry,
        )

        if render_holes and layer.is_copper():
            elements.extend(
                self._hole_knockout_svg_elements(
                    ctx,
                    layer,
                    include_metadata=include_metadata,
                )
            )

        return elements

    def __repr__(self) -> str:
        pad_type = "TH" if self.is_through_hole else "SMT"
        return (
            f"<AltiumPcbPad '{self.designator}' {pad_type} "
            f"layer={self.layer} "
            f"pos=({self.x_mils:.2f}, {self.y_mils:.2f}) "
            f"size=({self.width_mils:.2f}x{self._from_internal_units(self.height):.2f})mil>"
        )
