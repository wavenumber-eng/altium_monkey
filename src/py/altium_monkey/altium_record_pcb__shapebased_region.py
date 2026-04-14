"""
Altium PCB ShapeBasedRegion Record

Represents RENDERED polygon geometry (filled copper) from ShapeBasedRegions6/Data stream.
This is different from Polygons6 which contains polygon DEFINITIONS.

ShapeBasedRegions contain the actual filled copper after Altium computes thermal reliefs,
spoke connections, clearances, and dead copper removal.
"""

import html
import logging
import math
import struct
from typing import TYPE_CHECKING

from .altium_pcb_enums import PcbRegionKind
from .altium_record_types import PcbGraphicalObject, PcbLayer, PcbRecordType
from .altium_svg_arc_helpers import choose_svg_sweep_flag_for_center

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext

log = logging.getLogger(__name__)


class PcbExtendedVertex:
    """
    Extended vertex format with arc support.

    Used in ShapeBasedRegions6 for outline vertices.
    Total size: 37 bytes per vertex.
    """

    def __init__(self) -> None:
        self._is_round_raw: int = 0  # Raw byte value (0=line, non-zero=arc)
        self.x: int = 0  # Position X (internal units)
        self.y: int = 0  # Position Y (internal units)
        self.center_x: int = 0  # Arc center X (internal units)
        self.center_y: int = 0  # Arc center Y (internal units)
        self.radius: int = 0  # Arc radius (internal units)
        self.start_angle: float = 0.0  # Arc start angle (radians)
        self.end_angle: float = 0.0  # Arc end angle (radians)

    @property
    def is_round(self) -> bool:
        """
        False=line segment, True=arc.
        """
        return self._is_round_raw != 0

    @is_round.setter
    def is_round(self, value: bool) -> None:
        self._is_round_raw = 1 if value else 0

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

    @property
    def center_x_mils(self) -> float:
        """
        Arc center X in mils.
        """
        return self.center_x / 10000.0

    @property
    def center_y_mils(self) -> float:
        """
        Arc center Y in mils.
        """
        return self.center_y / 10000.0

    @property
    def radius_mils(self) -> float:
        """
        Arc radius in mils.
        """
        return self.radius / 10000.0

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        if self.is_round:
            return (
                f"PcbExtendedVertex(arc, pos=({self.x_mils:.2f}, {self.y_mils:.2f}), "
                f"center=({self.center_x_mils:.2f}, {self.center_y_mils:.2f}), "
                f"r={self.radius_mils:.2f})"
            )
        else:
            return (
                f"PcbExtendedVertex(line, pos=({self.x_mils:.2f}, {self.y_mils:.2f}))"
            )


class PcbSimpleVertex:
    """
    Simple vertex format (double precision coordinates).

    Used for hole vertices in ShapeBasedRegions6.
    Total size: 16 bytes per vertex.
    """

    def __init__(self) -> None:
        self.x: float = 0.0  # Position X (double, in internal units)
        self.y: float = 0.0  # Position Y (double, in internal units)

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

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        return f"PcbSimpleVertex(pos=({self.x_mils:.2f}, {self.y_mils:.2f}))"


class AltiumPcbShapeBasedRegion(PcbGraphicalObject):
    """
    Shape-based PCB region primitive record.

    Represents RENDERED polygon geometry (filled copper).
    This is the actual copper after Altium computes thermal reliefs, clearances, etc.

    Different from Polygons6 which contains polygon DEFINITIONS.

    Based on record format's AREGION6 parser:
    Attributes:
        layer: PCB layer (1=TOP, 32=BOTTOM, etc.)
        is_locked: Is region locked
        is_keepout: Is keepout region
        net_index: Net index (uint16)
        polygon_index: Polygon index (uint16)
        component_index: Component index (uint16)
        hole_count: Number of holes/cutouts

        # Properties (text fields)
        kind: Region type (COPPER, BOARD_CUTOUT, POLYGON_CUTOUT, etc.)
        is_shapebased: TRUE for rendered geometry
        subpoly_index: Sub-polygon index
        keepout_restrictions: Keepout restrictions bitmask (default 0x1F)
        union_index: Union index for polygon grouping

        # Geometry
        outline: List of PcbExtendedVertex (outline vertices with arc support)
        holes: List of lists of PcbSimpleVertex (hole vertices)
    """

    def __init__(self) -> None:
        super().__init__()

        # Header fields
        self.layer: int = 1  # Default to TOP layer
        self.is_locked: bool = False
        self.is_keepout: bool = False
        self.net_index: int = 0xFFFF
        self.polygon_index: int = 0xFFFF
        self.component_index: int = 0xFFFF
        self.hole_count: int = 0

        # Properties
        self.kind: PcbRegionKind = PcbRegionKind.COPPER
        self.is_shapebased: bool = False
        self.subpoly_index: int = 0
        self.keepout_restrictions: int = 0x1F
        self.union_index: int = 0

        # Geometry
        self.outline: list[PcbExtendedVertex] = []
        self.holes: list[list[PcbSimpleVertex]] = []

        # Raw properties dict
        self.properties: dict[str, str] = {}

        # Raw byte preservation for round-trip
        self._flags1_raw: int = 0x04  # Full raw flags1 byte
        self._header_skip5: bytes = b"\x00" * 5  # 5 bytes at SR1 offsets 9-13
        self._header_skip2: bytes = b"\x00" * 2  # 2 bytes at SR1 offsets 16-17
        self._raw_properties_bytes: bytes | None = None  # Original props chunk
        self._props_has_trailing_null: bool = (
            False  # Null terminator after props_len bytes
        )
        self._sr1_length_bytes: bytes | None = None  # Original 4-byte SR1 length field

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse ShapeBasedRegion record from binary data.

        Based on record format's AREGION6 constructor with aExtendedVertices=True.

        Args:
            data: Binary data buffer
            offset: Starting offset in buffer

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If record type is not 0x0B (REGION)
            struct.error: If binary data is malformed
        """
        original_offset = offset

        # Type byte verification
        type_byte = data[offset]
        if type_byte != PcbRecordType.REGION:
            raise ValueError(
                f"Expected REGION type 0x{int(PcbRecordType.REGION):02X}, "
                f"got 0x{type_byte:02X}"
            )
        offset += 1

        # SubRecord 1 length (preserve raw bytes for round-trip - some records
        # have non-standard values that don't match actual content length)
        self._sr1_length_bytes = bytes(data[offset : offset + 4])
        offset += 4

        # Layer
        self.layer = data[offset]
        offset += 1

        # Flags1 (bit 0x04 inverted = is_locked)
        flags1 = data[offset]
        self._flags1_raw = flags1
        self.is_locked = (flags1 & 0x04) == 0
        self.is_polygon_outline = (flags1 & 0x02) != 0
        offset += 1

        # Flags2 (value 2 = is_keepout)
        flags2 = data[offset]
        self.is_keepout = flags2 == 2
        offset += 1

        # Net, polygon, component indices
        self.net_index = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        self.polygon_index = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        self.component_index = struct.unpack_from("<H", data, offset)[0]
        offset += 2

        # Preserve 5 raw bytes (union_index-like + unknown)
        self._header_skip5 = bytes(data[offset : offset + 5])
        offset += 5

        # Hole count
        self.hole_count = struct.unpack_from("<H", data, offset)[0]
        offset += 2

        # Preserve 2 raw bytes
        self._header_skip2 = bytes(data[offset : offset + 2])
        offset += 2

        # Properties (length-prefixed text, may include trailing null in length)
        props_len = struct.unpack_from("<I", data, offset)[0]
        offset += 4

        # Save raw properties bytes for byte-identical round-trip
        props_raw = data[offset : offset + props_len]
        self._raw_properties_bytes = bytes(props_raw)

        # Read and parse properties text (strip trailing null if present)
        props_text = props_raw.decode("utf-8", errors="ignore").rstrip("\x00")
        offset += props_len

        # Parse properties
        self.properties = {}
        for pair in props_text.split("|"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                self.properties[key] = value

        # Skip null terminator if present (some files have null AFTER props_len bytes)
        if offset < len(data) and data[offset] == 0:
            self._props_has_trailing_null = True
            offset += 1

        # Parse properties
        self._parse_properties()

        # Outline vertices count
        num_outline_vertices = struct.unpack_from("<I", data, offset)[0]
        offset += 4

        # ShapeBasedRegions use extended vertices with closing vertex added
        num_outline_vertices += 1

        # Parse outline vertices (extended format: 37 bytes each)
        self.outline = []
        for i in range(num_outline_vertices):
            vertex = PcbExtendedVertex()

            # isRound (uint8) - preserve raw byte for round-trip
            vertex._is_round_raw = data[offset]
            offset += 1

            # Position X, Y (int32 each)
            vertex.x = struct.unpack_from("<i", data, offset)[0]
            offset += 4
            vertex.y = struct.unpack_from("<i", data, offset)[0]
            offset += 4

            # Center X, Y (int32 each)
            vertex.center_x = struct.unpack_from("<i", data, offset)[0]
            offset += 4
            vertex.center_y = struct.unpack_from("<i", data, offset)[0]
            offset += 4

            # Radius (int32)
            vertex.radius = struct.unpack_from("<i", data, offset)[0]
            offset += 4

            # Start angle, end angle (double each, 8 bytes)
            vertex.start_angle = struct.unpack_from("<d", data, offset)[0]
            offset += 8
            vertex.end_angle = struct.unpack_from("<d", data, offset)[0]
            offset += 8

            self.outline.append(vertex)

        # Parse holes
        self.holes = []
        for k in range(self.hole_count):
            num_hole_vertices = struct.unpack_from("<I", data, offset)[0]
            offset += 4

            hole_vertices = []
            for i in range(num_hole_vertices):
                vertex = PcbSimpleVertex()

                # X, Y (double each, 8 bytes)
                vertex.x = struct.unpack_from("<d", data, offset)[0]
                offset += 8
                vertex.y = struct.unpack_from("<d", data, offset)[0]
                offset += 8

                hole_vertices.append(vertex)

            self.holes.append(hole_vertices)

        # Calculate bytes consumed
        bytes_consumed = offset - original_offset

        self._raw_binary = data[original_offset:offset]
        self._raw_binary_signature = self._state_signature()
        return bytes_consumed

    def _parse_properties(self) -> None:
        """
        Parse properties dict into typed fields.
        """
        # KIND field determines region type
        pkind = int(self.properties.get("KIND", "0"))
        is_cutout = self.properties.get("ISBOARDCUTOUT", "FALSE").upper() == "TRUE"

        if pkind == 0:
            if is_cutout:
                self.kind = PcbRegionKind.BOARD_CUTOUT
            else:
                self.kind = PcbRegionKind.COPPER
        elif pkind == 1:
            self.kind = PcbRegionKind.POLYGON_CUTOUT
        elif pkind == 2:
            self.kind = PcbRegionKind.DASHED_OUTLINE
        elif pkind == 3:
            self.kind = PcbRegionKind.UNKNOWN_3
        elif pkind == 4:
            self.kind = PcbRegionKind.CAVITY_DEFINITION
        else:
            self.kind = PcbRegionKind.UNKNOWN

        # Other properties
        self.is_shapebased = (
            self.properties.get("ISSHAPEBASED", "FALSE").upper() == "TRUE"
        )
        self.subpoly_index = int(self.properties.get("SUBPOLYINDEX", "0"))
        self.keepout_restrictions = int(
            self.properties.get("KEEPOUTRESTRIC", "31")
        )  # 0x1F
        self.union_index = int(self.properties.get("UNIONINDEX", "0"))

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of semantically known shape-based fields.
        """
        return (
            int(self.layer),
            int(self.net_index) if self.net_index is not None else 0xFFFF,
            int(self.polygon_index),
            int(self.component_index) if self.component_index is not None else 0xFFFF,
            bool(self.is_locked),
            bool(self.is_keepout),
            int(self._flags1_raw),
            self._header_skip5,
            self._header_skip2,
            self._raw_properties_bytes,
            bool(self._props_has_trailing_null),
            self._sr1_length_bytes,
            int(self.kind),
            bool(self.is_shapebased),
            int(self.subpoly_index),
            int(self.keepout_restrictions),
            int(self.union_index),
            tuple(
                (
                    int(v._is_round_raw),
                    int(v.x),
                    int(v.y),
                    int(v.center_x),
                    int(v.center_y),
                    int(v.radius),
                    float(v.start_angle),
                    float(v.end_angle),
                )
                for v in self.outline
            ),
            tuple(tuple((float(v.x), float(v.y)) for v in hole) for hole in self.holes),
            tuple(sorted((str(k), str(v)) for k, v in self.properties.items())),
        )

    def _properties_string(self) -> str:
        """
        Build pipe-separated properties string from object state.
        """
        props: dict[str, str] = {str(k): str(v) for k, v in self.properties.items()}

        kind_value = int(self.kind)
        is_board_cutout = False
        if self.kind == PcbRegionKind.BOARD_CUTOUT:
            kind_value = 0
            is_board_cutout = True

        props["KIND"] = str(kind_value)
        props["ISBOARDCUTOUT"] = "TRUE" if is_board_cutout else "FALSE"
        props["ISSHAPEBASED"] = "TRUE" if self.is_shapebased else "FALSE"
        props["SUBPOLYINDEX"] = str(int(self.subpoly_index))
        props["KEEPOUTRESTRIC"] = str(int(self.keepout_restrictions))
        props["UNIONINDEX"] = str(int(self.union_index))
        return "|".join(f"{k}={v}" for k, v in props.items())

    @staticmethod
    def _outline_for_write(
        vertices: list[PcbExtendedVertex],
    ) -> list[PcbExtendedVertex]:
        """
        Ensure an explicit closing vertex exists for shape-based encoding.
        """
        if not vertices:
            return []
        first = vertices[0]
        last = vertices[-1]
        if int(first.x) == int(last.x) and int(first.y) == int(last.y):
            return list(vertices)
        closing = PcbExtendedVertex()
        closing.is_round = first.is_round
        closing.x = int(first.x)
        closing.y = int(first.y)
        closing.center_x = int(first.center_x)
        closing.center_y = int(first.center_y)
        closing.radius = int(first.radius)
        closing.start_angle = float(first.start_angle)
        closing.end_angle = float(first.end_angle)
        out = list(vertices)
        out.append(closing)
        return out

    def serialize_to_binary(self) -> bytes:
        """
        Serialize ShapeBasedRegion record to binary format.

        Reuses raw binary only when semantic fields are unchanged.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        subrecord = bytearray()
        subrecord.append(max(0, min(255, int(self.layer))))
        # Flags1: preserve raw byte, update only known semantic bits
        flags1 = self._flags1_raw & ~0x06  # Clear bits 1,2 (known semantics)
        if self.is_polygon_outline:
            flags1 |= 0x02
        if not self.is_locked:
            flags1 |= 0x04
        subrecord.append(flags1)
        subrecord.append(0x02 if self.is_keepout else 0x00)

        net_index = (
            0xFFFF
            if self.net_index is None
            else max(0, min(0xFFFF, int(self.net_index)))
        )
        comp_index = (
            0xFFFF
            if self.component_index is None
            else max(0, min(0xFFFF, int(self.component_index)))
        )
        subrecord.extend(struct.pack("<H", net_index))
        subrecord.extend(
            struct.pack("<H", max(0, min(0xFFFF, int(self.polygon_index))))
        )
        subrecord.extend(struct.pack("<H", comp_index))
        subrecord.extend(self._header_skip5)

        holes = self.holes or []
        subrecord.extend(struct.pack("<H", max(0, min(0xFFFF, len(holes)))))
        subrecord.extend(self._header_skip2)

        # Properties: replay raw bytes for byte-identical round-trip
        if self._raw_properties_bytes is not None:
            subrecord.extend(struct.pack("<I", len(self._raw_properties_bytes)))
            subrecord.extend(self._raw_properties_bytes)
            if self._props_has_trailing_null:
                subrecord.extend(b"\x00")
        else:
            props_bytes = self._properties_string().encode("utf-8", errors="replace")
            subrecord.extend(struct.pack("<I", len(props_bytes)))
            subrecord.extend(props_bytes)
            subrecord.extend(b"\x00")

        # Write outline vertices directly - self.outline already includes the
        # closing vertex from parsing (parser reads count+1 vertices).
        # Don't use _outline_for_write which may add an extra closing vertex.
        count_without_closing = max(0, len(self.outline) - 1)
        subrecord.extend(struct.pack("<I", count_without_closing))
        for vertex in self.outline:
            subrecord.append(vertex._is_round_raw & 0xFF)
            subrecord.extend(struct.pack("<i", int(vertex.x)))
            subrecord.extend(struct.pack("<i", int(vertex.y)))
            subrecord.extend(struct.pack("<i", int(vertex.center_x)))
            subrecord.extend(struct.pack("<i", int(vertex.center_y)))
            subrecord.extend(struct.pack("<i", int(vertex.radius)))
            subrecord.extend(struct.pack("<d", float(vertex.start_angle)))
            subrecord.extend(struct.pack("<d", float(vertex.end_angle)))

        for hole in holes:
            subrecord.extend(struct.pack("<I", len(hole)))
            for vertex in hole:
                subrecord.extend(struct.pack("<d", float(vertex.x)))
                subrecord.extend(struct.pack("<d", float(vertex.y)))

        record = bytearray()
        record.append(0x0B)
        # Use original SR1 length bytes if available (some records have non-standard values)
        if self._sr1_length_bytes is not None:
            record.extend(self._sr1_length_bytes)
        else:
            record.extend(struct.pack("<I", len(subrecord)))
        record.extend(subrecord)

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB region record discriminator.
        """
        return PcbRecordType.REGION

    @staticmethod
    def _outline_vertices_without_closing_duplicate(
        vertices: list[PcbExtendedVertex],
    ) -> list[PcbExtendedVertex]:
        """
        Trim duplicated closing vertex emitted by ShapeBasedRegions stream.
        """
        if len(vertices) < 2:
            return vertices

        first = vertices[0]
        last = vertices[-1]
        if math.isclose(first.x_mils, last.x_mils, abs_tol=1e-6) and math.isclose(
            first.y_mils, last.y_mils, abs_tol=1e-6
        ):
            return vertices[:-1]

        return vertices

    def _arc_segment_commands(
        self,
        ctx: "PcbSvgRenderContext",
        current: PcbExtendedVertex,
        nxt: PcbExtendedVertex,
    ) -> list[str]:
        """
        Build SVG arc command(s) for one ShapeBasedRegion arc segment.
        """
        sx_svg = ctx.x_to_svg(current.x_mils)
        sy_svg = ctx.y_to_svg(current.y_mils)
        ex_svg = ctx.x_to_svg(nxt.x_mils)
        ey_svg = ctx.y_to_svg(nxt.y_mils)

        radius_mm = current.radius_mils * 0.0254
        if radius_mm <= 0.0:
            return [f"L {ctx.fmt(ex_svg)} {ctx.fmt(ey_svg)}"]

        start_deg = float(current.start_angle)
        end_deg = float(current.end_angle)
        sweep_ccw = (end_deg - start_deg) % 360.0
        raw_delta = end_deg - start_deg
        full_circle = (
            math.isclose(sweep_ccw, 0.0, abs_tol=1e-9)
            and not math.isclose(raw_delta, 0.0, abs_tol=1e-9)
            and math.hypot(ex_svg - sx_svg, ey_svg - sy_svg) <= 1e-6
        )

        if full_circle:
            mid_deg = start_deg + 180.0
            mx_mils = current.center_x_mils + current.radius_mils * math.cos(
                math.radians(mid_deg)
            )
            my_mils = current.center_y_mils + current.radius_mils * math.sin(
                math.radians(mid_deg)
            )
            mx_svg = ctx.x_to_svg(mx_mils)
            my_svg = ctx.y_to_svg(my_mils)
            sweep_flag = "1" if raw_delta >= 0.0 else "0"
            radius = ctx.fmt(radius_mm)
            return [
                f"A {radius} {radius} 0 1 {sweep_flag} {ctx.fmt(mx_svg)} {ctx.fmt(my_svg)}",
                f"A {radius} {radius} 0 1 {sweep_flag} {ctx.fmt(ex_svg)} {ctx.fmt(ey_svg)}",
            ]

        large_arc_int = 1 if sweep_ccw > 180.0 else 0
        cx_svg = ctx.x_to_svg(current.center_x_mils)
        cy_svg = ctx.y_to_svg(current.center_y_mils)
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
                default_sweep_flag=1 if raw_delta >= 0.0 else 0,
            )
        )

        radius = ctx.fmt(radius_mm)
        return [
            (
                f"A {radius} {radius} 0 {large_arc_int} {sweep_flag} "
                f"{ctx.fmt(ex_svg)} {ctx.fmt(ey_svg)}"
            )
        ]

    def _outline_path(self, ctx: "PcbSvgRenderContext") -> str:
        """
        Build SVG path for the region outline (line + arc segments).
        """
        vertices = self._outline_vertices_without_closing_duplicate(self.outline)
        if len(vertices) < 3:
            return ""

        first = vertices[0]
        parts = [
            f"M {ctx.fmt(ctx.x_to_svg(first.x_mils))} {ctx.fmt(ctx.y_to_svg(first.y_mils))}"
        ]

        count = len(vertices)
        for idx, current in enumerate(vertices):
            nxt = vertices[(idx + 1) % count]
            if current.is_round and current.radius > 0:
                parts.extend(self._arc_segment_commands(ctx, current, nxt))
            else:
                parts.append(
                    f"L {ctx.fmt(ctx.x_to_svg(nxt.x_mils))} {ctx.fmt(ctx.y_to_svg(nxt.y_mils))}"
                )

        parts.append("Z")
        return " ".join(parts)

    @staticmethod
    def _hole_path(ctx: "PcbSvgRenderContext", hole: list[PcbSimpleVertex]) -> str:
        """
        Build SVG subpath for a hole contour.
        """
        if len(hole) < 3:
            return ""
        first = hole[0]
        parts = [
            f"M {ctx.fmt(ctx.x_to_svg(first.x_mils))} {ctx.fmt(ctx.y_to_svg(first.y_mils))}"
        ]
        for vertex in hole[1:]:
            parts.append(
                f"L {ctx.fmt(ctx.x_to_svg(vertex.x_mils))} {ctx.fmt(ctx.y_to_svg(vertex.y_mils))}"
            )
        parts.append("Z")
        return " ".join(parts)

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | None = None,
    ) -> list[str]:
        """
        Render shape-based region to an SVG filled path.
        """
        if ctx is None:
            return []
        if for_layer is not None and int(self.layer) != for_layer.value:
            return []

        outline_path = self._outline_path(ctx)
        if not outline_path:
            return []

        color = stroke
        if color is None:
            try:
                color = ctx.layer_color(PcbLayer(int(self.layer)))
            except ValueError:
                color = "#808080"

        path_parts = [outline_path]
        for hole in self.holes:
            hole_path = self._hole_path(ctx, hole)
            if hole_path:
                path_parts.append(hole_path)

        attrs = [
            f'd="{" ".join(path_parts)}"',
            f'fill="{html.escape(color)}"',
            'fill-rule="evenodd"',
            'stroke="none"',
        ]
        if include_metadata:
            attrs.append('data-primitive="shapebased-region"')
            attrs.extend(ctx.layer_metadata_attrs(int(self.layer)))
            attrs.append(f'data-kind="{self.kind.name}"')
            attrs.extend(
                ctx.relationship_metadata_attrs(
                    net_index=self.net_index,
                    component_index=self.component_index,
                )
            )
            element_id_attr = ctx.primitive_id_attr(
                "shapebased-region",
                self,
                layer_id=int(self.layer),
                role="main",
            )
            if element_id_attr:
                attrs.append(element_id_attr)

        return [f"<path {' '.join(attrs)}/>"]

    def __str__(self) -> str:
        """
        String representation.
        """
        return (
            f"AltiumPcbShapeBasedRegion(layer={self.layer}, kind={self.kind.name}, "
            f"{len(self.outline)} vertices, {len(self.holes)} holes)"
        )

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        return (
            f"AltiumPcbShapeBasedRegion(layer={self.layer}, kind={self.kind.name}, "
            f"net={self.net_index}, poly={self.polygon_index}, "
            f"outline={len(self.outline)}, holes={len(self.holes)})"
        )
