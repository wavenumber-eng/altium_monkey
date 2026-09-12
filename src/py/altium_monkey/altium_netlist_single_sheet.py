"""
Single-sheet netlist compiler.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cmp_to_key
from typing import TYPE_CHECKING, Callable, Protocol, TypeAlias, cast

from ._compiler_source import _compiler_document_source
from .altium_netlist_options import NetlistOptions
from .altium_netlist_common import (
    PinGroup,
    _NetPinLike,
    _altium_net_total_sort_key,
    _emit_auto_named_nets,
    _emit_bridge_roots,
    _emit_named_roots,
    _emit_port_named_nets,
    _pin_electrical_to_pintype,
    _resolve_component_display_value,
)
from .altium_netlist_model import (
    GraphicalPinRef,
    Net,
    NetEndpoint,
    NetGraphical,
    Netlist,
    NetlistComponent,
    Terminal,
    UnionFind,
)
from .altium_netlist_wire_connectivity import (
    ALTIUM_COORD_SCALE,
    AnalyserNetItemKind,
    RootPoint,
    WireGeometryIndex,
    altium_internal_tolerance_for_display_unit,
    analyser_net_item_kind,
    build_wire_graph as _build_wire_graph,
    group_pins_by_network as _group_pins_by_network,
    precise_points_connected,
    _WireLike,
    connect_endpoint_t_junctions,
    _power_bus_net_label_text,
)
from .altium_compiled_design_support import (
    _compiler_source_rows,
    _compiler_hidden_net_name,
    _compiler_sheet_symbol_sources,
    _harness_connector_master_entry_point,
    _compiler_component_sources,
    _compiler_source_pins,
)
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key, dotnet_utf16_units
from .altium_managed_alpha_numeric import managed_alpha_numeric_compare
from .altium_sch_record_helpers import (
    _basic_entry_distance_to_rounded_native_units,
    _coord_scalar_to_native_parts,
    _coord_scalar_to_rounded_native_units,
    _coord_scalar_with_basic_entry_distance_to_native_parts,
    _coord_scalar_with_basic_entry_distance_to_rounded_native_units,
    _effective_basic_entry_distance_frac1,
)
from .altium_sch_display_mode import (
    pin_belongs_to_component_view,
    pin_is_managed_part_member,
    pin_is_runtime_hidden,
)

if TYPE_CHECKING:
    from .altium_schdoc import AltiumSchDoc
    from .altium_schdoc_info import (
        SchComponentInfo,
        SchCrossSheetConnectorInfo,
        SchNetLabelInfo,
        SchPinInfo,
        SchPortInfo,
        SchPowerPortInfo,
        SchSheetSymbolInfo,
    )

log = logging.getLogger(__name__)


PinGroupsByRoot: TypeAlias = dict[RootPoint, PinGroup]


@dataclass(frozen=True, slots=True)
class _NetNameCandidate:
    name: str
    priority: int
    kind: str


def _managed_name_priority(kind: str, options: NetlistOptions) -> int:
    power_mode = options.power_port_names_take_priority
    priority_by_kind = {
        "net_label": (10, 8)[power_mode],
        "power_port": (6, 10)[power_mode],
        "offsheet_connector": (8, 6)[power_mode],
        "port": (
            4
            if options.allow_ports_to_name_nets
            else 2
            if not options.allow_sheet_entries_to_name_nets
            else 0
        ),
        "sheet_entry": (
            2
            if options.allow_sheet_entries_to_name_nets
            and not options.allow_ports_to_name_nets
            else 0
        ),
        "pin": 2 if options.allow_ports_to_name_nets else 4,
        "hidden_pin": 2 if options.allow_ports_to_name_nets else 4,
    }
    return priority_by_kind.get(kind, priority_by_kind["pin"])


@dataclass(frozen=True, slots=True)
class _BusRange:
    prefix: str
    start: int
    end: int

    @property
    def width(self) -> int:
        return abs(self.end - self.start) + 1

    @property
    def offset(self) -> int:
        return min(self.start, self.end)

    def members(self) -> tuple[str, ...]:
        step = 1 if self.start <= self.end else -1
        return tuple(
            f"{self.prefix}{index}"
            for index in range(self.start, self.end + step, step)
        )

    def contains(self, name: str) -> bool:
        if not name.startswith(self.prefix):
            return False
        suffix = name[len(self.prefix) :]
        if not suffix.isascii() or not suffix.isdigit():
            return False
        value = int(suffix)
        return min(self.start, self.end) <= value <= max(self.start, self.end)


@dataclass(frozen=True, slots=True)
class _BusRangeItem:
    name: str
    kind: str
    points: tuple[RootPoint, ...]
    element_id: str
    object_id: str
    source_occurrence_id: str


def _bus_endpoint_already_present(
    endpoints: Sequence[NetEndpoint],
    item: _BusRangeItem,
    role: str,
    name: str,
    bus_signal_index: int | None,
) -> bool:
    return any(
        endpoint.role == role
        and endpoint.name == name
        and endpoint._source_occurrence_id == item.source_occurrence_id
        and endpoint._bus_signal_index == bus_signal_index
        for endpoint in endpoints
    )


def _unique_bus_endpoint_id(
    endpoints: Sequence[NetEndpoint],
    item: _BusRangeItem,
    role: str,
) -> str:
    emitted = {endpoint.endpoint_id for endpoint in endpoints}
    historical = f"{role}:{item.element_id or item.name}"
    if historical not in emitted:
        return historical
    occurrence = f"{role}:{item.source_occurrence_id or item.name}"
    if occurrence not in emitted:
        return occurrence
    duplicate_index = 1
    while f"{occurrence}:duplicate:{duplicate_index}" in emitted:
        duplicate_index += 1
    return f"{occurrence}:duplicate:{duplicate_index}"


def _parse_bus_range(name: str) -> _BusRange | None:
    open_index = name.find("[")
    if open_index < 0:
        return None
    close_index = name.find("]", open_index + 1)
    if close_index < 0:
        return None
    operands = name[open_index + 1 : close_index].split("..", 1)
    if len(operands) != 2:
        return None
    start = _parse_bus_bound(operands[0])
    end = _parse_bus_bound(operands[1])
    if start is None or end is None:
        return None
    return _BusRange(name[:open_index], start, end)


def _parse_bus_bound(value: str) -> int | None:
    value = value.strip()
    digits = value[1:] if value[:1] in {"+", "-"} else value
    if not digits or not digits.isascii() or not digits.isdigit():
        return None
    parsed = int(value)
    return parsed if 0 <= parsed <= 2_147_483_647 else None


def _managed_name_candidate_wins(candidate: str, current: str) -> bool:
    managed_order = managed_alpha_numeric_compare(candidate, current)
    if managed_order:
        return managed_order < 0
    return dotnet_utf16_units(candidate) > dotnet_utf16_units(current)


def _bus_context_candidate_wins(
    candidate_width: int,
    candidate_offset: int,
    current_width: int,
    current_offset: int,
) -> bool | None:
    if candidate_width != current_width:
        return candidate_width > current_width
    if candidate_offset != current_offset:
        return candidate_offset < current_offset
    return None


def _managed_pin_compare(left: object, right: object) -> int:
    component_order = managed_alpha_numeric_compare(
        str(getattr(left, "component_designator")),
        str(getattr(right, "component_designator")),
    )
    if component_order:
        return component_order
    pin_order = managed_alpha_numeric_compare(
        str(getattr(left, "designator")),
        str(getattr(right, "designator")),
    )
    if pin_order:
        return pin_order
    left_key = _managed_pin_adapter_tiebreak_key(left)
    right_key = _managed_pin_adapter_tiebreak_key(right)
    return (left_key > right_key) - (left_key < right_key)


def _managed_pin_adapter_tiebreak_key(
    pin: object,
) -> tuple[
    bool,
    int,
    int,
    int,
    int,
    int,
    tuple[int, ...],
    tuple[int, ...],
    int,
    int,
]:
    raw_pin = getattr(pin, "pin", pin)
    inferred_value = getattr(raw_pin, "is_inferred", False)
    inferred = bool(inferred_value() if callable(inferred_value) else inferred_value)
    component = getattr(pin, "component", None)
    component_record = getattr(component, "record", component)
    current_part_id = int(getattr(component_record, "current_part_id", None) or 1)
    location = getattr(raw_pin, "location", None)
    if location is not None and hasattr(location, "x"):
        x = int(location.x)
        x_frac = int(getattr(location, "x_frac", 0))
        y = int(location.y)
        y_frac = int(getattr(location, "y_frac", 0))
    else:
        x, y = getattr(pin, "location", (0, 0))
        x_frac = 0
        y_frac = 0
    return (
        inferred,
        current_part_id,
        int(x),
        x_frac,
        int(y),
        y_frac,
        dotnet_utf16_units(str(getattr(pin, "component_unique_id", "") or "")),
        dotnet_utf16_units(str(getattr(pin, "unique_id", "") or "")),
        int(getattr(component_record, "_record_index", 0) or 0),
        int(getattr(raw_pin, "_record_index", 0) or 0),
    )


class _CreateNetFn(Protocol):
    def __call__(
        self,
        name: str,
        pins: PinGroup,
        root: RootPoint,
        is_auto_named: bool = False,
    ) -> Net: ...


def _managed_local_net_compare(left: Net, right: Net) -> int:
    """Compare logical/physical local nets like managed ``NetAdapter.Compare``."""

    name_order = managed_alpha_numeric_compare(left.name, right.name)
    if name_order:
        return name_order
    pin_order = len(right.terminals) - len(left.terminals)
    if pin_order:
        return pin_order
    item_order = right._managed_item_count - left._managed_item_count
    if item_order:
        return item_order
    removed_order = right._managed_removed_item_count - left._managed_removed_item_count
    if removed_order:
        return removed_order
    owner_order = managed_alpha_numeric_compare(
        left._managed_first_item_owner,
        right._managed_first_item_owner,
    )
    if owner_order:
        return owner_order
    left_location = left._managed_first_item_location
    right_location = right._managed_first_item_location
    if left_location is None or right_location is None:
        return 0
    return (left_location > right_location) - (left_location < right_location)


def _sheet_entry_precise_connection_point(
    sheet_symbol: SchSheetSymbolInfo,
    entry: object,
) -> tuple[int, int, int, int]:
    """Return the entry location represented in SchDoc coordinate fields."""
    record = sheet_symbol.record
    sym_x_parts = _coord_scalar_to_native_parts(
        record.location.x,
        getattr(record.location, "x_frac", 0),
    )
    sym_y_parts = _coord_scalar_to_native_parts(
        record.location.y,
        getattr(record.location, "y_frac", 0),
    )
    right_x_parts = _coord_scalar_to_native_parts(
        record.location.x + record.x_size,
        getattr(record.location, "x_frac", 0) + getattr(record, "x_size_frac", 0),
    )
    bottom_y_parts = _coord_scalar_to_native_parts(
        record.location.y - record.y_size,
        getattr(record.location, "y_frac", 0) - getattr(record, "y_size_frac", 0),
    )
    distance_from_top = getattr(entry, "distance_from_top", None)
    if distance_from_top is None:
        distance = _basic_entry_distance_to_rounded_native_units(entry)
        entry_x_parts = _coord_scalar_to_native_parts(
            record.location.x + distance,
            getattr(record.location, "x_frac", 0),
        )
        entry_y_parts = _coord_scalar_to_native_parts(
            record.location.y - distance,
            getattr(record.location, "y_frac", 0),
        )
    else:
        distance_from_top_frac1 = _effective_basic_entry_distance_frac1(entry)
        entry_x_parts = _coord_scalar_with_basic_entry_distance_to_native_parts(
            record.location.x,
            getattr(record.location, "x_frac", 0),
            distance_from_top,
            distance_from_top_frac1,
            direction=1,
        )
        entry_y_parts = _coord_scalar_with_basic_entry_distance_to_native_parts(
            record.location.y,
            getattr(record.location, "y_frac", 0),
            distance_from_top,
            distance_from_top_frac1,
            direction=-1,
        )
    side = int(getattr(entry, "side", -1))
    if side == 0:
        return (
            sym_x_parts[0],
            entry_y_parts[0],
            sym_x_parts[1],
            entry_y_parts[1],
        )
    if side == 1:
        return (
            right_x_parts[0],
            entry_y_parts[0],
            right_x_parts[1],
            entry_y_parts[1],
        )
    if side == 2:
        return (
            entry_x_parts[0],
            sym_y_parts[0],
            entry_x_parts[1],
            sym_y_parts[1],
        )
    if side == 3:
        return (
            entry_x_parts[0],
            bottom_y_parts[0],
            entry_x_parts[1],
            bottom_y_parts[1],
        )
    raise ValueError(f"Unknown sheet entry side {side}")


def _unique_sheet_entry_element_id(
    base_element_id: str,
    emitted_element_ids: set[str],
) -> str:
    candidate = base_element_id
    duplicate_index = 0
    while candidate in emitted_element_ids:
        duplicate_index += 1
        candidate = f"{base_element_id}:duplicate:{duplicate_index}"
    emitted_element_ids.add(candidate)
    return candidate


class AltiumNetlistSingleSheetCompiler:
    """
    Independent single-sheet compiler.

        This mirrors the proven single-sheet behavior as the active netlist
        compiler for one-sheet designs.
    """

    def __init__(
        self,
        schdoc: AltiumSchDoc,
        tolerance: int = 0,
        strict: bool = True,
        options: NetlistOptions | None = None,
    ) -> None:
        """
        Initialize generator with a parsed SchDoc.

        Args:
            schdoc: Parsed AltiumSchDoc object
            tolerance: Optional connection tolerance in parsed coordinate units.
                The default is exact connectivity, matching Altium wire-list
                output for off-grid wire crossings without explicit junctions.
            strict: If True, normalize special chars to ASCII (default: True)
            options: Netlist generation options (default: free document defaults)
        """
        self.schdoc: AltiumSchDoc = _compiler_document_source(schdoc)
        source = self.schdoc
        self.strict = strict
        self.options = options or NetlistOptions()

        self.tolerance = tolerance
        display_unit = int(getattr(getattr(source, "sheet", None), "display_unit", 0))
        self.internal_tolerance = (
            tolerance * ALTIUM_COORD_SCALE
            if tolerance > 0
            else altium_internal_tolerance_for_display_unit(display_unit)
        )

        # Internal storage
        self._components: list[SchComponentInfo] = []
        self._pins: list[SchPinInfo] = []
        self._wires: list = []  # Wire objects
        self._junctions: list[RootPoint] = []
        self._net_labels: list[tuple[RootPoint, str, SchNetLabelInfo]] = []
        self._power_ports: list[
            tuple[RootPoint, str, SchPowerPortInfo | SchCrossSheetConnectorInfo]
        ] = []
        self._ports: list[tuple[RootPoint, str, SchPortInfo]] = []
        self._port_source_occurrence_ids: dict[int, str] = {}
        self._inferred_port_segments: list[tuple[RootPoint, RootPoint]] = []

        # Sheet entry tracking (for hierarchical connectivity)
        # Maps connection hotspot -> (entry_name, sheet_symbol_info)
        self._sheet_entries: list[
            tuple[RootPoint, str, SchSheetSymbolInfo, object]
        ] = []
        self._sheet_symbol_uids_by_info_id: dict[int, str] = {}
        self._sheet_entry_names_by_element_id: dict[str, str] = {}
        self._sheet_entry_object_ids_by_element_id: dict[str, str] = {}
        self._sheet_entry_connection_points_by_element_id: dict[
            str, tuple[int, int]
        ] = {}
        self._wire_sort_keys_by_element_id: dict[
            str, tuple[int, int, int, int, int, int, int, int, str]
        ] = {}

        # Order tracking
        self._net_label_names_ordered: list[str] = []
        self._power_port_names_ordered: list[str] = []
        self._port_names_ordered: list[str] = []

        # Spatial index for wire geometry (built in _build_wire_connectivity)
        self._geo_index: WireGeometryIndex | None = None
        self._net_item_roots: dict[RootPoint, tuple[RootPoint, bool]] = {}
        self._compiled_pin_roots: dict[
            tuple[str, str, str], tuple[SchPinInfo, ...]
        ] = {}
        self._nonwire_net_label_object_ids: set[int] = set()
        self._bus_range_items: list[_BusRangeItem] = []
        self._active_pin_designators_by_component: dict[str, set[str]] = defaultdict(
            set
        )
        self._masked_pin_designators_by_component: dict[str, set[str]] = defaultdict(
            set
        )
        self._compile_masked_components: dict[int, bool] = {}
        self._bridge_eligible_interface_ids: frozenset[str] | None = None
        self._component_contract_ids: dict[int, str] = {}

        # Compile mask bounds - components inside are excluded from netlist
        # Each bound is (min_x, min_y, max_x, max_y)
        self._compile_mask_bounds: list[tuple[int, int, int, int]] = []
        if hasattr(schdoc, "_collect_compile_mask_bounds"):
            self._compile_mask_bounds = schdoc._collect_compile_mask_bounds()
            if self._compile_mask_bounds:
                log.debug(f"Found {len(self._compile_mask_bounds)} compile masks")
        self._compile_mask_precise_bounds: list[tuple[int, int, int, int]] = []
        precise_mask_collector = getattr(
            schdoc,
            "_collect_compile_mask_precise_bounds",
            None,
        )
        if callable(precise_mask_collector):
            precise_bounds = cast(
                Iterable[tuple[int, int, int, int]],
                precise_mask_collector(),
            )
            self._compile_mask_precise_bounds = list(precise_bounds)

    def _is_inside_compile_mask(self, x: int, y: int) -> bool:
        """
        Check if a point is inside any compile mask.

                Compile masks exclude components from compilation/netlist generation.
                Components whose location falls within a compile mask's bounds are excluded.

                Args:
                    x: X coordinate in Altium internal units
                    y: Y coordinate in Altium internal units

                Returns:
                    True if point is inside any compile mask bounds
        """
        for min_x, min_y, max_x, max_y in self._compile_mask_bounds:
            if min_x < x < max_x and min_y < y < max_y:
                return True
        return False

    def _mask_contains_every_precise_point(
        self,
        points: Sequence[RootPoint],
    ) -> bool:
        if self._compile_mask_precise_bounds:
            return any(
                all(
                    min_x < point[0] * ALTIUM_COORD_SCALE + point[2] < max_x
                    and min_y < point[1] * ALTIUM_COORD_SCALE + point[3] < max_y
                    for point in points
                )
                for min_x, min_y, max_x, max_y in self._compile_mask_precise_bounds
            )
        return any(
            all(
                min_x < point[0] < max_x and min_y < point[1] < max_y
                for point in points
            )
            for min_x, min_y, max_x, max_y in self._compile_mask_bounds
        )

    @staticmethod
    def _record_location_point(value: object) -> RootPoint:
        record = getattr(value, "record", value)
        location = getattr(record, "location", None)
        if location is not None and hasattr(location, "x"):
            return (
                int(location.x),
                int(location.y),
                int(getattr(location, "x_frac", 0)),
                int(getattr(location, "y_frac", 0)),
            )
        whole = getattr(value, "location", (0, 0))
        return (int(whole[0]), int(whole[1]), 0, 0)

    def _component_is_inside_compile_mask(self, component: object) -> bool:
        points = [self._record_location_point(component)]
        for pin in getattr(component, "pins", ()):
            raw_pin = getattr(pin, "pin", pin)
            if (
                pin_belongs_to_component_view(raw_pin, component)
                and pin_is_managed_part_member(raw_pin)
                and not pin_is_runtime_hidden(raw_pin, component)
            ):
                precise_hotspot = getattr(pin, "_precise_connection_point", None)
                if precise_hotspot is None:
                    hot_spot = raw_pin.get_hot_spot()
                    precise_hotspot = (
                        hot_spot.x,
                        hot_spot.y,
                        hot_spot.x_frac,
                        hot_spot.y_frac,
                    )
                points.append(precise_hotspot)
        return self._mask_contains_every_precise_point(points)

    @staticmethod
    def _component_mask_identity(component: object) -> int:
        return id(getattr(component, "record", component))

    def _port_is_inside_compile_mask(
        self, connection_points: Sequence[RootPoint]
    ) -> bool:
        return self._mask_contains_every_precise_point(connection_points)

    def _precise_point_is_inside_compile_mask(self, point: RootPoint) -> bool:
        if not self._compile_mask_precise_bounds:
            return self._is_inside_compile_mask(point[0], point[1])
        point_x = point[0] * ALTIUM_COORD_SCALE + point[2]
        point_y = point[1] * ALTIUM_COORD_SCALE + point[3]
        return any(
            min_x < point_x < max_x and min_y < point_y < max_y
            for min_x, min_y, max_x, max_y in self._compile_mask_precise_bounds
        )

    def generate(self) -> Netlist:
        """
        Generate Netlist from the SchDoc.

        Returns:
            Netlist object with components and nets
        """
        log.debug("Starting netlist generation (single-sheet compiler)")

        # Step 1: Extract all elements using clean API
        self._extract_components()
        self._extract_pins()
        self._extract_wires()
        self._extract_junctions()
        self._extract_net_labels()
        self._extract_power_ports()
        self._extract_ports()
        self._extract_sheet_entries()
        self._separate_analyser_items()

        log.debug(
            f"Extracted: {len(self._components)} components, "
            f"{len(self._pins)} pins, {len(self._wires)} wires, "
            f"{len(self._junctions)} junctions, "
            f"{len(self._net_labels)} net labels, "
            f"{len(self._power_ports)} power ports, "
            f"{len(self._ports)} ports, "
            f"{len(self._sheet_entries)} sheet entries"
        )

        # Step 2: Build components list
        components = self._build_components()

        # Step 3: Build connectivity and nets
        nets = self._build_nets()

        # Step 4: Set source_sheets on all nets.
        sheet_name = self.schdoc.filepath.name if self.schdoc.filepath else ""
        for net in nets:
            net.source_sheets = [sheet_name] if sheet_name else []
            for endpoint in net.endpoints:
                if sheet_name and not endpoint.source_sheet:
                    endpoint.source_sheet = sheet_name

        log.debug(f"Generated {len(nets)} nets")

        return Netlist(nets=nets, components=components)

    # =========================================================================
    # Extraction Methods - Using Clean SchDoc API
    # =========================================================================

    def _extract_components(self) -> None:
        """
        Extract components using clean SchDoc API.

                Filters out components that should not appear in netlist:
                - GRAPHICAL: Visual-only components (logos, notes, etc.)
                - MECHANICAL: Non-electrical components (mounting holes, standoffs)
                - STANDARD_NO_BOM: Standard components excluded from BOM/netlist
                - NET_TIE_NO_BOM: Net ties excluded from netlist
                - Components inside compile masks (masked from compilation)
        """
        masked_count = 0
        for comp in _compiler_component_sources(self.schdoc):
            if comp.includes_in_netlist():
                # Check if component is inside a compile mask
                x, y = comp.location
                component_masked = self._component_is_inside_compile_mask(comp)
                self._compile_masked_components[self._component_mask_identity(comp)] = (
                    component_masked
                )
                if component_masked:
                    log.debug(
                        f"Component {comp.designator} excluded by compile mask at ({x}, {y})"
                    )
                    masked_count += 1
                    continue
                self._components.append(comp)
        if masked_count > 0:
            log.debug(f"Excluded {masked_count} components inside compile masks")

    def _extract_pins(self) -> None:
        """
        Extract pins using clean SchDoc API.

                Pins from components inside compile masks are excluded.
                Display-mode filtering is handled by the SchDoc pin API, which
                only yields pins for each component's active display mode.
        """
        masked_count = 0
        for pin in _compiler_source_pins(self.schdoc):
            # A component mask removes the whole part. Pin-level masks retain hidden
            # pins because the managed PartsProcessor admits them before masking.
            comp = pin.component
            if not pin_is_managed_part_member(pin):
                continue
            if comp:
                if not comp.includes_in_netlist():
                    continue
                component_key = self._component_mask_identity(comp)
                component_masked = self._compile_masked_components.get(component_key)
                if component_masked is None:
                    component_masked = self._component_is_inside_compile_mask(comp)
                    self._compile_masked_components[component_key] = component_masked
                if component_masked:
                    log.debug(
                        f"Pin {pin.designator}.{pin.name} excluded (parent inside compile mask)"
                    )
                    masked_count += 1
                    self._masked_pin_designators_by_component[
                        pin.component_designator.lower()
                    ].add(pin.designator)
                    continue

            pin_x, pin_y = pin.connection_point
            is_hidden = comp is not None and pin_is_runtime_hidden(pin, comp)
            precise_hotspot = getattr(
                pin,
                "_precise_connection_point",
                (pin_x, pin_y, 0, 0),
            )
            if not is_hidden and self._precise_point_is_inside_compile_mask(
                precise_hotspot
            ):
                log.debug(
                    "Pin %s.%s excluded by compile mask at (%s, %s)",
                    pin.designator,
                    pin.name,
                    pin_x,
                    pin_y,
                )
                masked_count += 1
                self._masked_pin_designators_by_component[
                    pin.component_designator.lower()
                ].add(pin.designator)
                continue

            self._pins.append(pin)
            self._active_pin_designators_by_component[
                pin.component_designator.lower()
            ].add(pin.designator)
        if masked_count > 0:
            log.debug(
                f"Excluded {masked_count} pins from components inside compile masks"
            )

    def _extract_wires(self) -> None:
        """
        Extract wires using clean SchDoc API.
        """
        self._wires = self.schdoc.get_wires()

    def _extract_junctions(self) -> None:
        """
        Extract junctions using clean SchDoc API.
        """
        for junc in self.schdoc.get_junctions():
            loc = getattr(junc, "location", None)
            if loc:
                self._junctions.append((loc.x, loc.y, loc.x_frac, loc.y_frac))

    def _extract_net_labels(self) -> None:
        """
        Extract net labels using clean SchDoc API.

                Net labels inside compile masks are excluded from netlist generation.
        """
        masked_count = 0
        for nl in self.schdoc.get_net_labels():
            # A NetLabel record owned by a component is symbol-body text, not a
            # placed connectivity object. It remains available to rendering.
            if getattr(nl.record, "parent", None) is not None:
                continue
            loc = nl.connection_point
            text = nl.text
            if text:
                # Check if net label is inside a compile mask
                location = nl.record.location
                precise_location = (
                    location.x,
                    location.y,
                    location.x_frac,
                    location.y_frac,
                )
                if self._precise_point_is_inside_compile_mask(precise_location):
                    log.debug(f"NetLabel '{text}' excluded by compile mask at {loc}")
                    masked_count += 1
                    continue
                self._net_labels.append(
                    (
                        (location.x, location.y, location.x_frac, location.y_frac),
                        text,
                        nl,
                    )
                )
                if text not in self._net_label_names_ordered:
                    self._net_label_names_ordered.append(text)
        if masked_count > 0:
            log.debug(f"Excluded {masked_count} net labels inside compile masks")

    def _extract_power_ports(self) -> None:
        """
        Extract power-like naming objects using clean SchDoc API.

                Power ports and cross-sheet connectors both persist through Altium's
                power-object family and participate in the same net naming and
                cross-sheet merge behavior in native netlist generation.
                Objects inside compile masks are excluded from netlist generation.
        """
        masked_count = 0
        gnd_bus_ordinal = 0
        vcc_bus_ordinal = 0
        power_like_objects = [
            *((power_port, True) for power_port in self.schdoc.get_power_ports()),
            *(
                (connector, False)
                for connector in self.schdoc.get_cross_sheet_connectors()
            ),
        ]
        for pp, is_power_object in power_like_objects:
            loc = pp.connection_point
            text = pp.text
            if text:
                # Check if the power-like naming object is inside a compile mask.
                if self._precise_point_is_inside_compile_mask(
                    pp._precise_connection_point
                ):
                    log.debug(
                        "Power-like naming object '%s' excluded by compile mask at %s",
                        text,
                        loc,
                    )
                    masked_count += 1
                    continue
                if is_power_object:
                    folded = text.casefold()
                    if "[" in text and "]" in text and "gndbus" in folded:
                        gnd_bus_ordinal += 1
                    elif "[" in text and "]" in text and "vccbus" in folded:
                        vcc_bus_ordinal += 1
                    text = (
                        _power_bus_net_label_text(
                            text,
                            gnd_ordinal=gnd_bus_ordinal,
                            vcc_ordinal=vcc_bus_ordinal,
                        )
                        or text
                    )
                self._power_ports.append((pp._precise_connection_point, text, pp))
                if text not in self._power_port_names_ordered:
                    self._power_port_names_ordered.append(text)
        if masked_count > 0:
            log.debug(
                "Excluded %s power-like naming objects inside compile masks",
                masked_count,
            )

    def _extract_ports(self) -> None:
        """
        Extract ports using clean SchDoc API.
        """
        masked_count = 0
        for port_index, port in _compiler_source_rows(self.schdoc.get_ports()):
            self._port_source_occurrence_ids[id(port)] = f"port:{port_index}"
            masked_count += self._extract_port(port)
        if masked_count > 0:
            log.debug("Excluded %s ports inside compile masks", masked_count)

    def _extract_port(self, port: SchPortInfo) -> bool:
        name = port.name
        if not name:
            return False
        connection_points = port._precise_connection_points
        if not connection_points:
            return False
        first_point = connection_points[0]
        if self._port_is_inside_compile_mask(connection_points):
            return True
        self._ports.append((first_point, name, port))
        harness_type = str(getattr(port.record, "harness_type", "") or "")
        if len(connection_points) > 1 and ".." not in name and not harness_type:
            self._inferred_port_segments.append((first_point, connection_points[1]))
        if name not in self._port_names_ordered:
            self._port_names_ordered.append(name)
        return False

    def _extract_sheet_entries(self) -> None:
        """
        Extract sheet entries from sheet symbols using clean SchDoc API.

                For each sheet symbol, computes entry connection hotspots based on
                symbol geometry (location, side, distance_from_top). These hotspots
                are registered so they can participate in union-find connectivity,
                enabling hierarchical net bridging in multi-sheet designs.

                Entry connection point formula:
                    Left side (0):  hotspot = (sym.location.x, sym.location.y - dist)
                    Right side (1): hotspot = (sym.location.x + sym.x_size, sym.location.y - dist)
                The symbol and DistanceFromTop whole/fraction fields are combined
                before the hotspot is reduced to the integer connectivity grid.
        """
        for symbol_index, sheet_sym_info in _compiler_source_rows(
            _compiler_sheet_symbol_sources(self.schdoc)
        ):
            raw_symbol_uid = str(getattr(sheet_sym_info.record, "unique_id", "") or "")
            self._sheet_symbol_uids_by_info_id[id(sheet_sym_info)] = (
                raw_symbol_uid or f"sheet_symbol:{symbol_index}"
            )
            ss = sheet_sym_info.record
            sym_x = _coord_scalar_to_rounded_native_units(
                ss.location.x,
                getattr(ss.location, "x_frac", 0),
            )
            sym_y = _coord_scalar_to_rounded_native_units(
                ss.location.y,
                getattr(ss.location, "y_frac", 0),
            )
            right_x = _coord_scalar_to_rounded_native_units(
                ss.location.x + ss.x_size,
                getattr(ss.location, "x_frac", 0) + getattr(ss, "x_size_frac", 0),
            )
            bottom_y = _coord_scalar_to_rounded_native_units(
                ss.location.y - ss.y_size,
                getattr(ss.location, "y_frac", 0) - getattr(ss, "y_size_frac", 0),
            )
            for entry in sheet_sym_info.entries:
                entry_name = entry.display_name or ""
                if not entry_name:
                    continue
                precise_hotspot = _sheet_entry_precise_connection_point(
                    sheet_sym_info,
                    entry,
                )
                if self._precise_point_is_inside_compile_mask(precise_hotspot):
                    continue

                distance_from_top = getattr(entry, "distance_from_top", None)
                distance_from_top_frac1 = _effective_basic_entry_distance_frac1(entry)
                side = entry.side

                if distance_from_top is None:
                    dist = entry._rounded_distance_from_top_native_units()
                    entry_x = _coord_scalar_to_rounded_native_units(
                        ss.location.x + dist,
                        getattr(ss.location, "x_frac", 0),
                    )
                    entry_y = _coord_scalar_to_rounded_native_units(
                        ss.location.y - dist,
                        getattr(ss.location, "y_frac", 0),
                    )
                else:
                    entry_x = (
                        _coord_scalar_with_basic_entry_distance_to_rounded_native_units(
                            ss.location.x,
                            getattr(ss.location, "x_frac", 0),
                            distance_from_top,
                            distance_from_top_frac1,
                            direction=1,
                        )
                    )
                    entry_y = (
                        _coord_scalar_with_basic_entry_distance_to_rounded_native_units(
                            ss.location.y,
                            getattr(ss.location, "y_frac", 0),
                            distance_from_top,
                            distance_from_top_frac1,
                            direction=-1,
                        )
                    )
                if side == 0:  # Left
                    hotspot = (sym_x, entry_y)
                elif side == 1:  # Right
                    hotspot = (right_x, entry_y)
                elif side == 2:  # Top
                    hotspot = (entry_x, sym_y)
                elif side == 3:  # Bottom
                    hotspot = (entry_x, bottom_y)
                else:
                    log.warning(f"Unknown sheet entry side {side} for '{entry_name}'")
                    continue

                self._sheet_entries.append(
                    (
                        precise_hotspot,
                        entry_name,
                        sheet_sym_info,
                        entry,
                    )
                )
                log.debug(
                    f"Sheet entry '{entry_name}' at hotspot {hotspot} "
                    f"(side={side}, dist={distance_from_top})"
                )

    def _separate_analyser_items(self) -> None:
        """Separate AD bus/harness items from scalar wire coordinates."""
        signal_harnesses = tuple(self.schdoc.get_signal_harnesses())
        connector_primary_points = tuple(
            _harness_connector_master_entry_point(connector.record)
            for connector in self.schdoc.get_harness_connectors()
        )

        def kind(
            identifier: str,
            locations: tuple[RootPoint, ...],
            harness_type: str = "",
        ) -> AnalyserNetItemKind:
            return analyser_net_item_kind(
                identifier,
                harness_type=harness_type,
                locations=locations,
                signal_harnesses=signal_harnesses,
                harness_connector_primary_points=connector_primary_points,
                internal_tolerance=self.internal_tolerance,
            )

        self._pins = [
            pin
            for pin in self._pins
            if kind(pin.designator or pin.name, (pin._precise_connection_point,))
            is AnalyserNetItemKind.WIRE
        ]
        virtual_index = 0

        def virtual_root() -> RootPoint:
            nonlocal virtual_index
            virtual_index += 1
            return (-2_000_000_000, virtual_index, 0, 0)

        separated_labels: list[tuple[RootPoint, str, SchNetLabelInfo]] = []
        for location, name, obj in self._net_labels:
            item_kind = kind(name, (location,))
            if item_kind is AnalyserNetItemKind.WIRE:
                separated_labels.append((location, name, obj))
                continue
            if item_kind is AnalyserNetItemKind.BUS:
                self._capture_bus_range_item(name, "net_label", (location,), obj)
            self._nonwire_net_label_object_ids.add(id(obj))
            separated_labels.append((virtual_root(), name, obj))
        self._net_labels = separated_labels

        separated_power_ports: list[
            tuple[RootPoint, str, SchPowerPortInfo | SchCrossSheetConnectorInfo]
        ] = []
        for location, name, obj in self._power_ports:
            item_kind = kind(
                name,
                (location,),
                str(getattr(obj.record, "harness_type", "") or ""),
            )
            if item_kind is AnalyserNetItemKind.WIRE:
                separated_power_ports.append((location, name, obj))
            elif item_kind is AnalyserNetItemKind.BUS:
                self._capture_bus_range_item(name, "power_port", (location,), obj)
        self._power_ports = separated_power_ports
        self._power_port_names_ordered = [
            name
            for name in self._power_port_names_ordered
            if any(
                candidate_name == name
                for _location, candidate_name, _obj in self._power_ports
            )
        ]

        separated_ports: list[tuple[RootPoint, str, SchPortInfo]] = []
        scalar_port_segments: list[tuple[RootPoint, RootPoint]] = []
        for _location, name, obj in self._ports:
            points = tuple(obj._precise_connection_points)
            item_kind = kind(
                name,
                points,
                str(getattr(obj.record, "harness_type", "") or ""),
            )
            if item_kind is AnalyserNetItemKind.BUS:
                self._capture_bus_range_item(
                    name,
                    "port",
                    points,
                    obj,
                    source_occurrence_id=self._port_source_occurrence_ids[id(obj)],
                )
            if item_kind is not AnalyserNetItemKind.WIRE:
                continue
            separated_ports.append((points[0], name, obj))
            if len(points) == 2:
                scalar_port_segments.append((points[0], points[1]))
        self._ports = separated_ports
        self._port_names_ordered = [
            name
            for name in self._port_names_ordered
            if any(
                candidate_name == name
                for _location, candidate_name, _obj in self._ports
            )
        ]

        separated_entries: list[tuple[RootPoint, str, SchSheetSymbolInfo, object]] = []
        emitted_bus_entry_ids: set[str] = set()
        for location, name, symbol, entry in self._sheet_entries:
            item_kind = kind(
                name,
                (location,),
                str(getattr(entry, "harness_type", "") or ""),
            )
            if item_kind is AnalyserNetItemKind.BUS:
                symbol_uid = self._sheet_symbol_uid(symbol)
                element_id = _unique_sheet_entry_element_id(
                    f"{symbol_uid}_{name}",
                    emitted_bus_entry_ids,
                )
                self._capture_bus_range_item(
                    name,
                    "sheet_entry",
                    (location,),
                    entry,
                    element_id=element_id,
                    source_occurrence_id=element_id,
                )
            separated_entries.append(
                (
                    location
                    if item_kind is AnalyserNetItemKind.WIRE
                    else virtual_root(),
                    name,
                    symbol,
                    entry,
                )
            )
        self._sheet_entries = separated_entries
        self._inferred_port_segments = [segment for segment in scalar_port_segments]

    def _capture_bus_range_item(
        self,
        name: str,
        kind: str,
        points: tuple[RootPoint, ...],
        source: object,
        *,
        element_id: str | None = None,
        source_occurrence_id: str | None = None,
    ) -> None:
        if _parse_bus_range(name) is None:
            return
        raw_object_id = str(getattr(source, "unique_id", "") or "")
        resolved_element_id = raw_object_id if element_id is None else element_id
        self._bus_range_items.append(
            _BusRangeItem(
                name=name,
                kind=kind,
                points=points,
                element_id=resolved_element_id,
                object_id=raw_object_id or resolved_element_id,
                source_occurrence_id=(
                    resolved_element_id
                    if source_occurrence_id is None
                    else source_occurrence_id
                ),
            )
        )

    def _expand_bus_ranges(
        self,
        nets: list[Net],
        wire_connectivity: UnionFind[RootPoint],
        wire_ids_by_root: dict[RootPoint, list[str]],
    ) -> None:
        bus_connectivity = self._build_bus_connectivity()
        if bus_connectivity is None:
            return
        bus_roots, bus_index, link_ids = bus_connectivity
        sources_by_root: dict[RootPoint, list[_BusRangeItem]] = defaultdict(list)
        for item in self._bus_range_items:
            root = self._connected_bus_root(item, bus_roots, bus_index)
            if root is not None:
                sources_by_root[root].append(item)
        for root, sources in sources_by_root.items():
            link_id = link_ids[root]
            for item in sources:
                self._expand_bus_source(nets, item, link_id)
        self._attach_bus_entry_evidence(
            nets,
            wire_connectivity,
            wire_ids_by_root,
            bus_roots,
            bus_index,
            sources_by_root,
        )

    def _build_bus_connectivity(
        self,
    ) -> (
        tuple[
            UnionFind[RootPoint],
            WireGeometryIndex,
            dict[RootPoint, str],
        ]
        | None
    ):
        get_buses = getattr(self.schdoc, "get_buses", None)
        buses = (
            list(cast(Iterable[_WireLike], get_buses())) if callable(get_buses) else []
        )
        if not buses:
            return None
        index = WireGeometryIndex(buses, tolerance=self.internal_tolerance)
        connectivity: UnionFind[RootPoint] = UnionFind()
        for bus in buses:
            points = index.get_points(bus)
            for left, right in zip(points, points[1:], strict=False):
                connectivity.union(left, right)
        self._connect_bus_endpoints(index, connectivity)
        return connectivity, index, self._bus_link_ids(buses, index, connectivity)

    def _connect_bus_endpoints(
        self,
        index: WireGeometryIndex,
        connectivity: UnionFind[RootPoint],
    ) -> None:
        search_tolerance = (
            self.internal_tolerance + ALTIUM_COORD_SCALE - 1
        ) // ALTIUM_COORD_SCALE
        for endpoint in index.get_all_endpoints():
            for other in index.find_nearby_endpoints(endpoint[:2], search_tolerance):
                if endpoint != other and precise_points_connected(
                    endpoint,
                    other,
                    self.internal_tolerance,
                ):
                    connectivity.union(endpoint, other)
            connect_endpoint_t_junctions(
                endpoint,
                index,
                connectivity,
                self.internal_tolerance,
            )

    def _bus_link_ids(
        self,
        buses: Iterable[object],
        index: WireGeometryIndex,
        connectivity: UnionFind[RootPoint],
    ) -> dict[RootPoint, str]:
        ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)
        for bus in buses:
            points = index.get_points(bus)
            if not points:
                continue
            root = connectivity.find(points[0])
            bus_id = str(getattr(bus, "unique_id", "") or "")
            if bus_id and bus_id not in ids_by_root[root]:
                ids_by_root[root].append(bus_id)
        link_ids = {
            root: ids[0] if ids else self._fallback_bus_link_id(root)
            for root, ids in ids_by_root.items()
        }
        for endpoint in index.get_all_endpoints():
            root = connectivity.find(endpoint)
            link_ids.setdefault(root, self._fallback_bus_link_id(root))
        return link_ids

    @staticmethod
    def _fallback_bus_link_id(root: RootPoint) -> str:
        return "bus-link:" + ":".join(str(value) for value in root)

    def _connected_bus_root(
        self,
        item: _BusRangeItem,
        connectivity: UnionFind[RootPoint],
        index: WireGeometryIndex,
    ) -> RootPoint | None:
        for point in item.points:
            matches = index.find_wire_connections_for_break_point(
                point,
                self.internal_tolerance,
            )
            if matches:
                return connectivity.find(matches[0])
        return None

    def _expand_bus_source(
        self,
        nets: list[Net],
        item: _BusRangeItem,
        link_id: str,
    ) -> None:
        bus_range = _parse_bus_range(item.name)
        if bus_range is None:
            return
        if item.kind != "port":
            carrier = self._ensure_bus_carrier(nets, item)
            carrier._contains_bus = True
            self._append_bus_source_graphical(carrier, item)
            self._append_bus_endpoint(carrier, item, item.kind, item.name)
            self._apply_bus_candidate(
                carrier,
                item,
                bus_range,
                item.name[len(bus_range.prefix) :],
                link_id,
                wide_or_bus_creation=True,
            )
        for bus_signal_index, member_name in enumerate(bus_range.members()):
            member = self._ensure_bus_net(nets, member_name)
            member._contains_bus = True
            if item.kind == "port" and all(
                dotnet_ordinal_ignore_case_key(existing)
                != dotnet_ordinal_ignore_case_key(member_name)
                for existing in member._port_bus_member_names
            ):
                member._port_bus_member_names += (member_name,)
            if item.name != member.name and item.name not in member.aliases:
                member.aliases.append(item.name)
            self._append_bus_endpoint(
                member,
                item,
                "bus_member",
                item.name,
                bus_signal_index=bus_signal_index,
            )
            self._apply_bus_candidate(
                member,
                item,
                bus_range,
                member_name[len(bus_range.prefix) :],
                link_id,
                wide_or_bus_creation=False,
            )

    @staticmethod
    def _ensure_bus_net(nets: list[Net], name: str) -> Net:
        for net in nets:
            if net.name == name:
                return net
        net = Net(name=name)
        nets.append(net)
        return net

    @classmethod
    def _ensure_bus_carrier(cls, nets: list[Net], item: _BusRangeItem) -> Net:
        selected: Net | None = None
        selected_rank = 0
        for net in nets:
            rank = cls._bus_carrier_match_rank(net, item)
            if rank == 3:
                return net
            if rank > selected_rank:
                selected = net
                selected_rank = rank
        if selected is not None:
            return selected
        return cls._ensure_bus_net(nets, item.name)

    @classmethod
    def _bus_carrier_match_rank(cls, net: Net, item: _BusRangeItem) -> int:
        same_name = net.name == item.name
        if net._contains_bus and same_name:
            return 3
        source_match = cls._net_has_bus_source(net, item)
        if net._contains_bus and source_match:
            return 3
        if source_match:
            return 2
        return int(same_name)

    @staticmethod
    def _net_has_bus_source(net: Net, item: _BusRangeItem) -> bool:
        return any(
            endpoint.name == item.name
            and (
                endpoint.role == item.kind
                or bool(item.element_id and endpoint.element_id == item.element_id)
            )
            for endpoint in net.endpoints
        )

    def _apply_bus_candidate(
        self,
        net: Net,
        item: _BusRangeItem,
        bus_range: _BusRange,
        bus_suffix: str,
        link_id: str,
        *,
        wide_or_bus_creation: bool,
    ) -> None:
        priority = self._bus_name_priority(item.name, item.kind)
        bus_width = bus_range.width if wide_or_bus_creation else 0
        bus_offset = bus_range.offset if wide_or_bus_creation else 0
        if not self._bus_candidate_wins(
            net,
            priority,
            bus_range.prefix,
            f"{bus_range.prefix}{bus_suffix}",
            bus_width,
            bus_offset,
            wide_or_bus_creation=wide_or_bus_creation,
        ):
            return
        net._name_source_kind = item.kind
        net._name_source_priority = priority
        net._name_source_raw_name = f"{bus_range.prefix}{bus_suffix}"
        net._name_source_bus_prefix = bus_range.prefix
        net._name_source_bus_suffix = bus_suffix
        net._name_source_is_bus = True
        if wide_or_bus_creation:
            net._bus_signal_width = bus_width
            net._bus_signal_offset = bus_offset
        net._source_connection_link_id = link_id
        net.auto_named = self._bus_source_is_autogenerated(item.kind)

    def _bus_name_priority(self, name: str, kind: str) -> int:
        if (
            kind == "port"
            and not self.options.allow_ports_to_name_nets
            and not self.options.allow_sheet_entries_to_name_nets
        ):
            return 3
        return self._name_candidate(name, kind).priority + 1

    def _bus_source_is_autogenerated(self, kind: str) -> bool:
        return (
            kind == "port"
            and not self.options.allow_ports_to_name_nets
            or (
                kind == "sheet_entry"
                and not self.options.allow_sheet_entries_to_name_nets
            )
        )

    @staticmethod
    def _bus_candidate_wins(
        net: Net,
        priority: int,
        bus_prefix: str,
        candidate_name: str,
        bus_width: int,
        bus_offset: int,
        *,
        wide_or_bus_creation: bool,
    ) -> bool:
        if wide_or_bus_creation and net._name_source_bus_suffix:
            if priority != net._name_source_priority:
                return priority > net._name_source_priority
            return _managed_name_candidate_wins(
                bus_prefix,
                net._name_source_bus_prefix,
            )
        if wide_or_bus_creation:
            context_result = _bus_context_candidate_wins(
                bus_width,
                bus_offset,
                net._bus_signal_width,
                net._bus_signal_offset,
            )
            if context_result is not None:
                return context_result
        if priority != net._name_source_priority:
            return priority > net._name_source_priority
        current_name = net._name_source_raw_name or net.name
        return _managed_name_candidate_wins(candidate_name, current_name)

    @staticmethod
    def _append_bus_source_graphical(net: Net, item: _BusRangeItem) -> None:
        if not item.element_id:
            return
        field_by_kind = {
            "net_label": net.graphical.labels,
            "power_port": net.graphical.power_ports,
            "port": net.graphical.ports,
            "sheet_entry": net.graphical.sheet_entries,
        }
        values = field_by_kind.get(item.kind)
        if values is not None and item.element_id not in values:
            values.append(item.element_id)

    def _append_bus_endpoint(
        self,
        net: Net,
        item: _BusRangeItem,
        role: str,
        name: str,
        *,
        bus_signal_index: int | None = None,
    ) -> None:
        if _bus_endpoint_already_present(
            net.endpoints,
            item,
            role,
            name,
            bus_signal_index,
        ):
            return
        endpoint_id = _unique_bus_endpoint_id(net.endpoints, item, role)
        source_sheet = self.schdoc.filepath.name if self.schdoc.filepath else ""
        point = item.points[0] if item.points else None
        net.endpoints.append(
            NetEndpoint(
                endpoint_id=endpoint_id,
                role=role,
                element_id=item.element_id,
                object_id=item.object_id,
                name=name,
                source_sheet=source_sheet,
                connection_point=point[:2] if point is not None else None,
                _source_occurrence_id=item.source_occurrence_id,
                _bus_signal_index=bus_signal_index,
            )
        )

    def _attach_bus_entry_evidence(
        self,
        nets: list[Net],
        wire_connectivity: UnionFind[RootPoint],
        wire_ids_by_root: dict[RootPoint, list[str]],
        bus_connectivity: UnionFind[RootPoint],
        bus_index: WireGeometryIndex,
        sources_by_root: dict[RootPoint, list[_BusRangeItem]],
    ) -> None:
        net_by_wire_root = self._net_by_wire_root(
            nets,
            wire_connectivity,
            wire_ids_by_root,
        )
        for entry in getattr(self.schdoc, "bus_entries", ()):
            self._attach_one_bus_entry(
                entry,
                net_by_wire_root,
                wire_connectivity,
                bus_connectivity,
                bus_index,
                sources_by_root,
            )

    @staticmethod
    def _net_by_wire_root(
        nets: list[Net],
        wire_connectivity: UnionFind[RootPoint],
        wire_ids_by_root: dict[RootPoint, list[str]],
    ) -> dict[RootPoint, Net]:
        net_by_wire_id = {
            wire_id: net for net in nets for wire_id in net.graphical.wires
        }
        normalized_wire_ids: dict[RootPoint, list[str]] = defaultdict(list)
        for root, wire_ids in wire_ids_by_root.items():
            normalized_wire_ids[wire_connectivity.find(root)].extend(wire_ids)
        return {
            root: net_by_wire_id[wire_id]
            for root, wire_ids in normalized_wire_ids.items()
            for wire_id in wire_ids
            if wire_id in net_by_wire_id
        }

    def _attach_one_bus_entry(
        self,
        entry: object,
        net_by_wire_root: dict[RootPoint, Net],
        wire_connectivity: UnionFind[RootPoint],
        bus_connectivity: UnionFind[RootPoint],
        bus_index: WireGeometryIndex,
        sources_by_root: dict[RootPoint, list[_BusRangeItem]],
    ) -> None:
        endpoints = (
            self._record_precise_point(getattr(entry, "location", None)),
            self._record_precise_point(getattr(entry, "corner", None)),
        )
        resolved = self._resolve_bus_entry(endpoints, bus_connectivity, bus_index)
        if resolved is None:
            return
        bus_root, wire_point = resolved
        wire_matches = self._require_geo_index().find_wire_endpoints_for_precise_point(
            wire_point,
            self.internal_tolerance,
        )
        if not wire_matches:
            return
        net = net_by_wire_root.get(wire_connectivity.find(wire_matches[0]))
        if net is None:
            return
        for item in sources_by_root.get(bus_root, ()):
            self._attach_bus_source_to_scalar_net(net, item)

    def _attach_bus_source_to_scalar_net(
        self,
        net: Net,
        item: _BusRangeItem,
    ) -> None:
        bus_range = _parse_bus_range(item.name)
        if bus_range is None or not bus_range.contains(net.name):
            return
        net._contains_bus = True
        if item.name not in net.aliases and item.name != net.name:
            net.aliases.append(item.name)
        self._append_bus_endpoint(net, item, "bus_entry_member", item.name)

    def _resolve_bus_entry(
        self,
        endpoints: tuple[RootPoint, RootPoint],
        connectivity: UnionFind[RootPoint],
        index: WireGeometryIndex,
    ) -> tuple[RootPoint, RootPoint] | None:
        matches = tuple(
            index.find_wire_connections_for_break_point(
                endpoint,
                self.internal_tolerance,
            )
            for endpoint in endpoints
        )
        if bool(matches[0]) == bool(matches[1]):
            return None
        bus_side = 0 if matches[0] else 1
        return connectivity.find(matches[bus_side][0]), endpoints[1 - bus_side]

    @staticmethod
    def _record_precise_point(point: object) -> RootPoint:
        return (
            int(getattr(point, "x", 0)),
            int(getattr(point, "y", 0)),
            int(getattr(point, "x_frac", 0)),
            int(getattr(point, "y_frac", 0)),
        )

    # =========================================================================
    # Building Methods
    # =========================================================================

    def _build_components(self) -> list[NetlistComponent]:
        """
        Build NetlistComponent list from extracted components.
        """
        result = []
        source_uid_counts = Counter(
            str(getattr(component, "unique_id", "") or "")
            for component in self._components
        )
        for index, comp in enumerate(self._components):
            # Wire List uses Comment field with parameter evaluation.
            # If expression resolves to empty, fall back to raw Comment text
            # (Altium shows "=Value" literally when the Value param is empty).
            # Resolve the display value the same way downstream views will.
            value = _resolve_component_display_value(
                comp,
                project_params=self.options.project_parameters,
                sheet_params=self.options.sheet_parameters,
                component_description=comp.description,
            )
            if not value and comp.comment:
                value = comp.comment

            # Capture all component parameters so downstream consumers do not
            # need to reach back into the source SchDoc.
            parameters = {}
            for param in comp.parameters:
                name = param.name or ""
                text = param.text or ""
                if name:
                    parameters[name] = text

            # Capture ComponentKind and derive the BOM exclusion flag once.
            from .altium_component_kind import component_kind_includes_in_bom

            kind_value = (
                comp.component_kind.value
                if hasattr(comp.component_kind, "value")
                else int(comp.component_kind)
            )
            exclude_from_bom = not component_kind_includes_in_bom(comp.component_kind)
            source_uid = str(comp.unique_id or "")
            component_id = (
                f"source-component:{source_uid}"
                if source_uid and source_uid_counts[source_uid] == 1
                else f"single-sheet-component:{index}"
            )
            self._component_contract_ids[id(comp)] = component_id
            self._component_contract_ids[id(comp.record)] = component_id

            result.append(
                NetlistComponent(
                    designator=comp.designator,
                    value=value,
                    footprint=comp.footprint,
                    library_ref=comp.library_ref,
                    description=comp.description,
                    parameters=parameters,
                    component_kind=kind_value,
                    exclude_from_bom=exclude_from_bom,
                    _source_component_uid=source_uid,
                    component_id=component_id,
                )
            )
        return result

    def _component_contract_id(self, component: object | None) -> str:
        """Return the b0 component identity for one extracted source object."""
        if component is None:
            return ""
        direct = self._component_contract_ids.get(id(component))
        if direct is not None:
            return direct
        return self._component_contract_ids.get(
            id(getattr(component, "record", component)), ""
        )

    def _build_nets(self) -> list[Net]:
        """
        Build nets using Union-Find connectivity algorithm.
        """
        # Build the wire connectivity graph.
        uf = UnionFind()
        wire_ids_by_root = self._build_wire_connectivity(uf)

        # Group pins by connected wire network.
        pin_groups, floating_pin_roots = self._group_pins_by_network(uf)

        # Assign scalar names first, then project connected bus ranges over them.
        nets = self._assign_names_and_build_nets(
            uf, wire_ids_by_root, pin_groups, floating_pin_roots
        )
        self._expand_bus_ranges(nets, uf, wire_ids_by_root)
        return nets

    def _compiled_pin_roots_by_component(
        self,
    ) -> dict[tuple[str, str, str], tuple[SchPinInfo, ...]]:
        """Return one active pin representative for each compiled signal root."""
        return self._compiled_pin_roots

    def _compiled_masked_pin_counts_by_component(self) -> dict[str, int]:
        """Return masked-only logical pin counts keyed by component designator."""
        return {
            component: len(
                masked - self._active_pin_designators_by_component[component]
            )
            for component, masked in self._masked_pin_designators_by_component.items()
        }

    def _capture_compiled_pin_roots(
        self,
        pin_groups: dict[RootPoint, list[SchPinInfo]],
    ) -> None:
        representatives: dict[tuple[str, str, str], list[SchPinInfo]] = defaultdict(
            list
        )
        for pins in pin_groups.values():
            root_representatives: dict[tuple[int, str], SchPinInfo] = {}
            for pin in pins:
                source_key = (
                    self._component_mask_identity(pin.component)
                    if pin.component is not None
                    else id(getattr(pin, "pin", pin)),
                    pin.designator,
                )
                current = root_representatives.get(source_key)
                if current is None or self._ad_common_pin_sort_key(
                    pin
                ) < self._ad_common_pin_sort_key(current):
                    root_representatives[source_key] = pin
            for pin in root_representatives.values():
                component_key = pin.component_unique_id or pin.unique_id
                key = (
                    component_key or pin.component_designator.lower(),
                    pin.component_designator.lower(),
                    pin.designator,
                )
                representatives[key].append(pin)
        self._compiled_pin_roots = {
            key: tuple(pins) for key, pins in representatives.items()
        }

    @staticmethod
    def _ad_common_pin_sort_key(pin: _NetPinLike) -> tuple[int, int, int]:
        """Order common-pin representatives like AD's pin-adapter merge."""
        owner_part_id = int(
            getattr(getattr(pin, "pin", None), "owner_part_id", None) or 1
        )
        location_x, location_y = getattr(pin, "location", (0, 0))
        return (owner_part_id, location_x, location_y)

    def _get_wire_points(self, wire: object) -> list[RootPoint]:
        """
        Extract points from wire object (cached via spatial index).
        """
        if self._geo_index is not None:
            return self._geo_index.get_points(wire)
        points = getattr(wire, "points", [])
        return [(p.x, p.y, p.x_frac, p.y_frac) for p in points]

    def _require_geo_index(self) -> WireGeometryIndex:
        """
        Return the built wire geometry index or raise if connectivity is not ready.
        """
        if self._geo_index is None:
            raise RuntimeError("Wire geometry index has not been built yet")
        return self._geo_index

    def _build_wire_connectivity(self, uf: UnionFind) -> dict[RootPoint, list[str]]:
        """
        Build wire connectivity graph using Union-Find.

                Uses WireGeometryIndex for O(E*K) spatial lookups instead of O(W^2*P).

                Args:
                    uf: Union-Find structure to populate

                Returns:
                    Mapping of root -> list of wire graphical IDs
        """
        shared_uf, wire_ids_by_root, wire_index = _build_wire_graph(
            self.schdoc,
            tolerance=self.internal_tolerance,
            cell_size=100,
            inferred_segments=self._inferred_port_segments,
        )
        self._geo_index = wire_index
        self._wire_sort_keys_by_element_id.clear()
        for wire in self._wires:
            element_id = str(getattr(wire, "unique_id", "") or "")
            if not element_id or element_id in self._wire_sort_keys_by_element_id:
                continue
            key = self._source_wire_sort_key(wire, element_id)
            if key is not None:
                self._wire_sort_keys_by_element_id[element_id] = key
        uf._parent = dict(shared_uf._parent)
        return wire_ids_by_root

    def _group_pins_by_network(
        self, uf: UnionFind
    ) -> tuple[dict[RootPoint, list], set[RootPoint]]:
        """
        Group pins by which wire network they connect to.

                Uses spatial index for O(P*K) pin-to-wire lookups.

                Args:
                    uf: Union-Find structure with wire connectivity

                Returns:
                    Tuple of (pin_groups, floating_pin_roots):
                    - pin_groups: root -> list of SchPinInfo
                    - floating_pin_roots: set of roots for truly floating pins
        """
        return _group_pins_by_network(
            self.schdoc,
            uf,
            self._require_geo_index(),
            internal_tolerance=self.internal_tolerance,
            pins=self._pins,
        )

    def _find_wire_point_for_location(
        self,
        loc: tuple[int, int],
        *,
        tolerance: int | None = None,
    ) -> RootPoint | None:
        """
        Find wire point that the location connects to.
        """
        return self._require_geo_index().find_wire_connection(
            loc,
            self.tolerance if tolerance is None else tolerance,
        )

    def _find_wire_point_for_netlabel(
        self,
        nl_obj: SchNetLabelInfo,
    ) -> RootPoint | None:
        """
        Find wire point for a net label, using strict fractional coordinate matching.
        """
        return self._require_geo_index().find_wire_connection_for_netlabel(
            nl_obj, self.internal_tolerance
        )

    def _find_ordinary_item_root(
        self,
        precise_location: tuple[int, int, int, int],
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
    ) -> tuple[RootPoint | None, bool]:
        """Find an ordinary net item's endpoint-only wire or pin connection."""
        wire_point = self._require_geo_index().find_wire_endpoint_for_precise_point(
            precise_location,
            self.internal_tolerance,
        )
        if wire_point is not None:
            return uf.find(wire_point), False
        pin_root = self._find_pin_root_for_precise_location(
            precise_location,
            pin_groups,
        )
        return (pin_root, True) if pin_root is not None else (None, False)

    def _find_pin_root_for_precise_location(
        self,
        precise_location: tuple[int, int, int, int],
        pin_groups: dict[RootPoint, list],
    ) -> RootPoint | None:
        """Return the already-grouped pin root at an AD full coordinate."""
        for pin in self._pins:
            pin_location = getattr(
                pin,
                "_precise_connection_point",
                (*pin.connection_point, 0, 0),
            )
            if precise_points_connected(
                precise_location,
                pin_location,
                self.internal_tolerance,
            ):
                for root, pins in pin_groups.items():
                    if pin in pins:
                        return root
        return None

    def _find_wire_point_for_sheet_entry(
        self,
        precise_point: RootPoint,
    ) -> RootPoint | None:
        return self._require_geo_index().find_wire_endpoint_for_precise_point(
            precise_point,
            self.internal_tolerance,
        )

    def _prepare_net_item_roots(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
    ) -> None:
        """Apply AD's full-coordinate NameTouchName relation before naming."""
        items = self._collect_net_items()
        connected = [
            self._anchor_net_item(location, association, obj, uf, pin_groups)
            for location, association, obj in items
        ]
        self._union_coincident_net_items(items, connected, uf)
        self._index_net_item_roots(items, connected, uf)

    def _collect_net_items(self) -> list[tuple[RootPoint, str, object]]:
        """Collect ordinary and segment-capable net items in source order."""
        items: list[tuple[RootPoint, str, object]] = []
        items.extend(
            (location, "label", obj)
            for location, _name, obj in self._net_labels
            if id(obj) not in self._nonwire_net_label_object_ids
        )
        items.extend(
            (location, "break", obj) for location, _name, obj in self._power_ports
        )
        items.extend(
            (location, "endpoint", obj) for location, _name, obj in self._ports
        )
        items.extend(
            (location, "endpoint", entry)
            for location, _name, _symbol, entry in self._sheet_entries
        )
        return items

    def _anchor_net_item(
        self,
        location: RootPoint,
        association: str,
        obj: object,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
    ) -> bool:
        """Attach one net item using AD's object-specific wire/pin rule."""
        uf.add_root(location)
        root: RootPoint | None = None
        if association == "label":
            wire_points = self._require_geo_index().find_wire_connections_for_netlabel(
                cast("SchNetLabelInfo", obj),
                self.internal_tolerance,
            )
            if not wire_points:
                root = self._find_pin_root_for_precise_location(
                    location,
                    pin_groups,
                )
        elif association == "break":
            wire_points = (
                self._require_geo_index().find_wire_connections_for_break_point(
                    location,
                    self.internal_tolerance,
                )
            )
            root = (
                None
                if wire_points
                else self._find_pin_root_for_precise_location(location, pin_groups)
            )
        else:
            wire_points = (
                self._require_geo_index().find_wire_endpoints_for_precise_point(
                    location,
                    self.internal_tolerance,
                )
            )
            root = (
                None
                if wire_points
                else self._find_pin_root_for_precise_location(location, pin_groups)
            )
        if wire_points:
            for wire_point in wire_points:
                uf.union(location, wire_point)
            return True
        if root is None:
            return False
        uf.union(location, root)
        return True

    def _union_coincident_net_items(
        self,
        items: list[tuple[RootPoint, str, object]],
        connected: list[bool],
        uf: UnionFind,
    ) -> None:
        """Apply AD NameTouchName equality to each distinct item pair."""
        for left in range(len(items)):
            for right in range(left + 1, len(items)):
                if precise_points_connected(
                    items[left][0],
                    items[right][0],
                    self.internal_tolerance,
                ):
                    uf.union(items[left][0], items[right][0])
                    connected[left] = True
                    connected[right] = True

    def _index_net_item_roots(
        self,
        items: list[tuple[RootPoint, str, object]],
        connected: list[bool],
        uf: UnionFind,
    ) -> None:
        """Index final roots while preserving collocated item connectivity."""
        self._net_item_roots = {}
        for index, (location, _association, _obj) in enumerate(items):
            root, was_connected = self._net_item_roots.get(
                location,
                (uf.find(location), False),
            )
            self._net_item_roots[location] = (
                uf.find(root),
                was_connected or connected[index],
            )

    def _prepared_net_item_root(
        self,
        location: RootPoint,
        uf: UnionFind,
        *,
        require_connection: bool,
    ) -> RootPoint | None:
        prepared = self._net_item_roots.get(location)
        if prepared is None or (require_connection and not prepared[1]):
            return None
        return uf.find(prepared[0])

    @staticmethod
    def _remap_list_maps(
        uf: UnionFind, **maps: dict[RootPoint, list]
    ) -> dict[str, dict[RootPoint, list]]:
        """
        Remap root-keyed list maps to final union-find roots.

                Each input map has root-to-list values. Returns a dict of remapped maps
                where all roots are resolved to their final UF representatives.
        """
        result = {}
        for name, old_map in maps.items():
            new_map: dict[RootPoint, list] = defaultdict(list)
            for old_root, items in old_map.items():
                new_map[uf.find(old_root)].extend(items)
            result[name] = new_map
        return result

    def _assign_names_and_build_nets(
        self,
        uf: UnionFind,
        wire_ids_by_root: dict[RootPoint, list[str]],
        pin_groups: dict[RootPoint, list],
        floating_pin_roots: set[RootPoint],
    ) -> list[Net]:
        """
        Assign net names and build final Net objects.

                Orchestrator that delegates to focused sub-methods for each naming
                priority level, then remaps roots and builds output nets.

                Args:
                    uf: Union-Find structure with connectivity
                    wire_ids_by_root: Wire graphical IDs by root
                    pin_groups: Pins grouped by root
                    floating_pin_roots: Roots for floating pins

                Returns:
                    List of Net objects
        """
        net_names: dict[RootPoint, _NetNameCandidate] = {}
        name_to_root: dict[str, RootPoint] = {}
        scalar_merge_name_sources: dict[RootPoint, list[tuple[str, str]]] = defaultdict(
            list
        )
        self._prepare_net_item_roots(uf, pin_groups)

        # Priority 1: NetLabels
        nl_roots, label_names_by_root, floating_labels, floating_label_roots, nl_ids = (
            self._process_net_labels(
                uf,
                pin_groups,
                net_names,
                name_to_root,
                scalar_merge_name_sources,
            )
        )

        # Priority 2: PowerPorts
        power_roots, offsheet_roots, pp_ids = self._process_power_ports(
            uf,
            pin_groups,
            net_names,
            name_to_root,
            scalar_merge_name_sources,
        )

        self._process_visible_pin_names(pin_groups, net_names, name_to_root)

        # Priority 2.5: Sheet entries
        se_roots, se_ids = self._process_sheet_entries(
            uf,
            pin_groups,
            net_names,
            name_to_root,
        )

        # Priority 3: Ports
        port_roots, port_ids = self._process_ports(
            uf,
            pin_groups,
            net_names,
            name_to_root,
            scalar_merge_name_sources,
        )

        # Step 6.5: Hidden pins
        hidden_pin_roots = self._process_hidden_pins(
            uf,
            pin_groups,
            floating_pin_roots,
            net_names,
            name_to_root,
            scalar_merge_name_sources,
        )
        self._attach_floating_labels_to_power_names(
            uf,
            floating_labels,
            floating_label_roots,
            nl_roots,
            nl_ids,
            power_roots,
            hidden_pin_roots,
            net_names,
            name_to_root,
        )
        self._merge_cross_category_authored_names(
            uf,
            nl_roots,
            power_roots,
            hidden_pin_roots,
        )
        self._merge_cross_category_authored_names(uf, offsheet_roots, port_roots)

        # Remap all list-valued maps to final UF roots
        remapped = self._remap_list_maps(
            uf,
            pin_groups=pin_groups,
            wire_ids=wire_ids_by_root,
            nl_ids=nl_ids,
            pp_ids=pp_ids,
            port_ids=port_ids,
            se_ids=se_ids,
            label_names=label_names_by_root,
            scalar_merge_name_sources=scalar_merge_name_sources,
        )

        # Remap net_names (first-wins logic, not list-extend)
        final_candidates: dict[RootPoint, _NetNameCandidate] = {}
        for old_root, candidate in net_names.items():
            current_root = uf.find(old_root)
            existing = final_candidates.get(current_root)
            if existing is None or self._candidate_is_better(candidate, existing):
                final_candidates[current_root] = candidate

        final_net_names = {
            root: candidate.name for root, candidate in final_candidates.items()
        }
        final_name_source_kinds = {
            root: candidate.kind for root, candidate in final_candidates.items()
        }
        final_name_source_priorities = {
            root: candidate.priority for root, candidate in final_candidates.items()
        }
        explicit_named_net_names = {
            root: candidate.name
            for root, candidate in final_candidates.items()
            if candidate.kind
            in {"net_label", "power_port", "offsheet_connector", "hidden_pin"}
        }
        sheet_entry_net_names = {
            root: candidate.name
            for root, candidate in final_candidates.items()
            if candidate.kind == "sheet_entry"
        }
        port_net_names = {
            root: candidate.name
            for root, candidate in final_candidates.items()
            if candidate.kind == "port"
        }

        self._capture_compiled_pin_roots(remapped["pin_groups"])

        # Build and order final nets
        return self._order_and_output_nets(
            uf=uf,
            final_pin_groups=remapped["pin_groups"],
            final_net_names=final_net_names,
            final_name_source_kinds=final_name_source_kinds,
            final_name_source_priorities=final_name_source_priorities,
            explicit_named_net_names=explicit_named_net_names,
            sheet_entry_net_names=sheet_entry_net_names,
            port_net_names=port_net_names,
            final_wire_ids=remapped["wire_ids"],
            final_nl_ids=remapped["nl_ids"],
            final_pp_ids=remapped["pp_ids"],
            final_port_ids=remapped["port_ids"],
            final_se_ids=remapped["se_ids"],
            final_label_names=remapped["label_names"],
            final_scalar_merge_name_sources=remapped["scalar_merge_name_sources"],
            floating_net_labels=floating_labels,
            floating_pin_roots=floating_pin_roots,
            port_roots=port_roots,
            se_roots=se_roots,
        )

    # -----------------------------------------------------------------
    # Net naming sub-methods (called by _assign_names_and_build_nets)
    # -----------------------------------------------------------------

    def _process_net_labels(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
        scalar_merge_name_sources: dict[RootPoint, list[tuple[str, str]]],
    ) -> tuple[
        dict[str, list[RootPoint]],
        dict[RootPoint, list[str]],
        dict[str, list[str]],
        dict[str, list[RootPoint]],
        dict[RootPoint, list[str]],
    ]:
        """
        Priority 1: Process net labels - find roots, merge same-named, assign names.

                Returns:
                    (net_label_roots, root_to_label_names, floating_net_labels, net_label_ids_by_root)
        """
        net_label_roots: dict[str, list[RootPoint]] = defaultdict(list)
        root_to_label_names: dict[RootPoint, list[str]] = defaultdict(list)
        floating_net_labels: dict[str, list[str]] = defaultdict(list)
        floating_label_roots: dict[str, list[RootPoint]] = defaultdict(list)
        net_label_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)

        for precise_location, name, nl_obj in self._net_labels:
            if not name or name.isspace():
                continue
            if id(nl_obj) in self._nonwire_net_label_object_ids:
                scalar_merge_name_sources[precise_location].append(("net_label", name))
                if nl_obj.unique_id:
                    floating_net_labels[name].append(nl_obj.unique_id)
                else:
                    floating_net_labels.setdefault(name, [])
                floating_label_roots[name].append(precise_location)
                continue
            wp = self._find_wire_point_for_netlabel(nl_obj)
            if wp is not None:
                root = uf.find(wp)
                scalar_merge_name_sources[root].append(("net_label", name))
                net_label_roots[dotnet_ordinal_ignore_case_key(name)].append(root)
                root_to_label_names[root].append(name)
                if nl_obj.unique_id:
                    net_label_ids_by_root[root].append(nl_obj.unique_id)
            else:
                # No wire connection - check direct pin connection
                pin_root = self._find_pin_root_for_precise_location(
                    precise_location,
                    pin_groups,
                )
                if pin_root is None:
                    pin_root = self._prepared_net_item_root(
                        precise_location,
                        uf,
                        require_connection=True,
                    )
                if pin_root is None:
                    scalar_merge_name_sources[precise_location].append(
                        ("net_label", name)
                    )
                    if nl_obj.unique_id:
                        floating_net_labels[name].append(nl_obj.unique_id)
                    else:
                        floating_net_labels.setdefault(name, [])
                    floating_label_roots[name].append(precise_location)
                    continue
                scalar_merge_name_sources[pin_root].append(("net_label", name))
                net_label_roots[dotnet_ordinal_ignore_case_key(name)].append(pin_root)
                root_to_label_names[pin_root].append(name)
                if nl_obj.unique_id:
                    net_label_ids_by_root[pin_root].append(nl_obj.unique_id)

        floating_net_labels, floating_label_roots = self._coalesce_floating_labels(
            floating_net_labels,
            floating_label_roots,
            net_label_roots,
            root_to_label_names,
            net_label_ids_by_root,
        )

        # Merge same-named net labels
        for _name, roots in net_label_roots.items():
            if len(roots) > 1:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])

        # Resolve equal-priority labels with AD's managed comparator.
        for orig_root, names in root_to_label_names.items():
            current_root = uf.find(orig_root)
            for name in names:
                self._assign_name_candidate(
                    net_names,
                    current_root,
                    self._name_candidate(name, "net_label"),
                )
                name_to_root[name] = current_root

        return (
            net_label_roots,
            root_to_label_names,
            floating_net_labels,
            floating_label_roots,
            net_label_ids_by_root,
        )

    def _coalesce_floating_labels(
        self,
        floating_labels: dict[str, list[str]],
        floating_roots: dict[str, list[RootPoint]],
        connected_roots: dict[str, list[RootPoint]],
        names_by_root: dict[RootPoint, list[str]],
        ids_by_root: dict[RootPoint, list[str]],
    ) -> tuple[dict[str, list[str]], dict[str, list[RootPoint]]]:
        names_by_key: dict[str, list[str]] = defaultdict(list)
        for name in floating_labels:
            names_by_key[dotnet_ordinal_ignore_case_key(name)].append(name)

        retained_ids: dict[str, list[str]] = defaultdict(list)
        retained_roots: dict[str, list[RootPoint]] = defaultdict(list)
        for name_key, names in names_by_key.items():
            if roots := connected_roots.get(name_key):
                for name in names:
                    names_by_root[roots[0]].append(name)
                    ids_by_root[roots[0]].extend(floating_labels[name])
                continue
            selected = self._best_label_name(names)
            for name in names:
                retained_ids[selected].extend(floating_labels[name])
                retained_roots[selected].extend(floating_roots[name])
        return retained_ids, retained_roots

    def _best_label_name(self, names: list[str]) -> str:
        selected = names[0]
        for name in names[1:]:
            if self._candidate_is_better(
                self._name_candidate(name, "net_label"),
                self._name_candidate(selected, "net_label"),
            ):
                selected = name
        return selected

    def _process_power_ports(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
        scalar_merge_name_sources: dict[RootPoint, list[tuple[str, str]]],
    ) -> tuple[dict, dict, dict]:
        """
        Priority 2: Process power ports - find roots, merge same-named.

                Returns:
                    (power_port_roots, power_port_ids_by_root)
        """
        power_port_roots: dict[str, list[RootPoint]] = defaultdict(list)
        offsheet_roots: dict[str, list[RootPoint]] = defaultdict(list)
        power_port_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)

        for precise_location, name, pp_obj in self._power_ports:
            if not name or name.isspace():
                continue
            root, pin_found = self._find_ordinary_item_root(
                precise_location,
                uf,
                pin_groups,
            )
            if root is None:
                root = self._prepared_net_item_root(
                    precise_location,
                    uf,
                    require_connection=False,
                )
            if root is not None:
                is_offsheet = pp_obj.__class__.__name__ == "SchCrossSheetConnectorInfo"
                kind = "offsheet_connector" if is_offsheet else "power_port"
                scalar_merge_name_sources[root].append((kind, name))
                roots = offsheet_roots if is_offsheet else power_port_roots
                roots[dotnet_ordinal_ignore_case_key(name)].append(root)
                self._assign_name_candidate(
                    net_names,
                    root,
                    self._name_candidate(name, kind),
                )
                name_to_root[name] = root
                if pp_obj.unique_id:
                    power_port_ids_by_root[root].append(pp_obj.unique_id)

        # Merge same-named power ports
        for _name, roots in power_port_roots.items():
            if len(roots) > 1:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])

        for roots in offsheet_roots.values():
            for left, right in zip(roots, roots[1:]):
                uf.union(left, right)

        return power_port_roots, offsheet_roots, power_port_ids_by_root

    def _process_sheet_entries(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
    ) -> tuple[dict, dict]:
        """
        Priority 2.5: Process sheet entries for hierarchical bridging.

                Returns:
                    (sheet_entry_roots, sheet_entry_ids_by_root)
        """
        sheet_entry_roots: dict[str, list[RootPoint]] = defaultdict(list)
        sheet_entry_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)
        emitted_element_ids: set[str] = set()
        self._sheet_entry_names_by_element_id.clear()
        self._sheet_entry_object_ids_by_element_id.clear()

        for precise_location, entry_name, sheet_sym_info, entry in self._sheet_entries:
            if not entry_name or entry_name.isspace():
                continue
            root, _pin_found = self._find_ordinary_item_root(
                precise_location,
                uf,
                pin_groups,
            )
            if root is None:
                root = self._prepared_net_item_root(
                    precise_location,
                    uf,
                    require_connection=False,
                )
            entry_uid = self._sheet_symbol_uid(sheet_sym_info)
            if root is not None:
                sheet_entry_roots[dotnet_ordinal_ignore_case_key(entry_name)].append(
                    root
                )
                if self.options.allow_sheet_entries_to_name_nets:
                    self._assign_name_candidate(
                        net_names,
                        root,
                        self._name_candidate(entry_name, "sheet_entry"),
                    )
                    name_to_root[entry_name] = root
                element_id = _unique_sheet_entry_element_id(
                    f"{entry_uid}_{entry_name}",
                    emitted_element_ids,
                )
                self._sheet_entry_names_by_element_id[element_id] = entry_name
                self._sheet_entry_object_ids_by_element_id[element_id] = str(
                    getattr(entry, "unique_id", "") or element_id
                )
                self._sheet_entry_connection_points_by_element_id[element_id] = (
                    precise_location[0],
                    precise_location[1],
                )
                sheet_entry_ids_by_root[root].append(element_id)

        return sheet_entry_roots, sheet_entry_ids_by_root

    def _process_visible_pin_names(
        self,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
    ) -> None:
        for root, pins in pin_groups.items():
            visible_pins = [
                pin
                for pin in pins
                if not pin_is_runtime_hidden(pin, getattr(pin, "component", None))
            ]
            if not visible_pins:
                continue
            first_pin = min(visible_pins, key=cmp_to_key(_managed_pin_compare))
            name = f"Net{first_pin.component_designator}_{first_pin.designator}"
            self._assign_name_candidate(
                net_names,
                root,
                self._name_candidate(name, "pin"),
            )
            name_to_root[name] = root

    def _process_ports(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
        scalar_merge_name_sources: dict[RootPoint, list[tuple[str, str]]],
    ) -> tuple[dict, dict]:
        """
        Priority 3: Process ports - find roots, merge same-named.

                Returns:
                    (port_roots, port_ids_by_root)
        """
        port_roots: dict[str, list[RootPoint]] = defaultdict(list)
        port_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)

        for precise_location, name, port_obj in self._ports:
            if not name or name.isspace():
                continue
            port_uid = port_obj.unique_id
            root, _pin_found = self._find_ordinary_item_root(
                precise_location,
                uf,
                pin_groups,
            )
            if root is None:
                root = self._prepared_net_item_root(
                    precise_location,
                    uf,
                    require_connection=False,
                )

            if root is not None:
                # Connected via wire or direct pin connection
                scalar_merge_name_sources[root].append(("port", name))
                port_roots[dotnet_ordinal_ignore_case_key(name)].append(root)
                if self.options.allow_ports_to_name_nets:
                    self._assign_name_candidate(
                        net_names,
                        root,
                        self._name_candidate(name, "port"),
                    )
                    name_to_root[name] = root
                if port_uid:
                    port_ids_by_root[root].append(port_uid)
            elif port_uid:
                # Dangling port - create virtual root for hierarchy bridge
                uf.find(precise_location)  # Register in union-find
                scalar_merge_name_sources[precise_location].append(("port", name))
                port_roots[dotnet_ordinal_ignore_case_key(name)].append(
                    precise_location
                )
                port_ids_by_root[precise_location].append(port_uid)
                if self.options.allow_ports_to_name_nets:
                    self._assign_name_candidate(
                        net_names,
                        precise_location,
                        self._name_candidate(name, "port"),
                    )
                    name_to_root[name] = precise_location

        # Merge same-named ports
        for _name, roots in port_roots.items():
            if len(roots) > 1:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])

        return port_roots, port_ids_by_root

    def _hidden_pin_nets(
        self,
        pin_groups: dict[RootPoint, list],
        floating_pin_roots: set[RootPoint],
        scalar_merge_name_sources: dict[RootPoint, list[tuple[str, str]]],
    ) -> tuple[dict[str, list[RootPoint]], dict[str, str]]:
        hidden_pin_nets: dict[str, list[RootPoint]] = defaultdict(list)
        hidden_pin_names: dict[str, str] = {}
        for root in floating_pin_roots:
            for pin in pin_groups.get(root, []):
                hidden_net_name = str(_compiler_hidden_net_name(pin) or "")
                component = getattr(pin, "component", None)
                if component is None or not pin_is_runtime_hidden(pin, component):
                    continue
                if hidden_net_name and not hidden_net_name.isspace():
                    scalar_merge_name_sources.setdefault(root, []).append(
                        ("hidden_pin", hidden_net_name)
                    )
                    log.debug(
                        f"Hidden pin {pin.component_designator}.{pin.designator} "
                        f"({pin.name}) will connect to net '{hidden_net_name}'"
                    )
                    name_key = dotnet_ordinal_ignore_case_key(hidden_net_name)
                    hidden_pin_nets[name_key].append(root)
                    current_name = hidden_pin_names.get(name_key)
                    if current_name is None or self._candidate_is_better(
                        self._name_candidate(hidden_net_name, "hidden_pin"),
                        self._name_candidate(current_name, "hidden_pin"),
                    ):
                        hidden_pin_names[name_key] = hidden_net_name
        return hidden_pin_nets, hidden_pin_names

    def _merge_hidden_pin_nets(
        self,
        uf: UnionFind,
        hidden_pin_nets: dict[str, list[RootPoint]],
        hidden_pin_names: dict[str, str],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
    ) -> None:
        for name_key, roots in hidden_pin_nets.items():
            for index in range(len(roots) - 1):
                uf.union(roots[index], roots[index + 1])
            if roots:
                final_root = uf.find(roots[0])
                self._assign_name_candidate(
                    net_names,
                    final_root,
                    self._name_candidate(hidden_pin_names[name_key], "hidden_pin"),
                )
                name_to_root[net_names[final_root].name] = final_root

    def _process_hidden_pins(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        floating_pin_roots: set[RootPoint],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
        scalar_merge_name_sources: dict[RootPoint, list[tuple[str, str]]],
    ) -> dict[str, list[RootPoint]]:
        """
        Step 6.5: Handle hidden pins (implicit power connections).

                Hidden pins without wire connections are implicitly connected only
                when their authored hidden_net_name is non-empty.
                Mutates net_names and name_to_root in place.
        """
        hidden_pin_nets, hidden_pin_names = self._hidden_pin_nets(
            pin_groups,
            floating_pin_roots,
            scalar_merge_name_sources,
        )
        self._merge_hidden_pin_nets(
            uf,
            hidden_pin_nets,
            hidden_pin_names,
            net_names,
            name_to_root,
        )
        return hidden_pin_nets

    @staticmethod
    def _merge_cross_category_authored_names(
        uf: UnionFind,
        *root_maps: dict[str, list[RootPoint]],
    ) -> None:
        roots_by_name: dict[str, list[RootPoint]] = defaultdict(list)
        for root_map in root_maps:
            for name, roots in root_map.items():
                if not name or name.isspace():
                    continue
                roots_by_name[dotnet_ordinal_ignore_case_key(name)].extend(roots)
        for roots in roots_by_name.values():
            for left, right in zip(roots, roots[1:]):
                uf.union(left, right)

    def _attach_floating_labels_to_power_names(
        self,
        uf: UnionFind,
        floating_labels: dict[str, list[str]],
        floating_label_roots: dict[str, list[RootPoint]],
        label_roots: dict[str, list[RootPoint]],
        label_ids_by_root: dict[RootPoint, list[str]],
        power_roots: dict[str, list[RootPoint]],
        hidden_roots: dict[str, list[RootPoint]],
        net_names: dict[RootPoint, _NetNameCandidate],
        name_to_root: dict[str, RootPoint],
    ) -> None:
        for name in tuple(floating_label_roots):
            name_key = dotnet_ordinal_ignore_case_key(name)
            peers = [*power_roots.get(name_key, ()), *hidden_roots.get(name_key, ())]
            if not peers:
                continue
            label_points = floating_label_roots.pop(name)
            for point in label_points:
                uf.find(point)
                uf.union(point, peers[0])
                self._assign_name_candidate(
                    net_names,
                    point,
                    self._name_candidate(name, "net_label"),
                )
            label_roots[name_key].extend(label_points)
            label_ids_by_root[label_points[0]].extend(floating_labels.pop(name, ()))
            name_to_root[name] = label_points[0]

    def _name_candidate(self, name: str, kind: str) -> _NetNameCandidate:
        return _NetNameCandidate(
            name=name,
            priority=_managed_name_priority(kind, self.options),
            kind=kind,
        )

    @staticmethod
    def _candidate_is_better(
        candidate: _NetNameCandidate,
        current: _NetNameCandidate,
    ) -> bool:
        if candidate.priority != current.priority:
            return candidate.priority > current.priority
        managed_order = managed_alpha_numeric_compare(candidate.name, current.name)
        if managed_order:
            return managed_order < 0
        candidate_units = dotnet_utf16_units(candidate.name)
        current_units = dotnet_utf16_units(current.name)
        return candidate_units > current_units

    @classmethod
    def _assign_name_candidate(
        cls,
        net_names: dict[RootPoint, _NetNameCandidate],
        root: RootPoint,
        candidate: _NetNameCandidate,
    ) -> None:
        if not candidate.name or candidate.name.isspace():
            return
        current = net_names.get(root)
        if current is None or cls._candidate_is_better(candidate, current):
            net_names[root] = candidate

    # -----------------------------------------------------------------
    # Net creation and ordering (called by _assign_names_and_build_nets)
    # -----------------------------------------------------------------

    def _create_net_from_pins(
        self,
        name: str,
        pins: PinGroup,
        root: RootPoint,
        final_wire_ids: dict,
        final_nl_ids: dict,
        final_pp_ids: dict,
        final_port_ids: dict,
        final_se_ids: dict,
        final_label_names: dict,
        is_auto_named: bool = False,
    ) -> Net:
        """
        Create Net from pins (deduplicated) with typed graphical data.

                Populates structured NetGraphical data and preserves actual pin
                unique IDs for SVG element references.
        """
        terminals = []
        endpoints: list[NetEndpoint] = []
        endpoint_seen: set[str] = set()
        representative_pins: dict[tuple[int, str], _NetPinLike] = {}

        label_ids = sorted(
            dict.fromkeys(final_nl_ids.get(root, [])),
            key=self._net_label_object_sort_key,
        )
        graphical = NetGraphical(
            wires=sorted(
                final_wire_ids.get(root, []),
                key=self._wire_object_sort_key,
            ),
            labels=label_ids,
            power_ports=list(final_pp_ids.get(root, [])),
            ports=list(final_port_ids.get(root, [])),
            sheet_entries=list(final_se_ids.get(root, [])),
        )

        for candidate in pins:
            component_key = id(getattr(candidate, "component", candidate))
            key = (component_key, candidate.designator)
            current = representative_pins.get(key)
            if current is None or self._ad_common_pin_sort_key(
                candidate
            ) < self._ad_common_pin_sort_key(current):
                representative_pins[key] = candidate

        ordered_pins = sorted(
            representative_pins.values(),
            key=cmp_to_key(_managed_pin_compare),
        )
        display_pin_counts = Counter(
            (pin.component_designator, pin.designator) for pin in ordered_pins
        )
        for pin in ordered_pins:
            comp_des = pin.component_designator
            pin_des = pin.designator

            pin_name = pin.name or ""
            pin_type = _pin_electrical_to_pintype(pin.electrical)

            # Resolve part value for parameter evaluation side effects.
            comp = getattr(pin, "component", None)
            component_id = self._component_contract_id(comp)
            if comp:
                _resolve_component_display_value(
                    comp,
                    project_params=self.options.project_parameters,
                    sheet_params=self.options.sheet_parameters,
                    component_description=comp.description,
                )

            terminals.append(
                Terminal(
                    designator=comp_des,
                    pin=pin_des,
                    pin_name=pin_name,
                    pin_type=pin_type,
                    _source_component_uid=str(
                        getattr(pin, "component_unique_id", "") or ""
                    ),
                    _source_pin_uid=str(getattr(pin, "unique_id", "") or ""),
                    _source_pin_object_id=id(getattr(pin, "pin", pin)),
                    _source_owner_part_id=int(
                        getattr(getattr(pin, "pin", None), "owner_part_id", None) or 1
                    ),
                    component_id=component_id,
                )
            )

            pin_svg_id = pin.unique_id
            if pin_svg_id:
                graphical.pins.append(
                    GraphicalPinRef(
                        designator=comp_des,
                        pin=pin_des,
                        svg_id=pin_svg_id,
                        component_id=component_id,
                    )
                )
            endpoint_id = f"pin:{comp_des}:{pin_des}"
            if display_pin_counts[(comp_des, pin_des)] > 1:
                source_suffix = pin_svg_id or str(
                    getattr(pin, "component_unique_id", "") or ""
                )
                if source_suffix:
                    endpoint_id = f"{endpoint_id}:{source_suffix}"
            self._append_net_endpoint(
                endpoints,
                endpoint_seen,
                NetEndpoint(
                    endpoint_id=endpoint_id,
                    role="pin",
                    _source_pin_object_id=id(getattr(pin, "pin", pin)),
                    element_id=pin_svg_id,
                    object_id=pin_svg_id,
                    name=pin_name,
                    designator=comp_des,
                    pin=pin_des,
                    pin_name=pin_name,
                    pin_type=pin_type,
                    connection_point=pin.connection_point,
                    component_id=component_id,
                ),
            )

        self._append_endpoint_ids(
            endpoints,
            endpoint_seen,
            role="power_port",
            ids=final_pp_ids.get(root, []),
            name_for_id=self._power_like_name_for_id,
            role_for_id=self._power_like_role_for_id,
            connection_point_for_id=self._power_like_connection_point_for_id,
        )
        self._append_endpoint_ids(
            endpoints,
            endpoint_seen,
            role="port",
            ids=final_port_ids.get(root, []),
            name_for_id=self._port_name_for_id,
            connection_point_for_id=self._port_connection_point_for_id,
        )
        self._append_endpoint_ids(
            endpoints,
            endpoint_seen,
            role="sheet_entry",
            ids=final_se_ids.get(root, []),
            name_for_id=self._sheet_entry_name_for_id,
            object_id_for_id=self._sheet_entry_object_id_for_id,
            connection_point_for_id=self._sheet_entry_connection_point_for_id,
        )

        all_label_names = final_label_names.get(root, [])
        aliases = sorted(set(n for n in all_label_names if n != name))

        net = Net(
            name=name,
            terminals=terminals,
            graphical=graphical,
            auto_named=is_auto_named,
            aliases=aliases,
            endpoints=endpoints,
        )
        net._managed_item_count = (
            len(ordered_pins)
            + len(label_ids)
            + len(graphical.power_ports)
            + len(graphical.ports)
            + len(graphical.sheet_entries)
        )
        net._managed_removed_item_count = len(pins) - len(ordered_pins)
        net._managed_first_item_location = self._managed_first_item_location(
            ordered_pins,
            label_ids,
            graphical,
        )
        return net

    def _managed_first_item_location(
        self,
        ordered_pins: list[_NetPinLike],
        label_ids: list[str],
        graphical: NetGraphical,
    ) -> tuple[int, int] | None:
        if ordered_pins:
            point = ordered_pins[0].connection_point
            return (point[0], point[1])
        if label_ids:
            key = self._net_label_object_sort_key(label_ids[0])
            return (key[0], key[2])
        typed_ids = (
            self._power_like_ids_for_role(graphical.power_ports, "offsheet_connector"),
            graphical.ports,
            self._power_like_ids_for_role(graphical.power_ports, "power_port"),
            graphical.sheet_entries,
        )
        point_lookups = (
            self._power_like_connection_point_for_id,
            self._port_connection_point_for_id,
            self._power_like_connection_point_for_id,
            self._sheet_entry_connection_point_for_id,
        )
        for element_ids, point_lookup in zip(typed_ids, point_lookups, strict=True):
            if point := self._minimum_connection_point(element_ids, point_lookup):
                return point
        return None

    def _power_like_ids_for_role(self, element_ids: list[str], role: str) -> list[str]:
        return [
            element_id
            for element_id in element_ids
            if self._power_like_role_for_id(element_id) == role
        ]

    @staticmethod
    def _minimum_connection_point(
        element_ids: list[str],
        point_lookup: Callable[[str], tuple[int, int] | None],
    ) -> tuple[int, int] | None:
        points = (
            point
            for element_id in element_ids
            if (point := point_lookup(element_id)) is not None
        )
        return min(points, default=None)

    @staticmethod
    def _append_net_endpoint(
        endpoints: list[NetEndpoint],
        seen: set[str],
        endpoint: NetEndpoint,
    ) -> None:
        key = endpoint.endpoint_id or (
            f"{endpoint.role}:{endpoint.element_id}:{endpoint.designator}:{endpoint.pin}"
        )
        if not key or key in seen:
            return
        seen.add(key)
        endpoints.append(endpoint)

    def _net_label_object_sort_key(
        self,
        element_id: str,
    ) -> tuple[int, int, int, int, str]:
        """Order equal-type label objects like AD's compiled SignalContext."""
        for location, _name, obj in self._net_labels:
            if getattr(obj, "unique_id", "") == element_id:
                x, y, x_frac, y_frac = location
                return (x, x_frac, y, y_frac, element_id)
        return (2**31 - 1, 2**31 - 1, 2**31 - 1, 2**31 - 1, element_id)

    def _wire_object_sort_key(
        self, element_id: str
    ) -> tuple[int, int, int, int, int, int, int, int, str]:
        """Order source wires like AD's compiled ``LineAdapter`` collection."""
        maximum = 2**63 - 1
        return self._wire_sort_keys_by_element_id.get(
            element_id,
            (
                maximum,
                maximum,
                maximum,
                maximum,
                maximum,
                maximum,
                maximum,
                maximum,
                element_id,
            ),
        )

    def _source_wire_sort_key(
        self, wire: object, element_id: str
    ) -> tuple[int, int, int, int, int, int, int, int, str] | None:
        segments = []
        points = self._get_wire_points(wire)
        for first, second in zip(points, points[1:]):
            first_key = (first[0], first[2], first[1], first[3])
            second_key = (second[0], second[2], second[1], second[3])
            segments.append((*min(first_key, second_key), *max(first_key, second_key)))
        return (*min(segments), element_id) if segments else None

    def _append_endpoint_ids(
        self,
        endpoints: list[NetEndpoint],
        seen: set[str],
        *,
        role: str,
        ids: list[str],
        name_for_id: Callable[[str], str],
        role_for_id: Callable[[str], str] | None = None,
        object_id_for_id: Callable[[str], str] | None = None,
        connection_point_for_id: Callable[[str], tuple[int, int] | None] | None = None,
    ) -> None:
        for element_id in ids:
            clean_id = str(element_id or "").strip()
            if not clean_id:
                continue
            endpoint_role = role_for_id(clean_id) if role_for_id else role
            object_id = object_id_for_id(clean_id) if object_id_for_id else clean_id
            self._append_net_endpoint(
                endpoints,
                seen,
                NetEndpoint(
                    endpoint_id=f"{endpoint_role}:{clean_id}",
                    role=endpoint_role,
                    element_id=clean_id,
                    object_id=object_id,
                    name=name_for_id(clean_id),
                    connection_point=(
                        connection_point_for_id(clean_id)
                        if connection_point_for_id
                        else None
                    ),
                ),
            )

    def _power_like_name_for_id(self, element_id: str) -> str:
        for _location, _name, obj in self._power_ports:
            if getattr(obj, "unique_id", "") == element_id:
                return str(getattr(obj, "text", "") or "")
        return ""

    def _power_like_role_for_id(self, element_id: str) -> str:
        for _location, _name, obj in self._power_ports:
            if getattr(obj, "unique_id", "") != element_id:
                continue
            if obj.__class__.__name__ == "SchCrossSheetConnectorInfo":
                return "offsheet_connector"
            return "power_port"
        return "power_port"

    def _power_like_connection_point_for_id(
        self, element_id: str
    ) -> tuple[int, int] | None:
        for location, _name, obj in self._power_ports:
            if getattr(obj, "unique_id", "") == element_id:
                return (location[0], location[1])
        return None

    def _port_name_for_id(self, element_id: str) -> str:
        for _location, _name, obj in self._ports:
            if getattr(obj, "unique_id", "") == element_id:
                return str(getattr(obj, "name", "") or "")
        return ""

    def _port_connection_point_for_id(self, element_id: str) -> tuple[int, int] | None:
        for location, _name, obj in self._ports:
            if getattr(obj, "unique_id", "") == element_id:
                return (location[0], location[1])
        return None

    def _sheet_entry_name_for_id(self, element_id: str) -> str:
        mapped_name = self._sheet_entry_names_by_element_id.get(element_id)
        if mapped_name is not None:
            return mapped_name
        for _location, entry_name, sheet_sym_info, entry in self._sheet_entries:
            if f"{self._sheet_symbol_uid(sheet_sym_info)}_{entry_name}" == element_id:
                return entry_name
            if getattr(entry, "unique_id", "") == element_id:
                return entry_name
        return ""

    def _sheet_entry_object_id_for_id(self, element_id: str) -> str:
        mapped_object_id = self._sheet_entry_object_ids_by_element_id.get(element_id)
        if mapped_object_id is not None:
            return mapped_object_id
        for _location, entry_name, sheet_sym_info, entry in self._sheet_entries:
            if f"{self._sheet_symbol_uid(sheet_sym_info)}_{entry_name}" != element_id:
                continue
            return str(getattr(entry, "unique_id", "") or element_id)
        return element_id

    def _sheet_entry_connection_point_for_id(
        self, element_id: str
    ) -> tuple[int, int] | None:
        return self._sheet_entry_connection_points_by_element_id.get(element_id)

    def _sheet_symbol_uid(self, sheet_symbol: SchSheetSymbolInfo) -> str:
        raw = str(getattr(sheet_symbol.record, "unique_id", "") or "")
        return raw or self._sheet_symbol_uids_by_info_id.get(id(sheet_symbol), "")

    def _order_and_output_nets(
        self,
        uf: UnionFind,
        final_pin_groups: dict,
        final_net_names: dict,
        final_name_source_kinds: dict[RootPoint, str],
        final_name_source_priorities: dict[RootPoint, int],
        explicit_named_net_names: dict,
        sheet_entry_net_names: dict,
        port_net_names: dict,
        final_wire_ids: dict,
        final_nl_ids: dict,
        final_pp_ids: dict,
        final_port_ids: dict,
        final_se_ids: dict,
        final_label_names: dict,
        final_scalar_merge_name_sources: dict[RootPoint, list[tuple[str, str]]],
        floating_net_labels: dict[str, list[str]],
        floating_pin_roots: set[RootPoint],
        port_roots: dict,
        se_roots: dict,
    ) -> list[Net]:
        """
        Order nets by Altium's priority and build final Net objects.

                Returns:
                    Ordered list of Net objects
        """

        # Shorthand for create_net with all the final maps
        def create_net(
            name: str,
            pins: PinGroup,
            root: RootPoint,
            is_auto_named: bool = False,
        ) -> Net:
            net = self._create_net_from_pins(
                name,
                pins,
                root,
                final_wire_ids,
                final_nl_ids,
                final_pp_ids,
                final_port_ids,
                final_se_ids,
                final_label_names,
                is_auto_named,
            )
            net._name_source_kind = (
                "pin" if is_auto_named else final_name_source_kinds.get(root, "")
            )
            net._name_source_priority = final_name_source_priorities.get(root, 0)
            net._name_source_raw_name = name
            net._scalar_merge_name_sources = tuple(
                dict.fromkeys(final_scalar_merge_name_sources.get(root, ()))
            )
            return net

        nets: list[Net] = []
        processed_roots: set[RootPoint] = set()

        # Order 1: Named nets (net labels + power ports + floating labels)
        self._emit_named_nets(
            nets,
            processed_roots,
            create_net,
            explicit_named_net_names,
            final_pin_groups,
            floating_net_labels,
        )

        # Order 2.5: Ports (if naming enabled)
        if self.options.allow_ports_to_name_nets:
            _emit_port_named_nets(
                nets,
                processed_roots,
                create_net,
                port_net_names,
                final_pin_groups,
            )

        # Order 2.75: Sheet entry named nets
        if self.options.allow_sheet_entries_to_name_nets:
            _emit_named_roots(
                nets,
                processed_roots,
                create_net,
                sheet_entry_net_names,
                final_pin_groups,
                allow_empty_pins=True,
            )

        # Order 2.8: Unprocessed port/entry roots for hierarchy bridging
        bridge_exact_names: dict[RootPoint, str] = {}
        for root in {*final_port_ids, *final_se_ids}:
            candidates = [
                *(
                    self._port_name_for_id(value)
                    for value in final_port_ids.get(root, ())
                ),
                *(
                    self._sheet_entry_name_for_id(value)
                    for value in final_se_ids.get(root, ())
                ),
            ]
            candidates = [value for value in candidates if value]
            if candidates:
                bridge_exact_names[root] = self._best_label_name(candidates)
        _emit_bridge_roots(
            nets,
            processed_roots,
            create_net,
            uf,
            self.options.net_identifier_scope,
            final_net_names,
            final_pin_groups,
            final_port_ids,
            final_se_ids,
            port_roots,
            se_roots,
            bridge_exact_names,
            self._bridge_eligible_interface_ids,
        )

        # Order 3: Auto-named nets
        _emit_auto_named_nets(
            nets,
            processed_roots,
            create_net,
            uf,
            final_pin_groups,
            floating_pin_roots,
            include_single_pin_nets=self.options.allow_single_pin_nets,
        )

        nets.sort(key=cmp_to_key(_managed_local_net_compare))
        return nets

    def _emit_named_nets(
        self,
        nets: list[Net],
        processed_roots: set[RootPoint],
        create_net: _CreateNetFn,
        explicit_named_net_names: dict[RootPoint, str],
        final_pin_groups: PinGroupsByRoot,
        floating_net_labels: dict[str, list[str]],
    ) -> None:
        """
        Emit named nets: net labels + power ports + floating labels (Order 1).
        """
        named_rows = [
            *((name, root) for root, name in explicit_named_net_names.items()),
            *((name, None) for name in floating_net_labels),
        ]
        named_rows.sort(
            key=lambda row: (_altium_net_total_sort_key(row[0]), row[1] is not None),
            reverse=True,
        )
        for name, root in named_rows:
            if root is not None:
                nets.append(create_net(name, final_pin_groups.get(root, []), root))
                processed_roots.add(root)
            else:
                label_ids = sorted(
                    dict.fromkeys(floating_net_labels[name]),
                    key=self._net_label_object_sort_key,
                )
                nets.append(
                    Net(
                        name=name,
                        terminals=[],
                        graphical=NetGraphical(labels=label_ids),
                        auto_named=False,
                        _name_source_kind="net_label",
                        _name_source_priority=self._name_candidate(
                            name, "net_label"
                        ).priority,
                        _name_source_raw_name=name,
                        _scalar_merge_name_sources=(("net_label", name),),
                        _managed_item_count=len(label_ids),
                        _managed_first_item_location=(
                            self._net_label_object_sort_key(label_ids[0])[0],
                            self._net_label_object_sort_key(label_ids[0])[2],
                        )
                        if label_ids
                        else None,
                        endpoints=[
                            NetEndpoint(
                                endpoint_id=f"net_label:{label_id}",
                                role="net_label",
                                element_id=label_id,
                                object_id=label_id,
                                name=name,
                            )
                            for label_id in label_ids
                        ],
                    )
                )


__all__ = ["AltiumNetlistSingleSheetCompiler"]
