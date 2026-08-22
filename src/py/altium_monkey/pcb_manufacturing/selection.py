"""Dependency-closed selection from complete manufacturing output."""

from __future__ import annotations

from dataclasses import dataclass

from msgspec import UNSET

from .generated import (
    BoardOccurrence,
    ChildBoardRequest,
    ComponentOccurrence,
    Diagnostic,
    DrillSpan,
    Feature,
    HoleFeature,
    LayerInstance,
    ManufacturingDocument,
    MaterialFeature,
    ProfileFeature,
    SelectionProjection,
    SourceNet,
    SourceProvenance,
    StackRegion,
    UnresolvedSource,
    VariantSelection,
)
from .validation import validate_manufacturing_document


@dataclass(frozen=True)
class ManufacturingProjectionError(ValueError):
    """One stable complete-output projection failure."""

    code: str
    reference: str


@dataclass(frozen=True)
class _ProjectionContent:
    board_occurrences: list[BoardOccurrence]
    child_board_requests: list[ChildBoardRequest]
    stack_regions: list[StackRegion]
    layers: list[LayerInstance]
    nets: list[SourceNet]
    variant_selections: list[VariantSelection]
    component_occurrences: list[ComponentOccurrence]
    drill_spans: list[DrillSpan]
    features: list[Feature]
    diagnostics: list[Diagnostic]


def project_complete(
    document: ManufacturingDocument,
    selection: SelectionProjection,
) -> ManufacturingDocument:
    """Project a complete document while retaining every semantic dependency."""

    validate_manufacturing_document(document)
    _validate_selection(document, selection)
    content = _select_content(document, selection)
    projected = _build_document(document, selection, content)
    validate_manufacturing_document(projected)
    return projected


def _select_content(
    document: ManufacturingDocument,
    selection: SelectionProjection,
) -> _ProjectionContent:
    features, drill_spans, layers = _select_physical_rows(document, selection)
    stack_regions, board_occurrences, nets, components, variants = _select_context_rows(
        document, layers, features
    )
    child_board_requests = [
        row
        for row in document.child_board_requests
        if row.parent_board_occurrence_ref in {item.id for item in board_occurrences}
    ]
    surviving_ids = _surviving_ids(
        selection,
        board_occurrences,
        child_board_requests,
        stack_regions,
        layers,
        nets,
        variants,
        components,
        drill_spans,
        features,
    )
    sources = _sources(
        board_occurrences,
        child_board_requests,
        stack_regions,
        nets,
        variants,
        components,
        drill_spans,
        features,
    )
    return _ProjectionContent(
        board_occurrences=board_occurrences,
        child_board_requests=child_board_requests,
        stack_regions=stack_regions,
        layers=layers,
        nets=nets,
        variant_selections=variants,
        component_occurrences=components,
        drill_spans=drill_spans,
        features=features,
        diagnostics=_selected_diagnostics(document, surviving_ids, sources),
    )


def _select_physical_rows(
    document: ManufacturingDocument,
    selection: SelectionProjection,
) -> tuple[list[Feature], list[DrillSpan], list[LayerInstance]]:
    feature_ids = _selected_feature_ids(document, selection)
    features = [row for row in document.features if row.id in feature_ids]
    span_ids = {row.drill_span_ref for row in features if isinstance(row, HoleFeature)}
    drill_spans = [row for row in document.drill_spans if row.id in span_ids]
    layer_ids = _selected_layer_ids(selection, features, drill_spans)
    layers = [row for row in document.layers if row.id in layer_ids]
    return features, drill_spans, layers


def _select_context_rows(
    document: ManufacturingDocument,
    layers: list[LayerInstance],
    features: list[Feature],
) -> tuple[
    list[StackRegion],
    list[BoardOccurrence],
    list[SourceNet],
    list[ComponentOccurrence],
    list[VariantSelection],
]:
    stack_regions = _selected_stack_regions(document, layers)
    component_ids = {
        row.component_occurrence_ref
        for row in features
        if isinstance(row, MaterialFeature)
        and row.component_occurrence_ref is not UNSET
    }
    components = [
        row for row in document.component_occurrences if row.id in component_ids
    ]
    selection_ids = {row.fitted_selection_ref for row in components}
    variants = [row for row in document.variant_selections if row.id in selection_ids]
    board_occurrences = _selected_board_occurrences(
        document, stack_regions, features, components
    )
    nets = _selected_nets(document, features)
    return stack_regions, board_occurrences, nets, components, variants


def _selected_stack_regions(
    document: ManufacturingDocument,
    layers: list[LayerInstance],
) -> list[StackRegion]:
    region_ids = {row.stack_region_ref for row in layers}
    return [row for row in document.stack_regions if row.id in region_ids]


def _selected_board_occurrences(
    document: ManufacturingDocument,
    stack_regions: list[StackRegion],
    features: list[Feature],
    components: list[ComponentOccurrence],
) -> list[BoardOccurrence]:
    board_ids = _selected_board_ids(document, stack_regions, features, components)
    return [row for row in document.board_occurrences if row.id in board_ids]


def _selected_nets(
    document: ManufacturingDocument,
    features: list[Feature],
) -> list[SourceNet]:
    net_ids = {
        row.source_net_ref
        for row in features
        if isinstance(row, MaterialFeature) and row.source_net_ref is not UNSET
    }
    return [row for row in document.nets if row.id in net_ids]


def _build_document(
    document: ManufacturingDocument,
    selection: SelectionProjection,
    content: _ProjectionContent,
) -> ManufacturingDocument:
    return ManufacturingDocument(
        type=document.type,
        version=document.version,
        generator_revision=document.generator_revision,
        strictness=document.strictness,
        board_occurrences=content.board_occurrences,
        child_board_requests=content.child_board_requests,
        stack_regions=content.stack_regions,
        layers=content.layers,
        nets=content.nets,
        variant_selections=content.variant_selections,
        component_occurrences=content.component_occurrences,
        drill_spans=content.drill_spans,
        features=content.features,
        projections=[selection],
        diagnostics=content.diagnostics,
    )


def _validate_selection(
    document: ManufacturingDocument,
    selection: SelectionProjection,
) -> None:
    layer_ids = {row.id for row in document.layers}
    feature_ids = {row.id for row in document.features}
    nonprojection_ids = _nonprojection_ids(document)
    if selection.id in nonprojection_ids:
        raise ManufacturingProjectionError("selection_identity_conflict", selection.id)
    _require_unique_refs(selection.requested_layer_refs, "duplicate_layer_request")
    _require_unique_refs(selection.requested_product_refs, "duplicate_product_request")
    _require_known_refs(selection.requested_layer_refs, layer_ids, "unknown_layer")
    _require_known_refs(
        selection.requested_product_refs, feature_ids, "unknown_product"
    )


def _nonprojection_ids(document: ManufacturingDocument) -> set[str]:
    rows = (
        *document.board_occurrences,
        *document.child_board_requests,
        *document.stack_regions,
        *document.layers,
        *document.nets,
        *document.variant_selections,
        *document.component_occurrences,
        *document.drill_spans,
        *document.features,
        *document.diagnostics,
    )
    return {row.id for row in rows}


def _require_unique_refs(references: list[str], code: str) -> None:
    seen: set[str] = set()
    for reference in references:
        if reference in seen:
            raise ManufacturingProjectionError(code, reference)
        seen.add(reference)


def _require_known_refs(
    references: list[str],
    known: set[str],
    code: str,
) -> None:
    for reference in references:
        if reference not in known:
            raise ManufacturingProjectionError(code, reference)


def _selected_feature_ids(
    document: ManufacturingDocument,
    selection: SelectionProjection,
) -> set[str]:
    selected = set(selection.requested_product_refs)
    requested_layers = set(selection.requested_layer_refs)
    selected.update(
        row.id
        for row in document.features
        if isinstance(row, MaterialFeature) and row.layer_ref in requested_layers
    )
    parents = {
        row.id: row.parent_feature_ref
        for row in document.features
        if row.parent_feature_ref is not UNSET
    }
    pending = list(selected)
    while pending:
        parent = parents.get(pending.pop())
        if parent is not None and parent not in selected:
            selected.add(parent)
            pending.append(parent)
    return selected


def _selected_layer_ids(
    selection: SelectionProjection,
    features: list[Feature],
    drill_spans: list[DrillSpan],
) -> set[str]:
    selected = set(selection.requested_layer_refs)
    selected.update(
        row.layer_ref for row in features if isinstance(row, MaterialFeature)
    )
    for row in drill_spans:
        selected.add(row.start_layer_ref)
        selected.add(row.end_layer_ref)
    return selected


def _selected_board_ids(
    document: ManufacturingDocument,
    stack_regions: list[StackRegion],
    features: list[Feature],
    components: list[ComponentOccurrence],
) -> set[str]:
    selected = {row.board_occurrence_ref for row in stack_regions}
    selected.update(
        row.board_occurrence_ref for row in features if isinstance(row, ProfileFeature)
    )
    selected.update(row.board_occurrence_ref for row in components)
    parents = {
        row.id: row.parent_occurrence_ref
        for row in document.board_occurrences
        if row.parent_occurrence_ref is not UNSET
    }
    pending = list(selected)
    while pending:
        parent = parents.get(pending.pop())
        if parent is not None and parent not in selected:
            selected.add(parent)
            pending.append(parent)
    return selected


def _surviving_ids(
    selection: SelectionProjection,
    board_occurrences: list[BoardOccurrence],
    child_board_requests: list[ChildBoardRequest],
    stack_regions: list[StackRegion],
    layers: list[LayerInstance],
    nets: list[SourceNet],
    variant_selections: list[VariantSelection],
    component_occurrences: list[ComponentOccurrence],
    drill_spans: list[DrillSpan],
    features: list[Feature],
) -> set[str]:
    rows = (
        *board_occurrences,
        *child_board_requests,
        *stack_regions,
        *layers,
        *nets,
        *variant_selections,
        *component_occurrences,
        *drill_spans,
        *features,
    )
    return {selection.id, *(row.id for row in rows)}


def _sources(
    board_occurrences: list[BoardOccurrence],
    child_board_requests: list[ChildBoardRequest],
    stack_regions: list[StackRegion],
    nets: list[SourceNet],
    variant_selections: list[VariantSelection],
    component_occurrences: list[ComponentOccurrence],
    drill_spans: list[DrillSpan],
    features: list[Feature],
) -> list[SourceProvenance]:
    rows = (
        *board_occurrences,
        *child_board_requests,
        *stack_regions,
        *nets,
        *variant_selections,
        *component_occurrences,
        *drill_spans,
        *features,
    )
    sources = [row.source for row in rows]
    sources.extend(
        row.variation_source
        for row in component_occurrences
        if row.variation_source is not UNSET
    )
    return sources


def _selected_diagnostics(
    document: ManufacturingDocument,
    surviving_ids: set[str],
    sources: list[SourceProvenance],
) -> list[Diagnostic]:
    selected_ids = _referenced_diagnostic_ids(sources)
    selected_ids.update(_subject_diagnostic_ids(document, surviving_ids))
    _close_diagnostic_sources(document, selected_ids)
    return [row for row in document.diagnostics if row.id in selected_ids]


def _referenced_diagnostic_ids(sources: list[SourceProvenance]) -> set[str]:
    return {
        source.diagnostic_ref
        for source in sources
        if isinstance(source, UnresolvedSource)
    }


def _subject_diagnostic_ids(
    document: ManufacturingDocument,
    surviving_ids: set[str],
) -> set[str]:
    return {
        row.id
        for row in document.diagnostics
        if row.affected_ref is UNSET or row.affected_ref in surviving_ids
    }


def _close_diagnostic_sources(
    document: ManufacturingDocument,
    selected_ids: set[str],
) -> None:
    by_id = {row.id: row for row in document.diagnostics}
    pending = list(selected_ids)
    while pending:
        row = by_id[pending.pop()]
        if not isinstance(row.source, UnresolvedSource):
            continue
        if _add(selected_ids, row.source.diagnostic_ref):
            pending.append(row.source.diagnostic_ref)


def _add(values: set[str], value: str) -> bool:
    if value in values:
        return False
    values.add(value)
    return True
