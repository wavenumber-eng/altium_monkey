"""Linear physical-model selection over prepared schematic ownership."""

from __future__ import annotations

from dataclasses import dataclass

from ._altium_sch_bounds_source_tree import _BoundsSourceTree
from ._altium_record_sch__physical_model import _AltiumSchHarnessCavityComponent
from .altium_record_sch__component import AltiumSchHarnessComponent
from .altium_record_sch__parameter import AltiumSchImageParameter
from .altium_record_types import SchGraphicalObject, SchRecordType
from .altium_sch_geometry_oracle import SchGeometryBounds
from ._altium_record_sch__harness_layout import _unchecked_i32


_ENGINE_EMPTY_BOUNDS = SchGeometryBounds(
    left=2_147_483_647,
    bottom=2_147_483_647,
    right=-2_147_483_647,
    top=-2_147_483_647,
)


def _enclose_engine_bounds(
    aggregate: SchGeometryBounds, candidate: SchGeometryBounds | None
) -> SchGeometryBounds:
    if candidate is None or candidate == _ENGINE_EMPTY_BOUNDS or candidate == aggregate:
        return aggregate
    left = min(candidate.left, candidate.right, aggregate.left)
    bottom = min(candidate.bottom, candidate.top, aggregate.bottom)
    right = max(candidate.left, candidate.right, aggregate.right)
    top = max(candidate.bottom, candidate.top, aggregate.top)
    return SchGeometryBounds(
        left=min(left, right),
        bottom=min(bottom, top),
        right=max(left, right),
        top=max(bottom, top),
    )


def _finish_cavity_bounds(
    aggregate: SchGeometryBounds, location: tuple[int, int]
) -> SchGeometryBounds:
    if aggregate != _ENGINE_EMPTY_BOUNDS:
        return aggregate
    x, y = location
    # Unlike union, the engine fallback does not normalize after signed wrap.
    return SchGeometryBounds(
        left=_unchecked_i32(x - 500_000),
        bottom=_unchecked_i32(y - 500_000),
        right=_unchecked_i32(x + 500_000),
        top=_unchecked_i32(y + 500_000),
    )


_MODEL_TYPES = frozenset(
    {
        SchRecordType.IMAGE,
        SchRecordType.LINE_VIEW,
        SchRecordType.HARNESS_CAVITY_COMPONENT,
    }
)


@dataclass(frozen=True, slots=True)
class _BoundsPhysicalModelIndex:
    tree: _BoundsSourceTree
    main_parameters: tuple[int | None, ...]
    parameter_models: tuple[int | None, ...]
    cavity_components: tuple[int | None, ...]
    reference_count: int


def _first_selected_child(
    tree: _BoundsSourceTree, index: int, selected: list[int | None]
) -> int | None:
    return next(
        (
            selected[child]
            for child in tree.children[index]
            if selected[child] is not None
        ),
        None,
    )


def _first_model_in_subtrees(tree: _BoundsSourceTree) -> list[int | None]:
    selected: list[int | None] = [None] * len(tree.records)
    done = bytearray(len(tree.records))
    for start in range(len(tree.records)):
        if done[start]:
            continue
        stack = [(start, False)]
        while stack:
            index, closing = stack.pop()
            if done[index]:
                continue
            if not closing:
                stack.append((index, True))
                stack.extend((child, False) for child in reversed(tree.children[index]))
                continue
            record = tree.records[index]
            selected[index] = (
                index
                if isinstance(record, SchGraphicalObject)
                and record.record_type in _MODEL_TYPES
                else _first_selected_child(tree, index, selected)
            )
            done[index] = 1
    return selected


def _first_main_parameter(tree: _BoundsSourceTree, owner: int) -> int | None:
    for index in tree.children[owner]:
        candidate = tree.records[index]
        if isinstance(candidate, AltiumSchImageParameter) and candidate.is_main_model:
            return index
    return None


def _physical_links(
    tree: _BoundsSourceTree, index: int, selected: list[int | None]
) -> tuple[int | None, int | None, int | None]:
    record = tree.records[index]
    if isinstance(record, AltiumSchHarnessComponent):
        cavity = next(
            (
                child
                for child in tree.children[index]
                if isinstance(tree.records[child], _AltiumSchHarnessCavityComponent)
            ),
            None,
        )
        return _first_main_parameter(tree, index), None, cavity
    if isinstance(record, AltiumSchImageParameter):
        return None, _first_selected_child(tree, index, selected), None
    return None, None, None


def _build_bounds_physical_model_index(
    tree: _BoundsSourceTree,
    *,
    max_sources: int = 1_000_000,
    max_references: int = 1_000_000,
) -> _BoundsPhysicalModelIndex:
    if min(max_sources, max_references) < 0:
        raise ValueError("physical-model limits cannot be negative")
    if len(tree.records) > max_sources:
        raise ValueError("physical-model source limit exceeded")
    selected = _first_model_in_subtrees(tree)
    # Subtree selection includes the node itself; an image parameter's model
    # starts strictly below it. Main selection is first-level and ignores hidden.
    main_parameters: list[int | None] = []
    parameter_models: list[int | None] = []
    cavity_components: list[int | None] = []
    references = 0
    for index in range(len(tree.records)):
        main, model, cavity = _physical_links(tree, index, selected)
        references += sum(value is not None for value in (main, model, cavity))
        if references > max_references:
            raise ValueError("physical-model reference limit exceeded")
        main_parameters.append(main)
        parameter_models.append(model)
        cavity_components.append(cavity)
    return _BoundsPhysicalModelIndex(
        tree,
        tuple(main_parameters),
        tuple(parameter_models),
        tuple(cavity_components),
        references,
    )
