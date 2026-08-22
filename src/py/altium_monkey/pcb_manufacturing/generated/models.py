"""Generated strict msgspec bindings. Do not edit."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from msgspec import UNSET, Meta, Struct, UnsetType

type DiagnosticCode = Annotated[
    Literal[
        "unresolved_source",
        "corrupt_identity",
        "integer_overflow",
        "topology_degeneracy",
        "unsupported_geometry",
        "unresolved_layer",
        "contradictory_material",
        "child_document_invalid_path",
        "child_document_denied",
        "child_document_missing",
        "child_document_ambiguous",
        "child_document_changed_revision",
        "child_document_cyclic_reference",
        "child_document_resource_limit",
        "child_document_invalid_format",
    ],
    Meta(description="Closed diagnostic code inventory for the a0 skeleton."),
]

type DiagnosticSeverity = Annotated[
    Literal["info", "warning", "error"], Meta(description="Closed diagnostic severity.")
]

type FilmBaseline = Annotated[
    Literal["empty", "full", "profile"],
    Meta(description="Baseline against which ordered film operations are evaluated."),
]

type Geometry = Annotated[
    PathGeometry
    | StrokeGeometry
    | CircleGeometry
    | CapsuleGeometry
    | OrientedRectangleGeometry
    | RoundedRectangleGeometry
    | RegionGeometry,
    Meta(description="Closed analytic geometry inventory for the a0 skeleton."),
]

type PathSegment = Annotated[
    LineSegment | CircularArcSegment,
    Meta(description="Closed analytic path-segment inventory."),
]

type LayerSide = Annotated[
    Literal["top", "internal", "bottom", "both", "none"],
    Meta(description="Closed layer-side classification."),
]

type LogicalPath = Annotated[
    str, Meta(description="Non-empty project-relative logical path.", min_length=1)
]

type ChildBoardRequestDisposition = Annotated[
    Literal["loaded", "unavailable"],
    Meta(description="Provider resolution state for one retained child-board request."),
]

type ComponentSide = Annotated[
    Literal["top", "bottom"], Meta(description="Closed component mounting side.")
]

type Feature = Annotated[
    MaterialFeature | HoleFeature | ProfileFeature,
    Meta(description="Closed linked feature inventory for the a0 skeleton."),
]

type MaterialFeatureKind = Annotated[
    Literal["route", "land", "fill"],
    Meta(
        description="Semantic manufacturing role independent of analytic geometry and provenance."
    ),
]

type ProfileOperation = Annotated[
    Literal["outer", "cutout"], Meta(description="Closed board-profile contour role.")
]

type VariantSelectionKind = Annotated[
    Literal["no_variations", "project_variant"],
    Meta(description="Closed output variant selection kind."),
]

type MaterialPolarity = Annotated[
    Literal["add", "subtract"], Meta(description="Ordered material operation polarity.")
]

type MaterialRole = Annotated[
    Literal[
        "conductor",
        "solder_mask",
        "paste",
        "silkscreen",
        "profile",
        "hole",
        "plating",
        "mechanical",
    ],
    Meta(description="Closed physical material role."),
]

type Sha256 = Annotated[
    str, Meta(description="Lowercase SHA-256 digest.", pattern="^[0-9a-f]{64}$")
]

type SourceProvenance = Annotated[
    LocatedSource | RuntimeSource | UnresolvedSource,
    Meta(description="Complete or explicitly degraded source provenance."),
]

type StableRef = Annotated[
    str, Meta(description="Non-empty stable semantic reference.", min_length=1)
]

type StrictnessMode = Annotated[
    Literal["strict", "permissive"],
    Meta(description="Closed materialization strictness mode."),
]

type TopologyStatus = Annotated[
    Literal["stable", "unfused_degeneracy"],
    Meta(
        description="Whether quantization preserves topology or requires unfused handling."
    ),
]


class CapsuleGeometry(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.geometry.capsule",
    tag_field="type",
):
    """Analytic capsule with its overall major axis on local +X."""

    center: Point2d
    overall_length_nm: Annotated[
        int, Meta(ge=-9223372036854775808, le=9223372036854775807)
    ]
    diameter_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    affine: PcbDecimalAffine2d


class CircleGeometry(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.geometry.circle",
    tag_field="type",
):
    """Analytic circle."""

    center: Point2d
    radius_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]


class CircularArcSegment(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.segment.circular_arc",
    tag_field="type",
):
    """Native circular-arc path segment."""

    start: Point2d
    end: Point2d
    center: Point2d
    clockwise: bool
    sweep_degrees_e12: Annotated[
        int,
        Meta(
            description="Positive analytic sweep magnitude in degrees, scaled by 10^12.",
            ge=-9223372036854775808,
            le=9223372036854775807,
        ),
    ]


class LineSegment(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.segment.line",
    tag_field="type",
):
    """Straight analytic path segment."""

    start: Point2d
    end: Point2d


class OrientedRectangleGeometry(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.geometry.oriented_rectangle",
    tag_field="type",
):
    """Analytic oriented rectangle."""

    center: Point2d
    width_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    height_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    affine: PcbDecimalAffine2d


class PathGeometry(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.geometry.path",
    tag_field="type",
):
    """Analytic open or closed path."""

    closed: bool
    segments: Annotated[list[PathSegment], Meta(min_length=1)]


class PcbDecimalAffine2d(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Local decimal-fixed rigid/reflected affine with coefficient scale 10^15."""

    type: Literal["pcb.manufacturing.affine2d.decimal_e15"]
    a_e15: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    b_e15: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    c_e15: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    d_e15: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    tx_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    ty_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    composition_depth: Annotated[int, Meta(ge=0, le=4294967295)]


class Point2d(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Signed integer-nanometer coordinate."""

    x_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    y_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]


class RegionGeometry(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.geometry.region",
    tag_field="type",
):
    """Analytic region with explicit outer and hole rings."""

    outer: RegionRing
    holes: list[RegionRing]


class RegionRing(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """One explicitly oriented analytic region ring."""

    clockwise: bool
    segments: Annotated[list[PathSegment], Meta(min_length=1)]


class RoundedRectangleGeometry(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.geometry.rounded_rectangle",
    tag_field="type",
):
    """Analytic rounded rectangle with one radius shared by all corners."""

    center: Point2d
    width_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    height_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    corner_radius_nm: Annotated[
        int, Meta(ge=-9223372036854775808, le=9223372036854775807)
    ]
    affine: PcbDecimalAffine2d


class StrokeGeometry(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.geometry.stroke",
    tag_field="type",
):
    """Constant-width material swept along one analytic centerline path."""

    path: PathGeometry
    width_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]


class LocatedSource(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.source.located",
    tag_field="type",
):
    """Exact source-record locator and optional persistent identity."""

    document_revision_sha256: Sha256
    logical_path: LogicalPath
    stream_name: str
    record_index: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    subrecord_index: (
        Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
        | UnsetType
    ) = UNSET
    persistent_uid: StableRef | UnsetType = UNSET
    source_unit_nm_numerator: (
        Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
        | UnsetType
    ) = UNSET
    source_unit_nm_denominator: (
        Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
        | UnsetType
    ) = UNSET


class BoardOccurrence(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """One source or repeated board occurrence."""

    id: StableRef
    source: SourceProvenance
    parent_occurrence_ref: StableRef | UnsetType = UNSET
    child_request_ref: StableRef | UnsetType = UNSET
    provider_id: StableRef | UnsetType = UNSET
    resolved_logical_path: LogicalPath | UnsetType = UNSET
    document_revision_sha256: Sha256 | UnsetType = UNSET
    row_index: Annotated[int, Meta(ge=0, le=4294967295)] | UnsetType = UNSET
    column_index: Annotated[int, Meta(ge=0, le=4294967295)] | UnsetType = UNSET
    step_row_index: Annotated[int, Meta(ge=0, le=4294967295)] | UnsetType = UNSET
    step_column_index: Annotated[int, Meta(ge=0, le=4294967295)] | UnsetType = UNSET
    affine: PcbDecimalAffine2d


class ChildBoardRequest(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """One retained child-board request before occurrence expansion."""

    id: StableRef
    parent_board_occurrence_ref: StableRef
    source: SourceProvenance
    requested_document_path: LogicalPath
    provider_id: StableRef
    expected_revision_sha256: Sha256 | UnsetType = UNSET
    disposition: ChildBoardRequestDisposition
    resolved_logical_path: LogicalPath | UnsetType = UNSET
    observed_revision_sha256: Sha256 | UnsetType = UNSET
    document_revision_sha256: Sha256 | UnsetType = UNSET
    diagnostic_ref: StableRef | UnsetType = UNSET


class ComponentOccurrence(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    """One source component occurrence and its resolved fitted decision."""

    id: StableRef
    board_occurrence_ref: StableRef
    source: SourceProvenance
    source_component_unique_id: StableRef | UnsetType = UNSET
    display_designator: str
    footprint: str
    side: ComponentSide
    origin: Point2d
    rotation_degrees_e12: Annotated[
        int, Meta(ge=-9223372036854775808, le=9223372036854775807)
    ]
    local_to_board_affine: PcbDecimalAffine2d
    fitted: bool
    fitted_selection_ref: StableRef
    variation_kind: (
        Annotated[
            int,
            Meta(
                description="Supported values are 0 (fitted) and 1 (not fitted).",
                ge=0,
                le=4294967295,
            ),
        ]
        | UnsetType
    ) = UNSET
    variation_source: SourceProvenance | UnsetType = UNSET


class Diagnostic(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Typed materialization diagnostic."""

    id: StableRef
    code: DiagnosticCode
    severity: DiagnosticSeverity
    message: str
    source: SourceProvenance | UnsetType = UNSET
    affected_ref: StableRef | UnsetType = UNSET


class DrillSpan(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """V7-aware physical drill span."""

    id: StableRef
    source: SourceProvenance
    start_layer_ref: StableRef
    end_layer_ref: StableRef
    backdrill: bool


class HoleFeature(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.feature.hole",
    tag_field="type",
):
    """Physical drilled or routed void linked to its owner and span."""

    id: StableRef
    parent_feature_ref: StableRef | UnsetType = UNSET
    source: SourceProvenance
    precision: PrecisionEnvelope
    geometry: Geometry
    drill_span_ref: StableRef
    plated: bool


class LayerInstance(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """One exact resolved physical or output layer instance."""

    id: StableRef
    stack_region_ref: StableRef
    pcb_layer_ref: StableRef
    legacy_layer_id: (
        Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
        | UnsetType
    ) = UNSET
    saved_v7_layer_id: (
        Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
        | UnsetType
    ) = UNSET
    side: LayerSide
    material_role: MaterialRole
    z_min_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    z_max_nm: Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
    film_baseline: FilmBaseline


class ManufacturingDocument(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    """Standalone normalized PCB manufacturing materialization root."""

    __wn_annotations__: ClassVar[dict[str, object]] = {
        "title": "Altium Monkey PCB Manufacturing Materialization a0"
    }
    __wn_schema_id__: ClassVar[str] = (
        "urn:wavenumber:schema:altium_monkey.pcb.manufacturing_materialization:a0"
    )
    type: Literal["altium_monkey.pcb.manufacturing_materialization"]
    version: Literal["a0"]
    generator_revision: StableRef
    strictness: StrictnessMode
    board_occurrences: list[BoardOccurrence]
    child_board_requests: list[ChildBoardRequest]
    stack_regions: list[StackRegion]
    layers: list[LayerInstance]
    nets: list[SourceNet]
    variant_selections: list[VariantSelection]
    component_occurrences: list[ComponentOccurrence]
    drill_spans: list[DrillSpan]
    features: list[Feature]
    projections: list[SelectionProjection]
    diagnostics: list[Diagnostic]


class MaterialFeature(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.feature.material",
    tag_field="type",
):
    """Analytic ordered material operation."""

    id: StableRef
    parent_feature_ref: StableRef | UnsetType = UNSET
    source: SourceProvenance
    layer_ref: StableRef
    source_net_ref: StableRef | UnsetType = UNSET
    component_occurrence_ref: StableRef | UnsetType = UNSET
    feature_kind: MaterialFeatureKind
    material_role: MaterialRole
    polarity: MaterialPolarity
    precision: PrecisionEnvelope
    geometry: Geometry


class PrecisionEnvelope(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Conservative propagated precision bound for one physical feature."""

    max_coordinate_error_pm: Annotated[
        int, Meta(ge=-9223372036854775808, le=9223372036854775807)
    ]
    max_scalar_error_pm: Annotated[
        int, Meta(ge=-9223372036854775808, le=9223372036854775807)
    ]
    affine_depth: Annotated[int, Meta(ge=0, le=4294967295)]
    topology_status: TopologyStatus


class ProfileFeature(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.feature.profile",
    tag_field="type",
):
    """One analytic board-profile contour with explicit outer/cutout semantics."""

    id: StableRef
    parent_feature_ref: StableRef | UnsetType = UNSET
    board_occurrence_ref: StableRef
    source: SourceProvenance
    operation: ProfileOperation
    clockwise: bool
    precision: PrecisionEnvelope
    geometry: PathGeometry


class SelectionProjection(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    """Named complete-output projection request."""

    id: StableRef
    requested_layer_refs: list[StableRef]
    requested_product_refs: list[StableRef]


class SourceNet(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Source net retained independently of adapter naming."""

    id: StableRef
    board_occurrence_ref: Annotated[
        StableRef,
        Meta(
            description="Exact board occurrence that owns this electrical net identity."
        ),
    ]
    source: SourceProvenance
    display_name: str | UnsetType = UNSET


class StackRegion(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """One resolved stack region."""

    id: StableRef
    board_occurrence_ref: StableRef
    source: SourceProvenance


class VariantSelection(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Output variant authority applied to one exact board occurrence."""

    id: StableRef
    board_occurrence_ref: StableRef
    source: SourceProvenance
    kind: VariantSelectionKind
    display_name: str
    project_variant_unique_id: StableRef | UnsetType = UNSET


class RuntimeSource(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.source.runtime",
    tag_field="type",
):
    """Stable identity for an object created through a governed mutation API."""

    document_ref: StableRef
    object_ref: StableRef
    persistent_uid: StableRef | UnsetType = UNSET
    source_unit_nm_numerator: (
        Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
        | UnsetType
    ) = UNSET
    source_unit_nm_denominator: (
        Annotated[int, Meta(ge=-9223372036854775808, le=9223372036854775807)]
        | UnsetType
    ) = UNSET


class UnresolvedSource(
    Struct,
    forbid_unknown_fields=True,
    frozen=True,
    kw_only=True,
    tag="pcb.manufacturing.source.unresolved",
    tag_field="type",
):
    """Typed degraded provenance retained when no trustworthy locator exists."""

    diagnostic_ref: StableRef
    reason: str


WN_MODEL_OPS: dict[str, tuple[object, ...]] = {}

WN_SCHEMA_ROOTS: dict[object, str] = {
    ManufacturingDocument: "urn:wavenumber:schema:altium_monkey.pcb.manufacturing_materialization:a0",
}

__all__ = (
    "DiagnosticCode",
    "DiagnosticSeverity",
    "FilmBaseline",
    "CapsuleGeometry",
    "CircleGeometry",
    "CircularArcSegment",
    "Geometry",
    "LineSegment",
    "OrientedRectangleGeometry",
    "PathGeometry",
    "PathSegment",
    "PcbDecimalAffine2d",
    "Point2d",
    "RegionGeometry",
    "RegionRing",
    "RoundedRectangleGeometry",
    "StrokeGeometry",
    "LayerSide",
    "LocatedSource",
    "LogicalPath",
    "BoardOccurrence",
    "ChildBoardRequest",
    "ChildBoardRequestDisposition",
    "ComponentOccurrence",
    "ComponentSide",
    "Diagnostic",
    "DrillSpan",
    "Feature",
    "HoleFeature",
    "LayerInstance",
    "ManufacturingDocument",
    "MaterialFeature",
    "MaterialFeatureKind",
    "PrecisionEnvelope",
    "ProfileFeature",
    "ProfileOperation",
    "SelectionProjection",
    "SourceNet",
    "StackRegion",
    "VariantSelection",
    "VariantSelectionKind",
    "MaterialPolarity",
    "MaterialRole",
    "RuntimeSource",
    "Sha256",
    "SourceProvenance",
    "StableRef",
    "StrictnessMode",
    "TopologyStatus",
    "UnresolvedSource",
    "WN_MODEL_OPS",
    "WN_SCHEMA_ROOTS",
)
