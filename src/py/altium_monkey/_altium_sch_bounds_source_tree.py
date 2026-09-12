"""Bounded all-level traversal over normalized schematic source ownership."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from .altium_record_sch__component import AltiumSchComponent
from .altium_record_sch__implementation import (
    AltiumSchImplementation,
    AltiumSchMapDefinerList,
)
from .altium_sch_paint_order import (
    _bound_or_last_owner_field,
    _component_bound_children,
    order_harness_layout_children_by_source,
)


@dataclass(frozen=True)
class _BoundsSourceTree:
    records: tuple[object, ...]
    parents: tuple[int | None, ...]
    children: tuple[tuple[int, ...], ...]

    def iter_descendant_indices(
        self, root: int, predicate: Callable[[int], bool] | None = None
    ) -> Iterator[int]:
        if not 0 <= root < len(self.records):
            raise ValueError("bounds traversal root is outside the source tree")
        stack = list(reversed(self.children[root]))
        while stack:
            index = stack.pop()
            if predicate is None or predicate(index):
                yield index
            # All-level iteration never prunes a filtered-out owner's subtree.
            stack.extend(reversed(self.children[index]))


def _validate_bounds_parent_forest(parents: Sequence[int | None]) -> None:
    states = bytearray(len(parents))
    for start in range(len(parents)):
        if states[start]:
            continue
        trail: list[int] = []
        cursor: int | None = start
        while cursor is not None and states[cursor] == 0:
            states[cursor] = 1
            trail.append(cursor)
            cursor = parents[cursor]
        if cursor is not None and states[cursor] == 1:
            raise ValueError("cycle in bounds source ownership")
        for index in trail:
            states[index] = 2


def _bounds_ordered_source_children(
    owner: object, children: Sequence[object]
) -> list[object]:
    if isinstance(owner, AltiumSchComponent):
        return _component_bound_children(owner, children)
    if isinstance(owner, AltiumSchImplementation):
        # The implementation's Map is a field after its ordinary object list.
        field = _bound_or_last_owner_field(owner.map, children, AltiumSchMapDefinerList)
        return [
            child
            for child in children
            if not isinstance(child, AltiumSchMapDefinerList)
        ] + ([] if field is None else [field])
    return order_harness_layout_children_by_source(owner, children)


def _build_bounds_source_tree(
    records: Sequence[object],
    parent_by_source_id: Mapping[int, object | None],
    *,
    max_sources: int = 1_000_000,
) -> _BoundsSourceTree:
    """Index one request's complete, already-normalized source-parent mapping."""
    if max_sources < 0:
        raise ValueError("bounds source limit cannot be negative")
    if len(records) > max_sources:
        raise ValueError("bounds source limit exceeded")
    sources = tuple(records)
    indexes = {id(record): index for index, record in enumerate(sources)}
    if len(indexes) != len(sources):
        raise ValueError("duplicate source identity in bounds tree")
    if indexes.keys() != parent_by_source_id.keys():
        raise ValueError("bounds source-parent mapping must be complete and exact")
    parents, children = _bounds_source_adjacency(sources, indexes, parent_by_source_id)
    _validate_bounds_parent_forest(parents)
    ordered_children = tuple(
        tuple(
            indexes[id(child)]
            for child in _bounds_ordered_source_children(
                owner, [sources[index] for index in child_indexes]
            )
        )
        for owner, child_indexes in zip(sources, children, strict=True)
    )
    return _BoundsSourceTree(sources, tuple(parents), ordered_children)


def _bounds_source_adjacency(
    sources: tuple[object, ...],
    indexes: Mapping[int, int],
    parent_by_source_id: Mapping[int, object | None],
) -> tuple[list[int | None], list[list[int]]]:
    parents: list[int | None] = []
    children: list[list[int]] = [[] for _ in sources]
    for index, record in enumerate(sources):
        parent = parent_by_source_id[id(record)]
        if parent is not None and id(parent) not in indexes:
            raise ValueError("bounds source parent is outside the source tree")
        parent_index = indexes[id(parent)] if parent is not None else None
        parents.append(parent_index)
        if parent_index is not None:
            children[parent_index].append(index)
    return parents, children


@dataclass(frozen=True, slots=True)
class _BoundsDocumentOwnerState:
    """Captured data-object kind test and engine ISchDocument-owner result."""

    source: object
    is_document_or_library: bool
    exposes_document_owner: bool


@dataclass(frozen=True, slots=True)
class _BoundsDocumentOwnerIndex:
    tree: _BoundsSourceTree
    document_sources: tuple[int | None, ...]
    parent_visits: int


def _bounds_owner_parent_snapshot(
    parents: Sequence[int | None], count: int
) -> tuple[int | None, ...]:
    if len(parents) != count:
        raise ValueError("bounds owner-parent vector must cover the exact source tree")
    for parent in parents:
        if parent is not None and (type(parent) is not int or not 0 <= parent < count):
            raise ValueError("bounds owner parent is outside the source tree")
    return tuple(parents)


def _bounds_owner_roots(
    parents: tuple[int | None, ...], starts: Sequence[int]
) -> tuple[list[int | None], int]:
    roots: list[int | None] = [None] * len(parents)
    states = bytearray(len(parents))
    visits = 0
    for start in starts:
        if states[start] == 2:
            continue
        trail: list[int] = []
        cursor = start
        while states[cursor] == 0:
            states[cursor] = 1
            visits += 1
            trail.append(cursor)
            parent = parents[cursor]
            if parent is None:
                roots[cursor] = cursor
                break
            cursor = parent
        if roots[cursor] is None:
            raise ValueError("cycle in effective bounds document ownership")
        for index in trail:
            roots[index] = roots[cursor]
            states[index] = 2
    return roots, visits


def _bounds_document_owner_states(
    tree: _BoundsSourceTree, documents: Mapping[int, _BoundsDocumentOwnerState]
) -> dict[int, _BoundsDocumentOwnerState]:
    if len(documents) > len(tree.records):
        raise ValueError("bounds document-state count exceeds the source tree")
    result: dict[int, _BoundsDocumentOwnerState] = {}
    for index, state in documents.items():
        if type(index) is not int or not 0 <= index < len(tree.records):
            raise ValueError("bounds document state is outside the source tree")
        if (
            not isinstance(state, _BoundsDocumentOwnerState)
            or state.source is not tree.records[index]
        ):
            raise ValueError("bounds document state has a different source")
        if (
            type(state.is_document_or_library) is not bool
            or type(state.exposes_document_owner) is not bool
        ):
            raise ValueError("bounds document state flags must be booleans")
        result[index] = state
    return result


def _build_bounds_document_owner_index(
    tree: _BoundsSourceTree,
    original_parents: Sequence[int | None],
    documents: Mapping[int, _BoundsDocumentOwnerState],
    *,
    max_sources: int = 1_000_000,
) -> _BoundsDocumentOwnerIndex:
    """Resolve captured current/original owner chains without changing sources.

    Original parents are explicit source-phase facts, never inferred from saved
    OwnerIndex or final-tree attachment. Missing document states mean neither a
    managed document/library kind nor an engine document owner. Current roots
    with a document/library kind suppress fallback even if their owner is null.
    """
    if type(max_sources) is not int or max_sources < 0:
        raise ValueError("bounds document source limit must be a nonnegative integer")
    count = len(tree.records)
    if count > max_sources:
        raise ValueError("bounds document source limit exceeded")
    current = _bounds_owner_parent_snapshot(tree.parents, count)
    original = _bounds_owner_parent_snapshot(original_parents, count)
    states = _bounds_document_owner_states(tree, documents)
    effective, visits = _bounds_effective_owner_roots(current, original, states)
    return _BoundsDocumentOwnerIndex(tree, effective, visits)


def _bounds_effective_owner_roots(
    current: tuple[int | None, ...],
    original: tuple[int | None, ...],
    states: dict[int, _BoundsDocumentOwnerState],
) -> tuple[tuple[int | None, ...], int]:
    roots, visits = _bounds_owner_roots(current, range(len(current)))
    fallback = [
        index
        for index, root in enumerate(roots)
        if root not in states or not states[root].is_document_or_library
    ]
    mixed = tuple(
        parent if parent is not None else original[index]
        for index, parent in enumerate(current)
    )
    original_roots, original_visits = _bounds_owner_roots(mixed, fallback)
    for index in fallback:
        roots[index] = original_roots[index]
    effective = tuple(
        root if root in states and states[root].exposes_document_owner else None
        for root in roots
    )
    return effective, visits + original_visits
