"""Build compiled schematic design models."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .altium_annotation import (
    AnnotationFile,
    DesignatorAnnotation,
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
from .altium_component_kind import (
    component_kind_includes_in_bom,
    component_kind_includes_in_netlist,
)
from .altium_netlist_common import (
    _altium_net_total_sort_key,
    _component_part_alpha_suffix,
    _natural_sort_key,
    _pin_electrical_to_pintype,
    _resolve_component_display_value,
    _sheet_entry_display_name as _inter_sheet_entry_display_name,
)
from .altium_compiled_design_support import (
    RoomDetails,
    _build_child_harness_entry_map,
    _build_port_location_map,
    _build_room_details,
    _build_wire_endpoint_map,
    _connected_signal_harness_indexes,
    _harness_connector_master_entry_point,
    _harness_entry_connection_point,
    _parse_entry_repeat,
    _signal_harness_ids,
    _strip_diff_pair_suffix,
    apply_channel_pattern,
    find_harness_bundle_info,
    find_harness_port_name,
)
from .altium_netlist_wire_connectivity import (
    AnalyserNetItemKind,
    RootPoint,
    altium_internal_tolerance_for_display_unit,
    analyser_net_item_kind,
    build_wire_graph as _build_wire_graph,
)
from .altium_netlist_model import NetEndpoint
from .altium_netlist_options import NetlistOptions
from .altium_netlist_single_sheet import (
    AltiumNetlistSingleSheetCompiler,
    _sheet_entry_precise_connection_point,
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
    from .altium_schdoc import AltiumSchDoc
    from .altium_schdoc_info import (
        SchComponentInfo,
        SchPinInfo,
        SchPortInfo,
        SchSheetSymbolInfo,
    )

_REPEAT_PATTERN = re.compile(
    r"^REPEAT\s*\(\s*([^,]+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$",
    re.IGNORECASE,
)
_BUS_RANGE_PATTERN = re.compile(r"^(.+)\[(\d+)\.\.(\d+)\]$")
_UNRESOLVED_CHILD_ERROR_PHASE = "python-physical-tree-instantiation"
_DEFAULT_CHANNEL_DESIGNATOR_FORMAT = "$Component$ChannelAlpha"


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
    channel_index: int
    channel_prefix: str | None
    channel_alpha: str | None
    room_name: str
    sheet_number: str | None
    document_number: str | None


@dataclass(frozen=True, slots=True)
class _HarnessWireEndpoint:
    wire_id: str
    name: str
    element_id: str
    parent_id: str
    connection_point: tuple[int, int]


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


def _raw_document_parameter(schdoc: "AltiumSchDoc", name: str) -> str | None:
    for parameter in getattr(schdoc, "parameters", ()):
        if str(getattr(parameter, "name", "")).casefold() == name.casefold():
            value = getattr(parameter, "text", "")
            return str(value) if value else None
    return None


def _has_document_parameter(schdoc: "AltiumSchDoc", name: str) -> bool:
    return any(
        str(getattr(parameter, "name", "")).casefold() == name.casefold()
        for parameter in getattr(schdoc, "parameters", ())
    )


def _resolved_document_parameter(
    schdoc: "AltiumSchDoc",
    name: str,
    project_parameters: dict[str, str],
) -> str | None:
    for parameter in getattr(schdoc, "parameters", ()):
        if str(getattr(parameter, "name", "")).casefold() != name.casefold():
            continue
        value = str(getattr(parameter, "text", "") or "")
        if value and value != "*":
            return value
        for project_name, project_value in project_parameters.items():
            if project_name.casefold() != name.casefold():
                continue
            resolved = str(project_value or "")
            return resolved if resolved and resolved != "*" else None
        return None
    return None


def _preserved_device_sheet_number(
    schdoc: "AltiumSchDoc",
    logical_id: str,
    saved_numbers: Mapping[str, str] | None,
) -> str | None:
    sheet_number = (saved_numbers or {}).get(logical_id)
    if sheet_number is not None:
        return sheet_number
    sheet_number = _raw_document_parameter(schdoc, "SheetNumber")
    if sheet_number is not None:
        return sheet_number
    source_sheet = getattr(schdoc, "sheet", None)
    source_value = getattr(source_sheet, "sheet_number", None)
    return str(source_value) if source_value not in {None, ""} else None


def _sheet_numbers_by_logical_id(
    schdocs: list["AltiumSchDoc"],
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
        return document_order.get(logical_id, document.ordinal), document.ordinal

    child_ids_by_parent: dict[str, set[str]] = defaultdict(set)
    for symbol in sheet_symbols:
        if symbol.child_logical_document_id is not None:
            child_ids_by_parent[symbol.logical_document_id].add(
                symbol.child_logical_document_id
            )

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
    top_candidates = [
        document for document in root_documents if document.is_top_level_candidate
    ]
    numbering_documents = top_candidates or root_documents or list(logical_documents)
    if not numbering_documents:
        return result
    top_document = numbering_documents[0]
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
    schdocs: list["AltiumSchDoc"],
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
    sheet_numbers_by_logical_id: dict[str, str],
    document_numbers_by_logical_id: dict[str, str],
) -> tuple[AltiumCompiledPhysicalDocument, ...]:
    updated_documents: list[AltiumCompiledPhysicalDocument] = []
    for document in physical_documents:
        physical_index_path = document.sheet_number or ""
        sheet_number_base = sheet_numbers_by_logical_id.get(
            document.logical_document_id
        )
        if sheet_number_base == "*":
            sheet_number = physical_index_path or None
        elif sheet_number_base is not None:
            sheet_number = f"{sheet_number_base}{physical_index_path}"
        else:
            sheet_number = None
        updated_documents.append(
            replace(
                document,
                sheet_number=sheet_number,
                document_number=document_numbers_by_logical_id.get(
                    document.logical_document_id
                ),
            )
        )
    return tuple(updated_documents)


def _normalize_project_reference(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lower()


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
        if component.logical_designator and component.physical_designator:
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
        if not component.logical_designator or not component.physical_designator:
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
    return int(getattr(getattr(component, "record", None), "part_count", 1) or 1)


def _component_record_current_part_id(component: object) -> int:
    return int(getattr(getattr(component, "record", None), "current_part_id", 1) or 1)


def _designator_binds_multipart_records(designator: str) -> bool:
    """Return whether Altium annotation can bind separate placed part records."""
    return bool(designator) and "?" not in designator


def _local_multipart_designators(schdoc: "AltiumSchDoc") -> set[str]:
    placed_part_ids_by_designator: dict[str, set[int]] = defaultdict(set)
    for component in schdoc.get_components():
        logical_designator = str(component.designator or "")
        if _designator_binds_multipart_records(logical_designator):
            placed_part_ids_by_designator[logical_designator].add(
                _component_record_current_part_id(component)
            )
    return {
        designator
        for designator, part_ids in placed_part_ids_by_designator.items()
        if len({part_id for part_id in part_ids if part_id > 0}) > 1
    }


def _component_pin_full_designators_by_pin(
    schdoc: "AltiumSchDoc",
    *,
    multipart_designators: set[str] | frozenset[str] | None = None,
) -> dict[tuple[str, str], str]:
    local_multipart_designators = _local_multipart_designators(schdoc)
    effective_multipart_designators = set(multipart_designators or ()) | (
        local_multipart_designators
    )
    result: dict[tuple[str, str], str] = {}
    for component in schdoc.get_components():
        logical_designator = str(component.designator or "")
        if not logical_designator:
            continue
        component_record = getattr(component, "record", None)
        part_count = int(getattr(component_record, "part_count", 1) or 1)
        component_part_id = _component_record_current_part_id(component)
        for pin in component.pins:
            pin_number = str(getattr(pin, "designator", "") or "")
            if not pin_number:
                continue
            owner_part_id_raw = getattr(pin, "owner_part_id", None)
            owner_part_id = int(
                owner_part_id_raw
                or (
                    1
                    if logical_designator in effective_multipart_designators
                    else component_part_id
                )
                or 1
            )
            suffix = (
                _component_part_alpha_suffix(
                    part_count=part_count,
                    current_part_id=owner_part_id,
                )
                if logical_designator in effective_multipart_designators
                else ""
            )
            result[(logical_designator, pin_number)] = f"{logical_designator}{suffix}"
    return result


def _compiled_component_source_groups(
    components: tuple["SchComponentInfo", ...],
) -> tuple[tuple["SchComponentInfo", ...], ...]:
    groups: list[list[SchComponentInfo]] = []
    multipart_groups: dict[str, list[tuple[int, set[int]]]] = defaultdict(list)
    for component in components:
        designator = str(getattr(component, "designator", "") or "")
        if (
            not _designator_binds_multipart_records(designator)
            or _component_record_part_count(component) <= 1
        ):
            groups.append([component])
            continue
        part_id = _component_record_current_part_id(component)
        candidate_groups = multipart_groups[designator.lower()]
        candidate_group = next(
            (
                candidate
                for candidate in candidate_groups
                if part_id not in candidate[1]
            ),
            None,
        )
        if candidate_group is None:
            candidate_groups.append((len(groups), {part_id}))
            groups.append([component])
            continue
        group_index, part_ids = candidate_group
        groups[group_index].append(component)
        part_ids.add(part_id)
    return tuple(tuple(group) for group in groups)


def _compiled_component_group_representative(
    group: tuple["SchComponentInfo", ...],
) -> "SchComponentInfo":
    return min(
        group,
        key=lambda component: (
            _component_record_current_part_id(component),
            str(getattr(component, "unique_id", "") or ""),
        ),
    )


def _compiled_component_group_pin_count(group: tuple[object, ...]) -> int:
    return len(
        {
            str(getattr(pin, "designator", "") or "")
            for component in group
            for pin in (getattr(component, "pins", ()) or ())
            if getattr(pin, "designator", "")
        }
    )


def _compiled_component_group_all_pin_count(group: tuple[object, ...]) -> int:
    pin_designators: set[str] = set()
    stored_counts: list[int] = []
    for component in group:
        record = getattr(component, "record", None)
        stored_count = int(getattr(record, "all_pin_count", 0) or 0)
        if stored_count > 0:
            stored_counts.append(stored_count)
        display_mode = int(getattr(component, "display_mode", 0) or 0)
        for pin in getattr(record, "pins", ()) or ():
            pin_mode = int(getattr(pin, "owner_part_display_mode", 0) or 0)
            designator = str(getattr(pin, "designator", "") or "")
            if pin_mode == display_mode and designator:
                pin_designators.add(designator)
    if stored_counts:
        return max(stored_counts)
    return len(pin_designators)


def _collapse_multipart_component_rows(
    component_rows: list[AltiumCompiledComponent],
) -> list[AltiumCompiledComponent]:
    result: list[AltiumCompiledComponent] = []
    groups_by_designator: dict[str, list[tuple[int, set[int], int]]] = {}
    for component in component_rows:
        if component.part_count <= 1 or not _designator_binds_multipart_records(
            component.physical_designator
        ):
            result.append(component)
            continue
        key = component.physical_designator.lower()
        candidate_groups = groups_by_designator.setdefault(key, [])
        existing_index = None
        group_index = None
        representative_part_id = component.current_part_id
        for candidate_index, (
            result_index,
            part_ids,
            candidate_representative_part_id,
        ) in enumerate(candidate_groups):
            if component.current_part_id not in part_ids:
                existing_index = result_index
                group_index = candidate_index
                representative_part_id = candidate_representative_part_id
                part_ids.add(component.current_part_id)
                break
        if existing_index is None:
            candidate_groups.append(
                (
                    len(result),
                    {component.current_part_id},
                    component.current_part_id,
                )
            )
            result.append(replace(component, current_part_id=1))
            continue
        existing = result[existing_index]
        representative = (
            component
            if component.current_part_id < representative_part_id
            else existing
        )
        if component.current_part_id < representative_part_id:
            assert group_index is not None
            result_index, part_ids, _ = candidate_groups[group_index]
            candidate_groups[group_index] = (
                result_index,
                part_ids,
                component.current_part_id,
            )
        result[existing_index] = replace(
            representative,
            pin_count=min(
                max(existing.all_pin_count, component.all_pin_count),
                existing.pin_count + component.pin_count,
            ),
            all_pin_count=max(existing.all_pin_count, component.all_pin_count),
            current_part_id=1,
            diagnostics=(*existing.diagnostics, *component.diagnostics),
            _project_multipart_collapsed=True,
        )
    return result


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
        sheet_number=physical_document.sheet_number or "",
        document_number=physical_document.document_number or "",
    )


def _physical_auto_name_from_terminal(
    local_net: AltiumCompiledNet,
    designator_map: dict[str, str],
) -> str | None:
    candidates: list[str] = []
    for terminal in local_net.terminals:
        if not terminal.designator or not terminal.pin:
            continue
        designator = designator_map.get(terminal.designator)
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


def _physical_channel_net_name(
    local_net: AltiumCompiledNet,
    physical_document: AltiumCompiledPhysicalDocument,
    *,
    designator_map: dict[str, str],
    channel_designator_format: str,
    auto_name_designator_map: dict[str, str] | None = None,
    room_channel_index_override: str | None = None,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument] | None = None,
    physical_instance_offsets: dict[str, int] | None = None,
    physical_count_by_logical_id: dict[str, int] | None = None,
    compile_options: AltiumProjectCompileOptions | None = None,
) -> str:
    if physical_document.parent_sheet_symbol_id is None:
        return local_net.name
    if (
        compile_options is not None
        and compile_options.effective_hierarchy_mode == "GLOBAL"
        and any(endpoint.role == "power_port" for endpoint in local_net.endpoints)
    ):
        return local_net.name
    if local_net.auto_named:
        return (
            _physical_auto_name_from_terminal(
                local_net,
                auto_name_designator_map or designator_map,
            )
            or local_net.name
        )
    if (
        physical_count_by_logical_id is not None
        and physical_count_by_logical_id.get(physical_document.logical_document_id, 0)
        <= 1
    ):
        return local_net.name
    if not physical_document.channel_alpha:
        return local_net.name

    base_name, diff_suffix = _strip_diff_pair_suffix(local_net.name)
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
            compile_options=compile_options,
            channel_designator_format=channel_designator_format,
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
        return (
            apply_channel_pattern(channel_designator_format, room, base_name)
            + diff_suffix
        )
    if physical_document.channel_alpha:
        return f"{base_name}{physical_document.channel_alpha}{diff_suffix}"
    return local_net.name


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


def _replace_endpoint_net_name(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    source_name: str,
    target_name: str,
) -> AltiumCompiledNetEndpoint:
    if endpoint.role != "net_label" or not source_name or not target_name:
        return endpoint
    if endpoint.name != source_name:
        return endpoint
    return replace(endpoint, name=target_name)


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
) -> AltiumCompiledNetTerminal:
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
    designator = (
        designator_map.get(terminal.designator, terminal.designator)
        if terminal.designator
        else terminal.designator
    )
    return AltiumCompiledNetTerminal(
        id=physical_terminal_id,
        designator=designator,
        pin=terminal.pin,
        pin_name=terminal.pin_name,
        pin_type=terminal.pin_type,
        _source_component_uid=terminal._source_component_uid,
        _source_pin_uid=terminal._source_pin_uid,
        _source_owner_part_id=terminal._source_owner_part_id,
    )


def _compiled_endpoint_for_physical(
    endpoint: AltiumCompiledNetEndpoint,
    *,
    source_id: str,
    source_prefix: str,
    target_id: str,
    target_prefix: str,
    source_name: str,
    target_name: str,
    designator_map: dict[str, str],
) -> AltiumCompiledNetEndpoint:
    endpoint_id = _replace_compiled_net_id_prefix_precomputed(
        endpoint.id,
        source_id=source_id,
        source_prefix=source_prefix,
        target_id=target_id,
        target_prefix=target_prefix,
    )
    designator = endpoint.designator
    if designator:
        physical_designator = designator_map.get(designator)
        if physical_designator is not None:
            endpoint_id = _replace_pin_endpoint_id_designator(
                endpoint_id,
                designator_map,
            )
            designator = physical_designator
    name = endpoint.name
    if endpoint.role == "net_label" and endpoint.name == source_name and target_name:
        name = target_name
    return AltiumCompiledNetEndpoint(
        id=endpoint_id,
        role=endpoint.role,
        element_id=endpoint.element_id,
        object_id=endpoint.object_id,
        name=name,
        parent_id=endpoint.parent_id,
        designator=designator,
        pin=endpoint.pin,
        pin_name=endpoint.pin_name,
        connection_point=endpoint.connection_point,
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
    source_name: str,
    target_name: str,
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
        source_name=source_name,
        target_name=target_name,
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
    source_name: str,
    target_name: str,
    designator_map: dict[str, str],
    physical_document_id: str,
) -> AltiumCompiledNetItem:
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
    designator = (
        designator_map.get(item.designator, item.designator)
        if item.designator
        else item.designator
    )
    name = item.name
    if item.kind == "net_label" and item.name == source_name and target_name:
        name = target_name
    return AltiumCompiledNetItem(
        id=item_id,
        kind=item.kind,
        name=name,
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
    )


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
    clean_object_id = str(object_id or "").lower()
    if not clean_role or not clean_object_id:
        return False
    return any(
        endpoint.role.lower() == clean_role
        and (
            endpoint.element_id.lower() == clean_object_id
            or endpoint.object_id.lower() == clean_object_id
        )
        for endpoint in net.endpoints
    )


def _schdocs_by_logical_id(
    schdocs: list["AltiumSchDoc"],
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
    schdocs: list["AltiumSchDoc"],
    *,
    project_base_dir: Path | None,
) -> tuple[
    dict[str, tuple["AltiumSchDoc", ...]], dict[str, tuple["AltiumSchDoc", ...]]
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
            by_file_name[file_name.lower()].append(schdoc)
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


def _compile_mask_bounds(
    schdoc: "AltiumSchDoc",
) -> tuple[tuple[int, int, int, int], ...]:
    collect = getattr(schdoc, "_collect_compile_mask_bounds", None)
    if not callable(collect):
        return ()
    return tuple(cast(Iterable[tuple[int, int, int, int]], collect()))


def _point_in_compile_mask(
    bounds: tuple[tuple[int, int, int, int], ...],
    x: int,
    y: int,
) -> bool:
    return any(
        min_x <= x <= max_x and min_y <= y <= max_y
        for min_x, min_y, max_x, max_y in bounds
    )


def _component_includes_in_netlist(
    component: "SchComponentInfo",
    compile_mask_bounds: tuple[tuple[int, int, int, int], ...],
) -> bool:
    if not component.designator:
        return False
    includes = getattr(component, "includes_in_netlist", None)
    if callable(includes):
        participates = bool(includes())
    else:
        participates = component_kind_includes_in_netlist(component.component_kind)
    if not participates:
        return False
    x, y = _component_location(component)
    return not _point_in_compile_mask(compile_mask_bounds, x, y)


def _repeat_parts(designator: str) -> tuple[bool, str | None, int | None, int | None]:
    match = _REPEAT_PATTERN.match(str(designator or "").strip())
    if match is None:
        return False, None, None, None
    return True, match.group(1).strip(), int(match.group(2)), int(match.group(3))


def _parse_bus_range(name: str) -> tuple[str, ...]:
    match = _BUS_RANGE_PATTERN.match(name)
    if match is None:
        return ()
    prefix = match.group(1)
    start = int(match.group(2))
    end = int(match.group(3))
    if start <= end:
        return tuple(f"{prefix}{index}" for index in range(start, end + 1))
    return tuple(f"{prefix}{index}" for index in range(start, end - 1, -1))


def _bus_range_base_name(name: str) -> str | None:
    match = _BUS_RANGE_PATTERN.match(name)
    if match is None:
        return None
    return match.group(1)


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
) -> _PhysicalChildInstance:
    channel_index = _room_channel_index(room, fallback_channel_index)
    return _PhysicalChildInstance(
        instance_key=instance_key,
        path_segment=room.room_name,
        channel_index=channel_index,
        channel_prefix=room.channel_prefix or None,
        channel_alpha=room.channel_alpha or None,
        room_name=room.room_name,
        sheet_number=room.sheet_number or None,
        document_number=room.document_number or None,
    )


def _single_child_instance(symbol: AltiumCompiledSheetSymbol) -> _PhysicalChildInstance:
    child_filename = symbol.child_source_path or symbol.child_filename
    segment = symbol.designator or Path(child_filename).stem or symbol.id
    return _PhysicalChildInstance(
        instance_key="single",
        path_segment=segment,
        channel_index=0,
        channel_prefix=None,
        channel_alpha=None,
        room_name=segment,
        sheet_number=None,
        document_number=None,
    )


def _physical_child_instances(
    symbol: AltiumCompiledSheetSymbol,
    multi_ref_room: RoomDetails | None,
    repeat_channel_values: dict[tuple[str, int], int],
    *,
    new_indexing: bool,
) -> tuple[_PhysicalChildInstance, ...]:
    if (
        symbol.is_repeat
        and symbol.repeat_prefix
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
                (symbol.id, repeat_value),
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
            room.sheet_number = str(sheets_index)
            room.document_number = str(sheets_index)
            instances.append(
                _physical_child_instance_from_room(
                    instance_key=f"repeat:{repeat_value}",
                    room=room,
                    fallback_channel_index=channel_value,
                )
            )
        return tuple(instances)

    if multi_ref_room is not None:
        return (
            _physical_child_instance_from_room(
                instance_key=f"channel:{multi_ref_room.channel_index}",
                room=multi_ref_room,
                fallback_channel_index=1,
            ),
        )

    return (_single_child_instance(symbol),)


def _repeat_parent_rows_by_child(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> dict[str, list[tuple[str, int, str, bool]]]:
    parent_rows_by_child: dict[
        str,
        list[tuple[str, int, str, bool]],
    ] = defaultdict(list)
    for symbol in sheet_symbols:
        child_id = symbol.child_logical_document_id
        if child_id is None:
            continue
        if (
            symbol.is_repeat
            and symbol.repeat_prefix is not None
            and symbol.repeat_start is not None
            and symbol.repeat_end is not None
        ):
            for channel_value in range(symbol.repeat_start, symbol.repeat_end + 1):
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
    rows = sorted(parent_rows, key=lambda row: (row[0].casefold(), row[1]))
    previous_name = ""
    next_value = 0 if new_indexing else 1
    for instance_name, channel_value, symbol_id, is_repeat in rows:
        if not is_repeat:
            previous_name = ""
            next_value = 0 if new_indexing else 1
            continue

        same_instance = instance_name.casefold() == previous_name.casefold()
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
        previous_name = instance_name
        next_value = updated_value + 1
    return result


def _repeat_channel_values(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    *,
    new_indexing: bool,
) -> dict[tuple[str, int], int]:
    """Apply the AD26 channel-details updater to repeat parent entries."""

    result: dict[tuple[str, int], int] = {}
    for parent_rows in _repeat_parent_rows_by_child(sheet_symbols).values():
        result.update(
            _updated_repeat_values_for_child(
                parent_rows,
                new_indexing=new_indexing,
            )
        )
    return result


def _multi_reference_channel_rooms(
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> dict[str, RoomDetails]:
    symbols_by_parent_and_child: dict[
        tuple[str, str], list[AltiumCompiledSheetSymbol]
    ] = defaultdict(list)
    for symbol in sheet_symbols:
        if symbol.child_logical_document_id is not None and not symbol.is_repeat:
            symbols_by_parent_and_child[
                (symbol.logical_document_id, symbol.child_logical_document_id)
            ].append(symbol)

    rooms_by_symbol_id: dict[str, RoomDetails] = {}
    for symbols in symbols_by_parent_and_child.values():
        if len(symbols) <= 1:
            continue
        ordered_symbols = sorted(
            symbols,
            key=lambda symbol: _natural_sort_key(symbol.designator),
        )
        for instance_offset, symbol in enumerate(ordered_symbols):
            room_name = symbol.designator or f"Channel{instance_offset + 1}"
            rooms_by_symbol_id[symbol.id] = _build_room_details(
                room_name,
                instance_offset,
                sheet_designator=symbol.designator,
            )
    return rooms_by_symbol_id


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
        sheet_number=document.sheet_number or inferred_room.sheet_number,
        document_number=document.document_number or inferred_room.document_number,
    )


def _compiled_alpha_index(index: int) -> str:
    if 1 <= index <= 26:
        return chr(ord("A") + index - 1)
    return str(index)


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
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
) -> bool:
    """True when the document was instantiated by a ``Repeat()`` sheet symbol.

    A non-repeat sheet symbol yields exactly one child instance per physical
    parent instance, so sibling documents sharing
    ``(parent_id, parent_sheet_symbol_id)`` can only come from the repeat
    branch of ``_physical_child_instances``.
    """
    if document.parent_sheet_symbol_id is None:
        return False
    return any(
        row.id != document.id
        and row.parent_id == document.parent_id
        and row.parent_sheet_symbol_id == document.parent_sheet_symbol_id
        for row in physical_document_by_id.values()
    )


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
    match = re.match(r"^(.*?)(\d+)$", room_name)
    if match is None:
        return None
    value = document.channel_index
    if compile_options.new_indexing_of_sheet_symbols:
        value += 1
    return f"{match.group(1)}{_compiled_alpha_index(value)}"


def _documents_sharing_room_name(
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    logical_document_id: str,
    room_name: str,
) -> list[AltiumCompiledPhysicalDocument]:
    """Physical instances of a logical document with the same effective room name."""
    lowered = room_name.lower()
    return [
        physical_document
        for physical_document in physical_document_by_id.values()
        if (
            physical_document.logical_document_id == logical_document_id
            and (
                physical_document.room_name or Path(physical_document.file_name).stem
            ).lower()
            == lowered
        )
    ]


def _component_naming_room_name(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    physical_instance_offsets: dict[str, int],
    physical_count_by_logical_id: dict[str, int],
    compile_options: AltiumProjectCompileOptions,
) -> str:
    if compile_options.effective_hierarchy_mode in {"FLAT", "GLOBAL"}:
        return document.physical_instance_path.rsplit("/", 1)[-1]
    room_name = document.room_name or Path(document.file_name).stem
    style = compile_options.channel_room_naming_style
    if style in {
        ChannelRoomNamingStyle.FLAT_NUMERIC_WITH_NAMES,
        ChannelRoomNamingStyle.FLAT_ALPHA_WITH_NAMES,
    }:
        matching_documents = _documents_sharing_room_name(
            physical_document_by_id,
            logical_document_id=document.logical_document_id,
            room_name=room_name,
        )
        if len(matching_documents) <= 1:
            return (
                _repeat_channel_alpha_room_name(
                    document,
                    physical_document_by_id,
                    compile_options=compile_options,
                )
                or room_name
            )
        matching_documents.sort(
            key=lambda physical_document: (
                _natural_sort_key(physical_document.physical_instance_path),
                physical_instance_offsets.get(physical_document.id, 0),
            )
        )
        instance_index = next(
            index
            for index, physical_document in enumerate(matching_documents, start=1)
            if physical_document.id == document.id
        )
        return (
            f"{room_name}{_compiled_room_suffix(instance_index, style=style, depth=0)}"
        )

    path_documents: list[AltiumCompiledPhysicalDocument] = []
    current: AltiumCompiledPhysicalDocument | None = document
    while current is not None:
        if (
            current.parent_sheet_symbol_id is not None
            and physical_count_by_logical_id.get(current.logical_document_id, 0) > 1
        ):
            path_documents.append(current)
        current = physical_document_by_id.get(current.parent_id or "")
    if not path_documents:
        return room_name
    if len(path_documents) == 1:
        path_room_name = (
            path_documents[0].room_name or Path(path_documents[0].file_name).stem
        )
        duplicate_room_count = len(
            _documents_sharing_room_name(
                physical_document_by_id,
                logical_document_id=path_documents[0].logical_document_id,
                room_name=path_room_name,
            )
        )
        if duplicate_room_count <= 1:
            return (
                _repeat_channel_alpha_room_name(
                    document,
                    physical_document_by_id,
                    compile_options=compile_options,
                )
                or path_room_name
            )

    separator = compile_options.channel_room_level_separator or "_"
    parts: list[str] = []
    for depth, path_document in enumerate(reversed(path_documents)):
        index = physical_instance_offsets.get(path_document.id, 0) + 1
        part_name = path_document.room_name or Path(path_document.file_name).stem
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
    compile_options: AltiumProjectCompileOptions,
    channel_designator_format: str,
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
            physical_instance_offsets=physical_instance_offsets,
            physical_count_by_logical_id=physical_count_by_logical_id,
            compile_options=compile_options,
        ),
    )
    # Altium substitutes the designator-sorted channel rank into every
    # channel designator format; there is no format gate.
    rank = _channel_rank_for_document(document, physical_document_by_id)
    room = replace(
        room,
        channel_index=str(rank),
        channel_alpha=_compiled_alpha_index(rank),
    )
    # AD26 channel-prefix resolution: discrete channels use the bottom
    # designator, but Repeat() channels use the repeat base name, which the
    # physical document already carries as channel_prefix.
    if "$RoomName" not in channel_designator_format and not (
        _is_repeat_channel_document(document, physical_document_by_id)
    ):
        room = replace(
            room,
            channel_prefix=document.room_name or room.channel_prefix,
        )
    return room


def _channel_rank_for_document(
    document: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
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
    siblings = [
        row
        for row in physical_document_by_id.values()
        if row.logical_document_id == document.logical_document_id
    ]
    if len(siblings) <= 1:
        return 1
    local_indices = _channel_local_indices(physical_document_by_id)

    def path_key(row: AltiumCompiledPhysicalDocument) -> tuple[tuple[object, ...], ...]:
        levels: list[tuple[object, ...]] = []
        node: AltiumCompiledPhysicalDocument | None = row
        while node is not None and node.parent_id:
            levels.append(
                tuple(_natural_sort_key(f"{node.room_name}{local_indices[node.id]}"))
            )
            node = physical_document_by_id.get(node.parent_id)
        return tuple(reversed(levels))

    siblings.sort(key=path_key)
    for rank, row in enumerate(siblings, start=1):
        if row.id == document.id:
            return rank
    return 1


def _channel_identity(
    row: AltiumCompiledPhysicalDocument,
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
) -> tuple[str, str]:
    """Identity of a distinct channel, shared across parent instances."""
    parent = physical_document_by_id.get(row.parent_id or "")
    parent_logical_id = parent.logical_document_id if parent is not None else ""
    return (parent_logical_id, row.room_name.lower())


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
        sort_key_by_identity: dict[tuple[str, str], tuple[object, ...]] = {}
        for row in rows:
            parent = physical_document_by_id.get(row.parent_id or "")
            parent_name = parent.file_name.lower() if parent is not None else ""
            identity = _channel_identity(row, physical_document_by_id)
            sort_key_by_identity.setdefault(
                identity, (parent_name, _natural_sort_key(row.room_name))
            )
        ordered = sorted(sort_key_by_identity, key=sort_key_by_identity.__getitem__)
        index_by_identity = {identity: index for index, identity in enumerate(ordered)}
        for row in rows:
            local_indices[row.id] = index_by_identity[
                _channel_identity(row, physical_document_by_id)
            ]
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


def _apply_inferred_channel_fields(
    physical_rows: tuple[AltiumCompiledPhysicalDocument, ...],
) -> tuple[AltiumCompiledPhysicalDocument, ...]:
    rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]] = defaultdict(
        list
    )
    for row in physical_rows:
        rows_by_logical_id[row.logical_document_id].append(row)

    updated_rows: list[AltiumCompiledPhysicalDocument] = []
    for row in physical_rows:
        logical_rows = rows_by_logical_id[row.logical_document_id]
        if len(logical_rows) <= 1 or row.channel_alpha is not None:
            updated_rows.append(row)
            continue
        instance_offset = logical_rows.index(row)
        room = _room_details_for_physical_document(row, instance_offset=instance_offset)
        updated_rows.append(
            replace(
                row,
                channel_index=int(room.channel_index),
                channel_prefix=room.channel_prefix or None,
                channel_alpha=room.channel_alpha or None,
                room_name=room.room_name,
                sheet_number=row.sheet_number,
                document_number=row.document_number,
            )
        )
    return tuple(updated_rows)


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
) -> None:
    endpoints = cast(list[NetEndpoint], getattr(net, "endpoints", []))
    if any(endpoint.endpoint_id == endpoint_id for endpoint in endpoints):
        return
    endpoints.append(
        NetEndpoint(
            endpoint_id=endpoint_id,
            role=role,
            element_id=element_id,
            object_id=object_id,
            name=name,
            connection_point=connection_point,
        )
    )


def _annotate_sheet_symbol_harness_ports(
    schdoc: "AltiumSchDoc",
    local_nets: Sequence[object],
) -> None:
    for sheet_symbol in schdoc.get_sheet_symbols():
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
                    _append_local_net_endpoint(
                        net,
                        endpoint_id=f"harness_port:{sheet_entry_id}",
                        role="harness_port",
                        element_id=sheet_entry_id,
                        object_id=str(getattr(entry, "unique_id", "") or ""),
                        name=entry_name,
                    )


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
    internal_tolerance: int,
    wire_graph: object,
    wire_index: object,
    net_by_wire_root: Mapping[RootPoint, object],
) -> None:
    for entry in connector.entries:
        entry_name = str(getattr(entry, "name", "") or "")
        if not entry_name:
            continue
        stats["harness_entry_candidate_count"] += 1
        precise_connection_point = _harness_entry_connection_point(connector, entry)
        net = _local_net_for_harness_entry(
            precise_connection_point,
            internal_tolerance=internal_tolerance,
            wire_graph=wire_graph,
            wire_index=wire_index,
            net_by_wire_root=net_by_wire_root,
        )
        if net is None:
            continue

        entry_id = str(getattr(entry, "unique_id", "") or "")
        endpoint_id = f"harness_entry:{entry_id or entry_name}"
        endpoints = cast(list[NetEndpoint], getattr(net, "endpoints", []))
        if any(endpoint.endpoint_id == endpoint_id for endpoint in endpoints):
            stats["harness_entry_matched_count"] += 1
            continue
        endpoint_name = (
            f"{harness_port_name}.{entry_name}" if harness_port_name else entry_name
        )
        _append_local_net_endpoint(
            net,
            endpoint_id=endpoint_id,
            role="harness_entry",
            element_id=entry_id,
            object_id=entry_id,
            name=endpoint_name,
            connection_point=precise_connection_point[:2],
        )
        stats["harness_entry_matched_count"] += 1


def _annotate_local_harness_entry_endpoints(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
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

    for connector in harness_connectors:
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
        _annotate_harness_connector_entries(
            connector,
            harness_port_name,
            stats,
            internal_tolerance=internal_tolerance,
            wire_graph=wire_graph,
            wire_index=wire_index,
            net_by_wire_root=net_by_wire_root,
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
) -> set[str]:
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
    return {
        port_name
        for port_name, harness_ids in connector_networks
        if connected_ids.intersection(harness_ids)
    }


def _net_has_harness_member(endpoints: list[NetEndpoint], port_names: set[str]) -> bool:
    return any(
        endpoint.role == "harness_entry"
        and endpoint.name.partition(".")[0] in port_names
        for endpoint in endpoints
    )


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
    port_names = _harness_power_member_port_names(
        power_object,
        harnesses,
        connector_networks,
        internal_tolerance,
    )
    if not port_names:
        return
    object_id = str(getattr(power_object, "unique_id", "") or "")
    object_name = str(getattr(power_object, "text", "") or "")
    for net in local_nets:
        endpoints = cast(list[NetEndpoint], getattr(net, "endpoints", []))
        if not _net_has_harness_member(endpoints, port_names):
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
    for connector in harness_connectors:
        display_unit = int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0))
        internal_tolerance = altium_internal_tolerance_for_display_unit(display_unit)
        harness_port_name = find_harness_port_name(
            connector,
            getattr(schdoc, "signal_harnesses", None),
            port_location_map,
            internal_tolerance,
        )
        for entry in connector.entries:
            entry_name = str(getattr(entry, "name", "") or "")
            if not entry_name:
                continue
            precise_connection_point = _harness_entry_connection_point(connector, entry)
            endpoint_name = (
                f"{harness_port_name}.{entry_name}" if harness_port_name else entry_name
            )
            wire_id = wire_endpoint_map.get(precise_connection_point)
            if not wire_id or wire_id in represented_wire_ids:
                continue
            endpoint = _HarnessWireEndpoint(
                wire_id=wire_id,
                name=endpoint_name,
                element_id=str(getattr(entry, "unique_id", "") or ""),
                parent_id=_harness_entry_parent_id(endpoint_name),
                connection_point=precise_connection_point[:2],
            )
            if endpoint not in endpoints_by_wire_id[wire_id]:
                endpoints_by_wire_id[wire_id].append(endpoint)

    return tuple(
        (wire_id, tuple(endpoints))
        for wire_id, endpoints in sorted(endpoints_by_wire_id.items())
        if len(endpoints) > 1
    )


def _harness_wire_endpoint_member_name(endpoint_name: str) -> str:
    _harness_name, separator, member_name = endpoint_name.partition(".")
    return member_name if separator and member_name else endpoint_name


def _merge_harness_entry_maps(
    target: dict[str, list[dict[str, str]]],
    source: dict[str, list[dict[str, str]]],
) -> None:
    for harness_name, entries in source.items():
        target_entries = target.setdefault(harness_name, [])
        seen = {
            (
                str(entry.get("name", "") or "").lower(),
                str(entry.get("object_id", "") or "").lower(),
            )
            for entry in target_entries
        }
        for entry in entries:
            key = (
                str(entry.get("name", "") or "").lower(),
                str(entry.get("object_id", "") or "").lower(),
            )
            if key in seen:
                continue
            target_entries.append(entry)
            seen.add(key)


def _nested_child_harness_entry_map(
    schdoc: "AltiumSchDoc",
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    *,
    seen_schdoc_ids: frozenset[int] = frozenset(),
    cache: dict[
        tuple[int, frozenset[int]],
        dict[str, tuple[dict[str, str], ...]],
    ]
    | None = None,
) -> dict[str, list[dict[str, str]]]:
    if cache is None:
        cache = {}
    cache_key = (id(schdoc), seen_schdoc_ids)
    cached = cache.get(cache_key)
    if cached is not None:
        return {
            key: [dict(entry) for entry in entries] for key, entries in cached.items()
        }
    if id(schdoc) in seen_schdoc_ids:
        return {}
    next_seen = seen_schdoc_ids | {id(schdoc)}
    result = {}
    if getattr(schdoc, "harness_connectors", None):
        result = {
            harness_name: list(entries)
            for harness_name, entries in _build_child_harness_entry_map(
                schdoc,
            ).items()
        }
    for sheet_symbol in schdoc.get_sheet_symbols():
        child_schdoc = _resolve_referenced_schdoc(
            str(getattr(sheet_symbol, "child_filename", "") or ""),
            schdoc_by_source_ref,
            schdoc_by_file_name,
        )
        if child_schdoc is None:
            continue
        child_entries = _nested_child_harness_entry_map(
            child_schdoc,
            schdoc_by_source_ref,
            schdoc_by_file_name,
            seen_schdoc_ids=next_seen,
            cache=cache,
        )
        for entry in sheet_symbol.entries:
            harness_type = str(getattr(entry, "harness_type", "") or "")
            entry_name = str(
                getattr(entry, "display_name", "") or getattr(entry, "name", "") or ""
            )
            if not harness_type or not entry_name:
                continue
            members = child_entries.get(entry_name.lower())
            if not members:
                continue
            _merge_harness_entry_maps(result, {entry_name.lower(): members})
    cache[cache_key] = {
        key: tuple(dict(entry) for entry in entries) for key, entries in result.items()
    }
    return result


def _typed_sheet_entry_harness_members(
    schdoc: "AltiumSchDoc",
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    *,
    nested_harness_entry_cache: dict[
        tuple[int, frozenset[int]],
        dict[str, tuple[dict[str, str], ...]],
    ]
    | None = None,
) -> dict[str, tuple[dict[str, str], ...]]:
    result: dict[str, list[dict[str, str]]] = {}
    for sheet_symbol in schdoc.get_sheet_symbols():
        child_schdoc = _resolve_referenced_schdoc(
            str(getattr(sheet_symbol, "child_filename", "") or ""),
            schdoc_by_source_ref,
            schdoc_by_file_name,
        )
        if child_schdoc is None:
            continue
        child_entries = _nested_child_harness_entry_map(
            child_schdoc,
            schdoc_by_source_ref,
            schdoc_by_file_name,
            cache=nested_harness_entry_cache,
        )
        for entry in sheet_symbol.entries:
            harness_type = str(getattr(entry, "harness_type", "") or "")
            entry_name = str(
                getattr(entry, "display_name", "") or getattr(entry, "name", "") or ""
            )
            if not harness_type or not entry_name:
                continue
            members = child_entries.get(entry_name.lower())
            if not members:
                continue
            _merge_harness_entry_maps(result, {entry_name.lower(): members})
    return {key: tuple(value) for key, value in result.items()}


def _compiled_typed_harness_member_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    nested_harness_entry_cache: dict[
        tuple[int, frozenset[int]],
        dict[str, tuple[dict[str, str], ...]],
    ]
    | None = None,
) -> tuple[AltiumCompiledNet, ...]:
    members_by_harness_name = _typed_sheet_entry_harness_members(
        schdoc,
        schdoc_by_source_ref,
        schdoc_by_file_name,
        nested_harness_entry_cache=nested_harness_entry_cache,
    )
    if not members_by_harness_name:
        return ()

    rows: list[AltiumCompiledNet] = []
    for net in getattr(local_netlist, "nets", ()):
        harness_names = tuple(
            dict.fromkeys(
                endpoint.name
                for endpoint in getattr(net, "endpoints", ())
                if endpoint.role in {"harness_port", "sheet_entry", "port"}
                and endpoint.name
                and endpoint.name.lower() in members_by_harness_name
            )
        )
        for harness_name in harness_names:
            for member in members_by_harness_name.get(harness_name.lower(), ()):
                member_name = str(member.get("name", "") or "").strip()
                if not member_name:
                    continue
                endpoint_name = f"{harness_name}.{member_name}"
                net_name = f"{harness_name}_{member_name}"
                net_id = _compiled_local_net_id(
                    logical_document.id,
                    net_name,
                    first_net_index + len(rows),
                )
                endpoint_id = f"{net_id}:endpoint:0:harness_entry:{endpoint_name}"
                endpoint = AltiumCompiledNetEndpoint(
                    id=endpoint_id,
                    role="harness_entry",
                    element_id=str(member.get("object_id", "") or endpoint_name),
                    object_id=str(member.get("object_id", "") or ""),
                    name=endpoint_name,
                    parent_id=_harness_entry_parent_id(endpoint_name),
                )
                rows.append(
                    AltiumCompiledNet(
                        id=net_id,
                        name=net_name,
                        original_name=net_name,
                        scope="logical_local",
                        logical_document_id=logical_document.id,
                        auto_named=True,
                        single_pin=False,
                        aliases=(harness_name,),
                        endpoint_ids=(endpoint_id,),
                        endpoints=(endpoint,),
                        item_ids=(endpoint_id,),
                        items=(_compiled_item_from_endpoint(endpoint),),
                    )
                )
    return tuple(rows)


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
            f"{net_id}:endpoint:{endpoint_index}:harness_entry:{wire_id}:{endpoint_index}"
            for endpoint_index, _endpoint in enumerate(endpoints)
        )
        compiled_endpoints = tuple(
            AltiumCompiledNetEndpoint(
                id=endpoint_id,
                role="harness_entry",
                element_id=endpoint.element_id,
                name=endpoint.name,
                parent_id=endpoint.parent_id,
                connection_point=endpoint.connection_point,
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
        schdoc, tolerance=internal_tolerance
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
    for sheet_symbol in schdoc.get_sheet_symbols():
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
            element_id = f"{sheet_symbol_uid}_{entry_name}"
            if element_id.lower() in represented_sheet_entry_ids:
                continue
            connection_point = _sheet_entry_connection_point(sheet_symbol, entry)
            if connection_point is None:
                continue
            precise_connection_point = _sheet_entry_precise_connection_point(
                sheet_symbol,
                entry,
            )
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
            replace(_compiled_item_from_endpoint(endpoint), removed=True)
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
    for sheet_symbol in schdoc.get_sheet_symbols():
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


def _harness_entry_parent_id(name: str) -> str:
    harness_name, _, _entry_name = name.partition(".")
    if not harness_name:
        return ""
    return f"{{{harness_name}}}"


def _compiled_endpoint_parent_id(
    endpoint: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    sheet_entry_parent_ids_by_element_id: dict[str, str],
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
        replacement_number = row.sheet_number
        for record in annotation.sheet_numbers:
            document_matches = (
                not record.document_name
                or record.document_name.lower() == row.file_name.lower()
            )
            path_matches = not record.unique_id_path or _normalized_unique_id_path(
                record.unique_id_path
            ) == _normalized_unique_id_path(row.physical_instance_unique_id)
            if document_matches and path_matches:
                replacement_number = record.sheet_number or replacement_number
                matched_indices.add(record.source_index)
        updated_rows.append(replace(row, sheet_number=replacement_number))

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


def _top_level_document_ids(
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
) -> set[str]:
    child_ids = {
        symbol.child_logical_document_id
        for symbol in sheet_symbols
        if symbol.child_logical_document_id is not None
    }
    top_ids = {
        document.id for document in logical_documents if document.id not in child_ids
    }
    if top_ids:
        return top_ids
    return {logical_documents[0].id} if logical_documents else set()


def _build_logical_and_symbol_rows(
    schdocs: list["AltiumSchDoc"],
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
    logical_ids_by_source_ref: dict[str, list[str]] = {}
    logical_ids_by_file_name: dict[str, list[str]] = {}
    for ordinal, schdoc in enumerate(schdocs):
        source_path = _relative_source_path(schdoc.filepath, project_base_dir)
        logical_id = _logical_document_id(source_path, ordinal)
        file_name = schdoc.filepath.name if schdoc.filepath else f"sheet{ordinal}"
        logical_ids.append(logical_id)
        source_paths.append(source_path)
        file_names.append(file_name)
        source_path_by_logical_id[logical_id] = source_path
        if source_path:
            logical_ids_by_source_ref.setdefault(
                _normalize_project_reference(source_path),
                [],
            ).append(logical_id)
        logical_ids_by_file_name.setdefault(file_name.lower(), []).append(logical_id)

    symbol_rows: list[AltiumCompiledSheetSymbol] = []
    symbol_info_by_id: dict[str, "SchSheetSymbolInfo"] = {}
    symbol_ids_by_logical_id: dict[str, list[str]] = {}
    parent_symbol_ids_by_logical_id: dict[str, list[str]] = {}
    diagnostics: list[AltiumCompileDiagnostic] = []
    for ordinal, schdoc in enumerate(schdocs):
        logical_id = logical_ids[ordinal]
        for symbol_index, symbol in enumerate(schdoc.get_sheet_symbols()):
            child_file_name = symbol.child_filename
            symbol_id = _sheet_symbol_id(logical_id, symbol, symbol_index)
            symbol_diagnostics: list[AltiumCompileDiagnostic] = []
            child_logical_id: str | None = None
            child_source_path: str | None = None
            child_match_kind = "missing_filename"
            child_candidates: tuple[str, ...] = ()
            child_ref = _normalize_project_reference(child_file_name)
            if child_ref:
                source_matches = tuple(logical_ids_by_source_ref.get(child_ref, ()))
                if len(source_matches) == 1:
                    child_logical_id = source_matches[0]
                    child_source_path = source_path_by_logical_id[child_logical_id]
                    child_match_kind = "source_path"
                    child_candidates = source_matches
                elif len(source_matches) > 1:
                    child_match_kind = "ambiguous_source_path"
                    child_candidates = source_matches
                else:
                    file_matches = tuple(
                        logical_ids_by_file_name.get(_reference_basename(child_ref), ())
                    )
                    if len(file_matches) == 1:
                        child_logical_id = file_matches[0]
                        child_source_path = source_path_by_logical_id[child_logical_id]
                        child_match_kind = "file_name"
                        child_candidates = file_matches
                    elif len(file_matches) > 1:
                        child_match_kind = "ambiguous_file_name"
                        child_candidates = file_matches
                    else:
                        stem_matches = tuple(
                            logical_id
                            for file_name, ids in logical_ids_by_file_name.items()
                            if _reference_stem(file_name) == _reference_stem(child_ref)
                            for logical_id in ids
                        )
                        if len(stem_matches) == 1:
                            child_logical_id = stem_matches[0]
                            child_source_path = source_path_by_logical_id[
                                child_logical_id
                            ]
                            child_match_kind = "file_stem"
                            child_candidates = stem_matches
                        elif len(stem_matches) > 1:
                            child_match_kind = "ambiguous_file_stem"
                            child_candidates = stem_matches
                        else:
                            child_match_kind = "unresolved"
            if child_match_kind in {
                "missing_filename",
                "ambiguous_source_path",
                "ambiguous_file_name",
                "ambiguous_file_stem",
                "unresolved",
            }:
                code = {
                    "missing_filename": "missing_sheet_symbol_child_filename",
                    "ambiguous_source_path": "ambiguous_sheet_symbol_child",
                    "ambiguous_file_name": "ambiguous_sheet_symbol_child",
                    "ambiguous_file_stem": "ambiguous_sheet_symbol_child",
                    "unresolved": "unresolved_sheet_symbol_child",
                }[child_match_kind]
                diagnostic = AltiumCompileDiagnostic(
                    severity="error",
                    code=code,
                    message=(
                        "Sheet symbol child filename does not resolve to a unique "
                        "loaded logical document; physical tree instantiation cannot "
                        "continue through this symbol."
                    ),
                    source_id=symbol_id,
                )
                symbol_diagnostics.append(diagnostic)
                diagnostics.append(diagnostic)
            if child_logical_id is not None:
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
                child_logical_document_id=child_logical_id,
                child_source_path=child_source_path,
                child_match_kind=child_match_kind,
                child_candidate_logical_document_ids=child_candidates,
                is_repeat=is_repeat,
                repeat_prefix=repeat_prefix,
                repeat_start=repeat_start,
                repeat_end=repeat_end,
                entry_count=len(symbol.entries),
                diagnostics=tuple(symbol_diagnostics),
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
                component_source_count=len(schdoc.get_components()),
                local_net_count=0,
            )
        )

    top_ids = _top_level_document_ids(tuple(logical_rows), tuple(symbol_rows))
    if saved_structure_top_level_file_name:
        saved_top_level_name = saved_structure_top_level_file_name.lower()
        saved_top_level_ids = [
            row.id
            for row in logical_rows
            if row.file_name.lower() == saved_top_level_name
            or Path(row.source_path.replace("\\", "/")).name.lower()
            == saved_top_level_name
        ]
        if len(saved_top_level_ids) == 1:
            top_ids = {saved_top_level_ids[0]}
    logical_rows = [
        replace(row, is_top_level_candidate=row.id in top_ids) for row in logical_rows
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
    for component_group in _compiled_component_source_groups(tuple(logical_components)):
        component = _compiled_component_group_representative(component_group)
        value = _resolve_component_display_value(
            component,
            project_params=options.project_parameters,
            sheet_params=options.sheet_parameters,
            component_description=component.description,
        )
        if not value and component.comment:
            value = component.comment
        parameters = tuple(
            sorted(
                (
                    str(getattr(parameter, "name", "") or ""),
                    str(getattr(parameter, "text", "") or ""),
                )
                for parameter in component.parameters
                if getattr(parameter, "name", "")
            )
        )
        kind_value = (
            component.component_kind.value
            if hasattr(component.component_kind, "value")
            else int(component.component_kind)
        )
        infos.append(
            _CompiledComponentSourceInfo(
                component=component,
                components=component_group,
                include_in_netlist=_component_includes_in_netlist(
                    component,
                    compile_mask_bounds,
                ),
                logical_designator=component.designator,
                part_count=_component_record_part_count(component),
                current_part_id=_component_record_current_part_id(component),
                value=value,
                parameters=parameters,
                kind_value=kind_value,
                footprint=component.footprint,
                component_kind=_component_kind_name(component),
                pin_count=(
                    _compiled_component_group_all_pin_count(component_group)
                    if _component_record_part_count(component) > 1
                    and _component_record_current_part_id(component) > 1
                    else _compiled_component_group_pin_count(component_group)
                ),
                all_pin_count=_compiled_component_group_all_pin_count(component_group),
                exclude_from_bom=not component_kind_includes_in_bom(
                    component.component_kind
                ),
                description=component.description,
                library_ref=component.library_ref,
                design_item_id=str(
                    getattr(component.record, "design_item_id", "") or ""
                ),
                unique_id=component.unique_id,
                cross_document_multipart=(
                    component.designator.lower() in cross_document_multipart_designators
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
    compile_options: AltiumProjectCompileOptions,
    channel_designator_format: str,
    sheet_symbol_designator_by_id: Mapping[str, str],
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
        compile_options=compile_options,
        channel_designator_format=channel_designator_format,
        sheet_designator=sheet_symbol_designator_by_id.get(
            physical_row.parent_sheet_symbol_id or "",
            "",
        ),
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
    if naming_room is not None and logical_designator:
        physical_designator = apply_channel_pattern(
            channel_designator_format,
            naming_room,
            logical_designator,
        )
        annotation_state = "channel_format"
    annotation_record, matched_state, match_diagnostic = _find_designator_annotation(
        source_unique_id_path,
        annotation,
        exact_component_paths=exact_component_paths,
        used_annotation_indices=used_annotation_indices,
    )
    if annotation_record is not None:
        used_annotation_indices.add(annotation_record.source_index)
        if annotation_record.physical_designator:
            physical_designator = annotation_record.physical_designator
        annotation_state = matched_state
        annotation_locked = annotation_record.locked
    elif match_diagnostic is not None:
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
    )


def _compiled_component_body_rows_for_physical_document(
    physical_row: AltiumCompiledPhysicalDocument,
    component_info: _CompiledComponentSourceInfo,
    component_row: AltiumCompiledComponent,
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
            part_count=_component_record_part_count(source_component),
            current_part_id=_component_record_current_part_id(source_component),
        )
        for body_index, source_component in enumerate(component_info.components)
    )


@dataclass(slots=True)
class _PhysicalInstantiationState:
    logical_by_id: dict[str, AltiumCompiledLogicalDocument]
    symbols_by_logical_id: dict[str, list[AltiumCompiledSheetSymbol]]
    channel_rooms_by_symbol_id: dict[str, RoomDetails]
    repeat_channel_values: dict[tuple[str, int], int]
    options: NetlistOptions
    new_indexing_of_sheet_symbols: bool
    flat_physical_tree: bool
    physical_rows: list[AltiumCompiledPhysicalDocument]
    physical_sheet_symbol_rows: list[AltiumCompiledPhysicalSheetSymbol]
    physical_child_ids: dict[str, list[str]]
    symbol_child_physical_ids: dict[str, list[str]]
    flat_physical_document_id_by_logical_id: dict[str, str]
    diagnostics: list[AltiumCompileDiagnostic]


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
        )
    )

    next_stack = (*stack, logical_document.id)
    for symbol in state.symbols_by_logical_id.get(logical_document.id, ()):
        child_logical_id = symbol.child_logical_document_id
        if child_logical_id is None:
            continue
        child_logical_document = state.logical_by_id[child_logical_id]
        if child_logical_id in next_stack:
            state.diagnostics.append(
                AltiumCompileDiagnostic(
                    severity="error",
                    code="cyclic_sheet_symbol_child",
                    message="Sheet-symbol child graph contains a cycle.",
                    source_id=symbol.id,
                )
            )
            continue

        for child_instance in _physical_child_instances(
            symbol,
            state.channel_rooms_by_symbol_id.get(symbol.id),
            state.repeat_channel_values,
            new_indexing=state.new_indexing_of_sheet_symbols,
        ):
            parent_index_path = state.physical_rows[physical_ordinal].sheet_number or ""
            child_sheet_number = parent_index_path or None
            if symbol.is_repeat or symbol.id in state.channel_rooms_by_symbol_id:
                child_sheet_number = (
                    f"{parent_index_path}.{child_instance.sheet_number}"
                )
            if state.options.net_identifier_scope in {
                NetIdentifierScope.FLAT,
                NetIdentifierScope.GLOBAL,
            }:
                child_path = (
                    Path(child_logical_document.file_name).stem
                    or child_instance.path_segment
                )
            else:
                child_path = _path_join(
                    physical_instance_path,
                    child_instance.path_segment,
                )
            source_object_id = symbol.source_object_id or symbol.id
            unique_segment = source_object_id
            if symbol.is_repeat:
                unique_segment = f"{child_instance.channel_index}{source_object_id}"
            child_unique_id = _unique_id_path_join(
                physical_instance_unique_id,
                unique_segment,
            )
            child_physical_id = _physical_child_document_id(
                physical_id,
                symbol.id,
                child_instance.instance_key,
            )
            child_already_instantiated = False
            if state.flat_physical_tree:
                child_physical_id = _physical_document_id(
                    child_logical_document.id,
                    len(state.physical_rows),
                )
                child_path = Path(child_logical_document.file_name).stem
                child_unique_id = ""
                child_instance = _PhysicalChildInstance(
                    instance_key=child_instance.instance_key,
                    path_segment=child_instance.path_segment,
                    channel_index=0,
                    channel_prefix=None,
                    channel_alpha=None,
                    room_name=child_path,
                    sheet_number=None,
                    document_number=None,
                )
                child_sheet_number = None
                existing_child_id = state.flat_physical_document_id_by_logical_id.get(
                    child_logical_document.id
                )
                if existing_child_id is not None:
                    child_physical_id = existing_child_id
                    child_already_instantiated = True
            if not state.flat_physical_tree:
                state.physical_child_ids.setdefault(physical_id, []).append(
                    child_physical_id
                )
            state.symbol_child_physical_ids[symbol.id].append(child_physical_id)
            state.physical_sheet_symbol_rows.append(
                AltiumCompiledPhysicalSheetSymbol(
                    id=(
                        f"{physical_id}:sheet_symbol:"
                        f"{symbol.source_object_id or symbol.id}:"
                        f"{child_instance.instance_key}"
                    ),
                    logical_sheet_symbol_id=symbol.id,
                    owner_logical_document_id=logical_document.id,
                    owner_physical_document_id=physical_id,
                    source_object_id=symbol.source_object_id,
                    logical_designator=child_instance.path_segment,
                    physical_designator=child_instance.path_segment,
                    # AD26 reports the resolved child document file name here,
                    # so extension-less symbol references still surface the
                    # child's real file name; the raw symbol filename is only a
                    # fallback for unresolved symbols, which never reach
                    # physical instantiation (SheetSymbolAdapter
                    # DM_SheetSymbolFileName / DM_RawSheetSymbolFileName).
                    sheet_symbol_file_name=child_logical_document.file_name,
                    child_logical_document_id=child_logical_id,
                    child_source_path=symbol.child_source_path,
                    child_physical_document_id=child_physical_id,
                    child_physical_instance_path=child_path,
                    child_physical_instance_unique_id=child_unique_id,
                    child_channel_index=child_instance.channel_index,
                    entry_count=symbol.entry_count,
                )
            )
            if not child_already_instantiated:
                _instantiate_physical_document(
                    state,
                    logical_document=child_logical_document,
                    physical_id=child_physical_id,
                    physical_instance_path=child_path,
                    physical_instance_unique_id=child_unique_id,
                    channel_index=child_instance.channel_index,
                    channel_prefix=child_instance.channel_prefix,
                    channel_alpha=child_instance.channel_alpha,
                    room_name=child_instance.room_name,
                    parent_id=None if state.flat_physical_tree else physical_id,
                    parent_sheet_symbol_id=symbol.id,
                    sheet_number=child_sheet_number,
                    document_number=None,
                    stack=next_stack,
                )


def _cross_document_multipart_designators(
    component_rows_by_logical_id: Mapping[str, Sequence["SchComponentInfo"]],
) -> set[str]:
    logical_ids_by_designator: dict[str, set[str]] = defaultdict(set)
    for logical_id, source_components in component_rows_by_logical_id.items():
        for component in source_components:
            if (
                _designator_binds_multipart_records(component.designator)
                and _component_record_part_count(component) > 1
            ):
                logical_ids_by_designator[component.designator.lower()].add(logical_id)
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


def _build_compiled_component_rows(
    physical_rows: tuple[AltiumCompiledPhysicalDocument, ...],
    *,
    component_rows_by_logical_id: Mapping[str, Sequence["SchComponentInfo"]],
    compile_mask_bounds_by_logical_id: Mapping[
        str, tuple[tuple[int, int, int, int], ...]
    ],
    cross_document_multipart_designators: set[str],
    compile_options: AltiumProjectCompileOptions,
    options: NetlistOptions,
    annotation: AnnotationFile,
    channel_designator_format: str,
    sheet_symbol_designator_by_id: Mapping[str, str],
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
    exact_component_paths = _exact_component_paths(
        physical_rows,
        component_rows_by_logical_id,
    )
    used_annotation_indices: set[int] = set()
    source_info_by_logical_id: dict[str, tuple[_CompiledComponentSourceInfo, ...]] = {}
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
            compile_options=compile_options,
            channel_designator_format=channel_designator_format,
            sheet_symbol_designator_by_id=sheet_symbol_designator_by_id,
        )
        if naming_room is not None:
            naming_rooms_by_physical_id[physical_row.id] = naming_room
        component_infos = source_info_by_logical_id.get(
            physical_row.logical_document_id
        )
        if component_infos is None:
            component_infos = _compiled_component_source_infos(
                physical_row.logical_document_id,
                component_source_rows_by_logical_id=component_rows_by_logical_id,
                compile_mask_bounds_by_logical_id=compile_mask_bounds_by_logical_id,
                options=options,
                cross_document_multipart_designators=(
                    cross_document_multipart_designators
                ),
            )
            source_info_by_logical_id[physical_row.logical_document_id] = (
                component_infos
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
    final_physical_rows = tuple(
        replace(
            row,
            child_ids=tuple(physical_child_ids.get(row.id, ())),
            component_ids=tuple(
                component_id
                for component_id in physical_component_ids.get(row.id, ())
                if component_id in final_component_ids
            ),
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
    schdocs: list["AltiumSchDoc"],
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
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
        logical_id: tuple(schdoc.get_components())
        for logical_id, schdoc in schdoc_by_logical_id.items()
    }
    cross_document_multipart_designators = _cross_document_multipart_designators(
        component_source_rows_by_logical_id
    )
    compile_mask_bounds_by_logical_id = {
        logical_id: _compile_mask_bounds(schdoc)
        for logical_id, schdoc in schdoc_by_logical_id.items()
    }
    symbols_by_logical_id: dict[str, list[AltiumCompiledSheetSymbol]] = defaultdict(
        list
    )
    for symbol in sheet_symbols:
        symbols_by_logical_id[symbol.logical_document_id].append(symbol)
    sheet_symbol_designator_by_id = {
        symbol.id: symbol.designator for symbol in sheet_symbols
    }

    channel_rooms_by_symbol_id = _multi_reference_channel_rooms(sheet_symbols)
    repeat_channel_values = _repeat_channel_values(
        sheet_symbols,
        new_indexing=compile_options.new_indexing_of_sheet_symbols,
    )

    instantiation_state = _PhysicalInstantiationState(
        logical_by_id=logical_by_id,
        symbols_by_logical_id=symbols_by_logical_id,
        channel_rooms_by_symbol_id=channel_rooms_by_symbol_id,
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
            channel_index=0,
            channel_prefix=None,
            channel_alpha=None,
            room_name=root_name,
            parent_id=None,
            parent_sheet_symbol_id=None,
            sheet_number=None,
            document_number=None,
            stack=(),
        )

    physical_rows_tuple = _apply_inferred_channel_fields(tuple(physical_rows))
    physical_rows_tuple = _apply_default_sheet_and_document_numbers(
        physical_rows_tuple,
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
        compile_mask_bounds_by_logical_id=compile_mask_bounds_by_logical_id,
        cross_document_multipart_designators=(cross_document_multipart_designators),
        compile_options=compile_options,
        options=options,
        annotation=annotation,
        channel_designator_format=channel_designator_format,
        sheet_symbol_designator_by_id=sheet_symbol_designator_by_id,
    )
    component_rows = _collapse_multipart_component_rows(component_rows)
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


def _compiled_component_single_pin_local_nets(
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    pin_roots_by_component: Mapping[tuple[str, str, str], Sequence["SchPinInfo"]],
    multipart_designators: set[str] | frozenset[str] | None = None,
    local_multipart_designators: set[str] | frozenset[str] | None = None,
) -> tuple[AltiumCompiledNet, ...]:
    represented_pins: Counter[tuple[str, str, str]] = Counter()
    for net in getattr(local_netlist, "nets", ()):
        for terminal in getattr(net, "terminals", ()):
            designator = str(getattr(terminal, "designator", "") or "").lower()
            pin = str(getattr(terminal, "pin", "") or "")
            pin_name = str(getattr(terminal, "pin_name", "") or "")
            if designator and pin:
                represented_pins[(designator, pin, pin_name)] += 1

    rows: list[AltiumCompiledNet] = []
    multipart = set(multipart_designators or ())
    multipart.update(local_multipart_designators or ())
    for root_pins in pin_roots_by_component.values():
        for pin in root_pins:
            represented_key = (
                str(
                    getattr(pin, "component_designator", "")
                    or getattr(pin.component, "designator", "")
                    or ""
                ).lower(),
                pin.designator,
                pin.name,
            )
            if represented_pins[represented_key] > 0:
                represented_pins[represented_key] -= 1
                continue
            component = pin.component
            logical_designator = component.designator
            pin_number = pin.designator
            part_count = _component_record_part_count(component)
            component_part_id = _component_record_current_part_id(component)
            owner_part_id = int(
                getattr(pin.pin, "owner_part_id", None)
                or (1 if logical_designator in multipart else component_part_id)
                or 1
            )
            suffix = (
                _component_part_alpha_suffix(
                    part_count=part_count,
                    current_part_id=owner_part_id,
                )
                if logical_designator in multipart
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
                )
            )
    return tuple(rows)


def _bus_range_suffix(name: str) -> str:
    start = name.find("[")
    end = name.rfind("]")
    if start == -1 or end <= start:
        return ""
    return name[start : end + 1]


def _compiled_terminal_from_net_terminal(
    terminal_id: str,
    terminal: object,
    terminal_full_designators_by_pin: Mapping[tuple[str, str], str],
) -> AltiumCompiledNetTerminal:
    designator = str(getattr(terminal, "designator", "") or "")
    pin = str(getattr(terminal, "pin", "") or "")
    return AltiumCompiledNetTerminal(
        id=terminal_id,
        designator=terminal_full_designators_by_pin.get((designator, pin), designator),
        pin=pin,
        pin_name=str(getattr(terminal, "pin_name", "") or ""),
        pin_type=str(
            getattr(getattr(terminal, "pin_type", None), "name", "") or "PASSIVE"
        ),
        _source_component_uid=str(getattr(terminal, "_source_component_uid", "") or ""),
        _source_pin_uid=str(getattr(terminal, "_source_pin_uid", "") or ""),
        _source_owner_part_id=int(
            getattr(terminal, "_source_owner_part_id", None) or 1
        ),
    )


def _compiled_local_net_row(
    net: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    net_index: int,
    net_label_by_id: Mapping[str, object],
    sheet_entry_parent_ids_by_element_id: dict[str, str],
    terminal_full_designators_by_pin: dict[tuple[str, str], str],
) -> AltiumCompiledNet:
    net_name = str(getattr(net, "name", "") or "")
    net_terminals = tuple(getattr(net, "terminals", ()) or ())
    net_endpoints = tuple(getattr(net, "endpoints", ()) or ())
    net_id = _compiled_local_net_id(
        logical_document.id,
        net_name,
        net_index,
    )
    terminal_ids = tuple(
        (
            f"{net_id}:terminal:{terminal_index}:"
            f"{terminal_full_designators_by_pin.get((str(terminal.designator), str(terminal.pin)), str(terminal.designator))}:"
            f"{terminal.pin}"
        )
        for terminal_index, terminal in enumerate(net_terminals)
    )
    terminals = tuple(
        _compiled_terminal_from_net_terminal(
            terminal_id,
            terminal,
            terminal_full_designators_by_pin,
        )
        for terminal_id, terminal in zip(
            terminal_ids,
            net_terminals,
            strict=True,
        )
    )
    base_endpoint_ids = tuple(
        f"{net_id}:endpoint:{endpoint_index}:{endpoint.endpoint_id}"
        for endpoint_index, endpoint in enumerate(net_endpoints)
    )
    base_endpoints = tuple(
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
        for endpoint_id, endpoint in zip(
            base_endpoint_ids,
            net_endpoints,
            strict=True,
        )
    )
    base_endpoints = tuple(
        replace(
            endpoint,
            name=str(
                getattr(net_label_by_id.get(endpoint.element_id), "text", "")
                or endpoint.name
            ),
            connection_point=(
                getattr(
                    net_label_by_id.get(endpoint.element_id),
                    "connection_point",
                    None,
                )
                if endpoint.connection_point is None
                else endpoint.connection_point
            ),
        )
        if endpoint.role == "net_label" and endpoint.element_id in net_label_by_id
        else endpoint
        for endpoint in base_endpoints
    )
    base_label_ids = {
        endpoint.element_id
        for endpoint in base_endpoints
        if endpoint.role == "net_label" and endpoint.element_id
    }
    missing_label_ids = tuple(
        label_id
        for label_id in getattr(getattr(net, "graphical", None), "labels", ())
        if label_id and label_id not in base_label_ids
    )
    label_endpoint_ids = tuple(
        f"{net_id}:endpoint:{len(base_endpoint_ids) + label_index}:net_label:{label_id}"
        for label_index, label_id in enumerate(missing_label_ids)
    )
    label_endpoints = tuple(
        AltiumCompiledNetEndpoint(
            id=endpoint_id,
            role="net_label",
            element_id=label_id,
            object_id=label_id,
            name=str(getattr(net_label_by_id.get(label_id), "text", "") or net_name),
            parent_id=logical_document.file_name,
            connection_point=getattr(
                net_label_by_id.get(label_id),
                "connection_point",
                None,
            ),
        )
        for endpoint_id, label_id in zip(
            label_endpoint_ids,
            missing_label_ids,
            strict=True,
        )
    )
    endpoint_ids = (*base_endpoint_ids, *label_endpoint_ids)
    endpoints = (*base_endpoints, *label_endpoints)
    graphical_items = _compiled_items_from_graphical(
        net_id,
        getattr(net, "graphical", None),
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
                net_terminals,
                strict=True,
            )
        ),
        *(_compiled_item_from_endpoint(endpoint) for endpoint in endpoints),
        *graphical_items,
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
    )


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
        for port in schdoc_by_logical_id[logical_document.id].get_ports():
            port_name = str(getattr(port, "name", "") or "")
            if port_name:
                counts[_compiled_case_insensitive_key(port_name)] += 1
    return counts


def _project_multipart_designators(schdocs: list["AltiumSchDoc"]) -> set[str]:
    placed_part_ids_by_designator: dict[str, set[int]] = defaultdict(set)
    for schdoc in schdocs:
        for component in schdoc.get_components():
            logical_designator = str(component.designator or "")
            if _designator_binds_multipart_records(logical_designator):
                placed_part_ids_by_designator[logical_designator].add(
                    _component_record_current_part_id(component)
                )
    return {
        designator
        for designator, part_ids in placed_part_ids_by_designator.items()
        if any(part_id > 1 for part_id in part_ids)
    }


def _compiled_standalone_port_connector_local_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
    top_level_port_name_counts: dict[str, int],
) -> tuple[AltiumCompiledNet, ...]:
    if logical_document.parent_sheet_symbol_ids:
        return ()

    direct_port_net_has_terminals: set[str] = set()
    direct_port_net_ids: set[str] = set()
    for net in getattr(local_netlist, "nets", ()):
        for endpoint in getattr(net, "endpoints", ()):
            if str(getattr(endpoint, "role", "") or "").lower() != "port":
                continue
            object_id = str(getattr(endpoint, "object_id", "") or "")
            if not object_id:
                endpoint_id = str(getattr(endpoint, "endpoint_id", "") or "")
                object_id = endpoint_id.rsplit(":", 1)[-1] if endpoint_id else ""
            if not object_id:
                continue
            direct_port_net_ids.add(object_id)
            if getattr(net, "terminals", ()):
                direct_port_net_has_terminals.add(object_id)

    rows: list[AltiumCompiledNet] = []
    for port_index, port in enumerate(schdoc.get_ports(), start=1):
        port_name = str(getattr(port, "name", "") or "")
        if not port_name:
            continue
        port_id = str(getattr(port, "unique_id", "") or "")
        if port_id and port_id in direct_port_net_has_terminals:
            continue

        bus_members = _parse_bus_range(port_name)
        if bus_members and port_id and port_id in direct_port_net_ids:
            continue
        same_name_top_level_count = top_level_port_name_counts.get(
            _compiled_case_insensitive_key(port_name),
            0,
        )
        if bus_members and same_name_top_level_count > 1:
            net_name = port_name
            auto_named = False
            aliases: tuple[str, ...] = ()
        elif bus_members or (port_id and port_id in direct_port_net_ids):
            bus_suffix = _bus_range_suffix(port_name)
            net_name = (
                f"N000-{port_index}_{bus_suffix}"
                if bus_suffix
                else f"N000-{port_index}"
            )
            auto_named = True
            aliases = (port_name,) if port_name != net_name else ()
        else:
            continue

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
            )
        )
    return tuple(rows)


def _compiled_isolated_power_port_local_nets(
    schdoc: "AltiumSchDoc",
    local_netlist: object,
    *,
    logical_document: AltiumCompiledLogicalDocument,
    first_net_index: int,
) -> tuple[AltiumCompiledNet, ...]:
    represented_power_port_ids = {
        str(getattr(endpoint, "element_id", "") or "")
        for net in getattr(local_netlist, "nets", ())
        for endpoint in getattr(net, "endpoints", ())
        if getattr(endpoint, "role", "") == "power_port"
    }
    power_ports_by_name: dict[str, list[object]] = defaultdict(list)
    signal_harnesses = tuple(schdoc.get_signal_harnesses())
    connector_primary_points = tuple(
        _harness_connector_master_entry_point(connector.record)
        for connector in schdoc.get_harness_connectors()
    )
    internal_tolerance = altium_internal_tolerance_for_display_unit(
        int(getattr(getattr(schdoc, "sheet", None), "display_unit", 0) or 0)
    )
    for power_port in schdoc.get_power_ports():
        power_port_id = str(getattr(power_port, "unique_id", "") or "")
        power_name = str(getattr(power_port, "text", "") or "").strip()
        if not power_port_id or not power_name:
            continue
        if power_port_id in represented_power_port_ids:
            continue
        if bool(
            getattr(getattr(power_port, "record", None), "is_not_accessible", False)
        ):
            continue
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
        power_ports_by_name[power_name].append(power_port)

    rows: list[AltiumCompiledNet] = []
    for power_name, power_ports in sorted(
        power_ports_by_name.items(),
        key=lambda item: item[0].lower(),
    ):
        net_id = _compiled_local_net_id(
            logical_document.id,
            power_name,
            first_net_index + len(rows),
        )
        endpoints: list[AltiumCompiledNetEndpoint] = []
        for endpoint_index, power_port in enumerate(power_ports):
            power_port_id = str(getattr(power_port, "unique_id", "") or "")
            endpoints.append(
                AltiumCompiledNetEndpoint(
                    id=f"{net_id}:endpoint:{endpoint_index}:power_port:{power_port_id}",
                    role="power_port",
                    element_id=power_port_id,
                    object_id=power_port_id,
                    name=power_name,
                    parent_id=logical_document.file_name,
                    connection_point=getattr(power_port, "connection_point", None),
                )
            )
        endpoint_ids = tuple(endpoint.id for endpoint in endpoints)
        items = tuple(_compiled_item_from_endpoint(endpoint) for endpoint in endpoints)
        rows.append(
            AltiumCompiledNet(
                id=net_id,
                name=power_name,
                original_name=power_name,
                scope="logical_local",
                logical_document_id=logical_document.id,
                auto_named=False,
                single_pin=False,
                endpoint_ids=endpoint_ids,
                endpoints=tuple(endpoints),
                item_ids=tuple(item.id for item in items),
                items=items,
            )
        )
    return tuple(rows)


def _compile_local_connectivity(
    schdocs: list["AltiumSchDoc"],
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...],
    *,
    options: NetlistOptions,
    effective_hierarchy_mode: str,
    project_base_dir: Path | None,
) -> tuple[
    tuple[AltiumCompiledNet, ...],
    dict[str, int],
    dict[str, int],
    dict[str, dict[str, tuple[int, int, int]]],
    tuple[AltiumCompileDiagnostic, ...],
]:
    net_rows: list[AltiumCompiledNet] = []
    local_net_counts: dict[str, int] = {}
    harness_endpoint_stats = _initial_harness_endpoint_stats()
    component_pin_root_counts: dict[str, dict[str, tuple[int, int, int]]] = {}
    diagnostics: list[AltiumCompileDiagnostic] = []
    schdoc_by_logical_id = _schdocs_by_logical_id(
        schdocs,
        project_base_dir=project_base_dir,
    )
    schdoc_by_source_ref, schdoc_by_file_name = _schdoc_reference_maps(
        schdocs,
        project_base_dir=project_base_dir,
    )
    nested_harness_entry_cache: dict[
        tuple[int, frozenset[int]],
        dict[str, tuple[dict[str, str], ...]],
    ] = {}
    _require_source_schdocs(logical_documents, schdoc_by_logical_id)
    top_level_port_name_counts = _top_level_port_name_counts_for_local_connectivity(
        logical_documents,
        schdoc_by_logical_id,
    )
    project_multipart_designators = _project_multipart_designators(schdocs)

    for logical_document in logical_documents:
        schdoc = schdoc_by_logical_id[logical_document.id]
        local_multipart_designators = _local_multipart_designators(schdoc)
        sheet_entry_parent_ids_by_element_id = _sheet_entry_parent_ids_by_element_id(
            schdoc
        )
        net_label_by_id = {
            label.unique_id: label
            for label in schdoc.get_net_labels()
            if label.unique_id and getattr(label.record, "parent", None) is None
        }
        try:
            single_sheet_compiler = AltiumNetlistSingleSheetCompiler(
                schdoc,
                options=options,
            )
            local_netlist = single_sheet_compiler.generate()
            pin_roots_by_component = (
                single_sheet_compiler._compiled_pin_roots_by_component()
            )
            masked_pin_counts = (
                single_sheet_compiler._compiled_masked_pin_counts_by_component()
            )
            component_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for (
                _component_occurrence_key,
                component_designator,
                _pin_designator,
            ), roots in pin_roots_by_component.items():
                component_counts[component_designator][0] += 1
                component_counts[component_designator][1] += len(roots)
            component_pin_root_counts[logical_document.id] = {
                designator: (
                    counts[0],
                    counts[1],
                    masked_pin_counts.get(designator.lower(), 0),
                )
                for designator, counts in component_counts.items()
            }
            bridge_scope_options = _effective_bridge_scope_options(
                options,
                effective_hierarchy_mode=effective_hierarchy_mode,
            )
            if bridge_scope_options is not None:
                should_probe_bridge_scope = not local_netlist.nets
                if (
                    not should_probe_bridge_scope
                    and not any(
                        getattr(net, "terminals", ()) for net in local_netlist.nets
                    )
                    and _sheet_entry_wire_endpoint_groups(schdoc, local_netlist)
                ):
                    should_probe_bridge_scope = True
                use_bridge_scope = False
                if should_probe_bridge_scope:
                    bridge_netlist = AltiumNetlistSingleSheetCompiler(
                        schdoc,
                        options=bridge_scope_options,
                    ).generate()
                    if len(bridge_netlist.nets) > len(local_netlist.nets):
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
        )
        synthetic_isolated_power_nets = _compiled_isolated_power_port_local_nets(
            schdoc,
            local_netlist,
            logical_document=logical_document,
            first_net_index=len(local_netlist.nets)
            + len(synthetic_port_connector_nets),
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
            schdoc_by_source_ref=schdoc_by_source_ref,
            schdoc_by_file_name=schdoc_by_file_name,
            nested_harness_entry_cache=nested_harness_entry_cache,
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
        )
        harness_endpoint_stats["sheet_entry_wire_synthetic_net_count"] += len(
            synthetic_sheet_entry_nets
        )

        terminal_full_designators_by_pin = _component_pin_full_designators_by_pin(
            schdoc,
            multipart_designators=project_multipart_designators,
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
            net_rows.append(
                _compiled_local_net_row(
                    net,
                    logical_document=logical_document,
                    net_index=net_index,
                    net_label_by_id=net_label_by_id,
                    sheet_entry_parent_ids_by_element_id=(
                        sheet_entry_parent_ids_by_element_id
                    ),
                    terminal_full_designators_by_pin=terminal_full_designators_by_pin,
                )
            )
        net_rows.extend(synthetic_port_connector_nets)
        net_rows.extend(synthetic_isolated_power_nets)
        net_rows.extend(single_pin_nets)
        net_rows.extend(synthetic_harness_nets)
        net_rows.extend(synthetic_typed_harness_nets)
        net_rows.extend(synthetic_sheet_entry_nets)

    return (
        tuple(net_rows),
        local_net_counts,
        harness_endpoint_stats,
        component_pin_root_counts,
        tuple(diagnostics),
    )


def _elaborate_local_connectivity(
    local_nets: tuple[AltiumCompiledNet, ...],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    components: tuple[AltiumCompiledComponent, ...],
    *,
    channel_designator_format: str,
    compile_options: AltiumProjectCompileOptions,
) -> tuple[AltiumCompiledNet, ...]:
    """Instantiate logical-local net rows for each physical document instance."""
    physical_rows_by_logical_id: dict[str, list[AltiumCompiledPhysicalDocument]] = (
        defaultdict(list)
    )
    for physical_document in physical_documents:
        physical_rows_by_logical_id[physical_document.logical_document_id].append(
            physical_document
        )
    physical_document_by_id = {document.id: document for document in physical_documents}
    physical_instance_offsets = _logical_instance_offsets(physical_documents)
    physical_count_by_logical_id = _physical_count_by_logical_id(physical_documents)
    designator_maps = _component_designator_maps_by_physical_document(components)
    auto_name_designator_maps = _component_base_designator_maps_by_physical_document(
        components
    )
    room_paths_by_parent: dict[
        tuple[str | None, str, str],
        list[AltiumCompiledPhysicalDocument],
    ] = defaultdict(list)
    for physical_document in physical_documents:
        room_paths_by_parent[
            (
                physical_document.parent_id,
                physical_document.logical_document_id,
                physical_document.room_name,
            )
        ].append(physical_document)
    component_designators_by_physical_id: dict[str, list[str]] = defaultdict(list)
    for component in components:
        if component.physical_designator:
            component_designators_by_physical_id[component.physical_document_id].append(
                component.physical_designator
            )

    def repeated_room_sort_key(
        document: AltiumCompiledPhysicalDocument,
    ) -> tuple[int, tuple[list, str], int, str]:
        designators = sorted(
            component_designators_by_physical_id.get(document.id, ()),
            key=_altium_net_total_sort_key,
        )
        if designators:
            return (
                0,
                _altium_net_total_sort_key(designators[0]),
                document.channel_index,
                document.id,
            )
        return (1, ([], ""), document.channel_index, document.id)

    room_channel_index_by_physical_id = {
        document.id: str(index)
        for room_documents in room_paths_by_parent.values()
        if len(room_documents) > 1
        for index, document in enumerate(
            sorted(room_documents, key=repeated_room_sort_key),
            start=1,
        )
    }

    physical_nets: list[AltiumCompiledNet] = []
    for local_net in local_nets:
        if local_net.scope != "logical_local" or local_net.logical_document_id is None:
            continue
        for physical_document in physical_rows_by_logical_id.get(
            local_net.logical_document_id,
            (),
        ):
            physical_net_id = _compiled_physical_net_id(
                physical_document.id,
                local_net.id,
            )
            source_prefix = f"{local_net.id}:"
            physical_net_prefix = f"{physical_net_id}:"
            designator_map = designator_maps.get(physical_document.id, {})
            physical_name = _physical_channel_net_name(
                local_net,
                physical_document,
                designator_map=designator_map,
                auto_name_designator_map=auto_name_designator_maps.get(
                    physical_document.id,
                    {},
                ),
                channel_designator_format=channel_designator_format,
                room_channel_index_override=room_channel_index_by_physical_id.get(
                    physical_document.id
                ),
                physical_document_by_id=physical_document_by_id,
                physical_instance_offsets=physical_instance_offsets,
                physical_count_by_logical_id=physical_count_by_logical_id,
                compile_options=compile_options,
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
                )
                for terminal_id, terminal in zip(
                    local_net.terminal_ids,
                    local_net.terminals,
                    strict=True,
                )
            )
            terminal_ids = tuple(terminal.id for terminal in terminals)
            endpoint_ids = tuple(
                _replace_pin_endpoint_id_designator(
                    _replace_compiled_net_id_prefix_precomputed(
                        endpoint_id,
                        source_id=local_net.id,
                        source_prefix=source_prefix,
                        target_id=physical_net_id,
                        target_prefix=physical_net_prefix,
                    ),
                    designator_map,
                )
                for endpoint_id in local_net.endpoint_ids
            )
            endpoints = tuple(
                _compiled_endpoint_for_physical(
                    endpoint,
                    source_id=local_net.id,
                    source_prefix=source_prefix,
                    target_id=physical_net_id,
                    target_prefix=physical_net_prefix,
                    source_name=local_net.name,
                    target_name=physical_name,
                    designator_map=designator_map,
                )
                for endpoint in local_net.endpoints
            )
            item_ids = tuple(
                _replace_compiled_item_designator(
                    _replace_compiled_net_id_prefix_precomputed(
                        item_id,
                        source_id=local_net.id,
                        source_prefix=source_prefix,
                        target_id=physical_net_id,
                        target_prefix=physical_net_prefix,
                    ),
                    designator_map,
                )
                for item_id in local_net.item_ids
            )
            items = tuple(
                _compiled_item_for_physical(
                    item,
                    source_id=local_net.id,
                    source_prefix=source_prefix,
                    target_id=physical_net_id,
                    target_prefix=physical_net_prefix,
                    source_name=local_net.name,
                    target_name=physical_name,
                    designator_map=designator_map,
                    physical_document_id=physical_document.id,
                )
                for item in local_net.items
            )
            physical_nets.append(
                AltiumCompiledNet(
                    id=physical_net_id,
                    name=physical_name,
                    original_name=local_net.original_name,
                    override_name=local_net.override_name,
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
                )
            )

    return tuple(physical_nets)


def _find_physical_net_with_sheet_entry(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    sheet_symbol_uid: str,
    entry_name: str,
) -> AltiumCompiledNet | None:
    if not entry_name:
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
    clean_symbol_uid = str(sheet_symbol_uid or "").lower()
    clean_entry_name = str(entry_name or "").lower()
    if not clean_symbol_uid or not clean_entry_name:
        return False
    expected_prefix = f"{clean_symbol_uid}_"
    return any(
        endpoint.role.lower() == "sheet_entry"
        and endpoint.element_id.lower().startswith(expected_prefix)
        and endpoint.name.lower() == clean_entry_name
        for endpoint in net.endpoints
    )


def _find_physical_net_with_any_sheet_entry_name(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    entry_name: str,
) -> AltiumCompiledNet | None:
    clean_entry_name = str(entry_name or "").lower()
    if not clean_entry_name:
        return None
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        if any(
            endpoint.role.lower() == "sheet_entry"
            and endpoint.name.lower() == clean_entry_name
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
    clean_port_name = str(port_name or "").lower()
    if not clean_port_name:
        return False
    return any(
        endpoint.role.lower() == "port" and endpoint.name.lower() == clean_port_name
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


def _find_physical_harness_wire_net_with_harness_entry(
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
        if net.terminals or _compiled_net_label_names(net):
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


def _parent_harness_member_names_for_entry(
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    physical_document_id: str,
    entry_name: str,
) -> tuple[str, ...]:
    prefix = f"{entry_name}."
    prefix_key = prefix.lower()
    members: list[str] = []
    for net in physical_nets:
        if physical_document_id not in net.physical_document_ids:
            continue
        for harness_entry_name in _compiled_net_harness_entry_names(net):
            if not harness_entry_name.lower().startswith(prefix_key):
                continue
            member_name = harness_entry_name[len(prefix) :]
            if member_name:
                members.append(member_name)
    return _dedupe_compiled_net_values(members)


def _append_inter_sheet_link_net(
    link_nets: list[AltiumCompiledNet],
    *,
    link_id: str,
    name: str,
    parent_net: AltiumCompiledNet,
    child_net: AltiumCompiledNet,
    aliases: tuple[str, ...] = (),
    source_net_ids: tuple[str, ...] | None = None,
) -> None:
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
        )
    )


def _child_ports_by_name_for_inter_sheet_linking(
    child_ports_by_logical_id: dict[str, dict[str, "SchPortInfo"]],
    child_document: AltiumCompiledPhysicalDocument,
    child_schdoc: "AltiumSchDoc",
) -> dict[str, "SchPortInfo"]:
    child_ports_by_name = child_ports_by_logical_id.get(
        child_document.logical_document_id
    )
    if child_ports_by_name is None:
        child_ports_by_name = {
            port.name.lower(): port for port in child_schdoc.get_ports() if port.name
        }
        child_ports_by_logical_id[child_document.logical_document_id] = (
            child_ports_by_name
        )
    return child_ports_by_name


def _child_harness_members_for_inter_sheet_entry(
    child_schdoc: "AltiumSchDoc",
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    nested_harness_entry_cache: dict[
        tuple[int, frozenset[int]],
        dict[str, tuple[dict[str, str], ...]],
    ],
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    entry_name: str,
) -> list[dict[str, str]]:
    child_harness_entries = _nested_child_harness_entry_map(
        child_schdoc,
        schdoc_by_source_ref,
        schdoc_by_file_name,
        cache=nested_harness_entry_cache,
    )
    child_harness_members = list(child_harness_entries.get(entry_name.lower(), []))
    if child_harness_members:
        return child_harness_members
    direct_child_harness_entries = _build_child_harness_entry_map(child_schdoc)
    if entry_name.lower() not in direct_child_harness_entries:
        return []
    return [
        {
            "name": member_name,
            "object_id": "",
            "definition_expanded": "true",
        }
        for member_name in _parent_harness_member_names_for_entry(
            physical_nets,
            physical_document_id=parent_document.id,
            entry_name=entry_name,
        )
    ]


def _resolve_parent_harness_link_net(
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    *,
    parent_harness_entry_name: str,
    harness_entry_name: str,
    child_net: AltiumCompiledNet | None,
) -> tuple[AltiumCompiledNet | None, bool, AltiumCompiledNet | None]:
    parent_net = _find_physical_harness_wire_net_with_harness_entry(
        physical_nets,
        physical_document_id=parent_document.id,
        harness_entry_name=parent_harness_entry_name,
    ) or _find_physical_net_by_name(
        physical_nets,
        physical_document_id=parent_document.id,
        net_name=harness_entry_name,
    )
    parent_net_from_child_label = False
    if parent_net is None and child_net is not None:
        for label_name in _compiled_net_label_names(child_net):
            parent_net = _find_physical_net_with_any_sheet_entry_name(
                physical_nets,
                physical_document_id=parent_document.id,
                entry_name=label_name,
            )
            if parent_net is not None:
                parent_net_from_child_label = True
                break

    parent_sheet_entry_bridge_net = None
    if parent_net is not None and child_net is not None:
        for label_name in _compiled_net_label_names(child_net):
            candidate_parent_net = _find_physical_net_with_any_sheet_entry_name(
                physical_nets,
                physical_document_id=parent_document.id,
                entry_name=label_name,
            )
            if (
                candidate_parent_net is not None
                and candidate_parent_net.id != parent_net.id
            ):
                parent_sheet_entry_bridge_net = candidate_parent_net
                break
    return parent_net, parent_net_from_child_label, parent_sheet_entry_bridge_net


def _harness_inter_sheet_source_net_ids(
    child_net: AltiumCompiledNet,
    parent_net: AltiumCompiledNet,
    *,
    parent_net_from_child_label: bool,
    parent_sheet_entry_bridge_net: AltiumCompiledNet | None,
) -> tuple[str, ...] | None:
    if parent_net_from_child_label:
        return tuple(dict.fromkeys((child_net.id, parent_net.id)))
    if parent_sheet_entry_bridge_net is not None:
        return tuple(
            dict.fromkeys(
                (
                    child_net.id,
                    parent_net.id,
                    parent_sheet_entry_bridge_net.id,
                )
            )
        )
    return None


def _append_harness_inter_sheet_links(
    link_nets: list[AltiumCompiledNet],
    stats: dict[str, int],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    entry_name: str,
    child_schdoc: "AltiumSchDoc",
    schdoc_by_source_ref: dict[str, tuple["AltiumSchDoc", ...]],
    schdoc_by_file_name: dict[str, tuple["AltiumSchDoc", ...]],
    nested_harness_entry_cache: dict[
        tuple[int, frozenset[int]],
        dict[str, tuple[dict[str, str], ...]],
    ],
) -> None:
    child_harness_members = _child_harness_members_for_inter_sheet_entry(
        child_schdoc,
        schdoc_by_source_ref,
        schdoc_by_file_name,
        nested_harness_entry_cache,
        physical_nets,
        parent_document,
        entry_name,
    )
    for harness_entry in child_harness_members:
        harness_entry_name = str(harness_entry.get("name", "") or "")
        if not harness_entry_name:
            continue
        stats["candidate_count"] += 1
        stats["harness_candidate_count"] += 1
        parent_harness_entry_name = (
            f"{entry_name}.{harness_entry_name}" if entry_name else harness_entry_name
        )
        child_net = _find_physical_net_with_harness_entry(
            physical_nets,
            physical_document_id=child_document.id,
            harness_entry_name=(
                f"{entry_name}.{harness_entry_name}"
                if entry_name
                else harness_entry_name
            ),
        )
        if child_net is None and harness_entry.get("definition_expanded") == "true":
            child_net = _find_physical_net_by_name(
                physical_nets,
                physical_document_id=child_document.id,
                net_name=harness_entry_name,
            )
        (
            parent_net,
            parent_net_from_child_label,
            parent_sheet_entry_bridge_net,
        ) = _resolve_parent_harness_link_net(
            physical_nets,
            parent_document,
            parent_harness_entry_name=parent_harness_entry_name,
            harness_entry_name=harness_entry_name,
            child_net=child_net,
        )
        if child_net is None:
            stats["unmatched_candidate_count"] += 1
            continue
        if parent_net is None:
            stats["harness_endpoint_only_count"] += 1
            continue
        link_id = _compiled_inter_sheet_link_id(
            parent_document.id,
            child_document.id,
            symbol.source_object_id,
            parent_harness_entry_name,
        )
        _append_inter_sheet_link_net(
            link_nets,
            link_id=link_id,
            name=harness_entry_name,
            parent_net=parent_net,
            child_net=child_net,
            aliases=(entry_name,),
            source_net_ids=_harness_inter_sheet_source_net_ids(
                child_net,
                parent_net,
                parent_net_from_child_label=parent_net_from_child_label,
                parent_sheet_entry_bridge_net=parent_sheet_entry_bridge_net,
            ),
        )
        stats["matched_count"] += 1
        stats["harness_matched_count"] += 1


def _append_regular_inter_sheet_link(
    link_nets: list[AltiumCompiledNet],
    stats: dict[str, int],
    bus_member_sources: dict[
        tuple[str, str, str],
        list[tuple[AltiumCompiledPhysicalDocument, AltiumCompiledNet]],
    ],
    *,
    physical_nets: tuple[AltiumCompiledNet, ...],
    parent_document: AltiumCompiledPhysicalDocument,
    child_document: AltiumCompiledPhysicalDocument,
    symbol: AltiumCompiledSheetSymbol,
    entry_name: str,
    child_ports_by_name: Mapping[str, object],
) -> None:
    inner_port = _parse_entry_repeat(entry_name)
    bus_members = _parse_bus_range(entry_name)
    match_name = inner_port or entry_name
    stats["candidate_count"] += 1
    if bus_members:
        for member_name in bus_members:
            child_member_net = _find_physical_net_by_name_or_label(
                physical_nets,
                physical_document_id=child_document.id,
                net_name=member_name,
            )
            if child_member_net is not None:
                bus_member_sources[
                    (
                        parent_document.id,
                        entry_name.lower(),
                        member_name.lower(),
                    )
                ].append((child_document, child_member_net))
        return

    child_port = child_ports_by_name.get(match_name.lower())
    if child_port is None:
        stats["unmatched_candidate_count"] += 1
        return

    parent_entry_name = (
        f"{inner_port}{child_document.channel_index}"
        if inner_port is not None
        else entry_name
    )
    parent_net = None
    if inner_port is not None:
        parent_net = _find_physical_net_by_name_or_label(
            physical_nets,
            physical_document_id=parent_document.id,
            net_name=parent_entry_name,
        )
    if parent_net is None:
        parent_net = _find_physical_net_with_sheet_entry(
            physical_nets,
            physical_document_id=parent_document.id,
            sheet_symbol_uid=symbol.source_object_id,
            entry_name=parent_entry_name,
        )
    if parent_net is None and inner_port is not None:
        parent_net = _find_physical_net_with_sheet_entry(
            physical_nets,
            physical_document_id=parent_document.id,
            sheet_symbol_uid=symbol.source_object_id,
            entry_name=entry_name,
        )
    child_net = _find_physical_net_with_port(
        physical_nets,
        physical_document_id=child_document.id,
        port_uid=getattr(child_port, "unique_id", "") or "",
        port_name=match_name,
    )
    if parent_net is None or child_net is None:
        stats["unmatched_candidate_count"] += 1
        return

    link_id = _compiled_inter_sheet_link_id(
        parent_document.id,
        child_document.id,
        symbol.source_object_id,
        parent_entry_name,
    )
    _append_inter_sheet_link_net(
        link_nets,
        link_id=link_id,
        name=parent_entry_name,
        parent_net=parent_net,
        child_net=child_net,
        aliases=(match_name,) if match_name != parent_entry_name else (),
    )
    stats["matched_count"] += 1


_BusMemberSource = tuple[AltiumCompiledPhysicalDocument, AltiumCompiledNet]


def _unique_bus_member_sources(
    child_sources: list[_BusMemberSource],
) -> list[_BusMemberSource]:
    unique_sources: list[_BusMemberSource] = []
    seen_source_ids: set[str] = set()
    for child_document, child_net in child_sources:
        if child_net.id in seen_source_ids:
            continue
        seen_source_ids.add(child_net.id)
        unique_sources.append((child_document, child_net))
    return unique_sources


def _bus_member_name(
    unique_sources: list[_BusMemberSource],
    member_name_key: str,
) -> str:
    member_name = unique_sources[0][1].name
    for _child_document, child_net in unique_sources:
        for label_name in _compiled_net_label_names(child_net):
            if label_name.lower() == member_name_key:
                return label_name
    return member_name


def _bus_range_label_nets(
    unique_sources: list[_BusMemberSource],
    physical_nets: tuple[AltiumCompiledNet, ...],
    entry_name_key: str,
) -> list[AltiumCompiledNet]:
    return [
        physical_net
        for child_document, _child_net in unique_sources
        for physical_net in physical_nets
        if child_document.id in physical_net.physical_document_ids
        and physical_net.name.lower() == entry_name_key
        and any(
            endpoint.role == "net_label" and endpoint.name.lower() == entry_name_key
            for endpoint in physical_net.endpoints
        )
    ]


def _bus_member_source_ids(
    source_nets: list[AltiumCompiledNet],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    physical_document_ids: list[str] = []
    terminal_ids: list[str] = []
    endpoint_ids: list[str] = []
    item_ids: list[str] = []
    for child_net in source_nets:
        physical_document_ids.extend(child_net.physical_document_ids)
        terminal_ids.extend(child_net.terminal_ids)
        endpoint_ids.extend(child_net.endpoint_ids)
        item_ids.extend(child_net.item_ids)
    return (
        tuple(dict.fromkeys(physical_document_ids)),
        tuple(dict.fromkeys(terminal_ids)),
        tuple(dict.fromkeys(endpoint_ids)),
        tuple(dict.fromkeys(item_ids)),
    )


def _compiled_bus_member_link(
    *,
    parent_physical_id: str,
    entry_name_key: str,
    member_name_key: str,
    unique_sources: list[_BusMemberSource],
    physical_nets: tuple[AltiumCompiledNet, ...],
) -> AltiumCompiledNet:
    member_name = _bus_member_name(unique_sources, member_name_key)
    source_nets = [child_net for _child_document, child_net in unique_sources]
    bus_label_nets = _bus_range_label_nets(
        unique_sources, physical_nets, entry_name_key
    )
    evidence_nets = [*source_nets, *bus_label_nets]
    bus_label_name = bus_label_nets[0].name if bus_label_nets else entry_name_key
    link_id = f"{parent_physical_id}:bus_member_link:{entry_name_key}:{member_name_key}"
    physical_document_ids, terminal_ids, endpoint_ids, item_ids = (
        _bus_member_source_ids(source_nets)
    )
    return AltiumCompiledNet(
        id=link_id,
        name=member_name,
        original_name=member_name,
        scope="inter_sheet_link",
        logical_document_id=None,
        physical_document_ids=physical_document_ids,
        auto_named=False,
        single_pin=False,
        aliases=(bus_label_name,),
        terminal_ids=terminal_ids,
        terminals=_dedupe_compiled_terminals(
            [terminal for child_net in source_nets for terminal in child_net.terminals]
        ),
        endpoint_ids=endpoint_ids,
        endpoints=_dedupe_compiled_endpoints(
            [endpoint for net in evidence_nets for endpoint in net.endpoints]
        ),
        item_ids=item_ids,
        items=_dedupe_compiled_items(
            [item for net in evidence_nets for item in net.items]
        ),
        source_net_ids=tuple(child_net.id for child_net in source_nets),
        link_ids=(link_id,),
    )


def _append_bus_member_inter_sheet_links(
    link_nets: list[AltiumCompiledNet],
    stats: dict[str, int],
    physical_nets: tuple[AltiumCompiledNet, ...],
    bus_member_sources: dict[tuple[str, str, str], list[_BusMemberSource]],
) -> None:
    for source_key, child_sources in bus_member_sources.items():
        unique_sources = _unique_bus_member_sources(child_sources)
        if len(unique_sources) < 2:
            stats["unmatched_candidate_count"] += 1
            continue
        parent_physical_id, entry_name_key, member_name_key = source_key
        link_nets.append(
            _compiled_bus_member_link(
                parent_physical_id=parent_physical_id,
                entry_name_key=entry_name_key,
                member_name_key=member_name_key,
                unique_sources=unique_sources,
                physical_nets=physical_nets,
            )
        )
        stats["matched_count"] += 1


def _build_inter_sheet_link_nets(
    schdocs: list["AltiumSchDoc"],
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...],
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...],
    sheet_symbol_info_by_id: dict[str, "SchSheetSymbolInfo"],
    physical_nets: tuple[AltiumCompiledNet, ...],
    *,
    options: NetlistOptions,
    project_base_dir: Path | None,
) -> tuple[
    tuple[AltiumCompiledNet, ...],
    dict[str, object],
    tuple[AltiumCompileDiagnostic, ...],
]:
    """Create parent/child bridge rows from physical sheet-entry and port matches."""
    schdoc_by_logical_id = _schdocs_by_logical_id(
        schdocs,
        project_base_dir=project_base_dir,
    )
    schdoc_by_source_ref, schdoc_by_file_name = _schdoc_reference_maps(
        schdocs,
        project_base_dir=project_base_dir,
    )
    nested_harness_entry_cache: dict[
        tuple[int, frozenset[int]],
        dict[str, tuple[dict[str, str], ...]],
    ] = {}
    physical_by_id = {document.id: document for document in physical_documents}
    symbol_by_id = {symbol.id: symbol for symbol in sheet_symbols}
    link_nets: list[AltiumCompiledNet] = []
    stats: dict[str, int] = {
        "candidate_count": 0,
        "matched_count": 0,
        "unmatched_candidate_count": 0,
        "harness_candidate_count": 0,
        "harness_matched_count": 0,
        "harness_endpoint_only_count": 0,
    }
    diagnostics: list[AltiumCompileDiagnostic] = []
    bus_member_sources: dict[
        tuple[str, str, str],
        list[tuple[AltiumCompiledPhysicalDocument, AltiumCompiledNet]],
    ] = defaultdict(list)
    child_ports_by_logical_id: dict[str, dict[str, SchPortInfo]] = {}

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
        if (
            parent_document is None
            or symbol is None
            or symbol_info is None
            or child_schdoc is None
        ):
            continue

        child_ports_by_name = _child_ports_by_name_for_inter_sheet_linking(
            child_ports_by_logical_id,
            child_document,
            child_schdoc,
        )
        for entry in symbol_info.entries:
            entry_name = _inter_sheet_entry_display_name(entry)
            if not entry_name:
                continue

            harness_type = str(getattr(entry, "harness_type", "") or "")
            if harness_type:
                _append_harness_inter_sheet_links(
                    link_nets,
                    stats,
                    physical_nets=physical_nets,
                    parent_document=parent_document,
                    child_document=child_document,
                    symbol=symbol,
                    entry_name=entry_name,
                    child_schdoc=child_schdoc,
                    schdoc_by_source_ref=schdoc_by_source_ref,
                    schdoc_by_file_name=schdoc_by_file_name,
                    nested_harness_entry_cache=nested_harness_entry_cache,
                )
                continue

            _append_regular_inter_sheet_link(
                link_nets,
                stats,
                bus_member_sources,
                physical_nets=physical_nets,
                parent_document=parent_document,
                child_document=child_document,
                symbol=symbol,
                entry_name=entry_name,
                child_ports_by_name=child_ports_by_name,
            )
    _append_bus_member_inter_sheet_links(
        link_nets,
        stats,
        physical_nets,
        bus_member_sources,
    )

    return (
        tuple(link_nets),
        {
            **stats,
            "mode": "physical_tree_bridge_candidates",
            "unmatched_candidate_policy": "deferred_to_naming_and_oracle_parity",
        },
        tuple(diagnostics),
    )


def _dedupe_compiled_endpoints(
    endpoints: list[AltiumCompiledNetEndpoint],
) -> tuple[AltiumCompiledNetEndpoint, ...]:
    seen: set[tuple[str, str, str, str, str, str, str, str, tuple[int, int] | None]] = (
        set()
    )
    result: list[AltiumCompiledNetEndpoint] = []
    for endpoint in endpoints:
        key = (
            endpoint.role,
            endpoint.element_id,
            endpoint.object_id,
            endpoint.name,
            endpoint.parent_id,
            endpoint.designator,
            endpoint.pin,
            endpoint.pin_name,
            endpoint.connection_point,
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


def _compiled_case_insensitive_key(value: str) -> str:
    return value.lower()


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
    if net.logical_document_id is None:
        return False
    bus_members = bus_member_names_by_logical_id.get(net.logical_document_id)
    if not bus_members:
        return False
    return net.name in bus_members


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
        base.lower() == base_name.lower()
        for base in _compiled_net_repeat_sheet_entry_bases(net)
    )


def _compiled_net_has_bus_range_label_for_base(
    net: AltiumCompiledNet,
    base_name: str,
) -> bool:
    return any(
        base.lower() == base_name.lower()
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
                repeat_bases_by_document_id[document_id].add(base.lower())
            for base in _compiled_net_bus_range_label_bases(net):
                bus_bases_by_document_id[document_id].setdefault(base.lower(), base)

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
                endpoint.name.lower()
                for endpoint in net.endpoints
                if endpoint.role == "net_label"
            }
            for document_id in net.physical_document_ids:
                for base in structural_bases.get(document_id, ()):
                    if (
                        base.lower() in label_names
                        and _compiled_net_has_repeat_sheet_entry_for_base(net, base)
                    ):
                        suppress = True
                        break
                if suppress:
                    break
        if not suppress:
            result.append(net)
    return tuple(result)


def _compiled_component_pin_counts(
    components: tuple[AltiumCompiledComponent, ...],
    flat_nets: tuple[AltiumCompiledNet, ...],
    component_pin_root_counts: Mapping[str, Mapping[str, tuple[int, int, int]]],
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
    result: list[AltiumCompiledComponent] = []
    for component in components:
        physical_key = component.physical_designator.lower()
        root_counts = component_pin_root_counts.get(
            component.logical_document_id, {}
        ).get(component.logical_designator.lower())
        if root_counts is not None and not component._project_multipart_collapsed:
            distinct_active_pins, active_pin_roots, masked_pin_count = root_counts
            pin_count = max(0, component.pin_count - masked_pin_count) + max(
                0, active_pin_roots - distinct_active_pins
            )
        elif (
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
) -> tuple[int, int, int | str, str | int]:
    """Approximate legacy merge-list ordering for flattened terminal provenance."""
    primary_power_name = _compiled_net_primary_power_name(net)
    if primary_power_name:
        canonical_rank = (
            0
            if canonical_power_key is not None
            and primary_power_name.upper() == canonical_power_key
            else 1
        )
        return (
            0,
            canonical_rank,
            source_order[net.id],
            primary_power_name.upper(),
        )

    port_names = _compiled_net_port_names(net)
    if port_names:
        port_key = min(port_names, key=lambda name: name.lower()).lower()
        return (1, 0, port_key, source_order[net.id])

    return (2, 0, "", source_order[net.id])


def _compiled_ordered_source_nets(
    group: list[AltiumCompiledNet],
    *,
    hierarchy_mode: str,
    source_order: dict[str, int],
    merge_order: dict[str, int],
) -> list[AltiumCompiledNet]:
    if hierarchy_mode in {"HIERARCHICAL", "STRICT_HIERARCHICAL"}:
        return sorted(
            group,
            key=lambda net: merge_order[net.id],
        )
    primary_power_keys = {
        power_name.upper()
        for net in group
        for power_name in [_compiled_net_primary_power_name(net)]
        if power_name
    }
    label_backed_power_keys = {
        power_name.upper()
        for net in group
        for power_name in [_compiled_net_primary_power_name(net)]
        if power_name and _compiled_net_label_names(net)
    }
    canonical_power_key = None
    if label_backed_power_keys:
        canonical_power_key = min(label_backed_power_keys)
    elif primary_power_keys:
        canonical_power_key = min(primary_power_keys)
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


def _compiled_flat_sheet_number_suffix(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    compile_options: AltiumProjectCompileOptions,
) -> str | None:
    if not compile_options.append_sheet_number_to_local_nets:
        return None
    physical_document_ids = _dedupe_compiled_net_values(
        [
            document_id
            for net in group
            if net.scope == "physical_local"
            for document_id in net.physical_document_ids
        ]
    )
    if len(physical_document_ids) != 1:
        return None
    if any(
        _compiled_net_has_endpoint_role(net, role)
        for net in group
        for role in ("power_port", "port")
    ):
        return None
    physical_document = physical_document_by_id.get(physical_document_ids[0])
    if physical_document is None:
        return None
    return physical_document.sheet_number or None


def _finalize_compiled_flat_name(
    selected_name: str,
    *,
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
    auto_named: bool,
) -> tuple[str, str, str | None, bool]:
    original_name = selected_name
    sheet_number = _compiled_flat_sheet_number_suffix(
        group,
        physical_document_by_id,
        compile_options=compile_options,
    )
    if sheet_number and not original_name.endswith(f"_{sheet_number}"):
        original_name = f"{original_name}_{sheet_number}"
    override_name = next(
        (
            record.override_net_name
            for record in reversed(annotation.net_names)
            if record.original_net_name.casefold() == original_name.casefold()
        ),
        None,
    )
    return override_name or original_name, original_name, override_name, auto_named


def _compiled_higher_level_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    higher_level_names = _compiled_higher_level_bridge_names(group)
    if compile_options.name_nets_hierarchically and higher_level_names:
        return min(higher_level_names, key=lambda name: name.lower()), False
    return None


def _compiled_priority_power_name_candidate(
    power_names: list[str],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    if compile_options.power_port_names_take_priority and power_names:
        return min(power_names, key=lambda name: name.upper()), False
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
        return min(channel_terminal_names, key=lambda name: name.lower()), False
    return None


def _compiled_label_name_candidate(
    group: list[AltiumCompiledNet],
    label_names: list[str],
    compile_options: AltiumProjectCompileOptions,
    *,
    allow_terminal_less_bridge_names: bool,
) -> tuple[str, bool] | None:
    if not label_names:
        return None
    selected_name = min(label_names, key=_altium_net_total_sort_key)
    terminal_label_spellings = [
        effective_name
        for net in group
        for effective_name in [
            _compiled_effective_name_for_scope(
                net,
                hierarchy_mode=compile_options.effective_hierarchy_mode,
            )
        ]
        if effective_name
        and effective_name.lower() == selected_name.lower()
        and net.scope == "physical_local"
        and net.terminals
        and not net.auto_named
        and not _compiled_net_has_endpoint_role(net, "power_port")
        and (
            allow_terminal_less_bridge_names
            or not _compiled_net_is_terminal_less_bridge_evidence(net)
        )
    ]
    if terminal_label_spellings:
        selected_name = min(terminal_label_spellings, key=lambda name: name.lower())
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
        return min(
            terminal_explicit_physical_names, key=lambda name: name.lower()
        ), False
    return None


def _compiled_harness_entry_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    harness_entry_names = [
        harness_entry_name
        for net in group
        if compile_options.effective_hierarchy_mode != "HIERARCHICAL"
        for harness_entry_name in _compiled_net_harness_entry_names(net)
    ]
    if harness_entry_names:
        return min(harness_entry_names, key=lambda name: name.lower()), False
    return None


def _compiled_port_name_candidate(
    group: list[AltiumCompiledNet],
    compile_options: AltiumProjectCompileOptions,
) -> tuple[str, bool] | None:
    port_names = [
        port_name for net in group for port_name in _compiled_net_port_names(net)
    ]
    if compile_options.allow_port_net_names and port_names:
        return min(port_names, key=lambda name: name.lower()), False
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
        and net.name.lower() not in power_name_keys
    ]
    if channel_power_names:
        return min(channel_power_names, key=lambda name: name.lower()), False
    return None


def _compiled_power_name_candidate(power_names: list[str]) -> tuple[str, bool] | None:
    if power_names:
        return min(power_names, key=lambda name: name.upper()), False
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
        return min(positive_names, key=lambda name: name.lower()), include_auto_named
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
    yield _compiled_harness_entry_name_candidate(group, compile_options)
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


def _compiled_flat_name(
    group: list[AltiumCompiledNet],
    physical_document_by_id: dict[str, AltiumCompiledPhysicalDocument],
    *,
    compile_options: AltiumProjectCompileOptions,
    annotation: AnnotationFile,
) -> tuple[str, str, str | None, bool]:
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


def _compiled_flat_auto_named(
    selected_name: str,
    physical_group: list[AltiumCompiledNet],
) -> bool:
    has_harness_member_evidence = any(
        _compiled_net_has_endpoint_role(net, "harness_entry") for net in physical_group
    )
    auto_named = all(net.auto_named for net in physical_group) and not (
        has_harness_member_evidence
    )
    if any(net.auto_named and net.name == selected_name for net in physical_group):
        return True
    return auto_named


def _compiled_first_list_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
) -> list[tuple[str, str]]:
    if hierarchy_mode == "STRICT_HIERARCHICAL":
        return []
    first_list_roots: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        first_list_names = []
        if not _compiled_net_has_endpoint_role(physical_net, "port"):
            first_list_names.extend(_compiled_net_power_names(physical_net))
        if hierarchy_mode == "GLOBAL":
            first_list_names.extend(_compiled_net_label_names(physical_net))
        for first_list_name in first_list_names:
            first_list_key = _compiled_case_insensitive_key(first_list_name)
            existing_root = first_list_roots.get(first_list_key)
            if existing_root is None:
                first_list_roots[first_list_key] = physical_net.id
            else:
                pairs.append((existing_root, physical_net.id))
    return pairs


def _compiled_port_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
) -> list[tuple[str, str]]:
    if hierarchy_mode not in {"FLAT", "GLOBAL"}:
        return []
    port_roots: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        for port_name in _compiled_net_port_merge_list_names(physical_net):
            port_key = _compiled_case_insensitive_key(port_name)
            existing_root = port_roots.get(port_key)
            if existing_root is None:
                port_roots[port_key] = physical_net.id
            else:
                pairs.append((existing_root, physical_net.id))
    return pairs


def _compiled_scoped_harness_entry_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
) -> list[tuple[str, str]]:
    harness_entry_roots: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        for harness_entry_name in _compiled_net_harness_entry_names(physical_net):
            harness_key = "|".join(
                (
                    _compiled_case_insensitive_key(harness_entry_name),
                    *physical_net.physical_document_ids,
                )
            )
            existing_root = harness_entry_roots.get(harness_key)
            if existing_root is None:
                harness_entry_roots[harness_key] = physical_net.id
            else:
                pairs.append((existing_root, physical_net.id))
    return pairs


def _compiled_flat_harness_entry_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
) -> list[tuple[str, str]]:
    if hierarchy_mode not in {"FLAT", "GLOBAL"}:
        return []
    flat_harness_entry_roots: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        for harness_entry_name in _compiled_net_harness_entry_names(physical_net):
            harness_key = _compiled_case_insensitive_key(harness_entry_name)
            existing_root = flat_harness_entry_roots.get(harness_key)
            if existing_root is None:
                flat_harness_entry_roots[harness_key] = physical_net.id
            else:
                pairs.append((existing_root, physical_net.id))
    return pairs


def _compiled_same_name_merge_pairs(
    physical_by_id: dict[str, AltiumCompiledNet],
    hierarchy_mode: str,
    bus_member_names_by_logical_id: dict[str, frozenset[str]],
) -> list[tuple[str, str]]:
    if hierarchy_mode not in {"FLAT", "GLOBAL"}:
        return []
    name_roots: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for physical_net in physical_by_id.values():
        if physical_net.auto_named:
            continue
        if hierarchy_mode == "FLAT" and not _compiled_net_is_flat_bus_member(
            physical_net,
            bus_member_names_by_logical_id,
        ):
            continue
        if hierarchy_mode == "GLOBAL" and _compiled_net_port_merge_list_names(
            physical_net
        ):
            continue
        physical_name = _compiled_effective_name_for_scope(
            physical_net,
            hierarchy_mode=hierarchy_mode,
        )
        if not physical_name:
            continue
        name_key = _compiled_case_insensitive_key(physical_name)
        existing_root = name_roots.get(name_key)
        if existing_root is None:
            name_roots[name_key] = physical_net.id
        else:
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
    terminals = _dedupe_compiled_terminals(
        [terminal for net in ordered_group for terminal in net.terminals]
    )
    terminal_ids = _dedupe_compiled_net_values([terminal.id for terminal in terminals])
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
    item_ids = _dedupe_compiled_net_values(
        [
            item_id
            for net in (*ordered_group, *evidence_link_rows)
            for item_id in net.item_ids
        ]
    )
    items = _dedupe_compiled_items(
        [item for net in (*ordered_group, *evidence_link_rows) for item in net.items]
    )
    physical_document_ids = _dedupe_compiled_net_values(
        [
            document_id
            for net in ordered_group
            for document_id in net.physical_document_ids
        ]
    )
    aliases = tuple(
        sorted(
            {
                alias
                for net in naming_group
                for alias in (*net.aliases, net.name)
                if alias and alias != final_name
            },
            key=_altium_net_total_sort_key,
        )
    )
    link_ids = _dedupe_compiled_net_values(
        [link_id for net in link_rows for link_id in net.link_ids]
    )
    return (
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
    harness_merge_pairs = [
        *_compiled_scoped_harness_entry_merge_pairs(physical_by_id),
        *_compiled_flat_harness_entry_merge_pairs(physical_by_id, hierarchy_mode),
    ]
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
    for master_id, absorbed_id in (
        *power_merge_pairs,
        *port_merge_pairs,
        *harness_merge_pairs,
        *same_name_merge_pairs,
        *hierarchy_link_merge_pairs,
    ):
        merge_physical_sources(master_id=master_id, absorbed_id=absorbed_id)
    power_merge_count = len(power_merge_pairs)
    port_merge_count = len(port_merge_pairs)
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
        auto_named = _compiled_flat_auto_named(name, group)
        ordered_group = _compiled_ordered_source_nets(
            group,
            hierarchy_mode=hierarchy_mode,
            source_order=source_order,
            merge_order=merge_order,
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
    element_id = str(getattr(item, "element_id", "") or "")
    if not physical_document_id or not entry_name or not element_id:
        return None
    suffix = f"_{entry_name}"
    if not element_id.lower().endswith(suffix.lower()):
        return None
    sheet_symbol_uid = element_id[: -len(suffix)]
    if not sheet_symbol_uid:
        return None
    return (physical_document_id, sheet_symbol_uid, entry_name.lower())


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
                keys.add((physical_document_id, port_name.lower()))
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
            key = net.name.lower()
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
                            key=str.lower,
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
        (flat_net.name.lower(), tuple(flat_net.physical_document_ids))
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
        key = (physical_net.name.lower(), tuple(physical_net.physical_document_ids))
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
        net.name.lower() for net in flat_rows if _parse_bus_range(net.name)
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
                physical_symbol.source_object_id,
                entry_name.lower(),
            )
            if key in represented_keys:
                continue
            inner_port = _parse_entry_repeat(entry_name)
            match_name = inner_port or entry_name
            if (
                inner_port is None
                and (
                    physical_symbol.child_physical_document_id,
                    match_name.lower(),
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
                name_key = name.lower()
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
        document.id: positions.get(
            _normalize_project_reference(document.source_path),
            document.ordinal,
        )
        for document in logical_documents
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
    project = design.project
    project_base_dir = project.filepath.parent if project and project.filepath else None
    options = design._options or NetlistOptions()
    effective_hierarchy_mode = design._resolve_design_effective_scope(options)
    compile_options = _compile_options_from_netlist_options(
        options,
        effective_hierarchy_mode=effective_hierarchy_mode,
        project=project,
        allow_device_sheet_editing=allow_device_sheet_editing,
    )
    annotation = load_project_annotation(project.filepath if project else None)
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
        design.schdocs,
        project_base_dir=project_base_dir,
        saved_structure_top_level_file_name=saved_structure_top_level_file_name,
    )
    schdoc_by_logical_id = _schdocs_by_logical_id(
        design.schdocs,
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
        design.schdocs,
        logical_documents,
        sheet_symbols,
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
    physical_instance_offsets = _logical_instance_offsets(physical_documents)
    physical_count_by_logical_id = _physical_count_by_logical_id(physical_documents)
    physical_documents = tuple(
        replace(
            document,
            physical_room_name=_component_naming_room_name(
                document,
                physical_document_by_id,
                physical_instance_offsets=physical_instance_offsets,
                physical_count_by_logical_id=physical_count_by_logical_id,
                compile_options=compile_options,
            ),
        )
        for document in physical_documents
    )
    (
        local_nets,
        local_net_counts,
        local_harness_endpoint_stats,
        component_pin_root_counts,
        local_connectivity_diagnostics,
    ) = _compile_local_connectivity(
        design.schdocs,
        logical_documents,
        options=options,
        effective_hierarchy_mode=effective_hierarchy_mode,
        project_base_dir=project_base_dir,
    )
    physical_nets = _elaborate_local_connectivity(
        local_nets,
        physical_documents,
        components,
        channel_designator_format=_effective_channel_designator_format(
            compile_options.channel_designator_format,
        ),
        compile_options=compile_options,
    )
    inter_sheet_nets, inter_sheet_stats, inter_sheet_diagnostics = (
        _build_inter_sheet_link_nets(
            design.schdocs,
            physical_documents,
            sheet_symbols,
            sheet_symbol_info_by_id,
            physical_nets,
            options=options,
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
        component_pin_root_counts,
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
    top_level_candidates = [
        document for document in logical_documents if document.is_top_level_candidate
    ]
    top_level_document = min(
        top_level_candidates,
        key=lambda document: (
            -len(document.sheet_symbol_ids),
            _natural_sort_key(Path(document.file_name).stem),
        ),
        default=None,
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
    )
    return replace(
        compiled_design,
        diagnostics=(*compiled_design.diagnostics, *projection_diagnostics),
        compiled_schematic_graph=graph,
        physical_page_metadata=physical_page_metadata,
        _component_body_evidence=component_body_evidence,
    )


__all__ = ["compile_design"]
