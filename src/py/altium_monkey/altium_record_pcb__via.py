"""
Parse PCB via primitive records.
"""

import html
import logging
import struct
from typing import TYPE_CHECKING

from .altium_pcb_enums import PcbViaMode
from .altium_pcb_mask_paste_rules import get_via_mask_expansion_iu
from .altium_record_types import PcbGraphicalObject, PcbLayer, PcbRecordType

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext


_MIL_TO_MM = 0.0254
_VIA_SUBRECORD_DEFAULT_LENGTH = 321
_STACKCR_PCT_REL_OFFSETS = (0, 3, 6, 9, 12, 15, 18, 21)
_STACKCR_USE_PERCENT_REL_OFFSETS = (1, 4, 7, 10, 16, 19)
_STACKCR_SIZE_REL_OFFSETS = (2, 5, 8, 11, 14, 17, 20)

log = logging.getLogger(__name__)


def _write_u8(content: bytearray, offset: int, value: int, *, u8: callable) -> None:
    if 0 <= offset < len(content):
        content[offset] = u8(value)


def _write_i32(content: bytearray, offset: int, value: int) -> None:
    if 0 <= offset and offset + 4 <= len(content):
        struct.pack_into("<i", content, offset, int(value))


def _write_bytes(content: bytearray, offset: int, value: bytes) -> None:
    if offset >= len(content):
        return
    raw = bytes(value or b"")
    end = min(len(content), offset + len(raw))
    if end > offset:
        content[offset:end] = raw[: end - offset]


class AltiumPcbVia(PcbGraphicalObject):
    """
    PCB via primitive record.

    Represents a via (through-hole or blind/buried) on PCB.

    Attributes:
        x: X position (internal units, 10k/mil)
        y: Y position (internal units)
        diameter: Via pad diameter (internal units)
        hole_size: Drill hole diameter (internal units)
        layer_start: Start layer (1=TOP, 32=BOTTOM)
        layer_end: End layer
        net_index: Links to net (uint16, 0xFFFF=unlinked)

        # Linkage
        polygon_index: Polygon pour linkage (uint16, 0xFFFF=unlinked)
        component_index: Component linkage (uint16, None=free primitive) [from PcbPrimitive]

        # Plane connection
        plane_connect_style: Plane connection style (0=NoConnect, 1=Relief, 2=Direct)

        # Flags
        is_locked: Via cannot be moved
        is_tent_top: Apply soldermask tent on top
        is_tent_bottom: Apply soldermask tent on bottom
        is_test_fab_top: Test/fabrication point on top
        is_test_fab_bottom: Test/fabrication point on bottom
        is_keepout: Via is marked as keepout
        is_assy_testpoint_top: Assembly testpoint on top
        is_assy_testpoint_bottom: Assembly testpoint on bottom
        is_selected: Via selected-state flag
        is_polygon_outline: Via polygon-outline flag

        # Thermal relief (if present)
        thermal_relief_airgap: Thermal relief air gap width
        thermal_relief_conductorcount: Number of thermal relief conductors
        thermal_relief_conductorwidth: Width of thermal relief conductors

        # Soldermask
        soldermask_expansion_front: Soldermask expansion on front
        soldermask_expansion_back: Soldermask expansion on back
        solder_mask_expansion_mode: Mask expansion mode (0=NoMask, 1=Rule, 2=Manual)
        paste_mask_expansion_mode: Mask expansion mode (0=NoMask, 1=Rule, 2=Manual)
        soldermask_expansion_manual: Derived bool (True when mode==Manual)
        soldermask_expansion_linked: Soldermask expansions are linked
        soldermask_expansion_from_hole_edge: Expansion measured from hole edge flag

        # Power plane fields
        power_plane_relief_expansion: Plane thermal relief expansion (IU)
        power_plane_clearance: Plane clearance distance (IU)
        paste_mask_expansion: Paste mask expansion (IU, always 0 for VIAs)

        # Advanced
        via_mode: Via stack mode (SIMPLE, LOCAL_STACK, EXTERNAL_STACK)
        diameter_by_layer: Diameter for each layer (32 layers)
        external_stack_entries: EXTERNAL stack table entries
            (layer_id, size_on_layer, entry_state_raw)
            NOTE: AD25 normalizes entry_state_raw like a signed boolean on save:
              1..127 -> 1, 0/128..255 -> 0.
        external_stack_marker: AD25 canonical marker dword after EXTERNAL table entries
            (observed `0x0000002A` on native save).
        unique_id_bytes: 16-byte per-via identity blob in tail zone
        tail_signature_bytes: 16-byte blob between unique_id_bytes and tolerances
        union_index: Union grouping index byte
        drill_layer_pair_type: Drill pair type enum byte
        pos_tolerance: Positive position tolerance
        neg_tolerance: Negative position tolerance
        stackcr_pct_tokens: Raw StackCR percent lane bytes from VIA tail zone.
            Maps to native `IPCB_Pad4.Get/SetState_StackCRPct(OnLayer/ExOnLayer)` routing.
        stackcr_use_percent_tokens: Raw StackCR use-percent lane bytes from VIA tail zone.
            Non-zero token semantics route through `Get/SetState_StackCRUsePercentOnLayer`.
            Note: tail byte `+13` is not included here; it is owned separately as
            `drill_layer_pair_type`.
        stackcr_size_tokens: Raw StackCR size lane bytes from VIA tail zone.
            Maps to native `Get/SetState_StackCRSizeOnLayer` routing.
    """

    def __init__(self) -> None:
        super().__init__()
        self.diameter: int = 0  # Internal units
        self.hole_size: int = 0  # Internal units
        self.layer_start: int = 1  # TOP
        self.layer_end: int = 32  # BOTTOM

        # Flags
        self.is_locked: bool = False
        self.is_keepout: bool = False
        self.is_tent_top: bool = False
        self.is_tent_bottom: bool = False
        self.is_test_fab_top: bool = False
        self.is_test_fab_bottom: bool = False
        self.is_assy_testpoint_top: bool = False
        self.is_assy_testpoint_bottom: bool = False
        self.is_selected: bool = False
        self.is_polygon_outline: bool = False

        # Thermal relief
        self.thermal_relief_airgap: int = 0
        self.thermal_relief_conductorcount: int = 0
        self.thermal_relief_conductorwidth: int = 0

        # Soldermask / mask expansion modes (byte enum)
        # 0=eMaskExpansionMode_NoMask, 1=eMaskExpansionMode_Rule, 2=eMaskExpansionMode_Manual
        self.soldermask_expansion_front: int = 0
        self.soldermask_expansion_back: int = 0
        self.solder_mask_expansion_mode: int = 0  # Offset 66: mask expansion mode
        self.paste_mask_expansion_mode: int = 0  # Offset 59: mask expansion mode
        self.soldermask_expansion_linked: bool = False
        self.soldermask_expansion_from_hole_edge: bool = False
        self._has_soldermask_expansion_front: bool = False
        self._has_soldermask_expansion_back: bool = False

        # Advanced
        self.via_mode: int = PcbViaMode.SIMPLE
        self.diameter_by_layer: list[int] = [0] * 32
        self.external_stack_entries: list[tuple[int, int, int]] = []
        self.external_stack_marker: int = 0x0000002A
        self.unique_id_bytes: bytes = b"\x00" * 16
        self.tail_signature_bytes: bytes = b"\x00" * 16
        self.is_pad_removed: list[bool] = [False] * 32
        self.union_index: int = 0
        self.drill_layer_pair_type: int = 0
        self.pos_tolerance: int = 0
        self.neg_tolerance: int = 0
        self.backdrill_params: dict[str, object] = {}
        self.counterhole_params: dict[str, object] = {}
        self._subrecord_length: int = _VIA_SUBRECORD_DEFAULT_LENGTH
        self._raw_subrecord1_content: bytes | None = None
        self._tail_layout_shift: int = 0
        self._soldermask_from_hole_edge_offset: int = 258
        self._unique_id_offset: int = 259
        self._tail_signature_offset: int = 275
        self._pos_tolerance_offset: int = 291
        self._neg_tolerance_offset: int = 295
        self._tail_start_offset: int = 299
        self._drill_layer_pair_type_offset: int = 312
        self._external_stack_entry_count: int = 0
        self._external_stack_entry_stride: int = 0

        # Offsets 5-6: Polygon pour linkage (uint16, 0xFFFF=unlinked; VIAs never in polygon)
        self.polygon_index: int = 0xFFFF
        # Offsets 9-12: Always 0xFFFFFFFF for VIAs (unused association slots)
        self._reserved_9_12: bytes = b"\xff" * 4
        # Offset 31: Plane connection style enum
        # 0=ePlaneNoConnect, 1=ePlaneReliefConnect, 2=ePlaneDirectConnect
        self.plane_connect_style: int = 0
        # Offset 37: Confirmed padding (always 0 in corpus)
        self._padding_37: int = 0x00
        # Offsets 42-45: Power plane relief expansion (int32, IU)
        self.power_plane_relief_expansion: int = 0
        # Offsets 46-49: Power plane clearance (int32, IU)
        self.power_plane_clearance: int = 0
        # Offsets 50-53: Paste mask expansion (int32, always 0 for VIAs)
        self.paste_mask_expansion: int = 0

        # Preserved raw flag bytes for round-trip fidelity
        self._raw_flags1: int = 0x00
        self._raw_flags2: int = 0x00

        # Offset 58: Internal plane connection bitmask (low byte of InternalPlanes)
        # Each bit = one internal plane layer. Corpus: 0=no planes, 0xF9/0xCF/0xFF = various.
        self.cache_planes: int = 0x00
        # Offsets 60-64, 67-68: cache validity bytes (0=Invalid, 1=Valid, 2=Manual)
        # These indicate whether each cached property is from rules or manually overridden.
        self.cache_valid_60: int = 0  # Solder/paste validity (0/1 split ~60/40)
        self.cache_valid_61: int = 0  # Almost always Valid(1)
        self.cache_valid_62: int = 0  # Almost always Valid(1)
        self.cache_valid_63: int = 0  # Almost always Valid(1)
        self.cache_valid_64: int = 0  # Almost always Valid(1)
        self._padding_65: int = 0x00  # Offset 65: always 0
        self.cache_valid_67: int = 0  # Almost always Valid(1)
        self.cache_valid_68: int = 0  # Cache validity (0/1 split ~60/40)
        self._padding_69: int = 0x00  # Offset 69: always 0
        self._raw_union_index_byte: int = 0x00
        # Offset 71: cache validity byte (0=Invalid, 1=Valid, 2=Manual)
        self.cache_valid_71: int = 0
        # Offset 72: Cache flags byte (0 or 0x10 in corpus)
        self.cache_flags_72: int = 0
        self._padding_73: int = 0x00  # Offset 73: always 0
        # Offsets 203-206: Invariant across entire corpus [0x0F, 0x00, 0x03, 0x01]
        self._invariant_203_206: bytes = bytes([0x0F, 0x00, 0x03, 0x01])
        self._raw_assy_testpoint_top_byte: int = 0x00
        self._raw_assy_testpoint_bottom_byte: int = 0x00
        self._raw_soldermask_linked_byte: int = 0x00
        self._raw_soldermask_from_hole_edge_byte: int = 0x00
        self._raw_drill_layer_pair_type_byte: int = 0x00
        self._reserved_246_pre_tolerance: bytes = b"\x00" * 45
        self._tail_299_plus: bytes = b""
        self.stackcr_pct_tokens: list[int] = [0] * len(_STACKCR_PCT_REL_OFFSETS)
        self.stackcr_use_percent_tokens: list[int] = [0] * len(
            _STACKCR_USE_PERCENT_REL_OFFSETS
        )
        self.stackcr_size_tokens: list[int] = [0] * len(_STACKCR_SIZE_REL_OFFSETS)

    @staticmethod
    def _decode_external_stack_entries(
        payload: bytes,
        count: int,
        stride: int,
    ) -> list[tuple[int, int, int]]:
        """
        Decode EXTERNAL stack entries as (layer_id, size_on_layer, entry_state_raw).
        """
        count_int = int(count)
        stride_int = int(stride)
        if count_int <= 0:
            return []
        if stride_int < 9:
            return []

        entries: list[tuple[int, int, int]] = []
        entry_start = 254
        for idx in range(count_int):
            off = entry_start + idx * stride_int
            if off + 9 > len(payload):
                break
            layer_id = int(struct.unpack("<I", payload[off : off + 4])[0])
            size_on_layer = int(struct.unpack("<i", payload[off + 4 : off + 8])[0])
            entry_state_raw = int(payload[off + 8])
            entries.append((layer_id, size_on_layer, entry_state_raw))
        return entries

    @property
    def stackcr_use_percent_flags(self) -> tuple[bool, ...]:
        """
        Return StackCR use-percent lane booleans derived from raw lane tokens.
        """
        return tuple(bool(int(v)) for v in self.stackcr_use_percent_tokens)  # type: ignore[return-value]

    # -- Reserved-field properties for audit and analysis scripts --

    @property
    def _reserved_0(self) -> int:
        """
        Serialized view over the layer byte at offset 0.
        """
        return int(self.layer)

    @_reserved_0.setter
    def _reserved_0(self, value: int) -> None:
        self.layer = int(value)

    @property
    def _reserved_5_12(self) -> bytes:
        """
        Rebuild bytes 5..12 from promoted fields.
        """
        comp_idx = (
            0xFFFF
            if self.component_index is None
            else min(0xFFFF, int(self.component_index))
        )
        return (
            struct.pack("<H", min(0xFFFF, int(self.polygon_index)))
            + struct.pack("<H", comp_idx)
            + self._fit_bytes(self._reserved_9_12, 4)
        )

    @_reserved_5_12.setter
    def _reserved_5_12(self, value: bytes) -> None:
        raw = bytes(value or b"\xff" * 8)
        if len(raw) >= 2:
            self.polygon_index = struct.unpack("<H", raw[0:2])[0]
        if len(raw) >= 4:
            comp_raw = struct.unpack("<H", raw[2:4])[0]
            self.component_index = None if comp_raw == 0xFFFF else int(comp_raw)
        if len(raw) >= 8:
            self._reserved_9_12 = bytes(raw[4:8])

    @property
    def _reserved_31(self) -> int:
        """
        Serialized view over plane_connect_style at offset 31.
        """
        return int(self.plane_connect_style)

    @_reserved_31.setter
    def _reserved_31(self, value: int) -> None:
        self.plane_connect_style = int(value)

    @property
    def _reserved_37(self) -> int:
        """
        Serialized view over the padding byte at offset 37.
        """
        return int(self._padding_37)

    @_reserved_37.setter
    def _reserved_37(self, value: int) -> None:
        self._padding_37 = int(value)

    @property
    def _reserved_42_53(self) -> bytes:
        """
        Rebuild bytes 42..53 from promoted fields.
        """
        return (
            struct.pack("<i", int(self.power_plane_relief_expansion))
            + struct.pack("<i", int(self.power_plane_clearance))
            + struct.pack("<i", int(self.paste_mask_expansion))
        )

    @_reserved_42_53.setter
    def _reserved_42_53(self, value: bytes) -> None:
        raw = bytes(value or b"\x00" * 12)
        if len(raw) >= 4:
            self.power_plane_relief_expansion = struct.unpack("<i", raw[0:4])[0]
        if len(raw) >= 8:
            self.power_plane_clearance = struct.unpack("<i", raw[4:8])[0]
        if len(raw) >= 12:
            self.paste_mask_expansion = struct.unpack("<i", raw[8:12])[0]

    @property
    def _reserved_58_65(self) -> bytes:
        """
        Rebuild bytes 58..65 from promoted fields.
        """
        return bytes(
            [
                self._u8(self.cache_planes),
                self._u8(self.paste_mask_expansion_mode),
                self._u8(self.cache_valid_60),
                self._u8(self.cache_valid_61),
                self._u8(self.cache_valid_62),
                self._u8(self.cache_valid_63),
                self._u8(self.cache_valid_64),
                self._u8(self._padding_65),
            ]
        )

    @_reserved_58_65.setter
    def _reserved_58_65(self, value: bytes) -> None:
        raw = bytes(value or b"\x00" * 8)
        if len(raw) >= 1:
            self.cache_planes = int(raw[0])
        if len(raw) >= 2:
            self.paste_mask_expansion_mode = int(raw[1])
        if len(raw) >= 3:
            self.cache_valid_60 = int(raw[2])
        if len(raw) >= 4:
            self.cache_valid_61 = int(raw[3])
        if len(raw) >= 5:
            self.cache_valid_62 = int(raw[4])
        if len(raw) >= 6:
            self.cache_valid_63 = int(raw[5])
        if len(raw) >= 7:
            self.cache_valid_64 = int(raw[6])
        if len(raw) >= 8:
            self._padding_65 = int(raw[7])

    @property
    def _raw_soldermask_manual_byte(self) -> int:
        """
        Serialized view over solder_mask_expansion_mode at offset 66.
        """
        return int(self.solder_mask_expansion_mode)

    @_raw_soldermask_manual_byte.setter
    def _raw_soldermask_manual_byte(self, value: int) -> None:
        self.solder_mask_expansion_mode = int(value)

    @property
    def soldermask_expansion_manual(self) -> bool:
        """
        Derived: True when solder_mask_expansion_mode == 2 (Manual).
        """
        return int(self.solder_mask_expansion_mode) == 2

    @soldermask_expansion_manual.setter
    def soldermask_expansion_manual(self, value: bool) -> None:
        if value:
            self.solder_mask_expansion_mode = 2  # eMaskExpansionMode_Manual
        elif int(self.solder_mask_expansion_mode) == 2:
            self.solder_mask_expansion_mode = 1  # Fall back to Rule

    @property
    def _reserved_67_69(self) -> bytes:
        """
        Rebuild bytes 67..69 from promoted fields.
        """
        return bytes(
            [
                self._u8(self.cache_valid_67),
                self._u8(self.cache_valid_68),
                self._u8(self._padding_69),
            ]
        )

    @_reserved_67_69.setter
    def _reserved_67_69(self, value: bytes) -> None:
        raw = bytes(value or b"\x00" * 3)
        if len(raw) >= 1:
            self.cache_valid_67 = int(raw[0])
        if len(raw) >= 2:
            self.cache_valid_68 = int(raw[1])
        if len(raw) >= 3:
            self._padding_69 = int(raw[2])

    @property
    def _reserved_71_73(self) -> bytes:
        """
        Rebuild bytes 71..73 from promoted fields.
        """
        return bytes(
            [
                self._u8(self.cache_valid_71),
                self._u8(self.cache_flags_72),
                self._u8(self._padding_73),
            ]
        )

    @_reserved_71_73.setter
    def _reserved_71_73(self, value: bytes) -> None:
        raw = bytes(value or b"\x00" * 3)
        if len(raw) >= 1:
            self.cache_valid_71 = int(raw[0])
        if len(raw) >= 2:
            self.cache_flags_72 = int(raw[1])
        if len(raw) >= 3:
            self._padding_73 = int(raw[2])

    @property
    def _reserved_203_206(self) -> bytes:
        """
        Alias over _invariant_203_206.
        """
        return bytes(self._invariant_203_206 or bytes([0x0F, 0x00, 0x03, 0x01]))

    @_reserved_203_206.setter
    def _reserved_203_206(self, value: bytes) -> None:
        self._invariant_203_206 = bytes(value or bytes([0x0F, 0x00, 0x03, 0x01]))

    @staticmethod
    def _extract_tail_tokens(
        tail_bytes: bytes, rel_offsets: tuple[int, ...]
    ) -> list[int]:
        """
        Extract u8 tokens from tail-relative offsets, defaulting to 0 when absent.
        """
        values: list[int] = [0] * len(rel_offsets)
        raw = bytes(tail_bytes or b"")
        for idx, rel in enumerate(rel_offsets):
            rel_int = int(rel)
            if 0 <= rel_int < len(raw):
                values[idx] = int(raw[rel_int])
        return values

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB via record discriminator.
        """
        return PcbRecordType.VIA

    @property
    def diameter_mils(self) -> float:
        """
        Via diameter in mils.
        """
        return self.diameter / 10000.0

    @property
    def hole_size_mils(self) -> float:
        """
        Hole size in mils.
        """
        return self.hole_size / 10000.0

    @property
    def x_mils(self) -> float:
        """
        X position in mils.
        """
        return self.x / 10000.0

    @property
    def y_mils(self) -> float:
        """
        Y position in mils.
        """
        return self.y / 10000.0

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse VIA record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short
        """
        if len(data) < offset + 1:
            raise ValueError("Data too short for VIA record")

        cursor = offset

        # Verify type byte
        type_byte = data[cursor]
        if type_byte != PcbRecordType.VIA:
            raise ValueError(
                f"Invalid VIA type byte: 0x{type_byte:02X} "
                f"(expected 0x{int(PcbRecordType.VIA):02X})"
            )
        cursor += 1

        # SubRecord 1: Main geometry
        cursor += self._parse_subrecord1(data, cursor)

        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()
        return cursor - offset

    def _parse_subrecord1(self, data: bytes, offset: int) -> int:
        """
        Parse SubRecord 1 (main geometry).
        """
        cursor = offset

        if len(data) < cursor + 4:
            raise ValueError("SubRecord 1: not enough data for length field")

        subrecord_length = struct.unpack("<I", data[cursor : cursor + 4])[0]
        cursor += 4
        subrecord_start = cursor
        subrecord_end = cursor + subrecord_length

        if len(data) < subrecord_end:
            raise ValueError(
                f"SubRecord 1: truncated, expected {subrecord_length} bytes, got {len(data) - cursor}"
            )
        if subrecord_length < 31:
            raise ValueError(f"SubRecord 1: too short ({subrecord_length} bytes)")

        payload = data[subrecord_start:subrecord_end]
        self._subrecord_length = int(subrecord_length)
        self._raw_subrecord1_content: bytes = bytes(payload)

        self.layer = int(payload[0])  # Always 74 (MULTI_LAYER) for VIAs
        self._raw_flags1 = int(payload[1])
        self._raw_flags2 = int(payload[2])
        self.is_test_fab_top = (self._raw_flags1 & 0x80) != 0
        self.is_tent_bottom = (self._raw_flags1 & 0x40) != 0
        self.is_tent_top = (self._raw_flags1 & 0x20) != 0
        self.is_polygon_outline = (self._raw_flags1 & 0x02) != 0
        self.is_selected = (self._raw_flags1 & 0x01) != 0
        self.is_locked = (self._raw_flags1 & 0x04) == 0  # Note: inverted logic
        self.is_test_fab_bottom = (self._raw_flags2 & 0x01) != 0
        self.is_keepout = (self._raw_flags2 & 0x02) != 0
        self.net_index = struct.unpack("<H", payload[3:5])[0]
        self.polygon_index = struct.unpack("<H", payload[5:7])[0]
        comp_idx_raw = struct.unpack("<H", payload[7:9])[0]
        self.component_index = None if comp_idx_raw == 0xFFFF else int(comp_idx_raw)
        self._reserved_9_12 = bytes(payload[9:13])
        self.x = struct.unpack("<i", payload[13:17])[0]
        self.y = struct.unpack("<i", payload[17:21])[0]
        self.diameter = struct.unpack("<i", payload[21:25])[0]
        self.hole_size = struct.unpack("<i", payload[25:29])[0]
        self.layer_start = int(payload[29])
        self.layer_end = int(payload[30])

        # Reset optional tails before conditionally populating.
        self._has_soldermask_expansion_front = False
        self._has_soldermask_expansion_back = False
        self.soldermask_expansion_from_hole_edge = False
        self.drill_layer_pair_type = 0
        self.union_index = 0
        self._tail_layout_shift = 0
        (
            self._soldermask_from_hole_edge_offset,
            self._pos_tolerance_offset,
            self._neg_tolerance_offset,
            self._tail_start_offset,
            self._drill_layer_pair_type_offset,
        ) = self._tail_layout_offsets(0)
        self._unique_id_offset = self._soldermask_from_hole_edge_offset + 1
        self._tail_signature_offset = self._unique_id_offset + 16
        self._invariant_203_206 = bytes([0x0F, 0x00, 0x03, 0x01])
        self._raw_assy_testpoint_top_byte = 0x00
        self._raw_assy_testpoint_bottom_byte = 0x00
        self._raw_soldermask_linked_byte = 0x00
        self._raw_soldermask_from_hole_edge_byte = 0x00
        self._raw_drill_layer_pair_type_byte = 0x00
        self.cache_planes = 0x00
        self.paste_mask_expansion_mode = 0
        self.cache_valid_60 = 0
        self.cache_valid_61 = 0
        self.cache_valid_62 = 0
        self.cache_valid_63 = 0
        self.cache_valid_64 = 0
        self._padding_65 = 0x00
        self.cache_valid_67 = 0
        self.cache_valid_68 = 0
        self._padding_69 = 0x00
        self._raw_union_index_byte = 0x00
        self.cache_valid_71 = 0
        self.cache_flags_72 = 0
        self._padding_73 = 0x00
        self._reserved_246_pre_tolerance = b"\x00" * 45
        self.plane_connect_style = 0
        self._padding_37 = 0x00
        self.power_plane_relief_expansion = 0
        self.power_plane_clearance = 0
        self.paste_mask_expansion = 0
        self.solder_mask_expansion_mode = 0
        self._tail_299_plus = b""
        self.stackcr_pct_tokens = [0] * len(_STACKCR_PCT_REL_OFFSETS)
        self.stackcr_use_percent_tokens = [0] * len(_STACKCR_USE_PERCENT_REL_OFFSETS)
        self.stackcr_size_tokens = [0] * len(_STACKCR_SIZE_REL_OFFSETS)
        self.unique_id_bytes = b"\x00" * 16
        self.tail_signature_bytes = b"\x00" * 16
        self._external_stack_entry_count = 0
        self._external_stack_entry_stride = 0
        self.external_stack_entries = []
        self.external_stack_marker = 0x0000002A

        if subrecord_length > 74:
            self.plane_connect_style = int(payload[31])
            self.thermal_relief_airgap = struct.unpack("<i", payload[32:36])[0]
            self.thermal_relief_conductorcount = int(payload[36])
            self._padding_37 = int(payload[37])
            self.thermal_relief_conductorwidth = struct.unpack("<i", payload[38:42])[0]
            self.power_plane_relief_expansion = struct.unpack("<i", payload[42:46])[0]
            self.power_plane_clearance = struct.unpack("<i", payload[46:50])[0]
            self.paste_mask_expansion = struct.unpack("<i", payload[50:54])[0]
            self.soldermask_expansion_front = struct.unpack("<i", payload[54:58])[0]
            self._has_soldermask_expansion_front = True
            # Offsets 58-65: cache planes, paste mask mode, cache validity bytes
            self.cache_planes = int(payload[58])
            self.paste_mask_expansion_mode = int(payload[59])
            self.cache_valid_60 = int(payload[60])
            self.cache_valid_61 = int(payload[61])
            self.cache_valid_62 = int(payload[62])
            self.cache_valid_63 = int(payload[63])
            self.cache_valid_64 = int(payload[64])
            self._padding_65 = int(payload[65])
            # Offset 66: mask expansion mode (0=NoMask, 1=Rule, 2=Manual)
            self.solder_mask_expansion_mode = int(payload[66])
            # Offsets 67-69: cache validity + padding
            self.cache_valid_67 = int(payload[67])
            self.cache_valid_68 = int(payload[68])
            self._padding_69 = int(payload[69])
            # Offset 70: union index
            self._raw_union_index_byte = int(payload[70])
            self.union_index = int(self._raw_union_index_byte)
            # Offsets 71-73: cache validity, flags, padding
            self.cache_valid_71 = int(payload[71])
            self.cache_flags_72 = int(payload[72])
            self._padding_73 = int(payload[73])
            self.via_mode = int(payload[74])
            for i in range(32):
                start = 75 + i * 4
                end = start + 4
                if end > subrecord_length:
                    break
                self.diameter_by_layer[i] = struct.unpack("<i", payload[start:end])[0]

        # Stack table at offset 246 is universal across ALL via modes.
        # SIMPLE vias may have count=0 (marker only) or count=1 (one entry + marker).
        if subrecord_length >= 258:
            self._external_stack_entry_count = int(
                struct.unpack("<I", payload[246:250])[0]
            )
            self._external_stack_entry_stride = int(
                struct.unpack("<I", payload[250:254])[0]
            )
            self.external_stack_entries = self._decode_external_stack_entries(
                payload,
                self._external_stack_entry_count,
                self._external_stack_entry_stride,
            )
            marker_offset = 254 + (
                int(self._external_stack_entry_count)
                * int(self._external_stack_entry_stride)
            )
            if marker_offset + 4 <= subrecord_length:
                self.external_stack_marker = int(
                    struct.unpack("<I", payload[marker_offset : marker_offset + 4])[0]
                )

        self._tail_layout_shift = self._compute_tail_layout_shift(
            subrecord_length,
            self.via_mode,
            payload=payload,
        )
        (
            self._soldermask_from_hole_edge_offset,
            self._pos_tolerance_offset,
            self._neg_tolerance_offset,
            self._tail_start_offset,
            self._drill_layer_pair_type_offset,
        ) = self._tail_layout_offsets(self._tail_layout_shift)
        self._unique_id_offset = self._soldermask_from_hole_edge_offset + 1
        self._tail_signature_offset = self._unique_id_offset + 16

        if subrecord_length >= 246:
            self._invariant_203_206 = bytes(payload[203:207])
            self._raw_assy_testpoint_top_byte = int(payload[207])
            self._raw_assy_testpoint_bottom_byte = int(payload[208])
            self.is_assy_testpoint_top = (self._raw_assy_testpoint_top_byte & 0x01) != 0
            self.is_assy_testpoint_bottom = (
                self._raw_assy_testpoint_bottom_byte & 0x01
            ) != 0
            for i in range(32):
                self.is_pad_removed[i] = payload[209 + i] != 0
            self._raw_soldermask_linked_byte = int(payload[241])
            self.soldermask_expansion_linked = (
                self._raw_soldermask_linked_byte & 0x01
            ) != 0
            self.soldermask_expansion_back = struct.unpack("<i", payload[242:246])[0]
            self._has_soldermask_expansion_back = True
            mask_offset = self._soldermask_from_hole_edge_offset
            if 0 <= mask_offset < subrecord_length:
                self._raw_soldermask_from_hole_edge_byte = int(payload[mask_offset])
                self.soldermask_expansion_from_hole_edge = (
                    self._raw_soldermask_from_hole_edge_byte & 0x01
                ) != 0
            uid_offset = int(self._unique_id_offset)
            if 0 <= uid_offset and uid_offset + 16 <= subrecord_length:
                self.unique_id_bytes = bytes(payload[uid_offset : uid_offset + 16])
            sig_offset = int(self._tail_signature_offset)
            if 0 <= sig_offset and sig_offset + 16 <= subrecord_length:
                self.tail_signature_bytes = bytes(payload[sig_offset : sig_offset + 16])
            drill_offset = self._drill_layer_pair_type_offset
            if 0 <= drill_offset < subrecord_length:
                self._raw_drill_layer_pair_type_byte = int(payload[drill_offset])
                self.drill_layer_pair_type = int(self._raw_drill_layer_pair_type_byte)

        if subrecord_length >= self._tail_start_offset:
            pos_offset = self._pos_tolerance_offset
            neg_offset = self._neg_tolerance_offset
            if pos_offset > 246:
                self._reserved_246_pre_tolerance = bytes(payload[246:pos_offset])
            if pos_offset + 4 <= subrecord_length:
                self.pos_tolerance = struct.unpack(
                    "<i", payload[pos_offset : pos_offset + 4]
                )[0]
            if neg_offset + 4 <= subrecord_length:
                self.neg_tolerance = struct.unpack(
                    "<i", payload[neg_offset : neg_offset + 4]
                )[0]
            self._tail_299_plus = bytes(payload[self._tail_start_offset :])
            self.stackcr_pct_tokens = self._extract_tail_tokens(
                self._tail_299_plus,
                _STACKCR_PCT_REL_OFFSETS,
            )
            self.stackcr_use_percent_tokens = self._extract_tail_tokens(
                self._tail_299_plus,
                _STACKCR_USE_PERCENT_REL_OFFSETS,
            )
            self.stackcr_size_tokens = self._extract_tail_tokens(
                self._tail_299_plus,
                _STACKCR_SIZE_REL_OFFSETS,
            )

        cursor = subrecord_end
        return cursor - offset

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of semantically known and preserved VIA fields.
        """
        return (
            int(self.x),
            int(self.y),
            int(self.diameter),
            int(self.hole_size),
            int(self.layer_start),
            int(self.layer_end),
            int(self.net_index) if self.net_index is not None else 0xFFFF,
            bool(self.is_locked),
            bool(self.is_tent_top),
            bool(self.is_tent_bottom),
            bool(self.is_test_fab_top),
            bool(self.is_test_fab_bottom),
            bool(self.is_keepout),
            bool(self.is_assy_testpoint_top),
            bool(self.is_assy_testpoint_bottom),
            bool(self.is_selected),
            bool(self.is_polygon_outline),
            int(self.thermal_relief_airgap),
            int(self.thermal_relief_conductorcount),
            int(self.thermal_relief_conductorwidth),
            int(self.soldermask_expansion_front),
            int(self.soldermask_expansion_back),
            int(self.solder_mask_expansion_mode),
            bool(self.soldermask_expansion_linked),
            bool(self.soldermask_expansion_from_hole_edge),
            int(self.via_mode),
            tuple(int(v) for v in self.diameter_by_layer),
            tuple(bool(v) for v in self.is_pad_removed),
            int(self.union_index),
            int(self.drill_layer_pair_type),
            int(self.pos_tolerance),
            int(self.neg_tolerance),
            int(self._subrecord_length),
            int(self._tail_layout_shift),
            int(self._soldermask_from_hole_edge_offset),
            int(self._unique_id_offset),
            int(self._tail_signature_offset),
            int(self._pos_tolerance_offset),
            int(self._neg_tolerance_offset),
            int(self._tail_start_offset),
            int(self._drill_layer_pair_type_offset),
            int(self._external_stack_entry_count),
            int(self._external_stack_entry_stride),
            tuple(
                (int(a), int(b), int(c)) for (a, b, c) in self.external_stack_entries
            ),
            int(self.external_stack_marker),
            int(self.layer),
            int(self._raw_flags1),
            int(self._raw_flags2),
            int(self.polygon_index),
            self.component_index,
            bytes(self._reserved_9_12),
            int(self.plane_connect_style),
            int(self._padding_37),
            int(self.power_plane_relief_expansion),
            int(self.power_plane_clearance),
            int(self.paste_mask_expansion),
            int(self.cache_planes),
            int(self.paste_mask_expansion_mode),
            int(self.cache_valid_60),
            int(self.cache_valid_61),
            int(self.cache_valid_62),
            int(self.cache_valid_63),
            int(self.cache_valid_64),
            int(self._padding_65),
            int(self.cache_valid_67),
            int(self.cache_valid_68),
            int(self._padding_69),
            int(self._raw_union_index_byte),
            int(self.cache_valid_71),
            int(self.cache_flags_72),
            int(self._padding_73),
            bytes(self._invariant_203_206),
            int(self._raw_assy_testpoint_top_byte),
            int(self._raw_assy_testpoint_bottom_byte),
            int(self._raw_soldermask_linked_byte),
            int(self._raw_soldermask_from_hole_edge_byte),
            int(self._raw_drill_layer_pair_type_byte),
            bytes(self.unique_id_bytes),
            bytes(self.tail_signature_bytes),
            bytes(self._reserved_246_pre_tolerance),
            tuple(int(v) for v in self.stackcr_pct_tokens),
            tuple(int(v) for v in self.stackcr_use_percent_tokens),
            tuple(int(v) for v in self.stackcr_size_tokens),
            bytes(self._tail_299_plus),
        )

    @staticmethod
    def _u16_or_unlinked(value: int | None) -> int:
        """
        Encode optional uint16 index with 0xFFFF as unlinked sentinel.
        """
        if value is None:
            return 0xFFFF
        value_int = int(value)
        if value_int < 0:
            return 0xFFFF
        return min(value_int, 0xFFFF)

    @staticmethod
    def _u8(value: int) -> int:
        """
        Clamp value to uint8.
        """
        return max(0, min(255, int(value)))

    @staticmethod
    def _fit_bytes(value: bytes, length: int) -> bytes:
        """
        Return bytes fitted to a fixed length (trim or right-pad with zeros).
        """
        raw = bytes(value or b"")
        if len(raw) < length:
            return raw + (b"\x00" * (length - len(raw)))
        return raw[:length]

    @staticmethod
    def _external_shift_from_table(
        subrecord_length: int, count: int, stride: int
    ) -> int:
        """
        Return dynamic EXTERNAL tail shift from stack-entry table metadata.
        """
        count_int = int(count)
        stride_int = int(stride)
        if count_int <= 0 or count_int > 64:
            return 0
        if stride_int <= 0 or stride_int > 64:
            return 0

        shift = count_int * stride_int
        if (291 + shift + 4) > int(subrecord_length):
            return 0
        if (312 + shift) >= int(subrecord_length):
            return 0
        return shift

    @staticmethod
    def _compute_tail_layout_shift(
        subrecord_length: int,
        via_mode: int,
        *,
        payload: bytes | None = None,
    ) -> int:
        """
        Return tail layout shift for VIA records with per-layer stack entries.

        The stack table at offset 246 (count/stride/entries/marker) is universal
        across ALL via modes.  SIMPLE vias with count=1 have subrecord_length=330
        (321+9), identical structure to EXTERNAL_STACK.  Shift = count * stride.
        """
        if int(subrecord_length) >= 330:
            if payload is not None and len(payload) >= 254:
                count = int(struct.unpack("<I", payload[246:250])[0])
                stride = int(struct.unpack("<I", payload[250:254])[0])
                dynamic_shift = AltiumPcbVia._external_shift_from_table(
                    subrecord_length=subrecord_length,
                    count=count,
                    stride=stride,
                )
                if dynamic_shift > 0:
                    return dynamic_shift
            # Fallback: legacy single-entry EXTERNAL tail.
            if int(via_mode) == PcbViaMode.EXTERNAL_STACK:
                return 9
        return 0

    @staticmethod
    def _tail_layout_offsets(shift: int) -> tuple[int, int, int, int, int]:
        """
        Return (mask_from_hole_edge, pos_tol, neg_tol, tail_start, drill_pair) offsets.
        """
        base = int(shift)
        return (
            258 + base,
            291 + base,
            295 + base,
            299 + base,
            312 + base,
        )

    def serialize_to_binary(self) -> bytes:
        """
        Serialize VIA record to binary format.

        Uses raw passthrough only when no known/preserved field changed; otherwise
        emits a deterministic SubRecord payload preserving original record length
        and reserved bytes when available.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        content_length, content = self._prepare_serialization_buffer()
        (
            mask_offset,
            pos_tolerance_offset,
            neg_tolerance_offset,
            tail_start_offset,
            drill_layer_pair_type_offset,
            unique_id_offset,
            tail_signature_offset,
        ) = self._resolve_tail_layout(content_length)

        self._serialize_header_and_geometry(content)
        self._serialize_extended_thermal_block(content)

        self._serialize_pad_removal_block(content)

        # Tolerances and trailing preserved tail.
        if content_length > 246:
            pre_tol_end = min(
                content_length,
                pos_tolerance_offset if pos_tolerance_offset > 246 else content_length,
            )
            pre_tol_len = max(0, pre_tol_end - 246)
            pre_tol = bytearray(
                self._fit_bytes(self._reserved_246_pre_tolerance, pre_tol_len)
            )
            # Stack table is universal - write count/stride/entries/marker for all modes.
            if pre_tol_len >= 8:
                entries = [
                    (int(layer_id), int(size_on_layer), int(entry_state_raw))
                    for (layer_id, size_on_layer, entry_state_raw) in (
                        self.external_stack_entries or []
                    )
                ]
                table_count = int(self._external_stack_entry_count)
                if table_count <= 0:
                    table_count = len(entries)

                table_stride = int(self._external_stack_entry_stride)
                if table_count > 0:
                    if table_stride <= 0:
                        table_stride = 9
                    table_stride = max(9, table_stride)

                max_count_by_space = max(0, (pre_tol_len - 8) // max(1, table_stride))
                if table_count > max_count_by_space:
                    table_count = max_count_by_space
                if table_count > len(entries):
                    table_count = len(entries)
                if table_count < 0:
                    table_count = 0

                struct.pack_into("<I", pre_tol, 0, int(table_count))
                struct.pack_into("<I", pre_tol, 4, int(table_stride))
                for idx in range(table_count):
                    entry_off = 8 + idx * table_stride
                    if entry_off + 9 > pre_tol_len:
                        break
                    layer_id, size_on_layer, entry_state_raw = entries[idx]
                    struct.pack_into("<I", pre_tol, entry_off, layer_id & 0xFFFFFFFF)
                    struct.pack_into("<i", pre_tol, entry_off + 4, int(size_on_layer))
                    pre_tol[entry_off + 8] = self._u8(entry_state_raw)
                marker_off = 8 + (table_count * table_stride)
                if marker_off + 4 <= pre_tol_len:
                    struct.pack_into(
                        "<I",
                        pre_tol,
                        marker_off,
                        int(self.external_stack_marker) & 0xFFFFFFFF,
                    )

            _write_bytes(content, 246, bytes(pre_tol))
            if 0 <= mask_offset < content_length:
                mask_byte = int(self._raw_soldermask_from_hole_edge_byte) & ~0x01
                if self.soldermask_expansion_from_hole_edge:
                    mask_byte |= 0x01
                _write_u8(content, mask_offset, mask_byte, u8=self._u8)
            _write_bytes(
                content, unique_id_offset, self._fit_bytes(self.unique_id_bytes, 16)
            )
            _write_bytes(
                content,
                tail_signature_offset,
                self._fit_bytes(self.tail_signature_bytes, 16),
            )
            _write_i32(content, pos_tolerance_offset, int(self.pos_tolerance))
            _write_i32(content, neg_tolerance_offset, int(self.neg_tolerance))
            _write_bytes(content, tail_start_offset, self._tail_299_plus)
            for idx, rel in enumerate(_STACKCR_PCT_REL_OFFSETS):
                if idx < len(self.stackcr_pct_tokens):
                    _write_u8(
                        content,
                        tail_start_offset + int(rel),
                        int(self.stackcr_pct_tokens[idx]),
                        u8=self._u8,
                    )
            for idx, rel in enumerate(_STACKCR_USE_PERCENT_REL_OFFSETS):
                if idx < len(self.stackcr_use_percent_tokens):
                    _write_u8(
                        content,
                        tail_start_offset + int(rel),
                        int(self.stackcr_use_percent_tokens[idx]),
                        u8=self._u8,
                    )
            for idx, rel in enumerate(_STACKCR_SIZE_REL_OFFSETS):
                if idx < len(self.stackcr_size_tokens):
                    _write_u8(
                        content,
                        tail_start_offset + int(rel),
                        int(self.stackcr_size_tokens[idx]),
                        u8=self._u8,
                    )
            if 0 <= drill_layer_pair_type_offset < content_length:
                _write_u8(
                    content,
                    drill_layer_pair_type_offset,
                    int(self.drill_layer_pair_type),
                    u8=self._u8,
                )

        record = bytearray()
        record.append(0x03)  # VIA type
        record.extend(struct.pack("<I", len(content)))
        record.extend(content)
        result = bytes(record)

        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    def _prepare_serialization_buffer(self) -> tuple[int, bytearray]:
        """Return the target subrecord length and a writable content buffer."""
        content_length = int(self._subrecord_length or _VIA_SUBRECORD_DEFAULT_LENGTH)
        if content_length < 31:
            content_length = 31
        raw_sr1 = getattr(self, "_raw_subrecord1_content", None)
        if raw_sr1 and len(raw_sr1) == content_length:
            return content_length, bytearray(raw_sr1)
        return content_length, bytearray(content_length)

    def _resolve_tail_layout(
        self,
        content_length: int,
    ) -> tuple[int, int, int, int, int, int, int]:
        """Resolve the VIA tail layout offsets for the current content length."""
        shift = max(0, int(self._tail_layout_shift))
        inferred_shift = 0
        if content_length >= 330:
            table_count, table_stride = self._resolve_external_stack_table_shape()
            inferred_shift = self._external_shift_from_table(
                subrecord_length=content_length,
                count=table_count,
                stride=table_stride,
            )
            if inferred_shift <= 0 and int(self.via_mode) == PcbViaMode.EXTERNAL_STACK:
                inferred_shift = 9
        if shift == 0 and inferred_shift > 0:
            shift = inferred_shift
        (
            mask_offset,
            pos_tolerance_offset,
            neg_tolerance_offset,
            tail_start_offset,
            drill_layer_pair_type_offset,
        ) = self._tail_layout_offsets(shift)
        unique_id_offset = int(mask_offset) + 1
        tail_signature_offset = unique_id_offset + 16
        return (
            mask_offset,
            pos_tolerance_offset,
            neg_tolerance_offset,
            tail_start_offset,
            drill_layer_pair_type_offset,
            unique_id_offset,
            tail_signature_offset,
        )

    def _resolve_external_stack_table_shape(self) -> tuple[int, int]:
        """Resolve count and stride for the serialized external stack table."""
        table_count = int(self._external_stack_entry_count)
        table_stride = int(self._external_stack_entry_stride)
        if table_count <= 0 and self.external_stack_entries:
            table_count = len(self.external_stack_entries)
        if table_stride <= 0 and self.external_stack_entries:
            table_stride = 9
        if table_count > 0 and table_stride > 0:
            return table_count, table_stride
        pre = bytes(self._reserved_246_pre_tolerance or b"")
        if len(pre) >= 8:
            return (
                int(struct.unpack("<I", pre[0:4])[0]),
                int(struct.unpack("<I", pre[4:8])[0]),
            )
        return table_count, table_stride

    def _serialize_header_and_geometry(self, content: bytearray) -> None:
        """Write the fixed VIA header and geometry block."""

        def _write_u16(offset: int, value: int) -> None:
            if 0 <= offset and offset + 2 <= len(content):
                struct.pack_into("<H", content, offset, int(value))

        _write_u8(content, 0, int(self.layer), u8=self._u8)
        flags1 = int(self._raw_flags1) & ~(0x80 | 0x40 | 0x20 | 0x04 | 0x02 | 0x01)
        if self.is_test_fab_top:
            flags1 |= 0x80
        if self.is_tent_bottom:
            flags1 |= 0x40
        if self.is_tent_top:
            flags1 |= 0x20
        if self.is_polygon_outline:
            flags1 |= 0x02
        if self.is_selected:
            flags1 |= 0x01
        if not self.is_locked:
            flags1 |= 0x04
        _write_u8(content, 1, flags1, u8=self._u8)

        flags2 = int(self._raw_flags2) & ~(0x01 | 0x02)
        if self.is_test_fab_bottom:
            flags2 |= 0x01
        if self.is_keepout:
            flags2 |= 0x02
        _write_u8(content, 2, flags2, u8=self._u8)
        _write_u16(3, self._u16_or_unlinked(self.net_index))
        _write_u16(5, min(0xFFFF, int(self.polygon_index)))
        component_index = (
            0xFFFF
            if self.component_index is None
            else min(0xFFFF, int(self.component_index))
        )
        _write_u16(7, component_index)
        _write_bytes(content, 9, self._fit_bytes(self._reserved_9_12, 4))

        _write_i32(content, 13, int(self.x))
        _write_i32(content, 17, int(self.y))
        _write_i32(content, 21, int(self.diameter))
        _write_i32(content, 25, int(self.hole_size))
        _write_u8(content, 29, int(self.layer_start), u8=self._u8)
        _write_u8(content, 30, int(self.layer_end), u8=self._u8)

    def _serialize_extended_thermal_block(self, content: bytearray) -> None:
        """Write the VIA thermal, mask, and per-layer size block."""
        content_length = len(content)
        if content_length <= 74:
            return

        _write_u8(content, 31, int(self.plane_connect_style), u8=self._u8)
        _write_i32(content, 32, int(self.thermal_relief_airgap))
        _write_u8(content, 36, int(self.thermal_relief_conductorcount), u8=self._u8)
        _write_u8(content, 37, self._padding_37, u8=self._u8)
        _write_i32(content, 38, int(self.thermal_relief_conductorwidth))
        _write_i32(content, 42, int(self.power_plane_relief_expansion))
        _write_i32(content, 46, int(self.power_plane_clearance))
        _write_i32(content, 50, int(self.paste_mask_expansion))
        _write_i32(content, 54, int(self.soldermask_expansion_front))
        _write_u8(content, 58, int(self.cache_planes), u8=self._u8)
        _write_u8(content, 59, int(self.paste_mask_expansion_mode), u8=self._u8)
        _write_u8(content, 60, int(self.cache_valid_60), u8=self._u8)
        _write_u8(content, 61, int(self.cache_valid_61), u8=self._u8)
        _write_u8(content, 62, int(self.cache_valid_62), u8=self._u8)
        _write_u8(content, 63, int(self.cache_valid_63), u8=self._u8)
        _write_u8(content, 64, int(self.cache_valid_64), u8=self._u8)
        _write_u8(content, 65, int(self._padding_65), u8=self._u8)
        _write_u8(content, 66, int(self.solder_mask_expansion_mode), u8=self._u8)
        _write_u8(content, 67, int(self.cache_valid_67), u8=self._u8)
        _write_u8(content, 68, int(self.cache_valid_68), u8=self._u8)
        _write_u8(content, 69, int(self._padding_69), u8=self._u8)
        _write_u8(content, 70, int(self.union_index), u8=self._u8)
        _write_u8(content, 71, int(self.cache_valid_71), u8=self._u8)
        _write_u8(content, 72, int(self.cache_flags_72), u8=self._u8)
        _write_u8(content, 73, int(self._padding_73), u8=self._u8)
        _write_u8(content, 74, int(self.via_mode), u8=self._u8)
        for idx in range(32):
            _write_i32(content, 75 + idx * 4, int(self.diameter_by_layer[idx]))

    def _serialize_pad_removal_block(self, content: bytearray) -> None:
        """Write the pad-removal and back-mask block."""
        content_length = len(content)
        if content_length < 246:
            return

        _write_bytes(content, 203, self._fit_bytes(self._invariant_203_206, 4))
        assy_top_byte = int(self._raw_assy_testpoint_top_byte) & ~0x01
        if self.is_assy_testpoint_top:
            assy_top_byte |= 0x01
        _write_u8(content, 207, assy_top_byte, u8=self._u8)
        assy_bottom_byte = int(self._raw_assy_testpoint_bottom_byte) & ~0x01
        if self.is_assy_testpoint_bottom:
            assy_bottom_byte |= 0x01
        _write_u8(content, 208, assy_bottom_byte, u8=self._u8)
        for idx in range(32):
            _write_u8(
                content,
                209 + idx,
                0x01 if self.is_pad_removed[idx] else 0x00,
                u8=self._u8,
            )
        linked_byte = int(self._raw_soldermask_linked_byte) & ~0x01
        if self.soldermask_expansion_linked:
            linked_byte |= 0x01
        _write_u8(content, 241, linked_byte, u8=self._u8)
        _write_i32(content, 242, int(self.soldermask_expansion_back))

    def _diameter_for_layer(self, layer: PcbLayer) -> int:
        """
        Get via diameter in internal units for a specific layer.
        """
        idx = layer.value - 1
        if 0 <= idx < len(self.diameter_by_layer):
            layer_dia = int(self.diameter_by_layer[idx] or 0)
            if layer_dia > 0:
                return layer_dia
        return int(self.diameter or 0)

    def _spans_layer(self, layer: PcbLayer) -> bool:
        """
        Check whether via barrel spans the requested layer.
        """
        if not layer.is_copper():
            return False
        lo = min(int(self.layer_start), int(self.layer_end))
        hi = max(int(self.layer_start), int(self.layer_end))
        return lo <= layer.value <= hi

    def _should_render_on_layer(self, layer: PcbLayer) -> bool:
        """
        Check whether via contributes geometry on the requested layer.
        """
        if layer.is_copper():
            if not self._spans_layer(layer):
                return False
            layer_idx = layer.value - 1
            if (
                0 <= layer_idx < len(self.is_pad_removed)
                and self.is_pad_removed[layer_idx]
            ):
                return False
            return True

        lo = min(int(self.layer_start), int(self.layer_end))
        hi = max(int(self.layer_start), int(self.layer_end))
        if layer == PcbLayer.TOP_SOLDER:
            return lo == PcbLayer.TOP.value and not self.is_tent_top
        if layer == PcbLayer.BOTTOM_SOLDER:
            return hi == PcbLayer.BOTTOM.value and not self.is_tent_bottom
        return False

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
        Render via annular ring and drill hole to SVG for one layer.
        """
        if ctx is None:
            return []
        if for_layer is None:
            return []
        if not self._should_render_on_layer(for_layer):
            return []

        if for_layer.is_copper():
            diameter_iu = self._diameter_for_layer(for_layer)
        elif for_layer == PcbLayer.TOP_SOLDER:
            base_dia = int(self.diameter or self._diameter_for_layer(PcbLayer.TOP) or 0)
            diameter_iu = base_dia + 2 * get_via_mask_expansion_iu(self, "top")
        elif for_layer == PcbLayer.BOTTOM_SOLDER:
            base_dia = int(
                self.diameter or self._diameter_for_layer(PcbLayer.BOTTOM) or 0
            )
            diameter_iu = base_dia + 2 * get_via_mask_expansion_iu(self, "bottom")
        else:
            return []
        if diameter_iu <= 0:
            return []

        outer_radius_mm = max((diameter_iu / 10000.0) * _MIL_TO_MM / 2.0, 0.0)
        hole_radius_mm = 0.0
        if for_layer.is_copper():
            hole_radius_mm = max(self.hole_size_mils * _MIL_TO_MM / 2.0, 0.0)
        cx = ctx.x_to_svg(self.x_mils)
        cy = ctx.y_to_svg(self.y_mils)
        color = stroke or ctx.layer_color(for_layer)

        ring_attrs: list[str] = []
        if include_metadata:
            ring_attrs.extend(
                [
                    'data-primitive="via"',
                ]
            )
            ring_attrs.extend(ctx.layer_metadata_attrs(for_layer.value))
            ring_attrs.extend(
                ctx.relationship_metadata_attrs(
                    net_index=self.net_index,
                )
            )
            ring_id_attr = ctx.primitive_id_attr(
                "via",
                self,
                layer_id=for_layer.value,
                role="main",
            )
            if ring_id_attr:
                ring_attrs.append(ring_id_attr)

        elements = [
            f'<circle cx="{ctx.fmt(cx)}" cy="{ctx.fmt(cy)}" '
            f'r="{ctx.fmt(outer_radius_mm)}" fill="{html.escape(color)}" {" ".join(ring_attrs)}/>'
        ]

        if render_holes and for_layer.is_copper() and hole_radius_mm > 0:
            hole_attrs: list[str] = []
            if include_metadata:
                hole_attrs.extend(
                    [
                        'data-primitive="via-hole"',
                        'data-hole-kind="round"',
                        'data-hole-plating="plated"',
                        'data-hole-render="fill"',
                        'data-hole-owner="via"',
                    ]
                )
                hole_attrs.extend(ctx.layer_metadata_attrs(for_layer.value))
                hole_attrs.extend(
                    ctx.relationship_metadata_attrs(
                        net_index=self.net_index,
                    )
                )
                hole_id_attr = ctx.primitive_id_attr(
                    "via",
                    self,
                    layer_id=for_layer.value,
                    role="hole",
                )
                if hole_id_attr:
                    hole_attrs.append(hole_id_attr)
            elements.append(
                f'<circle cx="{ctx.fmt(cx)}" cy="{ctx.fmt(cy)}" '
                f'r="{ctx.fmt(hole_radius_mm)}" fill="#FFFFFF" {" ".join(hole_attrs)}/>'
            )

        return elements

    def __repr__(self) -> str:
        """
        String representation.
        """
        return (
            f"AltiumPcbVia(pos=({self.x_mils:.1f}, {self.y_mils:.1f}), "
            f"dia={self.diameter_mils:.1f}, hole={self.hole_size_mils:.1f}, "
            f"layers={self.layer_start}-{self.layer_end}, net={self.net_index})"
        )
