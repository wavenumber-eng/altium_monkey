"""
Parse PCB fill primitive records.
"""

import html
import logging
import struct
from typing import TYPE_CHECKING

from .altium_record_types import PcbGraphicalObject, PcbLayer, PcbRecordType

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext


_MIL_TO_MM = 0.0254

log = logging.getLogger(__name__)


class AltiumPcbFill(PcbGraphicalObject):
    """
    PCB fill primitive record.

    Represents a filled rectangle on PCB (copper fill, silkscreen fill).

    Attributes:
        pos1_x: First corner X position (internal units, 10k/mil)
        pos1_y: First corner Y position (internal units)
        pos2_x: Opposite corner X position (internal units)
        pos2_y: Opposite corner Y position (internal units)
        rotation: Rotation angle (degrees, around center)
        layer: Layer number (1=TOP, 32=BOTTOM, etc.)
        component_index: Links to component (uint16, 0xFFFF=unlinked)
        net_index: Links to net (uint16, 0xFFFF=unlinked)
        polygon_index: Polygon index (uint16, always 0xFFFF for fills)
        union_index: Union membership (uint32, 0xFFFFFFFF=none)
        is_locked: Locked for editing
        is_keepout: Keepout fill region
        is_polygon_outline: Part of polygon outline (always False for fills)
        user_routed: True if manually routed (flags1 bit 3)
        solder_mask_expansion: Solder mask expansion value (int32, internal units)
        paste_mask_expansion: Paste mask expansion value (uint8, always 0 in practice)
        v7_layer_id: V7 layer identifier field (uint32)
        keepout_restrictions: Keepout bitmask (bit0=Via, bit1=Track, bit2=Copper,
                              bit3=SmdPad, bit4=ThPad)
    """

    def __init__(self) -> None:
        super().__init__()
        self.pos1_x: int = 0  # Internal units (10k/mil)
        self.pos1_y: int = 0
        self.pos2_x: int = 0
        self.pos2_y: int = 0
        self.rotation: float = 0.0  # Degrees
        self.polygon_index: int = 0xFFFF  # Always 0xFFFF for fills
        self.union_index: int = 0xFFFFFFFF  # No union sentinel
        self.user_routed: bool = True
        self.solder_mask_expansion: int = 0
        self.paste_mask_expansion: int = 0
        self.v7_layer_id: int = 0
        self.keepout_restrictions: int = 0
        self._flags1_raw: int = 0x04  # Full raw flags1 byte for round-trip
        self._reserved_tail: bytes = b"\x00" * 3
        self._original_content_len: int = (
            50  # Track original subrecord length for format preservation
        )

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB fill record discriminator.
        """
        return PcbRecordType.FILL

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse FILL record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short
        """
        if len(data) < offset + 1:
            raise ValueError("Data too short for FILL record")

        cursor = offset

        # Verify type byte
        type_byte = data[cursor]
        if type_byte != PcbRecordType.FILL:
            raise ValueError(
                f"Invalid FILL type byte: 0x{type_byte:02X} "
                f"(expected 0x{int(PcbRecordType.FILL):02X})"
            )
        cursor += 1

        # Parse SubRecord 1: Main data
        if len(data) < cursor + 4:
            raise ValueError("Data too short for SubRecord length")

        subrecord_len = struct.unpack("<I", data[cursor : cursor + 4])[0]
        cursor += 4

        if len(data) < cursor + subrecord_len:
            raise ValueError(
                f"SubRecord truncated: expected {subrecord_len} bytes, got {len(data) - cursor}"
            )

        content = data[cursor : cursor + subrecord_len]
        cursor += subrecord_len
        self._original_content_len = subrecord_len

        # Parse SubRecord content
        if len(content) < 37:
            log.warning(
                f"FILL SubRecord shorter than expected: {len(content)} bytes (expected >=37)"
            )

        pos = 0

        # Layer (offset 0)
        self.layer = content[pos]
        pos += 1

        # Flags1 (offset 1): bit0=?, bit1=poly_outline, bit2=~locked, bit3=user_routed
        flags1 = content[pos]
        self._flags1_raw = flags1
        self.is_locked = (flags1 & 0x04) == 0  # Inverted logic: bit set = unlocked
        self.is_polygon_outline = (flags1 & 0x02) != 0
        self.user_routed = (flags1 & 0x08) != 0
        pos += 1

        # Flags2 (offset 2)
        flags2 = content[pos]
        self.is_keepout = flags2 == 0x02
        pos += 1

        # Net index (offset 3-4)
        self.net_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.net_index == 0xFFFF:
            self.net_index = None
        pos += 2

        # Polygon index (offset 5-6): always 0xFFFF for fills
        self.polygon_index = struct.unpack("<H", content[pos : pos + 2])[0]
        pos += 2

        # Component index (offset 7-8)
        self.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.component_index == 0xFFFF:
            self.component_index = None
        pos += 2

        # Union index (offset 9-12): uint32, 0xFFFFFFFF = no union
        self.union_index = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        # Position 1 (offset 13-20: 2x int32)
        self.pos1_x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.pos1_y = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Position 2 (offset 21-28: 2x int32)
        self.pos2_x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.pos2_y = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Rotation (offset 29-36: double)
        self.rotation = struct.unpack("<d", content[pos : pos + 8])[0]
        pos += 8

        # Trailing fields (offsets 37-49) - not present in pre-AD17 files
        # Solder mask expansion (offset 37-40, int32, internal units)
        if pos + 4 <= len(content):
            self.solder_mask_expansion = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
        else:
            pos = len(content)

        # Paste mask expansion (offset 41, uint8, always 0 in corpus)
        if pos < len(content):
            self.paste_mask_expansion = content[pos]
            pos += 1

        # V7 layer identifier field (offset 42-45, uint32)
        if pos + 4 <= len(content):
            self.v7_layer_id = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4

        # Keepout restrictions bitmask (offset 46)
        if pos < len(content):
            self.keepout_restrictions = content[pos]
            pos += 1

        # Reserved tail (offset 47-49, always 0)
        self._reserved_tail = (
            content[pos : pos + 3] if pos + 3 <= len(content) else b"\x00" * 3
        )

        # Store raw binary for round-trip
        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()

        return cursor - offset

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of all FILL fields for dirty-state detection.
        """
        return (
            int(self.layer),
            bool(self.is_locked),
            bool(self.is_polygon_outline),
            bool(self.is_keepout),
            bool(self.user_routed),
            int(self._flags1_raw),
            -1 if self.net_index is None else int(self.net_index),
            int(self.polygon_index),
            -1 if self.component_index is None else int(self.component_index),
            int(self.union_index),
            int(self.pos1_x),
            int(self.pos1_y),
            int(self.pos2_x),
            int(self.pos2_y),
            float(self.rotation),
            int(self.solder_mask_expansion),
            int(self.paste_mask_expansion),
            int(self.v7_layer_id),
            int(self.keepout_restrictions),
            bytes(self._reserved_tail),
        )

    def serialize_to_binary(self) -> bytes:
        """
        Serialize FILL record to binary format.

        Returns:
            Binary data ready to write to stream

        Strategy:
            For round-trip, use stored raw binary if available.
            Otherwise, construct from scratch with all known fields.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        # Create from scratch
        # Build SubRecord content
        content = bytearray()

        # Layer (offset 0)
        content.append(self.layer)

        # Flags1 (offset 1): reconstruct from raw, updating known semantic bits
        flags1 = self._flags1_raw & ~0x0E  # Clear bits 1,2,3 (known semantics)
        if self.is_polygon_outline:
            flags1 |= 0x02
        if not self.is_locked:  # Inverted logic: bit set = unlocked
            flags1 |= 0x04
        if self.user_routed:
            flags1 |= 0x08
        content.append(flags1)

        # Flags2 (offset 2)
        flags2 = 0x02 if self.is_keepout else 0x00
        content.append(flags2)

        # Net index (offset 3-4)
        net_idx = self.net_index if self.net_index is not None else 0xFFFF
        content.extend(struct.pack("<H", net_idx))

        # Polygon index (offset 5-6)
        content.extend(struct.pack("<H", self.polygon_index))

        # Component index (offset 7-8)
        comp_idx = self.component_index if self.component_index is not None else 0xFFFF
        content.extend(struct.pack("<H", comp_idx))

        # Union index (offset 9-12)
        content.extend(struct.pack("<I", self.union_index))

        # Position 1 (offset 13-20)
        content.extend(struct.pack("<i", self.pos1_x))
        content.extend(struct.pack("<i", self.pos1_y))

        # Position 2 (offset 21-28)
        content.extend(struct.pack("<i", self.pos2_x))
        content.extend(struct.pack("<i", self.pos2_y))

        # Rotation (offset 29-36)
        content.extend(struct.pack("<d", self.rotation))

        # Trailing fields (offsets 37-49) - three format generations:
        #   37B (very old): stops right after rotation
        #   46B (pre-5.01): stops after v7_layer_id (no keepout_restrictions)
        #   50B (modern AD17+): full record with keepout_restrictions + reserved_tail
        # Preserve original subrecord length for byte-identical round-trip.
        target_len = self._original_content_len
        remaining = target_len - len(content)

        if remaining > 0:
            # Solder mask expansion (offset 37-40, int32)
            sme_bytes = struct.pack("<i", self.solder_mask_expansion)
            content.extend(sme_bytes[: min(4, remaining)])
            remaining = target_len - len(content)

        if remaining > 0:
            # Paste mask expansion (offset 41, uint8)
            content.append(self.paste_mask_expansion & 0xFF)
            remaining = target_len - len(content)

        if remaining > 0:
            # V7 Layer ID (offset 42-45, uint32)
            v7_bytes = struct.pack("<I", self.v7_layer_id)
            content.extend(v7_bytes[: min(4, remaining)])
            remaining = target_len - len(content)

        if remaining > 0:
            # Keepout restrictions (offset 46)
            content.append(self.keepout_restrictions & 0xFF)
            remaining = target_len - len(content)

        if remaining > 0:
            # Reserved tail (offset 47-49)
            tail = bytes(self._reserved_tail[:3]).ljust(3, b"\x00")
            content.extend(tail[:remaining])

        # Build full record: [type byte] [SubRecord]
        record = bytearray()
        record.append(0x06)  # Type byte

        # SubRecord: [length (uint32)] [content]
        record.extend(struct.pack("<I", len(content)))
        record.extend(content)

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    @property
    def pos1_x_mils(self) -> float:
        """
        Get position 1 X in mils.
        """
        return self._from_internal_units(self.pos1_x)

    @property
    def pos1_y_mils(self) -> float:
        """
        Get position 1 Y in mils.
        """
        return self._from_internal_units(self.pos1_y)

    @property
    def pos2_x_mils(self) -> float:
        """
        Get position 2 X in mils.
        """
        return self._from_internal_units(self.pos2_x)

    @property
    def pos2_y_mils(self) -> float:
        """
        Get position 2 Y in mils.
        """
        return self._from_internal_units(self.pos2_y)

    @property
    def center_x(self) -> int:
        """
        Get center X in internal units.
        """
        return (self.pos1_x + self.pos2_x) // 2

    @property
    def center_y(self) -> int:
        """
        Get center Y in internal units.
        """
        return (self.pos1_y + self.pos2_y) // 2

    @property
    def width_internal(self) -> int:
        """
        Get width in internal units.
        """
        return abs(self.pos2_x - self.pos1_x)

    @property
    def height_internal(self) -> int:
        """
        Get height in internal units.
        """
        return abs(self.pos2_y - self.pos1_y)

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | None = None,
    ) -> list[str]:
        """
        Render fill primitive as a rectangle (with optional rotation).
        """
        if ctx is None:
            return []

        if for_layer is not None and int(self.layer) != for_layer.value:
            return []

        width_mils = abs(self.pos2_x_mils - self.pos1_x_mils)
        height_mils = abs(self.pos2_y_mils - self.pos1_y_mils)
        if width_mils <= 0 or height_mils <= 0:
            return []

        cx = ctx.x_to_svg(self._from_internal_units(self.center_x))
        cy = ctx.y_to_svg(self._from_internal_units(self.center_y))
        width_mm = width_mils * _MIL_TO_MM
        height_mm = height_mils * _MIL_TO_MM
        color = stroke
        if color is None:
            try:
                color = ctx.layer_color(PcbLayer(int(self.layer)))
            except ValueError:
                color = "#808080"

        attrs = [
            f'x="{ctx.fmt(cx - width_mm / 2.0)}"',
            f'y="{ctx.fmt(cy - height_mm / 2.0)}"',
            f'width="{ctx.fmt(width_mm)}"',
            f'height="{ctx.fmt(height_mm)}"',
            f'fill="{html.escape(color)}"',
        ]

        rotation = -float(self.rotation or 0.0)
        if abs(rotation) > 1e-9:
            attrs.append(
                f'transform="rotate({ctx.fmt(rotation)} {ctx.fmt(cx)} {ctx.fmt(cy)})"'
            )

        if include_metadata:
            attrs.append('data-primitive="fill"')
            attrs.extend(ctx.layer_metadata_attrs(int(self.layer)))
            attrs.extend(
                ctx.relationship_metadata_attrs(
                    net_index=self.net_index,
                    component_index=self.component_index,
                )
            )
            element_id_attr = ctx.primitive_id_attr(
                "fill",
                self,
                layer_id=int(self.layer),
                role="main",
            )
            if element_id_attr:
                attrs.append(element_id_attr)

        return [f"<rect {' '.join(attrs)}/>"]

    def __repr__(self) -> str:
        return (
            f"<AltiumPcbFill layer={self.layer} "
            f"pos1=({self.pos1_x_mils:.2f}, {self.pos1_y_mils:.2f}) "
            f"pos2=({self.pos2_x_mils:.2f}, {self.pos2_y_mils:.2f}) "
            f"rot={self.rotation:.1f}deg>"
        )
