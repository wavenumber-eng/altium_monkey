"""
Altium Board

Represents board-level information from Altium PcbDoc files,
including board outline geometry from the Board6/Data record.
"""

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from .altium_pcb_stream_helpers import (
    parse_altium_bool_token as _parse_altium_bool_token,
)
from .altium_pcb_stream_helpers import parse_altium_int_token as _parse_altium_int_token

log = logging.getLogger(__name__)

# Regex patterns for V9 indexed fields
_V9_STACK_RE = re.compile(r"^V9_STACK_LAYER(\d+)_(.+)$", re.IGNORECASE)
_V9_CACHE_RE = re.compile(r"^V9_CACHE_LAYER(\d+)_(.+)$", re.IGNORECASE)
_PLANE_NETNAME_RE = re.compile(r"^PLANE(\d+)NETNAME$", re.IGNORECASE)
_HOLESHAPE_HASH_INDEX_RE = re.compile(r"^HASHVALUE#(\d+)$", re.IGNORECASE)
_LAYER_PAIR_LOW_RE = re.compile(r"^LAYERPAIR(\d+)LOW$", re.IGNORECASE)
_SUBSTACK_FIELD_FAMILIES = (
    (
        re.compile(r"^V9_SUBSTACK(\d+)_ID$", re.IGNORECASE),
        {
            "id": "V9_SUBSTACK{index}_ID",
            "name": "V9_SUBSTACK{index}_NAME",
            "is_flex": "V9_SUBSTACK{index}_ISFLEX",
            "show_top_dielectric": "V9_SUBSTACK{index}_SHOWTOPDIELECTRIC",
            "show_bottom_dielectric": "V9_SUBSTACK{index}_SHOWBOTTOMDIELECTRIC",
            "service": "V9_SUBSTACK{index}_SERVICE",
            "used_by_primitives": "V9_SUBSTACK{index}_USEDBYPRIMS",
            "stackup_type": "V9_SUBSTACK{index}_TYPE",
        },
        "v9",
    ),
    (
        re.compile(r"^SUBSTACK(\d+)_ID$", re.IGNORECASE),
        {
            "id": "SUBSTACK{index}_ID",
            "name": "SUBSTACK{index}_NAME",
            "is_flex": "SUBSTACK{index}_ISFLEX",
            "show_top_dielectric": "SUBSTACK{index}_SHOWTOPDIELECTRIC",
            "show_bottom_dielectric": "SUBSTACK{index}_SHOWBOTTOMDIELECTRIC",
            "service": "SUBSTACK{index}_SERVICE",
            "used_by_primitives": "SUBSTACK{index}_USEDBYPRIMS",
            "stackup_type": "SUBSTACK{index}_TYPE",
        },
        "legacy",
    ),
    (
        re.compile(r"^LAYERSUBSTACK_V8_(\d+)ID$", re.IGNORECASE),
        {
            "id": "LAYERSUBSTACK_V8_{index}ID",
            "name": "LAYERSUBSTACK_V8_{index}NAME",
            "is_flex": "LAYERSUBSTACK_V8_{index}ISFLEX",
            "show_top_dielectric": "LAYERSUBSTACK_V8_{index}SHOWTOPDIELECTRIC",
            "show_bottom_dielectric": "LAYERSUBSTACK_V8_{index}SHOWBOTTOMDIELECTRIC",
            "service": "LAYERSUBSTACK_V8_{index}SERVICE",
            "used_by_primitives": "LAYERSUBSTACK_V8_{index}USEDBYPRIMS",
            "stackup_type": "LAYERSUBSTACK_V8_{index}TYPE",
        },
        "v8",
    ),
)
_HOLESHAPE_HASH_KEY_VALUE_RE = re.compile(
    r"^\[([-\d]+)\]\[([-\d]+)\]\[([-\d]+)\]\[([-\d]+)\](?:\[[^\]]*\])?$",
    re.IGNORECASE,
)


def _order_segments(
    segments: list[tuple[float, float, float, float]], tol: float = 1.0
) -> list["BoardOutlineVertex"]:
    """
    Order line segments into a closed polygon by connecting head-to-tail.

    Uses a greedy nearest-endpoint algorithm with tolerance matching.

    Args:
        segments: List of (x1, y1, x2, y2) tuples in mils
        tol: Distance tolerance for endpoint matching (mils)

    Returns:
        Ordered list of BoardOutlineVertex forming a closed polygon
    """
    if not segments:
        return []

    remaining = list(segments)
    # Start with first segment
    current = remaining.pop(0)
    ordered = [BoardOutlineVertex(x_mils=current[0], y_mils=current[1])]
    tail_x, tail_y = current[2], current[3]

    while remaining:
        found = False
        for i, seg in enumerate(remaining):
            sx1, sy1, sx2, sy2 = seg
            # Check if segment start matches current tail
            if abs(sx1 - tail_x) < tol and abs(sy1 - tail_y) < tol:
                ordered.append(BoardOutlineVertex(x_mils=sx1, y_mils=sy1))
                tail_x, tail_y = sx2, sy2
                remaining.pop(i)
                found = True
                break
            # Check if segment end matches current tail (reverse direction)
            if abs(sx2 - tail_x) < tol and abs(sy2 - tail_y) < tol:
                ordered.append(BoardOutlineVertex(x_mils=sx2, y_mils=sy2))
                tail_x, tail_y = sx1, sy1
                remaining.pop(i)
                found = True
                break

        if not found:
            # No matching segment found - break to avoid infinite loop
            log.warning(f"Board outline: {len(remaining)} unconnected segments")
            break

    return ordered


@dataclass
class AltiumLayerStackup:
    """
    PCB layer stackup information.

    Represents a single layer in the PCB stackup with copper and dielectric properties.

    Attributes:
        layer_id: Layer ID (1-30 for signal layers, 32 for bottom)
        name: Layer name (e.g., "Top Layer", "Signal Layer 2", "Bottom Layer")
        copper_thickness: Copper thickness in mils
        diel_constant: Dielectric constant
        diel_height: Dielectric height in mils
        diel_material: Dielectric material name (e.g., "FR-4", "1067MS(74)")
        diel_type: Dielectric type (0=Core, 2=Prepreg)
        mech_enabled: Is this a mechanical layer
        layer_next: Index of next layer in stackup
        layer_prev: Index of previous layer in stackup
    """

    layer_id: int = 0
    name: str = ""
    copper_thickness: float = 0.0  # mils
    diel_constant: float = 0.0
    diel_height: float = 0.0  # mils
    diel_material: str = ""
    diel_type: int = 0
    mech_enabled: bool = False
    layer_next: int = 0
    layer_prev: int = 0

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        return (
            f"AltiumLayerStackup(id={self.layer_id}, name='{self.name}', "
            f"copper={self.copper_thickness}mil, diel={self.diel_material})"
        )


@dataclass
class AltiumLayerV9:
    """
    Single layer in the V9 physical stackup.

    Parsed from V9_STACK_LAYER{N}_ indexed fields in the Board6/Data record.
    The V9 format is the authoritative modern layer definition

    The physical stack is ordered top-to-bottom:
        Top Paste -> Top Overlay -> Top Solder -> Top Layer -> Dielectric ->
        Bottom Layer -> Bottom Solder -> Bottom Overlay -> Bottom Paste

    Attributes:
        layer_id: Integer ID used by primitives (e.g., 16777217 for Top Layer)
        name: Display name (e.g., "Top Layer", "Dielectric 1")
        stack_index: Position in physical stack (0=topmost)
        copper_thickness: Copper thickness in mils (0 for non-copper layers)
        diel_constant: Dielectric constant (Er)
        diel_loss_tangent: Dielectric dissipation factor/loss tangent
        diel_height: Dielectric thickness in mils
        diel_material: Material name (e.g., "FR-4", "Solder Resist")
        diel_type: 0=Core, 1=Prepreg, 3=SolderMask
        mech_enabled: Whether this mechanical layer is enabled
        used_by_prims: Whether any primitives exist on this layer
        component_placement: 0=none, 1=top, 2=bottom
    """

    layer_id: int = 0
    name: str = ""
    stack_index: int = 0
    copper_thickness: float = 0.0  # mils
    diel_constant: float = 0.0
    diel_loss_tangent: float = 0.0
    diel_height: float = 0.0  # mils
    diel_material: str = ""
    diel_type: int = 0
    mech_enabled: bool = False
    used_by_prims: bool = False
    component_placement: int = 0  # 0=none, 1=top, 2=bottom

    @property
    def is_copper(self) -> bool:
        """
        True if this is a copper (signal) layer.
        """
        return self.copper_thickness > 0

    @property
    def is_dielectric(self) -> bool:
        """
        True if this is a dielectric layer.
        """
        return self.diel_height > 0

    def __repr__(self) -> str:
        if self.is_copper:
            return (
                f"AltiumLayerV9(id={self.layer_id}, name='{self.name}', "
                f"copper={self.copper_thickness}mil)"
            )
        if self.is_dielectric:
            return (
                f"AltiumLayerV9(id={self.layer_id}, name='{self.name}', "
                f"diel={self.diel_material}, h={self.diel_height}mil)"
            )
        return f"AltiumLayerV9(id={self.layer_id}, name='{self.name}')"


@dataclass
class BoardOutlineVertex:
    """
    Single board-outline vertex in public PCB mil units.

    The segment type is attached to the starting vertex: a plain vertex starts
    a line segment to the next vertex, while an arc vertex starts a circular arc
    segment to the next vertex using the stored center, radius, and angles.
    """

    x_mils: float
    y_mils: float
    is_arc: bool = False
    center_x_mils: float = 0.0
    center_y_mils: float = 0.0
    radius_mils: float = 0.0
    start_angle_deg: float | None = None
    end_angle_deg: float | None = None

    @classmethod
    def line(cls, x_mils: float, y_mils: float) -> "BoardOutlineVertex":
        """
        Create a vertex that starts a straight segment to the next vertex.
        """
        return cls(x_mils=float(x_mils), y_mils=float(y_mils))

    @classmethod
    def arc(
        cls,
        x_mils: float,
        y_mils: float,
        *,
        center_mils: tuple[float, float],
        radius_mils: float,
        start_angle_degrees: float,
        end_angle_degrees: float,
    ) -> "BoardOutlineVertex":
        """
        Create a vertex that starts a circular arc segment to the next vertex.
        """
        if radius_mils <= 0:
            raise ValueError("radius_mils must be positive")
        return cls(
            x_mils=float(x_mils),
            y_mils=float(y_mils),
            is_arc=True,
            center_x_mils=float(center_mils[0]),
            center_y_mils=float(center_mils[1]),
            radius_mils=float(radius_mils),
            start_angle_deg=float(start_angle_degrees),
            end_angle_deg=float(end_angle_degrees),
        )


def resolve_outline_arc_segment(
    start_vertex: "BoardOutlineVertex",
    end_vertex: "BoardOutlineVertex",
    tol_mils: float = 0.5,
) -> tuple[bool, float]:
    """
    Resolve board-outline arc direction and sweep for start->end segment.

        Returns:
            (clockwise, sweep_deg) where sweep_deg is in [0, 360).
    """
    cx = start_vertex.center_x_mils
    cy = start_vertex.center_y_mils

    sx = start_vertex.x_mils - cx
    sy = start_vertex.y_mils - cy
    ex = end_vertex.x_mils - cx
    ey = end_vertex.y_mils - cy

    # Fallback from endpoint vectors (minor-arc assumption).
    cross = sx * ey - sy * ex
    clockwise = cross < 0.0
    if clockwise:
        start_ang = math.degrees(math.atan2(sy, sx)) % 360.0
        end_ang = math.degrees(math.atan2(ey, ex)) % 360.0
        sweep = (start_ang - end_ang) % 360.0
    else:
        start_ang = math.degrees(math.atan2(sy, sx)) % 360.0
        end_ang = math.degrees(math.atan2(ey, ex)) % 360.0
        sweep = (end_ang - start_ang) % 360.0

    sa = start_vertex.start_angle_deg
    ea = start_vertex.end_angle_deg
    if sa is None or ea is None:
        return clockwise, sweep

    # If SA/EA are available, use them to disambiguate direction and major arcs.
    r = start_vertex.radius_mils
    if r <= 0.0:
        r = math.hypot(sx, sy)
    if r <= 0.0:
        return clockwise, sweep

    sa_rad = math.radians(sa)
    ea_rad = math.radians(ea)
    sa_pt = (cx + r * math.cos(sa_rad), cy + r * math.sin(sa_rad))
    ea_pt = (cx + r * math.cos(ea_rad), cy + r * math.sin(ea_rad))

    curr = (start_vertex.x_mils, start_vertex.y_mils)
    nxt = (end_vertex.x_mils, end_vertex.y_mils)

    curr_to_sa = math.hypot(curr[0] - sa_pt[0], curr[1] - sa_pt[1])
    curr_to_ea = math.hypot(curr[0] - ea_pt[0], curr[1] - ea_pt[1])
    next_to_sa = math.hypot(nxt[0] - sa_pt[0], nxt[1] - sa_pt[1])
    next_to_ea = math.hypot(nxt[0] - ea_pt[0], nxt[1] - ea_pt[1])

    span = (ea - sa) % 360.0
    if span == 0.0:
        span = 360.0

    ccw_match = curr_to_sa <= tol_mils and next_to_ea <= tol_mils
    cw_match = curr_to_ea <= tol_mils and next_to_sa <= tol_mils

    # Tiny/near-degenerate arcs on older boards can satisfy both toleranced
    # match patterns. In that case, pick the orientation with the lower total
    # endpoint error instead of relying on branch order.
    if ccw_match and cw_match:
        ccw_error = curr_to_sa + next_to_ea
        cw_error = curr_to_ea + next_to_sa
        if cw_error < ccw_error:
            return True, span
        return False, span

    # CCW case: current near SA, next near EA.
    if ccw_match:
        return False, span

    # CW case: current near EA, next near SA.
    if cw_match:
        return True, span

    return clockwise, sweep


@dataclass
class AltiumBoardOutline:
    """
    Board outline geometry.

    Primary source: VX0/VY0..VXn/VYn indexed fields in Board6/Data record.
    Fallback: Reconstruct from keepout-layer tracks/arcs.

    The outline is a closed polygon defined by ordered vertices.
    Cutouts are holes in the board (e.g., mounting slots) from regions
    marked as board cutouts.
    """

    vertices: list[BoardOutlineVertex] = field(default_factory=list)
    cutouts: list[list[BoardOutlineVertex]] = field(default_factory=list)

    @classmethod
    def from_points_mils(
        cls,
        points_mils: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    ) -> "AltiumBoardOutline":
        """
        Create a straight-segment outline from footprint-plane point tuples.

        Args:
            points_mils: Ordered outline vertices as `(x_mils, y_mils)` tuples.

        Returns:
            `AltiumBoardOutline` with straight line segments between vertices.
        """
        if len(points_mils) < 3:
            raise ValueError("Board outline requires at least 3 points")
        return cls(
            vertices=[
                BoardOutlineVertex.line(float(x_mils), float(y_mils))
                for x_mils, y_mils in points_mils
            ]
        )

    @classmethod
    def rectangle_mils(
        cls,
        *,
        left_mils: float,
        bottom_mils: float,
        right_mils: float,
        top_mils: float,
    ) -> "AltiumBoardOutline":
        """
        Create a rectangular outline from explicit mil-unit bounds.

        Args:
            left_mils: Left X coordinate in mils.
            bottom_mils: Bottom Y coordinate in mils.
            right_mils: Right X coordinate in mils.
            top_mils: Top Y coordinate in mils.

        Returns:
            `AltiumBoardOutline` rectangle with four straight segments.
        """
        if right_mils <= left_mils or top_mils <= bottom_mils:
            raise ValueError("Rectangle bounds must have positive width and height")
        return cls.from_points_mils(
            (
                (left_mils, bottom_mils),
                (right_mils, bottom_mils),
                (right_mils, top_mils),
                (left_mils, top_mils),
            )
        )

    @property
    def vertex_count(self) -> int:
        """
        Number of ordered outer-outline vertices.
        """
        return len(self.vertices)

    @property
    def points_mils(self) -> tuple[tuple[float, float], ...]:
        """
        Return outline vertex coordinates as `(x_mils, y_mils)` tuples.
        """
        return tuple((vertex.x_mils, vertex.y_mils) for vertex in self.vertices)

    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """
        Returns (min_x, min_y, max_x, max_y) in mils.
        """
        if not self.vertices:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [v.x_mils for v in self.vertices]
        ys = [v.y_mils for v in self.vertices]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class AltiumBoardSubstack:
    """
    Typed rigid/flex substack metadata parsed from board settings.
    """

    index: int
    field_family: str
    source_stackup_ref: str
    name: str
    is_flex: bool | None = None
    show_top_dielectric: bool | None = None
    show_bottom_dielectric: bool | None = None
    service_stackup: bool | None = None
    used_by_primitives: bool | None = None
    raw_stackup_type: str = ""


@dataclass(frozen=True)
class AltiumBoardLayerPair:
    """
    Typed drill-pair metadata parsed from board settings.
    """

    pair_index: int
    low_layer_token: str
    high_layer_token: str
    source_substack_refs: tuple[str, ...] = ()
    drill_pair_type_raw: int | None = None
    is_backdrill: bool | None = None
    is_inverted: bool | None = None
    drill_guide: bool | None = None
    drill_drawing: bool | None = None

    @property
    def name(self) -> str:
        return f"{self.low_layer_token}-{self.high_layer_token}"

    def to_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            f"LAYERPAIR{self.pair_index}LOW": self.low_layer_token,
            f"LAYERPAIR{self.pair_index}HIGH": self.high_layer_token,
        }
        if self.drill_guide is not None:
            record[f"LAYERPAIR{self.pair_index}DRILLGUIDE"] = self.drill_guide
        if self.drill_drawing is not None:
            record[f"LAYERPAIR{self.pair_index}DRILLDRAWING"] = self.drill_drawing
        if self.is_backdrill is not None:
            record[f"LAYERPAIR{self.pair_index}BACKDRILL"] = self.is_backdrill
        if self.drill_pair_type_raw is not None:
            record[f"LAYERPAIR{self.pair_index}DRILLPAIRTYPE"] = (
                self.drill_pair_type_raw
            )
        if self.is_inverted is not None:
            record[f"LAYERPAIR{self.pair_index}INVERTED"] = self.is_inverted
        for substack_index, substack_ref in enumerate(self.source_substack_refs):
            record[f"LAYERPAIR{self.pair_index}SUBSTACK_{substack_index}"] = (
                substack_ref
            )
        return record


@dataclass
class AltiumBoard:
    """
    Board-level information from Altium PcbDoc file.

    Stores board metadata parsed from the Board6/Data stream.

    Attributes:
        origin_x: Board origin X coordinate in mils (from ORIGINX field)
        origin_y: Board origin Y coordinate in mils (from ORIGINY field)
        layer_stackup: Legacy LAYER1-32 stackup definitions
        v9_stack: V9 physical stackup (top to bottom), authoritative in AD25+
        v9_layer_cache: V9 layer ID -> name mapping (102 entries for standard boards)
        display_unit: 0=Imperial (mils), 1=Metric (mm)
        raw_record: Original text record dict from Board6/Data

    Position Offset:
        The origin coordinates represent the board's coordinate system offset.
        Component positions are typically relative to this origin.

        To get absolute component position:
            absolute_x = component_x - board_origin_x
            absolute_y = component_y - board_origin_y

    Layer Stackup:
        Two stackup representations are available:
        - layer_stackup: Legacy LAYER1-32 format (copper layers only, IDs 1-32)
        - v9_stack: Modern V9 format with full physical stack including
          paste, overlay, solder mask, copper, and dielectric layers.
          V9 layer IDs are large integers (e.g., 16777217 for Top Layer).

        Use layer_name() to resolve any layer ID to its display name.
    """

    origin_x: float = 0.0  # mils
    origin_y: float = 0.0  # mils
    layer_stackup: list[AltiumLayerStackup] = field(default_factory=list)
    v9_stack: list[AltiumLayerV9] = field(default_factory=list)
    v9_layer_cache: dict[int, str] = field(default_factory=dict)
    display_unit: int = 0  # 0=Imperial, 1=Metric
    outline: AltiumBoardOutline | None = None
    raw_record: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        """
        Return a copy of the source board record for serialization/passthrough.
        """
        return dict(self.raw_record or {})

    @property
    def plane_net_names_by_index(self) -> dict[int, str]:
        """
        Return internal-plane index to net-name assignments from board metadata.
        """
        mapping: dict[int, str] = {}
        for key, value in (self.raw_record or {}).items():
            match = _PLANE_NETNAME_RE.fullmatch(str(key or ""))
            if not match:
                continue
            net_name = str(value or "").strip()
            if not net_name or net_name == "(No Net)":
                continue
            mapping[int(match.group(1))] = net_name
        return mapping

    @property
    def hole_shape_symbol_map(self) -> dict[tuple[int, int, int, int], int]:
        """
        Return typed HOLESHAPEHASH drill-symbol assignments from board metadata.
        """
        mapping: dict[tuple[int, int, int, int], int] = {}
        raw_record = self.raw_record or {}
        for key, value in raw_record.items():
            value_match = _HOLESHAPE_HASH_INDEX_RE.fullmatch(str(key or ""))
            if not value_match:
                continue

            index = value_match.group(1)
            key_text = str(raw_record.get(f"HASHKEY#{index}", "") or "").strip()
            key_match = _HOLESHAPE_HASH_KEY_VALUE_RE.fullmatch(key_text)
            if not key_match:
                continue

            try:
                symbol_code = int(str(value or "").strip())
            except (TypeError, ValueError):
                continue

            mapping[
                (
                    int(key_match.group(1)),
                    int(key_match.group(2)),
                    int(key_match.group(3)),
                    int(key_match.group(4)),
                )
            ] = symbol_code
        return mapping

    @property
    def has_legacy_hole_shape_hash(self) -> bool:
        """
        True when Board6/Data uses the older 4-field HASHKEY encoding.
        """
        raw_record = self.raw_record or {}
        for key, value in raw_record.items():
            if not str(key or "").upper().startswith("HASHKEY#"):
                continue
            if str(value or "").strip().count("][") == 3:
                return True
        return False

    @property
    def substacks(self) -> tuple[AltiumBoardSubstack, ...]:
        """
        Return typed rigid/flex substack definitions from board metadata.
        """
        raw_record = self.raw_record or {}
        for index_pattern, field_template, family_name in _SUBSTACK_FIELD_FAMILIES:
            indices = sorted(
                {
                    int(match.group(1))
                    for key in raw_record
                    for match in [index_pattern.match(str(key or ""))]
                    if match is not None
                }
            )
            if not indices:
                continue

            results: list[AltiumBoardSubstack] = []
            seen_refs: set[str] = set()
            for index in indices:
                field_names = {
                    field_name: template.format(index=index)
                    for field_name, template in field_template.items()
                }
                source_stackup_ref = str(
                    raw_record.get(field_names["id"], "") or ""
                ).strip()
                if not source_stackup_ref or source_stackup_ref in seen_refs:
                    continue
                seen_refs.add(source_stackup_ref)
                name = str(raw_record.get(field_names["name"], "") or "").strip()
                results.append(
                    AltiumBoardSubstack(
                        index=index,
                        field_family=family_name,
                        source_stackup_ref=source_stackup_ref,
                        name=name or f"Board Layer Stack {index}",
                        is_flex=_parse_altium_bool_token(
                            raw_record.get(field_names["is_flex"])
                        ),
                        show_top_dielectric=_parse_altium_bool_token(
                            raw_record.get(field_names["show_top_dielectric"])
                        ),
                        show_bottom_dielectric=_parse_altium_bool_token(
                            raw_record.get(field_names["show_bottom_dielectric"])
                        ),
                        service_stackup=_parse_altium_bool_token(
                            raw_record.get(field_names["service"])
                        ),
                        used_by_primitives=_parse_altium_bool_token(
                            raw_record.get(field_names["used_by_primitives"])
                        ),
                        raw_stackup_type=str(
                            raw_record.get(field_names["stackup_type"], "") or ""
                        ).strip(),
                    )
                )
            if results:
                return tuple(results)
        return ()

    @property
    def primary_substack(self) -> AltiumBoardSubstack | None:
        substacks = self.substacks
        if not substacks:
            return None
        return substacks[0]

    @property
    def substack_is_flex_flags(self) -> tuple[bool, ...]:
        flags: list[bool] = []
        for substack in self.substacks:
            if substack.is_flex is not None:
                flags.append(substack.is_flex)
        return tuple(flags)

    @property
    def master_stack_is_flex(self) -> bool | None:
        for key in ("V9_MASTERSTACK_ISFLEX", "LAYERMASTERSTACK_V8ISFLEX"):
            parsed = _parse_altium_bool_token((self.raw_record or {}).get(key))
            if parsed is not None:
                return parsed
        return None

    def substack_layer_context_value(
        self,
        source_stackup_ref: str,
        layer_index: int,
    ) -> int | None:
        """
        Return the board's substack layer-context token for a physical layer.
        """
        source_stackup_ref = str(source_stackup_ref or "").strip()
        if not source_stackup_ref:
            return None
        candidate_keys = (
            f"V9_STACK_LAYER{int(layer_index)}_{source_stackup_ref}CONTEXT",
            f"LAYER_V8_{int(layer_index)}_{source_stackup_ref}CONTEXT",
        )
        raw_record = self.raw_record or {}
        for key in candidate_keys:
            parsed = _parse_altium_int_token(raw_record.get(key))
            if parsed is not None:
                return parsed
        return None

    @property
    def layer_pairs(self) -> tuple[AltiumBoardLayerPair, ...]:
        """
        Return typed drill-pair metadata from board settings.
        """
        raw_record = self.raw_record or {}
        pair_indices = sorted(
            {
                int(match.group(1))
                for key in raw_record
                for match in [_LAYER_PAIR_LOW_RE.match(str(key or ""))]
                if match is not None
            }
        )
        pairs: list[AltiumBoardLayerPair] = []
        for pair_index in pair_indices:
            low_layer_token = str(
                raw_record.get(f"LAYERPAIR{pair_index}LOW", "") or ""
            ).strip()
            high_layer_token = str(
                raw_record.get(f"LAYERPAIR{pair_index}HIGH", "") or ""
            ).strip()
            if not low_layer_token or not high_layer_token:
                continue
            source_substack_refs: list[str] = []
            for key, value in sorted(raw_record.items(), key=lambda item: str(item[0])):
                if (
                    re.match(
                        rf"^LAYERPAIR{pair_index}SUBSTACK_\d+$",
                        str(key or ""),
                        re.IGNORECASE,
                    )
                    is None
                ):
                    continue
                ref = str(value or "").strip()
                if ref and ref not in source_substack_refs:
                    source_substack_refs.append(ref)
            pairs.append(
                AltiumBoardLayerPair(
                    pair_index=pair_index,
                    low_layer_token=low_layer_token,
                    high_layer_token=high_layer_token,
                    source_substack_refs=tuple(source_substack_refs),
                    drill_pair_type_raw=_parse_altium_int_token(
                        raw_record.get(f"LAYERPAIR{pair_index}DRILLPAIRTYPE")
                    ),
                    is_backdrill=_parse_altium_bool_token(
                        raw_record.get(f"LAYERPAIR{pair_index}BACKDRILL")
                    ),
                    is_inverted=_parse_altium_bool_token(
                        raw_record.get(f"LAYERPAIR{pair_index}INVERTED")
                    ),
                    drill_guide=_parse_altium_bool_token(
                        raw_record.get(f"LAYERPAIR{pair_index}DRILLGUIDE")
                    ),
                    drill_drawing=_parse_altium_bool_token(
                        raw_record.get(f"LAYERPAIR{pair_index}DRILLDRAWING")
                    ),
                )
            )
        return tuple(pairs)

    @property
    def enabled_mechanical_v7_save_ids(self) -> tuple[int, ...]:
        """
        Return enabled mechanical-layer V7 save IDs from the board cache.
        """
        raw_record = self.raw_record or {}
        enabled: list[int] = []
        for key, value in raw_record.items():
            match = re.match(
                r"^V9_CACHE_LAYER(\d+)_MECHENABLED$", str(key or ""), re.IGNORECASE
            )
            if match is None or str(value or "").strip().upper() != "TRUE":
                continue
            raw_v7_id = raw_record.get(f"V9_CACHE_LAYER{match.group(1)}_LAYERID")
            v7_id = _parse_altium_int_token(raw_v7_id)
            if v7_id is None:
                continue
            if ((v7_id >> 16) & 0xFF) != 2:
                continue
            enabled.append(v7_id)
        return tuple(enabled)

    @property
    def component_layer_flip_map(self) -> dict[int, int]:
        from .altium_pcbdoc_layers import _build_component_layer_flip_map

        return _build_component_layer_flip_map(self.raw_record or {})

    @property
    def component_v7_layer_flip_map(self) -> dict[int, int]:
        from .altium_pcbdoc_layers import _build_component_v7_layer_flip_map

        return _build_component_v7_layer_flip_map(self.raw_record or {})

    def display_name_for_legacy_layer(self, layer_id: int) -> str:
        """
        Resolve a legacy PCB layer id to a user-facing display name.
        """
        from .altium_record_types import PcbLayer
        from .altium_resolved_layer_stack import legacy_layer_to_v7_save_id

        raw_name = str(
            (self.raw_record or {}).get(f"LAYER{int(layer_id)}NAME", "") or ""
        ).strip()
        if raw_name:
            return raw_name

        try:
            v7_id = legacy_layer_to_v7_save_id(int(layer_id))
        except ValueError:
            v7_id = None
        if v7_id is not None:
            name = self.v9_layer_cache.get(v7_id)
            if name:
                return name

        if layer_id == PcbLayer.TOP.value:
            return "Top Layer"
        if layer_id == PcbLayer.BOTTOM.value:
            return "Bottom Layer"
        if PcbLayer.TOP.value < layer_id < PcbLayer.BOTTOM.value:
            return f"Mid-Layer {layer_id - PcbLayer.TOP.value}"
        if layer_id == PcbLayer.TOP_OVERLAY.value:
            return "Top Overlay"
        if layer_id == PcbLayer.BOTTOM_OVERLAY.value:
            return "Bottom Overlay"
        if layer_id == PcbLayer.TOP_PASTE.value:
            return "Top Paste"
        if layer_id == PcbLayer.BOTTOM_PASTE.value:
            return "Bottom Paste"
        if layer_id == PcbLayer.TOP_SOLDER.value:
            return "Top Solder"
        if layer_id == PcbLayer.BOTTOM_SOLDER.value:
            return "Bottom Solder"
        if layer_id == PcbLayer.KEEPOUT.value:
            return "Keep-Out Layer"
        if layer_id == PcbLayer.DRILL_GUIDE.value:
            return "Drill Guide"
        if layer_id == PcbLayer.DRILL_DRAWING.value:
            return "Drill Drawing"
        if layer_id == PcbLayer.MULTI_LAYER.value:
            return "Multi-Layer"
        if (
            PcbLayer.INTERNAL_PLANE_1.value
            <= layer_id
            <= PcbLayer.INTERNAL_PLANE_16.value
        ):
            return f"Internal Plane {layer_id - PcbLayer.INTERNAL_PLANE_1.value + 1}"
        if PcbLayer.MECHANICAL_1.value <= layer_id <= PcbLayer.MECHANICAL_16.value:
            return f"Mechanical {layer_id - PcbLayer.MECHANICAL_1.value + 1}"
        return f"Unknown ({layer_id})"

    @staticmethod
    def _make_mils(s: str) -> float:
        """
        Convert Altium position string to mils (float).

        Args:
            s: Position string (e.g., "27514.9995mil")

        Returns:
            Position in mils (e.g., 27514.9995)
        """
        return float(str(s).strip("mil"))

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "AltiumBoard":
        """
        Create AltiumBoard from Board6/Data record.

        Args:
            record: Parsed Board6/Data record dict

        Returns:
            AltiumBoard instance
        """
        origin_x = 0.0
        origin_y = 0.0

        if "ORIGINX" in record:
            origin_x = cls._make_mils(str(record["ORIGINX"]))

        if "ORIGINY" in record:
            origin_y = cls._make_mils(str(record["ORIGINY"]))

        # Parse layer stackup (layers 1-32)
        # Layer 1 = TOP, Layers 2-31 = internal signal layers, Layer 32 = BOTTOM
        layer_stackup = []

        for layer_id in range(1, 33):
            # Check if layer has any definition
            name_key = f"LAYER{layer_id}NAME"
            copthick_key = f"LAYER{layer_id}COPTHICK"

            # Skip layers that don't have copper thickness defined
            if copthick_key not in record:
                continue

            # Parse layer properties
            layer = AltiumLayerStackup()
            layer.layer_id = layer_id

            # Layer name
            if name_key in record:
                layer.name = str(record[name_key])
            elif layer_id == 1:
                layer.name = "Top Layer"
            elif layer_id == 32:
                layer.name = "Bottom Layer"
            else:
                layer.name = f"Mid-Layer {layer_id - 1}"

            # Copper thickness
            if copthick_key in record:
                layer.copper_thickness = cls._make_mils(str(record[copthick_key]))

            # Dielectric constant
            dielconst_key = f"LAYER{layer_id}DIELCONST"
            if dielconst_key in record:
                try:
                    layer.diel_constant = float(record[dielconst_key])
                except (ValueError, TypeError):
                    layer.diel_constant = 0.0

            # Dielectric height
            dielheight_key = f"LAYER{layer_id}DIELHEIGHT"
            if dielheight_key in record:
                layer.diel_height = cls._make_mils(str(record[dielheight_key]))

            # Dielectric material
            dielmaterial_key = f"LAYER{layer_id}DIELMATERIAL"
            if dielmaterial_key in record:
                layer.diel_material = str(record[dielmaterial_key])

            # Dielectric type (0=Core, 2=Prepreg)
            dieltype_key = f"LAYER{layer_id}DIELTYPE"
            if dieltype_key in record:
                try:
                    layer.diel_type = int(record[dieltype_key])
                except (ValueError, TypeError):
                    layer.diel_type = 0

            # Mechanical enabled
            mechenabled_key = f"LAYER{layer_id}MECHENABLED"
            if mechenabled_key in record:
                layer.mech_enabled = str(record[mechenabled_key]).upper() == "TRUE"

            # Layer next/prev
            next_key = f"LAYER{layer_id}NEXT"
            if next_key in record:
                try:
                    layer.layer_next = int(record[next_key])
                except (ValueError, TypeError):
                    layer.layer_next = 0

            prev_key = f"LAYER{layer_id}PREV"
            if prev_key in record:
                try:
                    layer.layer_prev = int(record[prev_key])
                except (ValueError, TypeError):
                    layer.layer_prev = 0

            layer_stackup.append(layer)

        # Parse display unit
        display_unit = int(record.get("DISPLAYUNIT", "0"))

        # Parse V9 layer stack and cache
        v9_stack = cls._parse_v9_stack(record)
        v9_layer_cache = cls._parse_v9_cache(record)

        if v9_stack:
            log.debug(
                "V9 stack: %d layers, cache: %d entries",
                len(v9_stack),
                len(v9_layer_cache),
            )
        elif layer_stackup:
            log.debug("Legacy stackup: %d layers (no V9 data)", len(layer_stackup))

        board = cls(
            origin_x=origin_x,
            origin_y=origin_y,
            layer_stackup=layer_stackup,
            v9_stack=v9_stack,
            v9_layer_cache=v9_layer_cache,
            display_unit=display_unit,
            raw_record=record,
        )

        # Parse board outline polygon from indexed VX/VY/KIND fields
        board.outline = board._parse_outline_from_record(record)

        return board

    @classmethod
    def _parse_v9_stack(cls, record: dict[str, Any]) -> list[AltiumLayerV9]:
        """
        Parse V9 physical layer stack from Board6/Data record.

        V9_STACK_LAYER{N}_ indexed fields define the physical stackup
        ordered top-to-bottom. Each layer has at minimum LAYERID and NAME.
        Copper layers add COPTHICK, dielectric layers add DIELHEIGHT/DIELMATERIAL.

        Returns:
            List of AltiumLayerV9 ordered by stack position (top to bottom).
            Empty list if V9 fields are not present.
        """
        # Collect all V9_STACK_LAYER fields grouped by index
        stack_fields: dict[int, dict[str, str]] = {}
        for key, value in record.items():
            m = _V9_STACK_RE.match(str(key))
            if m:
                idx = int(m.group(1))
                suffix = m.group(2).upper()
                if idx not in stack_fields:
                    stack_fields[idx] = {}
                stack_fields[idx][suffix] = str(value)

        if not stack_fields:
            return []

        layers = []
        for idx in sorted(stack_fields):
            fields = stack_fields[idx]
            layer = AltiumLayerV9(stack_index=idx)
            layer.name = fields.get("NAME", "")
            try:
                layer.layer_id = int(fields.get("LAYERID", "0"))
            except (ValueError, TypeError):
                layer.layer_id = 0

            layer.used_by_prims = fields.get("USEDBYPRIMS", "FALSE").upper() == "TRUE"

            # Copper properties
            if "COPTHICK" in fields:
                layer.copper_thickness = cls._make_mils(fields["COPTHICK"])

            # Component placement
            if "COMPONENTPLACEMENT" in fields:
                try:
                    layer.component_placement = int(fields["COMPONENTPLACEMENT"])
                except (ValueError, TypeError):
                    pass

            # Dielectric properties
            if "DIELTYPE" in fields:
                try:
                    layer.diel_type = int(fields["DIELTYPE"])
                except (ValueError, TypeError):
                    pass
            if "DIELCONST" in fields:
                try:
                    layer.diel_constant = float(fields["DIELCONST"])
                except (ValueError, TypeError):
                    pass
            if "DIELLOSSTANGENT" in fields:
                try:
                    layer.diel_loss_tangent = float(fields["DIELLOSSTANGENT"])
                except (ValueError, TypeError):
                    pass
            if "DIELHEIGHT" in fields:
                layer.diel_height = cls._make_mils(fields["DIELHEIGHT"])
            if "DIELMATERIAL" in fields:
                layer.diel_material = fields["DIELMATERIAL"]

            # Mechanical enabled
            if "MECHENABLED" in fields:
                layer.mech_enabled = fields["MECHENABLED"].upper() == "TRUE"

            layers.append(layer)

        return layers

    @staticmethod
    def _parse_v9_cache(record: dict[str, Any]) -> dict[int, str]:
        """
        Parse V9 layer cache (ID -> name mapping) from Board6/Data record.

        V9_CACHE_LAYER{N}_ indexed fields provide a flat lookup table of
        all possible layers (typically 102 entries for standard AD25 boards).
        This maps integer layer IDs to display names for primitive resolution.

        Returns:
            Dict mapping layer_id (int) -> name (str).
            Empty dict if V9 fields are not present.
        """
        cache_fields: dict[int, dict[str, str]] = {}
        for key, value in record.items():
            m = _V9_CACHE_RE.match(str(key))
            if m:
                idx = int(m.group(1))
                suffix = m.group(2).upper()
                if idx not in cache_fields:
                    cache_fields[idx] = {}
                cache_fields[idx][suffix] = str(value)

        if not cache_fields:
            return {}

        cache: dict[int, str] = {}
        for idx in sorted(cache_fields):
            fields = cache_fields[idx]
            name = fields.get("NAME", "")
            try:
                layer_id = int(fields.get("LAYERID", "0"))
            except (ValueError, TypeError):
                continue
            if layer_id and name:
                cache[layer_id] = name

        return cache

    def layer_name(self, layer_id: int) -> str:
        """
        Resolve a layer ID to its display name.

        Handles both legacy IDs (1=Top, 32=Bottom) and V9 IDs (16777217, etc.).
        Uses v9_layer_cache first, then falls back to PcbLayer enum names.

        Args:
            layer_id: Integer layer ID from a primitive record

        Returns:
            Display name string (e.g., "Top Layer", "Bottom Overlay").
            Returns "Unknown (ID)" if not found in any lookup.
        """
        # Try V9 cache first (most complete)
        if self.v9_layer_cache:
            name = self.v9_layer_cache.get(layer_id)
            if name:
                return name

        # Fall back to PcbLayer enum (legacy 1-74 IDs)
        from .altium_record_types import PcbLayer

        try:
            pcb_layer = PcbLayer(layer_id)
            return pcb_layer.to_json_name()
        except ValueError:
            pass

        return f"Unknown ({layer_id})"

    @staticmethod
    def _parse_outline_from_record(record: dict[str, Any]) -> AltiumBoardOutline:
        """
        Parse board outline polygon from Board6/Data indexed fields.

        The Board6/Data record stores the board outline as:
            KIND0, VX0, VY0, CX0, CY0, SA0, EA0, R0,
            KIND1, VX1, VY1, CX1, CY1, SA1, EA1, R1,
        where KIND=0 is a line segment and KIND=1 is an arc.
        The last vertex repeats the first to close the polygon.

        Args:
            record: Parsed Board6/Data text record dict

        Returns:
            AltiumBoardOutline with vertices parsed from record
        """
        vertices = []
        i = 0
        while True:
            vx_key = f"VX{i}"
            vy_key = f"VY{i}"
            if vx_key not in record or vy_key not in record:
                break

            kind = int(record.get(f"KIND{i}", "0"))
            x_mils = float(str(record[vx_key]).strip("mil"))
            y_mils = float(str(record[vy_key]).strip("mil"))

            if kind == 1:
                # Arc segment
                cx = float(str(record.get(f"CX{i}", "0")).strip("mil"))
                cy = float(str(record.get(f"CY{i}", "0")).strip("mil"))
                r = float(str(record.get(f"R{i}", "0")).strip("mil"))
                sa = float(str(record.get(f"SA{i}", "0")))
                ea = float(str(record.get(f"EA{i}", "0")))
                vertices.append(
                    BoardOutlineVertex(
                        x_mils=x_mils,
                        y_mils=y_mils,
                        is_arc=True,
                        center_x_mils=cx,
                        center_y_mils=cy,
                        radius_mils=r,
                        start_angle_deg=sa,
                        end_angle_deg=ea,
                    )
                )
            else:
                # Line segment
                vertices.append(BoardOutlineVertex(x_mils=x_mils, y_mils=y_mils))

            i += 1

        # Remove closing duplicate vertex (last == first)
        if len(vertices) >= 2:
            first, last = vertices[0], vertices[-1]
            if (
                abs(first.x_mils - last.x_mils) < 0.01
                and abs(first.y_mils - last.y_mils) < 0.01
            ):
                vertices.pop()

        if vertices:
            log.debug(f"Board outline from record: {len(vertices)} vertices")
        else:
            log.debug("No outline vertices in Board6/Data record")

        return AltiumBoardOutline(vertices=vertices)

    def extract_outline_from_primitives(
        self,
        tracks: list[Any],
        regions: list[Any],
        arcs: list[Any] | None = None,
        layer: int | None = None,
    ) -> None:
        """
        Extract board outline from tracks/arcs on a given layer (fallback method).

        Collects tracks and arcs on the specified layer, connects them
        head-to-tail into an ordered vertex list, and collects regions marked
        as board cutouts.

        Use this when the Board6/Data record has no outline vertices (e.g.,
        older files or custom workflows) or when extracting an outline from
        an arbitrary layer.

        Args:
            tracks: List of AltiumPcbTrack records
            regions: List of AltiumPcbRegion records
            arcs: List of AltiumPcbArc records (optional)
            layer: Layer ID to collect from (default: KEEPOUT=56)
        """
        import math
        from .altium_record_types import PcbLayer

        if layer is None:
            layer = PcbLayer.KEEPOUT.value  # 56

        # Collect tracks as line segments: (x1, y1, x2, y2) in mils
        segments = []
        for track in tracks:
            if track.layer == layer:
                segments.append(
                    (
                        track.start_x_mils,
                        track.start_y_mils,
                        track.end_x_mils,
                        track.end_y_mils,
                    )
                )

        # Collect arcs as segments using their start/end points
        if arcs:
            for arc in arcs:
                if arc.layer == layer:
                    # Compute start/end points from center, radius, angles
                    cx = arc.center_x_mils
                    cy = arc.center_y_mils
                    r = arc.radius_mils
                    sa_rad = math.radians(arc.start_angle)
                    ea_rad = math.radians(arc.end_angle)
                    x1 = cx + r * math.cos(sa_rad)
                    y1 = cy + r * math.sin(sa_rad)
                    x2 = cx + r * math.cos(ea_rad)
                    y2 = cy + r * math.sin(ea_rad)
                    segments.append((x1, y1, x2, y2))

        if not segments:
            log.debug(f"No tracks/arcs on layer {layer} for outline extraction")
            self.outline = AltiumBoardOutline()
            return

        # Order segments into a closed polygon by connecting head-to-tail
        vertices = _order_segments(segments)

        # Collect board cutout regions
        cutouts = []
        for region in regions:
            props = dict(getattr(region, "properties", {}) or {})
            is_prop = (
                str(props.get("ISBOARDCUTOUT", "")).strip("\x00").upper() == "TRUE"
            )
            is_flag = bool(getattr(region, "is_board_cutout", False))
            kind = getattr(region, "kind", None)
            try:
                kind_value = (
                    int(getattr(kind, "value", kind)) if kind is not None else None
                )
            except (TypeError, ValueError):
                kind_value = None
            is_legacy_kind = kind_value == 3
            if is_prop or is_flag or is_legacy_kind:
                cutout_verts = [
                    BoardOutlineVertex(x_mils=v.x_mils, y_mils=v.y_mils)
                    for v in region.outline_vertices
                ]
                if cutout_verts:
                    cutouts.append(cutout_verts)

        self.outline = AltiumBoardOutline(vertices=vertices, cutouts=cutouts)
        log.debug(
            f"Board outline from primitives: {len(vertices)} vertices, {len(cutouts)} cutouts"
        )

    def __str__(self) -> str:
        """
        String representation.
        """
        v9_info = f", v9={len(self.v9_stack)} layers" if self.v9_stack else ""
        return (
            f"AltiumBoard(origin=({self.origin_x}, {self.origin_y}) mils, "
            f"{len(self.layer_stackup)} legacy layers{v9_info})"
        )

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        return (
            f"AltiumBoard(origin_x={self.origin_x}, origin_y={self.origin_y}, "
            f"legacy={len(self.layer_stackup)}, v9={len(self.v9_stack)})"
        )
