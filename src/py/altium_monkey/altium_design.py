"""
Composed Altium design model.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from ._logical_source_identity import (
    _combine_project_document_identity,
    _logical_source_basename,
    _logical_source_identity_key,
    _normalize_logical_source_identity,
)
from .altium_api_markers import public_api
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_netlist_common import (
    _evaluate_altium_expression,
    _unique_nonempty_strings,
)
from .altium_pnp_position import (
    PNP_POSITION_MODE_ALTIUM_PICK_PLACE,
    PnpPositionMode,
    normalize_pnp_position_mode,
)

if TYPE_CHECKING:
    from ._compiler_source import _CompilerDocumentSource
    from ._altium_sch_component_project_state import _ComponentProjectRenderState
    from .altium_compiled_design_model import (
        AltiumCompileDiagnostic,
        AltiumCompiledComponent,
        AltiumCompiledDesign,
        AltiumCompiledNet,
        AltiumCompiledPhysicalDocument,
    )
    from .altium_compiled_schematic_graph import AltiumCompiledSchematicGraph
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
    from .altium_record_sch__component import AltiumSchComponent
    from .altium_sch_geometry_oracle import SchGeometryDocument
    from .altium_sch_svg_renderer import SchSvgRenderOptions
    from .altium_schdoc import AltiumSchDoc
    from .altium_schdoc_info import SchComponentInfo
    from .altium_schematic_bom import SchematicBomPayload

log = logging.getLogger(__name__)

DESIGN_JSON_SCHEMA = "altium_monkey.design.b0"
DESIGN_JSON_GENERATOR = "altium_monkey"
SCHEMATIC_HIERARCHY_SCHEMA = "altium_monkey.schematic_hierarchy.a1"
_CANONICAL_DECIMAL_RE = re.compile(r"0|[1-9][0-9]*")


@public_api
class AltiumProjectLoadMode(StrEnum):
    """Workload selected when loading an Altium project."""

    FULL = "full"
    METADATA_ONLY = "metadata_only"


@public_api
class AltiumProjectCapabilityError(RuntimeError):
    """The selected project load mode does not provide a requested capability."""


@dataclass(frozen=True)
class _DesignLoadDiagnostic:
    severity: str
    code: str
    source_identity: str


def _design_loader_resolution_diagnostics(
    project_identity: str,
    project_document_identities: Sequence[str],
    supplied_identities: Sequence[str],
) -> tuple[_DesignLoadDiagnostic, ...]:
    """Freeze deterministic missing/extra source diagnostics for native loaders."""
    supplied_by_key: dict[str, str] = {}
    for identity in supplied_identities:
        normalized = _normalize_design_source_identity(identity)
        key = _design_source_identity_key(normalized)
        if key in supplied_by_key:
            raise ValueError(f"duplicate normalized source identity {normalized!r}")
        supplied_by_key[key] = normalized

    diagnostics: list[_DesignLoadDiagnostic] = []
    referenced: set[str] = set()
    normalized_project = _normalize_design_source_identity(project_identity)
    for reference in project_document_identities:
        normalized = _combine_project_document_identity(normalized_project, reference)
        key = _design_source_identity_key(normalized)
        if key in referenced:
            continue
        referenced.add(key)
        if key not in supplied_by_key:
            diagnostics.append(
                _DesignLoadDiagnostic(
                    severity="error",
                    code="unresolved_project_schematic",
                    source_identity=normalized,
                )
            )
    for identity in supplied_identities:
        normalized = _normalize_design_source_identity(identity)
        if _design_source_identity_key(normalized) not in referenced:
            diagnostics.append(
                _DesignLoadDiagnostic(
                    severity="warning",
                    code="unreferenced_schematic_source",
                    source_identity=normalized,
                )
            )
    return tuple(diagnostics)


def _normalize_design_source_identity(identity: str) -> str:
    return _normalize_logical_source_identity(identity)


def _design_source_identity_key(identity: str) -> str:
    """Return the portable scalar-lowercase, no-normalization identity key."""
    return _logical_source_identity_key(identity)


def _coerce_variant_parameter_overrides(
    variant_data: dict[str, object],
) -> dict[str, dict[str, str]]:
    """
    Return variant parameter overrides grouped by source designator.
    """
    overrides: dict[str, dict[str, str]] = {}
    designator_names: dict[str, str] = {}
    parameter_names: dict[str, dict[str, str]] = {}

    def add_override(designator: str, parameter: str, value: str) -> None:
        designator_key = dotnet_ordinal_ignore_case_key(designator)
        selected_designator = designator_names.setdefault(designator_key, designator)
        selected_parameters = overrides.setdefault(selected_designator, {})
        selected_names = parameter_names.setdefault(designator_key, {})
        parameter_key = dotnet_ordinal_ignore_case_key(parameter)
        selected_parameter = selected_names.setdefault(parameter_key, parameter)
        selected_parameters[selected_parameter] = value

    for designator, parameter, value in _variant_override_rows(variant_data):
        add_override(designator, parameter, value)

    return overrides


def _variant_override_rows(
    variant_data: dict[str, object],
) -> list[tuple[str, str, str]]:
    return [
        *_explicit_variant_override_rows(variant_data.get("parameter_overrides")),
        *_indexed_variant_override_rows(variant_data.get("param_variations")),
    ]


def _explicit_variant_override_rows(
    raw_overrides: object,
) -> list[tuple[str, str, str]]:
    if not isinstance(raw_overrides, dict):
        return []
    rows: list[tuple[str, str, str]] = []
    for designator, parameters in raw_overrides.items():
        designator_text = str(designator or "").strip()
        if not designator_text or not isinstance(parameters, dict):
            continue
        rows.extend(
            (designator_text, str(name), str(value))
            for name, value in parameters.items()
            if str(name or "").strip()
        )
    return rows


def _indexed_variant_override_rows(
    raw_rows: object,
) -> list[tuple[str, str, str]]:
    if not isinstance(raw_rows, list):
        return []
    rows: list[tuple[str, str, str]] = []
    for row in raw_rows:
        parsed = _indexed_variant_override_row(row)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _indexed_variant_override_row(row: object) -> tuple[str, str, str] | None:
    if not isinstance(row, dict):
        return None
    designator = str(
        _variant_field(row, "ParamDesignator")
        or _variant_field(row, "Designator")
        or ""
    ).strip()
    parameter = str(_variant_field(row, "ParameterName") or "").strip()
    if not designator or not parameter:
        return None
    raw_value = _variant_field(row, "VariantValue")
    return designator, parameter, str("" if raw_value is None else raw_value)


def _variant_dnp_designators(variant_data: dict[str, object]) -> set[str]:
    """
    Return designators marked not fitted in one project variant.
    """
    dnp_designators: set[str] = set()
    raw_variations = variant_data.get("variations", [])
    variations = raw_variations if isinstance(raw_variations, list) else []
    for variation in variations:
        if not isinstance(variation, dict):
            continue
        if _variant_field(variation, "Kind") != "1":
            continue
        designator = str(_variant_field(variation, "Designator") or "").strip()
        if designator:
            dnp_designators.add(designator)
    return dnp_designators


def _variant_field(values: Mapping[str, object], name: str) -> object | None:
    key = dotnet_ordinal_ignore_case_key(name)
    for candidate, value in reversed(tuple(values.items())):
        if dotnet_ordinal_ignore_case_key(str(candidate)) == key:
            return value
    return None


def _case_insensitive_mapping_value[V](
    values: Mapping[str, V],
    name: str,
) -> V | None:
    key = dotnet_ordinal_ignore_case_key(name)
    for candidate, value in values.items():
        if dotnet_ordinal_ignore_case_key(candidate) == key:
            return value
    return None


def _variant_show_alternate_symbols(variant_data: dict[str, object] | None) -> bool:
    """Return the current project variant's managed overwrite-symbol flag."""
    if variant_data is None:
        return True
    value = _variant_field(variant_data, "overwrite_schematic_symbol")
    if value is None:
        value = _variant_field(variant_data, "OverwriteSchematicSymbol")
    if value is None:
        return True
    if type(value) is bool:
        return value
    return dotnet_ordinal_ignore_case_key(str(value).strip()) in {
        "1",
        "t",
        "true",
        "y",
        "yes",
    }


class _PhysicalVariantIndex:
    """Index one variant snapshot for repeated physical-component lookups."""

    def __init__(self, variant_data: dict[str, object] | None) -> None:
        self._by_path: dict[tuple[str, str], Mapping[str, object]] = {}
        self._by_designator: dict[tuple[str, str], Mapping[str, object]] = {}
        raw_rows = (variant_data or {}).get("variations", [])
        if not isinstance(raw_rows, list):
            return
        for row in raw_rows:
            self._index_path(row)
        # Dictionary replacement keeps the first path's slot but its latest
        # payload. Fallback must be built AFTER replacement so stale or renamed
        # designators cannot win, and the first surviving path keeps priority.
        for (logical_key, _), row in self._by_path.items():
            designator = str(_variant_field(row, "Designator") or "")
            key = (logical_key, dotnet_ordinal_ignore_case_key(designator))
            self._by_designator.setdefault(key, row)

    def _index_path(self, row: object) -> None:
        if not isinstance(row, dict):
            return
        unique_id = str(_variant_field(row, "UniqueId") or "")
        separator = unique_id.rfind("\\")
        if separator < 0:
            return
        logical_key = dotnet_ordinal_ignore_case_key(unique_id[separator + 1 :])
        path_key = dotnet_ordinal_ignore_case_key(unique_id)
        self._by_path[(logical_key, path_key)] = row

    def select(
        self,
        *,
        logical_unique_id: str,
        unique_id_path: str,
        physical_designator: str,
    ) -> Mapping[str, object] | None:
        logical_key = dotnet_ordinal_ignore_case_key(logical_unique_id)
        path_key = dotnet_ordinal_ignore_case_key(unique_id_path)
        exact = self._by_path.get((logical_key, path_key))
        if exact is not None:
            return exact
        designator_key = dotnet_ordinal_ignore_case_key(physical_designator)
        return self._by_designator.get((logical_key, designator_key))


def _selected_physical_variation(
    variant_data: dict[str, object],
    *,
    logical_unique_id: str,
    unique_id_path: str,
    physical_designator: str,
) -> Mapping[str, object] | None:
    """Select the managed physical variation for one compiled component."""
    unique_id_order, latest_by_unique_id = _indexed_physical_variations(
        variant_data,
        logical_unique_id,
    )
    return _select_indexed_physical_variation(
        unique_id_order,
        latest_by_unique_id,
        unique_id_path=unique_id_path,
        physical_designator=physical_designator,
    )


def _indexed_physical_variations(
    variant_data: dict[str, object],
    logical_unique_id: str,
) -> tuple[list[str], dict[str, Mapping[str, object]]]:
    logical_key = dotnet_ordinal_ignore_case_key(logical_unique_id)
    latest_by_unique_id: dict[str, Mapping[str, object]] = {}
    unique_id_order: list[str] = []
    raw_variations = variant_data.get("variations", [])
    variations = raw_variations if isinstance(raw_variations, list) else []
    for row in variations:
        if not isinstance(row, dict):
            continue
        unique_id = str(_variant_field(row, "UniqueId") or "")
        if not _variation_targets_logical_id(unique_id, logical_key):
            continue
        unique_key = dotnet_ordinal_ignore_case_key(unique_id)
        if unique_key not in latest_by_unique_id:
            unique_id_order.append(unique_key)
        latest_by_unique_id[unique_key] = row
    return unique_id_order, latest_by_unique_id


def _variation_targets_logical_id(unique_id: str, logical_key: str) -> bool:
    separator = unique_id.rfind("\\")
    return (
        separator >= 0
        and dotnet_ordinal_ignore_case_key(unique_id[separator + 1 :]) == logical_key
    )


def _select_indexed_physical_variation(
    unique_id_order: list[str],
    latest_by_unique_id: Mapping[str, Mapping[str, object]],
    *,
    unique_id_path: str,
    physical_designator: str,
) -> Mapping[str, object] | None:
    path_key = dotnet_ordinal_ignore_case_key(unique_id_path)
    designator_key = dotnet_ordinal_ignore_case_key(physical_designator)
    fallback: Mapping[str, object] | None = None
    for unique_key in unique_id_order:
        row = latest_by_unique_id[unique_key]
        row_unique_id = str(_variant_field(row, "UniqueId") or "")
        if dotnet_ordinal_ignore_case_key(row_unique_id) == path_key:
            return row
        row_designator = str(_variant_field(row, "Designator") or "")
        if (
            fallback is None
            and dotnet_ordinal_ignore_case_key(row_designator) == designator_key
        ):
            fallback = row
    return fallback


def _lookup_case_insensitive(values: dict[str, str], name: str) -> str | None:
    lookup_key = dotnet_ordinal_ignore_case_key(name)
    for key, value in values.items():
        if dotnet_ordinal_ignore_case_key(key) == lookup_key:
            return value
    return None


def _set_case_insensitive_value(values: dict[str, str], name: str, value: str) -> None:
    lookup_key = dotnet_ordinal_ignore_case_key(name)
    for existing_name in values:
        if dotnet_ordinal_ignore_case_key(existing_name) == lookup_key:
            values[existing_name] = value
            return
    values[name] = value


_MANAGED_SYSTEM_PARAMETER_NAMES = frozenset(
    dotnet_ordinal_ignore_case_key(name)
    for name in (
        "User defined",
        "CurrentTime",
        "CurrentDate",
        "Time",
        "Date",
        "DocumentFullPathAndName",
        "DocumentName",
        "ModifiedDate",
        "ApprovedBy",
        "CheckedBy",
        "Author",
        "CompanyName",
        "DrawnBy",
        "Engineer",
        "Organization",
        "Address1",
        "Address2",
        "Address3",
        "Address4",
        "Title",
        "DocumentNumber",
        "Revision",
        "SheetNumber",
        "SheetTotal",
        "Rule",
        "ImagePath",
        "ConfigurationParameters",
        "ConfiguratorName",
        "VersionControl_RevNumber",
        "IsUserConfigurable",
        "ProjectName",
        "Application_BuildNumber",
        "VersionControl_ProjFolderRevNumber",
        "SPICEModelCache",
        "SheetSymbolDesignator",
        "VersionControl_RevNumberShort",
        "VersionControl_ProjFolderRevNumberShort",
        "SystemDesign_ComponentTerminationType",
    )
)
_MANAGED_VARIED_SYSTEM_PARAMETERS = frozenset(
    dotnet_ordinal_ignore_case_key(name)
    for name in ("Comment", "Description", "Footprint")
)


def _managed_parameter_can_be_varied(name: str) -> bool:
    key = dotnet_ordinal_ignore_case_key(name)
    return (
        key not in _MANAGED_SYSTEM_PARAMETER_NAMES
        or key in _MANAGED_VARIED_SYSTEM_PARAMETERS
    )


def _update_case_insensitive_parameters(
    parameters: dict[str, str],
    overrides: Mapping[str, str],
) -> None:
    varied_values = {
        dotnet_ordinal_ignore_case_key(name): value for name, value in overrides.items()
    }
    for name in parameters:
        if not _managed_parameter_can_be_varied(name):
            continue
        varied = varied_values.get(dotnet_ordinal_ignore_case_key(name))
        if varied is not None:
            parameters[name] = varied


def _has_truthy_attribute(value: object, names: tuple[str, ...]) -> bool:
    return any(bool(getattr(value, name, False)) for name in names)


def _first_text_attribute(values: tuple[object, ...], name: str) -> str:
    for value in values:
        text = str(getattr(value, name, "") or "")
        if text:
            return text
    return ""


def _sorted_json_mapping(values: dict) -> dict:
    return dict(sorted(values.items(), key=lambda item: str(item[0])))


def _sorted_json_rows(values: object) -> list:
    if not isinstance(values, list):
        return []
    return [
        _sorted_json_mapping(row) if isinstance(row, dict) else row for row in values
    ]


def _sorted_parameter_overrides(
    overrides: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    return {
        designator: dict(sorted(parameters.items()))
        for designator, parameters in sorted(overrides.items())
    }


def _design_json_sheet_number_value(sheet_number: str) -> int | str:
    if _CANONICAL_DECIMAL_RE.fullmatch(sheet_number):
        value = int(sheet_number)
        if -(1 << 63) <= value < (1 << 63):
            return value
    return sheet_number


def _graph_component_svg_index_rows(
    graph: "AltiumCompiledSchematicGraph",
) -> list[tuple[str, str]]:
    component_designators = {
        str(row.get("id", "")): str(
            row.get("display_designator", "") or row.get("physical_designator", "")
        )
        for row in graph.component_occurrences
    }
    rows: list[tuple[str, str]] = []
    for link in graph.graphical_artifact_links:
        if link.get("target_type") != "sch.component_occurrence":
            continue
        element_id = str(link.get("element_id", ""))
        designator = component_designators.get(str(link.get("target_ref", "")), "")
        if element_id and designator:
            rows.append((element_id, designator))
    return rows


def _resolve_component_value_from_parameters(
    *,
    base_value: str,
    parameters: dict[str, str],
    project_parameters: dict[str, str] | None = None,
) -> str:
    """
    Resolve a component display value from an effective parameter dictionary.
    """
    comment = _lookup_case_insensitive(parameters, "Comment")
    if not comment:
        return base_value
    if not comment.startswith("="):
        return comment

    expr = comment[1:]
    if "+" not in expr and "'" not in expr:
        if expr.lower() == "value":
            return _lookup_case_insensitive(parameters, "Value") or base_value
        resolved = _lookup_case_insensitive(parameters, expr)
        if resolved is not None:
            return resolved
        if project_parameters:
            resolved = _lookup_case_insensitive(project_parameters, expr)
            if resolved is not None:
                return resolved
        return base_value

    expression_parameters: dict[str, str] = {}
    if project_parameters:
        expression_parameters.update(project_parameters)
    for name, parameter_value in parameters.items():
        _set_case_insensitive_value(expression_parameters, name, parameter_value)
    if _lookup_case_insensitive(expression_parameters, "Value") is None:
        expression_parameters["Value"] = base_value
    return _evaluate_altium_expression(expr, expression_parameters)


@dataclass(frozen=True, slots=True)
class _BomComponentValues:
    parameters: dict[str, str]
    value: str
    description: str
    footprint: str
    dnp: bool


def _bom_component_values(
    *,
    base_parameters: Mapping[str, str],
    base_value: str,
    base_description: str,
    base_footprint: str,
    variation: Mapping[str, object] | None,
    parameter_overrides: Mapping[str, Mapping[str, str]],
    project_parameters: dict[str, str] | None,
) -> _BomComponentValues:
    """Apply the established BOM variant/value rules to one component row."""
    parameters = dict(base_parameters)
    managed_parameters = variation.get("_parameters") if variation is not None else None
    if isinstance(managed_parameters, dict):
        component_overrides = {
            str(name): str(value) for name, value in managed_parameters.items()
        }
    else:
        variation_designator = (
            str(_variant_field(variation, "Designator") or "")
            if variation is not None
            else ""
        )
        component_overrides = (
            _case_insensitive_mapping_value(parameter_overrides, variation_designator)
            or {}
        )
    if component_overrides:
        _update_case_insensitive_parameters(parameters, component_overrides)

    value = _resolve_component_value_from_parameters(
        base_value=base_value,
        parameters=parameters,
        project_parameters=project_parameters,
    )
    description_override = _case_insensitive_mapping_value(
        component_overrides, "Description"
    )
    description = (
        description_override
        if description_override is not None
        else _lookup_case_insensitive(parameters, "Description") or base_description
    )
    footprint_override = _case_insensitive_mapping_value(
        component_overrides, "Footprint"
    )
    footprint = footprint_override if footprint_override is not None else base_footprint
    return _BomComponentValues(
        parameters=parameters,
        value=value,
        description=description,
        footprint=footprint,
        dnp=variation is not None and _variant_field(variation, "Kind") == "1",
    )


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

        components = design.to_bom()
        variant_components = design.to_bom(variant="V1")
    """

    project: AltiumPrjPcb | None = None
    schdocs: list[AltiumSchDoc] = field(default_factory=list)
    _netlist: Netlist | None = None
    _netlist_allow_device_sheet_editing: bool | None = field(
        default=None,
        repr=False,
    )
    _compiled_design: AltiumCompiledDesign | None = None
    _compiled_design_allow_device_sheet_editing: bool | None = field(
        default=None,
        repr=False,
    )
    _options: NetlistOptions | None = None
    _load_mode: AltiumProjectLoadMode = field(
        default=AltiumProjectLoadMode.FULL,
        repr=False,
    )

    # Lazy-loaded PcbDoc for pick-and-place and PCB view operations.
    _pcbdoc: AltiumPcbDoc | None = field(default=None, repr=False)
    _pcbdoc_loaded: bool = field(default=False, repr=False)
    _pcbdoc_cache: dict[str, AltiumPcbDoc] = field(default_factory=dict, repr=False)

    @classmethod
    def from_prjpcb(
        cls,
        path: Path | str,
        *,
        load_mode: AltiumProjectLoadMode = AltiumProjectLoadMode.FULL,
    ) -> AltiumDesign:
        """
        Load design from PrjPcb project file.

                Loads compile-participating SchDoc files from durable project metadata.

                Args:
                    path: Path to .PrjPcb file.
                    load_mode: ``FULL`` loads schematics for compilation.
                        ``METADATA_ONLY`` loads project and PCB-discovery facts
                        without reading schematic or PCB document bytes.

                Returns:
                    AltiumDesign with project metadata and, in ``FULL`` mode,
                    all compile-participating schematics loaded.
        """
        from .altium_netlist_options import NetlistOptions
        from .altium_prjpcb import AltiumPrjPcb

        if not isinstance(load_mode, AltiumProjectLoadMode):
            raise TypeError("load_mode must be an AltiumProjectLoadMode")

        path = Path(path)
        project = AltiumPrjPcb(path)

        options = NetlistOptions.from_prjpcb(project)
        if load_mode is AltiumProjectLoadMode.METADATA_ONLY:
            return cls(
                project=project,
                schdocs=[],
                _options=options,
                _load_mode=load_mode,
            )

        from .altium_schdoc import AltiumSchDoc

        schdoc_by_path: dict[Path, AltiumSchDoc] = {}

        def load_schdoc(schdoc_path: Path) -> AltiumSchDoc:
            resolved_path = schdoc_path.resolve()
            schdoc = schdoc_by_path.get(resolved_path)
            if schdoc is None:
                schdoc = AltiumSchDoc(schdoc_path)
                schdoc_by_path[resolved_path] = schdoc
            return schdoc

        schdoc_paths = project.get_reachable_schdoc_paths()
        schdocs = [load_schdoc(schdoc_path) for schdoc_path in schdoc_paths]
        effective_scope = cls(
            project=project,
            schdocs=schdocs,
            _options=options,
        )._resolve_design_effective_scope(options)
        if effective_scope in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
            saved_structure_paths = project.get_saved_structure_schdoc_paths()
            if saved_structure_paths:
                loaded_paths = {schdoc_path.resolve() for schdoc_path in schdoc_paths}
                for schdoc_path in saved_structure_paths:
                    resolved_path = schdoc_path.resolve()
                    if resolved_path not in loaded_paths:
                        loaded_paths.add(resolved_path)
                        schdoc_paths.append(schdoc_path)
                schdocs = [load_schdoc(schdoc_path) for schdoc_path in schdoc_paths]
        else:
            all_schdoc_paths = project.get_schdoc_paths()
            if [path.resolve() for path in all_schdoc_paths] != [
                path.resolve() for path in schdoc_paths
            ]:
                schdocs = [load_schdoc(schdoc_path) for schdoc_path in all_schdoc_paths]

        # Sheet parameters are merged so later sheets can override earlier ones.
        from ._compiler_source import _compiler_document_source

        sheet_params = {}
        for schdoc in schdocs:
            sheet_params.update(_compiler_document_source(schdoc).get_parameter_dict())
        options.sheet_parameters = sheet_params

        return cls(
            project=project,
            schdocs=schdocs,
            _options=options,
            _load_mode=load_mode,
        )

    @property
    def load_mode(self) -> AltiumProjectLoadMode:
        """Return the immutable workload selected at project construction."""
        return self._load_mode

    def _require_schematic_capability(self, operation: str) -> None:
        if self._load_mode is AltiumProjectLoadMode.METADATA_ONLY:
            raise AltiumProjectCapabilityError(
                f"{operation} is unavailable for a metadata-only project; "
                "construct a new design with load_mode=AltiumProjectLoadMode.FULL"
            )

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

    def compile(
        self,
        *,
        force: bool = False,
        allow_device_sheet_editing: bool = False,
    ) -> AltiumCompiledDesign:
        """
        Compile this design into the beta compiled schematic model.

        Args:
            force: Rebuild the cached compiled model when True.
            allow_device_sheet_editing: Match Altium's global preference that
                permits managed device sheets to participate in automatic
                sheet numbering.

        Returns:
            Compiled schematic design with logical documents, physical sheet
            rows, components, options, annotation state, nets, and diagnostics.
        """
        self._require_schematic_capability("schematic compilation")
        if not self.schdocs:
            raise ValueError(
                "AltiumDesign.compile() requires at least one schematic document"
            )
        if (
            force
            or self._compiled_design_allow_device_sheet_editing
            != allow_device_sheet_editing
        ):
            self._compiled_design = None
            self._netlist = None
        if self._compiled_design is None:
            from .altium_design_compiler import compile_design

            self._compiled_design = compile_design(
                self,
                allow_device_sheet_editing=allow_device_sheet_editing,
            )
            self._compiled_design_allow_device_sheet_editing = (
                allow_device_sheet_editing
            )
        return self._compiled_design

    def to_netlist(self, *, allow_device_sheet_editing: bool = False) -> Netlist:
        """
        Generate unified netlist (cached).

                Args:
                    allow_device_sheet_editing: Match Altium's global device-
                        sheet editing preference during compilation.

                Returns:
                    Netlist object with all nets and components.
        """
        if (
            self._netlist is None
            or self._netlist_allow_device_sheet_editing != allow_device_sheet_editing
        ):
            self._compile_cached_netlist(allow_device_sheet_editing)
        if self._netlist is None:
            raise RuntimeError("Netlist compilation did not produce a netlist")
        return self._netlist

    def _compile_cached_netlist(self, allow_device_sheet_editing: bool = False) -> None:
        """
        Compile and cache the design netlist from the compiled design model.
        """
        if (
            self._netlist is not None
            and self._netlist_allow_device_sheet_editing == allow_device_sheet_editing
        ):
            return

        compiled = self.compile(allow_device_sheet_editing=allow_device_sheet_editing)
        self._netlist = compiled.to_netlist()
        self._netlist_allow_device_sheet_editing = allow_device_sheet_editing
        from .altium_compiled_design_netlist import compiled_design_schematic_hierarchy

        self._netlist.schematic_hierarchy = compiled_design_schematic_hierarchy(
            compiled,
            self.schdocs,
        )

    def to_json(
        self,
        include_indexes: bool = True,
        *,
        include_compile_metadata: bool = False,
        include_pnp: bool = True,
    ) -> dict:
        """
        Serialize design state to JSON-compatible dict.

                The returned format is the package-owned design contract with
                project metadata, enriched components, optional PCB-backed
                pick-and-place placements, and compiled nets.

                Args:
                    include_indexes: If True, include pre-computed lookup indexes
                    include_compile_metadata: If True, include low-level compile
                        summary/options/annotation state and diagnostics. The
                        default keeps the user-facing design projection compact.
                    include_pnp: If True, include the PcbDoc-backed pick-and-place
                        projection when the project references a board. Set to False
                        for a schematic-only projection that never loads the PcbDoc.

        Returns:
            JSON-compatible dict with design data
        """
        compiled = self.compile()
        if self._has_repeated_physical_instances(compiled):
            return self._design_json_from_netlist(
                None,
                include_indexes,
                compiled=compiled,
                include_compile_metadata=include_compile_metadata,
                include_pnp=include_pnp,
            )
        return self._design_json_from_netlist(
            self.to_netlist(),
            include_indexes,
            compiled=compiled,
            include_compile_metadata=include_compile_metadata,
            include_pnp=include_pnp,
        )

    def to_physical_ir(
        self,
        page_occurrence_ref: str,
        project_parameters: dict[str, str] | None = None,
        *,
        profile: str = "onscreen",
        render_options: "SchSvgRenderOptions | None" = None,
    ) -> "SchGeometryDocument":
        """
        Render one compiled physical schematic page to geometry IR.

        The logical SchDoc geometry is reused, but component designator text is
        resolved through the compiled physical page before measurement and IR
        emission. This is the project-level rendering path for repeated sheets
        and instantiated channels.
        """
        compiled = self.compile()
        physical = self._resolve_physical_document(compiled, page_occurrence_ref)
        requested_page_id = str(page_occurrence_ref or "").strip()
        graph = getattr(compiled, "compiled_schematic_graph", None)
        canonical_page_id = ""
        if graph is not None and any(
            str(page.get("id", "")) == requested_page_id
            for page in getattr(graph, "page_occurrences", ())
        ):
            canonical_page_id = requested_page_id
        if not canonical_page_id and graph is not None:
            canonical_page_id = next(
                (
                    str(page.get("id", ""))
                    for document, page in zip(
                        compiled.physical_documents,
                        getattr(graph, "page_occurrences", ()),
                        strict=False,
                    )
                    if document is physical
                ),
                "",
            )
        if not canonical_page_id:
            canonical_page_id = physical.id
        schdoc = self._schdoc_for_physical_document(compiled, physical)
        from .altium_sch_svg_renderer import SchSvgRenderOptions

        naming_options = render_options or SchSvgRenderOptions.onscreen_compiled()
        designator_overrides = self._physical_designator_text_overrides(
            compiled,
            physical,
            schdoc,
            expand=bool(naming_options.expand_component_designators),
            multipart_naming_method=naming_options.multipart_naming_method,
            multipart_separator=naming_options.multipart_separator,
        )
        active_variant = self._active_variant_name()
        variant_data = self._variant_data(active_variant)
        effective_project_parameters = self._physical_project_parameters(
            project_parameters,
            active_variant=active_variant,
            variant_data=variant_data,
        )
        project_states = self._physical_component_project_states(
            compiled,
            physical,
            schdoc,
            project_parameters=effective_project_parameters,
            variant_data=variant_data,
        )

        document = schdoc._render_physical_ir(
            project_parameters=effective_project_parameters,
            profile=profile,
            render_options=render_options,
            designator_text_overrides=designator_overrides,
            component_project_states=project_states,
        )
        runtime_image_hrefs = getattr(document, "_runtime_image_hrefs", None)
        render_hints = dict(document.render_hints or {})
        render_hints["page_occurrence_ref"] = canonical_page_id
        render_hints["artifact_key"] = "sch.dwg_scene"
        render_hints["physical_source_sheet"] = physical.file_name
        render_hints["physical_designator_text_override_count"] = len(
            designator_overrides
        )
        extras = dict(document.extras or {})
        extras["physical_page"] = self._physical_page_render_metadata(physical)
        extras["physical_page"]["page_occurrence_ref"] = canonical_page_id
        if graph is not None:
            extras["compiled_schematic_graphical_links"] = [
                dict(link)
                for link in getattr(graph, "graphical_artifact_links", ())
                if str(link.get("page_occurrence_ref", "")) == canonical_page_id
                and str(link.get("artifact_key", "")) == "sch.dwg_scene"
            ]
        if designator_overrides:
            extras["physical_designator_text_overrides"] = dict(
                sorted(designator_overrides.items())
            )

        physical_document = replace(
            document,
            document_id=canonical_page_id,
            source_path=physical.source_path or document.source_path,
            render_hints=render_hints,
            extras=extras,
        )
        if isinstance(runtime_image_hrefs, dict):
            object.__setattr__(
                physical_document,
                "_runtime_image_hrefs",
                runtime_image_hrefs,
            )
        return physical_document

    def to_physical_svg(
        self,
        page_occurrence_ref: str,
        *,
        include_border: bool = True,
        scale: float = 1.0,
        options: "SchSvgRenderOptions | None" = None,
        project_parameters: dict[str, str] | None = None,
        wrap_components: bool = False,
    ) -> str:
        """
        Render one compiled physical schematic page to SVG.

        `AltiumSchDoc.to_svg()` remains the logical-sheet renderer. This method
        is the project-level physical renderer for design review consumers that
        need resolved channel/repeated-sheet designators.
        """
        from .altium_sch_geometry_renderer import (
            SchGeometrySvgRenderOptions,
            SchGeometrySvgRenderer,
        )
        from .altium_sch_svg_renderer import SchSvgRenderOptions

        del wrap_components
        if abs(scale - 1.0) > 1e-9:
            raise ValueError("Schematic IR SVG rendering currently requires scale=1.0")

        render_options = options if options is not None else SchSvgRenderOptions()
        ir_profile = (
            "oracle" if render_options.truncate_font_size_for_baseline else "onscreen"
        )
        document = self.to_physical_ir(
            page_occurrence_ref,
            project_parameters=project_parameters,
            profile=ir_profile,
            render_options=render_options,
        )
        runtime_image_hrefs = getattr(document, "_runtime_image_hrefs", None)

        if not include_border:
            document = replace(
                document,
                records=[
                    record
                    for record in document.records
                    if str(getattr(record, "kind", "") or "") != "sheet"
                ],
                workspace_background_color=None,
            )
            if isinstance(runtime_image_hrefs, dict):
                object.__setattr__(
                    document,
                    "_runtime_image_hrefs",
                    runtime_image_hrefs,
                )

        return SchGeometrySvgRenderer(
            SchGeometrySvgRenderOptions(
                include_workspace_background=include_border,
                text_mode=(
                    "native_svg_export"
                    if render_options.truncate_font_size_for_baseline
                    else "onscreen"
                ),
                compile_mask_render_mode=render_options.compile_mask_render_mode,
                text_as_polygons=render_options.text_as_polygons,
                polygon_text_tolerance=render_options.polygon_text_tolerance,
                include_view_box=render_options.include_view_box,
                embed_bundled_fallback_fonts=(
                    render_options.embed_bundled_fallback_fonts
                ),
            )
        ).render(document)

    @staticmethod
    def _physical_page_render_metadata(
        physical_document: AltiumCompiledPhysicalDocument,
    ) -> dict:
        return {
            "id": physical_document.id,
            "logical_document_id": physical_document.logical_document_id,
            "source_sheet": physical_document.file_name,
            "source_path": physical_document.source_path,
            "physical_instance_path": physical_document.physical_instance_path,
            "physical_instance_unique_id": (
                physical_document.physical_instance_unique_id
            ),
            "channel_index": physical_document.channel_index,
            "channel_prefix": physical_document.channel_prefix or "",
            "channel_alpha": physical_document.channel_alpha or "",
            "room_name": physical_document.room_name,
            "parent_id": physical_document.parent_id,
            "parent_sheet_symbol_id": physical_document.parent_sheet_symbol_id,
            "sheet_number": physical_document.sheet_number,
            "document_number": physical_document.document_number,
        }

    @staticmethod
    def _path_identity(path_value: object) -> str:
        if not path_value:
            return ""
        try:
            return str(Path(str(path_value)).resolve()).lower()
        except OSError:
            return str(Path(str(path_value))).lower()

    def _resolve_physical_document(
        self,
        compiled: AltiumCompiledDesign,
        page_occurrence_ref: str,
    ) -> AltiumCompiledPhysicalDocument:
        requested = str(page_occurrence_ref or "").strip()
        if not requested:
            raise ValueError("page_occurrence_ref is required")
        graph = getattr(compiled, "compiled_schematic_graph", None)
        if graph is not None:
            for page in getattr(graph, "page_occurrences", ()):
                if str(page.get("id", "")) != requested:
                    continue
                instance_order = int(page.get("instance_order", 0) or 0)
                definition_ref = str(page.get("page_definition_ref", ""))
                definition = next(
                    (
                        row
                        for row in getattr(graph, "page_definitions", ())
                        if str(row.get("id", "")) == definition_ref
                    ),
                    None,
                )
                if definition is not None:
                    unit_definition_ref = str(definition.get("unit_definition_ref", ""))
                    logical_ref_by_definition = {
                        str(unit_ref): logical.id
                        for logical, unit_ref in zip(
                            compiled.logical_documents,
                            (
                                row.get("id", "")
                                for row in getattr(graph, "unit_definitions", ())
                            ),
                            strict=False,
                        )
                    }
                    logical_id = logical_ref_by_definition.get(unit_definition_ref)
                    candidates = [
                        document
                        for document in compiled.physical_documents
                        if document.logical_document_id == logical_id
                    ]
                    source_path = str(
                        dict(page.get("source_identity", {})).get(
                            "sch.source_key.source_path", ""
                        )
                    )
                    for document in candidates:
                        if (
                            document.physical_instance_unique_id
                            and document.physical_instance_unique_id == source_path
                        ):
                            return document
                    for document in candidates:
                        if document.ordinal == instance_order:
                            return document
                    if candidates:
                        return candidates[0]
        for document in compiled.physical_documents:
            identifiers = {
                str(value)
                for value in (
                    document.id,
                    document.physical_instance_path,
                    document.physical_instance_unique_id,
                )
                if value
            }
            if requested in identifiers:
                return document
        available = ", ".join(
            document.id for document in compiled.physical_documents[:10]
        )
        if len(compiled.physical_documents) > 10:
            available += ", ..."
        raise ValueError(
            f"unknown page_occurrence_ref {page_occurrence_ref!r}; "
            f"available legacy physical ids: {available}"
        )

    def _schdoc_for_physical_document(
        self,
        compiled: AltiumCompiledDesign,
        physical_document: AltiumCompiledPhysicalDocument,
    ) -> AltiumSchDoc:
        logical_by_id = {
            document.id: document for document in compiled.logical_documents
        }
        logical = logical_by_id.get(physical_document.logical_document_id)
        path_candidates = {
            self._path_identity(getattr(physical_document, "source_path", "")),
        }
        if logical is not None:
            path_candidates.add(
                self._path_identity(getattr(logical, "source_path", ""))
            )
        path_candidates.discard("")
        for schdoc in self.schdocs:
            if (
                schdoc.filepath
                and self._path_identity(schdoc.filepath) in path_candidates
            ):
                return schdoc

        file_name = str(
            getattr(logical, "file_name", "")
            if logical is not None
            else getattr(physical_document, "file_name", "")
        )
        file_name_key = _logical_source_identity_key(
            _logical_source_basename(file_name)
        )
        matches = [
            schdoc
            for schdoc in self.schdocs
            if schdoc.filepath
            and _logical_source_identity_key(schdoc.filepath.name) == file_name_key
        ]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(
            "could not resolve source SchDoc for physical page "
            f"{physical_document.id!r}"
        )

    @staticmethod
    def _source_designator_record_id(component_record: object) -> str:
        from ._sch_source_projection import _component_bound_field_slots

        children = getattr(component_record, "children", ()) or getattr(
            component_record, "parameters", ()
        )
        designator_record = _component_bound_field_slots(children).get("designator")
        designator_unique_id = str(getattr(designator_record, "unique_id", "") or "")
        if designator_unique_id:
            return designator_unique_id
        component_unique_id = str(getattr(component_record, "unique_id", "") or "")
        if component_unique_id:
            return f"component:{component_unique_id}:designator"
        return ""

    @classmethod
    def _source_designator_record_indexes(
        cls, schdoc: AltiumSchDoc
    ) -> tuple[dict[str, str], dict[str, str]]:
        from .altium_compiled_design_support import _compiler_component_sources

        designator_record_by_component_id: dict[str, str] = {}
        designator_record_by_logical_designator: dict[str, str] = {}
        for component_info in _compiler_component_sources(schdoc):
            component_record = component_info.record
            designator_record_id = cls._source_designator_record_id(component_record)
            if not designator_record_id:
                continue
            component_id = str(getattr(component_record, "unique_id", "") or "")
            if component_id:
                designator_record_by_component_id.setdefault(
                    dotnet_ordinal_ignore_case_key(component_id), designator_record_id
                )
            logical_designator = str(component_info.designator or "")
            if logical_designator:
                designator_record_by_logical_designator.setdefault(
                    logical_designator,
                    designator_record_id,
                )
        return (
            designator_record_by_component_id,
            designator_record_by_logical_designator,
        )

    def _physical_designator_text_overrides(
        self,
        compiled: AltiumCompiledDesign,
        physical_document: AltiumCompiledPhysicalDocument,
        schdoc: AltiumSchDoc,
        *,
        expand: bool = True,
        multipart_naming_method: int = 0,
        multipart_separator: str = ":",
    ) -> dict[str, str]:
        self._validate_physical_designator_options(
            expand, multipart_naming_method, multipart_separator
        )
        if not expand:
            return {}
        (
            designator_record_by_component_id,
            designator_record_by_logical_designator,
        ) = self._source_designator_record_indexes(schdoc)
        overrides: dict[str, str] = {}
        # Flattened rows can collapse bodies from different source pages. Use
        # the compiler's pre-collapse evidence, as the schematic graph does.
        # Older programmatically constructed snapshots may lack that evidence.
        component_bodies = compiled._component_body_evidence or compiled.components
        for component in component_bodies:
            if component.physical_document_id != physical_document.id:
                continue
            override = self._physical_designator_override(
                component,
                designator_record_by_component_id,
                designator_record_by_logical_designator,
                has_body_evidence=bool(compiled._component_body_evidence),
                multipart_naming_method=multipart_naming_method,
                multipart_separator=multipart_separator,
            )
            if override is not None:
                designator_record_id, resolved_designator = override
                overrides[designator_record_id] = resolved_designator
        return overrides

    def _physical_project_parameters(
        self,
        supplied: dict[str, str] | None,
        *,
        active_variant: str | None,
        variant_data: dict[str, object] | None,
    ) -> dict[str, str]:
        if supplied is not None:
            parameters = dict(supplied)
        elif self.project is not None:
            parameters = dict(self.project.parameters)
        else:
            parameters = {}
        variant_parameters = _variant_field(variant_data or {}, "parameters")
        if isinstance(variant_parameters, Mapping):
            for name, value in variant_parameters.items():
                _set_case_insensitive_value(parameters, str(name), str(value))
        _set_case_insensitive_value(
            parameters,
            "VariantName",
            active_variant or "[No Variations]",
        )
        return parameters

    @staticmethod
    def _validate_physical_designator_options(
        expand: bool,
        multipart_naming_method: int,
        multipart_separator: str,
    ) -> None:
        if type(expand) is not bool:
            raise ValueError("expand component designators must be a boolean")
        if type(multipart_naming_method) is not int or multipart_naming_method not in (
            0,
            1,
        ):
            raise ValueError("multipart naming method must be 0 or 1")
        if not isinstance(multipart_separator, str):
            raise ValueError("multipart separator must be a string")

    @classmethod
    def _physical_designator_override(
        cls,
        component: AltiumCompiledComponent,
        designator_record_by_component_id: Mapping[str, str],
        designator_record_by_logical_designator: Mapping[str, str],
        *,
        has_body_evidence: bool,
        multipart_naming_method: int,
        multipart_separator: str,
    ) -> tuple[str, str] | None:
        resolved = cls._physical_display_designator(
            component,
            multipart_naming_method=multipart_naming_method,
            multipart_separator=multipart_separator,
        )
        if not has_body_evidence:
            resolved = resolved or component.physical_designator
        # Managed display updates install only nonempty expanded names; an
        # empty compiler name leaves the logical display in place.
        if not resolved:
            return None
        record_id = designator_record_by_component_id.get(
            dotnet_ordinal_ignore_case_key(component.source_object_id)
        )
        if not record_id and not has_body_evidence:
            record_id = designator_record_by_logical_designator.get(
                component.logical_designator
            )
        return (record_id, resolved) if record_id else None

    @staticmethod
    def _physical_display_designator(
        component: AltiumCompiledComponent,
        *,
        multipart_naming_method: int,
        multipart_separator: str,
    ) -> str:
        display = component.display_designator
        if multipart_naming_method == 0 or component.part_count <= 2:
            return display
        from .altium_netlist_common import _component_part_alpha_suffix

        alpha = _component_part_alpha_suffix(
            part_count=component.part_count - 1,
            current_part_id=component.current_part_id,
        )
        replacement = f"{multipart_separator}{component.current_part_id}"
        base = component.physical_designator
        candidates = [
            index
            for index in range(len(base) + 1)
            if f"{base[:index]}{alpha}{base[index:]}" == display
        ]
        if len(candidates) != 1:
            return display
        index = candidates[0]
        return f"{base[:index]}{replacement}{base[index:]}"

    def _physical_component_project_states(
        self,
        compiled: AltiumCompiledDesign,
        physical_document: AltiumCompiledPhysicalDocument,
        schdoc: AltiumSchDoc,
        *,
        project_parameters: Mapping[str, str],
        variant_data: dict[str, object] | None = None,
    ) -> dict[int, _ComponentProjectRenderState]:
        from ._altium_sch_component_project_state import (
            _ProjectVariantsLibraryIndex,
            _prepare_component_project_render_state,
        )

        if variant_data is None:
            return {}
        variant_index = _PhysicalVariantIndex(variant_data)
        source_by_id, source_by_designator = self._source_component_render_indexes(
            schdoc
        )
        project_path = self.project.filepath if self.project is not None else None
        library_index = _ProjectVariantsLibraryIndex(project_path)
        show_alternate = _variant_show_alternate_symbols(variant_data)
        result: dict[int, _ComponentProjectRenderState] = {}
        component_bodies = compiled._component_body_evidence or compiled.components
        for body in component_bodies:
            if body.physical_document_id != physical_document.id:
                continue
            source = self._source_component_for_body(
                body,
                source_by_id,
                source_by_designator,
                allow_designator_fallback=not compiled._component_body_evidence,
            )
            if source is None or id(source) in result:
                continue
            variation = self._component_variation(
                variant_data,
                logical_unique_id=body.source_object_id,
                unique_id_path=body.source_unique_id_path,
                physical_designator=body.physical_designator,
                variant_index=variant_index,
            )
            if variation is None:
                continue
            result[id(source)] = _prepare_component_project_render_state(
                source,
                variation,
                project_path=project_path,
                component_parameters=dict(body.parameters),
                project_parameters=project_parameters,
                show_alternate_symbols=show_alternate,
                library_index=library_index,
            )
        return result

    @staticmethod
    def _source_component_render_indexes(
        schdoc: AltiumSchDoc,
    ) -> tuple[dict[str, AltiumSchComponent], dict[str, AltiumSchComponent]]:
        from .altium_compiled_design_support import _compiler_component_sources

        sources = _compiler_component_sources(schdoc)
        by_id: dict[str, AltiumSchComponent] = {}
        by_designator: dict[str, AltiumSchComponent] = {}
        for info in sources:
            unique_id = str(info.record.unique_id or "")
            if unique_id:
                by_id[dotnet_ordinal_ignore_case_key(unique_id)] = info.record
            designator = str(info.designator or "")
            if designator:
                by_designator[designator] = info.record
        return by_id, by_designator

    @staticmethod
    def _source_component_for_body(
        body: AltiumCompiledComponent,
        source_by_id: Mapping[str, AltiumSchComponent],
        source_by_designator: Mapping[str, AltiumSchComponent],
        *,
        allow_designator_fallback: bool,
    ) -> AltiumSchComponent | None:
        source = source_by_id.get(dotnet_ordinal_ignore_case_key(body.source_object_id))
        if source is None and allow_designator_fallback:
            return source_by_designator.get(body.logical_designator)
        return source

    def _design_json_from_netlist(
        self,
        netlist: Netlist | None,
        include_indexes: bool,
        *,
        compiled: AltiumCompiledDesign | None = None,
        include_compile_metadata: bool,
        include_pnp: bool,
    ) -> dict:
        """
        Build enriched design JSON around an already-generated netlist.
        """

        from .altium_netlist_options import NetlistOptions

        options = self._options or NetlistOptions()
        compiled = compiled or self.compile()

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
        active_variant_name = self._active_variant_name()
        active_variant_data = self._variant_data(active_variant_name)
        active_variant_index = _PhysicalVariantIndex(active_variant_data)
        has_repeated_physical_instances = self._has_repeated_physical_instances(
            compiled
        )
        comp_data_map = {}
        if not has_repeated_physical_instances:
            if netlist is None:
                netlist = self.to_netlist()
            comp_data_map = self._build_component_data_map(
                netlist,
                compiled,
            )
        components_data = []
        if has_repeated_physical_instances:
            components_data = self._build_compiled_components_data(
                compiled,
                active_variant_data=active_variant_data,
                variant_index=active_variant_index,
            )
        if not components_data and not has_repeated_physical_instances:
            if netlist is None:
                netlist = self.to_netlist()
            components_data = []
            for comp in netlist.components:
                data = comp_data_map.get(comp.designator, {})
                is_dnp = self._component_is_dnp(
                    active_variant_data,
                    variant_index=active_variant_index,
                    logical_unique_id=str(data.get("source_unique_id") or ""),
                    unique_id_path=str(data.get("source_unique_id_path") or ""),
                    physical_designator=comp.designator,
                )
                components_data.append(
                    self._enrich_component(
                        comp,
                        sheet=data.get("sheet", ""),
                        pin_count=data.get("pin_count", 0),
                        svg_id=data.get("svg_id", ""),
                        component_context={
                            **data,
                            "dnp": is_dnp,
                            "fitted": not is_dnp,
                        },
                    )
                )
        net_name_sources_by_id: dict[str, list[dict]] = {}
        net_aliases_by_key: dict[tuple[str, str, tuple[str, ...]], list[str]] = {}
        graph = compiled.compiled_schematic_graph
        if graph is None:
            raise RuntimeError("compiled design did not produce a schematic graph")

        result = {
            "schema": DESIGN_JSON_SCHEMA,
            "generator": DESIGN_JSON_GENERATOR,
            "project": project_data,
            "variants": variants_data,
            "options": options_data,
        }

        if include_compile_metadata:
            result["compile"] = self._build_compile_data(compiled)
            result["diagnostics"] = self._compiled_design_diagnostics(compiled)

        result.update(
            {
                "sheets": sheets_data,
                "components": components_data,
                "schematic_hierarchy": self._build_schematic_hierarchy_data(
                    netlist,
                    compiled=compiled,
                ),
                "compiled_schematic_graph": graph.to_json(),
                "physical_page_metadata": [
                    row.to_json() for row in compiled.physical_page_metadata
                ],
            }
        )

        if include_pnp:
            pnp_data = self._build_pnp_data()
            if pnp_data is not None:
                result["pnp"] = pnp_data

        if has_repeated_physical_instances:
            nets_data = self._build_compiled_nets_data(
                compiled,
                net_name_sources_by_id=net_name_sources_by_id,
                net_aliases_by_key=net_aliases_by_key,
            )
        else:
            if netlist is None:
                netlist = self.to_netlist()
            nets_data = self._build_legacy_json_nets_data(
                netlist,
                compiled,
                net_name_sources_by_id=net_name_sources_by_id,
                net_aliases_by_key=net_aliases_by_key,
            )
        result["nets"] = nets_data

        if include_indexes:
            result["indexes"] = self._build_indexes(
                components_data,
                nets_data,
                graph,
            )

        return result

    @staticmethod
    def _build_compile_data(compiled: AltiumCompiledDesign) -> dict:
        """
        Build compact compiled-design metadata for the public design JSON.
        """
        return {
            "schema": compiled.schema,
            "summary": compiled.summary.to_dict(),
            "options": compiled.options.to_dict(),
            "annotation": compiled.annotation.to_dict(),
            "stats": dict(sorted(compiled.compile.items())),
        }

    @staticmethod
    def _compiled_design_diagnostics(
        compiled: AltiumCompiledDesign,
    ) -> list[dict]:
        """
        Return compile diagnostics from every public compiled-design owner.
        """
        rows: list[dict] = []

        def add_rows(
            owner_kind: str,
            owner_id: str,
            diagnostics: tuple[AltiumCompileDiagnostic, ...],
        ) -> None:
            for diagnostic in diagnostics:
                row = diagnostic.to_dict()
                row["owner_kind"] = owner_kind
                if owner_id:
                    row["owner_id"] = owner_id
                rows.append(row)

        add_rows("compile", "", compiled.diagnostics)
        add_rows("annotation", "", compiled.annotation.diagnostics)
        for document in compiled.logical_documents:
            add_rows("logical_document", document.id, document.diagnostics)
        for document in compiled.physical_documents:
            add_rows("physical_document", document.id, document.diagnostics)
        for symbol in compiled.sheet_symbols:
            add_rows("sheet_symbol", symbol.id, symbol.diagnostics)
        for component in compiled.components:
            add_rows("component", component.id, component.diagnostics)
        for net in compiled.nets:
            add_rows("net", net.id, net.diagnostics)
        return rows

    @staticmethod
    def _has_repeated_physical_instances(compiled: AltiumCompiledDesign) -> bool:
        physical_count_by_logical_id: dict[str, int] = {}
        for document in compiled.physical_documents:
            logical_id = str(document.logical_document_id or "")
            if not logical_id:
                continue
            physical_count_by_logical_id[logical_id] = (
                physical_count_by_logical_id.get(logical_id, 0) + 1
            )
        return any(count > 1 for count in physical_count_by_logical_id.values())

    def _build_legacy_json_nets_data(
        self,
        netlist: Netlist,
        compiled: AltiumCompiledDesign,
        *,
        net_name_sources_by_id: dict[str, list[dict]] | None = None,
        net_aliases_by_key: dict[tuple[str, str, tuple[str, ...]], list[str]]
        | None = None,
    ) -> list[dict]:
        name_source_cache = (
            net_name_sources_by_id if net_name_sources_by_id is not None else {}
        )
        alias_cache = net_aliases_by_key if net_aliases_by_key is not None else {}
        compiled_flat_by_id = {
            net.id: net for net in compiled.nets if net.scope == "compiled_flat"
        }
        nets_data = [self._design_b0_net_data(net) for net in netlist.nets]
        for net_data in nets_data:
            compiled_id = str(net_data.get("uid") or "")
            compiled_net = compiled_flat_by_id.get(compiled_id)
            if compiled_net is not None:
                name_sources = name_source_cache.get(compiled_net.id)
                if name_sources is None:
                    name_sources = self._compiled_net_name_sources(compiled_net)
                    name_source_cache[compiled_net.id] = name_sources
                net_name = str(net_data.get("name") or "")
                raw_aliases = net_data.get("aliases")
                source_aliases = (
                    [str(value) for value in raw_aliases]
                    if isinstance(raw_aliases, list)
                    else []
                )
                aliases = self._design_net_aliases_cached(
                    alias_cache,
                    compiled_net.id,
                    net_name,
                    source_aliases,
                    name_sources,
                )
                net_data["aliases"] = aliases
                if len(name_sources) > 1:
                    net_data["name_sources"] = self._copy_net_name_sources(name_sources)
        compiled_nets_data = self._build_compiled_nets_data(
            compiled,
            net_name_sources_by_id=name_source_cache,
            net_aliases_by_key=alias_cache,
        )
        return self._merge_legacy_json_nets_data(
            nets_data,
            compiled_nets_data,
            frozenset(compiled_flat_by_id),
        )

    @staticmethod
    def _design_b0_net_data(net: Net) -> dict[str, object]:
        """Project a rich net into the stable embedded Design b0 shape."""
        from .altium_netlist_model import (
            _endpoint_sort_key,
            _hierarchy_path_to_json,
            _terminal_sort_key,
            _unique_sorted_dicts,
            _unique_sorted_json_values,
            _unique_sorted_strings,
        )

        data: dict[str, object] = {
            "uid": net.uid,
            "name": net.name,
            "auto_named": net.auto_named,
            "source_sheets": _unique_sorted_strings(net.source_sheets),
            "terminals": sorted(
                [
                    {
                        "designator": terminal.designator,
                        "pin": terminal.pin,
                        "pin_name": terminal.pin_name,
                        "pin_type": terminal.pin_type.name,
                    }
                    for terminal in net.terminals
                ],
                key=_terminal_sort_key,
            ),
            "graphical": net.graphical.to_json(),
            "aliases": _unique_sorted_strings(net.aliases),
            "endpoints": _unique_sorted_dicts(
                [endpoint.to_json() for endpoint in net.endpoints],
                _endpoint_sort_key,
            ),
        }
        if net.hierarchy_paths:
            data["hierarchy_paths"] = _unique_sorted_json_values(
                [_hierarchy_path_to_json(path) for path in net.hierarchy_paths]
            )
        return data

    @staticmethod
    def _merge_legacy_json_nets_data(
        nets_data: list[dict],
        compiled_nets_data: list[dict],
        compiled_flat_ids: frozenset[str],
    ) -> list[dict]:
        legacy_by_compiled_id = AltiumDesign._legacy_json_nets_by_compiled_id(
            nets_data, compiled_flat_ids
        )
        ordered_nets_data = [
            legacy_by_compiled_id.pop(str(net_data.get("id") or ""), net_data)
            for net_data in compiled_nets_data
        ]
        ordered_nets_data.extend(
            net_data
            for net_data in nets_data
            if (
                str(net_data.get("uid") or "") not in compiled_flat_ids
                or str(net_data.get("uid") or "") in legacy_by_compiled_id
            )
        )
        for index, net_data in enumerate(ordered_nets_data, start=1):
            net_data["uid"] = f"{index:012x}"
        return ordered_nets_data

    @staticmethod
    def _legacy_json_nets_by_compiled_id(
        nets_data: list[dict], compiled_flat_ids: frozenset[str]
    ) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for net_data in nets_data:
            compiled_id = str(net_data.get("uid") or "")
            if compiled_id in compiled_flat_ids:
                result.setdefault(compiled_id, net_data)
        return result

    def _build_compiled_components_data(
        self,
        compiled: AltiumCompiledDesign,
        *,
        active_variant_data: dict[str, object] | None = None,
        variant_index: _PhysicalVariantIndex | None = None,
    ) -> list[dict]:
        """
        Build top-level design JSON component rows from compiled physical rows.
        """
        from .altium_netlist_model import ComponentClassification

        if variant_index is None:
            variant_index = _PhysicalVariantIndex(active_variant_data)
        physical_by_id = {
            document.id: document for document in compiled.physical_documents
        }
        flat_nets = [net for net in compiled.nets if net.scope == "compiled_flat"]
        component_to_nets = self._compiled_component_to_nets(flat_nets)
        components_data: list[dict] = []
        for component in sorted(
            compiled.components,
            key=lambda item: (
                item.physical_document_id,
                item.display_designator or item.physical_designator,
                item.id,
            ),
        ):
            designator = component.display_designator or component.physical_designator
            if not designator:
                continue
            is_dnp = self._component_is_dnp(
                active_variant_data,
                variant_index=variant_index,
                logical_unique_id=component.source_object_id,
                unique_id_path=component.source_unique_id_path,
                physical_designator=component.physical_designator,
            )
            physical = physical_by_id.get(component.physical_document_id)
            sheet = physical.file_name if physical is not None else ""
            hierarchy = self._parse_hierarchy(designator, sheet)
            classification = ComponentClassification.from_component(
                designator,
                component.pin_count,
            )
            components_data.append(
                {
                    "id": component.id,
                    "designator": designator,
                    "logical_designator": component.logical_designator,
                    "physical_designator": component.physical_designator,
                    "physical_sheet_id": component.physical_document_id,
                    "source_unique_id": component.source_object_id,
                    "source_unique_id_path": component.source_unique_id_path,
                    "compiled_component_id": component.id,
                    "svg_id": component.source_object_id,
                    "sheet": sheet,
                    "pin_count": component.pin_count,
                    "part_count": component.part_count,
                    "current_part_id": component.current_part_id,
                    "value": component.value,
                    "footprint": component.footprint,
                    "library_ref": component.lib_reference,
                    "description": component.description,
                    "design_item_id": component.design_item_id,
                    "component_kind": component.component_kind,
                    "component_kind_value": component.component_kind_value,
                    "include_in_netlist": component.include_in_netlist,
                    "exclude_from_bom": component.exclude_from_bom,
                    "dnp": is_dnp,
                    "fitted": not is_dnp,
                    "annotation_state": component.annotation_state,
                    "annotation_locked": component.annotation_locked,
                    "hierarchy": hierarchy.to_json(),
                    "classification": classification.to_json(),
                    "parameters": dict(sorted(dict(component.parameters).items())),
                    "nets": component_to_nets.get(designator, []),
                }
            )
        return components_data

    @staticmethod
    def _compiled_component_to_nets(
        flat_nets: Sequence[AltiumCompiledNet],
    ) -> dict[str, list[str]]:
        component_to_nets: dict[str, set[str]] = {}
        for net in flat_nets:
            net_name = str(getattr(net, "name", "") or "")
            if not net_name:
                continue
            for terminal in getattr(net, "terminals", ()) or ():
                designator = str(getattr(terminal, "designator", "") or "")
                if not designator:
                    continue
                component_to_nets.setdefault(designator, set()).add(net_name)
        return {
            designator: sorted(nets)
            for designator, nets in sorted(component_to_nets.items())
        }

    def _build_compiled_nets_data(
        self,
        compiled: AltiumCompiledDesign,
        *,
        net_name_sources_by_id: dict[str, list[dict]] | None = None,
        net_aliases_by_key: dict[tuple[str, str, tuple[str, ...]], list[str]]
        | None = None,
    ) -> list[dict]:
        """
        Build top-level design JSON net rows from compiled-flat nets.
        """
        name_source_cache = (
            net_name_sources_by_id if net_name_sources_by_id is not None else {}
        )
        alias_cache = net_aliases_by_key if net_aliases_by_key is not None else {}
        nets_data: list[dict] = []
        flat_nets = [net for net in compiled.nets if net.scope == "compiled_flat"]
        for index, net in enumerate(flat_nets, start=1):
            name_sources = name_source_cache.get(net.id)
            if name_sources is None:
                name_sources = self._compiled_net_name_sources(net)
                name_source_cache[net.id] = name_sources
            net_data = {
                "id": net.id,
                "uid": f"{index:012x}",
                "name": net.name,
                "aliases": self._design_net_aliases_cached(
                    alias_cache,
                    net.id,
                    net.name,
                    list(net.aliases),
                    name_sources,
                ),
                "physical_document_ids": list(net.physical_document_ids),
                "source_sheets": list(net.physical_document_ids),
                "terminal_count": len(net.terminals),
                "terminals": [terminal.to_dict() for terminal in net.terminals],
                "graphical": self._compiled_page_net_graphical(list(net.items)),
                "endpoint_ids": _unique_nonempty_strings(
                    [endpoint.id for endpoint in net.endpoints]
                ),
                "endpoints": [endpoint.to_dict() for endpoint in net.endpoints],
                "auto_named": net.auto_named,
                "single_pin": net.single_pin,
            }
            if len(name_sources) > 1:
                net_data["name_sources"] = self._copy_net_name_sources(name_sources)
            nets_data.append(net_data)
        return nets_data

    def _build_schematic_hierarchy_data(
        self,
        netlist: Netlist | None,
        *,
        compiled: AltiumCompiledDesign | None = None,
    ) -> dict:
        """
        Build schematic hierarchy JSON for the design payload.
        """
        hierarchy = getattr(netlist, "schematic_hierarchy", None)
        if isinstance(hierarchy, dict) and hierarchy:
            return hierarchy
        if compiled is not None:
            from .altium_compiled_design_netlist import (
                compiled_design_schematic_hierarchy,
            )

            return compiled_design_schematic_hierarchy(compiled, self.schdocs)

        from .altium_netlist_options import NetlistOptions

        options = self._options or NetlistOptions()
        effective_scope = self._resolve_design_effective_scope(options)
        return {
            "schema": SCHEMATIC_HIERARCHY_SCHEMA,
            "requested_scope": options.net_identifier_scope.name,
            "effective_scope": effective_scope,
            "documents": [
                {
                    "sheet_index": idx,
                    "filename": schdoc.filepath.name
                    if schdoc.filepath
                    else f"sheet{idx}",
                    "path": str(schdoc.filepath) if schdoc.filepath else "",
                    "is_top_level": True,
                    "metadata": {},
                }
                for idx, schdoc in enumerate(self.schdocs)
            ],
            "sheet_symbols": [],
            "hierarchy_paths": [],
            "channels": [],
            "links": [],
            "unresolved": [],
        }

    def _resolve_design_effective_scope(
        self,
        options: "NetlistOptions",
        *,
        sources: Sequence[AltiumSchDoc | _CompilerDocumentSource] | None = None,
    ) -> str:
        """
        Resolve automatic scope for hierarchy metadata fallback payloads.
        """
        from .altium_prjpcb import NetIdentifierScope
        from ._compiler_source import _compiler_document_source

        scope = options.net_identifier_scope
        if scope != NetIdentifierScope.AUTOMATIC:
            return scope.name

        if sources is None:
            sources = [_compiler_document_source(schdoc) for schdoc in self.schdocs]

        has_sheet_entries = any(
            sheet_symbol.entries
            for schdoc in sources
            for sheet_symbol in schdoc.get_sheet_symbols()
        )
        if has_sheet_entries:
            return NetIdentifierScope.HIERARCHICAL.name
        if any(schdoc.get_ports() for schdoc in sources):
            return NetIdentifierScope.FLAT.name
        return NetIdentifierScope.GLOBAL.name

    def _build_pnp_data(self) -> dict | None:
        """
        Build optional PCB-backed pick-and-place data for design JSON.
        """
        pcbdoc_paths = self.get_pcbdoc_paths()
        if not pcbdoc_paths:
            return None

        position_mode = PNP_POSITION_MODE_ALTIUM_PICK_PLACE
        placements = self.to_pnp(units="mm", position_mode=position_mode)
        source_path = self._pcbdoc.filepath if self._pcbdoc else pcbdoc_paths[0]
        source_name = Path(source_path).name if source_path else pcbdoc_paths[0].name
        return {
            "units": "mm",
            "position_mode": position_mode,
            "source_pcbdoc": source_name,
            "placements": [entry.to_json() for entry in placements],
        }

    def _build_component_data_map(
        self,
        netlist: Netlist,
        compiled: AltiumCompiledDesign | None = None,
    ) -> dict[str, dict]:
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

        if compiled is not None:
            logical_by_id = {
                document.id: document for document in compiled.logical_documents
            }
            physical_by_id = {
                document.id: document for document in compiled.physical_documents
            }
            compiled_by_designator: dict[str, AltiumCompiledComponent] = {}
            for component in compiled.components:
                designator = (
                    component.display_designator or component.physical_designator
                )
                if not component.include_in_netlist or not designator:
                    continue
                # Match compiled_design_to_netlist: the latest admitted row
                # supplies the value while retaining the first dict position.
                compiled_by_designator[designator] = component
            for designator, component in compiled_by_designator.items():
                if designator in result:
                    physical = physical_by_id.get(component.physical_document_id)
                    logical = logical_by_id.get(component.logical_document_id)
                    result[designator].update(
                        {
                            "sheet": physical.file_name
                            if physical is not None
                            else logical.file_name
                            if logical is not None
                            else "",
                            "pin_count": component.pin_count,
                            "svg_id": component.source_object_id,
                            "source_unique_id": component.source_object_id,
                            "source_unique_id_path": component.source_unique_id_path,
                            "logical_designator": component.logical_designator,
                            "physical_designator": component.physical_designator,
                            "physical_sheet_id": component.physical_document_id,
                            "compiled_component_id": component.id,
                        }
                    )
                    continue
                physical = physical_by_id.get(component.physical_document_id)
                logical = logical_by_id.get(component.logical_document_id)
                result[designator] = {
                    "sheet": physical.file_name
                    if physical is not None
                    else logical.file_name
                    if logical is not None
                    else "",
                    "pin_count": component.pin_count,
                    "svg_id": component.source_object_id,
                    "source_unique_id": component.source_object_id,
                    "source_unique_id_path": component.source_unique_id_path,
                    "logical_designator": component.logical_designator,
                    "physical_designator": component.physical_designator,
                    "physical_sheet_id": component.physical_document_id,
                    "compiled_component_id": component.id,
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

    @staticmethod
    def _source_component_rendered_pin_svg_ids(
        source_component: object,
    ) -> dict[str, str]:
        source_record = getattr(source_component, "record", source_component)
        source_values = (source_component, source_record)
        show_hidden_pins = any(
            _has_truthy_attribute(value, ("show_hidden_pins",))
            for value in source_values
        )
        pin_svg_ids: dict[str, str] = {}
        for pin in getattr(source_component, "pins", ()) or ():
            pin_record = getattr(pin, "record", pin)
            pin_values = (pin, pin_record)
            hidden = any(
                _has_truthy_attribute(value, ("hidden", "is_hidden"))
                for value in pin_values
            )
            if hidden and not show_hidden_pins:
                continue
            designator = _first_text_attribute(pin_values, "designator")
            unique_id = _first_text_attribute(pin_values, "unique_id")
            if designator and unique_id:
                pin_svg_ids[designator] = unique_id
        return pin_svg_ids

    @staticmethod
    def _compiled_page_net_graphical(items: Sequence[object]) -> dict:
        pins: list[dict] = []
        labels: list[str] = []
        power_ports: list[str] = []
        ports: list[str] = []
        sheet_entries: list[str] = []
        object_ids: list[str] = []
        for item in items:
            object_id = str(
                getattr(item, "element_id", "") or getattr(item, "object_id", "") or ""
            )
            if not object_id:
                continue
            object_ids.append(object_id)
            kind = str(getattr(item, "kind", "") or "")
            if kind == "pin":
                pins.append(
                    {
                        "designator": str(getattr(item, "designator", "") or ""),
                        "pin": str(getattr(item, "pin", "") or ""),
                        "svg_id": object_id,
                    }
                )
            elif kind == "net_label":
                labels.append(object_id)
            elif kind == "power_port":
                power_ports.append(object_id)
            elif kind in {"port", "harness_port"}:
                ports.append(object_id)
            elif kind in {"sheet_entry", "harness_entry"}:
                sheet_entries.append(object_id)
        return {
            "pins": sorted(
                pins,
                key=lambda item: (
                    item.get("designator", ""),
                    item.get("pin", ""),
                    item.get("svg_id", ""),
                ),
            ),
            "labels": sorted(_unique_nonempty_strings(labels)),
            "power_ports": sorted(_unique_nonempty_strings(power_ports)),
            "ports": sorted(_unique_nonempty_strings(ports)),
            "sheet_entries": sorted(_unique_nonempty_strings(sheet_entries)),
            "object_ids": sorted(_unique_nonempty_strings(object_ids)),
        }

    @staticmethod
    def _compiled_net_name_sources(compiled_net: object) -> list[dict]:
        rows: list[dict] = []
        seen: set[tuple[str, str, str, str, str, str]] = set()

        def add(
            *,
            name: str,
            role: str,
            source: str,
            physical_document_id: str = "",
            object_id: str = "",
            graphical_id: str = "",
        ) -> None:
            clean = str(name or "").strip()
            if not clean:
                return
            key = (
                clean,
                role,
                source,
                physical_document_id,
                object_id,
                graphical_id,
            )
            if key in seen:
                return
            seen.add(key)
            row: dict[str, object] = {
                "name": clean,
                "role": role,
                "source": source,
            }
            if physical_document_id:
                row["physical_document_id"] = physical_document_id
            if object_id:
                row["object_id"] = object_id
            if graphical_id:
                row["graphical_id"] = graphical_id
            rows.append(row)

        winner = str(getattr(compiled_net, "name", "") or "")
        add(name=winner, role="winner", source="compiled_net")
        original_name = str(getattr(compiled_net, "original_name", "") or "")
        add(name=original_name, role="alias", source="original_name")
        override_name = str(getattr(compiled_net, "override_name", "") or "")
        add(name=override_name, role="alias", source="override_name")
        for alias in getattr(compiled_net, "aliases", ()) or ():
            add(name=str(alias), role="alias", source="compiled_alias")
        net_items = [*(getattr(compiled_net, "items", ()) or ())]
        for item in net_items:
            add(
                name=str(getattr(item, "name", "") or ""),
                role="candidate",
                source=str(getattr(item, "kind", "") or "net_item"),
                physical_document_id=str(
                    getattr(item, "physical_document_id", "") or ""
                ),
                object_id=str(getattr(item, "object_id", "") or ""),
                graphical_id=str(
                    getattr(item, "element_id", "")
                    or getattr(item, "object_id", "")
                    or ""
                ),
            )
        for endpoint in getattr(compiled_net, "endpoints", ()) or ():
            add(
                name=str(getattr(endpoint, "name", "") or ""),
                role="candidate",
                source=str(getattr(endpoint, "role", "") or "endpoint"),
                object_id=str(getattr(endpoint, "object_id", "") or ""),
                graphical_id=str(
                    getattr(endpoint, "element_id", "")
                    or getattr(endpoint, "object_id", "")
                    or ""
                ),
            )
        return rows

    @staticmethod
    def _design_net_aliases(
        net_name: str,
        aliases: list[str],
        name_sources: list[dict],
    ) -> list[str]:
        values = list(aliases)
        for source in name_sources:
            source_name = str(source.get("name") or "")
            if source_name != net_name:
                values.append(source_name)
        return sorted(_unique_nonempty_strings(values))

    @staticmethod
    def _copy_net_name_sources(name_sources: list[dict]) -> list[dict]:
        return [dict(row) for row in name_sources]

    @staticmethod
    def _design_net_aliases_cached(
        cache: dict[tuple[str, str, tuple[str, ...]], list[str]],
        net_id: str,
        net_name: str,
        aliases: list[str],
        name_sources: list[dict],
    ) -> list[str]:
        key = (
            str(net_id or ""),
            str(net_name or ""),
            tuple(str(alias) for alias in aliases),
        )
        cached = cache.get(key)
        if cached is not None:
            return list(cached)
        value = AltiumDesign._design_net_aliases(net_name, aliases, name_sources)
        cache[key] = value
        return list(value)

    def _build_sheets_data(self, options: NetlistOptions) -> list[dict]:
        """
        Build sheet information for JSON output.
        """
        sheet_numbers = self._resolve_sheet_numbers(options)
        sheets = []
        for idx, schdoc in enumerate(self.schdocs):
            filename = schdoc.filepath.name if schdoc.filepath else f"sheet{idx}"
            sheet_number = sheet_numbers.get(filename) or str(idx + 1)
            sheets.append(
                {
                    "filename": filename,
                    "sheet_number": _design_json_sheet_number_value(sheet_number),
                }
            )
        return sheets

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
            "current_variant": self.project.get_current_variant(),
            "parameters": dict(sorted(self.project.parameters.items())),
        }

    def _active_variant_name(self) -> str | None:
        if not self.project:
            return None
        managed_reader = getattr(self.project, "_managed_current_variant", None)
        if callable(managed_reader):
            managed = managed_reader()
            if isinstance(managed, str) and managed:
                return managed
        return self.project.get_current_variant()

    def _variant_data(self, variant_name: str | None) -> dict[str, object] | None:
        if not self.project or not variant_name:
            return None
        managed_reader = getattr(self.project, "_managed_variant_data", None)
        if callable(managed_reader):
            managed = managed_reader(variant_name)
            if isinstance(managed, dict):
                return managed
        return self.project.variants.get(variant_name)

    @staticmethod
    def _component_variation(
        variant_data: dict[str, object] | None,
        *,
        logical_unique_id: str,
        unique_id_path: str,
        physical_designator: str,
        variant_index: _PhysicalVariantIndex | None = None,
    ) -> Mapping[str, object] | None:
        if not variant_data or not logical_unique_id:
            return None
        if variant_index is not None:
            return variant_index.select(
                logical_unique_id=logical_unique_id,
                unique_id_path=unique_id_path,
                physical_designator=physical_designator,
            )
        return _selected_physical_variation(
            variant_data,
            logical_unique_id=logical_unique_id,
            unique_id_path=unique_id_path,
            physical_designator=physical_designator,
        )

    @classmethod
    def _component_is_dnp(
        cls,
        variant_data: dict[str, object] | None,
        *,
        logical_unique_id: str,
        unique_id_path: str,
        physical_designator: str,
        variant_index: _PhysicalVariantIndex | None = None,
    ) -> bool:
        variation = cls._component_variation(
            variant_data,
            variant_index=variant_index,
            logical_unique_id=logical_unique_id,
            unique_id_path=unique_id_path,
            physical_designator=physical_designator,
        )
        return variation is not None and _variant_field(variation, "Kind") == "1"

    def _build_variants_data(self) -> list[dict]:
        """
        Build variants information for JSON output.

                Returns:
                    List of variant dicts with 'name' and 'dnp' (list of designators).
                    Empty list if no project or no variants defined.
        """
        if not self.project or not self.project.variants:
            return []

        current_variant = self._active_variant_name()
        variants_list = []
        for variant_name, variant_data in self.project.variants.items():
            dnp_designators = sorted(_variant_dnp_designators(variant_data))

            variant_entry = {
                "name": variant_name,
                "is_current": variant_name == current_variant,
                "dnp": dnp_designators,
            }
            for key in ("variations", "parameters", "param_variations"):
                values = variant_data.get(key, [])
                if values:
                    if key == "variations":
                        values = [
                            {
                                **value,
                                **(
                                    {"AlternatePart": ""}
                                    if str(value.get("Kind", "")) != "2"
                                    else {}
                                ),
                            }
                            for value in values
                        ]
                    variant_entry[key] = _sorted_json_rows(values)
            parameter_overrides = _coerce_variant_parameter_overrides(variant_data)
            if parameter_overrides:
                variant_entry["parameter_overrides"] = _sorted_parameter_overrides(
                    parameter_overrides
                )
            variants_list.append(variant_entry)

        return variants_list

    def _enrich_component(
        self,
        comp: NetlistComponent,
        sheet: str,
        pin_count: int,
        svg_id: str,
        component_context: dict | None = None,
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

        context = component_context or {}
        return {
            "designator": comp.designator,
            "logical_designator": context.get("logical_designator", comp.designator),
            "physical_designator": context.get("physical_designator", comp.designator),
            "physical_sheet_id": context.get("physical_sheet_id", ""),
            "ambiguous_physical_designator": bool(
                context.get("ambiguous_physical_designator", False)
            ),
            "source_unique_id": context.get("source_unique_id", svg_id),
            "source_unique_id_path": context.get("source_unique_id_path", ""),
            "compiled_component_id": context.get("compiled_component_id", ""),
            "svg_id": svg_id,
            "value": comp.value,
            "footprint": comp.footprint,
            "library_ref": comp.library_ref,
            "description": comp.description,
            "dnp": bool(context.get("dnp", False)),
            "fitted": bool(context.get("fitted", True)),
            "hierarchy": hierarchy.to_json(),
            "classification": classification.to_json(),
            "parameters": dict(sorted(comp.parameters.items())),
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

    @staticmethod
    def _build_component_svg_indexes(
        components_data: list[dict],
        graph: "AltiumCompiledSchematicGraph",
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        svg_to_components: dict[str, list[str]] = {}
        for comp_data in components_data:
            svg_id = comp_data.get("svg_id", "")
            designator = str(comp_data.get("designator", ""))
            if svg_id:
                svg_to_components.setdefault(str(svg_id), []).append(designator)

        for svg_id, designator in _graph_component_svg_index_rows(graph):
            svg_to_components.setdefault(svg_id, []).append(designator)

        svg_to_component = {
            svg_id: designators[0]
            for svg_id, designators in svg_to_components.items()
            if len(_unique_nonempty_strings(designators)) == 1
        }
        return svg_to_component, svg_to_components

    @staticmethod
    def _build_net_component_indexes(
        nets_data: list[dict],
    ) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
        component_to_nets: dict[str, set[str]] = {}
        net_to_components: dict[str, list[str]] = {}
        for net in nets_data:
            net_name = str(net.get("name") or "")
            if not net_name:
                continue
            designators: set[str] = set()
            for terminal in net.get("terminals", []):
                if not isinstance(terminal, dict):
                    continue
                designator = str(terminal.get("designator") or "")
                if not designator:
                    continue
                designators.add(designator)
                component_to_nets.setdefault(designator, set()).add(net_name)
            net_to_components[net_name] = sorted(designators)

        return component_to_nets, net_to_components

    @staticmethod
    def _sorted_unique_mapping(
        values: Mapping[str, Iterable[str]],
    ) -> dict[str, list[str]]:
        return {
            key: sorted(_unique_nonempty_strings(tuple(value)))
            for key, value in sorted(values.items())
        }

    def _build_indexes(
        self,
        components_data: list[dict],
        nets_data: list[dict],
        graph: "AltiumCompiledSchematicGraph",
    ) -> dict:
        """
        Build pre-computed lookup indexes.
        """
        svg_to_component, svg_to_components = self._build_component_svg_indexes(
            components_data,
            graph,
        )
        component_to_nets, net_to_components = self._build_net_component_indexes(
            nets_data
        )
        return {
            "svg_to_component": svg_to_component,
            "svg_to_components": self._sorted_unique_mapping(svg_to_components),
            "component_to_nets": self._sorted_unique_mapping(component_to_nets),
            "net_to_components": dict(sorted(net_to_components.items())),
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
        self._compiled_design = None
        return self.to_netlist()

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def components(self) -> list[SchComponentInfo]:
        """
        All components across all schematics.
        """
        self._require_schematic_capability("schematic component queries")
        result = []
        for schdoc in self.schdocs:
            result.extend(schdoc.get_components())
        return result

    def get_component(self, designator: str) -> SchComponentInfo | None:
        """
        Find component by designator across all schematics.
        """
        self._require_schematic_capability("schematic component queries")
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

    def to_bom_payload(self, variant: str | None = None) -> SchematicBomPayload:
        """Return a versioned BOM payload projected from compiled components."""
        from .altium_common_enums import ComponentKind
        from .altium_component_kind import component_kind_includes_in_bom
        from .altium_schematic_bom import (
            SCHEMATIC_BOM_SCHEMA,
            SchematicBomPayload,
        )
        from .altium_schematic_contract import (
            SchematicContractError,
            SchematicContractLimits,
        )
        from .sch_compiled_design.generated.models import (
            SchematicBomA0,
            SchematicBomA0Component,
            SchematicSourcePage,
        )

        variant_data, parameter_overrides = self._bom_payload_variant(variant)
        compiled = self.compile()
        variant_index = _PhysicalVariantIndex(variant_data)
        physical_by_id = {
            document.id: document for document in compiled.physical_documents
        }
        project_parameters = self.project.parameters if self.project else None
        rows: list[SchematicBomA0Component] = []
        for source_index, component in enumerate(compiled.components):
            path = f"/components/{source_index}"
            try:
                kind = ComponentKind(component.component_kind_value)
            except ValueError as error:
                raise SchematicContractError(
                    "invariant", f"{path}/component_kind", "unknown component kind"
                ) from error
            included = component_kind_includes_in_bom(kind)
            if component.exclude_from_bom == included:
                raise SchematicContractError(
                    "invariant",
                    f"{path}/exclude_from_bom",
                    "compiled BOM exclusion disagrees with component kind",
                )
            designator = component.display_designator or component.physical_designator
            if not included or not designator:
                continue
            physical = physical_by_id.get(component.physical_document_id)
            if physical is None:
                raise SchematicContractError(
                    "invariant",
                    f"{path}/source_page",
                    "compiled component references an unknown physical document",
                )
            variation = self._component_variation(
                variant_data,
                variant_index=variant_index,
                logical_unique_id=component.source_object_id,
                unique_id_path=component.source_unique_id_path,
                physical_designator=component.physical_designator,
            )
            values = _bom_component_values(
                base_parameters=dict(component.parameters),
                base_value=component.value,
                base_description=component.description,
                base_footprint=component.footprint,
                variation=variation,
                parameter_overrides=parameter_overrides,
                project_parameters=project_parameters,
            )
            rows.append(
                SchematicBomA0Component(
                    component_id=component.id,
                    designator=designator,
                    logical_designator=component.logical_designator or None,
                    physical_designator=component.physical_designator or None,
                    source_page=SchematicSourcePage(
                        physical_document_id=physical.id,
                        source_sheet_file=physical.file_name,
                    ),
                    value=values.value,
                    footprint=values.footprint,
                    library_ref=component.lib_reference,
                    description=values.description,
                    parameters=values.parameters,
                    fitted=not values.dnp,
                    dnp=values.dnp,
                )
            )
        dto = SchematicBomA0(
            schema=SCHEMATIC_BOM_SCHEMA,
            generator="altium_monkey",
            selected_variant=variant,
            components=rows,
        )
        return SchematicBomPayload._from_dto(dto, SchematicContractLimits())

    def _bom_payload_variant(
        self, variant: str | None
    ) -> tuple[dict[str, object] | None, dict[str, dict[str, str]]]:
        from .altium_schematic_contract import SchematicContractError

        if variant is None:
            return None, {}
        if not isinstance(variant, str) or not variant or self.project is None:
            raise SchematicContractError(
                "variant_not_found",
                "/selected_variant",
                "variant must name an exact project variant",
            )
        available = self.project.variants
        if variant not in available:
            raise SchematicContractError(
                "variant_not_found",
                "/selected_variant",
                f"project variant {variant!r} was not found",
            )
        variant_data = self._variant_data(variant)
        if variant_data is None:
            raise SchematicContractError(
                "variant_not_found",
                "/selected_variant",
                f"project variant {variant!r} could not be read",
            )
        return variant_data, _coerce_variant_parameter_overrides(available[variant])

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
        compiled = self.compile()
        netlist = self.to_netlist()
        comp_data_map = self._build_component_data_map(netlist, compiled)

        # Get DNP list for this variant (if specified)
        parameter_overrides: dict[str, dict[str, str]] = {}
        variant_data: dict[str, object] | None = None
        if variant and self.project:
            variant_data = self._variant_data(variant)
            parameter_overrides = self.get_variant_parameter_overrides(variant)

        variant_index = _PhysicalVariantIndex(variant_data)
        result = []
        for comp in netlist.components:
            # GRAPHICAL, NET_TIE_NO_BOM, and STANDARD_NO_BOM are excluded.
            if comp.exclude_from_bom:
                continue

            component_context = comp_data_map.get(comp.designator, {})
            variation = self._component_variation(
                variant_data,
                variant_index=variant_index,
                logical_unique_id=str(component_context.get("source_unique_id") or ""),
                unique_id_path=str(
                    component_context.get("source_unique_id_path") or ""
                ),
                physical_designator=comp.designator,
            )
            values = _bom_component_values(
                base_parameters=comp.parameters,
                base_value=comp.value,
                base_description=comp.description,
                base_footprint=comp.footprint,
                variation=variation,
                parameter_overrides=parameter_overrides,
                project_parameters=self.project.parameters if self.project else None,
            )

            # Use parameters from the compiled netlist component rather than
            # reaching back into the source SchDoc.
            component_data = {
                "designator": comp.designator,
                "value": values.value,
                "footprint": values.footprint,
                "library_ref": comp.library_ref,
                "description": values.description,
                "sheet": component_context.get("sheet", ""),
                "parameters": values.parameters,
                "dnp": values.dnp,
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

    def get_variant_parameter_overrides(
        self, variant: str | None = None
    ) -> dict[str, dict[str, str]]:
        """
        Get active variant parameter overrides grouped by source designator.

        Args:
            variant: Variant name to resolve. If omitted, use the project's
                current variant.

        Returns:
            Mapping of designator -> parameter name -> variant value. Empty
            when there is no project, no active variant, or no overrides.
        """
        if not self.project:
            return {}
        variant_name = variant or self.project.get_current_variant()
        if not variant_name:
            return {}
        variant_data = self.project.variants.get(variant_name)
        if not variant_data:
            return {}
        return _coerce_variant_parameter_overrides(variant_data)

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

        log.debug(f"Loading PcbDoc: {target_path.name}")
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
        position_mode: PnpPositionMode | str = PNP_POSITION_MODE_ALTIUM_PICK_PLACE,
    ) -> list[PnpEntry]:
        """
        Generate Pick-and-Place data from PCB.

                PcbDoc data is loaded on demand so BOM-only workflows do not pay the
                parse cost of the PCB file. The default ``altium-pick-place``
                position mode matches Altium's Pick Place export: it uses the
                center of the bounding box of component-owned pad anchor points
                and falls back to the component origin when no pads exist.
                ``component-origin`` returns the footprint placement origin
                directly.

                Args:
                    variant: If specified, filter components by variant (DNP handling).
                            If None, returns all components.
                    units: Position units - "mm" (default) or "mils"
                    exclude_no_bom: If True, exclude STANDARD_NO_BOM/GRAPHICAL components.
                                   Default False because PnP may need mechanical placements.
                    position_mode: "altium-pick-place" (default) or
                                   "component-origin".

                Returns:
                    List of PnpEntry objects with position/rotation data.

                Raises:
                    ValueError: If no PcbDoc found in project.
        """
        self._require_schematic_capability("pick-and-place generation")

        from .altium_component_kind import component_kind_includes_in_bom
        from .altium_common_enums import ComponentKind
        from .altium_netlist_model import PnpEntry

        # Lazy-load PcbDoc on first call
        pcbdoc = self._get_or_load_pcbdoc()

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

        normalized_position_mode = normalize_pnp_position_mode(position_mode)
        result: list[PnpEntry] = []
        for component_index, pcb_comp in enumerate(pcbdoc.components):
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

            # Calculate the selected Pick Place position relative to the board origin.
            x_mils, y_mils = pcbdoc.get_component_pnp_position_mils(
                component_index,
                position_mode=normalized_position_mode,
            )

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
                parameters={
                    str(key): str(value)
                    for key, value in (
                        sch_comp.parameters if sch_comp else (pcb_comp.parameters or {})
                    ).items()
                },
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
