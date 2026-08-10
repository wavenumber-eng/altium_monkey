"""Compiled schematic design model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .altium_api_markers import public_api

if TYPE_CHECKING:
    from .altium_compiled_schematic_graph import (
        AltiumCompiledSchematicGraph,
        AltiumPhysicalPageMetadata,
    )
    from .altium_netlist_model import Netlist

COMPILED_DESIGN_SCHEMA = "altium_monkey.sch.compiled_design_model.b0"
COMPILED_DESIGN_GENERATOR = "altium_monkey"


def _compiled_connection_point_to_dict(
    connection_point: tuple[int, int] | None,
) -> dict[str, object] | None:
    if connection_point is None:
        return None
    return {
        "x": connection_point[0],
        "y": connection_point[1],
        "units": "altium_coord",
    }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompileDiagnostic:
    """Diagnostic emitted while building a compiled schematic design."""

    severity: str
    code: str
    message: str
    source_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible diagnostic record."""
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source_id": self.source_id,
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledDesignSummary:
    """Summary counts and health for a compiled schematic design."""

    logical_document_count: int
    physical_document_count: int
    sheet_symbol_count: int
    physical_sheet_symbol_count: int
    component_count: int
    net_count: int
    flattened_net_count: int
    diagnostic_count: int
    has_errors: bool
    has_warnings: bool

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible summary record."""
        return {
            "logical_document_count": self.logical_document_count,
            "physical_document_count": self.physical_document_count,
            "sheet_symbol_count": self.sheet_symbol_count,
            "physical_sheet_symbol_count": self.physical_sheet_symbol_count,
            "component_count": self.component_count,
            "net_count": self.net_count,
            "flattened_net_count": self.flattened_net_count,
            "diagnostic_count": self.diagnostic_count,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumProjectCompileOptions:
    """Resolved project options that influence schematic compilation."""

    hierarchy_mode: str
    hierarchy_mode_value: int
    effective_hierarchy_mode: str
    allow_port_net_names: bool
    allow_sheet_entry_net_names: bool
    netlist_single_pin_nets: bool
    append_sheet_number_to_local_nets: bool
    power_port_names_take_priority: bool
    name_nets_hierarchically: bool
    auto_sheet_numbering: bool
    allow_device_sheet_editing: bool
    channel_designator_format: str
    channel_room_naming_style: int
    channel_room_level_separator: str
    new_indexing_of_sheet_symbols: bool
    reorder_documents_on_compile: bool
    push_eco_to_annotation_file: bool
    option_sources: dict[str, str] = field(default_factory=dict)
    deferred_options: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible compile-options record."""
        return {
            "hierarchy_mode": self.hierarchy_mode,
            "hierarchy_mode_value": self.hierarchy_mode_value,
            "effective_hierarchy_mode": self.effective_hierarchy_mode,
            "allow_port_net_names": self.allow_port_net_names,
            "allow_sheet_entry_net_names": self.allow_sheet_entry_net_names,
            "netlist_single_pin_nets": self.netlist_single_pin_nets,
            "append_sheet_number_to_local_nets": (
                self.append_sheet_number_to_local_nets
            ),
            "power_port_names_take_priority": self.power_port_names_take_priority,
            "name_nets_hierarchically": self.name_nets_hierarchically,
            "auto_sheet_numbering": self.auto_sheet_numbering,
            "allow_device_sheet_editing": self.allow_device_sheet_editing,
            "channel_designator_format": self.channel_designator_format,
            "channel_room_naming_style": self.channel_room_naming_style,
            "channel_room_level_separator": self.channel_room_level_separator,
            "new_indexing_of_sheet_symbols": self.new_indexing_of_sheet_symbols,
            "reorder_documents_on_compile": self.reorder_documents_on_compile,
            "push_eco_to_annotation_file": self.push_eco_to_annotation_file,
            "option_sources": dict(sorted(self.option_sources.items())),
            "deferred_options": list(self.deferred_options),
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledAnnotationState:
    """Annotation-file state used for a compiled schematic design."""

    status: str
    path: str | None
    designator_record_count: int = 0
    sheet_number_record_count: int = 0
    net_name_override_count: int = 0
    diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible annotation state record."""
        return {
            "status": self.status,
            "path": self.path,
            "designator_record_count": self.designator_record_count,
            "sheet_number_record_count": self.sheet_number_record_count,
            "net_name_override_count": self.net_name_override_count,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledLogicalDocument:
    """One logical schematic document participating in a compile."""

    id: str
    source_path: str
    file_name: str
    ordinal: int
    is_top_level_candidate: bool
    sheet_symbol_ids: tuple[str, ...] = ()
    parent_sheet_symbol_ids: tuple[str, ...] = ()
    component_source_count: int = 0
    local_net_count: int = 0
    diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible logical-document record."""
        return {
            "id": self.id,
            "source_path": self.source_path,
            "file_name": self.file_name,
            "ordinal": self.ordinal,
            "is_top_level_candidate": self.is_top_level_candidate,
            "sheet_symbol_ids": list(self.sheet_symbol_ids),
            "parent_sheet_symbol_ids": list(self.parent_sheet_symbol_ids),
            "component_source_count": self.component_source_count,
            "local_net_count": self.local_net_count,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledSheetSymbol:
    """Sheet-symbol row with its resolved child logical document when known."""

    id: str
    logical_document_id: str
    source_object_id: str
    designator: str
    child_filename: str
    child_logical_document_id: str | None
    child_source_path: str | None = None
    child_match_kind: str = "none"
    child_candidate_logical_document_ids: tuple[str, ...] = ()
    child_physical_document_ids: tuple[str, ...] = ()
    is_repeat: bool = False
    repeat_prefix: str | None = None
    repeat_start: int | None = None
    repeat_end: int | None = None
    entry_count: int = 0
    diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible sheet-symbol record."""
        return {
            "id": self.id,
            "logical_document_id": self.logical_document_id,
            "source_object_id": self.source_object_id,
            "designator": self.designator,
            "child_filename": self.child_filename,
            "child_logical_document_id": self.child_logical_document_id,
            "child_source_path": self.child_source_path,
            "child_match_kind": self.child_match_kind,
            "child_candidate_logical_document_ids": list(
                self.child_candidate_logical_document_ids
            ),
            "child_physical_document_ids": list(self.child_physical_document_ids),
            "is_repeat": self.is_repeat,
            "repeat_prefix": self.repeat_prefix,
            "repeat_start": self.repeat_start,
            "repeat_end": self.repeat_end,
            "entry_count": self.entry_count,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledPhysicalSheetSymbol:
    """One physical sheet-symbol placement in the compiled hierarchy."""

    id: str
    logical_sheet_symbol_id: str
    owner_logical_document_id: str
    owner_physical_document_id: str
    source_object_id: str
    logical_designator: str
    physical_designator: str
    sheet_symbol_file_name: str
    child_logical_document_id: str | None
    child_source_path: str | None
    child_physical_document_id: str
    child_physical_instance_path: str
    child_physical_instance_unique_id: str
    child_channel_index: int
    entry_count: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible physical sheet-symbol record."""
        return {
            "id": self.id,
            "logical_sheet_symbol_id": self.logical_sheet_symbol_id,
            "owner_logical_document_id": self.owner_logical_document_id,
            "owner_physical_document_id": self.owner_physical_document_id,
            "source_object_id": self.source_object_id,
            "logical_designator": self.logical_designator,
            "physical_designator": self.physical_designator,
            "sheet_symbol_file_name": self.sheet_symbol_file_name,
            "child_logical_document_id": self.child_logical_document_id,
            "child_source_path": self.child_source_path,
            "child_physical_document_id": self.child_physical_document_id,
            "child_physical_instance_path": self.child_physical_instance_path,
            "child_physical_instance_unique_id": self.child_physical_instance_unique_id,
            "child_channel_index": self.child_channel_index,
            "entry_count": self.entry_count,
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledPhysicalDocument:
    """One physical schematic sheet instance in the compiled design."""

    id: str
    logical_document_id: str
    source_path: str
    file_name: str
    ordinal: int
    physical_instance_path: str
    physical_instance_unique_id: str
    channel_index: int
    channel_prefix: str | None
    channel_alpha: str | None
    room_name: str
    parent_id: str | None
    parent_sheet_symbol_id: str | None
    child_ids: tuple[str, ...] = ()
    component_ids: tuple[str, ...] = ()
    sheet_number: str | None = None
    document_number: str | None = None
    physical_room_name: str = ""
    diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible physical-document record."""
        return {
            "id": self.id,
            "logical_document_id": self.logical_document_id,
            "source_path": self.source_path,
            "file_name": self.file_name,
            "ordinal": self.ordinal,
            "physical_instance_path": self.physical_instance_path,
            "physical_instance_unique_id": self.physical_instance_unique_id,
            "channel_index": self.channel_index,
            "channel_prefix": self.channel_prefix,
            "channel_alpha": self.channel_alpha,
            "room_name": self.room_name,
            "parent_id": self.parent_id,
            "parent_sheet_symbol_id": self.parent_sheet_symbol_id,
            "child_ids": list(self.child_ids),
            "component_ids": list(self.component_ids),
            "sheet_number": self.sheet_number,
            "document_number": self.document_number,
            "physical_room_name": self.physical_room_name,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledComponent:
    """One physical component row in the compiled design."""

    id: str
    logical_document_id: str
    physical_document_id: str
    source_object_id: str
    source_unique_id_path: str
    logical_designator: str
    physical_designator: str
    display_designator: str
    lib_reference: str
    design_item_id: str
    footprint: str
    component_kind: str
    component_kind_value: int
    include_in_netlist: bool
    exclude_from_bom: bool
    value: str
    description: str
    parameters: tuple[tuple[str, str], ...]
    pin_count: int
    all_pin_count: int
    part_count: int
    current_part_id: int
    annotation_state: str = "logical"
    annotation_locked: bool = False
    diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()
    _project_multipart_collapsed: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible component record."""
        return {
            "id": self.id,
            "logical_document_id": self.logical_document_id,
            "physical_document_id": self.physical_document_id,
            "source_object_id": self.source_object_id,
            "source_unique_id_path": self.source_unique_id_path,
            "logical_designator": self.logical_designator,
            "physical_designator": self.physical_designator,
            "display_designator": self.display_designator,
            "lib_reference": self.lib_reference,
            "design_item_id": self.design_item_id,
            "footprint": self.footprint,
            "component_kind": self.component_kind,
            "component_kind_value": self.component_kind_value,
            "include_in_netlist": self.include_in_netlist,
            "exclude_from_bom": self.exclude_from_bom,
            "value": self.value,
            "description": self.description,
            "parameters": dict(self.parameters),
            "pin_count": self.pin_count,
            "all_pin_count": self.all_pin_count,
            "part_count": self.part_count,
            "current_part_id": self.current_part_id,
            "annotation_state": self.annotation_state,
            "annotation_locked": self.annotation_locked,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledNetTerminal:
    """Structured terminal row carried by staged compiled nets."""

    id: str
    designator: str
    pin: str
    pin_name: str = ""
    pin_type: str = "PASSIVE"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible terminal record."""
        return {
            "id": self.id,
            "designator": self.designator,
            "pin": self.pin,
            "pin_name": self.pin_name,
            "pin_type": self.pin_type,
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledNetEndpoint:
    """Structured endpoint row carried by staged compiled nets."""

    id: str
    role: str
    element_id: str = ""
    object_id: str = ""
    name: str = ""
    parent_id: str = ""
    designator: str = ""
    pin: str = ""
    pin_name: str = ""
    connection_point: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible endpoint record."""
        return {
            "id": self.id,
            "role": self.role,
            "element_id": self.element_id,
            "object_id": self.object_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "designator": self.designator,
            "pin": self.pin,
            "pin_name": self.pin_name,
            "connection_point": _compiled_connection_point_to_dict(
                self.connection_point
            ),
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledNetItem:
    """Structured net-item provenance row carried by staged compiled nets."""

    id: str
    kind: str
    name: str = ""
    parent_id: str = ""
    element_id: str = ""
    object_id: str = ""
    designator: str = ""
    pin: str = ""
    pin_name: str = ""
    physical_document_id: str = ""
    connection_point: tuple[int, int] | None = None
    removed: bool = False
    inferred_from_harness: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible net-item record."""
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "parent_id": self.parent_id,
            "element_id": self.element_id,
            "object_id": self.object_id,
            "designator": self.designator,
            "pin": self.pin,
            "pin_name": self.pin_name,
            "physical_document_id": self.physical_document_id,
            "connection_point": _compiled_connection_point_to_dict(
                self.connection_point
            ),
            "removed": self.removed,
            "inferred_from_harness": self.inferred_from_harness,
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledNet:
    """One compiled net row."""

    id: str
    name: str
    original_name: str | None = None
    override_name: str | None = None
    scope: str = "logical_local"
    logical_document_id: str | None = None
    physical_document_ids: tuple[str, ...] = ()
    auto_named: bool = False
    single_pin: bool = False
    aliases: tuple[str, ...] = ()
    terminal_ids: tuple[str, ...] = ()
    terminals: tuple[AltiumCompiledNetTerminal, ...] = ()
    endpoint_ids: tuple[str, ...] = ()
    endpoints: tuple[AltiumCompiledNetEndpoint, ...] = ()
    item_ids: tuple[str, ...] = ()
    items: tuple[AltiumCompiledNetItem, ...] = ()
    source_net_ids: tuple[str, ...] = ()
    parent_net_id: str = ""
    child_net_id: str = ""
    link_ids: tuple[str, ...] = ()
    diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible net record."""
        return {
            "id": self.id,
            "name": self.name,
            "original_name": self.original_name,
            "override_name": self.override_name,
            "scope": self.scope,
            "logical_document_id": self.logical_document_id,
            "physical_document_ids": list(self.physical_document_ids),
            "auto_named": self.auto_named,
            "single_pin": self.single_pin,
            "aliases": list(self.aliases),
            "terminal_ids": list(self.terminal_ids),
            "terminals": [terminal.to_dict() for terminal in self.terminals],
            "endpoint_ids": list(self.endpoint_ids),
            "endpoints": [endpoint.to_dict() for endpoint in self.endpoints],
            "item_ids": list(self.item_ids),
            "items": [item.to_dict() for item in self.items],
            "source_net_ids": list(self.source_net_ids),
            "parent_net_id": self.parent_net_id,
            "child_net_id": self.child_net_id,
            "link_ids": list(self.link_ids),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }


@public_api
@dataclass(frozen=True, slots=True)
class AltiumCompiledDesign:
    """Compiled schematic design returned by `AltiumDesign.compile()`."""

    schema: str
    source_path: str | None
    options: AltiumProjectCompileOptions
    annotation: AltiumCompiledAnnotationState
    logical_documents: tuple[AltiumCompiledLogicalDocument, ...]
    physical_documents: tuple[AltiumCompiledPhysicalDocument, ...]
    sheet_symbols: tuple[AltiumCompiledSheetSymbol, ...]
    components: tuple[AltiumCompiledComponent, ...]
    nets: tuple[AltiumCompiledNet, ...]
    diagnostics: tuple[AltiumCompileDiagnostic, ...]
    physical_sheet_symbols: tuple[AltiumCompiledPhysicalSheetSymbol, ...] = ()
    compile: dict[str, object] = field(default_factory=dict)
    debug: dict[str, object] = field(default_factory=dict)
    compiled_schematic_graph: AltiumCompiledSchematicGraph | None = None
    physical_page_metadata: tuple[AltiumPhysicalPageMetadata, ...] = ()
    _component_body_evidence: tuple[AltiumCompiledComponent, ...] = ()

    @property
    def top_physical_document(self) -> AltiumCompiledPhysicalDocument | None:
        """Return the first top-level physical document, when one exists."""
        top_id = self.compile.get("top_level_physical_document_id")
        if isinstance(top_id, str) and top_id:
            for document in self.physical_documents:
                if document.id == top_id:
                    return document
        for document in self.physical_documents:
            if document.parent_id is None:
                return document
        return self.physical_documents[0] if self.physical_documents else None

    @property
    def summary(self) -> AltiumCompiledDesignSummary:
        """Return summary counts and health for this compiled design."""
        diagnostics = [
            *self.diagnostics,
            *self.annotation.diagnostics,
            *[
                diagnostic
                for document in self.logical_documents
                for diagnostic in document.diagnostics
            ],
            *[
                diagnostic
                for document in self.physical_documents
                for diagnostic in document.diagnostics
            ],
            *[
                diagnostic
                for symbol in self.sheet_symbols
                for diagnostic in symbol.diagnostics
            ],
            *[
                diagnostic
                for component in self.components
                for diagnostic in component.diagnostics
            ],
            *[diagnostic for net in self.nets for diagnostic in net.diagnostics],
        ]
        net_flattening = self.compile.get("net_flattening")
        flattened_net_count = len(
            [net for net in self.nets if net.scope == "compiled_flat"]
        )
        if isinstance(net_flattening, dict) and isinstance(
            net_flattening.get("flattened_net_count"),
            int,
        ):
            flattened_net_count = net_flattening["flattened_net_count"]
        return AltiumCompiledDesignSummary(
            logical_document_count=len(self.logical_documents),
            physical_document_count=len(self.physical_documents),
            sheet_symbol_count=len(self.sheet_symbols),
            physical_sheet_symbol_count=len(self.physical_sheet_symbols),
            component_count=len(self.components),
            net_count=len(self.nets),
            flattened_net_count=flattened_net_count,
            diagnostic_count=len(diagnostics),
            has_errors=any(
                diagnostic.severity == "error" for diagnostic in diagnostics
            ),
            has_warnings=any(
                diagnostic.severity == "warning" for diagnostic in diagnostics
            ),
        )

    def to_dict(self, *, include_debug: bool = False) -> dict[str, object]:
        """Return a deterministic JSON-compatible compiled-design payload."""
        payload: dict[str, object] = {
            "schema": self.schema,
            "summary": self.summary.to_dict(),
            "options": self.options.to_dict(),
            "annotation": self.annotation.to_dict(),
            "logical_documents": [
                document.to_dict() for document in self.logical_documents
            ],
            "physical_documents": [
                document.to_dict() for document in self.physical_documents
            ],
            "sheet_symbols": [symbol.to_dict() for symbol in self.sheet_symbols],
            "physical_sheet_symbols": [
                symbol.to_dict() for symbol in self.physical_sheet_symbols
            ],
            "components": [component.to_dict() for component in self.components],
            "nets": [net.to_dict() for net in self.nets],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "source_path": self.source_path,
            "compile": dict(sorted(self.compile.items())),
        }
        if include_debug:
            payload["debug"] = dict(sorted(self.debug.items()))
        return payload

    def to_netlist(self) -> "Netlist":
        """Return the existing Netlist model derived from compiled-flat rows."""
        from .altium_compiled_design_netlist import compiled_design_to_netlist

        return compiled_design_to_netlist(self)


__all__ = [
    "COMPILED_DESIGN_GENERATOR",
    "COMPILED_DESIGN_SCHEMA",
    "AltiumCompileDiagnostic",
    "AltiumCompiledAnnotationState",
    "AltiumCompiledComponent",
    "AltiumCompiledDesign",
    "AltiumCompiledDesignSummary",
    "AltiumCompiledLogicalDocument",
    "AltiumCompiledNet",
    "AltiumCompiledNetEndpoint",
    "AltiumCompiledNetItem",
    "AltiumCompiledNetTerminal",
    "AltiumCompiledPhysicalDocument",
    "AltiumCompiledPhysicalSheetSymbol",
    "AltiumCompiledSheetSymbol",
    "AltiumProjectCompileOptions",
]
