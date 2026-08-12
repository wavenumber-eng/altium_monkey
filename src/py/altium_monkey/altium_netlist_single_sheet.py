"""
Single-sheet netlist compiler.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Callable, Protocol, TypeAlias, cast

from .altium_netlist_options import NetlistOptions
from .altium_netlist_common import (
    CHASSIS_GND_MAPPINGS,
    PinGroup,
    POWER_PIN_NAMES,
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
    _power_bus_net_label_text,
)
from .altium_compiled_design_support import _harness_connector_master_entry_point
from .altium_sch_record_helpers import (
    _basic_entry_distance_to_rounded_native_units,
    _coord_scalar_to_native_parts,
    _coord_scalar_to_rounded_native_units,
    _coord_scalar_with_basic_entry_distance_to_native_parts,
    _coord_scalar_with_basic_entry_distance_to_rounded_native_units,
    _effective_basic_entry_distance_frac1,
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


class _CreateNetFn(Protocol):
    def __call__(
        self,
        name: str,
        pins: PinGroup,
        root: RootPoint,
        is_auto_named: bool = False,
    ) -> Net: ...


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
        self.schdoc = schdoc
        self.strict = strict
        self.options = options or NetlistOptions()

        self.tolerance = tolerance
        display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
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
        self._inferred_port_segments: list[tuple[RootPoint, RootPoint]] = []

        # Sheet entry tracking (for hierarchical connectivity)
        # Maps connection hotspot -> (entry_name, sheet_symbol_info)
        self._sheet_entries: list[
            tuple[RootPoint, str, SchSheetSymbolInfo, object]
        ] = []

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
        self._active_pin_designators_by_component: dict[str, set[str]] = defaultdict(
            set
        )
        self._masked_pin_designators_by_component: dict[str, set[str]] = defaultdict(
            set
        )

        # Compile mask bounds - components inside are excluded from netlist
        # Each bound is (min_x, min_y, max_x, max_y)
        self._compile_mask_bounds: list[tuple[int, int, int, int]] = []
        if hasattr(schdoc, "_collect_compile_mask_bounds"):
            self._compile_mask_bounds = schdoc._collect_compile_mask_bounds()
            if self._compile_mask_bounds:
                log.debug(f"Found {len(self._compile_mask_bounds)} compile masks")

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
            if min_x <= x <= max_x and min_y <= y <= max_y:
                return True
        return False

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
        for comp in self.schdoc.get_components():
            if comp.designator and comp.includes_in_netlist():
                # Check if component is inside a compile mask
                x, y = comp.location
                if self._is_inside_compile_mask(x, y):
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
        for pin in self.schdoc.get_all_pins():
            # AD eliminates a pin when either the component or the pin hotspot is
            # inside a compile mask. A mask may cover only part of a large symbol.
            comp = pin.component
            if comp:
                x, y = comp.location
                if self._is_inside_compile_mask(x, y):
                    log.debug(
                        f"Pin {pin.designator}.{pin.name} excluded (parent inside compile mask)"
                    )
                    masked_count += 1
                    self._masked_pin_designators_by_component[
                        pin.component_designator.lower()
                    ].add(pin.designator)
                    continue

            pin_x, pin_y = pin.connection_point
            if self._is_inside_compile_mask(pin_x, pin_y):
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
                if self._is_inside_compile_mask(loc[0], loc[1]):
                    log.debug(f"NetLabel '{text}' excluded by compile mask at {loc}")
                    masked_count += 1
                    continue
                location = nl.record.location
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
                if self._is_inside_compile_mask(loc[0], loc[1]):
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
        for port in self.schdoc.get_ports():
            name = port.name
            if name:
                connection_points = port._precise_connection_points
                if not connection_points:
                    continue
                self._ports.append((connection_points[0], name, port))
                harness_type = str(getattr(port.record, "harness_type", "") or "")
                if len(connection_points) > 1 and ".." not in name and not harness_type:
                    self._inferred_port_segments.append(
                        (connection_points[0], connection_points[1])
                    )
                if name not in self._port_names_ordered:
                    self._port_names_ordered.append(name)

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
        for sheet_sym_info in self.schdoc.get_sheet_symbols():
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
                        _sheet_entry_precise_connection_point(sheet_sym_info, entry),
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
            if kind(name, (location,)) is AnalyserNetItemKind.WIRE:
                separated_labels.append((location, name, obj))
                continue
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
            separated_ports.append(
                (
                    points[0]
                    if item_kind is AnalyserNetItemKind.WIRE
                    else virtual_root(),
                    name,
                    obj,
                )
            )
            if item_kind is AnalyserNetItemKind.WIRE and len(points) == 2:
                scalar_port_segments.append((points[0], points[1]))
        self._ports = separated_ports

        separated_entries: list[tuple[RootPoint, str, SchSheetSymbolInfo, object]] = []
        for location, name, symbol, entry in self._sheet_entries:
            item_kind = kind(
                name,
                (location,),
                str(getattr(entry, "harness_type", "") or ""),
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

    # =========================================================================
    # Building Methods
    # =========================================================================

    def _build_components(self) -> list[NetlistComponent]:
        """
        Build NetlistComponent list from extracted components.
        """
        result = []
        for comp in self._components:
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
                    _source_component_uid=comp.unique_id,
                )
            )
        return result

    def _build_nets(self) -> list[Net]:
        """
        Build nets using Union-Find connectivity algorithm.
        """
        # Build the wire connectivity graph.
        uf = UnionFind()
        wire_ids_by_root = self._build_wire_connectivity(uf)

        # Group pins by connected wire network.
        pin_groups, floating_pin_roots = self._group_pins_by_network(uf)

        # Assign names and build final nets.
        return self._assign_names_and_build_nets(
            uf, wire_ids_by_root, pin_groups, floating_pin_roots
        )

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
            root_representatives: dict[tuple[str, str, str], SchPinInfo] = {}
            for pin in pins:
                component_key = pin.component_unique_id or pin.unique_id
                key = (
                    component_key or pin.component_designator.lower(),
                    pin.component_designator.lower(),
                    pin.designator,
                )
                current = root_representatives.get(key)
                if current is None or self._ad_common_pin_sort_key(
                    pin
                ) < self._ad_common_pin_sort_key(current):
                    root_representatives[key] = pin
            for key, pin in root_representatives.items():
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
        net_names: dict[RootPoint, str] = {}
        name_to_root: dict[str, RootPoint] = {}
        self._prepare_net_item_roots(uf, pin_groups)

        # Priority 1: NetLabels
        nl_roots, label_names_by_root, floating_labels, nl_ids = (
            self._process_net_labels(uf, pin_groups, net_names, name_to_root)
        )

        # Priority 2: PowerPorts
        pp_roots, pp_ids = self._process_power_ports(
            uf, pin_groups, net_names, name_to_root
        )

        # Priority 2.5: Sheet entries
        se_roots, se_ids = self._process_sheet_entries(
            uf,
            pin_groups,
            net_names,
            name_to_root,
        )

        # Priority 3: Ports
        port_roots, port_ids = self._process_ports(
            uf, pin_groups, net_names, name_to_root
        )

        # Step 6.5: Hidden pins
        self._process_hidden_pins(
            uf, pin_groups, floating_pin_roots, net_names, name_to_root
        )

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
        )

        # Remap net_names (first-wins logic, not list-extend)
        final_net_names: dict[RootPoint, str] = {}
        for old_root, name in net_names.items():
            current_root = uf.find(old_root)
            if current_root not in final_net_names:
                final_net_names[current_root] = name

        final_name_to_root: dict[str, RootPoint] = {}
        for root, name in final_net_names.items():
            if name not in final_name_to_root:
                final_name_to_root[name] = root

        self._capture_compiled_pin_roots(remapped["pin_groups"])

        # Build and order final nets
        return self._order_and_output_nets(
            uf=uf,
            final_pin_groups=remapped["pin_groups"],
            final_net_names=final_net_names,
            final_name_to_root=final_name_to_root,
            final_wire_ids=remapped["wire_ids"],
            final_nl_ids=remapped["nl_ids"],
            final_pp_ids=remapped["pp_ids"],
            final_port_ids=remapped["port_ids"],
            final_se_ids=remapped["se_ids"],
            final_label_names=remapped["label_names"],
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
        net_names: dict[RootPoint, str],
        name_to_root: dict[str, RootPoint],
    ) -> tuple[
        dict[str, list[RootPoint]],
        dict[RootPoint, list[str]],
        dict[str, list[str]],
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
        net_label_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)

        for precise_location, name, nl_obj in self._net_labels:
            if id(nl_obj) in self._nonwire_net_label_object_ids:
                if nl_obj.unique_id:
                    floating_net_labels[name].append(nl_obj.unique_id)
                else:
                    floating_net_labels.setdefault(name, [])
                continue
            wp = self._find_wire_point_for_netlabel(nl_obj)
            if wp is not None:
                root = uf.find(wp)
                net_label_roots[name].append(root)
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
                    if nl_obj.unique_id:
                        floating_net_labels[name].append(nl_obj.unique_id)
                    else:
                        floating_net_labels.setdefault(name, [])
                    continue
                net_label_roots[name].append(pin_root)
                root_to_label_names[pin_root].append(name)
                if nl_obj.unique_id:
                    net_label_ids_by_root[pin_root].append(nl_obj.unique_id)

        for name in set(floating_net_labels) & set(net_label_roots):
            root = net_label_roots[name][0]
            net_label_ids_by_root[root].extend(floating_net_labels.pop(name))

        # Merge same-named net labels
        for _name, roots in net_label_roots.items():
            if len(roots) > 1:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])

        # Assign names (alphabetically first when multiple)
        for orig_root, names in root_to_label_names.items():
            current_root = uf.find(orig_root)
            if current_root not in net_names:
                chosen_name = min(names)
                net_names[current_root] = chosen_name
                name_to_root[chosen_name] = current_root
            else:
                existing = net_names[current_root]
                chosen_name = min(names + [existing])
                net_names[current_root] = chosen_name
                name_to_root[chosen_name] = current_root

        return (
            net_label_roots,
            root_to_label_names,
            floating_net_labels,
            net_label_ids_by_root,
        )

    def _process_power_ports(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, str],
        name_to_root: dict[str, RootPoint],
    ) -> tuple[dict, dict]:
        """
        Priority 2: Process power ports - find roots, merge same-named.

                Returns:
                    (power_port_roots, power_port_ids_by_root)
        """
        power_port_roots: dict[str, list[RootPoint]] = defaultdict(list)
        power_port_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)

        for precise_location, name, pp_obj in self._power_ports:
            root, pin_found = self._find_ordinary_item_root(
                precise_location,
                uf,
                pin_groups,
            )
            if root is None:
                root = self._prepared_net_item_root(
                    precise_location,
                    uf,
                    require_connection=True,
                )
            if root is not None and not pin_found:
                # Connected via wire
                power_port_roots[name].append(root)
                if root not in net_names:
                    net_names[root] = name
                    name_to_root[name] = root
                if pp_obj.unique_id:
                    power_port_ids_by_root[root].append(pp_obj.unique_id)
            elif root is not None and pin_found:
                # Direct pin connection (root == pp_loc)
                net_names[root] = name
                name_to_root[name] = root
                power_port_roots[name].append(root)
                if pp_obj.unique_id:
                    power_port_ids_by_root[root].append(pp_obj.unique_id)

        # Merge same-named power ports
        for _name, roots in power_port_roots.items():
            if len(roots) > 1:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])

        return power_port_roots, power_port_ids_by_root

    def _process_sheet_entries(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, str],
        name_to_root: dict[str, RootPoint],
    ) -> tuple[dict, dict]:
        """
        Priority 2.5: Process sheet entries for hierarchical bridging.

                Returns:
                    (sheet_entry_roots, sheet_entry_ids_by_root)
        """
        sheet_entry_roots: dict[str, list[RootPoint]] = defaultdict(list)
        sheet_entry_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)

        for precise_location, entry_name, sheet_sym_info, _entry in self._sheet_entries:
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
            entry_uid = getattr(sheet_sym_info.record, "unique_id", "") or ""
            if root is not None:
                sheet_entry_roots[entry_name].append(root)
                if (
                    self.options.allow_sheet_entries_to_name_nets
                    and root not in net_names
                ):
                    net_names[root] = entry_name
                    name_to_root[entry_name] = root
                if entry_uid:
                    sheet_entry_ids_by_root[root].append(f"{entry_uid}_{entry_name}")

        return sheet_entry_roots, sheet_entry_ids_by_root

    def _process_ports(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        net_names: dict[RootPoint, str],
        name_to_root: dict[str, RootPoint],
    ) -> tuple[dict, dict]:
        """
        Priority 3: Process ports - find roots, merge same-named.

                Returns:
                    (port_roots, port_ids_by_root)
        """
        port_roots: dict[str, list[RootPoint]] = defaultdict(list)
        port_ids_by_root: dict[RootPoint, list[str]] = defaultdict(list)

        for precise_location, name, port_obj in self._ports:
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
                port_roots[name].append(root)
                if self.options.allow_ports_to_name_nets and root not in net_names:
                    net_names[root] = name
                    name_to_root[name] = root
                if port_uid:
                    port_ids_by_root[root].append(port_uid)
            elif port_uid:
                # Dangling port - create virtual root for hierarchy bridge
                uf.find(precise_location)  # Register in union-find
                port_roots[name].append(precise_location)
                port_ids_by_root[precise_location].append(port_uid)
                if (
                    self.options.allow_ports_to_name_nets
                    and precise_location not in net_names
                ):
                    net_names[precise_location] = name
                    name_to_root[name] = precise_location

        # Merge same-named ports
        for _name, roots in port_roots.items():
            if len(roots) > 1:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])

        return port_roots, port_ids_by_root

    def _process_hidden_pins(
        self,
        uf: UnionFind,
        pin_groups: dict[RootPoint, list],
        floating_pin_roots: set[RootPoint],
        net_names: dict[RootPoint, str],
        name_to_root: dict[str, RootPoint],
    ) -> None:
        """
        Step 6.5: Handle hidden pins (implicit power connections).

                Hidden pins without wire connections are implicitly connected to their
                hidden_net_name or pin name (for power pins like GND/VCC).
                Mutates net_names and name_to_root in place.
        """
        hidden_pin_nets: dict[str, list[RootPoint]] = defaultdict(list)

        for root in floating_pin_roots:
            pins = pin_groups.get(root, [])
            for pin in pins:
                if hasattr(pin.pin, "is_hidden") and pin.pin.is_hidden:
                    hidden_net = None
                    hidden_net_name = getattr(pin.pin, "hidden_net_name", "") or ""
                    if hidden_net_name:
                        hidden_net = hidden_net_name
                    else:
                        pin_name_upper = (pin.name or "").upper()
                        if (
                            pin_name_upper in POWER_PIN_NAMES
                            or pin_name_upper in CHASSIS_GND_MAPPINGS
                        ):
                            if pin_name_upper in CHASSIS_GND_MAPPINGS:
                                hidden_net = "GND"
                            else:
                                hidden_net = pin.name

                    if hidden_net:
                        log.debug(
                            f"Hidden pin {pin.component_designator}.{pin.designator} "
                            f"({pin.name}) will connect to net '{hidden_net}'"
                        )
                        hidden_pin_nets[hidden_net].append(root)

        # Merge hidden pins with their named nets or create new named groups
        for name, roots in hidden_pin_nets.items():
            if name in name_to_root:
                existing_root = name_to_root[name]
                for r in roots:
                    uf.union(r, existing_root)
            else:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])
                if roots:
                    final_root = uf.find(roots[0])
                    net_names[final_root] = name
                    name_to_root[name] = final_root

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
        representative_pins: dict[tuple[str, str], _NetPinLike] = {}

        label_ids = sorted(
            dict.fromkeys(final_nl_ids.get(root, [])),
            key=self._net_label_object_sort_key,
        )
        graphical = NetGraphical(
            wires=list(final_wire_ids.get(root, [])),
            labels=label_ids,
            power_ports=list(final_pp_ids.get(root, [])),
            ports=list(final_port_ids.get(root, [])),
            sheet_entries=list(final_se_ids.get(root, [])),
        )

        for candidate in pins:
            component_key = (
                str(getattr(candidate, "component_unique_id", "") or "")
                or str(getattr(candidate, "unique_id", "") or "")
                or candidate.component_designator
            )
            key = (component_key, candidate.designator)
            current = representative_pins.get(key)
            if current is None or self._ad_common_pin_sort_key(
                candidate
            ) < self._ad_common_pin_sort_key(current):
                representative_pins[key] = candidate

        display_pin_counts = Counter(
            (pin.component_designator, pin.designator)
            for pin in representative_pins.values()
        )
        for pin in representative_pins.values():
            comp_des = pin.component_designator
            pin_des = pin.designator

            pin_name = pin.name or ""
            pin_type = _pin_electrical_to_pintype(pin.electrical)

            # Resolve part value for parameter evaluation side effects.
            comp = getattr(pin, "component", None)
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
                    _source_owner_part_id=int(
                        getattr(getattr(pin, "pin", None), "owner_part_id", None) or 1
                    ),
                )
            )

            pin_svg_id = pin.unique_id
            if pin_svg_id:
                graphical.pins.append(
                    GraphicalPinRef(designator=comp_des, pin=pin_des, svg_id=pin_svg_id)
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
                    element_id=pin_svg_id,
                    object_id=pin_svg_id,
                    name=pin_name,
                    designator=comp_des,
                    pin=pin_des,
                    pin_name=pin_name,
                    pin_type=pin_type,
                    connection_point=pin.connection_point,
                ),
            )

        self._append_endpoint_ids(
            endpoints,
            endpoint_seen,
            role="power_port",
            ids=final_pp_ids.get(root, []),
            name_for_id=self._power_like_name_for_id,
            role_for_id=self._power_like_role_for_id,
        )
        self._append_endpoint_ids(
            endpoints,
            endpoint_seen,
            role="port",
            ids=final_port_ids.get(root, []),
            name_for_id=self._port_name_for_id,
        )
        self._append_endpoint_ids(
            endpoints,
            endpoint_seen,
            role="sheet_entry",
            ids=final_se_ids.get(root, []),
            name_for_id=self._sheet_entry_name_for_id,
            object_id_for_id=self._sheet_entry_object_id_for_id,
        )

        all_label_names = final_label_names.get(root, [])
        aliases = sorted(set(n for n in all_label_names if n != name))

        return Net(
            name=name,
            terminals=terminals,
            graphical=graphical,
            auto_named=is_auto_named,
            aliases=aliases,
            endpoints=endpoints,
        )

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

    def _port_name_for_id(self, element_id: str) -> str:
        for _location, _name, obj in self._ports:
            if getattr(obj, "unique_id", "") == element_id:
                return str(getattr(obj, "name", "") or "")
        return ""

    def _sheet_entry_name_for_id(self, element_id: str) -> str:
        for _location, entry_name, sheet_sym_info, entry in self._sheet_entries:
            if f"{sheet_sym_info.unique_id}_{entry_name}" == element_id:
                return entry_name
            if getattr(entry, "unique_id", "") == element_id:
                return entry_name
        return ""

    def _sheet_entry_object_id_for_id(self, element_id: str) -> str:
        for _location, entry_name, sheet_sym_info, entry in self._sheet_entries:
            if f"{sheet_sym_info.unique_id}_{entry_name}" != element_id:
                continue
            return str(getattr(entry, "unique_id", "") or element_id)
        return element_id

    def _order_and_output_nets(
        self,
        uf: UnionFind,
        final_pin_groups: dict,
        final_net_names: dict,
        final_name_to_root: dict,
        final_wire_ids: dict,
        final_nl_ids: dict,
        final_pp_ids: dict,
        final_port_ids: dict,
        final_se_ids: dict,
        final_label_names: dict,
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
            return self._create_net_from_pins(
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

        nets: list[Net] = []
        processed_roots: set[RootPoint] = set()

        # Order 1: Named nets (net labels + power ports + floating labels)
        self._emit_named_nets(
            nets,
            processed_roots,
            create_net,
            final_name_to_root,
            final_pin_groups,
            floating_net_labels,
        )

        # Order 2.5: Ports (if naming enabled)
        if self.options.allow_ports_to_name_nets:
            _emit_port_named_nets(
                nets,
                processed_roots,
                create_net,
                self._port_names_ordered,
                final_name_to_root,
                final_pin_groups,
            )

        # Order 2.75: Sheet entry named nets
        if self.options.allow_sheet_entries_to_name_nets:
            _emit_named_roots(
                nets,
                processed_roots,
                create_net,
                sorted(
                    set(se_roots.keys()),
                    key=_altium_net_total_sort_key,
                    reverse=True,
                ),
                final_name_to_root,
                final_pin_groups,
                allow_empty_pins=True,
            )

        # Order 2.8: Unprocessed port/entry roots for hierarchy bridging
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
        )

        # Order 3: Auto-named nets
        _emit_auto_named_nets(
            nets,
            processed_roots,
            create_net,
            uf,
            final_pin_groups,
            floating_pin_roots,
        )

        return nets

    def _emit_named_nets(
        self,
        nets: list[Net],
        processed_roots: set[RootPoint],
        create_net: _CreateNetFn,
        final_name_to_root: dict[str, RootPoint],
        final_pin_groups: PinGroupsByRoot,
        floating_net_labels: dict[str, list[str]],
    ) -> None:
        """
        Emit named nets: net labels + power ports + floating labels (Order 1).
        """
        all_named_nets = (
            set(self._net_label_names_ordered)
            | set(self._power_port_names_ordered)
            | set(floating_net_labels)
        )
        named_nets_sorted = sorted(
            all_named_nets, key=_altium_net_total_sort_key, reverse=True
        )
        for name in named_nets_sorted:
            if name in final_name_to_root:
                root = final_name_to_root[name]
                if root not in processed_roots:
                    if root in final_pin_groups:
                        pins = final_pin_groups[root]
                        if pins:
                            nets.append(create_net(name, pins, root))
                            processed_roots.add(root)
                    else:
                        nets.append(create_net(name, [], root))
                        processed_roots.add(root)
            if name in floating_net_labels:
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
