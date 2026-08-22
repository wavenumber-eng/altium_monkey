"""Resolve stored PCB facts before manufacturing geometry materialization."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import math
import re
from typing import TYPE_CHECKING, Callable, Generic, Literal, TypeVar

from msgspec import UNSET

from altium_monkey.altium_board import BoardOutlineVertex, resolve_outline_arc_segment
from altium_monkey.altium_pcb_enums import PadShape, PcbViaMode
from altium_monkey.altium_pcb_layer_ref import (
    PcbLayerRef,
    PcbPrimitiveLayerState,
    _same_pcb_layer_ref_representation,
    _v7_only_legacy_placeholder_id,
)
from altium_monkey.altium_pcb_mask_expansion import PcbMaskExpansionMode
from altium_monkey.altium_pcb_rule import (
    AltiumBoardOutlineClearanceRule,
    AltiumClearanceRule,
    AltiumConnectStyleSettings,
    AltiumPasteMaskExpansionRule,
    AltiumPcbRule,
    AltiumPlaneClearanceRule,
    AltiumPlaneConnectRule,
    AltiumPolygonConnectRule,
    AltiumSolderMaskExpansionRule,
)
from altium_monkey.altium_record_types import PcbLayer
from altium_monkey.altium_resolved_layer_stack import ResolvedLayer, ResolvedLayerStack

from .affine import (
    apply_affine,
    compose_affines,
    identity_affine,
    rotation_affine_degrees,
)
from .child_document_provider import (
    PcbChildDocumentLoad,
    PcbChildDocumentProvider,
    PcbChildDocumentProviderError,
    PcbChildRevisionIdentity,
)
from .generated import (
    BoardOccurrence,
    ChildBoardRequest,
    Diagnostic,
    DiagnosticCode,
    LocatedSource,
    PcbDecimalAffine2d,
    Point2d,
    SourceProvenance,
    UnresolvedSource,
)
from .source_provenance import PcbDocSourceIndex, source_occurrence_ref
from .rule_resolution import (
    ManufacturingBinaryRuleQuery,
    ManufacturingRuleQuery,
    PcbRuleResolutionError,
    select_manufacturing_rule,
)
from .units import pcb_internal_to_nm

if TYPE_CHECKING:
    from altium_monkey.altium_pcb_component import AltiumPcbComponent
    from altium_monkey.altium_pcb_embedded_board import AltiumPcbEmbeddedBoard
    from altium_monkey.altium_pcbdoc import AltiumPcbDoc
    from altium_monkey.altium_record_pcb__arc import AltiumPcbArc
    from altium_monkey.altium_record_pcb__fill import AltiumPcbFill
    from altium_monkey.altium_record_pcb__netclass import AltiumPcbNetClass
    from altium_monkey.altium_record_pcb__pad import AltiumPcbPad
    from altium_monkey.altium_record_pcb__polygon import AltiumPcbPolygon
    from altium_monkey.altium_record_pcb__track import AltiumPcbTrack
    from altium_monkey.altium_record_pcb__via import AltiumPcbVia

Strictness = Literal["strict", "permissive"]
WinningValueSource = Literal["stored", "cache", "rule", "default"]
PrimitiveCacheState = Literal["invalid", "valid", "manual"]
CoordinateFrame = Literal["board"]
ComponentSide = Literal["top", "bottom"]
MaskSide = Literal["top", "bottom"]
SolderMaskMode = Literal["none", "rule", "manual"]
PasteMaskMode = Literal["none", "rule", "manual"]
PasteMaskMeasure = Literal["absolute", "percent"]
PlaneConnectPrimitiveKind = Literal["default", "pad", "via"]
PlaneConnectStyle = Literal["direct", "relief", "no_connect"]
PlaneRuleSelectionDisposition = Literal["binary_context_required"]
PolygonConnectPrimitiveKind = Literal["default", "th_pad", "smd_pad", "via"]
PolygonReliefAngleDegrees = Literal[0, 45, 90, 135]
PolygonConnectRuleSelectionDisposition = Literal["binary_context_required"]
ClearanceRuleKind = Literal["clearance", "board_outline_clearance"]
ClearanceRuleSelectionDisposition = Literal["binary_context_required"]
PlaneCacheValidityMapping = Literal["pad_partial", "via_unmapped"]
_PlaneRuleQueryRequirement = Literal[
    "peer_plane_primitive",
    "plane_layer_occurrence",
    "board_region_or_substack",
    "primitive_to_plane_net_relationship",
]
_PolygonConnectRuleQueryRequirement = Literal[
    "peer_polygon_occurrence",
    "polygon_layer_occurrence",
    "board_region_or_substack",
    "primitive_to_polygon_net_relationship",
    "primitive_object_kind",
]
_ClearanceRuleQueryRequirement = Literal[
    "first_primitive_occurrence",
    "second_primitive_or_profile_edge_occurrence",
    "layer_occurrence",
    "board_region_or_substack",
    "primitive_net_relationship",
    "profile_edge_kind_when_applicable",
]
ProfileOperation = Literal["outer", "cutout"]
_EmbeddedBoardResolutionDisposition = Literal["child_provider_required"]
_EmbeddedBoardRepeatDisposition = Literal["source_array_unexpanded"]
_ValueT = TypeVar("_ValueT")
_ARC_SWEEP_SCALE = 10**12
_MIN_INT64 = -(1 << 63)
_MAX_INT64 = (1 << 63) - 1
_CHILD_DOCUMENT_DIAGNOSTIC_CODES: dict[str, DiagnosticCode] = {
    "invalid_child_path": "child_document_invalid_path",
    "denied_child": "child_document_denied",
    "missing_child": "child_document_missing",
    "ambiguous_child": "child_document_ambiguous",
    "changed_child_revision": "child_document_changed_revision",
    "cyclic_child_reference": "child_document_cyclic_reference",
    "child_resource_limit": "child_document_resource_limit",
    "invalid_child_document": "child_document_invalid_format",
}


@dataclass(frozen=True)
class PcbResolvedInputError(ValueError):
    """Stable failure while resolving raw PCB records into manufacturing inputs."""

    code: str
    detail: str


@dataclass(frozen=True)
class ResolvedWinningValue(Generic[_ValueT]):
    """Selected value plus explicit stored/cache/rule/default evidence."""

    selected_value: _ValueT
    selected_from: WinningValueSource
    stored_value: _ValueT | None = None
    cached_value: _ValueT | None = None
    rule_value: _ValueT | None = None
    rule_ref: str | None = None
    default_value: _ValueT | None = None
    cache_validity: str | None = None

    def __post_init__(self) -> None:
        evidence = {
            "stored": self.stored_value,
            "cache": self.cached_value,
            "rule": self.rule_value,
            "default": self.default_value,
        }[self.selected_from]
        if evidence is None or evidence != self.selected_value:
            raise ValueError("selected value must equal its selected evidence")
        if self.selected_from == "rule" and not str(self.rule_ref or "").strip():
            raise ValueError("rule-selected values require rule_ref evidence")
        if self.selected_from == "cache" and not str(self.cache_validity or "").strip():
            raise ValueError("cache-selected values require cache_validity evidence")


@dataclass(frozen=True)
class ResolvedLayerBinding:
    """Exact primitive-to-stack binding without copying the stack model."""

    layer_ref: PcbLayerRef
    layer_key: str
    source_field: str
    stored_legacy_layer_id: int | None
    stored_v7_saved_layer_id: int | None
    applicable_substack_refs: tuple[str, ...]
    applicable_region_stack_refs: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedNetInput:
    """One source net with occurrence identity independent of its display name."""

    id: str
    source: SourceProvenance
    display_name: str


@dataclass(frozen=True)
class ResolvedNetClassInput:
    """One explicit source net class bound to exact resolved net occurrences."""

    id: str
    source: SourceProvenance
    display_name: str
    member_net_refs: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedComponentClassInput:
    """One explicit source component class bound to exact component occurrences."""

    id: str
    source: SourceProvenance
    display_name: str
    member_component_refs: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedComponentOccurrenceInput:
    """One component placement and owner identity, separate from board geometry."""

    id: str
    source: SourceProvenance
    source_component_unique_id: str
    display_designator: str
    footprint: str
    side: ComponentSide
    origin_x_source_units: ResolvedWinningValue[int]
    origin_y_source_units: ResolvedWinningValue[int]
    rotation_degrees: ResolvedWinningValue[float]
    local_to_board_affine: PcbDecimalAffine2d


@dataclass(frozen=True)
class ResolvedEmbeddedBoardReferenceInput:
    """One stored child-board request before provider loading or expansion."""

    id: str
    source: SourceProvenance
    requested_document_path: str
    placement_layer: str
    origin_x_source_units: int
    origin_y_source_units: int
    bounds_x1_source_units: int
    bounds_y1_source_units: int
    bounds_x2_source_units: int
    bounds_y2_source_units: int
    rotation_degrees_e12: int
    mirror: bool
    row_count: int
    column_count: int
    row_spacing_source_units: int
    column_spacing_source_units: int
    origin_mode: int
    resolution_disposition: _EmbeddedBoardResolutionDisposition
    repeat_disposition: _EmbeddedBoardRepeatDisposition
    diagnostics: tuple[Diagnostic, ...] = ()


class PcbChildRequestOutcome:
    """Resolver-created immutable child request authority."""

    __slots__ = (
        "_diagnostics",
        "_loaded_child",
        "_provider_error",
        "_request",
        "_row",
    )

    def __init__(
        self,
        *,
        construction_token: object,
        request: ResolvedEmbeddedBoardReferenceInput,
        row: ChildBoardRequest,
        loaded_child: PcbChildDocumentLoad | None,
        provider_error: PcbChildDocumentProviderError | None,
        diagnostics: tuple[Diagnostic, ...],
    ) -> None:
        if construction_token is not _CHILD_OUTCOME_CONSTRUCTION_TOKEN:
            raise TypeError("PcbChildRequestOutcome must be created by its resolver")
        object.__setattr__(self, "_request", request)
        object.__setattr__(self, "_row", row)
        object.__setattr__(self, "_loaded_child", loaded_child)
        object.__setattr__(self, "_provider_error", provider_error)
        object.__setattr__(self, "_diagnostics", diagnostics)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def request(self) -> ResolvedEmbeddedBoardReferenceInput:
        """Exact resolved request used to create this outcome."""

        return self._request

    @property
    def row(self) -> ChildBoardRequest:
        """Normalized request row bound by this outcome."""

        return self._row

    @property
    def loaded_child(self) -> PcbChildDocumentLoad | None:
        """Exact loaded child, when provider resolution succeeded."""

        return self._loaded_child

    @property
    def provider_error(self) -> PcbChildDocumentProviderError | None:
        """Typed provider failure retained by a permissive outcome."""

        return self._provider_error

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Request and provider diagnostics retained by this outcome."""

        return self._diagnostics


_CHILD_OUTCOME_CONSTRUCTION_TOKEN = object()


@dataclass(frozen=True)
class PcbResolvedChildBoardOccurrence:
    """One bounded direct child occurrence before normalized graph emission."""

    id: str
    request_ref: str
    parent_board_occurrence_ref: str
    source: SourceProvenance
    child_identity: PcbChildRevisionIdentity
    row_index: int
    column_index: int
    step_row_index: int
    step_column_index: int
    child_anchor_x_source_units: int
    child_anchor_y_source_units: int
    repeat_offset_x_source_units: int
    repeat_offset_y_source_units: int
    local_to_parent_affine: PcbDecimalAffine2d
    affine: PcbDecimalAffine2d


@dataclass(frozen=True)
class ResolvedTrackInput:
    """One direct stored track in its resolved board coordinate frame."""

    id: str
    source: SourceProvenance
    layer: ResolvedLayerBinding
    source_net_ref: str | None
    component_occurrence_ref: str | None
    coordinate_frame: CoordinateFrame
    source_to_board_affine: PcbDecimalAffine2d
    start_x_source_units: ResolvedWinningValue[int]
    start_y_source_units: ResolvedWinningValue[int]
    end_x_source_units: ResolvedWinningValue[int]
    end_y_source_units: ResolvedWinningValue[int]
    width_source_units: ResolvedWinningValue[int]
    polygon_index: int
    is_keepout: bool
    is_polygon_outline: bool


@dataclass(frozen=True)
class ResolvedArcInput:
    """One direct stored circular arc in its resolved board coordinate frame."""

    id: str
    source: SourceProvenance
    layer: ResolvedLayerBinding
    source_net_ref: str | None
    component_occurrence_ref: str | None
    coordinate_frame: CoordinateFrame
    source_to_board_affine: PcbDecimalAffine2d
    center_x_source_units: ResolvedWinningValue[int]
    center_y_source_units: ResolvedWinningValue[int]
    radius_source_units: ResolvedWinningValue[int]
    start_angle_degrees: ResolvedWinningValue[float]
    end_angle_degrees: ResolvedWinningValue[float]
    width_source_units: ResolvedWinningValue[int]
    polygon_index: int
    is_keepout: bool
    is_polygon_outline: bool


@dataclass(frozen=True)
class ResolvedPadLandInput:
    """One exact stored pad land on one resolved copper layer."""

    layer: ResolvedLayerBinding
    center_x_source_units: ResolvedWinningValue[int]
    center_y_source_units: ResolvedWinningValue[int]
    width_source_units: ResolvedWinningValue[int]
    height_source_units: ResolvedWinningValue[int]
    shape_code: ResolvedWinningValue[int]
    rotation_degrees: ResolvedWinningValue[float]
    corner_radius_percent_e12: ResolvedWinningValue[int] | None = None


@dataclass(frozen=True)
class ResolvedSolderMaskSideInput:
    """One side of a resolved solder-mask decision before geometry."""

    side: MaskSide
    expansion_source_units: ResolvedWinningValue[int]
    tented: ResolvedWinningValue[bool]


@dataclass(frozen=True)
class ResolvedSolderMaskInput:
    """Resolved pad/via solder-mask authority with cache evidence."""

    mode: ResolvedWinningValue[SolderMaskMode]
    cache_marker_raw: int | None
    cache_state: PrimitiveCacheState | None
    from_hole_edge: ResolvedWinningValue[bool]
    top: ResolvedSolderMaskSideInput
    bottom: ResolvedSolderMaskSideInput


@dataclass(frozen=True)
class ResolvedPasteMaskSideInput:
    """One side of a resolved paste request before aperture geometry."""

    side: MaskSide
    enabled: ResolvedWinningValue[bool]
    measure: ResolvedWinningValue[PasteMaskMeasure]
    absolute_expansion_source_units: ResolvedWinningValue[int] | None
    percent_e12: ResolvedWinningValue[int] | None
    cached_expansion_source_units: int | None
    cache_validity: str | None


@dataclass(frozen=True)
class ResolvedPasteMaskInput:
    """Resolved pad paste authority without lowering an aperture."""

    mode: ResolvedWinningValue[PasteMaskMode]
    cache_marker_raw: int | None
    cache_state: PrimitiveCacheState | None
    top: ResolvedPasteMaskSideInput
    bottom: ResolvedPasteMaskSideInput


@dataclass(frozen=True)
class ResolvedPlaneCacheEvidence:
    """Saved primitive cache facts that are hints, never rule authority."""

    cache_present: bool
    connection_style_code: int
    relief_conductor_width_source_units: int
    relief_entries: int
    relief_air_gap_source_units: int
    relief_expansion_source_units: int
    clearance_source_units: int
    validity_mapping: PlaneCacheValidityMapping
    connection_style_valid_raw: int | None
    relief_conductor_width_valid_raw: int | None
    relief_entries_valid_raw: int | None
    relief_air_gap_valid_raw: int | None
    relief_expansion_valid_raw: int | None
    clearance_valid_raw: int | None
    unmapped_validity_raw: tuple[int, ...] = ()


@dataclass(frozen=True)
class ResolvedPlaneClearanceRuleAuthority:
    """One parsed plane-clearance rule, without claiming applicability."""

    id: str
    source: SourceProvenance
    enabled: bool
    priority: int
    scope1_expression: str
    scope2_expression: str
    net_scope: str
    layer_kind: str
    clearance_source_units: int


@dataclass(frozen=True)
class ResolvedPlaneConnectSettingsAuthority:
    """One DEFAULT/PAD/VIA settings block from a plane-connect rule."""

    primitive_kind: PlaneConnectPrimitiveKind
    connect_style: PlaneConnectStyle | None
    relief_air_gap_source_units: int | None
    relief_conductor_width_source_units: int | None
    relief_entries: int | None
    relief_expansion_source_units: int | None


@dataclass(frozen=True)
class ResolvedPlaneConnectRuleAuthority:
    """One parsed plane-connect rule, without claiming applicability."""

    id: str
    source: SourceProvenance
    enabled: bool
    priority: int
    scope1_expression: str
    scope2_expression: str
    net_scope: str
    layer_kind: str
    settings: tuple[ResolvedPlaneConnectSettingsAuthority, ...]


@dataclass(frozen=True)
class ResolvedPlaneRuleAuthority:
    """Plane-rule facts whose winner remains blocked on binary plane context."""

    selection_disposition: PlaneRuleSelectionDisposition
    required_query_context: tuple[_PlaneRuleQueryRequirement, ...]
    clearance_rules: tuple[ResolvedPlaneClearanceRuleAuthority, ...]
    connect_rules: tuple[ResolvedPlaneConnectRuleAuthority, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class ResolvedPolygonConnectSettingsAuthority:
    """One exact DEFAULT/THPAD/SMDPAD/VIA block from a polygon rule."""

    primitive_kind: PolygonConnectPrimitiveKind
    connect_style: PlaneConnectStyle | None
    air_gap_source_units: int | None
    relief_angle_degrees: PolygonReliefAngleDegrees | None
    relief_conductor_width_source_units: int | None
    relief_entries: int | None


@dataclass(frozen=True)
class ResolvedPolygonConnectRuleCandidateAuthority:
    """One polygon-connect candidate without claiming binary applicability."""

    id: str
    source: SourceProvenance
    enabled: bool
    priority: int
    scope1_expression: str
    scope2_expression: str
    net_scope: str
    layer_kind: str
    settings: tuple[ResolvedPolygonConnectSettingsAuthority, ...]


@dataclass(frozen=True)
class ResolvedPolygonConnectRuleAuthority:
    """Polygon-connect facts blocked on a concrete primitive/polygon query."""

    selection_disposition: PolygonConnectRuleSelectionDisposition
    required_query_context: tuple[_PolygonConnectRuleQueryRequirement, ...]
    rules: tuple[ResolvedPolygonConnectRuleCandidateAuthority, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class ResolvedPolygonConnectRuleSelection:
    """One source-correlated PolygonConnect winner and effective settings block."""

    query: ManufacturingBinaryRuleQuery
    rule: ResolvedPolygonConnectRuleCandidateAuthority
    primitive_kind: Literal["th_pad", "smd_pad", "via"]
    settings: ResolvedPolygonConnectSettingsAuthority
    settings_selected_from: Literal["exact_primitive", "default_inheritance"]

    def __post_init__(self) -> None:
        if self.primitive_kind not in {"th_pad", "smd_pad", "via"}:
            raise ValueError("unknown PolygonConnect primitive kind")
        if self.settings_selected_from not in {
            "exact_primitive",
            "default_inheritance",
        }:
            raise ValueError("unknown PolygonConnect settings evidence")
        expected_kind: PolygonConnectPrimitiveKind = (
            self.primitive_kind
            if self.settings_selected_from == "exact_primitive"
            else "default"
        )
        if self.settings.primitive_kind != expected_kind:
            raise ValueError(
                "selected PolygonConnect settings do not match their evidence"
            )
        if self.rule.settings.count(self.settings) != 1:
            raise ValueError(
                "selected PolygonConnect settings are not a unique winning-rule block"
            )


@dataclass(frozen=True)
class ResolvedClearancePairAuthority:
    """One exact stored object-pair matrix entry; zero remains a real value."""

    raw_object_a_kind: str
    raw_object_b_kind: str
    object_a_kind: str
    object_b_kind: str
    clearance_source_units: int


@dataclass(frozen=True)
class ResolvedClearanceRuleCandidateAuthority:
    """One clearance candidate without claiming binary-scope applicability."""

    id: str
    source: SourceProvenance
    rule_kind: ClearanceRuleKind
    enabled: bool
    priority: int
    scope1_expression: str
    scope2_expression: str
    net_scope: str
    layer_kind: str
    gap_source_units: int
    generic_clearance_source_units: int | None
    ignore_pad_to_pad_clearance_in_footprint: bool | None
    object_pairs: tuple[ResolvedClearancePairAuthority, ...]


@dataclass(frozen=True)
class ResolvedClearanceRuleAuthority:
    """Clearance facts whose winner remains blocked on complete binary context."""

    selection_disposition: ClearanceRuleSelectionDisposition
    required_query_context: tuple[_ClearanceRuleQueryRequirement, ...]
    rules: tuple[ResolvedClearanceRuleCandidateAuthority, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class ResolvedPadInput:
    """One stored pad occurrence with per-layer lands and physical hole facts."""

    id: str
    source: SourceProvenance
    source_net_ref: str | None
    component_occurrence_ref: str | None
    coordinate_frame: CoordinateFrame
    source_to_board_affine: PcbDecimalAffine2d
    center_x_source_units: ResolvedWinningValue[int]
    center_y_source_units: ResolvedWinningValue[int]
    lands: tuple[ResolvedPadLandInput, ...]
    hole_size_source_units: ResolvedWinningValue[int]
    hole_shape_code: ResolvedWinningValue[int]
    slot_size_source_units: ResolvedWinningValue[int]
    slot_rotation_degrees: ResolvedWinningValue[float]
    paste_mask: ResolvedPasteMaskInput
    solder_mask: ResolvedSolderMaskInput
    plane_cache: ResolvedPlaneCacheEvidence
    plated: bool
    rotation_degrees: ResolvedWinningValue[float] | None = None
    rule_query: ManufacturingRuleQuery | None = None


@dataclass(frozen=True)
class ResolvedViaLandInput:
    """One stored via land on one exact copper layer."""

    layer: ResolvedLayerBinding
    diameter_source_units: ResolvedWinningValue[int]


@dataclass(frozen=True)
class ResolvedViaInput:
    """One stored simple via with exact span, lands, and physical-hole facts."""

    id: str
    source: SourceProvenance
    source_net_ref: str | None
    component_occurrence_ref: str | None
    coordinate_frame: CoordinateFrame
    source_to_board_affine: PcbDecimalAffine2d
    center_x_source_units: ResolvedWinningValue[int]
    center_y_source_units: ResolvedWinningValue[int]
    start_layer_ref: PcbLayerRef
    end_layer_ref: PcbLayerRef
    lands: tuple[ResolvedViaLandInput, ...]
    hole_size_source_units: ResolvedWinningValue[int]
    solder_mask: ResolvedSolderMaskInput
    plane_cache: ResolvedPlaneCacheEvidence
    plated: bool
    rule_query: ManufacturingRuleQuery | None = None


@dataclass(frozen=True)
class ResolvedPolygonInput:
    """One saved polygon definition with explicit binary-rule fact availability."""

    id: str
    source: SourceProvenance
    layer: ResolvedLayerBinding
    source_net_ref: str | None
    net_identity_exact: bool
    definition_name: str | None
    polygon_type: str
    is_keepout: bool | None
    is_shelved: bool | None
    is_polygon_outline: bool | None
    rule_query: ManufacturingRuleQuery | None


@dataclass(frozen=True)
class ResolvedFillInput:
    """One direct stored rectangular fill before analytic materialization."""

    id: str
    source: SourceProvenance
    layer: ResolvedLayerBinding
    source_net_ref: str | None
    component_occurrence_ref: str | None
    coordinate_frame: CoordinateFrame
    source_to_board_affine: PcbDecimalAffine2d
    pos1_x_source_units: ResolvedWinningValue[int]
    pos1_y_source_units: ResolvedWinningValue[int]
    pos2_x_source_units: ResolvedWinningValue[int]
    pos2_y_source_units: ResolvedWinningValue[int]
    rotation_degrees: ResolvedWinningValue[float]
    polygon_index: int
    is_keepout: bool
    is_polygon_outline: bool


@dataclass(frozen=True)
class ResolvedProfileVertex:
    """One exact source-unit profile vertex and its outgoing segment."""

    x_source_units: int
    y_source_units: int
    is_arc: bool
    center_x_source_units: int = 0
    center_y_source_units: int = 0
    clockwise: bool = False
    sweep_degrees_e12: int = 0


@dataclass(frozen=True)
class ResolvedProfileInput:
    """One authoritative outer or cutout contour before geometry lowering."""

    id: str
    source: SourceProvenance
    operation: ProfileOperation
    vertices: tuple[ResolvedProfileVertex, ...]


@dataclass(frozen=True)
class ResolvedPcbInputs:
    """Typed stored-input boundary consumed by later materialization steps."""

    nets: tuple[ResolvedNetInput, ...]
    components: tuple[ResolvedComponentOccurrenceInput, ...]
    tracks: tuple[ResolvedTrackInput, ...]
    arcs: tuple[ResolvedArcInput, ...]
    pads: tuple[ResolvedPadInput, ...]
    vias: tuple[ResolvedViaInput, ...]
    fills: tuple[ResolvedFillInput, ...]
    plane_rule_authority: ResolvedPlaneRuleAuthority | None
    profile: ResolvedProfileInput | None
    cutouts: tuple[ResolvedProfileInput, ...]
    diagnostics: tuple[Diagnostic, ...]
    polygons: tuple[ResolvedPolygonInput, ...] = ()
    clearance_rule_authority: ResolvedClearanceRuleAuthority | None = None
    polygon_connect_rule_authority: ResolvedPolygonConnectRuleAuthority | None = None


def stored_winning_value(value: _ValueT) -> ResolvedWinningValue[_ValueT]:
    """Record a direct stored value as the selected manufacturing evidence."""

    return ResolvedWinningValue(
        selected_value=value,
        selected_from="stored",
        stored_value=value,
    )


_PLANE_RULE_REQUIRED_QUERY_CONTEXT: tuple[_PlaneRuleQueryRequirement, ...] = (
    "peer_plane_primitive",
    "plane_layer_occurrence",
    "board_region_or_substack",
    "primitive_to_plane_net_relationship",
)

_POLYGON_CONNECT_RULE_REQUIRED_QUERY_CONTEXT: tuple[
    _PolygonConnectRuleQueryRequirement, ...
] = (
    "peer_polygon_occurrence",
    "polygon_layer_occurrence",
    "board_region_or_substack",
    "primitive_to_polygon_net_relationship",
    "primitive_object_kind",
)

_CLEARANCE_RULE_REQUIRED_QUERY_CONTEXT: tuple[_ClearanceRuleQueryRequirement, ...] = (
    "first_primitive_occurrence",
    "second_primitive_or_profile_edge_occurrence",
    "layer_occurrence",
    "board_region_or_substack",
    "primitive_net_relationship",
    "profile_edge_kind_when_applicable",
)

_COPPER_CLEARANCE_OBJECT_KINDS = frozenset(
    {
        "Arc",
        "Track",
        "SMDPad",
        "THPad",
        "Via",
        "Fill",
        "Poly",
        "Region",
        "Text",
        "Hole",
    }
)
_BOARD_OUTLINE_SUBJECT_OBJECT_KINDS = _COPPER_CLEARANCE_OBJECT_KINDS - {"Hole"}
_BOARD_OUTLINE_EDGE_OBJECT_KINDS = frozenset(
    {
        "OutlineEdge",
        "CavityEdge",
        "CutoutEdge",
        "SplitBarrier",
        "SplitContinuation",
    }
)


def resolve_pcb_clearance_rule_authority(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
) -> ResolvedClearanceRuleAuthority:
    """Parse clearance candidates without selecting a context-free winner."""

    if strictness not in {"strict", "permissive"}:
        raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
    source_index.assert_current()
    diagnostics: list[Diagnostic] = []
    candidates: list[ResolvedClearanceRuleCandidateAuthority] = []
    for rule in pcbdoc.rules:
        normalized_kind = rule.rule_kind.strip().casefold()
        if normalized_kind not in {"clearance", "boardoutlineclearance"}:
            continue
        if normalized_kind == "boardoutlineclearance":
            expected_type = AltiumBoardOutlineClearanceRule
            rule_kind: ClearanceRuleKind = "board_outline_clearance"
        else:
            expected_type = AltiumClearanceRule
            rule_kind = "clearance"
        if not isinstance(rule, expected_type):
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"input.rule.{rule.index} {rule_kind} row has the wrong typed model",
            )
        source, rule_ref = _manufacturing_rule_source_and_ref(
            rule,
            source_index=source_index,
            strictness=strictness,
            diagnostics=diagnostics,
        )
        enabled, priority = _manufacturing_rule_state(rule, rule_ref)
        candidates.append(
            ResolvedClearanceRuleCandidateAuthority(
                id=rule_ref,
                source=source,
                rule_kind=rule_kind,
                enabled=enabled,
                priority=priority,
                scope1_expression=rule.scope1_expression,
                scope2_expression=rule.scope2_expression,
                net_scope=rule.net_scope,
                layer_kind=rule.layer_kind,
                gap_source_units=_clearance_rule_length_source_units(
                    rule.gap,
                    rule_ref,
                    "GAP",
                ),
                generic_clearance_source_units=(
                    _optional_clearance_rule_length_source_units(
                        rule.generic_clearance,
                        rule_ref,
                        "GENERICCLEARANCE",
                    )
                ),
                ignore_pad_to_pad_clearance_in_footprint=(
                    rule.ignore_pad_to_pad_clearance_in_footprint
                ),
                object_pairs=_clearance_object_pairs(rule, rule_ref, rule_kind),
            )
        )
    return ResolvedClearanceRuleAuthority(
        selection_disposition="binary_context_required",
        required_query_context=_CLEARANCE_RULE_REQUIRED_QUERY_CONTEXT,
        rules=tuple(candidates),
        diagnostics=tuple(diagnostics),
    )


def _clearance_object_pairs(
    rule: AltiumClearanceRule,
    rule_ref: str,
    rule_kind: ClearanceRuleKind,
) -> tuple[ResolvedClearancePairAuthority, ...]:
    raw_table = str(rule.object_clearances_raw or "")
    if not raw_table:
        return ()
    pairs: list[ResolvedClearancePairAuthority] = []
    seen: set[tuple[str, str]] = set()
    for token in raw_table.split(";"):
        if token.count(":") != 1:
            raise PcbResolvedInputError(
                "unsupported_rule_value",
                f"{rule_ref} OBJECTCLEARANCES has malformed entry {token!r}",
            )
        raw_key, raw_value = token.split(":", 1)
        if raw_key.count("-") != 1:
            raise PcbResolvedInputError(
                "unsupported_rule_value",
                f"{rule_ref} OBJECTCLEARANCES has malformed pair {raw_key!r}",
            )
        raw_a, raw_b = raw_key.split("-", 1)
        object_a = _clearance_object_kind(raw_a, rule_ref)
        object_b = _clearance_object_kind(raw_b, rule_ref)
        _validate_clearance_pair_kinds(object_a, object_b, rule_ref, rule_kind)
        canonical_pair = (
            (object_a, object_b) if object_a <= object_b else (object_b, object_a)
        )
        if canonical_pair in seen:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"{rule_ref} OBJECTCLEARANCES repeats pair {canonical_pair!r}",
            )
        seen.add(canonical_pair)
        pairs.append(
            ResolvedClearancePairAuthority(
                raw_object_a_kind=raw_a,
                raw_object_b_kind=raw_b,
                object_a_kind=object_a,
                object_b_kind=object_b,
                clearance_source_units=_rule_nonnegative_int(
                    raw_value,
                    rule_ref,
                    f"OBJECTCLEARANCES[{raw_key}]",
                ),
            )
        )
    return tuple(pairs)


def _clearance_object_kind(value: str, rule_ref: str) -> str:
    prefix = "ClearanceObj_"
    if not value.startswith(prefix) or len(value) == len(prefix):
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{rule_ref} has unsupported clearance object kind {value!r}",
        )
    return value[len(prefix) :]


def _validate_clearance_pair_kinds(
    object_a: str,
    object_b: str,
    rule_ref: str,
    rule_kind: ClearanceRuleKind,
) -> None:
    if rule_kind == "clearance":
        valid = (
            object_a in _COPPER_CLEARANCE_OBJECT_KINDS
            and object_b in _COPPER_CLEARANCE_OBJECT_KINDS
        )
    else:
        valid = (
            object_a in _BOARD_OUTLINE_SUBJECT_OBJECT_KINDS
            and object_b in _BOARD_OUTLINE_EDGE_OBJECT_KINDS
        ) or (
            object_b in _BOARD_OUTLINE_SUBJECT_OBJECT_KINDS
            and object_a in _BOARD_OUTLINE_EDGE_OBJECT_KINDS
        )
    if not valid:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{rule_ref} has unsupported {rule_kind} pair {(object_a, object_b)!r}",
        )


def resolve_pcb_plane_rule_authority(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
) -> ResolvedPlaneRuleAuthority:
    """Parse plane-rule authority without selecting a context-free winner."""

    if strictness not in {"strict", "permissive"}:
        raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
    source_index.assert_current()
    diagnostics: list[Diagnostic] = []
    clearance_rules: list[ResolvedPlaneClearanceRuleAuthority] = []
    connect_rules: list[ResolvedPlaneConnectRuleAuthority] = []
    for rule in pcbdoc.rules:
        normalized_kind = rule.rule_kind.strip().casefold()
        if normalized_kind in {"planeclearance", "powerplaneclearance"}:
            if not isinstance(rule, AltiumPlaneClearanceRule):
                raise PcbResolvedInputError(
                    "corrupt_identity",
                    f"input.rule.{rule.index} plane-clearance row has the wrong typed model",
                )
            source, rule_ref = _manufacturing_rule_source_and_ref(
                rule,
                source_index=source_index,
                strictness=strictness,
                diagnostics=diagnostics,
            )
            enabled, priority = _manufacturing_rule_state(rule, rule_ref)
            clearance_rules.append(
                ResolvedPlaneClearanceRuleAuthority(
                    id=rule_ref,
                    source=source,
                    enabled=enabled,
                    priority=priority,
                    scope1_expression=rule.scope1_expression,
                    scope2_expression=rule.scope2_expression,
                    net_scope=rule.net_scope,
                    layer_kind=rule.layer_kind,
                    clearance_source_units=_rule_length_source_units(
                        rule.clearance,
                        rule_ref,
                        "CLEARANCE",
                    ),
                )
            )
        elif normalized_kind in {"planeconnect", "powerplaneconnectstyle"}:
            if not isinstance(rule, AltiumPlaneConnectRule):
                raise PcbResolvedInputError(
                    "corrupt_identity",
                    f"input.rule.{rule.index} plane-connect row has the wrong typed model",
                )
            source, rule_ref = _manufacturing_rule_source_and_ref(
                rule,
                source_index=source_index,
                strictness=strictness,
                diagnostics=diagnostics,
            )
            enabled, priority = _manufacturing_rule_state(rule, rule_ref)
            connect_rules.append(
                ResolvedPlaneConnectRuleAuthority(
                    id=rule_ref,
                    source=source,
                    enabled=enabled,
                    priority=priority,
                    scope1_expression=rule.scope1_expression,
                    scope2_expression=rule.scope2_expression,
                    net_scope=rule.net_scope,
                    layer_kind=rule.layer_kind,
                    settings=_plane_connect_settings(rule, rule_ref),
                )
            )
    return ResolvedPlaneRuleAuthority(
        selection_disposition="binary_context_required",
        required_query_context=_PLANE_RULE_REQUIRED_QUERY_CONTEXT,
        clearance_rules=tuple(clearance_rules),
        connect_rules=tuple(connect_rules),
        diagnostics=tuple(diagnostics),
    )


def resolve_pcb_polygon_connect_rule_authority(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
) -> ResolvedPolygonConnectRuleAuthority:
    """Parse polygon-connect candidates without selecting a unary winner."""

    if strictness not in {"strict", "permissive"}:
        raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
    source_index.assert_current()
    diagnostics: list[Diagnostic] = []
    candidates: list[ResolvedPolygonConnectRuleCandidateAuthority] = []
    for rule in pcbdoc.rules:
        if rule.rule_kind.strip().casefold() not in {
            "polygonconnect",
            "polygonconnectstyle",
        }:
            continue
        if not isinstance(rule, AltiumPolygonConnectRule):
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"input.rule.{rule.index} polygon-connect row has the wrong typed model",
            )
        source, rule_ref = _manufacturing_rule_source_and_ref(
            rule,
            source_index=source_index,
            strictness=strictness,
            diagnostics=diagnostics,
        )
        enabled, priority = _manufacturing_rule_state(rule, rule_ref)
        candidates.append(
            ResolvedPolygonConnectRuleCandidateAuthority(
                id=rule_ref,
                source=source,
                enabled=enabled,
                priority=priority,
                scope1_expression=rule.scope1_expression,
                scope2_expression=rule.scope2_expression,
                net_scope=rule.net_scope,
                layer_kind=rule.layer_kind,
                settings=_polygon_connect_settings(rule, rule_ref),
            )
        )
    return ResolvedPolygonConnectRuleAuthority(
        selection_disposition="binary_context_required",
        required_query_context=_POLYGON_CONNECT_RULE_REQUIRED_QUERY_CONTEXT,
        rules=tuple(candidates),
        diagnostics=tuple(diagnostics),
    )


def _manufacturing_rule_source_and_ref(
    rule: AltiumPcbRule,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> tuple[SourceProvenance, str]:
    affected_ref = f"input.rule.{rule.index}"
    resolution = source_index.resolve(
        rule,
        strictness=strictness,
        affected_ref=affected_ref,
    )
    diagnostics.extend(resolution.diagnostics)
    return resolution.source, _input_ref("rule", resolution.source, affected_ref)


def _manufacturing_rule_state(rule: AltiumPcbRule, rule_ref: str) -> tuple[bool, int]:
    if rule.enabled is None:
        raise PcbResolvedInputError(
            "invalid_rule_enabled",
            f"{rule_ref} has no valid enabled state",
        )
    if rule.priority is None or rule.priority < 1:
        raise PcbResolvedInputError(
            "invalid_rule_priority",
            f"{rule_ref} has no positive priority",
        )
    return rule.enabled, rule.priority


def _plane_connect_settings(
    rule: AltiumPlaneConnectRule,
    rule_ref: str,
) -> tuple[ResolvedPlaneConnectSettingsAuthority, ...]:
    resolved: list[ResolvedPlaneConnectSettingsAuthority] = []
    blocks: tuple[tuple[str, PlaneConnectPrimitiveKind], ...] = (
        ("DEFAULT", "default"),
        ("PAD", "pad"),
        ("VIA", "via"),
    )
    for raw_kind, kind in blocks:
        settings = rule.connect_settings.get(raw_kind)
        if settings is None:
            continue
        resolved.append(_plane_connect_settings_block(settings, kind, rule_ref))
    unknown_kinds = sorted(set(rule.connect_settings) - {"DEFAULT", "PAD", "VIA"})
    if unknown_kinds:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{rule_ref} has unsupported plane-connect settings blocks {unknown_kinds!r}",
        )
    if not resolved:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{rule_ref} has no plane-connect settings block",
        )
    return tuple(resolved)


def _plane_connect_settings_block(
    settings: AltiumConnectStyleSettings,
    kind: PlaneConnectPrimitiveKind,
    rule_ref: str,
) -> ResolvedPlaneConnectSettingsAuthority:
    prefix = kind.upper()
    return ResolvedPlaneConnectSettingsAuthority(
        primitive_kind=kind,
        connect_style=_optional_plane_connect_style(
            settings.connect_style,
            rule_ref,
            f"{prefix}.PLANECONNECTSTYLE",
        ),
        relief_air_gap_source_units=_optional_rule_length_source_units(
            settings.relief_air_gap,
            rule_ref,
            f"{prefix}.RELIEFAIRGAP",
        ),
        relief_conductor_width_source_units=_optional_rule_length_source_units(
            settings.relief_conductor_width,
            rule_ref,
            f"{prefix}.RELIEFCONDUCTORWIDTH",
        ),
        relief_entries=_optional_rule_nonnegative_int(
            settings.relief_entries,
            rule_ref,
            f"{prefix}.RELIEFENTRIES",
        ),
        relief_expansion_source_units=_optional_rule_length_source_units(
            settings.relief_expansion,
            rule_ref,
            f"{prefix}.RELIEFEXPANSION",
        ),
    )


def _polygon_connect_settings(
    rule: AltiumPolygonConnectRule,
    rule_ref: str,
) -> tuple[ResolvedPolygonConnectSettingsAuthority, ...]:
    resolved: list[ResolvedPolygonConnectSettingsAuthority] = []
    blocks: tuple[tuple[str, PolygonConnectPrimitiveKind], ...] = (
        ("DEFAULT", "default"),
        ("THPAD", "th_pad"),
        ("SMDPAD", "smd_pad"),
        ("VIA", "via"),
    )
    for raw_kind, kind in blocks:
        settings = rule.connect_settings.get(raw_kind)
        if settings is None:
            continue
        resolved.append(_polygon_connect_settings_block(settings, kind, rule_ref))
    unknown_kinds = sorted(
        set(rule.connect_settings) - {"DEFAULT", "THPAD", "SMDPAD", "VIA"}
    )
    if unknown_kinds:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{rule_ref} has unsupported polygon-connect settings blocks {unknown_kinds!r}",
        )
    if not resolved:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{rule_ref} has no polygon-connect settings block",
        )
    return tuple(resolved)


def _polygon_connect_settings_block(
    settings: AltiumConnectStyleSettings,
    kind: PolygonConnectPrimitiveKind,
    rule_ref: str,
) -> ResolvedPolygonConnectSettingsAuthority:
    prefix = kind.upper()
    return ResolvedPolygonConnectSettingsAuthority(
        primitive_kind=kind,
        connect_style=_optional_plane_connect_style(
            settings.connect_style,
            rule_ref,
            f"{prefix}.CONNECTSTYLE",
        ),
        air_gap_source_units=_optional_clearance_rule_length_source_units(
            settings.air_gap_width,
            rule_ref,
            f"{prefix}.AIRGAPWIDTH",
        ),
        relief_angle_degrees=_optional_polygon_relief_angle_degrees(
            settings.relief_angle,
            rule_ref,
            f"{prefix}.POLYGONRELIEFANGLE",
        ),
        relief_conductor_width_source_units=(
            _optional_clearance_rule_length_source_units(
                settings.relief_conductor_width,
                rule_ref,
                f"{prefix}.RELIEFCONDUCTORWIDTH",
            )
        ),
        relief_entries=_optional_rule_nonnegative_int32(
            settings.relief_entries,
            rule_ref,
            f"{prefix}.RELIEFENTRIES",
        ),
    )


def _optional_polygon_relief_angle_degrees(
    value: str,
    affected_ref: str,
    field_name: str,
) -> PolygonReliefAngleDegrees | None:
    normalized = "".join(str(value).strip().casefold().split())
    if not normalized:
        return None
    aliases: dict[str, PolygonReliefAngleDegrees] = {
        "0": 0,
        "0angle": 0,
        "epolygonreliefangle_0": 0,
        "45": 45,
        "45angle": 45,
        "epolygonreliefangle_45": 45,
        "90": 90,
        "90angle": 90,
        "epolygonreliefangle_90": 90,
        "135": 135,
        "135angle": 135,
        "epolygonreliefangle_135": 135,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} has unsupported angle {value!r}",
        ) from exc


def _optional_rule_length_source_units(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int | None:
    if not str(value).strip():
        return None
    return _rule_length_source_units(value, affected_ref, field_name)


def _optional_rule_nonnegative_int(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        result = int(text)
    except ValueError as exc:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is not an integer",
        ) from exc
    if result < 0 or result > _MAX_INT64:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is outside the nonnegative int64 range",
        )
    return result


def _optional_rule_nonnegative_int32(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int | None:
    result = _optional_rule_nonnegative_int(value, affected_ref, field_name)
    if result is not None and result > 0x7FFFFFFF:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is outside the nonnegative int32 range",
        )
    return result


def _optional_plane_connect_style(
    value: str,
    affected_ref: str,
    field_name: str,
) -> PlaneConnectStyle | None:
    normalized = "".join(str(value).strip().casefold().split())
    if not normalized:
        return None
    aliases: dict[str, PlaneConnectStyle] = {
        "direct": "direct",
        "directconnect": "direct",
        "directconnecttoplane": "direct",
        "edirectconnecttoplane": "direct",
        "relief": "relief",
        "reliefconnect": "relief",
        "reliefconnecttoplane": "relief",
        "ereliefconnecttoplane": "relief",
        "noconnect": "no_connect",
        "enoconnect": "no_connect",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} has unsupported style {value!r}",
        ) from exc


def resolve_pcb_net_class_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    nets: Iterable[ResolvedNetInput],
) -> tuple[ResolvedNetClassInput, ...]:
    """Resolve explicit `KIND=0` classes through exact source net occurrences."""

    source_index._assert_current_pcbdoc(pcbdoc)
    supplied_nets = tuple(nets)
    _replay_net_class_net_authority(pcbdoc, source_index, supplied_nets)
    nets_by_name = _net_class_net_authority(supplied_nets)
    source_classes = tuple(
        row for row in pcbdoc.net_classes if row._raw_record.get("KIND") == "0"
    )
    _validate_net_class_names(source_classes)
    return tuple(
        _resolve_explicit_net_class(
            row,
            ordinal=ordinal,
            source_index=source_index,
            nets_by_name=nets_by_name,
        )
        for ordinal, row in enumerate(source_classes)
        if _net_class_is_explicit(row)
    )


def resolve_pcb_component_class_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    components: Iterable[ResolvedComponentOccurrenceInput],
) -> tuple[ResolvedComponentClassInput, ...]:
    """Resolve explicit `KIND=1` classes through exact component occurrences."""

    source_index._assert_current_pcbdoc(pcbdoc)
    supplied_components = tuple(components)
    _replay_component_class_component_authority(
        pcbdoc,
        source_index,
        supplied_components,
    )
    components_by_designator = _component_class_component_authority(
        supplied_components,
        source_index,
    )
    source_classes = tuple(
        row for row in pcbdoc.net_classes if row._raw_record.get("KIND") == "1"
    )
    _validate_component_class_names(source_classes)
    return tuple(
        _resolve_explicit_component_class(
            row,
            ordinal=ordinal,
            source_index=source_index,
            components_by_designator=components_by_designator,
        )
        for ordinal, row in enumerate(source_classes)
        if _component_class_is_explicit(row)
    )


def _replay_component_class_component_authority(
    pcbdoc: AltiumPcbDoc,
    source_index: PcbDocSourceIndex,
    supplied: tuple[ResolvedComponentOccurrenceInput, ...],
) -> None:
    diagnostics: list[Diagnostic] = []
    expected = tuple(
        _resolve_component(
            component,
            ordinal=ordinal,
            source_index=source_index,
            strictness="strict",
            diagnostics=diagnostics,
        )
        for ordinal, component in enumerate(pcbdoc.components)
    )
    if len(supplied) != len(expected):
        raise PcbResolvedInputError(
            "corrupt_identity",
            "resolved component authority does not match the current Components6 source revision",
        )
    for supplied_component, expected_component in zip(supplied, expected, strict=True):
        if (
            supplied_component.id != expected_component.id
            or supplied_component.source != expected_component.source
        ):
            raise PcbResolvedInputError(
                "foreign_source_identity",
                "resolved component occurrence is not bound to the current Components6 source revision",
            )
        if supplied_component != expected_component:
            raise PcbResolvedInputError(
                "corrupt_identity",
                "resolved component facts do not match the current Components6 source record",
            )


def _component_class_component_authority(
    components: tuple[ResolvedComponentOccurrenceInput, ...],
    source_index: PcbDocSourceIndex,
) -> dict[str, tuple[ResolvedComponentOccurrenceInput, ...]]:
    by_designator: dict[str, list[ResolvedComponentOccurrenceInput]] = {}
    occurrence_ids: set[str] = set()
    for component in components:
        if component.id in occurrence_ids:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"duplicate resolved component occurrence {component.id!r}",
            )
        occurrence_ids.add(component.id)
        if (
            not isinstance(component.source, LocatedSource)
            or component.source.stream_name != "Components6/Data"
            or component.id != source_occurrence_ref("component", component.source)
            or not source_index._contains_located_source(component.source)
        ):
            raise PcbResolvedInputError(
                "foreign_source_identity",
                f"component occurrence {component.id!r} is not bound to the current source revision",
            )
        designator = component.display_designator
        if type(designator) is str and designator.strip():
            by_designator.setdefault(designator, []).append(component)
    return {name: tuple(rows) for name, rows in by_designator.items()}


def _validate_component_class_names(
    classes: tuple[AltiumPcbNetClass, ...],
) -> None:
    names: set[str] = set()
    for row in classes:
        raw_name = row._raw_record.get("NAME")
        if type(raw_name) is not str or not raw_name.strip() or row.name != raw_name:
            raise PcbResolvedInputError(
                "corrupt_identity", "KIND=1 class has no exact source name"
            )
        normalized = raw_name.casefold()
        if normalized in names:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"component class name {raw_name!r} identifies multiple source occurrences",
            )
        names.add(normalized)


def _component_class_is_explicit(row: AltiumPcbNetClass) -> bool:
    value = row._raw_record.get("SUPERCLASS")
    if value in (None, "FALSE"):
        return True
    if value == "TRUE":
        return False
    raise PcbResolvedInputError(
        "unsupported_class_record",
        f"component class {row.name!r} has unsupported SUPERCLASS value {value!r}",
    )


def _resolve_explicit_component_class(
    row: AltiumPcbNetClass,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    components_by_designator: dict[str, tuple[ResolvedComponentOccurrenceInput, ...]],
) -> ResolvedComponentClassInput:
    members = _exact_object_class_members(
        row,
        class_label="component class",
        case_insensitive_members=False,
    )
    member_refs = tuple(
        _resolve_component_class_member(
            member,
            class_name=row.name,
            components_by_designator=components_by_designator,
        )
        for member in members
    )
    source = source_index.source_for(row)
    if source.stream_name != "Classes6/Data":
        raise PcbResolvedInputError(
            "foreign_source_identity",
            f"component class {row.name!r} is not bound to Classes6/Data",
        )
    return ResolvedComponentClassInput(
        id=_input_ref("component_class", source, f"input.component_class.{ordinal}"),
        source=source,
        display_name=row.name,
        member_component_refs=member_refs,
    )


def _resolve_component_class_member(
    member: str,
    *,
    class_name: str,
    components_by_designator: dict[str, tuple[ResolvedComponentOccurrenceInput, ...]],
) -> str:
    matches = components_by_designator.get(member, ())
    if not matches:
        raise PcbResolvedInputError(
            "unresolved_class_member",
            f"component class {class_name!r} member {member!r} has no exact source component",
        )
    if len(matches) != 1:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"component class {class_name!r} member {member!r} identifies multiple source components",
        )
    return matches[0].id


def _replay_net_class_net_authority(
    pcbdoc: AltiumPcbDoc,
    source_index: PcbDocSourceIndex,
    supplied: tuple[ResolvedNetInput, ...],
) -> None:
    expected = tuple(
        ResolvedNetInput(
            id=source_occurrence_ref("net", source),
            source=source,
            display_name=net.name,
        )
        for net in pcbdoc.nets
        for source in (source_index.source_for(net),)
    )
    if len(supplied) != len(expected):
        raise PcbResolvedInputError(
            "corrupt_identity",
            "resolved net authority does not match the current Nets6 source revision",
        )
    for supplied_net, expected_net in zip(supplied, expected, strict=True):
        if (
            supplied_net.id != expected_net.id
            or supplied_net.source != expected_net.source
        ):
            raise PcbResolvedInputError(
                "foreign_source_identity",
                "resolved net occurrence is not bound to the current Nets6 source revision",
            )
        if supplied_net.display_name != expected_net.display_name:
            raise PcbResolvedInputError(
                "corrupt_identity",
                "resolved net name does not match the current Nets6 source record",
            )


def _net_class_net_authority(
    nets: tuple[ResolvedNetInput, ...],
) -> dict[str, ResolvedNetInput]:
    result: dict[str, ResolvedNetInput] = {}
    ids: set[str] = set()
    for net in nets:
        if net.id in ids:
            raise PcbResolvedInputError(
                "corrupt_identity", f"duplicate resolved net occurrence {net.id!r}"
            )
        ids.add(net.id)
        if type(net.display_name) is not str or not net.display_name.strip():
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"resolved net occurrence {net.id!r} has no exact display name",
            )
        normalized = net.display_name.casefold()
        if normalized in result:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"net name {net.display_name!r} identifies multiple source occurrences",
            )
        result[normalized] = net
    return result


def _validate_net_class_names(classes: tuple[AltiumPcbNetClass, ...]) -> None:
    names: set[str] = set()
    for row in classes:
        raw_name = row._raw_record.get("NAME")
        if type(raw_name) is not str or not raw_name.strip() or row.name != raw_name:
            raise PcbResolvedInputError(
                "corrupt_identity", "KIND=0 class has no exact source name"
            )
        normalized = raw_name.casefold()
        if normalized in names:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"net class name {raw_name!r} identifies multiple source occurrences",
            )
        names.add(normalized)


def _net_class_is_explicit(row: AltiumPcbNetClass) -> bool:
    value = row._raw_record.get("SUPERCLASS")
    if value in (None, "FALSE"):
        return True
    if value == "TRUE":
        return False
    raise PcbResolvedInputError(
        "unsupported_class_record",
        f"net class {row.name!r} has unsupported SUPERCLASS value {value!r}",
    )


def _resolve_explicit_net_class(
    row: AltiumPcbNetClass,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    nets_by_name: dict[str, ResolvedNetInput],
) -> ResolvedNetClassInput:
    members = _exact_net_class_members(row)
    member_refs = tuple(
        _resolve_net_class_member(
            member,
            class_name=row.name,
            source_index=source_index,
            nets_by_name=nets_by_name,
        )
        for member in members
    )
    source = source_index.source_for(row)
    if source.stream_name != "Classes6/Data":
        raise PcbResolvedInputError(
            "foreign_source_identity",
            f"net class {row.name!r} is not bound to Classes6/Data",
        )
    return ResolvedNetClassInput(
        id=_input_ref("net_class", source, f"input.net_class.{ordinal}"),
        source=source,
        display_name=row.name,
        member_net_refs=member_refs,
    )


def _exact_net_class_members(row: AltiumPcbNetClass) -> tuple[str, ...]:
    return _exact_object_class_members(
        row,
        class_label="net class",
        case_insensitive_members=True,
    )


def _exact_object_class_members(
    row: AltiumPcbNetClass,
    *,
    class_label: str,
    case_insensitive_members: bool,
) -> tuple[str, ...]:
    member_keys = _net_class_member_keys(row)
    expected_keys = tuple(f"M{index}" for index in range(len(member_keys)))
    if member_keys != expected_keys:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{class_label} {row.name!r} has noncontiguous member records",
        )
    members = tuple(row._raw_record[key] for key in member_keys)
    _validate_object_class_member_values(row, members, class_label=class_label)
    _validate_object_class_member_count(row, len(members), class_label=class_label)
    _validate_unique_object_class_members(
        row,
        members,
        class_label=class_label,
        case_insensitive=case_insensitive_members,
    )
    return members


def _net_class_member_keys(row: AltiumPcbNetClass) -> tuple[str, ...]:
    keys = (key for key in row._raw_record if re.fullmatch(r"M\d+", key))
    return tuple(sorted(keys, key=lambda key: int(key[1:])))


def _validate_object_class_member_values(
    row: AltiumPcbNetClass,
    members: tuple[str, ...],
    *,
    class_label: str,
) -> None:
    if any(type(member) is not str or not member.strip() for member in members):
        raise PcbResolvedInputError(
            "corrupt_identity", f"{class_label} {row.name!r} has an empty member name"
        )
    if members != tuple(row.members):
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{class_label} {row.name!r} parsed members disagree with source records",
        )


def _validate_unique_object_class_members(
    row: AltiumPcbNetClass,
    members: tuple[str, ...],
    *,
    class_label: str,
    case_insensitive: bool,
) -> None:
    normalized = tuple(
        member.casefold() if case_insensitive else member for member in members
    )
    if len(set(normalized)) != len(normalized):
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{class_label} {row.name!r} contains duplicate member names",
        )


def _validate_object_class_member_count(
    row: AltiumPcbNetClass,
    actual: int,
    *,
    class_label: str,
) -> None:
    raw_count = row._raw_record.get("MEMBERCOUNT")
    if raw_count is None:
        return
    try:
        parsed_count = int(raw_count)
    except (TypeError, ValueError) as exc:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{class_label} {row.name!r} has malformed MEMBERCOUNT {raw_count!r}",
        ) from exc
    if parsed_count != actual:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{class_label} {row.name!r} MEMBERCOUNT does not match member records",
        )


def _resolve_net_class_member(
    member: str,
    *,
    class_name: str,
    source_index: PcbDocSourceIndex,
    nets_by_name: dict[str, ResolvedNetInput],
) -> str:
    try:
        net = nets_by_name[member.casefold()]
    except KeyError as exc:
        raise PcbResolvedInputError(
            "unresolved_class_member",
            f"net class {class_name!r} member {member!r} has no exact source net",
        ) from exc
    if (
        not isinstance(net.source, LocatedSource)
        or net.source.stream_name != "Nets6/Data"
        or net.id != source_occurrence_ref("net", net.source)
        or not source_index._contains_located_source(net.source)
    ):
        raise PcbResolvedInputError(
            "foreign_source_identity",
            f"net class {class_name!r} member {member!r} is not bound to the current source revision",
        )
    return net.id


def resolve_pcb_embedded_board_references(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
) -> tuple[ResolvedEmbeddedBoardReferenceInput, ...]:
    """Resolve stored child references without loading or expanding a child board."""

    if strictness not in {"strict", "permissive"}:
        raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
    source_index.assert_current()
    parse_error = pcbdoc._embedded_board_parse_error
    if parse_error is not None:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"EmbeddedBoards6 source records are malformed: {parse_error}",
        )
    return tuple(
        _resolve_embedded_board_reference(
            record,
            ordinal=ordinal,
            source_index=source_index,
            strictness=strictness,
        )
        for ordinal, record in enumerate(pcbdoc.embedded_boards)
    )


def _resolve_embedded_board_reference(
    record: AltiumPcbEmbeddedBoard,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
) -> ResolvedEmbeddedBoardReferenceInput:
    fallback_ref = f"input.embedded_board.{ordinal}"
    resolution = source_index.resolve(
        record,
        strictness=strictness,
        affected_ref=fallback_ref,
    )
    input_id = _input_ref("embedded_board", resolution.source, fallback_ref)
    document_path = _embedded_board_required_text(
        record.document_path,
        input_id,
        "DOCUMENTPATH",
    )
    placement_layer = _embedded_board_required_text(
        record.layer,
        input_id,
        "LAYER",
    )
    return ResolvedEmbeddedBoardReferenceInput(
        id=input_id,
        source=resolution.source,
        requested_document_path=document_path,
        placement_layer=placement_layer,
        origin_x_source_units=_embedded_board_length(record.x, input_id, "X"),
        origin_y_source_units=_embedded_board_length(record.y, input_id, "Y"),
        bounds_x1_source_units=_embedded_board_length(record.x1, input_id, "X1"),
        bounds_y1_source_units=_embedded_board_length(record.y1, input_id, "Y1"),
        bounds_x2_source_units=_embedded_board_length(record.x2, input_id, "X2"),
        bounds_y2_source_units=_embedded_board_length(record.y2, input_id, "Y2"),
        rotation_degrees_e12=_embedded_board_decimal_scaled_int(
            record.rotation,
            decimal_places=12,
            affected_ref=input_id,
            field_name="ROTATION",
            minimum=_MIN_INT64,
            maximum=_MAX_INT64,
        ),
        mirror=_embedded_board_bool(record.mirror, input_id, "MIRROR"),
        row_count=_embedded_board_count(record.row_count, input_id, "ROWCOUNT"),
        column_count=_embedded_board_count(
            record.column_count,
            input_id,
            "COLCOUNT",
        ),
        row_spacing_source_units=_embedded_board_length(
            record.row_spacing,
            input_id,
            "ROWSPACING",
        ),
        column_spacing_source_units=_embedded_board_length(
            record.column_spacing,
            input_id,
            "COLSPACING",
        ),
        origin_mode=_embedded_board_nonnegative_int(
            record.origin_mode,
            input_id,
            "ORIGINMODE",
        ),
        resolution_disposition="child_provider_required",
        repeat_disposition="source_array_unexpanded",
        diagnostics=resolution.diagnostics,
    )


def resolve_pcb_child_request_outcome(
    request: ResolvedEmbeddedBoardReferenceInput,
    *,
    parent_board_occurrence_ref: str,
    owner_logical_path: str,
    provider: PcbChildDocumentProvider,
    strictness: Strictness,
    expected_revision_sha256: str | None = None,
    active_revisions: Sequence[PcbChildRevisionIdentity] = (),
) -> PcbChildRequestOutcome:
    """Resolve one retained child request without expanding board occurrences."""

    if strictness not in {"strict", "permissive"}:
        raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
    if not parent_board_occurrence_ref:
        raise ValueError("parent_board_occurrence_ref must be nonempty")
    row_id = request.id
    resolved_owner_logical_path = _child_request_owner_logical_path(
        request,
        owner_logical_path,
    )
    try:
        loaded = provider.load(
            owner_logical_path=resolved_owner_logical_path,
            requested_document_path=request.requested_document_path,
            expected_revision_sha256=expected_revision_sha256,
            active_revisions=active_revisions,
        )
    except PcbChildDocumentProviderError as exc:
        code = _child_document_diagnostic_code(exc)
        if strictness == "strict":
            raise PcbResolvedInputError(
                code,
                f"{row_id} {exc.detail}",
            ) from exc
        diagnostic = _child_request_diagnostic(
            row_id=row_id,
            request=request,
            code=code,
            provider_error=exc,
        )
        retained_expected_revision = (
            exc.expected_revision_sha256
            if exc.expected_revision_sha256 is not None
            else expected_revision_sha256
        )
        row = ChildBoardRequest(
            id=row_id,
            parent_board_occurrence_ref=parent_board_occurrence_ref,
            source=request.source,
            requested_document_path=request.requested_document_path,
            provider_id=provider.provider_id,
            expected_revision_sha256=(
                UNSET
                if retained_expected_revision is None
                else retained_expected_revision
            ),
            disposition="unavailable",
            resolved_logical_path=(
                UNSET if exc.logical_path is None else exc.logical_path
            ),
            observed_revision_sha256=(
                UNSET
                if exc.observed_revision_sha256 is None
                else exc.observed_revision_sha256
            ),
            diagnostic_ref=diagnostic.id,
        )
        return PcbChildRequestOutcome(
            construction_token=_CHILD_OUTCOME_CONSTRUCTION_TOKEN,
            request=request,
            row=row,
            loaded_child=None,
            provider_error=exc,
            diagnostics=(*request.diagnostics, diagnostic),
        )
    row = ChildBoardRequest(
        id=row_id,
        parent_board_occurrence_ref=parent_board_occurrence_ref,
        source=request.source,
        requested_document_path=request.requested_document_path,
        provider_id=loaded.identity.provider_id,
        expected_revision_sha256=(
            UNSET if expected_revision_sha256 is None else expected_revision_sha256
        ),
        disposition="loaded",
        resolved_logical_path=loaded.identity.logical_path,
        document_revision_sha256=loaded.identity.document_revision_sha256,
    )
    return PcbChildRequestOutcome(
        construction_token=_CHILD_OUTCOME_CONSTRUCTION_TOKEN,
        request=request,
        row=row,
        loaded_child=loaded,
        provider_error=None,
        diagnostics=request.diagnostics,
    )


def resolve_pcb_child_board_occurrences(
    request: ResolvedEmbeddedBoardReferenceInput,
    outcome: PcbChildRequestOutcome,
    *,
    parent_affine: PcbDecimalAffine2d,
    max_occurrences: int,
) -> tuple[PcbResolvedChildBoardOccurrence, ...]:
    """Expand one loaded request in native row-major order without child geometry."""

    if max_occurrences < 1:
        raise ValueError("max_occurrences must be positive")
    loaded = _verified_loaded_child(request, outcome)
    if loaded is None:
        return ()
    count = request.row_count * request.column_count
    if count > max_occurrences:
        raise PcbResolvedInputError(
            "child_resource_limit",
            f"{request.id} expands to {count} child occurrences",
        )
    anchor_x, anchor_y = _child_board_anchor_source_units(request, loaded)
    occurrences: list[PcbResolvedChildBoardOccurrence] = []
    for row_index in range(request.row_count):
        for column_index in range(request.column_count):
            occurrences.append(
                _resolved_child_board_occurrence(
                    request,
                    outcome,
                    loaded.identity,
                    parent_affine,
                    anchor_x,
                    anchor_y,
                    row_index,
                    column_index,
                )
            )
    return tuple(occurrences)


def resolve_pcb_child_board_occurrence_rows(
    request: ResolvedEmbeddedBoardReferenceInput,
    outcome: PcbChildRequestOutcome,
    *,
    parent_affine: PcbDecimalAffine2d,
    max_occurrences: int,
) -> tuple[tuple[BoardOccurrence, ...], tuple[Diagnostic, ...]]:
    """Resolve normalized child rows and their owner-bound diagnostics."""

    occurrences = resolve_pcb_child_board_occurrences(
        request,
        outcome,
        parent_affine=parent_affine,
        max_occurrences=max_occurrences,
    )
    rows: list[BoardOccurrence] = []
    diagnostics: list[Diagnostic] = []
    for occurrence in occurrences:
        row, diagnostic = _normalized_child_board_occurrence(occurrence)
        rows.append(row)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
    return tuple(rows), tuple(diagnostics)


def _normalized_child_board_occurrence(
    occurrence: PcbResolvedChildBoardOccurrence,
) -> tuple[BoardOccurrence, Diagnostic | None]:
    identity = occurrence.child_identity
    source, diagnostic = _child_occurrence_source(occurrence)
    return BoardOccurrence(
        id=occurrence.id,
        source=source,
        parent_occurrence_ref=occurrence.parent_board_occurrence_ref,
        child_request_ref=occurrence.request_ref,
        provider_id=identity.provider_id,
        resolved_logical_path=identity.logical_path,
        document_revision_sha256=identity.document_revision_sha256,
        row_index=occurrence.row_index,
        column_index=occurrence.column_index,
        step_row_index=occurrence.step_row_index,
        step_column_index=occurrence.step_column_index,
        affine=occurrence.affine,
    ), diagnostic


def _child_occurrence_source(
    occurrence: PcbResolvedChildBoardOccurrence,
) -> tuple[SourceProvenance, Diagnostic | None]:
    source = occurrence.source
    if not isinstance(source, UnresolvedSource):
        return source, None
    diagnostic_ref = f"diagnostic.{occurrence.id}.unresolved_source"
    return UnresolvedSource(
        diagnostic_ref=diagnostic_ref,
        reason=source.reason,
    ), Diagnostic(
        id=diagnostic_ref,
        code="unresolved_source",
        severity="warning",
        message="Source provenance is degraded; board occurrence is retained.",
        affected_ref=occurrence.id,
    )


def _verified_loaded_child(
    request: ResolvedEmbeddedBoardReferenceInput,
    outcome: PcbChildRequestOutcome,
) -> PcbChildDocumentLoad | None:
    if type(outcome) is not PcbChildRequestOutcome:
        raise TypeError("outcome must be a resolver-created PcbChildRequestOutcome")
    if outcome.request != request:
        raise ValueError(
            "child request outcome does not belong to the resolved request"
        )
    row = outcome.row
    if (row.id, row.source) != (request.id, request.source):
        raise ValueError(
            "child request outcome does not belong to the resolved request"
        )
    if outcome.loaded_child is None:
        _assert_unavailable_child_outcome(outcome)
        return None
    loaded = outcome.loaded_child
    _assert_loaded_child_outcome(outcome, loaded)
    source_index = PcbDocSourceIndex.from_pcbdoc(
        loaded.document,
        logical_path=loaded.identity.logical_path,
    )
    if (
        source_index.document_revision_sha256
        != loaded.identity.document_revision_sha256
    ):
        raise PcbResolvedInputError(
            "changed_child_revision",
            f"{request.id} parsed child revision does not match provider identity",
        )
    return loaded


def _assert_unavailable_child_outcome(outcome: PcbChildRequestOutcome) -> None:
    if (outcome.row.disposition, outcome.provider_error is None) != (
        "unavailable",
        False,
    ):
        raise ValueError("child request outcome has contradictory unavailable state")


def _assert_loaded_child_outcome(
    outcome: PcbChildRequestOutcome,
    loaded: PcbChildDocumentLoad,
) -> None:
    identity = loaded.identity
    observed = (
        outcome.row.disposition,
        outcome.provider_error,
        outcome.row.provider_id,
        outcome.row.resolved_logical_path,
        outcome.row.document_revision_sha256,
    )
    expected = (
        "loaded",
        None,
        identity.provider_id,
        identity.logical_path,
        identity.document_revision_sha256,
    )
    if observed != expected:
        raise ValueError("child request outcome has contradictory loaded identity")


def _child_board_anchor_source_units(
    request: ResolvedEmbeddedBoardReferenceInput,
    loaded: PcbChildDocumentLoad,
) -> tuple[int, int]:
    if request.origin_mode != 1:
        raise PcbResolvedInputError(
            "unsupported_embedded_board_origin_mode",
            f"{request.id} origin mode {request.origin_mode} is not promoted yet",
        )
    board = loaded.document.board
    if board is None:
        raise PcbResolvedInputError(
            "invalid_child_board_origin",
            f"{request.id} child has no Board6 record",
        )
    raw_board = board.raw_record
    try:
        origin_x = str(raw_board["ORIGINX"])
        origin_y = str(raw_board["ORIGINY"])
    except KeyError as exc:
        raise PcbResolvedInputError(
            "invalid_child_board_origin",
            f"{request.id} child Board6 origin is incomplete",
        ) from exc
    return (
        _embedded_board_length(origin_x, request.id, "CHILD.ORIGINX"),
        _embedded_board_length(origin_y, request.id, "CHILD.ORIGINY"),
    )


def _resolved_child_board_occurrence(
    request: ResolvedEmbeddedBoardReferenceInput,
    outcome: PcbChildRequestOutcome,
    child_identity: PcbChildRevisionIdentity,
    parent_affine: PcbDecimalAffine2d,
    anchor_x_source_units: int,
    anchor_y_source_units: int,
    row_index: int,
    column_index: int,
) -> PcbResolvedChildBoardOccurrence:
    step_row_index = request.row_count - row_index - 1 if request.mirror else row_index
    step_column_index = (
        request.column_count - column_index - 1 if request.mirror else column_index
    )
    repeat_offset_x = _embedded_board_repeat_offset(
        request.column_spacing_source_units,
        step_column_index,
        request.id,
    )
    repeat_offset_y = _embedded_board_repeat_offset(
        request.row_spacing_source_units,
        step_row_index,
        request.id,
    )
    local_affine = _embedded_board_local_affine(
        request,
        anchor_x_source_units=anchor_x_source_units,
        anchor_y_source_units=anchor_y_source_units,
        repeat_offset_x_source_units=repeat_offset_x,
        repeat_offset_y_source_units=repeat_offset_y,
    )
    occurrence_id = _child_board_occurrence_ref(
        request,
        outcome.row.parent_board_occurrence_ref,
        child_identity,
        row_index,
        column_index,
    )
    return PcbResolvedChildBoardOccurrence(
        id=occurrence_id,
        request_ref=request.id,
        parent_board_occurrence_ref=outcome.row.parent_board_occurrence_ref,
        source=request.source,
        child_identity=child_identity,
        row_index=row_index,
        column_index=column_index,
        step_row_index=step_row_index,
        step_column_index=step_column_index,
        child_anchor_x_source_units=anchor_x_source_units,
        child_anchor_y_source_units=anchor_y_source_units,
        repeat_offset_x_source_units=repeat_offset_x,
        repeat_offset_y_source_units=repeat_offset_y,
        local_to_parent_affine=local_affine,
        affine=compose_affines(parent_affine, local_affine),
    )


def _embedded_board_repeat_offset(spacing: int, index: int, owner: str) -> int:
    result = spacing * index
    if not _MIN_INT64 <= result <= _MAX_INT64:
        raise PcbResolvedInputError(
            "integer_overflow",
            f"{owner} repeat offset exceeds signed int64 source units",
        )
    return result


def _embedded_board_local_affine(
    request: ResolvedEmbeddedBoardReferenceInput,
    *,
    anchor_x_source_units: int,
    anchor_y_source_units: int,
    repeat_offset_x_source_units: int,
    repeat_offset_y_source_units: int,
) -> PcbDecimalAffine2d:
    rotation = rotation_affine_degrees(request.rotation_degrees_e12 / 10**12)
    linear = PcbDecimalAffine2d(
        type="pcb.manufacturing.affine2d.decimal_e15",
        a_e15=-rotation.a_e15 if request.mirror else rotation.a_e15,
        b_e15=-rotation.b_e15 if request.mirror else rotation.b_e15,
        c_e15=rotation.c_e15,
        d_e15=rotation.d_e15,
        tx_nm=0,
        ty_nm=0,
        composition_depth=0,
    )
    pre_rotation = Point2d(
        x_nm=pcb_internal_to_nm(repeat_offset_x_source_units - anchor_x_source_units),
        y_nm=pcb_internal_to_nm(repeat_offset_y_source_units - anchor_y_source_units),
    )
    transformed = apply_affine(linear, pre_rotation)
    tx_nm = pcb_internal_to_nm(request.origin_x_source_units) + transformed.x_nm
    ty_nm = pcb_internal_to_nm(request.origin_y_source_units) + transformed.y_nm
    if not _MIN_INT64 <= tx_nm <= _MAX_INT64 or not _MIN_INT64 <= ty_nm <= _MAX_INT64:
        raise PcbResolvedInputError(
            "integer_overflow",
            f"{request.id} child occurrence translation exceeds signed int64 nanometers",
        )
    return PcbDecimalAffine2d(
        type=linear.type,
        a_e15=linear.a_e15,
        b_e15=linear.b_e15,
        c_e15=linear.c_e15,
        d_e15=linear.d_e15,
        tx_nm=tx_nm,
        ty_nm=ty_nm,
        composition_depth=0,
    )


def _child_board_occurrence_ref(
    request: ResolvedEmbeddedBoardReferenceInput,
    parent_ref: str,
    identity: PcbChildRevisionIdentity,
    row_index: int,
    column_index: int,
) -> str:
    payload = "\0".join(
        (
            "pcb.manufacturing.child_occurrence.a0",
            parent_ref,
            request.id,
            identity.provider_id,
            identity.logical_path,
            identity.document_revision_sha256,
            str(row_index),
            str(column_index),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"board_occurrence.{digest}"


def _child_request_owner_logical_path(
    request: ResolvedEmbeddedBoardReferenceInput,
    fallback: str,
) -> str:
    if isinstance(request.source, LocatedSource):
        if fallback != request.source.logical_path:
            raise ValueError("owner_logical_path must match the located request source")
        return request.source.logical_path
    if not fallback:
        raise ValueError(
            "owner_logical_path is required when request provenance has no logical path"
        )
    return fallback


def _child_document_diagnostic_code(
    error: PcbChildDocumentProviderError,
) -> DiagnosticCode:
    try:
        return _CHILD_DOCUMENT_DIAGNOSTIC_CODES[error.code]
    except KeyError as exc:  # pragma: no cover - closed provider error inventory
        raise RuntimeError(f"unknown child provider error: {error.code}") from exc


def _child_request_diagnostic(
    *,
    row_id: str,
    request: ResolvedEmbeddedBoardReferenceInput,
    code: DiagnosticCode,
    provider_error: PcbChildDocumentProviderError,
) -> Diagnostic:
    if isinstance(request.source, UnresolvedSource):
        return Diagnostic(
            id=f"diagnostic.{row_id}.{code}",
            code=code,
            severity="error",
            message=f"{row_id}: {provider_error.detail}",
            affected_ref=row_id,
        )
    return Diagnostic(
        id=f"diagnostic.{row_id}.{code}",
        code=code,
        severity="error",
        message=f"{row_id}: {provider_error.detail}",
        source=request.source,
        affected_ref=row_id,
    )


def _embedded_board_required_text(
    value: str,
    affected_ref: str,
    field_name: str,
) -> str:
    if not value or "\x00" in value:
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} is missing or malformed",
        )
    return value


def _embedded_board_length(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int:
    text = value.strip()
    if len(text) < 4 or text[-3:].casefold() != "mil":
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} is not an Altium mil value",
        )
    return _embedded_board_decimal_scaled_int(
        text[:-3].strip(),
        decimal_places=4,
        affected_ref=affected_ref,
        field_name=field_name,
        minimum=-(1 << 31),
        maximum=(1 << 31) - 1,
    )


def _embedded_board_count(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int:
    count = _embedded_board_nonnegative_int(value, affected_ref, field_name)
    if count < 1:
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} must be positive",
        )
    return count


def _embedded_board_nonnegative_int(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int:
    try:
        result = int(value.strip())
    except ValueError as exc:
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} is not an integer",
        ) from exc
    if not 0 <= result <= (1 << 31) - 1:
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} is outside the nonnegative int32 range",
        )
    return result


def _embedded_board_decimal_scaled_int(
    value: str,
    *,
    decimal_places: int,
    affected_ref: str,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    text = value.strip()
    if len(text) > 128:
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} exceeds the supported decimal length",
        )
    try:
        result = _exact_decimal_scaled_int(
            text,
            decimal_places=decimal_places,
            affected_ref=affected_ref,
            field_name=field_name,
            invalid_description="finite decimal",
            fractional_description="beyond the supported exact precision",
        )
    except PcbResolvedInputError as exc:
        detail = (
            "is outside the supported range"
            if exc.code == "integer_overflow"
            else "is not a finite decimal or exceeds the supported exact precision"
        )
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} {detail}",
        ) from exc
    if not minimum <= result <= maximum:
        raise PcbResolvedInputError(
            "invalid_embedded_board_reference",
            f"{affected_ref} {field_name} is outside the supported range",
        )
    return result


def _embedded_board_bool(
    value: str,
    affected_ref: str,
    field_name: str,
) -> bool:
    if value == "TRUE":
        return True
    if value == "FALSE":
        return False
    raise PcbResolvedInputError(
        "invalid_embedded_board_reference",
        f"{affected_ref} {field_name} is not an exact Altium Boolean",
    )


def resolve_pcb_stored_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    include_routes: bool = True,
    include_physical_features: bool = True,
    include_profile: bool = False,
) -> ResolvedPcbInputs:
    """Resolve direct stored nets, tracks, arcs, and pads before geometry."""

    if strictness not in {"strict", "permissive"}:
        raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
    source_index.assert_current()

    diagnostics: list[Diagnostic] = []
    profile, cutouts = (
        _resolve_profile_inputs(
            pcbdoc,
            source_index=source_index,
            strictness=strictness,
            diagnostics=diagnostics,
        )
        if include_profile
        else (None, ())
    )
    nets, components = _resolve_ownership_inputs(
        pcbdoc,
        enabled=include_routes or include_physical_features,
        source_index=source_index,
        layer_stack=layer_stack,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    net_refs = tuple(net.id for net in nets)
    component_refs = tuple(component.id for component in components)
    tracks: tuple[ResolvedTrackInput, ...] = ()
    arcs: tuple[ResolvedArcInput, ...] = ()
    if include_routes:
        tracks, arcs = _resolve_route_inputs(
            pcbdoc,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            component_refs=component_refs,
            diagnostics=diagnostics,
        )
    pads: tuple[ResolvedPadInput, ...] = ()
    vias: tuple[ResolvedViaInput, ...] = ()
    fills: tuple[ResolvedFillInput, ...] = ()
    polygons: tuple[ResolvedPolygonInput, ...] = ()
    plane_rule_authority: ResolvedPlaneRuleAuthority | None = None
    clearance_rule_authority: ResolvedClearanceRuleAuthority | None = None
    polygon_connect_rule_authority: ResolvedPolygonConnectRuleAuthority | None = None
    if include_physical_features:
        plane_rule_authority = resolve_pcb_plane_rule_authority(
            pcbdoc,
            source_index=source_index,
            strictness=strictness,
        )
        diagnostics.extend(plane_rule_authority.diagnostics)
        clearance_rule_authority = resolve_pcb_clearance_rule_authority(
            pcbdoc,
            source_index=source_index,
            strictness=strictness,
        )
        diagnostics.extend(clearance_rule_authority.diagnostics)
        polygon_connect_rule_authority = resolve_pcb_polygon_connect_rule_authority(
            pcbdoc,
            source_index=source_index,
            strictness=strictness,
        )
        diagnostics.extend(polygon_connect_rule_authority.diagnostics)
        pads, vias, fills, polygons = _resolve_physical_inputs(
            pcbdoc,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            component_refs=component_refs,
            diagnostics=diagnostics,
        )
    return ResolvedPcbInputs(
        nets=nets,
        components=components,
        tracks=tracks,
        arcs=arcs,
        pads=pads,
        vias=vias,
        fills=fills,
        plane_rule_authority=plane_rule_authority,
        profile=profile,
        cutouts=cutouts,
        diagnostics=tuple(diagnostics),
        polygons=polygons,
        clearance_rule_authority=clearance_rule_authority,
        polygon_connect_rule_authority=polygon_connect_rule_authority,
    )


def _resolve_ownership_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    enabled: bool,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> tuple[
    tuple[ResolvedNetInput, ...],
    tuple[ResolvedComponentOccurrenceInput, ...],
]:
    if not enabled:
        return (), ()
    _assert_unique_layer_stack_authority(layer_stack, "pcbdoc")
    return _resolve_nets_and_components(
        pcbdoc,
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )


def _resolve_nets_and_components(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> tuple[
    tuple[ResolvedNetInput, ...],
    tuple[ResolvedComponentOccurrenceInput, ...],
]:
    nets = tuple(
        _resolve_net(
            net,
            ordinal=ordinal,
            source_index=source_index,
            strictness=strictness,
            diagnostics=diagnostics,
        )
        for ordinal, net in enumerate(pcbdoc.nets)
    )
    components = _resolve_component_inputs(
        pcbdoc,
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return nets, components


def _resolve_component_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> tuple[ResolvedComponentOccurrenceInput, ...]:
    return tuple(
        _resolve_component(
            component,
            ordinal=ordinal,
            source_index=source_index,
            strictness=strictness,
            diagnostics=diagnostics,
        )
        for ordinal, component in enumerate(pcbdoc.components)
    )


def _resolve_route_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    component_refs: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> tuple[tuple[ResolvedTrackInput, ...], tuple[ResolvedArcInput, ...]]:
    tracks = tuple(
        _resolve_track(
            track,
            ordinal=ordinal,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            component_refs=component_refs,
            diagnostics=diagnostics,
        )
        for ordinal, track in enumerate(pcbdoc.tracks)
    )
    arcs = tuple(
        _resolve_arc(
            arc,
            ordinal=ordinal,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            component_refs=component_refs,
            diagnostics=diagnostics,
        )
        for ordinal, arc in enumerate(pcbdoc.arcs)
    )
    return tracks, arcs


def _resolve_physical_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    component_refs: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> tuple[
    tuple[ResolvedPadInput, ...],
    tuple[ResolvedViaInput, ...],
    tuple[ResolvedFillInput, ...],
    tuple[ResolvedPolygonInput, ...],
]:
    solder_mask_cache: dict[ManufacturingRuleQuery, _SolderMaskRuleFacts | None] = {}
    paste_mask_cache: dict[ManufacturingRuleQuery, _PasteMaskRuleFacts | None] = {}
    pads = tuple(
        _resolve_pad(
            pad,
            ordinal=ordinal,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            component_refs=component_refs,
            rules=pcbdoc.rules,
            solder_mask_cache=solder_mask_cache,
            paste_mask_cache=paste_mask_cache,
            diagnostics=diagnostics,
        )
        for ordinal, pad in enumerate(pcbdoc.pads)
    )
    vias = tuple(
        _resolve_via(
            via,
            ordinal=ordinal,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            component_refs=component_refs,
            rules=pcbdoc.rules,
            solder_mask_cache=solder_mask_cache,
            diagnostics=diagnostics,
        )
        for ordinal, via in enumerate(pcbdoc.vias)
    )
    fills = tuple(
        _resolve_fill(
            fill,
            ordinal=ordinal,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            component_refs=component_refs,
            diagnostics=diagnostics,
        )
        for ordinal, fill in enumerate(pcbdoc.fills)
    )
    polygons = tuple(
        _resolve_polygon(
            polygon,
            ordinal=ordinal,
            source_index=source_index,
            layer_stack=layer_stack,
            strictness=strictness,
            net_refs=net_refs,
            diagnostics=diagnostics,
        )
        for ordinal, polygon in enumerate(pcbdoc.polygons)
    )
    _assert_unique_resolved_polygon_ids(polygons)
    return pads, vias, fills, polygons


def _resolve_profile_inputs(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> tuple[ResolvedProfileInput | None, tuple[ResolvedProfileInput, ...]]:
    board = pcbdoc.board
    outline = board.outline if board is not None else None
    if board is None or outline is None or not outline.vertices:
        return None, ()
    board_resolution = source_index.resolve(
        board,
        strictness=strictness,
        affected_ref="board.root",
    )
    diagnostics.extend(board_resolution.diagnostics)
    profile = ResolvedProfileInput(
        id="input.profile.outer",
        source=board_resolution.source,
        operation="outer",
        vertices=_resolved_profile_vertices(outline.vertices, "input.profile.outer"),
    )
    cutouts = tuple(
        _resolve_cutout(
            region,
            vertices,
            ordinal=ordinal,
            source_index=source_index,
            strictness=strictness,
            diagnostics=diagnostics,
        )
        for ordinal, (region, vertices) in enumerate(_authoritative_cutouts(pcbdoc))
    )
    return profile, cutouts


def _authoritative_cutouts(
    pcbdoc: AltiumPcbDoc,
) -> tuple[tuple[object, list[BoardOutlineVertex]], ...]:
    shapebased = _cutout_rows(pcbdoc, tuple(pcbdoc.shapebased_regions))
    if shapebased:
        return shapebased
    return _cutout_rows(pcbdoc, tuple(pcbdoc.regions))


def _cutout_rows(
    pcbdoc: AltiumPcbDoc,
    regions: tuple[object, ...],
) -> tuple[tuple[object, list[BoardOutlineVertex]], ...]:
    rows: list[tuple[object, list[BoardOutlineVertex]]] = []
    for region in regions:
        if not pcbdoc._is_board_cutout_region(region):
            continue
        vertices = pcbdoc._region_cutout_vertices(region)
        if vertices:
            rows.append((region, vertices))
    return tuple(rows)


def _resolve_cutout(
    region: object,
    vertices: list[BoardOutlineVertex],
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> ResolvedProfileInput:
    source, input_id = _record_source_and_ref(
        region,
        kind="profile_cutout",
        fallback_ref=f"input.profile.cutout.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return ResolvedProfileInput(
        id=input_id,
        source=source,
        operation="cutout",
        vertices=_resolved_profile_vertices(vertices, input_id),
    )


def _resolved_profile_vertices(
    vertices: list[BoardOutlineVertex],
    affected_ref: str,
) -> tuple[ResolvedProfileVertex, ...]:
    if not vertices:
        raise PcbResolvedInputError(
            "unsupported_geometry", f"{affected_ref} has no vertices"
        )
    if len(vertices) == 1 and not vertices[0].is_arc:
        raise PcbResolvedInputError(
            "unsupported_geometry",
            f"{affected_ref} has one non-circular vertex",
        )
    resolved: list[ResolvedProfileVertex] = []
    for index, vertex in enumerate(vertices):
        end_vertex = vertices[(index + 1) % len(vertices)]
        if not vertex.is_arc:
            resolved.append(
                ResolvedProfileVertex(
                    x_source_units=_mils_to_source_units(vertex.x_mils, affected_ref),
                    y_source_units=_mils_to_source_units(vertex.y_mils, affected_ref),
                    is_arc=False,
                )
            )
            continue
        clockwise, sweep = resolve_outline_arc_segment(vertex, end_vertex)
        sweep_e12 = int(
            (Decimal(str(sweep)) * _ARC_SWEEP_SCALE).quantize(
                Decimal(1), rounding=ROUND_HALF_EVEN
            )
        )
        if not 0 < sweep_e12 <= 360 * _ARC_SWEEP_SCALE:
            raise PcbResolvedInputError(
                "unsupported_geometry", f"{affected_ref} has an invalid arc sweep"
            )
        resolved.append(
            ResolvedProfileVertex(
                x_source_units=_mils_to_source_units(vertex.x_mils, affected_ref),
                y_source_units=_mils_to_source_units(vertex.y_mils, affected_ref),
                is_arc=True,
                center_x_source_units=_mils_to_source_units(
                    vertex.center_x_mils, affected_ref
                ),
                center_y_source_units=_mils_to_source_units(
                    vertex.center_y_mils, affected_ref
                ),
                clockwise=clockwise,
                sweep_degrees_e12=sweep_e12,
            )
        )
    return tuple(resolved)


def _mils_to_source_units(value: float, affected_ref: str) -> int:
    source_units = Decimal(str(value)) * Decimal(10_000)
    integral = source_units.to_integral_value(rounding=ROUND_HALF_EVEN)
    if source_units != integral:
        raise PcbResolvedInputError(
            "unsupported_geometry",
            f"{affected_ref} profile coordinate is off the PCB source grid",
        )
    return int(integral)


def resolve_pcb_layer_binding(
    state: PcbPrimitiveLayerState,
    *,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    affected_ref: str,
) -> tuple[ResolvedLayerBinding, tuple[Diagnostic, ...]]:
    """Resolve one stored layer state with fail-closed identifier agreement."""

    _assert_unique_layer_stack_authority(layer_stack, affected_ref)
    diagnostics: list[Diagnostic] = []
    binding = _resolve_layer_binding(
        state,
        layer_stack=layer_stack,
        strictness=strictness,
        affected_ref=affected_ref,
        diagnostics=diagnostics,
    )
    return binding, tuple(diagnostics)


def _validate_primitive_layer_state_numeric_domains(
    state: PcbPrimitiveLayerState,
    *,
    affected_ref: str,
) -> None:
    fields = (
        ("stored legacy layer ID", state.stored_legacy_layer_id, 0xFF),
        ("stored saved V7 layer ID", state.stored_v7_saved_layer_id, 0xFFFFFFFF),
        (
            "stored runtime V7 layer ID",
            state.stored_runtime_v7_layer_id,
            0xFFFFFFFF,
        ),
    )
    for field_name, value, maximum in fields:
        if value is None:
            continue
        if type(value) is int and 0 <= value <= maximum:
            continue
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} {field_name} must be an integer from 0 through {maximum}",
        )


def _assert_unique_layer_stack_authority(
    layer_stack: ResolvedLayerStack,
    affected_ref: str,
) -> None:
    for layer in layer_stack.layers:
        _resolved_layer_ref_authority(layer, affected_ref=affected_ref)
    _assert_layer_id_authority(
        layer_stack,
        affected_ref=affected_ref,
        field_name="legacy layer ID",
        identifier=lambda layer: layer.legacy_id,
    )
    _assert_layer_id_authority(
        layer_stack,
        affected_ref=affected_ref,
        field_name="saved V7 layer ID",
        identifier=lambda layer: layer.v7_id,
    )

    substack_refs: set[str] = set()
    for substack in layer_stack.substacks:
        source_ref = substack.source_stackup_ref
        if not source_ref:
            continue
        if source_ref in substack_refs:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"{affected_ref} ResolvedLayerStack has duplicate substack source "
                f"reference {source_ref!r}",
            )
        substack_refs.add(source_ref)


def _resolved_layer_ref_authority(
    layer: ResolvedLayer,
    *,
    affected_ref: str,
) -> PcbLayerRef | None:
    numeric_refs: list[tuple[str, PcbLayerRef]] = []
    if layer.legacy_id is not None:
        legacy_id = _exact_layer_numeric_id(
            layer.legacy_id,
            maximum=0xFF,
            field_name="legacy layer ID",
            layer=layer,
            affected_ref=affected_ref,
        )
        try:
            numeric_refs.append(("legacy layer ID", PcbLayerRef.from_legacy(legacy_id)))
        except ValueError as exc:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"{affected_ref} resolved layer {layer.layer_key!r} has invalid "
                f"legacy layer ID {legacy_id}",
            ) from exc
    if layer.v7_id is not None:
        v7_id = _exact_layer_numeric_id(
            layer.v7_id,
            maximum=0xFFFFFFFF,
            field_name="saved V7 layer ID",
            layer=layer,
            affected_ref=affected_ref,
        )
        numeric_refs.append(
            ("saved V7 layer ID", PcbLayerRef.from_v7_saved_layer_id(v7_id))
        )
    candidates = [*numeric_refs]
    if layer.layer_ref is not None:
        candidates.append(("semantic layer ref", layer.layer_ref))
    if not candidates:
        return None
    authority_field, authority_ref = candidates[0]
    for field_name, candidate_ref in candidates[1:]:
        if _same_pcb_layer_ref_representation(authority_ref, candidate_ref):
            continue
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} resolved layer {layer.layer_key!r} {field_name} "
            f"disagrees with its {authority_field}",
        )
    return authority_ref


def _exact_layer_numeric_id(
    value: int,
    *,
    maximum: int,
    field_name: str,
    layer: ResolvedLayer,
    affected_ref: str,
) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} resolved layer {layer.layer_key!r} {field_name} "
            f"must be an integer from 0 through {maximum}",
        )
    return value


def _assert_layer_id_authority(
    layer_stack: ResolvedLayerStack,
    *,
    affected_ref: str,
    field_name: str,
    identifier: Callable[[ResolvedLayer], int | None],
) -> None:
    groups: dict[int, list[ResolvedLayer]] = {}
    for layer in layer_stack.layers:
        value = identifier(layer)
        if value is not None:
            groups.setdefault(value, []).append(layer)
    for value, layers in groups.items():
        if len(layers) < 2 or _contextual_layer_reuse_is_unambiguous(
            layer_stack,
            layers=layers,
            identifier=identifier,
            value=value,
        ):
            continue
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} ResolvedLayerStack has ambiguous {field_name} {value}",
        )


def _contextual_layer_reuse_is_unambiguous(
    layer_stack: ResolvedLayerStack,
    *,
    layers: list[ResolvedLayer],
    identifier: Callable[[ResolvedLayer], int | None],
    value: int,
) -> bool:
    if not layer_stack.substacks:
        return False
    refs = {layer.layer_ref for layer in layers}
    source_ids = {layer.source_record_id for layer in layers}
    if None in refs or len(refs) != 1:
        return False
    if "" in source_ids or len(source_ids) != len(layers):
        return False
    return all(
        sum(identifier(layer) == value for layer in substack.layers) <= 1
        for substack in layer_stack.substacks
    )


def _resolve_net(
    net: object,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> ResolvedNetInput:
    resolution = source_index.resolve(
        net,
        strictness=strictness,
        affected_ref=f"input.net.{ordinal}",
    )
    diagnostics.extend(resolution.diagnostics)
    return ResolvedNetInput(
        id=_input_ref("net", resolution.source, f"input.net.{ordinal}"),
        source=resolution.source,
        display_name=str(getattr(net, "name", "") or ""),
    )


def _resolve_component(
    component: AltiumPcbComponent,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> ResolvedComponentOccurrenceInput:
    source, input_id = _record_source_and_ref(
        component,
        kind="component",
        fallback_ref=f"input.component.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    x_source_units = _component_position_source_units(component.x, input_id, "X")
    y_source_units = _component_position_source_units(component.y, input_id, "Y")
    rotation_degrees = _component_rotation_degrees(component.rotation, input_id)
    side = _component_side(component.layer, input_id)
    return ResolvedComponentOccurrenceInput(
        id=input_id,
        source=source,
        source_component_unique_id=component.source_unique_id,
        display_designator=component.designator,
        footprint=component.footprint,
        side=side,
        origin_x_source_units=stored_winning_value(x_source_units),
        origin_y_source_units=stored_winning_value(y_source_units),
        rotation_degrees=stored_winning_value(rotation_degrees),
        local_to_board_affine=_component_local_to_board_affine(
            x_source_units,
            y_source_units,
            rotation_degrees,
            side,
        ),
    )


def _component_position_source_units(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int:
    text = str(value).strip()
    if len(text) < 4 or text[-3:].lower() != "mil":
        raise PcbResolvedInputError(
            "invalid_component_placement",
            f"{affected_ref} {field_name} is not an Altium mil coordinate",
        )
    try:
        mils = Decimal(text[:-3].strip())
    except (InvalidOperation, ValueError) as exc:
        raise PcbResolvedInputError(
            "invalid_component_placement",
            f"{affected_ref} {field_name} is not a finite decimal coordinate",
        ) from exc
    if not mils.is_finite():
        raise PcbResolvedInputError(
            "invalid_component_placement",
            f"{affected_ref} {field_name} is not a finite decimal coordinate",
        )
    scaled = mils * 10_000
    if scaled != scaled.to_integral_value():
        raise PcbResolvedInputError(
            "invalid_component_placement",
            f"{affected_ref} {field_name} is not exactly representable on the PCB source grid",
        )
    return int(scaled)


def _component_rotation_degrees(value: str, affected_ref: str) -> float:
    text = str(value).strip() or "0"
    try:
        rotation = float(Decimal(text))
    except (InvalidOperation, ValueError) as exc:
        raise PcbResolvedInputError(
            "invalid_component_placement",
            f"{affected_ref} ROTATION is not a finite decimal angle",
        ) from exc
    if not math.isfinite(rotation):
        raise PcbResolvedInputError(
            "invalid_component_placement",
            f"{affected_ref} ROTATION is not a finite decimal angle",
        )
    return rotation


def _component_side(value: str, affected_ref: str) -> ComponentSide:
    normalized = str(value).strip().upper()
    if normalized in {"TOP", "TOPLAYER"}:
        return "top"
    if normalized in {"BOTTOM", "BOTTOMLAYER"}:
        return "bottom"
    raise PcbResolvedInputError(
        "invalid_component_placement",
        f"{affected_ref} LAYER does not identify the top or bottom placement side",
    )


def _component_local_to_board_affine(
    x_source_units: int,
    y_source_units: int,
    rotation_degrees: float,
    side: ComponentSide,
) -> PcbDecimalAffine2d:
    rotation = rotation_affine_degrees(rotation_degrees)
    reflected = side == "bottom"
    tx_nm = pcb_internal_to_nm(x_source_units)
    ty_nm = pcb_internal_to_nm(y_source_units)
    if not _MIN_INT64 <= tx_nm <= _MAX_INT64 or not _MIN_INT64 <= ty_nm <= _MAX_INT64:
        raise PcbResolvedInputError(
            "integer_overflow",
            "component placement translation exceeds signed int64 nanometers",
        )
    return PcbDecimalAffine2d(
        type="pcb.manufacturing.affine2d.decimal_e15",
        a_e15=rotation.a_e15,
        b_e15=rotation.b_e15,
        c_e15=-rotation.c_e15 if reflected else rotation.c_e15,
        d_e15=-rotation.d_e15 if reflected else rotation.d_e15,
        tx_nm=tx_nm,
        ty_nm=ty_nm,
        composition_depth=0,
    )


def _resolve_track(
    track: AltiumPcbTrack,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    component_refs: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> ResolvedTrackInput:
    source, input_id = _record_source_and_ref(
        track,
        kind="track",
        fallback_ref=f"input.track.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return ResolvedTrackInput(
        id=input_id,
        source=source,
        layer=_resolve_layer_binding(
            track.layer_state(),
            layer_stack=layer_stack,
            strictness=strictness,
            affected_ref=input_id,
            diagnostics=diagnostics,
        ),
        source_net_ref=_indexed_ref(track.net_index, net_refs, "net", input_id),
        component_occurrence_ref=_indexed_ref(
            track.component_index, component_refs, "component", input_id
        ),
        coordinate_frame="board",
        source_to_board_affine=identity_affine(),
        start_x_source_units=stored_winning_value(track.start_x),
        start_y_source_units=stored_winning_value(track.start_y),
        end_x_source_units=stored_winning_value(track.end_x),
        end_y_source_units=stored_winning_value(track.end_y),
        width_source_units=stored_winning_value(track.width),
        polygon_index=track.polygon_index,
        is_keepout=track.is_keepout,
        is_polygon_outline=track.is_polygon_outline,
    )


def _resolve_arc(
    arc: AltiumPcbArc,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    component_refs: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> ResolvedArcInput:
    source, input_id = _record_source_and_ref(
        arc,
        kind="arc",
        fallback_ref=f"input.arc.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return ResolvedArcInput(
        id=input_id,
        source=source,
        layer=_resolve_layer_binding(
            arc.layer_state(),
            layer_stack=layer_stack,
            strictness=strictness,
            affected_ref=input_id,
            diagnostics=diagnostics,
        ),
        source_net_ref=_indexed_ref(arc.net_index, net_refs, "net", input_id),
        component_occurrence_ref=_indexed_ref(
            arc.component_index, component_refs, "component", input_id
        ),
        coordinate_frame="board",
        source_to_board_affine=identity_affine(),
        center_x_source_units=stored_winning_value(arc.center_x),
        center_y_source_units=stored_winning_value(arc.center_y),
        radius_source_units=stored_winning_value(arc.radius),
        start_angle_degrees=stored_winning_value(arc.start_angle),
        end_angle_degrees=stored_winning_value(arc.end_angle),
        width_source_units=stored_winning_value(arc.width),
        polygon_index=arc.polygon_index,
        is_keepout=arc.is_keepout,
        is_polygon_outline=arc.is_polygon_outline,
    )


def _resolve_pad(
    pad: AltiumPcbPad,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    component_refs: tuple[str, ...],
    rules: Iterable[AltiumPcbRule],
    solder_mask_cache: dict[ManufacturingRuleQuery, _SolderMaskRuleFacts | None],
    paste_mask_cache: dict[ManufacturingRuleQuery, _PasteMaskRuleFacts | None],
    diagnostics: list[Diagnostic],
) -> ResolvedPadInput:
    source, input_id = _record_source_and_ref(
        pad,
        kind="pad",
        fallback_ref=f"input.pad.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    rule_query = _manufacturing_rule_query(pad, object_kind="pad")
    return ResolvedPadInput(
        id=input_id,
        source=source,
        source_net_ref=_indexed_ref(pad.net_index, net_refs, "net", input_id),
        component_occurrence_ref=_indexed_ref(
            pad.component_index, component_refs, "component", input_id
        ),
        coordinate_frame="board",
        source_to_board_affine=identity_affine(),
        center_x_source_units=stored_winning_value(pad.x),
        center_y_source_units=stored_winning_value(pad.y),
        lands=_resolve_pad_lands(
            pad,
            layer_stack=layer_stack,
            strictness=strictness,
            affected_ref=input_id,
            diagnostics=diagnostics,
        ),
        hole_size_source_units=stored_winning_value(pad.hole_size),
        hole_shape_code=stored_winning_value(pad.hole_shape),
        slot_size_source_units=stored_winning_value(pad.slot_size),
        slot_rotation_degrees=stored_winning_value(pad.slot_rotation),
        paste_mask=_resolve_pad_paste_mask(
            pad,
            rules=rules,
            query=rule_query,
            rule_cache=paste_mask_cache,
            source_index=source_index,
            strictness=strictness,
            affected_ref=input_id,
            diagnostics=diagnostics,
        ),
        solder_mask=_resolve_pad_solder_mask(
            pad,
            rules=rules,
            query=rule_query,
            rule_cache=solder_mask_cache,
            source_index=source_index,
            strictness=strictness,
            affected_ref=input_id,
            diagnostics=diagnostics,
        ),
        plane_cache=_pad_plane_cache_evidence(pad),
        plated=pad.is_plated,
        rotation_degrees=stored_winning_value(pad.rotation),
        rule_query=rule_query,
    )


def _pad_plane_cache_evidence(pad: AltiumPcbPad) -> ResolvedPlaneCacheEvidence:
    if not pad._has_pad_cache:
        return ResolvedPlaneCacheEvidence(
            cache_present=False,
            connection_style_code=int(pad.plane_connection_style),
            relief_conductor_width_source_units=int(pad.cache_relief_conductor_width),
            relief_entries=int(pad.cache_relief_entries),
            relief_air_gap_source_units=int(pad.cache_relief_air_gap),
            relief_expansion_source_units=int(pad.cache_power_plane_relief_expansion),
            clearance_source_units=int(pad.cache_power_plane_clearance),
            validity_mapping="pad_partial",
            connection_style_valid_raw=None,
            relief_conductor_width_valid_raw=None,
            relief_entries_valid_raw=None,
            relief_air_gap_valid_raw=None,
            relief_expansion_valid_raw=None,
            clearance_valid_raw=None,
        )
    return ResolvedPlaneCacheEvidence(
        cache_present=True,
        connection_style_code=int(pad.plane_connection_style),
        relief_conductor_width_source_units=int(pad.cache_relief_conductor_width),
        relief_entries=int(pad.cache_relief_entries),
        relief_air_gap_source_units=int(pad.cache_relief_air_gap),
        relief_expansion_source_units=int(pad.cache_power_plane_relief_expansion),
        clearance_source_units=int(pad.cache_power_plane_clearance),
        validity_mapping="pad_partial",
        connection_style_valid_raw=int(pad.cache_plane_connection_valid),
        relief_conductor_width_valid_raw=int(pad.cache_relief_conductor_width_valid),
        relief_entries_valid_raw=int(pad.cache_relief_entries_valid),
        relief_air_gap_valid_raw=int(pad.cache_relief_air_gap_valid),
        relief_expansion_valid_raw=int(pad.cache_power_plane_relief_expansion_valid),
        clearance_valid_raw=None,
    )


@dataclass(frozen=True)
class _SolderMaskRuleFacts:
    top_expansion_source_units: int
    bottom_expansion_source_units: int
    from_hole_edge: bool
    tented_top: bool
    tented_bottom: bool
    rule_ref: str


def _manufacturing_rule_query(
    primitive: AltiumPcbPad | AltiumPcbVia,
    *,
    object_kind: Literal["pad", "via"],
) -> ManufacturingRuleQuery:
    if object_kind == "pad":
        top_names = (
            "is_assy_test_point_top",
            "is_fab_test_point_top",
            "is_test_fab_top",
            "is_test_top",
        )
        bottom_names = (
            "is_assy_test_point_bottom",
            "is_fab_test_point_bottom",
            "is_test_fab_bottom",
            "is_test_bottom",
        )
    else:
        top_names = ("is_assy_testpoint_top", "is_test_fab_top")
        bottom_names = ("is_assy_testpoint_bottom", "is_test_fab_bottom")
    return ManufacturingRuleQuery(
        object_kind=object_kind,
        locked=bool(getattr(primitive, "is_locked", False)),
        testpoint_top=any(bool(getattr(primitive, name, False)) for name in top_names),
        testpoint_bottom=any(
            bool(getattr(primitive, name, False)) for name in bottom_names
        ),
    )


def _resolve_solder_mask_rule(
    rules: Iterable[AltiumPcbRule],
    *,
    query: ManufacturingRuleQuery,
    rule_cache: dict[ManufacturingRuleQuery, _SolderMaskRuleFacts | None],
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> _SolderMaskRuleFacts | None:
    if query in rule_cache:
        return rule_cache[query]
    try:
        winner = select_manufacturing_rule(
            rules,
            rule_kind="SolderMaskExpansion",
            query=query,
        )
    except PcbRuleResolutionError as exc:
        raise PcbResolvedInputError(exc.code, f"{affected_ref}: {exc.detail}") from exc
    if winner is None:
        rule_cache[query] = None
        return None
    if not isinstance(winner, AltiumSolderMaskExpansionRule):
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} selected solder-mask rule has the wrong typed model",
        )
    resolution = source_index.resolve(
        winner,
        strictness=strictness,
        affected_ref=f"input.rule.{winner.index}",
    )
    diagnostics.extend(resolution.diagnostics)
    rule_ref = _input_ref(
        "rule",
        resolution.source,
        f"input.rule.{winner.index}",
    )
    top_expansion = _rule_length_source_units(
        winner.top_expansion,
        affected_ref,
        "EXPANSION",
    )
    bottom_expansion = top_expansion
    if winner.use_separate_expansions is True:
        bottom_expansion = _rule_length_source_units(
            winner.bottom_expansion,
            affected_ref,
            "EXPANSIONBOTTOM",
        )
    facts = _SolderMaskRuleFacts(
        top_expansion_source_units=top_expansion,
        bottom_expansion_source_units=bottom_expansion,
        from_hole_edge=_rule_bool(winner.from_hole_edge, affected_ref, "FROMHOLEEDGE"),
        tented_top=bool(winner.is_tenting_top),
        tented_bottom=bool(winner.is_tenting_bottom),
        rule_ref=rule_ref,
    )
    rule_cache[query] = facts
    return facts


def _rule_length_source_units(value: str, affected_ref: str, field_name: str) -> int:
    text = str(value).strip()
    if len(text) < 4 or text[-3:].casefold() != "mil":
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is not an Altium mil value",
        )
    return _exact_decimal_scaled_int(
        text[:-3].strip(),
        decimal_places=4,
        affected_ref=affected_ref,
        field_name=field_name,
        invalid_description="finite decimal value",
        fractional_description="off the PCB source grid",
    )


def _rule_nonnegative_int(value: str, affected_ref: str, field_name: str) -> int:
    result = _optional_rule_nonnegative_int(value, affected_ref, field_name)
    if result is None:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is missing",
        )
    if result > 0x7FFFFFFF:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is outside the nonnegative int32 range",
        )
    return result


def _clearance_rule_length_source_units(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int:
    result = _rule_length_source_units(value, affected_ref, field_name)
    if result < 0 or result > 0x7FFFFFFF:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is outside the nonnegative int32 range",
        )
    return result


def _optional_clearance_rule_length_source_units(
    value: str,
    affected_ref: str,
    field_name: str,
) -> int | None:
    if not str(value).strip():
        return None
    return _clearance_rule_length_source_units(value, affected_ref, field_name)


def _exact_decimal_scaled_int(
    value: str,
    *,
    decimal_places: int,
    affected_ref: str,
    field_name: str,
    invalid_description: str,
    fractional_description: str,
) -> int:
    number = _finite_decimal(
        value,
        affected_ref=affected_ref,
        field_name=field_name,
        invalid_description=invalid_description,
    )
    sign, digits, exponent = _significant_decimal_parts(
        number,
        affected_ref=affected_ref,
        field_name=field_name,
        invalid_description=invalid_description,
    )
    if not digits:
        return 0
    integral_digits, trailing_zeros = _integral_scaled_decimal_digits(
        digits,
        exponent + decimal_places,
        affected_ref=affected_ref,
        field_name=field_name,
        fractional_description=fractional_description,
    )
    return _signed_decimal_digits_int64(
        sign,
        integral_digits,
        trailing_zeros,
        affected_ref=affected_ref,
        field_name=field_name,
    )


def _finite_decimal(
    value: str,
    *,
    affected_ref: str,
    field_name: str,
    invalid_description: str,
) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is not a {invalid_description}",
        ) from exc
    if not number.is_finite():
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is not a {invalid_description}",
        )
    return number


def _significant_decimal_parts(
    number: Decimal,
    *,
    affected_ref: str,
    field_name: str,
    invalid_description: str,
) -> tuple[int, tuple[int, ...], int]:
    sign, raw_digits, exponent = number.as_tuple()
    if not isinstance(exponent, int):
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} {field_name} is not a {invalid_description}",
        )
    first_nonzero = next(
        (index for index, digit in enumerate(raw_digits) if digit),
        len(raw_digits),
    )
    digits = raw_digits[first_nonzero:]
    return sign, digits, exponent


def _integral_scaled_decimal_digits(
    digits: tuple[int, ...],
    scaled_exponent: int,
    *,
    affected_ref: str,
    field_name: str,
    fractional_description: str,
) -> tuple[tuple[int, ...], int]:
    if scaled_exponent < 0:
        required_trailing_zeros = -scaled_exponent
        if required_trailing_zeros >= len(digits) or any(
            digits[-required_trailing_zeros:]
        ):
            raise PcbResolvedInputError(
                "unsupported_rule_value",
                f"{affected_ref} {field_name} is {fractional_description}",
            )
        digits = digits[:-required_trailing_zeros]
        scaled_exponent = 0
    return digits, scaled_exponent


def _signed_decimal_digits_int64(
    sign: int,
    digits: tuple[int, ...],
    trailing_zeros: int,
    *,
    affected_ref: str,
    field_name: str,
) -> int:
    result_digit_count = len(digits) + trailing_zeros
    if result_digit_count > 19:
        raise PcbResolvedInputError(
            "integer_overflow",
            f"{affected_ref} {field_name} exceeds signed int64",
        )
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    result = coefficient * (10**trailing_zeros)
    if sign:
        result = -result
    if not _MIN_INT64 <= result <= _MAX_INT64:
        raise PcbResolvedInputError(
            "integer_overflow",
            f"{affected_ref} {field_name} exceeds signed int64",
        )
    return result


def _rule_bool(value: str, affected_ref: str, field_name: str) -> bool:
    normalized = str(value).strip().casefold()
    if normalized in {"", "false", "0", "no"}:
        return False
    if normalized in {"true", "1", "yes"}:
        return True
    raise PcbResolvedInputError(
        "unsupported_rule_value",
        f"{affected_ref} {field_name} has unsupported boolean value {value!r}",
    )


@dataclass(frozen=True)
class _PasteMaskRuleFacts:
    measure: PasteMaskMeasure
    measure_from_rule: bool
    absolute_expansion_source_units: int | None
    absolute_from_rule: bool
    percent_e12: int | None
    percent_from_rule: bool
    use_paste: bool | None
    use_top_paste: bool | None
    use_bottom_paste: bool | None
    rule_ref: str


def _resolve_pad_paste_mask(
    pad: AltiumPcbPad,
    *,
    rules: Iterable[AltiumPcbRule],
    query: ManufacturingRuleQuery,
    rule_cache: dict[ManufacturingRuleQuery, _PasteMaskRuleFacts | None],
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedPasteMaskInput:
    mode = _paste_mask_mode(pad.pastemask_expansion_mode, affected_ref)
    cache_marker_raw = (
        int(pad.pastemask_expansion_mode) if pad._has_mask_expansion else None
    )
    cache_state = _primitive_cache_state(
        cache_marker_raw,
        affected_ref=affected_ref,
        field_name="paste-mask cache state",
    )
    cache_context = _cache_context(cache_state)
    facts = (
        _resolve_paste_mask_rule(
            rules,
            query=query,
            rule_cache=rule_cache,
            source_index=source_index,
            strictness=strictness,
            affected_ref=affected_ref,
            diagnostics=diagnostics,
        )
        if mode == "rule"
        else None
    )
    applicability = _paste_applicability(pad)
    cached_expansion = (
        int(pad.pastemask_expansion_manual)
        if mode == "rule" and pad._has_mask_expansion
        else None
    )
    return ResolvedPasteMaskInput(
        mode=stored_winning_value(mode),
        cache_marker_raw=cache_marker_raw,
        cache_state=cache_state,
        top=_resolved_paste_mask_side(
            side="top",
            mode=mode,
            applicability=applicability,
            stored_expansion=int(pad.pastemask_expansion_manual),
            cached_expansion=cached_expansion,
            cache_context=cache_context,
            facts=facts,
            affected_ref=affected_ref,
            diagnostics=diagnostics,
        ),
        bottom=_resolved_paste_mask_side(
            side="bottom",
            mode=mode,
            applicability=applicability,
            stored_expansion=int(pad.pastemask_expansion_manual),
            cached_expansion=cached_expansion,
            cache_context=cache_context,
            facts=facts,
            affected_ref=affected_ref,
            diagnostics=diagnostics,
        ),
    )


def _resolve_paste_mask_rule(
    rules: Iterable[AltiumPcbRule],
    *,
    query: ManufacturingRuleQuery,
    rule_cache: dict[ManufacturingRuleQuery, _PasteMaskRuleFacts | None],
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> _PasteMaskRuleFacts | None:
    if query in rule_cache:
        return rule_cache[query]
    winner = _selected_paste_mask_rule(rules, query, affected_ref)
    if winner is None:
        rule_cache[query] = None
        return None
    rule_ref = _paste_rule_ref(
        winner,
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    facts = _paste_rule_facts(winner, rule_ref, affected_ref)
    rule_cache[query] = facts
    return facts


def _selected_paste_mask_rule(
    rules: Iterable[AltiumPcbRule],
    query: ManufacturingRuleQuery,
    affected_ref: str,
) -> AltiumPasteMaskExpansionRule | None:
    try:
        winner = select_manufacturing_rule(
            rules,
            rule_kind="PasteMaskExpansion",
            query=query,
        )
    except PcbRuleResolutionError as exc:
        raise PcbResolvedInputError(exc.code, f"{affected_ref}: {exc.detail}") from exc
    if winner is None:
        return None
    if not isinstance(winner, AltiumPasteMaskExpansionRule):
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} selected paste-mask rule has the wrong typed model",
        )
    known_fields = {spec.raw_key for spec in winner.RULE_FIELDS}
    unknown_semantic_fields = sorted(
        key
        for key in winner.raw_record
        if ("PASTE" in key or "PERCENT" in key) and key not in known_fields
    )
    if unknown_semantic_fields:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} paste-mask rule has unsupported semantic fields "
            f"{unknown_semantic_fields!r}",
        )
    return winner


def _paste_rule_ref(
    winner: AltiumPasteMaskExpansionRule,
    *,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> str:
    resolution = source_index.resolve(
        winner,
        strictness=strictness,
        affected_ref=f"input.rule.{winner.index}",
    )
    diagnostics.extend(resolution.diagnostics)
    return _input_ref(
        "rule",
        resolution.source,
        f"input.rule.{winner.index}",
    )


def _paste_rule_facts(
    winner: AltiumPasteMaskExpansionRule,
    rule_ref: str,
    affected_ref: str,
) -> _PasteMaskRuleFacts:
    use_percents = winner.use_percents is True
    expansion_text = str(winner.expansion).strip()
    percent_text = str(winner.percents).strip()
    absolute, absolute_from_rule = _paste_absolute_rule_value(
        use_percents,
        expansion_text,
        affected_ref,
    )
    percent, percent_from_rule = _paste_percent_rule_value(
        use_percents,
        percent_text,
        affected_ref,
    )
    return _PasteMaskRuleFacts(
        measure="percent" if use_percents else "absolute",
        measure_from_rule=winner.use_percents is not None,
        absolute_expansion_source_units=absolute,
        absolute_from_rule=absolute_from_rule,
        percent_e12=percent,
        percent_from_rule=percent_from_rule,
        use_paste=winner.use_paste,
        use_top_paste=None,
        use_bottom_paste=None,
        rule_ref=rule_ref,
    )


def _paste_absolute_rule_value(
    use_percents: bool,
    expansion_text: str,
    affected_ref: str,
) -> tuple[int | None, bool]:
    if use_percents:
        return None, False
    if expansion_text:
        return (
            _rule_length_source_units(expansion_text, affected_ref, "EXPANSION"),
            True,
        )
    return 0, False


def _paste_percent_rule_value(
    use_percents: bool,
    percent_text: str,
    affected_ref: str,
) -> tuple[int | None, bool]:
    if not use_percents:
        return None, False
    if percent_text:
        return _rule_percent_e12(percent_text, affected_ref, "PERCENTS"), True
    return 0, False


def _rule_percent_e12(value: str, affected_ref: str, field_name: str) -> int:
    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1].strip()
    return _exact_decimal_scaled_int(
        text,
        decimal_places=12,
        affected_ref=affected_ref,
        field_name=field_name,
        invalid_description="finite decimal percentage",
        fractional_description="not exactly representable at e12",
    )


def _paste_mask_mode(value: int, affected_ref: str) -> PasteMaskMode:
    try:
        mode = PcbMaskExpansionMode(int(value))
    except ValueError as exc:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} has unknown paste-mask expansion mode {value}",
        ) from exc
    if mode is PcbMaskExpansionMode.NONE:
        return "none"
    if mode is PcbMaskExpansionMode.RULE:
        return "rule"
    return "manual"


def _paste_applicability(
    pad: AltiumPcbPad,
) -> Literal["top", "bottom", "through_hole", "none"]:
    if int(pad.hole_size) > 0 or int(pad.layer) == PcbLayer.MULTI_LAYER.value:
        return "through_hole"
    if int(pad.layer) == PcbLayer.TOP.value:
        return "top"
    if int(pad.layer) == PcbLayer.BOTTOM.value:
        return "bottom"
    return "none"


def _resolved_paste_mask_side(
    *,
    side: MaskSide,
    mode: PasteMaskMode,
    applicability: Literal["top", "bottom", "through_hole", "none"],
    stored_expansion: int,
    cached_expansion: int | None,
    cache_context: str,
    facts: _PasteMaskRuleFacts | None,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedPasteMaskSideInput:
    enabled = _resolved_paste_enabled(
        side=side,
        mode=mode,
        applicability=applicability,
        facts=facts,
    )
    measure = _resolved_paste_measure(mode, facts)
    absolute = _resolved_paste_absolute(mode, stored_expansion, facts)
    percent = _resolved_paste_percent(mode, facts)
    cache_validity = _paste_cache_validity(
        enabled=enabled.selected_value,
        measure=measure.selected_value,
        absolute=absolute,
        cached_expansion=cached_expansion,
        cache_context=cache_context,
        side=side,
        affected_ref=affected_ref,
        diagnostics=diagnostics,
    )
    return ResolvedPasteMaskSideInput(
        side=side,
        enabled=enabled,
        measure=measure,
        absolute_expansion_source_units=absolute,
        percent_e12=percent,
        cached_expansion_source_units=cached_expansion,
        cache_validity=cache_validity,
    )


def _resolved_paste_enabled(
    *,
    side: MaskSide,
    mode: PasteMaskMode,
    applicability: Literal["top", "bottom", "through_hole", "none"],
    facts: _PasteMaskRuleFacts | None,
) -> ResolvedWinningValue[bool]:
    if mode == "none" or applicability == "none":
        return stored_winning_value(False)
    if applicability in {"top", "bottom"} and applicability != side:
        return stored_winning_value(False)
    if mode == "manual":
        return stored_winning_value(applicability != "through_hole")
    rule_value: bool | None = None
    default_value = applicability in {"top", "bottom"}
    rule_ref: str | None = None
    if facts is not None:
        rule_ref = facts.rule_ref
        if applicability == "through_hole":
            rule_value = (
                facts.use_top_paste if side == "top" else facts.use_bottom_paste
            )
        else:
            rule_value = facts.use_paste
    if rule_value is None:
        return ResolvedWinningValue(
            selected_value=default_value,
            selected_from="default",
            default_value=default_value,
        )
    return ResolvedWinningValue(
        selected_value=rule_value,
        selected_from="rule",
        rule_value=rule_value,
        rule_ref=rule_ref,
    )


def _resolved_paste_measure(
    mode: PasteMaskMode, facts: _PasteMaskRuleFacts | None
) -> ResolvedWinningValue[PasteMaskMeasure]:
    if mode != "rule":
        return stored_winning_value("absolute")
    if facts is None or not facts.measure_from_rule:
        return ResolvedWinningValue(
            selected_value="absolute",
            selected_from="default",
            default_value="absolute",
        )
    return ResolvedWinningValue(
        selected_value=facts.measure,
        selected_from="rule",
        rule_value=facts.measure,
        rule_ref=facts.rule_ref,
    )


def _resolved_paste_absolute(
    mode: PasteMaskMode,
    stored_expansion: int,
    facts: _PasteMaskRuleFacts | None,
) -> ResolvedWinningValue[int] | None:
    if mode == "none":
        return stored_winning_value(0)
    if mode == "manual":
        return stored_winning_value(stored_expansion)
    if facts is not None and facts.measure == "percent":
        return None
    value = facts.absolute_expansion_source_units if facts is not None else 0
    assert value is not None
    if facts is None or not facts.absolute_from_rule:
        return ResolvedWinningValue(
            selected_value=value,
            selected_from="default",
            default_value=value,
        )
    return ResolvedWinningValue(
        selected_value=value,
        selected_from="rule",
        rule_value=value,
        rule_ref=facts.rule_ref,
    )


def _resolved_paste_percent(
    mode: PasteMaskMode, facts: _PasteMaskRuleFacts | None
) -> ResolvedWinningValue[int] | None:
    if mode != "rule" or facts is None or facts.measure != "percent":
        return None
    value = facts.percent_e12
    assert value is not None
    if not facts.percent_from_rule:
        return ResolvedWinningValue(
            selected_value=value,
            selected_from="default",
            default_value=value,
        )
    return ResolvedWinningValue(
        selected_value=value,
        selected_from="rule",
        rule_value=value,
        rule_ref=facts.rule_ref,
    )


def _paste_cache_validity(
    *,
    enabled: bool,
    measure: PasteMaskMeasure,
    absolute: ResolvedWinningValue[int] | None,
    cached_expansion: int | None,
    cache_context: str,
    side: MaskSide,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    if cached_expansion is None:
        return None
    if not enabled:
        return f"inactive_{cache_context}"
    if measure == "percent":
        return f"derived_{cache_context}"
    assert absolute is not None
    selected = absolute.selected_value
    if cached_expansion == selected:
        return f"matches_selected_{cache_context}"
    detail = (
        f"{affected_ref} {side} paste-mask cache {cached_expansion!r} differs "
        f"from selected absolute expansion {selected!r}"
    )
    diagnostics.append(
        Diagnostic(
            id=_diagnostic_id("contradictory_material", affected_ref, detail),
            code="contradictory_material",
            severity="warning",
            message=detail,
            affected_ref=affected_ref,
        )
    )
    return f"differs_from_selected_{cache_context}"


def _primitive_cache_state(
    value: int | None,
    *,
    affected_ref: str,
    field_name: str,
) -> PrimitiveCacheState | None:
    if value is None:
        return None
    states: tuple[PrimitiveCacheState, ...] = ("invalid", "valid", "manual")
    if 0 <= value < len(states):
        return states[value]
    raise PcbResolvedInputError(
        "corrupt_identity",
        f"{affected_ref} {field_name} has unknown TCacheState value {value}",
    )


def _cache_context(state: PrimitiveCacheState | None) -> str:
    return f"{state}_cache" if state is not None else "unavailable_cache"


def _solder_mask_mode(value: int, affected_ref: str) -> SolderMaskMode:
    try:
        mode = PcbMaskExpansionMode(int(value))
    except ValueError as exc:
        raise PcbResolvedInputError(
            "unsupported_rule_value",
            f"{affected_ref} has unknown solder-mask expansion mode {value}",
        ) from exc
    if mode is PcbMaskExpansionMode.NONE:
        return "none"
    if mode is PcbMaskExpansionMode.RULE:
        return "rule"
    return "manual"


def _resolved_mask_value(
    *,
    stored_override: _ValueT | None,
    cached_value: _ValueT | None,
    rule_value: _ValueT | None,
    rule_ref: str | None,
    default_value: _ValueT,
    cache_context: str,
    cache_is_derived: bool,
    affected_ref: str,
    field_name: str,
    diagnostics: list[Diagnostic],
) -> ResolvedWinningValue[_ValueT]:
    if stored_override is not None:
        selected_value = stored_override
        selected_from: WinningValueSource = "stored"
    elif rule_value is not None:
        selected_value = rule_value
        selected_from = "rule"
    else:
        selected_value = default_value
        selected_from = "default"

    cache_validity: str | None = None
    if cached_value is not None:
        if cached_value == selected_value:
            cache_validity = f"matches_selected_{cache_context}"
        elif cache_is_derived:
            cache_validity = f"derived_{cache_context}"
        else:
            cache_validity = f"differs_from_selected_{cache_context}"
            detail = (
                f"{affected_ref} {field_name} cache {cached_value!r} differs from "
                f"selected {selected_value!r}"
            )
            diagnostics.append(
                Diagnostic(
                    id=_diagnostic_id("contradictory_material", affected_ref, detail),
                    code="contradictory_material",
                    severity="warning",
                    message=detail,
                    affected_ref=affected_ref,
                )
            )
    return ResolvedWinningValue(
        selected_value=selected_value,
        selected_from=selected_from,
        stored_value=stored_override,
        cached_value=cached_value,
        rule_value=rule_value,
        rule_ref=rule_ref,
        default_value=default_value,
        cache_validity=cache_validity,
    )


@dataclass(frozen=True)
class _PrimitiveSolderMaskEvidence:
    mode: SolderMaskMode
    cache_context: str
    cache_marker_raw: int | None = None
    cache_state: PrimitiveCacheState | None = None
    stored_from_hole: bool | None = None
    cached_from_hole: bool | None = None
    stored_top_expansion: int | None = None
    cached_top_expansion: int | None = None
    stored_bottom_expansion: int | None = None
    cached_bottom_expansion: int | None = None
    stored_tented_top: bool | None = None
    cached_tented_top: bool | None = None
    stored_tented_bottom: bool | None = None
    cached_tented_bottom: bool | None = None


def _resolve_pad_solder_mask(
    pad: AltiumPcbPad,
    *,
    rules: Iterable[AltiumPcbRule],
    query: ManufacturingRuleQuery,
    rule_cache: dict[ManufacturingRuleQuery, _SolderMaskRuleFacts | None],
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedSolderMaskInput:
    evidence = _pad_solder_mask_evidence(pad, affected_ref)
    facts = _rule_facts_for_mode(
        evidence.mode,
        rules=rules,
        query=query,
        rule_cache=rule_cache,
        source_index=source_index,
        strictness=strictness,
        affected_ref=affected_ref,
        diagnostics=diagnostics,
    )
    return _resolved_solder_mask(
        evidence,
        facts=facts,
        affected_ref=affected_ref,
        diagnostics=diagnostics,
    )


def _pad_solder_mask_evidence(
    pad: AltiumPcbPad, affected_ref: str
) -> _PrimitiveSolderMaskEvidence:
    mode = _solder_mask_mode(pad.soldermask_expansion_mode, affected_ref)
    cache_marker_raw = (
        int(pad.soldermask_expansion_mode) if pad._has_mask_expansion else None
    )
    cache_state = _primitive_cache_state(
        cache_marker_raw,
        affected_ref=affected_ref,
        field_name="solder-mask cache state",
    )
    cache_context = _cache_context(cache_state)
    if mode == "manual":
        return _PrimitiveSolderMaskEvidence(
            mode=mode,
            cache_context=cache_context,
            cache_marker_raw=cache_marker_raw,
            cache_state=cache_state,
            stored_top_expansion=pad.soldermask_expansion_manual,
            stored_bottom_expansion=pad.soldermask_expansion_manual,
            stored_tented_top=pad.is_tenting_top,
            stored_tented_bottom=pad.is_tenting_bottom,
        )
    if mode == "rule":
        cached_expansion = None
        if pad._has_mask_expansion:
            cached_expansion = pad.soldermask_expansion_manual
        return _PrimitiveSolderMaskEvidence(
            mode=mode,
            cache_context=cache_context,
            cache_marker_raw=cache_marker_raw,
            cache_state=cache_state,
            cached_top_expansion=cached_expansion,
            cached_bottom_expansion=cached_expansion,
            cached_tented_top=pad.is_tenting_top,
            cached_tented_bottom=pad.is_tenting_bottom,
        )
    return _PrimitiveSolderMaskEvidence(
        mode=mode,
        cache_context=cache_context,
        cache_marker_raw=cache_marker_raw,
        cache_state=cache_state,
        stored_from_hole=False,
        stored_top_expansion=0,
        stored_bottom_expansion=0,
        stored_tented_top=pad.is_tenting_top,
        stored_tented_bottom=pad.is_tenting_bottom,
    )


def _rule_facts_for_mode(
    mode: SolderMaskMode,
    *,
    rules: Iterable[AltiumPcbRule],
    query: ManufacturingRuleQuery,
    rule_cache: dict[ManufacturingRuleQuery, _SolderMaskRuleFacts | None],
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> _SolderMaskRuleFacts | None:
    if mode != "rule":
        return None
    return _resolve_solder_mask_rule(
        rules,
        query=query,
        rule_cache=rule_cache,
        source_index=source_index,
        strictness=strictness,
        affected_ref=affected_ref,
        diagnostics=diagnostics,
    )


def _resolved_solder_mask(
    evidence: _PrimitiveSolderMaskEvidence,
    *,
    facts: _SolderMaskRuleFacts | None,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedSolderMaskInput:
    rule_ref: str | None = None
    rule_from_hole: bool | None = None
    rule_top_expansion: int | None = None
    rule_bottom_expansion: int | None = None
    rule_tented_top: bool | None = None
    rule_tented_bottom: bool | None = None
    if facts is not None:
        rule_ref = facts.rule_ref
        rule_from_hole = facts.from_hole_edge
        rule_top_expansion = facts.top_expansion_source_units
        rule_bottom_expansion = facts.bottom_expansion_source_units
        rule_tented_top = facts.tented_top
        rule_tented_bottom = facts.tented_bottom
    return ResolvedSolderMaskInput(
        mode=stored_winning_value(evidence.mode),
        cache_marker_raw=evidence.cache_marker_raw,
        cache_state=evidence.cache_state,
        from_hole_edge=_resolved_mask_value(
            stored_override=evidence.stored_from_hole,
            cached_value=evidence.cached_from_hole,
            rule_value=rule_from_hole,
            rule_ref=rule_ref,
            default_value=False,
            cache_context=evidence.cache_context,
            cache_is_derived=False,
            affected_ref=affected_ref,
            field_name="solder-mask from-hole-edge",
            diagnostics=diagnostics,
        ),
        top=_resolved_solder_mask_side(
            side="top",
            stored_expansion=evidence.stored_top_expansion,
            cached_expansion=evidence.cached_top_expansion,
            rule_expansion=rule_top_expansion,
            stored_tented=evidence.stored_tented_top,
            cached_tented=evidence.cached_tented_top,
            rule_tented=rule_tented_top,
            rule_ref=rule_ref,
            cache_context=evidence.cache_context,
            affected_ref=affected_ref,
            diagnostics=diagnostics,
        ),
        bottom=_resolved_solder_mask_side(
            side="bottom",
            stored_expansion=evidence.stored_bottom_expansion,
            cached_expansion=evidence.cached_bottom_expansion,
            rule_expansion=rule_bottom_expansion,
            stored_tented=evidence.stored_tented_bottom,
            cached_tented=evidence.cached_tented_bottom,
            rule_tented=rule_tented_bottom,
            rule_ref=rule_ref,
            cache_context=evidence.cache_context,
            affected_ref=affected_ref,
            diagnostics=diagnostics,
        ),
    )


def _resolved_solder_mask_side(
    *,
    side: MaskSide,
    stored_expansion: int | None,
    cached_expansion: int | None,
    rule_expansion: int | None,
    stored_tented: bool | None,
    cached_tented: bool | None,
    rule_tented: bool | None,
    rule_ref: str | None,
    cache_context: str,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedSolderMaskSideInput:
    tented = _resolved_mask_value(
        stored_override=stored_tented,
        cached_value=cached_tented,
        rule_value=rule_tented,
        rule_ref=rule_ref,
        default_value=False,
        cache_context=cache_context,
        cache_is_derived=False,
        affected_ref=affected_ref,
        field_name=f"{side} solder-mask tenting",
        diagnostics=diagnostics,
    )
    return ResolvedSolderMaskSideInput(
        side=side,
        expansion_source_units=_resolved_mask_value(
            stored_override=stored_expansion,
            cached_value=cached_expansion,
            rule_value=rule_expansion,
            rule_ref=rule_ref,
            default_value=40_000,
            cache_context=cache_context,
            cache_is_derived=tented.selected_value,
            affected_ref=affected_ref,
            field_name=f"{side} solder-mask expansion",
            diagnostics=diagnostics,
        ),
        tented=tented,
    )


def _resolve_via_solder_mask(
    via: AltiumPcbVia,
    *,
    rules: Iterable[AltiumPcbRule],
    query: ManufacturingRuleQuery,
    rule_cache: dict[ManufacturingRuleQuery, _SolderMaskRuleFacts | None],
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedSolderMaskInput:
    evidence = _via_solder_mask_evidence(via, affected_ref)
    facts = _rule_facts_for_mode(
        evidence.mode,
        rules=rules,
        query=query,
        rule_cache=rule_cache,
        source_index=source_index,
        strictness=strictness,
        affected_ref=affected_ref,
        diagnostics=diagnostics,
    )
    return _resolved_solder_mask(
        evidence,
        facts=facts,
        affected_ref=affected_ref,
        diagnostics=diagnostics,
    )


def _via_solder_mask_evidence(
    via: AltiumPcbVia, affected_ref: str
) -> _PrimitiveSolderMaskEvidence:
    mode = _solder_mask_mode(via.solder_mask_expansion_mode, affected_ref)
    from_hole_edge: bool | None = None
    mask_offset = int(via._soldermask_from_hole_edge_offset)
    if 0 <= mask_offset < int(via._subrecord_length):
        from_hole_edge = via.soldermask_expansion_from_hole_edge
    top_value = via.soldermask_expansion_front
    bottom_value = via.soldermask_expansion_back
    if via.soldermask_expansion_linked:
        bottom_value = top_value
    if mode == "manual":
        return _PrimitiveSolderMaskEvidence(
            mode=mode,
            cache_context="stored_cache",
            stored_from_hole=from_hole_edge,
            stored_top_expansion=top_value,
            stored_bottom_expansion=bottom_value,
            stored_tented_top=via.is_tent_top,
            stored_tented_bottom=via.is_tent_bottom,
        )
    if mode == "none":
        return _PrimitiveSolderMaskEvidence(
            mode=mode,
            cache_context="stored_cache",
            stored_from_hole=from_hole_edge,
            stored_top_expansion=0,
            stored_bottom_expansion=0,
            stored_tented_top=via.is_tent_top,
            stored_tented_bottom=via.is_tent_bottom,
        )
    cached_top: int | None = None
    cached_bottom: int | None = None
    if via._has_soldermask_expansion_front:
        cached_top = top_value
        cached_bottom = top_value
    if via._has_soldermask_expansion_back:
        cached_bottom = bottom_value
    return _PrimitiveSolderMaskEvidence(
        mode=mode,
        cache_context="stored_cache",
        cached_from_hole=from_hole_edge,
        cached_top_expansion=cached_top,
        cached_bottom_expansion=cached_bottom,
        cached_tented_top=via.is_tent_top,
        cached_tented_bottom=via.is_tent_bottom,
    )


def _resolve_pad_lands(
    pad: AltiumPcbPad,
    *,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> tuple[ResolvedPadLandInput, ...]:
    state = pad.layer_state()
    if state.ref.legacy_layer is not PcbLayer.MULTI_LAYER:
        binding = _resolve_layer_binding(
            state,
            layer_stack=layer_stack,
            strictness=strictness,
            affected_ref=affected_ref,
            diagnostics=diagnostics,
        )
        layer = _resolved_layer_for_binding(binding, layer_stack, affected_ref)
        return (_pad_land(pad, layer, binding, affected_ref=affected_ref),)
    copper_layers = tuple(
        layer
        for layer in layer_stack.layers
        if layer.family == "copper"
        and layer.physical_row
        and layer.layer_ref is not None
    )
    if not copper_layers:
        raise PcbResolvedInputError(
            "unresolved_layer", f"{affected_ref} has no resolved copper layers"
        )
    return tuple(
        _pad_land(
            pad,
            layer,
            _expanded_layer_binding(state, layer, layer_stack),
            affected_ref=affected_ref,
        )
        for layer in copper_layers
    )


def _resolved_layer_for_binding(
    binding: ResolvedLayerBinding,
    layer_stack: ResolvedLayerStack,
    affected_ref: str,
) -> ResolvedLayer:
    layer = next(
        (row for row in layer_stack.layers if row.layer_key == binding.layer_key),
        None,
    )
    if layer is None:
        raise PcbResolvedInputError(
            "unresolved_layer", f"{affected_ref} layer binding has no stack row"
        )
    return layer


def _expanded_layer_binding(
    state: PcbPrimitiveLayerState,
    layer: ResolvedLayer,
    layer_stack: ResolvedLayerStack,
) -> ResolvedLayerBinding:
    if layer.layer_ref is None:
        raise PcbResolvedInputError(
            "unresolved_layer", f"{layer.layer_key} has no exact layer identity"
        )
    return ResolvedLayerBinding(
        layer_ref=layer.layer_ref,
        layer_key=layer.layer_key,
        source_field="multilayer_expansion",
        stored_legacy_layer_id=state.stored_legacy_layer_id,
        stored_v7_saved_layer_id=state.stored_v7_saved_layer_id,
        applicable_substack_refs=_applicable_substack_refs(layer, layer_stack),
        applicable_region_stack_refs=_applicable_region_stack_refs(layer, layer_stack),
    )


def _pad_land(
    pad: AltiumPcbPad,
    layer: ResolvedLayer,
    binding: ResolvedLayerBinding,
    *,
    affected_ref: str,
) -> ResolvedPadLandInput:
    legacy_layer = _legacy_pad_layer(layer)
    width, height = _pad_land_size(pad, layer, legacy_layer)
    shape = _pad_land_shape(pad, layer, legacy_layer)
    offset_x, offset_y = pad.pad_offset_internal_units(legacy_layer)
    return ResolvedPadLandInput(
        layer=binding,
        center_x_source_units=stored_winning_value(pad.x + offset_x),
        center_y_source_units=stored_winning_value(pad.y + offset_y),
        width_source_units=stored_winning_value(width),
        height_source_units=stored_winning_value(height),
        shape_code=stored_winning_value(shape),
        corner_radius_percent_e12=_pad_corner_radius_percent_e12(
            pad,
            layer=layer,
            legacy_layer=legacy_layer,
            shape=shape,
            affected_ref=affected_ref,
        ),
        rotation_degrees=stored_winning_value(pad.rotation),
    )


def _pad_corner_radius_percent_e12(
    pad: AltiumPcbPad,
    *,
    layer: ResolvedLayer,
    legacy_layer: PcbLayer | None,
    shape: int,
    affected_ref: str,
) -> ResolvedWinningValue[int] | None:
    if shape != int(PadShape.ROUNDED_RECTANGLE):
        return None
    source_layer = legacy_layer
    if source_layer is None:
        if layer.side == "top":
            source_layer = PcbLayer.TOP
        elif layer.side == "bottom":
            source_layer = PcbLayer.BOTTOM
        else:
            source_layer = PcbLayer.MID1
    exact = pad.exact_corner_radius_percent_on_layer(source_layer)
    if exact is not None:
        percent_e12 = _exact_decimal_scaled_int(
            str(exact),
            decimal_places=12,
            affected_ref=affected_ref,
            field_name=f"{source_layer.name} corner radius percentage",
            invalid_description="finite decimal percentage",
            fractional_description="not exactly representable at e12",
        )
        return stored_winning_value(percent_e12)
    layer_index = source_layer.value - 1
    percent = (
        int(pad.corner_radius[layer_index])
        if 0 <= layer_index < len(pad.corner_radius)
        else 0
    )
    return stored_winning_value(percent * 10**12)


def _legacy_pad_layer(layer: ResolvedLayer) -> PcbLayer | None:
    if layer.legacy_id is None:
        return None
    try:
        return PcbLayer(layer.legacy_id)
    except ValueError:
        return None


def _pad_land_size(
    pad: AltiumPcbPad,
    layer: ResolvedLayer,
    legacy_layer: PcbLayer | None,
) -> tuple[int, int]:
    if legacy_layer is not None:
        return pad._layer_size(legacy_layer)
    if layer.side == "top":
        return int(pad.top_width), int(pad.top_height)
    if layer.side == "bottom":
        return int(pad.bot_width), int(pad.bot_height)
    return int(pad.mid_width), int(pad.mid_height)


def _pad_land_shape(
    pad: AltiumPcbPad,
    layer: ResolvedLayer,
    legacy_layer: PcbLayer | None,
) -> int:
    if legacy_layer is not None:
        return pad._layer_shape(legacy_layer)
    if layer.side == "top":
        return int(pad.top_shape)
    if layer.side == "bottom":
        return int(pad.bot_shape)
    return int(pad.mid_shape)


def _resolve_via(
    via: AltiumPcbVia,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    component_refs: tuple[str, ...],
    rules: Iterable[AltiumPcbRule],
    solder_mask_cache: dict[ManufacturingRuleQuery, _SolderMaskRuleFacts | None],
    diagnostics: list[Diagnostic],
) -> ResolvedViaInput:
    source, input_id = _record_source_and_ref(
        via,
        kind="via",
        fallback_ref=f"input.via.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    if via.via_mode != PcbViaMode.SIMPLE:
        raise PcbResolvedInputError(
            "unsupported_geometry", f"{input_id} advanced via stack is pending"
        )
    start_ref, end_ref = via.layer_span_refs()
    copper_span = _via_copper_span(start_ref, end_ref, layer_stack, input_id)
    rule_query = _manufacturing_rule_query(via, object_kind="via")
    return ResolvedViaInput(
        id=input_id,
        source=source,
        source_net_ref=_optional_indexed_ref(via.net_index, net_refs, "net", input_id),
        component_occurrence_ref=_optional_indexed_ref(
            via.component_index, component_refs, "component", input_id
        ),
        coordinate_frame="board",
        source_to_board_affine=identity_affine(),
        center_x_source_units=stored_winning_value(via.x),
        center_y_source_units=stored_winning_value(via.y),
        start_layer_ref=start_ref,
        end_layer_ref=end_ref,
        lands=tuple(_via_land(via, layer, layer_stack) for layer in copper_span),
        hole_size_source_units=stored_winning_value(via.hole_size),
        solder_mask=_resolve_via_solder_mask(
            via,
            rules=rules,
            query=rule_query,
            rule_cache=solder_mask_cache,
            source_index=source_index,
            strictness=strictness,
            affected_ref=input_id,
            diagnostics=diagnostics,
        ),
        plane_cache=_via_plane_cache_evidence(via),
        plated=True,
        rule_query=rule_query,
    )


def _via_plane_cache_evidence(via: AltiumPcbVia) -> ResolvedPlaneCacheEvidence:
    cache_present = via._subrecord_length > 74
    return ResolvedPlaneCacheEvidence(
        cache_present=cache_present,
        connection_style_code=int(via.plane_connect_style),
        relief_conductor_width_source_units=int(via.thermal_relief_conductorwidth),
        relief_entries=int(via.thermal_relief_conductorcount),
        relief_air_gap_source_units=int(via.thermal_relief_airgap),
        relief_expansion_source_units=int(via.power_plane_relief_expansion),
        clearance_source_units=int(via.power_plane_clearance),
        validity_mapping="via_unmapped",
        connection_style_valid_raw=None,
        relief_conductor_width_valid_raw=None,
        relief_entries_valid_raw=None,
        relief_air_gap_valid_raw=None,
        relief_expansion_valid_raw=None,
        clearance_valid_raw=None,
        unmapped_validity_raw=(
            (
                int(via.cache_valid_60),
                int(via.cache_valid_61),
                int(via.cache_valid_62),
                int(via.cache_valid_63),
                int(via.cache_valid_64),
                int(via.cache_valid_67),
                int(via.cache_valid_68),
                int(via.cache_valid_71),
            )
            if cache_present
            else ()
        ),
    )


def _resolve_fill(
    fill: AltiumPcbFill,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    component_refs: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> ResolvedFillInput:
    source, input_id = _record_source_and_ref(
        fill,
        kind="fill",
        fallback_ref=f"input.fill.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return ResolvedFillInput(
        id=input_id,
        source=source,
        layer=_resolve_layer_binding(
            fill.layer_state(),
            layer_stack=layer_stack,
            strictness=strictness,
            affected_ref=input_id,
            diagnostics=diagnostics,
        ),
        source_net_ref=_optional_indexed_ref(fill.net_index, net_refs, "net", input_id),
        component_occurrence_ref=_optional_indexed_ref(
            fill.component_index, component_refs, "component", input_id
        ),
        coordinate_frame="board",
        source_to_board_affine=identity_affine(),
        pos1_x_source_units=stored_winning_value(fill.pos1_x),
        pos1_y_source_units=stored_winning_value(fill.pos1_y),
        pos2_x_source_units=stored_winning_value(fill.pos2_x),
        pos2_y_source_units=stored_winning_value(fill.pos2_y),
        rotation_degrees=stored_winning_value(fill.rotation),
        polygon_index=fill.polygon_index,
        is_keepout=fill.is_keepout,
        is_polygon_outline=fill.is_polygon_outline,
    )


def _resolve_polygon(
    polygon: AltiumPcbPolygon,
    *,
    ordinal: int,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    net_refs: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> ResolvedPolygonInput:
    source, input_id = _record_source_and_ref(
        polygon,
        kind="polygon",
        fallback_ref=f"input.polygon.{ordinal}",
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    polygon_type = _polygon_source_text(polygon, "POLYGONTYPE", input_id)
    definition_name = _polygon_optional_definition_name(polygon, input_id)
    net_index = _polygon_optional_source_int(polygon, "NET", input_id)
    locked = _polygon_optional_source_bool(polygon, "LOCKED", input_id)
    return ResolvedPolygonInput(
        id=input_id,
        source=source,
        layer=_resolve_polygon_layer_binding(
            polygon,
            layer_stack=layer_stack,
            affected_ref=input_id,
        ),
        source_net_ref=(
            None
            if net_index is None
            else _optional_indexed_ref(net_index, net_refs, "net", input_id)
        ),
        net_identity_exact=net_index is not None,
        definition_name=definition_name,
        polygon_type=polygon_type,
        is_keepout=_polygon_optional_source_bool(polygon, "KEEPOUT", input_id),
        is_shelved=_polygon_optional_source_bool(polygon, "SHELVED", input_id),
        is_polygon_outline=_polygon_optional_source_bool(
            polygon, "POLYGONOUTLINE", input_id
        ),
        rule_query=(
            None
            if locked is None
            else ManufacturingRuleQuery(object_kind="polygon", locked=locked)
        ),
    )


def _resolve_polygon_layer_binding(
    polygon: AltiumPcbPolygon,
    *,
    layer_stack: ResolvedLayerStack,
    affected_ref: str,
) -> ResolvedLayerBinding:
    token = _polygon_source_text(polygon, "LAYER", affected_ref)
    try:
        layer_ref = PcbLayerRef.parse(token)
    except ValueError as exc:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} has unsupported polygon layer token {token!r}",
        ) from exc
    resolved_layer = _unique_primitive_layer_occurrence(
        (
            layer
            for layer in layer_stack.layers
            if layer.layer_ref is not None and layer.layer_ref == layer_ref
        ),
        affected_ref=affected_ref,
        identity=f"polygon layer reference {layer_ref.token}",
    )
    return ResolvedLayerBinding(
        layer_ref=layer_ref,
        layer_key=resolved_layer.layer_key,
        source_field="Polygons6/Data.LAYER",
        stored_legacy_layer_id=None,
        stored_v7_saved_layer_id=None,
        applicable_substack_refs=_applicable_substack_refs(resolved_layer, layer_stack),
        applicable_region_stack_refs=_applicable_region_stack_refs(
            resolved_layer, layer_stack
        ),
    )


def _polygon_source_text(
    polygon: AltiumPcbPolygon,
    field_name: str,
    affected_ref: str,
) -> str:
    value: object = polygon._raw_record.get(field_name)
    if value is None or not str(value).strip():
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} has no exact Polygons6/Data {field_name} fact",
        )
    return str(value).strip()


def _polygon_optional_definition_name(
    polygon: AltiumPcbPolygon,
    affected_ref: str,
) -> str | None:
    value: object = polygon._raw_record.get("NAME")
    if value is None or not str(value).strip():
        return None
    name = polygon.name.strip()
    if not name:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} has an invalid Polygons6/Data NAME fact",
        )
    return name


def _polygon_optional_source_int(
    polygon: AltiumPcbPolygon,
    field_name: str,
    affected_ref: str,
) -> int | None:
    value: object = polygon._raw_record.get(field_name)
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return int(text, 10)
    except ValueError as exc:
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} has invalid Polygons6/Data {field_name} {text!r}",
        ) from exc


def _polygon_optional_source_bool(
    polygon: AltiumPcbPolygon,
    field_name: str,
    affected_ref: str,
) -> bool | None:
    value: object = polygon._raw_record.get(field_name)
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().casefold()
    if text == "true":
        return True
    if text == "false":
        return False
    raise PcbResolvedInputError(
        "corrupt_identity",
        f"{affected_ref} has invalid Polygons6/Data {field_name} boolean {text!r}",
    )


def _assert_unique_resolved_polygon_ids(
    polygons: tuple[ResolvedPolygonInput, ...],
) -> None:
    ids: set[str] = set()
    for polygon in polygons:
        if polygon.id in ids:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"duplicate resolved polygon occurrence {polygon.id!r}",
            )
        ids.add(polygon.id)


def _via_copper_span(
    start_ref: PcbLayerRef,
    end_ref: PcbLayerRef,
    layer_stack: ResolvedLayerStack,
    affected_ref: str,
) -> tuple[ResolvedLayer, ...]:
    copper = tuple(
        layer
        for layer in layer_stack.layers
        if layer.family == "copper"
        and layer.physical_row
        and layer.layer_ref is not None
    )
    refs = tuple(layer.layer_ref for layer in copper)
    try:
        start = refs.index(start_ref)
        end = refs.index(end_ref)
    except ValueError as exc:
        raise PcbResolvedInputError(
            "unresolved_layer", f"{affected_ref} via span endpoint is absent"
        ) from exc
    lo, hi = sorted((start, end))
    return copper[lo : hi + 1]


def _via_land(
    via: AltiumPcbVia,
    layer: ResolvedLayer,
    layer_stack: ResolvedLayerStack,
) -> ResolvedViaLandInput:
    if layer.layer_ref is None:
        raise PcbResolvedInputError(
            "unresolved_layer", f"{layer.layer_key} has no exact layer identity"
        )
    legacy_layer = _legacy_pad_layer(layer)
    if legacy_layer is None:
        diameter = int(via.diameter)
    else:
        layer_index = legacy_layer.value - 1
        if (
            0 <= layer_index < len(via.is_pad_removed)
            and via.is_pad_removed[layer_index]
        ):
            raise PcbResolvedInputError(
                "unsupported_geometry", "simple via with removed land is pending"
            )
        diameter = via._diameter_for_layer(legacy_layer)
    state = PcbPrimitiveLayerState(
        ref=layer.layer_ref,
        source_field="via_span_expansion",
        stored_legacy_layer_id=layer.legacy_id,
        stored_v7_saved_layer_id=layer.v7_id,
    )
    return ResolvedViaLandInput(
        layer=_expanded_layer_binding(state, layer, layer_stack),
        diameter_source_units=stored_winning_value(diameter),
    )


def _record_ref(
    record: object,
    *,
    kind: str,
    fallback_ref: str,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> str:
    _source, record_ref = _record_source_and_ref(
        record,
        kind=kind,
        fallback_ref=fallback_ref,
        source_index=source_index,
        strictness=strictness,
        diagnostics=diagnostics,
    )
    return record_ref


def _record_source_and_ref(
    record: object,
    *,
    kind: str,
    fallback_ref: str,
    source_index: PcbDocSourceIndex,
    strictness: Strictness,
    diagnostics: list[Diagnostic],
) -> tuple[SourceProvenance, str]:
    resolution = source_index.resolve(
        record,
        strictness=strictness,
        affected_ref=fallback_ref,
    )
    diagnostics.extend(resolution.diagnostics)
    return resolution.source, _input_ref(kind, resolution.source, fallback_ref)


def _input_ref(kind: str, source: SourceProvenance, fallback_ref: str) -> str:
    if isinstance(source, UnresolvedSource):
        return fallback_ref
    return source_occurrence_ref(kind, source)


def _resolve_layer_binding(
    state: PcbPrimitiveLayerState,
    *,
    layer_stack: ResolvedLayerStack,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedLayerBinding:
    _validate_primitive_layer_state_numeric_domains(state, affected_ref=affected_ref)
    resolved_layer = _resolved_layer_for_state(
        state,
        layer_stack=layer_stack,
        affected_ref=affected_ref,
    )
    if resolved_layer is None:
        return _unresolved_layer_binding(
            state,
            strictness=strictness,
            affected_ref=affected_ref,
            diagnostics=diagnostics,
        )
    substack_refs = _applicable_substack_refs(resolved_layer, layer_stack)
    region_stack_refs = _applicable_region_stack_refs(resolved_layer, layer_stack)
    return ResolvedLayerBinding(
        layer_ref=state.ref,
        layer_key=resolved_layer.layer_key,
        source_field=state.source_field,
        stored_legacy_layer_id=state.stored_legacy_layer_id,
        stored_v7_saved_layer_id=state.stored_v7_saved_layer_id,
        applicable_substack_refs=substack_refs,
        applicable_region_stack_refs=region_stack_refs,
    )


def _resolved_layer_for_state(
    state: PcbPrimitiveLayerState,
    *,
    layer_stack: ResolvedLayerStack,
    affected_ref: str,
) -> ResolvedLayer | None:
    saved_layer = _saved_layer_for_state(state, layer_stack, affected_ref)
    legacy_layer = _legacy_layer_for_state(state, layer_stack, affected_ref)
    if (
        saved_layer is not None
        and legacy_layer is not None
        and not _same_semantic_layer(saved_layer, legacy_layer)
        and not _is_known_v7_legacy_placeholder(
            state, saved_layer, affected_ref=affected_ref
        )
    ):
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} has contradictory saved V7 and legacy layer evidence",
        )
    resolved_layer = saved_layer or legacy_layer
    resolved_ref = (
        None
        if resolved_layer is None
        else _resolved_layer_ref_authority(resolved_layer, affected_ref=affected_ref)
    )
    if resolved_ref is not None and not _same_pcb_layer_ref_representation(
        state.ref, resolved_ref
    ):
        raise PcbResolvedInputError(
            "corrupt_identity",
            f"{affected_ref} resolved layer disagrees with its semantic layer ref",
        )
    return resolved_layer


def _is_known_v7_legacy_placeholder(
    state: PcbPrimitiveLayerState,
    saved_layer: ResolvedLayer,
    *,
    affected_ref: str,
) -> bool:
    ref = _resolved_layer_ref_authority(saved_layer, affected_ref=affected_ref)
    if ref is None:
        return False
    placeholder_id = _v7_only_legacy_placeholder_id(ref)
    return placeholder_id is not None and state.stored_legacy_layer_id == placeholder_id


def _saved_layer_for_state(
    state: PcbPrimitiveLayerState,
    layer_stack: ResolvedLayerStack,
    affected_ref: str,
) -> ResolvedLayer | None:
    layer_id = state.stored_v7_saved_layer_id
    if layer_id in (None, 0):
        return None
    return _unique_primitive_layer_occurrence(
        (layer for layer in layer_stack.layers if layer.v7_id == layer_id),
        affected_ref=affected_ref,
        identity=f"saved V7 layer ID {layer_id}",
    )


def _legacy_layer_for_state(
    state: PcbPrimitiveLayerState,
    layer_stack: ResolvedLayerStack,
    affected_ref: str,
) -> ResolvedLayer | None:
    layer_id = state.stored_legacy_layer_id
    if layer_id is None:
        return None
    return _unique_primitive_layer_occurrence(
        (layer for layer in layer_stack.layers if layer.legacy_id == layer_id),
        affected_ref=affected_ref,
        identity=f"legacy layer ID {layer_id}",
    )


def _unique_primitive_layer_occurrence(
    candidates: Iterable[ResolvedLayer],
    *,
    affected_ref: str,
    identity: str,
) -> ResolvedLayer:
    matches: tuple[ResolvedLayer, ...] = tuple(candidates)
    if not matches:
        raise PcbResolvedInputError(
            "unresolved_layer",
            f"{affected_ref} {identity} is not in ResolvedLayerStack",
        )
    if len(matches) > 1:
        raise PcbResolvedInputError(
            "unresolved_layer_context",
            f"{affected_ref} {identity} names multiple contextual layer occurrences",
        )
    return next(iter(matches))


def _same_semantic_layer(first: ResolvedLayer, second: ResolvedLayer) -> bool:
    if first.layer_ref is not None and second.layer_ref is not None:
        return first.layer_ref == second.layer_ref
    return first.layer_key == second.layer_key


def _layer_matches(candidate: ResolvedLayer, selected: ResolvedLayer) -> bool:
    return _same_semantic_layer(candidate, selected)


def _applicable_substack_refs(
    selected_layer: ResolvedLayer, layer_stack: ResolvedLayerStack
) -> tuple[str, ...]:
    return tuple(
        substack.source_stackup_ref
        for substack in layer_stack.substacks
        if any(_layer_matches(layer, selected_layer) for layer in substack.layers)
    )


def _applicable_region_stack_refs(
    selected_layer: ResolvedLayer, layer_stack: ResolvedLayerStack
) -> tuple[str, ...]:
    return tuple(
        context.layerstack_id
        for context in layer_stack.board_region_contexts
        if context.layerstack_id
        and any(
            _layer_matches(layer, selected_layer)
            for layer in layer_stack.layers_for_board_region(context.layerstack_id)
        )
    )


def _unresolved_layer_binding(
    state: PcbPrimitiveLayerState,
    *,
    strictness: Strictness,
    affected_ref: str,
    diagnostics: list[Diagnostic],
) -> ResolvedLayerBinding:
    detail = f"{affected_ref} layer {state.ref.token} is absent from ResolvedLayerStack"
    if strictness == "strict":
        raise PcbResolvedInputError("unresolved_layer", detail)
    diagnostic_id = _diagnostic_id("unresolved_layer", affected_ref, detail)
    diagnostics.append(
        Diagnostic(
            id=diagnostic_id,
            code="unresolved_layer",
            severity="warning",
            message=detail,
            affected_ref=affected_ref,
        )
    )
    return ResolvedLayerBinding(
        layer_ref=state.ref,
        layer_key=f"unresolved.{state.ref.token.lower()}",
        source_field=state.source_field,
        stored_legacy_layer_id=state.stored_legacy_layer_id,
        stored_v7_saved_layer_id=state.stored_v7_saved_layer_id,
        applicable_substack_refs=(),
        applicable_region_stack_refs=(),
    )


def _indexed_ref(
    index: int | None,
    refs: tuple[str, ...],
    kind: str,
    affected_ref: str,
) -> str | None:
    if index is None:
        return None
    if 0 <= index < len(refs):
        return refs[index]
    raise PcbResolvedInputError(
        "corrupt_identity",
        f"{affected_ref} references missing {kind} index {index}",
    )


def _optional_indexed_ref(
    index: int | None,
    refs: tuple[str, ...],
    kind: str,
    affected_ref: str,
) -> str | None:
    if index in (None, 0xFFFF, 0xFFFFFFFF):
        return None
    return _indexed_ref(index, refs, kind, affected_ref)


def _diagnostic_id(code: str, affected_ref: str, detail: str) -> str:
    digest = hashlib.sha256(
        f"{code}\0{affected_ref}\0{detail}".encode("utf-8")
    ).hexdigest()[:20]
    return f"diagnostic.{code}.{digest}"


__all__ = (
    "ClearanceRuleKind",
    "ClearanceRuleSelectionDisposition",
    "ComponentSide",
    "CoordinateFrame",
    "PcbChildRequestOutcome",
    "PcbResolvedChildBoardOccurrence",
    "PcbResolvedInputError",
    "PlaneCacheValidityMapping",
    "PlaneConnectPrimitiveKind",
    "PlaneConnectStyle",
    "PlaneRuleSelectionDisposition",
    "PrimitiveCacheState",
    "PolygonConnectPrimitiveKind",
    "PolygonConnectRuleSelectionDisposition",
    "PolygonReliefAngleDegrees",
    "ResolvedArcInput",
    "ResolvedClearancePairAuthority",
    "ResolvedClearanceRuleAuthority",
    "ResolvedClearanceRuleCandidateAuthority",
    "ResolvedComponentClassInput",
    "ResolvedComponentOccurrenceInput",
    "ResolvedEmbeddedBoardReferenceInput",
    "ResolvedFillInput",
    "ResolvedLayerBinding",
    "ResolvedNetClassInput",
    "ResolvedNetInput",
    "ResolvedPadInput",
    "ResolvedPadLandInput",
    "ResolvedPasteMaskInput",
    "ResolvedPasteMaskSideInput",
    "ResolvedPcbInputs",
    "ResolvedPlaneCacheEvidence",
    "ResolvedPlaneClearanceRuleAuthority",
    "ResolvedPlaneConnectRuleAuthority",
    "ResolvedPlaneConnectSettingsAuthority",
    "ResolvedPlaneRuleAuthority",
    "ResolvedPolygonConnectRuleAuthority",
    "ResolvedPolygonConnectRuleCandidateAuthority",
    "ResolvedPolygonConnectRuleSelection",
    "ResolvedPolygonConnectSettingsAuthority",
    "ResolvedPolygonInput",
    "ResolvedSolderMaskInput",
    "ResolvedSolderMaskSideInput",
    "ResolvedTrackInput",
    "ResolvedViaInput",
    "ResolvedViaLandInput",
    "ResolvedWinningValue",
    "SolderMaskMode",
    "Strictness",
    "WinningValueSource",
    "resolve_pcb_stored_inputs",
    "resolve_pcb_child_request_outcome",
    "resolve_pcb_component_class_inputs",
    "resolve_pcb_child_board_occurrence_rows",
    "resolve_pcb_child_board_occurrences",
    "resolve_pcb_embedded_board_references",
    "resolve_pcb_clearance_rule_authority",
    "resolve_pcb_plane_rule_authority",
    "resolve_pcb_polygon_connect_rule_authority",
    "resolve_pcb_layer_binding",
    "resolve_pcb_net_class_inputs",
    "stored_winning_value",
)
