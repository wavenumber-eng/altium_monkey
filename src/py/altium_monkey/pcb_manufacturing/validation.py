"""Semantic validation beyond the generated structural contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import lru_cache
import math
from typing import cast

from msgspec import UNSET, UnsetType
from shapely.geometry import Polygon

from .affine import rotation_affine_degrees
from .generated import (
    BoardOccurrence,
    CapsuleGeometry,
    ChildBoardRequest,
    CircleGeometry,
    CircularArcSegment,
    ComponentOccurrence,
    Diagnostic,
    Feature,
    Geometry,
    HoleFeature,
    LayerInstance,
    LineSegment,
    LocatedSource,
    ManufacturingDocument,
    MaterialFeature,
    OrientedRectangleGeometry,
    PathGeometry,
    PathSegment,
    PcbDecimalAffine2d,
    ProfileFeature,
    RegionGeometry,
    RoundedRectangleGeometry,
    SourceProvenance,
    SourceNet,
    StackRegion,
    StrokeGeometry,
    UnresolvedSource,
    VariantSelection,
)
from .variant_selection import _is_source_unique_id

_ANGLE_SCALE = 10**12
_RIGHT_ANGLE_E12 = 90 * _ANGLE_SCALE
_FULL_ANGLE_E12 = 360 * _ANGLE_SCALE
_DECIMAL_PRECISION = 180
_TRIG_SERIES_EPSILON = Decimal("1e-95")
# This dominates decimal/trig error after multiplication by the largest
# possible difference of two signed-int64 coordinates.
_NUMERIC_ERROR_NM = Decimal("1e-45")
_MAX_INT64 = (1 << 63) - 1
_PI = Decimal(
    "3.141592653589793238462643383279502884197169399375105820974944592307816406286"
    "208998628034825342117067982148086513282306647093844609550582231725359408128"
    "48111745028410270193852110555964462294895493038196"
)
_SQRT_TWO_LOWER = Decimal(
    "1.41421356237309504880168872420969807856967187537694807317667973799"
)
_SQRT_TWO_UPPER = Decimal(
    "1.41421356237309504880168872420969807856967187537694807317667973800"
)
_CONTAINMENT_SAGITTA_NM = 0.25
_CONTAINMENT_NUMERIC_GUARD_NM = Decimal("0.25")
_MAX_CONTAINMENT_LOCAL_COORDINATE_NM = 2**48
_MAX_CONTAINMENT_ARC_SEGMENTS = 250_000
_CHILD_DOCUMENT_DIAGNOSTIC_CODES = frozenset(
    {
        "child_document_invalid_path",
        "child_document_denied",
        "child_document_missing",
        "child_document_ambiguous",
        "child_document_changed_revision",
        "child_document_cyclic_reference",
        "child_document_resource_limit",
        "child_document_invalid_format",
    }
)


@dataclass(frozen=True)
class ManufacturingValidationError(ValueError):
    """One stable semantic validation failure."""

    code: str
    path: str


def validate_manufacturing_document(document: ManufacturingDocument) -> None:
    """Validate identity, references, provenance, and basic analytic invariants."""

    ids = _collect_ids(document)
    _validate_sources(document, ids)
    _validate_references(document, ids)
    _validate_affines(document)
    _validate_geometry(document)


def _collect_ids(document: ManufacturingDocument) -> set[str]:
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
        *document.projections,
        *document.diagnostics,
    )
    ids: set[str] = set()
    for row in rows:
        if row.id in ids:
            raise ManufacturingValidationError("duplicate_identity", row.id)
        ids.add(row.id)
    return ids


def _validate_sources(document: ManufacturingDocument, ids: set[str]) -> None:
    diagnostics = {row.id: row for row in document.diagnostics}
    for owner, source in _source_rows(document):
        _validate_source(document.strictness, owner, source, diagnostics, ids)


def _source_rows(
    document: ManufacturingDocument,
) -> list[tuple[str, SourceProvenance]]:
    sources: list[tuple[str, SourceProvenance]] = []
    sources.extend((row.id, row.source) for row in document.board_occurrences)
    sources.extend((row.id, row.source) for row in document.child_board_requests)
    sources.extend((row.id, row.source) for row in document.stack_regions)
    sources.extend((row.id, row.source) for row in document.nets)
    sources.extend(_component_source_rows(document))
    sources.extend((row.id, row.source) for row in document.drill_spans)
    sources.extend((row.id, row.source) for row in document.features)
    sources.extend(
        (row.id, row.source) for row in document.diagnostics if row.source is not UNSET
    )
    return sources


def _component_source_rows(
    document: ManufacturingDocument,
) -> list[tuple[str, SourceProvenance]]:
    sources = [(row.id, row.source) for row in document.variant_selections]
    sources.extend((row.id, row.source) for row in document.component_occurrences)
    sources.extend(
        (row.id, row.variation_source)
        for row in document.component_occurrences
        if row.variation_source is not UNSET
    )
    return sources


def _validate_source(
    strictness: str,
    owner: str,
    source: SourceProvenance,
    diagnostics: dict[str, Diagnostic],
    ids: set[str],
) -> None:
    if not isinstance(source, UnresolvedSource):
        _validate_source_unit(owner, source)
        return
    if strictness == "strict":
        raise ManufacturingValidationError("unresolved_source_in_strict_mode", owner)
    diagnostic = diagnostics.get(source.diagnostic_ref)
    if diagnostic is None or diagnostic.code != "unresolved_source":
        raise ManufacturingValidationError(
            "missing_unresolved_source_diagnostic", owner
        )
    if diagnostic.affected_ref is UNSET or diagnostic.affected_ref != owner:
        raise ManufacturingValidationError("diagnostic_owner_mismatch", owner)
    if source.diagnostic_ref not in ids:
        raise ManufacturingValidationError("unknown_diagnostic_ref", owner)


def _validate_source_unit(owner: str, source: SourceProvenance) -> None:
    if isinstance(source, UnresolvedSource):
        return
    numerator = source.source_unit_nm_numerator
    denominator = source.source_unit_nm_denominator
    if (numerator is UNSET) != (denominator is UNSET):
        raise ManufacturingValidationError("incomplete_source_unit", owner)
    if numerator is UNSET or denominator is UNSET:
        return
    if numerator <= 0 or denominator <= 0:
        raise ManufacturingValidationError("invalid_source_unit", owner)


def _validate_references(document: ManufacturingDocument, ids: set[str]) -> None:
    board_ids = {row.id for row in document.board_occurrences}
    region_ids = {row.id for row in document.stack_regions}
    layer_ids = {row.id for row in document.layers}
    net_ids = {row.id for row in document.nets}
    nets_by_id = {row.id: row for row in document.nets}
    component_ids, components_by_id = _component_indexes(document)
    span_ids = {row.id for row in document.drill_spans}
    feature_ids = {row.id for row in document.features}
    _validate_board_occurrence_graph(document, board_ids)
    _validate_stack_references(document, board_ids, region_ids)
    for net in document.nets:
        _required_ref(net.board_occurrence_ref, board_ids, net.id)
    _validate_variant_references(document, board_ids)
    _validate_child_board_requests(document, board_ids)
    _validate_physical_references(
        document,
        board_ids,
        layer_ids,
        net_ids,
        nets_by_id,
        component_ids,
        components_by_id,
        span_ids,
        feature_ids,
    )
    _validate_projection_references(document, layer_ids, ids)


def _component_indexes(
    document: ManufacturingDocument,
) -> tuple[set[str], dict[str, ComponentOccurrence]]:
    return (
        {row.id for row in document.component_occurrences},
        {row.id: row for row in document.component_occurrences},
    )


def _validate_variant_references(
    document: ManufacturingDocument,
    board_ids: set[str],
) -> None:
    selections = {row.id: row for row in document.variant_selections}
    selection_boards: set[str] = set()
    for selection in document.variant_selections:
        _required_ref(selection.board_occurrence_ref, board_ids, selection.id)
        if selection.board_occurrence_ref in selection_boards:
            raise ManufacturingValidationError(
                "duplicate_board_variant_selection", selection.id
            )
        selection_boards.add(selection.board_occurrence_ref)
        _validate_variant_selection(selection)
    _validate_component_variant_references(document, board_ids, selections)


def _validate_component_variant_references(
    document: ManufacturingDocument,
    board_ids: set[str],
    selections: dict[str, VariantSelection],
) -> None:
    component_source_ids: set[tuple[str, str]] = set()
    variation_indices: set[tuple[str, int]] = set()
    for component in document.component_occurrences:
        _required_ref(component.board_occurrence_ref, board_ids, component.id)
        selection = selections.get(component.fitted_selection_ref)
        if selection is None:
            raise ManufacturingValidationError(
                "unknown_fitted_selection_ref", component.id
            )
        if selection.board_occurrence_ref != component.board_occurrence_ref:
            raise ManufacturingValidationError(
                "component_selection_owner_mismatch", component.id
            )
        _validate_component_selection(component, selection)
        variation_index = _component_variation_record_index(component, selection)
        if variation_index is not None:
            index_key = (selection.id, variation_index)
            if index_key in variation_indices:
                raise ManufacturingValidationError(
                    "duplicate_component_variation_index", component.id
                )
            variation_indices.add(index_key)
        if component.source_component_unique_id is not UNSET:
            key = (
                component.board_occurrence_ref,
                component.source_component_unique_id,
            )
            if key in component_source_ids:
                raise ManufacturingValidationError(
                    "duplicate_component_source_identity", component.id
                )
            component_source_ids.add(key)


def _validate_variant_selection(selection: VariantSelection) -> None:
    if selection.kind == "no_variations":
        _validate_no_variations_selection(selection)
        return
    _validate_project_variant_selection(selection)


def _validate_no_variations_selection(selection: VariantSelection) -> None:
    if (
        selection.display_name != "[No Variations]"
        or selection.project_variant_unique_id is not UNSET
    ):
        raise ManufacturingValidationError(
            "invalid_no_variations_selection", selection.id
        )


def _validate_project_variant_selection(selection: VariantSelection) -> None:
    if not selection.display_name or selection.display_name == "[No Variations]":
        raise ManufacturingValidationError(
            "invalid_project_variant_selection", selection.id
        )
    if selection.project_variant_unique_id is UNSET or not isinstance(
        selection.source, LocatedSource
    ):
        raise ManufacturingValidationError(
            "invalid_project_variant_selection", selection.id
        )
    if (
        selection.source.persistent_uid != selection.project_variant_unique_id
        or selection.source.record_index != 0
        or not _is_project_variant_section(selection.source.stream_name)
    ):
        raise ManufacturingValidationError(
            "invalid_project_variant_provenance", selection.id
        )


def _validate_component_selection(
    component: ComponentOccurrence,
    selection: VariantSelection,
) -> None:
    has_kind = component.variation_kind is not UNSET
    has_source = component.variation_source is not UNSET
    _validate_component_variation_fields(component, has_kind, has_source)
    if selection.kind == "no_variations":
        _validate_no_variations_component(component, has_kind)
        return
    _validate_project_variant_component(component, has_kind)


def _validate_component_variation_fields(
    component: ComponentOccurrence,
    has_kind: bool,
    has_source: bool,
) -> None:
    if has_kind != has_source:
        raise ManufacturingValidationError(
            "incomplete_component_variation", component.id
        )
    if component.source_component_unique_id is not UNSET and not _is_source_unique_id(
        component.source_component_unique_id
    ):
        raise ManufacturingValidationError(
            "invalid_component_source_identity", component.id
        )


def _validate_no_variations_component(
    component: ComponentOccurrence,
    has_kind: bool,
) -> None:
    if not component.fitted or has_kind:
        raise ManufacturingValidationError(
            "invalid_no_variations_component", component.id
        )


def _validate_project_variant_component(
    component: ComponentOccurrence,
    has_kind: bool,
) -> None:
    if component.source_component_unique_id is UNSET:
        raise ManufacturingValidationError(
            "missing_component_source_identity", component.id
        )
    if not has_kind:
        if not component.fitted:
            raise ManufacturingValidationError(
                "invalid_default_fitted_component", component.id
            )
        return
    if component.variation_kind not in (0, 1):
        raise ManufacturingValidationError(
            "unsupported_component_variation_kind", component.id
        )
    expected_fitted = component.variation_kind == 0
    if component.fitted != expected_fitted:
        raise ManufacturingValidationError(
            "component_fitted_decision_mismatch", component.id
        )


def _component_variation_record_index(
    component: ComponentOccurrence,
    selection: VariantSelection,
) -> int | None:
    if component.variation_source is UNSET:
        return None
    source = component.variation_source
    if not isinstance(selection.source, LocatedSource) or not isinstance(
        source, LocatedSource
    ):
        raise ManufacturingValidationError(
            "invalid_component_variation_provenance", component.id
        )
    if source.persistent_uid != component.source_component_unique_id:
        raise ManufacturingValidationError(
            "component_variation_uid_mismatch", component.id
        )
    if (
        source.document_revision_sha256 != selection.source.document_revision_sha256
        or source.logical_path != selection.source.logical_path
        or source.stream_name != selection.source.stream_name
    ):
        raise ManufacturingValidationError(
            "component_variation_source_mismatch", component.id
        )
    if source.record_index <= 0:
        raise ManufacturingValidationError(
            "invalid_component_variation_index", component.id
        )
    return source.record_index


def _is_project_variant_section(value: str) -> bool:
    prefix = "ProjectVariant"
    return value.startswith(prefix) and value[len(prefix) :].isdecimal()


def _validate_stack_references(
    document: ManufacturingDocument,
    board_ids: set[str],
    region_ids: set[str],
) -> None:
    for row in document.stack_regions:
        _required_ref(row.board_occurrence_ref, board_ids, row.id)
    for row in document.layers:
        _required_ref(row.stack_region_ref, region_ids, row.id)
        if row.z_min_nm > row.z_max_nm:
            raise ManufacturingValidationError("reversed_z_envelope", row.id)


def _validate_board_occurrence_graph(
    document: ManufacturingDocument,
    board_ids: set[str],
) -> None:
    requests = {row.id: row for row in document.child_board_requests}
    boards = {row.id: row for row in document.board_occurrences}
    original_indices: set[tuple[str, int, int]] = set()
    step_indices: set[tuple[str, int, int]] = set()
    for row in document.board_occurrences:
        _optional_ref(row.parent_occurrence_ref, board_ids, row.id)
    _validate_board_occurrence_ancestry(document.board_occurrences)
    for row in document.board_occurrences:
        _validate_child_board_occurrence(
            row,
            requests,
            boards,
            original_indices,
            step_indices,
        )


def _validate_child_board_occurrence(
    row: BoardOccurrence,
    requests: dict[str, ChildBoardRequest],
    boards: dict[str, BoardOccurrence],
    original_indices: set[tuple[str, int, int]],
    step_indices: set[tuple[str, int, int]],
) -> None:
    child_values = (
        row.child_request_ref,
        row.provider_id,
        row.resolved_logical_path,
        row.document_revision_sha256,
        row.row_index,
        row.column_index,
        row.step_row_index,
        row.step_column_index,
    )
    child_field_count = sum(value is not UNSET for value in child_values)
    if child_field_count == 0:
        _validate_root_board_occurrence(row)
        return
    if child_field_count != len(child_values) or row.parent_occurrence_ref is UNSET:
        raise ManufacturingValidationError("invalid_child_board_occurrence", row.id)
    _validate_child_occurrence_indices(row)
    request_ref = cast(str, row.child_request_ref)
    parent_ref = cast(str, row.parent_occurrence_ref)
    row_index = cast(int, row.row_index)
    column_index = cast(int, row.column_index)
    step_row_index = cast(int, row.step_row_index)
    step_column_index = cast(int, row.step_column_index)
    request = requests.get(request_ref)
    if request is None:
        raise ManufacturingValidationError("unknown_child_request_ref", row.id)
    _validate_child_occurrence_request_identity(row, request)
    parent = boards[parent_ref]
    if row.affine.composition_depth != parent.affine.composition_depth + 1:
        raise ManufacturingValidationError("child_occurrence_affine_depth", row.id)
    original = (request_ref, row_index, column_index)
    step = (request_ref, step_row_index, step_column_index)
    if original in original_indices:
        raise ManufacturingValidationError("duplicate_child_occurrence_index", row.id)
    if step in step_indices:
        raise ManufacturingValidationError(
            "duplicate_child_occurrence_step_index", row.id
        )
    original_indices.add(original)
    step_indices.add(step)


def _validate_child_occurrence_indices(row: BoardOccurrence) -> None:
    values = (
        row.row_index,
        row.column_index,
        row.step_row_index,
        row.step_column_index,
    )
    if any(type(value) is not int for value in values):
        raise ManufacturingValidationError("invalid_child_occurrence_index", row.id)
    if any(not 0 <= cast(int, value) <= (1 << 32) - 1 for value in values):
        raise ManufacturingValidationError("invalid_child_occurrence_index", row.id)


def _validate_root_board_occurrence(row: BoardOccurrence) -> None:
    if row.parent_occurrence_ref is not UNSET:
        raise ManufacturingValidationError("invalid_child_board_occurrence", row.id)
    if row.affine.composition_depth != 0:
        raise ManufacturingValidationError("root_occurrence_affine_depth", row.id)


def _validate_child_occurrence_request_identity(
    row: BoardOccurrence,
    request: ChildBoardRequest,
) -> None:
    observed = (
        request.disposition,
        row.parent_occurrence_ref,
        row.provider_id,
        row.resolved_logical_path,
        row.document_revision_sha256,
    )
    expected = (
        "loaded",
        request.parent_board_occurrence_ref,
        request.provider_id,
        request.resolved_logical_path,
        request.document_revision_sha256,
    )
    if observed != expected or not _same_child_request_source(row, request):
        raise ManufacturingValidationError("child_occurrence_request_mismatch", row.id)


def _same_child_request_source(
    row: BoardOccurrence,
    request: ChildBoardRequest,
) -> bool:
    if not isinstance(request.source, UnresolvedSource):
        return row.source == request.source
    return isinstance(row.source, UnresolvedSource) and (
        row.source.reason == request.source.reason
    )


def _validate_board_occurrence_ancestry(rows: list[BoardOccurrence]) -> None:
    parents = {
        row.id: row.parent_occurrence_ref
        for row in rows
        if row.parent_occurrence_ref is not UNSET
    }
    for start in parents:
        seen: set[str] = set()
        current = start
        while current in parents:
            if current in seen:
                raise ManufacturingValidationError("cyclic_board_occurrence", start)
            seen.add(current)
            current = parents[current]


def _validate_child_board_requests(
    document: ManufacturingDocument,
    board_ids: set[str],
) -> None:
    diagnostics = {row.id: row for row in document.diagnostics}
    referenced_diagnostics: set[str] = set()
    for row in document.child_board_requests:
        _required_ref(row.parent_board_occurrence_ref, board_ids, row.id)
        if not row.requested_document_path or "\x00" in row.requested_document_path:
            raise ManufacturingValidationError("invalid_child_document_path", row.id)
        if row.disposition == "loaded":
            _validate_loaded_child_request(row)
            continue
        if document.strictness == "strict":
            raise ManufacturingValidationError(
                "unavailable_child_in_strict_mode", row.id
            )
        diagnostic = _validate_unavailable_child_request(row, diagnostics)
        referenced_diagnostics.add(diagnostic.id)
    for diagnostic in document.diagnostics:
        if (
            diagnostic.code in _CHILD_DOCUMENT_DIAGNOSTIC_CODES
            and diagnostic.id not in referenced_diagnostics
        ):
            raise ManufacturingValidationError(
                "orphan_child_document_diagnostic",
                diagnostic.id,
            )


def _validate_loaded_child_request(row: ChildBoardRequest) -> None:
    if (
        row.resolved_logical_path is UNSET
        or row.observed_revision_sha256 is not UNSET
        or row.document_revision_sha256 is UNSET
        or row.diagnostic_ref is not UNSET
    ):
        raise ManufacturingValidationError("invalid_loaded_child_request", row.id)
    if (
        row.expected_revision_sha256 is not UNSET
        and row.expected_revision_sha256 != row.document_revision_sha256
    ):
        raise ManufacturingValidationError("invalid_child_revision_evidence", row.id)


def _validate_unavailable_child_request(
    row: ChildBoardRequest,
    diagnostics: dict[str, Diagnostic],
) -> Diagnostic:
    if row.document_revision_sha256 is not UNSET or row.diagnostic_ref is UNSET:
        raise ManufacturingValidationError("invalid_unavailable_child_request", row.id)
    diagnostic = diagnostics.get(row.diagnostic_ref)
    if diagnostic is None:
        raise ManufacturingValidationError("missing_child_document_diagnostic", row.id)
    _validate_child_document_diagnostic(row, diagnostic)
    if row.observed_revision_sha256 is not UNSET:
        if (
            row.expected_revision_sha256 is UNSET
            or diagnostic.code != "child_document_changed_revision"
            or row.expected_revision_sha256 == row.observed_revision_sha256
        ):
            raise ManufacturingValidationError(
                "invalid_child_revision_evidence", row.id
            )
    _validate_child_document_diagnostic_source(row, diagnostic)
    return diagnostic


def _validate_child_document_diagnostic(
    row: ChildBoardRequest,
    diagnostic: Diagnostic,
) -> None:
    if (
        diagnostic.code not in _CHILD_DOCUMENT_DIAGNOSTIC_CODES
        or diagnostic.severity != "error"
    ):
        raise ManufacturingValidationError("invalid_child_document_diagnostic", row.id)
    if diagnostic.affected_ref is UNSET or diagnostic.affected_ref != row.id:
        raise ManufacturingValidationError("diagnostic_owner_mismatch", row.id)


def _validate_child_document_diagnostic_source(
    row: ChildBoardRequest,
    diagnostic: Diagnostic,
) -> None:
    if isinstance(row.source, UnresolvedSource):
        if diagnostic.source is not UNSET:
            raise ManufacturingValidationError("diagnostic_source_mismatch", row.id)
    elif diagnostic.source is UNSET or diagnostic.source != row.source:
        raise ManufacturingValidationError("diagnostic_source_mismatch", row.id)


def _validate_physical_references(
    document: ManufacturingDocument,
    board_ids: set[str],
    layer_ids: set[str],
    net_ids: set[str],
    nets_by_id: dict[str, SourceNet],
    component_ids: set[str],
    components_by_id: dict[str, ComponentOccurrence],
    span_ids: set[str],
    feature_ids: set[str],
) -> None:
    boards_by_id = {row.id: row for row in document.board_occurrences}
    regions_by_id = {row.id: row for row in document.stack_regions}
    layers_by_id = {row.id: row for row in document.layers}
    features_by_id = {row.id: row for row in document.features}
    for row in document.drill_spans:
        _required_ref(row.start_layer_ref, layer_ids, row.id)
        _required_ref(row.end_layer_ref, layer_ids, row.id)
    for row in document.features:
        _optional_ref(row.parent_feature_ref, feature_ids, row.id)
        if isinstance(row, MaterialFeature):
            _validate_material_feature_references(
                row,
                boards_by_id=boards_by_id,
                regions_by_id=regions_by_id,
                layers_by_id=layers_by_id,
                layer_ids=layer_ids,
                net_ids=net_ids,
                nets_by_id=nets_by_id,
                component_ids=component_ids,
                components_by_id=components_by_id,
            )
        elif isinstance(row, HoleFeature):
            _required_ref(row.drill_span_ref, span_ids, row.id)
        elif isinstance(row, ProfileFeature):
            _validate_profile_owner_depth(row, boards_by_id, board_ids)
            _validate_profile_reference(row, features_by_id)


def _validate_material_feature_references(
    feature: MaterialFeature,
    *,
    boards_by_id: dict[str, BoardOccurrence],
    regions_by_id: dict[str, StackRegion],
    layers_by_id: dict[str, LayerInstance],
    layer_ids: set[str],
    net_ids: set[str],
    nets_by_id: dict[str, SourceNet],
    component_ids: set[str],
    components_by_id: dict[str, ComponentOccurrence],
) -> None:
    _required_ref(feature.layer_ref, layer_ids, feature.id)
    layer = layers_by_id[feature.layer_ref]
    region = regions_by_id[layer.stack_region_ref]
    owner = boards_by_id[region.board_occurrence_ref]
    if feature.precision.affine_depth != owner.affine.composition_depth:
        raise ManufacturingValidationError(
            "material_feature_affine_depth_mismatch",
            feature.id,
        )
    _optional_ref(feature.source_net_ref, net_ids, feature.id)
    if feature.source_net_ref is not UNSET:
        net = nets_by_id[feature.source_net_ref]
        if net.board_occurrence_ref != owner.id:
            raise ManufacturingValidationError(
                "source_net_owner_mismatch",
                feature.id,
            )
    _optional_ref(feature.component_occurrence_ref, component_ids, feature.id)
    if feature.component_occurrence_ref is not UNSET:
        component = components_by_id[feature.component_occurrence_ref]
        if component.board_occurrence_ref != owner.id:
            raise ManufacturingValidationError(
                "component_owner_mismatch",
                feature.id,
            )
        if not component.fitted:
            raise ManufacturingValidationError(
                "not_fitted_component_has_geometry",
                feature.id,
            )


def _validate_profile_owner_depth(
    feature: ProfileFeature,
    boards_by_id: dict[str, BoardOccurrence],
    board_ids: set[str],
) -> None:
    _required_ref(feature.board_occurrence_ref, board_ids, feature.id)
    owner = boards_by_id[feature.board_occurrence_ref]
    if feature.precision.affine_depth != owner.affine.composition_depth:
        raise ManufacturingValidationError(
            "profile_affine_depth_mismatch",
            feature.id,
        )


def _validate_profile_reference(
    feature: ProfileFeature,
    features_by_id: dict[str, Feature],
) -> None:
    if feature.operation == "outer":
        if feature.parent_feature_ref is not UNSET:
            raise ManufacturingValidationError("outer_profile_has_parent", feature.id)
        return
    if feature.parent_feature_ref is UNSET:
        raise ManufacturingValidationError("cutout_missing_profile_parent", feature.id)
    parent = features_by_id.get(feature.parent_feature_ref)
    if not isinstance(parent, ProfileFeature) or parent.operation != "outer":
        raise ManufacturingValidationError("invalid_profile_parent", feature.id)
    if parent.board_occurrence_ref != feature.board_occurrence_ref:
        raise ManufacturingValidationError("cutout_profile_owner_mismatch", feature.id)


def _validate_projection_references(
    document: ManufacturingDocument,
    layer_ids: set[str],
    ids: set[str],
) -> None:
    for row in document.projections:
        for reference in row.requested_layer_refs:
            _required_ref(reference, layer_ids, row.id)
        for reference in row.requested_product_refs:
            _required_ref(reference, ids, row.id)


def _validate_geometry(document: ManufacturingDocument) -> None:
    diagnostics = tuple(document.diagnostics)
    for feature in document.features:
        _validate_feature_geometry(
            feature,
            strictness=document.strictness,
            diagnostics=diagnostics,
        )
    _validate_profile_containment(document.features)


def _validate_feature_geometry(
    feature: Feature,
    *,
    strictness: str,
    diagnostics: tuple[Diagnostic, ...],
) -> None:
    if isinstance(feature, ProfileFeature):
        _validate_profile_geometry(feature)
    _validate_precision_bounds(feature)
    degenerate = _validate_geometry_shape(
        feature.geometry,
        owner=feature.id,
        coordinate_error_pm=feature.precision.max_coordinate_error_pm,
    )
    _validate_topology_status(
        feature,
        strictness=strictness,
        degenerate=degenerate,
        diagnostics=diagnostics,
    )


def _validate_profile_geometry(feature: ProfileFeature) -> None:
    if not feature.geometry.closed:
        raise ManufacturingValidationError("open_profile_contour", feature.id)
    clockwise = _path_clockwise(feature.geometry.segments, feature.id)
    if clockwise != feature.clockwise:
        raise ManufacturingValidationError("profile_orientation_mismatch", feature.id)


def _validate_profile_containment(features: list[Feature]) -> None:
    profiles = {
        feature.id: feature
        for feature in features
        if isinstance(feature, ProfileFeature)
    }
    outers_by_owner: dict[str, str] = {}
    for profile in profiles.values():
        if profile.operation != "outer":
            continue
        existing = outers_by_owner.get(profile.board_occurrence_ref)
        if existing is not None:
            raise ManufacturingValidationError(
                "conflicting_outer_profile",
                f"{existing}:{profile.id}",
            )
        outers_by_owner[profile.board_occurrence_ref] = profile.id
    for cutout in profiles.values():
        if cutout.operation != "cutout":
            continue
        parent_ref = cutout.parent_feature_ref
        if not isinstance(parent_ref, str):
            continue
        parent = profiles.get(parent_ref)
        if parent is None:
            continue
        _validate_cutout_containment(parent, cutout)


def _validate_cutout_containment(
    outer: ProfileFeature,
    cutout: ProfileFeature,
) -> None:
    origin = outer.geometry.segments[0].start
    outer_polygon = _containment_polygon(outer, origin.x_nm, origin.y_nm)
    cutout_polygon = _containment_polygon(cutout, origin.x_nm, origin.y_nm)
    if not outer_polygon.is_valid:
        raise ManufacturingValidationError("invalid_outer_profile", outer.id)
    if not cutout_polygon.is_valid:
        raise ManufacturingValidationError("invalid_cutout_profile", cutout.id)
    if outer_polygon.boundary.intersects(cutout_polygon.boundary) or not (
        outer_polygon.contains(cutout_polygon)
    ):
        raise ManufacturingValidationError("cutout_outside_profile", cutout.id)
    clearance_nm = outer_polygon.boundary.distance(cutout_polygon.boundary)
    if Decimal(str(clearance_nm)) <= _containment_uncertainty_nm(outer, cutout):
        raise ManufacturingValidationError(
            "indeterminate_cutout_containment",
            cutout.id,
        )


def _containment_polygon(
    profile: ProfileFeature,
    origin_x_nm: int,
    origin_y_nm: int,
) -> Polygon:
    segments = profile.geometry.segments
    points = [
        _containment_point(
            segments[0].start.x_nm,
            segments[0].start.y_nm,
            origin_x_nm,
            origin_y_nm,
            profile.id,
        )
    ]
    for segment in segments:
        if isinstance(segment, CircularArcSegment):
            points.extend(
                _containment_arc_points(
                    segment,
                    origin_x_nm,
                    origin_y_nm,
                    profile.id,
                )
            )
        else:
            points.append(
                _containment_point(
                    segment.end.x_nm,
                    segment.end.y_nm,
                    origin_x_nm,
                    origin_y_nm,
                    profile.id,
                )
            )
    return Polygon(points)


def _containment_point(
    x_nm: int,
    y_nm: int,
    origin_x_nm: int,
    origin_y_nm: int,
    owner: str,
) -> tuple[float, float]:
    x_local = x_nm - origin_x_nm
    y_local = y_nm - origin_y_nm
    if max(abs(x_local), abs(y_local)) > _MAX_CONTAINMENT_LOCAL_COORDINATE_NM:
        raise ManufacturingValidationError("containment_numeric_range", owner)
    return float(x_local), float(y_local)


def _containment_arc_points(
    segment: CircularArcSegment,
    origin_x_nm: int,
    origin_y_nm: int,
    owner: str,
) -> list[tuple[float, float]]:
    center_x, center_y = _containment_point(
        segment.center.x_nm,
        segment.center.y_nm,
        origin_x_nm,
        origin_y_nm,
        owner,
    )
    start_x, start_y = _containment_point(
        segment.start.x_nm,
        segment.start.y_nm,
        origin_x_nm,
        origin_y_nm,
        owner,
    )
    radius_nm = math.hypot(start_x - center_x, start_y - center_y)
    sweep_radians = math.radians(segment.sweep_degrees_e12 / _ANGLE_SCALE)
    count = _containment_arc_segment_count(radius_nm, sweep_radians, owner)
    direction = -1.0 if segment.clockwise else 1.0
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    points = [
        (
            center_x
            + radius_nm * math.cos(start_angle + direction * sweep_radians * i / count),
            center_y
            + radius_nm * math.sin(start_angle + direction * sweep_radians * i / count),
        )
        for i in range(1, count)
    ]
    points.append(
        _containment_point(
            segment.end.x_nm,
            segment.end.y_nm,
            origin_x_nm,
            origin_y_nm,
            owner,
        )
    )
    return points


def _containment_arc_segment_count(
    radius_nm: float,
    sweep_radians: float,
    owner: str,
) -> int:
    if radius_nm <= _CONTAINMENT_SAGITTA_NM:
        count = max(1, math.ceil(sweep_radians / math.pi))
    else:
        maximum_step = 2.0 * math.acos(
            max(-1.0, 1.0 - _CONTAINMENT_SAGITTA_NM / radius_nm)
        )
        if maximum_step == 0.0:
            raise ManufacturingValidationError("containment_resolution", owner)
        count = max(1, math.ceil(sweep_radians / maximum_step))
    if count > _MAX_CONTAINMENT_ARC_SEGMENTS:
        raise ManufacturingValidationError("containment_resolution", owner)
    return count


def _containment_uncertainty_nm(
    outer: ProfileFeature,
    cutout: ProfileFeature,
) -> Decimal:
    per_axis_source_error_nm = Decimal(
        outer.precision.max_coordinate_error_pm
        + cutout.precision.max_coordinate_error_pm
    ) / Decimal(1_000)
    source_error = per_axis_source_error_nm * _SQRT_TWO_UPPER
    linearization_error = Decimal(str(2 * _CONTAINMENT_SAGITTA_NM))
    return source_error + linearization_error + _CONTAINMENT_NUMERIC_GUARD_NM


def _path_clockwise(segments: list[PathSegment], owner: str) -> bool:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        area_twice = Decimal(0)
        for segment in segments:
            chord_cross = (
                segment.start.x_nm * segment.end.y_nm
                - segment.end.x_nm * segment.start.y_nm
            )
            area_twice += Decimal(chord_cross)
            if not isinstance(segment, CircularArcSegment):
                continue
            signed_sweep = (
                -segment.sweep_degrees_e12
                if segment.clockwise
                else segment.sweep_degrees_e12
            )
            start_x = segment.start.x_nm - segment.center.x_nm
            start_y = segment.start.y_nm - segment.center.y_nm
            radius_squared = start_x * start_x + start_y * start_y
            theta = Decimal(signed_sweep) * _PI / Decimal(180 * _ANGLE_SCALE)
            center_cross = segment.center.x_nm * (
                segment.end.y_nm - segment.start.y_nm
            ) - segment.center.y_nm * (segment.end.x_nm - segment.start.x_nm)
            area_twice += (
                Decimal(center_cross - chord_cross) + Decimal(radius_squared) * theta
            )
    if area_twice == 0:
        raise ManufacturingValidationError("zero_area_profile", owner)
    return area_twice < 0


def _validate_precision_bounds(feature: Feature) -> None:
    if (
        feature.precision.max_coordinate_error_pm < 0
        or feature.precision.max_scalar_error_pm < 0
    ):
        raise ManufacturingValidationError("negative_precision_bound", feature.id)


def _validate_geometry_shape(
    geometry: Geometry,
    *,
    owner: str,
    coordinate_error_pm: int,
) -> bool:
    if isinstance(geometry, CircleGeometry):
        _validate_positive_int64(geometry.radius_nm, owner)
        return False
    elif isinstance(geometry, CapsuleGeometry):
        _validate_positive_int64(geometry.overall_length_nm, owner)
        _validate_positive_int64(geometry.diameter_nm, owner)
        if geometry.overall_length_nm <= geometry.diameter_nm:
            raise ManufacturingValidationError("noncanonical_capsule", owner)
        return False
    elif isinstance(geometry, OrientedRectangleGeometry):
        _validate_positive_int64(geometry.width_nm, owner)
        _validate_positive_int64(geometry.height_nm, owner)
        return False
    elif isinstance(geometry, RoundedRectangleGeometry):
        _validate_positive_int64(geometry.width_nm, owner)
        _validate_positive_int64(geometry.height_nm, owner)
        _validate_positive_int64(geometry.corner_radius_nm, owner)
        if 2 * geometry.corner_radius_nm >= min(geometry.width_nm, geometry.height_nm):
            raise ManufacturingValidationError("noncanonical_rounded_rectangle", owner)
        return False
    elif isinstance(geometry, PathGeometry):
        return _validate_segment_chain(
            geometry.segments,
            geometry.closed,
            owner,
            coordinate_error_pm=coordinate_error_pm,
        )
    elif isinstance(geometry, StrokeGeometry):
        return _validate_stroke_geometry(
            geometry,
            owner,
            coordinate_error_pm,
        )
    elif isinstance(geometry, RegionGeometry):
        return _validate_region_geometry(
            geometry,
            owner=owner,
            coordinate_error_pm=coordinate_error_pm,
        )
    raise TypeError(f"unknown manufacturing geometry for {owner}")


def _validate_positive_int64(value: int, owner: str) -> None:
    if value <= 0:
        raise ManufacturingValidationError("nonpositive_dimension", owner)
    if value > _MAX_INT64:
        raise ManufacturingValidationError("integer_overflow", owner)


def _validate_region_geometry(
    geometry: RegionGeometry,
    *,
    owner: str,
    coordinate_error_pm: int,
) -> bool:
    degenerate = _validate_segment_chain(
        geometry.outer.segments,
        True,
        owner,
        coordinate_error_pm=coordinate_error_pm,
    )
    for hole in geometry.holes:
        degenerate = (
            _validate_segment_chain(
                hole.segments,
                True,
                owner,
                coordinate_error_pm=coordinate_error_pm,
            )
            or degenerate
        )
    return degenerate


def _validate_stroke_geometry(
    geometry: StrokeGeometry,
    owner: str,
    coordinate_error_pm: int,
) -> bool:
    if geometry.width_nm <= 0:
        raise ManufacturingValidationError("nonpositive_stroke", owner)
    return _validate_segment_chain(
        geometry.path.segments,
        geometry.path.closed,
        owner,
        coordinate_error_pm=coordinate_error_pm,
    )


def _validate_affines(document: ManufacturingDocument) -> None:
    for row in document.board_occurrences:
        _validate_affine(row.affine, row.id)
    for row in document.component_occurrences:
        _validate_affine(row.local_to_board_affine, row.id)
        _validate_component_affine(row)
    for feature in document.features:
        geometry = feature.geometry
        if isinstance(
            geometry,
            (CapsuleGeometry, OrientedRectangleGeometry, RoundedRectangleGeometry),
        ):
            _validate_affine(geometry.affine, feature.id)


def _validate_component_affine(row: ComponentOccurrence) -> None:
    affine = row.local_to_board_affine
    if affine.composition_depth != 0:
        raise ManufacturingValidationError("component_affine_depth_mismatch", row.id)
    if affine.tx_nm != row.origin.x_nm or affine.ty_nm != row.origin.y_nm:
        raise ManufacturingValidationError("component_affine_origin_mismatch", row.id)
    expected = rotation_affine_degrees(row.rotation_degrees_e12 / _ANGLE_SCALE)
    expected_coefficients = (
        expected.a_e15,
        expected.b_e15,
        -expected.c_e15 if row.side == "bottom" else expected.c_e15,
        -expected.d_e15 if row.side == "bottom" else expected.d_e15,
    )
    observed_coefficients = (
        affine.a_e15,
        affine.b_e15,
        affine.c_e15,
        affine.d_e15,
    )
    if any(
        observed != wanted
        for observed, wanted in zip(
            observed_coefficients, expected_coefficients, strict=True
        )
    ):
        raise ManufacturingValidationError("component_affine_rotation_mismatch", row.id)


def _validate_affine(affine: PcbDecimalAffine2d, owner: str) -> None:
    scale = 10**15
    tolerance = 3
    if not 0 <= affine.composition_depth <= (1 << 32) - 1:
        raise ManufacturingValidationError("invalid_affine_depth", owner)
    length_x = affine.a_e15 * affine.a_e15 + affine.b_e15 * affine.b_e15
    length_y = affine.c_e15 * affine.c_e15 + affine.d_e15 * affine.d_e15
    dot = affine.a_e15 * affine.c_e15 + affine.b_e15 * affine.d_e15
    determinant = affine.a_e15 * affine.d_e15 - affine.b_e15 * affine.c_e15
    squared = scale * scale
    if abs(length_x - squared) > tolerance * scale:
        raise ManufacturingValidationError("nonrigid_affine", owner)
    if abs(length_y - squared) > tolerance * scale:
        raise ManufacturingValidationError("nonrigid_affine", owner)
    if abs(dot) > tolerance * scale:
        raise ManufacturingValidationError("nonorthogonal_affine", owner)
    if abs(abs(determinant) - squared) > tolerance * scale:
        raise ManufacturingValidationError("invalid_affine_determinant", owner)


def _validate_segment_chain(
    segments: list[PathSegment],
    closed: bool,
    owner: str,
    *,
    coordinate_error_pm: int,
) -> bool:
    degenerate = False
    for segment in segments:
        if isinstance(segment, CircularArcSegment):
            degenerate = (
                _validate_circular_segment(
                    segment,
                    owner=owner,
                    coordinate_error_pm=coordinate_error_pm,
                )
                or degenerate
            )
        elif isinstance(segment, LineSegment) and segment.start == segment.end:
            degenerate = True
    for left, right in zip(segments, segments[1:], strict=False):
        if left.end != right.start:
            raise ManufacturingValidationError("discontinuous_path", owner)
    if closed and segments[-1].end != segments[0].start:
        raise ManufacturingValidationError("open_ring", owner)
    return degenerate


def _validate_circular_segment(
    segment: CircularArcSegment,
    *,
    owner: str,
    coordinate_error_pm: int,
) -> bool:
    if not 0 < segment.sweep_degrees_e12 <= 360 * 10**12:
        raise ManufacturingValidationError("invalid_arc_sweep", owner)
    if segment.start == segment.center or segment.end == segment.center:
        raise ManufacturingValidationError("nonpositive_arc_radius", owner)
    start_x = segment.start.x_nm - segment.center.x_nm
    start_y = segment.start.y_nm - segment.center.y_nm
    end_x = segment.end.x_nm - segment.center.x_nm
    end_y = segment.end.y_nm - segment.center.y_nm
    tolerance_nm = _arc_tolerance_nm(coordinate_error_pm)
    if not _radii_agree(start_x, start_y, end_x, end_y, tolerance_nm):
        raise ManufacturingValidationError("inconsistent_arc_radius", owner)
    if not _sweep_agrees(
        start_x,
        start_y,
        end_x,
        end_y,
        sweep_degrees_e12=segment.sweep_degrees_e12,
        clockwise=segment.clockwise,
        tolerance_nm=tolerance_nm,
    ):
        raise ManufacturingValidationError("inconsistent_arc_sweep", owner)
    return segment.start == segment.end


def _arc_tolerance_nm(coordinate_error_pm: int) -> Decimal:
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        return Decimal(4 * coordinate_error_pm) * _SQRT_TWO_LOWER / Decimal(1_000)


def _radii_agree(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    tolerance_nm: Decimal,
) -> bool:
    start_squared = start_x * start_x + start_y * start_y
    end_squared = end_x * end_x + end_y * end_y
    if start_squared == end_squared:
        return True
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        difference = abs(Decimal(start_squared).sqrt() - Decimal(end_squared).sqrt())
    return difference + _NUMERIC_ERROR_NM <= tolerance_nm


def _sweep_agrees(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    sweep_degrees_e12: int,
    clockwise: bool,
    tolerance_nm: Decimal,
) -> bool:
    signed_sweep = -sweep_degrees_e12 if clockwise else sweep_degrees_e12
    if signed_sweep % _RIGHT_ANGLE_E12 == 0:
        expected_x, expected_y = _rotate_cardinal(start_x, start_y, signed_sweep)
        return _integer_distance_within(
            end_x - expected_x,
            end_y - expected_y,
            tolerance_nm,
        )
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        sine, cosine = _decimal_sine_cosine(signed_sweep)
        expected_x = Decimal(start_x) * cosine - Decimal(start_y) * sine
        expected_y = Decimal(start_x) * sine + Decimal(start_y) * cosine
        residual = (
            (Decimal(end_x) - expected_x) ** 2 + (Decimal(end_y) - expected_y) ** 2
        ).sqrt()
    return residual + _NUMERIC_ERROR_NM <= tolerance_nm


def _rotate_cardinal(x_value: int, y_value: int, signed_sweep: int) -> tuple[int, int]:
    quarter_turns = (signed_sweep // _RIGHT_ANGLE_E12) % 4
    rotations = (
        (x_value, y_value),
        (-y_value, x_value),
        (-x_value, -y_value),
        (y_value, -x_value),
    )
    return rotations[quarter_turns]


def _integer_distance_within(
    x_delta: int,
    y_delta: int,
    tolerance_nm: Decimal,
) -> bool:
    squared_distance = x_delta * x_delta + y_delta * y_delta
    if squared_distance == 0:
        return True
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        distance = Decimal(squared_distance).sqrt()
    return distance + _NUMERIC_ERROR_NM <= tolerance_nm


@lru_cache(maxsize=4_096)
def _decimal_sine_cosine(signed_sweep: int) -> tuple[Decimal, Decimal]:
    normalized = signed_sweep % _FULL_ANGLE_E12
    quadrant, remainder = divmod(normalized, _RIGHT_ANGLE_E12)
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        radians = Decimal(remainder) * _PI / Decimal(180 * _ANGLE_SCALE)
        sine, cosine = _taylor_sine_cosine(radians)
        variants = (
            (sine, cosine),
            (cosine, -sine),
            (-sine, -cosine),
            (-cosine, sine),
        )
        return variants[quadrant]


def _taylor_sine_cosine(radians: Decimal) -> tuple[Decimal, Decimal]:
    squared = radians * radians
    sine_term = radians
    cosine_term = Decimal(1)
    sine = sine_term
    cosine = cosine_term
    index = 1
    while True:
        sine_term *= -squared / Decimal((2 * index) * (2 * index + 1))
        cosine_term *= -squared / Decimal((2 * index - 1) * (2 * index))
        sine += sine_term
        cosine += cosine_term
        if max(abs(sine_term), abs(cosine_term)) < _TRIG_SERIES_EPSILON:
            return sine, cosine
        index += 1


def _validate_topology_status(
    feature: Feature,
    *,
    strictness: str,
    degenerate: bool,
    diagnostics: tuple[Diagnostic, ...],
) -> None:
    status = feature.precision.topology_status
    matching = [
        row
        for row in diagnostics
        if row.code == "topology_degeneracy" and row.affected_ref == feature.id
    ]
    if status == "stable":
        _validate_stable_topology(feature.id, degenerate, matching)
        return
    _validate_unfused_topology(
        feature.id,
        strictness=strictness,
        degenerate=degenerate,
        matching=matching,
    )


def _validate_stable_topology(
    owner: str,
    degenerate: bool,
    matching: list[Diagnostic],
) -> None:
    if degenerate:
        raise ManufacturingValidationError("unmarked_topology_degeneracy", owner)
    if matching:
        raise ManufacturingValidationError("contradictory_topology_diagnostic", owner)


def _validate_unfused_topology(
    owner: str,
    *,
    strictness: str,
    degenerate: bool,
    matching: list[Diagnostic],
) -> None:
    if strictness != "permissive":
        raise ManufacturingValidationError(
            "unfused_degeneracy_in_strict_mode",
            owner,
        )
    if not degenerate:
        raise ManufacturingValidationError("spurious_topology_degeneracy", owner)
    if len(matching) != 1 or matching[0].severity != "warning":
        raise ManufacturingValidationError(
            "missing_topology_degeneracy_diagnostic",
            owner,
        )


def _required_ref(reference: str, targets: set[str], owner: str) -> None:
    if reference not in targets:
        raise ManufacturingValidationError("unknown_reference", f"{owner}:{reference}")


def _optional_ref(reference: str | UnsetType, targets: set[str], owner: str) -> None:
    if reference is not UNSET:
        _required_ref(reference, targets, owner)
