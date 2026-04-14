"""
Parse PCB track primitive records.
"""

import html
import logging
import struct
from typing import TYPE_CHECKING

from .altium_record_types import PcbGraphicalObject, PcbLayer, PcbRecordType

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext

log = logging.getLogger(__name__)


class AltiumPcbTrack(PcbGraphicalObject):
    """
    PCB track primitive record.

    Represents a straight line segment on PCB (copper trace or silkscreen line).

    Attributes:
        start_x: Start X position (internal units, 10k/mil)
        start_y: Start Y position (internal units)
        end_x: End X position (internal units)
        end_y: End Y position (internal units)
        width: Line width (internal units)
        layer: Layer number (1=TOP, 32=BOTTOM, etc.)
        component_index: Links to component (uint16, 0xFFFF=unlinked)
        net_index: Links to net (uint16, 0xFFFF=unlinked)
        polygon_index: Polygon index (uint16)
        subpoly_index: Sub-polygon index (uint16)
        union_index: Union membership (uint32, 0xFFFFFFFF=none)
        is_locked: Locked for editing
        is_keepout: Keepout track
        is_polygon_outline: Part of polygon outline
        user_routed: True if manually routed (flags1 bit 3)
        solder_mask_expansion: Solder mask expansion value (int32, internal units)
        paste_mask_expansion: Paste mask expansion value (int16, always 0 in practice)
        v7_layer_id: V7 layer identifier field (uint32)
        keepout_restrictions: Keepout bitmask (bit0=Track, bit1=Via, bit2=Copper,
                              bit3=SmdPad, bit4=ThPad)
    """

    def __init__(self) -> None:
        super().__init__()
        self.start_x: int = 0  # Internal units (10k/mil)
        self.start_y: int = 0
        self.end_x: int = 0
        self.end_y: int = 0
        self.polygon_index: int = 0
        self.subpoly_index: int = 0
        self.union_index: int = 0xFFFFFFFF  # No union sentinel
        self.user_routed: bool = True
        self.solder_mask_expansion: int = 0
        self.paste_mask_expansion: int = 0
        self.v7_layer_id: int = 0
        self.keepout_restrictions: int = 0
        self._flags1_raw: int = 0x04  # Full raw flags1 byte for round-trip
        self._reserved_tail: bytes = b"\x00" * 3
        self._original_content_len: int = (
            49  # Track original subrecord length for format preservation
        )

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB track record discriminator.
        """
        return PcbRecordType.TRACK

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse TRACK record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short
        """
        if len(data) < offset + 1:
            raise ValueError("Data too short for TRACK record")

        cursor = offset

        # Verify type byte
        type_byte = data[cursor]
        if type_byte != PcbRecordType.TRACK:
            raise ValueError(
                f"Invalid TRACK type byte: 0x{type_byte:02X} "
                f"(expected 0x{int(PcbRecordType.TRACK):02X})"
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
        if len(content) < 35:
            log.warning(
                f"TRACK SubRecord shorter than expected: {len(content)} bytes (expected >=35)"
            )

        pos = 0

        # Layer (offset 0)
        self.layer = content[pos]
        pos += 1

        # Flags1 (offset 1): bit0=?, bit1=poly_outline, bit2=~locked, bit3=user_routed, bit4+=unknown
        flags1 = content[pos]
        self._flags1_raw = flags1
        self.is_locked = (flags1 & 0x04) == 0  # Inverted logic: bit set = unlocked
        self.is_polygon_outline = (flags1 & 0x02) != 0
        self.user_routed = (flags1 & 0x08) != 0
        pos += 1

        # Flags2 (offset 2)
        flags2 = content[pos]
        self.is_keepout = flags2 == 2
        pos += 1

        # Net index (offset 3-4)
        self.net_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.net_index == 0xFFFF:
            self.net_index = None
        pos += 2

        # Polygon index (offset 5-6)
        self.polygon_index = struct.unpack("<H", content[pos : pos + 2])[0]
        pos += 2

        # Component index (offset 7-8) - key linkage
        self.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.component_index == 0xFFFF:
            self.component_index = None
        pos += 2

        # Union index (offset 9-12): uint32, 0xFFFFFFFF = no union
        self.union_index = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        # Start point (offset 13-20: 2x int32)
        self.start_x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.start_y = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # End point (offset 21-28: 2x int32)
        self.end_x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.end_y = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Width (offset 29-32: uint32)
        self.width = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        # Subpolyindex (offset 33-34)
        self.subpoly_index = struct.unpack("<H", content[pos : pos + 2])[0]
        pos += 2

        # Trailing fields (offsets 35-48) - not present in pre-AD17 files (45-byte subrecords)
        # Solder mask expansion (offset 35-38, int32, internal units)
        if pos + 4 <= len(content):
            self.solder_mask_expansion = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
        else:
            pos = len(content)

        # Paste mask expansion (offset 39-40, int16, always 0 in corpus)
        if pos + 2 <= len(content):
            self.paste_mask_expansion = struct.unpack("<h", content[pos : pos + 2])[0]
            pos += 2

        # V7 layer identifier field (offset 41-44, uint32)
        if pos + 4 <= len(content):
            self.v7_layer_id = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4

        # Keepout restrictions bitmask (offset 45)
        if pos < len(content):
            self.keepout_restrictions = content[pos]
            pos += 1

        # Reserved tail (offset 46-48, always 0)
        self._reserved_tail = (
            content[pos : pos + 3] if pos + 3 <= len(content) else b"\x00" * 3
        )

        # Store raw binary for round-trip (includes unknown trailing bytes)
        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()

        return cursor - offset

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of all TRACK fields for dirty-state detection.
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
            int(self.start_x),
            int(self.start_y),
            int(self.end_x),
            int(self.end_y),
            int(self.width),
            int(self.subpoly_index),
            int(self.solder_mask_expansion),
            int(self.paste_mask_expansion),
            int(self.v7_layer_id),
            int(self.keepout_restrictions),
            bytes(self._reserved_tail),
        )

    def serialize_to_binary(self) -> bytes:
        """
        Serialize TRACK record to binary format.

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
        flags2 = 2 if self.is_keepout else 0
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

        # Start point (offset 13-20)
        content.extend(struct.pack("<i", self.start_x))
        content.extend(struct.pack("<i", self.start_y))

        # End point (offset 21-28)
        content.extend(struct.pack("<i", self.end_x))
        content.extend(struct.pack("<i", self.end_y))

        # Width (offset 29-32)
        content.extend(struct.pack("<I", self.width))

        # Subpolyindex (offset 33-34)
        content.extend(struct.pack("<H", self.subpoly_index))

        # Trailing fields (offsets 35-48) - three format generations:
        #   36B (very old): stops 1 byte into solder_mask_expansion
        #   45B (pre-5.01): stops after v7_layer_id (no keepout_restrictions)
        #   49B (modern AD17+): full record with keepout_restrictions + reserved_tail
        # Preserve original subrecord length for byte-identical round-trip.
        target_len = self._original_content_len
        remaining = target_len - len(content)

        if remaining > 0:
            # Solder mask expansion (offset 35-38, int32)
            sme_bytes = struct.pack("<i", self.solder_mask_expansion)
            content.extend(sme_bytes[: min(4, remaining)])
            remaining = target_len - len(content)

        if remaining > 0:
            # Paste mask expansion (offset 39-40, int16)
            pme_bytes = struct.pack("<h", self.paste_mask_expansion)
            content.extend(pme_bytes[: min(2, remaining)])
            remaining = target_len - len(content)

        if remaining > 0:
            # V7 Layer ID (offset 41-44, uint32)
            v7_bytes = struct.pack("<I", self.v7_layer_id)
            content.extend(v7_bytes[: min(4, remaining)])
            remaining = target_len - len(content)

        if remaining > 0:
            # Keepout restrictions (offset 45)
            content.append(self.keepout_restrictions & 0xFF)
            remaining = target_len - len(content)

        if remaining > 0:
            # Reserved tail (offset 46-48)
            tail = bytes(self._reserved_tail[:3]).ljust(3, b"\x00")
            content.extend(tail[:remaining])

        # Build full record: [type byte] [SubRecord]
        record = bytearray()
        record.append(0x04)  # Type byte

        # SubRecord: [length (uint32)] [content]
        record.extend(struct.pack("<I", len(content)))
        record.extend(content)

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    @property
    def start_x_mils(self) -> float:
        """
        Get start X in mils.
        """
        return self._from_internal_units(self.start_x)

    @property
    def start_y_mils(self) -> float:
        """
        Get start Y in mils.
        """
        return self._from_internal_units(self.start_y)

    @property
    def end_x_mils(self) -> float:
        """
        Get end X in mils.
        """
        return self._from_internal_units(self.end_x)

    @property
    def end_y_mils(self) -> float:
        """
        Get end Y in mils.
        """
        return self._from_internal_units(self.end_y)

    @property
    def width_mils(self) -> float:
        """
        Get width in mils.
        """
        return self._from_internal_units(self.width)

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | None = None,
    ) -> list[str]:
        """
        Render track to SVG as a stroked line segment.

        Args:
            ctx: PCB SVG render context.
            stroke: Optional stroke color override.
            include_metadata: Include data-* attributes for downstream tooling.

        Returns:
            List containing one SVG line element, or empty if no context.
        """
        if ctx is None:
            return []
        if for_layer is not None and int(self.layer) != for_layer.value:
            return []

        try:
            layer_enum = PcbLayer(int(self.layer))
        except ValueError:
            layer_enum = None

        stroke_color = stroke or (
            ctx.layer_color(layer_enum) if layer_enum is not None else "#808080"
        )
        stroke_width = max(self.width_mils * 0.0254, 0.001)

        attrs = [
            f'x1="{ctx.fmt(ctx.x_to_svg(self.start_x_mils))}"',
            f'y1="{ctx.fmt(ctx.y_to_svg(self.start_y_mils))}"',
            f'x2="{ctx.fmt(ctx.x_to_svg(self.end_x_mils))}"',
            f'y2="{ctx.fmt(ctx.y_to_svg(self.end_y_mils))}"',
            f'stroke="{html.escape(stroke_color)}"',
            f'stroke-width="{ctx.fmt(stroke_width)}"',
            'stroke-linecap="round"',
            'fill="none"',
        ]

        if include_metadata:
            attrs.append('data-primitive="track"')
            attrs.extend(ctx.layer_metadata_attrs(int(self.layer)))
            attrs.extend(
                ctx.relationship_metadata_attrs(
                    net_index=self.net_index,
                    component_index=self.component_index,
                )
            )
            element_id_attr = ctx.primitive_id_attr(
                "track",
                self,
                layer_id=int(self.layer),
                role="main",
            )
            if element_id_attr:
                attrs.append(element_id_attr)

        return [f"<line {' '.join(attrs)}/>"]

    def __repr__(self) -> str:
        return (
            f"<AltiumPcbTrack layer={self.layer} "
            f"start=({self.start_x_mils:.2f}, {self.start_y_mils:.2f}) "
            f"end=({self.end_x_mils:.2f}, {self.end_y_mils:.2f}) "
            f"width={self.width_mils:.2f}mil>"
        )
