"""
Parse PCB component body records from the component-body streams.
"""

import logging
import struct

from .altium_pcb_enums import PcbBodyProjection
from .altium_pcb_enums import PcbRegionKind
from .altium_record_types import PcbGraphicalObject, PcbRecordType

# Reuse vertex classes from shapebased_region
from .altium_record_pcb__shapebased_region import (
    PcbExtendedVertex,
    PcbSimpleVertex,
)

log = logging.getLogger(__name__)


class AltiumPcbComponentBody(PcbGraphicalObject):
    """
    PCB component body primitive record.

    Represents a 3D extruded shape for component mechanical representation.
    Extends Region with additional 3D properties.

    Found in:
    - ComponentBodies6/Data (body definitions)
    - ShapeBasedComponentBodies6/Data (rendered bodies)

    Attributes:
        layer: PCB layer (typically mechanical layers)
        is_locked: Is body locked
        is_keepout: Is keepout region
        net_index: Net index (uint16)
        polygon_index: Polygon index (uint16)
        component_index: Component index (uint16)
        hole_count: Number of holes/cutouts

        # Region properties (inherited behavior)
        kind: Region type (COPPER, BOARD_CUTOUT, etc.)
        is_shapebased: TRUE for rendered geometry
        subpoly_index: Sub-polygon index
        union_index: Union index for polygon grouping

        # 3D-specific properties
        standoff_height: Height from PCB surface to body bottom (internal units)
        overall_height: Total height of body (internal units)
        body_projection: Projection mode (TOP/BOTTOM/BOTH/NONE)
        body_color_3d: 3D view color (RGB uint32)
        body_opacity_3d: 3D view opacity (0.0-1.0)
        identifier: Unique identifier string
        texture: Texture file path
        texture_center_x: Texture center X (internal units)
        texture_center_y: Texture center Y (internal units)
        texture_size_x: Texture size X (internal units)
        texture_size_y: Texture size Y (internal units)
        texture_rotation: Texture rotation (degrees)
        arc_resolution: Arc resolution (mils)
        cavity_height: Cavity height (internal units)

        # Geometry (from Region)
        outline: List of PcbExtendedVertex (outline vertices with arc support)
        holes: List of lists of PcbSimpleVertex (hole vertices)
    """

    def __init__(self) -> None:
        super().__init__()

        # Region header fields
        self.layer: int = 13  # Default to MECHANICAL13
        self.is_locked: bool = False
        self.is_keepout: bool = False
        self.net_index: int = 0xFFFF
        self.polygon_index: int = 0xFFFF
        self.component_index: int = 0xFFFF
        self.hole_count: int = 0

        # Region properties
        self.kind: PcbRegionKind = PcbRegionKind.COPPER
        self.is_shapebased: bool = False
        self.subpoly_index: int = 0
        self.union_index: int = 0

        # 3D-specific properties
        self.standoff_height: int = 0  # Internal units
        self.overall_height: int = 0  # Internal units
        self.body_projection: PcbBodyProjection = PcbBodyProjection.NONE
        self.body_color_3d: int = 0x808080  # Default gray
        self.body_opacity_3d: float = 1.0
        self.identifier: str = ""
        self.texture: str = ""
        self.texture_center_x: int = 0
        self.texture_center_y: int = 0
        self.texture_size_x: int = 0
        self.texture_size_y: int = 0
        self.texture_rotation: float = 0.0
        self.arc_resolution: float = 0.5  # mils
        self.cavity_height: int = 0
        self.v7_layer: str = ""
        self.name: str = ""
        self.model_id: str = ""
        self.model_checksum: int = 0
        self.model_is_embedded: bool = False
        self.model_name: str = ""
        self.model_2d_x: int = 0
        self.model_2d_y: int = 0
        self.model_2d_rotation: float = 0.0
        self.model_3d_rotx: float = 0.0
        self.model_3d_roty: float = 0.0
        self.model_3d_rotz: float = 0.0
        self.model_3d_dz: int = 0
        self.model_type: int = 0
        self.model_source: str = ""
        self.model_extruded_min_z: int = 0
        self.model_extruded_max_z: int = 0
        self.model_cylinder_radius: int = 0
        self.model_cylinder_height: int = 0
        self.model_sphere_radius: int = 0
        self.body_override_color: bool = False
        self.model_snap_count: int = 0
        self.model_s0x: int = 0
        self.model_s0y: int = 0
        self.model_s0z: int = 0

        # Geometry
        self.outline: list[PcbExtendedVertex] = []
        self.holes: list[list[PcbSimpleVertex]] = []

        # Raw properties dict
        self.properties: dict[str, str] = {}
        # Optional parser hint from stream context:
        #   False -> ComponentBodies6 (double-vertex payload)
        #   True  -> ShapeBasedComponentBodies6 (extended-vertex payload)
        #   None  -> infer from properties/size
        self._force_extended_vertices: bool | None = None
        self._tail_payload: bytes = b""

        # Raw byte preservation for byte-identical round-trip
        self._flags1_raw: int = 0x04
        self._skip_bytes_9: bytes = b"\x00" * 5
        self._skip_bytes_16: bytes = b"\x00" * 2
        self._properties_raw: bytes | None = None
        self._properties_raw_signature: tuple | None = (
            None  # Snapshot of property fields at parse time
        )
        # Native component-body records include the C-string terminator inside the
        # length-prefixed property payload. If it is emitted as an extra byte after
        # the payload, Altium reads the following outline count at the wrong offset.
        self._props_has_null_terminator: bool = False
        # Geometry format that was successfully parsed: (use_extended, include_closing_vertex)
        self._geometry_variant: tuple[bool, bool] | None = None

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse ComponentBody record from binary data.

        ComponentBody uses the same structure as Region but with a distinct
        record discriminator and different properties.

        Args:
            data: Binary data buffer
            offset: Starting offset in buffer

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If record type is not 0x0C (COMPONENT_BODY)
            struct.error: If binary data is malformed
        """
        original_offset = offset
        if len(data) < original_offset + 5:
            raise ValueError("Data too short for COMPONENT_BODY header")

        # Type byte verification
        type_byte = data[offset]
        if type_byte != PcbRecordType.COMPONENT_BODY:
            raise ValueError(
                "Expected COMPONENT_BODY type "
                f"0x{int(PcbRecordType.COMPONENT_BODY):02X}, got 0x{type_byte:02X}"
            )
        offset += 1

        # SubRecord 1 length
        subrecord_length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        subrecord_start = offset
        subrecord_end = subrecord_start + subrecord_length
        if subrecord_end > len(data):
            raise ValueError(
                "ComponentBody subrecord truncated: "
                f"expected {subrecord_length} bytes, got {len(data) - subrecord_start}"
            )

        content = memoryview(data)[subrecord_start:subrecord_end]
        cursor = 0

        def _require(size: int, label: str) -> None:
            if cursor + size > len(content):
                raise ValueError(
                    f"ComponentBody {label} exceeds declared subrecord size "
                    f"(need {size} bytes at {cursor}, size={len(content)})"
                )

        def _read_u8(label: str) -> int:
            nonlocal cursor
            _require(1, label)
            value = int(content[cursor])
            cursor += 1
            return value

        def _read_u16(label: str) -> int:
            nonlocal cursor
            _require(2, label)
            value = struct.unpack_from("<H", content, cursor)[0]
            cursor += 2
            return value

        def _read_u32(label: str) -> int:
            nonlocal cursor
            _require(4, label)
            value = struct.unpack_from("<I", content, cursor)[0]
            cursor += 4
            return value

        def _read_i32(label: str) -> int:
            nonlocal cursor
            _require(4, label)
            value = struct.unpack_from("<i", content, cursor)[0]
            cursor += 4
            return value

        def _read_f64(label: str) -> float:
            nonlocal cursor
            _require(8, label)
            value = struct.unpack_from("<d", content, cursor)[0]
            cursor += 8
            return value

        # Layer
        self.layer = _read_u8("layer")

        # Flags1 (bit 0x04 inverted = is_locked)
        flags1 = _read_u8("flags1")
        self._flags1_raw = flags1
        self.is_locked = (flags1 & 0x04) == 0

        # Flags2 (value 2 = is_keepout)
        flags2 = _read_u8("flags2")
        self.is_keepout = flags2 == 2

        # Net, polygon, component indices
        self.net_index = _read_u16("net_index")
        self.polygon_index = _read_u16("polygon_index")
        self.component_index = _read_u16("component_index")

        # Bytes at offset 9-13 (union_index + padding)
        _require(5, "reserved_5")
        self._skip_bytes_9 = bytes(content[cursor : cursor + 5])
        cursor += 5

        # Hole count
        self.hole_count = _read_u16("hole_count")

        # Bytes at offset 16-17
        _require(2, "reserved_2")
        self._skip_bytes_16 = bytes(content[cursor : cursor + 2])
        cursor += 2

        # Properties (length-prefixed text)
        props_len = _read_u32("properties_length")
        _require(props_len, "properties_payload")
        self._properties_raw = bytes(content[cursor : cursor + props_len])
        props_text = self._properties_raw.decode("utf-8", errors="ignore")
        cursor += props_len

        # Parse properties
        self.properties = {}
        for pair in props_text.split("|"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                self.properties[key] = value

        # Skip null terminator if present
        self._props_has_null_terminator = (
            cursor < len(content) and int(content[cursor]) == 0
        )
        if self._props_has_null_terminator:
            cursor += 1

        # Parse properties into typed fields
        self._parse_properties()

        # Outline vertices count (stored count; some stream variants add a closing
        # vertex in payload and some do not).
        outline_count_raw = _read_u32("outline_vertex_count")
        geometry_start = cursor

        def _parse_geometry(
            use_extended: bool, include_closing_vertex: bool
        ) -> tuple[list[PcbExtendedVertex], list[list[PcbSimpleVertex]], int]:
            local_cursor = geometry_start
            local_outline: list[PcbExtendedVertex] = []
            local_holes: list[list[PcbSimpleVertex]] = []
            outline_vertices = outline_count_raw + (1 if include_closing_vertex else 0)

            def _local_require(size: int, label: str) -> None:
                if local_cursor + size > len(content):
                    raise ValueError(
                        f"ComponentBody {label} exceeds declared subrecord size "
                        f"(need {size} bytes at {local_cursor}, size={len(content)})"
                    )

            def _local_read_u8(label: str) -> int:
                nonlocal local_cursor
                _local_require(1, label)
                value = int(content[local_cursor])
                local_cursor += 1
                return value

            def _local_read_u32(label: str) -> int:
                nonlocal local_cursor
                _local_require(4, label)
                value = struct.unpack_from("<I", content, local_cursor)[0]
                local_cursor += 4
                return value

            def _local_read_i32(label: str) -> int:
                nonlocal local_cursor
                _local_require(4, label)
                value = struct.unpack_from("<i", content, local_cursor)[0]
                local_cursor += 4
                return value

            def _local_read_f64(label: str) -> float:
                nonlocal local_cursor
                _local_require(8, label)
                value = struct.unpack_from("<d", content, local_cursor)[0]
                local_cursor += 8
                return value

            for _ in range(outline_vertices):
                vertex = PcbExtendedVertex()
                if use_extended:
                    vertex.is_round = _local_read_u8("outline.is_round") != 0
                    vertex.x = _local_read_i32("outline.x")
                    vertex.y = _local_read_i32("outline.y")
                    vertex.center_x = _local_read_i32("outline.center_x")
                    vertex.center_y = _local_read_i32("outline.center_y")
                    vertex.radius = _local_read_i32("outline.radius")
                    vertex.start_angle = _local_read_f64("outline.start_angle")
                    vertex.end_angle = _local_read_f64("outline.end_angle")
                else:
                    vertex.is_round = False
                    vertex.x = _local_read_f64("outline.x")
                    vertex.y = _local_read_f64("outline.y")
                    vertex.center_x = vertex.x
                    vertex.center_y = vertex.y
                local_outline.append(vertex)

            for _ in range(self.hole_count):
                num_hole_vertices = _local_read_u32("hole_vertex_count")
                hole_vertices: list[PcbSimpleVertex] = []
                for _ in range(num_hole_vertices):
                    hole_vertex = PcbSimpleVertex()
                    hole_vertex.x = _local_read_f64("hole.x")
                    hole_vertex.y = _local_read_f64("hole.y")
                    hole_vertices.append(hole_vertex)
                local_holes.append(hole_vertices)

            return local_outline, local_holes, local_cursor

        # Prefer stream-hint geometry format first, then fall back to alternatives.
        force_extended = self._force_extended_vertices
        candidate_modes: list[tuple[bool, bool]] = []
        if force_extended is True:
            candidate_modes.extend([(True, True), (True, False), (False, False)])
        elif force_extended is False:
            candidate_modes.extend([(False, False), (True, False), (True, True)])
        elif self.is_shapebased:
            candidate_modes.extend([(True, True), (True, False), (False, False)])
        else:
            candidate_modes.extend([(False, False), (True, False), (True, True)])

        ordered_candidates: list[tuple[bool, bool]] = []
        seen_candidates: set[tuple[bool, bool]] = set()
        for mode in candidate_modes:
            if mode in seen_candidates:
                continue
            seen_candidates.add(mode)
            ordered_candidates.append(mode)

        parsed_outline: list[PcbExtendedVertex] | None = None
        parsed_holes: list[list[PcbSimpleVertex]] | None = None
        parsed_cursor = geometry_start
        parse_errors: list[str] = []

        for use_extended, include_closing_vertex in ordered_candidates:
            try:
                outline, holes, end_cursor = _parse_geometry(
                    use_extended, include_closing_vertex
                )
                parsed_outline = outline
                parsed_holes = holes
                parsed_cursor = end_cursor
                self._geometry_variant = (use_extended, include_closing_vertex)
                break
            except ValueError as exc:
                parse_errors.append(str(exc))

        if parsed_outline is None or parsed_holes is None:
            # Keep record boundaries stable even when geometry variant is unknown.
            self.outline = []
            self.holes = []
            parsed_cursor = geometry_start
            if parse_errors:
                log.debug(
                    "ComponentBody geometry parse fallback to tail passthrough: %s",
                    parse_errors[0],
                )
        else:
            self.outline = parsed_outline
            self.holes = parsed_holes

        cursor = parsed_cursor

        # Preserve any trailing bytes inside the declared record boundary.
        self._tail_payload = bytes(content[cursor:]) if cursor < len(content) else b""

        # Consume the full declared record, not only known parsed fields.
        bytes_consumed = subrecord_end - original_offset

        self._raw_binary = data[original_offset:subrecord_end]
        self._raw_binary_signature = self._state_signature()
        # Snapshot property-field values at parse time for properties_raw validity check
        self._properties_raw_signature = self._properties_field_signature()
        return bytes_consumed

    def _parse_properties(self) -> None:
        """
        Parse properties dict into typed fields.
        """
        # Region properties (inherited behavior)
        pkind = int(self.properties.get("KIND", "0").strip().strip("\x00") or "0")
        is_cutout = (
            self.properties.get("ISBOARDCUTOUT", "FALSE").strip().strip("\x00").upper()
            == "TRUE"
        )

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

        self.is_shapebased = (
            self.properties.get("ISSHAPEBASED", "FALSE").strip().strip("\x00").upper()
            == "TRUE"
        )
        self.subpoly_index = int(
            self.properties.get("SUBPOLYINDEX", "-1").strip().strip("\x00") or "-1"
        )
        self.union_index = int(
            self.properties.get("UNIONINDEX", "0").strip().strip("\x00") or "0"
        )

        # 3D-specific properties
        # Parse heights (values with units like "21.6535mil")
        def parse_mil_value(value_str: str) -> int:
            """
            Parse value like '21.6535mil' to internal units (10k units/mil).
            """
            value_str = value_str.strip().strip("\x00")
            if value_str.lower().endswith("mil"):
                try:
                    mils = float(value_str[:-3].strip())
                except ValueError:
                    return 0
                return int(round(mils * 10000.0))
            if value_str:
                try:
                    return int(round(float(value_str)))
                except ValueError:
                    return 0
            return 0

        def parse_float_value(value_str: str, default: float = 0.0) -> float:
            """
            Parse float values with optional scientific notation and whitespace.
            """
            text = value_str.strip().strip("\x00")
            if not text:
                return float(default)
            try:
                return float(text)
            except ValueError:
                return float(default)

        def parse_int_value(value_str: str, default: int = 0) -> int:
            """
            Parse integer values with whitespace handling.
            """
            text = value_str.strip().strip("\x00")
            if not text:
                return int(default)
            try:
                return int(text)
            except ValueError:
                return int(default)

        standoff_str = self.properties.get("STANDOFFHEIGHT", "0mil")
        self.standoff_height = parse_mil_value(standoff_str)

        overall_str = self.properties.get("OVERALLHEIGHT", "0mil")
        self.overall_height = parse_mil_value(overall_str)

        cavity_str = self.properties.get("CAVITYHEIGHT", "0mil")
        self.cavity_height = parse_mil_value(cavity_str)

        # Body projection
        proj_value = int(
            self.properties.get("BODYPROJECTION", "0").strip().strip("\x00") or "0"
        )
        try:
            self.body_projection = PcbBodyProjection(proj_value)
        except ValueError:
            self.body_projection = PcbBodyProjection.NONE

        # Colors and opacity
        self.body_color_3d = parse_int_value(
            self.properties.get("BODYCOLOR3D", "8421504"), default=8421504
        )
        self.body_opacity_3d = parse_float_value(
            self.properties.get("BODYOPACITY3D", "1.0"), default=1.0
        )

        # Strings
        self.identifier = self.properties.get("IDENTIFIER", "")
        self.texture = self.properties.get("TEXTURE", "")
        self.texture_center_x = parse_mil_value(
            self.properties.get("TEXTURECENTERX", "0mil")
        )
        self.texture_center_y = parse_mil_value(
            self.properties.get("TEXTURECENTERY", "0mil")
        )
        self.texture_size_x = parse_mil_value(
            self.properties.get("TEXTURESIZEX", "0mil")
        )
        self.texture_size_y = parse_mil_value(
            self.properties.get("TEXTURESIZEY", "0mil")
        )
        self.texture_rotation = parse_float_value(
            self.properties.get("TEXTUREROTATION", "0")
        )
        self.v7_layer = self.properties.get("V7_LAYER", "")
        self.name = self.properties.get("NAME", "")
        self.model_id = self.properties.get("MODELID", "")
        self.model_checksum = parse_int_value(
            self.properties.get("MODEL.CHECKSUM", "0")
        )
        self.model_is_embedded = (
            self.properties.get("MODEL.EMBED", "FALSE").upper() == "TRUE"
        )
        model_name = self.properties.get("MODEL.NAME", "")
        self.model_name = "" if not model_name.strip("\x00") else model_name
        self.model_2d_x = parse_mil_value(self.properties.get("MODEL.2D.X", "0mil"))
        self.model_2d_y = parse_mil_value(self.properties.get("MODEL.2D.Y", "0mil"))
        self.model_2d_rotation = parse_float_value(
            self.properties.get("MODEL.2D.ROTATION", "0")
        )
        self.model_3d_rotx = parse_float_value(
            self.properties.get("MODEL.3D.ROTX", "0")
        )
        self.model_3d_roty = parse_float_value(
            self.properties.get("MODEL.3D.ROTY", "0")
        )
        self.model_3d_rotz = parse_float_value(
            self.properties.get("MODEL.3D.ROTZ", "0")
        )
        self.model_3d_dz = parse_mil_value(self.properties.get("MODEL.3D.DZ", "0mil"))
        self.model_type = parse_int_value(self.properties.get("MODEL.MODELTYPE", "0"))
        self.model_source = self.properties.get("MODEL.MODELSOURCE", "")
        self.model_extruded_min_z = parse_mil_value(
            self.properties.get("MODEL.EXTRUDED.MINZ", "0mil")
        )
        self.model_extruded_max_z = parse_mil_value(
            self.properties.get("MODEL.EXTRUDED.MAXZ", "0mil")
        )
        self.model_cylinder_radius = parse_mil_value(
            self.properties.get("MODEL.CYLINDER.RADIUS", "0mil")
        )
        self.model_cylinder_height = parse_mil_value(
            self.properties.get("MODEL.CYLINDER.HEIGHT", "0mil")
        )
        self.model_sphere_radius = parse_mil_value(
            self.properties.get("MODEL.SPHERE.RADIUS", "0mil")
        )
        self.body_override_color = (
            self.properties.get("BODYOVERRIDECOLOR", "FALSE")
            .strip()
            .strip("\x00")
            .upper()
            == "TRUE"
        )
        self.model_snap_count = parse_int_value(
            self.properties.get("MODEL.SNAPCOUNT", "0")
        )
        self.model_s0x = parse_int_value(self.properties.get("MODEL.S0X", "0"))
        self.model_s0y = parse_int_value(self.properties.get("MODEL.S0Y", "0"))
        self.model_s0z = parse_int_value(self.properties.get("MODEL.S0Z", "0"))

        # Arc resolution
        arc_res_str = (
            self.properties.get("ARCRESOLUTION", "0.5mil").strip().strip("\x00")
        )
        if arc_res_str.lower().endswith("mil"):
            self.arc_resolution = parse_float_value(arc_res_str[:-3], default=0.5)
        else:
            self.arc_resolution = parse_float_value(arc_res_str, default=0.5)

    def _properties_field_signature(self) -> tuple:
        """
        Snapshot of all typed fields that contribute to the properties string.

                If this changes from parse time, _properties_raw is stale and must
                be regenerated via _properties_string().
        """
        return (
            int(self.kind),
            bool(self.is_shapebased),
            int(self.subpoly_index),
            int(self.union_index),
            int(self.standoff_height),
            int(self.overall_height),
            int(self.cavity_height),
            int(self.body_projection),
            int(self.body_color_3d),
            float(self.body_opacity_3d),
            str(self.identifier),
            str(self.texture),
            int(self.texture_center_x),
            int(self.texture_center_y),
            int(self.texture_size_x),
            int(self.texture_size_y),
            float(self.texture_rotation),
            float(self.arc_resolution),
            str(self.v7_layer),
            str(self.name),
            str(self.model_id),
            int(self.model_checksum),
            bool(self.model_is_embedded),
            str(self.model_name),
            int(self.model_2d_x),
            int(self.model_2d_y),
            float(self.model_2d_rotation),
            float(self.model_3d_rotx),
            float(self.model_3d_roty),
            float(self.model_3d_rotz),
            int(self.model_3d_dz),
            int(self.model_type),
            str(self.model_source),
            int(self.model_extruded_min_z),
            int(self.model_extruded_max_z),
            int(self.model_cylinder_radius),
            int(self.model_cylinder_height),
            int(self.model_sphere_radius),
            bool(self.body_override_color),
            int(self.model_snap_count),
            int(self.model_s0x),
            int(self.model_s0y),
            int(self.model_s0z),
            tuple(sorted((str(k), str(v)) for k, v in self.properties.items())),
        )

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of semantically known component-body fields.
        """
        return (
            int(self.layer),
            int(self.net_index) if self.net_index is not None else 0xFFFF,
            int(self.polygon_index),
            int(self.component_index) if self.component_index is not None else 0xFFFF,
            int(self.hole_count),
            bool(self.is_locked),
            bool(self.is_keepout),
            int(self.kind),
            bool(self.is_shapebased),
            int(self.subpoly_index),
            int(self.union_index),
            int(self.standoff_height),
            int(self.overall_height),
            int(self.cavity_height),
            int(self.body_projection),
            int(self.body_color_3d),
            float(self.body_opacity_3d),
            str(self.identifier),
            str(self.texture),
            int(self.texture_center_x),
            int(self.texture_center_y),
            int(self.texture_size_x),
            int(self.texture_size_y),
            float(self.texture_rotation),
            float(self.arc_resolution),
            str(self.v7_layer),
            str(self.name),
            str(self.model_id),
            int(self.model_checksum),
            bool(self.model_is_embedded),
            str(self.model_name),
            int(self.model_2d_x),
            int(self.model_2d_y),
            float(self.model_2d_rotation),
            float(self.model_3d_rotx),
            float(self.model_3d_roty),
            float(self.model_3d_rotz),
            int(self.model_3d_dz),
            int(self.model_type),
            str(self.model_source),
            int(self.model_extruded_min_z),
            int(self.model_extruded_max_z),
            int(self.model_cylinder_radius),
            int(self.model_cylinder_height),
            int(self.model_sphere_radius),
            bool(self.body_override_color),
            int(self.model_snap_count),
            int(self.model_s0x),
            int(self.model_s0y),
            int(self.model_s0z),
            tuple(
                (
                    bool(v.is_round),
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
            bytes(self._tail_payload),
            tuple(sorted((str(k), str(v)) for k, v in self.properties.items())),
            int(self._flags1_raw),
            self._skip_bytes_9,
            self._skip_bytes_16,
            self._geometry_variant,
        )

    @staticmethod
    def _format_mil_value(internal_units: int) -> str:
        """
        Format an internal-unit value as '<mils>mil'.
        """
        return f"{(float(internal_units) / 10000.0):.4f}mil"

    @staticmethod
    def _format_legacy_scientific(value: float) -> str:
        """
        Format float with legacy Altium-style exponent padding.
        """
        text = f"{float(value): .14E}"
        mantissa, exponent = text.split("E", 1)
        return f"{mantissa}E{int(exponent):+05d}"

    def _properties_string(self) -> str:
        """
        Build pipe-separated component-body properties from object state.
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
        props["UNIONINDEX"] = str(int(self.union_index))
        props["STANDOFFHEIGHT"] = self._format_mil_value(int(self.standoff_height))
        props["OVERALLHEIGHT"] = self._format_mil_value(int(self.overall_height))
        props["CAVITYHEIGHT"] = self._format_mil_value(int(self.cavity_height))
        props["BODYPROJECTION"] = str(int(self.body_projection))
        props["BODYCOLOR3D"] = str(int(self.body_color_3d))
        props["BODYOPACITY3D"] = str(float(self.body_opacity_3d))
        props["IDENTIFIER"] = str(self.identifier)
        props["TEXTURE"] = str(self.texture)
        props["TEXTURECENTERX"] = self._format_mil_value(int(self.texture_center_x))
        props["TEXTURECENTERY"] = self._format_mil_value(int(self.texture_center_y))
        props["TEXTURESIZEX"] = self._format_mil_value(int(self.texture_size_x))
        props["TEXTURESIZEY"] = self._format_mil_value(int(self.texture_size_y))
        props["TEXTUREROTATION"] = self._format_legacy_scientific(
            float(self.texture_rotation)
        )
        props["ARCRESOLUTION"] = f"{float(self.arc_resolution):.4f}mil"
        props["V7_LAYER"] = str(self.v7_layer)
        props["NAME"] = str(self.name)
        props["MODELID"] = str(self.model_id)
        props["MODEL.CHECKSUM"] = str(int(self.model_checksum))
        props["MODEL.EMBED"] = "TRUE" if self.model_is_embedded else "FALSE"
        props["MODEL.NAME"] = str(self.model_name)
        props["MODEL.2D.X"] = self._format_mil_value(int(self.model_2d_x))
        props["MODEL.2D.Y"] = self._format_mil_value(int(self.model_2d_y))
        props["MODEL.2D.ROTATION"] = f"{float(self.model_2d_rotation):.3f}"
        props["MODEL.3D.ROTX"] = f"{float(self.model_3d_rotx):.3f}"
        props["MODEL.3D.ROTY"] = f"{float(self.model_3d_roty):.3f}"
        props["MODEL.3D.ROTZ"] = f"{float(self.model_3d_rotz):.3f}"
        props["MODEL.3D.DZ"] = self._format_mil_value(int(self.model_3d_dz))
        props["MODEL.MODELTYPE"] = str(int(self.model_type))
        if "MODEL.MODELSOURCE" in props or self.model_source:
            props["MODEL.MODELSOURCE"] = str(self.model_source)
        if "MODEL.EXTRUDED.MINZ" in props or self.model_extruded_min_z:
            props["MODEL.EXTRUDED.MINZ"] = self._format_mil_value(
                int(self.model_extruded_min_z)
            )
        if "MODEL.EXTRUDED.MAXZ" in props or self.model_extruded_max_z:
            props["MODEL.EXTRUDED.MAXZ"] = self._format_mil_value(
                int(self.model_extruded_max_z)
            )
        if "MODEL.CYLINDER.RADIUS" in props or self.model_cylinder_radius:
            props["MODEL.CYLINDER.RADIUS"] = self._format_mil_value(
                int(self.model_cylinder_radius)
            )
        if "MODEL.CYLINDER.HEIGHT" in props or self.model_cylinder_height:
            props["MODEL.CYLINDER.HEIGHT"] = self._format_mil_value(
                int(self.model_cylinder_height)
            )
        if "MODEL.SPHERE.RADIUS" in props or self.model_sphere_radius:
            props["MODEL.SPHERE.RADIUS"] = self._format_mil_value(
                int(self.model_sphere_radius)
            )
        if "BODYOVERRIDECOLOR" in props or self.body_override_color:
            props["BODYOVERRIDECOLOR"] = "TRUE" if self.body_override_color else "FALSE"
        if "MODEL.SNAPCOUNT" in props or self.model_snap_count:
            props["MODEL.SNAPCOUNT"] = str(int(self.model_snap_count))
        if "MODEL.S0X" in props or self.model_s0x:
            props["MODEL.S0X"] = str(int(self.model_s0x))
        if "MODEL.S0Y" in props or self.model_s0y:
            props["MODEL.S0Y"] = str(int(self.model_s0y))
        if "MODEL.S0Z" in props or self.model_s0z:
            props["MODEL.S0Z"] = str(int(self.model_s0z))
        return "|".join(f"{k}={v}" for k, v in props.items())

    @staticmethod
    def _outline_for_write(
        vertices: list[PcbExtendedVertex], is_shapebased: bool
    ) -> list[PcbExtendedVertex]:
        """
        Prepare outline list for serialization with optional closing vertex.
        """
        if not vertices:
            return []
        if not is_shapebased:
            return list(vertices)
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
        Serialize ComponentBody record to binary format.

        Reuses raw binary only when semantic fields are unchanged.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        subrecord = bytearray()
        subrecord.append(max(0, min(255, int(self.layer))))

        # Reconstruct flags1 from raw value, updating only known semantic bits
        flags1 = self._flags1_raw
        flags1 = flags1 & ~0x06  # Clear bits 1 and 2
        if not self.is_locked:
            flags1 |= 0x04
        if self.is_polygon_outline:
            flags1 |= 0x02
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
        subrecord.extend(self._skip_bytes_9)

        holes = self.holes or []
        subrecord.extend(struct.pack("<H", max(0, min(0xFFFF, len(holes)))))
        subrecord.extend(self._skip_bytes_16)

        # Use original properties bytes if available and property fields are unchanged
        props_unchanged = (
            self._properties_raw is not None
            and self._properties_raw_signature is not None
            and self._properties_field_signature() == self._properties_raw_signature
        )
        if props_unchanged:
            subrecord.extend(struct.pack("<I", len(self._properties_raw)))
            subrecord.extend(self._properties_raw)
        else:
            props_bytes = self._properties_string().encode("utf-8", errors="replace")
            if not self._props_has_null_terminator and not props_bytes.endswith(
                b"\x00"
            ):
                props_bytes += b"\x00"
            subrecord.extend(struct.pack("<I", len(props_bytes)))
            subrecord.extend(props_bytes)
        if self._props_has_null_terminator:
            subrecord.extend(b"\x00")

        # Use parsed geometry variant to determine format
        use_extended = self.is_shapebased
        include_closing = True  # default
        if self._geometry_variant is not None:
            use_extended, include_closing = self._geometry_variant

        if use_extended:
            # For shapebased: write count as stored count (excluding closing vertex)
            # If parsed WITH closing vertex, outline already has it - stored count = len - 1
            # If parsed WITHOUT closing vertex, outline has no extra - stored count = len
            if include_closing:
                stored_count = max(0, len(self.outline) - 1)
            else:
                stored_count = len(self.outline)
            subrecord.extend(struct.pack("<I", stored_count))
            for vertex in self.outline:
                subrecord.append(1 if vertex.is_round else 0)
                subrecord.extend(struct.pack("<i", int(vertex.x)))
                subrecord.extend(struct.pack("<i", int(vertex.y)))
                subrecord.extend(struct.pack("<i", int(vertex.center_x)))
                subrecord.extend(struct.pack("<i", int(vertex.center_y)))
                subrecord.extend(struct.pack("<i", int(vertex.radius)))
                subrecord.extend(struct.pack("<d", float(vertex.start_angle)))
                subrecord.extend(struct.pack("<d", float(vertex.end_angle)))
        else:
            subrecord.extend(struct.pack("<I", len(self.outline)))
            for vertex in self.outline:
                subrecord.extend(struct.pack("<d", float(vertex.x)))
                subrecord.extend(struct.pack("<d", float(vertex.y)))

        for hole in holes:
            subrecord.extend(struct.pack("<I", len(hole)))
            for vertex in hole:
                subrecord.extend(struct.pack("<d", float(vertex.x)))
                subrecord.extend(struct.pack("<d", float(vertex.y)))

        if self._tail_payload:
            subrecord.extend(self._tail_payload)

        record = bytearray()
        record.append(0x0C)
        record.extend(struct.pack("<I", len(subrecord)))
        record.extend(subrecord)

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB component-body record discriminator.
        """
        return PcbRecordType.COMPONENT_BODY

    @property
    def standoff_height_mils(self) -> float:
        """
        Standoff height in mils.
        """
        return self.standoff_height / 10000.0

    @property
    def overall_height_mils(self) -> float:
        """
        Overall height in mils.
        """
        return self.overall_height / 10000.0

    @property
    def cavity_height_mils(self) -> float:
        """
        Cavity height in mils.
        """
        return self.cavity_height / 10000.0

    def __str__(self) -> str:
        """
        String representation.
        """
        return (
            f"AltiumPcbComponentBody(layer={self.layer}, kind={self.kind.name}, "
            f"{len(self.outline)} vertices, {len(self.holes)} holes, "
            f"height={self.overall_height_mils:.2f}mil)"
        )

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        return (
            f"AltiumPcbComponentBody(layer={self.layer}, kind={self.kind.name}, "
            f"component={self.component_index}, "
            f"outline={len(self.outline)}, holes={len(self.holes)}, "
            f"standoff={self.standoff_height_mils:.2f}mil, "
            f"height={self.overall_height_mils:.2f}mil)"
        )
