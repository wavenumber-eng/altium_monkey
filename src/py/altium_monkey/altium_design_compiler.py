"""Build compiled schematic design models."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import cmp_to_key
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from ._compiler_source import _compiler_document_source
from .altium_record_sch__sheet_symbol import _repeat_name_int32
from ._logical_source_identity import (
    _logical_source_basename,
    _logical_source_identity_key,
    _normalize_project_document_identity,
)
from ._harness_definitions import (
    HarnessDefinitionCandidate,
    HarnessDefinitionEntry,
    ResolvedHarnessDefinition,
    ResolvedHarnessMember,
    resolve_harness_definitions,
    resolve_project_harness_definitions,
)
from .altium_annotation import (
    AnnotationFile,
    DesignatorAnnotation,
    SheetNumberAnnotation as _SheetNumberAnnotation,
    load_project_annotation,
)
from .altium_compiled_design_model import (
    COMPILED_DESIGN_GENERATOR,
    COMPILED_DESIGN_SCHEMA,
    AltiumCompileDiagnostic,
    AltiumCompiledAnnotationState,
    AltiumCompiledComponent,
    AltiumCompiledDesign,
    AltiumCompiledLogicalDocument,
    AltiumCompiledNet,
    AltiumCompiledNetEndpoint,
    AltiumCompiledNetItem,
    AltiumCompiledNetTerminal,
    AltiumCompiledPhysicalDocument,
    AltiumCompiledPhysicalSheetSymbol,
    AltiumCompiledSheetSymbol,
    AltiumProjectCompileOptions,
)
from .altium_dotnet_ordinal import (
    dotnet_ordinal_ignore_case_key,
    dotnet_ordinal_ignore_case_sort_key,
    dotnet_utf16_units,
    dotnet_trim,
)
from .altium_managed_alpha_numeric import (
    managed_alpha_numeric_compare,
    managed_designator_prefix,
)
from .altium_managed_collation import managed_en_us_compare
from .altium_component_kind import (
    component_kind_includes_in_bom,
    component_kind_includes_in_netlist,
)
from .altium_netlist_common import (
    _altium_net_total_sort_key,
    _component_part_alpha_suffix,
    _evaluate_altium_expression,
    _natural_sort_key as _natural_sort_key,
    _pin_electrical_to_pintype,
    _resolve_component_display_value,
    _sheet_entry_display_name as _inter_sheet_entry_display_name,
)
from .altium_compiled_design_support import (
    _compiler_source_rows,
    _compiler_entry_rows,
    _compiler_harness_entries,
    _compiler_hidden_net_name,
    _compiler_sheet_symbol_sources,
    RoomDetails,
    _build_port_location_map,
    _build_room_details,
    _build_wire_endpoint_map,
    _compiler_implemented_part_counts,
    _compiler_component_sources,
    _connected_signal_harness_indexes,
    _format_part_physical_designator,
    _harness_connector_master_entry_point,
    _harness_entry_connection_point,
    _parse_entry_repeat,
    _point_on_signal_harness,
    _signal_harness_ids,
    apply_channel_pattern,
    find_harness_bundle_info,
    find_harness_port_name,
)
from .altium_netlist_wire_connectivity import (
    ALTIUM_COORD_SCALE,
    AnalyserNetItemKind,
    RootPoint,
    altium_internal_tolerance_for_display_unit,
    analyser_net_item_kind,
    build_wire_graph as _build_wire_graph,
    precise_points_connected,
)
from .altium_netlist_model import NetEndpoint, _SignalHarnessNameCandidate
from .altium_netlist_options import NetlistOptions
from .altium_netlist_single_sheet import (
    AltiumNetlistSingleSheetCompiler,
    _managed_name_priority,
    _parse_bus_range as _parse_managed_bus_range,
    _sheet_entry_precise_connection_point,
    _unique_sheet_entry_element_id,
)
from .altium_sch_display_mode import (
    _pin_owner_part_id_for_component_view,
    pin_belongs_to_component_view,
    pin_is_managed_part_member,
    pin_is_runtime_hidden,
)
from .altium_prjpcb import ChannelRoomNamingStyle, NetIdentifierScope
from .altium_sch_record_helpers import (
    _effective_basic_entry_distance_frac1,
    _coord_scalar_to_rounded_native_units,
    _coord_scalar_with_basic_entry_distance_to_rounded_native_units,
)

if TYPE_CHECKING:
    from .altium_design import AltiumDesign
    from .altium_prjpcb import AltiumPrjPcb
    from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
    from .altium_record_sch__sheet_entry import AltiumSchSheetEntry
    from .altium_schdoc import AltiumSchDoc
    from .altium_schdoc_info import (
        SchComponentInfo,
        SchPinInfo,
        SchPortInfo,
        SchPowerPortInfo,
        SchSheetSymbolInfo,
    )

_UNRESOLVED_CHILD_ERROR_PHASE = "python-physical-tree-instantiation"
_DEFAULT_CHANNEL_DESIGNATOR_FORMAT = "$Component"


def _basic_entry_axis_coordinate(
    location: int,
    location_frac: int,
    entry: object,
    *,
    direction: int,
) -> int:
    distance_from_top = getattr(entry, "distance_from_top", None)
    if distance_from_top is not None:
        return _coord_scalar_with_basic_entry_distance_to_rounded_native_units(
            location,
            location_frac,
            distance_from_top,
            _effective_basic_entry_distance_frac1(entry),
            direction=direction,
        )
    distance_fn = getattr(entry, "_rounded_distance_from_top_native_units", None)
    distance = (
        int(cast(int | float | str, distance_fn())) if callable(distance_fn) else 0
    )
    return _coord_scalar_to_rounded_native_units(
        location + direction * distance,
        location_frac,
    )


@dataclass(frozen=True, slots=True)
class _PhysicalChildInstance:
    instance_key: str
    path_segment: str
    sheet_symbol_designator: str
    channel_index: int
    channel_prefix: str | None
    channel_alpha: str | None
    room_name: str
    sheet_number: str | None
    document_number: str | None
    managed_repeat_channel_value: int | None = None


@dataclass(frozen=True, slots=True)
class _HarnessWireEndpoint:
    wire_id: str
    role: str
    name: str
    element_id: str
    object_id: str
    parent_id: str
    connection_point: tuple[int, int]
    source_occurrence_id: str = ""
    harness_entries_path: tuple[str, ...] = ()
    harness_type_name: str = ""
    harness_interface_name: str = ""


@dataclass(frozen=True, slots=True)
class _SheetEntryWireEndpoint:
    wire_id: str
    name: str
    element_id: str
    object_id: str
    parent_id: str
    connection_point: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _CompiledComponentSourceInfo:
    source_index: int
    component: "SchComponentInfo"
    components: tuple["SchComponentInfo", ...]
    include_in_netlist: bool
    logical_designator: str
    part_count: int
    current_part_id: int
    value: str
    parameters: tuple[tuple[str, str], ...]
    kind_value: int
    footprint: str
    component_kind: str
    pin_count: int
    all_pin_count: int
    exclude_from_bom: bool
    description: str
    library_ref: str
    design_item_id: str
    unique_id: str
    cross_document_multipart: bool


@dataclass(frozen=True, slots=True)
class _CompiledManagedPinRow:
    physical_part_designator: str
    designator: str
    component_part_id: int
    inferred: bool
    local_signal_id: str | None
    global_signal_id: str | None
    owner_document_name: str
    owner_physical_room_name: str
    location: tuple[int, int]
    source_component_uid: str
    source_pin_uid: str
    source_pin_object_id: int
    source_index: int


@dataclass(frozen=True, slots=True)
class _CompiledRawPinRow:
    designator: str
    owner_part_id: int
    owner_part_display_mode: int
    location: tuple[int, int]
    source_pin_uid: str
    source_index: int


@dataclass(frozen=True, slots=True)
class _CompiledComponentPinEvidence:
    current_part_id: int
    subparts_count: int
    display_mode: int
    display_mode_count: int
    owner_document_name: str
    source_component_uid: str
    active_pins: tuple[_CompiledManagedPinRow, ...]
    raw_pins: tuple[_CompiledRawPinRow, ...]
    physical_part_designator: str = ""
    logical_designator: str = ""


@dataclass(frozen=True, slots=True)
class _SourcePinDesignator:
    logical_designator: str
    physical_designator: str
    base_designator: str


@dataclass(frozen=True, slots=True)
class _CompiledComponentParameter:
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class _CompiledComponentExpressionView:
    comment: str
    value: str
    description: str
    parameters: tuple[_CompiledComponentParameter, ...]

    def get_parameter(self, name: str) -> str | None:
        key = dotnet_ordinal_ignore_case_key(name)
        for parameter in self.parameters:
            if dotnet_ordinal_ignore_case_key(parameter.name) == key:
                return parameter.text
        return None


def _as_posix_path(path: Path) -> str:
    return path.as_posix()


def _relative_source_path(path: Path | None, base_dir: Path | None) -> str:
    if path is None:
        return ""
    if base_dir is not None:
        try:
            return _as_posix_path(path.resolve().relative_to(base_dir.resolve()))
        except ValueError:
            pass
        except OSError:
            pass
    return _as_posix_path(path)


def _source_path_for_project(project: "AltiumPrjPcb | None") -> str | None:
    if project is None or project.filepath is None:
        return None
    return str(project.filepath)


def _saved_project_structure_top_level_file_name(
    project_path: Path | None,
) -> str | None:
    if project_path is None:
        return None
    structure_path = project_path.with_suffix(".PrjPcbStructure")
    try:
        lines = structure_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        ).splitlines()
    except OSError:
        return None
    for line in lines:
        fields: dict[str, str] = {}
        for part in line.split("|"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip().lower()] = value.strip()
        if fields.get("record", "").lower() != "topleveldocument":
            continue
        file_name = Path(fields.get("filename", "").replace("\\", "/")).name
        if file_name:
            return file_name
    return None


def _saved_project_structure_sheet_numbers(
    project_path: Path | None,
) -> dict[str, str]:
    if project_path is None:
        return {}
    structure_path = project_path.with_suffix(".PrjPcbStructure")
    try:
        lines = structure_path.read_text(
            encoding="utf-8-sig",
            errors="replace",
        ).splitlines()
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in lines:
        fields: dict[str, str] = {}
        for part in line.split("|"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key.strip().lower()] = value.strip()
        file_name = _normalize_project_reference(fields.get("filename", ""))
        sheet_number = fields.get("sheetnumber", "")
        if file_name and sheet_number:
            result[file_name] = sheet_number
    return result


def _project_saved_structure_top_level_file_name(
    project: "AltiumPrjPcb | None",
) -> str | None:
    if project is None:
        return None
    getter = getattr(project, "get_saved_structure_top_level_schdoc_name", None)
    if callable(getter):
        value = getter()
        if value:
            return str(value)
    return _saved_project_structure_top_level_file_name(
        project.filepath if project.filepath else None
    )


def _logical_document_id(source_path: str, ordinal: int) -> str:
    seed = source_path or f"document{ordinal}"
    return f"logical:{ordinal}:{seed}"


def _document_parameter_value(parameters: Mapping[str, str], name: str) -> str | None:
    key = dotnet_ordinal_ignore_case_key(name)
    result: str | None = None
    for candidate, value in parameters.items():
        if dotnet_ordinal_ignore_case_key(candidate) == key:
            result = str(value or "")
    return result


def _raw_document_parameter(schdoc: object, name: str) -> str | None:
    key = dotnet_ordinal_ignore_case_key(name)
    result: str | None = None
    records = getattr(schdoc, "_document_parameters", None)
    if callable(records):
        for parameter in cast(Callable[[], Iterable[object]], records)():
            candidate = str(getattr(parameter, "name", "") or "")
            if dotnet_ordinal_ignore_case_key(candidate) == key:
                result = str(getattr(parameter, "text", "") or "")
        return result
    get_parameter_dict = getattr(schdoc, "get_parameter_dict")
    return _document_parameter_value(get_parameter_dict(), name)


def _logical_document_ids_with_parameter(
    schdocs: Sequence["AltiumSchDoc"],
    *,
    project_base_dir: Path | None,
    name: str,
) -> frozenset[str]:
    return frozenset(
        _logical_document_id(
            _relative_source_path(getattr(schdoc, "filepath", None), project_base_dir),
            ordinal,
        )
        for ordinal, schdoc in enumerate(schdocs)
        if _has_document_parameter(schdoc, name)
    )


def _has_document_parameter(schdoc: object, name: str) -> bool:
    return _raw_document_parameter(schdoc, name) is not None


def _resolved_document_parameter(
    schdoc: object,
    name: str,
    project_parameters: dict[str, str],
) -> str | None:
    matched = _raw_document_parameter(schdoc, name)
    if matched is None:
        return None
    if matched and matched != "*":
        return matched
    for project_name, project_value in project_parameters.items():
        if dotnet_ordinal_ignore_case_key(
            project_name
        ) != dotnet_ordinal_ignore_case_key(name):
            continue
        resolved = str(project_value or "")
        return resolved if resolved and resolved != "*" else None
    return None


def _preserved_device_sheet_number(
    schdoc: object,
    logical_id: str,
    saved_numbers: Mapping[str, str] | None,
) -> str | None:
    sheet_number = (saved_numbers or {}).get(logical_id)
    if sheet_number not in {None, "*"}:
        return sheet_number
    sheet_number = _raw_document_parameter(schdoc, "SheetNumber")
    if sheet_number not in {None, "*"}:
        return sheet_number
    return None


def _sheet_numbers_by_logical_id(
    schdocs: Sequence["AltiumSchDoc"],
    *,
    project_base_dir: Path | None,
    options: AltiumProjectCompileOptions,
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    device_sheet_logical_ids: frozenset[str] = frozenset(),
    device_sheet_numbers_by_logical_id: Mapping[str, str] | None = None,
    document_order_by_logical_id: Mapping[str, int] | None = None,
) -> dict[str, str]:
    schdoc_by_logical_id: dict[str, AltiumSchDoc] = {}
    for ordinal, schdoc in enumerate(schdocs):
        source_path = _relative_source_path(
            getattr(schdoc, "filepath", None),
            project_base_dir,
        )
        logical_id = _logical_document_id(source_path, ordinal)
        schdoc_by_logical_id[logical_id] = schdoc

    if not options.auto_sheet_numbering:
        return {
            logical_id: sheet_number
            for logical_id, schdoc in schdoc_by_logical_id.items()
            if (sheet_number := _raw_document_parameter(schdoc, "SheetNumber"))
            is not None
        }

    document_by_id = {document.id: document for document in logical_documents}
    document_order = document_order_by_logical_id or {}

    def document_position(logical_id: str) -> tuple[int, int]:
        document = document_by_id[logical_id]
        fallback = (
            document.ordinal if document_order_by_logical_id is None else sys.maxsize
        )
        return document_order.get(logical_id, fallback), document.ordinal

    child_ids_by_parent: dict[str, set[str]] = defaultdict(set)
    for symbol in sheet_symbols:
        retained_child_ids = symbol._managed_retained_child_logical_document_ids
        if retained_child_ids is None:
            retained_child_ids = (
                (symbol.child_logical_document_id,)
                if symbol.child_logical_document_id is not None
                else ()
            )
        child_ids_by_parent[symbol.logical_document_id].update(retained_child_ids)

    result: dict[str, str] = {}
    visited: set[str] = set()
    next_sheet_number = 1

    def number_document(logical_id: str) -> bool:
        nonlocal next_sheet_number
        if logical_id in visited:
            return False
        visited.add(logical_id)
        schdoc = schdoc_by_logical_id.get(logical_id)
        if schdoc is not None:
            if (
                logical_id in device_sheet_logical_ids
                and not options.allow_device_sheet_editing
            ):
                sheet_number = _preserved_device_sheet_number(
                    schdoc,
                    logical_id,
                    device_sheet_numbers_by_logical_id,
                )
                if sheet_number is not None:
                    result[logical_id] = sheet_number
                return True
            if (
                document_order_by_logical_id is not None
                and logical_id not in document_order
            ):
                return True
            if _has_document_parameter(schdoc, "SheetNumber"):
                result[logical_id] = str(next_sheet_number)
            next_sheet_number += 1
        return True

    def collect_top_tree(logical_id: str) -> None:
        if not number_document(logical_id):
            return
        for child_id in sorted(
            child_ids_by_parent.get(logical_id, ()),
            key=document_position,
        ):
            collect_top_tree(child_id)

    parented_ids = {
        child_id for child_ids in child_ids_by_parent.values() for child_id in child_ids
    }
    root_documents = sorted(
        (document for document in logical_documents if document.id not in parented_ids),
        key=lambda document: document_position(document.id),
    )
    if not logical_documents:
        return result
    top_document = _primary_top_level_document(logical_documents, sheet_symbols)
    if top_document is None:
        top_document = (root_documents or list(logical_documents))[0]
    # AD26 SheetNumberingResolver seeds the flat pass with the top schematic
    # plus every schematic unreachable from it (ProjectStructureBuilder.
    # GetNoMainPathSchematicsIds), ordered by project-document position, so
    # children of detached parents are numbered flatly as well.
    reachable_ids: set[str] = set()
    pending_ids = [top_document.id]
    while pending_ids:
        current_id = pending_ids.pop()
        if current_id in reachable_ids:
            continue
        reachable_ids.add(current_id)
        pending_ids.extend(child_ids_by_parent.get(current_id, ()))
    no_main_path_documents = [
        document for document in logical_documents if document.id not in reachable_ids
    ]
    seed_documents = sorted(
        [top_document, *no_main_path_documents],
        key=lambda document: document_position(document.id),
    )
    for document in seed_documents:
        if document.id == top_document.id:
            collect_top_tree(document.id)
        else:
            number_document(document.id)
    return result


def _document_numbers_by_logical_id(
    schdocs: Sequence["AltiumSchDoc"],
    *,
    project_base_dir: Path | None,
    project_parameters: dict[str, str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for ordinal, schdoc in enumerate(schdocs):
        source_path = _relative_source_path(
            getattr(schdoc, "filepath", None),
            project_base_dir,
        )
        logical_id = _logical_document_id(source_path, ordinal)
        document_number = _resolved_document_parameter(
            schdoc,
            "DocumentNumber",
            project_parameters,
        )
        if document_number is not None:
            result[logical_id] = document_number
    return result


def _apply_default_sheet_and_document_numbers(
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    *,
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    new_indexing_of_sheet_symbols: bool,
    sheet_numbers_by_logical_id: dict[str, str],
    sheet_number_parameter_logical_ids: frozenset[str],
    document_numbers_by_logical_id: dict[str, str],
) -> tuple[AltiumCompiledPhysicalDocument, ...]:
    physical_index_paths = _physical_sheet_indices_paths(
        physical_documents,
        sheet_symbols=sheet_symbols,
        new_indexing_of_sheet_symbols=new_indexing_of_sheet_symbols,
    )
    updated_documents: list[AltiumCompiledPhysicalDocument] = []
    for document in physical_documents:
        physical_index_path = physical_index_paths[document.id]
        sheet_number_base = sheet_numbers_by_logical_id.get(
            document.logical_document_id
        )
        sheet_number = _physical_sheet_number(
            has_parameter=(
                document.logical_document_id in sheet_number_parameter_logical_ids
            ),
            base=sheet_number_base,
            physical_index_path=physical_index_path,
        )
        document_number_base = document_numbers_by_logical_id.get(
            document.logical_document_id
        )
        logical_sheet_number = (
            "" if sheet_number_base in {None, "*"} else sheet_number_base
        )
        updated_documents.append(
            replace(
                document,
                sheet_number=sheet_number,
                document_number=document_number_base or None,
                _managed_room_sheet_number=logical_sheet_number,
                _managed_room_document_number=_physical_document_number(
                    base=document_number_base,
                    physical_index_path=physical_index_path,
                ),
            )
        )
    return tuple(updated_documents)


def _physical_sheet_number(
    *,
    has_parameter: bool,
    base: str | None,
    physical_index_path: str,
) -> str | None:
    if not has_parameter:
        return None
    if physical_index_path:
        logical_value = "" if base in {None, "*"} else base
        return f"{logical_value}.{physical_index_path}"
    return None if base in {None, "", "*"} else base


def _physical_document_number(
    *,
    base: str | None,
    physical_index_path: str,
) -> str | None:
    logical_value = "" if base in {None, "*"} else base
    if logical_value and physical_index_path:
        return f"{logical_value}.{physical_index_path}"
    return logical_value or physical_index_path or None


def _physical_sheet_indices_paths(
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    *,
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    new_indexing_of_sheet_symbols: bool,
) -> dict[str, str]:
    documents_by_id = {document.id: document for document in physical_documents}
    local_indices = _channel_local_indices(documents_by_id)
    matching_symbol_counts = _matching_sheet_symbol_counts(sheet_symbols)
    sentinel = _no_channel_index(new_indexing=new_indexing_of_sheet_symbols)
    results: dict[str, str] = {}
    for document in physical_documents:
        if document.parent_id is None:
            results[document.id] = ""
            continue
        parent = documents_by_id[document.parent_id]
        results[document.id] = _physical_sheet_index_path(
            document,
            prefix=results[parent.id],
            local_index=local_indices[document.id],
            matching_symbol_count=matching_symbol_counts[
                (parent.logical_document_id, document.logical_document_id)
            ],
            sentinel=sentinel,
        )
    return results


def _matching_sheet_symbol_counts(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = defaultdict(int)
    for symbol in sheet_symbols:
        for child_id in _managed_sheet_symbol_child_ids(symbol):
            result[(symbol.logical_document_id, child_id)] += 1
    return result


def _physical_sheet_index_path(
    document: AltiumCompiledPhysicalDocument,
    *,
    prefix: str,
    local_index: int,
    matching_symbol_count: int,
    sentinel: int,
) -> str:
    index = document.channel_index
    if index == sentinel:
        index = local_index + 1
        # Managed numbering suppresses only a first-level singleton reference.
        if not prefix and matching_symbol_count <= 1:
            index = sentinel
    if index <= sentinel:
        return ""
    if prefix:
        return f"{prefix}.{index}"
    return str(index)


def _normalize_project_reference(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        normalized = _normalize_project_document_identity(raw)
    except ValueError:
        return ""
    return _logical_source_identity_key(normalized)


def _reference_basename(value: str) -> str:
    return _normalize_project_reference(value).rsplit("/", 1)[-1]


# Altium resolves sheet-symbol child references stem-to-stem: references with
# a recognized schematic extension are stripped to their stem, and
# extension-less references are first-class.
_SCHEMATIC_REFERENCE_SUFFIXES = (".schdoc", ".schdot", ".sch")


def _reference_stem(value: str) -> str:
    basename = _reference_basename(value)
    for suffix in _SCHEMATIC_REFERENCE_SUFFIXES:
        if basename.endswith(suffix):
            return basename[: -len(suffix)]
    return basename


def _sheet_symbol_id(
    logical_document_id: str, symbol: "SchSheetSymbolInfo", index: int
) -> str:
    source_object_id = symbol.unique_id or f"sheet_symbol:{index}"
    return f"{logical_document_id}:sheet_symbol:{source_object_id}"


def _physical_document_id(logical_document_id: str, ordinal: int) -> str:
    return f"physical:{ordinal}:{logical_document_id}"


def _physical_child_document_id(
    parent_id: str,
    sheet_symbol_id: str,
    instance_key: str,
) -> str:
    return f"{parent_id}:child:{sheet_symbol_id}:{instance_key}"


def _component_id(
    physical_document_id: str, component: "SchComponentInfo", index: int
) -> str:
    source_object_id = component.unique_id or f"component:{index}"
    return f"{physical_document_id}:component:{source_object_id}"


def _component_unique_id_path(
    physical_unique_id_path: str, source_object_id: str
) -> str:
    if not source_object_id:
        return ""
    return _unique_id_path_join(physical_unique_id_path, source_object_id)


def _compiled_local_net_id(logical_document_id: str, net_name: str, index: int) -> str:
    return f"{logical_document_id}:local_net:{index}:{net_name}"


def _compiled_physical_net_id(physical_document_id: str, local_net_id: str) -> str:
    return f"{physical_document_id}:physical_net:{local_net_id}"


def _compiled_inter_sheet_link_id(
    parent_physical_id: str,
    child_physical_id: str,
    source_object_id: str,
    link_name: str,
) -> str:
    return (
        f"{parent_physical_id}:link:{child_physical_id}:{source_object_id}:{link_name}"
    )


def _compiled_flat_net_id(index: int, name: str) -> str:
    return f"compiled_net:{index}:{name}"


def _component_designator_maps_by_physical_document(
    components: tuple[AltiumCompiledComponent, ...],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = defaultdict(dict)
    for component in components:
        if component.logical_designator:
            result[component.physical_document_id][component.logical_designator] = (
                component.physical_designator
            )
            for part_id in range(1, component.part_count + 1):
                suffix = _component_part_alpha_suffix(
                    part_count=component.part_count,
                    current_part_id=part_id,
                )
                if not suffix:
                    continue
                result[component.physical_document_id][
                    f"{component.logical_designator}{suffix}"
                ] = f"{component.physical_designator}{suffix}"
    return result


def _component_base_designator_maps_by_physical_document(
    components: tuple[AltiumCompiledComponent, ...],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = defaultdict(dict)
    for component in components:
        if not component.logical_designator:
            continue
        designator = component.physical_designator
        suffix = _component_full_designator_part_suffix(
            part_count=component.part_count,
            current_part_id=component.current_part_id,
        )
        if suffix and designator.endswith(suffix):
            designator = designator[: -len(suffix)]
        result[component.physical_document_id][component.logical_designator] = (
            designator
        )
        for part_id in range(1, component.part_count + 1):
            part_suffix = _component_part_alpha_suffix(
                part_count=component.part_count,
                current_part_id=part_id,
            )
            if not part_suffix:
                continue
            result[component.physical_document_id][
                f"{component.logical_designator}{part_suffix}"
            ] = designator
    return result


def _component_source_pin_designator_maps(
    components: Sequence[AltiumCompiledComponent],
    evidence_by_document: Mapping[str, Mapping[int, _CompiledComponentPinEvidence]],
) -> dict[str, dict[int, _SourcePinDesignator]]:
    """Bind each retained source part to its aggregate winner, not its label."""
    result: dict[str, dict[int, _SourcePinDesignator]] = defaultdict(dict)
    for component in components:
        full = component.physical_designator
        suffix = _component_full_designator_part_suffix(
            part_count=component.part_count,
            current_part_id=component.current_part_id,
        )
        base = full.removesuffix(suffix) if suffix else full
        for (
            physical_id,
            logical_id,
            index,
            _,
        ) in component._managed_source_component_occurrences:
            evidence = evidence_by_document.get(logical_id, {}).get(index)
            if evidence is None:
                continue
            projection = _SourcePinDesignator(evidence.logical_designator, full, base)
            for pin in evidence.active_pins:
                result[physical_id][pin.source_pin_object_id] = projection
    return result


def _source_pin_designator_map(
    designator: str,
    source_pin_object_id: int,
    by_name: dict[str, str],
    by_source_pin: Mapping[int, _SourcePinDesignator] | None,
) -> dict[str, str]:
    projection = (by_source_pin or {}).get(source_pin_object_id)
    if projection is None:
        return by_name
    result = {projection.logical_designator: projection.physical_designator}
    suffix = (
        designator.removeprefix(projection.logical_designator)
        if designator.startswith(projection.logical_designator)
        else ""
    )
    result[designator] = projection.physical_designator + suffix
    return result


def _component_full_designator_part_suffix(
    *,
    part_count: int,
    current_part_id: int,
) -> str:
    if part_count <= 1 or current_part_id <= 1:
        return ""
    return _component_part_alpha_suffix(
        part_count=part_count,
        current_part_id=current_part_id - 1,
    )


def _component_record_part_count(component: object) -> int:
    value = getattr(getattr(component, "record", None), "part_count", 1)
    return int(1 if value is None else value)


def _component_record_subparts_count(component: object) -> int:
    return _component_record_part_count(component) - 1


def _component_record_is_multipart(component: object) -> bool:
    return _component_record_subparts_count(component) > 1


def _component_record_current_part_id(component: object) -> int:
    value = getattr(getattr(component, "record", None), "current_part_id", 1)
    return int(1 if value is None else value)


def _component_design_item_id(component: object) -> str:
    return str(getattr(getattr(component, "record", None), "design_item_id", "") or "")


def _component_source_library_name(component: object) -> str:
    return str(
        getattr(getattr(component, "record", None), "source_library_name", "") or ""
    )


def _designator_binds_multipart_records(designator: str) -> bool:
    """Return whether Altium annotation can bind separate placed part records."""
    return "?" not in designator


def _local_multipart_designators(schdoc: "AltiumSchDoc") -> set[str]:
    placed_part_ids_by_designator: dict[str, set[int]] = defaultdict(set)
    for component in _compiler_component_sources(schdoc):
        logical_designator = str(component.designator or "")
        if _designator_binds_multipart_records(
            logical_designator
        ) and _component_record_is_multipart(component):
            placed_part_ids_by_designator[
                dotnet_ordinal_ignore_case_key(logical_designator)
            ].add(_component_record_current_part_id(component))
    return {
        designator
        for designator, part_ids in placed_part_ids_by_designator.items()
        if len({part_id for part_id in part_ids if part_id > 0}) > 1
    }


def _component_pin_part_suffix(pin: object, part_count: int, multipart: bool) -> str:
    if not multipart:
        return ""
    owner_part_id = _pin_owner_part_id_for_component_view(pin)
    return _component_part_alpha_suffix(
        part_count=part_count, current_part_id=owner_part_id if owner_part_id > 0 else 1
    )


def _component_pin_full_designators_by_pin(
    schdoc: "AltiumSchDoc",
    *,
    multipart_designators: set[str] | frozenset[str] | None = None,
    source_full_designators: dict[int, str] | None = None,
) -> dict[tuple[str, str], str]:
    local_multipart_designators = _local_multipart_designators(schdoc)
    effective_multipart_designators = {
        dotnet_ordinal_ignore_case_key(designator)
        for designator in (multipart_designators or ())
    } | local_multipart_designators
    result: dict[tuple[str, str], str] = {}
    for component in _compiler_component_sources(schdoc):
        logical_designator = str(component.designator or "")
        logical_designator_key = dotnet_ordinal_ignore_case_key(logical_designator)
        part_count = _component_record_subparts_count(component)
        for pin in component.pins:
            pin_number = str(getattr(pin, "designator", "") or "")
            if not pin_number:
                continue
            suffix = _component_pin_part_suffix(
                pin,
                part_count,
                logical_designator_key in effective_multipart_designators,
            )
            full_designator = f"{logical_designator}{suffix}"
            if logical_designator:
                result[(logical_designator, pin_number)] = full_designator
            if source_full_designators is not None:
                source_full_designators[id(getattr(pin, "pin", pin))] = full_designator
    return result


def _compiled_component_source_groups(
    components: tuple["SchComponentInfo", ...],
) -> tuple[tuple["SchComponentInfo", ...], ...]:
    groups: list[list[SchComponentInfo]] = []
    multipart_groups: dict[str, list[tuple[int, set[int], str]]] = defaultdict(list)
    for component in components:
        designator = str(getattr(component, "designator", "") or "")
        if not _designator_binds_multipart_records(
            designator
        ) or not _component_record_is_multipart(component):
            groups.append([component])
            continue
        part_id = _component_record_current_part_id(component)
        designator_key = dotnet_ordinal_ignore_case_key(designator)
        design_item_key = dotnet_ordinal_ignore_case_key(
            _component_design_item_id(component)
        )
        candidate_groups = multipart_groups[designator_key]
        candidate_group = next(
            (
                candidate
                for candidate in candidate_groups
                if part_id not in candidate[1] and design_item_key == candidate[2]
            ),
            None,
        )
        if candidate_group is None:
            candidate_groups.append((len(groups), {part_id}, design_item_key))
            groups.append([component])
            continue
        group_index, part_ids, _ = candidate_group
        groups[group_index].append(component)
        part_ids.add(part_id)
    return tuple(tuple(group) for group in groups)


def _compiled_component_group_definition(
    group: tuple["SchComponentInfo", ...],
) -> "SchComponentInfo":
    return min(group, key=_component_record_current_part_id)


def _compiled_component_group_identity(
    group: tuple["SchComponentInfo", ...],
) -> "SchComponentInfo":
    return max(group, key=_component_record_current_part_id)


def _compiled_component_group_footprint(
    group: tuple["SchComponentInfo", ...],
) -> str:
    return min(
        (str(getattr(component, "footprint", "") or "") for component in group),
        key=cmp_to_key(managed_alpha_numeric_compare),
    )


def _compiled_component_group_parameters(
    group: tuple["SchComponentInfo", ...],
    options: NetlistOptions | None = None,
    sheet_parameters: Mapping[str, str] | None = None,
    hierarchy_parameters: Mapping[str, str] | None = None,
) -> tuple[_CompiledComponentParameter, ...]:
    result: list[_CompiledComponentParameter] = []
    seen: set[str] = set()
    for part_index, component in enumerate(
        sorted(group, key=_component_record_current_part_id)
    ):
        _append_component_authored_parameters(result, seen, component)
        if part_index == 0:
            # PartInfo creates these entries before MultiPartInfo merges the next
            # slot, so even empty lower-slot virtuals block later authored names.
            seen.update(
                dotnet_ordinal_ignore_case_key(name)
                for name in (
                    "Comment",
                    "Description",
                    "Library Reference",
                    "Library Name",
                    "Component Kind",
                )
            )
    if options is None:
        return tuple(result)
    return _evaluate_component_parameter_map(
        group[0],
        tuple(result),
        options,
        sheet_parameters=sheet_parameters,
        hierarchy_parameters=hierarchy_parameters,
    )


def _append_component_authored_parameters(
    result: list[_CompiledComponentParameter],
    seen: set[str],
    component: "SchComponentInfo",
) -> None:
    for parameter in component.parameters:
        name = str(getattr(parameter, "name", "") or "")
        if not name:
            continue
        key = dotnet_ordinal_ignore_case_key(name)
        text = str(getattr(parameter, "text", "") or "")
        if _is_component_directive_parameter(name):
            result.append(_CompiledComponentParameter(name=name, text=text))
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(_CompiledComponentParameter(name=name, text=text))


def _evaluate_component_parameter_map(
    component: "SchComponentInfo",
    parameters: tuple[_CompiledComponentParameter, ...],
    options: NetlistOptions,
    *,
    sheet_parameters: Mapping[str, str] | None = None,
    hierarchy_parameters: Mapping[str, str] | None = None,
) -> tuple[_CompiledComponentParameter, ...]:
    values = _component_expression_values(
        component,
        parameters,
        options,
        sheet_parameters=sheet_parameters,
        hierarchy_parameters=hierarchy_parameters,
    )

    resolved: dict[str, str] = {}
    active: set[str] = set()

    def resolve(key: str) -> str:
        if key in resolved:
            return resolved[key]
        name, raw = values[key]
        if key in active:
            return raw
        if not raw.startswith("="):
            resolved[key] = raw
            return raw
        active.add(key)
        context = {
            candidate_name: resolve(candidate_key)
            for candidate_key, (candidate_name, _) in values.items()
            if candidate_key != key
        }
        active.remove(key)
        resolved[key] = _evaluate_altium_expression(
            raw[1:],
            context,
            preserve_unresolved_formula=True,
        )
        return resolved[key]

    context = {name: resolve(key) for key, (name, _) in values.items()}
    evaluated_candidates: list[_CompiledComponentParameter] = []
    for parameter in parameters:
        key = dotnet_ordinal_ignore_case_key(parameter.name)
        if not _is_component_directive_parameter(parameter.name):
            evaluated_candidates.append(
                _CompiledComponentParameter(parameter.name, resolve(key))
            )
            continue
        text = parameter.text
        if text.startswith("="):
            text = _evaluate_altium_expression(
                text[1:],
                context,
                preserve_unresolved_formula=True,
            )
        evaluated_candidates.append(_CompiledComponentParameter(parameter.name, text))

    evaluated_candidates.sort(
        key=cmp_to_key(
            lambda left, right: managed_alpha_numeric_compare(
                left.name,
                right.name,
            )
        )
    )
    evaluated: list[_CompiledComponentParameter] = []
    seen_directives: set[str] = set()
    for parameter in evaluated_candidates:
        if not _is_component_directive_parameter(parameter.name):
            evaluated.append(parameter)
            continue
        key = dotnet_ordinal_ignore_case_key(parameter.name)
        text = parameter.text
        directive_key = f"{key}\0{dotnet_ordinal_ignore_case_key(text)}"
        if directive_key in seen_directives:
            continue
        seen_directives.add(directive_key)
        evaluated.append(parameter)
    evaluated.sort(
        key=cmp_to_key(
            lambda left, right: _compiled_component_parameter_pair_compare(
                (left.name, left.text),
                (right.name, right.text),
            )
        )
    )
    return tuple(evaluated)


def _component_expression_values(
    component: "SchComponentInfo",
    parameters: tuple[_CompiledComponentParameter, ...],
    options: NetlistOptions,
    *,
    sheet_parameters: Mapping[str, str] | None,
    hierarchy_parameters: Mapping[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    values: dict[str, tuple[str, str]] = {}
    project_values: dict[str, tuple[str, str]] = {}

    def add_value(name: str, value: str) -> None:
        values[dotnet_ordinal_ignore_case_key(name)] = (name, value)

    for name, value in options.project_parameters.items():
        project_values[dotnet_ordinal_ignore_case_key(name)] = (name, value)
        add_value(name, value)
    for name, value in (hierarchy_parameters or {}).items():
        add_value(name, value)
    owning_sheet_parameters = (
        options.sheet_parameters if sheet_parameters is None else sheet_parameters
    )
    for name, value in owning_sheet_parameters.items():
        add_value(
            name,
            _recursive_component_document_parameter(
                name,
                value,
                project_values,
            ),
        )
    for name, value in (
        ("CurrentFootprint", component.footprint),
        ("VariantName", "[No Variations]"),
        ("Comment", component.comment),
        ("Description", component.description),
        ("Library Reference", _component_design_item_id(component)),
        ("Library Name", _component_source_library_name(component)),
        ("Component Kind", _component_kind_name(component)),
    ):
        add_value(name, value)
    for parameter in parameters:
        if not _is_component_directive_parameter(parameter.name):
            add_value(parameter.name, parameter.text)
    return values


def _recursive_component_document_parameter(
    name: str,
    value: str,
    project_values: Mapping[str, tuple[str, str]],
) -> str:
    if value not in {"", "*"}:
        return value
    project_value = project_values.get(dotnet_ordinal_ignore_case_key(name))
    return value if project_value is None else project_value[1]


def _evaluate_component_field(
    raw: str,
    parameters: tuple[_CompiledComponentParameter, ...],
    component: "SchComponentInfo",
    options: NetlistOptions,
    *,
    sheet_parameters: Mapping[str, str] | None = None,
    hierarchy_parameters: Mapping[str, str] | None = None,
) -> str:
    if not raw.startswith("="):
        return raw
    values = _component_expression_values(
        component,
        parameters,
        options,
        sheet_parameters=sheet_parameters,
        hierarchy_parameters=hierarchy_parameters,
    )
    context = {name: value for name, value in values.values()}
    return _evaluate_altium_expression(
        raw[1:],
        context,
        preserve_unresolved_formula=True,
    )


def _is_component_directive_parameter(name: str) -> bool:
    key = dotnet_ordinal_ignore_case_key(name)
    return key in {
        dotnet_ordinal_ignore_case_key(candidate)
        for candidate in (
            "Rule",
            "ClassName",
            "CompClassName",
            "DifferentialPair",
            "DifferentialPairClassName",
        )
    }


def _compiled_component_group_pin_count(group: tuple[object, ...]) -> int:
    return len(
        {
            str(getattr(pin, "designator", "") or "")
            for component in group
            for pin in (getattr(component, "pins", ()) or ())
        }
    )


def _compiled_component_group_all_pin_count(
    group: tuple["SchComponentInfo", ...],
) -> int:
    definition = _compiled_component_group_definition(group)
    definition_record = getattr(definition, "record", None)
    stored_count = int(getattr(definition_record, "all_pin_count", 0) or 0)
    if stored_count > 0:
        return stored_count
    return _compiled_component_group_inferred_all_pin_count(group)


def _compiled_component_parameter_pair_compare(
    left: tuple[str, str],
    right: tuple[str, str],
) -> int:
    name_comparison = managed_alpha_numeric_compare(left[0], right[0])
    if name_comparison:
        return name_comparison
    return managed_alpha_numeric_compare(left[1], right[1])


def _compiled_component_group_inferred_all_pin_count(
    group: tuple["SchComponentInfo", ...],
) -> int:
    component = _compiled_component_group_definition(group)
    pin_designators: set[str] = set()
    record = getattr(component, "record", None)
    display_mode = int(getattr(component, "display_mode", 0) or 0)
    for pin in getattr(record, "pins", ()) or ():
        pin_mode = int(getattr(pin, "owner_part_display_mode", 0) or 0)
        designator = str(getattr(pin, "designator", "") or "")
        if pin_mode == display_mode:
            pin_designators.add(designator)
    return len(pin_designators)


def _compiled_managed_pin_row_compare(
    left: _CompiledManagedPinRow,
    right: _CompiledManagedPinRow,
) -> int:
    comparison = managed_alpha_numeric_compare(
        left.physical_part_designator,
        right.physical_part_designator,
    )
    if comparison:
        return comparison
    comparison = managed_alpha_numeric_compare(left.designator, right.designator)
    if comparison:
        return comparison
    comparison = int(left.inferred) - int(right.inferred)
    if comparison:
        return comparison
    comparison = _managed_int32_subtract(
        left.component_part_id,
        right.component_part_id,
    )
    if comparison:
        return comparison
    comparison = managed_alpha_numeric_compare(
        left.owner_document_name,
        right.owner_document_name,
    )
    if comparison:
        return comparison
    comparison = managed_alpha_numeric_compare(
        left.owner_physical_room_name,
        right.owner_physical_room_name,
    )
    if comparison:
        return comparison
    if left.location[0] != right.location[0]:
        return _managed_int32_subtract(left.location[0], right.location[0])
    return _managed_int32_subtract(left.location[1], right.location[1])


_CompiledSortRow = TypeVar("_CompiledSortRow")


def _compiled_dotnet_insertion_sort(
    values: list[_CompiledSortRow],
    low: int,
    high: int,
    compare: Callable[[_CompiledSortRow, _CompiledSortRow], int],
) -> None:
    for index in range(low, high):
        value = values[index + 1]
        prior = index
        while prior >= low and compare(value, values[prior]) < 0:
            values[prior + 1] = values[prior]
            prior -= 1
        values[prior + 1] = value


def _compiled_dotnet_down_heap(
    values: list[_CompiledSortRow],
    index: int,
    count: int,
    low: int,
    compare: Callable[[_CompiledSortRow, _CompiledSortRow], int],
) -> None:
    value = values[low + index - 1]
    while index <= count // 2:
        child = 2 * index
        if child < count and compare(values[low + child - 1], values[low + child]) < 0:
            child += 1
        if compare(value, values[low + child - 1]) >= 0:
            break
        values[low + index - 1] = values[low + child - 1]
        index = child
    values[low + index - 1] = value


def _compiled_dotnet_heap_sort(
    values: list[_CompiledSortRow],
    low: int,
    high: int,
    compare: Callable[[_CompiledSortRow, _CompiledSortRow], int],
) -> None:
    count = high - low + 1
    for index in range(count // 2, 0, -1):
        _compiled_dotnet_down_heap(values, index, count, low, compare)
    for index in range(count, 1, -1):
        values[low], values[low + index - 1] = values[low + index - 1], values[low]
        _compiled_dotnet_down_heap(values, 1, index - 1, low, compare)


def _compiled_dotnet_pick_pivot(
    values: list[_CompiledSortRow],
    low: int,
    high: int,
    compare: Callable[[_CompiledSortRow, _CompiledSortRow], int],
) -> int:
    middle = low + ((high - low) // 2)
    for left, right in ((low, middle), (low, high), (middle, high)):
        if compare(values[left], values[right]) > 0:
            values[left], values[right] = values[right], values[left]
    pivot = values[middle]
    values[middle], values[high - 1] = values[high - 1], values[middle]
    left = low
    right = high - 1
    while True:
        left += 1
        while compare(values[left], pivot) < 0:
            left += 1
        right -= 1
        while compare(pivot, values[right]) < 0:
            right -= 1
        if left >= right:
            break
        values[left], values[right] = values[right], values[left]
    values[left], values[high - 1] = values[high - 1], values[left]
    return left


def _compiled_dotnet_intro_sort_range(
    values: list[_CompiledSortRow],
    low: int,
    high: int,
    depth_limit: int,
    compare: Callable[[_CompiledSortRow, _CompiledSortRow], int],
) -> None:
    while high > low:
        partition_size = high - low + 1
        if partition_size <= 16:
            if partition_size == 2:
                if compare(values[low], values[high]) > 0:
                    values[low], values[high] = values[high], values[low]
                return
            if partition_size == 3:
                for left, right in (
                    (low, high - 1),
                    (low, high),
                    (high - 1, high),
                ):
                    if compare(values[left], values[right]) > 0:
                        values[left], values[right] = values[right], values[left]
                return
            _compiled_dotnet_insertion_sort(values, low, high, compare)
            return
        if depth_limit == 0:
            _compiled_dotnet_heap_sort(values, low, high, compare)
            return
        depth_limit -= 1
        pivot = _compiled_dotnet_pick_pivot(values, low, high, compare)
        _compiled_dotnet_intro_sort_range(values, pivot + 1, high, depth_limit, compare)
        high = pivot - 1


def _compiled_dotnet_sort(
    values: list[_CompiledSortRow],
    compare: Callable[[_CompiledSortRow, _CompiledSortRow], int],
) -> None:
    """Match the non-stable introsort used by managed List<T>.Sort."""

    if len(values) > 1:
        depth_limit = 2 * len(values).bit_length()
        _compiled_dotnet_intro_sort_range(
            values, 0, len(values) - 1, depth_limit, compare
        )


def _compiled_managed_pin_sort(pins: list[_CompiledManagedPinRow]) -> None:
    _compiled_dotnet_sort(pins, _compiled_managed_pin_row_compare)


def _compiled_physical_document_compare(
    left: AltiumCompiledPhysicalDocument,
    right: AltiumCompiledPhysicalDocument,
) -> int:
    comparison = managed_alpha_numeric_compare(left.file_name, right.file_name)
    if comparison:
        return comparison
    return managed_alpha_numeric_compare(
        left.physical_room_name or left.room_name,
        right.physical_room_name or right.room_name,
    )


def _managed_physical_document_order(
    documents: tuple[AltiumCompiledPhysicalDocument, ...],
) -> tuple[AltiumCompiledPhysicalDocument, ...]:
    ordered = list(documents)
    _compiled_dotnet_sort(ordered, _compiled_physical_document_compare)
    return tuple(ordered)


def _compiled_active_managed_pins(
    ordered: Sequence[tuple[str, str, _CompiledComponentPinEvidence]],
    global_signal_ids_by_pin: Mapping[tuple[str, int], str],
) -> list[_CompiledManagedPinRow]:
    common_signal_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    for physical_document_id, _, evidence in ordered:
        for pin in evidence.active_pins:
            if not _compiled_pin_is_common(evidence, pin):
                continue
            signal_id = global_signal_ids_by_pin.get(
                (physical_document_id, pin.source_pin_object_id)
            )
            if signal_id is not None:
                common_signal_ids[(physical_document_id, pin.designator)].add(signal_id)

    pins: list[_CompiledManagedPinRow] = []
    for physical_document_id, physical_room_name, evidence in ordered:
        part_pins: list[_CompiledManagedPinRow] = []
        for pin in evidence.active_pins:
            occurrence_local_signal_id = (
                f"{physical_document_id}:{pin.local_signal_id}"
                if pin.local_signal_id is not None
                else None
            )
            global_signal_id = (
                global_signal_ids_by_pin.get(
                    (physical_document_id, pin.source_pin_object_id)
                )
                if occurrence_local_signal_id is not None
                else None
            )
            if global_signal_id is None and _compiled_pin_is_common(evidence, pin):
                candidates = common_signal_ids.get(
                    (physical_document_id, pin.designator), set()
                )
                if len(candidates) == 1:
                    global_signal_id = next(iter(candidates))
                else:
                    occurrence_local_signal_id = None
            elif global_signal_id is None:
                global_signal_id = occurrence_local_signal_id
            part_pins.append(
                replace(
                    pin,
                    physical_part_designator=evidence.physical_part_designator,
                    local_signal_id=occurrence_local_signal_id,
                    global_signal_id=global_signal_id,
                    owner_physical_room_name=physical_room_name,
                )
            )
        _compiled_managed_pin_sort(part_pins)
        pins.extend(part_pins)
    return pins


def _compiled_pin_is_common(
    evidence: _CompiledComponentPinEvidence,
    pin: _CompiledManagedPinRow,
) -> bool:
    if pin.source_index >= len(evidence.raw_pins):
        return False
    return evidence.raw_pins[pin.source_index].owner_part_id == 0


def _compiled_append_unused_managed_pins(
    pins: list[_CompiledManagedPinRow],
    ordered: Sequence[tuple[str, str, _CompiledComponentPinEvidence]],
) -> None:
    visited_part_ids = {evidence.current_part_id for _, _, evidence in ordered}
    definition = ordered[0][2]
    if definition.subparts_count <= len(ordered):
        return
    _, last_physical_room_name, last = ordered[-1]
    for raw_pin in last.raw_pins:
        if (
            last.display_mode_count > 1
            and raw_pin.owner_part_display_mode != last.display_mode
        ):
            continue
        if raw_pin.owner_part_id in visited_part_ids:
            continue
        if raw_pin.owner_part_id == 0 and any(
            pin.designator == raw_pin.designator for pin in pins
        ):
            continue
        pins.append(
            _CompiledManagedPinRow(
                physical_part_designator=last.physical_part_designator,
                designator=raw_pin.designator,
                component_part_id=last.current_part_id,
                inferred=True,
                local_signal_id=None,
                global_signal_id=None,
                owner_document_name=last.owner_document_name,
                owner_physical_room_name=last_physical_room_name,
                location=raw_pin.location,
                source_component_uid=last.source_component_uid,
                source_pin_uid=raw_pin.source_pin_uid,
                source_pin_object_id=0,
                source_index=raw_pin.source_index,
            )
        )


def _compiled_merged_managed_pin_count(
    pins: Sequence[_CompiledManagedPinRow],
) -> int:
    duplicate_indices: set[int] = set()
    for pin_index, pin in enumerate(pins[:-1]):
        if pin_index in duplicate_indices:
            continue
        for candidate_index in range(pin_index + 1, len(pins)):
            candidate = pins[candidate_index]
            if managed_alpha_numeric_compare(pin.designator, candidate.designator):
                break
            if (
                pin.global_signal_id == candidate.global_signal_id
                or pin.local_signal_id == candidate.local_signal_id
            ):
                duplicate_indices.add(candidate_index)
    return len(pins) - len(duplicate_indices)


def _compiled_managed_pin_count(
    evidence_occurrences: Sequence[tuple[str, str, _CompiledComponentPinEvidence]],
    global_signal_ids_by_pin: Mapping[tuple[str, int], str],
) -> int:
    if not evidence_occurrences:
        return 0
    ordered = sorted(
        evidence_occurrences,
        key=lambda item: item[2].current_part_id,
    )
    pins = _compiled_active_managed_pins(ordered, global_signal_ids_by_pin)
    _compiled_append_unused_managed_pins(pins, ordered)
    _compiled_managed_pin_sort(pins)
    return _compiled_merged_managed_pin_count(pins)


def _collapse_multipart_component_rows(
    component_rows: list[AltiumCompiledComponent],
) -> list[AltiumCompiledComponent]:
    groups: list[list[AltiumCompiledComponent]] = []
    groups_by_designator: dict[str, list[tuple[int, set[int], str]]] = {}
    for component in component_rows:
        if component.part_count <= 1 or not _designator_binds_multipart_records(
            component.physical_designator
        ):
            groups.append([component])
            continue
        key = dotnet_ordinal_ignore_case_key(component.physical_designator)
        design_item_key = dotnet_ordinal_ignore_case_key(component.design_item_id)
        candidate_groups = groups_by_designator.setdefault(key, [])
        existing_index: int | None = None
        for result_index, part_ids, candidate_design_item_key in candidate_groups:
            if (
                component.current_part_id not in part_ids
                and design_item_key == candidate_design_item_key
            ):
                existing_index = result_index
                part_ids.add(component.current_part_id)
                break
        if existing_index is None:
            candidate_groups.append(
                (
                    len(groups),
                    {component.current_part_id},
                    design_item_key,
                )
            )
            groups.append([component])
            continue
        groups[existing_index].append(component)
    return [_merge_compiled_component_group(group) for group in groups]


def _merge_compiled_component_group(
    group: list[AltiumCompiledComponent],
) -> AltiumCompiledComponent:
    if len(group) == 1:
        component = group[0]
        if component.part_count > 1:
            return replace(component, current_part_id=1)
        return component
    first = group[0]
    definition = min(group, key=lambda component: component.current_part_id)
    identity = max(group, key=lambda component: component.current_part_id)
    footprint = min(
        (component.footprint for component in group),
        key=cmp_to_key(managed_alpha_numeric_compare),
    )
    parameters = _merged_compiled_row_parameters(group)
    identity_parent = definition.source_unique_id_path.rpartition("\\")[0]
    source_unique_id_path = _unique_id_path_join(
        identity_parent,
        identity.source_object_id,
    )
    return replace(
        definition,
        id=first.id,
        source_object_id=identity.source_object_id,
        source_unique_id_path=source_unique_id_path,
        logical_designator=first.logical_designator,
        physical_designator=first.physical_designator,
        display_designator=first.display_designator,
        footprint=footprint,
        parameters=parameters,
        pin_count=definition.all_pin_count,
        current_part_id=1,
        annotation_state=first.annotation_state,
        annotation_locked=first.annotation_locked,
        diagnostics=tuple(
            diagnostic for component in group for diagnostic in component.diagnostics
        ),
        _project_multipart_collapsed=True,
        _managed_source_component_occurrences=tuple(
            occurrence
            for component in group
            for occurrence in component._managed_source_component_occurrences
        ),
    )


def _merged_compiled_row_parameters(
    group: list[AltiumCompiledComponent],
) -> tuple[tuple[str, str], ...]:
    selected: list[tuple[str, str]] = []
    seen_ordinary: set[str] = set()
    for part_index, component in enumerate(
        sorted(group, key=lambda row: row.current_part_id)
    ):
        for name, text in component.parameters:
            key = dotnet_ordinal_ignore_case_key(name)
            if _is_component_directive_parameter(name):
                selected.append((name, text))
                continue
            if key in seen_ordinary:
                continue
            seen_ordinary.add(key)
            selected.append((name, text))
        if part_index == 0:
            seen_ordinary.update(
                dotnet_ordinal_ignore_case_key(name)
                for name in (
                    "Comment",
                    "Description",
                    "Library Reference",
                    "Library Name",
                    "Component Kind",
                )
            )
    selected.sort(
        key=cmp_to_key(
            lambda left, right: managed_alpha_numeric_compare(left[0], right[0])
        )
    )
    result: list[tuple[str, str]] = []
    seen_directives: set[str] = set()
    for name, text in selected:
        if _is_component_directive_parameter(name):
            directive_key = (
                f"{dotnet_ordinal_ignore_case_key(name)}\0"
                f"{dotnet_ordinal_ignore_case_key(text)}"
            )
            if directive_key in seen_directives:
                continue
            seen_directives.add(directive_key)
        result.append((name, text))
    result.sort(key=cmp_to_key(_compiled_component_parameter_pair_compare))
    return tuple(result)


def _physical_room_details(
    physical_document: AltiumCompiledPhysicalDocument,
) -> RoomDetails:
    return RoomDetails(
        room_name=physical_document.room_name,
        channel_prefix=physical_document.channel_prefix or "",
        channel_index=(
            str(physical_document.channel_index)
            if physical_document.channel_index >= 0
            else ""
        ),
        channel_alpha=physical_document.channel_alpha or "",
        sheet_number=(
            physical_document._managed_room_sheet_number
            if physical_document._managed_room_sheet_number is not None
            else physical_document.sheet_number or ""
        ),
        document_number=(
            physical_document._managed_room_document_number
            if physical_document._managed_room_document_number is not None
            else physical_document.document_number or ""
        ),
    )


def _physical_auto_name_from_terminal(
    local_net: AltiumCompiledNet,
    designator_map: dict[str, str],
    source_pin_designators: Mapping[int, _SourcePinDesignator] | None = None,
) -> str | None:
    candidates: list[str] = []
    for terminal in local_net.terminals:
        if not terminal.pin:
            continue
        projection = (source_pin_designators or {}).get(terminal._source_pin_object_id)
        designator = (
            projection.base_designator
            if projection is not None
            else designator_map.get(terminal.designator)
        )
        if designator is None:
            part_match = re.match(r"^(.*\d)[A-Z]+$", terminal.designator)
            base_designator = part_match.group(1) if part_match else ""
            designator = (
                designator_map.get(base_designator, base_designator)
                if base_designator
                else terminal.designator
            )
        candidates.append(f"Net{designator}_{terminal.pin}")
    if candidates:
        return min(candidates, key=_altium_net_total_sort_key)
    return None


def _compiled_net_name_annotation(
    name: str,
    annotation: AnnotationFile | None,
) -> str | None:
    if annotation is None:
        return None
    name_key = dotnet_ordinal_ignore_case_key(name)
    return next(
        (
            row.override_net_name
            for row in reversed(annotation.net_names)
            if dotnet_ordinal_ignore_case_key(row.original_net_name) == name_key
        ),
        None,
    )


def _project_differential_pair_suffixes(
    project: "AltiumPrjPcb | None",
) -> tuple[tuple[str, str], ...]:
    if project is None:
        return ()
    source_lines = getattr(project, "_source_lines", ())
    pairs: list[tuple[str, str]] = []
    for index, line in enumerate(source_lines):
        prefix = dotnet_ordinal_ignore_case_key("[DiffPairSuffix")
        if not dotnet_ordinal_ignore_case_key(line).startswith(prefix):
            continue
        if index + 2 >= len(source_lines):
            continue
        positive_line = source_lines[index + 1]
        negative_line = source_lines[index + 2]
        if not dotnet_ordinal_ignore_case_key(positive_line).startswith(
            dotnet_ordinal_ignore_case_key("Positive=")
        ) or not dotnet_ordinal_ignore_case_key(negative_line).startswith(
            dotnet_ordinal_ignore_case_key("Negative=")
        ):
            continue
        pairs.append(
            (
                positive_line.split("=", 1)[1],
                negative_line.split("=", 1)[1],
            )
        )
    return tuple(pairs)


def _compiled_strip_diff_pair_suffix(
    name: str,
    configured_pairs: tuple[tuple[str, str], ...] = (),
) -> tuple[str, str]:
    index = name.rfind("_")
    if index < 0:
        return name, ""
    suffix = name[index:]
    suffix_key = dotnet_ordinal_ignore_case_key(suffix)
    candidates = ("_P", "_N", *(value for pair in configured_pairs for value in pair))
    if any(
        dotnet_ordinal_ignore_case_key(candidate) == suffix_key
        for candidate in candidates
    ):
        return name[:index], suffix
    return name, ""


def _physical_channel_net_name(
    local_net: AltiumCompiledNet,
    physical_document: AltiumCompiledPhysicalDocument,
    *,
    designator_map: dict[str, str],
    channel_designator_format: str,
    auto_name_designator_map: dict[str, str] | None = None,
    source_pin_designators: Mapping[int, _SourcePinDesignator] | None = None,
    room_channel_index_override: str | None = None,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument] | None = None,
    physical_instance_offsets: dict[str, int] | None = None,
    physical_count_by_logical_id: dict[str, int] | None = None,
    channel_global_indices: Mapping[str, int] | None = None,
    channel_differentiate_values: Mapping[str, int] | None = None,
    compile_options: AltiumProjectCompileOptions | None = None,
    differential_pair_suffixes: tuple[tuple[str, str], ...] = (),
    nonlocal_hierarchical_power: bool = False,
) -> str:
    if not dotnet_trim(local_net.name):
        return local_net.name
    if nonlocal_hierarchical_power:
        return local_net.name
    nonhierarchical = (
        compile_options is not None
        and compile_options.effective_hierarchy_mode in {"FLAT", "GLOBAL"}
    )
    if local_net.auto_named:
        if nonhierarchical:
            return local_net.name
        return (
            _physical_auto_name_from_terminal(
                local_net,
                auto_name_designator_map or designator_map,
                source_pin_designators,
            )
            or local_net.name
        )
    if nonhierarchical:
        return _physical_sheet_number_net_name(
            local_net,
            physical_document,
            compile_options=compile_options,
            differential_pair_suffixes=differential_pair_suffixes,
        )
    if physical_document.parent_sheet_symbol_id is None:
        return _physical_sheet_number_net_name(
            local_net,
            physical_document,
            compile_options=compile_options,
            differential_pair_suffixes=differential_pair_suffixes,
        )
    if (
        physical_count_by_logical_id is not None
        and physical_count_by_logical_id.get(physical_document.logical_document_id, 0)
        <= 1
    ):
        return _physical_sheet_number_net_name(
            local_net,
            physical_document,
            compile_options=compile_options,
            differential_pair_suffixes=differential_pair_suffixes,
        )
    base_name, diff_suffix, bus_suffix = _physical_name_parts(
        local_net,
        differential_pair_suffixes,
    )
    if (
        physical_document_by_id is not None
        and physical_instance_offsets is not None
        and physical_count_by_logical_id is not None
        and compile_options is not None
    ):
        room = _room_details_for_compiled_naming(
            physical_document,
            physical_document_by_id,
            physical_instance_offsets=physical_instance_offsets,
            physical_count_by_logical_id=physical_count_by_logical_id,
            channel_global_indices=channel_global_indices,
            channel_differentiate_values=channel_differentiate_values,
            compile_options=compile_options,
        )
    else:
        room = _physical_room_details(physical_document)
    if room_channel_index_override and room.room_name and compile_options is not None:
        override_suffix = _compiled_room_suffix(
            int(room_channel_index_override),
            style=compile_options.channel_room_naming_style,
            depth=0,
        )
        room = replace(
            room,
            room_name=f"{physical_document.room_name}{override_suffix}",
        )
    if channel_designator_format:
        component = f"{base_name}{diff_suffix}{bus_suffix}" if bus_suffix else base_name
        rendered = apply_channel_pattern(channel_designator_format, room, component)
        return rendered if bus_suffix else f"{rendered}{diff_suffix}"
    if room.channel_alpha:
        if bus_suffix:
            return f"{base_name}{diff_suffix}{bus_suffix}{room.channel_alpha}"
        return f"{base_name}{room.channel_alpha}{diff_suffix}"
    return local_net.name


def _physical_name_parts(
    local_net: AltiumCompiledNet,
    differential_pair_suffixes: tuple[tuple[str, str], ...],
) -> tuple[str, str, str]:
    bus_suffix = local_net._name_source_bus_suffix
    bus_prefix = local_net._name_source_bus_prefix
    if bus_suffix and not bus_prefix and local_net.name.endswith(bus_suffix):
        bus_prefix = local_net.name[: -len(bus_suffix)]
    diff_input = bus_prefix if bus_suffix and bus_prefix else local_net.name
    base_name, diff_suffix = _compiled_strip_diff_pair_suffix(
        diff_input,
        differential_pair_suffixes,
    )
    return base_name, diff_suffix, bus_suffix


def _physical_sheet_number_net_name(
    local_net: AltiumCompiledNet,
    physical_document: AltiumCompiledPhysicalDocument,
    *,
    compile_options: AltiumProjectCompileOptions | None,
    differential_pair_suffixes: tuple[tuple[str, str], ...],
) -> str:
    if (
        compile_options is None
        or not compile_options.append_sheet_number_to_local_nets
        or not physical_document.sheet_number
        or local_net.auto_named
        or local_net._contains_bus
        or any(
            endpoint.role
            in {"bus", "bus_entry_member", "bus_member", "power_port", "port"}
            for endpoint in local_net.endpoints
        )
    ):
        return local_net.name
    base_name, diff_suffix, bus_suffix = _physical_name_parts(
        local_net,
        differential_pair_suffixes,
    )
    if bus_suffix:
        return local_net.name
    return f"{base_name}_{physical_document.sheet_number}{diff_suffix}"


def _replace_terminal_designator(
    terminal_id: str,
    designator_map: dict[str, str],
) -> str:
    if not designator_map:
        return terminal_id
    head, separator, tail = terminal_id.rpartition(":terminal:")
    if not separator:
        return terminal_id
    parts = tail.split(":", 2)
    if len(parts) != 3:
        return terminal_id
    terminal_index, designator, pin = parts
    physical_designator = designator_map.get(designator)
    if physical_designator is None:
        return terminal_id
    return f"{head}{separator}{terminal_index}:{physical_designator}:{pin}"


def _replace_pin_endpoint_id_designator(
    endpoint_id: str,
    designator_map: dict[str, str],
) -> str:
    if not designator_map:
        return endpoint_id
    head, separator, tail = endpoint_id.rpartition(":endpoint:")
    if not separator:
        return endpoint_id
    parts = tail.split(":", 3)
    if len(parts) != 4:
        return endpoint_id
    endpoint_index, role, designator, pin = parts
    if role != "pin":
        return endpoint_id
    physical_designator = designator_map.get(designator)
    if physical_designator is None:
        return endpoint_id
    return f"{head}{separator}{endpoint_index}:{role}:{physical_designator}:{pin}"


def _replace_compiled_item_designator(
    item_id: str,
    designator_map: dict[str, str],
) -> str:
    return _replace_pin_endpoint_id_designator(
        _replace_terminal_designator(item_id, designator_map),
        designator_map,
    )


def _replace_endpoint_designator(
    endpoint: AltiumCompiledNetEndpoint,
    designator_map: dict[str, str],
) -> AltiumCompiledNetEndpoint:
    if not endpoint.designator:
        return endpoint
    physical_designator = designator_map.get(endpoint.designator)
    if physical_designator is None:
        return endpoint
    return replace(
        endpoint,
        id=_replace_pin_endpoint_id_designator(endpoint.id, designator_map),
        designator=physical_designator,
    )


def _replace_compiled_net_id_prefix_precomputed(
    value: str,
    *,
    source_id: str,
    source_prefix: str,
    target_id: str,
    target_prefix: str,
) -> str:
    if value == source_id:
        return target_id
    if value.startswith(source_prefix):
        return f"{target_prefix}{value[len(source_prefix) :]}"
    raise RuntimeError(
        f"Compiled net item id {value!r} does not start with source id {source_id!r}"
    )


def _compiled_terminal_for_physical(
    terminal_id: str,
    terminal: AltiumCompiledNetTerminal,
    *,
    source_id: str,
    source_prefix: str,
    target_id: str,
    target_prefix: str,
    designator_map: dict[str, str],
    source_pin_designators: Mapping[int, _SourcePinDesignator] | None = None,
) -> AltiumCompiledNetTerminal:
    designator_map = _source_pin_designator_map(
        terminal.designator,
        terminal._source_pin_object_id,
        designator_map,
        source_pin_designators,
    )
    physical_terminal_id = _replace_terminal_designator(
        _replace_compiled_net_id_prefix_precomputed(
            terminal_id,
            source_id=source_id,
            source_prefix=source_prefix,
            target_id=target_id,
            target_prefix=target_prefix,
        ),
        designator_map,
    )
    designator = designator_map.get(terminal.designator, terminal.designator)
    return AltiumCompiledNetTerminal(
        id=physical_terminal_id,
        designator=designator,
        pin=terminal.pin,
        pin_name=terminal.pin_name,
        pin_type=terminal.pin_type,
        _source_component_uid=terminal._source_component_uid,
        _source_pin_uid=terminal._source_pin_uid,
        _source_pin_object_id=terminal._source_pin_object_id,
        _source_owner_part_id=terminal._source_owner_part_id,
    )


def _compiled_endpoint_for_physical(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    source_id: str,
    source_prefix: str,
    target_id: str,
    target_prefix: str,
    designator_map: dict[str, str],
    source_pin_designators: Mapping[int, _SourcePinDesignator] | None = None,
    repeat_value_override: int | None = None,
) -> AltiumCompiledNetEndpoint:
    designator_map = _source_pin_designator_map(
        endpoint.designator,
        endpoint._source_pin_object_id,
        designator_map,
        source_pin_designators,
    )
    endpoint_id = _replace_compiled_net_id_prefix_precomputed(
        endpoint.id,
        source_id=source_id,
        source_prefix=source_prefix,
        target_id=target_id,
        target_prefix=target_prefix,
    )
    designator = endpoint.designator
    if designator or endpoint._source_pin_object_id:
        physical_designator = designator_map.get(designator)
        if physical_designator is not None:
            endpoint_id = _replace_pin_endpoint_id_designator(
                endpoint_id,
                designator_map,
            )
            designator = physical_designator
    repeat_value = endpoint._repeat_value
    if repeat_value_override is not None:
        repeat_value = repeat_value_override
        endpoint_id = f"{endpoint_id}:repeat:{repeat_value_override}"
    return AltiumCompiledNetEndpoint(
        id=endpoint_id,
        role=endpoint.role,
        element_id=endpoint.element_id,
        object_id=endpoint.object_id,
        name=endpoint.name,
        parent_id=endpoint.parent_id,
        designator=designator,
        pin=endpoint.pin,
        pin_name=endpoint.pin_name,
        connection_point=endpoint.connection_point,
        _source_occurrence_id=endpoint._source_occurrence_id,
        _source_pin_object_id=endpoint._source_pin_object_id,
        _bus_signal_index=endpoint._bus_signal_index,
        _harness_entries_path=endpoint._harness_entries_path,
        _repeat_value=repeat_value,
        _harness_type_name=endpoint._harness_type_name,
        _harness_interface_name=endpoint._harness_interface_name,
        _harness_type_inferred=endpoint._harness_type_inferred,
    )


def _compiled_endpoints_for_physical(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    source_id: str,
    source_prefix: str,
    target_id: str,
    target_prefix: str,
    designator_map: dict[str, str],
    repeat_values: Sequence[int],
    source_pin_designators: Mapping[int, _SourcePinDesignator] | None = None,
) -> tuple[AltiumCompiledNetEndpoint, ...]:
    values: Sequence[int | None] = repeat_values or (None,)
    return tuple(
        _compiled_endpoint_for_physical(
            endpoint,
            source_id=source_id,
            source_prefix=source_prefix,
            target_id=target_id,
            target_prefix=target_prefix,
            designator_map=designator_map,
            repeat_value_override=repeat_value,
            source_pin_designators=source_pin_designators,
        )
        for repeat_value in values
    )


def _inferred_harness_endpoint_applies(
    endpoint: AltiumCompiledNetEndpoint,
    allowed_types_by_name: Mapping[str, tuple[str, ...]],
) -> bool:
    if not endpoint._harness_type_inferred:
        return True
    allowed_types = allowed_types_by_name.get(
        dotnet_ordinal_ignore_case_key(endpoint._harness_interface_name), ()
    )
    endpoint_type_key = dotnet_ordinal_ignore_case_key(endpoint._harness_type_name)
    return any(
        dotnet_ordinal_ignore_case_key(type_name) == endpoint_type_key
        for type_name in allowed_types
    )


def _compiled_item_from_terminal(
    item_id: str,
    terminal: object,
    *,
    physical_document_id: str = "",
) -> AltiumCompiledNetItem:
    return AltiumCompiledNetItem(
        id=item_id,
        kind="terminal",
        _source_pin_object_id=int(getattr(terminal, "_source_pin_object_id", 0)),
        designator=str(getattr(terminal, "designator", "") or ""),
        pin=str(getattr(terminal, "pin", "") or ""),
        pin_name=str(getattr(terminal, "pin_name", "") or ""),
        physical_document_id=physical_document_id,
    )


def _compiled_item_from_endpoint(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    physical_document_id: str = "",
) -> AltiumCompiledNetItem:
    return AltiumCompiledNetItem(
        id=endpoint.id,
        kind="power_port" if endpoint.role == "harness_power" else endpoint.role,
        _source_pin_object_id=endpoint._source_pin_object_id,
        name=endpoint.name,
        parent_id=endpoint.parent_id,
        element_id=endpoint.element_id,
        object_id=endpoint.object_id,
        designator=endpoint.designator,
        pin=endpoint.pin,
        pin_name=endpoint.pin_name,
        physical_document_id=physical_document_id,
        connection_point=endpoint.connection_point,
        removed=endpoint.role == "harness_entry",
        inferred_from_harness=endpoint.role
        in {"harness_entry", "harness_port", "harness_power"},
    )


def _compiled_item_from_local_endpoint(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    linked_sheet_entry_ids: frozenset[str],
) -> AltiumCompiledNetItem:
    item = _compiled_item_from_endpoint(endpoint)
    if _compiled_sheet_entry_endpoint_is_linked(endpoint, linked_sheet_entry_ids):
        return replace(item, removed=True)
    return item


def _compiled_sheet_entry_endpoint_is_linked(
    endpoint: AltiumCompiledNetEndpoint,
    linked_sheet_entry_ids: frozenset[str],
) -> bool:
    if endpoint.role != "sheet_entry":
        return False
    if endpoint.object_id and (
        dotnet_ordinal_ignore_case_key(endpoint.object_id) in linked_sheet_entry_ids
    ):
        return True
    if not endpoint.element_id:
        return False
    element_key = dotnet_ordinal_ignore_case_key(endpoint.element_id)
    if element_key in linked_sheet_entry_ids:
        return True
    duplicate_base, separator, _suffix = endpoint.element_id.partition(":duplicate:")
    return bool(
        separator
        and dotnet_ordinal_ignore_case_key(duplicate_base) in linked_sheet_entry_ids
    )


def _compiled_items_from_graphical(
    net_id: str,
    graphical: object,
) -> tuple[AltiumCompiledNetItem, ...]:
    rows: list[AltiumCompiledNetItem] = []
    seen: set[tuple[str, str]] = set()
    for kind, attr_name in (
        ("wire", "wires"),
        ("junction", "junctions"),
        ("net_label", "labels"),
        ("power_port", "power_ports"),
        ("port", "ports"),
        ("sheet_entry", "sheet_entries"),
    ):
        for index, value in enumerate(getattr(graphical, attr_name, ()) or ()):
            element_id = str(value or "").strip()
            if not element_id:
                continue
            key = (kind, element_id)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                AltiumCompiledNetItem(
                    id=f"{net_id}:graphical:{kind}:{index}:{element_id}",
                    kind=kind,
                    element_id=element_id,
                    object_id=element_id,
                )
            )
    return tuple(rows)


def _replace_compiled_item_for_physical(
    item: AltiumCompiledNetItem,
    *,
    source_id: str,
    target_id: str,
    designator_map: dict[str, str],
    physical_document_id: str,
) -> AltiumCompiledNetItem:
    source_prefix = f"{source_id}:"
    target_prefix = f"{target_id}:"
    return _compiled_item_for_physical(
        item,
        source_id=source_id,
        source_prefix=source_prefix,
        target_id=target_id,
        target_prefix=target_prefix,
        designator_map=designator_map,
        physical_document_id=physical_document_id,
    )


def _compiled_item_for_physical(
    item: AltiumCompiledNetItem,
    *,
    source_id: str,
    source_prefix: str,
    target_id: str,
    target_prefix: str,
    designator_map: dict[str, str],
    physical_document_id: str,
    source_pin_designators: Mapping[int, _SourcePinDesignator] | None = None,
) -> AltiumCompiledNetItem:
    designator_map = _source_pin_designator_map(
        item.designator,
        item._source_pin_object_id,
        designator_map,
        source_pin_designators,
    )
    item_id = _replace_compiled_item_designator(
        _replace_compiled_net_id_prefix_precomputed(
            item.id,
            source_id=source_id,
            source_prefix=source_prefix,
            target_id=target_id,
            target_prefix=target_prefix,
        ),
        designator_map,
    )
    designator = designator_map.get(item.designator, item.designator)
    return AltiumCompiledNetItem(
        id=item_id,
        kind=item.kind,
        name=item.name,
        parent_id=item.parent_id,
        element_id=item.element_id,
        object_id=item.object_id,
        designator=designator,
        pin=item.pin,
        pin_name=item.pin_name,
        physical_document_id=physical_document_id,
        connection_point=item.connection_point,
        removed=item.removed,
        inferred_from_harness=item.inferred_from_harness,
        _source_pin_object_id=item._source_pin_object_id,
    )


def _compiled_items_for_physical(
    items: Sequence[AltiumCompiledNetItem],
    *,
    projected_endpoints_by_source_id: Mapping[str, Sequence[AltiumCompiledNetEndpoint]],
    source_id: str,
    source_prefix: str,
    target_id: str,
    target_prefix: str,
    designator_map: dict[str, str],
    physical_document_id: str,
    source_pin_designators: Mapping[int, _SourcePinDesignator] | None = None,
    projected_terminals_by_source_id: Mapping[str, AltiumCompiledNetTerminal]
    | None = None,
) -> tuple[AltiumCompiledNetItem, ...]:
    rows: list[AltiumCompiledNetItem] = []
    for item in items:
        endpoint_rows = projected_endpoints_by_source_id.get(item.id)
        if endpoint_rows is not None and (item.removed or item.inferred_from_harness):
            projected_item = _compiled_item_for_physical(
                item,
                source_id=source_id,
                source_prefix=source_prefix,
                target_id=target_id,
                target_prefix=target_prefix,
                designator_map=designator_map,
                physical_document_id=physical_document_id,
                source_pin_designators=source_pin_designators,
            )
            rows.extend(
                replace(projected_item, id=endpoint.id) for endpoint in endpoint_rows
            )
            continue
        projected_item = _compiled_item_for_physical(
            item,
            source_id=source_id,
            source_prefix=source_prefix,
            target_id=target_id,
            target_prefix=target_prefix,
            designator_map=designator_map,
            physical_document_id=physical_document_id,
            source_pin_designators=source_pin_designators,
        )
        terminal = (projected_terminals_by_source_id or {}).get(item.id)
        if terminal is not None:
            projected_item = replace(projected_item, id=terminal.id)
        rows.append(projected_item)
    return tuple(rows)


def _replace_compiled_net_id_prefix(value: str, source_id: str, target_id: str) -> str:
    if value == source_id:
        return target_id
    prefix = f"{source_id}:"
    if value.startswith(prefix):
        return f"{target_id}:{value[len(prefix) :]}"
    raise RuntimeError(
        f"Compiled net item id {value!r} does not start with source id {source_id!r}"
    )


def _compiled_endpoint_from_net_endpoint(
    endpoint_id: str,
    endpoint: object,
    *,
    parent_id: str = "",
) -> AltiumCompiledNetEndpoint:
    connection_point = getattr(endpoint, "connection_point", None)
    return AltiumCompiledNetEndpoint(
        id=endpoint_id,
        role=str(getattr(endpoint, "role", "") or ""),
        element_id=str(getattr(endpoint, "element_id", "") or ""),
        object_id=str(getattr(endpoint, "object_id", "") or ""),
        name=str(getattr(endpoint, "name", "") or ""),
        parent_id=parent_id,
        designator=str(getattr(endpoint, "designator", "") or ""),
        pin=str(getattr(endpoint, "pin", "") or ""),
        pin_name=str(getattr(endpoint, "pin_name", "") or ""),
        connection_point=connection_point
        if isinstance(connection_point, tuple)
        else None,
        _source_occurrence_id=str(getattr(endpoint, "_source_occurrence_id", "") or ""),
        _source_pin_object_id=int(getattr(endpoint, "_source_pin_object_id", 0)),
        _bus_signal_index=getattr(endpoint, "_bus_signal_index", None),
        _harness_entries_path=tuple(
            str(value) for value in getattr(endpoint, "_harness_entries_path", ()) or ()
        ),
        _repeat_value=getattr(endpoint, "_repeat_value", None),
        _harness_type_name=str(getattr(endpoint, "_harness_type_name", "") or ""),
        _harness_interface_name=str(
            getattr(endpoint, "_harness_interface_name", "") or ""
        ),
        _harness_type_inferred=bool(getattr(endpoint, "_harness_type_inferred", False)),
    )


def _replace_compiled_endpoint_id_prefix(
    endpoint: AltiumCompiledNetEndpoint,
    source_id: str,
    target_id: str,
) -> AltiumCompiledNetEndpoint:
    return replace(
        endpoint,
        id=_replace_compiled_net_id_prefix(endpoint.id, source_id, target_id),
    )


def _compiled_net_has_endpoint(
    net: AltiumCompiledNet, role: str, object_id: str
) -> bool:
    clean_role = str(role or "").lower()
    clean_object_id = dotnet_ordinal_ignore_case_key(str(object_id or ""))
    if not clean_role or not clean_object_id:
        return False
    return any(
        endpoint.role.lower() == clean_role
        and (
            dotnet_ordinal_ignore_case_key(endpoint.element_id) == clean_object_id
            or dotnet_ordinal_ignore_case_key(endpoint.object_id) == clean_object_id
        )
        for endpoint in net.endpoints
    )


def _schdocs_by_logical_id(
    schdocs: Sequence["AltiumSchDoc"],
    *,
    project_base_dir: Path | None,
) -> dict[str, "AltiumSchDoc"]:
    return {
        _logical_document_id(
            _relative_source_path(schdoc.filepath, project_base_dir),
            ordinal,
        ): schdoc
        for ordinal, schdoc in enumerate(schdocs)
    }


def _schdoc_reference_maps(
    schdocs: Sequence["AltiumSchDoc"],
    *,
    project_base_dir: Path | None,
) -> tuple[
    dict[str, tuple["AltiumSchDoc", ...]],
    dict[str, tuple["AltiumSchDoc", ...]],
]:
    by_source_ref: dict[str, list["AltiumSchDoc"]] = defaultdict(list)
    by_file_name: dict[str, list["AltiumSchDoc"]] = defaultdict(list)
    for schdoc in schdocs:
        source_ref = _normalize_project_reference(
            _relative_source_path(schdoc.filepath, project_base_dir)
        )
        if source_ref:
            by_source_ref[source_ref].append(schdoc)
        file_name = schdoc.filepath.name if schdoc.filepath else ""
        if file_name:
            by_file_name[_logical_source_identity_key(file_name)].append(schdoc)
    return (
        {key: tuple(value) for key, value in by_source_ref.items()},
        {key: tuple(value) for key, value in by_file_name.items()},
    )


def _resolve_referenced_schdoc(
    child_filename: str,
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> "AltiumSchDoc | None":
    child_ref = _normalize_project_reference(child_filename)
    if not child_ref:
        return None
    source_matches = schdoc_by_source_ref.get(child_ref, ())
    if len(source_matches) == 1:
        return source_matches[0]
    file_matches = schdoc_by_file_name.get(_reference_basename(child_ref), ())
    if len(file_matches) == 1:
        return file_matches[0]
    stem_matches = _schdoc_stem_matches(child_ref, schdoc_by_file_name)
    if len(stem_matches) == 1:
        return stem_matches[0]
    return None


def _schdoc_stem_matches(
    child_ref: str,
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> tuple["AltiumSchDoc", ...]:
    reference_stem = _reference_stem(child_ref)
    if not reference_stem:
        return ()
    matches: list["AltiumSchDoc"] = []
    for file_name, schdocs in schdoc_by_file_name.items():
        if _reference_stem(file_name) == reference_stem:
            matches.extend(schdocs)
    return tuple(matches)


def _bus_member_names_by_logical_id(
    schdoc_by_logical_id: dict[str, "AltiumSchDoc"],
) -> dict[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for logical_id, schdoc in schdoc_by_logical_id.items():
        names: set[str] = set()
        for port in schdoc.get_ports():
            port_name = str(getattr(port, "name", "") or "")
            names.update(_parse_bus_range(port_name))
        if names:
            result[logical_id] = frozenset(names)
    return result


def _require_source_schdocs(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    schdoc_by_logical_id: dict[str, "AltiumSchDoc"],
) -> None:
    missing_ids = [
        document.id
        for document in logical_documents
        if document.id not in schdoc_by_logical_id
    ]
    if missing_ids:
        raise RuntimeError(
            "Logical document rows do not have matching source SchDocs: "
            + ", ".join(missing_ids)
        )


def _path_join(parent: str, child: str) -> str:
    if not parent:
        return child
    if not child:
        return parent
    return f"{parent}\\{child}"


def _unique_id_path_join(parent: str, child: str) -> str:
    if not parent:
        return f"\\{child}" if child else ""
    if not child:
        return parent
    return f"{parent}\\{child}"


def _component_kind_name(component: "SchComponentInfo") -> str:
    kind = component.component_kind
    name = getattr(kind, "name", None)
    if name:
        return str(name)
    return str(kind)


def _component_location(component: "SchComponentInfo") -> tuple[int, int]:
    location = getattr(component, "location", None)
    if isinstance(location, tuple) and len(location) >= 2:
        return int(location[0]), int(location[1])
    x = int(getattr(location, "x", 0) or 0)
    y = int(getattr(location, "y", 0) or 0)
    return x, y


def _component_includes_in_netlist(
    component: "SchComponentInfo",
    compile_mask_bounds: tuple[tuple[int, int, int, int], ...],
) -> bool:
    includes = getattr(component, "includes_in_netlist", None)
    if callable(includes):
        participates = bool(includes())
    else:
        participates = component_kind_includes_in_netlist(component.component_kind)
    if not participates:
        return False
    if not compile_mask_bounds:
        return True
    points = [_compiled_record_location(component)]
    points.extend(
        _compiled_pin_hotspot(pin)
        for pin in getattr(component, "pins", ())
        if pin_belongs_to_component_view(pin, component)
        and pin_is_managed_part_member(pin)
        and not pin_is_runtime_hidden(pin, component)
    )
    return not any(
        _compiled_mask_contains_every_point(mask, points)
        for mask in compile_mask_bounds
    )


def _compiled_record_location(value: object) -> RootPoint:
    record = getattr(value, "record", value)
    location = getattr(record, "location", None)
    if location is not None and hasattr(location, "x"):
        return (
            int(location.x),
            int(location.y),
            int(getattr(location, "x_frac", 0)),
            int(getattr(location, "y_frac", 0)),
        )
    x, y = _component_location(cast("SchComponentInfo", value))
    return (x, y, 0, 0)


def _compiled_pin_hotspot(value: object) -> RootPoint:
    precise = getattr(value, "_precise_connection_point", None)
    if precise is not None:
        return cast(RootPoint, precise)
    pin = getattr(value, "pin", value)
    get_hot_spot = getattr(pin, "get_hot_spot", None)
    if not callable(get_hot_spot):
        raise TypeError("compiled component pin does not expose a hotspot")
    hotspot = get_hot_spot()
    return (
        int(getattr(hotspot, "x")),
        int(getattr(hotspot, "y")),
        int(getattr(hotspot, "x_frac", 0)),
        int(getattr(hotspot, "y_frac", 0)),
    )


def _repeat_parts(designator: str) -> tuple[bool, str | None, int | None, int | None]:
    value = str(designator or "")
    opening = value.find("(")
    if opening < 0 or dotnet_ordinal_ignore_case_key(
        dotnet_trim(value[:opening])
    ) != dotnet_ordinal_ignore_case_key("REPEAT"):
        return False, None, None, None
    closing = value.find(")", opening + 1)
    if closing < 0:
        return False, None, None, None
    parts = value[opening + 1 : closing].split(",", 2)
    if len(parts) != 3 or "," in parts[2]:
        return False, None, None, None
    repeat_range = _portable_repeat_range(parts[1], parts[2])
    if repeat_range is None:
        return False, None, None, None
    return True, dotnet_trim(parts[0]), *repeat_range


def _portable_repeat_range(first: str, last: str) -> tuple[int, int] | None:
    start = _repeat_name_int32(first)
    end = _repeat_name_int32(last)
    if start is None or end is None or start < 0:
        return None
    difference = (end - start + (1 << 31)) % (1 << 32) - (1 << 31)
    if difference < 0:
        return None
    # Recognition preserves the managed wrap, but the portable compiler's
    # nonnegative channel contract must not expand wrapped channel values.
    if end < 0:
        raise ValueError(
            "sheet_symbol_repeat_range: wrapped negative endpoints are not supported"
        )
    return start, end


def _parse_bus_range(name: str) -> tuple[str, ...]:
    bus_range = _parse_managed_bus_range(name)
    if bus_range is None:
        return ()
    return bus_range.members()


def _bus_range_base_name(name: str) -> str | None:
    bus_range = _parse_managed_bus_range(name)
    return None if bus_range is None else bus_range.prefix


def _room_channel_index(room: RoomDetails, fallback: int) -> int:
    try:
        return int(room.channel_index)
    except ValueError:
        return fallback


def _physical_child_instance_from_room(
    *,
    instance_key: str,
    room: RoomDetails,
    fallback_channel_index: int,
    sheet_symbol_designator: str | None = None,
) -> _PhysicalChildInstance:
    channel_index = _room_channel_index(room, fallback_channel_index)
    return _PhysicalChildInstance(
        instance_key=instance_key,
        path_segment=room.room_name,
        sheet_symbol_designator=(
            room.room_name
            if sheet_symbol_designator is None
            else sheet_symbol_designator
        ),
        channel_index=channel_index,
        channel_prefix=room.channel_prefix or None,
        channel_alpha=room.channel_alpha or None,
        room_name=room.room_name,
        sheet_number=room.sheet_number or None,
        document_number=room.document_number or None,
    )


def _no_channel_index(*, new_indexing: bool) -> int:
    return -1 if new_indexing else 0


def _repeat_document_channel_index(
    symbol: AltiumCompiledSheetSymbol,
    channel_value: int,
    *,
    new_indexing: bool,
) -> int:
    assert symbol.repeat_start is not None and symbol.repeat_end is not None
    if not symbol.repeat_start <= channel_value <= symbol.repeat_end:
        return _no_channel_index(new_indexing=new_indexing)
    matched_offset = channel_value - symbol.repeat_start
    return channel_value if new_indexing or matched_offset != 0 else 1


def _single_child_instance(
    symbol: AltiumCompiledSheetSymbol,
    *,
    new_indexing: bool,
) -> _PhysicalChildInstance:
    child_filename = symbol.child_source_path or symbol.child_filename
    segment = symbol.designator or Path(child_filename).stem or symbol.id
    return _PhysicalChildInstance(
        instance_key="single",
        path_segment=segment,
        sheet_symbol_designator=segment,
        channel_index=_no_channel_index(new_indexing=new_indexing),
        channel_prefix=None,
        channel_alpha=None,
        room_name=segment,
        sheet_number=None,
        document_number=None,
    )


def _physical_child_instances(
    symbol: AltiumCompiledSheetSymbol,
    child_logical_id: str,
    multi_ref_room: RoomDetails | None,
    repeat_channel_values: dict[tuple[str, str, int], int],
    *,
    new_indexing: bool,
) -> tuple[_PhysicalChildInstance, ...]:
    if (
        symbol.is_repeat
        and symbol.repeat_prefix is not None
        and symbol.repeat_start is not None
        and symbol.repeat_end is not None
    ):
        if symbol.repeat_end < symbol.repeat_start:
            return ()
        instances: list[_PhysicalChildInstance] = []
        for instance_offset, repeat_value in enumerate(
            range(symbol.repeat_start, symbol.repeat_end + 1)
        ):
            channel_value = repeat_channel_values.get(
                (symbol.id, child_logical_id, repeat_value),
                repeat_value,
            )
            room = _build_room_details(
                f"{symbol.repeat_prefix}{channel_value}",
                instance_offset,
                sheet_designator=symbol.designator,
            )
            # AD26 sheet/document-number path levels for Repeat() channels use
            # the raw repeat VALUE at this expansion position under new
            # indexing (not the expansion ordinal), or 1 for the first
            # position under old indexing.
            sheets_index = (
                repeat_value
                if new_indexing
                else (1 if instance_offset == 0 else repeat_value)
            )
            document_channel_index = _repeat_document_channel_index(
                symbol,
                channel_value,
                new_indexing=new_indexing,
            )
            room.sheet_number = str(sheets_index)
            room.document_number = str(sheets_index)
            instances.append(
                replace(
                    _physical_child_instance_from_room(
                        instance_key=f"repeat:{repeat_value}",
                        room=room,
                        fallback_channel_index=repeat_value,
                        sheet_symbol_designator=(
                            f"{symbol.repeat_prefix}{repeat_value}"
                        ),
                    ),
                    channel_index=document_channel_index,
                    managed_repeat_channel_value=channel_value,
                )
            )
        return tuple(instances)

    if multi_ref_room is not None:
        return (
            replace(
                _physical_child_instance_from_room(
                    instance_key=f"channel:{multi_ref_room.channel_index}",
                    room=multi_ref_room,
                    fallback_channel_index=1,
                ),
                channel_index=_no_channel_index(new_indexing=new_indexing),
                channel_prefix=None,
                channel_alpha=None,
            ),
        )

    return (_single_child_instance(symbol, new_indexing=new_indexing),)


def _repeat_parent_rows_by_child(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    *,
    parent_rank_by_logical_id: Mapping[str, int] | None = None,
) -> dict[str, list[tuple[str, int, str, bool]]]:
    parent_rows_by_child: dict[
        str,
        list[tuple[str, int, str, bool]],
    ] = defaultdict(list)
    ranked_symbols = sorted(
        enumerate(sheet_symbols),
        key=lambda item: (
            (parent_rank_by_logical_id or {}).get(
                item[1].logical_document_id,
                item[0],
            ),
            item[0],
        ),
    )
    for _source_index, symbol in ranked_symbols:
        child_ids = symbol._managed_retained_child_logical_document_ids
        if child_ids is None:
            child_ids = _managed_sheet_symbol_child_ids(symbol)
        for child_id in child_ids:
            if (
                symbol.is_repeat
                and symbol.repeat_prefix is not None
                and symbol.repeat_start is not None
                and symbol.repeat_end is not None
            ):
                for channel_value in range(
                    symbol.repeat_start,
                    symbol.repeat_end + 1,
                ):
                    parent_rows_by_child[child_id].append(
                        (symbol.repeat_prefix, channel_value, symbol.id, True)
                    )
            else:
                parent_rows_by_child[child_id].append(
                    (symbol.designator, -1, symbol.id, False)
                )
    return parent_rows_by_child


def _updated_repeat_values_for_child(
    parent_rows: Sequence[tuple[str, int, str, bool]],
    *,
    new_indexing: bool,
) -> dict[tuple[str, int], int]:
    result: dict[tuple[str, int], int] = {}
    rows = sorted(
        parent_rows,
        key=lambda row: (dotnet_ordinal_ignore_case_sort_key(row[0]), row[1]),
    )
    previous_name_key = ""
    next_value = 0 if new_indexing else 1
    for instance_name, channel_value, symbol_id, is_repeat in rows:
        if not is_repeat:
            previous_name_key = ""
            next_value = 0 if new_indexing else 1
            continue

        instance_name_key = dotnet_ordinal_ignore_case_key(instance_name)
        same_instance = instance_name_key == previous_name_key
        if new_indexing:
            if not same_instance:
                next_value = max(channel_value, 0)
            elif channel_value > next_value:
                next_value = channel_value
            updated_value = next_value
        else:
            if not same_instance:
                next_value = 1
            updated_value = next_value

        result[(symbol_id, channel_value)] = updated_value
        previous_name_key = instance_name_key
        next_value = updated_value + 1
    return result


def _repeat_channel_values(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    *,
    new_indexing: bool,
    parent_rank_by_logical_id: Mapping[str, int] | None = None,
) -> dict[tuple[str, str, int], int]:
    """Apply the AD26 channel-details updater to repeat parent entries."""

    result: dict[tuple[str, str, int], int] = {}
    for child_id, parent_rows in _repeat_parent_rows_by_child(
        sheet_symbols,
        parent_rank_by_logical_id=parent_rank_by_logical_id,
    ).items():
        updated = _updated_repeat_values_for_child(
            parent_rows,
            new_indexing=new_indexing,
        )
        for (symbol_id, raw_value), updated_value in updated.items():
            result[(symbol_id, child_id, raw_value)] = updated_value
    return result


def _multi_reference_channel_rooms(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> dict[tuple[str, str], RoomDetails]:
    symbols_by_parent_and_child: dict[
        tuple[str, str], list[AltiumCompiledSheetSymbol]
    ] = defaultdict(list)
    for symbol in sheet_symbols:
        child_ids = symbol._managed_retained_child_logical_document_ids
        if child_ids is None:
            child_ids = _managed_sheet_symbol_child_ids(symbol)
        for child_id in child_ids:
            if symbol.is_repeat:
                continue
            symbols_by_parent_and_child[(symbol.logical_document_id, child_id)].append(
                symbol
            )

    rooms_by_symbol_child_id: dict[tuple[str, str], RoomDetails] = {}
    for (_parent_id, child_id), symbols in symbols_by_parent_and_child.items():
        if len(symbols) <= 1:
            continue
        ordered_symbols = sorted(
            symbols,
            key=cmp_to_key(
                lambda left, right: managed_alpha_numeric_compare(
                    left.designator,
                    right.designator,
                )
            ),
        )
        for instance_offset, symbol in enumerate(ordered_symbols):
            room_name = symbol.designator or f"Channel{instance_offset + 1}"
            rooms_by_symbol_child_id[(symbol.id, child_id)] = _build_room_details(
                room_name,
                instance_offset,
                sheet_designator=symbol.designator,
            )
    return rooms_by_symbol_child_id


def _effective_channel_designator_format(format_str: str) -> str:
    return format_str or _DEFAULT_CHANNEL_DESIGNATOR_FORMAT


def _room_details_for_physical_document(
    document: AltiumCompiledPhysicalDocument,
    *,
    instance_offset: int,
    sheet_designator: str = "",
    use_instance_offset_for_channel: bool = False,
) -> RoomDetails:
    room_name = document.room_name or Path(document.file_name).stem
    inferred_room = _build_room_details(room_name, instance_offset)
    instance_offset_room = _build_room_details("", instance_offset)
    has_explicit_channel_data = (
        document.channel_alpha is not None or document.channel_prefix is not None
    )
    channel_index = (
        str(document.channel_index)
        if has_explicit_channel_data
        else inferred_room.channel_index
    )
    channel_alpha = document.channel_alpha or inferred_room.channel_alpha
    if use_instance_offset_for_channel:
        channel_index = instance_offset_room.channel_index
        channel_alpha = instance_offset_room.channel_alpha
    return RoomDetails(
        room_name=room_name,
        channel_prefix=document.channel_prefix or inferred_room.channel_prefix,
        channel_index=channel_index,
        channel_alpha=channel_alpha,
        sheet_designator=sheet_designator,
        sheet_number=(
            document._managed_room_sheet_number
            if document._managed_room_sheet_number is not None
            else document.sheet_number or inferred_room.sheet_number
        ),
        document_number=(
            document._managed_room_document_number
            if document._managed_room_document_number is not None
            else document.document_number or inferred_room.document_number
        ),
    )


def _compiled_alpha_index(index: int) -> str:
    if index <= 0:
        return ""
    result = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _compiled_room_style_uses_alpha(style: int, depth: int) -> bool:
    if style in {
        ChannelRoomNamingStyle.FLAT_ALPHA_WITH_NAMES,
        ChannelRoomNamingStyle.ALPHA_NAME_PATH,
    }:
        return True
    if style == ChannelRoomNamingStyle.MIXED_NAME_PATH:
        return depth % 2 == 0
    return False


def _compiled_room_suffix(index: int, *, style: int, depth: int) -> str:
    if _compiled_room_style_uses_alpha(style, depth):
        return _compiled_alpha_index(index)
    return str(index)


def _is_repeat_channel_document(
    document: AltiumCompiledPhysicalDocument,
    _physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
) -> bool:
    """Return the retained managed source-symbol Repeat classification."""
    return document._managed_parent_from_repeat_sheet_symbol


def _managed_repeat_channel_value(
    document: AltiumCompiledPhysicalDocument,
) -> int:
    value = document._managed_repeat_channel_value
    return document.channel_index if value is None else value


def _repeat_channel_alpha_room_name(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    compile_options: AltiumProjectCompileOptions,
) -> str | None:
    """AD26 alpha swap for unique-designator ``Repeat()`` channel rooms.

    HierarchyBuilderRoomDetails.GetRoomName (:57-61): when the bottom channel
    value is set (Repeat expansion) and GetSwapToAlphanumericForm holds at the
    bottom hierarchy depth, the room is the designator prefix plus
    GetIndexInAlphanumericForm of the channel value (plus one under
    NewIndexingOfSheetSymbols) instead of the expanded designator.
    """
    if not _is_repeat_channel_document(document, physical_document_by_id):
        return None
    depth = 0
    node = physical_document_by_id.get(document.parent_id or "")
    while node is not None and node.parent_sheet_symbol_id is not None:
        depth += 1
        node = physical_document_by_id.get(node.parent_id or "")
    style = compile_options.channel_room_naming_style
    if not _compiled_room_style_uses_alpha(style, depth):
        return None
    room_name = document.room_name or Path(document.file_name).stem
    value = _managed_repeat_channel_value(document)
    if compile_options.new_indexing_of_sheet_symbols:
        value += 1
    return f"{managed_designator_prefix(room_name)}{_compiled_alpha_index(value)}"


def _component_naming_room_name(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    physical_count_by_logical_id: dict[str, int],
    channel_global_indices: Mapping[str, int] | None = None,
    channel_differentiate_values: Mapping[str, int] | None = None,
    compile_options: AltiumProjectCompileOptions,
) -> str:
    if compile_options.effective_hierarchy_mode in {"FLAT", "GLOBAL"}:
        return document.physical_instance_path.rsplit("/", 1)[-1]
    room_name = document.room_name or Path(document.file_name).stem
    style = compile_options.channel_room_naming_style
    hierarchy_documents = _physical_hierarchy_documents(
        document,
        physical_document_by_id,
    )
    multi_start = _multi_channel_start(
        hierarchy_documents,
        physical_count_by_logical_id,
    )
    if multi_start is None:
        return room_name
    multi_documents = hierarchy_documents[multi_start:]
    if len(multi_documents) == 1 and not _has_duplicate_bottom_designator(
        document,
        physical_document_by_id,
    ):
        return (
            _repeat_channel_alpha_room_name(
                document,
                physical_document_by_id,
                compile_options=compile_options,
            )
            or room_name
        )

    if style in {
        ChannelRoomNamingStyle.FLAT_NUMERIC_WITH_NAMES,
        ChannelRoomNamingStyle.FLAT_ALPHA_WITH_NAMES,
    }:
        return _flat_component_naming_room_name(
            document,
            physical_document_by_id,
            room_name=room_name,
            depth=len(hierarchy_documents) - 1,
            style=style,
            channel_differentiate_values=channel_differentiate_values,
        )
    return _path_component_naming_room_name(
        multi_documents,
        physical_document_by_id,
        multi_start=multi_start,
        style=style,
        channel_global_indices=channel_global_indices,
        compile_options=compile_options,
    )


def _physical_hierarchy_documents(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: Mapping[str, AltiumCompiledPhysicalDocument],
) -> list[AltiumCompiledPhysicalDocument]:
    hierarchy_documents: list[AltiumCompiledPhysicalDocument] = []
    current: AltiumCompiledPhysicalDocument | None = document
    while current is not None and current.parent_id is not None:
        hierarchy_documents.append(current)
        current = physical_document_by_id.get(current.parent_id)
    hierarchy_documents.reverse()
    return hierarchy_documents


def _multi_channel_start(
    hierarchy_documents: Sequence[AltiumCompiledPhysicalDocument],
    physical_count_by_logical_id: Mapping[str, int],
) -> int | None:
    return next(
        (
            index
            for index, path_document in enumerate(hierarchy_documents)
            if physical_count_by_logical_id.get(path_document.logical_document_id, 0)
            > 1
        ),
        None,
    )


def _has_duplicate_bottom_designator(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: Mapping[str, AltiumCompiledPhysicalDocument],
) -> bool:
    source_key = dotnet_ordinal_ignore_case_key(document.room_name)
    return any(
        row.parent_id is not None
        and row.logical_document_id == document.logical_document_id
        and row._managed_parent_sheet_symbol_source_id
        != document._managed_parent_sheet_symbol_source_id
        and dotnet_ordinal_ignore_case_key(row.room_name) == source_key
        for row in physical_document_by_id.values()
    )


def _flat_component_naming_room_name(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    room_name: str,
    depth: int,
    style: int,
    channel_differentiate_values: Mapping[str, int] | None,
) -> str:
    differentiate_values = channel_differentiate_values
    if differentiate_values is None:
        differentiate_values = _channel_differentiate_values(physical_document_by_id)
    instance_index = differentiate_values[document.id]
    part_name = (
        document.channel_prefix
        if _is_repeat_channel_document(document, physical_document_by_id)
        else room_name
    )
    suffix = _compiled_room_suffix(instance_index, style=style, depth=depth)
    return f"{part_name or room_name}{suffix}"


def _path_component_naming_room_name(
    multi_documents: Sequence[AltiumCompiledPhysicalDocument],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    multi_start: int,
    style: int,
    channel_global_indices: Mapping[str, int] | None,
    compile_options: AltiumProjectCompileOptions,
) -> str:
    separator = compile_options.channel_room_level_separator or "_"
    parts: list[str] = []
    global_indices = channel_global_indices
    if global_indices is None:
        global_indices = _channel_global_indices(physical_document_by_id)
    for depth, path_document in enumerate(
        multi_documents,
        start=multi_start,
    ):
        is_repeat = _is_repeat_channel_document(path_document, physical_document_by_id)
        part_name = (
            path_document.channel_prefix if is_repeat else path_document.room_name
        ) or Path(path_document.file_name).stem
        uses_alpha = _compiled_room_style_uses_alpha(style, depth)
        index = (
            _managed_repeat_channel_value(path_document)
            + int(compile_options.new_indexing_of_sheet_symbols and uses_alpha)
            if is_repeat
            else global_indices[path_document.id] + 1
        )
        parts.append(
            f"{part_name}{_compiled_room_suffix(index, style=style, depth=depth)}"
        )
    return separator.join(parts)


def _room_details_for_compiled_naming(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    physical_instance_offsets: dict[str, int],
    physical_count_by_logical_id: dict[str, int],
    channel_global_indices: Mapping[str, int] | None = None,
    channel_differentiate_values: Mapping[str, int] | None = None,
    compile_options: AltiumProjectCompileOptions,
    sheet_designator: str = "",
) -> RoomDetails:
    instance_offset = physical_instance_offsets.get(document.id, 0)
    room = _room_details_for_physical_document(
        document,
        instance_offset=instance_offset,
        sheet_designator=sheet_designator,
    )
    room = replace(
        room,
        room_name=_component_naming_room_name(
            document,
            physical_document_by_id,
            physical_count_by_logical_id=physical_count_by_logical_id,
            channel_global_indices=channel_global_indices,
            channel_differentiate_values=channel_differentiate_values,
            compile_options=compile_options,
        ),
    )
    # Altium substitutes the designator-sorted channel rank into every
    # channel designator format; there is no format gate.
    rank = _channel_rank_for_document(
        document,
        physical_document_by_id,
        channel_global_indices=channel_global_indices,
    )
    room = replace(
        room,
        channel_index=str(rank),
        channel_alpha=_compiled_alpha_index(rank),
    )
    # AD26 channel-prefix resolution: discrete channels use the bottom
    # designator, but Repeat() channels use the repeat base name, which the
    # physical document already carries as channel_prefix.
    if not _is_repeat_channel_document(document, physical_document_by_id):
        room = replace(
            room,
            channel_prefix=document.room_name or room.channel_prefix,
        )
    return room


def _channel_rank_for_document(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    channel_global_indices: Mapping[str, int] | None = None,
) -> int:
    """Return the 1-based rank of a channel among its child document's instances.

    Altium's ``$ChannelIndex`` is the hierarchy-path-sorted position of the
    channel among ALL physical instances of the child schematic: paths are
    compared level by level from the root on ``designator + localIndex``
    (alphanumeric), where the local index orders the distinct channels of the
    child schematic by (parent schematic name, channel name). It is not the
    instantiation order, the designator's trailing digits, or the Repeat()
    value.
    """
    global_indices = channel_global_indices
    if global_indices is None:
        global_indices = _channel_global_indices(physical_document_by_id)
    return global_indices.get(document.id, 0) + 1


def _channel_global_indices(
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
) -> dict[str, int]:
    local_indices = _channel_local_indices(physical_document_by_id)

    def path_levels(row: AltiumCompiledPhysicalDocument) -> tuple[str, ...]:
        levels: list[str] = []
        node: AltiumCompiledPhysicalDocument | None = row
        while node is not None and node.parent_id:
            levels.append(f"{node.room_name}{local_indices[node.id]}")
            node = physical_document_by_id.get(node.parent_id)
        return tuple(reversed(levels))

    rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]] = defaultdict(
        list
    )
    for row in physical_document_by_id.values():
        rows_by_logical_id[row.logical_document_id].append(row)
    result: dict[str, int] = {}
    for siblings in rows_by_logical_id.values():
        siblings.sort(
            key=cmp_to_key(
                lambda left, right: _managed_hierarchy_path_compare(
                    path_levels(left),
                    path_levels(right),
                )
            )
        )
        result.update((row.id, rank) for rank, row in enumerate(siblings))
    return result


def _channel_differentiate_values(
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    channel_global_indices: Mapping[str, int] | None = None,
) -> dict[str, int]:
    global_indices = channel_global_indices
    if global_indices is None:
        global_indices = _channel_global_indices(physical_document_by_id)
    rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]] = defaultdict(
        list
    )
    for row in physical_document_by_id.values():
        if row.parent_id is not None:
            rows_by_logical_id[row.logical_document_id].append(row)

    result: dict[str, int] = {}
    for rows in rows_by_logical_id.values():
        # Stable successive ordering starts from the globally sorted path list
        # before walking adjacent ordinal-ignore-case designator groups.
        rows.sort(key=lambda row: global_indices[row.id])

        def compare(
            left: AltiumCompiledPhysicalDocument, right: AltiumCompiledPhysicalDocument
        ) -> int:
            order = managed_en_us_compare(left.room_name, right.room_name)
            if order:
                return order
            left_value = (
                _managed_repeat_channel_value(left)
                if left._managed_parent_from_repeat_sheet_symbol
                else -1
            )
            right_value = (
                _managed_repeat_channel_value(right)
                if right._managed_parent_from_repeat_sheet_symbol
                else -1
            )
            return _compare_values(left_value, right_value)

        rows.sort(key=cmp_to_key(compare))
        previous_designator: str | None = None
        sequence = 0
        for row in rows:
            if previous_designator is None or (
                dotnet_ordinal_ignore_case_key(previous_designator)
                != dotnet_ordinal_ignore_case_key(row.room_name)
            ):
                sequence = 1
            else:
                sequence += 1
            result[row.id] = (
                global_indices[row.id] + 1
                if row._managed_parent_from_repeat_sheet_symbol
                else sequence
            )
            previous_designator = row.room_name
    return result


def _channel_differentiate_values_for_room_style(
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    channel_global_indices: Mapping[str, int],
    style: int,
) -> dict[str, int]:
    if style not in {
        ChannelRoomNamingStyle.FLAT_NUMERIC_WITH_NAMES,
        ChannelRoomNamingStyle.FLAT_ALPHA_WITH_NAMES,
    }:
        return {}
    return _channel_differentiate_values(
        physical_document_by_id,
        channel_global_indices=channel_global_indices,
    )


@dataclass(frozen=True)
class _ManagedChannelInfo:
    schematic_name: str
    channel_name: str
    from_repeat_sheet_symbol: bool
    sheet_symbol_index: int
    sheet_symbol_location: tuple[int, int]
    sheet_symbol_id: str


def _compare_values(
    left: bytes | int | tuple[int, int],
    right: bytes | int | tuple[int, int],
) -> int:
    if isinstance(left, bytes) and isinstance(right, bytes):
        return (left > right) - (left < right)
    if isinstance(left, int) and isinstance(right, int):
        return (left > right) - (left < right)
    if isinstance(left, tuple) and isinstance(right, tuple):
        return (left > right) - (left < right)
    raise TypeError("managed comparison values must have matching types")


def _managed_hierarchy_path_compare(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> int:
    for left_level, right_level in zip(left, right, strict=False):
        result = managed_alpha_numeric_compare(left_level, right_level)
        if result:
            return result
    return _compare_values(len(left), len(right))


def _managed_channel_info(
    row: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
) -> _ManagedChannelInfo:
    parent = physical_document_by_id.get(row.parent_id or "")
    return _ManagedChannelInfo(
        schematic_name=Path(parent.file_name).stem if parent is not None else "",
        channel_name=row.room_name,
        from_repeat_sheet_symbol=row._managed_parent_from_repeat_sheet_symbol,
        sheet_symbol_index=row._managed_parent_sheet_symbol_index,
        sheet_symbol_location=row._managed_parent_sheet_symbol_location,
        sheet_symbol_id=row._managed_parent_sheet_symbol_source_id,
    )


def _managed_channel_identity(info: _ManagedChannelInfo) -> tuple[object, ...]:
    return (
        dotnet_ordinal_ignore_case_sort_key(info.schematic_name),
        dotnet_ordinal_ignore_case_sort_key(info.channel_name),
        info.from_repeat_sheet_symbol,
        info.sheet_symbol_index,
        info.sheet_symbol_location,
        info.sheet_symbol_id,
    )


def _managed_int32_subtract(left: int, right: int) -> int:
    """Return wrapping signed 32-bit subtraction for valid source indices."""
    return ((left - right + (1 << 31)) % (1 << 32)) - (1 << 31)


def _managed_channel_info_compare(
    left: _ManagedChannelInfo,
    right: _ManagedChannelInfo,
) -> int:
    result = _compare_values(
        dotnet_ordinal_ignore_case_sort_key(left.schematic_name),
        dotnet_ordinal_ignore_case_sort_key(right.schematic_name),
    )
    if result:
        return result
    result = managed_alpha_numeric_compare(left.channel_name, right.channel_name)
    if result:
        return result
    result = _compare_values(
        left.from_repeat_sheet_symbol,
        right.from_repeat_sheet_symbol,
    )
    if result:
        return result
    result = _managed_int32_subtract(
        right.sheet_symbol_index,
        left.sheet_symbol_index,
    )
    if result:
        return result
    result = _compare_values(
        right.sheet_symbol_location,
        left.sheet_symbol_location,
    )
    if result:
        return result
    return _compare_values(
        dotnet_ordinal_ignore_case_sort_key(left.sheet_symbol_id),
        dotnet_ordinal_ignore_case_sort_key(right.sheet_symbol_id),
    )


def _channel_local_indices(
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
) -> dict[str, int]:
    """Local channel index per physical document id.

    Mirrors Altium's per-child ``ChannelInfo`` sort: the distinct channels of
    each child schematic (deduplicated across parent instances) are ordered by
    (parent schematic name, channel name alphanumeric) and indexed from 0.
    """
    rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]] = defaultdict(
        list
    )
    for row in physical_document_by_id.values():
        rows_by_logical_id[row.logical_document_id].append(row)
    local_indices: dict[str, int] = {}
    for rows in rows_by_logical_id.values():
        info_by_identity: dict[tuple[object, ...], _ManagedChannelInfo] = {}
        for row in rows:
            info = _managed_channel_info(row, physical_document_by_id)
            info_by_identity.setdefault(_managed_channel_identity(info), info)
        ordered = sorted(
            info_by_identity,
            key=cmp_to_key(
                lambda left, right: _managed_channel_info_compare(
                    info_by_identity[left],
                    info_by_identity[right],
                )
            ),
        )
        index_by_identity = {identity: index for index, identity in enumerate(ordered)}
        for row in rows:
            info = _managed_channel_info(row, physical_document_by_id)
            local_indices[row.id] = index_by_identity[_managed_channel_identity(info)]
    return local_indices


def _logical_instance_offsets(
    physical_documents: Iterable[AltiumCompiledPhysicalDocument],
) -> dict[str, int]:
    rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]] = defaultdict(
        list
    )
    for row in physical_documents:
        rows_by_logical_id[row.logical_document_id].append(row)
    return {
        row.id: instance_offset
        for logical_rows in rows_by_logical_id.values()
        for instance_offset, row in enumerate(logical_rows)
    }


def _physical_count_by_logical_id(
    physical_documents: Iterable[AltiumCompiledPhysicalDocument],
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in physical_documents:
        counts[row.logical_document_id] += 1
    return dict(counts)


def _project_design_int(
    project: "AltiumPrjPcb | None",
    key: str,
    default: int,
) -> int:
    if project is None or not project.config.has_option("Design", key):
        return default
    try:
        return int(project.config.get("Design", key))
    except ValueError:
        return default


def _project_design_bool(
    project: "AltiumPrjPcb | None",
    key: str,
    default: bool,
) -> bool:
    if project is None or not project.config.has_option("Design", key):
        return default
    try:
        return project.config.getboolean("Design", key)
    except ValueError:
        return default


def _project_design_str(
    project: "AltiumPrjPcb | None",
    key: str,
    default: str,
) -> str:
    if project is None or not project.config.has_option("Design", key):
        return default
    return project.config.get("Design", key, fallback=default)


def _project_design_str_any(
    project: "AltiumPrjPcb | None",
    keys: tuple[str, ...],
    default: str,
) -> str:
    for key in keys:
        value = _project_design_str(project, key, "")
        if value:
            return value
    return default


def _compile_options_from_netlist_options(
    options: NetlistOptions,
    *,
    effective_hierarchy_mode: str,
    project: "AltiumPrjPcb | None",
    allow_device_sheet_editing: bool,
) -> AltiumProjectCompileOptions:
    scope = options.net_identifier_scope
    option_sources = _project_option_sources(project)
    return AltiumProjectCompileOptions(
        hierarchy_mode=scope.name,
        hierarchy_mode_value=int(scope.value),
        effective_hierarchy_mode=effective_hierarchy_mode,
        allow_port_net_names=options.allow_ports_to_name_nets,
        allow_sheet_entry_net_names=options.allow_sheet_entries_to_name_nets,
        netlist_single_pin_nets=options.allow_single_pin_nets,
        append_sheet_number_to_local_nets=(options.append_sheet_numbers_to_local_nets),
        power_port_names_take_priority=options.power_port_names_take_priority,
        name_nets_hierarchically=options.higher_level_names_take_priority,
        auto_sheet_numbering=options.auto_sheet_numbering,
        allow_device_sheet_editing=allow_device_sheet_editing,
        channel_designator_format=options.channel_designator_format,
        channel_room_naming_style=_project_design_int(
            project,
            "ChannelRoomNamingStyle",
            0,
        ),
        channel_room_level_separator=_project_design_str_any(
            project,
            ("ChannelRoomLevelSeperator", "ChannelRoomLevelSeparator"),
            "_",
        ),
        new_indexing_of_sheet_symbols=_project_design_bool(
            project,
            "NewIndexingOfSheetSymbols",
            True,
        ),
        reorder_documents_on_compile=_project_design_bool(
            project,
            "ReorderDocumentsOnCompile",
            False,
        ),
        push_eco_to_annotation_file=_project_design_bool(
            project,
            "PushECOToAnnotationFile",
            False,
        ),
        option_sources=option_sources,
        deferred_options=("UniqueIdsMappings",),
    )


def _project_option_sources(project: "AltiumPrjPcb | None") -> dict[str, str]:
    if project is None:
        return {}

    design_section = "Design"

    def source_for(key: str) -> str:
        if project.config.has_option(design_section, key):
            return "project"
        return "default"

    def source_for_any(*keys: str) -> str:
        if any(project.config.has_option(design_section, key) for key in keys):
            return "project"
        return "default"

    return {
        "hierarchy_mode": source_for("HierarchyMode"),
        "allow_port_net_names": source_for("AllowPortNetNames"),
        "allow_sheet_entry_net_names": source_for("AllowSheetEntryNetNames"),
        "netlist_single_pin_nets": source_for("NetlistSinglePinNets"),
        "append_sheet_number_to_local_nets": source_for("AppendSheetNumberToLocalNets"),
        "power_port_names_take_priority": source_for("PowerPortNamesTakePriority"),
        "name_nets_hierarchically": source_for("NameNetsHierarchically"),
        "auto_sheet_numbering": source_for("AutoSheetNumbering"),
        "allow_device_sheet_editing": "compile_argument",
        "channel_designator_format": source_for("ChannelDesignatorFormatString"),
        "channel_room_naming_style": source_for("ChannelRoomNamingStyle"),
        "channel_room_level_separator": source_for_any(
            "ChannelRoomLevelSeperator",
            "ChannelRoomLevelSeparator",
        ),
        "new_indexing_of_sheet_symbols": source_for("NewIndexingOfSheetSymbols"),
        "reorder_documents_on_compile": source_for("ReorderDocumentsOnCompile"),
        "push_eco_to_annotation_file": source_for("PushECOToAnnotationFile"),
        "effective_hierarchy_mode": "derived",
    }


def _effective_bridge_scope_options(
    options: NetlistOptions,
    *,
    effective_hierarchy_mode: str,
) -> NetlistOptions | None:
    if options.net_identifier_scope != NetIdentifierScope.AUTOMATIC:
        return None
    if effective_hierarchy_mode not in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
        return None
    return replace(
        options,
        net_identifier_scope=NetIdentifierScope[effective_hierarchy_mode],
    )


def _compiled_only_retained_single_pin_net(net: object) -> bool:
    return bool(getattr(net, "_single_pin_retention_only", False))


def _effective_bridge_baseline_nets(netlist: object) -> tuple[object, ...]:
    return tuple(
        net
        for net in cast(Iterable[object], getattr(netlist, "nets", ()) or ())
        if not _compiled_only_retained_single_pin_net(net)
    )


def _local_net_by_wire_root(
    local_nets: Sequence[object],
    root_by_wire_id: Mapping[str, RootPoint],
    wire_graph: object,
    wire_index: object,
) -> dict[RootPoint, object]:
    net_by_wire_root: dict[RootPoint, object] = {}
    for net in local_nets:
        for wire_id in getattr(getattr(net, "graphical", None), "wires", ()):
            wire_root = root_by_wire_id.get(wire_id)
            if wire_root is not None:
                net_by_wire_root.setdefault(wire_root, net)
        for endpoint in getattr(net, "endpoints", ()):
            connection_point = getattr(endpoint, "connection_point", None)
            if connection_point is None:
                continue
            wire_root = _wire_network_root_for_point(
                wire_graph,
                wire_index,
                connection_point,
            )
            if wire_root is not None:
                net_by_wire_root.setdefault(wire_root, net)
    return net_by_wire_root


def _add_net_label_roots_to_local_net_map(
    schdoc: "AltiumSchDoc",
    local_nets: Sequence[object],
    wire_graph: object,
    wire_index: object,
    net_by_wire_root: dict[RootPoint, object],
    internal_tolerance: int,
) -> None:
    """Map every labeled wire root to AD's same-name local net."""
    find_connections = getattr(wire_index, "find_wire_connections_for_netlabel", None)
    find_root = getattr(wire_graph, "find", None)
    if not callable(find_connections) or not callable(find_root):
        return
    net_by_name = {
        str(getattr(net, "name", "") or "").lower(): net
        for net in local_nets
        if str(getattr(net, "name", "") or "")
    }
    for label in schdoc.get_net_labels():
        if getattr(label.record, "parent", None) is not None:
            continue
        label_name = str(getattr(label, "text", "") or "")
        net = net_by_name.get(label_name.lower())
        if net is None:
            continue
        connections = cast(
            Iterable[RootPoint],
            find_connections(label, internal_tolerance),
        )
        for connection in connections:
            net_by_wire_root.setdefault(cast(RootPoint, find_root(connection)), net)


def _local_net_for_precise_harness_connection_point(
    connection_point: tuple[int, int, int, int],
    *,
    internal_tolerance: int,
    wire_graph: object,
    wire_index: object,
    net_by_wire_root: Mapping[RootPoint, object],
) -> object | None:
    wire_root = _wire_network_root_for_precise_point(
        wire_graph,
        wire_index,
        connection_point,
        internal_tolerance,
    )
    if wire_root is None:
        return None
    return net_by_wire_root.get(wire_root)


def _append_local_net_endpoint(
    net: object,
    *,
    endpoint_id: str,
    role: str,
    element_id: str = "",
    object_id: str = "",
    name: str = "",
    connection_point: tuple[int, int] | None = None,
    source_occurrence_id: str = "",
    bus_signal_index: int | None = None,
    harness_entries_path: tuple[str, ...] = (),
    repeat_value: int | None = None,
    harness_type_name: str = "",
    harness_interface_name: str = "",
    harness_type_inferred: bool = False,
    endpoint_id_is_unique: bool = False,
) -> None:
    endpoints = cast(list[NetEndpoint], getattr(net, "endpoints", []))
    if not endpoint_id_is_unique and any(
        endpoint.endpoint_id == endpoint_id for endpoint in endpoints
    ):
        return
    endpoints.append(
        NetEndpoint(
            endpoint_id=endpoint_id,
            role=role,
            element_id=element_id,
            object_id=object_id,
            name=name,
            connection_point=connection_point,
            _source_occurrence_id=source_occurrence_id,
            _bus_signal_index=bus_signal_index,
            _harness_entries_path=harness_entries_path,
            _repeat_value=repeat_value,
            _harness_type_name=harness_type_name,
            _harness_interface_name=harness_interface_name,
            _harness_type_inferred=harness_type_inferred,
        )
    )


@dataclass(slots=True)
class _LocalNetEndpointIdAllocator:
    occupied: set[str]
    next_duplicate_by_base: dict[str, int]

    @classmethod
    def from_net(cls, net: object) -> _LocalNetEndpointIdAllocator:
        endpoints = cast(list[NetEndpoint], getattr(net, "endpoints", []))
        return cls(
            occupied={endpoint.endpoint_id for endpoint in endpoints},
            next_duplicate_by_base={},
        )

    def allocate(self, base_id: str) -> str:
        if base_id not in self.occupied:
            self.occupied.add(base_id)
            return base_id
        duplicate_index = self.next_duplicate_by_base.get(base_id, 1)
        candidate = f"{base_id}:duplicate:{duplicate_index}"
        while candidate in self.occupied:
            duplicate_index += 1
            candidate = f"{base_id}:duplicate:{duplicate_index}"
        self.next_duplicate_by_base[base_id] = duplicate_index + 1
        self.occupied.add(candidate)
        return candidate


def _local_net_endpoint_id_allocator(
    net: object,
    allocators: dict[int, _LocalNetEndpointIdAllocator],
) -> _LocalNetEndpointIdAllocator:
    net_identity = id(net)
    allocator = allocators.get(net_identity)
    if allocator is None:
        allocator = _LocalNetEndpointIdAllocator.from_net(net)
        allocators[net_identity] = allocator
    return allocator


def _endpoint_id_allocator_map(
    allocators: dict[int, _LocalNetEndpointIdAllocator] | None,
) -> dict[int, _LocalNetEndpointIdAllocator]:
    return {} if allocators is None else allocators


def _annotate_sheet_symbol_harness_ports(
    schdoc: "AltiumSchDoc",
    local_nets: Sequence[object],
) -> None:
    for sheet_symbol in _compiler_sheet_symbol_sources(schdoc):
        sheet_symbol_uid = str(getattr(sheet_symbol, "unique_id", "") or "")
        for entry in sheet_symbol.entries:
            harness_type = str(getattr(entry, "harness_type", "") or "")
            entry_name = str(
                getattr(entry, "display_name", "") or getattr(entry, "name", "") or ""
            )
            if not harness_type or not sheet_symbol_uid or not entry_name:
                continue
            sheet_entry_id = f"{sheet_symbol_uid}_{entry_name}"
            for net in local_nets:
                endpoints = cast(list[NetEndpoint], getattr(net, "endpoints", []))
                if any(
                    endpoint.role == "sheet_entry"
                    and endpoint.element_id.lower() == sheet_entry_id.lower()
                    for endpoint in endpoints
                ):
                    _prefer_managed_local_net_spelling(net, entry_name)
                    _append_local_net_endpoint(
                        net,
                        endpoint_id=f"harness_port:{sheet_entry_id}",
                        role="harness_port",
                        element_id=sheet_entry_id,
                        object_id=str(getattr(entry, "unique_id", "") or ""),
                        name=entry_name,
                    )


def _prefer_managed_local_net_spelling(net: object, candidate: str) -> None:
    current = str(getattr(net, "name", "") or "")
    if (
        dotnet_ordinal_ignore_case_key(candidate)
        == dotnet_ordinal_ignore_case_key(current)
        and _compiled_managed_source_name_comparison(candidate, current) < 0
    ):
        setattr(net, "name", candidate)


def _annotate_connector_harness_port(
    local_nets: Sequence[object],
    harness_port_name: str,
) -> None:
    for net in local_nets:
        for endpoint in cast(list[NetEndpoint], getattr(net, "endpoints", [])):
            if (
                endpoint.role == "port"
                and endpoint.name.lower() == harness_port_name.lower()
            ):
                _append_local_net_endpoint(
                    net,
                    endpoint_id=f"harness_port:{endpoint.element_id}",
                    role="harness_port",
                    element_id=endpoint.element_id,
                    object_id=endpoint.object_id,
                    name=harness_port_name,
                )


def _local_net_for_harness_entry(
    connection_point: tuple[int, int, int, int],
    *,
    internal_tolerance: int,
    wire_graph: object,
    wire_index: object,
    net_by_wire_root: Mapping[RootPoint, object],
) -> object | None:
    return _local_net_for_precise_harness_connection_point(
        connection_point,
        internal_tolerance=internal_tolerance,
        wire_graph=wire_graph,
        wire_index=wire_index,
        net_by_wire_root=net_by_wire_root,
    )


def _annotate_harness_connector_entries(
    connector: "AltiumSchHarnessConnector",
    harness_port_name: str,
    stats: dict[str, int],
    *,
    provider: tuple[str, str, int, str] | None,
    source_connection_link_id: str,
    internal_tolerance: int,
    wire_graph: object,
    wire_index: object,
    net_by_wire_root: Mapping[RootPoint, object],
    compile_masks: Sequence[tuple[int, int, int, int]] = (),
    providers: Sequence[tuple[str, str, int, str]] = (),
    endpoint_id_allocators: dict[int, _LocalNetEndpointIdAllocator] | None = None,
    source_entries: Iterable[object] | None = None,
) -> None:
    allocators = _endpoint_id_allocator_map(endpoint_id_allocators)
    for entry in connector.entries if source_entries is None else source_entries:
        entry_name = str(getattr(entry, "name", "") or "")
        if not entry_name or _compiled_point_inside_any_mask(
            _harness_entry_connection_point(connector, entry),
            compile_masks,
        ):
            continue
        stats["harness_entry_candidate_count"] += 1
        if _annotate_harness_connector_entry(
            connector,
            entry,
            entry_name=entry_name,
            harness_port_name=harness_port_name,
            provider=provider,
            providers=providers,
            source_connection_link_id=source_connection_link_id,
            internal_tolerance=internal_tolerance,
            wire_graph=wire_graph,
            wire_index=wire_index,
            net_by_wire_root=net_by_wire_root,
            endpoint_id_allocators=allocators,
        ):
            stats["harness_entry_matched_count"] += 1


def _annotate_harness_connector_entry(
    connector: "AltiumSchHarnessConnector",
    entry: object,
    *,
    entry_name: str,
    harness_port_name: str,
    provider: tuple[str, str, int, str] | None,
    source_connection_link_id: str,
    internal_tolerance: int,
    wire_graph: object,
    wire_index: object,
    net_by_wire_root: Mapping[RootPoint, object],
    providers: Sequence[tuple[str, str, int, str]] = (),
    endpoint_id_allocators: dict[int, _LocalNetEndpointIdAllocator] | None = None,
) -> bool:
    precise_connection_point = _harness_entry_connection_point(connector, entry)
    net = _local_net_for_harness_entry(
        precise_connection_point,
        internal_tolerance=internal_tolerance,
        wire_graph=wire_graph,
        wire_index=wire_index,
        net_by_wire_root=net_by_wire_root,
    )
    if net is None:
        return False

    entry_id = str(getattr(entry, "unique_id", "") or "")
    allocators = _endpoint_id_allocator_map(endpoint_id_allocators)
    endpoint_id = _local_net_endpoint_id_allocator(net, allocators).allocate(
        f"harness_entry:{entry_id or entry_name}"
    )
    provider_name = provider[0] if provider is not None else harness_port_name
    endpoint_name = f"{provider_name}.{entry_name}" if provider_name else entry_name
    _append_local_net_endpoint(
        net,
        endpoint_id=endpoint_id,
        role="harness_entry",
        element_id=entry_id,
        object_id=entry_id,
        name=endpoint_name,
        connection_point=precise_connection_point[:2],
        endpoint_id_is_unique=True,
    )
    relation_providers = providers or (() if provider is None else (provider,))
    for (
        source_name,
        source_kind,
        source_priority,
        source_element_id,
    ) in relation_providers:
        candidate_value = f"{source_name}.{entry_name}" if source_name else entry_name
        _append_signal_harness_name_candidate(
            net,
            _signal_harness_name_candidate(
                value=candidate_value,
                value_from_source_object=source_name,
                source_kind=source_kind,
                source_priority=source_priority,
                source_element_id=source_element_id,
                source_connection_link_id=source_connection_link_id,
                harness_entries_path=(entry_name,),
            ),
        )
    return True


def _append_signal_harness_name_candidate(
    net: object,
    candidate: _SignalHarnessNameCandidate,
) -> None:
    existing = tuple(
        cast(
            Iterable[_SignalHarnessNameCandidate],
            getattr(net, "_signal_harness_name_candidates", ()),
        )
    )
    if candidate not in existing:
        setattr(net, "_signal_harness_name_candidates", (*existing, candidate))


def _signal_harness_name_candidate(
    *,
    value: str,
    value_from_source_object: str,
    source_kind: str,
    source_priority: int,
    source_element_id: str = "",
    source_connection_link_id: str = "",
    harness_entries_path: tuple[str, ...] = (),
    bus_signal_index: int | None = None,
) -> _SignalHarnessNameCandidate:
    bus_prefix, bus_suffix, bus_width, bus_offset = _harness_candidate_bus_context(
        value,
        value_from_source_object,
        harness_entries_path,
        bus_signal_index,
    )
    return _SignalHarnessNameCandidate(
        value=value,
        value_from_source_object=value_from_source_object,
        source_kind=source_kind,
        source_priority=source_priority,
        source_element_id=source_element_id,
        source_connection_link_id=source_connection_link_id,
        harness_entries_path=harness_entries_path,
        bus_signal_index=bus_signal_index,
        bus_signal_width=bus_width,
        bus_signal_offset=bus_offset,
        bus_signal_prefix=bus_prefix,
        bus_signal_suffix=bus_suffix,
    )


def _harness_candidate_bus_context(
    value: str,
    value_from_source_object: str,
    harness_entries_path: tuple[str, ...],
    bus_signal_index: int | None,
) -> tuple[str, str, int, int]:
    if bus_signal_index is None:
        return "", "", 0, 0
    expression = _harness_candidate_bus_expression(
        value,
        value_from_source_object,
        harness_entries_path,
        bus_signal_index,
    )
    bus_range = _parse_managed_bus_range(expression)
    if bus_range is None:
        return "", "", 0, 0
    suffix_source = value if value.startswith(bus_range.prefix) else expression
    suffix = (
        suffix_source[len(bus_range.prefix) :]
        if suffix_source.startswith(bus_range.prefix)
        else ""
    )
    return bus_range.prefix, suffix, bus_range.width, bus_range.offset


def _harness_candidate_bus_expression(
    value: str,
    value_from_source_object: str,
    harness_entries_path: tuple[str, ...],
    bus_signal_index: int,
) -> str:
    if bus_signal_index in {-1, -2}:
        return value
    authored_path = ".".join(reversed(harness_entries_path))
    return ".".join(part for part in (value_from_source_object, authored_path) if part)


@dataclass(frozen=True, slots=True)
class _HarnessNamingObject:
    name: str
    kind: str
    points: tuple[RootPoint, ...]
    element_id: str


def _connected_harness_name_provider(
    schdoc: "AltiumSchDoc",
    connector: "AltiumSchHarnessConnector",
    options: NetlistOptions,
    internal_tolerance: int,
) -> tuple[str, str, int, str] | None:
    return _selected_harness_name_provider(
        _connected_harness_name_providers(
            schdoc,
            connector,
            options,
            internal_tolerance,
        )
    )


def _connected_harness_name_providers(
    schdoc: "AltiumSchDoc",
    connector: "AltiumSchHarnessConnector",
    options: NetlistOptions,
    internal_tolerance: int,
) -> tuple[tuple[str, str, int, str], ...]:
    harnesses: list[object] = list(
        cast(Iterable[object], getattr(schdoc, "signal_harnesses", ()) or ())
    )
    connector_master = _harness_connector_master_entry_point(connector)
    connected = _connected_signal_harness_indexes(
        harnesses,
        (connector_master,),
        internal_tolerance,
    )
    providers: list[tuple[str, str, int, str]] = []
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    for item in _harness_naming_objects(schdoc):
        candidate = _connected_harness_naming_candidate(
            item,
            harnesses,
            connected,
            connector_master,
            options,
            internal_tolerance,
            compile_masks,
        )
        if candidate is not None:
            providers.append(candidate)
    selected = _selected_harness_name_provider(providers)
    if selected is None:
        return tuple(providers)
    return (selected, *(provider for provider in providers if provider != selected))


def _selected_harness_name_provider(
    providers: Iterable[tuple[str, str, int, str]],
) -> tuple[str, str, int, str] | None:
    selected: tuple[str, str, int, str] | None = None
    for provider in providers:
        if provider[2] > 0 and _harness_name_provider_wins(provider, selected):
            selected = provider
    return selected


def _connected_harness_link_id(
    schdoc: "AltiumSchDoc",
    connector: "AltiumSchHarnessConnector",
    internal_tolerance: int,
) -> str:
    harnesses = list(
        cast(Iterable[object], getattr(schdoc, "signal_harnesses", ()) or ())
    )
    connected = _connected_signal_harness_indexes(
        harnesses,
        (_harness_connector_master_entry_point(connector),),
        internal_tolerance,
    )
    if not connected:
        return ""
    return str(getattr(harnesses[connected[0]], "unique_id", "") or "")


def _harness_naming_objects(
    schdoc: "AltiumSchDoc",
) -> Iterable[_HarnessNamingObject]:
    for item in schdoc.get_net_labels():
        if getattr(item.record, "parent", None) is None:
            yield _harness_naming_object(item, "net_label")
    for item in schdoc.get_power_ports():
        yield _harness_naming_object(item, "power_port")
    for item in schdoc.get_ports():
        yield _port_harness_naming_object(item)
    yield from _sheet_entry_harness_naming_objects(schdoc)


def _port_harness_naming_object(item: object) -> _HarnessNamingObject:
    return _HarnessNamingObject(
        name=str(getattr(item, "name", "") or ""),
        kind="port",
        points=tuple(
            cast(
                Iterable[RootPoint],
                getattr(item, "_precise_connection_points", ()) or (),
            )
        ),
        element_id=str(getattr(item, "unique_id", "") or ""),
    )


def _sheet_entry_harness_naming_objects(
    schdoc: "AltiumSchDoc",
) -> Iterable[_HarnessNamingObject]:
    for symbol in _compiler_sheet_symbol_sources(schdoc):
        for entry in symbol.entries:
            yield _HarnessNamingObject(
                name=_inter_sheet_entry_display_name(entry),
                kind="sheet_entry",
                points=(_sheet_entry_precise_connection_point(symbol, entry),),
                element_id=str(getattr(entry, "unique_id", "") or ""),
            )


def _harness_naming_object(item: object, kind: str) -> _HarnessNamingObject:
    return _HarnessNamingObject(
        name=str(getattr(item, "text", "") or ""),
        kind=kind,
        points=(cast(RootPoint, getattr(item, "_precise_connection_point")),),
        element_id=str(getattr(item, "unique_id", "") or ""),
    )


def _connected_harness_naming_candidate(
    item: _HarnessNamingObject,
    harnesses: Sequence[object],
    connected: Sequence[int],
    connector_master: RootPoint,
    options: NetlistOptions,
    internal_tolerance: int,
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> tuple[str, str, int, str] | None:
    priority = _managed_name_priority(item.kind, options)
    if (
        not item.name
        or not item.points
        or all(
            _compiled_point_inside_any_mask(point, compile_masks)
            for point in item.points
        )
    ):
        return None
    if not any(
        precise_points_connected(point, connector_master, internal_tolerance)
        or any(
            _point_on_signal_harness(
                point,
                harnesses[harness_index],
                internal_tolerance,
            )
            for harness_index in connected
        )
        for point in item.points
    ):
        return None
    return item.name, item.kind, priority, item.element_id


def _harness_name_provider_wins(
    candidate: tuple[str, str, int, str],
    selected: tuple[str, str, int, str] | None,
) -> bool:
    if selected is None or candidate[2] != selected[2]:
        return selected is None or candidate[2] > selected[2]
    return _compiled_managed_source_name_comparison(candidate[0], selected[0]) < 0


def _annotate_local_harness_entry_endpoints(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    options: NetlistOptions,
) -> dict[str, int]:
    """Attach harness-member endpoint evidence to local nets by source wire UID."""
    stats = {
        "harness_entry_candidate_count": 0,
        "harness_entry_matched_count": 0,
        "harness_entry_nearby_label_fallback_count": 0,
    }
    harness_connectors = getattr(schdoc, "harness_connectors", None)

    (
        wire_graph,
        wire_index,
        root_by_wire_id,
        _representative_wire_id_by_root,
    ) = _wire_network_maps(schdoc)
    port_location_map = _build_port_location_map(schdoc)
    local_nets = list(cast(Iterable[object], getattr(local_netlist, "nets", ())))
    net_by_wire_root = _local_net_by_wire_root(
        local_nets,
        root_by_wire_id,
        wire_graph,
        wire_index,
    )
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    _add_net_label_roots_to_local_net_map(
        schdoc,
        local_nets,
        wire_graph,
        wire_index,
        net_by_wire_root,
        internal_tolerance,
    )
    _annotate_sheet_symbol_harness_ports(schdoc, local_nets)

    if not harness_connectors:
        return stats

    compile_masks = _compiled_compile_mask_bounds(schdoc)
    endpoint_id_allocators: dict[int, _LocalNetEndpointIdAllocator] = {}
    for connector in harness_connectors:
        if _compiled_point_inside_any_mask(
            _harness_connector_master_entry_point(connector),
            compile_masks,
        ):
            continue
        harness_port_name = (
            find_harness_port_name(
                connector,
                getattr(schdoc, "signal_harnesses", None),
                port_location_map,
                internal_tolerance,
            )
            or ""
        )
        if harness_port_name:
            _annotate_connector_harness_port(local_nets, harness_port_name)
        providers = _connected_harness_name_providers(
            schdoc,
            connector,
            options,
            internal_tolerance,
        )
        provider = _selected_harness_name_provider(providers)
        _annotate_harness_connector_entries(
            connector,
            harness_port_name,
            stats,
            source_entries=_compiler_harness_entries(schdoc, connector),
            provider=provider,
            providers=providers,
            source_connection_link_id=_connected_harness_link_id(
                schdoc, connector, internal_tolerance
            ),
            internal_tolerance=internal_tolerance,
            wire_graph=wire_graph,
            wire_index=wire_index,
            net_by_wire_root=net_by_wire_root,
            compile_masks=compile_masks,
            endpoint_id_allocators=endpoint_id_allocators,
        )

    return stats


def _harness_connector_networks(
    schdoc: "AltiumSchDoc",
    harnesses: list[object],
    internal_tolerance: int,
) -> tuple[tuple[str, frozenset[str]], ...]:
    port_location_map = _build_port_location_map(schdoc)
    connector_networks: list[tuple[str, frozenset[str]]] = []
    for connector in getattr(schdoc, "harness_connectors", ()) or ():
        bundle = find_harness_bundle_info(
            connector,
            harnesses,
            port_location_map,
            internal_tolerance,
        )
        port_name = str(bundle.get("port_name", "") or "")
        harness_ids = frozenset(
            str(value)
            for value in cast(Iterable[object], bundle.get("signal_harness_ids", ()))
            if value
        )
        if port_name and harness_ids:
            connector_networks.append((port_name, harness_ids))
    return tuple(connector_networks)


def _harness_power_member_port_names(
    power_object: object,
    harnesses: list[object],
    connector_networks: tuple[tuple[str, frozenset[str]], ...],
    internal_tolerance: int,
) -> tuple[set[str], frozenset[str]]:
    location = cast(
        tuple[int, int, int, int],
        getattr(power_object, "_precise_connection_point"),
    )
    connected_indexes = _connected_signal_harness_indexes(
        harnesses,
        (location,),
        internal_tolerance,
    )
    connected_ids = frozenset(_signal_harness_ids(harnesses, connected_indexes))
    port_names = {
        port_name
        for port_name, harness_ids in connector_networks
        if connected_ids.intersection(harness_ids)
    }
    return port_names, connected_ids


def _net_has_harness_member(
    net: object,
    port_names: set[str],
    connected_harness_ids: frozenset[str],
) -> bool:
    endpoints = cast(list[NetEndpoint], getattr(net, "endpoints", []))
    structural_name_match = any(
        endpoint.role == "harness_entry"
        and endpoint.name.partition(".")[0] in port_names
        for endpoint in endpoints
    )
    candidate_link_match = any(
        candidate.source_connection_link_id in connected_harness_ids
        for candidate in cast(
            Iterable[_SignalHarnessNameCandidate],
            getattr(net, "_signal_harness_name_candidates", ()),
        )
        if candidate.source_connection_link_id
    )
    return structural_name_match or candidate_link_match


def _annotate_harness_power_object(
    power_object: object,
    *,
    harnesses: list[object],
    connector_networks: tuple[tuple[str, frozenset[str]], ...],
    local_nets: list[object],
    internal_tolerance: int,
) -> None:
    location = cast(
        tuple[int, int, int, int],
        getattr(power_object, "_precise_connection_point"),
    )
    port_names, connected_harness_ids = _harness_power_member_port_names(
        power_object,
        harnesses,
        connector_networks,
        internal_tolerance,
    )
    if not port_names and not connected_harness_ids:
        return
    object_id = str(getattr(power_object, "unique_id", "") or "")
    object_name = str(getattr(power_object, "text", "") or "")
    for net in local_nets:
        if not _net_has_harness_member(net, port_names, connected_harness_ids):
            continue
        _append_local_net_endpoint(
            net,
            endpoint_id=f"harness_power:{object_id or object_name}",
            role="harness_power",
            element_id=object_id,
            object_id=object_id,
            name=object_name,
            connection_point=location[:2],
        )


def _annotate_harness_power_object_endpoints(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
) -> None:
    """Attach a harness-network power object to each member net."""
    harnesses = list(
        cast(Iterable[object], getattr(schdoc, "signal_harnesses", ()) or ())
    )
    if not harnesses or not getattr(schdoc, "harness_connectors", None):
        return
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    connector_networks = _harness_connector_networks(
        schdoc,
        harnesses,
        internal_tolerance,
    )

    local_nets = list(cast(Iterable[object], getattr(local_netlist, "nets", ())))
    for power_object in schdoc.get_power_ports():
        _annotate_harness_power_object(
            power_object,
            harnesses=harnesses,
            connector_networks=connector_networks,
            local_nets=local_nets,
            internal_tolerance=internal_tolerance,
        )


def _harness_wire_endpoint_groups(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
) -> tuple[tuple[str, tuple[_HarnessWireEndpoint, ...]], ...]:
    represented_wire_ids = {
        wire_id
        for net in getattr(local_netlist, "nets", ())
        for wire_id in getattr(getattr(net, "graphical", None), "wires", ())
    }
    harness_connectors = getattr(schdoc, "harness_connectors", None)
    if not harness_connectors:
        return ()

    wire_endpoint_map = _build_wire_endpoint_map(schdoc)
    port_location_map = _build_port_location_map(schdoc)
    endpoints_by_wire_id: dict[str, list[_HarnessWireEndpoint]] = defaultdict(list)
    type_names = _harness_connector_type_names(harness_connectors)
    for connector in harness_connectors:
        _append_harness_connector_wire_endpoints(
            endpoints_by_wire_id,
            connector=connector,
            schdoc=schdoc,
            type_names=type_names,
            wire_endpoint_map=wire_endpoint_map,
            represented_wire_ids=represented_wire_ids,
            port_location_map=cast(dict[tuple[int, ...], str], port_location_map),
        )
    _append_harness_wire_port_endpoints(
        endpoints_by_wire_id,
        schdoc=schdoc,
        wire_endpoint_map=wire_endpoint_map,
        represented_wire_ids=represented_wire_ids,
    )

    return tuple(
        (wire_id, tuple(endpoints))
        for wire_id, endpoints in sorted(endpoints_by_wire_id.items())
        if len(endpoints) > 1
    )


def _append_harness_connector_wire_endpoints(
    endpoints_by_wire_id: defaultdict[str, list[_HarnessWireEndpoint]],
    *,
    connector: object,
    schdoc: "AltiumSchDoc",
    type_names: Mapping[int, str],
    wire_endpoint_map: Mapping[RootPoint, str],
    represented_wire_ids: set[str],
    port_location_map: dict[tuple[int, ...], str],
) -> None:
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    harness_port_name = find_harness_port_name(
        connector,
        getattr(schdoc, "signal_harnesses", None),
        port_location_map,
        internal_tolerance,
    )
    typed_port_interface = _unique_connected_typed_port_interface(
        schdoc,
        harness_port_name=str(harness_port_name or ""),
        harness_type_name=type_names[id(connector)],
        type_names=type_names,
    )
    for entry in _compiler_harness_entries(schdoc, connector):
        endpoint = _harness_connector_wire_endpoint(
            connector,
            entry,
            harness_port_name=str(harness_port_name or ""),
            typed_port_interface=typed_port_interface,
            wire_endpoint_map=wire_endpoint_map,
            represented_wire_ids=represented_wire_ids,
        )
        if (
            endpoint is not None
            and endpoint not in endpoints_by_wire_id[endpoint.wire_id]
        ):
            endpoints_by_wire_id[endpoint.wire_id].append(endpoint)


def _harness_connector_wire_endpoint(
    connector: object,
    entry: object,
    *,
    harness_port_name: str,
    typed_port_interface: _TypedHarnessInterface | None,
    wire_endpoint_map: Mapping[RootPoint, str],
    represented_wire_ids: set[str],
) -> _HarnessWireEndpoint | None:
    entry_name = str(getattr(entry, "name", "") or "")
    if not entry_name:
        return None
    precise_connection_point = _harness_entry_connection_point(connector, entry)
    wire_id = wire_endpoint_map.get(precise_connection_point)
    if not wire_id or wire_id in represented_wire_ids:
        return None
    endpoint_name = (
        f"{harness_port_name}.{entry_name}" if harness_port_name else entry_name
    )
    source_occurrence_id, harness_path, type_name, interface_name = (
        _harness_wire_typed_identity(typed_port_interface, entry_name)
    )
    return _HarnessWireEndpoint(
        wire_id=wire_id,
        role="harness_entry",
        name=endpoint_name,
        element_id=str(getattr(entry, "unique_id", "") or ""),
        object_id=str(getattr(entry, "unique_id", "") or ""),
        parent_id=_harness_entry_parent_id(endpoint_name),
        connection_point=precise_connection_point[:2],
        source_occurrence_id=source_occurrence_id,
        harness_entries_path=harness_path,
        harness_type_name=type_name,
        harness_interface_name=interface_name,
    )


def _harness_wire_typed_identity(
    interface: _TypedHarnessInterface | None,
    entry_name: str,
) -> tuple[str, tuple[str, ...], str, str]:
    if interface is None:
        return "", (), "", ""
    return (
        interface.source_occurrence_id,
        (entry_name,),
        interface.type_name,
        interface.name,
    )


def _unique_connected_typed_port_interface(
    schdoc: "AltiumSchDoc",
    *,
    harness_port_name: str,
    harness_type_name: str,
    type_names: Mapping[int, str],
) -> _TypedHarnessInterface | None:
    name_key = dotnet_ordinal_ignore_case_key(harness_port_name)
    type_key = dotnet_ordinal_ignore_case_key(harness_type_name)
    matches = tuple(
        interface
        for interface in _typed_harness_interfaces(schdoc)
        if interface.role == "port"
        and dotnet_ordinal_ignore_case_key(interface.name) == name_key
        and dotnet_ordinal_ignore_case_key(interface.type_name) == type_key
        and _typed_port_interface_has_signal_harness_carrier(
            schdoc, interface, type_names
        )
    )
    return matches[0] if len(matches) == 1 else None


def _append_harness_wire_port_endpoints(
    endpoints_by_wire_id: defaultdict[str, list[_HarnessWireEndpoint]],
    *,
    schdoc: "AltiumSchDoc",
    wire_endpoint_map: Mapping[RootPoint, str],
    represented_wire_ids: set[str],
) -> None:
    parent_id = schdoc.filepath.name if schdoc.filepath else ""
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    for port_index, port in _compiler_source_rows(schdoc.get_ports()):
        for endpoint in _harness_wire_port_endpoints_for_port(
            port,
            port_index=port_index,
            parent_id=parent_id,
            compile_masks=compile_masks,
            wire_endpoint_map=wire_endpoint_map,
            represented_wire_ids=represented_wire_ids,
        ):
            if endpoint not in endpoints_by_wire_id[endpoint.wire_id]:
                endpoints_by_wire_id[endpoint.wire_id].append(endpoint)


def _harness_wire_port_endpoints_for_port(
    port: "SchPortInfo",
    *,
    port_index: int,
    parent_id: str,
    compile_masks: Sequence[tuple[int, int, int, int]],
    wire_endpoint_map: Mapping[RootPoint, str],
    represented_wire_ids: set[str],
) -> tuple[_HarnessWireEndpoint, ...]:
    if _port_is_compile_masked(port, compile_masks):
        return ()
    port_id = str(getattr(port, "unique_id", "") or "")
    port_name = str(getattr(port, "name", "") or "")
    return tuple(
        _HarnessWireEndpoint(
            wire_id=wire_id,
            role="port",
            name=port_name,
            element_id=port_id,
            object_id=port_id,
            parent_id=parent_id,
            connection_point=point[:2],
            source_occurrence_id=f"port:{port_index}",
        )
        for point in tuple(getattr(port, "_precise_connection_points", ()) or ())
        if (wire_id := wire_endpoint_map.get(point))
        and wire_id not in represented_wire_ids
    )


def _harness_wire_endpoint_member_name(endpoint_name: str) -> str:
    _harness_name, separator, member_name = endpoint_name.partition(".")
    return member_name if separator and member_name else endpoint_name


@dataclass(frozen=True, slots=True)
class _TypedHarnessInterface:
    name: str
    type_name: str
    role: str
    object_id: str
    source_occurrence_id: str
    type_inferred: bool = False


def _harness_connector_type_name(connector: object) -> str:
    from ._sch_source_projection import (
        _hierarchy_bound_field_slots,
        _record_import_ignores_source,
    )
    from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
    from .altium_record_sch__harness_type import AltiumSchHarnessType

    if isinstance(connector, AltiumSchHarnessConnector):
        bound = connector.type_label
        if (
            isinstance(bound, AltiumSchHarnessType)
            and bound.parent is connector
            and not _record_import_ignores_source(bound)
        ):
            return bound.text or ""
        fields = _hierarchy_bound_field_slots(connector, connector.children)
        return str(getattr(fields.get("type_label"), "text", "") or "")
    type_label = getattr(connector, "type_label", None)
    return str(
        getattr(type_label, "text", "")
        or getattr(type_label, "name", "")
        or getattr(connector, "harness_type", "")
        or ""
    )


def _precise_point_sort_scalars(point: Sequence[int]) -> tuple[int, int]:
    return point[0] * ALTIUM_COORD_SCALE + point[2], point[
        1
    ] * ALTIUM_COORD_SCALE + point[3]


def _harness_connector_type_names(connector_infos: Iterable[object]) -> dict[int, str]:
    result: dict[int, str] = {}
    for info in connector_infos:
        connector = getattr(info, "record", info)
        result[id(connector)] = _harness_connector_type_name(connector)
    return result


def _project_harness_definition_candidate_groups(
    schdocs: Sequence["AltiumSchDoc"],
) -> tuple[tuple[HarnessDefinitionCandidate, ...], ...]:
    return tuple(
        _project_harness_definition_candidates(schdoc, document_index)
        for document_index, schdoc in enumerate(schdocs)
    )


def _project_harness_definition_candidates(
    schdoc: "AltiumSchDoc",
    document_index: int,
) -> tuple[HarnessDefinitionCandidate, ...]:
    connector_infos = tuple(schdoc.get_harness_connectors())
    if not connector_infos:
        return ()
    type_names = _harness_connector_type_names(connector_infos)
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    signal_harnesses = tuple(_schdoc_rows(schdoc, "get_signal_harnesses"))
    non_harness_lines = (
        *_schdoc_rows(schdoc, "get_wires"),
        *_schdoc_rows(schdoc, "get_buses"),
    )
    non_harness_points = _schdoc_non_harness_scalar_connection_points(schdoc)
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0) or 0)
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    return tuple(
        candidate
        for connector_index, connector_info in _compiler_source_rows(connector_infos)
        if (
            candidate := _project_harness_definition_candidate(
                connector_info,
                connector_infos=connector_infos,
                type_names=type_names,
                document_index=document_index,
                connector_index=connector_index,
                signal_harnesses=signal_harnesses,
                non_harness_lines=non_harness_lines,
                non_harness_points=non_harness_points,
                compile_masks=compile_masks,
                internal_tolerance=internal_tolerance,
            )
        )
        is not None
    )


def _project_harness_definition_candidate(
    connector_info: object,
    *,
    connector_infos: Sequence[object],
    type_names: Mapping[int, str],
    document_index: int,
    connector_index: int,
    signal_harnesses: Sequence[object],
    non_harness_lines: Sequence[object],
    non_harness_points: Sequence[RootPoint],
    compile_masks: Sequence[tuple[int, int, int, int]],
    internal_tolerance: int,
) -> HarnessDefinitionCandidate | None:
    connector = getattr(connector_info, "record")
    type_name = type_names[id(connector)]
    if not type_name or _compiled_point_inside_any_mask(
        _harness_connector_master_entry_point(connector), compile_masks
    ):
        return None
    active_entries = sorted(
        (
            (ordinal, entry)
            for ordinal, entry in _compiler_entry_rows(
                connector_info, getattr(connector_info, "entries")
            )
            if getattr(entry, "name", "")
            and not _compiled_point_inside_any_mask(
                _harness_entry_connection_point(connector, entry), compile_masks
            )
        ),
        key=lambda row: _harness_definition_entry_sort_key(connector, row[1]),
    )
    entries = tuple(
        HarnessDefinitionEntry(
            name=str(getattr(entry, "name")),
            nested_type=_harness_definition_entry_nested_type(
                entry,
                connector=connector,
                connector_infos=connector_infos,
                type_names=type_names,
                signal_harnesses=signal_harnesses,
                non_harness_lines=non_harness_lines,
                non_harness_points=non_harness_points,
                compile_masks=compile_masks,
                internal_tolerance=internal_tolerance,
            ),
            source_identity=(
                str(getattr(entry, "unique_id", "") or "")
                or (
                    f"document:{document_index}:connector:{connector_index}:"
                    f"entry:{entry_index}"
                )
            ),
        )
        for entry_index, entry in active_entries
    )
    return HarnessDefinitionCandidate(
        type_name=type_name,
        entries=entries,
        source_identity=f"document:{document_index}:connector:{connector_index}",
    )


def _harness_definition_entry_sort_key(
    connector: object,
    entry: object,
) -> tuple[int, int]:
    x, y = _precise_point_sort_scalars(
        _harness_entry_connection_point(connector, entry)
    )
    return x, -y


def _schdoc_rows(schdoc: object, accessor_name: str) -> tuple[object, ...]:
    accessor = getattr(schdoc, accessor_name, None)
    if callable(accessor):
        return tuple(cast(Iterable[object], accessor()))
    attribute_name = accessor_name.removeprefix("get_")
    return tuple(cast(Iterable[object], getattr(schdoc, attribute_name, ()) or ()))


def _schdoc_non_harness_scalar_connection_points(
    schdoc: object,
) -> tuple[RootPoint, ...]:
    return (
        *_schdoc_port_connection_points(schdoc),
        *_schdoc_record_connection_points(schdoc),
        *_schdoc_pin_connection_points(schdoc),
        *_schdoc_sheet_entry_connection_points(schdoc),
    )


def _schdoc_port_connection_points(schdoc: object) -> tuple[RootPoint, ...]:
    return tuple(
        point
        for port in _schdoc_rows(schdoc, "get_ports")
        for point in cast(
            Iterable[RootPoint],
            getattr(port, "_precise_connection_points", ()) or (),
        )
    )


def _schdoc_record_connection_points(schdoc: object) -> tuple[RootPoint, ...]:
    return tuple(
        point
        for accessor_name in (
            "get_net_labels",
            "get_power_ports",
            "get_cross_sheet_connectors",
        )
        for value in _schdoc_rows(schdoc, accessor_name)
        if (point := _optional_precise_connection_point(value)) is not None
    )


def _optional_precise_connection_point(value: object) -> RootPoint | None:
    point = getattr(value, "_precise_connection_point", None)
    if isinstance(point, tuple) and len(point) == 4:
        return cast(RootPoint, point)
    connection_point = getattr(value, "connection_point", None)
    if isinstance(connection_point, tuple) and len(connection_point) == 2:
        return (int(connection_point[0]), int(connection_point[1]), 0, 0)
    return None


def _schdoc_pin_connection_points(schdoc: object) -> tuple[RootPoint, ...]:
    return tuple(
        _compiled_pin_hotspot(pin)
        for component in _schdoc_rows(schdoc, "get_components")
        for pin in cast(Iterable[object], getattr(component, "pins", ()) or ())
    )


def _schdoc_sheet_entry_connection_points(schdoc: object) -> tuple[RootPoint, ...]:
    return tuple(
        _sheet_entry_precise_connection_point(cast("SchSheetSymbolInfo", symbol), entry)
        for symbol in _schdoc_rows(schdoc, "get_sheet_symbols")
        for entry in cast(Iterable[object], getattr(symbol, "entries", ()) or ())
    )


def _harness_definition_entry_nested_type(
    entry: object,
    *,
    connector: object,
    connector_infos: Sequence[object],
    signal_harnesses: Sequence[object],
    non_harness_lines: Sequence[object],
    non_harness_points: Sequence[RootPoint],
    compile_masks: Sequence[tuple[int, int, int, int]],
    internal_tolerance: int,
    type_names: Mapping[int, str] | None = None,
) -> str:
    authored_type = str(getattr(entry, "harness_type", "") or "")
    entry_point = _harness_entry_connection_point(connector, entry)
    connected_indexes = _connected_signal_harness_indexes(
        list(signal_harnesses),
        (entry_point,),
        internal_tolerance,
    )
    if connected_indexes:
        connected_types = _connected_harness_output_types(
            connector_infos,
            type_names=type_names
            if type_names is not None
            else _harness_connector_type_names(connector_infos),
            signal_harnesses=signal_harnesses,
            connected_indexes=frozenset(connected_indexes),
            compile_masks=compile_masks,
            internal_tolerance=internal_tolerance,
        )
        return _select_harness_definition_nested_type(
            authored_type,
            signal_harness_connected=True,
            connected_output_types=connected_types,
            non_harness_connected=False,
        )
    non_harness_connected = any(
        _point_on_signal_harness(entry_point, line, internal_tolerance)
        for line in non_harness_lines
    ) or any(
        precise_points_connected(entry_point, point, internal_tolerance)
        for point in non_harness_points
    )
    return _select_harness_definition_nested_type(
        authored_type,
        signal_harness_connected=False,
        connected_output_types=(),
        non_harness_connected=non_harness_connected,
    )


def _select_harness_definition_nested_type(
    authored_type: str,
    *,
    signal_harness_connected: bool,
    connected_output_types: Sequence[str],
    non_harness_connected: bool,
) -> str:
    if signal_harness_connected:
        if connected_output_types:
            return min(
                connected_output_types,
                key=cmp_to_key(managed_alpha_numeric_compare),
            )
        return authored_type
    return "" if non_harness_connected else authored_type


def _connected_harness_output_types(
    connector_infos: Sequence[object],
    *,
    type_names: Mapping[int, str],
    signal_harnesses: Sequence[object],
    connected_indexes: frozenset[int],
    compile_masks: Sequence[tuple[int, int, int, int]],
    internal_tolerance: int,
) -> tuple[str, ...]:
    result: list[str] = []
    harness_rows = list(signal_harnesses)
    for connector_info in connector_infos:
        connector = getattr(connector_info, "record", connector_info)
        master_point = _harness_connector_master_entry_point(connector)
        if _compiled_point_inside_any_mask(master_point, compile_masks):
            continue
        output_indexes = frozenset(
            _connected_signal_harness_indexes(
                harness_rows,
                (master_point,),
                internal_tolerance,
            )
        )
        type_name = type_names[id(connector)]
        if type_name and connected_indexes.intersection(output_indexes):
            result.append(type_name)
    return tuple(result)


def _project_harness_definitions(
    schdocs: Sequence["AltiumSchDoc"],
) -> dict[str, ResolvedHarnessDefinition]:
    return resolve_project_harness_definitions(
        _project_harness_definition_candidate_groups(schdocs)
    )


def _project_inferred_port_harness_types(
    schdocs: Sequence["AltiumSchDoc"],
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> dict[int, dict[str, tuple[str, ...]]]:
    inferred: defaultdict[int, defaultdict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for schdoc in schdocs:
        compile_masks = _compiled_compile_mask_bounds(schdoc)
        for symbol in _compiler_sheet_symbol_sources(schdoc):
            _append_inferred_port_harness_types(
                inferred,
                symbol,
                schdoc_by_source_ref,
                schdoc_by_file_name,
                compile_masks,
            )
    return {
        document_id: {name: tuple(types) for name, types in by_name.items()}
        for document_id, by_name in inferred.items()
    }


def _project_traced_typed_port_occurrences(
    schdocs: Sequence["AltiumSchDoc"],
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    inferred_port_types: Mapping[int, Mapping[str, tuple[str, ...]]],
) -> dict[int, frozenset[tuple[str, str]]]:
    traced: defaultdict[int, set[tuple[str, str]]] = defaultdict(set)
    for schdoc in schdocs:
        compile_masks = _compiled_compile_mask_bounds(schdoc)
        for symbol in _compiler_sheet_symbol_sources(schdoc):
            child = _resolve_referenced_schdoc(
                str(getattr(symbol, "child_filename", "") or ""),
                schdoc_by_source_ref,
                schdoc_by_file_name,
            )
            if child is None:
                continue
            _append_traced_typed_port_occurrences(
                traced[id(child)],
                symbol=symbol,
                child=child,
                compile_masks=compile_masks,
                inferred_port_types=inferred_port_types.get(id(child), {}),
            )
    return {document_id: frozenset(rows) for document_id, rows in traced.items()}


def _append_traced_typed_port_occurrences(
    traced: set[tuple[str, str]],
    *,
    symbol: "SchSheetSymbolInfo",
    child: "AltiumSchDoc",
    compile_masks: Sequence[tuple[int, int, int, int]],
    inferred_port_types: Mapping[str, tuple[str, ...]],
) -> None:
    child_interfaces = _typed_harness_interfaces(child, inferred_port_types)
    for entry in symbol.entries:
        type_name = str(getattr(entry, "harness_type", "") or "")
        if not type_name or _sheet_entry_is_compile_masked(
            entry, symbol, compile_masks
        ):
            continue
        entry_name = _inter_sheet_entry_display_name(entry)
        for interface in _matching_child_typed_harness_interfaces(
            child_interfaces,
            port_name=_sheet_symbol_child_port_name(symbol, entry_name),
            type_name=type_name,
        ):
            traced.add(_typed_port_occurrence_key(interface))


def _sheet_symbol_child_port_name(
    symbol: "SchSheetSymbolInfo",
    entry_name: str,
) -> str:
    is_multichannel = getattr(symbol.record, "is_multichannel", None)
    if callable(is_multichannel) and is_multichannel():
        return _parse_entry_repeat(entry_name) or entry_name
    return entry_name


def _append_inferred_port_harness_types(
    inferred: defaultdict[int, defaultdict[str, list[str]]],
    symbol: "SchSheetSymbolInfo",
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> None:
    child = _resolve_referenced_schdoc(
        str(getattr(symbol, "child_filename", "") or ""),
        schdoc_by_source_ref,
        schdoc_by_file_name,
    )
    if child is None:
        return
    for entry in symbol.entries:
        if _sheet_entry_is_compile_masked(entry, symbol, compile_masks):
            continue
        type_name = str(getattr(entry, "harness_type", "") or "")
        entry_name = _inter_sheet_entry_display_name(entry)
        port_name = _sheet_symbol_child_port_name(symbol, entry_name)
        key = dotnet_ordinal_ignore_case_key(port_name)
        if type_name and type_name not in inferred[id(child)][key]:
            inferred[id(child)][key].append(type_name)


def _project_fallback_sheet_entry_harness_members(
    schdocs: Sequence["AltiumSchDoc"],
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    project_definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[int, Mapping[str, ResolvedHarnessDefinition]],
    inferred_port_types: Mapping[int, Mapping[str, tuple[str, ...]]],
) -> dict[int, dict[str, tuple[ResolvedHarnessMember, ...]]]:
    result: dict[int, dict[str, tuple[ResolvedHarnessMember, ...]]] = {}
    for schdoc in schdocs:
        members_by_occurrence: defaultdict[str, list[ResolvedHarnessMember]] = (
            defaultdict(list)
        )
        seen_by_occurrence: defaultdict[str, set[tuple[object, ...]]] = defaultdict(set)
        compile_masks = _compiled_compile_mask_bounds(schdoc)
        for symbol_index, symbol in _compiler_source_rows(
            _compiler_sheet_symbol_sources(schdoc)
        ):
            _append_symbol_fallback_sheet_entry_members(
                members_by_occurrence,
                seen_by_occurrence,
                schdoc_by_source_ref,
                schdoc_by_file_name,
                symbol=symbol,
                symbol_index=symbol_index,
                compile_masks=compile_masks,
                project_definitions=project_definitions,
                local_definitions=local_definitions,
                inferred_port_types=inferred_port_types,
            )
        if members_by_occurrence:
            result[id(schdoc)] = {
                occurrence: tuple(members)
                for occurrence, members in members_by_occurrence.items()
            }
    return result


def _append_symbol_fallback_sheet_entry_members(
    members_by_occurrence: defaultdict[str, list[ResolvedHarnessMember]],
    seen_by_occurrence: defaultdict[str, set[tuple[object, ...]]],
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    *,
    symbol: "SchSheetSymbolInfo",
    symbol_index: int,
    compile_masks: Sequence[tuple[int, int, int, int]],
    project_definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[int, Mapping[str, ResolvedHarnessDefinition]],
    inferred_port_types: Mapping[int, Mapping[str, tuple[str, ...]]],
) -> None:
    child = _resolve_referenced_schdoc(
        str(getattr(symbol, "child_filename", "") or ""),
        schdoc_by_source_ref,
        schdoc_by_file_name,
    )
    if child is None:
        return
    for entry_index, entry in _compiler_entry_rows(symbol, symbol.entries):
        name_key = _fallback_sheet_entry_name_key(
            entry,
            symbol=symbol,
            compile_masks=compile_masks,
            project_definitions=project_definitions,
        )
        if name_key is None:
            continue
        occurrence_id = f"sheet_symbol:{symbol_index}:entry:{entry_index}"
        for definition in _matching_child_port_harness_definitions(
            child,
            name_key=name_key,
            project_definitions=project_definitions,
            local_definitions=local_definitions.get(id(child), {}),
            inferred_port_types=inferred_port_types.get(id(child), {}),
        ):
            for member in definition.members:
                _append_unique_fallback_harness_member(
                    members_by_occurrence[occurrence_id],
                    seen_by_occurrence[occurrence_id],
                    member,
                )


def _fallback_sheet_entry_name_key(
    entry: object,
    *,
    symbol: "SchSheetSymbolInfo",
    compile_masks: Sequence[tuple[int, int, int, int]],
    project_definitions: Mapping[str, ResolvedHarnessDefinition],
) -> str | None:
    authored_type = str(getattr(entry, "harness_type", "") or "")
    entry_name = _inter_sheet_entry_display_name(entry)
    if not authored_type or not entry_name:
        return None
    if dotnet_ordinal_ignore_case_key(authored_type) in project_definitions:
        return None
    if _sheet_entry_is_compile_masked(entry, symbol, compile_masks):
        return None
    return dotnet_ordinal_ignore_case_key(
        _sheet_symbol_child_port_name(symbol, entry_name)
    )


def _matching_child_port_harness_definitions(
    child: "AltiumSchDoc",
    *,
    name_key: str,
    project_definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[str, ResolvedHarnessDefinition],
    inferred_port_types: Mapping[str, tuple[str, ...]],
) -> tuple[ResolvedHarnessDefinition, ...]:
    definitions: list[ResolvedHarnessDefinition] = []
    for interface in _typed_harness_interfaces(child, inferred_port_types):
        if interface.role != "port":
            continue
        if dotnet_ordinal_ignore_case_key(interface.name) != name_key:
            continue
        definition = _definition_for_typed_harness_interface(
            interface, project_definitions, local_definitions
        )
        if definition is not None:
            definitions.append(definition)
    return tuple(definitions)


def _append_unique_fallback_harness_member(
    members: list[ResolvedHarnessMember],
    seen: set[tuple[object, ...]],
    member: ResolvedHarnessMember,
) -> None:
    member_key = (
        tuple(dotnet_ordinal_ignore_case_key(segment.value) for segment in member.path),
        member.bus_signal_index,
        dotnet_ordinal_ignore_case_key(member.signal_name),
    )
    if member_key not in seen:
        seen.add(member_key)
        members.append(member)


def _typed_harness_interfaces(
    schdoc: "AltiumSchDoc",
    inferred_port_types: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[_TypedHarnessInterface, ...]:
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    return (
        *_typed_port_harness_interfaces(
            schdoc, compile_masks, inferred_port_types or {}
        ),
        *_typed_sheet_entry_harness_interfaces(schdoc, compile_masks),
    )


def _typed_port_occurrence_key(
    interface: _TypedHarnessInterface,
) -> tuple[str, str]:
    return (
        interface.source_occurrence_id,
        dotnet_ordinal_ignore_case_key(interface.type_name),
    )


def _typed_port_harness_interfaces(
    schdoc: "AltiumSchDoc",
    compile_masks: Sequence[tuple[int, int, int, int]],
    inferred_port_types: Mapping[str, tuple[str, ...]],
) -> tuple[_TypedHarnessInterface, ...]:
    return tuple(
        row
        for port_index, port in _compiler_source_rows(schdoc.get_ports())
        for row in _typed_port_harness_interface_rows(
            port,
            port_index=port_index,
            compile_masks=compile_masks,
            inferred_types=inferred_port_types.get(
                dotnet_ordinal_ignore_case_key(str(port.name or "")), ()
            ),
        )
    )


def _typed_port_harness_interface_rows(
    port: "SchPortInfo",
    *,
    port_index: int,
    compile_masks: Sequence[tuple[int, int, int, int]],
    inferred_types: Sequence[str],
) -> tuple[_TypedHarnessInterface, ...]:
    authored_type = str(
        getattr(getattr(port, "record", None), "harness_type", "") or ""
    )
    if (not authored_type and not inferred_types) or _port_is_compile_masked(
        port, compile_masks
    ):
        return ()
    name = str(getattr(port, "name", "") or "")
    object_id = str(getattr(port, "unique_id", "") or "")
    occurrence_id = f"port:{port_index}"
    authored_rows = (
        (
            _TypedHarnessInterface(
                name=name,
                type_name=authored_type,
                role="port",
                object_id=object_id,
                source_occurrence_id=occurrence_id,
            ),
        )
        if authored_type
        else ()
    )
    authored_key = dotnet_ordinal_ignore_case_key(authored_type)
    inferred_rows = tuple(
        _TypedHarnessInterface(
            name=name,
            type_name=type_name,
            role="port",
            object_id=object_id,
            source_occurrence_id=occurrence_id,
            type_inferred=True,
        )
        for type_name in inferred_types
        if dotnet_ordinal_ignore_case_key(type_name) != authored_key
    )
    return (*authored_rows, *inferred_rows)


def _typed_sheet_entry_harness_interfaces(
    schdoc: "AltiumSchDoc",
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> tuple[_TypedHarnessInterface, ...]:
    rows: list[_TypedHarnessInterface] = []
    for symbol_index, symbol in _compiler_source_rows(
        _compiler_sheet_symbol_sources(schdoc)
    ):
        for entry_index, entry in _compiler_entry_rows(symbol, symbol.entries):
            type_name = str(getattr(entry, "harness_type", "") or "")
            if not type_name or _sheet_entry_is_compile_masked(
                entry, symbol, compile_masks
            ):
                continue
            rows.append(
                _TypedHarnessInterface(
                    name=_inter_sheet_entry_display_name(entry),
                    type_name=type_name,
                    role="sheet_entry",
                    object_id=str(getattr(entry, "unique_id", "") or ""),
                    source_occurrence_id=(
                        f"sheet_symbol:{symbol_index}:entry:{entry_index}"
                    ),
                )
            )
    return tuple(rows)


def _compiled_typed_harness_member_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[str, ResolvedHarnessDefinition],
    inferred_port_types: Mapping[str, tuple[str, ...]],
    options: NetlistOptions | None = None,
    traced_typed_port_occurrences: frozenset[tuple[str, str]] = frozenset(),
    preexisting_typed_harness_nets: Sequence[AltiumCompiledNet] = (),
    fallback_sheet_entry_members: Mapping[str, tuple[ResolvedHarnessMember, ...]]
    | None = None,
    scalar_port_occurrences: frozenset[str] = frozenset(),
) -> tuple[AltiumCompiledNet, ...]:
    fallback_sheet_entry_members = fallback_sheet_entry_members or {}
    interfaces = _typed_harness_interfaces(schdoc, inferred_port_types)
    if not interfaces and not fallback_sheet_entry_members:
        return ()
    emitted = _compiled_typed_harness_member_keys(
        preexisting_typed_harness_nets,
        scalar_port_occurrences=scalar_port_occurrences,
    )
    rows: list[AltiumCompiledNet] = []
    for net in getattr(local_netlist, "nets", ()):
        for source_endpoint in getattr(net, "endpoints", ()):
            _append_missing_typed_harness_members(
                rows,
                emitted,
                local_netlist=local_netlist,
                source_net=net,
                source_endpoint=source_endpoint,
                interfaces=interfaces,
                definitions=definitions,
                local_definitions=local_definitions,
                logical_document=logical_document,
                first_net_index=first_net_index,
            )
    _append_signal_harness_carried_typed_members(
        rows,
        emitted,
        schdoc=schdoc,
        local_netlist=local_netlist,
        interfaces=interfaces,
        definitions=definitions,
        local_definitions=local_definitions,
        logical_document=logical_document,
        first_net_index=first_net_index,
    )
    _append_unconnected_typed_port_members(
        rows,
        emitted,
        interfaces=interfaces,
        definitions=definitions,
        local_definitions=local_definitions,
        traced_typed_port_occurrences=traced_typed_port_occurrences,
        logical_document=logical_document,
        first_net_index=first_net_index,
    )
    _append_fallback_sheet_entry_harness_members(
        rows,
        local_netlist=local_netlist,
        interfaces=interfaces,
        members_by_occurrence=fallback_sheet_entry_members,
        logical_document=logical_document,
        first_net_index=first_net_index,
    )
    return tuple(
        replace(
            row,
            _signal_harness_name_candidates=(
                _typed_harness_name_candidates_with_priorities(row, options)
            ),
        )
        for row in rows
    )


def _typed_harness_name_candidates_with_priorities(
    row: AltiumCompiledNet,
    options: NetlistOptions | None,
) -> tuple[_SignalHarnessNameCandidate, ...]:
    resolved_options = options or NetlistOptions()
    candidates: list[_SignalHarnessNameCandidate] = []
    for candidate in row._signal_harness_name_candidates:
        priority = _managed_name_priority(candidate.source_kind, resolved_options)
        if priority > 0:
            candidates.append(replace(candidate, source_priority=priority))
    return tuple(candidates)


def _compiled_typed_harness_member_keys(
    nets: Sequence[AltiumCompiledNet],
    *,
    scalar_port_occurrences: frozenset[str] = frozenset(),
) -> set[tuple[str, tuple[str, ...], int | None]]:
    return {
        (
            endpoint._source_occurrence_id,
            _harness_path_key(endpoint._harness_entries_path),
            endpoint._bus_signal_index,
        )
        for net in nets
        if not any(
            endpoint.role == "port"
            and endpoint._source_occurrence_id in scalar_port_occurrences
            for endpoint in net.endpoints
        )
        for endpoint in net.endpoints
        if endpoint.role == "harness_entry"
        and endpoint._source_occurrence_id
        and endpoint._harness_entries_path
    }


def _append_unconnected_typed_port_members(
    rows: list[AltiumCompiledNet],
    emitted: set[tuple[str, tuple[str, ...], int | None]],
    *,
    interfaces: Sequence[_TypedHarnessInterface],
    definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[str, ResolvedHarnessDefinition],
    traced_typed_port_occurrences: frozenset[tuple[str, str]],
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
) -> None:
    for interface in interfaces:
        if (
            interface.role != "port"
            or _typed_port_occurrence_key(interface)
            not in traced_typed_port_occurrences
        ):
            continue
        definition = _definition_for_typed_harness_interface(
            interface, definitions, local_definitions
        )
        if definition is None:
            continue
        for member in definition.members:
            key = _typed_harness_member_key(interface, member)
            if key in emitted:
                continue
            emitted.add(key)
            rows.append(
                _compiled_typed_harness_member_net(
                    interface,
                    member,
                    logical_document=logical_document,
                    net_index=first_net_index + len(rows),
                )
            )


def _append_signal_harness_carried_typed_members(
    rows: list[AltiumCompiledNet],
    emitted: set[tuple[str, tuple[str, ...], int | None]],
    *,
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    interfaces: Sequence[_TypedHarnessInterface],
    definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[str, ResolvedHarnessDefinition],
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
) -> None:
    type_names = _harness_connector_type_names(
        _schdoc_rows(schdoc, "get_harness_connectors")
    )
    for interface in interfaces:
        if not _typed_port_interface_has_signal_harness_carrier(
            schdoc, interface, type_names
        ):
            continue
        definition = _definition_for_typed_harness_interface(
            interface, definitions, local_definitions
        )
        if definition is None:
            continue
        for member in definition.members:
            key = _typed_harness_member_key(interface, member)
            if key in emitted:
                continue
            emitted.add(key)
            if _annotate_existing_typed_harness_member(
                local_netlist,
                interface=interface,
                member=member,
            ):
                continue
            rows.append(
                _compiled_typed_harness_member_net(
                    interface,
                    member,
                    logical_document=logical_document,
                    net_index=first_net_index + len(rows),
                )
            )


def _typed_port_interface_has_signal_harness_carrier(
    schdoc: "AltiumSchDoc",
    interface: _TypedHarnessInterface,
    type_names: Mapping[int, str],
) -> bool:
    if interface.role != "port":
        return False
    port = _typed_harness_interface_port(schdoc, interface)
    if port is None:
        return False
    signal_harnesses = tuple(_schdoc_rows(schdoc, "get_signal_harnesses"))
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0) or 0)
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    port_points = tuple(getattr(port, "_precise_connection_points", ()) or ())
    port_indexes = frozenset(
        _connected_signal_harness_indexes(
            list(signal_harnesses),
            port_points,
            internal_tolerance,
        )
    )
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    return any(
        _harness_connector_output_matches_interface(
            info,
            type_names=type_names,
            interface=interface,
            signal_harnesses=signal_harnesses,
            port_indexes=port_indexes,
            port_points=port_points,
            compile_masks=compile_masks,
            internal_tolerance=internal_tolerance,
        )
        for info in _schdoc_rows(schdoc, "get_harness_connectors")
    )


def _typed_harness_interface_port(
    schdoc: "AltiumSchDoc", interface: _TypedHarnessInterface
) -> object | None:
    interface_object_key = dotnet_ordinal_ignore_case_key(interface.object_id)
    return next(
        (
            row
            for row in schdoc.get_ports()
            if dotnet_ordinal_ignore_case_key(str(getattr(row, "unique_id", "") or ""))
            == interface_object_key
        ),
        None,
    )


def _harness_connector_output_matches_interface(
    info: object,
    *,
    type_names: Mapping[int, str],
    interface: _TypedHarnessInterface,
    signal_harnesses: Sequence[object],
    port_indexes: frozenset[int],
    port_points: Sequence[RootPoint],
    compile_masks: Sequence[tuple[int, int, int, int]],
    internal_tolerance: int,
) -> bool:
    connector = getattr(info, "record", info)
    master_point = _harness_connector_master_entry_point(connector)
    if dotnet_ordinal_ignore_case_key(
        type_names[id(connector)]
    ) != dotnet_ordinal_ignore_case_key(interface.type_name):
        return False
    if _compiled_point_inside_any_mask(master_point, compile_masks):
        return False
    if any(
        precise_points_connected(master_point, point, internal_tolerance)
        for point in port_points
    ):
        return True
    output_indexes = _connected_signal_harness_indexes(
        list(signal_harnesses), (master_point,), internal_tolerance
    )
    return bool(port_indexes.intersection(output_indexes))


def _append_fallback_sheet_entry_harness_members(
    rows: list[AltiumCompiledNet],
    *,
    local_netlist: object,
    interfaces: Sequence[_TypedHarnessInterface],
    members_by_occurrence: Mapping[str, tuple[ResolvedHarnessMember, ...]],
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
) -> None:
    connected_interfaces = tuple(
        interface
        for interface in interfaces
        if interface.role == "sheet_entry"
        and interface.source_occurrence_id in members_by_occurrence
        and _typed_harness_interface_has_local_carrier(interface, local_netlist)
    )
    grouped: dict[
        tuple[str, tuple[str, ...], int | None, str],
        tuple[ResolvedHarnessMember, list[_TypedHarnessInterface]],
    ] = {}
    for interface in connected_interfaces:
        for member in members_by_occurrence[interface.source_occurrence_id]:
            member_key = (
                dotnet_ordinal_ignore_case_key(interface.name),
                _harness_path_key(_runtime_harness_entries_path(member)),
                member.bus_signal_index,
                dotnet_ordinal_ignore_case_key(member.signal_name),
            )
            grouped.setdefault(member_key, (member, []))[1].append(interface)
    for member, member_interfaces in grouped.values():
        rows.append(
            _compiled_typed_harness_member_net_for_interfaces(
                member_interfaces,
                member,
                logical_document=logical_document,
                net_index=first_net_index + len(rows),
            )
        )


def _typed_harness_interface_has_local_carrier(
    interface: _TypedHarnessInterface,
    local_netlist: object,
) -> bool:
    return any(
        _typed_harness_interface_matches(
            interface,
            endpoint_role=str(getattr(endpoint, "role", "") or ""),
            endpoint_name=str(getattr(endpoint, "name", "") or ""),
            endpoint_object_id=str(getattr(endpoint, "object_id", "") or ""),
        )
        for net in getattr(local_netlist, "nets", ())
        for endpoint in getattr(net, "endpoints", ())
    )


def _append_missing_typed_harness_members(
    rows: list[AltiumCompiledNet],
    emitted: set[tuple[str, tuple[str, ...], int | None]],
    *,
    local_netlist: object,
    source_net: object,
    source_endpoint: object,
    interfaces: Sequence[_TypedHarnessInterface],
    definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[str, ResolvedHarnessDefinition],
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
) -> None:
    for interface in _matching_typed_harness_interfaces(source_endpoint, interfaces):
        definition = _definition_for_typed_harness_interface(
            interface, definitions, local_definitions
        )
        if definition is None:
            continue
        for member in definition.members:
            key = _typed_harness_member_key(interface, member)
            if key in emitted:
                continue
            emitted.add(key)
            if _annotate_existing_typed_harness_member(
                local_netlist,
                interface=interface,
                member=member,
                excluded_net=(
                    source_net if not logical_document.parent_sheet_symbol_ids else None
                ),
                reject_different_port=(not logical_document.parent_sheet_symbol_ids),
            ):
                continue
            rows.append(
                _compiled_typed_harness_member_net(
                    interface,
                    member,
                    logical_document=logical_document,
                    net_index=first_net_index + len(rows),
                )
            )


def _definition_for_typed_harness_interface(
    interface: _TypedHarnessInterface,
    project_definitions: Mapping[str, ResolvedHarnessDefinition],
    local_definitions: Mapping[str, ResolvedHarnessDefinition],
) -> ResolvedHarnessDefinition | None:
    key = dotnet_ordinal_ignore_case_key(interface.type_name)
    if interface.role == "port":
        local = local_definitions.get(key)
        if local is not None:
            return local
    return project_definitions.get(key)


def _typed_harness_member_key(
    interface: _TypedHarnessInterface,
    member: ResolvedHarnessMember,
) -> tuple[str, tuple[str, ...], int | None]:
    return (
        interface.source_occurrence_id,
        _harness_path_key(_runtime_harness_entries_path(member)),
        member.bus_signal_index,
    )


def _annotate_existing_typed_harness_member(
    local_netlist: object,
    *,
    interface: _TypedHarnessInterface,
    member: ResolvedHarnessMember,
    excluded_net: object | None = None,
    reject_different_port: bool = False,
) -> bool:
    member_values = _resolved_harness_member_values(member)
    endpoint_name = ".".join((interface.name, *member_values))
    matches = tuple(
        net
        for net in getattr(local_netlist, "nets", ())
        if net is not excluded_net
        if not reject_different_port
        or not any(
            str(getattr(endpoint, "role", "") or "") == "port"
            and dotnet_ordinal_ignore_case_key(
                str(getattr(endpoint, "object_id", "") or "")
            )
            != dotnet_ordinal_ignore_case_key(interface.object_id)
            for endpoint in getattr(net, "endpoints", ())
        )
        if any(
            _local_harness_member_endpoint_matches(
                endpoint,
                endpoint_name=endpoint_name,
                leaf_source_identity=member.path[-1].source_identity,
            )
            for endpoint in getattr(net, "endpoints", ())
        )
    )
    if len(matches) != 1:
        return False
    _append_local_net_endpoint(
        matches[0],
        endpoint_id=(
            f"typed_harness_interface:{interface.source_occurrence_id}:"
            f"{_harness_path_identity(_runtime_harness_entries_path(member))}:"
            f"{member.bus_signal_index}"
        ),
        role="harness_entry",
        element_id=interface.object_id or endpoint_name,
        object_id=interface.object_id,
        name=endpoint_name,
        source_occurrence_id=interface.source_occurrence_id,
        bus_signal_index=member.bus_signal_index,
        harness_entries_path=_runtime_harness_entries_path(member),
        harness_type_name=interface.type_name,
        harness_interface_name=interface.name,
        harness_type_inferred=interface.type_inferred,
    )
    return True


def _local_harness_member_endpoint_matches(
    endpoint: object,
    *,
    endpoint_name: str,
    leaf_source_identity: str,
) -> bool:
    if str(getattr(endpoint, "role", "") or "") != "harness_entry":
        return False
    if dotnet_ordinal_ignore_case_key(
        str(getattr(endpoint, "name", "") or "")
    ) != dotnet_ordinal_ignore_case_key(endpoint_name):
        return False
    object_id = str(getattr(endpoint, "object_id", "") or "")
    if leaf_source_identity and object_id:
        return dotnet_ordinal_ignore_case_key(
            object_id
        ) == dotnet_ordinal_ignore_case_key(leaf_source_identity)
    return True


def _runtime_harness_entries_path(
    member: ResolvedHarnessMember,
) -> tuple[str, ...]:
    return tuple(segment.value for segment in reversed(member.path))


def _harness_path_key(path: Sequence[str]) -> tuple[str, ...]:
    return tuple(dotnet_ordinal_ignore_case_key(value) for value in path)


def _harness_path_identity(path: Sequence[str]) -> str:
    return "".join(f"{len(value)}:{value}" for value in path)


def _matching_typed_harness_interfaces(
    endpoint: object,
    interfaces: Sequence[_TypedHarnessInterface],
) -> tuple[_TypedHarnessInterface, ...]:
    endpoint_role = str(getattr(endpoint, "role", "") or "")
    endpoint_name = str(getattr(endpoint, "name", "") or "")
    endpoint_object_id = str(getattr(endpoint, "object_id", "") or "")
    matches = tuple(
        interface
        for interface in interfaces
        if _typed_harness_interface_matches(
            interface,
            endpoint_role=endpoint_role,
            endpoint_name=endpoint_name,
            endpoint_object_id=endpoint_object_id,
        )
    )
    if len(matches) <= 1 or endpoint_object_id:
        return matches
    return ()


def _typed_harness_interface_matches(
    interface: _TypedHarnessInterface,
    *,
    endpoint_role: str,
    endpoint_name: str,
    endpoint_object_id: str,
) -> bool:
    if endpoint_role not in {interface.role, "harness_port"}:
        return False
    if dotnet_ordinal_ignore_case_key(interface.name) != dotnet_ordinal_ignore_case_key(
        endpoint_name
    ):
        return False
    return (
        not endpoint_object_id
        or not interface.object_id
        or dotnet_ordinal_ignore_case_key(interface.object_id)
        == dotnet_ordinal_ignore_case_key(endpoint_object_id)
    )


def _resolved_harness_member_values(
    member: ResolvedHarnessMember,
) -> tuple[str, ...]:
    values = tuple(segment.value for segment in member.path)
    if member.bus_signal_index is None or not member.signal_name:
        return values
    return (*values[:-1], member.signal_name)


def _compiled_typed_harness_member_net(
    interface: _TypedHarnessInterface,
    member: ResolvedHarnessMember,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    net_index: int,
) -> AltiumCompiledNet:
    return _compiled_typed_harness_member_net_for_interfaces(
        (interface,),
        member,
        logical_document=logical_document,
        net_index=net_index,
    )


def _compiled_typed_harness_member_net_for_interfaces(
    interfaces: Sequence[_TypedHarnessInterface],
    member: ResolvedHarnessMember,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    net_index: int,
) -> AltiumCompiledNet:
    if not interfaces:
        raise ValueError("typed harness member net requires an interface")
    interface = interfaces[0]
    member_values = _resolved_harness_member_values(member)
    net_name = "_".join((interface.name, *member_values))
    net_id = _compiled_local_net_id(logical_document.id, net_name, net_index)
    endpoints = tuple(
        _compiled_typed_harness_member_endpoint(
            row,
            member,
            net_id=net_id,
            endpoint_index=endpoint_index,
        )
        for endpoint_index, row in enumerate(interfaces)
    )
    candidates = tuple(
        _signal_harness_name_candidate(
            value=endpoint.name,
            value_from_source_object=row.name,
            source_kind=row.role,
            source_priority=0,
            source_element_id=row.object_id,
            harness_entries_path=_runtime_harness_entries_path(member),
            bus_signal_index=member.bus_signal_index,
        )
        for row, endpoint in zip(interfaces, endpoints, strict=True)
    )
    return AltiumCompiledNet(
        id=net_id,
        name=net_name,
        original_name=net_name,
        scope="logical_local",
        logical_document_id=logical_document.id,
        auto_named=True,
        single_pin=False,
        aliases=tuple(dict.fromkeys(row.name for row in interfaces)),
        endpoint_ids=tuple(endpoint.id for endpoint in endpoints),
        endpoints=endpoints,
        item_ids=tuple(endpoint.id for endpoint in endpoints),
        items=tuple(_compiled_item_from_endpoint(endpoint) for endpoint in endpoints),
        _signal_harness_name_candidates=candidates,
    )


def _compiled_typed_harness_member_endpoint(
    interface: _TypedHarnessInterface,
    member: ResolvedHarnessMember,
    *,
    net_id: str,
    endpoint_index: int,
) -> AltiumCompiledNetEndpoint:
    member_values = _resolved_harness_member_values(member)
    endpoint_name = ".".join((interface.name, *member_values))
    endpoint_id = f"{net_id}:endpoint:{endpoint_index}:harness_entry:{endpoint_name}"
    return AltiumCompiledNetEndpoint(
        id=endpoint_id,
        role="harness_entry",
        element_id=interface.object_id or endpoint_name,
        object_id=interface.object_id,
        name=endpoint_name,
        parent_id=_harness_entry_parent_id(endpoint_name),
        _source_occurrence_id=interface.source_occurrence_id,
        _bus_signal_index=member.bus_signal_index,
        _harness_entries_path=_runtime_harness_entries_path(member),
        _harness_type_name=interface.type_name,
        _harness_interface_name=interface.name,
        _harness_type_inferred=interface.type_inferred,
    )


def _compiled_harness_wire_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
) -> tuple[AltiumCompiledNet, ...]:
    rows: list[AltiumCompiledNet] = []
    for synthetic_index, (wire_id, endpoints) in enumerate(
        _harness_wire_endpoint_groups(schdoc, local_netlist),
        start=first_net_index,
    ):
        net_name = _harness_wire_endpoint_member_name(endpoints[0].name)
        net_id = _compiled_local_net_id(logical_document.id, net_name, synthetic_index)
        endpoint_ids = tuple(
            f"{net_id}:endpoint:{endpoint_index}:{endpoint.role}:{wire_id}:{endpoint_index}"
            for endpoint_index, endpoint in enumerate(endpoints)
        )
        compiled_endpoints = tuple(
            AltiumCompiledNetEndpoint(
                id=endpoint_id,
                role=endpoint.role,
                element_id=endpoint.element_id,
                object_id=endpoint.object_id,
                name=endpoint.name,
                parent_id=endpoint.parent_id,
                connection_point=endpoint.connection_point,
                _source_occurrence_id=endpoint.source_occurrence_id,
                _harness_entries_path=endpoint.harness_entries_path,
                _harness_type_name=endpoint.harness_type_name,
                _harness_interface_name=endpoint.harness_interface_name,
            )
            for endpoint_id, endpoint in zip(endpoint_ids, endpoints, strict=True)
        )
        items = tuple(
            _compiled_item_from_endpoint(endpoint) for endpoint in compiled_endpoints
        )
        rows.append(
            AltiumCompiledNet(
                id=net_id,
                name=net_name,
                original_name=net_name,
                scope="logical_local",
                logical_document_id=logical_document.id,
                auto_named=False,
                single_pin=False,
                endpoint_ids=endpoint_ids,
                endpoints=compiled_endpoints,
                item_ids=endpoint_ids,
                items=items,
            )
        )
    return tuple(rows)


def _sheet_entry_connection_point(
    sheet_symbol: "SchSheetSymbolInfo",
    entry: object,
) -> tuple[int, int] | None:
    record = sheet_symbol.record
    x = _coord_scalar_to_rounded_native_units(
        record.location.x,
        getattr(record.location, "x_frac", 0),
    )
    y = _coord_scalar_to_rounded_native_units(
        record.location.y,
        getattr(record.location, "y_frac", 0),
    )
    right_x = _coord_scalar_to_rounded_native_units(
        record.location.x + record.x_size,
        getattr(record.location, "x_frac", 0) + getattr(record, "x_size_frac", 0),
    )
    bottom_y = _coord_scalar_to_rounded_native_units(
        record.location.y - record.y_size,
        getattr(record.location, "y_frac", 0) - getattr(record, "y_size_frac", 0),
    )
    entry_x = _basic_entry_axis_coordinate(
        record.location.x,
        getattr(record.location, "x_frac", 0),
        entry,
        direction=1,
    )
    entry_y = _basic_entry_axis_coordinate(
        record.location.y,
        getattr(record.location, "y_frac", 0),
        entry,
        direction=-1,
    )
    side = int(getattr(entry, "side", -1))
    if side == 0:
        return (x, entry_y)
    if side == 1:
        return (right_x, entry_y)
    if side == 2:
        return (entry_x, y)
    if side == 3:
        return (entry_x, bottom_y)
    return None


def _wire_network_maps(
    schdoc: "AltiumSchDoc",
) -> tuple[object, object, dict[str, RootPoint], dict[RootPoint, str]]:
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    wire_graph, wire_ids_by_root, wire_index = _build_wire_graph(
        schdoc,
        tolerance=internal_tolerance,
        inferred_segments=_scalar_port_segments_for_wire_network(
            schdoc,
            internal_tolerance,
        ),
    )
    root_by_wire_id: dict[str, RootPoint] = {}
    representative_wire_id_by_root: dict[RootPoint, str] = {}
    for root, wire_ids in wire_ids_by_root.items():
        normalized_root = wire_graph.find(root)
        clean_wire_ids = tuple(sorted(str(wire_id) for wire_id in wire_ids if wire_id))
        if not clean_wire_ids:
            continue
        representative_wire_id_by_root[normalized_root] = clean_wire_ids[0]
        for wire_id in clean_wire_ids:
            root_by_wire_id[wire_id] = normalized_root
    return wire_graph, wire_index, root_by_wire_id, representative_wire_id_by_root


def _scalar_port_segments_for_wire_network(
    schdoc: "AltiumSchDoc",
    internal_tolerance: int,
) -> tuple[tuple[RootPoint, RootPoint], ...]:
    signal_harnesses = tuple(schdoc.get_signal_harnesses())
    connector_primary_points = tuple(
        _harness_connector_master_entry_point(connector.record)
        for connector in schdoc.get_harness_connectors()
    )
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    segments: list[tuple[RootPoint, RootPoint]] = []
    for port in schdoc.get_ports():
        points = tuple(port._precise_connection_points)
        if len(points) != 2 or any(
            _compiled_mask_contains_every_point(mask, points) for mask in compile_masks
        ):
            continue
        if (
            analyser_net_item_kind(
                port.name,
                harness_type=str(getattr(port.record, "harness_type", "") or ""),
                locations=points,
                signal_harnesses=signal_harnesses,
                harness_connector_primary_points=connector_primary_points,
                internal_tolerance=internal_tolerance,
            )
            is AnalyserNetItemKind.WIRE
        ):
            segments.append((points[0], points[1]))
    return tuple(segments)


def _scalar_port_occurrence_ids_for_wire_network(
    schdoc: "AltiumSchDoc",
) -> frozenset[str]:
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    scalar_segments = set(
        _scalar_port_segments_for_wire_network(schdoc, internal_tolerance)
    )
    return frozenset(
        f"port:{port_index}"
        for port_index, port in _compiler_source_rows(schdoc.get_ports())
        if len(points := tuple(port._precise_connection_points)) == 2
        and (points[0], points[1]) in scalar_segments
    )


def _wire_network_root_for_point(
    wire_graph: object,
    wire_index: object,
    connection_point: tuple[int, int],
) -> RootPoint | None:
    find_wire_connection = getattr(wire_index, "find_wire_connection", None)
    if not callable(find_wire_connection):
        return None
    wire_point = find_wire_connection(connection_point, 0)
    if wire_point is None:
        return None
    find = getattr(wire_graph, "find", None)
    if not callable(find):
        return None
    return cast(RootPoint, find(wire_point))


def _wire_network_root_for_precise_point(
    wire_graph: object,
    wire_index: object,
    connection_point: tuple[int, int, int, int],
    internal_tolerance: int,
) -> RootPoint | None:
    find_wire_connection = getattr(
        wire_index,
        "find_wire_endpoint_for_precise_point",
        None,
    )
    if not callable(find_wire_connection):
        return None
    wire_point = find_wire_connection(connection_point, internal_tolerance)
    if wire_point is None:
        return None
    find = getattr(wire_graph, "find", None)
    if not callable(find):
        return None
    return cast(RootPoint, find(wire_point))


def _sheet_entry_wire_endpoint_groups(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
) -> tuple[tuple[str, tuple[_SheetEntryWireEndpoint, ...]], ...]:
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    represented_wire_ids = {
        wire_id
        for net in getattr(local_netlist, "nets", ())
        for wire_id in getattr(getattr(net, "graphical", None), "wires", ())
    }
    represented_sheet_entry_ids = {
        sheet_entry_id.lower()
        for net in getattr(local_netlist, "nets", ())
        for sheet_entry_id in getattr(
            getattr(net, "graphical", None),
            "sheet_entries",
            (),
        )
        if sheet_entry_id
    }
    (
        wire_graph,
        wire_index,
        root_by_wire_id,
        representative_wire_id_by_root,
    ) = _wire_network_maps(schdoc)
    represented_wire_roots = {
        root_by_wire_id[wire_id]
        for wire_id in represented_wire_ids
        if wire_id in root_by_wire_id
    }
    endpoints_by_wire_root: dict[RootPoint, list[_SheetEntryWireEndpoint]] = (
        defaultdict(list)
    )
    display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
    internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
    emitted_element_ids: set[str] = set()
    for sheet_symbol in _compiler_sheet_symbol_sources(schdoc):
        sheet_symbol_uid = str(getattr(sheet_symbol, "unique_id", "") or "")
        child_filename = str(getattr(sheet_symbol, "child_filename", "") or "")
        if not sheet_symbol_uid or not child_filename:
            continue
        for entry in sheet_symbol.entries:
            entry_name = str(
                getattr(entry, "display_name", "") or getattr(entry, "name", "") or ""
            )
            if not entry_name:
                continue
            precise_connection_point = _sheet_entry_precise_connection_point(
                sheet_symbol,
                entry,
            )
            if _compiled_point_inside_any_mask(
                precise_connection_point,
                compile_masks,
            ):
                continue
            element_id = _unique_sheet_entry_element_id(
                f"{sheet_symbol_uid}_{entry_name}",
                emitted_element_ids,
            )
            if element_id.lower() in represented_sheet_entry_ids:
                continue
            connection_point = _sheet_entry_connection_point(sheet_symbol, entry)
            if connection_point is None:
                continue
            wire_root = _wire_network_root_for_precise_point(
                wire_graph,
                wire_index,
                precise_connection_point,
                internal_tolerance,
            )
            if wire_root is None or wire_root in represented_wire_roots:
                continue
            wire_id = representative_wire_id_by_root.get(wire_root)
            if not wire_id:
                continue
            endpoint = _SheetEntryWireEndpoint(
                wire_id=wire_id,
                name=entry_name,
                element_id=element_id,
                object_id=str(getattr(entry, "unique_id", "") or element_id),
                parent_id=child_filename,
                connection_point=connection_point,
            )
            if endpoint not in endpoints_by_wire_root[wire_root]:
                endpoints_by_wire_root[wire_root].append(endpoint)

    return tuple(
        (representative_wire_id_by_root[wire_root], tuple(endpoints))
        for wire_root, endpoints in sorted(
            endpoints_by_wire_root.items(),
            key=lambda item: representative_wire_id_by_root.get(item[0], ""),
        )
        if len(endpoints) > 1
    )


def _compiled_sheet_entry_wire_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    linked_sheet_entry_ids: frozenset[str] = frozenset(),
) -> tuple[AltiumCompiledNet, ...]:
    rows: list[AltiumCompiledNet] = []
    for synthetic_index, (wire_id, endpoints) in enumerate(
        _sheet_entry_wire_endpoint_groups(schdoc, local_netlist),
        start=first_net_index,
    ):
        net_name = endpoints[0].name
        net_id = _compiled_local_net_id(logical_document.id, net_name, synthetic_index)
        endpoint_ids = tuple(
            f"{net_id}:endpoint:{endpoint_index}:sheet_entry:{wire_id}:{endpoint_index}"
            for endpoint_index, _endpoint in enumerate(endpoints)
        )
        compiled_endpoints = tuple(
            AltiumCompiledNetEndpoint(
                id=endpoint_id,
                role="sheet_entry",
                element_id=endpoint.element_id,
                object_id=endpoint.object_id,
                name=endpoint.name,
                parent_id=endpoint.parent_id,
                connection_point=endpoint.connection_point,
            )
            for endpoint_id, endpoint in zip(endpoint_ids, endpoints, strict=True)
        )
        items = tuple(
            _compiled_item_from_local_endpoint(
                endpoint,
                linked_sheet_entry_ids=linked_sheet_entry_ids,
            )
            for endpoint in compiled_endpoints
        )
        rows.append(
            AltiumCompiledNet(
                id=net_id,
                name=net_name,
                original_name=net_name,
                scope="logical_local",
                logical_document_id=logical_document.id,
                auto_named=False,
                single_pin=False,
                endpoint_ids=endpoint_ids,
                endpoints=compiled_endpoints,
                item_ids=endpoint_ids,
                items=items,
            )
        )
    return tuple(rows)


def _sheet_entry_parent_ids_by_element_id(schdoc: "AltiumSchDoc") -> dict[str, str]:
    parent_ids: dict[str, str] = {}
    for sheet_symbol in _compiler_sheet_symbol_sources(schdoc):
        sheet_symbol_uid = str(getattr(sheet_symbol, "unique_id", "") or "")
        child_filename = str(getattr(sheet_symbol, "child_filename", "") or "")
        if not sheet_symbol_uid or not child_filename:
            continue
        for entry in sheet_symbol.entries:
            entry_name = str(
                getattr(entry, "display_name", "") or getattr(entry, "name", "") or ""
            )
            if not entry_name:
                continue
            parent_ids[f"{sheet_symbol_uid}_{entry_name}".lower()] = child_filename
            entry_uid = str(getattr(entry, "unique_id", "") or "")
            if entry_uid:
                parent_ids[entry_uid.lower()] = child_filename
    return parent_ids


def _linked_sheet_entry_ids(
    schdoc: "AltiumSchDoc",
    *,
    hierarchy_mode: str,
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> frozenset[str]:
    if hierarchy_mode not in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
        return frozenset()
    return frozenset(
        identity
        for symbol in _compiler_sheet_symbol_sources(schdoc)
        for identity in _linked_sheet_entry_ids_for_symbol(
            symbol,
            schdoc_by_source_ref=schdoc_by_source_ref,
            schdoc_by_file_name=schdoc_by_file_name,
        )
    )


def _linked_sheet_entry_ids_for_symbol(
    symbol: "SchSheetSymbolInfo",
    *,
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> tuple[str, ...]:
    children = _resolved_sheet_symbol_schdocs(
        symbol,
        schdoc_by_source_ref=schdoc_by_source_ref,
        schdoc_by_file_name=schdoc_by_file_name,
    )
    if not children:
        return ()
    child_port_names = {
        dotnet_ordinal_ignore_case_key(name)
        for child in children
        for name in _active_child_port_relative_names(child)
    }
    return tuple(
        identity
        for entry in symbol.entries
        if dotnet_ordinal_ignore_case_key(
            _sheet_symbol_child_port_name(
                symbol, _inter_sheet_entry_display_name(entry)
            )
        )
        in child_port_names
        for identity in _sheet_entry_identity_keys(symbol, entry)
    )


def _linked_hierarchy_interface_ids_by_document(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
    *,
    hierarchy_mode: str,
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> dict[str, frozenset[str]]:
    if hierarchy_mode not in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
        return {}
    logical_ids_by_schdoc: defaultdict[int, set[str]] = defaultdict(set)
    for logical_document in logical_documents:
        logical_ids_by_schdoc[id(schdoc_by_logical_id[logical_document.id])].add(
            logical_document.id
        )

    eligible_ids: defaultdict[str, set[str]] = defaultdict(set)
    for logical_document in logical_documents:
        parent = schdoc_by_logical_id[logical_document.id]
        for symbol in _compiler_sheet_symbol_sources(parent):
            _collect_linked_symbol_interface_ids(
                eligible_ids,
                logical_ids_by_schdoc,
                logical_document.id,
                symbol,
                schdoc_by_source_ref=schdoc_by_source_ref,
                schdoc_by_file_name=schdoc_by_file_name,
            )
    return {
        logical_id: frozenset(identities)
        for logical_id, identities in eligible_ids.items()
        if identities
    }


def _collect_linked_symbol_interface_ids(
    eligible_ids: defaultdict[str, set[str]],
    logical_ids_by_schdoc: Mapping[int, set[str]],
    parent_logical_id: str,
    symbol: "SchSheetSymbolInfo",
    *,
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> None:
    children = _resolved_sheet_symbol_schdocs(
        symbol,
        schdoc_by_source_ref=schdoc_by_source_ref,
        schdoc_by_file_name=schdoc_by_file_name,
    )
    for entry in symbol.entries:
        entry_name = dotnet_ordinal_ignore_case_key(
            _sheet_symbol_child_port_name(
                symbol, _inter_sheet_entry_display_name(entry)
            )
        )
        for child in children:
            child_logical_ids = logical_ids_by_schdoc.get(id(child), set())
            matching_ports = _matching_active_child_ports(child, entry_name)
            if not child_logical_ids or not matching_ports:
                continue
            eligible_ids[parent_logical_id].update(
                _sheet_entry_identity_keys(symbol, entry)
            )
            for child_logical_id in child_logical_ids:
                eligible_ids[child_logical_id].update(
                    dotnet_ordinal_ignore_case_key(port.unique_id)
                    for port in matching_ports
                    if port.unique_id
                )


def _matching_active_child_ports(
    child: "AltiumSchDoc", entry_name: str
) -> tuple["SchPortInfo", ...]:
    compile_masks = _compiled_compile_mask_bounds(child)
    return tuple(
        port
        for port in child.get_ports()
        if not _port_is_compile_masked(port, compile_masks)
        and entry_name
        in {dotnet_ordinal_ignore_case_key(name) for name in _port_relative_names(port)}
    )


def _active_child_port_relative_names(child: "AltiumSchDoc") -> tuple[str, ...]:
    compile_masks = _compiled_compile_mask_bounds(child)
    return tuple(
        name
        for port in child.get_ports()
        if not _port_is_compile_masked(port, compile_masks)
        for name in _port_relative_names(port)
    )


def _resolved_sheet_symbol_schdocs(
    symbol: "SchSheetSymbolInfo",
    *,
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
) -> tuple["AltiumSchDoc", ...]:
    raw_child_names = _managed_sheet_symbol_names(
        str(getattr(symbol, "child_filename", "") or "")
    )
    return tuple(
        child
        for child_name in raw_child_names
        if (
            child := _resolve_referenced_schdoc(
                child_name,
                schdoc_by_source_ref,
                schdoc_by_file_name,
            )
        )
        is not None
    )


def _port_relative_names(port: "SchPortInfo") -> tuple[str, ...]:
    name = str(port.name or "")
    return (name, *_parse_bus_range(name))


def _sheet_entry_identity_keys(
    symbol: "SchSheetSymbolInfo",
    entry: object,
) -> tuple[str, ...]:
    entry_name = _inter_sheet_entry_display_name(entry)
    entry_uid = str(getattr(entry, "unique_id", "") or "")
    symbol_uid = str(getattr(symbol, "unique_id", "") or "")
    element_id = f"{symbol_uid}_{entry_name}" if symbol_uid and entry_name else ""
    return tuple(
        dotnet_ordinal_ignore_case_key(value)
        for value in (entry_uid, element_id)
        if value
    )


def _harness_entry_parent_id(name: str) -> str:
    harness_name, _, _entry_name = name.partition(".")
    if not harness_name:
        return ""
    return f"{{{harness_name}}}"


def _compiled_endpoint_parent_id(
    endpoint: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    sheet_entry_parent_ids_by_element_id: Mapping[str, str],
) -> str:
    role = str(getattr(endpoint, "role", "") or "")
    if role in {
        "net_label",
        "port",
        "power_port",
        "harness_power",
        "offsheet_connector",
    }:
        return logical_document.file_name
    if role == "sheet_entry":
        element_id = str(getattr(endpoint, "element_id", "") or "").lower()
        object_id = str(getattr(endpoint, "object_id", "") or "").lower()
        return sheet_entry_parent_ids_by_element_id.get(
            element_id,
            sheet_entry_parent_ids_by_element_id.get(object_id, ""),
        )
    if role == "harness_entry":
        return _harness_entry_parent_id(str(getattr(endpoint, "name", "") or ""))
    return ""


def _annotation_state(annotation: AnnotationFile) -> AltiumCompiledAnnotationState:
    return AltiumCompiledAnnotationState(
        status=annotation.status,
        path=annotation.path,
        designator_record_count=len(annotation.designators),
        sheet_number_record_count=len(annotation.sheet_numbers),
        net_name_override_count=len(annotation.net_names),
        diagnostics=annotation.diagnostics,
    )


def _normalized_unique_id_path(value: str) -> str:
    return str(value or "").split("@", 1)[0].lower()


def _unique_id_suffixes(unique_id_path: str) -> tuple[str, ...]:
    normalized = _normalized_unique_id_path(unique_id_path)
    if not normalized:
        return ()
    parts = [part for part in normalized.split("\\") if part]
    return tuple("\\" + "\\".join(parts[index:]) for index in range(len(parts)))


def _find_designator_annotation(
    source_unique_id_path: str,
    annotation: AnnotationFile,
    *,
    exact_component_paths: set[str],
    used_annotation_indices: set[int],
) -> tuple[DesignatorAnnotation | None, str, AltiumCompileDiagnostic | None]:
    if not annotation.designators or not source_unique_id_path:
        return None, "", None

    target = _normalized_unique_id_path(source_unique_id_path)
    exact_matches = [
        record
        for record in annotation.designators
        if record.source_index not in used_annotation_indices
        if _normalized_unique_id_path(record.unique_id_path) == target
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], "annotation_exact", None
    if len(exact_matches) > 1:
        diagnostic = AltiumCompileDiagnostic(
            severity="warning",
            code="annotation_designator_ambiguous",
            message=(
                "Multiple .Annotation designator records exactly match "
                f"{source_unique_id_path}; leaving channel-format designator unchanged."
            ),
            source_id=source_unique_id_path,
        )
        return None, "annotation_ambiguous", diagnostic

    for suffix in _unique_id_suffixes(source_unique_id_path):
        suffix_matches = [
            record
            for record in annotation.designators
            if record.source_index not in used_annotation_indices
            if _normalized_unique_id_path(record.unique_id_path)
            not in exact_component_paths
            if _normalized_unique_id_path(record.unique_id_path).endswith(suffix)
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0], "annotation_suffix", None
        if len(suffix_matches) > 1:
            diagnostic = AltiumCompileDiagnostic(
                severity="warning",
                code="annotation_designator_ambiguous",
                message=(
                    "Multiple .Annotation designator records match suffix "
                    f"{suffix}; leaving channel-format designator unchanged."
                ),
                source_id=source_unique_id_path,
            )
            return None, "annotation_ambiguous", diagnostic

    return None, "", None


def _apply_sheet_number_annotations(
    physical_rows: tuple[AltiumCompiledPhysicalDocument, ...],
    annotation: AnnotationFile,
) -> tuple[
    tuple[AltiumCompiledPhysicalDocument, ...],
    tuple[AltiumCompileDiagnostic, ...],
]:
    if not annotation.sheet_numbers:
        return physical_rows, ()

    matched_indices: set[int] = set()
    updated_rows: list[AltiumCompiledPhysicalDocument] = []
    for row in physical_rows:
        record = _sheet_number_annotation_for_row(row, annotation.sheet_numbers)
        replacement_number = row.sheet_number
        if record is not None:
            replacement_number = record.sheet_number
            matched_indices.add(record.source_index)
            updated_rows.append(
                replace(
                    row,
                    sheet_number=replacement_number,
                    _managed_room_sheet_number=record.sheet_number,
                )
            )
        else:
            updated_rows.append(row)

    diagnostics = tuple(
        AltiumCompileDiagnostic(
            severity="warning",
            code="annotation_sheet_number_unmatched",
            message=(
                ".Annotation sheet-number record did not match any physical sheet: "
                f"index {record.source_index}"
            ),
            source_id=annotation.path,
        )
        for record in annotation.sheet_numbers
        if record.source_index not in matched_indices
    )
    return tuple(updated_rows), diagnostics


def _sheet_number_annotation_for_row(
    row: AltiumCompiledPhysicalDocument,
    records: tuple[_SheetNumberAnnotation, ...],
) -> _SheetNumberAnnotation | None:
    file_name = dotnet_ordinal_ignore_case_key(row.file_name)
    candidates = [
        record
        for record in records
        if dotnet_ordinal_ignore_case_key(record.document_name) == file_name
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) < 2:
        return None
    unique_id_path = dotnet_ordinal_ignore_case_key(row.physical_instance_unique_id)
    path_matches = [
        record
        for record in candidates
        if dotnet_ordinal_ignore_case_key(record.unique_id_path) == unique_id_path
    ]
    return path_matches[0] if len(path_matches) == 1 else None


@dataclass
class _ManagedHierarchyEdge:
    symbol_id: str
    child_id: str
    multiplicity: int
    removed: bool = False


@dataclass
class _ManagedCycleFrame:
    document_id: str
    next_edge: int


def _managed_hierarchy_edge_multiplicity(
    symbol: AltiumCompiledSheetSymbol,
) -> int:
    if (
        symbol.is_repeat
        and symbol.repeat_start is not None
        and symbol.repeat_end is not None
        and symbol.repeat_end >= symbol.repeat_start
    ):
        return symbol.repeat_end - symbol.repeat_start + 1
    return 1


def _managed_sheet_symbol_child_ids(
    symbol: AltiumCompiledSheetSymbol,
) -> tuple[str, ...]:
    if symbol._managed_child_logical_document_ids:
        return symbol._managed_child_logical_document_ids
    if symbol.child_logical_document_id is not None:
        return (symbol.child_logical_document_id,)
    return ()


def _managed_document_name_compare(
    left: AltiumCompiledLogicalDocument,
    right: AltiumCompiledLogicalDocument,
) -> int:
    return managed_alpha_numeric_compare(
        Path(left.file_name).stem,
        Path(right.file_name).stem,
    )


def _managed_hierarchy_edges(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> tuple[dict[str, list[_ManagedHierarchyEdge]], dict[str, int]]:
    edges_by_parent: dict[str, list[_ManagedHierarchyEdge]] = {
        document.id: [] for document in logical_documents
    }
    parent_counts = {document.id: 0 for document in logical_documents}
    symbols_by_parent: dict[str, list[AltiumCompiledSheetSymbol]] = defaultdict(list)
    for symbol in sheet_symbols:
        symbols_by_parent[symbol.logical_document_id].append(symbol)
    for document in sorted(
        logical_documents,
        key=cmp_to_key(_managed_document_name_compare),
    ):
        for symbol in symbols_by_parent.get(document.id, ()):
            multiplicity = _managed_hierarchy_edge_multiplicity(symbol)
            for child_id in _managed_sheet_symbol_child_ids(symbol):
                if child_id == document.id:
                    continue
                edges_by_parent[document.id].append(
                    _ManagedHierarchyEdge(symbol.id, child_id, multiplicity)
                )
                parent_counts[child_id] += multiplicity
    return edges_by_parent, parent_counts


def _managed_hierarchy_child_counts(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    edges_by_parent: Mapping[str, Sequence[_ManagedHierarchyEdge]],
) -> dict[str, int]:
    return {
        document.id: sum(edge.multiplicity for edge in edges_by_parent[document.id])
        for document in logical_documents
    }


def _remove_managed_paths_to_root(
    current_id: str,
    root_id: str,
    edges_by_parent: Mapping[str, Sequence[_ManagedHierarchyEdge]],
    parent_counts: dict[str, int],
    visited: set[str],
) -> None:
    if current_id in visited:
        return
    visited.add(current_id)
    stack = [_ManagedCycleFrame(current_id, len(edges_by_parent[current_id]))]
    while stack:
        frame = stack[-1]
        if frame.next_edge == 0:
            visited.remove(frame.document_id)
            stack.pop()
            continue
        frame.next_edge -= 1
        edge = edges_by_parent[frame.document_id][frame.next_edge]
        if edge.removed:
            continue
        if edge.child_id == root_id:
            edge.removed = True
            parent_counts[root_id] -= edge.multiplicity
        elif edge.child_id in edges_by_parent and edge.child_id not in visited:
            visited.add(edge.child_id)
            stack.append(
                _ManagedCycleFrame(
                    edge.child_id,
                    len(edges_by_parent[edge.child_id]),
                )
            )


def _remove_managed_hierarchy_cycles(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    edges_by_parent: Mapping[str, Sequence[_ManagedHierarchyEdge]],
    parent_counts: dict[str, int],
    child_counts: Mapping[str, int],
) -> None:
    def compare_documents(
        left: AltiumCompiledLogicalDocument,
        right: AltiumCompiledLogicalDocument,
    ) -> int:
        parent_result = parent_counts[left.id] - parent_counts[right.id]
        if parent_result:
            return parent_result
        child_result = child_counts[right.id] - child_counts[left.id]
        if child_result:
            return child_result
        return _managed_document_name_compare(left, right)

    traversal_order = sorted(
        logical_documents,
        key=cmp_to_key(compare_documents),
    )
    visited: set[str] = set()
    for root in traversal_order:
        for edge in tuple(edges_by_parent[root.id]):
            if not edge.removed:
                _remove_managed_paths_to_root(
                    edge.child_id,
                    root.id,
                    edges_by_parent,
                    parent_counts,
                    visited,
                )


def _managed_hierarchy_root_state(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> tuple[set[str], dict[str, int]]:
    """Apply AD26 ProjectStructureBuilder root and child-count semantics."""
    edges_by_parent, parent_counts = _managed_hierarchy_edges(
        logical_documents,
        sheet_symbols,
    )
    child_counts = _managed_hierarchy_child_counts(logical_documents, edges_by_parent)
    _remove_managed_hierarchy_cycles(
        logical_documents,
        edges_by_parent,
        parent_counts,
        child_counts,
    )

    top_ids = {
        document.id for document in logical_documents if parent_counts[document.id] == 0
    }
    retained_counts = {
        document.id: sum(
            edge.multiplicity
            for edge in edges_by_parent[document.id]
            if not edge.removed
        )
        for document in logical_documents
    }
    return top_ids, retained_counts


def _managed_retained_children_by_symbol(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> dict[str, tuple[str, ...]]:
    edges_by_parent, parent_counts = _managed_hierarchy_edges(
        logical_documents,
        sheet_symbols,
    )
    child_counts = _managed_hierarchy_child_counts(logical_documents, edges_by_parent)
    _remove_managed_hierarchy_cycles(
        logical_documents,
        edges_by_parent,
        parent_counts,
        child_counts,
    )
    retained: dict[str, list[str]] = {symbol.id: [] for symbol in sheet_symbols}
    for edges in edges_by_parent.values():
        for edge in edges:
            if not edge.removed:
                retained[edge.symbol_id].append(edge.child_id)
    return {symbol_id: tuple(child_ids) for symbol_id, child_ids in retained.items()}


def _top_level_document_ids(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> set[str]:
    top_ids, _child_counts = _managed_hierarchy_root_state(
        logical_documents,
        sheet_symbols,
    )
    return top_ids


def _primary_top_level_document(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> AltiumCompiledLogicalDocument | None:
    _, managed_child_counts = _managed_hierarchy_root_state(
        logical_documents,
        sheet_symbols,
    )
    candidates = [
        document for document in logical_documents if document.is_top_level_candidate
    ]

    def compare_candidates(
        left: AltiumCompiledLogicalDocument,
        right: AltiumCompiledLogicalDocument,
    ) -> int:
        child_result = managed_child_counts.get(right.id, 0) - (
            managed_child_counts.get(left.id, 0)
        )
        if child_result:
            return child_result
        return _managed_document_name_compare(left, right)

    return min(
        candidates,
        key=cmp_to_key(compare_candidates),
        default=None,
    )


def _managed_sheet_symbol_names(raw_file_name: str) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_file_name.split(";"):
        name = dotnet_trim(raw_name)
        if not name:
            continue
        if name.lower().endswith((".schdoc", ".schdot", ".sch")):
            name = name.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        key = dotnet_ordinal_ignore_case_key(name)
        if key not in seen:
            seen.add(key)
            names.append(name)
    return tuple(names)


@dataclass(frozen=True)
class _ManagedChildResolution:
    child_ids: tuple[str, ...]
    child_paths: tuple[str, ...]
    match_kind: str


def _managed_child_resolution(
    raw_file_name: str,
    *,
    owner_id: str,
    logical_id_by_name: Mapping[str, str],
    source_path_by_logical_id: Mapping[str, str],
    file_name_by_logical_id: Mapping[str, str],
) -> _ManagedChildResolution:
    names = _managed_sheet_symbol_names(raw_file_name)
    child_ids = tuple(
        child_id
        for name in names
        if (child_id := logical_id_by_name.get(dotnet_ordinal_ignore_case_key(name)))
        is not None
        and child_id != owner_id
    )
    child_paths = tuple(source_path_by_logical_id[child_id] for child_id in child_ids)
    if not names:
        match_kind = "missing_filename"
    elif not child_ids:
        match_kind = "unresolved"
    elif len(names) > 1:
        match_kind = "managed_multi_name"
    elif _normalize_project_reference(raw_file_name) == _normalize_project_reference(
        child_paths[0]
    ):
        match_kind = "source_path"
    elif dotnet_ordinal_ignore_case_key(
        raw_file_name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    ) == dotnet_ordinal_ignore_case_key(file_name_by_logical_id[child_ids[0]]):
        match_kind = "file_name"
    else:
        match_kind = "file_stem"
    return _ManagedChildResolution(child_ids, child_paths, match_kind)


def _managed_interface_child_ids(
    raw_file_name: str,
    logical_id_by_name: Mapping[str, str],
) -> tuple[str, ...]:
    return tuple(
        child_id
        for name in _managed_sheet_symbol_names(raw_file_name)
        if (child_id := logical_id_by_name.get(dotnet_ordinal_ignore_case_key(name)))
        is not None
    )


def _sheet_symbol_is_compile_masked(
    symbol: "SchSheetSymbolInfo",
    compile_mask_bounds: tuple[tuple[int, int, int, int], ...],
) -> bool:
    if not compile_mask_bounds:
        return False
    record = symbol.record
    points = [
        (
            int(getattr(record.location, "x", 0)) * ALTIUM_COORD_SCALE
            + int(getattr(record.location, "x_frac", 0)),
            int(getattr(record.location, "y", 0)) * ALTIUM_COORD_SCALE
            + int(getattr(record.location, "y_frac", 0)),
        )
    ]
    for entry in symbol.entries:
        try:
            x, y, x_frac, y_frac = _sheet_entry_precise_connection_point(
                symbol,
                entry,
            )
        except ValueError:
            continue
        points.append(
            (
                x * ALTIUM_COORD_SCALE + x_frac,
                y * ALTIUM_COORD_SCALE + y_frac,
            )
        )
    return any(
        all(min_x < x < max_x and min_y < y < max_y for x, y in points)
        for min_x, min_y, max_x, max_y in compile_mask_bounds
    )


def _build_logical_and_symbol_rows(
    schdocs: Sequence["AltiumSchDoc"],
    *,
    project_base_dir: Path | None,
    saved_structure_top_level_file_name: str | None,
) -> tuple[
    tuple[AltiumCompiledLogicalDocument, ...],
    tuple[AltiumCompiledSheetSymbol, ...],
    dict[str, "SchSheetSymbolInfo"],
    tuple[AltiumCompileDiagnostic, ...],
]:
    logical_ids: list[str] = []
    source_paths: list[str] = []
    file_names: list[str] = []
    source_path_by_logical_id: dict[str, str] = {}
    file_name_by_logical_id: dict[str, str] = {}
    logical_id_by_managed_name: dict[str, str] = {}
    for ordinal, schdoc in enumerate(schdocs):
        source_path = _relative_source_path(schdoc.filepath, project_base_dir)
        logical_id = _logical_document_id(source_path, ordinal)
        file_name = schdoc.filepath.name if schdoc.filepath else f"sheet{ordinal}"
        logical_ids.append(logical_id)
        source_paths.append(source_path)
        file_names.append(file_name)
        source_path_by_logical_id[logical_id] = source_path
        file_name_by_logical_id[logical_id] = file_name
        logical_id_by_managed_name.setdefault(
            dotnet_ordinal_ignore_case_key(Path(file_name).stem),
            logical_id,
        )

    symbol_rows: list[AltiumCompiledSheetSymbol] = []
    symbol_info_by_id: dict[str, "SchSheetSymbolInfo"] = {}
    symbol_ids_by_logical_id: dict[str, list[str]] = {}
    parent_symbol_ids_by_logical_id: dict[str, list[str]] = {}
    diagnostics: list[AltiumCompileDiagnostic] = []
    for ordinal, schdoc in enumerate(schdocs):
        logical_id = logical_ids[ordinal]
        compile_mask_bounds = tuple(schdoc._collect_compile_mask_precise_bounds())
        for symbol_index, symbol in _compiler_source_rows(
            _compiler_sheet_symbol_sources(schdoc)
        ):
            if _sheet_symbol_is_compile_masked(symbol, compile_mask_bounds):
                continue
            child_file_name = symbol.child_filename
            symbol_id = _sheet_symbol_id(logical_id, symbol, symbol_index)
            resolution = _managed_child_resolution(
                child_file_name,
                owner_id=logical_id,
                logical_id_by_name=logical_id_by_managed_name,
                source_path_by_logical_id=source_path_by_logical_id,
                file_name_by_logical_id=file_name_by_logical_id,
            )
            for child_logical_id in resolution.child_ids:
                parent_symbol_ids_by_logical_id.setdefault(
                    child_logical_id,
                    [],
                ).append(symbol_id)
            is_repeat, repeat_prefix, repeat_start, repeat_end = _repeat_parts(
                symbol.designator
            )
            row = AltiumCompiledSheetSymbol(
                id=symbol_id,
                logical_document_id=logical_id,
                source_object_id=symbol.unique_id,
                designator=symbol.designator,
                child_filename=child_file_name,
                child_logical_document_id=(
                    resolution.child_ids[0] if resolution.child_ids else None
                ),
                child_source_path=(
                    resolution.child_paths[0] if resolution.child_paths else None
                ),
                child_match_kind=resolution.match_kind,
                child_candidate_logical_document_ids=resolution.child_ids,
                is_repeat=is_repeat,
                repeat_prefix=repeat_prefix,
                repeat_start=repeat_start,
                repeat_end=repeat_end,
                entry_count=len(symbol.entries),
                diagnostics=(),
                _managed_child_logical_document_ids=resolution.child_ids,
                _managed_interface_child_logical_document_ids=(
                    _managed_interface_child_ids(
                        child_file_name,
                        logical_id_by_managed_name,
                    )
                ),
                _managed_source_index_in_sheet=int(
                    getattr(symbol.record, "index_in_sheet", 0)
                ),
                _managed_source_location=(
                    int(symbol.record.location.x),
                    int(symbol.record.location.y),
                ),
            )
            symbol_rows.append(row)
            symbol_info_by_id[symbol_id] = symbol
            symbol_ids_by_logical_id.setdefault(logical_id, []).append(symbol_id)

    logical_rows: list[AltiumCompiledLogicalDocument] = []
    for ordinal, schdoc in enumerate(schdocs):
        source_path = source_paths[ordinal]
        logical_id = logical_ids[ordinal]
        file_name = file_names[ordinal]
        logical_rows.append(
            AltiumCompiledLogicalDocument(
                id=logical_id,
                source_path=source_path,
                file_name=file_name,
                ordinal=ordinal,
                is_top_level_candidate=False,
                sheet_symbol_ids=tuple(symbol_ids_by_logical_id.get(logical_id, ())),
                parent_sheet_symbol_ids=tuple(
                    parent_symbol_ids_by_logical_id.get(logical_id, ())
                ),
                component_source_count=len(_compiler_component_sources(schdoc)),
                local_net_count=0,
            )
        )

    top_ids = _top_level_document_ids(tuple(logical_rows), tuple(symbol_rows))
    if saved_structure_top_level_file_name:
        saved_top_level_name = _logical_source_identity_key(
            saved_structure_top_level_file_name
        )
        saved_top_level_ids = [
            row.id
            for row in logical_rows
            if _logical_source_identity_key(row.file_name) == saved_top_level_name
            or _logical_source_identity_key(
                _logical_source_basename(row.source_path.replace("\\", "/"))
            )
            == saved_top_level_name
        ]
        if len(saved_top_level_ids) == 1:
            top_ids = {saved_top_level_ids[0]}
    logical_rows = [
        replace(row, is_top_level_candidate=row.id in top_ids) for row in logical_rows
    ]
    retained_children = _managed_retained_children_by_symbol(
        tuple(logical_rows),
        tuple(symbol_rows),
    )
    symbol_rows = [
        replace(
            symbol,
            _managed_retained_child_logical_document_ids=retained_children[symbol.id],
        )
        for symbol in symbol_rows
    ]
    return (
        tuple(logical_rows),
        tuple(symbol_rows),
        symbol_info_by_id,
        tuple(diagnostics),
    )


def _compiled_component_source_infos(
    logical_document_id: str,
    *,
    component_source_rows_by_logical_id: Mapping[str, Sequence["SchComponentInfo"]],
    compile_mask_bounds_by_logical_id: Mapping[
        str, tuple[tuple[int, int, int, int], ...]
    ],
    options: NetlistOptions,
    cross_document_multipart_designators: set[str],
    sheet_parameters: Mapping[str, str] | None = None,
    hierarchy_parameters: Mapping[str, str] | None = None,
) -> tuple[_CompiledComponentSourceInfo, ...]:
    logical_components = component_source_rows_by_logical_id.get(
        logical_document_id,
        (),
    )
    compile_mask_bounds = compile_mask_bounds_by_logical_id.get(
        logical_document_id,
        (),
    )
    infos: list[_CompiledComponentSourceInfo] = []
    for source_index, component in _compiler_source_rows(logical_components):
        component_group = (component,)
        definition_component = _compiled_component_group_definition(component_group)
        identity_component = _compiled_component_group_identity(component_group)
        full_designator_component = component_group[0]
        merged_parameters = _compiled_component_group_parameters(
            component_group,
            options,
            sheet_parameters,
            hierarchy_parameters,
        )
        definition_parameters = _compiled_component_group_parameters(
            (definition_component,),
            options,
            sheet_parameters,
            hierarchy_parameters,
        )
        definition_parameter_values = {
            dotnet_ordinal_ignore_case_key(parameter.name): parameter.text
            for parameter in definition_parameters
        }
        evaluated_comment = definition_parameter_values.get(
            dotnet_ordinal_ignore_case_key("Comment"),
            _evaluate_component_field(
                definition_component.comment,
                definition_parameters,
                definition_component,
                options,
                sheet_parameters=sheet_parameters,
                hierarchy_parameters=hierarchy_parameters,
            ),
        )
        evaluated_description = definition_parameter_values.get(
            dotnet_ordinal_ignore_case_key("Description"),
            _evaluate_component_field(
                definition_component.description,
                definition_parameters,
                definition_component,
                options,
                sheet_parameters=sheet_parameters,
                hierarchy_parameters=hierarchy_parameters,
            ),
        )
        value = _resolve_component_display_value(
            _CompiledComponentExpressionView(
                comment=evaluated_comment,
                value=definition_parameter_values.get(
                    dotnet_ordinal_ignore_case_key("Value"),
                    "",
                ),
                description=evaluated_description,
                parameters=definition_parameters,
            ),
            project_params=options.project_parameters,
            sheet_params=(
                options.sheet_parameters
                if sheet_parameters is None
                else dict(sheet_parameters)
            ),
            component_description=evaluated_description,
        )
        if not value and evaluated_comment:
            value = evaluated_comment
        parameters = tuple(
            sorted(
                ((parameter.name, parameter.text) for parameter in merged_parameters),
                key=cmp_to_key(_compiled_component_parameter_pair_compare),
            )
        )
        kind_value = (
            definition_component.component_kind.value
            if hasattr(definition_component.component_kind, "value")
            else int(definition_component.component_kind)
        )
        infos.append(
            _CompiledComponentSourceInfo(
                source_index=source_index,
                component=identity_component,
                components=component_group,
                include_in_netlist=_component_includes_in_netlist(
                    definition_component,
                    compile_mask_bounds,
                ),
                logical_designator=full_designator_component.designator,
                part_count=_component_record_subparts_count(definition_component),
                current_part_id=_component_record_current_part_id(definition_component),
                value=value,
                parameters=parameters,
                kind_value=kind_value,
                footprint=_compiled_component_group_footprint(component_group),
                component_kind=_component_kind_name(definition_component),
                pin_count=(
                    _compiled_component_group_all_pin_count(component_group)
                    if _component_record_is_multipart(definition_component)
                    else _compiled_component_group_pin_count(component_group)
                ),
                all_pin_count=_compiled_component_group_all_pin_count(component_group),
                exclude_from_bom=not component_kind_includes_in_bom(
                    definition_component.component_kind
                ),
                description=evaluated_description,
                library_ref=definition_component.library_ref,
                design_item_id=str(
                    getattr(definition_component.record, "design_item_id", "") or ""
                ),
                unique_id=identity_component.unique_id,
                cross_document_multipart=(
                    dotnet_ordinal_ignore_case_key(full_designator_component.designator)
                    in cross_document_multipart_designators
                ),
            )
        )
    return tuple(infos)


def _compiled_component_naming_room(
    physical_row: AltiumCompiledPhysicalDocument,
    physical_rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]],
    physical_row_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    physical_instance_offsets: dict[str, int],
    physical_count_by_logical_id: dict[str, int],
    channel_global_indices: Mapping[str, int],
    channel_differentiate_values: Mapping[str, int],
    compile_options: AltiumProjectCompileOptions,
) -> RoomDetails | None:
    logical_physical_rows = physical_rows_by_logical_id[
        physical_row.logical_document_id
    ]
    if len(logical_physical_rows) <= 1:
        return None
    return _room_details_for_compiled_naming(
        physical_row,
        physical_row_by_id,
        physical_instance_offsets=physical_instance_offsets,
        physical_count_by_logical_id=physical_count_by_logical_id,
        channel_global_indices=channel_global_indices,
        channel_differentiate_values=channel_differentiate_values,
        compile_options=compile_options,
        sheet_designator=physical_row.room_name,
    )


def _compiled_component_row_for_physical_document(
    physical_row: AltiumCompiledPhysicalDocument,
    component_info: _CompiledComponentSourceInfo,
    component_index: int,
    *,
    naming_room: RoomDetails | None,
    channel_designator_format: str,
    annotation: AnnotationFile,
    exact_component_paths: set[str],
    used_annotation_indices: set[int],
) -> AltiumCompiledComponent | None:
    if not component_info.include_in_netlist:
        return None
    component_id = _component_id(
        physical_row.id, component_info.component, component_index
    )
    logical_designator = component_info.logical_designator
    physical_designator = logical_designator
    source_unique_id_path = _component_unique_id_path(
        physical_row.physical_instance_unique_id,
        component_info.unique_id,
    )
    annotation_state = "logical"
    annotation_locked = False
    component_diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()
    annotation_record, matched_state, match_diagnostic = _find_designator_annotation(
        source_unique_id_path,
        annotation,
        exact_component_paths=exact_component_paths,
        used_annotation_indices=used_annotation_indices,
    )
    if annotation_record is not None:
        used_annotation_indices.add(annotation_record.source_index)
        physical_designator = annotation_record.physical_designator
        annotation_state = matched_state
        annotation_locked = annotation_record.locked
    else:
        if naming_room is not None:
            physical_designator = apply_channel_pattern(
                channel_designator_format, naming_room, logical_designator
            )
            annotation_state = "channel_format"
        if match_diagnostic is not None:
            annotation_state = matched_state
            component_diagnostics = (match_diagnostic,)

    return AltiumCompiledComponent(
        id=component_id,
        logical_document_id=physical_row.logical_document_id,
        physical_document_id=physical_row.id,
        source_object_id=component_info.unique_id,
        source_unique_id_path=source_unique_id_path,
        logical_designator=logical_designator,
        physical_designator=physical_designator,
        display_designator=physical_designator,
        lib_reference=component_info.library_ref,
        design_item_id=component_info.design_item_id,
        footprint=component_info.footprint,
        component_kind=component_info.component_kind,
        component_kind_value=component_info.kind_value,
        include_in_netlist=component_info.include_in_netlist,
        exclude_from_bom=component_info.exclude_from_bom,
        value=component_info.value,
        description=component_info.description,
        parameters=component_info.parameters,
        pin_count=component_info.pin_count,
        all_pin_count=component_info.all_pin_count,
        part_count=component_info.part_count,
        current_part_id=component_info.current_part_id,
        annotation_state=annotation_state,
        annotation_locked=annotation_locked,
        diagnostics=component_diagnostics,
        _project_multipart_collapsed=component_info.cross_document_multipart,
        _managed_source_component_occurrences=(
            (
                physical_row.id,
                physical_row.logical_document_id,
                component_info.source_index,
                physical_designator,
            ),
        ),
    )


def _compiled_component_body_rows_for_physical_document(
    physical_row: AltiumCompiledPhysicalDocument,
    component_info: _CompiledComponentSourceInfo,
    component_row: AltiumCompiledComponent,
    *,
    implemented_part_counts: Mapping[int, int],
    naming_room: RoomDetails | None,
    channel_designator_format: str,
) -> tuple[AltiumCompiledComponent, ...]:
    """Retain exact source-body evidence before multipart presentation collapse."""

    return tuple(
        replace(
            component_row,
            id=(
                f"{component_row.id}:body:{body_index}:"
                f"{source_component.unique_id or 'missing'}"
            ),
            source_object_id=source_component.unique_id,
            source_unique_id_path=_component_unique_id_path(
                physical_row.physical_instance_unique_id,
                source_component.unique_id,
            ),
            logical_designator=source_component.designator,
            part_count=_component_record_subparts_count(source_component),
            current_part_id=_component_record_current_part_id(source_component),
            display_designator=_compiled_component_body_display(
                source_component,
                component_row,
                implemented_part_counts=implemented_part_counts,
                naming_room=naming_room,
                channel_designator_format=channel_designator_format,
            ),
        )
        for body_index, source_component in enumerate(component_info.components)
    )


def _compiled_component_body_display(
    component: SchComponentInfo,
    row: AltiumCompiledComponent,
    *,
    implemented_part_counts: Mapping[int, int],
    naming_room: RoomDetails | None,
    channel_designator_format: str,
) -> str:
    custom = row.annotation_state in {"annotation_exact", "annotation_suffix"}
    base = row.physical_designator if custom else component.designator
    suffix = ""
    if implemented_part_counts[id(component.record)] > 1:
        suffix = _component_part_alpha_suffix(
            part_count=_component_record_subparts_count(component),
            current_part_id=_component_record_current_part_id(component),
        )
    return _format_part_physical_designator(
        channel_designator_format, None if custom else naming_room, base, suffix
    )


@dataclass(slots=True)
class _PhysicalInstantiationState:
    logical_by_id: dict[str, AltiumCompiledLogicalDocument]
    symbols_by_logical_id: dict[str, list[AltiumCompiledSheetSymbol]]
    channel_rooms_by_symbol_child_id: dict[tuple[str, str], RoomDetails]
    repeat_channel_values: dict[tuple[str, str, int], int]
    options: NetlistOptions
    new_indexing_of_sheet_symbols: bool
    flat_physical_tree: bool
    physical_rows: list[AltiumCompiledPhysicalDocument]
    physical_sheet_symbol_rows: list[AltiumCompiledPhysicalSheetSymbol]
    physical_child_ids: dict[str, list[str]]
    symbol_child_physical_ids: dict[str, list[str]]
    flat_physical_document_id_by_logical_id: dict[str, str]
    diagnostics: list[AltiumCompileDiagnostic]


@dataclass(frozen=True)
class _PhysicalChildPlacement:
    physical_id: str
    instance_path: str
    instance_unique_id: str
    instance: _PhysicalChildInstance
    sheet_number: str | None
    already_instantiated: bool = False


def _instantiate_physical_document(
    state: _PhysicalInstantiationState,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    physical_id: str,
    physical_instance_path: str,
    physical_instance_unique_id: str,
    channel_index: int,
    channel_prefix: str | None,
    channel_alpha: str | None,
    room_name: str,
    parent_id: str | None,
    parent_sheet_symbol_id: str | None,
    parent_from_repeat_sheet_symbol: bool = False,
    parent_sheet_symbol_index: int = 0,
    parent_sheet_symbol_location: tuple[int, int] = (0, 0),
    parent_sheet_symbol_source_id: str = "",
    managed_repeat_channel_value: int | None = None,
    sheet_number: str | None,
    document_number: str | None,
    stack: tuple[str, ...],
) -> None:
    physical_ordinal = len(state.physical_rows)
    if state.flat_physical_tree:
        state.flat_physical_document_id_by_logical_id.setdefault(
            logical_document.id,
            physical_id,
        )
    state.physical_rows.append(
        AltiumCompiledPhysicalDocument(
            id=physical_id,
            logical_document_id=logical_document.id,
            source_path=logical_document.source_path,
            file_name=logical_document.file_name,
            ordinal=physical_ordinal,
            physical_instance_path=physical_instance_path,
            physical_instance_unique_id=physical_instance_unique_id,
            channel_index=channel_index,
            channel_prefix=channel_prefix,
            channel_alpha=channel_alpha,
            room_name=room_name,
            parent_id=parent_id,
            parent_sheet_symbol_id=parent_sheet_symbol_id,
            sheet_number=sheet_number,
            document_number=document_number,
            _managed_parent_from_repeat_sheet_symbol=(parent_from_repeat_sheet_symbol),
            _managed_parent_sheet_symbol_index=parent_sheet_symbol_index,
            _managed_parent_sheet_symbol_location=parent_sheet_symbol_location,
            _managed_parent_sheet_symbol_source_id=parent_sheet_symbol_source_id,
            _managed_repeat_channel_value=managed_repeat_channel_value,
        )
    )

    next_stack = (*stack, logical_document.id)
    for symbol in state.symbols_by_logical_id.get(logical_document.id, ()):
        all_child_ids = _managed_sheet_symbol_child_ids(symbol)
        retained_child_ids = symbol._managed_retained_child_logical_document_ids
        if retained_child_ids is None:
            retained_child_ids = all_child_ids
        removed_child_ids = set(all_child_ids) - set(retained_child_ids)
        if removed_child_ids:
            state.diagnostics.append(
                AltiumCompileDiagnostic(
                    severity="error",
                    code="cyclic_sheet_symbol_child",
                    message="Sheet-symbol child graph contains a cycle.",
                    source_id=symbol.id,
                )
            )
        for child_ordinal, child_logical_id in enumerate(retained_child_ids):
            child_logical_document = state.logical_by_id[child_logical_id]
            if child_logical_id in next_stack:
                continue
            for child_instance in _physical_child_instances(
                symbol,
                child_logical_id,
                state.channel_rooms_by_symbol_child_id.get(
                    (symbol.id, child_logical_id)
                ),
                state.repeat_channel_values,
                new_indexing=state.new_indexing_of_sheet_symbols,
            ):
                _instantiate_physical_child(
                    state,
                    logical_document=logical_document,
                    physical_id=physical_id,
                    physical_instance_path=physical_instance_path,
                    physical_instance_unique_id=physical_instance_unique_id,
                    physical_ordinal=physical_ordinal,
                    symbol=symbol,
                    child_logical_id=child_logical_id,
                    child_logical_document=child_logical_document,
                    child_instance=child_instance,
                    child_ordinal=child_ordinal,
                    child_count=len(retained_child_ids),
                    next_stack=next_stack,
                )


def _physical_child_sheet_number(
    state: _PhysicalInstantiationState,
    *,
    physical_ordinal: int,
    symbol: AltiumCompiledSheetSymbol,
    child_logical_id: str,
    child_instance: _PhysicalChildInstance,
) -> str | None:
    parent_number = state.physical_rows[physical_ordinal].sheet_number or ""
    is_channel = (
        symbol.is_repeat
        or (
            symbol.id,
            child_logical_id,
        )
        in state.channel_rooms_by_symbol_child_id
    )
    if is_channel:
        return f"{parent_number}.{child_instance.sheet_number}"
    return parent_number or None


def _physical_child_paths(
    state: _PhysicalInstantiationState,
    *,
    physical_instance_path: str,
    physical_instance_unique_id: str,
    symbol: AltiumCompiledSheetSymbol,
    child_logical_document: AltiumCompiledLogicalDocument,
    child_instance: _PhysicalChildInstance,
) -> tuple[str, str]:
    if state.options.net_identifier_scope in {
        NetIdentifierScope.FLAT,
        NetIdentifierScope.GLOBAL,
    }:
        child_path = (
            Path(child_logical_document.file_name).stem or child_instance.path_segment
        )
    else:
        child_path = _path_join(
            physical_instance_path,
            child_instance.path_segment,
        )
    source_object_id = symbol.source_object_id or symbol.id
    repeat_channel_value = child_instance.managed_repeat_channel_value
    unique_segment = (
        f"{repeat_channel_value}{source_object_id}"
        if symbol.is_repeat
        else source_object_id
    )
    return child_path, _unique_id_path_join(
        physical_instance_unique_id,
        unique_segment,
    )


def _flat_physical_child_placement(
    state: _PhysicalInstantiationState,
    child_logical_document: AltiumCompiledLogicalDocument,
    child_instance: _PhysicalChildInstance,
) -> _PhysicalChildPlacement:
    child_path = Path(child_logical_document.file_name).stem
    existing_id = state.flat_physical_document_id_by_logical_id.get(
        child_logical_document.id
    )
    child_physical_id = existing_id or _physical_document_id(
        child_logical_document.id,
        len(state.physical_rows),
    )
    return _PhysicalChildPlacement(
        physical_id=child_physical_id,
        instance_path=child_path,
        instance_unique_id="",
        instance=_PhysicalChildInstance(
            instance_key=child_instance.instance_key,
            path_segment=child_instance.path_segment,
            sheet_symbol_designator=child_instance.sheet_symbol_designator,
            channel_index=_no_channel_index(
                new_indexing=state.new_indexing_of_sheet_symbols
            ),
            channel_prefix=None,
            channel_alpha=None,
            room_name=child_path,
            sheet_number=None,
            document_number=None,
        ),
        sheet_number=None,
        already_instantiated=existing_id is not None,
    )


def _physical_child_placement(
    state: _PhysicalInstantiationState,
    *,
    physical_id: str,
    physical_instance_path: str,
    physical_instance_unique_id: str,
    physical_ordinal: int,
    symbol: AltiumCompiledSheetSymbol,
    child_logical_id: str,
    child_logical_document: AltiumCompiledLogicalDocument,
    child_instance: _PhysicalChildInstance,
    child_ordinal: int,
    child_count: int,
) -> tuple[_PhysicalChildPlacement, str]:
    if state.flat_physical_tree:
        return (
            _flat_physical_child_placement(
                state,
                child_logical_document,
                child_instance,
            ),
            child_instance.instance_key,
        )
    child_path, child_unique_id = _physical_child_paths(
        state,
        physical_instance_path=physical_instance_path,
        physical_instance_unique_id=physical_instance_unique_id,
        symbol=symbol,
        child_logical_document=child_logical_document,
        child_instance=child_instance,
    )
    instance_key = (
        child_instance.instance_key
        if child_count == 1
        else f"child:{child_ordinal}:{child_instance.instance_key}"
    )
    return (
        _PhysicalChildPlacement(
            physical_id=_physical_child_document_id(
                physical_id,
                symbol.id,
                instance_key,
            ),
            instance_path=child_path,
            instance_unique_id=child_unique_id,
            instance=child_instance,
            sheet_number=_physical_child_sheet_number(
                state,
                physical_ordinal=physical_ordinal,
                symbol=symbol,
                child_logical_id=child_logical_id,
                child_instance=child_instance,
            ),
        ),
        instance_key,
    )


def _instantiate_physical_child(
    state: _PhysicalInstantiationState,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    physical_id: str,
    physical_instance_path: str,
    physical_instance_unique_id: str,
    physical_ordinal: int,
    symbol: AltiumCompiledSheetSymbol,
    child_logical_id: str,
    child_logical_document: AltiumCompiledLogicalDocument,
    child_instance: _PhysicalChildInstance,
    child_ordinal: int,
    child_count: int,
    next_stack: tuple[str, ...],
) -> None:
    placement, instance_key = _physical_child_placement(
        state,
        physical_id=physical_id,
        physical_instance_path=physical_instance_path,
        physical_instance_unique_id=physical_instance_unique_id,
        physical_ordinal=physical_ordinal,
        symbol=symbol,
        child_logical_id=child_logical_id,
        child_logical_document=child_logical_document,
        child_instance=child_instance,
        child_ordinal=child_ordinal,
        child_count=child_count,
    )
    if not state.flat_physical_tree:
        state.physical_child_ids.setdefault(physical_id, []).append(
            placement.physical_id
        )
    state.symbol_child_physical_ids[symbol.id].append(placement.physical_id)
    state.physical_sheet_symbol_rows.append(
        AltiumCompiledPhysicalSheetSymbol(
            id=(
                f"{physical_id}:sheet_symbol:"
                f"{symbol.source_object_id or symbol.id}:{instance_key}"
            ),
            logical_sheet_symbol_id=symbol.id,
            owner_logical_document_id=logical_document.id,
            owner_physical_document_id=physical_id,
            source_object_id=symbol.source_object_id,
            logical_designator=child_instance.sheet_symbol_designator,
            physical_designator=child_instance.sheet_symbol_designator,
            # AD26 reports the resolved child document file name here,
            # so extension-less symbol references still surface the
            # child's real file name; the raw symbol filename is only a
            # fallback for unresolved symbols, which never reach
            # physical instantiation (SheetSymbolAdapter
            # DM_SheetSymbolFileName / DM_RawSheetSymbolFileName).
            sheet_symbol_file_name=child_logical_document.file_name,
            child_logical_document_id=child_logical_id,
            child_source_path=child_logical_document.source_path,
            child_physical_document_id=placement.physical_id,
            child_physical_instance_path=placement.instance_path,
            child_physical_instance_unique_id=placement.instance_unique_id,
            child_channel_index=placement.instance.channel_index,
            entry_count=symbol.entry_count,
        )
    )
    if not placement.already_instantiated:
        _instantiate_physical_document(
            state,
            logical_document=child_logical_document,
            physical_id=placement.physical_id,
            physical_instance_path=placement.instance_path,
            physical_instance_unique_id=placement.instance_unique_id,
            channel_index=placement.instance.channel_index,
            channel_prefix=placement.instance.channel_prefix,
            channel_alpha=placement.instance.channel_alpha,
            room_name=placement.instance.room_name,
            parent_id=None if state.flat_physical_tree else physical_id,
            parent_sheet_symbol_id=symbol.id,
            parent_from_repeat_sheet_symbol=symbol.is_repeat,
            parent_sheet_symbol_index=symbol._managed_source_index_in_sheet,
            parent_sheet_symbol_location=symbol._managed_source_location,
            parent_sheet_symbol_source_id=symbol.source_object_id,
            managed_repeat_channel_value=(
                placement.instance.managed_repeat_channel_value
            ),
            sheet_number=placement.sheet_number,
            document_number=None,
            stack=next_stack,
        )


def _cross_document_multipart_designators(
    component_rows_by_logical_id: Mapping[str, Sequence["SchComponentInfo"]],
) -> set[str]:
    logical_ids_by_designator: dict[str, set[str]] = defaultdict(set)
    for logical_id, source_components in component_rows_by_logical_id.items():
        for component in source_components:
            if _designator_binds_multipart_records(
                component.designator
            ) and _component_record_is_multipart(component):
                logical_ids_by_designator[
                    dotnet_ordinal_ignore_case_key(component.designator)
                ].add(logical_id)
    return {
        designator
        for designator, logical_ids in logical_ids_by_designator.items()
        if len(logical_ids) > 1
    }


def _exact_component_paths(
    physical_rows: Sequence[AltiumCompiledPhysicalDocument],
    component_rows_by_logical_id: Mapping[str, Sequence["SchComponentInfo"]],
) -> set[str]:
    return {
        _normalized_unique_id_path(
            _component_unique_id_path(
                physical_row.physical_instance_unique_id,
                component.unique_id,
            )
        )
        for physical_row in physical_rows
        for component in component_rows_by_logical_id.get(
            physical_row.logical_document_id,
            (),
        )
        if component.unique_id
    }


def _sheet_symbol_parameter_values(
    symbol_info: "SchSheetSymbolInfo",
) -> tuple[tuple[str, str], ...]:
    record = symbol_info.record
    parameters = getattr(
        symbol_info, "_source_parameters", getattr(record, "parameters", None)
    )
    if parameters is None:
        parameters = getattr(record, "children", ())
    return tuple(
        (str(parameter.name or ""), str(parameter.text or ""))
        for parameter in parameters or ()
        if getattr(parameter, "name", None) and hasattr(parameter, "text")
    )


def _compiled_hierarchy_parameters(
    physical_row: AltiumCompiledPhysicalDocument,
    *,
    physical_row_by_id: Mapping[str, AltiumCompiledPhysicalDocument],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
    sheet_symbol_designator_by_child_physical_id: Mapping[str, str],
) -> dict[str, str]:
    if physical_row.parent_id is None:
        return {}

    symbol_parameters: dict[str, tuple[str, str]] = {}
    current = physical_row
    while current.parent_id is not None:
        symbol_id = current.parent_sheet_symbol_id
        symbol_info = (
            sheet_symbol_info_by_id.get(symbol_id) if symbol_id is not None else None
        )
        if symbol_info is not None:
            for name, value in _sheet_symbol_parameter_values(symbol_info):
                symbol_parameters.setdefault(
                    dotnet_ordinal_ignore_case_key(name),
                    (name, value),
                )
        parent = physical_row_by_id.get(current.parent_id)
        if parent is None:
            break
        current = parent

    values: dict[str, tuple[str, str]] = {}
    bottom_designator = sheet_symbol_designator_by_child_physical_id.get(
        physical_row.id,
        "",
    )
    if bottom_designator:
        values[dotnet_ordinal_ignore_case_key("SheetSymbolDesignator")] = (
            "SheetSymbolDesignator",
            bottom_designator,
        )
    values.update(symbol_parameters)
    return {name: value for name, value in values.values()}


def _build_compiled_component_rows(
    physical_rows: tuple[AltiumCompiledPhysicalDocument, ...],
    *,
    component_rows_by_logical_id: Mapping[str, Sequence["SchComponentInfo"]],
    implemented_part_counts_by_logical_id: Mapping[str, Mapping[int, int]],
    sheet_parameters_by_logical_id: Mapping[str, Mapping[str, str]],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
    physical_sheet_symbol_rows: Sequence[AltiumCompiledPhysicalSheetSymbol],
    compile_mask_bounds_by_logical_id: Mapping[
        str, tuple[tuple[int, int, int, int], ...]
    ],
    cross_document_multipart_designators: set[str],
    compile_options: AltiumProjectCompileOptions,
    options: NetlistOptions,
    annotation: AnnotationFile,
    channel_designator_format: str,
) -> tuple[
    list[AltiumCompiledComponent],
    list[AltiumCompiledComponent],
    dict[str, list[str]],
    dict[str, RoomDetails],
]:
    physical_rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]] = (
        defaultdict(list)
    )
    for physical_row in physical_rows:
        physical_rows_by_logical_id[physical_row.logical_document_id].append(
            physical_row
        )

    physical_row_by_id = {row.id: row for row in physical_rows}
    physical_instance_offsets = _logical_instance_offsets(physical_rows)
    physical_count_by_logical_id = _physical_count_by_logical_id(physical_rows)
    channel_global_indices = _channel_global_indices(physical_row_by_id)
    channel_differentiate_values = _channel_differentiate_values_for_room_style(
        physical_row_by_id,
        channel_global_indices=channel_global_indices,
        style=compile_options.channel_room_naming_style,
    )
    exact_component_paths = _exact_component_paths(
        physical_rows,
        component_rows_by_logical_id,
    )
    used_annotation_indices: set[int] = set()
    sheet_symbol_designator_by_child_physical_id = {
        row.child_physical_document_id: row.logical_designator
        for row in physical_sheet_symbol_rows
    }
    naming_rooms_by_physical_id: dict[str, RoomDetails] = {}
    component_rows: list[AltiumCompiledComponent] = []
    component_body_rows: list[AltiumCompiledComponent] = []
    physical_component_ids: dict[str, list[str]] = {}

    for physical_row in physical_rows:
        naming_room = _compiled_component_naming_room(
            physical_row,
            physical_rows_by_logical_id,
            physical_row_by_id,
            physical_instance_offsets=physical_instance_offsets,
            physical_count_by_logical_id=physical_count_by_logical_id,
            channel_global_indices=channel_global_indices,
            channel_differentiate_values=channel_differentiate_values,
            compile_options=compile_options,
        )
        if naming_room is not None:
            naming_rooms_by_physical_id[physical_row.id] = naming_room
        hierarchy_parameters = _compiled_hierarchy_parameters(
            physical_row,
            physical_row_by_id=physical_row_by_id,
            sheet_symbol_info_by_id=sheet_symbol_info_by_id,
            sheet_symbol_designator_by_child_physical_id=(
                sheet_symbol_designator_by_child_physical_id
            ),
        )
        component_infos = _compiled_component_source_infos(
            physical_row.logical_document_id,
            component_source_rows_by_logical_id=component_rows_by_logical_id,
            compile_mask_bounds_by_logical_id=compile_mask_bounds_by_logical_id,
            options=options,
            cross_document_multipart_designators=(cross_document_multipart_designators),
            sheet_parameters=sheet_parameters_by_logical_id.get(
                physical_row.logical_document_id,
                {},
            ),
            hierarchy_parameters=hierarchy_parameters,
        )
        for component_index, component_info in enumerate(component_infos):
            component_row = _compiled_component_row_for_physical_document(
                physical_row,
                component_info,
                component_index,
                naming_room=naming_room,
                channel_designator_format=channel_designator_format,
                annotation=annotation,
                exact_component_paths=exact_component_paths,
                used_annotation_indices=used_annotation_indices,
            )
            if component_row is None:
                continue
            component_rows.append(component_row)
            component_body_rows.extend(
                _compiled_component_body_rows_for_physical_document(
                    physical_row,
                    component_info,
                    component_row,
                    implemented_part_counts=implemented_part_counts_by_logical_id[
                        physical_row.logical_document_id
                    ],
                    naming_room=naming_room,
                    channel_designator_format=channel_designator_format,
                )
            )
            physical_component_ids.setdefault(physical_row.id, []).append(
                component_row.id
            )

    return (
        component_rows,
        component_body_rows,
        physical_component_ids,
        naming_rooms_by_physical_id,
    )


def _finalize_physical_tree_rows(
    physical_rows: Sequence[AltiumCompiledPhysicalDocument],
    component_rows: Sequence[AltiumCompiledComponent],
    physical_sheet_symbol_rows: Sequence[AltiumCompiledPhysicalSheetSymbol],
    sheet_symbols: Sequence[AltiumCompiledSheetSymbol],
    *,
    channel_designator_format: str,
    naming_rooms_by_physical_id: Mapping[str, RoomDetails],
    physical_child_ids: Mapping[str, Sequence[str]],
    physical_component_ids: Mapping[str, Sequence[str]],
    symbol_child_physical_ids: Mapping[str, Sequence[str]],
) -> tuple[
    tuple[AltiumCompiledPhysicalDocument, ...],
    tuple[AltiumCompiledSheetSymbol, ...],
    tuple[AltiumCompiledPhysicalSheetSymbol, ...],
]:
    named_physical_symbols = tuple(
        replace(
            symbol,
            physical_designator=apply_channel_pattern(
                channel_designator_format,
                naming_rooms_by_physical_id[symbol.owner_physical_document_id],
                symbol.logical_designator,
            ),
        )
        if symbol.owner_physical_document_id in naming_rooms_by_physical_id
        else symbol
        for symbol in physical_sheet_symbol_rows
    )
    final_component_ids = {component.id for component in component_rows}
    hierarchy_global_indices = _channel_global_indices(
        {row.id: row for row in physical_rows}
    )
    final_physical_rows = tuple(
        replace(
            row,
            child_ids=tuple(physical_child_ids.get(row.id, ())),
            component_ids=tuple(
                component_id
                for component_id in physical_component_ids.get(row.id, ())
                if component_id in final_component_ids
            ),
            _managed_hierarchy_global_index=hierarchy_global_indices.get(row.id),
        )
        for row in physical_rows
    )
    final_sheet_symbols = tuple(
        replace(
            symbol,
            child_physical_document_ids=tuple(
                symbol_child_physical_ids.get(symbol.id, ())
            ),
        )
        for symbol in sheet_symbols
    )
    return final_physical_rows, final_sheet_symbols, named_physical_symbols


def _build_physical_and_component_rows(
    schdocs: Sequence["AltiumSchDoc"],
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
    *,
    channel_designator_format: str,
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
    options: NetlistOptions,
    project_base_dir: Path | None,
    device_sheet_logical_ids: frozenset[str] = frozenset(),
    device_sheet_numbers_by_logical_id: Mapping[str, str] | None = None,
    document_order_by_logical_id: Mapping[str, int] | None = None,
) -> tuple[
    tuple[AltiumCompiledPhysicalDocument, ...],
    tuple[AltiumCompiledComponent, ...],
    tuple[AltiumCompiledComponent, ...],
    tuple[AltiumCompiledSheetSymbol, ...],
    tuple[AltiumCompiledPhysicalSheetSymbol, ...],
    tuple[AltiumCompileDiagnostic, ...],
]:
    physical_rows: list[AltiumCompiledPhysicalDocument] = []
    physical_sheet_symbol_rows: list[AltiumCompiledPhysicalSheetSymbol] = []
    physical_child_ids: dict[str, list[str]] = {}
    symbol_child_physical_ids: dict[str, list[str]] = defaultdict(list)
    diagnostics: list[AltiumCompileDiagnostic] = []
    flat_physical_tree = compile_options.effective_hierarchy_mode in {
        "FLAT",
        "GLOBAL",
    }

    logical_by_id = {document.id: document for document in logical_documents}
    schdoc_by_logical_id = _schdocs_by_logical_id(
        schdocs,
        project_base_dir=project_base_dir,
    )
    _require_source_schdocs(logical_documents, schdoc_by_logical_id)
    component_source_rows_by_logical_id = {
        logical_id: tuple(_compiler_component_sources(schdoc))
        for logical_id, schdoc in schdoc_by_logical_id.items()
    }
    implemented_part_counts_by_logical_id = {
        logical_id: _compiler_implemented_part_counts(
            schdoc.all_objects, component_source_rows_by_logical_id[logical_id]
        )
        for logical_id, schdoc in schdoc_by_logical_id.items()
    }
    sheet_parameters_by_logical_id = {
        logical_id: schdoc.get_parameter_dict()
        for logical_id, schdoc in schdoc_by_logical_id.items()
        if callable(getattr(schdoc, "get_parameter_dict", None))
    }
    cross_document_multipart_designators = _cross_document_multipart_designators(
        component_source_rows_by_logical_id
    )
    compile_mask_bounds_by_logical_id = {
        logical_id: _compiled_compile_mask_bounds(schdoc)
        for logical_id, schdoc in schdoc_by_logical_id.items()
    }
    symbols_by_logical_id: dict[str, list[AltiumCompiledSheetSymbol]] = defaultdict(
        list
    )
    for symbol in sheet_symbols:
        symbols_by_logical_id[symbol.logical_document_id].append(symbol)
    channel_rooms_by_symbol_child_id = _multi_reference_channel_rooms(sheet_symbols)
    repeat_channel_values = _repeat_channel_values(
        sheet_symbols,
        new_indexing=compile_options.new_indexing_of_sheet_symbols,
        parent_rank_by_logical_id={
            document.id: rank
            for rank, document in enumerate(
                sorted(
                    logical_documents,
                    key=cmp_to_key(_managed_document_name_compare),
                )
            )
        },
    )

    instantiation_state = _PhysicalInstantiationState(
        logical_by_id=logical_by_id,
        symbols_by_logical_id=symbols_by_logical_id,
        channel_rooms_by_symbol_child_id=channel_rooms_by_symbol_child_id,
        repeat_channel_values=repeat_channel_values,
        options=options,
        new_indexing_of_sheet_symbols=(compile_options.new_indexing_of_sheet_symbols),
        flat_physical_tree=flat_physical_tree,
        physical_rows=physical_rows,
        physical_sheet_symbol_rows=physical_sheet_symbol_rows,
        physical_child_ids=physical_child_ids,
        symbol_child_physical_ids=symbol_child_physical_ids,
        flat_physical_document_id_by_logical_id={},
        diagnostics=diagnostics,
    )

    top_logical_documents = [
        document for document in logical_documents if document.is_top_level_candidate
    ]
    if not flat_physical_tree and len(top_logical_documents) > 1:
        primary_top = _primary_top_level_document(logical_documents, sheet_symbols)
        top_logical_documents = [primary_top] if primary_top is not None else []
    if not top_logical_documents and logical_documents:
        top_logical_documents = [logical_documents[0]]

    for root_ordinal, logical_document in enumerate(top_logical_documents):
        root_name = Path(logical_document.file_name).stem or f"sheet{root_ordinal}"
        physical_id = _physical_document_id(logical_document.id, root_ordinal)
        _instantiate_physical_document(
            instantiation_state,
            logical_document=logical_document,
            physical_id=physical_id,
            physical_instance_path=root_name,
            physical_instance_unique_id="",
            channel_index=_no_channel_index(
                new_indexing=compile_options.new_indexing_of_sheet_symbols
            ),
            channel_prefix=None,
            channel_alpha=None,
            room_name=root_name,
            parent_id=None,
            parent_sheet_symbol_id=None,
            sheet_number=None,
            document_number=None,
            stack=(),
        )

    physical_rows_tuple = tuple(physical_rows)
    physical_rows_tuple = _apply_default_sheet_and_document_numbers(
        physical_rows_tuple,
        sheet_symbols=sheet_symbols,
        new_indexing_of_sheet_symbols=(compile_options.new_indexing_of_sheet_symbols),
        sheet_numbers_by_logical_id=_sheet_numbers_by_logical_id(
            schdocs,
            project_base_dir=project_base_dir,
            options=compile_options,
            logical_documents=logical_documents,
            sheet_symbols=sheet_symbols,
            device_sheet_logical_ids=device_sheet_logical_ids,
            device_sheet_numbers_by_logical_id=(device_sheet_numbers_by_logical_id),
            document_order_by_logical_id=document_order_by_logical_id,
        ),
        sheet_number_parameter_logical_ids=_logical_document_ids_with_parameter(
            schdocs,
            project_base_dir=project_base_dir,
            name="SheetNumber",
        ),
        document_numbers_by_logical_id=_document_numbers_by_logical_id(
            schdocs,
            project_base_dir=project_base_dir,
            project_parameters=options.project_parameters,
        ),
    )
    physical_rows_tuple, sheet_number_diagnostics = _apply_sheet_number_annotations(
        physical_rows_tuple,
        annotation,
    )
    diagnostics.extend(sheet_number_diagnostics)
    (
        component_rows,
        component_body_rows,
        physical_component_ids,
        naming_rooms_by_physical_id,
    ) = _build_compiled_component_rows(
        physical_rows_tuple,
        component_rows_by_logical_id=component_source_rows_by_logical_id,
        implemented_part_counts_by_logical_id=implemented_part_counts_by_logical_id,
        sheet_parameters_by_logical_id=sheet_parameters_by_logical_id,
        sheet_symbol_info_by_id=sheet_symbol_info_by_id,
        physical_sheet_symbol_rows=physical_sheet_symbol_rows,
        compile_mask_bounds_by_logical_id=compile_mask_bounds_by_logical_id,
        cross_document_multipart_designators=(cross_document_multipart_designators),
        compile_options=compile_options,
        options=options,
        annotation=annotation,
        channel_designator_format=channel_designator_format,
    )
    component_rows = _collapse_multipart_component_rows(component_rows)
    physical_component_ids = defaultdict(list)
    for component in component_rows:
        physical_component_ids[component.physical_document_id].append(component.id)
    physical_rows_final, sheet_symbol_rows, physical_sheet_symbol_rows_final = (
        _finalize_physical_tree_rows(
            physical_rows_tuple,
            component_rows,
            physical_sheet_symbol_rows,
            sheet_symbols,
            channel_designator_format=channel_designator_format,
            naming_rooms_by_physical_id=naming_rooms_by_physical_id,
            physical_child_ids=physical_child_ids,
            physical_component_ids=physical_component_ids,
            symbol_child_physical_ids=symbol_child_physical_ids,
        )
    )
    return (
        physical_rows_final,
        tuple(component_rows),
        tuple(component_body_rows),
        sheet_symbol_rows,
        physical_sheet_symbol_rows_final,
        tuple(diagnostics),
    )


def _compiled_source_object_priority(kind: str, options: NetlistOptions | None) -> int:
    options = options or NetlistOptions()
    return _managed_name_priority(kind, options)


def _represented_compiler_pins(
    local_netlist: object,
) -> Counter[int | tuple[str, str, str]]:
    result: Counter[int | tuple[str, str, str]] = Counter()
    for net in getattr(local_netlist, "nets", ()):
        for terminal in getattr(net, "terminals", ()):
            designator = str(getattr(terminal, "designator", "") or "").lower()
            pin = str(getattr(terminal, "pin", "") or "")
            pin_name = str(getattr(terminal, "pin_name", "") or "")
            if pin:
                source_id = int(getattr(terminal, "_source_pin_object_id", 0))
                result[source_id or (designator, pin, pin_name)] += 1
    return result


def _consume_represented_compiler_pin(
    pin: "SchPinInfo", represented: Counter[int | tuple[str, str, str]]
) -> bool:
    source_id = id(getattr(pin, "pin", pin))
    key = (
        source_id
        if source_id in represented
        else (
            str(
                getattr(pin, "component_designator", "")
                or getattr(pin.component, "designator", "")
                or ""
            ).lower(),
            pin.designator,
            pin.name,
        )
    )
    if represented[key] <= 0:
        return False
    represented[key] -= 1
    return True


def _compiled_component_single_pin_local_nets(
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    pin_roots_by_component: Mapping[tuple[str, str, str], Sequence["SchPinInfo"]],
    multipart_designators: set[str] | frozenset[str] | None = None,
    local_multipart_designators: set[str] | frozenset[str] | None = None,
    options: NetlistOptions | None = None,
) -> tuple[AltiumCompiledNet, ...]:
    represented_pins = _represented_compiler_pins(local_netlist)

    rows: list[AltiumCompiledNet] = []
    multipart = {
        dotnet_ordinal_ignore_case_key(designator)
        for designator in (
            *(multipart_designators or ()),
            *(local_multipart_designators or ()),
        )
    }
    for root_pins in pin_roots_by_component.values():
        for pin in root_pins:
            if _consume_represented_compiler_pin(pin, represented_pins):
                continue
            component = pin.component
            logical_designator = component.designator
            logical_designator_key = dotnet_ordinal_ignore_case_key(logical_designator)
            pin_number = pin.designator
            part_count = _component_record_subparts_count(component)
            component_part_id = _component_record_current_part_id(component)
            owner_part_id = int(
                getattr(pin.pin, "owner_part_id", None)
                or (1 if logical_designator_key in multipart else component_part_id)
                or 1
            )
            suffix = (
                _component_part_alpha_suffix(
                    part_count=part_count,
                    current_part_id=owner_part_id,
                )
                if logical_designator_key in multipart
                else ""
            )
            terminal_designator = f"{logical_designator}{suffix}"
            net_name = f"Net{logical_designator}_{pin_number}"
            net_id = _compiled_local_net_id(
                logical_document.id,
                net_name,
                first_net_index + len(rows),
            )
            terminal_id = f"{net_id}:terminal:0:{terminal_designator}:{pin_number}"
            terminal = AltiumCompiledNetTerminal(
                id=terminal_id,
                designator=terminal_designator,
                pin=pin_number,
                pin_name=str(getattr(pin, "name", "") or ""),
                pin_type=_pin_electrical_to_pintype(
                    getattr(pin, "electrical", None)
                ).name,
                _source_component_uid=str(
                    getattr(pin, "component_unique_id", "") or ""
                ),
                _source_pin_uid=str(getattr(pin, "unique_id", "") or ""),
                _source_pin_object_id=id(getattr(pin, "pin", pin)),
                _source_owner_part_id=owner_part_id,
            )
            rows.append(
                AltiumCompiledNet(
                    id=net_id,
                    name=net_name,
                    original_name=net_name,
                    scope="logical_local",
                    logical_document_id=logical_document.id,
                    auto_named=True,
                    single_pin=True,
                    terminal_ids=(terminal_id,),
                    terminals=(terminal,),
                    item_ids=(terminal_id,),
                    items=(_compiled_item_from_terminal(terminal_id, terminal),),
                    _name_source_kind="pin",
                    _name_source_priority=_compiled_source_object_priority(
                        "pin", options
                    ),
                    _name_source_raw_name=net_name,
                    _name_source_schematic_id=logical_document.id,
                    _name_source_full_name=net_name,
                    _name_source_autogenerated=False,
                )
            )
    return tuple(rows)


def _bus_range_suffix(name: str) -> str:
    start = name.find("[")
    end = name.rfind("]")
    if start == -1 or end <= start:
        return ""
    return name[start : end + 1]


def _local_terminal_full_designator(
    terminal: object,
    by_name: Mapping[tuple[str, str], str],
    by_source_pin: Mapping[int, str] | None,
) -> str:
    designator = str(getattr(terminal, "designator", "") or "")
    pin = str(getattr(terminal, "pin", "") or "")
    source_id = int(getattr(terminal, "_source_pin_object_id", 0))
    source_name = (by_source_pin or {}).get(source_id)
    return (
        source_name
        if source_name is not None
        else by_name.get((designator, pin), designator)
    )


def _compiled_terminal_from_net_terminal(
    terminal_id: str,
    terminal: object,
    terminal_full_designators_by_pin: Mapping[tuple[str, str], str],
    source_full_designators: Mapping[int, str] | None = None,
) -> AltiumCompiledNetTerminal:
    pin = str(getattr(terminal, "pin", "") or "")
    return AltiumCompiledNetTerminal(
        id=terminal_id,
        designator=_local_terminal_full_designator(
            terminal, terminal_full_designators_by_pin, source_full_designators
        ),
        pin=pin,
        pin_name=str(getattr(terminal, "pin_name", "") or ""),
        pin_type=str(
            getattr(getattr(terminal, "pin_type", None), "name", "") or "PASSIVE"
        ),
        _source_component_uid=str(getattr(terminal, "_source_component_uid", "") or ""),
        _source_pin_uid=str(getattr(terminal, "_source_pin_uid", "") or ""),
        _source_pin_object_id=int(
            getattr(terminal, "_source_pin_object_id", None) or 0
        ),
        _source_owner_part_id=int(
            getattr(terminal, "_source_owner_part_id", None) or 1
        ),
    )


def _compiled_local_terminal_rows(
    net_id: str,
    terminals: tuple[object, ...],
    terminal_full_designators_by_pin: Mapping[tuple[str, str], str],
    source_full_designators: Mapping[int, str] | None = None,
) -> tuple[tuple[str, ...], tuple[AltiumCompiledNetTerminal, ...]]:
    terminal_ids = tuple(
        (
            f"{net_id}:terminal:{terminal_index}:"
            f"{_local_terminal_full_designator(terminal, terminal_full_designators_by_pin, source_full_designators)}:"
            f"{getattr(terminal, 'pin', '')}"
        )
        for terminal_index, terminal in enumerate(terminals)
    )
    rows = tuple(
        _compiled_terminal_from_net_terminal(
            terminal_id,
            terminal,
            terminal_full_designators_by_pin,
            source_full_designators,
        )
        for terminal_id, terminal in zip(terminal_ids, terminals, strict=True)
    )
    return terminal_ids, rows


def _compiled_local_endpoint_rows(
    net_id: str,
    net_name: str,
    endpoints: tuple[object, ...],
    *,
    logical_document: AltiumCompiledLogicalDocument,
    net_label_by_id: Mapping[str, object],
    sheet_entry_parent_ids_by_element_id: Mapping[str, str],
    graphical_labels: Iterable[str],
) -> tuple[tuple[str, ...], tuple[AltiumCompiledNetEndpoint, ...]]:
    base_ids = tuple(
        f"{net_id}:endpoint:{endpoint_index}:{getattr(endpoint, 'endpoint_id', '')}"
        for endpoint_index, endpoint in enumerate(endpoints)
    )
    base_rows = _compiled_local_base_endpoint_rows(
        base_ids,
        endpoints,
        logical_document=logical_document,
        net_label_by_id=net_label_by_id,
        sheet_entry_parent_ids_by_element_id=sheet_entry_parent_ids_by_element_id,
    )
    missing_label_ids = _compiled_missing_graphical_label_ids(
        base_rows,
        graphical_labels,
    )
    label_ids = tuple(
        f"{net_id}:endpoint:{len(base_ids) + label_index}:net_label:{label_id}"
        for label_index, label_id in enumerate(missing_label_ids)
    )
    label_rows = tuple(
        _compiled_graphical_label_endpoint(
            endpoint_id,
            label_id,
            net_name=net_name,
            logical_document=logical_document,
            net_label_by_id=net_label_by_id,
        )
        for endpoint_id, label_id in zip(label_ids, missing_label_ids, strict=True)
    )
    return (*base_ids, *label_ids), (*base_rows, *label_rows)


def _compiled_local_base_endpoint_rows(
    base_ids: tuple[str, ...],
    endpoints: tuple[object, ...],
    *,
    logical_document: AltiumCompiledLogicalDocument,
    net_label_by_id: Mapping[str, object],
    sheet_entry_parent_ids_by_element_id: Mapping[str, str],
) -> tuple[AltiumCompiledNetEndpoint, ...]:
    rows = tuple(
        _compiled_endpoint_from_net_endpoint(
            endpoint_id,
            endpoint,
            parent_id=_compiled_endpoint_parent_id(
                endpoint,
                logical_document=logical_document,
                sheet_entry_parent_ids_by_element_id=(
                    sheet_entry_parent_ids_by_element_id
                ),
            ),
        )
        for endpoint_id, endpoint in zip(base_ids, endpoints, strict=True)
    )
    return tuple(
        _compiled_endpoint_with_net_label(endpoint, net_label_by_id)
        for endpoint in rows
    )


def _compiled_endpoint_with_net_label(
    endpoint: AltiumCompiledNetEndpoint,
    net_label_by_id: Mapping[str, object],
) -> AltiumCompiledNetEndpoint:
    if endpoint.role != "net_label" or endpoint.element_id not in net_label_by_id:
        return endpoint
    label = net_label_by_id[endpoint.element_id]
    connection_point = endpoint.connection_point
    if connection_point is None:
        connection_point = getattr(label, "connection_point", None)
    return replace(
        endpoint,
        name=str(getattr(label, "text", "") or endpoint.name),
        connection_point=connection_point,
    )


def _compiled_missing_graphical_label_ids(
    base_rows: tuple[AltiumCompiledNetEndpoint, ...],
    graphical_labels: Iterable[str],
) -> tuple[str, ...]:
    represented = {
        endpoint.element_id
        for endpoint in base_rows
        if endpoint.role == "net_label" and endpoint.element_id
    }
    return tuple(
        label_id
        for label_id in graphical_labels
        if label_id and label_id not in represented
    )


def _compiled_graphical_label_endpoint(
    endpoint_id: str,
    label_id: str,
    *,
    net_name: str,
    logical_document: AltiumCompiledLogicalDocument,
    net_label_by_id: Mapping[str, object],
) -> AltiumCompiledNetEndpoint:
    label = net_label_by_id.get(label_id)
    return AltiumCompiledNetEndpoint(
        id=endpoint_id,
        role="net_label",
        element_id=label_id,
        object_id=label_id,
        name=str(getattr(label, "text", "") or net_name),
        parent_id=logical_document.file_name,
        connection_point=getattr(label, "connection_point", None),
    )


def _compiled_local_net_row(
    net: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    net_index: int,
    net_label_by_id: Mapping[str, object],
    sheet_entry_parent_ids_by_element_id: dict[str, str],
    terminal_full_designators_by_pin: dict[tuple[str, str], str],
    linked_sheet_entry_ids: frozenset[str] = frozenset(),
    source_full_designators: Mapping[int, str] | None = None,
) -> AltiumCompiledNet:
    net_name = str(getattr(net, "name", "") or "")
    net_terminals = tuple(getattr(net, "terminals", ()) or ())
    net_endpoints = tuple(getattr(net, "endpoints", ()) or ())
    net_id = _compiled_local_net_id(
        logical_document.id,
        net_name,
        net_index,
    )
    terminal_ids, terminals = _compiled_local_terminal_rows(
        net_id,
        net_terminals,
        terminal_full_designators_by_pin,
        source_full_designators,
    )
    endpoint_ids, endpoints = _compiled_local_endpoint_rows(
        net_id,
        net_name,
        net_endpoints,
        logical_document=logical_document,
        net_label_by_id=net_label_by_id,
        sheet_entry_parent_ids_by_element_id=sheet_entry_parent_ids_by_element_id,
        graphical_labels=getattr(getattr(net, "graphical", None), "labels", ()),
    )
    item_ids, items = _compiled_local_net_items(
        net,
        net_id=net_id,
        terminal_ids=terminal_ids,
        terminals=net_terminals,
        endpoint_ids=endpoint_ids,
        endpoints=endpoints,
        linked_sheet_entry_ids=linked_sheet_entry_ids,
    )
    return AltiumCompiledNet(
        id=net_id,
        name=net_name,
        original_name=net_name,
        scope="logical_local",
        logical_document_id=logical_document.id,
        auto_named=bool(getattr(net, "auto_named", False)),
        single_pin=len(net_terminals) == 1,
        aliases=tuple(getattr(net, "aliases", ()) or ()),
        terminal_ids=terminal_ids,
        terminals=terminals,
        endpoint_ids=endpoint_ids,
        endpoints=endpoints,
        item_ids=item_ids,
        items=items,
        _name_source_kind=str(getattr(net, "_name_source_kind", "") or ""),
        _name_source_name=(
            net_name
            if str(getattr(net, "_name_source_kind", "") or "")
            in {"power_port", "hidden_pin"}
            else ""
        ),
        _name_source_bus_prefix=str(getattr(net, "_name_source_bus_prefix", "") or ""),
        _name_source_bus_suffix=str(getattr(net, "_name_source_bus_suffix", "") or ""),
        _name_source_priority=int(getattr(net, "_name_source_priority", 0) or 0),
        _name_source_raw_name=str(
            getattr(net, "_name_source_raw_name", "") or net_name
        ),
        _name_source_is_bus=bool(getattr(net, "_name_source_is_bus", False)),
        _name_source_schematic_id=logical_document.id,
        _name_source_full_name=net_name,
        _name_source_autogenerated=(
            bool(getattr(net, "auto_named", False))
            and str(getattr(net, "_name_source_kind", "") or "") != "pin"
        ),
        _bus_signal_width=int(getattr(net, "_bus_signal_width", 0) or 0),
        _bus_signal_offset=int(getattr(net, "_bus_signal_offset", 0) or 0),
        _source_connection_link_id=str(
            getattr(net, "_source_connection_link_id", "") or ""
        ),
        _contains_bus=bool(getattr(net, "_contains_bus", False)),
        _port_bus_member_names=tuple(
            str(name) for name in getattr(net, "_port_bus_member_names", ()) if name
        ),
        _scalar_merge_name_sources=tuple(
            (str(kind), str(name))
            for kind, name in getattr(net, "_scalar_merge_name_sources", ())
            if kind and name
        ),
        _signal_harness_name_candidates=tuple(
            replace(candidate, source_schematic_id=logical_document.id)
            for candidate in cast(
                Iterable[_SignalHarnessNameCandidate],
                getattr(net, "_signal_harness_name_candidates", ()),
            )
        ),
        _managed_item_count=int(getattr(net, "_managed_item_count", 0) or 0),
        _managed_endpoint_count=len(endpoints),
        _managed_removed_item_count=int(
            getattr(net, "_managed_removed_item_count", 0) or 0
        ),
    )


def _compiled_local_net_items(
    net: object,
    *,
    net_id: str,
    terminal_ids: Sequence[str],
    terminals: Sequence[object],
    endpoint_ids: Sequence[str],
    endpoints: Sequence[AltiumCompiledNetEndpoint],
    linked_sheet_entry_ids: frozenset[str],
) -> tuple[tuple[str, ...], tuple[AltiumCompiledNetItem, ...]]:
    endpoint_item_keys = {
        (
            "power_port" if endpoint.role == "harness_power" else endpoint.role,
            endpoint.element_id,
        )
        for endpoint in endpoints
        if endpoint.element_id
    }
    graphical_items = tuple(
        item
        for item in _compiled_items_from_graphical(
            net_id,
            getattr(net, "graphical", None),
        )
        if (item.kind, item.element_id) not in endpoint_item_keys
    )
    item_ids = (
        *terminal_ids,
        *endpoint_ids,
        *(item.id for item in graphical_items),
    )
    items = (
        *(
            _compiled_item_from_terminal(terminal_id, terminal)
            for terminal_id, terminal in zip(
                terminal_ids,
                terminals,
                strict=True,
            )
        ),
        *(
            _compiled_item_from_local_endpoint(
                endpoint,
                linked_sheet_entry_ids=linked_sheet_entry_ids,
            )
            for endpoint in endpoints
        ),
        *graphical_items,
    )
    return tuple(item_ids), tuple(items)


def _initial_harness_endpoint_stats() -> dict[str, int]:
    return {
        "harness_entry_candidate_count": 0,
        "harness_entry_matched_count": 0,
        "harness_entry_nearby_label_fallback_count": 0,
        "effective_scope_bridge_document_count": 0,
        "harness_wire_synthetic_net_count": 0,
        "typed_harness_member_synthetic_net_count": 0,
        "sheet_entry_wire_synthetic_net_count": 0,
        "isolated_power_port_synthetic_net_count": 0,
    }


def _top_level_port_name_counts_for_local_connectivity(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    schdoc_by_logical_id: dict[str, "AltiumSchDoc"],
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for logical_document in logical_documents:
        if logical_document.parent_sheet_symbol_ids:
            continue
        schdoc = schdoc_by_logical_id[logical_document.id]
        compile_masks = _compiled_compile_mask_bounds(schdoc)
        for port in schdoc.get_ports():
            if _port_is_compile_masked(port, compile_masks):
                continue
            port_name = str(getattr(port, "name", "") or "")
            if port_name:
                counts[_compiled_case_insensitive_key(port_name)] += 1
    return counts


def _compiled_pin_location(pin: object) -> tuple[int, int]:
    location = getattr(pin, "connection_point", None)
    if location is None:
        location = getattr(pin, "location", None)
    if isinstance(location, tuple) and len(location) >= 2:
        return (int(location[0]), int(location[1]))
    if (
        location is not None
        and not isinstance(location, tuple)
        and hasattr(location, "x")
        and hasattr(location, "y")
    ):
        return (int(location.x), int(location.y))
    return (0, 0)


@dataclass(frozen=True, slots=True)
class _CompiledPinRootBucket:
    occurrence_key: str
    designator: str
    roots: tuple["SchPinInfo", ...]


def _compiled_pin_root_index(
    pin_roots_by_component: Mapping[tuple[str, str, str], Sequence["SchPinInfo"]],
) -> dict[int, list[_CompiledPinRootBucket]]:
    result: dict[int, list[_CompiledPinRootBucket]] = defaultdict(list)
    for (occurrence_key, designator, _), roots in pin_roots_by_component.items():
        by_record: dict[int, list["SchPinInfo"]] = defaultdict(list)
        for pin in roots:
            record = getattr(getattr(pin, "component", None), "record", None)
            by_record[id(record)].append(pin)
        for record_id, record_roots in by_record.items():
            # Separate buckets retain their own filtered root-index numbering.
            result[record_id].append(
                _CompiledPinRootBucket(occurrence_key, designator, tuple(record_roots))
            )
    return result


def _compiled_pin_roots_for_component(
    component: "SchComponentInfo",
    source_component_uid: str,
    component_designator: str,
    occurrence_key: str,
    designator: str,
    roots: Sequence["SchPinInfo"],
) -> tuple["SchPinInfo", ...]:
    if source_component_uid and occurrence_key != source_component_uid:
        return ()
    if not source_component_uid and designator != component_designator.lower():
        return ()
    component_record = getattr(component, "record", None)
    return tuple(
        pin
        for pin in roots
        if getattr(getattr(pin, "component", None), "record", None) is component_record
    )


def _compiled_active_pin_signal_id(
    pin: "SchPinInfo",
    logical_document_id: str,
    component_index: int,
    root_index: int,
) -> str | None:
    if pin_is_runtime_hidden(pin, getattr(pin, "component", None)) and not str(
        _compiler_hidden_net_name(pin) or ""
    ):
        return None
    return f"{logical_document_id}:{component_index}:{pin.designator}:{root_index}"


def _compiled_active_pin_row(
    component: "SchComponentInfo",
    pin: "SchPinInfo",
    *,
    logical_document: AltiumCompiledLogicalDocument,
    component_index: int,
    root_index: int,
    source_component_uid: str,
    source_pins: tuple[object, ...],
    raw_source_indices: Mapping[int, int],
    active_index: int,
) -> _CompiledManagedPinRow:
    local_signal_id = _compiled_active_pin_signal_id(
        pin,
        logical_document.id,
        component_index,
        root_index,
    )
    source_pin_object = getattr(pin, "pin", pin)
    return _CompiledManagedPinRow(
        physical_part_designator="",
        designator=str(pin.designator or ""),
        component_part_id=_component_record_current_part_id(component),
        inferred=False,
        local_signal_id=local_signal_id,
        global_signal_id=local_signal_id,
        owner_document_name=logical_document.file_name,
        owner_physical_room_name="",
        location=_compiled_pin_location(pin),
        source_component_uid=source_component_uid,
        source_pin_uid=str(getattr(pin, "unique_id", "") or ""),
        source_pin_object_id=id(source_pin_object),
        source_index=raw_source_indices.get(
            id(source_pin_object),
            len(source_pins) + active_index,
        ),
    )


def _compiled_active_pin_rows(
    component: "SchComponentInfo",
    logical_document: AltiumCompiledLogicalDocument,
    component_index: int,
    source_pins: tuple[object, ...],
    pin_roots_by_component: Mapping[
        tuple[str, str, str],
        Sequence["SchPinInfo"],
    ],
    *,
    pin_roots_by_record: Mapping[int, Sequence[_CompiledPinRootBucket]] | None = None,
) -> tuple[_CompiledManagedPinRow, ...]:
    if pin_roots_by_record is None:
        pin_roots_by_record = _compiled_pin_root_index(pin_roots_by_component)
    source_component_uid = str(getattr(component, "unique_id", "") or "")
    component_designator = str(getattr(component, "designator", "") or "")
    raw_source_indices = {id(pin): index for index, pin in enumerate(source_pins)}
    active_pins: list[_CompiledManagedPinRow] = []
    for bucket in pin_roots_by_record.get(id(getattr(component, "record", None)), ()):
        component_roots = _compiled_pin_roots_for_component(
            component,
            source_component_uid,
            component_designator,
            bucket.occurrence_key,
            bucket.designator,
            bucket.roots,
        )
        if not component_roots:
            continue
        for root_index, pin in enumerate(component_roots):
            active_pins.append(
                _compiled_active_pin_row(
                    component,
                    pin,
                    logical_document=logical_document,
                    component_index=component_index,
                    root_index=root_index,
                    source_component_uid=source_component_uid,
                    source_pins=source_pins,
                    raw_source_indices=raw_source_indices,
                    active_index=len(active_pins),
                )
            )
    return tuple(sorted(active_pins, key=lambda pin: pin.source_index))


def _compiled_raw_pin_rows(
    source_pins: Sequence[object],
) -> tuple[_CompiledRawPinRow, ...]:
    return tuple(
        _CompiledRawPinRow(
            designator=str(getattr(pin, "designator", "") or ""),
            owner_part_id=_compiled_raw_pin_owner_part_id(pin),
            owner_part_display_mode=int(
                getattr(pin, "owner_part_display_mode", 0) or 0
            ),
            location=_compiled_pin_location(pin),
            source_pin_uid=str(getattr(pin, "unique_id", "") or ""),
            source_index=raw_index,
        )
        for raw_index, pin in enumerate(source_pins)
    )


def _compiled_raw_pin_owner_part_id(pin: object) -> int:
    return _pin_owner_part_id_for_component_view(pin)


def _compiled_component_pin_evidence(
    schdoc: "AltiumSchDoc",
    logical_document: AltiumCompiledLogicalDocument,
    pin_roots_by_component: Mapping[
        tuple[str, str, str],
        Sequence["SchPinInfo"],
    ],
) -> dict[int, _CompiledComponentPinEvidence]:
    result: dict[int, _CompiledComponentPinEvidence] = {}
    pin_roots_by_record = _compiled_pin_root_index(pin_roots_by_component)
    owner_document_name = logical_document.file_name
    for component_index, component in _compiler_source_rows(
        _compiler_component_sources(schdoc)
    ):
        source_component_uid = str(getattr(component, "unique_id", "") or "")
        record = getattr(component, "record", None)
        source_pins = tuple(getattr(record, "pins", ()) or ())
        result[component_index] = _CompiledComponentPinEvidence(
            logical_designator=str(getattr(component, "designator", "") or ""),
            current_part_id=_component_record_current_part_id(component),
            subparts_count=_component_record_subparts_count(component),
            display_mode=int(getattr(component, "display_mode", 0) or 0),
            display_mode_count=int(getattr(record, "display_mode_count", 0) or 0),
            owner_document_name=owner_document_name,
            source_component_uid=source_component_uid,
            active_pins=_compiled_active_pin_rows(
                component,
                logical_document,
                component_index,
                source_pins,
                pin_roots_by_component,
                pin_roots_by_record=pin_roots_by_record,
            ),
            raw_pins=_compiled_raw_pin_rows(source_pins),
        )
    return result


def _project_multipart_designators(
    schdocs: Sequence["AltiumSchDoc"],
) -> set[str]:
    placed_part_ids_by_designator: dict[str, set[int]] = defaultdict(set)
    for schdoc in schdocs:
        for component in _compiler_component_sources(schdoc):
            logical_designator = str(component.designator or "")
            if _designator_binds_multipart_records(
                logical_designator
            ) and _component_record_is_multipart(component):
                placed_part_ids_by_designator[
                    dotnet_ordinal_ignore_case_key(logical_designator)
                ].add(_component_record_current_part_id(component))
    return {
        designator
        for designator, part_ids in placed_part_ids_by_designator.items()
        if any(part_id > 1 for part_id in part_ids)
    }


def _compiled_direct_port_object_id(endpoint: object) -> str | None:
    if str(getattr(endpoint, "role", "") or "").lower() != "port":
        return None
    object_id = str(getattr(endpoint, "object_id", "") or "")
    if object_id:
        return object_id
    endpoint_id = str(getattr(endpoint, "endpoint_id", "") or "")
    return endpoint_id.rsplit(":", 1)[-1] if endpoint_id else None


def _compiled_direct_port_net_evidence(
    local_netlist: object,
) -> tuple[set[str], set[str]]:
    direct_port_net_has_terminals: set[str] = set()
    direct_port_net_ids: set[str] = set()
    for net in getattr(local_netlist, "nets", ()):
        for endpoint in getattr(net, "endpoints", ()):
            object_id = _compiled_direct_port_object_id(endpoint)
            if object_id is None:
                continue
            direct_port_net_ids.add(object_id)
            if getattr(net, "terminals", ()):
                direct_port_net_has_terminals.add(object_id)
    return direct_port_net_ids, direct_port_net_has_terminals


def _compiled_standalone_bus_port_identity(
    port_name: str,
    port_id: str,
    port_index: int,
    direct_port_net_ids: set[str],
    top_level_port_name_counts: Mapping[str, int],
) -> tuple[str, str, bool, tuple[str, ...]] | None:
    if port_id and port_id in direct_port_net_ids:
        return None
    same_name_count = top_level_port_name_counts.get(
        _compiled_case_insensitive_key(port_name),
        0,
    )
    if same_name_count > 1:
        return port_name, port_id, False, ()
    bus_suffix = _bus_range_suffix(port_name)
    net_name = f"N000-{port_index}_{bus_suffix}" if bus_suffix else f"N000-{port_index}"
    return _compiled_auto_port_net_identity(net_name, port_name, port_id)


def _compiled_auto_port_net_identity(
    net_name: str,
    port_name: str,
    port_id: str,
) -> tuple[str, str, bool, tuple[str, ...]]:
    aliases = (port_name,) if port_name != net_name else ()
    return net_name, port_id, True, aliases


def _compiled_standalone_port_net_identity(
    port: "SchPortInfo",
    port_index: int,
    direct_port_net_ids: set[str],
    direct_port_net_has_terminals: set[str],
    top_level_port_name_counts: Mapping[str, int],
    *,
    force_scalar_carrier: bool = False,
) -> tuple[str, str, bool, tuple[str, ...]] | None:
    port_name = str(getattr(port, "name", "") or "")
    port_id = str(getattr(port, "unique_id", "") or "")
    if not port_name or (port_id and port_id in direct_port_net_has_terminals):
        return None

    if _parse_bus_range(port_name):
        return _compiled_standalone_bus_port_identity(
            port_name,
            port_id,
            port_index,
            direct_port_net_ids,
            top_level_port_name_counts,
        )
    if not port_id or (port_id not in direct_port_net_ids and not force_scalar_carrier):
        return None
    return _compiled_auto_port_net_identity(
        f"N000-{port_index}",
        port_name,
        port_id,
    )


def _compiled_standalone_port_connector_local_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    top_level_port_name_counts: dict[str, int],
    options: NetlistOptions | None = None,
) -> tuple[AltiumCompiledNet, ...]:
    if logical_document.parent_sheet_symbol_ids:
        return ()

    direct_port_net_ids, direct_port_net_has_terminals = (
        _compiled_direct_port_net_evidence(local_netlist)
    )
    scalar_port_occurrences = _scalar_port_occurrence_ids_for_wire_network(schdoc)
    harness_carried_scalar_ports = {
        endpoint.source_occurrence_id
        for _wire_id, endpoints in _harness_wire_endpoint_groups(schdoc, local_netlist)
        if any(endpoint.role == "harness_entry" for endpoint in endpoints)
        for endpoint in endpoints
        if endpoint.role == "port"
        and endpoint.source_occurrence_id in scalar_port_occurrences
    }

    rows: list[AltiumCompiledNet] = []
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    for port_index, port in _compiler_source_rows(schdoc.get_ports(), start=1):
        if _port_is_compile_masked(port, compile_masks):
            continue
        identity = _compiled_standalone_port_net_identity(
            port,
            port_index,
            direct_port_net_ids,
            direct_port_net_has_terminals,
            top_level_port_name_counts,
            force_scalar_carrier=(
                f"port:{port_index - 1}" in harness_carried_scalar_ports
            ),
        )
        if identity is None:
            continue
        net_name, port_id, auto_named, aliases = identity
        port_name = str(getattr(port, "name", "") or "")

        net_id = _compiled_local_net_id(
            logical_document.id,
            net_name,
            first_net_index + len(rows),
        )
        endpoint = AltiumCompiledNetEndpoint(
            id=f"{net_id}:endpoint:0:port:{port_id or port_name}",
            role="port",
            element_id=port_id,
            object_id=port_id,
            name=port_name,
            parent_id=logical_document.file_name,
            connection_point=getattr(port, "connection_point", None),
            _source_occurrence_id=f"port:{port_index - 1}",
        )
        item = _compiled_item_from_endpoint(endpoint)
        rows.append(
            AltiumCompiledNet(
                id=net_id,
                name=net_name,
                original_name=net_name,
                scope="logical_local",
                logical_document_id=logical_document.id,
                auto_named=auto_named,
                single_pin=False,
                aliases=aliases,
                endpoint_ids=(endpoint.id,),
                endpoints=(endpoint,),
                item_ids=(item.id,),
                items=(item,),
                _name_source_kind="port",
                _name_source_priority=_compiled_source_object_priority("port", options),
                _name_source_raw_name=port_name,
                _name_source_schematic_id=logical_document.id,
                _name_source_full_name=net_name,
                _name_source_autogenerated=auto_named,
            )
        )
    return tuple(rows)


def _compiled_isolated_power_port_local_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    options: NetlistOptions | None = None,
) -> tuple[AltiumCompiledNet, ...]:
    groups = _compiled_isolated_power_port_groups(schdoc, local_netlist)
    return tuple(
        _compiled_isolated_power_net(
            power_name,
            power_ports,
            logical_document=logical_document,
            net_index=first_net_index + group_index,
            options=options,
        )
        for group_index, (power_name, power_ports) in enumerate(groups)
    )


def _compiled_isolated_power_port_groups(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
) -> tuple[tuple[str, tuple["SchPowerPortInfo", ...]], ...]:
    represented_power_port_ids = _compiled_represented_power_port_ids(local_netlist)
    power_ports_by_name: dict[str, tuple[str, list["SchPowerPortInfo"]]] = {}
    signal_harnesses = tuple(schdoc.get_signal_harnesses())
    connector_primary_points = tuple(
        _harness_connector_master_entry_point(connector.record)
        for connector in schdoc.get_harness_connectors()
    )
    internal_tolerance = altium_internal_tolerance_for_display_unit(
        int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0) or 0)
    )
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    for power_port in schdoc.get_power_ports():
        identity = _compiled_isolated_power_port_identity(
            power_port,
            represented_power_port_ids,
            compile_masks,
        )
        if identity is None:
            continue
        _power_port_id, power_name = identity
        if (
            analyser_net_item_kind(
                power_name,
                locations=(power_port._precise_connection_point,),
                signal_harnesses=signal_harnesses,
                harness_connector_primary_points=connector_primary_points,
                internal_tolerance=internal_tolerance,
            )
            is AnalyserNetItemKind.HARNESS
        ):
            continue
        power_key = _compiled_case_insensitive_key(power_name)
        _preserved_name, rows = power_ports_by_name.setdefault(
            power_key,
            (power_name, []),
        )
        rows.append(power_port)
    return tuple(
        (power_name, tuple(power_ports))
        for power_name, power_ports in sorted(
            power_ports_by_name.values(),
            key=lambda item: dotnet_ordinal_ignore_case_sort_key(item[0]),
        )
    )


def _compiled_represented_power_port_ids(local_netlist: object) -> set[str]:
    return {
        str(getattr(endpoint, "element_id", "") or "")
        for net in getattr(local_netlist, "nets", ())
        for endpoint in getattr(net, "endpoints", ())
        if getattr(endpoint, "role", "") == "power_port"
    }


def _compiled_isolated_power_port_identity(
    power_port: "SchPowerPortInfo",
    represented_power_port_ids: set[str],
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> tuple[str, str] | None:
    power_port_id = str(getattr(power_port, "unique_id", "") or "")
    power_name = str(getattr(power_port, "text", "") or "").strip()
    if not power_port_id or not power_name:
        return None
    if power_port_id in represented_power_port_ids:
        return None
    if bool(getattr(getattr(power_port, "record", None), "is_not_accessible", False)):
        return None
    if _compiled_point_inside_any_mask(
        power_port._precise_connection_point,
        compile_masks,
    ):
        return None
    return power_port_id, power_name


def _compiled_point_inside_any_mask(
    point: RootPoint,
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> bool:
    point_x = point[0] * 100_000 + point[2]
    point_y = point[1] * 100_000 + point[3]
    return any(
        min_x < point_x < max_x and min_y < point_y < max_y
        for min_x, min_y, max_x, max_y in compile_masks
    )


def _compiled_mask_contains_every_point(
    compile_mask: tuple[int, int, int, int],
    points: Sequence[RootPoint],
) -> bool:
    min_x, min_y, max_x, max_y = compile_mask
    return all(
        min_x < point[0] * ALTIUM_COORD_SCALE + point[2] < max_x
        and min_y < point[1] * ALTIUM_COORD_SCALE + point[3] < max_y
        for point in points
    )


def _compiled_isolated_power_net(
    power_name: str,
    power_ports: Sequence["SchPowerPortInfo"],
    *,
    logical_document: AltiumCompiledLogicalDocument,
    net_index: int,
    options: NetlistOptions | None = None,
) -> AltiumCompiledNet:
    net_id = _compiled_local_net_id(logical_document.id, power_name, net_index)
    endpoints = tuple(
        AltiumCompiledNetEndpoint(
            id=f"{net_id}:endpoint:{endpoint_index}:power_port:{power_port.unique_id}",
            role="power_port",
            element_id=power_port.unique_id,
            object_id=power_port.unique_id,
            name=str(power_port.text or "").strip(),
            parent_id=logical_document.file_name,
            connection_point=power_port.connection_point,
        )
        for endpoint_index, power_port in enumerate(power_ports)
    )
    items = tuple(_compiled_item_from_endpoint(endpoint) for endpoint in endpoints)
    return AltiumCompiledNet(
        id=net_id,
        name=power_name,
        original_name=power_name,
        scope="logical_local",
        logical_document_id=logical_document.id,
        auto_named=False,
        single_pin=False,
        endpoint_ids=tuple(endpoint.id for endpoint in endpoints),
        endpoints=endpoints,
        item_ids=tuple(item.id for item in items),
        items=items,
        _name_source_kind="power_port",
        _name_source_name=power_name,
        _name_source_priority=_compiled_source_object_priority("power_port", options),
        _name_source_raw_name=power_name,
        _name_source_schematic_id=logical_document.id,
        _name_source_full_name=power_name,
    )


def _physical_harness_occurrence_context(
    physical_documents: Sequence[AltiumCompiledPhysicalDocument],
    sheet_symbols: Sequence[AltiumCompiledSheetSymbol],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
) -> tuple[
    dict[str, dict[str, tuple[str, ...]]],
    dict[tuple[str, str], tuple[int, ...]],
]:
    physical_by_id = {document.id: document for document in physical_documents}
    symbol_by_id = {symbol.id: symbol for symbol in sheet_symbols}
    inferred: defaultdict[str, defaultdict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    repeats: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    for child in physical_documents:
        if child.parent_id is None or child.parent_sheet_symbol_id is None:
            continue
        parent = physical_by_id.get(child.parent_id)
        symbol = symbol_by_id.get(child.parent_sheet_symbol_id)
        symbol_info = sheet_symbol_info_by_id.get(child.parent_sheet_symbol_id)
        if parent is None or symbol is None or symbol_info is None:
            continue
        parent_schdoc = schdoc_by_logical_id.get(parent.logical_document_id)
        if parent_schdoc is None:
            continue
        _append_physical_harness_occurrence_context(
            inferred,
            repeats,
            child=child,
            parent=parent,
            symbol=symbol,
            symbol_info=symbol_info,
            parent_schdoc=parent_schdoc,
        )
    return (
        {
            document_id: {name: tuple(types) for name, types in rows.items()}
            for document_id, rows in inferred.items()
        },
        {key: tuple(values) for key, values in repeats.items()},
    )


def _append_physical_harness_occurrence_context(
    inferred: defaultdict[str, defaultdict[str, list[str]]],
    repeats: defaultdict[tuple[str, str], list[int]],
    *,
    child: AltiumCompiledPhysicalDocument,
    parent: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    symbol_info: "SchSheetSymbolInfo",
    parent_schdoc: "AltiumSchDoc",
) -> None:
    symbol_index = next(
        (
            index
            for index, candidate in _compiler_source_rows(
                _compiler_sheet_symbol_sources(parent_schdoc)
            )
            if candidate.record is symbol_info.record
        ),
        None,
    )
    if symbol_index is None:
        return
    compile_masks = _compiled_compile_mask_bounds(parent_schdoc)
    for entry_index, entry in _compiler_entry_rows(symbol_info, symbol_info.entries):
        type_name = str(getattr(entry, "harness_type", "") or "")
        if not type_name or _sheet_entry_is_compile_masked(
            entry, symbol_info, compile_masks
        ):
            continue
        entry_name = _inter_sheet_entry_display_name(entry)
        port_name = _parse_entry_repeat(entry_name) if symbol.is_repeat else None
        port_name = port_name or entry_name
        types = inferred[child.id][dotnet_ordinal_ignore_case_key(port_name)]
        if type_name not in types:
            types.append(type_name)
        repeat_value = child._managed_repeat_channel_value
        if (
            symbol.is_repeat
            and _parse_entry_repeat(entry_name)
            and repeat_value is not None
        ):
            occurrence_id = f"sheet_symbol:{symbol_index}:entry:{entry_index}"
            values = repeats[(parent.id, occurrence_id)]
            if repeat_value not in values:
                values.append(repeat_value)


def _compiled_compile_mask_bounds(
    schdoc: object,
) -> tuple[tuple[int, int, int, int], ...]:
    collector = getattr(schdoc, "_collect_compile_mask_precise_bounds", None)
    if not callable(collector):
        return ()
    return tuple(cast(Iterable[tuple[int, int, int, int]], collector()))


def _compiled_managed_local_net_compare(
    left: AltiumCompiledNet,
    right: AltiumCompiledNet,
) -> int:
    precedence = (
        managed_alpha_numeric_compare(left.name, right.name),
        len(right.terminals) - len(left.terminals),
        _compiled_managed_local_item_count(right)
        - _compiled_managed_local_item_count(left),
        _compiled_managed_local_removed_count(right)
        - _compiled_managed_local_removed_count(left),
    )
    for order in precedence:
        if order:
            return order
    return _compiled_managed_local_net_tiebreak(left, right)


def _compiled_managed_local_net_tiebreak(
    left: AltiumCompiledNet,
    right: AltiumCompiledNet,
) -> int:
    left_item = _compiled_first_managed_local_item(left)
    right_item = _compiled_first_managed_local_item(right)
    if left_item is None or right_item is None:
        return 0
    owner_order = managed_alpha_numeric_compare(
        left.logical_document_id or "",
        right.logical_document_id or "",
    )
    if owner_order:
        return owner_order
    left_location = left_item.connection_point or (-9_999, -9_999)
    right_location = right_item.connection_point or (-9_999, -9_999)
    return (left_location > right_location) - (left_location < right_location)


def _compiled_managed_local_item_count(net: AltiumCompiledNet) -> int:
    return net._managed_item_count + max(
        0,
        len(net.endpoints) - net._managed_endpoint_count,
    )


def _compiled_managed_local_removed_count(net: AltiumCompiledNet) -> int:
    return net._managed_removed_item_count + sum(item.removed for item in net.items)


def _compiled_first_managed_local_item(
    net: AltiumCompiledNet,
) -> AltiumCompiledNetEndpoint | None:
    role_rank = {
        "pin": 0,
        "net_label": 1,
        "offsheet_connector": 2,
        "port": 3,
        "power_port": 4,
        "sheet_entry": 5,
    }
    return min(
        net.endpoints,
        key=lambda endpoint: role_rank.get(endpoint.role, 6),
        default=None,
    )


def _rewrite_compiled_local_net_identity(
    net: AltiumCompiledNet,
    new_id: str,
) -> AltiumCompiledNet:
    old_id = net.id

    def rewrite(value: str) -> str:
        return f"{new_id}{value[len(old_id) :]}" if value.startswith(old_id) else value

    terminals = tuple(replace(row, id=rewrite(row.id)) for row in net.terminals)
    endpoints = tuple(replace(row, id=rewrite(row.id)) for row in net.endpoints)
    items = tuple(replace(row, id=rewrite(row.id)) for row in net.items)
    return replace(
        net,
        id=new_id,
        terminal_ids=tuple(row.id for row in terminals),
        terminals=terminals,
        endpoint_ids=tuple(row.id for row in endpoints),
        endpoints=endpoints,
        item_ids=tuple(row.id for row in items),
        items=items,
        source_net_ids=tuple(rewrite(value) for value in net.source_net_ids),
        parent_net_id=rewrite(net.parent_net_id),
        child_net_id=rewrite(net.child_net_id),
        link_ids=tuple(rewrite(value) for value in net.link_ids),
    )


def _compile_local_connectivity(
    schdocs: Sequence["AltiumSchDoc"],
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    *,
    options: NetlistOptions,
    effective_hierarchy_mode: str,
    project_base_dir: Path | None,
) -> tuple[
    tuple[AltiumCompiledNet, ...],
    dict[str, int],
    dict[str, int],
    dict[str, dict[int, _CompiledComponentPinEvidence]],
    tuple[AltiumCompileDiagnostic, ...],
]:
    net_rows: list[AltiumCompiledNet] = []
    local_net_counts: dict[str, int] = {}
    harness_endpoint_stats = _initial_harness_endpoint_stats()
    component_pin_evidence: dict[
        str,
        dict[int, _CompiledComponentPinEvidence],
    ] = {}
    diagnostics: list[AltiumCompileDiagnostic] = []
    schdoc_by_logical_id = _schdocs_by_logical_id(
        schdocs,
        project_base_dir=project_base_dir,
    )
    schdoc_by_source_ref, schdoc_by_file_name = _schdoc_reference_maps(
        schdocs,
        project_base_dir=project_base_dir,
    )
    harness_candidate_groups = _project_harness_definition_candidate_groups(schdocs)
    harness_definitions = resolve_project_harness_definitions(harness_candidate_groups)
    local_harness_definitions = {
        id(schdoc): resolve_harness_definitions(group)
        for schdoc, group in zip(schdocs, harness_candidate_groups, strict=True)
    }
    inferred_port_harness_types = _project_inferred_port_harness_types(
        schdocs, schdoc_by_source_ref, schdoc_by_file_name
    )
    traced_typed_port_occurrences = _project_traced_typed_port_occurrences(
        schdocs,
        schdoc_by_source_ref,
        schdoc_by_file_name,
        inferred_port_harness_types,
    )
    fallback_sheet_entry_members = _project_fallback_sheet_entry_harness_members(
        schdocs,
        schdoc_by_source_ref,
        schdoc_by_file_name,
        harness_definitions,
        local_harness_definitions,
        inferred_port_harness_types,
    )
    _require_source_schdocs(logical_documents, schdoc_by_logical_id)
    bridge_interface_ids_by_document = _linked_hierarchy_interface_ids_by_document(
        logical_documents,
        schdoc_by_logical_id,
        hierarchy_mode=effective_hierarchy_mode,
        schdoc_by_source_ref=schdoc_by_source_ref,
        schdoc_by_file_name=schdoc_by_file_name,
    )
    top_level_port_name_counts = _top_level_port_name_counts_for_local_connectivity(
        logical_documents,
        schdoc_by_logical_id,
    )
    project_multipart_designators = _project_multipart_designators(schdocs)
    compiled_local_options = replace(options, allow_single_pin_nets=True)

    for logical_document in logical_documents:
        document_net_rows: list[AltiumCompiledNet] = []
        schdoc = schdoc_by_logical_id[logical_document.id]
        local_multipart_designators = _local_multipart_designators(schdoc)
        sheet_entry_parent_ids_by_element_id = _sheet_entry_parent_ids_by_element_id(
            schdoc
        )
        linked_sheet_entry_ids = _linked_sheet_entry_ids(
            schdoc,
            hierarchy_mode=effective_hierarchy_mode,
            schdoc_by_source_ref=schdoc_by_source_ref,
            schdoc_by_file_name=schdoc_by_file_name,
        )
        net_label_by_id = {
            label.unique_id: label
            for label in schdoc.get_net_labels()
            if label.unique_id and getattr(label.record, "parent", None) is None
        }
        use_bridge_scope = False
        try:
            single_sheet_compiler = AltiumNetlistSingleSheetCompiler(
                schdoc,
                options=compiled_local_options,
            )
            local_netlist = single_sheet_compiler.generate()
            pin_roots_by_component = (
                single_sheet_compiler._compiled_pin_roots_by_component()
            )
            component_pin_evidence[logical_document.id] = (
                _compiled_component_pin_evidence(
                    schdoc,
                    logical_document,
                    pin_roots_by_component,
                )
            )
            bridge_scope_options = _effective_bridge_scope_options(
                compiled_local_options,
                effective_hierarchy_mode=effective_hierarchy_mode,
            )
            if bridge_scope_options is not None:
                source_nets = tuple(local_netlist.nets)
                baseline_nets = _effective_bridge_baseline_nets(local_netlist)
                bridge_eligible_interface_ids = bridge_interface_ids_by_document.get(
                    logical_document.id
                )
                should_probe_bridge_scope = (
                    bridge_eligible_interface_ids is not None and not source_nets
                )
                if (
                    bridge_eligible_interface_ids is not None
                    and not should_probe_bridge_scope
                    and not any(getattr(net, "terminals", ()) for net in baseline_nets)
                    and _sheet_entry_wire_endpoint_groups(schdoc, local_netlist)
                ):
                    should_probe_bridge_scope = True
                if should_probe_bridge_scope:
                    bridge_compiler = AltiumNetlistSingleSheetCompiler(
                        schdoc,
                        options=bridge_scope_options,
                    )
                    bridge_compiler._bridge_eligible_interface_ids = (
                        bridge_eligible_interface_ids
                    )
                    bridge_netlist = bridge_compiler.generate()
                    if len(_effective_bridge_baseline_nets(bridge_netlist)) > len(
                        baseline_nets
                    ):
                        use_bridge_scope = True
                    if use_bridge_scope:
                        local_netlist = bridge_netlist
                        harness_endpoint_stats[
                            "effective_scope_bridge_document_count"
                        ] += 1
        except Exception as exc:
            diagnostic = AltiumCompileDiagnostic(
                severity="error",
                code="local_connectivity_compile_failed",
                message=(
                    "Single-sheet local connectivity compilation failed for "
                    f"{logical_document.source_path or logical_document.file_name}: {exc}"
                ),
                source_id=logical_document.id,
            )
            diagnostics.append(diagnostic)
            local_net_counts[logical_document.id] = 0
            continue

        local_harness_stats = _annotate_local_harness_entry_endpoints(
            schdoc,
            local_netlist,
            options,
        )
        _annotate_harness_power_object_endpoints(schdoc, local_netlist)
        for key, value in local_harness_stats.items():
            harness_endpoint_stats[key] += value
        if local_harness_stats["harness_entry_nearby_label_fallback_count"]:
            diagnostics.append(
                AltiumCompileDiagnostic(
                    severity="warning",
                    code="harness_entry_nearby_label_fallback",
                    message=(
                        "Harness connector entries were matched by nearby net "
                        "label because direct wire ownership was unavailable."
                    ),
                    source_id=logical_document.id,
                )
            )

        synthetic_port_connector_nets = _compiled_standalone_port_connector_local_nets(
            schdoc,
            local_netlist,
            logical_document=logical_document,
            first_net_index=len(local_netlist.nets),
            top_level_port_name_counts=top_level_port_name_counts,
            options=options,
        )
        synthetic_isolated_power_nets = _compiled_isolated_power_port_local_nets(
            schdoc,
            local_netlist,
            logical_document=logical_document,
            first_net_index=len(local_netlist.nets)
            + len(synthetic_port_connector_nets),
            options=options,
        )
        harness_endpoint_stats["isolated_power_port_synthetic_net_count"] += len(
            synthetic_isolated_power_nets
        )
        single_pin_nets = _compiled_component_single_pin_local_nets(
            local_netlist,
            logical_document=logical_document,
            first_net_index=(
                len(local_netlist.nets)
                + len(synthetic_port_connector_nets)
                + len(synthetic_isolated_power_nets)
            ),
            pin_roots_by_component=pin_roots_by_component,
            multipart_designators=project_multipart_designators,
            local_multipart_designators=local_multipart_designators,
            options=options,
        )
        synthetic_harness_nets = _compiled_harness_wire_nets(
            schdoc,
            local_netlist,
            logical_document=logical_document,
            first_net_index=(
                len(local_netlist.nets)
                + len(synthetic_port_connector_nets)
                + len(synthetic_isolated_power_nets)
                + len(single_pin_nets)
            ),
        )
        harness_endpoint_stats["harness_wire_synthetic_net_count"] += len(
            synthetic_harness_nets
        )
        synthetic_typed_harness_nets = _compiled_typed_harness_member_nets(
            schdoc,
            local_netlist,
            logical_document=logical_document,
            first_net_index=(
                len(local_netlist.nets)
                + len(synthetic_port_connector_nets)
                + len(synthetic_isolated_power_nets)
                + len(single_pin_nets)
                + len(synthetic_harness_nets)
            ),
            definitions=harness_definitions,
            local_definitions=local_harness_definitions.get(id(schdoc), {}),
            inferred_port_types=inferred_port_harness_types.get(id(schdoc), {}),
            options=options,
            traced_typed_port_occurrences=(
                traced_typed_port_occurrences.get(id(schdoc), frozenset())
            ),
            preexisting_typed_harness_nets=synthetic_harness_nets,
            scalar_port_occurrences=(
                _scalar_port_occurrence_ids_for_wire_network(schdoc)
                if not logical_document.parent_sheet_symbol_ids
                else frozenset()
            ),
            fallback_sheet_entry_members=fallback_sheet_entry_members.get(
                id(schdoc), {}
            ),
        )
        harness_endpoint_stats["typed_harness_member_synthetic_net_count"] += len(
            synthetic_typed_harness_nets
        )
        synthetic_sheet_entry_nets = _compiled_sheet_entry_wire_nets(
            schdoc,
            local_netlist,
            logical_document=logical_document,
            first_net_index=(
                len(local_netlist.nets)
                + len(synthetic_port_connector_nets)
                + len(synthetic_isolated_power_nets)
                + len(single_pin_nets)
                + len(synthetic_harness_nets)
                + len(synthetic_typed_harness_nets)
            ),
            linked_sheet_entry_ids=linked_sheet_entry_ids,
        )
        harness_endpoint_stats["sheet_entry_wire_synthetic_net_count"] += len(
            synthetic_sheet_entry_nets
        )

        source_full_designators: dict[int, str] = {}
        terminal_full_designators_by_pin = _component_pin_full_designators_by_pin(
            schdoc,
            multipart_designators=project_multipart_designators,
            source_full_designators=source_full_designators,
        )
        local_net_counts[logical_document.id] = (
            len(local_netlist.nets)
            + len(synthetic_port_connector_nets)
            + len(synthetic_isolated_power_nets)
            + len(single_pin_nets)
            + len(synthetic_harness_nets)
            + len(synthetic_typed_harness_nets)
            + len(synthetic_sheet_entry_nets)
        )
        for net_index, net in enumerate(local_netlist.nets):
            document_net_rows.append(
                _compiled_local_net_row(
                    net,
                    logical_document=logical_document,
                    net_index=net_index,
                    net_label_by_id=net_label_by_id,
                    sheet_entry_parent_ids_by_element_id=(
                        sheet_entry_parent_ids_by_element_id
                    ),
                    terminal_full_designators_by_pin=terminal_full_designators_by_pin,
                    source_full_designators=source_full_designators,
                    linked_sheet_entry_ids=linked_sheet_entry_ids,
                )
            )
        document_net_rows.extend(synthetic_port_connector_nets)
        document_net_rows.extend(synthetic_isolated_power_nets)
        document_net_rows.extend(single_pin_nets)
        document_net_rows.extend(synthetic_harness_nets)
        document_net_rows.extend(synthetic_typed_harness_nets)
        document_net_rows.extend(synthetic_sheet_entry_nets)
        document_net_rows.sort(key=cmp_to_key(_compiled_managed_local_net_compare))
        net_rows.extend(
            _rewrite_compiled_local_net_identity(
                row,
                _compiled_local_net_id(logical_document.id, row.name, index),
            )
            for index, row in enumerate(document_net_rows)
        )

    return (
        tuple(net_rows),
        local_net_counts,
        harness_endpoint_stats,
        component_pin_evidence,
        tuple(diagnostics),
    )


@dataclass(frozen=True, slots=True)
class _CompiledNameHierarchy:
    paths_by_logical_id: dict[str, tuple[tuple[str, ...], ...]]
    first_child_logical_id_by_symbol_id: dict[str, str]


def _compiled_name_hierarchy(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    *,
    compile_options: AltiumProjectCompileOptions,
) -> _CompiledNameHierarchy:
    first_child_by_symbol_id: dict[str, str] = {}
    document_ordinal_by_id = {
        document.id: document.ordinal for document in logical_documents
    }
    for symbol in sheet_symbols:
        child_ids = symbol._managed_retained_child_logical_document_ids
        if child_ids is None:
            child_ids = _managed_sheet_symbol_child_ids(symbol)
        if child_ids:
            first_child_by_symbol_id[symbol.id] = min(
                child_ids,
                key=lambda child_id: document_ordinal_by_id.get(
                    child_id, len(logical_documents)
                ),
            )
    physical_by_id = {document.id: document for document in physical_documents}
    hierarchical = compile_options.effective_hierarchy_mode not in {"FLAT", "GLOBAL"}
    paths: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for document in physical_documents:
        paths[document.logical_document_id].append(
            _compiled_source_hierarchy_path(document, physical_by_id)
            if hierarchical
            else ()
        )
    for document in logical_documents:
        paths.setdefault(document.id, [])
    return _CompiledNameHierarchy(
        paths_by_logical_id={key: tuple(value) for key, value in paths.items()},
        first_child_logical_id_by_symbol_id=first_child_by_symbol_id,
    )


def _compiled_relative_multichannel_depth(
    hierarchy_path: tuple[str, ...],
    hierarchy: _CompiledNameHierarchy,
) -> int:
    for index, symbol_id in enumerate(hierarchy_path):
        child_id = hierarchy.first_child_logical_id_by_symbol_id.get(symbol_id)
        if (
            child_id is not None
            and len(hierarchy.paths_by_logical_id.get(child_id, ())) > 1
        ):
            return len(hierarchy_path) - index
    return len(hierarchy_path) + 1


def _compiled_source_hierarchy_path(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: Mapping[str, AltiumCompiledPhysicalDocument],
) -> tuple[str, ...]:
    path: list[str] = []
    visited: set[str] = set()
    current: AltiumCompiledPhysicalDocument | None = document
    while current is not None and current.id not in visited:
        visited.add(current.id)
        if current.parent_sheet_symbol_id is not None:
            path.append(current.parent_sheet_symbol_id)
        current = (
            physical_document_by_id.get(current.parent_id)
            if current.parent_id is not None
            else None
        )
    path.reverse()
    return tuple(path)


def _compiled_name_hierarchy_from_physical(
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    physical_document_by_id: Mapping[str, AltiumCompiledPhysicalDocument],
) -> _CompiledNameHierarchy:
    paths: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    first_child_by_symbol_id: dict[str, str] = {}
    for document in physical_documents:
        paths[document.logical_document_id].append(
            _compiled_source_hierarchy_path(document, physical_document_by_id)
        )
        if document.parent_sheet_symbol_id is not None:
            first_child_by_symbol_id.setdefault(
                document.parent_sheet_symbol_id,
                document.logical_document_id,
            )
    return _CompiledNameHierarchy(
        paths_by_logical_id={key: tuple(value) for key, value in paths.items()},
        first_child_logical_id_by_symbol_id=first_child_by_symbol_id,
    )


@dataclass(frozen=True, slots=True)
class _LocalElaborationSetup:
    physical_rows_by_logical_id: Mapping[
        str, tuple[AltiumCompiledPhysicalDocument, ...]
    ]
    inferred_harness_types_by_physical_id: Mapping[str, Mapping[str, tuple[str, ...]]]
    repeat_values_by_parent_endpoint: Mapping[tuple[str, str], tuple[int, ...]]
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument]
    resolved_name_hierarchy: _CompiledNameHierarchy
    physical_instance_offsets: dict[str, int]
    physical_count_by_logical_id: dict[str, int]
    channel_global_indices: dict[str, int]
    channel_differentiate_values: dict[str, int]
    designator_maps: dict[str, dict[str, str]]
    auto_name_designator_maps: dict[str, dict[str, str]]
    local_power_names: dict[str, set[str]]
    room_channel_index_by_physical_id: dict[str, str]


def _local_elaboration_setup(
    local_nets: tuple[AltiumCompiledNet, ...],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    components: tuple[AltiumCompiledComponent, ...],
    *,
    compile_options: AltiumProjectCompileOptions,
    inferred_harness_types_by_physical_id: Mapping[str, Mapping[str, tuple[str, ...]]]
    | None,
    repeat_values_by_parent_endpoint: Mapping[tuple[str, str], tuple[int, ...]] | None,
    name_hierarchy: _CompiledNameHierarchy | None,
) -> _LocalElaborationSetup:
    physical_rows_by_logical_id = _physical_rows_by_logical_id(physical_documents)
    physical_document_by_id = {document.id: document for document in physical_documents}
    resolved_name_hierarchy = name_hierarchy or _compiled_name_hierarchy_from_physical(
        physical_documents,
        physical_document_by_id,
    )
    channel_global_indices = _channel_global_indices(physical_document_by_id)
    return _LocalElaborationSetup(
        physical_rows_by_logical_id=physical_rows_by_logical_id,
        inferred_harness_types_by_physical_id=(
            inferred_harness_types_by_physical_id or {}
        ),
        repeat_values_by_parent_endpoint=repeat_values_by_parent_endpoint or {},
        physical_document_by_id=physical_document_by_id,
        resolved_name_hierarchy=resolved_name_hierarchy,
        physical_instance_offsets=_logical_instance_offsets(physical_documents),
        physical_count_by_logical_id=_physical_count_by_logical_id(physical_documents),
        channel_global_indices=channel_global_indices,
        channel_differentiate_values=_channel_differentiate_values_for_room_style(
            physical_document_by_id,
            channel_global_indices=channel_global_indices,
            style=compile_options.channel_room_naming_style,
        ),
        designator_maps=_component_designator_maps_by_physical_document(components),
        auto_name_designator_maps=(
            _component_base_designator_maps_by_physical_document(components)
        ),
        local_power_names=_compiled_local_power_names(local_nets),
        room_channel_index_by_physical_id=_room_channel_indices(
            physical_documents,
            components,
        ),
    )


def _physical_rows_by_logical_id(
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
) -> Mapping[str, tuple[AltiumCompiledPhysicalDocument, ...]]:
    physical_rows: dict[str, list[AltiumCompiledPhysicalDocument]] = defaultdict(list)
    for document in physical_documents:
        physical_rows[document.logical_document_id].append(document)
    return {key: tuple(value) for key, value in physical_rows.items()}


def _physical_room_paths_by_parent(
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
) -> Mapping[
    tuple[str | None, str, str],
    tuple[AltiumCompiledPhysicalDocument, ...],
]:
    room_paths_by_parent: dict[
        tuple[str | None, str, str],
        list[AltiumCompiledPhysicalDocument],
    ] = defaultdict(list)
    for document in physical_documents:
        room_paths_by_parent[
            (document.parent_id, document.logical_document_id, document.room_name)
        ].append(document)
    return {key: tuple(value) for key, value in room_paths_by_parent.items()}


def _physical_component_designators(
    components: tuple[AltiumCompiledComponent, ...],
) -> Mapping[str, tuple[str, ...]]:
    component_designators: dict[str, list[str]] = defaultdict(list)
    for component in components:
        if component.physical_designator:
            component_designators[component.physical_document_id].append(
                component.physical_designator
            )
    return {key: tuple(value) for key, value in component_designators.items()}


def _repeated_room_sort_key(
    document: AltiumCompiledPhysicalDocument,
    component_designators: Mapping[str, tuple[str, ...]],
) -> tuple[bool, tuple[list[tuple[int, object]], str], int, str]:
    designators = sorted(
        component_designators.get(document.id, ()),
        key=_altium_net_total_sort_key,
    )
    first_key = _altium_net_total_sort_key(designators[0]) if designators else ([], "")
    return (
        not designators,
        first_key,
        _managed_repeat_channel_value(document),
        document.id,
    )


def _room_channel_indices(
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    components: tuple[AltiumCompiledComponent, ...],
) -> dict[str, str]:
    room_paths_by_parent = _physical_room_paths_by_parent(physical_documents)
    component_designators = _physical_component_designators(components)
    return {
        document.id: str(index)
        for room_documents in room_paths_by_parent.values()
        if len(room_documents) > 1
        for index, document in enumerate(
            sorted(
                room_documents,
                key=lambda row: _repeated_room_sort_key(row, component_designators),
            ),
            start=1,
        )
    }


def _compiled_applicable_physical_endpoints(
    local_net: AltiumCompiledNet,
    physical_document_id: str,
    inferred_harness_types_by_physical_id: Mapping[str, Mapping[str, tuple[str, ...]]],
) -> tuple[AltiumCompiledNetEndpoint, ...] | None:
    endpoints = tuple(
        endpoint
        for endpoint in local_net.endpoints
        if _inferred_harness_endpoint_applies(
            endpoint,
            inferred_harness_types_by_physical_id.get(physical_document_id, {}),
        )
    )
    if (
        local_net.endpoints
        and not endpoints
        and not local_net.terminals
        and all(endpoint._harness_type_inferred for endpoint in local_net.endpoints)
    ):
        return None
    return endpoints


def _elaborate_local_connectivity(
    local_nets: tuple[AltiumCompiledNet, ...],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    components: tuple[AltiumCompiledComponent, ...],
    *,
    channel_designator_format: str,
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile | None = None,
    differential_pair_suffixes: tuple[tuple[str, str], ...] = (),
    inferred_harness_types_by_physical_id: Mapping[str, Mapping[str, tuple[str, ...]]]
    | None = None,
    repeat_values_by_parent_endpoint: Mapping[tuple[str, str], tuple[int, ...]]
    | None = None,
    name_hierarchy: _CompiledNameHierarchy | None = None,
    component_pin_evidence: Mapping[str, Mapping[int, _CompiledComponentPinEvidence]]
    | None = None,
) -> tuple[AltiumCompiledNet, ...]:
    """Instantiate logical-local net rows for each physical document instance."""
    setup = _local_elaboration_setup(
        local_nets,
        physical_documents,
        components,
        compile_options=compile_options,
        inferred_harness_types_by_physical_id=inferred_harness_types_by_physical_id,
        repeat_values_by_parent_endpoint=repeat_values_by_parent_endpoint,
        name_hierarchy=name_hierarchy,
    )
    physical_rows_by_logical_id = setup.physical_rows_by_logical_id
    inferred_harness_types_by_physical_id = setup.inferred_harness_types_by_physical_id
    repeat_values_by_parent_endpoint = setup.repeat_values_by_parent_endpoint
    physical_document_by_id = setup.physical_document_by_id
    resolved_name_hierarchy = setup.resolved_name_hierarchy
    physical_instance_offsets = setup.physical_instance_offsets
    physical_count_by_logical_id = setup.physical_count_by_logical_id
    channel_global_indices = setup.channel_global_indices
    channel_differentiate_values = setup.channel_differentiate_values
    designator_maps = setup.designator_maps
    auto_name_designator_maps = setup.auto_name_designator_maps
    local_power_names = setup.local_power_names
    room_channel_index_by_physical_id = setup.room_channel_index_by_physical_id
    source_pin_designator_maps = _component_source_pin_designator_maps(
        components, component_pin_evidence or {}
    )

    physical_nets: list[AltiumCompiledNet] = []
    for local_net in local_nets:
        if local_net.scope != "logical_local" or local_net.logical_document_id is None:
            continue
        for physical_document in physical_rows_by_logical_id.get(
            local_net.logical_document_id,
            (),
        ):
            source_hierarchy_path = (
                _compiled_source_hierarchy_path(
                    physical_document,
                    physical_document_by_id,
                )
                if compile_options.effective_hierarchy_mode not in {"FLAT", "GLOBAL"}
                else ()
            )
            source_endpoints = _compiled_applicable_physical_endpoints(
                local_net,
                physical_document.id,
                inferred_harness_types_by_physical_id,
            )
            if source_endpoints is None:
                continue
            physical_net_id = _compiled_physical_net_id(
                physical_document.id,
                local_net.id,
            )
            source_prefix = f"{local_net.id}:"
            physical_net_prefix = f"{physical_net_id}:"
            designator_map = designator_maps.get(physical_document.id, {})
            source_pin_designators = source_pin_designator_maps.get(
                physical_document.id, {}
            )
            nonlocal_hierarchical_power = _compiled_is_nonlocal_hierarchical_power(
                local_net,
                local_power_names,
                compile_options,
            )

            def project_name(source_net: AltiumCompiledNet) -> str:
                return _physical_channel_net_name(
                    source_net,
                    physical_document,
                    designator_map=designator_map,
                    auto_name_designator_map=auto_name_designator_maps.get(
                        physical_document.id,
                        {},
                    ),
                    source_pin_designators=source_pin_designators,
                    channel_designator_format=channel_designator_format,
                    room_channel_index_override=room_channel_index_by_physical_id.get(
                        physical_document.id
                    ),
                    physical_document_by_id=physical_document_by_id,
                    physical_instance_offsets=physical_instance_offsets,
                    physical_count_by_logical_id=physical_count_by_logical_id,
                    channel_global_indices=channel_global_indices,
                    channel_differentiate_values=channel_differentiate_values,
                    compile_options=compile_options,
                    differential_pair_suffixes=differential_pair_suffixes,
                    nonlocal_hierarchical_power=nonlocal_hierarchical_power,
                )

            physical_name = project_name(local_net)
            annotated_name = _compiled_net_name_annotation(physical_name, annotation)
            bus_prefix_full_name: str | None = None
            if local_net._name_source_is_bus:
                bus_prefix_net = replace(
                    local_net,
                    name=_compiled_bus_prefix(local_net),
                    _name_source_bus_prefix="",
                    _name_source_bus_suffix="",
                )
                physical_bus_prefix = project_name(bus_prefix_net)
                annotated_bus_prefix = _compiled_net_name_annotation(
                    physical_bus_prefix,
                    annotation,
                )
                bus_prefix_full_name = (
                    annotated_bus_prefix
                    if annotated_bus_prefix is not None
                    else physical_bus_prefix
                )
            signal_harness_name_candidates = tuple(
                _compiled_physical_harness_name_candidate(
                    candidate,
                    local_net=local_net,
                    project_name=project_name,
                    annotation=annotation,
                    source_hierarchy_path=source_hierarchy_path,
                    resolved_name_hierarchy=resolved_name_hierarchy,
                    compile_options=compile_options,
                )
                for candidate in local_net._signal_harness_name_candidates
            )
            terminals = tuple(
                _compiled_terminal_for_physical(
                    terminal_id,
                    terminal,
                    source_id=local_net.id,
                    source_prefix=source_prefix,
                    target_id=physical_net_id,
                    target_prefix=physical_net_prefix,
                    designator_map=designator_map,
                    source_pin_designators=source_pin_designators,
                )
                for terminal_id, terminal in zip(
                    local_net.terminal_ids,
                    local_net.terminals,
                    strict=True,
                )
            )
            terminal_ids = tuple(terminal.id for terminal in terminals)
            projected_endpoints_by_source_id = {
                endpoint.id: (
                    _compiled_endpoints_for_physical(
                        endpoint,
                        source_id=local_net.id,
                        source_prefix=source_prefix,
                        target_id=physical_net_id,
                        target_prefix=physical_net_prefix,
                        designator_map=designator_map,
                        source_pin_designators=source_pin_designators,
                        repeat_values=repeat_values_by_parent_endpoint.get(
                            (physical_document.id, endpoint._source_occurrence_id),
                            (),
                        ),
                    )
                    if endpoint in source_endpoints
                    else ()
                )
                for endpoint in local_net.endpoints
            }
            projected_item_endpoints_by_source_id = {
                endpoint.id: projected_endpoints_by_source_id[endpoint.id]
                for endpoint in local_net.endpoints
                if endpoint._harness_type_inferred
                or repeat_values_by_parent_endpoint.get(
                    (physical_document.id, endpoint._source_occurrence_id), ()
                )
            }
            endpoints = tuple(
                projected
                for endpoint_rows in projected_endpoints_by_source_id.values()
                for projected in endpoint_rows
            )
            endpoint_ids = tuple(endpoint.id for endpoint in endpoints)
            items = _compiled_items_for_physical(
                local_net.items,
                projected_endpoints_by_source_id=(
                    projected_item_endpoints_by_source_id
                ),
                source_id=local_net.id,
                source_prefix=source_prefix,
                target_id=physical_net_id,
                target_prefix=physical_net_prefix,
                designator_map=designator_map,
                physical_document_id=physical_document.id,
                source_pin_designators=source_pin_designators,
                projected_terminals_by_source_id=dict(
                    zip(local_net.terminal_ids, terminals, strict=True)
                ),
            )
            item_ids = tuple(item.id for item in items)
            physical_nets.append(
                AltiumCompiledNet(
                    id=physical_net_id,
                    name=(
                        annotated_name if annotated_name is not None else physical_name
                    ),
                    original_name=(
                        physical_name
                        if annotated_name is not None
                        else local_net.original_name
                    ),
                    override_name=(
                        annotated_name
                        if annotated_name is not None
                        else local_net.override_name
                    ),
                    scope="physical_local",
                    logical_document_id=local_net.logical_document_id,
                    physical_document_ids=(physical_document.id,),
                    auto_named=local_net.auto_named,
                    single_pin=local_net.single_pin,
                    aliases=local_net.aliases,
                    terminal_ids=terminal_ids,
                    terminals=terminals,
                    endpoint_ids=endpoint_ids,
                    endpoints=endpoints,
                    item_ids=item_ids,
                    items=items,
                    source_net_ids=(local_net.id,),
                    diagnostics=local_net.diagnostics,
                    _name_source_kind=local_net._name_source_kind,
                    _name_source_name=local_net._name_source_name,
                    _name_source_bus_prefix=local_net._name_source_bus_prefix,
                    _name_source_bus_prefix_full_name=bus_prefix_full_name,
                    _name_source_bus_suffix=local_net._name_source_bus_suffix,
                    _name_source_priority=local_net._name_source_priority,
                    _name_source_raw_name=local_net._name_source_raw_name,
                    _name_source_is_bus=local_net._name_source_is_bus,
                    _name_source_schematic_id=local_net.logical_document_id,
                    _name_source_hierarchy_path=source_hierarchy_path,
                    _name_source_is_multipath=(
                        compile_options.effective_hierarchy_mode
                        not in {"FLAT", "GLOBAL"}
                        and len(
                            resolved_name_hierarchy.paths_by_logical_id.get(
                                local_net.logical_document_id, ()
                            )
                        )
                        > 1
                    ),
                    _name_source_relative_multichannel_depth=(
                        _compiled_relative_multichannel_depth(
                            source_hierarchy_path,
                            resolved_name_hierarchy,
                        )
                    ),
                    _name_source_full_name=(
                        annotated_name if annotated_name is not None else physical_name
                    ),
                    _name_source_autogenerated=local_net._name_source_autogenerated,
                    _bus_signal_width=local_net._bus_signal_width,
                    _bus_signal_offset=local_net._bus_signal_offset,
                    _source_connection_link_id=local_net._source_connection_link_id,
                    _contains_bus=local_net._contains_bus,
                    _port_bus_member_names=local_net._port_bus_member_names,
                    _scalar_merge_name_sources=(local_net._scalar_merge_name_sources),
                    _signal_harness_name_candidates=(signal_harness_name_candidates),
                )
            )

    return tuple(physical_nets)


def _compiled_physical_harness_name_candidate(
    candidate: _SignalHarnessNameCandidate,
    *,
    local_net: AltiumCompiledNet,
    project_name: Callable[[AltiumCompiledNet], str],
    annotation: AnnotationFile | None,
    source_hierarchy_path: tuple[str, ...],
    resolved_name_hierarchy: _CompiledNameHierarchy,
    compile_options: AltiumProjectCompileOptions,
) -> _SignalHarnessNameCandidate:
    candidate_net = replace(
        local_net,
        name=candidate.value,
        auto_named=False,
        _name_source_kind=candidate.source_kind,
        _name_source_priority=candidate.source_priority,
        _name_source_raw_name=candidate.value,
        _name_source_bus_prefix=candidate.bus_signal_prefix,
        _name_source_bus_suffix=candidate.bus_signal_suffix,
        _name_source_is_bus=bool(candidate.bus_signal_prefix),
        _name_source_autogenerated=False,
    )
    physical_name = project_name(candidate_net)
    annotated_name = _compiled_net_name_annotation(physical_name, annotation)
    full_name = annotated_name if annotated_name is not None else physical_name
    bus_prefix_full_name: str | None = None
    if candidate.bus_signal_prefix:
        prefix_net = replace(
            candidate_net,
            name=candidate.bus_signal_prefix,
            _name_source_bus_prefix="",
            _name_source_bus_suffix="",
        )
        physical_prefix = project_name(prefix_net)
        annotated_prefix = _compiled_net_name_annotation(physical_prefix, annotation)
        bus_prefix_full_name = (
            annotated_prefix if annotated_prefix is not None else physical_prefix
        )
    return replace(
        candidate,
        source_schematic_id=local_net.logical_document_id or "",
        hierarchy_path=source_hierarchy_path,
        source_is_multipath=(
            compile_options.effective_hierarchy_mode not in {"FLAT", "GLOBAL"}
            and len(
                resolved_name_hierarchy.paths_by_logical_id.get(
                    local_net.logical_document_id or "", ()
                )
            )
            > 1
        ),
        relative_multichannel_depth=_compiled_relative_multichannel_depth(
            source_hierarchy_path,
            resolved_name_hierarchy,
        ),
        full_name=full_name,
        bus_prefix_full_name=bus_prefix_full_name,
    )


def _compiled_local_power_names(
    local_nets: tuple[AltiumCompiledNet, ...],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for net in local_nets:
        if net.logical_document_id is None:
            continue
        if not any(endpoint.role == "port" for endpoint in net.endpoints):
            continue
        result[net.logical_document_id].update(
            dotnet_ordinal_ignore_case_key(endpoint.name)
            for endpoint in net.endpoints
            if endpoint.role in {"power_port", "harness_power"} and endpoint.name
        )
    return result


def _compiled_is_nonlocal_hierarchical_power(
    net: AltiumCompiledNet,
    local_power_names: Mapping[str, set[str]],
    compile_options: AltiumProjectCompileOptions,
) -> bool:
    if compile_options.effective_hierarchy_mode != "HIERARCHICAL":
        return False
    if net._name_source_kind not in {"power_port", "hidden_pin"}:
        return False
    if net.logical_document_id is None:
        return False
    names = local_power_names.get(net.logical_document_id)
    return names is None or dotnet_ordinal_ignore_case_key(net.name) not in names


def _find_physical_net_with_sheet_entry(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    sheet_symbol_uid: str,
    entry_name: str,
    entry_uid: str = "",
) -> AltiumCompiledNet | None:
    if not entry_name:
        return None
    clean_entry_uid = dotnet_ordinal_ignore_case_key(str(entry_uid or ""))
    if clean_entry_uid:
        clean_symbol_uid = dotnet_ordinal_ignore_case_key(str(sheet_symbol_uid or ""))
        expected_prefix = f"{clean_symbol_uid}_" if clean_symbol_uid else ""
        for net in physical_nets:
            if physical_document_id not in net.physical_document_ids:
                continue
            if any(
                endpoint.role.lower() == "sheet_entry"
                and dotnet_ordinal_ignore_case_key(endpoint.object_id)
                == clean_entry_uid
                and (
                    not expected_prefix
                    or dotnet_ordinal_ignore_case_key(endpoint.element_id).startswith(
                        expected_prefix
                    )
                )
                for endpoint in net.endpoints
            ):
                return net
        return None
    entry_id = f"{sheet_symbol_uid}_{entry_name}" if sheet_symbol_uid else entry_name
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if _compiled_net_has_endpoint(net, "sheet_entry", entry_id):
            return net
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if _compiled_net_has_symbol_scoped_sheet_entry(
            net,
            sheet_symbol_uid=sheet_symbol_uid,
            entry_name=entry_name,
        ):
            return net
    return None


def _compiled_net_has_symbol_scoped_sheet_entry(
    net: AltiumCompiledNet,
    *,
    sheet_symbol_uid: str,
    entry_name: str,
) -> bool:
    clean_symbol_uid = dotnet_ordinal_ignore_case_key(str(sheet_symbol_uid or ""))
    clean_entry_name = dotnet_ordinal_ignore_case_key(str(entry_name or ""))
    if not clean_symbol_uid or not clean_entry_name:
        return False
    expected_prefix = f"{clean_symbol_uid}_"
    return any(
        endpoint.role.lower() == "sheet_entry"
        and dotnet_ordinal_ignore_case_key(endpoint.element_id).startswith(
            expected_prefix
        )
        and dotnet_ordinal_ignore_case_key(endpoint.name) == clean_entry_name
        for endpoint in net.endpoints
    )


def _find_physical_net_with_any_sheet_entry_name(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    entry_name: str,
) -> AltiumCompiledNet | None:
    clean_entry_name = dotnet_ordinal_ignore_case_key(str(entry_name or ""))
    if not clean_entry_name:
        return None
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if any(
            endpoint.role.lower() == "sheet_entry"
            and dotnet_ordinal_ignore_case_key(endpoint.name) == clean_entry_name
            for endpoint in net.endpoints
        ):
            return net
    return None


def _find_physical_net_with_port(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    port_uid: str,
    port_name: str,
) -> AltiumCompiledNet | None:
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if port_uid and _compiled_net_has_endpoint(net, "port", port_uid):
            return net
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if _compiled_net_has_port_name(net, port_name):
            return net
    return None


def _compiled_net_has_port_name(net: AltiumCompiledNet, port_name: str) -> bool:
    clean_port_name = dotnet_ordinal_ignore_case_key(str(port_name or ""))
    if not clean_port_name:
        return False
    return any(
        endpoint.role == "port"
        and dotnet_ordinal_ignore_case_key(endpoint.name) == clean_port_name
        for endpoint in net.endpoints
    )


def _find_physical_net_with_harness_entry(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    harness_entry_name: str,
) -> AltiumCompiledNet | None:
    clean_harness_entry_name = str(harness_entry_name or "").lower()
    if not clean_harness_entry_name:
        return None
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if any(
            endpoint.role.lower() == "harness_entry"
            and endpoint.name.lower() == clean_harness_entry_name
            for endpoint in net.endpoints
        ):
            return net
    return None


def _find_physical_net_by_name(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    net_name: str,
) -> AltiumCompiledNet | None:
    if not net_name:
        return None
    for net in physical_nets:
        if (
            physical_document_id in net.physical_document_ids
            and net.name.lower() == net_name.lower()
        ):
            return net
    return None


def _find_physical_net_by_name_or_label(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    net_name: str,
) -> AltiumCompiledNet | None:
    net = _find_physical_net_by_name(
        physical_nets,
        physical_document_id=physical_document_id,
        net_name=net_name,
    )
    if net is not None:
        return net
    clean_name = str(net_name or "").lower()
    if not clean_name:
        return None
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if any(
            label_name.lower() == clean_name
            for label_name in _compiled_net_label_names(net)
        ):
            return net
    return None


def _append_inter_sheet_link_net(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    *,
    link_id: str,
    name: str,
    parent_net: AltiumCompiledNet,
    child_net: AltiumCompiledNet,
    aliases: tuple[str, ...] = (),
    source_net_ids: tuple[str, ...] | None = None,
    hierarchy_parent_entry_name: str = "",
    hierarchy_link_name: str = "",
    hierarchy_child_name: str = "",
    hierarchy_child_object_ids: tuple[str, ...] = (),
    hierarchy_match_kind: str = "",
) -> None:
    link_id = _collision_safe_inter_sheet_link_id(link_id, link_id_counts)
    physical_document_ids = tuple(
        dict.fromkeys(
            (*parent_net.physical_document_ids, *child_net.physical_document_ids)
        )
    )
    terminal_ids = tuple(
        dict.fromkeys((*parent_net.terminal_ids, *child_net.terminal_ids))
    )
    endpoint_ids = tuple(
        dict.fromkeys((*parent_net.endpoint_ids, *child_net.endpoint_ids))
    )
    terminals = _dedupe_compiled_terminals(
        [*parent_net.terminals, *child_net.terminals]
    )
    endpoints = _dedupe_compiled_endpoints(
        [*parent_net.endpoints, *child_net.endpoints]
    )
    item_ids = tuple(dict.fromkeys((*parent_net.item_ids, *child_net.item_ids)))
    items = _dedupe_compiled_items([*parent_net.items, *child_net.items])
    if source_net_ids is None:
        source_net_ids = tuple(dict.fromkeys((parent_net.id, child_net.id)))
    link_nets.append(
        AltiumCompiledNet(
            id=link_id,
            name=name,
            original_name=name,
            scope="inter_sheet_link",
            logical_document_id=None,
            physical_document_ids=physical_document_ids,
            auto_named=parent_net.auto_named and child_net.auto_named,
            single_pin=False,
            aliases=aliases,
            terminal_ids=terminal_ids,
            terminals=terminals,
            endpoint_ids=endpoint_ids,
            endpoints=endpoints,
            item_ids=item_ids,
            items=items,
            source_net_ids=source_net_ids,
            parent_net_id=parent_net.id,
            child_net_id=child_net.id,
            link_ids=(link_id,),
            _hierarchy_parent_entry_name=hierarchy_parent_entry_name,
            _hierarchy_link_name=hierarchy_link_name or name,
            _hierarchy_child_name=hierarchy_child_name,
            _hierarchy_child_object_ids=hierarchy_child_object_ids,
            _hierarchy_match_kind=hierarchy_match_kind,
        )
    )


def _hierarchy_endpoint_object_ids(
    net: AltiumCompiledNet,
    *,
    roles: frozenset[str],
    name: str,
    source_occurrence_id: str = "",
) -> tuple[str, ...]:
    name_key = dotnet_ordinal_ignore_case_key(name)
    values: list[str] = []
    for endpoint in net.endpoints:
        if not _hierarchy_endpoint_matches(
            endpoint,
            roles=roles,
            name=name,
            name_key=name_key,
            source_occurrence_id=source_occurrence_id,
        ):
            continue
        values.extend((endpoint.object_id, endpoint.element_id))
    return tuple(dict.fromkeys(value for value in values if value))


def _hierarchy_endpoint_matches(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    roles: frozenset[str],
    name: str,
    name_key: str,
    source_occurrence_id: str,
) -> bool:
    if endpoint.role not in roles:
        return False
    if source_occurrence_id and endpoint._source_occurrence_id != source_occurrence_id:
        return False
    if not name_key:
        return True
    return (
        dotnet_ordinal_ignore_case_key(endpoint.name) == name_key
        or endpoint.role == "port"
        and _managed_port_collector_matches(endpoint.name, name)
    )


def _collision_safe_inter_sheet_link_id(
    base_link_id: str,
    link_id_counts: dict[str, int],
) -> str:
    candidate = base_link_id
    duplicate_index = 0
    while candidate in link_id_counts:
        duplicate_index += 1
        candidate = f"{base_link_id}:duplicate:{duplicate_index}"
    link_id_counts[candidate] = 1
    return candidate


@dataclass(frozen=True, slots=True)
class _InterSheetPortOccurrence:
    name: str
    unique_id: str
    source_occurrence_id: str


def _raw_ports_for_inter_sheet_linking(
    logical_document_id: str,
    child_schdoc: "AltiumSchDoc",
    raw_ports_by_logical_id: dict[str, tuple["SchPortInfo", ...]] | None,
) -> tuple["SchPortInfo", ...]:
    ports = (
        raw_ports_by_logical_id.get(logical_document_id)
        if raw_ports_by_logical_id is not None
        else None
    )
    if ports is None:
        ports = tuple(child_schdoc.get_ports())
        if raw_ports_by_logical_id is not None:
            raw_ports_by_logical_id[logical_document_id] = ports
    return ports


def _group_active_inter_sheet_ports(
    ports: tuple["SchPortInfo", ...],
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> dict[str, tuple[_InterSheetPortOccurrence, ...]]:
    grouped: defaultdict[str, list[_InterSheetPortOccurrence]] = defaultdict(list)
    for port_index, port in _compiler_source_rows(ports):
        harness_type = str(
            getattr(getattr(port, "record", None), "harness_type", "") or ""
        )
        if (
            _port_is_compile_masked(port, compile_masks)
            or not port.name
            or harness_type
        ):
            continue
        grouped[dotnet_ordinal_ignore_case_key(port.name)].append(
            _InterSheetPortOccurrence(
                name=str(port.name),
                unique_id=str(getattr(port, "unique_id", "") or ""),
                source_occurrence_id=f"port:{port_index}",
            )
        )
    return {name: tuple(matches) for name, matches in grouped.items()}


def _child_ports_by_name_for_inter_sheet_linking(
    child_ports_by_logical_id: dict[
        str, dict[str, tuple[_InterSheetPortOccurrence, ...]]
    ],
    child_document: AltiumCompiledPhysicalDocument,
    child_schdoc: "AltiumSchDoc",
    raw_ports_by_logical_id: dict[str, tuple["SchPortInfo", ...]] | None = None,
) -> dict[str, tuple[_InterSheetPortOccurrence, ...]]:
    child_ports_by_name = child_ports_by_logical_id.get(
        child_document.logical_document_id
    )
    if child_ports_by_name is None:
        ports = _raw_ports_for_inter_sheet_linking(
            child_document.logical_document_id,
            child_schdoc,
            raw_ports_by_logical_id,
        )
        child_ports_by_name = _group_active_inter_sheet_ports(
            ports,
            _compiled_compile_mask_bounds(child_schdoc),
        )
        child_ports_by_logical_id[child_document.logical_document_id] = (
            child_ports_by_name
        )
    return child_ports_by_name


def _append_harness_inter_sheet_links(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    stats: dict[str, int],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    symbol_info: "SchSheetSymbolInfo",
    entry: object,
    parent_schdoc: "AltiumSchDoc",
    child_schdoc: "AltiumSchDoc",
    definitions: Mapping[str, ResolvedHarnessDefinition],
    child_local_definitions: Mapping[str, ResolvedHarnessDefinition],
    child_inferred_port_types: Mapping[str, tuple[str, ...]],
) -> None:
    interface = _typed_sheet_entry_interface_for_object(
        parent_schdoc, symbol_info, entry
    )
    if interface is None:
        return
    definition = definitions.get(dotnet_ordinal_ignore_case_key(interface.type_name))
    if definition is None:
        _append_fallback_harness_inter_sheet_links(
            link_nets,
            link_id_counts,
            stats,
            physical_nets=physical_nets,
            parent_document=parent_document,
            child_document=child_document,
            symbol=symbol,
            parent_interface=interface,
            child_schdoc=child_schdoc,
            definitions=definitions,
            child_local_definitions=child_local_definitions,
            child_inferred_port_types=child_inferred_port_types,
        )
        return
    child_port_name = _repeat_harness_port_name(
        interface.name,
        child_document._managed_repeat_channel_value,
    )
    child_interfaces = _matching_child_typed_harness_interfaces(
        _typed_harness_interfaces(child_schdoc, child_inferred_port_types),
        port_name=child_port_name,
        type_name=interface.type_name,
    )
    for member in definition.members:
        _append_harness_member_inter_sheet_links(
            link_nets,
            link_id_counts,
            stats,
            physical_nets=physical_nets,
            parent_document=parent_document,
            child_document=child_document,
            symbol=symbol,
            parent_interface=interface,
            child_interfaces=child_interfaces,
            member=member,
        )


def _append_fallback_harness_inter_sheet_links(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    stats: dict[str, int],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    parent_interface: _TypedHarnessInterface,
    child_schdoc: "AltiumSchDoc",
    definitions: Mapping[str, ResolvedHarnessDefinition],
    child_local_definitions: Mapping[str, ResolvedHarnessDefinition],
    child_inferred_port_types: Mapping[str, tuple[str, ...]],
) -> None:
    candidates = _fallback_child_harness_members(
        child_schdoc,
        child_port_name=_repeat_harness_port_name(
            parent_interface.name,
            child_document._managed_repeat_channel_value,
        ),
        definitions=definitions,
        child_local_definitions=child_local_definitions,
        child_inferred_port_types=child_inferred_port_types,
    )
    for child_interface, member in candidates:
        stats["candidate_count"] += 1
        stats["harness_candidate_count"] += 1
        result = _append_one_fallback_harness_inter_sheet_link(
            link_nets,
            link_id_counts,
            physical_nets=physical_nets,
            parent_document=parent_document,
            child_document=child_document,
            symbol=symbol,
            parent_interface=parent_interface,
            child_interface=child_interface,
            member=member,
        )
        if result == 0:
            stats["unmatched_candidate_count"] += 1
        elif result == 1:
            stats["harness_endpoint_only_count"] += 1
        else:
            stats["matched_count"] += 1
            stats["harness_matched_count"] += 1


def _fallback_child_harness_members(
    child_schdoc: "AltiumSchDoc",
    *,
    child_port_name: str,
    definitions: Mapping[str, ResolvedHarnessDefinition],
    child_local_definitions: Mapping[str, ResolvedHarnessDefinition],
    child_inferred_port_types: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[_TypedHarnessInterface, ResolvedHarnessMember], ...]:
    result: list[tuple[_TypedHarnessInterface, ResolvedHarnessMember]] = []
    emitted: set[tuple[object, ...]] = set()
    parent_name_key = dotnet_ordinal_ignore_case_key(child_port_name)
    for interface in _typed_harness_interfaces(child_schdoc, child_inferred_port_types):
        if interface.role != "port":
            continue
        if dotnet_ordinal_ignore_case_key(interface.name) != parent_name_key:
            continue
        definition = _definition_for_typed_harness_interface(
            interface, definitions, child_local_definitions
        )
        if definition is None:
            continue
        for member in definition.members:
            key = (
                interface.source_occurrence_id,
                tuple(segment.value for segment in member.path),
                member.bus_signal_index,
            )
            if key not in emitted:
                emitted.add(key)
                result.append((interface, member))
    return tuple(result)


def _append_one_fallback_harness_inter_sheet_link(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    parent_interface: _TypedHarnessInterface,
    child_interface: _TypedHarnessInterface,
    member: ResolvedHarnessMember,
) -> int:
    child_net = _find_exact_physical_harness_member_net(
        physical_nets,
        physical_document_id=child_document.id,
        interface=child_interface,
        member=member,
    )
    if child_net is None:
        return 0
    parent_net = _find_exact_physical_harness_member_net(
        physical_nets,
        physical_document_id=parent_document.id,
        interface=parent_interface,
        member=member,
        repeat_value=child_document._managed_repeat_channel_value
        if symbol.is_repeat
        else None,
    )
    if parent_net is None:
        return 1
    _append_one_harness_member_inter_sheet_link(
        link_nets,
        link_id_counts,
        physical_document_ids=(parent_document.id, child_document.id),
        symbol_source_object_id=symbol.source_object_id,
        parent_interface=parent_interface,
        child_interfaces=(child_interface,),
        member=member,
        parent_net=parent_net,
        child_net=child_net,
    )
    return 2


def _matching_child_typed_harness_interfaces(
    interfaces: Sequence[_TypedHarnessInterface],
    *,
    port_name: str,
    type_name: str,
) -> tuple[_TypedHarnessInterface, ...]:
    port_key = dotnet_ordinal_ignore_case_key(port_name)
    type_key = dotnet_ordinal_ignore_case_key(type_name)
    return tuple(
        interface
        for interface in interfaces
        if interface.role == "port"
        and dotnet_ordinal_ignore_case_key(interface.name) == port_key
        and dotnet_ordinal_ignore_case_key(interface.type_name) == type_key
    )


def _append_harness_member_inter_sheet_links(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    stats: dict[str, int],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    parent_interface: _TypedHarnessInterface,
    child_interfaces: Sequence[_TypedHarnessInterface],
    member: ResolvedHarnessMember,
) -> None:
    stats["candidate_count"] += 1
    stats["harness_candidate_count"] += 1
    parent_net = _find_exact_physical_harness_member_net(
        physical_nets,
        physical_document_id=parent_document.id,
        interface=parent_interface,
        member=member,
        repeat_value=child_document._managed_repeat_channel_value,
    )
    child_matches = _exact_child_harness_member_matches(
        physical_nets,
        physical_document_id=child_document.id,
        child_interfaces=child_interfaces,
        member=member,
    )
    if not child_matches:
        stats["unmatched_candidate_count"] += 1
        return
    if parent_net is None:
        stats["harness_endpoint_only_count"] += len(child_matches)
        return
    for matching_child_interfaces, child_net in child_matches:
        _append_one_harness_member_inter_sheet_link(
            link_nets,
            link_id_counts,
            physical_document_ids=(parent_document.id, child_document.id),
            symbol_source_object_id=symbol.source_object_id,
            parent_interface=parent_interface,
            child_interfaces=matching_child_interfaces,
            member=member,
            parent_net=parent_net,
            child_net=child_net,
        )
        stats["matched_count"] += 1
        stats["harness_matched_count"] += 1


def _exact_child_harness_member_nets(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    child_interfaces: Sequence[_TypedHarnessInterface],
    member: ResolvedHarnessMember,
) -> tuple[AltiumCompiledNet, ...]:
    return tuple(
        net
        for _interfaces, net in _exact_child_harness_member_matches(
            physical_nets,
            physical_document_id=physical_document_id,
            child_interfaces=child_interfaces,
            member=member,
        )
    )


def _exact_child_harness_member_matches(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    child_interfaces: Sequence[_TypedHarnessInterface],
    member: ResolvedHarnessMember,
) -> tuple[tuple[tuple[_TypedHarnessInterface, ...], AltiumCompiledNet], ...]:
    matches_by_net_id: dict[
        str, tuple[list[_TypedHarnessInterface], AltiumCompiledNet]
    ] = {}
    for child_interface in child_interfaces:
        child_net = _find_exact_physical_harness_member_net(
            physical_nets,
            physical_document_id=physical_document_id,
            interface=child_interface,
            member=member,
        )
        if child_net is None:
            continue
        if child_net.id not in matches_by_net_id:
            matches_by_net_id[child_net.id] = ([], child_net)
        matches_by_net_id[child_net.id][0].append(child_interface)
    return tuple(
        (tuple(interfaces), net) for interfaces, net in matches_by_net_id.values()
    )


def _append_one_harness_member_inter_sheet_link(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    *,
    physical_document_ids: tuple[str, str],
    symbol_source_object_id: str,
    parent_interface: _TypedHarnessInterface,
    child_interfaces: tuple[_TypedHarnessInterface, ...] = (),
    member: ResolvedHarnessMember,
    parent_net: AltiumCompiledNet,
    child_net: AltiumCompiledNet,
) -> None:
    parent_document_id, child_document_id = physical_document_ids
    link_id = _compiled_inter_sheet_link_id(
        parent_document_id,
        child_document_id,
        symbol_source_object_id,
        ".".join((parent_interface.name, *(segment.value for segment in member.path))),
    )
    _append_inter_sheet_link_net(
        link_nets,
        link_id_counts,
        link_id=link_id,
        name=_resolved_harness_member_values(member)[-1],
        parent_net=parent_net,
        child_net=child_net,
        aliases=(parent_interface.name,),
        hierarchy_parent_entry_name=parent_interface.name,
        hierarchy_link_name=parent_interface.name,
        hierarchy_child_name=(
            child_interfaces[0].name if child_interfaces else child_net.name
        ),
        hierarchy_child_object_ids=tuple(
            dict.fromkeys(
                value
                for child_interface in child_interfaces
                for value in (
                    child_interface.object_id,
                    *_hierarchy_endpoint_object_ids(
                        child_net,
                        roles=frozenset({"harness_entry", "harness_port", "port"}),
                        name=child_interface.name,
                        source_occurrence_id=child_interface.source_occurrence_id,
                    ),
                )
                if value
            )
        ),
        hierarchy_match_kind="harness_name",
    )


def _typed_sheet_entry_interface_for_object(
    schdoc: "AltiumSchDoc",
    symbol_info: "SchSheetSymbolInfo",
    entry: object,
) -> _TypedHarnessInterface | None:
    compile_masks = _compiled_compile_mask_bounds(schdoc)
    for symbol_index, candidate_symbol in _compiler_source_rows(
        _compiler_sheet_symbol_sources(schdoc)
    ):
        if candidate_symbol.record is not symbol_info.record:
            continue
        for entry_index, candidate_entry in _compiler_entry_rows(
            candidate_symbol, candidate_symbol.entries
        ):
            if candidate_entry is not entry:
                continue
            type_name = str(getattr(candidate_entry, "harness_type", "") or "")
            if not type_name or _sheet_entry_is_compile_masked(
                candidate_entry, candidate_symbol, compile_masks
            ):
                return None
            return _TypedHarnessInterface(
                name=_inter_sheet_entry_display_name(candidate_entry),
                type_name=type_name,
                role="sheet_entry",
                object_id=str(getattr(candidate_entry, "unique_id", "") or ""),
                source_occurrence_id=(
                    f"sheet_symbol:{symbol_index}:entry:{entry_index}"
                ),
            )
    return None


def _repeat_harness_port_name(entry_name: str, repeat_value: int | None) -> str:
    if repeat_value is None:
        return entry_name
    return _parse_entry_repeat(entry_name) or entry_name


def _find_exact_physical_harness_member_net(
    physical_nets: Sequence[AltiumCompiledNet],
    *,
    physical_document_id: str,
    interface: _TypedHarnessInterface,
    member: ResolvedHarnessMember,
    repeat_value: int | None = None,
) -> AltiumCompiledNet | None:
    matches = tuple(
        net
        for net in physical_nets
        if net.scope == "physical_local"
        and physical_document_id in net.physical_document_ids
        and any(
            _compiled_harness_endpoint_matches(
                endpoint, interface, member, repeat_value=repeat_value
            )
            for endpoint in net.endpoints
        )
    )
    return matches[0] if len(matches) == 1 else None


def _compiled_harness_endpoint_matches(
    endpoint: AltiumCompiledNetEndpoint,
    interface: _TypedHarnessInterface,
    member: ResolvedHarnessMember,
    *,
    repeat_value: int | None,
) -> bool:
    return (
        endpoint.role == "harness_entry"
        and endpoint._source_occurrence_id == interface.source_occurrence_id
        and _harness_path_key(endpoint._harness_entries_path)
        == _harness_path_key(_runtime_harness_entries_path(member))
        and endpoint._bus_signal_index == member.bus_signal_index
        and endpoint._repeat_value == repeat_value
    )


def _append_regular_inter_sheet_link(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    stats: dict[str, int],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    entry_name: str,
    entry_uid: str = "",
    entry_occurrence_id: str = "",
    child_ports_by_name: Mapping[str, tuple[object, ...]],
) -> None:
    repeat_value = child_document._managed_repeat_channel_value
    parsed_repeat_name = _parse_entry_repeat(entry_name)
    inner_port = parsed_repeat_name if repeat_value is not None else None
    bus_members = _parse_bus_range(entry_name)
    match_name = inner_port if inner_port is not None else entry_name
    stats["candidate_count"] += 1
    if bus_members:
        _append_bus_member_inter_sheet_links(
            link_nets,
            link_id_counts,
            stats,
            physical_nets=physical_nets,
            parent_document=parent_document,
            child_document=child_document,
            symbol=symbol,
            entry_name=entry_name,
            entry_uid=entry_uid,
            entry_occurrence_id=entry_occurrence_id,
            bus_members=bus_members,
            child_ports=child_ports_by_name.get(
                dotnet_ordinal_ignore_case_key(entry_name), ()
            ),
        )
        return

    child_ports = child_ports_by_name.get(
        dotnet_ordinal_ignore_case_key(match_name), ()
    )
    if not child_ports:
        stats["unmatched_candidate_count"] += 1
        return

    parent_entry_name, parent_net = _regular_inter_sheet_parent_net(
        physical_nets,
        parent_document=parent_document,
        child_document=child_document,
        symbol=symbol,
        entry_name=entry_name,
        entry_uid=entry_uid,
        inner_port=inner_port,
    )
    child_nets = _dedupe_child_port_nets(
        physical_nets,
        child_document=child_document,
        child_ports=child_ports,
        match_name=match_name,
    )
    if (
        parent_net is None
        or not _compiled_net_is_scalar_inter_sheet_candidate(parent_net)
        or not child_nets
    ):
        stats["unmatched_candidate_count"] += 1
        return
    _append_child_port_inter_sheet_links(
        link_nets,
        link_id_counts,
        stats,
        parent_document=parent_document,
        child_document=child_document,
        symbol=symbol,
        parent_source_entry_name=entry_name,
        parent_entry_name=parent_entry_name,
        match_name=match_name,
        parent_net=parent_net,
        child_nets=child_nets,
    )


def _bus_member_endpoint_matches_entry(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    entry_name: str,
    entry_uid: str,
    entry_occurrence_id: str,
    bus_signal_index: int,
) -> bool:
    if endpoint.role != "bus_member" or dotnet_ordinal_ignore_case_key(
        endpoint.name
    ) != dotnet_ordinal_ignore_case_key(entry_name):
        return False
    if not entry_occurrence_id:
        return False
    if endpoint._source_occurrence_id != entry_occurrence_id:
        return False
    if endpoint._bus_signal_index != bus_signal_index:
        return False
    if entry_uid:
        return dotnet_ordinal_ignore_case_key(
            endpoint.object_id
        ) == dotnet_ordinal_ignore_case_key(entry_uid)
    return True


def _bus_member_endpoint_matches_port(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    port_name: str,
    port_occurrence_id: str,
    bus_signal_index: int,
) -> bool:
    if (
        endpoint.role != "bus_member"
        or dotnet_ordinal_ignore_case_key(endpoint.name)
        != dotnet_ordinal_ignore_case_key(port_name)
        or endpoint._bus_signal_index != bus_signal_index
    ):
        return False
    return bool(port_occurrence_id) and (
        endpoint._source_occurrence_id == port_occurrence_id
    )


def _exact_bus_member_net(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    endpoint_matches: Callable[[AltiumCompiledNetEndpoint], bool],
) -> AltiumCompiledNet | None:
    matches = [
        net
        for net in physical_nets
        if physical_document_id in net.physical_document_ids
        and any(endpoint_matches(endpoint) for endpoint in net.endpoints)
    ]
    return matches[0] if len(matches) == 1 else None


def _with_bus_member_net_label_evidence(
    net: AltiumCompiledNet,
    *,
    port_occurrence_id: str,
    port_name: str,
    bus_signal_index: int,
) -> AltiumCompiledNet:
    source_endpoints = _bus_member_net_label_sources(
        net,
        port_occurrence_id=port_occurrence_id,
        port_name=port_name,
        bus_signal_index=bus_signal_index,
    )
    projected = _project_bus_member_net_label_endpoints(net, source_endpoints)
    if not projected:
        return net
    projected_items = tuple(
        _compiled_item_from_endpoint(
            endpoint,
            physical_document_id=net.physical_document_ids[0],
        )
        for endpoint in projected
    )
    return replace(
        net,
        endpoint_ids=(*net.endpoint_ids, *(endpoint.id for endpoint in projected)),
        endpoints=(*net.endpoints, *projected),
        item_ids=(*net.item_ids, *(item.id for item in projected_items)),
        items=(*net.items, *projected_items),
    )


def _bus_member_net_label_sources(
    net: AltiumCompiledNet,
    *,
    port_occurrence_id: str,
    port_name: str,
    bus_signal_index: int,
) -> tuple[AltiumCompiledNetEndpoint, ...]:
    return tuple(
        endpoint
        for endpoint in net.endpoints
        if _bus_member_endpoint_is_label_source(
            endpoint,
            port_occurrence_id=port_occurrence_id,
            port_name=port_name,
            bus_signal_index=bus_signal_index,
        )
    )


def _project_bus_member_net_label_endpoints(
    net: AltiumCompiledNet,
    source_endpoints: Sequence[AltiumCompiledNetEndpoint],
) -> tuple[AltiumCompiledNetEndpoint, ...]:
    existing = {
        (
            dotnet_ordinal_ignore_case_key(endpoint.object_id),
            dotnet_ordinal_ignore_case_key(endpoint.name),
        )
        for endpoint in net.endpoints
        if endpoint.role == "net_label"
    }
    return tuple(
        replace(endpoint, id=f"{endpoint.id}:source:net_label", role="net_label")
        for endpoint in source_endpoints
        if (
            dotnet_ordinal_ignore_case_key(endpoint.object_id),
            dotnet_ordinal_ignore_case_key(endpoint.name),
        )
        not in existing
    )


def _bus_member_endpoint_is_label_source(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    port_occurrence_id: str,
    port_name: str,
    bus_signal_index: int,
) -> bool:
    return bool(
        endpoint.role == "bus_member"
        and endpoint._source_occurrence_id != port_occurrence_id
        and not endpoint._source_occurrence_id.startswith("port:")
        and endpoint._bus_signal_index == bus_signal_index
        and dotnet_ordinal_ignore_case_key(endpoint.name)
        == dotnet_ordinal_ignore_case_key(port_name)
    )


def _append_bus_member_inter_sheet_links(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    stats: dict[str, int],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    entry_name: str,
    entry_uid: str,
    entry_occurrence_id: str,
    bus_members: tuple[str, ...],
    child_ports: tuple[object, ...],
) -> None:
    emitted_count = 0
    for bus_signal_index, member_name in enumerate(bus_members):
        parent_net = _exact_bus_member_net(
            physical_nets,
            physical_document_id=parent_document.id,
            endpoint_matches=lambda endpoint: _bus_member_endpoint_matches_entry(
                endpoint,
                entry_name=entry_name,
                entry_uid=entry_uid,
                entry_occurrence_id=entry_occurrence_id,
                bus_signal_index=bus_signal_index,
            ),
        )
        if parent_net is None:
            continue
        for child_port in child_ports:
            port_name = str(getattr(child_port, "name", "") or entry_name)
            port_occurrence_id = str(
                getattr(child_port, "source_occurrence_id", "") or ""
            )
            child_net = _exact_bus_member_net(
                physical_nets,
                physical_document_id=child_document.id,
                endpoint_matches=lambda endpoint: _bus_member_endpoint_matches_port(
                    endpoint,
                    port_name=port_name,
                    port_occurrence_id=port_occurrence_id,
                    bus_signal_index=bus_signal_index,
                ),
            )
            if child_net is None:
                continue
            child_net = _with_bus_member_net_label_evidence(
                child_net,
                port_occurrence_id=port_occurrence_id,
                port_name=port_name,
                bus_signal_index=bus_signal_index,
            )
            base_link_id = _compiled_inter_sheet_link_id(
                parent_document.id,
                child_document.id,
                symbol.source_object_id,
                member_name,
            ).replace(":link:", ":bus_member_link:", 1)
            _append_inter_sheet_link_net(
                link_nets,
                link_id_counts,
                link_id=base_link_id,
                name=member_name,
                parent_net=parent_net,
                child_net=child_net,
                aliases=(entry_name,),
                hierarchy_parent_entry_name=entry_name,
                hierarchy_link_name=member_name,
                hierarchy_child_name=port_name,
                hierarchy_child_object_ids=_hierarchy_endpoint_object_ids(
                    child_net,
                    roles=frozenset({"bus_member", "port"}),
                    name=port_name,
                    source_occurrence_id=port_occurrence_id,
                ),
                hierarchy_match_kind="name",
            )
            emitted_count += 1
            stats["matched_count"] += 1
    if emitted_count == 0:
        stats["unmatched_candidate_count"] += 1


def _regular_inter_sheet_parent_net(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    entry_name: str,
    entry_uid: str,
    inner_port: str | None,
) -> tuple[str, AltiumCompiledNet | None]:
    sheet_symbol_uid = _inter_sheet_symbol_occurrence_key(symbol)
    parent_entry_name = (
        f"{inner_port}{child_document._managed_repeat_channel_value}"
        if inner_port is not None
        else entry_name
    )
    parent_net = _find_physical_net_with_sheet_entry(
        physical_nets,
        physical_document_id=parent_document.id,
        sheet_symbol_uid=sheet_symbol_uid,
        entry_name=entry_name,
        entry_uid=entry_uid,
    )
    if parent_net is not None and inner_port is not None:
        parent_net = (
            _repeat_structural_parent_net(
                physical_nets,
                physical_document_id=parent_document.id,
                inner_port=inner_port,
                resolved_name=parent_entry_name,
            )
            or parent_net
        )
    return parent_entry_name, parent_net


def _repeat_structural_parent_net(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    inner_port: str,
    resolved_name: str,
) -> AltiumCompiledNet | None:
    inner_key = dotnet_ordinal_ignore_case_key(inner_port)
    resolved_key = dotnet_ordinal_ignore_case_key(resolved_name)
    member_index = _repeat_structural_member_index(inner_port, resolved_name)
    if member_index is None:
        return None
    matches = [
        net
        for net in physical_nets
        if _is_repeat_structural_parent_candidate(
            net,
            physical_document_id=physical_document_id,
            inner_key=inner_key,
            resolved_key=resolved_key,
            member_index=member_index,
        )
    ]
    return matches[0] if len(matches) == 1 else None


def _repeat_structural_member_index(inner_port: str, resolved_name: str) -> int | None:
    suffix = resolved_name[len(inner_port) :]
    if not suffix.isascii() or not suffix.isdigit():
        return None
    return int(suffix)


def _is_repeat_structural_parent_candidate(
    net: AltiumCompiledNet,
    *,
    physical_document_id: str,
    inner_key: str,
    resolved_key: str,
    member_index: int,
) -> bool:
    if physical_document_id not in net.physical_document_ids:
        return False
    if not _compiled_net_is_scalar_inter_sheet_candidate(net):
        return False
    net_names = (net.name, *_compiled_net_label_names(net))
    if not any(
        dotnet_ordinal_ignore_case_key(name) == resolved_key for name in net_names
    ):
        return False
    return any(
        _endpoint_contains_repeat_structural_member(
            endpoint,
            inner_key=inner_key,
            member_index=member_index,
        )
        for endpoint in net.endpoints
    )


def _endpoint_contains_repeat_structural_member(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    inner_key: str,
    member_index: int,
) -> bool:
    if endpoint.role not in {"bus_member", "bus_entry_member"}:
        return False
    bus_range = _parse_managed_bus_range(endpoint.name)
    if bus_range is None:
        return False
    if dotnet_ordinal_ignore_case_key(bus_range.prefix) != inner_key:
        return False
    return (
        min(bus_range.start, bus_range.end)
        <= member_index
        <= max(
            bus_range.start,
            bus_range.end,
        )
    )


def _inter_sheet_symbol_occurrence_key(symbol: AltiumCompiledSheetSymbol) -> str:
    if symbol.source_object_id:
        return symbol.source_object_id
    marker = ":sheet_symbol:sheet_symbol:"
    _prefix, separator, ordinal = symbol.id.rpartition(marker)
    return f"sheet_symbol:{ordinal}" if separator and ordinal.isdecimal() else ""


def _append_child_port_inter_sheet_links(
    link_nets: list[AltiumCompiledNet],
    link_id_counts: dict[str, int],
    stats: dict[str, int],
    *,
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    parent_source_entry_name: str,
    parent_entry_name: str,
    match_name: str,
    parent_net: AltiumCompiledNet,
    child_nets: tuple[AltiumCompiledNet, ...],
) -> None:
    base_link_id = _compiled_inter_sheet_link_id(
        parent_document.id,
        child_document.id,
        symbol.source_object_id,
        parent_entry_name,
    )
    for match_index, child_net in enumerate(child_nets):
        link_id = (
            base_link_id if match_index == 0 else f"{base_link_id}:match:{match_index}"
        )
        _append_inter_sheet_link_net(
            link_nets,
            link_id_counts,
            link_id=link_id,
            name=parent_entry_name,
            parent_net=parent_net,
            child_net=child_net,
            aliases=(match_name,) if match_name != parent_entry_name else (),
            hierarchy_parent_entry_name=parent_source_entry_name,
            hierarchy_link_name=parent_entry_name,
            hierarchy_child_name=match_name,
            hierarchy_child_object_ids=_hierarchy_endpoint_object_ids(
                child_net,
                roles=frozenset({"port"}),
                name=match_name,
            ),
            hierarchy_match_kind="name",
        )
        stats["matched_count"] += 1


def _dedupe_child_port_nets(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    child_document: AltiumCompiledPhysicalDocument,
    child_ports: tuple[object, ...],
    match_name: str,
) -> tuple[AltiumCompiledNet, ...]:
    return _unique_scalar_inter_sheet_nets(
        chain(
            _child_port_net_candidates(
                physical_nets,
                child_document=child_document,
                child_ports=child_ports,
                match_name=match_name,
            ),
            _named_child_port_net_candidates(
                physical_nets,
                child_document=child_document,
                match_name=match_name,
            ),
        )
    )


def _child_port_net_candidates(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    child_document: AltiumCompiledPhysicalDocument,
    child_ports: tuple[object, ...],
    match_name: str,
) -> Iterable[AltiumCompiledNet]:
    for child_port in child_ports:
        child_net = _find_physical_net_with_port(
            physical_nets,
            physical_document_id=child_document.id,
            port_uid=getattr(child_port, "unique_id", "") or "",
            port_name=match_name,
        )
        if child_net is not None:
            yield child_net


def _named_child_port_net_candidates(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    child_document: AltiumCompiledPhysicalDocument,
    match_name: str,
) -> Iterable[AltiumCompiledNet]:
    for child_net in physical_nets:
        if (
            child_document.id in child_net.physical_document_ids
            and _compiled_net_has_port_name(child_net, match_name)
        ):
            yield child_net


def _unique_scalar_inter_sheet_nets(
    candidates: Iterable[AltiumCompiledNet],
) -> tuple[AltiumCompiledNet, ...]:
    matches: list[AltiumCompiledNet] = []
    seen_ids: set[str] = set()
    for child_net in candidates:
        if (
            child_net.id in seen_ids
            or not _compiled_net_is_scalar_inter_sheet_candidate(child_net)
        ):
            continue
        seen_ids.add(child_net.id)
        matches.append(child_net)
    return tuple(matches)


def _compiled_net_is_scalar_inter_sheet_candidate(net: AltiumCompiledNet) -> bool:
    has_scalar_bus_member = any(
        endpoint.role in {"bus_member", "bus_entry_member"}
        for endpoint in net.endpoints
    )
    return (not net._contains_bus or has_scalar_bus_member) and not any(
        endpoint.role in {"bus", "harness_port", "signal_harness"}
        for endpoint in net.endpoints
    )


def _unique_compiled_sheet_symbols_by_id(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> dict[str, AltiumCompiledSheetSymbol]:
    result: dict[str, AltiumCompiledSheetSymbol] = {}
    for symbol in sheet_symbols:
        if symbol.id in result:
            raise ValueError("duplicate compiled sheet-symbol identity")
        result[symbol.id] = symbol
    return result


def _round_ratio_half_away_from_zero(numerator: int, denominator: int) -> int:
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def _format_fixed_unit_value(
    numerator: int,
    denominator: int,
    decimal_places: int,
    suffix: str,
) -> str:
    scale = 10**decimal_places
    rounded = _round_ratio_half_away_from_zero(numerator * scale, denominator)
    sign = "-" if rounded < 0 else ""
    whole, fractional = divmod(abs(rounded), scale)
    if not fractional:
        return f"{sign}{whole}{suffix}"
    fractional_text = f"{fractional:0{decimal_places}d}".rstrip("0")
    return f"{sign}{whole}.{fractional_text}{suffix}"


def _managed_interface_location(
    point: RootPoint,
    *,
    display_unit: int,
) -> str:
    def coordinate(whole: int, fraction: int) -> str:
        composed = whole * ALTIUM_COORD_SCALE + fraction
        if display_unit == 0:
            return _format_fixed_unit_value(composed, 10_000, 3, "mil")
        return _format_fixed_unit_value(composed * 254, 100_000_000, 2, "mm")

    return f"{coordinate(point[0], point[2])},{coordinate(point[1], point[3])}"


def _interface_name(name: str, *, repeat_owner: bool) -> str:
    if not repeat_owner:
        return name
    repeat_name = _parse_entry_repeat(name)
    return repeat_name if repeat_name is not None else name


def _port_is_compile_masked(
    port: "SchPortInfo",
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> bool:
    if not compile_masks:
        return False
    points = tuple(port._precise_connection_points)
    return bool(points) and any(
        _compiled_mask_contains_every_point(mask, points) for mask in compile_masks
    )


def _sheet_entry_is_compile_masked(
    entry: object,
    symbol_info: "SchSheetSymbolInfo",
    compile_masks: Sequence[tuple[int, int, int, int]],
) -> bool:
    if not compile_masks:
        return False
    point = _sheet_entry_precise_connection_point(symbol_info, entry)
    return _compiled_point_inside_any_mask(point, compile_masks)


def _ports_by_logical_id(
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
    raw_ports_by_logical_id: dict[str, tuple["SchPortInfo", ...]],
    compile_masks_by_logical_id: Mapping[str, tuple[tuple[int, int, int, int], ...]],
) -> tuple[
    dict[str, tuple["SchPortInfo", ...]],
    dict[str, tuple["SchPortInfo", ...]],
]:
    all_ports: dict[str, tuple[SchPortInfo, ...]] = {}
    active_ports: dict[str, tuple[SchPortInfo, ...]] = {}
    for logical_id, schdoc in schdoc_by_logical_id.items():
        masks = compile_masks_by_logical_id.get(logical_id, ())
        raw_ports = raw_ports_by_logical_id.get(logical_id)
        if raw_ports is None:
            raw_ports = tuple(schdoc.get_ports())
            raw_ports_by_logical_id[logical_id] = raw_ports
        document_ports = tuple(raw_ports)
        all_ports[logical_id] = document_ports
        active_ports[logical_id] = tuple(
            port for port in document_ports if not _port_is_compile_masked(port, masks)
        )
    return all_ports, active_ports


def _managed_interface_bus_parts(name: str) -> tuple[int, str, str] | None:
    open_bracket = name.find("[")
    if open_bracket < 0:
        return None
    close_bracket = name.find("]", open_bracket + 1)
    separator = name.find("..", open_bracket + 1)
    if close_bracket < 0 or separator < 0 or separator >= close_bracket:
        return None
    left = dotnet_trim(name[open_bracket + 1 : separator])
    right = dotnet_trim(name[separator + 2 : close_bracket])
    if not left or not right:
        return None
    return open_bracket, left, right


def _managed_interface_int32(value: str) -> int | None:
    if not re.fullmatch(r"[+-]?[0-9]+", value):
        return None
    parsed = int(value)
    return parsed if -(2**31) <= parsed < 2**31 else None


def _managed_interface_bus_range(
    name: str,
) -> tuple[str, int | None, int | None] | None:
    parts = _managed_interface_bus_parts(name)
    if parts is None:
        return None
    open_bracket, left, right = parts
    start = _managed_interface_int32(left)
    end = _managed_interface_int32(right)
    if (start is not None and start < 0) or (end is not None and end < 0):
        return None
    return name[:open_bracket], start, end


def _managed_port_collector_matches(port_name: str, entry_name: str) -> bool:
    if dotnet_ordinal_ignore_case_key(port_name) == dotnet_ordinal_ignore_case_key(
        entry_name
    ):
        return True
    parsed = _managed_interface_bus_range(port_name)
    if parsed is None:
        return False
    prefix, start, end = parsed
    if start is None or end is None:
        return dotnet_ordinal_ignore_case_key(prefix) == dotnet_ordinal_ignore_case_key(
            entry_name
        )
    candidate_prefix = entry_name[: len(prefix)]
    suffix = entry_name[len(prefix) :]
    if dotnet_ordinal_ignore_case_key(prefix) != dotnet_ordinal_ignore_case_key(
        candidate_prefix
    ):
        return False
    if not suffix.isascii() or not suffix.isdigit():
        return False
    if suffix != "0" and suffix.startswith("0"):
        return False
    member = int(suffix)
    return min(start, end) <= member <= max(start, end)


def _entry_source_id(entry: object) -> str | None:
    value = str(getattr(entry, "unique_id", "") or "")
    return value or None


def _port_source_id(port: "SchPortInfo") -> str | None:
    value = str(getattr(port, "unique_id", "") or "")
    return value or None


def _interface_child_ids(
    symbol: AltiumCompiledSheetSymbol,
) -> tuple[str, ...]:
    if symbol._managed_interface_child_logical_document_ids:
        return symbol._managed_interface_child_logical_document_ids
    if symbol._managed_child_logical_document_ids:
        return symbol._managed_child_logical_document_ids
    if symbol.child_logical_document_id is not None:
        return (symbol.child_logical_document_id,)
    return ()


def _retained_interface_parent_symbols(
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> dict[str, tuple[AltiumCompiledSheetSymbol, ...]]:
    symbol_by_id = {symbol.id: symbol for symbol in sheet_symbols}
    rows: defaultdict[str, list[AltiumCompiledSheetSymbol]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for document in physical_documents:
        symbol_id = document.parent_sheet_symbol_id
        if document.parent_id is None or symbol_id is None:
            continue
        relation = (symbol_id, document.logical_document_id)
        if relation in seen:
            continue
        seen.add(relation)
        if (symbol := symbol_by_id.get(symbol_id)) is not None:
            rows[document.logical_document_id].append(symbol)
    return {child_id: tuple(symbols) for child_id, symbols in rows.items()}


def _sorted_interface_entries(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
) -> list[tuple[AltiumCompiledSheetSymbol, "AltiumSchSheetEntry", str]]:
    rows = [
        (
            symbol,
            entry,
            _interface_name(
                _inter_sheet_entry_display_name(entry),
                repeat_owner=symbol.is_repeat,
            ),
        )
        for symbol in sheet_symbols
        if (symbol_info := sheet_symbol_info_by_id.get(symbol.id)) is not None
        for entry in symbol_info.entries
    ]
    rows.sort(
        key=lambda row: dotnet_ordinal_ignore_case_sort_key(
            _inter_sheet_entry_display_name(row[1])
        )
    )
    return rows


def _entry_has_interface_port_match(
    symbol: AltiumCompiledSheetSymbol,
    match_name: str,
    all_ports: Mapping[str, tuple["SchPortInfo", ...]],
) -> bool:
    return any(
        _managed_port_collector_matches(port.name, match_name)
        for child_id in _interface_child_ids(symbol)
        for port in all_ports.get(child_id, ())
    )


def _unmatched_entry_diagnostics(
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
    compile_masks_by_logical_id: Mapping[str, tuple[tuple[int, int, int, int], ...]],
    main_path_logical_ids: set[str],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
    all_ports: Mapping[str, tuple["SchPortInfo", ...]],
) -> list[AltiumCompileDiagnostic]:
    diagnostics: list[AltiumCompileDiagnostic] = []
    for symbol, entry, match_name in _sorted_interface_entries(
        sheet_symbols,
        sheet_symbol_info_by_id,
    ):
        owner_schdoc = schdoc_by_logical_id.get(symbol.logical_document_id)
        if (
            symbol.logical_document_id not in main_path_logical_ids
            or owner_schdoc is None
        ):
            continue
        symbol_info = sheet_symbol_info_by_id[symbol.id]
        if _sheet_entry_is_compile_masked(
            entry,
            symbol_info,
            compile_masks_by_logical_id.get(symbol.logical_document_id, ()),
        ) or _entry_has_interface_port_match(symbol, match_name, all_ports):
            continue
        point = _sheet_entry_precise_connection_point(symbol_info, entry)
        display_unit = int(
            getattr(getattr(owner_schdoc, "sheet", None), "display_unit", 0)
        )
        name = _inter_sheet_entry_display_name(entry)
        diagnostics.append(
            AltiumCompileDiagnostic(
                severity="error",
                code="sheet_entry_not_linked_to_port",
                message=(
                    f"Sheet-Entry {name} not matched to Port at "
                    f"{_managed_interface_location(point, display_unit=display_unit)}"
                ),
                source_id=_entry_source_id(entry),
            )
        )
    return diagnostics


def _port_has_interface_entry_match(
    port: "SchPortInfo",
    parent_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
) -> bool:
    port_key = dotnet_ordinal_ignore_case_key(port.name)
    return any(
        dotnet_ordinal_ignore_case_key(
            _interface_name(
                _inter_sheet_entry_display_name(entry),
                repeat_owner=symbol.is_repeat,
            )
        )
        == port_key
        for symbol in parent_symbols
        for entry in sheet_symbol_info_by_id[symbol.id].entries
    )


def _unmatched_port_diagnostics(
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
    active_ports: Mapping[str, tuple["SchPortInfo", ...]],
    parents_by_child: Mapping[str, tuple[AltiumCompiledSheetSymbol, ...]],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
) -> list[AltiumCompileDiagnostic]:
    rows = [
        (child_id, parent_symbols, port)
        for child_id, parent_symbols in parents_by_child.items()
        for port in active_ports.get(child_id, ())
    ]
    rows.sort(key=lambda row: dotnet_ordinal_ignore_case_sort_key(row[2].name))
    diagnostics: list[AltiumCompileDiagnostic] = []
    for child_id, parent_symbols, port in rows:
        if _port_has_interface_entry_match(
            port,
            parent_symbols,
            sheet_symbol_info_by_id,
        ):
            continue
        child_schdoc = schdoc_by_logical_id[child_id]
        point = port._precise_connection_points[0]
        display_unit = int(
            getattr(getattr(child_schdoc, "sheet", None), "display_unit", 0)
        )
        diagnostics.append(
            AltiumCompileDiagnostic(
                severity="error",
                code="port_not_linked_to_sheet_symbol",
                message=(
                    f"Port {port.name} not matched to Sheet Entry at "
                    f"{_managed_interface_location(point, display_unit=display_unit)}"
                ),
                source_id=_port_source_id(port),
            )
        )
    return diagnostics


def _unmatched_interface_diagnostics(
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
    raw_ports_by_logical_id: dict[str, tuple["SchPortInfo", ...]],
) -> tuple[AltiumCompileDiagnostic, ...]:
    main_path_logical_ids = {
        document.logical_document_id for document in physical_documents
    }
    compile_masks_by_logical_id = {
        logical_id: _compiled_compile_mask_bounds(schdoc)
        for logical_id, schdoc in schdoc_by_logical_id.items()
    }
    all_ports, active_ports = _ports_by_logical_id(
        schdoc_by_logical_id,
        raw_ports_by_logical_id,
        compile_masks_by_logical_id,
    )
    parents_by_child = _retained_interface_parent_symbols(
        physical_documents,
        sheet_symbols,
    )
    return tuple(
        [
            *_unmatched_entry_diagnostics(
                schdoc_by_logical_id,
                compile_masks_by_logical_id,
                main_path_logical_ids,
                sheet_symbols,
                sheet_symbol_info_by_id,
                all_ports,
            ),
            *_unmatched_port_diagnostics(
                schdoc_by_logical_id,
                active_ports,
                parents_by_child,
                sheet_symbol_info_by_id,
            ),
        ]
    )


def _bus_entry_occurrence_ids(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: Mapping[str, "SchSheetSymbolInfo"],
    compile_masks_by_logical_id: Mapping[str, tuple[tuple[int, int, int, int], ...]],
) -> dict[int, str]:
    emitted_by_logical_id: defaultdict[str, set[str]] = defaultdict(set)
    result: dict[int, str] = {}
    for symbol in sheet_symbols:
        symbol_info = sheet_symbol_info_by_id.get(symbol.id)
        if symbol_info is None:
            continue
        symbol_uid = _inter_sheet_symbol_occurrence_key(symbol)
        for entry in symbol_info.entries:
            if _sheet_entry_is_compile_masked(
                entry,
                symbol_info,
                compile_masks_by_logical_id.get(symbol.logical_document_id, ()),
            ):
                continue
            entry_name = _inter_sheet_entry_display_name(entry)
            if not entry_name or not _parse_bus_range(entry_name):
                continue
            result[id(entry)] = _unique_sheet_entry_element_id(
                f"{symbol_uid}_{entry_name}",
                emitted_by_logical_id[symbol.logical_document_id],
            )
    return result


def _build_inter_sheet_link_nets(
    schdocs: Sequence["AltiumSchDoc"],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: dict[str, "SchSheetSymbolInfo"],
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    effective_hierarchy_mode: str,
    project_base_dir: Path | None,
) -> tuple[
    tuple[AltiumCompiledNet, ...],
    dict[str, object],
    tuple[AltiumCompileDiagnostic, ...],
]:
    """Create parent/child bridge rows from physical sheet-entry and port matches."""
    stats: dict[str, int] = {
        "candidate_count": 0,
        "matched_count": 0,
        "unmatched_candidate_count": 0,
        "harness_candidate_count": 0,
        "harness_matched_count": 0,
        "harness_endpoint_only_count": 0,
    }
    if effective_hierarchy_mode not in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
        return (
            (),
            {
                **stats,
                "mode": "physical_tree_bridge_candidates",
                "unmatched_candidate_policy": "hierarchical_logical_interface_validation",
            },
            (),
        )
    schdoc_by_logical_id = _schdocs_by_logical_id(
        schdocs,
        project_base_dir=project_base_dir,
    )
    schdoc_by_source_ref, schdoc_by_file_name = _schdoc_reference_maps(
        schdocs,
        project_base_dir=project_base_dir,
    )
    harness_candidate_groups = _project_harness_definition_candidate_groups(schdocs)
    harness_definitions = resolve_project_harness_definitions(harness_candidate_groups)
    local_harness_definitions = {
        id(schdoc): resolve_harness_definitions(group)
        for schdoc, group in zip(schdocs, harness_candidate_groups, strict=True)
    }
    inferred_port_harness_types = _project_inferred_port_harness_types(
        schdocs, schdoc_by_source_ref, schdoc_by_file_name
    )
    physical_by_id = {document.id: document for document in physical_documents}
    symbol_by_id = _unique_compiled_sheet_symbols_by_id(sheet_symbols)
    link_nets: list[AltiumCompiledNet] = []
    link_id_counts: dict[str, int] = {}
    diagnostics: list[AltiumCompileDiagnostic] = []
    child_ports_by_logical_id: dict[
        str, dict[str, tuple[_InterSheetPortOccurrence, ...]]
    ] = {}
    raw_ports_by_logical_id: dict[str, tuple[SchPortInfo, ...]] = {}
    compile_masks_by_logical_id = {
        logical_id: _compiled_compile_mask_bounds(schdoc)
        for logical_id, schdoc in schdoc_by_logical_id.items()
    }
    bus_entry_occurrence_ids = _bus_entry_occurrence_ids(
        sheet_symbols,
        sheet_symbol_info_by_id,
        compile_masks_by_logical_id,
    )

    for child_document in physical_documents:
        if (
            child_document.parent_id is None
            or child_document.parent_sheet_symbol_id is None
        ):
            continue
        parent_document = physical_by_id.get(child_document.parent_id)
        symbol = symbol_by_id.get(child_document.parent_sheet_symbol_id)
        symbol_info = sheet_symbol_info_by_id.get(child_document.parent_sheet_symbol_id)
        child_schdoc = schdoc_by_logical_id.get(child_document.logical_document_id)
        parent_schdoc = (
            schdoc_by_logical_id.get(parent_document.logical_document_id)
            if parent_document is not None
            else None
        )
        if (
            parent_document is None
            or symbol is None
            or symbol_info is None
            or child_schdoc is None
            or parent_schdoc is None
        ):
            continue

        child_ports_by_name = _child_ports_by_name_for_inter_sheet_linking(
            child_ports_by_logical_id,
            child_document,
            child_schdoc,
            raw_ports_by_logical_id,
        )
        for entry in symbol_info.entries:
            entry_name = _inter_sheet_entry_display_name(entry)
            if not entry_name:
                continue
            entry_uid = str(getattr(entry, "unique_id", "") or "")
            if (
                _find_physical_net_with_sheet_entry(
                    physical_nets,
                    physical_document_id=parent_document.id,
                    sheet_symbol_uid=_inter_sheet_symbol_occurrence_key(symbol),
                    entry_name=entry_name,
                    entry_uid=entry_uid,
                )
                is None
            ):
                continue

            harness_type = str(getattr(entry, "harness_type", "") or "")
            if harness_type:
                _append_harness_inter_sheet_links(
                    link_nets,
                    link_id_counts,
                    stats,
                    physical_nets=physical_nets,
                    parent_document=parent_document,
                    child_document=child_document,
                    symbol=symbol,
                    symbol_info=symbol_info,
                    entry=entry,
                    parent_schdoc=parent_schdoc,
                    child_schdoc=child_schdoc,
                    definitions=harness_definitions,
                    child_local_definitions=local_harness_definitions.get(
                        id(child_schdoc), {}
                    ),
                    child_inferred_port_types=inferred_port_harness_types.get(
                        id(child_schdoc), {}
                    ),
                )
                continue

            _append_regular_inter_sheet_link(
                link_nets,
                link_id_counts,
                stats,
                physical_nets=physical_nets,
                parent_document=parent_document,
                child_document=child_document,
                symbol=symbol,
                entry_name=entry_name,
                entry_uid=entry_uid,
                entry_occurrence_id=bus_entry_occurrence_ids.get(id(entry), ""),
                child_ports_by_name=child_ports_by_name,
            )
    diagnostics.extend(
        _unmatched_interface_diagnostics(
            schdoc_by_logical_id,
            physical_documents,
            sheet_symbols,
            sheet_symbol_info_by_id,
            raw_ports_by_logical_id,
        )
    )

    return (
        tuple(link_nets),
        {
            **stats,
            "mode": "physical_tree_bridge_candidates",
            "unmatched_candidate_policy": "hierarchical_logical_interface_validation",
        },
        tuple(diagnostics),
    )


def _dedupe_compiled_endpoints(
    endpoints: list[AltiumCompiledNetEndpoint],
) -> tuple[AltiumCompiledNetEndpoint, ...]:
    seen: set[tuple[object, ...]] = set()
    result: list[AltiumCompiledNetEndpoint] = []
    for endpoint in endpoints:
        key = (
            endpoint.id,
            endpoint.role,
            endpoint.element_id,
            endpoint.object_id,
            endpoint.name,
            endpoint.parent_id,
            endpoint.designator,
            endpoint.pin,
            endpoint.pin_name,
            endpoint.connection_point,
            endpoint._source_occurrence_id,
            endpoint._bus_signal_index,
            endpoint._harness_entries_path,
            endpoint._repeat_value,
            endpoint._harness_type_name,
            endpoint._harness_interface_name,
            endpoint._harness_type_inferred,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(endpoint)
    return tuple(result)


def _dedupe_compiled_terminals(
    terminals: list[AltiumCompiledNetTerminal],
) -> tuple[AltiumCompiledNetTerminal, ...]:
    seen: set[tuple[str, ...]] = set()
    result: list[AltiumCompiledNetTerminal] = []
    for terminal in terminals:
        pin_source_uid = str(terminal._source_pin_uid or "")
        if pin_source_uid or "?" in terminal.designator:
            key = (
                "source",
                terminal.id,
                str(terminal._source_component_uid or ""),
                pin_source_uid,
            )
        else:
            source_net_id, _separator, _terminal_selector = terminal.id.rpartition(
                ":terminal:"
            )
            key = (
                "uidless_physical_pin",
                source_net_id,
                terminal.designator,
                terminal.pin,
                terminal.pin_name,
                str(terminal._source_owner_part_id),
            )
        if key in seen:
            continue
        seen.add(key)
        result.append(terminal)
    return tuple(result)


def _dedupe_compiled_items(
    items: list[AltiumCompiledNetItem],
) -> tuple[AltiumCompiledNetItem, ...]:
    seen: set[str] = set()
    result: list[AltiumCompiledNetItem] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        result.append(item)
    return tuple(result)


def _dedupe_compiled_net_values(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


_compiled_case_insensitive_key = dotnet_ordinal_ignore_case_key


class _CompiledSourceMergeOrder:
    """Track source-net order from merge master/absorbed events."""

    def __init__(self, source_ids: Iterable[str]) -> None:
        source_ids_tuple = tuple(source_ids)
        self._parent = {source_id: source_id for source_id in source_ids_tuple}
        self._sequence = {source_id: [source_id] for source_id in source_ids_tuple}

    def _find(self, source_id: str) -> str:
        parent = self._parent[source_id]
        if parent != source_id:
            parent = self._find(parent)
            self._parent[source_id] = parent
        return parent

    def merge(self, *, master_id: str, absorbed_id: str) -> None:
        master_root = self._find(master_id)
        absorbed_root = self._find(absorbed_id)
        if master_root == absorbed_root:
            return
        self._sequence[master_root].extend(self._sequence[absorbed_root])
        self._parent[absorbed_root] = master_root

    def order_index(self) -> dict[str, int]:
        result = {}
        for source_id in self._parent:
            root = self._find(source_id)
            for index, ordered_source_id in enumerate(self._sequence[root]):
                result[ordered_source_id] = index
        return result


def _compiled_net_has_endpoint_role(net: AltiumCompiledNet, role: str) -> bool:
    return any(endpoint.role == role for endpoint in net.endpoints)


def _compiled_net_power_names(net: AltiumCompiledNet) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            endpoint.name
            for endpoint in net.endpoints
            if endpoint.role == "power_port" and endpoint.name
        )
    )


def _compiled_net_port_names(net: AltiumCompiledNet) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            endpoint.name
            for endpoint in net.endpoints
            if endpoint.role == "port" and endpoint.name
        )
    )


def _compiled_net_cross_sheet_connector_names(
    net: AltiumCompiledNet,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            endpoint.name
            for endpoint in net.endpoints
            if endpoint.role == "offsheet_connector" and endpoint.name
        )
    )


def _compiled_net_port_merge_list_names(net: AltiumCompiledNet) -> tuple[str, ...]:
    return _dedupe_compiled_net_values(
        [
            *_compiled_net_port_names(net),
            *_compiled_net_cross_sheet_connector_names(net),
        ]
    )


@dataclass(frozen=True, slots=True)
class _CompiledPortRelativeEntry:
    key: str
    name: str
    has_cross_sheet: bool
    parent_id: str
    object_kind: int
    logical_designator: str


def _compiled_scalar_merge_source_entries(
    net: AltiumCompiledNet,
    object_kind_by_source_kind: Mapping[str, int],
    *,
    cross_sheet_kind: str = "",
) -> tuple[_CompiledPortRelativeEntry, ...]:
    return tuple(
        _CompiledPortRelativeEntry(
            key=_compiled_case_insensitive_key(source_name),
            name=source_name,
            has_cross_sheet=source_kind == cross_sheet_kind,
            parent_id="",
            object_kind=object_kind,
            logical_designator="",
        )
        for source_kind, source_name in net._scalar_merge_name_sources
        if source_name
        and (object_kind := object_kind_by_source_kind.get(source_kind)) is not None
    )


def _compiled_append_scalar_merge_source_entries(
    entries_by_key: dict[str, _CompiledPortRelativeEntry],
    net: AltiumCompiledNet,
    object_kind_by_source_kind: Mapping[str, int],
    *,
    cross_sheet_kind: str = "",
) -> None:
    for candidate in _compiled_scalar_merge_source_entries(
        net,
        object_kind_by_source_kind,
        cross_sheet_kind=cross_sheet_kind,
    ):
        current = entries_by_key.get(candidate.key)
        if current is None or current.object_kind != candidate.object_kind:
            _compiled_retain_earliest_net_item(entries_by_key, candidate)


def _compiled_net_item_order_key(
    entry: _CompiledPortRelativeEntry,
) -> tuple[bytes, bytes, int, bytes]:
    return (
        dotnet_ordinal_ignore_case_sort_key(entry.name),
        dotnet_ordinal_ignore_case_sort_key(entry.parent_id),
        entry.object_kind,
        dotnet_ordinal_ignore_case_sort_key(entry.logical_designator),
    )


def _compiled_retain_earliest_net_item(
    entries_by_key: dict[str, _CompiledPortRelativeEntry],
    candidate: _CompiledPortRelativeEntry,
) -> None:
    current = entries_by_key.get(candidate.key)
    has_cross_sheet = candidate.has_cross_sheet or (
        current is not None and current.has_cross_sheet
    )
    if current is None or _compiled_net_item_order_key(
        candidate
    ) < _compiled_net_item_order_key(current):
        entries_by_key[candidate.key] = replace(
            candidate,
            has_cross_sheet=has_cross_sheet,
        )
    elif has_cross_sheet and not current.has_cross_sheet:
        entries_by_key[candidate.key] = replace(current, has_cross_sheet=True)


def _compiled_net_port_relative_entries(
    net: AltiumCompiledNet,
) -> tuple[_CompiledPortRelativeEntry, ...]:
    entries_by_key: dict[str, _CompiledPortRelativeEntry] = {}
    for endpoint in net.endpoints:
        if endpoint.role not in {"port", "offsheet_connector"} or not endpoint.name:
            continue
        key = _compiled_case_insensitive_key(endpoint.name)
        candidate = _CompiledPortRelativeEntry(
            key=key,
            name=endpoint.name,
            has_cross_sheet=endpoint.role == "offsheet_connector",
            parent_id=endpoint.parent_id,
            object_kind=56 if endpoint.role == "offsheet_connector" else 57,
            logical_designator=endpoint.designator,
        )
        _compiled_retain_earliest_net_item(entries_by_key, candidate)
    _compiled_append_scalar_merge_source_entries(
        entries_by_key,
        net,
        {"offsheet_connector": 56, "port": 57},
        cross_sheet_kind="offsheet_connector",
    )
    return tuple(entries_by_key.values())


def _compiled_net_label_names(net: AltiumCompiledNet) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            endpoint.name
            for endpoint in net.endpoints
            if endpoint.role == "net_label" and endpoint.name
        )
    )


def _compiled_net_harness_entry_names(net: AltiumCompiledNet) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *(
                    endpoint.name
                    for endpoint in net.endpoints
                    if endpoint.role == "harness_entry" and endpoint.name
                ),
                *(
                    item.name
                    for item in net.items
                    if item.kind == "harness_entry" and item.name
                ),
            ]
        )
    )


def _compiled_generated_net_designator(designator: str) -> str:
    part_match = re.match(r"^(.*\d)[A-Z]+$", designator)
    return part_match.group(1) if part_match else designator


def _compiled_net_primary_power_name(net: AltiumCompiledNet) -> str | None:
    for power_name in _compiled_net_power_names(net):
        return power_name
    return None


def _compiled_net_is_flat_bus_member(
    net: AltiumCompiledNet,
    bus_member_names_by_logical_id: dict[str, frozenset[str]],
) -> bool:
    del bus_member_names_by_logical_id
    return bool(net._port_bus_member_names)


def _compiled_auto_name_from_terminals(group: list[AltiumCompiledNet]) -> str | None:
    candidates: list[str] = []
    for net in group:
        for terminal in net.terminals:
            if terminal.designator and terminal.pin:
                designator = _compiled_generated_net_designator(terminal.designator)
                candidates.append(f"Net{designator}_{terminal.pin}")
    if candidates:
        return min(candidates, key=_altium_net_total_sort_key)
    return None


def _compiled_net_name_is_sheet_entry_only(
    net: AltiumCompiledNet,
) -> bool:
    if not _compiled_net_has_endpoint_role(net, "sheet_entry"):
        return False
    return not any(
        _compiled_net_has_endpoint_role(net, role)
        for role in ("net_label", "power_port", "port")
    )


def _compiled_net_is_terminal_less_bridge_evidence(
    net: AltiumCompiledNet,
) -> bool:
    if net.terminals:
        return False
    if any(
        _compiled_net_has_endpoint_role(net, role)
        for role in ("net_label", "power_port", "harness_entry")
    ):
        return False
    return any(
        _compiled_net_has_endpoint_role(net, role)
        for role in ("sheet_entry", "port", "offsheet_connector")
    )


def _compiled_net_has_only_connector_name_evidence(
    net: AltiumCompiledNet,
) -> bool:
    if net.auto_named:
        return True
    if not any(
        _compiled_net_has_endpoint_role(net, role)
        for role in ("sheet_entry", "port", "offsheet_connector")
    ):
        return False
    return not any(
        _compiled_net_has_endpoint_role(net, role)
        for role in ("net_label", "power_port", "harness_entry")
    )


def _compiled_group_should_autoname_before_connector_fallback(
    group: list[AltiumCompiledNet],
    *,
    compile_options: AltiumProjectCompileOptions,
) -> bool:
    if compile_options.effective_hierarchy_mode != "HIERARCHICAL":
        return False
    if compile_options.allow_port_net_names:
        return False
    if not any(net.scope == "inter_sheet_link" for net in group):
        return False
    if not any(net.terminals for net in group):
        return False
    if not any(
        net.scope == "physical_local" and net.terminals and net.auto_named
        for net in group
    ):
        return False
    if any(
        names
        for net in group
        for names in (
            _compiled_net_label_names(net),
            _compiled_net_power_names(net),
            _compiled_net_harness_entry_names(net),
        )
    ):
        return False
    return all(_compiled_net_has_only_connector_name_evidence(net) for net in group)


def _compiled_connector_auto_name(
    group: list[AltiumCompiledNet],
    *,
    hierarchy_mode: str,
) -> str | None:
    if hierarchy_mode == "HIERARCHICAL":
        return _compiled_auto_name_from_terminals(group)
    candidates: list[tuple[tuple[list, str], int, str]] = []
    for index, net in enumerate(group):
        if net.scope != "physical_local" or not net.terminals:
            continue
        effective_name = _compiled_effective_name_for_scope(
            net,
            hierarchy_mode=hierarchy_mode,
        )
        if not effective_name:
            continue
        candidates.append(
            (
                _altium_net_total_sort_key(effective_name),
                index,
                effective_name,
            )
        )
    if candidates:
        return min(candidates)[2]
    return _compiled_auto_name_from_terminals(group)


def _compiled_terminal_auto_name_by_total_sort(
    group: list[AltiumCompiledNet],
    *,
    hierarchy_mode: str,
) -> str | None:
    candidates: list[tuple[tuple[list, str], int, str]] = []
    for index, net in enumerate(group):
        if net.scope != "physical_local" or not net.terminals or not net.auto_named:
            continue
        effective_name = _compiled_effective_name_for_scope(
            net,
            hierarchy_mode=hierarchy_mode,
        )
        if not effective_name:
            continue
        candidates.append(
            (
                _altium_net_total_sort_key(effective_name),
                index,
                effective_name,
            )
        )
    if candidates:
        return min(candidates)[2]
    return None


def _compiled_net_bus_range_label_bases(
    net: AltiumCompiledNet,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            base_name
            for endpoint in net.endpoints
            if endpoint.role == "net_label"
            for base_name in [_bus_range_base_name(endpoint.name)]
            if base_name
        )
    )


def _compiled_net_repeat_sheet_entry_bases(
    net: AltiumCompiledNet,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            inner_port
            for endpoint in net.endpoints
            if endpoint.role == "sheet_entry"
            for inner_port in [_parse_entry_repeat(endpoint.name)]
            if inner_port is not None
        )
    )


def _compiled_net_has_repeat_sheet_entry_for_base(
    net: AltiumCompiledNet,
    base_name: str,
) -> bool:
    return any(
        dotnet_ordinal_ignore_case_key(base)
        == dotnet_ordinal_ignore_case_key(base_name)
        for base in _compiled_net_repeat_sheet_entry_bases(net)
    )


def _compiled_net_has_bus_range_label_for_base(
    net: AltiumCompiledNet,
    base_name: str,
) -> bool:
    return any(
        dotnet_ordinal_ignore_case_key(base)
        == dotnet_ordinal_ignore_case_key(base_name)
        for base in _compiled_net_bus_range_label_bases(net)
    )


def _compiled_net_has_repeat_bus_structural_base(
    net: AltiumCompiledNet,
    structural_bases: tuple[str, ...],
) -> bool:
    return any(
        _compiled_net_has_repeat_sheet_entry_for_base(net, base)
        or _compiled_net_has_bus_range_label_for_base(net, base)
        for base in structural_bases
    )


def _repeat_bus_structural_bases_by_physical_document_id(
    physical_nets: tuple[AltiumCompiledNet, ...],
) -> dict[str, tuple[str, ...]]:
    repeat_bases_by_document_id: dict[str, set[str]] = defaultdict(set)
    bus_bases_by_document_id: dict[str, dict[str, str]] = defaultdict(dict)
    for net in physical_nets:
        for document_id in net.physical_document_ids:
            for base in _compiled_net_repeat_sheet_entry_bases(net):
                repeat_bases_by_document_id[document_id].add(
                    dotnet_ordinal_ignore_case_key(base)
                )
            for base in _compiled_net_bus_range_label_bases(net):
                bus_bases_by_document_id[document_id].setdefault(
                    dotnet_ordinal_ignore_case_key(base),
                    base,
                )

    result: dict[str, tuple[str, ...]] = {}
    for document_id, repeat_bases in repeat_bases_by_document_id.items():
        bus_bases = bus_bases_by_document_id.get(document_id, {})
        matched_bases = [
            preserved_base
            for lower_base, preserved_base in bus_bases.items()
            if lower_base in repeat_bases
        ]
        if matched_bases:
            result[document_id] = tuple(matched_bases)
    return result


def _published_physical_nets(
    physical_nets: tuple[AltiumCompiledNet, ...],
) -> tuple[AltiumCompiledNet, ...]:
    structural_bases = _repeat_bus_structural_bases_by_physical_document_id(
        physical_nets
    )
    result: list[AltiumCompiledNet] = []
    for net in physical_nets:
        suppress = False
        if not net.terminals:
            label_names = {
                dotnet_ordinal_ignore_case_key(endpoint.name)
                for endpoint in net.endpoints
                if endpoint.role == "net_label"
            }
            for document_id in net.physical_document_ids:
                for base in structural_bases.get(document_id, ()):
                    if dotnet_ordinal_ignore_case_key(
                        base
                    ) in label_names and _compiled_net_has_repeat_sheet_entry_for_base(
                        net, base
                    ):
                        suppress = True
                        break
                if suppress:
                    break
        if not suppress:
            result.append(net)
    return tuple(result)


def _compiled_global_signal_ids_by_source_pin(
    flat_nets: Sequence[AltiumCompiledNet],
) -> dict[tuple[str, int], str]:
    result: dict[tuple[str, int], str] = {}
    for net in flat_nets:
        items_by_id = {item.id: item for item in net.items if item.kind == "terminal"}
        for terminal in net.terminals:
            item = items_by_id.get(terminal.id)
            if item is None or not item.physical_document_id:
                continue
            key = (
                item.physical_document_id,
                terminal._source_pin_object_id,
            )
            if terminal._source_pin_object_id:
                result.setdefault(key, net.id)
    return result


def _compiled_component_pin_counts(
    components: tuple[AltiumCompiledComponent, ...],
    flat_nets: tuple[AltiumCompiledNet, ...],
    component_pin_evidence: Mapping[
        str,
        Mapping[int, _CompiledComponentPinEvidence],
    ],
    physical_room_names_by_document_id: Mapping[str, str],
) -> tuple[AltiumCompiledComponent, ...]:
    pins_by_designator: dict[str, set[str]] = defaultdict(set)
    part_pins_by_base_designator: dict[str, set[str]] = defaultdict(set)
    document_ids_by_designator: dict[str, set[str]] = defaultdict(set)
    document_ids_by_base_designator: dict[str, set[str]] = defaultdict(set)
    for net in flat_nets:
        for terminal in net.terminals:
            key = terminal.designator.lower()
            if not key or not terminal.pin:
                continue
            pins_by_designator[key].add(terminal.pin)
            part_match = re.match(r"^(.*\d)[A-Z]+$", terminal.designator)
            if part_match:
                part_pins_by_base_designator[part_match.group(1).lower()].add(
                    terminal.pin
                )
        for item in net.items:
            if item.kind != "terminal" or not item.designator:
                continue
            key = item.designator.lower()
            if item.physical_document_id:
                document_ids_by_designator[key].add(item.physical_document_id)
            part_match = re.match(r"^(.*\d)[A-Z]+$", item.designator)
            if part_match and item.physical_document_id:
                document_ids_by_base_designator[part_match.group(1).lower()].add(
                    item.physical_document_id
                )
    global_signal_ids_by_pin = _compiled_global_signal_ids_by_source_pin(flat_nets)
    result: list[AltiumCompiledComponent] = []
    for component in components:
        physical_key = component.physical_designator.lower()
        evidence_occurrences = tuple(
            (
                physical_document_id,
                physical_room_names_by_document_id.get(physical_document_id, ""),
                replace(
                    evidence,
                    physical_part_designator=physical_part_designator,
                ),
            )
            for (
                physical_document_id,
                logical_document_id,
                source_index,
                physical_part_designator,
            ) in (component._managed_source_component_occurrences)
            if (
                evidence := component_pin_evidence.get(logical_document_id, {}).get(
                    source_index
                )
            )
            is not None
        )
        if evidence_occurrences:
            pin_count = _compiled_managed_pin_count(
                evidence_occurrences,
                global_signal_ids_by_pin,
            )
            all_pin_count = component.all_pin_count or pin_count
            result.append(
                replace(
                    component,
                    pin_count=pin_count,
                    all_pin_count=all_pin_count,
                )
            )
            continue
        if (
            component._project_multipart_collapsed
            or len(
                document_ids_by_designator.get(physical_key, set())
                | document_ids_by_base_designator.get(physical_key, set())
            )
            > 1
        ):
            pin_count = component.all_pin_count
        else:
            pins = pins_by_designator.get(physical_key, set())
            if component.part_count > 1:
                pins = pins | part_pins_by_base_designator.get(physical_key, set())
            pin_count = len(pins)
        result.append(replace(component, pin_count=pin_count))
    return tuple(result)


def _compiled_group_has_terminalless_repeat_bus_structural(
    group: list[AltiumCompiledNet],
    structural_bases_by_document_id: Mapping[str, Iterable[str]],
) -> bool:
    for net in group:
        if net.terminals:
            continue
        for document_id in net.physical_document_ids:
            structural_bases = structural_bases_by_document_id.get(document_id, ())
            if _compiled_net_has_repeat_bus_structural_base(
                net, tuple(structural_bases)
            ):
                return True
    return False


def _compiled_group_has_terminalless_bus_range_connector(
    group: list[AltiumCompiledNet],
) -> bool:
    for net in group:
        if net.terminals:
            continue
        if any(
            endpoint.role.lower() in {"sheet_entry", "port"}
            and _parse_bus_range(endpoint.name)
            for endpoint in net.endpoints
        ):
            return True
    return False


def _compiled_source_net_order_key(
    net: AltiumCompiledNet,
    *,
    canonical_power_key: str | None,
    source_order: dict[str, int],
) -> tuple[int, int, int | bytes, bytes | int]:
    """Approximate legacy merge-list ordering for flattened terminal provenance."""
    primary_power_name = _compiled_net_primary_power_name(net)
    if primary_power_name:
        canonical_rank = (
            0
            if canonical_power_key is not None
            and dotnet_ordinal_ignore_case_key(primary_power_name)
            == canonical_power_key
            else 1
        )
        return (
            0,
            canonical_rank,
            source_order[net.id],
            dotnet_ordinal_ignore_case_sort_key(primary_power_name),
        )

    port_names = _compiled_net_port_names(net)
    if port_names:
        port_key = dotnet_ordinal_ignore_case_sort_key(
            min(port_names, key=dotnet_ordinal_ignore_case_sort_key)
        )
        return (1, 0, port_key, source_order[net.id])

    return (2, 0, b"", source_order[net.id])


def _compiled_ordered_source_nets(
    group: list[AltiumCompiledNet],
    *,
    hierarchy_mode: str,
    source_order: dict[str, int],
    merge_order: dict[str, int],
    has_hierarchy_link: bool,
) -> list[AltiumCompiledNet]:
    if hierarchy_mode in {"HIERARCHICAL", "STRICT_HIERARCHICAL"} and has_hierarchy_link:
        return sorted(
            group,
            key=lambda net: merge_order[net.id],
        )
    if hierarchy_mode in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
        return sorted(group, key=lambda net: source_order[net.id])
    primary_power_names = {
        power_name
        for net in group
        for power_name in [_compiled_net_primary_power_name(net)]
        if power_name
    }
    label_backed_power_names = {
        power_name
        for net in group
        for power_name in [_compiled_net_primary_power_name(net)]
        if power_name and _compiled_net_label_names(net)
    }
    canonical_power_key = None
    if label_backed_power_names:
        canonical_power_key = dotnet_ordinal_ignore_case_key(
            min(
                label_backed_power_names,
                key=dotnet_ordinal_ignore_case_sort_key,
            )
        )
    elif primary_power_names:
        canonical_power_key = dotnet_ordinal_ignore_case_key(
            min(primary_power_names, key=dotnet_ordinal_ignore_case_sort_key)
        )
    return sorted(
        group,
        key=lambda net: _compiled_source_net_order_key(
            net,
            canonical_power_key=canonical_power_key,
            source_order=source_order,
        ),
    )


def _compiled_higher_level_bridge_names(
    group: list[AltiumCompiledNet],
) -> tuple[str, ...]:
    by_id = {net.id: net for net in group}
    names: list[str] = []
    for link_net in group:
        if link_net.scope != "inter_sheet_link" or not link_net.parent_net_id:
            continue
        parent_net = by_id.get(link_net.parent_net_id)
        if parent_net is None or parent_net.auto_named:
            continue
        if not _compiled_net_has_endpoint_role(parent_net, "sheet_entry"):
            continue
        if not _compiled_net_label_names(parent_net):
            continue
        names.append(parent_net.name)
    return _dedupe_compiled_net_values(names)


def _compiled_repeat_link_names(
    group: list[AltiumCompiledNet],
) -> tuple[str, ...]:
    names: list[str] = []
    for net in group:
        if net.scope != "inter_sheet_link" or not net.name:
            continue
        if any(
            alias
            and net.name.lower().startswith(alias.lower())
            and net.name.lower() != alias.lower()
            for alias in net.aliases
        ):
            names.append(net.name)
    return _dedupe_compiled_net_values(names)


def _compiled_effective_name_for_scope(
    net: AltiumCompiledNet,
    *,
    hierarchy_mode: str,
) -> str:
    if (
        hierarchy_mode in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}
        and net.scope == "inter_sheet_link"
    ):
        return ""
    if (
        hierarchy_mode in {"FLAT", "GLOBAL", "HIERARCHICAL"}
        and _compiled_net_name_is_sheet_entry_only(net)
        and (hierarchy_mode in {"FLAT", "GLOBAL"} or not net.terminals)
    ):
        return _compiled_auto_name_from_terminals([net]) or net.name
    return net.name


def _compiled_flat_order_bucket(
    net: AltiumCompiledNet,
    *,
    compile_options: AltiumProjectCompileOptions,
) -> int:
    """Mirror the legacy netlist emitter's named/bridge/auto output buckets."""
    if _compiled_net_label_names(net) or _compiled_net_power_names(net):
        return 0
    if compile_options.allow_port_net_names and _compiled_net_port_names(net):
        return 1
    if compile_options.allow_sheet_entry_net_names and _compiled_net_has_endpoint_role(
        net, "sheet_entry"
    ):
        return 2
    if _compiled_net_has_endpoint_role(net, "port") or _compiled_net_has_endpoint_role(
        net, "sheet_entry"
    ):
        return 3
    return 4


def _finalize_compiled_flat_name(
    selected_name: str,
    *,
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
    auto_named: bool,
) -> tuple[str, str, str | None, bool]:
    selected_key = dotnet_ordinal_ignore_case_key(selected_name)
    preapplied = next(
        (
            net
            for net in group
            if net.override_name is not None
            and net.original_name is not None
            and dotnet_ordinal_ignore_case_key(net.name) == selected_key
            and dotnet_ordinal_ignore_case_key(net.override_name) == selected_key
        ),
        None,
    )
    if preapplied is not None:
        return (
            selected_name,
            preapplied.original_name or selected_name,
            preapplied.override_name,
            auto_named,
        )
    original_name = selected_name
    override_name = _compiled_net_name_annotation(original_name, annotation)
    return (
        override_name if override_name is not None else original_name,
        original_name,
        override_name,
        auto_named,
    )


def _compiled_higher_level_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    higher_level_names = _compiled_higher_level_bridge_names(group)
    if compile_options.name_nets_hierarchically and higher_level_names:
        return _compiled_managed_source_name_min(higher_level_names), False
    return None


def _compiled_priority_power_name_candidate(
    power_names: list[str],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    if compile_options.power_port_names_take_priority and power_names:
        return _compiled_managed_source_name_min(power_names), False
    return None


def _compiled_repeat_link_name_candidate(
    group: list[AltiumCompiledNet],
) -> tuple[str, bool] | None:
    repeat_link_names = _compiled_repeat_link_names(group)
    if repeat_link_names:
        return min(repeat_link_names, key=_altium_net_total_sort_key), False
    return None


def _compiled_terminal_less_channel_name_candidate(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    allow_terminal_less_bridge_names: bool,
) -> tuple[str, bool] | None:
    if not allow_terminal_less_bridge_names:
        return None
    channel_names: list[tuple[int, str]] = []
    for net in group:
        if net.auto_named or _compiled_net_has_endpoint_role(net, "power_port"):
            continue
        if net.scope != "physical_local":
            continue
        candidate_depths = [
            len(physical_document.physical_instance_path.split("\\"))
            for document_id in net.physical_document_ids
            for physical_document in [physical_document_by_id.get(document_id)]
            if physical_document is not None
            and physical_document.parent_sheet_symbol_id is not None
        ]
        if candidate_depths:
            channel_names.append((min(candidate_depths), net.name))
    if channel_names:
        return min(
            channel_names,
            key=lambda item: (item[0], item[1].lower()),
        )[1], False
    return None


def _compiled_hierarchical_channel_terminal_name_candidate(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    if compile_options.effective_hierarchy_mode != "HIERARCHICAL":
        return None
    root_physical_names = {
        net.name.lower()
        for net in group
        if net.scope == "physical_local"
        and net.terminals
        and not net.auto_named
        and any(
            (
                physical_document_by_id.get(document_id) is not None
                and physical_document_by_id[document_id].parent_sheet_symbol_id is None
            )
            for document_id in net.physical_document_ids
        )
    }
    channel_terminal_names = [
        net.name
        for net in group
        if net.scope == "physical_local"
        and net.terminals
        and not net.auto_named
        and not _compiled_net_has_endpoint_role(net, "power_port")
        and any(
            (
                physical_document_by_id.get(document_id) is not None
                and physical_document_by_id[document_id].parent_sheet_symbol_id
                is not None
            )
            for document_id in net.physical_document_ids
        )
        # Channel-expanded spellings win over root sheet-entry-named nets
        # independent of ChannelDesignatorFormatString token order: AD26
        # selects the source object by priority before expanding
        # (NamingFunctionsProvider.Create registers eSheetEntry below ePort;
        # SignalsCreatorUtils.ShouldBeNewSignalName compares ObjectPriority
        # first and GetSignalFullName expands only the winner). The
        # original_name clause detects expansion when the format does not
        # lead with the base net name (e.g. $RoomName_$Component). This is
        # an approximation: equal-priority (sheet-entry vs sheet-entry)
        # candidates fall to alphanumeric comparison in AD26, so a renamed
        # (not channel-expanded) child spelling could match here too.
        and any(
            net.name.lower() != root_name
            and (
                net.name.lower().startswith(root_name)
                or (net.original_name or "").lower() == root_name
            )
            for root_name in root_physical_names
        )
    ]
    if channel_terminal_names:
        return _compiled_managed_source_name_min(channel_terminal_names), False
    return None


def _compiled_terminal_label_spelling(
    net: AltiumCompiledNet,
    selected_name: str,
    compile_options: AltiumProjectCompileOptions,
    *,
    allow_terminal_less_bridge_names: bool,
) -> str | None:
    if net.scope != "physical_local" or not net.terminals or net.auto_named:
        return None
    if _compiled_net_has_endpoint_role(net, "power_port"):
        return None
    if (
        not allow_terminal_less_bridge_names
        and _compiled_net_is_terminal_less_bridge_evidence(net)
    ):
        return None
    if not any(
        label_name.lower() == selected_name.lower()
        for label_name in _compiled_net_label_names(net)
    ):
        return None
    return (
        _compiled_effective_name_for_scope(
            net,
            hierarchy_mode=compile_options.effective_hierarchy_mode,
        )
        or None
    )


def _compiled_label_name_candidate(
    group: list[AltiumCompiledNet],
    label_names: list[str],
    compile_options: AltiumProjectCompileOptions,
    *,
    allow_terminal_less_bridge_names: bool,
) -> tuple[str, bool] | None:
    if not label_names:
        return None
    selected_name = _compiled_managed_source_name_min(label_names)
    terminal_label_spellings = [
        spelling
        for net in group
        if (
            spelling := _compiled_terminal_label_spelling(
                net,
                selected_name,
                compile_options,
                allow_terminal_less_bridge_names=allow_terminal_less_bridge_names,
            )
        )
    ]
    if terminal_label_spellings:
        selected_name = _compiled_managed_source_name_min(terminal_label_spellings)
    return selected_name, False


def _compiled_connector_auto_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    if not _compiled_group_should_autoname_before_connector_fallback(
        group,
        compile_options=compile_options,
    ):
        return None
    auto_name = _compiled_connector_auto_name(
        group,
        hierarchy_mode=compile_options.effective_hierarchy_mode,
    )
    if auto_name:
        return auto_name, True
    return None


def _compiled_terminal_explicit_physical_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
    *,
    allow_terminal_less_bridge_names: bool,
) -> tuple[str, bool] | None:
    terminal_explicit_physical_names = [
        effective_name
        for net in group
        for effective_name in [
            _compiled_effective_name_for_scope(
                net,
                hierarchy_mode=compile_options.effective_hierarchy_mode,
            )
        ]
        if effective_name
        and net.scope == "physical_local"
        and net.terminals
        and not net.auto_named
        and not _compiled_net_has_endpoint_role(net, "power_port")
        and (
            allow_terminal_less_bridge_names
            or not _compiled_net_is_terminal_less_bridge_evidence(net)
        )
    ]
    if terminal_explicit_physical_names:
        return _compiled_managed_source_name_min(
            terminal_explicit_physical_names
        ), False
    return None


def _compiled_port_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    port_names = [
        port_name for net in group for port_name in _compiled_net_port_names(net)
    ]
    if compile_options.allow_port_net_names and port_names:
        return _compiled_managed_source_name_min(port_names), False
    return None


def _compiled_terminal_auto_name_candidate(
    group: list[AltiumCompiledNet],
    power_names: list[str],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    if power_names:
        return None
    terminal_auto_name = _compiled_terminal_auto_name_by_total_sort(
        group,
        hierarchy_mode=compile_options.effective_hierarchy_mode,
    )
    if terminal_auto_name is not None:
        return terminal_auto_name, True
    return None


def _compiled_channel_power_name_candidate(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    power_names: list[str],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    group_physical_document_ids = {
        document_id for net in group for document_id in net.physical_document_ids
    }
    if (
        not power_names
        or len(group_physical_document_ids) != 1
        or compile_options.effective_hierarchy_mode
        not in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}
    ):
        return None
    power_name_keys = {_compiled_case_insensitive_key(name) for name in power_names}
    channel_power_names = [
        net.name
        for net in group
        for document_id in net.physical_document_ids
        for physical_document in [physical_document_by_id.get(document_id)]
        if net.name
        and net.scope == "physical_local"
        and len(net.physical_document_ids) == 1
        and net.terminals
        and not net.auto_named
        and _compiled_net_has_endpoint_role(net, "power_port")
        and physical_document is not None
        and physical_document.parent_sheet_symbol_id is not None
        and _compiled_case_insensitive_key(net.name) not in power_name_keys
    ]
    if channel_power_names:
        return _compiled_managed_source_name_min(channel_power_names), False
    return None


def _compiled_power_name_candidate(power_names: list[str]) -> tuple[str, bool] | None:
    if power_names:
        return _compiled_managed_source_name_min(power_names), False
    return None


def _compiled_positive_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
    *,
    allow_terminal_less_bridge_names: bool,
    include_auto_named: bool,
) -> tuple[str, bool] | None:
    positive_names = [
        effective_name
        for net in group
        for effective_name in [
            _compiled_effective_name_for_scope(
                net,
                hierarchy_mode=compile_options.effective_hierarchy_mode,
            )
        ]
        if effective_name
        and (include_auto_named or not net.auto_named)
        and (
            allow_terminal_less_bridge_names
            or not _compiled_net_is_terminal_less_bridge_evidence(net)
        )
    ]
    if positive_names:
        return _compiled_managed_source_name_min(positive_names), include_auto_named
    return None


def _finalized_compiled_flat_name_candidate(
    candidate: tuple[str, bool] | None,
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
) -> tuple[str, str, str | None, bool] | None:
    if candidate is None:
        return None
    selected_name, auto_named = candidate
    return _finalize_compiled_flat_name(
        selected_name,
        group=group,
        physical_document_by_id=physical_document_by_id,
        compile_options=compile_options,
        annotation=annotation,
        auto_named=auto_named,
    )


def _compiled_flat_name_candidates(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    power_names: list[str],
    label_names: list[str],
    *,
    compile_options: AltiumProjectCompileOptions,
    allow_terminal_less_bridge_names: bool,
) -> Iterable[tuple[str, bool] | None]:
    yield _compiled_higher_level_name_candidate(group, compile_options)
    yield _compiled_priority_power_name_candidate(power_names, compile_options)
    yield _compiled_repeat_link_name_candidate(group)
    yield _compiled_terminal_less_channel_name_candidate(
        group,
        physical_document_by_id,
        allow_terminal_less_bridge_names=allow_terminal_less_bridge_names,
    )
    yield _compiled_hierarchical_channel_terminal_name_candidate(
        group,
        physical_document_by_id,
        compile_options,
    )
    yield _compiled_label_name_candidate(
        group,
        label_names,
        compile_options,
        allow_terminal_less_bridge_names=allow_terminal_less_bridge_names,
    )
    yield _compiled_connector_auto_name_candidate(group, compile_options)
    yield _compiled_terminal_explicit_physical_name_candidate(
        group,
        compile_options,
        allow_terminal_less_bridge_names=allow_terminal_less_bridge_names,
    )
    yield _compiled_port_name_candidate(group, compile_options)
    yield _compiled_terminal_auto_name_candidate(group, power_names, compile_options)
    yield _compiled_channel_power_name_candidate(
        group,
        physical_document_by_id,
        power_names,
        compile_options,
    )
    yield _compiled_power_name_candidate(power_names)
    yield _compiled_positive_name_candidate(
        group,
        compile_options,
        allow_terminal_less_bridge_names=allow_terminal_less_bridge_names,
        include_auto_named=False,
    )
    yield _compiled_positive_name_candidate(
        group,
        compile_options,
        allow_terminal_less_bridge_names=allow_terminal_less_bridge_names,
        include_auto_named=True,
    )
    yield "N00000", True


def _compiled_net_name_comparison(
    candidate: AltiumCompiledNet,
    current: AltiumCompiledNet,
    *,
    candidate_name: str,
    current_name: str,
    candidate_full_name: str | None = None,
    current_full_name: str | None = None,
    compile_options: AltiumProjectCompileOptions,
) -> int:
    if not dotnet_trim(candidate_name):
        return 1
    if not dotnet_trim(current_name):
        return -1
    comparison = _compiled_descending_priority_comparison(
        candidate._name_source_priority,
        current._name_source_priority,
    )
    if comparison:
        return comparison
    comparison = _compiled_managed_source_name_comparison(candidate_name, current_name)
    if compile_options.name_nets_hierarchically:
        candidate_priority = -len(candidate._name_source_hierarchy_path)
        current_priority = -len(current._name_source_hierarchy_path)
        hierarchy_comparison = _compiled_descending_priority_comparison(
            candidate_priority,
            current_priority,
        )
        if hierarchy_comparison:
            return hierarchy_comparison
    if comparison:
        return comparison
    return _compiled_full_name_comparison(
        candidate,
        current,
        candidate_full_name=candidate_full_name,
        current_full_name=current_full_name,
        compile_options=compile_options,
    )


def _compiled_descending_priority_comparison(candidate: int, current: int) -> int:
    if candidate == current:
        return 0
    return -1 if candidate > current else 1


def _compiled_managed_source_name_comparison(candidate: str, current: str) -> int:
    comparison = managed_alpha_numeric_compare(candidate, current)
    if comparison:
        return comparison
    candidate_units = dotnet_utf16_units(candidate)
    current_units = dotnet_utf16_units(current)
    return (current_units > candidate_units) - (current_units < candidate_units)


def _compiled_managed_source_name_min(values: Sequence[str]) -> str:
    return min(values, key=cmp_to_key(_compiled_managed_source_name_comparison))


def _compiled_source_full_name(
    net: AltiumCompiledNet,
    explicit_full_name: str | None,
) -> str:
    if explicit_full_name is not None:
        return explicit_full_name
    return net._name_source_full_name or net.name


def _compiled_full_name_comparison(
    candidate: AltiumCompiledNet,
    current: AltiumCompiledNet,
    *,
    candidate_full_name: str | None = None,
    current_full_name: str | None = None,
    compile_options: AltiumProjectCompileOptions,
) -> int:
    if (
        candidate._name_source_schematic_id == current._name_source_schematic_id
        and candidate._name_source_hierarchy_path == current._name_source_hierarchy_path
    ):
        return 1
    if not current._name_source_is_multipath:
        return 1
    if not candidate._name_source_is_multipath:
        return -1
    candidate_full_name = _compiled_source_full_name(candidate, candidate_full_name)
    current_full_name = _compiled_source_full_name(current, current_full_name)
    if compile_options.channel_room_naming_style in {0, 1}:
        return managed_alpha_numeric_compare(candidate_full_name, current_full_name)
    if (
        candidate._name_source_relative_multichannel_depth
        != current._name_source_relative_multichannel_depth
    ):
        return (
            -1
            if candidate._name_source_relative_multichannel_depth
            < current._name_source_relative_multichannel_depth
            else 1
        )
    return managed_alpha_numeric_compare(candidate_full_name, current_full_name)


def _compiled_bus_prefix(net: AltiumCompiledNet) -> str:
    if net._name_source_bus_prefix:
        return net._name_source_bus_prefix
    suffix = net._name_source_bus_suffix
    raw_name = net._name_source_raw_name
    return (
        raw_name[: -len(suffix)] if suffix and raw_name.endswith(suffix) else raw_name
    )


def _compiled_bus_prefix_full_name(net: AltiumCompiledNet) -> str:
    if net._name_source_bus_prefix_full_name is None:
        return _compiled_bus_prefix(net)
    return net._name_source_bus_prefix_full_name


def _compiled_name_candidate_wins(
    candidate: AltiumCompiledNet,
    current: AltiumCompiledNet,
    *,
    compile_options: AltiumProjectCompileOptions,
) -> bool:
    if candidate._name_source_is_bus and current._name_source_is_bus:
        comparison = _compiled_net_name_comparison(
            candidate,
            current,
            candidate_name=_compiled_bus_prefix(candidate),
            current_name=_compiled_bus_prefix(current),
            candidate_full_name=_compiled_bus_prefix_full_name(candidate),
            current_full_name=_compiled_bus_prefix_full_name(current),
            compile_options=compile_options,
        )
        if comparison:
            return comparison < 0
    if candidate._bus_signal_width != current._bus_signal_width:
        return candidate._bus_signal_width > current._bus_signal_width
    if candidate._bus_signal_offset != current._bus_signal_offset:
        return candidate._bus_signal_offset < current._bus_signal_offset
    return (
        _compiled_net_name_comparison(
            candidate,
            current,
            candidate_name=candidate._name_source_raw_name or candidate.name,
            current_name=current._name_source_raw_name or current.name,
            compile_options=compile_options,
        )
        < 0
    )


def _compiled_flat_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    selected: AltiumCompiledNet | None = None
    for candidate in _compiled_flat_name_source_candidates(group):
        if not candidate._name_source_kind:
            continue
        comparison_name = candidate._name_source_raw_name or candidate.name
        if not dotnet_trim(comparison_name):
            continue
        if selected is None or _compiled_name_candidate_wins(
            candidate,
            selected,
            compile_options=compile_options,
        ):
            selected = candidate
    if selected is None:
        return None
    return selected.name, selected._name_source_autogenerated


def _compiled_flat_name_source_candidates(
    group: list[AltiumCompiledNet],
) -> Iterable[AltiumCompiledNet]:
    for net in group:
        yield net
        yield from (
            _compiled_net_from_harness_name_candidate(net, candidate)
            for candidate in net._signal_harness_name_candidates
            if candidate.source_priority > 0
        )


def _compiled_net_from_harness_name_candidate(
    net: AltiumCompiledNet,
    candidate: _SignalHarnessNameCandidate,
) -> AltiumCompiledNet:
    return replace(
        net,
        name=candidate.full_name or candidate.value,
        auto_named=False,
        _name_source_kind=candidate.source_kind,
        _name_source_priority=candidate.source_priority,
        _name_source_raw_name=candidate.value,
        _name_source_bus_prefix=candidate.bus_signal_prefix,
        _name_source_bus_prefix_full_name=candidate.bus_prefix_full_name,
        _name_source_bus_suffix=candidate.bus_signal_suffix,
        _name_source_is_bus=bool(candidate.bus_signal_prefix),
        _name_source_schematic_id=candidate.source_schematic_id,
        _name_source_hierarchy_path=candidate.hierarchy_path,
        _name_source_is_multipath=candidate.source_is_multipath,
        _name_source_relative_multichannel_depth=(
            candidate.relative_multichannel_depth
        ),
        _name_source_full_name=candidate.full_name or candidate.value,
        _name_source_autogenerated=False,
        _bus_signal_width=candidate.bus_signal_width,
        _bus_signal_offset=candidate.bus_signal_offset,
    )


def _compiled_finalized_exact_flat_name(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
) -> tuple[str, str, str | None, bool] | None:
    exact_candidate = _compiled_flat_name_candidate(group, compile_options)
    if exact_candidate is None:
        return None
    return _finalized_compiled_flat_name_candidate(
        exact_candidate,
        group,
        physical_document_by_id,
        compile_options=compile_options,
        annotation=annotation,
    )


def _compiled_flat_name(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
) -> tuple[str, str, str | None, bool]:
    exact_name = _compiled_finalized_exact_flat_name(
        group,
        physical_document_by_id,
        compile_options=compile_options,
        annotation=annotation,
    )
    if exact_name is not None:
        return exact_name
    allow_terminal_less_bridge_names = not any(net.terminals for net in group)
    power_names = [
        power_name for net in group for power_name in _compiled_net_power_names(net)
    ]
    label_names = [
        label_name for net in group for label_name in _compiled_net_label_names(net)
    ]
    for candidate in _compiled_flat_name_candidates(
        group,
        physical_document_by_id,
        power_names,
        label_names,
        compile_options=compile_options,
        allow_terminal_less_bridge_names=allow_terminal_less_bridge_names,
    ):
        finalized = _finalized_compiled_flat_name_candidate(
            candidate,
            group,
            physical_document_by_id,
            compile_options=compile_options,
            annotation=annotation,
        )
        if finalized is not None:
            return finalized
    raise AssertionError("unreachable compiled flat-name selection")


def _compiled_first_list_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
) -> list[tuple[str, str]]:
    if hierarchy_mode == "STRICT_HIERARCHICAL":
        return []
    evidence: list[tuple[_CompiledPortRelativeEntry, str]] = []
    for physical_net in physical_by_id.values():
        evidence.extend(
            (entry, physical_net.id)
            for entry in _compiled_net_first_list_entries(
                physical_net,
                hierarchy_mode,
            )
        )
    power_pairs, power_roots = _compiled_ordered_evidence_merge_plan(evidence)
    hidden_pairs = _compiled_hidden_pin_merge_pairs(
        physical_by_id,
        hierarchy_mode,
        preconnected_pairs=power_pairs,
        power_roots=power_roots,
    )
    return [*power_pairs, *hidden_pairs]


def _compiled_net_first_list_entries(
    net: AltiumCompiledNet,
    hierarchy_mode: str,
) -> tuple[_CompiledPortRelativeEntry, ...]:
    if not _compiled_net_is_scalar_inter_sheet_candidate(net):
        return ()
    forced_local = hierarchy_mode == "HIERARCHICAL" and (
        _compiled_net_has_endpoint_role(net, "port")
    )
    entries_by_key: dict[str, _CompiledPortRelativeEntry] = {}
    for endpoint in net.endpoints:
        object_kind = _compiled_first_list_object_kind(
            endpoint.role,
            hierarchy_mode,
            forced_local=forced_local,
        )
        if not endpoint.name or object_kind is None:
            continue
        candidate = _CompiledPortRelativeEntry(
            key=_compiled_case_insensitive_key(endpoint.name),
            name=endpoint.name,
            has_cross_sheet=False,
            parent_id=endpoint.parent_id,
            object_kind=object_kind,
            logical_designator=endpoint.designator,
        )
        _compiled_retain_earliest_net_item(entries_by_key, candidate)
    source_kinds = {
        kind: object_kind
        for kind in ("net_label", "power_port")
        if (
            object_kind := _compiled_first_list_object_kind(
                kind,
                hierarchy_mode,
                forced_local=forced_local,
            )
        )
        is not None
    }
    _compiled_append_scalar_merge_source_entries(
        entries_by_key,
        net,
        source_kinds,
    )
    return tuple(entries_by_key.values())


def _compiled_first_list_object_kind(
    endpoint_role: str,
    hierarchy_mode: str,
    *,
    forced_local: bool,
) -> int | None:
    if hierarchy_mode == "GLOBAL" and endpoint_role == "net_label":
        return 54
    if endpoint_role == "power_port" and not forced_local:
        return 55
    return None


def _compiled_ordered_evidence_merge_pairs(
    evidence: Iterable[tuple[_CompiledPortRelativeEntry, str]],
) -> list[tuple[str, str]]:
    pairs, _roots = _compiled_ordered_evidence_merge_plan(evidence)
    return pairs


def _compiled_ordered_evidence_merge_plan(
    evidence: Iterable[tuple[_CompiledPortRelativeEntry, str]],
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    roots: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for entry, net_id in sorted(
        evidence,
        key=lambda row: _compiled_net_item_order_key(row[0]),
    ):
        existing_root = roots.setdefault(entry.key, net_id)
        if existing_root != net_id:
            pairs.append((existing_root, net_id))
    return _compiled_acyclic_merge_pairs(pairs), roots


def _compiled_hidden_pin_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
    *,
    preconnected_pairs: Iterable[tuple[str, str]],
    power_roots: Mapping[str, str],
) -> list[tuple[str, str]]:
    if hierarchy_mode == "STRICT_HIERARCHICAL":
        return []
    groups: defaultdict[str, list[tuple[AltiumCompiledNet, bool]]] = defaultdict(list)
    for net in physical_by_id.values():
        for key, forced_local in _compiled_hidden_pin_group_rows(net, hierarchy_mode):
            groups[key].append((net, forced_local))
    pairs: list[tuple[str, str]] = []
    for key, rows in groups.items():
        pairs.extend(
            _compiled_hidden_pin_group_pairs(
                rows,
                power_root=power_roots.get(key),
                hierarchy_mode=hierarchy_mode,
            )
        )
    return _compiled_acyclic_merge_pairs(
        pairs,
        preconnected_pairs=preconnected_pairs,
    )


def _compiled_hidden_pin_group_rows(
    net: AltiumCompiledNet,
    hierarchy_mode: str,
) -> tuple[tuple[str, bool], ...]:
    if (
        net.logical_document_id is None
        or not _compiled_net_is_scalar_inter_sheet_candidate(net)
    ):
        return ()
    has_port = _compiled_net_has_endpoint_role(net, "port")
    hidden_names = _dedupe_compiled_net_values(
        [
            *(
                name
                for kind, name in net._scalar_merge_name_sources
                if kind == "hidden_pin"
            ),
            *(
                (net._name_source_name,)
                if net._name_source_kind == "hidden_pin" and net._name_source_name
                else ()
            ),
        ]
    )
    power_keys = {
        _compiled_case_insensitive_key(name)
        for kind, name in net._scalar_merge_name_sources
        if kind == "power_port" and name
    }
    power_keys.update(
        _compiled_case_insensitive_key(endpoint.name)
        for endpoint in net.endpoints
        if endpoint.role == "power_port" and endpoint.name
    )
    return tuple(
        (
            name_key,
            hierarchy_mode == "HIERARCHICAL" and has_port and name_key in power_keys,
        )
        for name in hidden_names
        if (name_key := _compiled_case_insensitive_key(name))
    )


def _compiled_hidden_pin_group_pairs(
    rows: list[tuple[AltiumCompiledNet, bool]],
    *,
    power_root: str | None,
    hierarchy_mode: str,
) -> list[tuple[str, str]]:
    if power_root is not None:
        return [(power_root, net.id) for net, _forced in rows if net.id != power_root]
    logical_ids = {net.logical_document_id for net, _forced in rows}
    if len(logical_ids) < 2:
        return []
    if hierarchy_mode == "HIERARCHICAL" and all(forced for _net, forced in rows):
        return []
    master_id = rows[0][0].id
    return [(master_id, net.id) for net, _forced in rows[1:]]


def _compiled_port_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
) -> list[tuple[str, str]]:
    if hierarchy_mode not in {"FLAT", "GLOBAL"}:
        return []
    evidence: list[tuple[_CompiledPortRelativeEntry, str]] = []
    for physical_net in physical_by_id.values():
        if not _compiled_net_is_scalar_inter_sheet_candidate(physical_net):
            continue
        evidence.extend(
            (entry, physical_net.id)
            for entry in _compiled_net_port_relative_entries(physical_net)
        )
    return _compiled_ordered_evidence_merge_pairs(evidence)


def _compiled_hierarchical_multisheet_connector_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
    physical_sheet_symbols: tuple[AltiumCompiledPhysicalSheetSymbol, ...],
) -> list[tuple[str, str]]:
    if hierarchy_mode not in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
        return []
    pairs: list[tuple[str, str]] = []
    for symbols in _compiled_physical_symbols_by_occurrence(
        physical_sheet_symbols
    ).values():
        child_ids = _compiled_multisheet_child_ids(symbols)
        if child_ids is not None:
            pairs.extend(_compiled_multisheet_relative_pairs(physical_by_id, child_ids))
    return _compiled_acyclic_merge_pairs(pairs)


def _compiled_acyclic_merge_pairs(
    pairs: Iterable[tuple[str, str]],
    *,
    preconnected_pairs: Iterable[tuple[str, str]] = (),
) -> list[tuple[str, str]]:
    from .altium_netlist_model import UnionFind

    closure: UnionFind[str] = UnionFind()
    result: list[tuple[str, str]] = []
    for master_id, absorbed_id in preconnected_pairs:
        closure.union(master_id, absorbed_id)
    for master_id, absorbed_id in pairs:
        if closure.find(master_id) == closure.find(absorbed_id):
            continue
        result.append((master_id, absorbed_id))
        closure.union(master_id, absorbed_id)
    return result


def _compiled_physical_symbols_by_occurrence(
    physical_sheet_symbols: tuple[AltiumCompiledPhysicalSheetSymbol, ...],
) -> dict[tuple[str, str], list[AltiumCompiledPhysicalSheetSymbol]]:
    symbols_by_occurrence: defaultdict[
        tuple[str, str], list[AltiumCompiledPhysicalSheetSymbol]
    ] = defaultdict(list)
    for symbol in physical_sheet_symbols:
        symbols_by_occurrence[
            (symbol.owner_physical_document_id, symbol.logical_sheet_symbol_id)
        ].append(symbol)
    return dict(symbols_by_occurrence)


def _compiled_multisheet_child_ids(
    symbols: list[AltiumCompiledPhysicalSheetSymbol],
) -> set[str] | None:
    child_logical_ids = {
        symbol.child_logical_document_id
        for symbol in symbols
        if symbol.child_logical_document_id
    }
    if len(child_logical_ids) < 2:
        return None
    return {
        symbol.child_physical_document_id
        for symbol in symbols
        if symbol.child_physical_document_id
    }


def _compiled_multisheet_relative_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    child_ids: set[str],
) -> list[tuple[str, str]]:
    roots_by_name: defaultdict[str, list[str]] = defaultdict(list)
    names_with_cross_sheet: set[str] = set()
    for physical_net in physical_by_id.values():
        if not _compiled_net_is_multisheet_relative_candidate(physical_net, child_ids):
            continue
        for entry in _compiled_net_port_relative_entries(physical_net):
            roots_by_name[entry.key].append(physical_net.id)
            if entry.has_cross_sheet:
                names_with_cross_sheet.add(entry.key)
    return [
        (net_ids[0], net_id)
        for name_key, net_ids in roots_by_name.items()
        if name_key in names_with_cross_sheet
        for net_id in net_ids[1:]
    ]


def _compiled_net_is_multisheet_relative_candidate(
    net: AltiumCompiledNet,
    child_ids: set[str],
) -> bool:
    return bool(child_ids.intersection(net.physical_document_ids)) and (
        _compiled_net_is_scalar_inter_sheet_candidate(net)
    )


def _compiled_scoped_harness_entry_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
) -> list[tuple[str, str]]:
    harness_entry_roots: dict[tuple[str, tuple[str, ...]], str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        for harness_entry_name in _compiled_net_harness_entry_names(physical_net):
            harness_key = (
                _compiled_case_insensitive_key(harness_entry_name),
                tuple(physical_net.physical_document_ids),
            )
            existing_root = harness_entry_roots.get(harness_key)
            if existing_root is None:
                harness_entry_roots[harness_key] = physical_net.id
            else:
                pairs.append((existing_root, physical_net.id))
    return pairs


def _compiled_scoped_bus_member_label_merge_pairs(
    physical_by_id: Mapping[str, AltiumCompiledNet],
) -> list[tuple[str, str]]:
    bus_members_by_key: defaultdict[tuple[tuple[str, ...], str], list[str]] = (
        defaultdict(list)
    )
    labels_by_key: defaultdict[tuple[tuple[str, ...], str], list[str]] = defaultdict(
        list
    )
    for net in physical_by_id.values():
        document_key = tuple(net.physical_document_ids)
        if _compiled_net_has_endpoint_role(net, "bus_member"):
            bus_members_by_key[
                (document_key, _compiled_case_insensitive_key(net.name))
            ].append(net.id)
        for label_name in _compiled_net_label_names(net):
            labels_by_key[
                (document_key, _compiled_case_insensitive_key(label_name))
            ].append(net.id)
    return [
        (label_id, bus_member_id)
        for key, bus_member_ids in bus_members_by_key.items()
        for label_id in labels_by_key.get(key, ())
        for bus_member_id in bus_member_ids
        if label_id != bus_member_id
    ]


def _compiled_flat_harness_entry_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
) -> list[tuple[str, str]]:
    if hierarchy_mode not in {"FLAT", "GLOBAL"}:
        return []
    roots: dict[tuple[str, ...], str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        for harness_key in _compiled_flat_harness_relation_merge_keys(
            physical_net, hierarchy_mode
        ):
            existing_root = roots.get(harness_key)
            if existing_root is None:
                roots[harness_key] = physical_net.id
            elif existing_root != physical_net.id:
                pairs.append((existing_root, physical_net.id))
    return pairs


def _compiled_flat_harness_relation_merge_keys(
    net: AltiumCompiledNet,
    hierarchy_mode: str,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        dict.fromkeys(
            (
                "relation",
                _compiled_harness_relation_family(candidate),
                _compiled_case_insensitive_key(candidate.value_from_source_object),
                *(
                    _compiled_case_insensitive_key(segment)
                    for segment in candidate.harness_entries_path
                ),
                "bus_index",
                "none"
                if candidate.bus_signal_index is None
                else str(candidate.bus_signal_index),
            )
            for candidate in net._signal_harness_name_candidates
            if candidate.harness_entries_path
            and _compiled_harness_relation_is_global(candidate, hierarchy_mode)
        )
    )


def _compiled_harness_relation_is_global(
    candidate: _SignalHarnessNameCandidate,
    hierarchy_mode: str,
) -> bool:
    if candidate.source_kind in {"port", "power_port"}:
        return True
    return hierarchy_mode == "GLOBAL" and candidate.source_kind == "net_label"


def _compiled_harness_relation_family(
    candidate: _SignalHarnessNameCandidate,
) -> str:
    if candidate.source_kind == "port":
        return "port_crosssheet"
    return "power_label"


def _compiled_same_name_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
    bus_member_names_by_logical_id: dict[str, frozenset[str]],
) -> list[tuple[str, str]]:
    if hierarchy_mode not in {"FLAT", "GLOBAL"}:
        return []
    port_member_roots: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        if not _compiled_net_is_flat_bus_member(
            physical_net,
            bus_member_names_by_logical_id,
        ):
            continue
        for physical_name in physical_net._port_bus_member_names:
            name_key = _compiled_case_insensitive_key(physical_name)
            existing_root = port_member_roots.get(name_key)
            if existing_root is None:
                port_member_roots[name_key] = physical_net.id
            elif existing_root != physical_net.id:
                pairs.append((existing_root, physical_net.id))
    return pairs


def _compiled_hierarchy_link_merge_pairs(
    inter_sheet_nets: tuple[AltiumCompiledNet, ...],
    physical_by_id: dict[str, AltiumCompiledNet],
    *,
    consume_hierarchy_links: bool,
) -> list[tuple[str, str]]:
    if not consume_hierarchy_links:
        return []
    pairs: list[tuple[str, str]] = []
    for link_net in inter_sheet_nets:
        source_ids = [
            source_id
            for source_id in link_net.source_net_ids
            if source_id in physical_by_id
        ]
        if not source_ids:
            continue
        first_source_id = source_ids[0]
        pairs.extend((first_source_id, source_id) for source_id in source_ids[1:])
    return pairs


def _compiled_terminal_less_flat_net_should_emit(
    ordered_group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    repeat_bus_structural_bases_by_document_id: Mapping[str, Iterable[str]],
) -> bool:
    has_harness_bundle = any(
        _compiled_net_has_endpoint_role(net, "harness_port") for net in ordered_group
    )
    has_repeat_bus_structural = _compiled_group_has_terminalless_repeat_bus_structural(
        ordered_group,
        repeat_bus_structural_bases_by_document_id,
    )
    has_bus_range_connector = _compiled_group_has_terminalless_bus_range_connector(
        ordered_group
    )
    has_standalone_port_connector = any(
        _compiled_net_has_endpoint_role(net, "port")
        and not _compiled_net_has_endpoint_role(net, "sheet_entry")
        and any(
            (
                physical_document_by_id.get(document_id) is not None
                and physical_document_by_id[document_id].parent_sheet_symbol_id is None
            )
            for document_id in net.physical_document_ids
        )
        and (
            net.auto_named
            or any(
                endpoint.role.lower() == "port" and _parse_bus_range(endpoint.name)
                for endpoint in net.endpoints
            )
        )
        for net in ordered_group
    )
    has_final_connector_evidence = any(
        _compiled_net_has_endpoint_role(net, role)
        for net in ordered_group
        for role in ("power_port", "port", "harness_entry")
    )
    has_terminal_less_final_name_evidence = any(
        _compiled_net_has_endpoint_role(net, role)
        for net in ordered_group
        for role in ("net_label", "power_port", "harness_entry")
    )
    has_harness_entry_evidence = any(
        _compiled_net_has_endpoint_role(net, "harness_entry") for net in ordered_group
    )
    has_non_harness_terminal_less_evidence = any(
        _compiled_net_has_endpoint_role(net, role)
        for net in ordered_group
        for role in (
            "net_label",
            "power_port",
            "port",
            "offsheet_connector",
            "sheet_entry",
        )
    )
    has_sheet_entry_evidence = any(
        _compiled_net_has_endpoint_role(net, "sheet_entry") for net in ordered_group
    )
    if has_harness_entry_evidence and not has_non_harness_terminal_less_evidence:
        return False
    if (
        not has_terminal_less_final_name_evidence
        and not has_sheet_entry_evidence
        and not has_standalone_port_connector
    ):
        return False
    if has_repeat_bus_structural and not has_final_connector_evidence:
        return False
    if has_bus_range_connector and not has_standalone_port_connector:
        return False
    if has_harness_bundle and not has_terminal_less_final_name_evidence:
        return False
    return True


def _compiled_link_rows_by_root(
    inter_sheet_nets: tuple[AltiumCompiledNet, ...],
    physical_by_id: dict[str, AltiumCompiledNet],
    root_by_source_id: dict[str, str],
    *,
    consume_hierarchy_links: bool,
) -> dict[str, list[AltiumCompiledNet]]:
    if not consume_hierarchy_links:
        return {}
    link_rows_by_root: dict[str, list[AltiumCompiledNet]] = defaultdict(list)
    for link_net in inter_sheet_nets:
        source_ids = [
            source_id
            for source_id in link_net.source_net_ids
            if source_id in physical_by_id
        ]
        if not source_ids:
            continue
        root = root_by_source_id[source_ids[0]]
        link_rows_by_root[root].append(link_net)
    return link_rows_by_root


def _compiled_net_name_is_flat_alias(
    net: AltiumCompiledNet,
    *,
    final_name: str,
    has_authored_net_name: bool,
) -> bool:
    if not net.name or net.name == final_name:
        return False
    if not has_authored_net_name or not net.auto_named:
        return True
    return not any(endpoint.role == "sheet_entry" for endpoint in net.endpoints)


def _compiled_flat_aliases(
    naming_group: Sequence[AltiumCompiledNet],
    *,
    final_name: str,
) -> tuple[str, ...]:
    has_authored_net_name = any(
        any(endpoint.role == "net_label" for endpoint in net.endpoints)
        for net in naming_group
    )
    aliases = {
        alias
        for net in naming_group
        for alias in net.aliases
        if alias and alias != final_name
    }
    aliases.update(
        net.name
        for net in naming_group
        if _compiled_net_name_is_flat_alias(
            net,
            final_name=final_name,
            has_authored_net_name=has_authored_net_name,
        )
    )
    return tuple(sorted(aliases, key=_altium_net_total_sort_key))


def _compiled_flat_row_source_parts(
    ordered_group: list[AltiumCompiledNet],
    naming_group: list[AltiumCompiledNet],
    link_rows: list[AltiumCompiledNet],
    *,
    final_name: str,
) -> tuple[
    list[str],
    tuple[str, ...],
    tuple[AltiumCompiledNetTerminal, ...],
    tuple[str, ...],
    tuple[AltiumCompiledNetEndpoint, ...],
    tuple[str, ...],
    tuple[AltiumCompiledNetItem, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    evidence_link_rows = [link for link in link_rows if ":bus_member_link:" in link.id]
    source_net_ids = [net.id for net in ordered_group]
    terminals = list(
        _dedupe_compiled_terminals(
            [terminal for net in ordered_group for terminal in net.terminals]
        )
    )
    _compiled_dotnet_sort(terminals, _compiled_terminal_compare)
    ordered_terminals = tuple(terminals)
    terminal_ids = _dedupe_compiled_net_values(
        [terminal.id for terminal in ordered_terminals]
    )
    endpoint_ids = _dedupe_compiled_net_values(
        [
            endpoint_id
            for net in (*ordered_group, *evidence_link_rows)
            for endpoint_id in net.endpoint_ids
        ]
    )
    endpoints = _dedupe_compiled_endpoints(
        [
            endpoint
            for net in (*ordered_group, *evidence_link_rows)
            for endpoint in net.endpoints
        ]
    )
    evidence_nets = (*ordered_group, *evidence_link_rows)
    items = _dedupe_compiled_items(
        [
            item
            for category in range(5)
            for net in evidence_nets
            for item in net.items
            if _compiled_flat_item_category(item) == category
        ]
    )
    item_ids = tuple(item.id for item in items)
    physical_document_ids = _dedupe_compiled_net_values(
        [
            document_id
            for net in ordered_group
            for document_id in net.physical_document_ids
        ]
    )
    aliases = _compiled_flat_aliases(naming_group, final_name=final_name)
    link_ids = _dedupe_compiled_net_values(
        [link_id for net in link_rows for link_id in net.link_ids]
    )
    return (
        source_net_ids,
        terminal_ids,
        ordered_terminals,
        endpoint_ids,
        endpoints,
        item_ids,
        items,
        physical_document_ids,
        aliases,
        link_ids,
    )


def _compiled_terminal_compare(
    left: AltiumCompiledNetTerminal,
    right: AltiumCompiledNetTerminal,
) -> int:
    comparison = managed_alpha_numeric_compare(left.designator, right.designator)
    if comparison:
        return comparison
    return managed_alpha_numeric_compare(left.pin, right.pin)


def _compiled_flat_item_category(item: AltiumCompiledNetItem) -> int:
    """Preserve AD collection boundaries in the unified product projection."""
    if _compiled_item_is_graphical_projection(item):
        return 3 if item.kind == "wire" else 4
    if not item.removed:
        return 0
    return 2 if item.kind == "harness_entry" else 1


def _compiled_item_is_graphical_projection(item: AltiumCompiledNetItem) -> bool:
    marker = f":graphical:{item.kind}:"
    _prefix, separator, tail = item.id.rpartition(marker)
    index_text, element_separator, element_id = tail.partition(":")
    return bool(
        separator
        and element_separator
        and index_text.isascii()
        and index_text.isdecimal()
        and element_id == item.element_id
        and item.object_id == item.element_id
    )


def _build_flattened_compiled_nets(
    physical_nets: tuple[AltiumCompiledNet, ...],
    inter_sheet_nets: tuple[AltiumCompiledNet, ...],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    physical_sheet_symbols: tuple[AltiumCompiledPhysicalSheetSymbol, ...],
    sheet_symbol_info_by_id: dict[str, "SchSheetSymbolInfo"],
    *,
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
    bus_member_names_by_logical_id: dict[str, frozenset[str]],
) -> tuple[tuple[AltiumCompiledNet, ...], dict[str, object]]:
    """Flatten staged physical/link rows into deterministic compiled net rows."""
    from .altium_netlist_model import UnionFind

    uf: UnionFind[str] = UnionFind()
    physical_by_id = {
        net.id: net for net in physical_nets if net.scope == "physical_local"
    }
    physical_document_by_id = {document.id: document for document in physical_documents}
    repeat_bus_structural_bases_by_document_id = (
        _repeat_bus_structural_bases_by_physical_document_id(physical_nets)
    )
    for physical_net_id in physical_by_id:
        uf.add_root(physical_net_id)
    source_merge_order = _CompiledSourceMergeOrder(physical_by_id)

    def merge_physical_sources(*, master_id: str, absorbed_id: str) -> None:
        uf.union(master_id, absorbed_id)
        source_merge_order.merge(master_id=master_id, absorbed_id=absorbed_id)

    hierarchy_mode = compile_options.effective_hierarchy_mode
    consume_hierarchy_links = hierarchy_mode in {
        "HIERARCHICAL",
        "STRICT_HIERARCHICAL",
    }
    power_merge_pairs = _compiled_first_list_merge_pairs(physical_by_id, hierarchy_mode)
    port_merge_pairs = _compiled_port_merge_pairs(physical_by_id, hierarchy_mode)
    effective_port_merge_pairs = _compiled_acyclic_merge_pairs(
        port_merge_pairs,
        preconnected_pairs=power_merge_pairs,
    )
    cross_sheet_merge_pairs = _compiled_hierarchical_multisheet_connector_merge_pairs(
        physical_by_id,
        hierarchy_mode,
        physical_sheet_symbols,
    )
    harness_merge_pairs = [
        *_compiled_scoped_harness_entry_merge_pairs(physical_by_id),
        *_compiled_flat_harness_entry_merge_pairs(physical_by_id, hierarchy_mode),
    ]
    bus_member_label_merge_pairs = _compiled_scoped_bus_member_label_merge_pairs(
        physical_by_id
    )
    same_name_merge_pairs = _compiled_same_name_merge_pairs(
        physical_by_id,
        hierarchy_mode,
        bus_member_names_by_logical_id,
    )
    hierarchy_link_merge_pairs = _compiled_hierarchy_link_merge_pairs(
        inter_sheet_nets,
        physical_by_id,
        consume_hierarchy_links=consume_hierarchy_links,
    )
    cross_sheet_merge_pairs = _compiled_acyclic_merge_pairs(
        cross_sheet_merge_pairs,
        preconnected_pairs=chain(
            power_merge_pairs,
            port_merge_pairs,
            harness_merge_pairs,
            bus_member_label_merge_pairs,
            same_name_merge_pairs,
            hierarchy_link_merge_pairs,
        ),
    )
    ordered_merge_pairs = (
        (
            *hierarchy_link_merge_pairs,
            *power_merge_pairs,
            *port_merge_pairs,
            *harness_merge_pairs,
            *bus_member_label_merge_pairs,
            *same_name_merge_pairs,
            *cross_sheet_merge_pairs,
        )
        if consume_hierarchy_links
        else (
            *power_merge_pairs,
            *port_merge_pairs,
            *harness_merge_pairs,
            *bus_member_label_merge_pairs,
            *same_name_merge_pairs,
            *cross_sheet_merge_pairs,
        )
    )
    for master_id, absorbed_id in ordered_merge_pairs:
        merge_physical_sources(master_id=master_id, absorbed_id=absorbed_id)
    power_merge_count = len(power_merge_pairs)
    port_merge_count = len(effective_port_merge_pairs) + len(cross_sheet_merge_pairs)
    harness_entry_merge_count = len(harness_merge_pairs)
    same_name_merge_count = len(same_name_merge_pairs)
    hierarchy_link_merge_count = len(hierarchy_link_merge_pairs)

    grouped_ids: dict[str, list[str]] = defaultdict(list)
    for physical_net_id in physical_by_id:
        grouped_ids[uf.find(physical_net_id)].append(physical_net_id)
    root_by_source_id = {
        physical_net_id: uf.find(physical_net_id) for physical_net_id in physical_by_id
    }
    source_order = {
        physical_net_id: order for order, physical_net_id in enumerate(physical_by_id)
    }
    merge_order = source_merge_order.order_index()

    link_rows_by_root = _compiled_link_rows_by_root(
        inter_sheet_nets,
        physical_by_id,
        root_by_source_id,
        consume_hierarchy_links=consume_hierarchy_links,
    )

    flat_rows: list[AltiumCompiledNet] = []
    for flat_index, root in enumerate(grouped_ids):
        source_net_ids = grouped_ids[root]
        group = [physical_by_id[source_id] for source_id in source_net_ids]
        link_rows = link_rows_by_root.get(root, [])
        naming_group = [*group, *link_rows]
        # Naming consumes raw provenance order. Terminal/source emission below
        # uses legacy merge-list order after the final name has been selected.
        name, original_name, override_name, auto_named = _compiled_flat_name(
            naming_group,
            physical_document_by_id,
            compile_options=compile_options,
            annotation=annotation,
        )
        ordered_group = _compiled_ordered_source_nets(
            group,
            hierarchy_mode=hierarchy_mode,
            source_order=source_order,
            merge_order=merge_order,
            has_hierarchy_link=bool(link_rows),
        )
        (
            source_net_ids,
            terminal_ids,
            terminals,
            endpoint_ids,
            endpoints,
            item_ids,
            items,
            physical_document_ids,
            aliases,
            link_ids,
        ) = _compiled_flat_row_source_parts(
            ordered_group,
            naming_group,
            link_rows,
            final_name=name,
        )
        if not terminals and not _compiled_terminal_less_flat_net_should_emit(
            ordered_group,
            physical_document_by_id,
            repeat_bus_structural_bases_by_document_id,
        ):
            continue
        flat_rows.append(
            AltiumCompiledNet(
                id=_compiled_flat_net_id(flat_index, name),
                name=name,
                original_name=original_name,
                override_name=override_name,
                scope="compiled_flat",
                logical_document_id=None,
                physical_document_ids=physical_document_ids,
                auto_named=auto_named,
                single_pin=len(terminals) == 1,
                aliases=aliases,
                terminal_ids=terminal_ids,
                terminals=terminals,
                endpoint_ids=endpoint_ids,
                endpoints=endpoints,
                item_ids=item_ids,
                items=items,
                source_net_ids=tuple(source_net_ids),
                link_ids=link_ids,
            )
        )

    flat_rows.extend(
        _missing_terminal_less_label_source_nets(
            flat_rows,
            physical_nets,
            start_index=len(flat_rows),
        )
    )
    flat_rows = _coalesce_terminal_less_bus_label_rows(flat_rows)
    flat_rows.extend(
        _missing_physical_sheet_symbol_interface_nets(
            flat_rows,
            physical_nets,
            physical_sheet_symbols,
            sheet_symbol_info_by_id,
            start_index=len(flat_rows),
        )
    )

    def flat_sort_key(net: AltiumCompiledNet) -> tuple[object, int, int]:
        strict_power_depth = 0
        if hierarchy_mode == "STRICT_HIERARCHICAL" and _compiled_net_has_endpoint_role(
            net, "power_port"
        ):
            strict_power_depth = max(
                (
                    len(
                        physical_document_by_id[
                            document_id
                        ].physical_instance_path.split("\\")
                    )
                    for document_id in net.physical_document_ids
                    if document_id in physical_document_by_id
                ),
                default=0,
            )
        return (
            _altium_net_total_sort_key(net.name),
            strict_power_depth,
            -len(net.link_ids),
        )

    flat_rows.sort(key=flat_sort_key, reverse=True)
    if len(physical_documents) == 1 and not inter_sheet_nets:
        flat_rows.sort(
            key=lambda net: _compiled_flat_order_bucket(
                net,
                compile_options=compile_options,
            )
        )
    flat_rows = [
        replace(net, id=_compiled_flat_net_id(index, net.name))
        for index, net in enumerate(flat_rows)
    ]

    return (
        tuple(flat_rows),
        {
            "mode": "transitive_physical_net_closure",
            "hierarchy_mode": hierarchy_mode,
            "physical_net_count": len(physical_nets),
            "inter_sheet_link_count": len(inter_sheet_nets),
            "flattened_net_count": len(flat_rows),
            "hierarchy_link_merge_count": hierarchy_link_merge_count,
            "port_merge_count": port_merge_count,
            "harness_entry_merge_count": harness_entry_merge_count,
            "power_merge_count": power_merge_count,
            "same_name_merge_count": same_name_merge_count,
        },
    )


def _sheet_entry_interface_key_from_item(
    item: object,
) -> tuple[str, str, str] | None:
    kind = str(getattr(item, "kind", "") or getattr(item, "role", "") or "").lower()
    if kind != "sheet_entry":
        return None
    physical_document_id = str(getattr(item, "physical_document_id", "") or "")
    entry_name = str(getattr(item, "name", "") or "")
    if not physical_document_id or not entry_name:
        return None
    suffix = f"_{entry_name}"
    suffix_key = dotnet_ordinal_ignore_case_key(suffix)
    for identity_name in ("element_id", "object_id"):
        identity = str(getattr(item, identity_name, "") or "")
        if not dotnet_ordinal_ignore_case_key(identity).endswith(suffix_key):
            continue
        sheet_symbol_uid = identity[: -len(suffix)]
        if sheet_symbol_uid:
            return (
                physical_document_id,
                dotnet_ordinal_ignore_case_key(sheet_symbol_uid),
                dotnet_ordinal_ignore_case_key(entry_name),
            )
    return None


def _represented_sheet_entry_interface_keys(
    flat_rows: list[AltiumCompiledNet],
) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for net in flat_rows:
        for item in net.items:
            key = _sheet_entry_interface_key_from_item(item)
            if key is not None:
                keys.add(key)
    return keys


def _represented_terminal_child_ports(
    flat_rows: list[AltiumCompiledNet],
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for net in flat_rows:
        if not net.terminals:
            continue
        for item in net.items:
            if str(getattr(item, "kind", "") or "").lower() != "port":
                continue
            physical_document_id = str(getattr(item, "physical_document_id", "") or "")
            port_name = str(getattr(item, "name", "") or "")
            if physical_document_id and port_name:
                keys.add(
                    (
                        physical_document_id,
                        dotnet_ordinal_ignore_case_key(port_name),
                    )
                )
    return keys


def _coalesce_terminal_less_bus_label_rows(
    flat_rows: list[AltiumCompiledNet],
) -> list[AltiumCompiledNet]:
    result: list[AltiumCompiledNet] = []
    index_by_name: dict[str, int] = {}
    for net in flat_rows:
        endpoint_roles = {endpoint.role for endpoint in net.endpoints}
        if (
            not net.terminals
            and _parse_bus_range(net.name)
            and endpoint_roles
            and endpoint_roles <= {"net_label"}
        ):
            key = dotnet_ordinal_ignore_case_key(net.name)
            existing_index = index_by_name.get(key)
            if existing_index is not None:
                existing = result[existing_index]
                result[existing_index] = replace(
                    existing,
                    physical_document_ids=_dedupe_compiled_net_values(
                        [
                            *existing.physical_document_ids,
                            *net.physical_document_ids,
                        ]
                    ),
                    source_net_ids=_dedupe_compiled_net_values(
                        [*existing.source_net_ids, *net.source_net_ids]
                    ),
                    endpoint_ids=_dedupe_compiled_net_values(
                        [*existing.endpoint_ids, *net.endpoint_ids]
                    ),
                    endpoints=_dedupe_compiled_endpoints(
                        [*existing.endpoints, *net.endpoints]
                    ),
                    item_ids=_dedupe_compiled_net_values(
                        [*existing.item_ids, *net.item_ids]
                    ),
                    items=_dedupe_compiled_items([*existing.items, *net.items]),
                    aliases=tuple(
                        sorted(
                            {
                                *existing.aliases,
                                *net.aliases,
                            },
                            key=_altium_net_total_sort_key,
                        )
                    ),
                )
                continue
            index_by_name[key] = len(result)
        result.append(net)
    return result


def _missing_terminal_less_label_source_nets(
    flat_rows: list[AltiumCompiledNet],
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    start_index: int,
) -> list[AltiumCompiledNet]:
    terminal_flat_source_ids = {
        source_id
        for flat_net in flat_rows
        if flat_net.terminals
        for source_id in flat_net.source_net_ids
    }
    represented_zero_keys = {
        (
            dotnet_ordinal_ignore_case_key(flat_net.name),
            tuple(flat_net.physical_document_ids),
        )
        for flat_net in flat_rows
        if not flat_net.terminals
    }
    rows: list[AltiumCompiledNet] = []
    for physical_net in physical_nets:
        if physical_net.id not in terminal_flat_source_ids:
            continue
        if physical_net.terminals:
            continue
        if not _compiled_net_label_names(physical_net):
            continue
        if not _compiled_net_has_endpoint_role(physical_net, "harness_entry"):
            continue
        endpoint_roles = {endpoint.role for endpoint in physical_net.endpoints}
        if not endpoint_roles or not endpoint_roles <= {"net_label", "harness_entry"}:
            continue
        key = (
            dotnet_ordinal_ignore_case_key(physical_net.name),
            tuple(physical_net.physical_document_ids),
        )
        if key in represented_zero_keys:
            continue
        net_label_endpoints = tuple(
            endpoint
            for endpoint in physical_net.endpoints
            if endpoint.role == "net_label"
        )
        if not net_label_endpoints:
            continue
        endpoint_ids = tuple(endpoint.id for endpoint in net_label_endpoints)
        rows.append(
            AltiumCompiledNet(
                id=_compiled_flat_net_id(start_index + len(rows), physical_net.name),
                name=physical_net.name,
                original_name=physical_net.original_name,
                override_name=physical_net.override_name,
                scope="compiled_flat",
                physical_document_ids=physical_net.physical_document_ids,
                auto_named=False,
                single_pin=False,
                endpoint_ids=endpoint_ids,
                endpoints=net_label_endpoints,
                item_ids=endpoint_ids,
                items=tuple(
                    _compiled_item_from_endpoint(endpoint)
                    for endpoint in net_label_endpoints
                ),
                source_net_ids=(physical_net.id,),
            )
        )
        represented_zero_keys.add(key)
    return rows


def _physical_sheet_symbol_interface_name(
    physical_symbol: AltiumCompiledPhysicalSheetSymbol,
    entry_name: str,
    physical_nets: tuple[AltiumCompiledNet, ...],
) -> str:
    inner_port = _parse_entry_repeat(entry_name)
    match_name = inner_port or entry_name
    parent_entry_name = (
        f"{inner_port}{physical_symbol.child_channel_index}"
        if inner_port is not None
        else entry_name
    )
    child_net = _find_physical_net_with_port(
        physical_nets,
        physical_document_id=physical_symbol.child_physical_document_id,
        port_uid="",
        port_name=match_name,
    )
    parent_net = _find_physical_net_with_sheet_entry(
        physical_nets,
        physical_document_id=physical_symbol.owner_physical_document_id,
        sheet_symbol_uid=physical_symbol.source_object_id,
        entry_name=parent_entry_name,
    )
    if parent_net is None and inner_port is not None:
        parent_net = _find_physical_net_with_sheet_entry(
            physical_nets,
            physical_document_id=physical_symbol.owner_physical_document_id,
            sheet_symbol_uid=physical_symbol.source_object_id,
            entry_name=entry_name,
        )
    if parent_net is None:
        parent_net = _find_physical_net_with_any_sheet_entry_name(
            physical_nets,
            physical_document_id=physical_symbol.owner_physical_document_id,
            entry_name=parent_entry_name,
        )
    if parent_net is None and inner_port is not None:
        parent_net = _find_physical_net_with_any_sheet_entry_name(
            physical_nets,
            physical_document_id=physical_symbol.owner_physical_document_id,
            entry_name=entry_name,
        )
    if parent_net is not None and parent_net.name:
        return parent_net.name
    if child_net is not None and child_net.name:
        return child_net.name
    return parent_entry_name


def _missing_physical_sheet_symbol_interface_nets(
    flat_rows: list[AltiumCompiledNet],
    physical_nets: tuple[AltiumCompiledNet, ...],
    physical_sheet_symbols: tuple[AltiumCompiledPhysicalSheetSymbol, ...],
    sheet_symbol_info_by_id: dict[str, "SchSheetSymbolInfo"],
    *,
    start_index: int,
) -> list[AltiumCompiledNet]:
    represented_keys = _represented_sheet_entry_interface_keys(flat_rows)
    represented_terminal_child_ports = _represented_terminal_child_ports(flat_rows)
    repeat_range_by_symbol: dict[tuple[str, str], tuple[int, int]] = {}
    repeat_indices_by_symbol: dict[tuple[str, str], list[int]] = defaultdict(list)
    for physical_symbol in physical_sheet_symbols:
        repeat_indices_by_symbol[
            (
                physical_symbol.owner_physical_document_id,
                physical_symbol.source_object_id,
            )
        ].append(physical_symbol.child_channel_index)
    for symbol_key, indices in repeat_indices_by_symbol.items():
        positive_indices = [index for index in indices if index > 0]
        if positive_indices:
            repeat_range_by_symbol[symbol_key] = (
                min(positive_indices),
                max(positive_indices),
            )
    rows: list[AltiumCompiledNet] = []
    emitted_bus_range_names: set[str] = {
        dotnet_ordinal_ignore_case_key(net.name)
        for net in flat_rows
        if _parse_bus_range(net.name)
    }
    for physical_symbol in physical_sheet_symbols:
        symbol_info = sheet_symbol_info_by_id.get(
            physical_symbol.logical_sheet_symbol_id
        )
        if symbol_info is None:
            continue
        for entry in symbol_info.entries:
            entry_name = str(
                getattr(entry, "display_name", "") or getattr(entry, "name", "") or ""
            )
            if not entry_name:
                continue
            if str(getattr(entry, "harness_type", "") or ""):
                continue
            key = (
                physical_symbol.owner_physical_document_id,
                dotnet_ordinal_ignore_case_key(physical_symbol.source_object_id),
                dotnet_ordinal_ignore_case_key(entry_name),
            )
            if key in represented_keys:
                continue
            inner_port = _parse_entry_repeat(entry_name)
            match_name = inner_port or entry_name
            if (
                inner_port is None
                and (
                    physical_symbol.child_physical_document_id,
                    dotnet_ordinal_ignore_case_key(match_name),
                )
                in represented_terminal_child_ports
            ):
                represented_keys.add(key)
                continue
            if inner_port is not None:
                repeat_start, repeat_end = repeat_range_by_symbol.get(
                    (
                        physical_symbol.owner_physical_document_id,
                        physical_symbol.source_object_id,
                    ),
                    (
                        physical_symbol.child_channel_index,
                        physical_symbol.child_channel_index,
                    ),
                )
                name = (
                    f"{inner_port}[{repeat_start}..{repeat_end}]"
                    if repeat_start != repeat_end
                    else f"{inner_port}{repeat_start}"
                )
            else:
                name = _physical_sheet_symbol_interface_name(
                    physical_symbol,
                    entry_name,
                    physical_nets,
                )
            if _parse_bus_range(name):
                name_key = dotnet_ordinal_ignore_case_key(name)
                if name_key in emitted_bus_range_names:
                    represented_keys.add(key)
                    continue
                emitted_bus_range_names.add(name_key)
            rows.append(
                AltiumCompiledNet(
                    id=_compiled_flat_net_id(start_index + len(rows), name),
                    name=name,
                    scope="compiled_flat",
                    physical_document_ids=(physical_symbol.owner_physical_document_id,),
                    auto_named=False,
                    single_pin=False,
                )
            )
            represented_keys.add(key)
    return rows


def _project_device_sheet_paths(project: "AltiumPrjPcb") -> frozenset[Path]:
    assert project.filepath is not None
    return frozenset(
        (project.filepath.parent / Path(document["path"].replace("\\", "/"))).resolve()
        for document in project.documents
        if project._document_source_sections.get(id(document), "")
        .casefold()
        .startswith("devicesheet")
    )


def _device_sheet_logical_ids(
    device_sheet_paths: frozenset[Path],
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
) -> frozenset[str]:
    return frozenset(
        logical_id
        for logical_id, schdoc in schdoc_by_logical_id.items()
        if (schdoc_filepath := getattr(schdoc, "filepath", None)) is not None
        and Path(schdoc_filepath).resolve() in device_sheet_paths
    )


def _device_sheet_saved_numbers(
    project_path: Path,
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    device_sheet_ids: frozenset[str],
) -> dict[str, str]:
    saved_numbers = _saved_project_structure_sheet_numbers(project_path)
    return {
        document.id: saved_numbers[normalized_source_path]
        for document in logical_documents
        if document.id in device_sheet_ids
        and (
            normalized_source_path := _normalize_project_reference(document.source_path)
        )
        in saved_numbers
    }


def _project_document_order(
    project: "AltiumPrjPcb",
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
) -> dict[str, int]:
    positions = {
        _normalize_project_reference(str(document.get("path", ""))): position
        for position, document in enumerate(project.documents)
        if document.get("path")
    }
    return {
        document.id: positions[normalized_source_path]
        for document in logical_documents
        if (
            normalized_source_path := _normalize_project_reference(document.source_path)
        )
        in positions
    }


def _device_sheet_numbering_inputs(
    project: "AltiumPrjPcb | None",
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    schdoc_by_logical_id: Mapping[str, "AltiumSchDoc"],
) -> tuple[frozenset[str], dict[str, str], dict[str, int]]:
    if project is None or project.filepath is None:
        return (
            frozenset(),
            {},
            {document.id: document.ordinal for document in logical_documents},
        )

    device_sheet_ids = _device_sheet_logical_ids(
        _project_device_sheet_paths(project),
        schdoc_by_logical_id,
    )
    return (
        device_sheet_ids,
        _device_sheet_saved_numbers(
            project.filepath,
            logical_documents,
            device_sheet_ids,
        ),
        _project_document_order(project, logical_documents),
    )


def compile_design(
    design: "AltiumDesign",
    *,
    allow_device_sheet_editing: bool = False,
) -> AltiumCompiledDesign:
    """Compile a project design into its resolved schematic model."""
    sources = [_compiler_document_source(schdoc) for schdoc in design.schdocs]
    project = design.project
    project_base_dir = project.filepath.parent if project and project.filepath else None
    options = design._options or NetlistOptions()
    effective_hierarchy_mode = design._resolve_design_effective_scope(
        options, sources=sources
    )
    compile_options = _compile_options_from_netlist_options(
        options,
        effective_hierarchy_mode=effective_hierarchy_mode,
        project=project,
        allow_device_sheet_editing=allow_device_sheet_editing,
    )
    annotation = load_project_annotation(project.filepath if project else None)
    differential_pair_suffixes = _project_differential_pair_suffixes(project)
    saved_structure_top_level_file_name = (
        _project_saved_structure_top_level_file_name(project)
        if effective_hierarchy_mode in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}
        else None
    )
    (
        logical_documents,
        sheet_symbols,
        sheet_symbol_info_by_id,
        diagnostics,
    ) = _build_logical_and_symbol_rows(
        sources,
        project_base_dir=project_base_dir,
        saved_structure_top_level_file_name=saved_structure_top_level_file_name,
    )
    schdoc_by_logical_id = _schdocs_by_logical_id(
        sources,
        project_base_dir=project_base_dir,
    )
    (
        device_sheet_logical_ids,
        device_sheet_numbers_by_logical_id,
        document_order_by_logical_id,
    ) = _device_sheet_numbering_inputs(
        project,
        logical_documents,
        schdoc_by_logical_id,
    )
    _require_source_schdocs(logical_documents, schdoc_by_logical_id)
    (
        physical_documents,
        components,
        component_body_evidence,
        sheet_symbols,
        physical_sheet_symbols,
        physical_diagnostics,
    ) = _build_physical_and_component_rows(
        sources,
        logical_documents,
        sheet_symbols,
        sheet_symbol_info_by_id,
        channel_designator_format=_effective_channel_designator_format(
            compile_options.channel_designator_format,
        ),
        compile_options=compile_options,
        annotation=annotation,
        options=options,
        project_base_dir=project_base_dir,
        device_sheet_logical_ids=device_sheet_logical_ids,
        device_sheet_numbers_by_logical_id=device_sheet_numbers_by_logical_id,
        document_order_by_logical_id=document_order_by_logical_id,
    )

    physical_document_by_id = {document.id: document for document in physical_documents}
    physical_count_by_logical_id = _physical_count_by_logical_id(physical_documents)
    channel_global_indices = _channel_global_indices(physical_document_by_id)
    channel_differentiate_values = _channel_differentiate_values_for_room_style(
        physical_document_by_id,
        channel_global_indices=channel_global_indices,
        style=compile_options.channel_room_naming_style,
    )
    physical_documents = tuple(
        replace(
            document,
            physical_room_name=(
                document.room_name
                if document.parent_id is None
                else _component_naming_room_name(
                    document,
                    physical_document_by_id,
                    physical_count_by_logical_id=physical_count_by_logical_id,
                    channel_global_indices=channel_global_indices,
                    channel_differentiate_values=channel_differentiate_values,
                    compile_options=compile_options,
                )
            ),
        )
        for document in physical_documents
    )
    (
        local_nets,
        local_net_counts,
        local_harness_endpoint_stats,
        component_pin_evidence,
        local_connectivity_diagnostics,
    ) = _compile_local_connectivity(
        sources,
        logical_documents,
        options=options,
        effective_hierarchy_mode=effective_hierarchy_mode,
        project_base_dir=project_base_dir,
    )
    (
        inferred_harness_types_by_physical_id,
        repeat_values_by_parent_endpoint,
    ) = _physical_harness_occurrence_context(
        physical_documents,
        sheet_symbols,
        sheet_symbol_info_by_id,
        schdoc_by_logical_id,
    )
    physical_nets = _elaborate_local_connectivity(
        local_nets,
        physical_documents,
        components,
        component_pin_evidence=component_pin_evidence,
        channel_designator_format=_effective_channel_designator_format(
            compile_options.channel_designator_format,
        ),
        compile_options=compile_options,
        annotation=annotation,
        differential_pair_suffixes=differential_pair_suffixes,
        inferred_harness_types_by_physical_id=(inferred_harness_types_by_physical_id),
        repeat_values_by_parent_endpoint=repeat_values_by_parent_endpoint,
        name_hierarchy=_compiled_name_hierarchy(
            logical_documents,
            sheet_symbols,
            physical_documents,
            compile_options=compile_options,
        ),
    )
    inter_sheet_nets, inter_sheet_stats, inter_sheet_diagnostics = (
        _build_inter_sheet_link_nets(
            sources,
            physical_documents,
            sheet_symbols,
            sheet_symbol_info_by_id,
            physical_nets,
            effective_hierarchy_mode=effective_hierarchy_mode,
            project_base_dir=project_base_dir,
        )
    )
    flat_nets, net_flattening_stats = _build_flattened_compiled_nets(
        physical_nets,
        inter_sheet_nets,
        physical_documents,
        physical_sheet_symbols,
        sheet_symbol_info_by_id,
        compile_options=compile_options,
        annotation=annotation,
        bus_member_names_by_logical_id=_bus_member_names_by_logical_id(
            schdoc_by_logical_id,
        ),
    )
    components = _compiled_component_pin_counts(
        components,
        flat_nets,
        component_pin_evidence,
        {document.id: document.physical_room_name for document in physical_documents},
    )
    published_physical_nets = _published_physical_nets(physical_nets)
    net_flattening_stats["physical_net_count"] = len(published_physical_nets)
    compiled_nets = (
        *local_nets,
        *published_physical_nets,
        *inter_sheet_nets,
        *flat_nets,
    )
    compiled_nets = tuple(
        replace(
            net,
            endpoints=tuple(
                replace(endpoint, role="power_port")
                if endpoint.role == "harness_power"
                else endpoint
                for endpoint in net.endpoints
            ),
        )
        for net in compiled_nets
    )
    physical_documents, _ = _apply_sheet_number_annotations(
        physical_documents,
        annotation,
    )
    logical_documents = tuple(
        replace(
            document,
            local_net_count=local_net_counts.get(document.id, 0),
        )
        for document in logical_documents
    )
    top_level_document = _primary_top_level_document(
        logical_documents,
        sheet_symbols,
    )
    top_level_logical_id = top_level_document.id if top_level_document else None
    top_level_physical_id = next(
        (
            document.id
            for document in physical_documents
            if document.logical_document_id == top_level_logical_id
        ),
        physical_documents[0].id if physical_documents else None,
    )
    compile_block: dict[str, object] = {
        "generator": COMPILED_DESIGN_GENERATOR,
        "implementation": "python_compiled_design",
        "status": "complete",
        "hierarchy_mode": effective_hierarchy_mode,
        "top_level_logical_document_id": top_level_logical_id,
        "top_level_physical_document_id": top_level_physical_id,
        "diagnostic_policy": {
            "unresolved_child_links": "error",
            "physical_tree_phase": _UNRESOLVED_CHILD_ERROR_PHASE,
        },
        "local_connectivity": local_harness_endpoint_stats,
        "inter_sheet_linking": inter_sheet_stats,
        "net_flattening": net_flattening_stats,
        "physical_sheet_symbol_count": len(physical_sheet_symbols),
    }
    compile_diagnostics = [*diagnostics, *physical_diagnostics]
    compile_diagnostics.extend(local_connectivity_diagnostics)
    compile_diagnostics.extend(inter_sheet_diagnostics)
    physical_documents = _managed_physical_document_order(physical_documents)

    compiled_design = AltiumCompiledDesign(
        schema=COMPILED_DESIGN_SCHEMA,
        source_path=_source_path_for_project(project),
        options=compile_options,
        annotation=_annotation_state(annotation),
        logical_documents=logical_documents,
        physical_documents=physical_documents,
        sheet_symbols=sheet_symbols,
        physical_sheet_symbols=physical_sheet_symbols,
        components=components,
        nets=compiled_nets,
        diagnostics=tuple(compile_diagnostics),
        compile=compile_block,
    )
    from .altium_compiled_schematic_graph_projection import (
        build_compiled_schematic_graph,
    )

    projection_diagnostics: list[AltiumCompileDiagnostic] = []
    graph, physical_page_metadata = build_compiled_schematic_graph(
        design,
        compiled_design,
        component_body_evidence=component_body_evidence,
        compile_diagnostics=projection_diagnostics,
        source_documents=sources,
    )
    return replace(
        compiled_design,
        diagnostics=(*compiled_design.diagnostics, *projection_diagnostics),
        compiled_schematic_graph=graph,
        physical_page_metadata=physical_page_metadata,
        _component_body_evidence=component_body_evidence,
    )


__all__ = ["compile_design"]
