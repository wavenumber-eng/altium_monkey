"""Bounded accessibility snapshots for a fixed schematic ownership phase."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ._altium_sch_bounds_source_tree import _BoundsSourceTree
from ._altium_sch_bounds_traversal import _bounds_is_spatial
from ._altium_sch_component_bounds_state import (
    _bounds_data_is_accessible,
    _bounds_is_accessible,
    _bounds_schematic_block_flag,
)
from .altium_record_sch__component import AltiumSchComponent
from .altium_record_types import SchPrimitive


def _bounds_normalization_call_ranks(
    tree: _BoundsSourceTree, calls: Sequence[int], max_calls: int
) -> tuple[tuple[int, ...], list[int]]:
    if max_calls < 0:
        raise ValueError("bounds normalization call limit cannot be negative")
    if len(calls) > max_calls:
        raise ValueError("bounds normalization call limit exceeded")
    snapshot = tuple(calls)
    ranks = [-1] * len(tree.records)
    for ordinal, index in enumerate(snapshot):
        if not 0 <= index < len(tree.records):
            raise ValueError("bounds normalizing component is outside the source tree")
        if not isinstance(tree.records[index], AltiumSchComponent):
            raise ValueError("bounds normalization requires a component source")
        ranks[index] = ordinal
    return snapshot, ranks


def _bounds_reachable_normalizers(
    tree: _BoundsSourceTree, calls: tuple[int, ...], ranks: Sequence[int]
) -> tuple[int | None, ...]:
    has_iterated_parent = bytearray(len(tree.records))
    for children in tree.children:
        for child in children:
            has_iterated_parent[child] = 1
    stack = [
        (index, -1)
        for index in range(len(tree.records) - 1, -1, -1)
        if not has_iterated_parent[index]
    ]
    result: list[int | None] = [None] * len(tree.records)
    while stack:
        index, active_ordinal = stack.pop()
        if active_ordinal >= 0:
            result[index] = calls[active_ordinal]
        # A component's own call applies only to its reachable descendants.
        child_ordinal = max(active_ordinal, ranks[index])
        stack.extend((child, child_ordinal) for child in reversed(tree.children[index]))
    return tuple(result)


@dataclass(frozen=True)
class _BoundsAccessibilityIndex:
    tree: _BoundsSourceTree
    normalizing_components: tuple[int | None, ...]
    is_accessible: tuple[bool | None, ...]


@dataclass(frozen=True, slots=True)
class _BoundsImportedAccessibility:
    source: SchPrimitive
    data_is_accessible: bool
    saved_is_not_accessible: bool
    assignment_revision: int


def _imported_bounds_is_accessible(
    record: SchPrimitive, entry: _BoundsImportedAccessibility
) -> bool:
    """Read one proven capture, respecting later explicit flag assignments."""
    if entry.source is not record:
        raise ValueError("bounds accessibility capture has a different source")
    if _bounds_schematic_block_flag(record):
        return False
    if record._accessibility_assignment_revision != entry.assignment_revision:
        return not record.is_not_accessible
    return entry.data_is_accessible


def _indexed_bounds_import_captures(
    tree: _BoundsSourceTree,
    captured: Mapping[SchPrimitive, _BoundsImportedAccessibility],
) -> dict[int, _BoundsImportedAccessibility]:
    if len(captured) > len(tree.records):
        raise ValueError("bounds accessibility capture count exceeds the source tree")
    source_ids = {id(record) for record in tree.records}
    indexed: dict[int, _BoundsImportedAccessibility] = {}
    for record, entry in captured.items():
        if not isinstance(entry, _BoundsImportedAccessibility):
            raise ValueError("invalid bounds accessibility capture")
        if entry.source is not record:
            raise ValueError("bounds accessibility capture has a different source")
        if id(record) not in source_ids:
            raise ValueError("bounds accessibility capture is outside the source tree")
        if not _bounds_is_spatial(record):
            raise ValueError("bounds accessibility capture requires a spatial source")
        indexed[id(record)] = entry
    return indexed


def _build_captured_bounds_accessibility_index(
    tree: _BoundsSourceTree,
    captured: Mapping[SchPrimitive, _BoundsImportedAccessibility],
) -> _BoundsAccessibilityIndex:
    """Snapshot proven import data and later explicit writes for this exact tree.

    This consumes already-maintained provenance; it does not replay component
    setters, repair detach/transfer state, or infer normalization from owners.
    """
    indexed = _indexed_bounds_import_captures(tree, captured)
    accessible: list[bool | None] = []
    for record in tree.records:
        if not isinstance(record, SchPrimitive) or not _bounds_is_spatial(record):
            accessible.append(None)
        elif (entry := indexed.get(id(record))) is not None:
            accessible.append(_imported_bounds_is_accessible(record, entry))
        else:
            accessible.append(_bounds_is_accessible(record))
    return _BoundsAccessibilityIndex(
        tree, (None,) * len(tree.records), tuple(accessible)
    )


def _capture_bounds_import_accessibility(
    tree: _BoundsSourceTree,
    normalization_calls: Sequence[int],
    *,
    max_calls: int = 1_000_000,
) -> dict[SchPrimitive, _BoundsImportedAccessibility]:
    """Capture only normalized spatial identities; retain no temporary tree.

    Object keys survive document staging through its deepcopy memo and support
    constant-time invalidation when their source is detached. This
    is import provenance, not a replay of later component setters or transfers.
    Keep data state separate so the live schematic-block gate can be applied.
    """
    calls, ranks = _bounds_normalization_call_ranks(
        tree, normalization_calls, max_calls
    )
    normalizers = _bounds_reachable_normalizers(tree, calls, ranks)
    result: dict[SchPrimitive, _BoundsImportedAccessibility] = {}
    for index, record in enumerate(tree.records):
        component_index = normalizers[index]
        if (
            component_index is None
            or not isinstance(record, SchPrimitive)
            or not _bounds_is_spatial(record)
        ):
            continue
        component = tree.records[component_index]
        assert isinstance(component, AltiumSchComponent)
        result[record] = _BoundsImportedAccessibility(
            record,
            _bounds_data_is_accessible(record, normalizing_component=component),
            record.is_not_accessible,
            record._accessibility_assignment_revision,
        )
    return result


def _build_bounds_accessibility_index(
    tree: _BoundsSourceTree,
    normalization_calls: Sequence[int] = (),
    *,
    max_calls: int = 1_000_000,
) -> _BoundsAccessibilityIndex:
    """Replay actual component-call order over fixed ownership and component state.

    Empty calls retain saved/BOC accessibility. Use the tree at the time of the
    calls, before later import phases can add or rebind children. Ownership
    transfers and library editor-tail state require a separate adapter.
    """
    calls, ranks = _bounds_normalization_call_ranks(
        tree, normalization_calls, max_calls
    )
    normalizers = _bounds_reachable_normalizers(tree, calls, ranks)
    accessible: list[bool | None] = []
    for index, record in enumerate(tree.records):
        component_index = normalizers[index]
        component = None if component_index is None else tree.records[component_index]
        if isinstance(record, SchPrimitive) and _bounds_is_spatial(record):
            assert component is None or isinstance(component, AltiumSchComponent)
            accessible.append(
                _bounds_is_accessible(record, normalizing_component=component)
            )
        else:
            accessible.append(None)
    return _BoundsAccessibilityIndex(tree, normalizers, tuple(accessible))
