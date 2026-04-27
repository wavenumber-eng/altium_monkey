"""
Single-sheet netlist compiler.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Callable, Protocol, TypeAlias

from .altium_netlist_options import NetlistOptions
from .altium_netlist_common import (
    CHASSIS_GND_MAPPINGS,
    POWER_PIN_NAMES,
    _altium_net_sort_key,
    _emit_auto_named_nets,
    _emit_bridge_roots,
    _emit_named_roots,
    _emit_port_named_nets,
    _pin_electrical_to_pintype,
    _points_connected,
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
    WireGeometryIndex,
    build_wire_graph as _build_wire_graph,
    group_pins_by_network as _group_pins_by_network,
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


RootPoint: TypeAlias = tuple[int, int]
PinGroupsByRoot: TypeAlias = dict[RootPoint, list["SchPinInfo"]]


class _CreateNetFn(Protocol):
    def __call__(
        self,
        name: str,
        pins: list["SchPinInfo"],
        root: RootPoint,
        is_auto_named: bool = False,
    ) -> Net: ...


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
            tolerance: Connection tolerance in internal units (default: 0 = exact)
            strict: If True, normalize special chars to ASCII (default: True)
            options: Netlist generation options (default: free document defaults)
        """
        self.schdoc = schdoc
        self.strict = strict
        self.options = options or NetlistOptions()

        # Get tolerance from sheet's hotspot grid if not explicitly provided
        if tolerance == 0 and schdoc.sheet:
            sheet_tolerance = getattr(schdoc.sheet, "hot_spot_grid_size", 0)
            self.tolerance = sheet_tolerance if sheet_tolerance else 0
        else:
            self.tolerance = tolerance

        # Internal storage
        self._components: dict[str, SchComponentInfo] = {}
        self._pins: list[SchPinInfo] = []
        self._wires: list = []  # Wire objects
        self._junctions: list[tuple[int, int]] = []
        self._net_labels: dict[tuple[int, int], str] = {}
        self._power_ports: dict[tuple[int, int], str] = {}
        self._ports: dict[tuple[int, int], str] = {}

        # Sheet entry tracking (for hierarchical connectivity)
        # Maps connection hotspot -> (entry_name, sheet_symbol_info)
        self._sheet_entries: dict[tuple[int, int], tuple[str, SchSheetSymbolInfo]] = {}

        # Graphical ID tracking (for SVG highlighting)
        self._net_label_objects: dict[tuple[int, int], SchNetLabelInfo] = {}
        self._power_port_objects: dict[
            tuple[int, int],
            SchPowerPortInfo | SchCrossSheetConnectorInfo,
        ] = {}
        self._port_objects: dict[tuple[int, int], SchPortInfo] = {}
        self._sheet_entry_objects: dict[tuple[int, int], object] = {}

        # Order tracking
        self._net_label_names_ordered: list[str] = []
        self._power_port_names_ordered: list[str] = []
        self._port_names_ordered: list[str] = []

        # Spatial index for wire geometry (built in _build_wire_connectivity)
        self._geo_index: WireGeometryIndex | None = None

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
                self._components[comp.designator] = comp
        if masked_count > 0:
            log.debug(f"Excluded {masked_count} components inside compile masks")

    def _extract_pins(self) -> None:
        """
        Extract pins using clean SchDoc API.

                Pins from components inside compile masks are excluded.
                Pins from alternate display modes are excluded (only primary mode pins included).
        """
        masked_count = 0
        display_mode_count = 0
        for pin in self.schdoc.get_all_pins():
            # Check if the parent component is inside a compile mask
            comp = pin.component
            if comp:
                x, y = comp.location
                if self._is_inside_compile_mask(x, y):
                    log.debug(
                        f"Pin {pin.designator}.{pin.name} excluded (parent inside compile mask)"
                    )
                    masked_count += 1
                    continue

            # Exclude pins from alternate display modes (only include primary mode)
            # Primary mode is owner_part_display_mode = None or 0
            # Alternate modes (1, 2, etc.) are for different symbol representations
            raw_pin = pin.pin
            display_mode = getattr(raw_pin, "owner_part_display_mode", None)
            if display_mode is not None and display_mode != 0:
                log.debug(
                    f"Pin {pin.component_designator}.{pin.designator} excluded "
                    f"(display_mode={display_mode})"
                )
                display_mode_count += 1
                continue

            self._pins.append(pin)
        if masked_count > 0:
            log.debug(
                f"Excluded {masked_count} pins from components inside compile masks"
            )
        if display_mode_count > 0:
            log.debug(
                f"Excluded {display_mode_count} pins from alternate display modes"
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
                self._junctions.append((loc.x, loc.y))

    def _extract_net_labels(self) -> None:
        """
        Extract net labels using clean SchDoc API.

                Net labels inside compile masks are excluded from netlist generation.
        """
        masked_count = 0
        for nl in self.schdoc.get_net_labels():
            loc = nl.connection_point
            text = nl.text
            if text:
                # Check if net label is inside a compile mask
                if self._is_inside_compile_mask(loc[0], loc[1]):
                    log.debug(f"NetLabel '{text}' excluded by compile mask at {loc}")
                    masked_count += 1
                    continue
                self._net_labels[loc] = text
                self._net_label_objects[loc] = nl  # Store full object for graphical_id
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
        power_like_objects = [
            *self.schdoc.get_power_ports(),
            *self.schdoc.get_cross_sheet_connectors(),
        ]
        for pp in power_like_objects:
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
                self._power_ports[loc] = text
                self._power_port_objects[loc] = pp  # Store full object for graphical_id
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
                # Ports can have multiple connection points (left and right edges)
                for loc in port.connection_points:
                    self._ports[loc] = name
                    self._port_objects[loc] = port  # Store full object for graphical_id
                if name not in self._port_names_ordered:
                    self._port_names_ordered.append(name)

    def _extract_sheet_entries(self) -> None:
        """
        Extract sheet entries from sheet symbols using clean SchDoc API.

                For each sheet symbol, computes entry connection hotspots based on
                symbol geometry (location, side, distance_from_top). These hotspots
                are registered so they can participate in union-find connectivity,
                enabling hierarchical net bridging in multi-sheet designs.

                Entry connection point formula (validated against native behavior):
                    Left side (0):  hotspot = (sym.location.x, sym.location.y - dist)
                    Right side (1): hotspot = (sym.location.x + sym.x_size, sym.location.y - dist)
                where dist = entry.distance_from_top * 10 (in CoordPoint/10-mil units).
        """
        for sheet_sym_info in self.schdoc.get_sheet_symbols():
            ss = sheet_sym_info.record
            sym_x = ss.location.x
            sym_y = ss.location.y

            for entry in sheet_sym_info.entries:
                entry_name = entry.display_name or ""
                if not entry_name:
                    continue

                # Compute connection hotspot based on entry side
                dist = (
                    entry.distance_from_top * 10
                )  # Convert to CoordPoint (10-mil) units
                side = entry.side

                if side == 0:  # Left
                    hotspot = (sym_x, sym_y - dist)
                elif side == 1:  # Right
                    hotspot = (sym_x + ss.x_size, sym_y - dist)
                elif side == 2:  # Top
                    hotspot = (sym_x + dist, sym_y)
                elif side == 3:  # Bottom
                    hotspot = (sym_x + dist, sym_y - ss.y_size)
                else:
                    log.warning(f"Unknown sheet entry side {side} for '{entry_name}'")
                    continue

                self._sheet_entries[hotspot] = (entry_name, sheet_sym_info)
                self._sheet_entry_objects[hotspot] = entry
                log.debug(
                    f"Sheet entry '{entry_name}' at hotspot {hotspot} "
                    f"(side={side}, dist={entry.distance_from_top})"
                )

    # =========================================================================
    # Building Methods
    # =========================================================================

    def _build_components(self) -> list[NetlistComponent]:
        """
        Build NetlistComponent list from extracted components.
        """
        result = []
        for comp in self._components.values():
            # Wire List uses Comment field with parameter evaluation.
            # If expression resolves to empty, fall back to raw Comment text
            # (Altium shows "=Value" literally when the Value param is empty).
            # Resolve the display value the same way downstream views will.
            value = _resolve_component_display_value(
                comp,
                project_params=self.options.project_parameters,
                sheet_params=self.options.sheet_parameters,
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

    def _get_wire_points(self, wire: object) -> list[RootPoint]:
        """
        Extract points from wire object (cached via spatial index).
        """
        if self._geo_index is not None:
            return self._geo_index.get_points(wire)
        points = getattr(wire, "points", [])
        return [(p.x, p.y) for p in points]

    def _require_geo_index(self) -> WireGeometryIndex:
        """
        Return the built wire geometry index or raise if connectivity is not ready.
        """
        if self._geo_index is None:
            raise RuntimeError("Wire geometry index has not been built yet")
        return self._geo_index

    def _build_wire_connectivity(
        self, uf: UnionFind
    ) -> dict[tuple[int, int], list[str]]:
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
            tolerance=self.tolerance,
            cell_size=100,
        )
        self._geo_index = wire_index
        uf._parent = dict(shared_uf._parent)
        return wire_ids_by_root

    def _group_pins_by_network(
        self, uf: UnionFind
    ) -> tuple[dict[tuple[int, int], list], set[tuple[int, int]]]:
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
            tolerance=self.tolerance,
            pins=self._pins,
        )

    def _find_wire_point_for_location(
        self, loc: tuple[int, int]
    ) -> tuple[int, int] | None:
        """
        Find wire point that the location connects to.
        """
        return self._require_geo_index().find_wire_connection(loc, self.tolerance)

    def _find_wire_point_for_netlabel(
        self,
        nl_obj: SchNetLabelInfo,
    ) -> RootPoint | None:
        """
        Find wire point for a net label, using strict fractional coordinate matching.
        """
        return self._require_geo_index().find_wire_connection_for_netlabel(
            nl_obj, self.tolerance
        )

    def _find_connectivity_root(
        self,
        location: tuple[int, int],
        uf: UnionFind,
        pin_groups: dict[tuple[int, int], list],
    ) -> tuple[tuple[int, int] | None, bool]:
        """
        Find union-find root for a location: wire point first, then direct pin fallback.

                Returns:
                    (root, pin_found): root is the UF root if connected (wire or pin),
                    None if floating. pin_found=True if a direct pin connection was used.
        """
        wp = self._find_wire_point_for_location(location)
        if wp is not None:
            return uf.find(wp), False
        for pin in self._pins:
            if _points_connected(location, pin.connection_point, self.tolerance):
                uf.add_root(location)
                pin_groups[location].append(pin)
                return location, True
        return None, False

    @staticmethod
    def _remap_list_maps(
        uf: UnionFind, **maps: dict[tuple[int, int], list]
    ) -> dict[str, dict[tuple[int, int], list]]:
        """
        Remap root-keyed list maps to final union-find roots.

                Each input map has root-to-list values. Returns a dict of remapped maps
                where all roots are resolved to their final UF representatives.
        """
        result = {}
        for name, old_map in maps.items():
            new_map: dict[tuple[int, int], list] = defaultdict(list)
            for old_root, items in old_map.items():
                new_map[uf.find(old_root)].extend(items)
            result[name] = new_map
        return result

    def _assign_names_and_build_nets(
        self,
        uf: UnionFind,
        wire_ids_by_root: dict[tuple[int, int], list[str]],
        pin_groups: dict[tuple[int, int], list],
        floating_pin_roots: set[tuple[int, int]],
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
        net_names: dict[tuple[int, int], str] = {}
        name_to_root: dict[str, tuple[int, int]] = {}

        # Priority 1: NetLabels
        nl_roots, label_names_by_root, floating_labels, nl_ids = (
            self._process_net_labels(uf, pin_groups, net_names, name_to_root)
        )

        # Priority 2: PowerPorts
        pp_roots, pp_ids = self._process_power_ports(
            uf, pin_groups, net_names, name_to_root
        )

        # Priority 2.5: Sheet entries
        se_roots, se_ids = self._process_sheet_entries(uf, net_names, name_to_root)

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
        final_net_names: dict[tuple[int, int], str] = {}
        for old_root, name in net_names.items():
            current_root = uf.find(old_root)
            if current_root not in final_net_names:
                final_net_names[current_root] = name

        final_name_to_root: dict[str, tuple[int, int]] = {}
        for root, name in final_net_names.items():
            if name not in final_name_to_root:
                final_name_to_root[name] = root

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
        pin_groups: dict[tuple[int, int], list],
        net_names: dict[tuple[int, int], str],
        name_to_root: dict[str, tuple[int, int]],
    ) -> tuple[dict, dict, set, dict]:
        """
        Priority 1: Process net labels - find roots, merge same-named, assign names.

                Returns:
                    (net_label_roots, root_to_label_names, floating_net_labels, net_label_ids_by_root)
        """
        net_label_roots: dict[str, list[tuple[int, int]]] = defaultdict(list)
        root_to_label_names: dict[tuple[int, int], list[str]] = defaultdict(list)
        floating_net_labels: set[str] = set()
        net_label_ids_by_root: dict[tuple[int, int], list[str]] = defaultdict(list)

        for nl_loc, name in self._net_labels.items():
            nl_obj = self._net_label_objects.get(nl_loc)
            # Use strict fractional coordinate matching for net labels
            wp = (
                self._find_wire_point_for_netlabel(nl_obj)
                if nl_obj
                else self._find_wire_point_for_location(nl_loc)
            )
            if wp is not None:
                root = uf.find(wp)
                net_label_roots[name].append(root)
                root_to_label_names[root].append(name)
                if nl_obj and nl_obj.unique_id:
                    net_label_ids_by_root[root].append(nl_obj.unique_id)
            else:
                # No wire connection - check direct pin connection
                found_pin = False
                for pin in self._pins:
                    if _points_connected(nl_loc, pin.connection_point, self.tolerance):
                        uf.add_root(nl_loc)
                        pin_groups[nl_loc].append(pin)
                        net_label_roots[name].append(nl_loc)
                        root_to_label_names[nl_loc].append(name)
                        if nl_obj and nl_obj.unique_id:
                            net_label_ids_by_root[nl_loc].append(nl_obj.unique_id)
                        found_pin = True
                        break
                if not found_pin:
                    floating_net_labels.add(name)

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
        pin_groups: dict[tuple[int, int], list],
        net_names: dict[tuple[int, int], str],
        name_to_root: dict[str, tuple[int, int]],
    ) -> tuple[dict, dict]:
        """
        Priority 2: Process power ports - find roots, merge same-named.

                Returns:
                    (power_port_roots, power_port_ids_by_root)
        """
        power_port_roots: dict[str, list[tuple[int, int]]] = defaultdict(list)
        power_port_ids_by_root: dict[tuple[int, int], list[str]] = defaultdict(list)

        for pp_loc, name in self._power_ports.items():
            pp_obj = self._power_port_objects.get(pp_loc)
            root, pin_found = self._find_connectivity_root(pp_loc, uf, pin_groups)
            if root is not None and not pin_found:
                # Connected via wire
                power_port_roots[name].append(root)
                if root not in net_names:
                    net_names[root] = name
                    name_to_root[name] = root
                if pp_obj and pp_obj.unique_id:
                    power_port_ids_by_root[root].append(pp_obj.unique_id)
            elif root is not None and pin_found:
                # Direct pin connection (root == pp_loc)
                net_names[root] = name
                name_to_root[name] = root
                power_port_roots[name].append(root)
                if pp_obj and pp_obj.unique_id:
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
        net_names: dict[tuple[int, int], str],
        name_to_root: dict[str, tuple[int, int]],
    ) -> tuple[dict, dict]:
        """
        Priority 2.5: Process sheet entries for hierarchical bridging.

                Returns:
                    (sheet_entry_roots, sheet_entry_ids_by_root)
        """
        sheet_entry_roots: dict[str, list[tuple[int, int]]] = defaultdict(list)
        sheet_entry_ids_by_root: dict[tuple[int, int], list[str]] = defaultdict(list)

        for se_loc, (entry_name, sheet_sym_info) in self._sheet_entries.items():
            wp = self._find_wire_point_for_location(se_loc)
            entry_uid = getattr(sheet_sym_info.record, "unique_id", "") or ""
            if wp is not None:
                root = uf.find(wp)
                sheet_entry_roots[entry_name].append(root)
                if (
                    self.options.allow_sheet_entries_to_name_nets
                    and root not in net_names
                ):
                    net_names[root] = entry_name
                    name_to_root[entry_name] = root
                if entry_uid:
                    sheet_entry_ids_by_root[root].append(f"{entry_uid}_{entry_name}")
            elif entry_uid:
                # Dangling sheet entry - create virtual root for hierarchy bridge
                virtual_root = se_loc
                uf.find(virtual_root)  # Register in union-find
                sheet_entry_roots[entry_name].append(virtual_root)
                sheet_entry_ids_by_root[virtual_root].append(
                    f"{entry_uid}_{entry_name}"
                )
                if (
                    self.options.allow_sheet_entries_to_name_nets
                    and virtual_root not in net_names
                ):
                    net_names[virtual_root] = entry_name
                    name_to_root[entry_name] = virtual_root

        return sheet_entry_roots, sheet_entry_ids_by_root

    def _process_ports(
        self,
        uf: UnionFind,
        pin_groups: dict[tuple[int, int], list],
        net_names: dict[tuple[int, int], str],
        name_to_root: dict[str, tuple[int, int]],
    ) -> tuple[dict, dict]:
        """
        Priority 3: Process ports - find roots, merge same-named.

                Returns:
                    (port_roots, port_ids_by_root)
        """
        port_roots: dict[str, list[tuple[int, int]]] = defaultdict(list)
        port_ids_by_root: dict[tuple[int, int], list[str]] = defaultdict(list)

        for port_loc, name in self._ports.items():
            port_obj = self._port_objects.get(port_loc)
            port_uid = port_obj.unique_id if port_obj else None
            root, pin_found = self._find_connectivity_root(port_loc, uf, pin_groups)

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
                uf.find(port_loc)  # Register in union-find
                port_roots[name].append(port_loc)
                port_ids_by_root[port_loc].append(port_uid)
                if self.options.allow_ports_to_name_nets and port_loc not in net_names:
                    net_names[port_loc] = name
                    name_to_root[name] = port_loc

        # Merge same-named ports
        for _name, roots in port_roots.items():
            if len(roots) > 1:
                for i in range(len(roots) - 1):
                    uf.union(roots[i], roots[i + 1])

        return port_roots, port_ids_by_root

    def _process_hidden_pins(
        self,
        uf: UnionFind,
        pin_groups: dict[tuple[int, int], list],
        floating_pin_roots: set[tuple[int, int]],
        net_names: dict[tuple[int, int], str],
        name_to_root: dict[str, tuple[int, int]],
    ) -> None:
        """
        Step 6.5: Handle hidden pins (implicit power connections).

                Hidden pins without wire connections are implicitly connected to their
                hidden_net_name or pin name (for power pins like GND/VCC).
                Mutates net_names and name_to_root in place.
        """
        hidden_pin_nets: dict[str, list[tuple[int, int]]] = defaultdict(list)

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
        pins: list,
        root: tuple[int, int],
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
        seen_pins: set[tuple[str, str]] = set()

        graphical = NetGraphical(
            wires=list(final_wire_ids.get(root, [])),
            labels=list(final_nl_ids.get(root, [])),
            power_ports=list(final_pp_ids.get(root, [])),
            ports=list(final_port_ids.get(root, [])),
            sheet_entries=list(final_se_ids.get(root, [])),
        )

        for pin in pins:
            comp_des = pin.component_designator
            pin_des = pin.designator

            key = (comp_des, pin_des)
            if key in seen_pins:
                continue
            seen_pins.add(key)

            pin_name = pin.name or ""
            pin_type = _pin_electrical_to_pintype(pin.electrical)

            # Resolve part value for parameter evaluation side effects.
            comp = self._components.get(comp_des)
            if comp:
                _resolve_component_display_value(
                    comp,
                    project_params=self.options.project_parameters,
                    sheet_params=self.options.sheet_parameters,
                )

            terminals.append(
                Terminal(
                    designator=comp_des,
                    pin=pin_des,
                    pin_name=pin_name,
                    pin_type=pin_type,
                )
            )

            pin_svg_id = pin.unique_id
            if pin_svg_id:
                graphical.pins.append(
                    GraphicalPinRef(designator=comp_des, pin=pin_des, svg_id=pin_svg_id)
                )
            self._append_net_endpoint(
                endpoints,
                endpoint_seen,
                NetEndpoint(
                    endpoint_id=f"pin:{comp_des}:{pin_des}",
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
        for obj in self._power_port_objects.values():
            if getattr(obj, "unique_id", "") == element_id:
                return str(getattr(obj, "text", "") or "")
        return ""

    def _power_like_role_for_id(self, element_id: str) -> str:
        for obj in self._power_port_objects.values():
            if getattr(obj, "unique_id", "") != element_id:
                continue
            if obj.__class__.__name__ == "SchCrossSheetConnectorInfo":
                return "offsheet_connector"
            return "power_port"
        return "power_port"

    def _port_name_for_id(self, element_id: str) -> str:
        for obj in self._port_objects.values():
            if getattr(obj, "unique_id", "") == element_id:
                return str(getattr(obj, "name", "") or "")
        return ""

    def _sheet_entry_name_for_id(self, element_id: str) -> str:
        for loc, (entry_name, sheet_sym_info) in self._sheet_entries.items():
            if f"{sheet_sym_info.unique_id}_{entry_name}" == element_id:
                return entry_name
            entry = self._sheet_entry_objects.get(loc)
            if getattr(entry, "unique_id", "") == element_id:
                return entry_name
        return ""

    def _sheet_entry_object_id_for_id(self, element_id: str) -> str:
        for loc, (entry_name, sheet_sym_info) in self._sheet_entries.items():
            if f"{sheet_sym_info.unique_id}_{entry_name}" != element_id:
                continue
            entry = self._sheet_entry_objects.get(loc)
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
        floating_net_labels: set[str],
        floating_pin_roots: set[tuple[int, int]],
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
            pins: list[SchPinInfo],
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
        processed_roots: set[tuple[int, int]] = set()

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
                sorted(set(se_roots.keys()), key=_altium_net_sort_key, reverse=True),
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
        floating_net_labels: set[str],
    ) -> None:
        """
        Emit named nets: net labels + power ports + floating labels (Order 1).
        """
        all_named_nets = (
            set(self._net_label_names_ordered)
            | set(self._power_port_names_ordered)
            | floating_net_labels
        )
        named_nets_sorted = sorted(
            all_named_nets, key=_altium_net_sort_key, reverse=True
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
            elif name in floating_net_labels:
                nets.append(
                    Net(
                        name=name,
                        terminals=[],
                        auto_named=False,
                    )
                )


__all__ = ["AltiumNetlistSingleSheetCompiler"]
