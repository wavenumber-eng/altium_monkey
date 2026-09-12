"""Generated strict msgspec bindings. Do not edit."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from msgspec import UNSET, Meta, Struct, UnsetType, field

type GraphSourceIdentity = Annotated[
    dict[str, GraphSourceIdentityValue],
    Meta(
        description="Non-empty structural identity evidence; semantic identity remains handwritten.",
        min_length=1,
    ),
]

type GraphSourceIdentityValue = Annotated[
    str | SignedInt64 | float | bool | list[object],
    Meta(
        description="Structurally admitted value in a compiled-graph source identity map."
    ),
]

type HierarchyLevel = dict[str, object]

type JsonObject = Annotated[
    dict[str, object],
    Meta(
        description="Arbitrary JSON object retained at an explicitly open compatibility seam."
    ),
]

type NetlistB0NonNegativeInt64 = Annotated[
    int, Meta(ge=0, extra={"x-altium-monkey-signed-integer-width": 64})
]

type NetlistB0NullableChannelIndex = Annotated[
    NetlistB0NonNegativeInt64 | Literal[None],
    Meta(
        description="Required non-negative channel index whose value may be JSON null."
    ),
]

type NetlistB0NullableConnectionPoint = Annotated[
    NetlistB0ConnectionPoint | Literal[None],
    Meta(description="Required connection point whose value may be JSON null."),
]

type NetlistB0NullablePinType = Annotated[
    NetlistB0PinType | Literal[None],
    Meta(description="Required endpoint pin type whose value may be JSON null."),
]

type NetlistB0NullableSourcePage = Annotated[
    SchematicSourcePage | Literal[None],
    Meta(description="Required source page whose value may be JSON null."),
]

type NetlistB0PinType = Annotated[
    Literal[
        "INPUT",
        "IO",
        "OUTPUT",
        "OPEN_COLLECTOR",
        "PASSIVE",
        "TRISTATE",
        "OPEN_EMITTER",
        "POWER",
        "UNKNOWN",
    ],
    Meta(
        description="Closed electrical pin-type inventory retained by the rich model."
    ),
]

type NullableInt64 = Annotated[
    SignedInt64 | Literal[None],
    Meta(
        description="Required field whose value may be a signed integer or JSON null."
    ),
]

type NullableString = Annotated[
    str | Literal[None],
    Meta(description="Required field whose value may be a string or JSON null."),
]

type ParameterMap = Annotated[
    dict[str, object],
    Meta(
        description="Arbitrary JSON parameter map retained in insertion-insensitive key order."
    ),
]

type PositionMode = Annotated[
    Literal["altium-pick-place", "component-origin"],
    Meta(description="Pick-and-place position convention."),
]

type SchematicIdentityString = Annotated[
    str,
    Meta(
        description="Non-empty identity used by current schematic transport contracts.",
        min_length=1,
    ),
]

type SchematicStringParameterMap = Annotated[
    dict[str, str],
    Meta(
        description="String-only component parameters for current schematic transports."
    ),
]

type SheetNumber = Annotated[
    SignedInt64 | str,
    Meta(
        description="Compatibility sheet number: canonical decimal integer or exact source string."
    ),
]

type SignedInt64 = Annotated[
    int,
    Meta(
        description="Signed 64-bit JSON integer shared by schema and Rust boundaries.",
        extra={"x-altium-monkey-signed-integer-width": 64},
    ),
]

type StringListMap = Annotated[
    dict[str, list[str]], Meta(description="String-list-valued map.")
]

type StringMap = Annotated[dict[str, str], Meta(description="String-valued map.")]

type VariantKeyValues = dict[str, str]

type VariantParameterOverrides = dict[str, ParameterMap]


class CompiledSchematicGraph(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    """Embedded compiled schematic graph body used by Design b0."""

    schema: Literal["altium_monkey.compiled_schematic_graph.a0"]
    type: Literal["sch.compiled_schematic_graph"]
    identity_namespace: Literal["sch.compiled_schematic_graph.a0"]
    unit_definitions: list[GraphUnitDefinition]
    page_definitions: list[GraphPageDefinition]
    unit_occurrences: list[GraphUnitOccurrence]
    page_occurrences: list[GraphPageOccurrence]
    hierarchy_occurrences: list[GraphHierarchyOccurrence]
    component_occurrences: list[GraphComponentOccurrence]
    local_net_occurrences: list[GraphLocalNetOccurrence]
    terminal_occurrences: list[GraphTerminalOccurrence]
    hierarchy_terminal_bindings: list[GraphHierarchyTerminalBinding]
    graphical_artifact_links: list[GraphGraphicalArtifactLink]


class CompiledSchematicGraphA0(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    """Standalone compiled schematic graph a0 transport."""

    __wn_annotations__: ClassVar[dict[str, object]] = {
        "title": "Altium Monkey compiled schematic graph a0"
    }
    __wn_schema_id__: ClassVar[str] = (
        "urn:altium-monkey:schema:compiled_schematic_graph_a0"
    )
    schema: Literal["altium_monkey.compiled_schematic_graph.a0"]
    type: Literal["sch.compiled_schematic_graph"]
    identity_namespace: Literal["sch.compiled_schematic_graph.a0"]
    unit_definitions: list[GraphUnitDefinition]
    page_definitions: list[GraphPageDefinition]
    unit_occurrences: list[GraphUnitOccurrence]
    page_occurrences: list[GraphPageOccurrence]
    hierarchy_occurrences: list[GraphHierarchyOccurrence]
    component_occurrences: list[GraphComponentOccurrence]
    local_net_occurrences: list[GraphLocalNetOccurrence]
    terminal_occurrences: list[GraphTerminalOccurrence]
    hierarchy_terminal_bindings: list[GraphHierarchyTerminalBinding]
    graphical_artifact_links: list[GraphGraphicalArtifactLink]


class ConnectionPoint(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    x: SignedInt64
    y: SignedInt64
    units: Literal["altium_coord"]


class DesignB0(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Required downstream Altium Design b0 payload."""

    __wn_annotations__: ClassVar[dict[str, object]] = {
        "title": "Altium Monkey design b0"
    }
    __wn_schema_id__: ClassVar[str] = "urn:altium-monkey:schema:design_b0"
    schema: Literal["altium_monkey.design.b0"]
    generator: Literal["altium_monkey"]
    project: DesignProject
    variants: list[DesignVariant]
    options: DesignOptions
    sheets: list[DesignSheet]
    components: list[DesignComponent]
    schematic_hierarchy: SchematicHierarchy
    pnp: DesignPnp | UnsetType = UNSET
    compile: DesignCompile | UnsetType = UNSET
    diagnostics: list[JsonObject] | UnsetType = UNSET
    nets: list[DesignNet]
    indexes: DesignIndexes | UnsetType = UNSET
    compiled_schematic_graph: CompiledSchematicGraph
    physical_page_metadata: list[PhysicalPageMetadata]


class DesignCompile(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    schema: str | UnsetType = UNSET
    summary: JsonObject | UnsetType = UNSET
    options: JsonObject | UnsetType = UNSET
    annotation: JsonObject | UnsetType = UNSET
    stats: JsonObject | UnsetType = UNSET
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class DesignComponent(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    designator: str
    svg_id: NullableString | UnsetType = UNSET
    value: str | UnsetType = UNSET
    footprint: str | UnsetType = UNSET
    library_ref: str | UnsetType = UNSET
    description: str | UnsetType = UNSET
    hierarchy: JsonObject | UnsetType = UNSET
    classification: JsonObject | UnsetType = UNSET
    parameters: ParameterMap | UnsetType = UNSET
    compiled_component_id: str | UnsetType = UNSET
    physical_document_id: str | UnsetType = UNSET
    physical_page_id: str | UnsetType = UNSET
    source_unique_id: NullableString | UnsetType = UNSET
    source_unique_id_path: NullableString | UnsetType = UNSET
    dnp: bool | UnsetType = UNSET
    fitted: bool | UnsetType = UNSET
    ambiguous_physical_designator: bool | UnsetType = UNSET
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class DesignIndexes(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    svg_to_component: StringMap | UnsetType = UNSET
    svg_to_components: StringListMap | UnsetType = UNSET
    component_to_nets: StringListMap | UnsetType = UNSET
    net_to_components: StringListMap | UnsetType = UNSET


class DesignNameSource(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    name: str
    role: str
    source: str
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class DesignNet(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    uid: str
    name: str
    auto_named: bool
    source_sheets: list[str]
    terminals: list[DesignTerminal]
    graphical: JsonObject
    aliases: list[str]
    endpoints: list[JsonObject] | UnsetType = UNSET
    hierarchy_paths: list[JsonObject] | UnsetType = UNSET
    name_sources: list[DesignNameSource] | UnsetType = UNSET
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class DesignOptions(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    net_identifier_scope: str
    allow_ports_to_name_nets: bool
    allow_sheet_entries_to_name_nets: bool
    allow_single_pin_nets: bool
    append_sheet_numbers_to_local_nets: bool
    power_port_names_take_priority: bool
    higher_level_names_take_priority: bool
    auto_sheet_numbering: bool
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class DesignPnp(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    units: Literal["mm"]
    position_mode: PositionMode
    source_pcbdoc: str
    placements: list[DesignPnpPlacement]


class DesignPnpPlacement(
    Struct, forbid_unknown_fields=False, frozen=True, kw_only=True
):
    designator: str
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class DesignProject(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    name: NullableString
    filename: NullableString
    parameters: ParameterMap
    current_variant: NullableString | UnsetType = UNSET


class DesignSheet(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    filename: str
    sheet_number: Annotated[
        SheetNumber,
        Meta(
            description="Canonical decimal integer or exact non-canonical Altium parameter value."
        ),
    ]


class DesignTerminal(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    designator: str
    pin: str
    pin_name: str | UnsetType = UNSET
    pin_type: str | UnsetType = UNSET
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class DesignVariant(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    name: str
    dnp: list[str]
    variations: list[VariantKeyValues] | UnsetType = UNSET
    parameters: list[VariantKeyValues] | UnsetType = UNSET
    param_variations: list[VariantKeyValues] | UnsetType = UNSET
    parameter_overrides: VariantParameterOverrides | UnsetType = UNSET
    is_current: bool | UnsetType = UNSET


class GraphComponentOccurrence(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.component_occurrence"]
    id: Annotated[str, Meta(min_length=1)]
    page_occurrence_ref: Annotated[str, Meta(min_length=1)]
    source_designator: str
    physical_designator: str
    display_designator: str
    unit: SignedInt64
    body_style: SignedInt64
    source_identity: GraphSourceIdentity


class GraphGraphicalArtifactLink(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.graphical_artifact_link"]
    id: Annotated[str, Meta(min_length=1)]
    page_occurrence_ref: Annotated[str, Meta(min_length=1)]
    target_type: str
    target_ref: Annotated[str, Meta(min_length=1)]
    artifact_key: Literal["sch.dwg_scene"]
    element_id: Annotated[str, Meta(min_length=1)]
    source_identity: GraphSourceIdentity


class GraphHierarchyOccurrence(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.hierarchy_occurrence"]
    id: Annotated[str, Meta(min_length=1)]
    parent_unit_occurrence_ref: Annotated[str, Meta(min_length=1)]
    parent_page_occurrence_ref: Annotated[str, Meta(min_length=1)]
    child_unit_occurrence_ref: Annotated[str, Meta(min_length=1)]
    source_identity: GraphSourceIdentity


class GraphHierarchyTerminalBinding(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.hierarchy_terminal_binding"]
    id: Annotated[str, Meta(min_length=1)]
    hierarchy_occurrence_ref: Annotated[str, Meta(min_length=1)]
    parent_terminal_occurrence_ref: Annotated[str, Meta(min_length=1)]
    child_terminal_occurrence_ref: Annotated[str, Meta(min_length=1)]
    design_net_ref: str | UnsetType = UNSET
    source_identity: GraphSourceIdentity


class GraphicalPin(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    designator: str
    pin: str
    svg_id: str


class GraphLocalNetOccurrence(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.local_net_occurrence"]
    id: Annotated[str, Meta(min_length=1)]
    page_occurrence_ref: Annotated[str, Meta(min_length=1)]
    display_name: str
    qualified_name: str
    aliases: list[str]
    source_identity: GraphSourceIdentity


class GraphPageDefinition(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.page_definition"]
    id: Annotated[str, Meta(min_length=1)]
    unit_definition_ref: Annotated[str, Meta(min_length=1)]
    display_name: str
    source_identity: GraphSourceIdentity


class GraphPageOccurrence(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.page_occurrence"]
    id: Annotated[str, Meta(min_length=1)]
    page_definition_ref: Annotated[str, Meta(min_length=1)]
    unit_occurrence_ref: Annotated[str, Meta(min_length=1)]
    display_name: str
    sheet_number: str
    instance_order: SignedInt64
    source_identity: GraphSourceIdentity


class GraphTerminalOccurrence(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.terminal_occurrence"]
    id: Annotated[str, Meta(min_length=1)]
    page_occurrence_ref: Annotated[str, Meta(min_length=1)]
    local_net_occurrence_ref: Annotated[str, Meta(min_length=1)]
    component_occurrence_ref: Annotated[str, Meta(min_length=1)] | UnsetType = UNSET
    design_component_pin_ref: str | UnsetType = UNSET
    design_net_ref: str | UnsetType = UNSET
    role: str
    name: str
    pin_designator: str
    resolution_diagnostics: list[str]
    source_identity: GraphSourceIdentity


class GraphUnitDefinition(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.unit_definition"]
    id: Annotated[str, Meta(min_length=1)]
    display_name: str
    page_definition_refs: list[str]
    source_identity: GraphSourceIdentity


class GraphUnitOccurrence(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    type: Literal["sch.unit_occurrence"]
    id: Annotated[str, Meta(min_length=1)]
    unit_definition_ref: Annotated[str, Meta(min_length=1)]
    display_name: str
    page_occurrence_refs: list[str]
    parent_hierarchy_occurrence_ref: Annotated[str, Meta(min_length=1)] | UnsetType = (
        UNSET
    )
    source_identity: GraphSourceIdentity


class HarnessBundleLink(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    id: str
    kind: Literal["harness_bundle"]
    topology: str
    name: str
    parent: JsonObject
    child: JsonObject
    bundle: JsonObject
    match_kind: str
    metadata: JsonObject
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class HierarchyChannel(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    id: str
    sheet_symbol_id: str
    parent_sheet_index: SignedInt64
    child_sheet_index: SignedInt64
    compiled_child_sheet_index: NullableInt64 | UnsetType = UNSET
    instance_index: SignedInt64
    channel_name: str | UnsetType = UNSET
    hierarchy_path_id: str
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class HierarchyDocument(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    sheet_index: SignedInt64
    filename: str
    path: str
    is_top_level: bool
    metadata: JsonObject
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class HierarchyLink(Struct, forbid_unknown_fields=False, frozen=True, kw_only=True):
    id: str
    kind: str
    parent: JsonObject
    child: JsonObject
    match_kind: str
    hierarchy_path_id: str
    metadata: JsonObject
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class HierarchyPathRecord(
    Struct, forbid_unknown_fields=False, frozen=True, kw_only=True
):
    id: str
    source_sheet_index: NullableInt64 | UnsetType = UNSET
    compiled_sheet_index: NullableInt64 | UnsetType = UNSET
    levels: list[HierarchyLevel]
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class HierarchySheetSymbol(
    Struct, forbid_unknown_fields=False, frozen=True, kw_only=True
):
    id: str
    parent_sheet_index: SignedInt64
    designator: str
    child_filename: str
    child_sheet_indices: list[SignedInt64]
    repeat: JsonObject | UnsetType = UNSET
    entries: list[JsonObject]
    metadata: JsonObject
    # Handwritten adapters flatten this collision-checked extension map on the wire.
    additional_properties: dict[str, object] = field(default_factory=dict)


class NetlistA0(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Generic netlist a0 transport."""

    __wn_annotations__: ClassVar[dict[str, object]] = {
        "title": "Altium Monkey netlist a0"
    }
    __wn_schema_id__: ClassVar[str] = "urn:altium-monkey:schema:netlist_a0"
    schema: Literal["altium_monkey.netlist.a0"]
    generator: Literal["altium_monkey"]
    components: list[NetlistComponent]
    nets: list[NetlistNet]


class NetlistB0(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Current semantic netlist transport."""

    __wn_annotations__: ClassVar[dict[str, object]] = {
        "title": "Altium Monkey netlist b0"
    }
    __wn_schema_id__: ClassVar[str] = "urn:altium-monkey:schema:netlist_b0"
    schema: Literal["altium_monkey.netlist.b0"]
    generator: Literal["altium_monkey"]
    components: list[NetlistB0Component]
    nets: list[NetlistB0Net]


class NetlistB0Component(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    component_id: SchematicIdentityString
    designator: SchematicIdentityString
    logical_designator: NullableString
    physical_designator: NullableString
    source_page: NetlistB0NullableSourcePage
    value: str
    footprint: str
    library_ref: str
    description: str
    parameters: SchematicStringParameterMap


class NetlistB0ConnectionPoint(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    x: SignedInt64
    y: SignedInt64
    units: Literal["altium_coord"]


class NetlistB0Endpoint(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    endpoint_id: SchematicIdentityString
    role: SchematicIdentityString
    element_id: str
    object_id: str
    name: str
    source_page: NetlistB0NullableSourcePage
    component_id: NullableString
    designator: NullableString
    pin: NullableString
    pin_name: NullableString
    pin_type: NetlistB0NullablePinType
    connection_point: NetlistB0NullableConnectionPoint


class NetlistB0Graphical(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    wires: list[str]
    junctions: list[str]
    labels: list[str]
    power_ports: list[str]
    ports: list[str]
    sheet_entries: list[str]
    pins: list[NetlistB0GraphicalPin]


class NetlistB0GraphicalPin(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    component_id: SchematicIdentityString
    designator: SchematicIdentityString
    pin: SchematicIdentityString
    svg_id: SchematicIdentityString


class NetlistB0HierarchyLevel(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    sheet_symbol_uid: SchematicIdentityString
    child_filename: SchematicIdentityString
    designator: str
    channel_name: str
    channel_index: NetlistB0NullableChannelIndex
    repeat_value: NullableInt64


class NetlistB0Net(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    uid: SchematicIdentityString
    name: str
    auto_named: bool
    source_pages: list[SchematicSourcePage]
    terminals: list[NetlistB0Terminal]
    graphical: NetlistB0Graphical
    aliases: list[str]
    endpoints: list[NetlistB0Endpoint]
    hierarchy_paths: list[list[NetlistB0HierarchyLevel]]


class NetlistB0Terminal(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    component_id: SchematicIdentityString
    designator: SchematicIdentityString
    pin: SchematicIdentityString
    pin_name: str
    pin_type: NetlistB0PinType


class NetlistComponent(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    designator: str
    value: str
    footprint: str
    library_ref: str
    description: str
    parameters: ParameterMap


class NetlistEndpoint(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    endpoint_id: str
    role: str
    element_id: str
    object_id: str
    name: str
    source_sheet: str
    designator: str | UnsetType = UNSET
    pin: str | UnsetType = UNSET
    pin_name: str | UnsetType = UNSET
    pin_type: str | UnsetType = UNSET
    sheet_index: SignedInt64 | UnsetType = UNSET
    compiled_sheet_index: SignedInt64 | UnsetType = UNSET
    connection_point: ConnectionPoint | UnsetType = UNSET


class NetlistGraphical(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    wires: list[str]
    junctions: list[str]
    labels: list[str]
    power_ports: list[str]
    ports: list[str]
    sheet_entries: list[str]
    pins: list[GraphicalPin]


class NetlistHierarchyLevel(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    sheet_symbol_uid: str
    child_filename: str
    designator: str | UnsetType = UNSET
    channel_name: str | UnsetType = UNSET
    channel_index: Annotated[SignedInt64, Meta(ge=0)] | UnsetType = UNSET
    repeat_value: SignedInt64 | UnsetType = UNSET


class NetlistNet(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    uid: str
    name: str
    auto_named: bool
    source_sheets: list[str]
    terminals: list[NetlistTerminal]
    graphical: NetlistGraphical
    aliases: list[str]
    endpoints: list[NetlistEndpoint] | UnsetType = UNSET
    hierarchy_paths: list[list[NetlistHierarchyLevel]] | UnsetType = UNSET


class NetlistTerminal(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    designator: str
    pin: str
    pin_name: str
    pin_type: str


class PhysicalPageMetadata(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    page_occurrence_ref: Annotated[str, Meta(min_length=1)]
    physical_instance_path: str
    channel_index: SignedInt64
    channel_prefix: str
    channel_alpha: str
    room_name: str
    physical_room_name: str
    document_number: str


class SchematicBomA0(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Versioned schematic BOM transport."""

    __wn_annotations__: ClassVar[dict[str, object]] = {
        "title": "Altium Monkey schematic BOM a0"
    }
    __wn_schema_id__: ClassVar[str] = "urn:altium-monkey:schema:schematic_bom_a0"
    schema: Literal["altium_monkey.schematic_bom.a0"]
    generator: Literal["altium_monkey"]
    selected_variant: NullableString
    components: list[SchematicBomA0Component]


class SchematicBomA0Component(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    component_id: SchematicIdentityString
    designator: SchematicIdentityString
    logical_designator: NullableString
    physical_designator: NullableString
    source_page: SchematicSourcePage
    value: str
    footprint: str
    library_ref: str
    description: str
    parameters: SchematicStringParameterMap
    fitted: bool
    dnp: bool


class SchematicHierarchy(Struct, forbid_unknown_fields=True, frozen=True, kw_only=True):
    """Embedded compatibility hierarchy body used by Design b0."""

    schema: Literal["altium_monkey.schematic_hierarchy.a1"]
    requested_scope: str
    effective_scope: str
    documents: list[HierarchyDocument]
    sheet_symbols: list[HierarchySheetSymbol]
    hierarchy_paths: list[HierarchyPathRecord]
    channels: list[HierarchyChannel]
    links: list[HierarchyLink]
    harness_bundle_links: list[HarnessBundleLink] | UnsetType = UNSET
    unresolved: list[JsonObject]


class SchematicHierarchyA1(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    """Standalone schematic hierarchy a1 transport."""

    __wn_annotations__: ClassVar[dict[str, object]] = {
        "title": "Altium Monkey schematic hierarchy a1"
    }
    __wn_schema_id__: ClassVar[str] = "urn:altium-monkey:schema:schematic_hierarchy_a1"
    schema: Literal["altium_monkey.schematic_hierarchy.a1"]
    requested_scope: str
    effective_scope: str
    documents: list[HierarchyDocument]
    sheet_symbols: list[HierarchySheetSymbol]
    hierarchy_paths: list[HierarchyPathRecord]
    channels: list[HierarchyChannel]
    links: list[HierarchyLink]
    harness_bundle_links: list[HarnessBundleLink] | UnsetType = UNSET
    unresolved: list[JsonObject]


class SchematicSourcePage(
    Struct, forbid_unknown_fields=True, frozen=True, kw_only=True
):
    """Correlated physical-page and logical-source identity."""

    physical_document_id: NullableString
    source_sheet_file: NullableString


WN_MODEL_OPS: dict[str, tuple[object, ...]] = {}

WN_SCHEMA_ROOTS: dict[object, str] = {
    CompiledSchematicGraphA0: "urn:altium-monkey:schema:compiled_schematic_graph_a0",
    DesignB0: "urn:altium-monkey:schema:design_b0",
    NetlistA0: "urn:altium-monkey:schema:netlist_a0",
    NetlistB0: "urn:altium-monkey:schema:netlist_b0",
    SchematicBomA0: "urn:altium-monkey:schema:schematic_bom_a0",
    SchematicHierarchyA1: "urn:altium-monkey:schema:schematic_hierarchy_a1",
}

__all__ = (
    "CompiledSchematicGraph",
    "CompiledSchematicGraphA0",
    "ConnectionPoint",
    "DesignB0",
    "DesignCompile",
    "DesignComponent",
    "DesignIndexes",
    "DesignNameSource",
    "DesignNet",
    "DesignOptions",
    "DesignPnp",
    "DesignPnpPlacement",
    "DesignProject",
    "DesignSheet",
    "DesignTerminal",
    "DesignVariant",
    "GraphComponentOccurrence",
    "GraphGraphicalArtifactLink",
    "GraphHierarchyOccurrence",
    "GraphHierarchyTerminalBinding",
    "GraphicalPin",
    "GraphLocalNetOccurrence",
    "GraphPageDefinition",
    "GraphPageOccurrence",
    "GraphSourceIdentity",
    "GraphSourceIdentityValue",
    "GraphTerminalOccurrence",
    "GraphUnitDefinition",
    "GraphUnitOccurrence",
    "HarnessBundleLink",
    "HierarchyChannel",
    "HierarchyDocument",
    "HierarchyLevel",
    "HierarchyLink",
    "HierarchyPathRecord",
    "HierarchySheetSymbol",
    "JsonObject",
    "NetlistA0",
    "NetlistB0",
    "NetlistB0Component",
    "NetlistB0ConnectionPoint",
    "NetlistB0Endpoint",
    "NetlistB0Graphical",
    "NetlistB0GraphicalPin",
    "NetlistB0HierarchyLevel",
    "NetlistB0Net",
    "NetlistB0NonNegativeInt64",
    "NetlistB0NullableChannelIndex",
    "NetlistB0NullableConnectionPoint",
    "NetlistB0NullablePinType",
    "NetlistB0NullableSourcePage",
    "NetlistB0PinType",
    "NetlistB0Terminal",
    "NetlistComponent",
    "NetlistEndpoint",
    "NetlistGraphical",
    "NetlistHierarchyLevel",
    "NetlistNet",
    "NetlistTerminal",
    "NullableInt64",
    "NullableString",
    "ParameterMap",
    "PhysicalPageMetadata",
    "PositionMode",
    "SchematicBomA0",
    "SchematicBomA0Component",
    "SchematicHierarchy",
    "SchematicHierarchyA1",
    "SchematicIdentityString",
    "SchematicSourcePage",
    "SchematicStringParameterMap",
    "SheetNumber",
    "SignedInt64",
    "StringListMap",
    "StringMap",
    "VariantKeyValues",
    "VariantParameterOverrides",
    "WN_MODEL_OPS",
    "WN_SCHEMA_ROOTS",
)
