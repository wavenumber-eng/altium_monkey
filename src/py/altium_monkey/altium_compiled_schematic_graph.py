"""Altium-owned transport for the CAD-neutral compiled schematic graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from .altium_compiled_schematic_graph_identity import (
    SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE,
)

ALTIUM_COMPILED_SCHEMATIC_GRAPH_SCHEMA = "altium_monkey.compiled_schematic_graph.a0"
ALTIUM_COMPILED_SCHEMATIC_GRAPH_TYPE = "sch.compiled_schematic_graph"
ALTIUM_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE = (
    SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE
)

COLLECTION_NAMES = (
    "unit_definitions",
    "page_definitions",
    "unit_occurrences",
    "page_occurrences",
    "hierarchy_occurrences",
    "component_occurrences",
    "local_net_occurrences",
    "terminal_occurrences",
    "hierarchy_terminal_bindings",
    "graphical_artifact_links",
)
_COLLECTION_TYPES = {name: f"sch.{name.removesuffix('s')}" for name in COLLECTION_NAMES}
_COLLECTION_TYPES.update(
    {
        "hierarchy_occurrences": "sch.hierarchy_occurrence",
        "hierarchy_terminal_bindings": "sch.hierarchy_terminal_binding",
        "graphical_artifact_links": "sch.graphical_artifact_link",
    }
)
_REF_TYPES: dict[str, tuple[tuple[str, str], ...]] = {
    "page_definitions": (("unit_definition_ref", "sch.unit_definition"),),
    "unit_occurrences": (
        ("unit_definition_ref", "sch.unit_definition"),
        ("parent_hierarchy_occurrence_ref", "sch.hierarchy_occurrence"),
    ),
    "page_occurrences": (
        ("page_definition_ref", "sch.page_definition"),
        ("unit_occurrence_ref", "sch.unit_occurrence"),
    ),
    "hierarchy_occurrences": (
        ("parent_unit_occurrence_ref", "sch.unit_occurrence"),
        ("parent_page_occurrence_ref", "sch.page_occurrence"),
        ("child_unit_occurrence_ref", "sch.unit_occurrence"),
    ),
    "component_occurrences": (("page_occurrence_ref", "sch.page_occurrence"),),
    "local_net_occurrences": (("page_occurrence_ref", "sch.page_occurrence"),),
    "terminal_occurrences": (
        ("page_occurrence_ref", "sch.page_occurrence"),
        ("local_net_occurrence_ref", "sch.local_net_occurrence"),
        ("component_occurrence_ref", "sch.component_occurrence"),
    ),
    "hierarchy_terminal_bindings": (
        ("hierarchy_occurrence_ref", "sch.hierarchy_occurrence"),
        ("parent_terminal_occurrence_ref", "sch.terminal_occurrence"),
        ("child_terminal_occurrence_ref", "sch.terminal_occurrence"),
    ),
    "graphical_artifact_links": (("page_occurrence_ref", "sch.page_occurrence"),),
}
_REF_LIST_TYPES: dict[str, tuple[tuple[str, str], ...]] = {
    "unit_definitions": (("page_definition_refs", "sch.page_definition"),),
    "unit_occurrences": (("page_occurrence_refs", "sch.page_occurrence"),),
}
_TERMINAL_ROLES = {"component_pin", "sheet_entry", "port", "power_port"}
_RESOLUTION_DIAGNOSTICS = {
    "logical_pin_unresolved",
    "component_occurrence_unresolved",
    "hierarchy_terminal_binding_unresolved",
    "design_net_unresolved",
}
_REQUIRED_ROW_FIELDS: dict[str, frozenset[str]] = {
    "unit_definitions": frozenset(
        {"type", "id", "display_name", "page_definition_refs", "source_identity"}
    ),
    "page_definitions": frozenset(
        {"type", "id", "unit_definition_ref", "display_name", "source_identity"}
    ),
    "unit_occurrences": frozenset(
        {
            "type",
            "id",
            "unit_definition_ref",
            "display_name",
            "page_occurrence_refs",
            "source_identity",
        }
    ),
    "page_occurrences": frozenset(
        {
            "type",
            "id",
            "page_definition_ref",
            "unit_occurrence_ref",
            "display_name",
            "sheet_number",
            "instance_order",
            "source_identity",
        }
    ),
    "hierarchy_occurrences": frozenset(
        {
            "type",
            "id",
            "parent_unit_occurrence_ref",
            "parent_page_occurrence_ref",
            "child_unit_occurrence_ref",
            "source_identity",
        }
    ),
    "component_occurrences": frozenset(
        {
            "type",
            "id",
            "page_occurrence_ref",
            "source_designator",
            "physical_designator",
            "display_designator",
            "unit",
            "body_style",
            "source_identity",
        }
    ),
    "local_net_occurrences": frozenset(
        {
            "type",
            "id",
            "page_occurrence_ref",
            "display_name",
            "qualified_name",
            "aliases",
            "source_identity",
        }
    ),
    "terminal_occurrences": frozenset(
        {
            "type",
            "id",
            "page_occurrence_ref",
            "local_net_occurrence_ref",
            "role",
            "name",
            "pin_designator",
            "resolution_diagnostics",
            "source_identity",
        }
    ),
    "hierarchy_terminal_bindings": frozenset(
        {
            "type",
            "id",
            "hierarchy_occurrence_ref",
            "parent_terminal_occurrence_ref",
            "child_terminal_occurrence_ref",
            "source_identity",
        }
    ),
    "graphical_artifact_links": frozenset(
        {
            "type",
            "id",
            "page_occurrence_ref",
            "target_type",
            "target_ref",
            "artifact_key",
            "element_id",
            "source_identity",
        }
    ),
}
_OPTIONAL_ROW_FIELDS: dict[str, frozenset[str]] = {
    "unit_occurrences": frozenset({"parent_hierarchy_occurrence_ref"}),
    "terminal_occurrences": frozenset(
        {"component_occurrence_ref", "design_component_pin_ref", "design_net_ref"}
    ),
    "hierarchy_terminal_bindings": frozenset({"design_net_ref"}),
}
_INTEGER_ROW_FIELDS = {"instance_order", "unit", "body_style"}
_LIST_ROW_FIELDS = {
    "page_definition_refs",
    "page_occurrence_refs",
    "aliases",
    "resolution_diagnostics",
}


def _validate_row_field(field_name: str, value: object) -> None:
    if field_name in _INTEGER_ROW_FIELDS:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"compiled schematic graph {field_name} must be an integer"
            )
        return
    if field_name in _LIST_ROW_FIELDS:
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(
                f"compiled schematic graph {field_name} must be a list of strings"
            )
        return
    if field_name != "source_identity" and not isinstance(value, str):
        raise ValueError(f"compiled schematic graph {field_name} must be a string")


def _validate_row_shape(collection: str, row: dict[str, object]) -> None:
    required = _REQUIRED_ROW_FIELDS[collection]
    missing = sorted(required - row.keys())
    if missing:
        raise ValueError(
            f"compiled schematic graph {collection} row is missing required fields: "
            + ", ".join(missing)
        )
    allowed = required | _OPTIONAL_ROW_FIELDS.get(collection, frozenset())
    unexpected = sorted(row.keys() - allowed)
    if unexpected:
        raise ValueError(
            f"compiled schematic graph {collection} row has unknown fields: "
            + ", ".join(unexpected)
        )
    source_identity = row["source_identity"]
    if not isinstance(source_identity, dict) or not source_identity:
        raise ValueError("compiled schematic graph source_identity must be an object")
    for field_name, value in row.items():
        _validate_row_field(field_name, value)


def _payload_collection(
    payload: dict[str, object], name: str
) -> list[dict[str, object]]:
    value = payload.get(name)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"compiled schematic graph {name} must be a list of objects")
    return cast(list[dict[str, object]], value)


@dataclass
class AltiumCompiledSchematicGraph:
    """Versioned graph payload with the ten generic contract collections."""

    unit_definitions: list[dict[str, object]] = field(default_factory=list)
    page_definitions: list[dict[str, object]] = field(default_factory=list)
    unit_occurrences: list[dict[str, object]] = field(default_factory=list)
    page_occurrences: list[dict[str, object]] = field(default_factory=list)
    hierarchy_occurrences: list[dict[str, object]] = field(default_factory=list)
    component_occurrences: list[dict[str, object]] = field(default_factory=list)
    local_net_occurrences: list[dict[str, object]] = field(default_factory=list)
    terminal_occurrences: list[dict[str, object]] = field(default_factory=list)
    hierarchy_terminal_bindings: list[dict[str, object]] = field(default_factory=list)
    graphical_artifact_links: list[dict[str, object]] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "schema": ALTIUM_COMPILED_SCHEMATIC_GRAPH_SCHEMA,
            "type": ALTIUM_COMPILED_SCHEMATIC_GRAPH_TYPE,
            "identity_namespace": ALTIUM_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE,
            **{name: list(getattr(self, name)) for name in COLLECTION_NAMES},
        }

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> "AltiumCompiledSchematicGraph":
        validate_compiled_schematic_graph(payload)
        return cls(
            **{
                name: [dict(row) for row in _payload_collection(payload, name)]
                for name in COLLECTION_NAMES
            }
        )


@dataclass(frozen=True, slots=True)
class AltiumPhysicalPageMetadata:
    """Altium-only physical/render metadata keyed by canonical page occurrence."""

    page_occurrence_ref: str
    physical_instance_path: str
    channel_index: int = 0
    channel_prefix: str = ""
    channel_alpha: str = ""
    room_name: str = ""
    physical_room_name: str = ""
    document_number: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "page_occurrence_ref": self.page_occurrence_ref,
            "physical_instance_path": self.physical_instance_path,
            "channel_index": self.channel_index,
            "channel_prefix": self.channel_prefix,
            "channel_alpha": self.channel_alpha,
            "room_name": self.room_name,
            "physical_room_name": self.physical_room_name,
            "document_number": self.document_number,
        }


def _validate_payload_header(payload: dict[str, object]) -> None:
    if payload.get("schema") != ALTIUM_COMPILED_SCHEMATIC_GRAPH_SCHEMA:
        raise ValueError("unsupported compiled schematic graph schema")
    if payload.get("type") != ALTIUM_COMPILED_SCHEMATIC_GRAPH_TYPE:
        raise ValueError("invalid compiled schematic graph type")
    if (
        payload.get("identity_namespace")
        != ALTIUM_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE
    ):
        raise ValueError("invalid compiled schematic graph identity namespace")


def _validate_ref_type(
    value: object,
    *,
    expected_type: str,
    field: str,
    row_by_id: dict[str, dict[str, object]],
) -> None:
    if value is None or str(value) == "":
        return
    target = row_by_id.get(str(value))
    if target is None:
        raise ValueError(f"unresolved compiled schematic graph ref {field}={value}")
    if target.get("type") != expected_type:
        raise ValueError(
            f"compiled schematic graph ref {field} must target {expected_type}"
        )


def _validate_collection_rows(name: str, values: list[dict[str, object]]) -> None:
    expected_type = _COLLECTION_TYPES[name]
    for row in values:
        _validate_row_shape(name, row)
        if row.get("type") != expected_type:
            raise ValueError(
                f"compiled schematic graph {name} rows must use {expected_type}"
            )


def _validate_scalar_refs(
    collections: dict[str, list[dict[str, object]]],
    row_by_id: dict[str, dict[str, object]],
) -> None:
    for collection, specs in _REF_TYPES.items():
        for row in collections[collection]:
            for field_name, expected_type in specs:
                _validate_ref_type(
                    row.get(field_name),
                    expected_type=expected_type,
                    field=field_name,
                    row_by_id=row_by_id,
                )


def _validate_list_refs(
    collections: dict[str, list[dict[str, object]]],
    row_by_id: dict[str, dict[str, object]],
) -> None:
    for collection, specs in _REF_LIST_TYPES.items():
        for row in collections[collection]:
            for field_name, expected_type in specs:
                values = row.get(field_name, [])
                if not isinstance(values, list):
                    raise ValueError(
                        f"compiled schematic graph {field_name} must be a list"
                    )
                for value in values:
                    _validate_ref_type(
                        value,
                        expected_type=expected_type,
                        field=field_name,
                        row_by_id=row_by_id,
                    )


def _validate_graph_rows(
    payload: dict[str, object],
) -> dict[str, dict[str, object]]:
    _validate_payload_header(payload)
    collections = {
        name: _payload_collection(payload, name) for name in COLLECTION_NAMES
    }
    rows = [row for values in collections.values() for row in values]
    for name, values in collections.items():
        _validate_collection_rows(name, values)
    ids = [str(row.get("id", "")) for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError(
            "compiled schematic graph row ids must be unique and non-empty"
        )
    row_by_id = {str(row["id"]): row for row in rows}
    _validate_scalar_refs(collections, row_by_id)
    _validate_list_refs(collections, row_by_id)
    return row_by_id


def _validate_definition_ownership(payload: dict[str, object]) -> None:
    unit_by_id = {
        str(row["id"]): row for row in _payload_collection(payload, "unit_definitions")
    }
    for page in _payload_collection(payload, "page_definitions"):
        unit = unit_by_id[str(page["unit_definition_ref"])]
        refs = unit.get("page_definition_refs", [])
        if not isinstance(refs, list) or str(page["id"]) not in map(str, refs):
            raise ValueError("page definition is not listed by its owning unit")


def _validate_occurrence_ownership(payload: dict[str, object]) -> None:
    page_definition_by_id = {
        str(row["id"]): row for row in _payload_collection(payload, "page_definitions")
    }
    unit_by_id = {
        str(row["id"]): row for row in _payload_collection(payload, "unit_occurrences")
    }
    for page in _payload_collection(payload, "page_occurrences"):
        definition = page_definition_by_id[str(page["page_definition_ref"])]
        unit = unit_by_id[str(page["unit_occurrence_ref"])]
        if definition.get("unit_definition_ref") != unit.get("unit_definition_ref"):
            raise ValueError("page occurrence definition has the wrong unit owner")
        refs = unit.get("page_occurrence_refs", [])
        if not isinstance(refs, list) or str(page["id"]) not in map(str, refs):
            raise ValueError(
                "page occurrence is not listed by its owning unit occurrence"
            )


def _validate_hierarchy(payload: dict[str, object]) -> None:
    page_by_id = {
        str(row["id"]): row for row in _payload_collection(payload, "page_occurrences")
    }
    unit_by_id = {
        str(row["id"]): row for row in _payload_collection(payload, "unit_occurrences")
    }
    parent_by_child: dict[str, str] = {}
    for hierarchy in _payload_collection(payload, "hierarchy_occurrences"):
        hierarchy_ref = str(hierarchy["id"])
        parent_unit_ref = str(hierarchy["parent_unit_occurrence_ref"])
        parent_page = page_by_id[str(hierarchy["parent_page_occurrence_ref"])]
        child_ref = str(hierarchy["child_unit_occurrence_ref"])
        if str(parent_page.get("unit_occurrence_ref", "")) != parent_unit_ref:
            raise ValueError("hierarchy parent page has the wrong unit owner")
        if child_ref in parent_by_child:
            raise ValueError("unit occurrence has multiple incoming hierarchy owners")
        parent_by_child[child_ref] = parent_unit_ref
        child = unit_by_id[child_ref]
        if str(child.get("parent_hierarchy_occurrence_ref", "")) != hierarchy_ref:
            raise ValueError("unit occurrence hierarchy inverse is inconsistent")
    for unit_ref in unit_by_id:
        visited: set[str] = set()
        current_ref = unit_ref
        while current_ref in parent_by_child:
            if current_ref in visited:
                raise ValueError("schematic hierarchy occurrences must be acyclic")
            visited.add(current_ref)
            current_ref = parent_by_child[current_ref]


def _terminal_diagnostics(terminal: dict[str, object]) -> set[str]:
    raw_diagnostics = terminal.get("resolution_diagnostics", [])
    if not isinstance(raw_diagnostics, list):
        raise ValueError("terminal resolution_diagnostics must be a list")
    diagnostics = {str(value) for value in raw_diagnostics}
    if not diagnostics <= _RESOLUTION_DIAGNOSTICS:
        raise ValueError("terminal occurrence has unregistered diagnostics")
    return diagnostics


def _validate_terminal_owner(
    terminal: dict[str, object],
    component_by_id: dict[str, dict[str, object]],
    local_by_id: dict[str, dict[str, object]],
) -> None:
    page_ref = str(terminal.get("page_occurrence_ref", ""))
    component_ref = terminal.get("component_occurrence_ref")
    if (
        component_ref is not None
        and component_by_id[str(component_ref)].get("page_occurrence_ref") != page_ref
    ):
        raise ValueError("terminal and component occurrence owners differ")
    local_ref = terminal.get("local_net_occurrence_ref")
    if (
        local_ref is not None
        and local_by_id[str(local_ref)].get("page_occurrence_ref") != page_ref
    ):
        raise ValueError("terminal and local-net occurrence owners differ")


def _validate_component_pin_terminal(
    terminal: dict[str, object], diagnostics: set[str]
) -> None:
    if (
        terminal.get("component_occurrence_ref") is None
        and "component_occurrence_unresolved" not in diagnostics
    ):
        raise ValueError(
            "component-pin terminal needs component ownership or a diagnostic"
        )
    if (
        terminal.get("design_component_pin_ref") is None
        and "logical_pin_unresolved" not in diagnostics
    ):
        raise ValueError(
            "component-pin terminal needs logical-pin ownership or a diagnostic"
        )


def _validate_terminal_rows(payload: dict[str, object]) -> None:
    component_by_id = {
        str(row["id"]): row
        for row in _payload_collection(payload, "component_occurrences")
    }
    local_by_id = {
        str(row["id"]): row
        for row in _payload_collection(payload, "local_net_occurrences")
    }
    for terminal in _payload_collection(payload, "terminal_occurrences"):
        role = str(terminal.get("role", ""))
        if role not in _TERMINAL_ROLES:
            raise ValueError(f"terminal occurrence has invalid role {role!r}")
        diagnostics = _terminal_diagnostics(terminal)
        _validate_terminal_owner(terminal, component_by_id, local_by_id)
        if role == "component_pin":
            _validate_component_pin_terminal(terminal, diagnostics)


def _page_refs_by_unit(payload: dict[str, object]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for page in _payload_collection(payload, "page_occurrences"):
        result.setdefault(str(page.get("unit_occurrence_ref", "")), set()).add(
            str(page["id"])
        )
    return result


def _validate_binding_row(
    binding: dict[str, object],
    hierarchy_by_id: dict[str, dict[str, object]],
    terminal_by_id: dict[str, dict[str, object]],
    page_refs_by_unit: dict[str, set[str]],
) -> str:
    hierarchy = hierarchy_by_id[str(binding["hierarchy_occurrence_ref"])]
    parent = terminal_by_id[str(binding["parent_terminal_occurrence_ref"])]
    child = terminal_by_id[str(binding["child_terminal_occurrence_ref"])]
    if parent.get("role") != "sheet_entry" or child.get("role") != "port":
        raise ValueError("hierarchy binding must connect a sheet_entry to a port")
    if parent.get("page_occurrence_ref") != hierarchy.get("parent_page_occurrence_ref"):
        raise ValueError("hierarchy binding parent terminal has wrong page owner")
    child_pages = page_refs_by_unit.get(
        str(hierarchy.get("child_unit_occurrence_ref", "")), set()
    )
    if str(child.get("page_occurrence_ref", "")) not in child_pages:
        raise ValueError("hierarchy binding child terminal has wrong unit owner")
    resolved_nets = {
        str(value)
        for value in (
            parent.get("design_net_ref"),
            child.get("design_net_ref"),
            binding.get("design_net_ref"),
        )
        if value
    }
    if len(resolved_nets) > 1:
        raise ValueError("hierarchy binding resolves to different design nets")
    return str(parent["id"])


def _validate_boundary_completeness(
    terminal_ref: str,
    terminal: dict[str, object],
    hierarchy_parent_pages: set[str],
    bound_parent_refs: set[str],
) -> None:
    if (
        terminal.get("role") != "sheet_entry"
        or str(terminal.get("page_occurrence_ref", "")) not in hierarchy_parent_pages
    ):
        return
    raw_diagnostics = terminal.get("resolution_diagnostics", [])
    diagnostics = (
        {str(value) for value in raw_diagnostics}
        if isinstance(raw_diagnostics, list)
        else set()
    )
    if (
        not terminal.get("design_net_ref")
        and "design_net_unresolved" not in diagnostics
    ):
        raise ValueError(
            "hierarchy sheet-entry terminal needs a design net or diagnostic"
        )
    if (
        terminal_ref not in bound_parent_refs
        and "hierarchy_terminal_binding_unresolved" not in diagnostics
    ):
        raise ValueError("hierarchy sheet-entry terminal needs a binding or diagnostic")


def _validate_bindings(payload: dict[str, object]) -> None:
    hierarchy_by_id = {
        str(row["id"]): row
        for row in _payload_collection(payload, "hierarchy_occurrences")
    }
    terminal_by_id = {
        str(row["id"]): row
        for row in _payload_collection(payload, "terminal_occurrences")
    }
    page_refs_by_unit = _page_refs_by_unit(payload)
    bound_parent_refs = {
        _validate_binding_row(
            binding, hierarchy_by_id, terminal_by_id, page_refs_by_unit
        )
        for binding in _payload_collection(payload, "hierarchy_terminal_bindings")
    }
    hierarchy_parent_pages = {
        str(row.get("parent_page_occurrence_ref", ""))
        for row in hierarchy_by_id.values()
    }
    for terminal_ref, terminal in terminal_by_id.items():
        _validate_boundary_completeness(
            terminal_ref, terminal, hierarchy_parent_pages, bound_parent_refs
        )


def _graphical_target_page(target_type: str, target: dict[str, object]) -> object:
    if target_type == "sch.hierarchy_occurrence":
        return target.get("parent_page_occurrence_ref")
    if target_type == "sch.page_occurrence":
        return target.get("id")
    return target.get("page_occurrence_ref")


def _validate_graphical_links(
    payload: dict[str, object], row_by_id: dict[str, dict[str, object]]
) -> None:
    selectors: dict[tuple[str, str, str], tuple[str, str]] = {}
    for link in _payload_collection(payload, "graphical_artifact_links"):
        selector = (
            str(link.get("page_occurrence_ref", "")),
            str(link.get("artifact_key", "")),
            str(link.get("element_id", "")),
        )
        target = (str(link.get("target_type", "")), str(link.get("target_ref", "")))
        if not all(selector):
            raise ValueError("graphical artifact selector fields must be non-empty")
        if not all(target):
            raise ValueError("graphical artifact target fields must be non-empty")
        previous = selectors.setdefault(selector, target)
        if previous != target:
            raise ValueError("graphical artifact selector resolves to multiple targets")
        target_row = row_by_id.get(target[1])
        if target_row is None:
            raise ValueError("graphical artifact target_ref does not resolve")
        if target_row.get("type") != target[0]:
            raise ValueError("graphical artifact target type does not match target row")
        if str(_graphical_target_page(target[0], target_row) or "") != selector[0]:
            raise ValueError("graphical artifact target has wrong page owner")


def validate_compiled_schematic_graph(payload: dict[str, object]) -> None:
    """Strictly validate embedded identity, ownership, and references."""

    row_by_id = _validate_graph_rows(payload)
    _validate_definition_ownership(payload)
    _validate_occurrence_ownership(payload)
    _validate_hierarchy(payload)
    _validate_terminal_rows(payload)
    _validate_bindings(payload)
    _validate_graphical_links(payload, row_by_id)


__all__ = [
    "ALTIUM_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE",
    "ALTIUM_COMPILED_SCHEMATIC_GRAPH_SCHEMA",
    "ALTIUM_COMPILED_SCHEMATIC_GRAPH_TYPE",
    "AltiumCompiledSchematicGraph",
    "AltiumPhysicalPageMetadata",
    "COLLECTION_NAMES",
    "validate_compiled_schematic_graph",
]
