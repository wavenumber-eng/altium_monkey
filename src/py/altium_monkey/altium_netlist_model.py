"""Shared netlist data structures used by the netlist compilation pipeline."""

import json
import uuid
from collections.abc import Callable, Hashable
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from .altium_schematic_contract import SchematicContractLimits


NETLIST_JSON_SCHEMA = "altium_monkey.netlist.b0"
JSON_GENERATOR = "altium_monkey"


@dataclass(frozen=True, slots=True)
class NetlistSourcePage:
    """Correlated physical-page and logical-source identity."""

    physical_document_id: str | None
    source_sheet_file: str | None

    def __post_init__(self) -> None:
        if self.physical_document_id == "" or self.source_sheet_file == "":
            raise ValueError("source-page identities must be non-empty when present")
        if self.physical_document_id is None and self.source_sheet_file is None:
            raise ValueError("source page must carry at least one identity")


# =============================================================================
# Hierarchy Path (immutable value type for sheet hierarchy tracking)
# =============================================================================


@dataclass(frozen=True)
class HierarchyLevel:
    """
    One level in the design sheet hierarchy.

        Represents a single sheet-symbol boundary crossing. Immutable by design
        (frozen dataclass) so hierarchy values remain stable and hashable.

        Fields capture the information needed to identify one downward traversal
        from a parent sheet symbol to a child schematic:
        - sheet_symbol_uid: unique ID of the parent sheet symbol
        - child_filename: child schematic filename
        - designator: sheet-symbol designator text
        - channel_index: expanded repeated-channel index
        - repeat_value: repeated-channel value when present
    """

    sheet_symbol_uid: str  # Parent sheet symbol unique ID
    child_filename: str  # Child SchDoc filename (lowercase)
    designator: str = ""  # Sheet symbol designator (e.g., "Sheet1")
    channel_name: str = ""  # Channel name if multi-channel (e.g., "ChannelA")
    channel_index: int = -1  # 0-based channel instance index (-1 = not multi-channel)
    repeat_value: int | None = None  # Repeat value for REPEAT() patterns


@dataclass(frozen=True)
class HierarchyPath:
    """
    Immutable path through the design hierarchy.

        Tracks position from the top-level sheet down to the current sheet.
        Each level represents a sheet-symbol boundary crossing. Mirrors the
        move_down()/move_up() return new instances rather than mutating in place.

        Used for:
        - Tracking net provenance through the hierarchy
        - Channel instance identification
        - Debug/log output showing full hierarchy context
        - C++ port target (maps to C++ value type)
    """

    levels: tuple[HierarchyLevel, ...] = ()

    def move_down(
        self,
        sheet_symbol_uid: str,
        child_filename: str,
        designator: str = "",
        channel_name: str = "",
        channel_index: int = -1,
        repeat_value: int | None = None,
    ) -> "HierarchyPath":
        """
        Return a new path with one additional level (entering a child sheet).
        """
        return HierarchyPath(
            self.levels
            + (
                HierarchyLevel(
                    sheet_symbol_uid=sheet_symbol_uid,
                    child_filename=child_filename,
                    designator=designator,
                    channel_name=channel_name,
                    channel_index=channel_index,
                    repeat_value=repeat_value,
                ),
            )
        )

    def move_up(self) -> "HierarchyPath":
        """
        Return a new path with the bottom level removed (leaving a child sheet).
        """
        if not self.levels:
            raise ValueError("Cannot move_up from an empty HierarchyPath")
        return HierarchyPath(self.levels[:-1])

    @property
    def depth(self) -> int:
        """
        Number of hierarchy levels (0 = top-level sheet).
        """
        return len(self.levels)

    @property
    def is_empty(self) -> bool:
        """
        True if this is a root-level path (no hierarchy crossings).
        """
        return len(self.levels) == 0

    @property
    def bottom(self) -> HierarchyLevel | None:
        """
        The deepest (most recent) hierarchy level, or None if empty.
        """
        return self.levels[-1] if self.levels else None

    @property
    def is_multi_channel(self) -> bool:
        """
        True if any level in this path involves multi-channel.
        """
        return any(level.channel_index >= 0 for level in self.levels)

    @property
    def unique_id_path(self) -> str:
        """
        Backslash-separated unique-ID path for hierarchy-sensitive identities.
        """
        return "\\".join(level.sheet_symbol_uid for level in self.levels)

    def __str__(self) -> str:
        if not self.levels:
            return "---"
        parts = []
        for level in self.levels:
            name = level.designator or level.child_filename
            if level.channel_name:
                name = f"{name}[{level.channel_name}]"
            parts.append(name)
        return " > ".join(parts)


# =============================================================================
# Union-Find Data Structure (shared by the netlist compilers)
# =============================================================================


UnionFindKey = TypeVar("UnionFindKey", bound=Hashable)


class UnionFind(Generic[UnionFindKey]):
    """
    Generic Union-Find (Disjoint Set Union) with path compression.

        Used by both single-sheet and multi-sheet compilers for efficient
        grouping of connected elements into electrical nets.

        The key type is generic - callers use tuple[int, int] for wire coordinates,
        int for id(net) bridging, or str for power-port name grouping.
    """

    def __init__(self) -> None:
        self._parent: dict[UnionFindKey, UnionFindKey] = {}

    def find(self, p: UnionFindKey) -> UnionFindKey:
        """
        Find root of the set containing p (with path compression).
        """
        if p not in self._parent:
            self._parent[p] = p
        if self._parent[p] != p:
            self._parent[p] = self.find(self._parent[p])
        return self._parent[p]

    def union(self, p1: UnionFindKey, p2: UnionFindKey) -> None:
        """
        Merge the sets containing p1 and p2.
        """
        r1, r2 = self.find(p1), self.find(p2)
        if r1 != r2:
            self._parent[r1] = r2

    def contains(self, p: UnionFindKey) -> bool:
        """
        Check if p is in the structure.
        """
        return p in self._parent

    def add_root(self, p: UnionFindKey) -> None:
        """
        Add p as its own root (if not already present).
        """
        if p not in self._parent:
            self._parent[p] = p


class PinType(Enum):
    """
    Electrical pin types (CAD-agnostic).
    """

    INPUT = 0
    IO = 1
    OUTPUT = 2
    OPEN_COLLECTOR = 3
    PASSIVE = 4
    TRISTATE = 5
    OPEN_EMITTER = 6
    POWER = 7
    UNKNOWN = 99


@dataclass
class Terminal:
    """
    A pin connection point in the netlist.
    """

    designator: str  # Component designator (e.g., "U1")
    pin: str  # Pin designator (e.g., "1", "VCC")
    pin_name: str = ""  # Pin name (e.g., "GPIO5")
    pin_type: PinType = PinType.PASSIVE
    _source_component_uid: str = field(default="", repr=False)
    _source_pin_uid: str = field(default="", repr=False)
    _source_pin_object_id: int = field(default=0, repr=False)
    _source_owner_part_id: int = field(default=1, repr=False)
    component_id: str = ""

    @property
    def full_name(self) -> str:
        """
        Full terminal name (e.g., "U1.1").
        """
        return f"{self.designator}.{self.pin}"


@dataclass
class GraphicalPinRef:
    """
    Pin reference in a net's graphical data using the actual SVG element ID.
    """

    designator: str  # Component designator (e.g., "R1")
    pin: str  # Pin designator (e.g., "1")
    svg_id: str  # Actual pin unique_id (SVG element ID)
    component_id: str = ""


@dataclass
class NetEndpoint:
    """
    Source-owned semantic endpoint for schematic net tracing.

    ``element_id`` names the current render target. ``object_id`` names the
    source electrical object when it differs from the rendered element. The
    optional connection point is in the local source schematic coordinate frame.
    """

    endpoint_id: str
    role: str
    element_id: str = ""
    object_id: str = ""
    name: str = ""
    designator: str = ""
    pin: str = ""
    pin_name: str = ""
    pin_type: PinType = PinType.PASSIVE
    source_sheet: str = ""
    sheet_index: int | None = None
    compiled_sheet_index: int | None = None
    connection_point: tuple[int, int] | None = None
    _source_occurrence_id: str = field(default="", repr=False, compare=False)
    _source_pin_object_id: int = field(default=0, repr=False, compare=False)
    _bus_signal_index: int | None = field(default=None, repr=False, compare=False)
    _harness_entries_path: tuple[str, ...] = field(
        default=(), repr=False, compare=False
    )
    _repeat_value: int | None = field(default=None, repr=False, compare=False)
    _harness_type_name: str = field(default="", repr=False, compare=False)
    _harness_interface_name: str = field(default="", repr=False, compare=False)
    _harness_type_inferred: bool = field(default=False, repr=False, compare=False)
    component_id: str = ""
    source_page: NetlistSourcePage | None = None

    def to_json(self) -> dict:
        """
        Serialize endpoint metadata using the package JSON naming convention.
        """
        data: dict[str, object] = {
            "endpoint_id": self.endpoint_id,
            "role": self.role,
            "element_id": self.element_id,
            "object_id": self.object_id,
            "name": self.name,
            "source_sheet": self.source_sheet,
        }
        if self.designator:
            data["designator"] = self.designator
        if self.pin:
            data["pin"] = self.pin
        if self.pin_name:
            data["pin_name"] = self.pin_name
        if self.role == "pin" or self.pin_type != PinType.PASSIVE:
            data["pin_type"] = self.pin_type.name
        if self.sheet_index is not None:
            data["sheet_index"] = self.sheet_index
        if self.compiled_sheet_index is not None:
            data["compiled_sheet_index"] = self.compiled_sheet_index
        if self.connection_point is not None:
            data["connection_point"] = {
                "x": self.connection_point[0],
                "y": self.connection_point[1],
                "units": "altium_coord",
            }
        return data

    @classmethod
    def from_json(cls, data: dict) -> "NetEndpoint":
        """
        Deserialize endpoint metadata, accepting missing optional fields.
        """
        point_data = data.get("connection_point")
        connection_point = None
        if isinstance(point_data, dict):
            x = point_data.get("x")
            y = point_data.get("y")
            if isinstance(x, int) and isinstance(y, int):
                connection_point = (x, y)

        pin_type_name = data.get("pin_type", "PASSIVE")
        try:
            pin_type = PinType[pin_type_name]
        except KeyError:
            pin_type = PinType.PASSIVE

        return cls(
            endpoint_id=data["endpoint_id"],
            role=data["role"],
            element_id=data.get("element_id", ""),
            object_id=data.get("object_id", ""),
            name=data.get("name", ""),
            designator=data.get("designator", ""),
            pin=data.get("pin", ""),
            pin_name=data.get("pin_name", ""),
            pin_type=pin_type,
            source_sheet=data.get("source_sheet", ""),
            sheet_index=data.get("sheet_index"),
            compiled_sheet_index=data.get("compiled_sheet_index"),
            connection_point=connection_point,
        )


def _sort_string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _sort_int(value: object) -> int:
    return value if isinstance(value, int) else -1


def _unique_sorted_strings(values: list[str]) -> list[str]:
    return sorted(set(values))


def _terminal_sort_key(row: dict) -> tuple[object, ...]:
    return (
        _sort_string(row.get("designator")),
        _sort_string(row.get("pin")),
        _sort_string(row.get("pin_name")),
        _sort_string(row.get("pin_type")),
    )


def _pin_ref_sort_key(row: dict) -> tuple[object, ...]:
    return (
        _sort_string(row.get("designator")),
        _sort_string(row.get("pin")),
        _sort_string(row.get("svg_id")),
    )


def _endpoint_sort_key(endpoint: dict) -> tuple[object, ...]:
    point_data = endpoint.get("connection_point")
    point = point_data if isinstance(point_data, dict) else {}
    return (
        _sort_string(endpoint.get("endpoint_id")),
        _sort_string(endpoint.get("role")),
        _sort_string(endpoint.get("element_id")),
        _sort_string(endpoint.get("object_id")),
        _sort_string(endpoint.get("name")),
        _sort_string(endpoint.get("source_sheet")),
        _sort_string(endpoint.get("designator")),
        _sort_string(endpoint.get("pin")),
        _sort_string(endpoint.get("pin_name")),
        _sort_string(endpoint.get("pin_type")),
        _sort_int(endpoint.get("sheet_index")),
        _sort_int(endpoint.get("compiled_sheet_index")),
        isinstance(point_data, dict),
        _sort_int(point.get("x")),
        _sort_int(point.get("y")),
    )


def _unique_sorted_dicts(
    values: list[dict], key_func: Callable[[dict], tuple[object, ...]]
) -> list[dict]:
    by_key = {}
    for value in values:
        by_key.setdefault(key_func(value), value)
    return [by_key[key] for key in sorted(by_key)]


def _stable_json_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _unique_sorted_json_values(values: list) -> list:
    by_key = {}
    for value in values:
        by_key.setdefault(_stable_json_key(value), value)
    return [by_key[key] for key in sorted(by_key)]


@dataclass
class NetGraphical:
    """
    Graphical elements for a net, grouped by type.

        Each array contains actual SVG element IDs from the source records.
    """

    wires: list[str] = field(default_factory=list)
    junctions: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    power_ports: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    sheet_entries: list[str] = field(default_factory=list)
    pins: list[GraphicalPinRef] = field(default_factory=list)

    def all_svg_ids(self) -> list[str]:
        """
        Get all SVG IDs for highlighting entire net.
        """
        ids = []
        ids.extend(self.wires)
        ids.extend(self.junctions)
        ids.extend(self.labels)
        ids.extend(self.power_ports)
        ids.extend(self.ports)
        ids.extend(self.sheet_entries)
        ids.extend(p.svg_id for p in self.pins)
        return ids

    def merge(self, other: "NetGraphical") -> None:
        """
        Merge another NetGraphical's elements into this one (in-place).
        """
        self.wires.extend(other.wires)
        self.junctions.extend(other.junctions)
        self.labels.extend(other.labels)
        self.power_ports.extend(other.power_ports)
        self.ports.extend(other.ports)
        self.sheet_entries.extend(other.sheet_entries)
        self.pins.extend(other.pins)

    def copy(self) -> "NetGraphical":
        """
        Return a shallow copy with independent lists.
        """
        return NetGraphical(
            wires=list(self.wires),
            junctions=list(self.junctions),
            labels=list(self.labels),
            power_ports=list(self.power_ports),
            ports=list(self.ports),
            sheet_entries=list(self.sheet_entries),
            pins=list(self.pins),
        )

    def contains_id(self, svg_id: str) -> bool:
        """
        Check if any typed array contains the given SVG ID.
        """
        return (
            svg_id in self.wires
            or svg_id in self.junctions
            or svg_id in self.labels
            or svg_id in self.power_ports
            or svg_id in self.ports
            or svg_id in self.sheet_entries
            or any(p.svg_id == svg_id for p in self.pins)
        )

    @property
    def is_empty(self) -> bool:
        """
        True if no graphical elements are stored.
        """
        return not (
            self.wires
            or self.junctions
            or self.labels
            or self.power_ports
            or self.ports
            or self.sheet_entries
            or self.pins
        )

    def to_json(self) -> dict:
        """
        Serialize to JSON-compatible dict.
        """
        return {
            "wires": _unique_sorted_strings(self.wires),
            "junctions": _unique_sorted_strings(self.junctions),
            "labels": _unique_sorted_strings(self.labels),
            "power_ports": _unique_sorted_strings(self.power_ports),
            "ports": _unique_sorted_strings(self.ports),
            "sheet_entries": _unique_sorted_strings(self.sheet_entries),
            "pins": _unique_sorted_dicts(
                [
                    {"designator": p.designator, "pin": p.pin, "svg_id": p.svg_id}
                    for p in self.pins
                ],
                _pin_ref_sort_key,
            ),
        }

    @classmethod
    def from_json(cls, data: dict) -> "NetGraphical":
        """
        Deserialize from JSON dict.
        """
        return cls(
            wires=data.get("wires", []),
            junctions=data.get("junctions", []),
            labels=data.get("labels", []),
            power_ports=data.get("power_ports", []),
            ports=data.get("ports", []),
            sheet_entries=data.get("sheet_entries", []),
            pins=[
                GraphicalPinRef(
                    designator=p["designator"], pin=p["pin"], svg_id=p["svg_id"]
                )
                for p in data.get("pins", [])
            ],
        )


def _hierarchy_path_to_json(hp: HierarchyPath) -> list[dict]:
    """
    Serialize a HierarchyPath to a JSON-compatible list of level dicts.
    """
    return [
        {
            "sheet_symbol_uid": level.sheet_symbol_uid,
            "child_filename": level.child_filename,
            **({"designator": level.designator} if level.designator else {}),
            **({"channel_name": level.channel_name} if level.channel_name else {}),
            **(
                {"channel_index": level.channel_index}
                if level.channel_index >= 0
                else {}
            ),
            **(
                {"repeat_value": level.repeat_value}
                if level.repeat_value is not None
                else {}
            ),
        }
        for level in hp.levels
    ]


def _hierarchy_path_from_json(data: list[dict]) -> HierarchyPath:
    """
    Deserialize a HierarchyPath from a JSON list of level dicts.
    """
    levels = tuple(
        HierarchyLevel(
            sheet_symbol_uid=d.get("sheet_symbol_uid", ""),
            child_filename=d.get("child_filename", ""),
            designator=d.get("designator", ""),
            channel_name=d.get("channel_name", ""),
            channel_index=d.get("channel_index", -1),
            repeat_value=d.get("repeat_value"),
        )
        for d in data
    )
    return HierarchyPath(levels=levels)


def _generate_uid() -> str:
    """
    Generate a 12-character hex UID for net identity.
    """
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True, slots=True)
class _SignalHarnessNameCandidate:
    """Internal naming evidence produced by a real signal-harness traversal."""

    value: str
    value_from_source_object: str
    source_kind: str
    source_priority: int
    source_element_id: str = ""
    source_connection_link_id: str = ""
    harness_entries_path: tuple[str, ...] = ()
    bus_signal_index: int | None = None
    bus_signal_width: int = 0
    bus_signal_offset: int = 0
    bus_signal_prefix: str = ""
    bus_signal_suffix: str = ""
    bus_prefix_full_name: str | None = None
    source_schematic_id: str = ""
    hierarchy_path: tuple[str, ...] = ()
    source_is_multipath: bool = False
    relative_multichannel_depth: int = 0
    full_name: str = ""


@dataclass
class Net:
    """
    A net (electrical connection) in the netlist.
    """

    name: str
    terminals: list[Terminal] = field(default_factory=list)
    graphical: NetGraphical = field(default_factory=NetGraphical)
    auto_named: bool = False  # True if name was auto-generated (e.g., "NetU1_1")
    uid: str = field(default_factory=_generate_uid)
    source_sheets: list[str] = field(
        default_factory=list
    )  # Originating SchDoc filename(s)
    aliases: list[str] = field(
        default_factory=list
    )  # Alternate names from cross-sheet merge
    hierarchy_paths: list[HierarchyPath] = field(
        default_factory=list
    )  # Hierarchy provenance
    endpoints: list[NetEndpoint] = field(
        default_factory=list
    )  # Source-owned semantic endpoints for trace/layout tools
    _name_source_kind: str = field(default="", repr=False, compare=False)
    _name_source_priority: int = field(default=0, repr=False, compare=False)
    _name_source_raw_name: str = field(default="", repr=False, compare=False)
    _name_source_bus_prefix: str = field(default="", repr=False, compare=False)
    _name_source_bus_suffix: str = field(default="", repr=False, compare=False)
    _name_source_is_bus: bool = field(default=False, repr=False, compare=False)
    _bus_signal_width: int = field(default=0, repr=False, compare=False)
    _bus_signal_offset: int = field(default=0, repr=False, compare=False)
    _source_connection_link_id: str = field(default="", repr=False, compare=False)
    _contains_bus: bool = field(default=False, repr=False, compare=False)
    _port_bus_member_names: tuple[str, ...] = field(
        default=(), repr=False, compare=False
    )
    _scalar_merge_name_sources: tuple[tuple[str, str], ...] = field(
        default=(), repr=False, compare=False
    )
    _signal_harness_name_candidates: tuple[_SignalHarnessNameCandidate, ...] = field(
        default=(), repr=False, compare=False
    )
    _managed_item_count: int = field(default=0, repr=False, compare=False)
    _managed_removed_item_count: int = field(default=0, repr=False, compare=False)
    _managed_first_item_owner: str = field(default="", repr=False, compare=False)
    _managed_first_item_location: tuple[int, int] | None = field(
        default=None, repr=False, compare=False
    )
    _single_pin_retention_only: bool = field(default=False, repr=False, compare=False)
    source_pages: list[NetlistSourcePage] = field(default_factory=list)

    @property
    def designators(self) -> list[str]:
        """
        List of unique component designators on this net.
        """
        return list(set(t.designator for t in self.terminals))

    @property
    def pin_count(self) -> int:
        """
        Number of terminals on this net.
        """
        return len(self.terminals)


@dataclass
class NetlistComponent:
    """
    A component in the netlist.

        The parameters dict contains ALL SchDoc component parameters,
        populated during netlist generation. This allows derived operations
        (BOM, signal trace, etc.) to access component data without reaching
        back to the SchDoc.

        Component kind (Altium-specific values):
            0 = Standard
            1 = Mechanical
            2 = Graphical
            3 = Net Tie (BOM)
            4 = Net Tie (No BOM)
            5 = Standard (No BOM)
            6 = Jumper

        The exclude_from_bom flag is derived from component_kind:
        - Graphical (2), Net Tie No-BOM (4), Standard No-BOM (5) are excluded
    """

    designator: str
    value: str = ""
    footprint: str = ""
    library_ref: str = ""
    description: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    component_kind: int = 0  # Raw Altium ComponentKind value
    exclude_from_bom: bool = False  # BOM filtering flag derived from component_kind
    _source_component_uid: str = field(default="", repr=False)
    component_id: str = ""
    logical_designator: str | None = None
    physical_designator: str | None = None
    source_page: NetlistSourcePage | None = None

    @property
    def prefix(self) -> str:
        """
        Component prefix (e.g., "U" from "U1", "R" from "R10").
        """
        import re

        match = re.match(r"^([A-Za-z]+)", self.designator)
        return match.group(1) if match else ""


@dataclass
class PnpEntry:
    """
    Pick-and-place entry for a component.

        Contains position/rotation from PcbDoc merged with parameters
        from schematic. Used for manufacturing pick-and-place output. By
        default, ``center_x`` and ``center_y`` use the ``altium-pick-place``
        mode from ``AltiumDesign.to_pnp()``: center of the bounding box of
        component-owned pad anchor points, with component-origin fallback.

        Attributes:
            designator: Component designator (e.g., "R1", "U1_2")
            comment: Display value from schematic (e.g., "10k", "LM358")
            layer: PCB layer - "top" or "bottom" (normalized)
            footprint: PCB footprint name
            center_x: Selected PnP position X in requested units (mm or mils)
            center_y: Selected PnP position Y in requested units (mm or mils)
            rotation: Rotation in degrees (0-360)
            description: Component description
            parameters: Dict of component parameters (MPN, Manufacturer, etc.)
    """

    designator: str
    comment: str
    layer: str
    footprint: str
    center_x: float
    center_y: float
    rotation: float
    description: str = ""
    parameters: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        """
        Serialize the pick-and-place entry to a JSON-compatible dict.
        """
        return {
            "designator": self.designator,
            "comment": self.comment,
            "layer": self.layer,
            "footprint": self.footprint,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "rotation": self.rotation,
            "description": self.description,
            "parameters": dict(sorted(self.parameters.items())),
        }


@dataclass
class ComponentHierarchy:
    """
    Hierarchy context for a component.

        Tracks where the component lives in the design hierarchy,
        including multi-channel information.
    """

    base_designator: str  # Original designator before channel annotation (e.g., "R1")
    channel: str | None = (
        None  # Channel letter (e.g., "A", "B") or None if not multi-channel
    )
    channel_index: int | None = None  # Channel numeric index (1, 2, 3...) or None
    sheet: str = ""  # Source sheet filename

    def to_json(self) -> dict:
        """
        Serialize to JSON-compatible dict.
        """
        return {
            "base_designator": self.base_designator,
            "channel": self.channel,
            "channel_index": self.channel_index,
            "sheet": self.sheet,
        }

    @classmethod
    def from_json(cls, data: dict) -> "ComponentHierarchy":
        """
        Deserialize from JSON dict.
        """
        return cls(
            base_designator=data.get("base_designator", ""),
            channel=data.get("channel"),
            channel_index=data.get("channel_index"),
            sheet=data.get("sheet", ""),
        )


class ComponentType(Enum):
    """
    Component type classification.
    """

    PASSIVE_2PIN = "passive_2pin"  # R, C, L, D, LED
    IC = "ic"  # U - integrated circuits
    CONNECTOR = "connector"  # J, P - connectors
    TRANSISTOR = "transistor"  # Q - transistors, FETs
    TRANSFORMER = "transformer"  # T - transformers
    CRYSTAL = "crystal"  # Y - crystals, oscillators
    FUSE = "fuse"  # F - fuses
    SWITCH = "switch"  # S, SW - switches
    RELAY = "relay"  # K, RY - relays
    TEST_POINT = "test_point"  # TP - test points
    FIDUCIAL = "fiducial"  # FID - fiducials
    MOUNTING_HOLE = "mounting_hole"  # MH - mounting holes
    UNKNOWN = "unknown"


# Map component prefix to type
_PREFIX_TO_TYPE = {
    "R": ComponentType.PASSIVE_2PIN,
    "C": ComponentType.PASSIVE_2PIN,
    "L": ComponentType.PASSIVE_2PIN,
    "D": ComponentType.PASSIVE_2PIN,
    "LED": ComponentType.PASSIVE_2PIN,
    "U": ComponentType.IC,
    "IC": ComponentType.IC,
    "J": ComponentType.CONNECTOR,
    "P": ComponentType.CONNECTOR,
    "CON": ComponentType.CONNECTOR,
    "Q": ComponentType.TRANSISTOR,
    "T": ComponentType.TRANSFORMER,
    "TR": ComponentType.TRANSFORMER,
    "Y": ComponentType.CRYSTAL,
    "X": ComponentType.CRYSTAL,
    "F": ComponentType.FUSE,
    "S": ComponentType.SWITCH,
    "SW": ComponentType.SWITCH,
    "K": ComponentType.RELAY,
    "RY": ComponentType.RELAY,
    "TP": ComponentType.TEST_POINT,
    "FID": ComponentType.FIDUCIAL,
    "MH": ComponentType.MOUNTING_HOLE,
}


@dataclass
class ComponentClassification:
    """
    Component classification for analysis.

        Used for filtering, grouping, and analysis operations.
    """

    prefix: str  # Component prefix (e.g., "R", "U", "J")
    type: ComponentType = ComponentType.UNKNOWN
    pin_count: int = 0  # Number of pins

    def to_json(self) -> dict:
        """
        Serialize to JSON-compatible dict.
        """
        return {
            "prefix": self.prefix,
            "type": self.type.value,
            "pin_count": self.pin_count,
        }

    @classmethod
    def from_json(cls, data: dict) -> "ComponentClassification":
        """
        Deserialize from JSON dict.
        """
        type_str = data.get("type", "unknown")
        try:
            comp_type = ComponentType(type_str)
        except ValueError:
            comp_type = ComponentType.UNKNOWN
        return cls(
            prefix=data.get("prefix", ""),
            type=comp_type,
            pin_count=data.get("pin_count", 0),
        )

    @classmethod
    def from_component(
        cls, designator: str, pin_count: int
    ) -> "ComponentClassification":
        """
        Create classification from component data.
        """
        import re

        match = re.match(r"^([A-Za-z]+)", designator)
        prefix = match.group(1).upper() if match else ""

        comp_type = _PREFIX_TO_TYPE.get(prefix, ComponentType.UNKNOWN)

        return cls(
            prefix=prefix,
            type=comp_type,
            pin_count=pin_count,
        )


@dataclass
class Netlist:
    """
    Complete netlist with all nets and components.
    """

    nets: list[Net] = field(default_factory=list)
    components: list[NetlistComponent] = field(default_factory=list)
    schematic_hierarchy: dict = field(default_factory=dict)
    _net_lookup: dict[str, list[Net]] = field(default_factory=dict, repr=False)
    _uid_lookup: dict[str, Net] = field(default_factory=dict, repr=False)
    _component_lookup: dict[str, NetlistComponent] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        """
        Build lookup dictionaries after initialization.
        """
        self._rebuild_lookups()

    def _rebuild_lookups(self) -> None:
        """
        Rebuild lookup dictionaries.
        """
        self._net_lookup = defaultdict(list)
        for n in self.nets:
            self._net_lookup[n.name].append(n)
        self._uid_lookup = {n.uid: n for n in self.nets}
        components_by_designator: dict[str, list[NetlistComponent]] = defaultdict(list)
        for component in self.components:
            components_by_designator[component.designator].append(component)
        self._component_lookup = {
            designator: rows[0]
            for designator, rows in components_by_designator.items()
            if len(rows) == 1
        }

    def get_net(self, name: str) -> Net | None:
        """
        Get the first net with the given name.
        """
        nets = self._net_lookup.get(name, [])
        return nets[0] if nets else None

    def get_nets(self, name: str) -> list[Net]:
        """
        Get all nets with given name (handles duplicate-named nets).

                In flat mode, same-named net labels on different sheets can produce
                separate nets with the same name.
        """
        return list(self._net_lookup.get(name, []))

    def get_net_by_uid(self, uid: str) -> Net | None:
        """
        Get net by unique ID.
        """
        return self._uid_lookup.get(uid)

    def get_component(self, designator: str) -> NetlistComponent | None:
        """
        Get component by designator.
        """
        return self._component_lookup.get(designator)

    def get_nets_for_component(self, designator: str) -> list[Net]:
        """
        Get all nets connected to a component.
        """
        return [n for n in self.nets if designator in n.designators]

    def get_graphical_ids_for_net(self, net_name: str) -> list[str]:
        """
        Get all SVG element IDs for a net (for SVG highlighting).
        """
        net = self.get_net(net_name)
        if not net:
            return []
        return net.graphical.all_svg_ids()

    def to_json(self) -> dict[str, object]:
        """Serialize the canonical current netlist b0 mapping."""
        from .altium_netlist_contract import netlist_to_mapping

        return netlist_to_mapping(self)

    def to_json_text(self, *, limits: "SchematicContractLimits | None" = None) -> str:
        """Serialize canonical compact netlist b0 JSON text."""
        return self.to_json_bytes(limits=limits).decode("utf-8")

    def to_json_bytes(
        self, *, limits: "SchematicContractLimits | None" = None
    ) -> bytes:
        """Serialize canonical compact netlist b0 UTF-8 bytes."""
        from .altium_netlist_contract import netlist_to_bytes

        return netlist_to_bytes(self, limits=limits)
