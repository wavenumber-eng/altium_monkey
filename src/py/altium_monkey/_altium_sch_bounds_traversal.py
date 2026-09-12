"""Request-local spatial/current-part and cavity filtering for bounds traversal."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from ._altium_sch_bounds_source_tree import _BoundsSourceTree
from ._altium_sch_component_bounds_state import (
    _component_primitive_passes_bounds_filter,
)
from .altium_record_sch__component import AltiumSchComponent, AltiumSchHarnessComponent
from .altium_record_sch__parameter import AltiumSchImageParameter, AltiumSchParameter
from .altium_record_sch__pin import AltiumSchPin
from .altium_record_sch__file_name import AltiumSchFileName
from .altium_record_sch__sheet_name import AltiumSchSheetName
from .altium_record_sch__harness_type import AltiumSchHarnessType
from .altium_record_types import SchPrimitive, SchRecordType


# File-record identities for the represented members of SpatialObjectsSet.
# ImageParameter and CrossSheetConnector share their base file-record identity.
_SPATIAL_RECORD_TYPES = frozenset(
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
        SchRecordType.SHEET,
        SchRecordType.SHEET_NAME,
        SchRecordType.FILE_NAME,
        SchRecordType.DESIGNATOR,
        SchRecordType.BUS_ENTRY,
        SchRecordType.PARAMETER,
        SchRecordType.PARAMETER_SET,
        SchRecordType.HARNESS_COMPONENT,
        SchRecordType.HARNESS_SPLICE,
        SchRecordType.HARNESS_LAYOUT_LABEL,
        SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT,
        SchRecordType.HARNESS_BUNDLE,
        SchRecordType.LINE_VIEW,
        SchRecordType.HARNESS_LAYOUT_COVERING,
        SchRecordType.HARNESS_CAVITY,
        SchRecordType.HARNESS_CAVITY_COMPONENT,
        SchRecordType.NOTE,
        SchRecordType.COMPILE_MASK,
        SchRecordType.HARNESS_CONNECTOR,
        SchRecordType.HARNESS_ENTRY,
        SchRecordType.HARNESS_TYPE,
        SchRecordType.SIGNAL_HARNESS,
        SchRecordType.BLANKET,
        SchRecordType.HYPERLINK,
    }
)

_BoundsDocumentKind = Literal[
    "schematic", "harness_layout", "harness_wiring", "library"
]


def _bounds_is_spatial(record: object) -> bool:
    return (
        isinstance(record, SchPrimitive) and record.record_type in _SPATIAL_RECORD_TYPES
    )


def _bounds_is_cavity_component(record: object) -> bool:
    return (
        isinstance(record, SchPrimitive)
        and record.record_type == SchRecordType.HARNESS_CAVITY_COMPONENT
    )


def _bounds_has_cavity_ancestor(tree: _BoundsSourceTree) -> tuple[bool, ...]:
    resolved: list[bool | None] = [None] * len(tree.records)
    for start in range(len(tree.records)):
        trail: list[int] = []
        cursor: int | None = start
        while cursor is not None and resolved[cursor] is None:
            trail.append(cursor)
            cursor = tree.parents[cursor]
        for index in reversed(trail):
            parent = tree.parents[index]
            resolved[index] = parent is not None and (
                bool(resolved[parent])
                or _bounds_is_cavity_component(tree.records[parent])
            )
    return tuple(bool(value) for value in resolved)


def _bounds_main_model_visible(tree: _BoundsSourceTree, owner: int) -> bool:
    for index in tree.children[owner]:
        candidate = tree.records[index]
        if isinstance(candidate, AltiumSchImageParameter) and candidate.is_main_model:
            # A hidden first main model does not fall through to a later one.
            return not candidate.is_hidden
    return False


def _bounds_harness_child_allowed(
    record: SchPrimitive, document_kind: _BoundsDocumentKind, main_model_visible: bool
) -> bool:
    if document_kind == "harness_wiring":
        return not isinstance(record, AltiumSchImageParameter)
    if document_kind == "harness_layout" and main_model_visible:
        return isinstance(record, AltiumSchParameter)
    return True


@dataclass(frozen=True)
class _BoundsTraversalIndex:
    tree: _BoundsSourceTree
    spatial_current_part: tuple[bool, ...]
    has_cavity_ancestor: tuple[bool, ...]
    ordinal_visible: tuple[bool | None, ...]

    def iter_visible_child_candidates(self, root: int) -> Iterator[tuple[int, bool]]:
        """Yield charged candidates in engine order, pruning rejected subtrees."""
        stack = [iter(self.tree.children[root])]
        while stack:
            index = next(stack[-1], None)
            if index is None:
                stack.pop()
                continue
            passes = self.spatial_current_part[index]
            if passes:
                visible = self.ordinal_visible[index]
                if visible is None:
                    raise NotImplementedError(
                        "library-dependent pin visibility requires prepared owner state"
                    )
                passes = visible
            yield index, passes
            if passes:
                stack.append(iter(self.tree.children[index]))

    def iter_spatial_descendant_indices(
        self, root: int, *, ignore_cavity_view: bool = False
    ) -> Iterator[int]:
        def passes(index: int) -> bool:
            return self.spatial_current_part[index] and (
                not ignore_cavity_view or not self.has_cavity_ancestor[index]
            )

        yield from self.tree.iter_descendant_indices(root, passes)


def _bounds_current_part_passes(
    tree: _BoundsSourceTree,
    index: int,
    document_kind: _BoundsDocumentKind,
    visible_main_models: frozenset[int],
    library_current_components: Mapping[int, AltiumSchComponent | None],
) -> bool:
    record = tree.records[index]
    parent = tree.parents[index]
    if not isinstance(record, SchPrimitive) or not _bounds_is_spatial(record):
        return False
    if parent is None:
        return False
    owner = tree.records[parent]
    if parent in library_current_components:
        component = library_current_components[parent]
        return component is not None and _component_primitive_passes_bounds_filter(
            record, component
        )
    if isinstance(owner, AltiumSchImageParameter):
        return False
    if isinstance(
        owner, AltiumSchHarnessComponent
    ) and not _bounds_harness_child_allowed(
        record, document_kind, parent in visible_main_models
    ):
        return False
    if isinstance(owner, AltiumSchComponent):
        return _component_primitive_passes_bounds_filter(record, owner)
    return True


def _bounds_pin_component_visibility(
    tree: _BoundsSourceTree,
    index: int,
    parent: int,
    libraries: Mapping[int, AltiumSchComponent | None],
    prepared: Mapping[int, bool],
) -> bool | None:
    if tree.parents[parent] in libraries:
        return prepared.get(index)
    owner = tree.records[parent]
    assert isinstance(owner, AltiumSchComponent)
    return owner.show_hidden_pins


def _bounds_pin_ordinal_visible(
    tree: _BoundsSourceTree,
    index: int,
    record: AltiumSchPin,
    library_current_components: Mapping[int, AltiumSchComponent | None],
    prepared: Mapping[int, bool],
) -> bool | None:
    parent = tree.parents[index]
    if parent is None:
        # Detached OriginalContainer state is outside this attached-tree query.
        return False
    if not record.is_hidden:
        return True
    owner = tree.records[parent]
    if parent in library_current_components:
        current = library_current_components[parent]
        return False if current is None else prepared.get(index)
    # OwnerComponent recognizes immediate ordinary/harness owners, not a
    # cavity component or a nearest ancestor. Detached history is not replayed.
    if isinstance(owner, AltiumSchComponent) and owner.record_type in (
        SchRecordType.COMPONENT,
        SchRecordType.HARNESS_COMPONENT,
    ):
        return _bounds_pin_component_visibility(
            tree, index, parent, library_current_components, prepared
        )
    return False


def _bounds_ordinal_visible(
    tree: _BoundsSourceTree,
    index: int,
    library_current_components: Mapping[int, AltiumSchComponent | None],
    prepared: Mapping[int, bool],
) -> bool | None:
    record = tree.records[index]
    if isinstance(record, AltiumSchPin):
        return _bounds_pin_ordinal_visible(
            tree, index, record, library_current_components, prepared
        )
    if isinstance(
        record,
        (
            AltiumSchParameter,
            AltiumSchHarnessType,
            AltiumSchSheetName,
            AltiumSchFileName,
        ),
    ):
        return not record.is_hidden
    # The ordinal explorer returns zero for types without an IsHidden interface.
    return True


def _build_bounds_traversal_index(
    tree: _BoundsSourceTree,
    *,
    document_kind: _BoundsDocumentKind = "schematic",
    library_current_components: Mapping[int, AltiumSchComponent | None] | None = None,
    pin_owner_show_hidden_pins: Mapping[int, bool] | None = None,
) -> _BoundsTraversalIndex:
    """Snapshot fixed-tree filters, with explicit library owner state.

    The optional pin-index map supplies effective OwnerComponent.GetShowHiddenPins
    results. Library-dependent ordinal filtering cannot infer that getter from
    the component's saved flag or its historical library-cache state.
    """
    if document_kind not in (
        "schematic",
        "harness_layout",
        "harness_wiring",
        "library",
    ):
        raise ValueError("unknown bounds document kind")
    libraries = {} if library_current_components is None else library_current_components
    prepared = _validated_pin_visibility_states(
        tree, libraries, pin_owner_show_hidden_pins
    )
    if any(not 0 <= index < len(tree.records) for index in libraries):
        raise ValueError("bounds library owner is outside the source tree")
    visible_main_models = frozenset(
        index
        for index, record in enumerate(tree.records)
        if isinstance(record, AltiumSchHarnessComponent)
        and _bounds_main_model_visible(tree, index)
    )
    return _BoundsTraversalIndex(
        tree,
        tuple(
            _bounds_current_part_passes(
                tree, index, document_kind, visible_main_models, libraries
            )
            for index in range(len(tree.records))
        ),
        _bounds_has_cavity_ancestor(tree),
        tuple(
            _bounds_ordinal_visible(tree, index, libraries, prepared)
            for index in range(len(tree.records))
        ),
    )


def _pin_visibility_is_library_dependent(
    tree: _BoundsSourceTree,
    index: int,
    libraries: Mapping[int, AltiumSchComponent | None],
) -> bool:
    parent = tree.parents[index]
    if parent is None:
        return False
    if parent in libraries:
        return libraries[parent] is not None
    owner = tree.records[parent]
    return (
        isinstance(owner, AltiumSchComponent)
        and owner.record_type
        in (SchRecordType.COMPONENT, SchRecordType.HARNESS_COMPONENT)
        and tree.parents[parent] in libraries
    )


def _validated_pin_visibility_states(
    tree: _BoundsSourceTree,
    libraries: Mapping[int, AltiumSchComponent | None],
    prepared: Mapping[int, bool] | None,
) -> Mapping[int, bool]:
    if prepared is None:
        return {}
    for index, visible in prepared.items():
        if not 0 <= index < len(tree.records) or not isinstance(
            tree.records[index], AltiumSchPin
        ):
            raise ValueError("prepared pin owner visibility requires a source pin")
        if type(visible) is not bool:
            raise ValueError("prepared pin owner visibility must be boolean")
        if not _pin_visibility_is_library_dependent(tree, index, libraries):
            raise ValueError(
                "prepared pin owner visibility requires a library-dependent source pin"
            )
    return prepared
