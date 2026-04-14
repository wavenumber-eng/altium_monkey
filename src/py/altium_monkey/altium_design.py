"""
Composed Altium design model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .altium_api_markers import public_api

if TYPE_CHECKING:
    from .altium_netlist_options import NetlistOptions
    from .altium_netlist_model import (
        ComponentHierarchy,
        Net,
        Netlist,
        NetlistComponent,
        PnpEntry,
    )
    from .altium_pcbdoc import AltiumPcbDoc
    from .altium_prjpcb import AltiumPrjPcb
    from .altium_schdoc import AltiumSchDoc
    from .altium_schdoc_info import SchComponentInfo

log = logging.getLogger(__name__)

DESIGN_JSON_SCHEMA = "altium_monkey.design.a0"
DESIGN_JSON_GENERATOR = "altium_monkey"


@public_api
@dataclass
class AltiumDesign:
    """
    Composed Altium design model.

    Unified entry point for all design analysis operations.

        # From single schematic
        design = AltiumDesign.from_schdoc("schematic.SchDoc")

        # Get netlist
        netlist = design.to_netlist()

        # Export
        wirelist = design.to_wirelist()

        components = design.to_bom()
        variant_components = design.to_bom(variant="V1")
    """

    project: AltiumPrjPcb | None = None
    schdocs: list[AltiumSchDoc] = field(default_factory=list)
    _netlist: Netlist | None = None
    _options: NetlistOptions | None = None

    # Lazy-loaded PcbDoc for pick-and-place and PCB view operations.
    _pcbdoc: AltiumPcbDoc | None = field(default=None, repr=False)
    _pcbdoc_loaded: bool = field(default=False, repr=False)
    _pcbdoc_cache: dict[str, AltiumPcbDoc] = field(default_factory=dict, repr=False)

    @classmethod
    def from_prjpcb(cls, path: Path | str) -> AltiumDesign:
        """
        Load design from PrjPcb project file.

                Loads active SchDoc files from durable project metadata.

                Args:
                    path: Path to .PrjPcb file

                Returns:
                    AltiumDesign with project and all schematics loaded
        """
        from .altium_netlist_options import NetlistOptions
        from .altium_prjpcb import AltiumPrjPcb
        from .altium_schdoc import AltiumSchDoc

        path = Path(path)
        project = AltiumPrjPcb(path)

        schdocs = []
        for schdoc_path in project.get_reachable_schdoc_paths():
            schdocs.append(AltiumSchDoc(schdoc_path))

        options = NetlistOptions.from_prjpcb(project)

        # Sheet parameters are merged so later sheets can override earlier ones.
        sheet_params = {}
        for schdoc in schdocs:
            sheet_params.update(schdoc.get_parameter_dict())
        options.sheet_parameters = sheet_params

        return cls(project=project, schdocs=schdocs, _options=options)

    @classmethod
    def from_schdoc(cls, path: Path | str) -> AltiumDesign:
        """
        Load design from single SchDoc (no project).

                Args:
                    path: Path to .SchDoc file

                Returns:
                    AltiumDesign with single schematic
        """
        from .altium_schdoc import AltiumSchDoc

        path = Path(path)
        schdoc = AltiumSchDoc(path)

        return cls(project=None, schdocs=[schdoc])

    @classmethod
    def from_pcbdoc(cls, path: Path | str) -> AltiumDesign:
        """
        Load design from a single PcbDoc (no project context).
        """
        from .altium_pcbdoc import AltiumPcbDoc

        pcb_path = Path(path)
        pcbdoc = AltiumPcbDoc.from_file(pcb_path)
        cache_key = str(pcb_path.resolve())
        design = cls(project=None, schdocs=[])
        design._pcbdoc = pcbdoc
        design._pcbdoc_loaded = True
        design._pcbdoc_cache[cache_key] = pcbdoc
        return design

    def to_netlist(self) -> Netlist:
        """
        Generate unified netlist (cached).

                Returns:
                    Netlist object with all nets and components
        """
        if self._netlist is None:
            self._compile_cached_netlist()
        return self._netlist

    def _compile_cached_netlist(self) -> None:
        """
        Compile and cache the design netlist through the primary compiler.
        """
        from .altium_netlist_options import NetlistOptions
        from .altium_netlist_compilation import compile_netlist

        if self._netlist is not None:
            return

        options = self._options or NetlistOptions()
        self._netlist = compile_netlist(self.schdocs, self.project, options)

    def to_wirelist(self, strict: bool = True) -> str:
        """
        Generate WireList format string.

                Args:
                    strict: Apply strict text normalization

                Returns:
                    WireList format string
        """
        options = self._options
        allow_single_pin = options.allow_single_pin_nets if options else False
        return self.to_netlist().to_wirelist(
            strict=strict, allow_single_pin_nets=allow_single_pin
        )

    def to_json(self, include_indexes: bool = True) -> dict:
        """
        Serialize design state to JSON-compatible dict.

                The returned format is the package-owned design contract with
                project metadata, enriched components, optional PCB-backed
                pick-and-place placements, and compiled nets.

                Args:
                    include_indexes: If True, include pre-computed lookup indexes

                Returns:
                    JSON-compatible dict with design data
        """
        return self._design_json_from_netlist(self.to_netlist(), include_indexes)

    def _design_json_from_netlist(
        self,
        netlist: Netlist,
        include_indexes: bool,
    ) -> dict:
        """
        Build enriched design JSON around an already-generated netlist.
        """

        from .altium_netlist_options import NetlistOptions

        options = self._options or NetlistOptions()

        options_data = {
            "net_identifier_scope": options.net_identifier_scope.name,
            "allow_ports_to_name_nets": options.allow_ports_to_name_nets,
            "allow_sheet_entries_to_name_nets": options.allow_sheet_entries_to_name_nets,
            "allow_single_pin_nets": options.allow_single_pin_nets,
            "append_sheet_numbers_to_local_nets": options.append_sheet_numbers_to_local_nets,
            "power_port_names_take_priority": options.power_port_names_take_priority,
            "higher_level_names_take_priority": options.higher_level_names_take_priority,
            "auto_sheet_numbering": options.auto_sheet_numbering,
        }

        sheets_data = self._build_sheets_data(options)
        project_data = self._build_project_data()
        variants_data = self._build_variants_data()
        comp_data_map = self._build_component_data_map(netlist)
        components_data = []
        for comp in netlist.components:
            data = comp_data_map.get(comp.designator, {})
            components_data.append(
                self._enrich_component(
                    comp,
                    sheet=data.get("sheet", ""),
                    pin_count=data.get("pin_count", 0),
                    svg_id=data.get("svg_id", ""),
                )
            )

        result = {
            "schema": DESIGN_JSON_SCHEMA,
            "generator": DESIGN_JSON_GENERATOR,
            "project": project_data,
            "variants": variants_data,
            "options": options_data,
            "sheets": sheets_data,
            "components": components_data,
        }

        pnp_data = self._build_pnp_data()
        if pnp_data is not None:
            result["pnp"] = pnp_data

        result["nets"] = netlist.to_json()["nets"]

        if include_indexes:
            result["indexes"] = self._build_indexes(netlist, components_data)

        return result

    def _build_pnp_data(self) -> dict | None:
        """
        Build optional PCB-backed pick-and-place data for design JSON.
        """
        pcbdoc_paths = self.get_pcbdoc_paths()
        if not pcbdoc_paths:
            return None

        placements = self.to_pnp(units="mm")
        source_path = self._pcbdoc.filepath if self._pcbdoc else pcbdoc_paths[0]
        source_name = Path(source_path).name if source_path else pcbdoc_paths[0].name
        return {
            "units": "mm",
            "source_pcbdoc": source_name,
            "placements": [entry.to_json() for entry in placements],
        }

    def _build_component_data_map(self, netlist: Netlist) -> dict[str, dict]:
        """
        Build mapping of designator -> {sheet, pin_count, svg_id}.

                Single-pass iteration over all schematics keeps downstream lookups O(1).
        """
        result: dict[str, dict] = {}
        for schdoc in self.schdocs:
            sheet_name = schdoc.filepath.name if schdoc.filepath else ""
            for comp in schdoc.get_components():
                result[comp.designator] = {
                    "sheet": sheet_name,
                    "pin_count": len(comp.pins),
                    "svg_id": comp.unique_id,
                }

        # Fallback for multi-channel components not directly in any SchDoc:
        # count unique pins from netlist terminals
        for comp in netlist.components:
            if comp.designator not in result:
                pins = set()
                for net in netlist.nets:
                    for terminal in net.terminals:
                        if terminal.designator == comp.designator:
                            pins.add(terminal.pin)
                result[comp.designator] = {
                    "sheet": "",
                    "pin_count": len(pins),
                    "svg_id": "",
                }

        return result

    def _build_sheets_data(self, options: NetlistOptions) -> list[dict]:
        """
        Build sheet information for JSON output.
        """
        sheet_numbers = self._resolve_sheet_numbers(options)
        return [
            {
                "filename": schdoc.filepath.name if schdoc.filepath else f"sheet{idx}",
                "sheet_number": int(
                    sheet_numbers.get(
                        schdoc.filepath.name if schdoc.filepath else "", "0"
                    )
                    or 0
                ),
            }
            for idx, schdoc in enumerate(self.schdocs)
        ]

    def _build_project_data(self) -> dict:
        """
        Build project information for JSON output.
        """
        if not self.project:
            return {
                "name": None,
                "filename": None,
                "parameters": {},
            }

        return {
            "name": self.project.filepath.stem if self.project.filepath else None,
            "filename": str(self.project.filepath.name)
            if self.project.filepath
            else None,
            "parameters": dict(self.project.parameters),
        }

    def _build_variants_data(self) -> list[dict]:
        """
        Build variants information for JSON output.

                Returns:
                    List of variant dicts with 'name' and 'dnp' (list of designators).
                    Empty list if no project or no variants defined.
        """
        if not self.project or not self.project.variants:
            return []

        variants_list = []
        for variant_name, variant_data in self.project.variants.items():
            dnp_designators = []
            for variation in variant_data.get("variations", []):
                # Kind=1 means Not Fitted (DNP)
                if variation.get("Kind") == "1":
                    designator = variation.get("Designator", "")
                    if designator:
                        dnp_designators.append(designator)

            variants_list.append({"name": variant_name, "dnp": dnp_designators})

        return variants_list

    def _enrich_component(
        self,
        comp: NetlistComponent,
        sheet: str,
        pin_count: int,
        svg_id: str,
    ) -> dict:
        """
        Build enriched component data for JSON.

                Args:
                    comp: NetlistComponent from netlist
                    sheet: Source sheet filename
                    pin_count: Pre-computed pin count from _build_component_data_map()
                    svg_id: Pre-computed SVG unique_id from _build_component_data_map()

                Returns:
                    Enriched component dict with hierarchy, classification, and all parameters
        """
        from .altium_netlist_model import ComponentClassification

        # Parse channel info from designator (e.g., "R1_A" -> base="R1", channel="A")
        hierarchy = self._parse_hierarchy(comp.designator, sheet)

        # Build classification
        classification = ComponentClassification.from_component(
            comp.designator, pin_count
        )

        return {
            "designator": comp.designator,
            "svg_id": svg_id,
            "value": comp.value,
            "footprint": comp.footprint,
            "library_ref": comp.library_ref,
            "description": comp.description,
            "hierarchy": hierarchy.to_json(),
            "classification": classification.to_json(),
            "parameters": comp.parameters,
        }

    def _parse_hierarchy(self, designator: str, sheet: str) -> ComponentHierarchy:
        """
        Parse hierarchy info from designator.

                Handles multi-channel designators like "R1_A", "R1_B" or "R1A", "R1B".
        """
        from .altium_netlist_model import ComponentHierarchy

        # Pattern: base_designator + optional channel suffix
        # Examples: "R1_A" -> R1, A; "R1A" -> R1, A; "U1_CH2" -> U1, CH2
        match = re.match(r"^([A-Za-z]+\d+)(?:_?([A-Z])|\d*)$", designator)
        if match:
            base = match.group(1)
            channel = match.group(2)
            channel_index = ord(channel) - ord("A") + 1 if channel else None
        else:
            base = designator
            channel = None
            channel_index = None

        return ComponentHierarchy(
            base_designator=base,
            channel=channel,
            channel_index=channel_index,
            sheet=sheet,
        )

    def _build_indexes(
        self,
        netlist: Netlist,
        components_data: list[dict],
    ) -> dict:
        """
        Build pre-computed lookup indexes.
        """
        # svg_to_component: SVG ID -> designator
        svg_to_component = {}
        for comp_data in components_data:
            svg_id = comp_data.get("svg_id", "")
            if svg_id:
                svg_to_component[svg_id] = str(comp_data.get("designator", ""))

        # component_to_nets: designator -> list of net names
        component_to_nets: dict[str, list[str]] = {}
        for net in netlist.nets:
            for terminal in net.terminals:
                des = terminal.designator
                if des not in component_to_nets:
                    component_to_nets[des] = []
                if net.name not in component_to_nets[des]:
                    component_to_nets[des].append(net.name)

        # net_to_components: net name -> list of designators
        net_to_components: dict[str, list[str]] = {}
        for net in netlist.nets:
            designators = list(set(t.designator for t in net.terminals))
            net_to_components[net.name] = sorted(designators)

        return {
            "svg_to_component": svg_to_component,
            "component_to_nets": component_to_nets,
            "net_to_components": net_to_components,
        }

    def _resolve_sheet_numbers(self, options: NetlistOptions) -> dict[str, str]:
        """
        Resolve sheet numbers for each document.

                Args:
                    options: NetlistOptions with auto_sheet_numbering flag

                Returns:
                    Dict mapping filename -> sheet number string
        """
        sheet_numbers: dict[str, str] = {}

        if options.auto_sheet_numbering:
            # Auto-number: assign 1, 2, 3, ... in document order
            for idx, schdoc in enumerate(self.schdocs):
                filename = schdoc.filepath.name if schdoc.filepath else f"sheet{idx}"
                sheet_numbers[filename] = str(idx + 1)
        else:
            # Manual: read from document parameters (SheetNumber)
            for idx, schdoc in enumerate(self.schdocs):
                filename = schdoc.filepath.name if schdoc.filepath else f"sheet{idx}"
                sheet_num = self._get_document_parameter(schdoc, "SheetNumber")
                if sheet_num:
                    sheet_numbers[filename] = sheet_num
                else:
                    sheet_numbers[filename] = str(idx + 1)

        return sheet_numbers

    @staticmethod
    def _get_document_parameter(schdoc: AltiumSchDoc, name: str) -> str | None:
        """
        Get a document parameter value by name from a SchDoc.

                Document parameters are SchParameter records at the document level
                (children of the SHEET record). These include SheetNumber, SheetTotal,
                Title, Revision, etc.
        """
        for p in schdoc.parameters:
            if getattr(p, "name", "") == name:
                value = getattr(p, "text", "")
                if value and value != "*":
                    return value
        return None

    def refresh_netlist(self) -> Netlist:
        """
        Force regeneration of netlist (clear cache).
        """
        self._netlist = None
        return self.to_netlist()

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def components(self) -> list[SchComponentInfo]:
        """
        All components across all schematics.
        """
        result = []
        for schdoc in self.schdocs:
            result.extend(schdoc.get_components())
        return result

    def get_component(self, designator: str) -> SchComponentInfo | None:
        """
        Find component by designator across all schematics.
        """
        for schdoc in self.schdocs:
            comp = schdoc.get_component(designator)
            if comp:
                return comp
        return None

    def get_net(self, name: str) -> Net | None:
        """
        Get net by name from netlist.
        """
        return self.to_netlist().get_net(name)

    # ------------------------------------------------------------------
    # BOM Generation
    # ------------------------------------------------------------------

    def to_bom(self, variant: str | None = None) -> list[dict]:
        """
        Generate BOM from schematic components.

                This extracts ALL components from the schematic with their parameters.
                BOM data comes from schematic (not PCB) because:
                1. Schematic is the canonical source for component data
                2. Variants are defined at schematic level
                3. Component parameters are stored on schematic symbols

                Args:
                    variant: If specified, filter components by variant (DNP handling).
                            If None, returns all components.

                Returns:
                    List of component dicts, each containing:
                    - designator: Component designator (e.g., "R1")
                    - value: Component value
                    - footprint: PCB footprint
                    - library_ref: Library reference (symbol name)
                    - description: Component description
                    - parameters: Dict of all component parameters
                    - dnp: True if component is Do Not Populate in this variant
        """
        netlist = self.to_netlist()
        comp_data_map = self._build_component_data_map(netlist)

        # Get DNP list for this variant (if specified)
        dnp_set: set[str] = set()
        if variant and self.project:
            variant_data = self.project.variants.get(variant, {})
            for variation in variant_data.get("variations", []):
                # Kind=1 means Not Fitted (DNP)
                if variation.get("Kind") == "1":
                    designator = variation.get("Designator", "")
                    if designator:
                        dnp_set.add(designator)

        result = []
        for comp in netlist.components:
            # GRAPHICAL, NET_TIE_NO_BOM, and STANDARD_NO_BOM are excluded.
            if comp.exclude_from_bom:
                continue

            # Use parameters from the compiled netlist component rather than
            # reaching back into the source SchDoc.
            component_data = {
                "designator": comp.designator,
                "value": comp.value,
                "footprint": comp.footprint,
                "library_ref": comp.library_ref,
                "description": comp.description,
                "sheet": comp_data_map.get(comp.designator, {}).get("sheet", ""),
                "parameters": comp.parameters,
                "dnp": comp.designator in dnp_set,
            }
            result.append(component_data)

        return result

    def get_variants(self) -> list[str]:
        """
        Get list of available variant names.

                Returns:
                    List of variant names, empty if no project or no variants defined.
        """
        if not self.project:
            return []
        return list(self.project.variants.keys())

    def get_pcb_project_parameters(self) -> dict[str, str]:
        """
        Resolve project-level parameters for PCB text substitution.
        """
        if not self.project:
            return {}
        parameters = dict(self.project.parameters)
        current_variant = self.project.get_current_variant()
        if current_variant:
            parameters["VariantName"] = current_variant
        return parameters

    @staticmethod
    def _filter_pcbdoc_paths(
        candidates: list[Path],
        selector: Path | str,
        *,
        project_dir: Path | None = None,
    ) -> list[Path]:
        """
        Filter PcbDoc path candidates by selector.

        Selector matching supports:
        - absolute path
        - project-relative path
        - filename
        - filename stem
        """
        selector_raw = str(selector).strip()
        if not selector_raw:
            return candidates

        selector_path = Path(selector_raw)
        selector_lower = selector_raw.lower()
        selector_norm = selector_raw.replace("\\", "/").lower()
        resolved_selector: Path | None = None

        if selector_path.is_absolute():
            resolved_selector = selector_path.resolve()
        elif project_dir is not None:
            resolved_selector = (project_dir / selector_path).resolve()

        matches: list[Path] = []
        for candidate in candidates:
            if (
                candidate.name.lower() == selector_lower
                or candidate.stem.lower() == selector_lower
            ):
                matches.append(candidate)
                continue

            if project_dir is not None:
                try:
                    rel = (
                        candidate.resolve()
                        .relative_to(project_dir.resolve())
                        .as_posix()
                        .lower()
                    )
                except ValueError:
                    rel = ""
                if rel and rel == selector_norm:
                    matches.append(candidate)
                    continue

            if (
                resolved_selector is not None
                and candidate.resolve() == resolved_selector
            ):
                matches.append(candidate)

        if not matches:
            raise ValueError(
                f"PcbDoc '{selector_raw}' not found. Available boards: "
                f"{', '.join(path.name for path in candidates)}"
            )

        deduped = list(dict.fromkeys(matches))
        if len(deduped) > 1:
            raise ValueError(
                f"PcbDoc selector '{selector_raw}' matched multiple boards: "
                f"{', '.join(path.name for path in deduped)}"
            )
        return deduped

    def get_pcbdoc_paths(self, selector: Path | str | None = None) -> list[Path]:
        """
        Get PcbDoc paths for this design.

        For project-backed designs, returns all referenced PcbDocs in project order.
        For standalone-PcbDoc designs, returns the single loaded path.
        """
        if self.project:
            candidates = self.project.get_pcbdoc_paths()
            if not candidates:
                return []
            if selector is None:
                return candidates
            return self._filter_pcbdoc_paths(
                candidates,
                selector,
                project_dir=self.project.filepath.parent
                if self.project.filepath
                else None,
            )

        standalone_path = (
            Path(self._pcbdoc.filepath).resolve()
            if self._pcbdoc and self._pcbdoc.filepath
            else None
        )
        if standalone_path is None:
            return []
        candidates = [standalone_path]
        if selector is None:
            return candidates
        return self._filter_pcbdoc_paths(candidates, selector)

    def load_pcbdoc(self, selector: Path | str | None = None) -> AltiumPcbDoc:
        """
        Load and cache a PcbDoc by selector.
        """
        pcb_paths = self.get_pcbdoc_paths(selector=selector)
        if not pcb_paths:
            if self.project:
                raise ValueError(f"No PcbDoc found in project: {self.project.filepath}")
            raise ValueError("No standalone PcbDoc is loaded in this design")

        if selector is None and self.project and len(pcb_paths) > 1:
            log.warning(
                f"Multiple PcbDoc files found, using first: {pcb_paths[0].name}"
            )

        target_path = pcb_paths[0]
        cache_key = str(target_path.resolve())
        cached = self._pcbdoc_cache.get(cache_key)
        if cached is not None:
            self._pcbdoc = cached
            self._pcbdoc_loaded = True
            return cached

        from .altium_pcbdoc import AltiumPcbDoc

        log.info(f"Loading PcbDoc: {target_path.name}")
        parsed = AltiumPcbDoc.from_file(target_path)
        self._pcbdoc_cache[cache_key] = parsed
        self._pcbdoc = parsed
        self._pcbdoc_loaded = True
        return parsed

    # ------------------------------------------------------------------
    # Pick and Place
    # ------------------------------------------------------------------

    def to_pnp(
        self,
        variant: str | None = None,
        units: str = "mm",
        exclude_no_bom: bool = False,
    ) -> list[PnpEntry]:
        """
        Generate Pick-and-Place data from PCB.

                PcbDoc data is loaded on demand so BOM-only workflows do not pay the
                parse cost of the PCB file.

                Args:
                    variant: If specified, filter components by variant (DNP handling).
                            If None, returns all components.
                    units: Position units - "mm" (default) or "mils"
                    exclude_no_bom: If True, exclude STANDARD_NO_BOM/GRAPHICAL components.
                                   Default False because PnP may need mechanical placements.

                Returns:
                    List of PnpEntry objects with position/rotation data.

                Raises:
                    ValueError: If no PcbDoc found in project.
        """
        from .altium_component_kind import component_kind_includes_in_bom
        from .altium_common_enums import ComponentKind
        from .altium_netlist_model import PnpEntry

        # Lazy-load PcbDoc on first call
        pcbdoc = self._get_or_load_pcbdoc()

        # Get board origin for coordinate adjustment
        origin_x = pcbdoc.board.origin_x if pcbdoc.board else 0.0
        origin_y = pcbdoc.board.origin_y if pcbdoc.board else 0.0

        # Build BOM lookup for schematic data (parameters, description, etc.)
        # Use netlist components which have parameters populated
        netlist = self.to_netlist()
        bom_lookup = {comp.designator: comp for comp in netlist.components}

        # Get DNP list for this variant (if specified)
        dnp_set: set[str] = set()
        if variant and self.project:
            variant_data = self.project.variants.get(variant, {})
            for variation in variant_data.get("variations", []):
                if variation.get("Kind") == "1":  # Kind=1 means Not Fitted
                    designator = variation.get("Designator", "")
                    if designator:
                        dnp_set.add(designator)

        # Unit conversion factor (mils to mm: 1 mil = 0.0254 mm)
        if units == "mm":
            scale = 0.0254
        elif units == "mils":
            scale = 1.0
        else:
            raise ValueError(f"Unknown units: {units}. Use 'mm' or 'mils'.")

        result = []
        for pcb_comp in pcbdoc.components:
            designator = pcb_comp.designator

            # Skip DNP components if variant specified
            if designator in dnp_set:
                continue

            # Get schematic data for this component (if available)
            sch_comp = bom_lookup.get(designator)

            # Optionally filter based on ComponentKind from schematic
            # Default: include all (PnP may need mechanical placements)
            if (
                exclude_no_bom
                and sch_comp
                and not component_kind_includes_in_bom(
                    ComponentKind(sch_comp.component_kind)
                )
            ):
                continue

            # Calculate position relative to board origin (reuse AltiumPcbComponent methods)
            x_mils = pcb_comp.get_x_mils(origin_x)
            y_mils = pcb_comp.get_y_mils(origin_y)

            # Convert to requested units
            center_x = round(x_mils * scale, 4)
            center_y = round(y_mils * scale, 4)

            # Use AltiumPcbComponent's layer normalization method
            layer = pcb_comp.get_layer_normalized()

            # Use AltiumPcbComponent's rotation method
            rotation = pcb_comp.get_rotation_degrees()

            # Build PnP entry with type-safe dataclass
            pnp_entry = PnpEntry(
                designator=designator,
                comment=sch_comp.value if sch_comp else "",
                layer=layer,
                footprint=pcb_comp.footprint,
                center_x=center_x,
                center_y=center_y,
                rotation=rotation,
                description=sch_comp.description if sch_comp else pcb_comp.description,
                parameters=sch_comp.parameters if sch_comp else pcb_comp.parameters,
            )
            result.append(pnp_entry)

        return result

    def _get_or_load_pcbdoc(self) -> AltiumPcbDoc:
        """
        Load and cache the default PcbDoc for this design.

                Returns:
                    Parsed AltiumPcbDoc instance

                Raises:
                    ValueError: If no PcbDoc found in project or no project loaded
        """
        return self.load_pcbdoc(selector=None)

    @property
    def pcbdoc(self) -> AltiumPcbDoc | None:
        """
        Loaded PcbDoc, if one has already been parsed.
        """
        return self._pcbdoc
