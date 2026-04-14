"""
Parse PCB arc primitive records.
"""

import html
import logging
import math
import struct
from typing import TYPE_CHECKING

from .altium_record_types import PcbGraphicalObject, PcbLayer, PcbRecordType
from .altium_svg_arc_helpers import choose_svg_sweep_flag_for_center

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext

log = logging.getLogger(__name__)


class AltiumPcbArc(PcbGraphicalObject):
    """
    PCB arc primitive record.

    Represents a curved line segment on PCB (copper arc or silkscreen arc).

    Attributes:
        center_x: Center X position (internal units, 10k/mil)
        center_y: Center Y position (internal units)
        radius: Arc radius (internal units)
        start_angle: Start angle (degrees, 0 deg = right, counterclockwise)
        end_angle: End angle (degrees)
        width: Line width (internal units)
        layer: Layer number (1=TOP, 32=BOTTOM, etc.)
        component_index: Links to component (uint16, 0xFFFF=unlinked)
        net_index: Links to net (uint16, 0xFFFF=unlinked)
        polygon_index: Polygon index (uint16)
        subpoly_index: Sub-polygon index (uint16)
        union_index: Union membership (uint32, 0xFFFFFFFF=none)
        is_locked: Locked for editing
        is_keepout: Keepout arc
        is_polygon_outline: Part of polygon outline
        user_routed: True if manually routed (flags1 bit 3)
        solder_mask_expansion: Solder mask expansion value (int32, internal units)
        paste_mask_expansion: Paste mask expansion value (uint8, always 0 in practice)
        v7_layer_id: V7 layer identifier field (uint32)
        keepout_restrictions: Keepout bitmask (bit0=Via, bit1=Track, bit2=Copper,
                              bit3=SmdPad, bit4=ThPad)
    """

    def __init__(self) -> None:
        super().__init__()
        self.center_x: int = 0  # Internal units (10k/mil)
        self.center_y: int = 0
        self.radius: int = 0
        self.start_angle: float = 0.0  # Degrees
        self.end_angle: float = 0.0  # Degrees
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
            60  # Track original subrecord length for format preservation
        )

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB arc record discriminator.
        """
        return PcbRecordType.ARC

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse ARC record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short
        """
        if len(data) < offset + 1:
            raise ValueError("Data too short for ARC record")

        cursor = offset

        # Verify type byte
        type_byte = data[cursor]
        if type_byte != PcbRecordType.ARC:
            raise ValueError(
                f"Invalid ARC type byte: 0x{type_byte:02X} "
                f"(expected 0x{int(PcbRecordType.ARC):02X})"
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
        if len(content) < 47:
            log.warning(
                f"ARC SubRecord shorter than expected: {len(content)} bytes (expected >=47)"
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

        # Component index (offset 7-8)
        self.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.component_index == 0xFFFF:
            self.component_index = None
        pos += 2

        # Union index (offset 9-12): uint32, 0xFFFFFFFF = no union
        self.union_index = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        # Center point (offset 13-20: 2x int32)
        self.center_x = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4
        self.center_y = struct.unpack("<i", content[pos : pos + 4])[0]
        pos += 4

        # Radius (offset 21-24: uint32)
        self.radius = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        # Start angle (offset 25-32: double)
        self.start_angle = struct.unpack("<d", content[pos : pos + 8])[0]
        pos += 8

        # End angle (offset 33-40: double)
        self.end_angle = struct.unpack("<d", content[pos : pos + 8])[0]
        pos += 8

        # Width (offset 41-44: uint32)
        self.width = struct.unpack("<I", content[pos : pos + 4])[0]
        pos += 4

        # Subpolyindex (offset 45-46) - not present in very old files (45-byte subrecords)
        if pos + 2 <= len(content):
            self.subpoly_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2
        else:
            pos = len(content)

        # Trailing fields (offsets 47-59) - not present in pre-AD17 files (56-byte subrecords)
        # Solder mask expansion (offset 47-50, int32, internal units)
        if pos + 4 <= len(content):
            self.solder_mask_expansion = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
        else:
            pos = len(content)

        # Paste mask expansion (offset 51, uint8, always 0 in corpus)
        if pos < len(content):
            self.paste_mask_expansion = content[pos]
            pos += 1

        # V7 layer identifier field (offset 52-55, uint32)
        if pos + 4 <= len(content):
            self.v7_layer_id = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4

        # Keepout restrictions bitmask (offset 56)
        if pos < len(content):
            self.keepout_restrictions = content[pos]
            pos += 1

        # Reserved tail (offset 57-59, always 0)
        self._reserved_tail = (
            content[pos : pos + 3] if pos + 3 <= len(content) else b"\x00" * 3
        )

        # Store raw binary for round-trip (includes optional keepout data)
        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()

        return cursor - offset

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of all ARC fields for dirty-state detection.
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
            int(self.center_x),
            int(self.center_y),
            int(self.radius),
            float(self.start_angle),
            float(self.end_angle),
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
        Serialize ARC record to binary format.

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

        # Center point (offset 13-20)
        content.extend(struct.pack("<i", self.center_x))
        content.extend(struct.pack("<i", self.center_y))

        # Radius (offset 21-24)
        content.extend(struct.pack("<I", self.radius))

        # Start angle (offset 25-32)
        content.extend(struct.pack("<d", self.start_angle))

        # End angle (offset 33-40)
        content.extend(struct.pack("<d", self.end_angle))

        # Width (offset 41-44)
        content.extend(struct.pack("<I", self.width))

        # Trailing fields use remaining-bytes approach to preserve original
        # subrecord size across three format generations:
        # - Very old (45B): stops after width, no subpoly_index
        # - Old (47B): stops after subpoly_index, no solder_mask_expansion
        # - Modern (60B): all fields including keepout_restrictions + reserved_tail
        target_len = self._original_content_len
        remaining = target_len - len(content)

        # Subpolyindex (offset 45-46)
        if remaining >= 2:
            content.extend(struct.pack("<H", self.subpoly_index))
            remaining = target_len - len(content)
        else:
            remaining = 0

        # Solder mask expansion (offset 47-50, int32)
        if remaining >= 4:
            content.extend(struct.pack("<i", self.solder_mask_expansion))
            remaining = target_len - len(content)

        # Paste mask expansion (offset 51, uint8)
        if remaining >= 1:
            content.append(self.paste_mask_expansion & 0xFF)
            remaining = target_len - len(content)

        # V7 Layer ID (offset 52-55, uint32)
        if remaining >= 4:
            content.extend(struct.pack("<I", self.v7_layer_id))
            remaining = target_len - len(content)

        # Keepout restrictions (offset 56)
        if remaining >= 1:
            content.append(self.keepout_restrictions & 0xFF)
            remaining = target_len - len(content)

        # Reserved tail (offset 57-59)
        if remaining > 0:
            tail = bytes(self._reserved_tail[:remaining]).ljust(remaining, b"\x00")
            content.extend(tail)

        # Build full record: [type byte] [SubRecord]
        record = bytearray()
        record.append(0x01)  # Type byte

        # SubRecord: [length (uint32)] [content]
        record.extend(struct.pack("<I", len(content)))
        record.extend(content)

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    @property
    def center_x_mils(self) -> float:
        """
        Get center X in mils.
        """
        return self._from_internal_units(self.center_x)

    @property
    def center_y_mils(self) -> float:
        """
        Get center Y in mils.
        """
        return self._from_internal_units(self.center_y)

    @property
    def radius_mils(self) -> float:
        """
        Get radius in mils.
        """
        return self._from_internal_units(self.radius)

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | None = None,
    ) -> list[str]:
        """
        Render arc to SVG path using native elliptical arc commands.

        Args:
            ctx: PCB SVG render context.
            stroke: Optional stroke color override.
            include_metadata: Include data-* attributes for downstream tooling.

        Returns:
            List containing one SVG path element, or empty if no context.
        """
        if ctx is None:
            return []
        if for_layer is not None and int(self.layer) != for_layer.value:
            return []

        radius_mils = self.radius_mils
        if radius_mils <= 0:
            return []

        try:
            layer_enum = PcbLayer(int(self.layer))
        except ValueError:
            layer_enum = None

        stroke_color = stroke or (
            ctx.layer_color(layer_enum) if layer_enum is not None else "#808080"
        )
        stroke_width = max(self.width_mils * 0.0254, 0.001)

        start_deg = float(self.start_angle)
        end_deg = float(self.end_angle)
        sweep_ccw = (end_deg - start_deg) % 360.0
        raw_delta = end_deg - start_deg

        cx_mils = self.center_x_mils
        cy_mils = self.center_y_mils

        sx_mils = cx_mils + radius_mils * math.cos(math.radians(start_deg))
        sy_mils = cy_mils + radius_mils * math.sin(math.radians(start_deg))
        ex_mils = cx_mils + radius_mils * math.cos(math.radians(end_deg))
        ey_mils = cy_mils + radius_mils * math.sin(math.radians(end_deg))

        cx_svg = ctx.x_to_svg(cx_mils)
        cy_svg = ctx.y_to_svg(cy_mils)
        sx_svg = ctx.x_to_svg(sx_mils)
        sy_svg = ctx.y_to_svg(sy_mils)
        ex_svg = ctx.x_to_svg(ex_mils)
        ey_svg = ctx.y_to_svg(ey_mils)

        sx = ctx.fmt(sx_svg)
        sy = ctx.fmt(sy_svg)
        ex = ctx.fmt(ex_svg)
        ey = ctx.fmt(ey_svg)
        radius_mm = radius_mils * 0.0254
        radius = ctx.fmt(radius_mm)

        full_circle = math.isclose(sweep_ccw, 0.0, abs_tol=1e-9) and not math.isclose(
            raw_delta, 0.0, abs_tol=1e-9
        )

        if full_circle:
            mid_deg = start_deg + 180.0
            mx_mils = cx_mils + radius_mils * math.cos(math.radians(mid_deg))
            my_mils = cy_mils + radius_mils * math.sin(math.radians(mid_deg))
            mx_svg = ctx.x_to_svg(mx_mils)
            my_svg = ctx.y_to_svg(my_mils)
            mx = ctx.fmt(mx_svg)
            my = ctx.fmt(my_svg)
            d = (
                f"M {sx} {sy} "
                f"A {radius} {radius} 0 1 1 {mx} {my} "
                f"A {radius} {radius} 0 1 1 {sx} {sy}"
            )
        else:
            large_arc_int = 1 if sweep_ccw > 180.0 else 0
            large_arc = str(large_arc_int)
            sweep_flag = str(
                choose_svg_sweep_flag_for_center(
                    sx_svg,
                    sy_svg,
                    ex_svg,
                    ey_svg,
                    radius_mm,
                    large_arc_int,
                    cx_svg,
                    cy_svg,
                    default_sweep_flag=1,
                )
            )
            d = f"M {sx} {sy} A {radius} {radius} 0 {large_arc} {sweep_flag} {ex} {ey}"

        attrs = [
            f'd="{d}"',
            f'stroke="{html.escape(stroke_color)}"',
            f'stroke-width="{ctx.fmt(stroke_width)}"',
            'stroke-linecap="round"',
            'fill="none"',
        ]

        if include_metadata:
            attrs.append('data-primitive="arc"')
            attrs.extend(ctx.layer_metadata_attrs(int(self.layer)))
            attrs.extend(
                ctx.relationship_metadata_attrs(
                    net_index=self.net_index,
                    component_index=self.component_index,
                )
            )
            element_id_attr = ctx.primitive_id_attr(
                "arc",
                self,
                layer_id=int(self.layer),
                role="main",
            )
            if element_id_attr:
                attrs.append(element_id_attr)

        return [f"<path {' '.join(attrs)}/>"]

    def __repr__(self) -> str:
        return (
            f"<AltiumPcbArc layer={self.layer} "
            f"center=({self.center_x_mils:.2f}, {self.center_y_mils:.2f}) "
            f"radius={self.radius_mils:.2f}mil "
            f"angles={self.start_angle:.1f}deg->{self.end_angle:.1f}deg>"
        )
