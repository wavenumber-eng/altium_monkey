"""Private identity-bound source projections for schematic producers."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import TYPE_CHECKING, Literal

from .altium_record_types import SchPrimitive, SchRecordType
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_record_sch__junction import AltiumSchJunction
from .altium_record_sch__parameter import AltiumSchImageParameter, AltiumSchParameter
from .altium_serializer import FieldDef, _read_param_boolean

if TYPE_CHECKING:
    from .altium_record_sch__component import AltiumSchComponent
    from ._sch_source_admission import _SourceAdmission
    from .altium_schdoc import AltiumSchDoc

_COMPONENT_OBJECT_LIST_GRAPHICAL_TYPES = frozenset(
    {
        SchRecordType.COMPONENT,
        SchRecordType.PIN,
        SchRecordType.IEEE_SYMBOL,
        SchRecordType.LABEL,
        SchRecordType.BEZIER,
        SchRecordType.POLYLINE,
        SchRecordType.POLYGON,
        SchRecordType.ELLIPSE,
        SchRecordType.PIECHART,
        SchRecordType.ROUND_RECTANGLE,
        SchRecordType.ELLIPTICAL_ARC,
        SchRecordType.ARC,
        SchRecordType.LINE,
        SchRecordType.RECTANGLE,
        SchRecordType.SHEET_SYMBOL,
        SchRecordType.SHEET_ENTRY,
        SchRecordType.POWER_PORT,
        SchRecordType.PORT,
        SchRecordType.NO_ERC,
        SchRecordType.NET_LABEL,
        SchRecordType.BUS,
        SchRecordType.WIRE,
        SchRecordType.TEXT_FRAME,
        SchRecordType.JUNCTION,
        SchRecordType.IMAGE,
        SchRecordType.BUS_ENTRY,
        SchRecordType.TEMPLATE,
        SchRecordType.PARAMETER,
        SchRecordType.PARAMETER_SET,
        SchRecordType.NOTE,
        SchRecordType.COMPILE_MASK,
        SchRecordType.HARNESS_CONNECTOR,
        SchRecordType.HARNESS_ENTRY,
        SchRecordType.SIGNAL_HARNESS,
        SchRecordType.BLANKET,
        SchRecordType.HYPERLINK,
        SchRecordType.HARNESS_COMPONENT,
        SchRecordType.HARNESS_SPLICE,
        SchRecordType.HARNESS_LAYOUT_LABEL,
        SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT,
        SchRecordType.HARNESS_BUNDLE,
        SchRecordType.LINE_VIEW,
        SchRecordType.HARNESS_LAYOUT_COVERING,
        SchRecordType.REUSE_BLOCK_IMPLEMENTATION_INFO,
        SchRecordType.HARNESS_CAVITY,
        SchRecordType.HARNESS_CAVITY_COMPONENT,
    }
)
_COMPONENT_BOUND_PARAMETER_NAMES = frozenset(
    dotnet_ordinal_ignore_case_key(name)
    for name in (
        "DefaultNet",
        "HiddenNetName",
        "PinSelectionMemory",
        "Comment",
        "Net",
        "Description",
        "PinUniqueId",
    )
)


def _parameter_attachment_role(
    parameter: AltiumSchParameter, owner: object | None
) -> Literal["ordinary", "comment", "description", "length", "pin_state", "unattached"]:
    name = dotnet_ordinal_ignore_case_key(parameter.name)
    owner_type = getattr(owner, "record_type", None)
    if name == "COMMENT":
        return (
            "comment"
            if owner_type
            in {
                SchRecordType.COMPONENT,
                SchRecordType.HARNESS_COMPONENT,
                SchRecordType.HARNESS_CAVITY_COMPONENT,
                SchRecordType.HARNESS_BUNDLE,
                SchRecordType.HARNESS_LAYOUT_LABEL,
                SchRecordType.HARNESS_LAYOUT_COVERING,
            }
            else "unattached"
        )
    if name == "DESCRIPTION":
        return (
            "description"
            if owner_type == SchRecordType.HARNESS_BUNDLE
            else "unattached"
        )
    if name == "LENGTHPARAMETER" and owner_type == SchRecordType.HARNESS_BUNDLE:
        return "length"
    if name in {"DEFAULTNET", "HIDDENNETNAME", "PINSELECTIONMEMORY", "PINUNIQUEID"}:
        from .altium_record_sch__pin import AltiumSchPin

        return "pin_state" if isinstance(owner, AltiumSchPin) else "unattached"
    return "unattached" if name == "NET" else "ordinary"


def _source_descendant_exclusions(
    objects: Iterable[object],
    excluded_ids: Iterable[int],
    parents: Mapping[int, object | None] | None = None,
) -> frozenset[int]:
    states = dict.fromkeys(excluded_ids, True)
    for record in objects:
        _observe_source_ignore_ancestry(record, states, parents=parents)
    return frozenset(object_id for object_id, blocked in states.items() if blocked)


def _document_source_parents(document: AltiumSchDoc) -> dict[int, object | None]:
    parents: dict[int, object | None] = {}
    for record in document.all_objects:
        parent = record.parent
        reference = document._normalized_owner_refs.get(id(record))
        if parent is None and reference is not None:
            stream, index = reference
            records = (
                document._fileheader_objects
                if stream == "FileHeader"
                else document._additional_objects
            )
            if 0 <= index < len(records):
                parent = records[index]
        parents[id(record)] = parent
    return parents


def _parameter_source_exclusions(
    objects: Collection[object],
    parents: Mapping[int, object | None],
    ignored: Collection[int],
) -> frozenset[int]:
    excluded: set[int] = set()
    fields: dict[tuple[int, str], int] = {}
    image_field_ids: set[int] = set()
    for parameter in objects:
        if (
            not isinstance(parameter, AltiumSchParameter)
            or parameter.record_type != SchRecordType.PARAMETER
            or id(parameter) in ignored
        ):
            continue
        owner = parents.get(id(parameter))
        role = _parameter_attachment_role(parameter, owner)
        if role in {"unattached", "pin_state"}:
            excluded.add(id(parameter))
        elif role != "ordinary":
            key = (id(owner), role)
            previous = fields.get(key)
            if previous is not None:
                excluded.add(previous)
                image_field_ids.discard(previous)
            fields[key] = id(parameter)
            if _image_parameter_field_drops_subtree(parameter, owner, role):
                image_field_ids.add(id(parameter))
    blocked = _source_descendant_exclusions(
        objects, excluded | image_field_ids, parents
    )
    return blocked - frozenset(ignored) - image_field_ids


def _image_parameter_field_drops_subtree(
    parameter: AltiumSchParameter, owner: object | None, role: str
) -> bool:
    if not isinstance(parameter, AltiumSchImageParameter):
        return False
    owner_type = getattr(owner, "record_type", None)
    return (
        role == "comment"
        and owner_type
        in {
            SchRecordType.COMPONENT,
            SchRecordType.HARNESS_COMPONENT,
            SchRecordType.HARNESS_CAVITY_COMPONENT,
        }
    ) or (role == "length" and owner_type == SchRecordType.HARNESS_BUNDLE)


def _parameter_projected_parents(
    objects: Collection[object], parents: Mapping[int, object | None]
) -> dict[int, object | None]:
    projected = dict(parents)
    for record in objects:
        owner = parents.get(id(record))
        if (
            isinstance(record, AltiumSchParameter)
            and record.record_type == SchRecordType.PARAMETER
            and getattr(owner, "record_type", None) == SchRecordType.IMPL_PARAMS
        ):
            if _parameter_attachment_role(record, owner) == "ordinary":
                projected[id(record)] = parents.get(id(owner))
    return projected


def _component_object_list_owner(record: SchPrimitive) -> SchPrimitive | None:
    if record.record_type not in _COMPONENT_OBJECT_LIST_GRAPHICAL_TYPES:
        return None
    if isinstance(record, AltiumSchJunction) and not record.locked:
        return None
    owner = record.parent
    if isinstance(record, AltiumSchParameter):
        if (
            dotnet_ordinal_ignore_case_key(record.name)
            in _COMPONENT_BOUND_PARAMETER_NAMES
        ):
            return None
        # UpdateOwner rewrites one ParameterList owner, not a recursive chain.
        if owner is not None and owner.record_type == SchRecordType.IMPL_PARAMS:
            owner = owner.parent
    return owner


_IGNORE_ON_LOAD_FIELD = FieldDef.simple("IgnoreOnLoad")


def _record_import_ignores_source(record: object) -> bool:
    if not isinstance(record, SchPrimitive) or record.record_type in (
        SchRecordType.PIN,
        SchRecordType.SHEET,
        SchRecordType.HEADER,
    ):
        return False
    # Pin and document import bypass ImportDataObject; an opaque key on those
    # families must not acquire semantics merely because the raw model kept it.
    return _read_param_boolean(record._record, _IGNORE_ON_LOAD_FIELD)


def _observe_source_ignore_ancestry(
    record: object,
    states: dict[int, bool],
    cycle_error: str | None = None,
    parents: Mapping[int, object | None] | None = None,
) -> None:
    trail: list[int] = []
    current: object | None = record
    while current is not None and id(current) not in states:
        object_id = id(current)
        # Validated ownership is acyclic. A malformed detached cycle is blocked
        # without recursion, rather than looping indefinitely during preparation.
        states[object_id] = True
        trail.append(object_id)
        if _record_import_ignores_source(current):
            break
        current = _projected_source_parent(current, parents)
    if cycle_error and current is not None and id(current) in trail:
        if not _record_import_ignores_source(current):
            raise ValueError(cycle_error)
    ignored = current is not None and states[id(current)]
    for object_id in trail:
        states[object_id] = ignored


def _projected_source_parent(
    record: object, parents: Mapping[int, object | None] | None
) -> object | None:
    return (
        parents.get(id(record))
        if parents is not None
        else getattr(record, "parent", None)
    )


def _ignored_source_object_ids(
    objects: Iterable[object],
    *,
    cycle_error: str | None = None,
    parents: Mapping[int, object | None] | None = None,
) -> frozenset[int]:
    """Prepare ignore-only ancestry exclusions for normalized SchDoc sources."""
    states: dict[int, bool] = {}
    for record in objects:
        _observe_source_ignore_ancestry(record, states, cycle_error, parents)
    return frozenset(object_id for object_id, ignored in states.items() if ignored)


def _source_compile_mask_bounds(
    objects: Iterable[object], *, precise: bool
) -> list[tuple[int, int, int, int]]:
    from .altium_netlist_wire_connectivity import ALTIUM_COORD_SCALE
    from .altium_record_sch__compile_mask import AltiumSchCompileMask

    bounds: list[tuple[int, int, int, int]] = []
    scale = ALTIUM_COORD_SCALE if precise else 1
    for record in objects:
        if not isinstance(record, AltiumSchCompileMask) or record.is_collapsed:
            continue
        x1 = record.location.x * scale + (record.location.x_frac if precise else 0)
        y1 = record.location.y * scale + (record.location.y_frac if precise else 0)
        x2 = record.corner.x * scale + (record.corner.x_frac if precise else 0)
        y2 = record.corner.y * scale + (record.corner.y_frac if precise else 0)
        bounds.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    return bounds


def _component_bound_field_role(child: object) -> str | None:
    from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
    from .altium_record_sch__designator import AltiumSchDesignator
    from .altium_record_sch__parameter import AltiumSchParameter

    if isinstance(child, AltiumSchDesignator):
        return "designator"
    if (
        isinstance(child, AltiumSchParameter)
        and dotnet_ordinal_ignore_case_key(child.name) == "COMMENT"
    ):
        return "comment"
    return None


def _component_bound_field_slots(children: Iterable[object]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for child in children:
        role = _component_bound_field_role(child)
        if role is not None and not _record_import_ignores_source(child):
            fields[role] = child
    return fields


def _hierarchy_field_role(owner: object, child: object) -> str | None:
    from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol
    from .altium_record_sch__sheet_name import AltiumSchSheetName
    from .altium_record_sch__file_name import AltiumSchFileName
    from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
    from .altium_record_sch__harness_type import AltiumSchHarnessType

    if isinstance(owner, AltiumSchSheetSymbol):
        if isinstance(child, AltiumSchSheetName):
            return "sheet_name"
        if isinstance(child, AltiumSchFileName):
            return "file_name"
    if isinstance(owner, AltiumSchHarnessConnector) and isinstance(
        child, AltiumSchHarnessType
    ):
        return "type_label"
    return None


def _hierarchy_bound_field_slots(
    owner: object,
    children: Iterable[object],
    *,
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> dict[str, SchPrimitive]:
    fields: dict[str, SchPrimitive] = {}
    bound: dict[str, SchPrimitive] = {}
    for child in children:
        role = _hierarchy_field_role(owner, child)
        if role is None or not isinstance(child, SchPrimitive):
            continue
        parent = (
            parent_by_source_id.get(id(child), child.parent)
            if parent_by_source_id is not None
            else child.parent
        )
        if parent is not owner or _record_import_ignores_source(child):
            continue
        fields[role] = child
        if child is getattr(owner, role, None):
            bound[role] = child
    # Public authoring may explicitly bind a live child. Import binds the last
    # raw candidate; if it was ignored, fall back to the last admitted one.
    fields.update(bound)
    return fields


def _hierarchy_bound_children(
    owner: object,
    children: Iterable[object],
    *,
    parent_by_source_id: Mapping[int, object | None] | None = None,
) -> list[object]:
    children = list(children)
    fields = _hierarchy_bound_field_slots(
        owner, children, parent_by_source_id=parent_by_source_id
    )
    return [
        child for child in children if _hierarchy_field_role(owner, child) is None
    ] + [
        fields[role]
        for role in ("sheet_name", "file_name", "type_label")
        if role in fields
    ]


def _hierarchy_render_children(
    owner: object,
    admission: _SourceAdmission,
) -> list[object]:
    children = admission.children_with_legacy(
        owner,
        [*getattr(owner, "children", ()), *getattr(owner, "entries", ())],
    )
    known = admission.parent_by_source_id
    if known is None:
        return children
    source_roles = {
        _hierarchy_field_role(owner, child) for child in children if id(child) in known
    } - {None}
    # Import may leave synthetic default fields outside the source store. They
    # cannot replace an admitted persisted field of the same managed role.
    return [
        child
        for child in children
        if id(child) in known or _hierarchy_field_role(owner, child) not in source_roles
    ]


def _logical_component_field_owners(
    components: Iterable[AltiumSchComponent],
) -> tuple[dict[int, int], dict[int, int]]:
    modes: dict[int, int] = {}
    fields: dict[int, int] = {}
    for component in components:
        if component.part_count <= 2:
            continue
        owner_id = id(component)
        modes[owner_id] = component.display_mode
        slots = _component_bound_field_slots(component.children or component.parameters)
        fields.update((id(field), owner_id) for field in slots.values())
    return modes, fields


def _logical_component_part_key(
    record: object, modes: Mapping[int, int], fields: Mapping[int, int]
) -> tuple[int, int] | None:
    if not isinstance(record, SchPrimitive):
        return None
    owner_id = fields.get(id(record))
    if owner_id is None:
        owner_id = id(_component_object_list_owner(record))
    part_id = record.owner_part_id or 0
    if owner_id not in modes or part_id in (-1, 0):
        return None
    if (record.owner_part_display_mode or 0) != modes[owner_id]:
        return None
    return owner_id, part_id


def _logical_component_suffix_ids(
    objects: Iterable[object], components: Iterable[AltiumSchComponent]
) -> frozenset[int]:
    """Prepare fresh SchDoc suffix gates from first-level graphical membership."""
    modes, fields = _logical_component_field_owners(components)
    if not modes:
        return frozenset()
    first_parts: dict[int, int] = {}
    result: set[int] = set()
    # Only the >1 predicate is needed. Keep one part per component, rather
    # than retaining every distinct part in a potentially large definition.
    for record in objects:
        key = _logical_component_part_key(record, modes, fields)
        if key is None:
            continue
        owner_id, part_id = key
        first_part = first_parts.setdefault(owner_id, part_id)
        if first_part != part_id:
            result.add(owner_id)
    return frozenset(result)
