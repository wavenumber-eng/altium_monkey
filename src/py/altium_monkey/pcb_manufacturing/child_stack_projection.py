"""Project one loaded child board into occurrence-qualified normalized rows."""

from __future__ import annotations

from typing import Literal, cast

from msgspec import UNSET

from altium_monkey.altium_board import AltiumBoard
from altium_monkey.altium_pcb_layer_ref import PcbLayerRef
from altium_monkey.altium_resolved_layer_stack import resolved_layer_stack_from_pcbdoc

from .generated import (
    BoardOccurrence,
    ComponentOccurrence,
    Diagnostic,
    DrillSpan,
    Feature,
    LayerInstance,
    MaterialFeature,
    ProfileFeature,
    SourceNet,
    StackRegion,
    UnresolvedSource,
    VariantSelection,
)
from .output_layers import (
    ManufacturingLayerOccurrence,
    walk_manufacturing_output_layers,
)
from .materialize_routes import (
    _component_rows,
    _materialize_physical_rows,
    _materialize_profile_rows,
    _materialize_route_rows,
)
from .resolved_inputs import (
    PcbChildRequestOutcome,
    ResolvedEmbeddedBoardReferenceInput,
    _child_board_occurrence_ref,
    _resolve_component_inputs,
    _verified_loaded_child,
)
from .source_provenance import PcbDocSourceIndex
from .variant_selection import PcbManufacturingVariantSelection

Strictness = Literal["strict", "permissive"]


def materialize_pcb_child_board_profile_rows(
    occurrence: BoardOccurrence,
    outcome: PcbChildRequestOutcome,
    *,
    strictness: Strictness,
) -> tuple[tuple[ProfileFeature, ...], tuple[Diagnostic, ...]]:
    """Materialize child-local profiles owned by one exact board occurrence."""

    loaded = _verified_loaded_child(outcome.request, outcome)
    if loaded is None:
        raise ValueError("child profile projection requires a loaded child outcome")
    _assert_occurrence_matches_loaded_child(occurrence, outcome)
    affine_depth = _child_affine_depth(occurrence)
    source_index = PcbDocSourceIndex.from_pcbdoc(
        loaded.document,
        logical_path=loaded.identity.logical_path,
    )
    return _materialize_profile_rows(
        loaded.document,
        source_index=source_index,
        layer_stack=resolved_layer_stack_from_pcbdoc(loaded.document),
        strictness=strictness,
        board_occurrence_ref=occurrence.id,
        feature_id_prefix=f"{occurrence.id}.",
        affine_depth=affine_depth,
    )


def materialize_pcb_child_board_route_rows(
    occurrence: BoardOccurrence,
    outcome: PcbChildRequestOutcome,
    *,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None = None,
) -> tuple[tuple[SourceNet, ...], tuple[MaterialFeature, ...], tuple[Diagnostic, ...]]:
    """Materialize child-local routes and nets owned by one board occurrence."""

    loaded = _verified_loaded_child(outcome.request, outcome)
    if loaded is None:
        raise ValueError("child route projection requires a loaded child outcome")
    _assert_occurrence_matches_loaded_child(occurrence, outcome)
    affine_depth = _child_affine_depth(occurrence)
    source_index = PcbDocSourceIndex.from_pcbdoc(
        loaded.document,
        logical_path=loaded.identity.logical_path,
    )
    if variant_selection is not None:
        variant_selection.assert_applies_to(source_index)
    return _materialize_route_rows(
        loaded.document,
        source_index=source_index,
        layer_stack=resolved_layer_stack_from_pcbdoc(loaded.document),
        strictness=strictness,
        id_prefix=f"{occurrence.id}.",
        board_occurrence_ref=occurrence.id,
        affine_depth=affine_depth,
        variant_selection=variant_selection,
    )


def materialize_pcb_child_board_physical_rows(
    occurrence: BoardOccurrence,
    outcome: PcbChildRequestOutcome,
    *,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None = None,
) -> tuple[
    tuple[SourceNet, ...],
    tuple[DrillSpan, ...],
    tuple[Feature, ...],
    tuple[Diagnostic, ...],
]:
    """Materialize child-local lands, holes, fills, spans, and owned nets."""

    loaded = _verified_loaded_child(outcome.request, outcome)
    if loaded is None:
        raise ValueError("child physical projection requires a loaded child outcome")
    _assert_occurrence_matches_loaded_child(occurrence, outcome)
    affine_depth = _child_affine_depth(occurrence)
    source_index = PcbDocSourceIndex.from_pcbdoc(
        loaded.document,
        logical_path=loaded.identity.logical_path,
    )
    if variant_selection is not None:
        variant_selection.assert_applies_to(source_index)
    return _materialize_physical_rows(
        loaded.document,
        source_index=source_index,
        layer_stack=resolved_layer_stack_from_pcbdoc(loaded.document),
        strictness=strictness,
        id_prefix=f"{occurrence.id}.",
        board_occurrence_ref=occurrence.id,
        affine_depth=affine_depth,
        variant_selection=variant_selection,
    )


def materialize_pcb_child_board_component_rows(
    occurrence: BoardOccurrence,
    outcome: PcbChildRequestOutcome,
    *,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection,
) -> tuple[
    tuple[VariantSelection, ...],
    tuple[ComponentOccurrence, ...],
    tuple[Diagnostic, ...],
]:
    """Materialize child-local component rows under exact selection authority."""

    if strictness not in {"strict", "permissive"}:
        raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
    loaded = _verified_loaded_child(outcome.request, outcome)
    if loaded is None:
        raise ValueError("child component projection requires a loaded child outcome")
    _assert_occurrence_matches_loaded_child(occurrence, outcome)
    source_index = PcbDocSourceIndex.from_pcbdoc(
        loaded.document,
        logical_path=loaded.identity.logical_path,
    )
    variant_selection.assert_applies_to(source_index)
    diagnostics: list[Diagnostic] = []
    components = _resolve_component_inputs(
        loaded.document,
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    variants, rows, _fitted = _component_rows(
        components,
        variant_selection=variant_selection,
        diagnostics=diagnostics,
        board_occurrence_ref=occurrence.id,
        id_prefix=f"{occurrence.id}.",
    )
    return tuple(variants), tuple(rows), tuple(diagnostics)


def resolve_pcb_child_board_stack_rows(
    occurrence: BoardOccurrence,
    outcome: PcbChildRequestOutcome,
    *,
    strictness: Strictness,
    requested_layer_refs: tuple[PcbLayerRef, ...] = (),
) -> tuple[tuple[StackRegion, ...], tuple[LayerInstance, ...], tuple[Diagnostic, ...]]:
    """Resolve occurrence-qualified stack and output-layer rows for one child."""

    loaded = _verified_loaded_child(outcome.request, outcome)
    if loaded is None:
        raise ValueError("child stack projection requires a loaded child outcome")
    _assert_occurrence_matches_loaded_child(occurrence, outcome)
    board = loaded.document.board
    if board is None:
        raise ValueError("child stack projection requires Board6/Data")

    source_index = PcbDocSourceIndex.from_pcbdoc(
        loaded.document,
        logical_path=loaded.identity.logical_path,
    )
    walked_layers = walk_manufacturing_output_layers(
        resolved_layer_stack_from_pcbdoc(loaded.document),
        requested_layer_refs=requested_layer_refs,
    )
    stack_regions, diagnostics = _child_stack_regions(
        occurrence,
        walked_layers,
        board=board,
        source_index=source_index,
        strictness=strictness,
    )
    layers = tuple(
        _child_layer_instance(occurrence, row, stack_regions) for row in walked_layers
    )
    return stack_regions, layers, diagnostics


def _child_affine_depth(occurrence: BoardOccurrence) -> int:
    affine_depth = occurrence.affine.composition_depth
    if type(affine_depth) is not int or not 1 <= affine_depth <= (1 << 32) - 1:
        raise ValueError(
            "child board occurrence affine depth must be uint32 and positive"
        )
    return affine_depth


def _assert_occurrence_matches_loaded_child(
    occurrence: BoardOccurrence,
    outcome: PcbChildRequestOutcome,
) -> None:
    loaded = outcome.loaded_child
    if loaded is None:
        raise ValueError("child stack projection requires a loaded child outcome")
    request_row = outcome.row
    identity = loaded.identity
    expected_identity = (
        request_row.id,
        request_row.parent_board_occurrence_ref,
        identity.provider_id,
        identity.logical_path,
        identity.document_revision_sha256,
    )
    actual_identity = (
        occurrence.child_request_ref,
        occurrence.parent_occurrence_ref,
        occurrence.provider_id,
        occurrence.resolved_logical_path,
        occurrence.document_revision_sha256,
    )
    if actual_identity != expected_identity:
        raise ValueError("board occurrence does not belong to the loaded child outcome")
    row_index, column_index, step_row_index, step_column_index = _child_indices(
        occurrence
    )
    request = outcome.request
    _assert_child_step_indices(
        request,
        row_index=row_index,
        column_index=column_index,
        step_row_index=step_row_index,
        step_column_index=step_column_index,
    )
    expected_ref = _child_board_occurrence_ref(
        request,
        request_row.parent_board_occurrence_ref,
        identity,
        row_index,
        column_index,
    )
    if occurrence.id != expected_ref or not _same_request_source(occurrence, outcome):
        raise ValueError("board occurrence does not belong to the loaded child outcome")


def _child_indices(occurrence: BoardOccurrence) -> tuple[int, int, int, int]:
    values = (
        occurrence.row_index,
        occurrence.column_index,
        occurrence.step_row_index,
        occurrence.step_column_index,
    )
    if any(type(value) is not int for value in values):
        raise ValueError("child board occurrence indices must be uint32")
    if any(not 0 <= cast(int, value) <= (1 << 32) - 1 for value in values):
        raise ValueError("child board occurrence indices must be uint32")
    return cast(tuple[int, int, int, int], values)


def _assert_child_step_indices(
    request: ResolvedEmbeddedBoardReferenceInput,
    *,
    row_index: int,
    column_index: int,
    step_row_index: int,
    step_column_index: int,
) -> None:
    expected_indices = (
        request.row_count - row_index - 1 if request.mirror else row_index,
        request.column_count - column_index - 1 if request.mirror else column_index,
    )
    if not (0 <= row_index < request.row_count) or not (
        0 <= column_index < request.column_count
    ):
        raise ValueError("child board occurrence repeat index is out of range")
    if (step_row_index, step_column_index) != expected_indices:
        raise ValueError("child board occurrence step indices do not match the request")


def _same_request_source(
    occurrence: BoardOccurrence,
    outcome: PcbChildRequestOutcome,
) -> bool:
    request_source = outcome.request.source
    if not isinstance(request_source, UnresolvedSource):
        return occurrence.source == request_source
    return isinstance(occurrence.source, UnresolvedSource) and (
        occurrence.source.reason == request_source.reason
    )


def _child_stack_regions(
    occurrence: BoardOccurrence,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    *,
    board: AltiumBoard,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
) -> tuple[tuple[StackRegion, ...], tuple[Diagnostic, ...]]:
    context_refs = tuple(dict.fromkeys(row.source_stackup_ref for row in walked_layers))
    regions: list[StackRegion] = []
    diagnostics: list[Diagnostic] = []
    for context_ref in context_refs:
        region_ref = _child_stack_region_ref(occurrence.id, context_ref)
        resolution = source_index.resolve(
            board,
            strictness=strictness,
            affected_ref=region_ref,
        )
        regions.append(
            StackRegion(
                id=region_ref,
                board_occurrence_ref=occurrence.id,
                source=resolution.source,
            )
        )
        diagnostics.extend(resolution.diagnostics)
    return tuple(regions), tuple(diagnostics)


def _child_stack_region_ref(occurrence_ref: str, source_stackup_ref: str) -> str:
    return f"{occurrence_ref}.stack_region.{source_stackup_ref or 'whole_board'}"


def _child_layer_instance(
    board_occurrence: BoardOccurrence,
    occurrence: ManufacturingLayerOccurrence,
    stack_regions: tuple[StackRegion, ...],
) -> LayerInstance:
    stack_region_ref = _child_stack_region_ref(
        board_occurrence.id,
        occurrence.source_stackup_ref,
    )
    if all(row.id != stack_region_ref for row in stack_regions):
        raise ValueError(f"unknown stack region for {occurrence.id}")
    layer_ref = occurrence.layer.layer_ref
    if layer_ref is None:
        raise ValueError(f"{occurrence.id} has no exact layer ref")
    return LayerInstance(
        id=f"{board_occurrence.id}.{occurrence.id}",
        stack_region_ref=stack_region_ref,
        pcb_layer_ref=layer_ref.token,
        legacy_layer_id=(
            occurrence.layer.legacy_id
            if occurrence.layer.legacy_id is not None
            else UNSET
        ),
        saved_v7_layer_id=(
            occurrence.layer.v7_id if occurrence.layer.v7_id is not None else UNSET
        ),
        side=occurrence.side,
        material_role=occurrence.material_role,
        z_min_nm=occurrence.z_min_nm,
        z_max_nm=occurrence.z_max_nm,
        film_baseline=occurrence.film_baseline,
    )
