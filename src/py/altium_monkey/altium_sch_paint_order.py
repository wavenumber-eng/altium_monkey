"""Reconstruct schematic geometry paint order from source record order."""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Sequence
from enum import Enum

from .altium_record_sch__component import AltiumSchComponent
from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__ellipse import AltiumSchEllipse
from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
from .altium_record_sch__parameter import AltiumSchParameter
from .altium_record_sch__polygon import AltiumSchPolygon
from .altium_record_sch__rectangle import AltiumSchRectangle
from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol
from .altium_sch_geometry_oracle import SchGeometryRecord


_GENERATED_RECORD_ID = re.compile(r"^(IEEE|PIE|TPL)(\d{5})$")
_GENERATED_TEMPLATE_CHILD_ID = re.compile(r"^TPL\d{5}C(\d{5})$")
_SOURCE_KIND_OVERRIDES = {
    "filename": "sheetfilename",
    "harnesstype": "harnessconnectortype",
    "ieeesymbol": "ieee_symbol",
    "piechart": "pie",
    "powerport": "power",
    "roundedrectangle": "roundrectangle",
}
_GENERATED_SOURCE_PREFIXES = {
    "ieee_symbol": "IEEE",
    "pie": "PIE",
    "template": "TPL",
}

_GeneratedSourceKey = tuple[str, int]


class _Transparency(Enum):
    SET = "set"
    NOT_SET = "not_set"
    NOT_POSSIBLE = "not_possible"


def _transparency(source_object: object) -> _Transparency:
    if isinstance(
        source_object,
        (AltiumSchEllipse, AltiumSchRectangle, AltiumSchPolygon),
    ):
        return (
            _Transparency.SET
            if bool(getattr(source_object, "transparent", False))
            else _Transparency.NOT_SET
        )
    return _Transparency.NOT_POSSIBLE


def _sort_transparent_objects(source_objects: Sequence[object]) -> list[object]:
    ordered = list(source_objects)
    position = len(ordered) - 1
    while position > 0:
        if _transparency(ordered[position]) is _Transparency.SET:
            target = position
            while (
                target > 0
                and _transparency(ordered[target - 1]) is _Transparency.NOT_POSSIBLE
            ):
                target -= 1
            scan = position
            while (
                scan < len(ordered)
                and _transparency(ordered[scan]) is _Transparency.SET
            ):
                ordered.insert(target, ordered.pop(scan))
                scan += 1
            position = target
        position -= 1
    return ordered


def order_component_children_by_source(
    owner: AltiumSchComponent,
    children: Sequence[object],
) -> list[object]:
    """Return filtered component children in the managed painter's draw order."""
    fields = _component_field_objects(owner, children)
    field_ids = {id(field) for field in fields}
    list_backed = [child for child in children if id(child) not in field_ids]
    return _sort_transparent_objects([*list_backed, *fields])


def _component_field_objects(
    owner: AltiumSchComponent, children: Sequence[object]
) -> list[object]:
    designator = next(
        (child for child in children if isinstance(child, AltiumSchDesignator)),
        None,
    )
    comment = next(
        (
            child
            for child in children
            if isinstance(child, AltiumSchParameter)
            and str(getattr(child, "name", "") or "").casefold() == "comment"
        ),
        None,
    )
    return [field for field in (designator, comment) if field is not None]


def _owner_field_objects(owner: object, children: Sequence[object]) -> list[object]:
    if isinstance(owner, AltiumSchComponent):
        return _component_field_objects(owner, children)
    if isinstance(owner, AltiumSchSheetSymbol):
        return [
            field
            for field in (owner.sheet_name, owner.file_name)
            if field is not None and field in children
        ]
    if isinstance(owner, AltiumSchHarnessConnector):
        type_label = getattr(owner, "type_label", None)
        return [type_label] if type_label is not None and type_label in children else []
    return []


def _root_participates_in_order(
    root: object,
    included_source_ids: set[int],
    *,
    sort_root_transparency: bool,
    eligible_source_ids: set[int] | None,
) -> bool:
    root_id = id(root)
    if root_id in included_source_ids:
        return True
    return (
        sort_root_transparency
        and eligible_source_ids is not None
        and root_id in eligible_source_ids
    )


def _semantic_source_objects(
    source_objects: Sequence[object],
    included_source_ids: set[int],
    *,
    sort_root_transparency: bool,
    eligible_source_ids: set[int] | None,
) -> list[object]:
    source_ids = {id(source_object) for source_object in source_objects}
    children_by_owner: dict[int, list[object]] = {}
    roots: list[object] = []
    for source_object in source_objects:
        parent = getattr(source_object, "parent", None)
        if parent is None or id(parent) not in source_ids:
            roots.append(source_object)
            continue
        children_by_owner.setdefault(id(parent), []).append(source_object)

    ordered: list[object] = []

    def append_tree(source_object: object) -> None:
        if id(source_object) in included_source_ids:
            ordered.append(source_object)
        source_children = children_by_owner.get(id(source_object), [])
        if isinstance(source_object, AltiumSchComponent):
            eligible_child_ids = {
                id(child) for _, child in source_object._ordered_geometry_children()
            }
            children = [
                child
                for child in source_children
                if id(child) in included_source_ids or id(child) in eligible_child_ids
            ]
        else:
            children = [
                child for child in source_children if id(child) in included_source_ids
            ]
        fields = _owner_field_objects(source_object, children)
        field_ids = {id(field) for field in fields}
        effective_children = [
            child for child in children if id(child) not in field_ids
        ] + fields
        if isinstance(source_object, AltiumSchComponent):
            effective_children = _sort_transparent_objects(effective_children)
        for child in effective_children:
            append_tree(child)

    effective_roots = [
        root
        for root in roots
        if _root_participates_in_order(
            root,
            included_source_ids,
            sort_root_transparency=sort_root_transparency,
            eligible_source_ids=eligible_source_ids,
        )
    ]
    if sort_root_transparency:
        effective_roots = _sort_transparent_objects(effective_roots)
    for root in effective_roots:
        append_tree(root)
    return ordered


def _generated_source_key(unique_id: str | None) -> _GeneratedSourceKey | None:
    match = _GENERATED_RECORD_ID.fullmatch(str(unique_id or ""))
    if match is not None:
        return match.group(1), int(match.group(2))
    template_child_match = _GENERATED_TEMPLATE_CHILD_ID.fullmatch(str(unique_id or ""))
    if template_child_match is not None:
        return "TPLC", int(template_child_match.group(1))
    return None


def _source_geometry_kind(source_object: object) -> str:
    class_name = type(source_object).__name__
    short_name = class_name.removeprefix("AltiumSch").replace("_", "").casefold()
    return _SOURCE_KIND_OVERRIDES.get(short_name, short_name)


def _normalized_record_index(source_object: object, fallback: int) -> int:
    raw_index = getattr(source_object, "_record_index", None)
    if raw_index is None:
        return fallback
    try:
        return int(raw_index)
    except (TypeError, ValueError):
        return fallback


def _source_generated_key(source_object: object) -> _GeneratedSourceKey | None:
    parent = getattr(source_object, "parent", None)
    if parent is not None and _source_geometry_kind(parent) == "template":
        return "TPLC", _normalized_record_index(source_object, 0)
    prefix = _GENERATED_SOURCE_PREFIXES.get(_source_geometry_kind(source_object))
    if prefix is None:
        return None
    # The geometry emitters use zero when an authored object has not yet been
    # assigned a persisted record index.
    return prefix, _normalized_record_index(source_object, 0)


def _generated_record_keys(
    records: Sequence[SchGeometryRecord],
) -> set[_GeneratedSourceKey]:
    return {
        key
        for record in records
        if (key := _generated_source_key(record.unique_id)) is not None
    }


def _identified_source_ids(
    source_objects: Sequence[object],
    record_ids: set[str],
    generated_keys: set[_GeneratedSourceKey],
) -> set[int]:
    matched: set[int] = set()
    for source_object in source_objects:
        unique_id = str(getattr(source_object, "unique_id", "") or "")
        if (
            unique_id in record_ids
            or _source_generated_key(source_object) in generated_keys
        ):
            matched.add(id(source_object))
    return matched


def _anonymous_source_ids(
    source_objects: Sequence[object],
    excluded_ids: set[int],
    anonymous_counts: Counter[str],
    eligible_source_ids: set[int] | None,
) -> set[int]:
    matched: set[int] = set()
    for source_object in source_objects:
        if id(source_object) in excluded_ids:
            continue
        if (
            eligible_source_ids is not None
            and id(source_object) not in eligible_source_ids
        ):
            continue
        unique_id = str(getattr(source_object, "unique_id", "") or "")
        kind = _source_geometry_kind(source_object)
        if unique_id or anonymous_counts[kind] <= 0:
            continue
        matched.add(id(source_object))
        anonymous_counts[kind] -= 1
    return matched


def _included_source_ids(
    source_objects: Sequence[object],
    records: Sequence[SchGeometryRecord],
    eligible_source_ids: set[int] | None,
) -> tuple[set[int], set[_GeneratedSourceKey]]:
    record_ids = {record.unique_id for record in records if record.unique_id}
    generated_keys = _generated_record_keys(records)
    anonymous_counts = Counter(
        str(record.kind or "") for record in records if not record.unique_id
    )
    included = _identified_source_ids(source_objects, record_ids, generated_keys)
    included.update(
        _anonymous_source_ids(
            source_objects,
            included,
            anonymous_counts,
            eligible_source_ids,
        )
    )
    return included, generated_keys


def _tagged_source_object(
    source_objects: Sequence[object], record: SchGeometryRecord
) -> object | None:
    source_index = record.source_object_index
    if source_index is None or not 0 <= source_index < len(source_objects):
        return None
    return source_objects[source_index]


def _index_semantic_objects(
    semantic_objects: Sequence[object],
    generated_source_keys: set[_GeneratedSourceKey],
) -> tuple[
    dict[str, int],
    dict[_GeneratedSourceKey, int],
    dict[str, deque[int]],
]:
    positions: dict[str, int] = {}
    generated_positions: dict[_GeneratedSourceKey, int] = {}
    anonymous_by_kind: dict[str, deque[int]] = defaultdict(deque)
    for position, source_object in enumerate(semantic_objects):
        unique_id = str(getattr(source_object, "unique_id", "") or "")
        if unique_id:
            positions.setdefault(unique_id, position)
        generated_key = _source_generated_key(source_object)
        if generated_key is not None:
            generated_positions.setdefault(generated_key, position)
        if not unique_id and generated_key not in generated_source_keys:
            anonymous_by_kind[_source_geometry_kind(source_object)].append(position)
    return positions, generated_positions, anonymous_by_kind


def _match_anonymous_record_positions(
    records: Sequence[SchGeometryRecord],
    anonymous_by_kind: dict[str, deque[int]],
) -> dict[int, int]:
    anonymous_positions: dict[int, int] = {}
    for record_position, record in enumerate(records):
        candidates = anonymous_by_kind[str(record.kind or "")]
        if not record.unique_id and candidates:
            anonymous_positions[record_position] = candidates.popleft()
    return anonymous_positions


def _position_maps(
    source_objects: Sequence[object],
    semantic_objects: Sequence[object],
    generated_source_keys: set[_GeneratedSourceKey],
    records: Sequence[SchGeometryRecord],
) -> tuple[
    dict[str, int],
    dict[_GeneratedSourceKey, int],
    dict[int, int],
]:
    positions, generated_positions, anonymous_by_kind = _index_semantic_objects(
        semantic_objects,
        generated_source_keys,
    )
    semantic_positions = {
        id(source_object): position
        for position, source_object in enumerate(semantic_objects)
    }
    tagged_positions = {
        record_position: semantic_positions[id(source_object)]
        for record_position, record in enumerate(records)
        if (source_object := _tagged_source_object(source_objects, record)) is not None
        and id(source_object) in semantic_positions
    }
    untagged_records = [
        record
        for record in records
        if _tagged_source_object(source_objects, record) is None
    ]
    untagged_positions = [
        record_position
        for record_position, record in enumerate(records)
        if _tagged_source_object(source_objects, record) is None
    ]
    anonymous_positions = _match_anonymous_record_positions(
        untagged_records,
        anonymous_by_kind,
    )
    tagged_positions.update(
        {
            untagged_positions[record_position]: source_position
            for record_position, source_position in anonymous_positions.items()
        }
    )
    return (
        positions,
        generated_positions,
        tagged_positions,
    )


def _source_positions(
    source_objects: Sequence[object],
    records: Sequence[SchGeometryRecord],
    *,
    sort_root_transparency: bool,
    eligible_source_ids: set[int] | None,
) -> tuple[
    dict[str, int],
    dict[_GeneratedSourceKey, int],
    dict[int, int],
]:
    tagged_source_ids = {
        id(source_object)
        for record in records
        if (source_object := _tagged_source_object(source_objects, record)) is not None
    }
    untagged_records = [
        record
        for record in records
        if _tagged_source_object(source_objects, record) is None
    ]
    included_ids, generated_source_keys = _included_source_ids(
        source_objects,
        untagged_records,
        eligible_source_ids,
    )
    included_ids.update(tagged_source_ids)
    generated_source_keys.update(_generated_record_keys(records))
    semantic_objects = _semantic_source_objects(
        source_objects,
        included_ids,
        sort_root_transparency=sort_root_transparency,
        eligible_source_ids=eligible_source_ids,
    )
    return _position_maps(
        source_objects,
        semantic_objects,
        generated_source_keys,
        records,
    )


def order_geometry_records_by_source(
    records: Sequence[SchGeometryRecord],
    source_objects: Iterable[object],
    *,
    sort_root_transparency: bool = False,
    eligible_source_objects: Iterable[object] | None = None,
) -> list[SchGeometryRecord]:
    """Return records in the source container's observable drawing order."""
    source_sequence = list(source_objects)
    eligible_source_ids = (
        None
        if eligible_source_objects is None
        else {id(source_object) for source_object in eligible_source_objects}
    )
    positions, generated_positions, record_positions = _source_positions(
        source_sequence,
        records,
        sort_root_transparency=sort_root_transparency,
        eligible_source_ids=eligible_source_ids,
    )
    unknown_base = len(source_sequence) + 1

    def paint_position(
        indexed_record: tuple[int, SchGeometryRecord],
    ) -> tuple[int, int]:
        original_position, record = indexed_record
        source_position = record_positions.get(original_position)
        if source_position is None:
            source_position = positions.get(record.unique_id)
        if source_position is None:
            generated_key = _generated_source_key(record.unique_id)
            if generated_key is not None:
                source_position = generated_positions.get(generated_key)
        if source_position is None and record.kind == "sheet":
            source_position = -1
        if source_position is None:
            source_position = unknown_base + original_position
        return source_position, original_position

    indexed_records = list(enumerate(records))
    indexed_records.sort(key=paint_position)
    return [record for _, record in indexed_records]
