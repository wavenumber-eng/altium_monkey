import "@typespec/json-schema";
import "./common.tsp";

using TypeSpec.JsonSchema;

namespace Wavenumber.AltiumMonkey.SchCompiledDesign;

/** Structurally admitted value in a compiled-graph source identity map. */
union GraphSourceIdentityValue {
  stringValue: string,
  integerValue: SignedInt64,
  numberValue: float64,
  booleanValue: boolean,
  arrayValue: unknown[],
}

/** Non-empty structural identity evidence; semantic identity remains handwritten. */
@minProperties(1)
model GraphSourceIdentity is Record<GraphSourceIdentityValue>;

model GraphUnitDefinition {
  type: "sch.unit_definition";
  @minLength(1) id: string;
  display_name: string;
  page_definition_refs: string[];
  source_identity: GraphSourceIdentity;
}

model GraphPageDefinition {
  type: "sch.page_definition";
  @minLength(1) id: string;
  @minLength(1) unit_definition_ref: string;
  display_name: string;
  source_identity: GraphSourceIdentity;
}

model GraphUnitOccurrence {
  type: "sch.unit_occurrence";
  @minLength(1) id: string;
  @minLength(1) unit_definition_ref: string;
  display_name: string;
  page_occurrence_refs: string[];
  @minLength(1) parent_hierarchy_occurrence_ref?: string;
  source_identity: GraphSourceIdentity;
}

model GraphPageOccurrence {
  type: "sch.page_occurrence";
  @minLength(1) id: string;
  @minLength(1) page_definition_ref: string;
  @minLength(1) unit_occurrence_ref: string;
  display_name: string;
  sheet_number: string;
  instance_order: SignedInt64;
  source_identity: GraphSourceIdentity;
}

model GraphHierarchyOccurrence {
  type: "sch.hierarchy_occurrence";
  @minLength(1) id: string;
  @minLength(1) parent_unit_occurrence_ref: string;
  @minLength(1) parent_page_occurrence_ref: string;
  @minLength(1) child_unit_occurrence_ref: string;
  source_identity: GraphSourceIdentity;
}

model GraphComponentOccurrence {
  type: "sch.component_occurrence";
  @minLength(1) id: string;
  @minLength(1) page_occurrence_ref: string;
  source_designator: string;
  physical_designator: string;
  display_designator: string;
  unit: SignedInt64;
  body_style: SignedInt64;
  source_identity: GraphSourceIdentity;
}

model GraphLocalNetOccurrence {
  type: "sch.local_net_occurrence";
  @minLength(1) id: string;
  @minLength(1) page_occurrence_ref: string;
  display_name: string;
  qualified_name: string;
  aliases: string[];
  source_identity: GraphSourceIdentity;
}

model GraphTerminalOccurrence {
  type: "sch.terminal_occurrence";
  @minLength(1) id: string;
  @minLength(1) page_occurrence_ref: string;
  @minLength(1) local_net_occurrence_ref: string;
  @minLength(1) component_occurrence_ref?: string;
  design_component_pin_ref?: string;
  design_net_ref?: string;
  role: string;
  name: string;
  pin_designator: string;
  resolution_diagnostics: string[];
  source_identity: GraphSourceIdentity;
}

model GraphHierarchyTerminalBinding {
  type: "sch.hierarchy_terminal_binding";
  @minLength(1) id: string;
  @minLength(1) hierarchy_occurrence_ref: string;
  @minLength(1) parent_terminal_occurrence_ref: string;
  @minLength(1) child_terminal_occurrence_ref: string;
  design_net_ref?: string;
  source_identity: GraphSourceIdentity;
}

model GraphGraphicalArtifactLink {
  type: "sch.graphical_artifact_link";
  @minLength(1) id: string;
  @minLength(1) page_occurrence_ref: string;
  target_type: string;
  @minLength(1) target_ref: string;
  artifact_key: "sch.dwg_scene";
  @minLength(1) element_id: string;
  source_identity: GraphSourceIdentity;
}

/** Embedded compiled schematic graph body used by Design b0. */
model CompiledSchematicGraph {
  schema: "altium_monkey.compiled_schematic_graph.a0";
  type: "sch.compiled_schematic_graph";
  identity_namespace: "sch.compiled_schematic_graph.a0";
  unit_definitions: GraphUnitDefinition[];
  page_definitions: GraphPageDefinition[];
  unit_occurrences: GraphUnitOccurrence[];
  page_occurrences: GraphPageOccurrence[];
  hierarchy_occurrences: GraphHierarchyOccurrence[];
  component_occurrences: GraphComponentOccurrence[];
  local_net_occurrences: GraphLocalNetOccurrence[];
  terminal_occurrences: GraphTerminalOccurrence[];
  hierarchy_terminal_bindings: GraphHierarchyTerminalBinding[];
  graphical_artifact_links: GraphGraphicalArtifactLink[];
}

/** Standalone compiled schematic graph a0 transport. */
@jsonSchema
@id("urn:altium-monkey:schema:compiled_schematic_graph_a0")
@extension("title", "Altium Monkey compiled schematic graph a0")
model CompiledSchematicGraphA0 {
  ...CompiledSchematicGraph;
}
