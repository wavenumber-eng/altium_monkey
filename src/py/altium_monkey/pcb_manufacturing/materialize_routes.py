"""Materialize stored PCB tracks and arcs into the normalized contract."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import TYPE_CHECKING, Literal

from msgspec import UNSET

from altium_monkey.altium_resolved_layer_stack import ResolvedLayerStack

from .affine import identity_affine, rotation_affine_degrees
from .generated import (
    BoardOccurrence,
    CapsuleGeometry,
    CircularArcSegment,
    CircleGeometry,
    ComponentOccurrence,
    Diagnostic,
    DrillSpan,
    Feature,
    Geometry,
    HoleFeature,
    LayerInstance,
    LineSegment,
    ManufacturingDocument,
    MaterialFeature,
    OrientedRectangleGeometry,
    PathGeometry,
    PathSegment,
    Point2d,
    PrecisionEnvelope,
    ProfileFeature,
    RoundedRectangleGeometry,
    SourceNet,
    SourceProvenance,
    StackRegion,
    StrokeGeometry,
    UnresolvedSource,
    VariantSelection,
)
from .output_layers import (
    ManufacturingLayerOccurrence,
    walk_manufacturing_output_layers,
)
from .resolved_inputs import (
    ResolvedArcInput,
    ResolvedComponentOccurrenceInput,
    ResolvedFillInput,
    ResolvedLayerBinding,
    ResolvedPcbInputs,
    ResolvedPadInput,
    ResolvedPadLandInput,
    ResolvedProfileInput,
    ResolvedProfileVertex,
    ResolvedTrackInput,
    ResolvedViaInput,
    resolve_pcb_stored_inputs,
)
from .source_provenance import PcbDocSourceIndex
from .units import (
    PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
    _round_ratio_ties_even,
    pcb_internal_to_nm,
)
from .validation import (
    ManufacturingValidationError,
    _path_clockwise,
    validate_manufacturing_document,
)
from .variant_selection import (
    PcbManufacturingVariantSelection,
    PcbManufacturingVariantSelectionError,
)

if TYPE_CHECKING:
    from altium_monkey.altium_pcbdoc import AltiumPcbDoc

Strictness = Literal["strict", "permissive"]
ARC_SWEEP_SCALE = 10**12
_MAX_INT64 = (1 << 63) - 1


@dataclass(frozen=True)
class PcbMaterializationError(ValueError):
    """One stable failure while lowering resolved PCB inputs to geometry."""

    code: str
    affected_ref: str


def materialize_stored_routes(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None = None,
    generator_revision: str = "pcb-manufacturing-python.a0",
) -> ManufacturingDocument:
    """Materialize direct stored tracks and arcs without adapter semantics."""

    return _materialize_stored(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        variant_selection=variant_selection,
        generator_revision=generator_revision,
        include_routes=True,
        include_physical_features=False,
        include_profile=False,
    )


def materialize_stored_features(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None = None,
    generator_revision: str = "pcb-manufacturing-python.a0",
) -> ManufacturingDocument:
    """Materialize accepted direct stored routes, pad lands, and round holes."""

    return _materialize_stored(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        variant_selection=variant_selection,
        generator_revision=generator_revision,
        include_routes=True,
        include_physical_features=True,
        include_profile=False,
    )


def materialize_stored_walking_slice(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None = None,
    generator_revision: str = "pcb-manufacturing-python.a0",
) -> ManufacturingDocument:
    """Materialize the accepted profile, route, land, and hole walking slice."""

    return _materialize_stored(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        variant_selection=variant_selection,
        generator_revision=generator_revision,
        include_routes=True,
        include_physical_features=True,
        include_profile=True,
    )


def materialize_stored_profile(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None = None,
    generator_revision: str = "pcb-manufacturing-python.a0",
) -> ManufacturingDocument:
    """Materialize the authoritative analytic board profile and cutouts."""

    return _materialize_stored(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        variant_selection=variant_selection,
        generator_revision=generator_revision,
        include_routes=False,
        include_physical_features=False,
        include_profile=True,
    )


def _materialize_stored(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None,
    generator_revision: str,
    include_routes: bool,
    include_physical_features: bool,
    include_profile: bool,
) -> ManufacturingDocument:
    board = getattr(pcbdoc, "board", None)
    if board is None:
        raise ValueError("manufacturing materialization requires Board6/Data")
    if variant_selection is not None:
        variant_selection.assert_applies_to(source_index)
    resolved = resolve_pcb_stored_inputs(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        include_routes=include_routes,
        include_physical_features=include_physical_features,
        include_profile=include_profile,
    )
    if include_profile:
        if resolved.profile is None:
            raise PcbMaterializationError("missing_board_profile", "board.root")
        board_source = resolved.profile.source
        board_diagnostics: tuple[Diagnostic, ...] = ()
    else:
        board_resolution = source_index.resolve(
            board,
            strictness=strictness,
            affected_ref="board.root",
        )
        board_source = board_resolution.source
        board_diagnostics = board_resolution.diagnostics
    walked_layers = (
        walk_manufacturing_output_layers(layer_stack)
        if include_routes or include_physical_features
        else ()
    )
    document = _route_document(
        resolved,
        walked_layers=walked_layers,
        board_source=board_source,
        board_diagnostics=board_diagnostics,
        strictness=strictness,
        variant_selection=variant_selection,
        generator_revision=generator_revision,
        include_routes=include_routes,
        include_physical_features=include_physical_features,
        include_profile=include_profile,
    )
    validate_manufacturing_document(document)
    return document


def _materialize_profile_rows(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    board_occurrence_ref: str,
    feature_id_prefix: str,
    affine_depth: int,
) -> tuple[tuple[ProfileFeature, ...], tuple[Diagnostic, ...]]:
    resolved = resolve_pcb_stored_inputs(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        include_routes=False,
        include_physical_features=False,
        include_profile=True,
    )
    if resolved.profile is None:
        raise PcbMaterializationError("missing_board_profile", board_occurrence_ref)
    diagnostics = list(resolved.diagnostics)
    rows = _profile_features(
        resolved,
        diagnostics=diagnostics,
        board_occurrence_ref=board_occurrence_ref,
        feature_id_prefix=feature_id_prefix,
        affine_depth=affine_depth,
    )
    return tuple(rows), tuple(diagnostics)


def _materialize_route_rows(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    id_prefix: str,
    board_occurrence_ref: str,
    affine_depth: int,
    variant_selection: PcbManufacturingVariantSelection | None,
) -> tuple[tuple[SourceNet, ...], tuple[MaterialFeature, ...], tuple[Diagnostic, ...]]:
    resolved = resolve_pcb_stored_inputs(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        include_routes=True,
        include_physical_features=False,
        include_profile=False,
    )
    diagnostics = list(resolved.diagnostics)
    fitted_components = (
        _component_fitted_map(resolved.components, variant_selection)
        if variant_selection is not None
        else None
    )
    if variant_selection is not None:
        component_diagnostic_refs = {
            component.source.diagnostic_ref
            for component in resolved.components
            if isinstance(component.source, UnresolvedSource)
        }
        diagnostics = [
            row for row in diagnostics if row.id not in component_diagnostic_refs
        ]
    walked_layers = walk_manufacturing_output_layers(layer_stack)
    nets = tuple(
        SourceNet(
            id=f"{id_prefix}{net.id}",
            board_occurrence_ref=board_occurrence_ref,
            source=_promote_unresolved_source(
                f"{id_prefix}{net.id}",
                net.source,
                diagnostics,
            ),
            display_name=net.display_name,
        )
        for net in resolved.nets
    )
    features = _route_features(
        resolved,
        walked_layers=walked_layers,
        strictness=strictness,
        diagnostics=diagnostics,
        fitted_components=fitted_components,
        id_prefix=id_prefix,
        affine_depth=affine_depth,
    )
    return nets, tuple(features), tuple(diagnostics)


def _materialize_physical_rows(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    id_prefix: str,
    board_occurrence_ref: str,
    affine_depth: int,
    variant_selection: PcbManufacturingVariantSelection | None,
) -> tuple[
    tuple[SourceNet, ...],
    tuple[DrillSpan, ...],
    tuple[Feature, ...],
    tuple[Diagnostic, ...],
]:
    resolved = resolve_pcb_stored_inputs(
        pcbdoc,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        include_routes=False,
        include_physical_features=True,
        include_profile=False,
    )
    diagnostics = list(resolved.diagnostics)
    fitted_components = (
        _component_fitted_map(resolved.components, variant_selection)
        if variant_selection is not None
        else None
    )
    if variant_selection is not None:
        component_diagnostic_refs = {
            component.source.diagnostic_ref
            for component in resolved.components
            if isinstance(component.source, UnresolvedSource)
        }
        diagnostics = [
            row for row in diagnostics if row.id not in component_diagnostic_refs
        ]
    walked_layers = walk_manufacturing_output_layers(layer_stack)
    nets = tuple(
        SourceNet(
            id=f"{id_prefix}{net.id}",
            board_occurrence_ref=board_occurrence_ref,
            source=_promote_unresolved_source(
                f"{id_prefix}{net.id}",
                net.source,
                diagnostics,
            ),
            display_name=net.display_name,
        )
        for net in resolved.nets
    )
    features, drill_spans = _physical_features(
        resolved,
        walked_layers=walked_layers,
        diagnostics=diagnostics,
        fitted_components=fitted_components,
        id_prefix=id_prefix,
        affine_depth=affine_depth,
    )
    return nets, tuple(drill_spans), tuple(features), tuple(diagnostics)


def _route_document(
    resolved: ResolvedPcbInputs,
    *,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    board_source: SourceProvenance,
    board_diagnostics: tuple[Diagnostic, ...],
    strictness: Strictness,
    variant_selection: PcbManufacturingVariantSelection | None,
    generator_revision: str,
    include_routes: bool,
    include_physical_features: bool,
    include_profile: bool,
) -> ManufacturingDocument:
    diagnostics = list(board_diagnostics)
    diagnostics.extend(resolved.diagnostics)
    stack_regions = _stack_regions(walked_layers, board_source, diagnostics)
    layers = [_layer_instance(row, stack_regions) for row in walked_layers]
    variant_rows, component_rows, fitted_components = _component_rows(
        resolved.components,
        variant_selection=variant_selection,
        diagnostics=diagnostics,
    )
    features: list[Feature] = (
        list(
            _route_features(
                resolved,
                walked_layers=walked_layers,
                strictness=strictness,
                diagnostics=diagnostics,
                fitted_components=fitted_components,
            )
        )
        if include_routes
        else []
    )
    drill_spans: list[DrillSpan] = []
    if include_physical_features:
        physical_features, drill_spans = _physical_features(
            resolved,
            walked_layers=walked_layers,
            diagnostics=diagnostics,
            fitted_components=fitted_components,
        )
        features.extend(physical_features)
    if include_profile:
        features.extend(_profile_features(resolved, diagnostics=diagnostics))
    return ManufacturingDocument(
        type="altium_monkey.pcb.manufacturing_materialization",
        version="a0",
        generator_revision=generator_revision,
        strictness=strictness,
        board_occurrences=[
            BoardOccurrence(
                id="board.root",
                source=board_source,
                affine=identity_affine(),
            )
        ],
        child_board_requests=[],
        stack_regions=stack_regions,
        layers=layers,
        nets=[
            SourceNet(
                id=net.id,
                board_occurrence_ref="board.root",
                source=net.source,
                display_name=net.display_name,
            )
            for net in resolved.nets
        ],
        variant_selections=variant_rows,
        component_occurrences=component_rows,
        drill_spans=drill_spans,
        features=features,
        projections=[],
        diagnostics=diagnostics,
    )


def _component_rows(
    components: tuple[ResolvedComponentOccurrenceInput, ...],
    *,
    variant_selection: PcbManufacturingVariantSelection | None,
    diagnostics: list[Diagnostic],
    board_occurrence_ref: str = "board.root",
    id_prefix: str = "",
) -> tuple[list[VariantSelection], list[ComponentOccurrence], dict[str, bool] | None]:
    if variant_selection is None:
        return [], [], None
    fitted = _component_fitted_map(components, variant_selection)
    selection_ref = f"{id_prefix}{variant_selection.id}"
    variant_row = VariantSelection(
        id=selection_ref,
        board_occurrence_ref=board_occurrence_ref,
        source=variant_selection.source,
        kind=variant_selection.kind,
        display_name=variant_selection.display_name,
        project_variant_unique_id=(
            variant_selection.project_variant_unique_id
            if variant_selection.project_variant_unique_id is not None
            else UNSET
        ),
    )
    rows: list[ComponentOccurrence] = []
    for component in components:
        source_unique_id = component.source_component_unique_id
        decision = variant_selection.decision_for(source_unique_id)
        rows.append(
            _component_occurrence_row(
                component,
                board_occurrence_ref=board_occurrence_ref,
                occurrence_ref=f"{id_prefix}{component.id}",
                selection_ref=selection_ref,
                fitted=fitted[component.id],
                variation_kind=decision.kind if decision is not None else None,
                variation_source=decision.source if decision is not None else None,
                diagnostics=diagnostics,
            )
        )
    return [variant_row], rows, fitted


def _component_fitted_map(
    components: tuple[ResolvedComponentOccurrenceInput, ...],
    variant_selection: PcbManufacturingVariantSelection,
) -> dict[str, bool]:
    source_ids: set[str] = set()
    fitted: dict[str, bool] = {}
    for component in components:
        source_unique_id = component.source_component_unique_id
        if not source_unique_id:
            if variant_selection.kind == "project_variant":
                raise PcbManufacturingVariantSelectionError(
                    "unresolved_component_identity",
                    "named variant selection requires component source_unique_id",
                )
            fitted[component.id] = True
            continue
        decision = variant_selection.decision_for(source_unique_id)
        fitted[component.id] = decision.fitted if decision is not None else True
        if source_unique_id in source_ids:
            raise PcbManufacturingVariantSelectionError(
                "corrupt_identity",
                f"duplicate component source_unique_id {source_unique_id!r}",
            )
        source_ids.add(source_unique_id)
    orphan_ids = {
        decision.source_component_unique_id
        for decision in variant_selection.decisions
        if decision.source_component_unique_id not in source_ids
    }
    if orphan_ids:
        raise PcbManufacturingVariantSelectionError(
            "orphan_component_variation",
            f"variant rows do not join the selected board: {sorted(orphan_ids)!r}",
        )
    return fitted


def _component_occurrence_row(
    component: ResolvedComponentOccurrenceInput,
    *,
    board_occurrence_ref: str,
    occurrence_ref: str,
    selection_ref: str,
    fitted: bool,
    variation_kind: Literal[0, 1] | None,
    variation_source: SourceProvenance | None,
    diagnostics: list[Diagnostic],
) -> ComponentOccurrence:
    return ComponentOccurrence(
        id=occurrence_ref,
        board_occurrence_ref=board_occurrence_ref,
        source=_promote_unresolved_source(
            occurrence_ref,
            component.source,
            diagnostics,
        ),
        source_component_unique_id=(
            component.source_component_unique_id
            if component.source_component_unique_id
            else UNSET
        ),
        display_designator=component.display_designator,
        footprint=component.footprint,
        side=component.side,
        origin=Point2d(
            x_nm=pcb_internal_to_nm(component.origin_x_source_units.selected_value),
            y_nm=pcb_internal_to_nm(component.origin_y_source_units.selected_value),
        ),
        rotation_degrees_e12=_degrees_e12(
            component.rotation_degrees.selected_value,
            component.id,
        ),
        local_to_board_affine=component.local_to_board_affine,
        fitted=fitted,
        fitted_selection_ref=selection_ref,
        variation_kind=variation_kind if variation_kind is not None else UNSET,
        variation_source=(variation_source if variation_source is not None else UNSET),
    )


def _degrees_e12(value: float, affected_ref: str) -> int:
    scaled = Decimal(str(value)) * ARC_SWEEP_SCALE
    if not scaled.is_finite():
        raise PcbMaterializationError("nonfinite_component_rotation", affected_ref)
    result = int(scaled.to_integral_value(rounding=ROUND_HALF_EVEN))
    if not -(2**63) <= result < 2**63:
        raise PcbMaterializationError("component_rotation_overflow", affected_ref)
    return result


def _route_features(
    resolved: ResolvedPcbInputs,
    *,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    strictness: Strictness,
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None = None,
    id_prefix: str = "",
    affine_depth: int = 0,
) -> list[MaterialFeature]:
    features: list[MaterialFeature] = []
    for track in resolved.tracks:
        feature = _track_feature(
            track,
            walked_layers=walked_layers,
            strictness=strictness,
            diagnostics=diagnostics,
            fitted_components=fitted_components,
            id_prefix=id_prefix,
            affine_depth=affine_depth,
        )
        if feature is not None:
            features.append(feature)
    for arc in resolved.arcs:
        feature = _arc_feature(
            arc,
            walked_layers=walked_layers,
            strictness=strictness,
            diagnostics=diagnostics,
            fitted_components=fitted_components,
            id_prefix=id_prefix,
            affine_depth=affine_depth,
        )
        if feature is not None:
            features.append(feature)
    return features


def _physical_features(
    resolved: ResolvedPcbInputs,
    *,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None = None,
    id_prefix: str = "",
    affine_depth: int = 0,
) -> tuple[list[Feature], list[DrillSpan]]:
    pad_features, drill_spans = _pad_features(
        resolved.pads,
        walked_layers,
        diagnostics,
        fitted_components,
        id_prefix=id_prefix,
        affine_depth=affine_depth,
    )
    via_features, via_spans = _via_features(
        resolved.vias,
        walked_layers,
        diagnostics,
        fitted_components,
        id_prefix=id_prefix,
        affine_depth=affine_depth,
    )
    return (
        [
            *pad_features,
            *via_features,
            *_fill_features(
                resolved.fills,
                walked_layers,
                diagnostics,
                fitted_components,
                id_prefix=id_prefix,
                affine_depth=affine_depth,
            ),
        ],
        [*drill_spans, *via_spans],
    )


def _profile_features(
    resolved: ResolvedPcbInputs,
    *,
    diagnostics: list[Diagnostic],
    board_occurrence_ref: str = "board.root",
    feature_id_prefix: str = "",
    affine_depth: int = 0,
) -> list[ProfileFeature]:
    if resolved.profile is None:
        raise PcbMaterializationError("missing_board_profile", "board.root")
    outer = _profile_feature(
        resolved.profile,
        diagnostics=diagnostics,
        board_occurrence_ref=board_occurrence_ref,
        feature_id_prefix=feature_id_prefix,
        affine_depth=affine_depth,
    )
    return [
        outer,
        *(
            _profile_feature(
                cutout,
                parent_feature_ref=outer.id,
                diagnostics=diagnostics,
                board_occurrence_ref=board_occurrence_ref,
                feature_id_prefix=feature_id_prefix,
                affine_depth=affine_depth,
            )
            for cutout in resolved.cutouts
        ),
    ]


def _profile_feature(
    profile: ResolvedProfileInput,
    *,
    diagnostics: list[Diagnostic],
    board_occurrence_ref: str,
    feature_id_prefix: str,
    affine_depth: int,
    parent_feature_ref: str | None = None,
) -> ProfileFeature:
    feature_id = f"{feature_id_prefix}feature.{profile.id}"
    segments = _profile_segments(profile.vertices)
    clockwise = _profile_clockwise(segments, feature_id)
    return ProfileFeature(
        id=feature_id,
        parent_feature_ref=(
            parent_feature_ref if parent_feature_ref is not None else UNSET
        ),
        board_occurrence_ref=board_occurrence_ref,
        source=_promote_unresolved_source(
            feature_id,
            profile.source,
            diagnostics,
            preserve_original_diagnostic=profile.operation == "outer",
        ),
        operation=profile.operation,
        clockwise=clockwise,
        precision=PrecisionEnvelope(
            max_coordinate_error_pm=(
                1_500 if any(vertex.is_arc for vertex in profile.vertices) else 500
            ),
            max_scalar_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
            affine_depth=affine_depth,
            topology_status="stable",
        ),
        geometry=PathGeometry(closed=True, segments=segments),
    )


def _profile_segments(
    vertices: tuple[ResolvedProfileVertex, ...],
) -> list[PathSegment]:
    points = [
        Point2d(
            x_nm=pcb_internal_to_nm(vertex.x_source_units),
            y_nm=pcb_internal_to_nm(vertex.y_source_units),
        )
        for vertex in vertices
    ]
    segments: list[PathSegment] = []
    for index, vertex in enumerate(vertices):
        start = points[index]
        end = points[(index + 1) % len(points)]
        if not vertex.is_arc:
            segments.append(LineSegment(start=start, end=end))
            continue
        center = Point2d(
            x_nm=pcb_internal_to_nm(vertex.center_x_source_units),
            y_nm=pcb_internal_to_nm(vertex.center_y_source_units),
        )
        if vertex.sweep_degrees_e12 == 360 * ARC_SWEEP_SCALE and start == end:
            opposite = Point2d(
                x_nm=2 * center.x_nm - start.x_nm,
                y_nm=2 * center.y_nm - start.y_nm,
            )
            segments.extend(
                (
                    CircularArcSegment(
                        start=start,
                        end=opposite,
                        center=center,
                        clockwise=vertex.clockwise,
                        sweep_degrees_e12=180 * ARC_SWEEP_SCALE,
                    ),
                    CircularArcSegment(
                        start=opposite,
                        end=end,
                        center=center,
                        clockwise=vertex.clockwise,
                        sweep_degrees_e12=180 * ARC_SWEEP_SCALE,
                    ),
                )
            )
            continue
        segments.append(
            CircularArcSegment(
                start=start,
                end=end,
                center=center,
                clockwise=vertex.clockwise,
                sweep_degrees_e12=vertex.sweep_degrees_e12,
            )
        )
    return segments


def _profile_clockwise(segments: list[PathSegment], affected_ref: str) -> bool:
    try:
        return _path_clockwise(segments, affected_ref)
    except ManufacturingValidationError as exc:
        if exc.code == "zero_area_profile":
            raise PcbMaterializationError("topology_degeneracy", affected_ref) from exc
        raise


def _stack_regions(
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    board_source: SourceProvenance,
    diagnostics: list[Diagnostic],
) -> list[StackRegion]:
    context_refs = tuple(dict.fromkeys(row.source_stackup_ref for row in walked_layers))
    return [
        StackRegion(
            id=_stack_region_ref(context_ref),
            board_occurrence_ref="board.root",
            source=_promote_unresolved_source(
                _stack_region_ref(context_ref),
                board_source,
                diagnostics,
                preserve_original_diagnostic=True,
            ),
        )
        for context_ref in context_refs
    ]


def _stack_region_ref(source_stackup_ref: str) -> str:
    return f"stack_region.{source_stackup_ref or 'whole_board'}"


def _layer_instance(
    occurrence: ManufacturingLayerOccurrence,
    stack_regions: list[StackRegion],
) -> LayerInstance:
    stack_region_ref = _stack_region_ref(occurrence.source_stackup_ref)
    if all(row.id != stack_region_ref for row in stack_regions):
        raise ValueError(f"unknown stack region for {occurrence.id}")
    layer_ref = occurrence.layer.layer_ref
    if layer_ref is None:
        raise ValueError(f"{occurrence.id} has no exact layer ref")
    return LayerInstance(
        id=occurrence.id,
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


def _track_feature(
    track: ResolvedTrackInput,
    *,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    strictness: Strictness,
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None = None,
    id_prefix: str = "",
    affine_depth: int = 0,
) -> MaterialFeature | None:
    include, component_ref = _component_owner(
        track.component_occurrence_ref,
        fitted_components=fitted_components,
        affected_ref=track.id,
        reference_prefix=id_prefix,
    )
    if not include:
        return None
    _reject_classified_route(
        track.id,
        is_keepout=track.is_keepout,
        is_polygon_outline=track.is_polygon_outline,
        polygon_index=track.polygon_index,
    )
    layer = _feature_layer(track.layer, walked_layers)
    if layer is None:
        return None
    start = Point2d(
        x_nm=pcb_internal_to_nm(track.start_x_source_units.selected_value),
        y_nm=pcb_internal_to_nm(track.start_y_source_units.selected_value),
    )
    end = Point2d(
        x_nm=pcb_internal_to_nm(track.end_x_source_units.selected_value),
        y_nm=pcb_internal_to_nm(track.end_y_source_units.selected_value),
    )
    feature_id = f"{id_prefix}feature.{track.id}"
    topology_status = _topology_status(
        feature_id,
        degenerate=start == end,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return _material_feature(
        feature_id=feature_id,
        source=track.source,
        layer=layer,
        source_net_ref=track.source_net_ref,
        component_occurrence_ref=component_ref,
        reference_prefix=id_prefix,
        feature_kind="route",
        geometry=StrokeGeometry(
            path=PathGeometry(
                closed=False,
                segments=[LineSegment(start=start, end=end)],
            ),
            width_nm=pcb_internal_to_nm(track.width_source_units.selected_value),
        ),
        coordinate_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
        topology_status=topology_status,
        affine_depth=affine_depth,
        diagnostics=diagnostics,
    )


def _arc_feature(
    arc: ResolvedArcInput,
    *,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    strictness: Strictness,
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None = None,
    id_prefix: str = "",
    affine_depth: int = 0,
) -> MaterialFeature | None:
    include, component_ref = _component_owner(
        arc.component_occurrence_ref,
        fitted_components=fitted_components,
        affected_ref=arc.id,
        reference_prefix=id_prefix,
    )
    if not include:
        return None
    _reject_classified_route(
        arc.id,
        is_keepout=arc.is_keepout,
        is_polygon_outline=arc.is_polygon_outline,
        polygon_index=arc.polygon_index,
    )
    layer = _feature_layer(arc.layer, walked_layers)
    if layer is None:
        return None
    center = Point2d(
        x_nm=pcb_internal_to_nm(arc.center_x_source_units.selected_value),
        y_nm=pcb_internal_to_nm(arc.center_y_source_units.selected_value),
    )
    radius_nm = pcb_internal_to_nm(arc.radius_source_units.selected_value)
    if arc.radius_source_units.selected_value <= 0 or radius_nm <= 0:
        raise PcbMaterializationError("nonpositive_radius", arc.id)
    start_angle = arc.start_angle_degrees.selected_value
    end_angle = arc.end_angle_degrees.selected_value
    feature_id = f"{id_prefix}feature.{arc.id}"
    segments, closed = _arc_segments(
        center,
        radius_nm,
        start_angle,
        end_angle,
        affected_ref=feature_id,
    )
    topology_status = _topology_status(
        feature_id,
        degenerate=any(_circular_segment_is_degenerate(row) for row in segments),
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return _material_feature(
        feature_id=feature_id,
        source=arc.source,
        layer=layer,
        source_net_ref=arc.source_net_ref,
        component_occurrence_ref=component_ref,
        reference_prefix=id_prefix,
        feature_kind="route",
        geometry=StrokeGeometry(
            path=PathGeometry(
                closed=closed,
                segments=segments,
            ),
            width_nm=pcb_internal_to_nm(arc.width_source_units.selected_value),
        ),
        coordinate_error_pm=1_500,
        topology_status=topology_status,
        affine_depth=affine_depth,
        diagnostics=diagnostics,
    )


def _reject_classified_route(
    affected_ref: str,
    *,
    is_keepout: bool,
    is_polygon_outline: bool,
    polygon_index: int,
) -> None:
    if is_keepout or is_polygon_outline or polygon_index != 0xFFFF:
        raise PcbMaterializationError("classified_route_geometry_pending", affected_ref)


def _promote_unresolved_source(
    owner_ref: str,
    source: SourceProvenance,
    diagnostics: list[Diagnostic],
    *,
    preserve_original_diagnostic: bool = False,
) -> SourceProvenance:
    if not isinstance(source, UnresolvedSource):
        return source
    if not preserve_original_diagnostic:
        diagnostics[:] = [
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.id != source.diagnostic_ref
        ]
    diagnostic_ref = f"diagnostic.{owner_ref}.unresolved_source"
    diagnostics.append(
        Diagnostic(
            id=diagnostic_ref,
            code="unresolved_source",
            severity="warning",
            message="Source provenance is degraded; physical geometry is retained.",
            affected_ref=owner_ref,
        )
    )
    return UnresolvedSource(diagnostic_ref=diagnostic_ref, reason=source.reason)


def _topology_status(
    owner_ref: str,
    *,
    degenerate: bool,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> Literal["stable", "unfused_degeneracy"]:
    if not degenerate:
        return "stable"
    if strictness == "strict":
        raise PcbMaterializationError("topology_degeneracy", owner_ref)
    diagnostics.append(
        Diagnostic(
            id=f"diagnostic.{owner_ref}.topology_degeneracy",
            code="topology_degeneracy",
            severity="warning",
            message=(
                "Nanometer quantization collapsed analytic route points; "
                "source sweep is retained and geometry remains unfused."
            ),
            affected_ref=owner_ref,
        )
    )
    return "unfused_degeneracy"


def _arc_segments(
    center: Point2d,
    radius_nm: int,
    start_angle: float,
    end_angle: float,
    *,
    affected_ref: str,
) -> tuple[list[PathSegment], bool]:
    sweep_degrees_e12, closed = _arc_sweep_degrees_e12(
        start_angle,
        end_angle,
        affected_ref=affected_ref,
    )
    start = _arc_point(center, radius_nm, start_angle)
    if not closed:
        end = _arc_point(center, radius_nm, end_angle)
        return [
            CircularArcSegment(
                start=start,
                end=end,
                center=center,
                clockwise=False,
                sweep_degrees_e12=sweep_degrees_e12,
            )
        ], False
    opposite = _arc_point(center, radius_nm, start_angle + 180.0)
    return [
        CircularArcSegment(
            start=start,
            end=opposite,
            center=center,
            clockwise=False,
            sweep_degrees_e12=180 * ARC_SWEEP_SCALE,
        ),
        CircularArcSegment(
            start=opposite,
            end=start,
            center=center,
            clockwise=False,
            sweep_degrees_e12=180 * ARC_SWEEP_SCALE,
        ),
    ], True


def _arc_sweep_degrees_e12(
    start_angle: float,
    end_angle: float,
    *,
    affected_ref: str,
) -> tuple[int, bool]:
    if not math.isfinite(start_angle) or not math.isfinite(end_angle):
        raise PcbMaterializationError("invalid_arc_sweep", affected_ref)
    raw_sweep = end_angle - start_angle
    if abs(raw_sweep) > 360.0 + 1e-12:
        raise PcbMaterializationError("invalid_arc_sweep", affected_ref)
    normalized = raw_sweep % 360.0
    closed = math.isclose(normalized, 0.0, abs_tol=1e-12) and not math.isclose(
        raw_sweep,
        0.0,
        abs_tol=1e-12,
    )
    sweep = 360.0 if closed else normalized
    sweep_e12 = int(
        (Decimal(str(sweep)) * ARC_SWEEP_SCALE).quantize(
            Decimal(1),
            rounding=ROUND_HALF_EVEN,
        )
    )
    if sweep_e12 <= 0 or sweep_e12 > 360 * ARC_SWEEP_SCALE:
        raise PcbMaterializationError("invalid_arc_sweep", affected_ref)
    return sweep_e12, closed


def _circular_segment_is_degenerate(segment: PathSegment) -> bool:
    if not isinstance(segment, CircularArcSegment):
        return False
    return (
        segment.start == segment.end
        or segment.start == segment.center
        or segment.end == segment.center
    )


def _arc_point(center: Point2d, radius_nm: int, angle_degrees: float) -> Point2d:
    radians = math.radians(angle_degrees)
    return Point2d(
        x_nm=center.x_nm + round(radius_nm * math.cos(radians)),
        y_nm=center.y_nm + round(radius_nm * math.sin(radians)),
    )


def _feature_layer(
    binding: ResolvedLayerBinding,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
) -> ManufacturingLayerOccurrence | None:
    candidates = tuple(
        row
        for row in walked_layers
        if row.layer.layer_key == binding.layer_key
        and (
            not binding.applicable_substack_refs
            or row.source_stackup_ref in binding.applicable_substack_refs
        )
    )
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError(
            f"layer {binding.layer_key} resolved to {len(candidates)} output occurrences"
        )
    return candidates[0]


def _material_feature(
    *,
    feature_id: str,
    source: SourceProvenance,
    layer: ManufacturingLayerOccurrence,
    source_net_ref: str | None,
    component_occurrence_ref: str | None = None,
    reference_prefix: str = "",
    feature_kind: Literal["route", "land", "fill"],
    geometry: Geometry,
    coordinate_error_pm: int,
    diagnostics: list[Diagnostic],
    topology_status: Literal["stable", "unfused_degeneracy"] = "stable",
    affine_depth: int = 0,
) -> MaterialFeature:
    source = _promote_unresolved_source(feature_id, source, diagnostics)
    return MaterialFeature(
        id=feature_id,
        source=source,
        layer_ref=f"{reference_prefix}{layer.id}",
        source_net_ref=(
            f"{reference_prefix}{source_net_ref}"
            if source_net_ref is not None
            else UNSET
        ),
        component_occurrence_ref=(
            component_occurrence_ref if component_occurrence_ref is not None else UNSET
        ),
        feature_kind=feature_kind,
        material_role=layer.material_role,
        polarity="add",
        precision=PrecisionEnvelope(
            max_coordinate_error_pm=coordinate_error_pm,
            max_scalar_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
            affine_depth=affine_depth,
            topology_status=topology_status,
        ),
        geometry=geometry,
    )


def _component_owner(
    component_occurrence_ref: str | None,
    *,
    fitted_components: dict[str, bool] | None,
    affected_ref: str,
    reference_prefix: str = "",
) -> tuple[bool, str | None]:
    if component_occurrence_ref is None:
        return True, None
    if fitted_components is None:
        raise PcbMaterializationError(
            "component_fitted_authority_required", affected_ref
        )
    if component_occurrence_ref not in fitted_components:
        raise PcbMaterializationError("unknown_component_owner", affected_ref)
    if not fitted_components[component_occurrence_ref]:
        return False, None
    return True, f"{reference_prefix}{component_occurrence_ref}"


def _pad_features(
    pads: tuple[ResolvedPadInput, ...],
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None,
    *,
    id_prefix: str = "",
    affine_depth: int = 0,
) -> tuple[list[Feature], list[DrillSpan]]:
    features: list[Feature] = []
    spans: list[DrillSpan] = []
    for pad in pads:
        pad_features, span = _one_pad_features(
            pad,
            walked_layers,
            diagnostics,
            fitted_components,
            id_prefix=id_prefix,
            affine_depth=affine_depth,
        )
        features.extend(pad_features)
        if span is not None:
            spans.append(span)
    return features, spans


def _one_pad_features(
    pad: ResolvedPadInput,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None,
    *,
    id_prefix: str,
    affine_depth: int,
) -> tuple[list[Feature], DrillSpan | None]:
    affected_ref = f"{id_prefix}{pad.id}"
    include, component_ref = _component_owner(
        pad.component_occurrence_ref,
        fitted_components=fitted_components,
        affected_ref=affected_ref,
        reference_prefix=id_prefix,
    )
    if not include:
        return [], None
    land_rows = [
        (land, _required_feature_layer(land.layer, walked_layers, affected_ref))
        for land in pad.lands
    ]
    land_features = [
        _pad_land_feature(
            pad,
            land,
            layer,
            diagnostics,
            component_occurrence_ref=component_ref,
            id_prefix=id_prefix,
            affine_depth=affine_depth,
        )
        for land, layer in land_rows
    ]
    if pad.hole_size_source_units.selected_value <= 0:
        return list(land_features), None
    span = _pad_drill_span(
        pad,
        land_rows,
        diagnostics,
        id_prefix=id_prefix,
    )
    hole = _pad_hole_feature(
        pad,
        land_features[0].id,
        span.id,
        diagnostics,
        id_prefix=id_prefix,
        affine_depth=affine_depth,
    )
    return [*land_features, hole], span


def _required_feature_layer(
    binding: ResolvedLayerBinding,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    affected_ref: str,
) -> ManufacturingLayerOccurrence:
    layer = _feature_layer(binding, walked_layers)
    if layer is None:
        raise PcbMaterializationError("unresolved_layer", affected_ref)
    return layer


def _pad_land_feature(
    pad: ResolvedPadInput,
    land: ResolvedPadLandInput,
    layer: ManufacturingLayerOccurrence,
    diagnostics: list[Diagnostic],
    *,
    component_occurrence_ref: str | None,
    id_prefix: str,
    affine_depth: int,
) -> MaterialFeature:
    center = Point2d(
        x_nm=pcb_internal_to_nm(land.center_x_source_units.selected_value),
        y_nm=pcb_internal_to_nm(land.center_y_source_units.selected_value),
    )
    geometry = _pad_land_geometry(f"{id_prefix}{pad.id}", land, center)
    return _material_feature(
        feature_id=f"{id_prefix}feature.{pad.id}.land.{layer.id}",
        source=pad.source,
        layer=layer,
        source_net_ref=pad.source_net_ref,
        component_occurrence_ref=component_occurrence_ref,
        reference_prefix=id_prefix,
        feature_kind="land",
        geometry=geometry,
        coordinate_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
        diagnostics=diagnostics,
        affine_depth=affine_depth,
    )


def _pad_land_geometry(
    affected_ref: str,
    land: ResolvedPadLandInput,
    center: Point2d,
) -> Geometry:
    width = land.width_source_units.selected_value
    height = land.height_source_units.selected_value
    shape = land.shape_code.selected_value
    if shape == 1:
        return _capsule_or_circle_geometry(
            center,
            width_source_units=width,
            height_source_units=height,
            rotation_degrees=land.rotation_degrees.selected_value,
            affected_ref=affected_ref,
        )
    if shape == 2 and width > 0 and height > 0:
        return OrientedRectangleGeometry(
            center=center,
            width_nm=_positive_dimension_nm(width, affected_ref),
            height_nm=_positive_dimension_nm(height, affected_ref),
            affine=rotation_affine_degrees(land.rotation_degrees.selected_value),
        )
    if shape == 4 and land.corner_radius_percent_e12 is not None:
        return _rounded_rectangle_geometry(
            center,
            width_source_units=width,
            height_source_units=height,
            corner_radius_percent_e12=(land.corner_radius_percent_e12.selected_value),
            rotation_degrees=land.rotation_degrees.selected_value,
            affected_ref=affected_ref,
        )
    raise PcbMaterializationError("unsupported_pad_land_geometry", affected_ref)


def _capsule_or_circle_geometry(
    center: Point2d,
    *,
    width_source_units: int,
    height_source_units: int,
    rotation_degrees: float,
    affected_ref: str,
) -> Geometry:
    width_nm = _positive_dimension_nm(width_source_units, affected_ref)
    height_nm = _positive_dimension_nm(height_source_units, affected_ref)
    if width_source_units == height_source_units:
        return CircleGeometry(
            center=center,
            radius_nm=_diameter_radius_nm(width_source_units, affected_ref),
        )
    major_is_y = height_source_units > width_source_units
    return CapsuleGeometry(
        center=center,
        overall_length_nm=max(width_nm, height_nm),
        diameter_nm=min(width_nm, height_nm),
        affine=rotation_affine_degrees(rotation_degrees + (90 if major_is_y else 0)),
    )


def _rounded_rectangle_geometry(
    center: Point2d,
    *,
    width_source_units: int,
    height_source_units: int,
    corner_radius_percent_e12: int,
    rotation_degrees: float,
    affected_ref: str,
) -> Geometry:
    width_nm = _positive_dimension_nm(width_source_units, affected_ref)
    height_nm = _positive_dimension_nm(height_source_units, affected_ref)
    if not 0 <= corner_radius_percent_e12 <= 100 * 10**12:
        raise PcbMaterializationError("unsupported_pad_land_geometry", affected_ref)
    if corner_radius_percent_e12 == 0:
        return OrientedRectangleGeometry(
            center=center,
            width_nm=width_nm,
            height_nm=height_nm,
            affine=rotation_affine_degrees(rotation_degrees),
        )
    minor_source_units = min(width_source_units, height_source_units)
    radius_nm = _round_ratio_ties_even(
        minor_source_units * corner_radius_percent_e12 * 127,
        10_000 * 10**12,
    )
    if radius_nm <= 0:
        raise PcbMaterializationError("topology_degeneracy", affected_ref)
    maximum_radius_nm = _diameter_radius_nm(minor_source_units, affected_ref)
    if radius_nm >= maximum_radius_nm:
        return _capsule_or_circle_geometry(
            center,
            width_source_units=width_source_units,
            height_source_units=height_source_units,
            rotation_degrees=rotation_degrees,
            affected_ref=affected_ref,
        )
    return RoundedRectangleGeometry(
        center=center,
        width_nm=width_nm,
        height_nm=height_nm,
        corner_radius_nm=radius_nm,
        affine=rotation_affine_degrees(rotation_degrees),
    )


def _pad_drill_span(
    pad: ResolvedPadInput,
    land_rows: list[tuple[ResolvedPadLandInput, ManufacturingLayerOccurrence]],
    diagnostics: list[Diagnostic],
    *,
    id_prefix: str,
) -> DrillSpan:
    if not land_rows:
        raise PcbMaterializationError("missing_pad_lands", f"{id_prefix}{pad.id}")
    span_id = f"{id_prefix}drill_span.{pad.id}"
    return DrillSpan(
        id=span_id,
        source=_promote_unresolved_source(span_id, pad.source, diagnostics),
        start_layer_ref=f"{id_prefix}{land_rows[0][1].id}",
        end_layer_ref=f"{id_prefix}{land_rows[-1][1].id}",
        backdrill=False,
    )


def _pad_hole_feature(
    pad: ResolvedPadInput,
    parent_feature_ref: str,
    drill_span_ref: str,
    diagnostics: list[Diagnostic],
    *,
    id_prefix: str,
    affine_depth: int,
) -> HoleFeature:
    diameter = pad.hole_size_source_units.selected_value
    hole_shape = pad.hole_shape_code.selected_value
    center = Point2d(
        x_nm=pcb_internal_to_nm(pad.center_x_source_units.selected_value),
        y_nm=pcb_internal_to_nm(pad.center_y_source_units.selected_value),
    )
    if hole_shape == 0:
        geometry: Geometry = CircleGeometry(
            center=center,
            radius_nm=_diameter_radius_nm(diameter, f"{id_prefix}{pad.id}"),
        )
    elif hole_shape == 2:
        slot_size = pad.slot_size_source_units.selected_value
        if slot_size < diameter or pad.rotation_degrees is None:
            raise PcbMaterializationError(
                "unsupported_pad_hole_geometry", f"{id_prefix}{pad.id}"
            )
        geometry = _capsule_or_circle_geometry(
            center,
            width_source_units=slot_size,
            height_source_units=diameter,
            rotation_degrees=(
                pad.rotation_degrees.selected_value
                + pad.slot_rotation_degrees.selected_value
            ),
            affected_ref=f"{id_prefix}{pad.id}",
        )
    else:
        raise PcbMaterializationError(
            "unsupported_pad_hole_geometry", f"{id_prefix}{pad.id}"
        )
    feature_id = f"{id_prefix}feature.{pad.id}.hole"
    return HoleFeature(
        id=feature_id,
        parent_feature_ref=parent_feature_ref,
        source=_promote_unresolved_source(feature_id, pad.source, diagnostics),
        precision=PrecisionEnvelope(
            max_coordinate_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
            max_scalar_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
            affine_depth=affine_depth,
            topology_status="stable",
        ),
        geometry=geometry,
        drill_span_ref=drill_span_ref,
        plated=pad.plated,
    )


def _diameter_radius_nm(diameter_source_units: int, affected_ref: str) -> int:
    if diameter_source_units <= 0:
        raise PcbMaterializationError("nonpositive_radius", affected_ref)
    radius_nm = _round_ratio_ties_even(diameter_source_units * 127, 100)
    if radius_nm <= 0:
        raise PcbMaterializationError("topology_degeneracy", affected_ref)
    if radius_nm > _MAX_INT64:
        raise PcbMaterializationError("integer_overflow", affected_ref)
    return radius_nm


def _positive_dimension_nm(source_units: int, affected_ref: str) -> int:
    if source_units <= 0:
        raise PcbMaterializationError("nonpositive_dimension", affected_ref)
    value_nm = pcb_internal_to_nm(source_units)
    if value_nm <= 0:
        raise PcbMaterializationError("topology_degeneracy", affected_ref)
    if value_nm > _MAX_INT64:
        raise PcbMaterializationError("integer_overflow", affected_ref)
    return value_nm


def _via_features(
    vias: tuple[ResolvedViaInput, ...],
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None,
    *,
    id_prefix: str = "",
    affine_depth: int = 0,
) -> tuple[list[Feature], list[DrillSpan]]:
    features: list[Feature] = []
    spans: list[DrillSpan] = []
    for via in vias:
        via_features, span = _one_via_features(
            via,
            walked_layers,
            diagnostics,
            fitted_components,
            id_prefix=id_prefix,
            affine_depth=affine_depth,
        )
        features.extend(via_features)
        if span is not None:
            spans.append(span)
    return features, spans


def _one_via_features(
    via: ResolvedViaInput,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None,
    *,
    id_prefix: str,
    affine_depth: int,
) -> tuple[list[Feature], DrillSpan | None]:
    affected_ref = f"{id_prefix}{via.id}"
    include, component_ref = _component_owner(
        via.component_occurrence_ref,
        fitted_components=fitted_components,
        affected_ref=affected_ref,
        reference_prefix=id_prefix,
    )
    if not include:
        return [], None
    land_rows = [
        (land, _required_feature_layer(land.layer, walked_layers, affected_ref))
        for land in via.lands
    ]
    if not land_rows:
        raise PcbMaterializationError("missing_via_lands", affected_ref)
    lands = [
        _via_land_feature(
            via,
            land.diameter_source_units.selected_value,
            layer,
            diagnostics,
            component_occurrence_ref=component_ref,
            id_prefix=id_prefix,
            affine_depth=affine_depth,
        )
        for land, layer in land_rows
    ]
    span_id = f"{id_prefix}drill_span.{via.id}"
    span = DrillSpan(
        id=span_id,
        source=_promote_unresolved_source(span_id, via.source, diagnostics),
        start_layer_ref=f"{id_prefix}{land_rows[0][1].id}",
        end_layer_ref=f"{id_prefix}{land_rows[-1][1].id}",
        backdrill=False,
    )
    hole = _via_hole_feature(
        via,
        lands[0].id,
        span.id,
        diagnostics,
        id_prefix=id_prefix,
        affine_depth=affine_depth,
    )
    return [*lands, hole], span


def _via_land_feature(
    via: ResolvedViaInput,
    diameter_source_units: int,
    layer: ManufacturingLayerOccurrence,
    diagnostics: list[Diagnostic],
    *,
    component_occurrence_ref: str | None,
    id_prefix: str,
    affine_depth: int,
) -> MaterialFeature:
    center = _via_center(via)
    return _material_feature(
        feature_id=f"{id_prefix}feature.{via.id}.land.{layer.id}",
        source=via.source,
        layer=layer,
        source_net_ref=via.source_net_ref,
        component_occurrence_ref=component_occurrence_ref,
        reference_prefix=id_prefix,
        feature_kind="land",
        geometry=CircleGeometry(
            center=center,
            radius_nm=_diameter_radius_nm(
                diameter_source_units, f"{id_prefix}{via.id}"
            ),
        ),
        coordinate_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
        diagnostics=diagnostics,
        affine_depth=affine_depth,
    )


def _via_hole_feature(
    via: ResolvedViaInput,
    parent_feature_ref: str,
    drill_span_ref: str,
    diagnostics: list[Diagnostic],
    *,
    id_prefix: str,
    affine_depth: int,
) -> HoleFeature:
    feature_id = f"{id_prefix}feature.{via.id}.hole"
    return HoleFeature(
        id=feature_id,
        parent_feature_ref=parent_feature_ref,
        source=_promote_unresolved_source(feature_id, via.source, diagnostics),
        precision=PrecisionEnvelope(
            max_coordinate_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
            max_scalar_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
            affine_depth=affine_depth,
            topology_status="stable",
        ),
        geometry=CircleGeometry(
            center=_via_center(via),
            radius_nm=_diameter_radius_nm(
                via.hole_size_source_units.selected_value,
                f"{id_prefix}{via.id}",
            ),
        ),
        drill_span_ref=drill_span_ref,
        plated=via.plated,
    )


def _via_center(via: ResolvedViaInput) -> Point2d:
    return Point2d(
        x_nm=pcb_internal_to_nm(via.center_x_source_units.selected_value),
        y_nm=pcb_internal_to_nm(via.center_y_source_units.selected_value),
    )


def _fill_features(
    fills: tuple[ResolvedFillInput, ...],
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None,
    *,
    id_prefix: str = "",
    affine_depth: int = 0,
) -> list[MaterialFeature]:
    return [
        feature
        for fill in fills
        for feature in [
            _fill_feature(
                fill,
                walked_layers,
                diagnostics,
                fitted_components,
                id_prefix=id_prefix,
                affine_depth=affine_depth,
            )
        ]
        if feature is not None
    ]


def _fill_feature(
    fill: ResolvedFillInput,
    walked_layers: tuple[ManufacturingLayerOccurrence, ...],
    diagnostics: list[Diagnostic],
    fitted_components: dict[str, bool] | None,
    *,
    id_prefix: str,
    affine_depth: int,
) -> MaterialFeature | None:
    affected_ref = f"{id_prefix}{fill.id}"
    include, component_ref = _component_owner(
        fill.component_occurrence_ref,
        fitted_components=fitted_components,
        affected_ref=affected_ref,
        reference_prefix=id_prefix,
    )
    if not include:
        return None
    if fill.is_keepout or fill.is_polygon_outline or fill.polygon_index != 0xFFFF:
        raise PcbMaterializationError("classified_fill_geometry_pending", affected_ref)
    x1 = fill.pos1_x_source_units.selected_value
    y1 = fill.pos1_y_source_units.selected_value
    x2 = fill.pos2_x_source_units.selected_value
    y2 = fill.pos2_y_source_units.selected_value
    width = abs(x2 - x1)
    height = abs(y2 - y1)
    if width <= 0 or height <= 0:
        raise PcbMaterializationError("degenerate_fill_geometry", affected_ref)
    layer = _required_feature_layer(fill.layer, walked_layers, affected_ref)
    geometry = OrientedRectangleGeometry(
        center=Point2d(
            x_nm=_coordinate_midpoint_nm(x1, x2),
            y_nm=_coordinate_midpoint_nm(y1, y2),
        ),
        width_nm=pcb_internal_to_nm(width),
        height_nm=pcb_internal_to_nm(height),
        affine=rotation_affine_degrees(fill.rotation_degrees.selected_value),
    )
    return _material_feature(
        feature_id=f"{id_prefix}feature.{fill.id}",
        source=fill.source,
        layer=layer,
        source_net_ref=fill.source_net_ref,
        component_occurrence_ref=component_ref,
        reference_prefix=id_prefix,
        feature_kind="fill",
        geometry=geometry,
        coordinate_error_pm=PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM,
        diagnostics=diagnostics,
        affine_depth=affine_depth,
    )


def _coordinate_midpoint_nm(first_source_units: int, second_source_units: int) -> int:
    return _round_ratio_ties_even((first_source_units + second_source_units) * 127, 100)


__all__ = (
    "PcbMaterializationError",
    "materialize_stored_features",
    "materialize_stored_profile",
    "materialize_stored_routes",
)
