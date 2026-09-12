import "@typespec/json-schema";
import "./common.tsp";
import "./compiled-graph-a0.tsp";
import "./hierarchy-a1.tsp";

using TypeSpec.JsonSchema;

namespace Wavenumber.AltiumMonkey.SchCompiledDesign;

model DesignSheet {
  filename: string;
  /** Canonical decimal integer or exact non-canonical Altium parameter value. */
  sheet_number: SheetNumber;
}

model VariantKeyValues is Record<string>;

model VariantParameterOverrides is Record<ParameterMap>;

model DesignVariant {
  name: string;
  dnp: string[];
  variations?: VariantKeyValues[];
  parameters?: VariantKeyValues[];
  param_variations?: VariantKeyValues[];
  parameter_overrides?: VariantParameterOverrides;
  is_current?: boolean;
}

model DesignProject {
  name: NullableString;
  filename: NullableString;
  parameters: ParameterMap;
  current_variant?: NullableString;
}

model DesignComponent {
  designator: string;
  svg_id?: NullableString;
  value?: string;
  footprint?: string;
  library_ref?: string;
  description?: string;
  hierarchy?: JsonObject;
  classification?: JsonObject;
  parameters?: ParameterMap;
  compiled_component_id?: string;
  physical_document_id?: string;
  physical_page_id?: string;
  source_unique_id?: NullableString;
  source_unique_id_path?: NullableString;
  dnp?: boolean;
  fitted?: boolean;
  ambiguous_physical_designator?: boolean;
  ...Record<unknown>;
}

model DesignOptions {
  net_identifier_scope: string;
  allow_ports_to_name_nets: boolean;
  allow_sheet_entries_to_name_nets: boolean;
  allow_single_pin_nets: boolean;
  append_sheet_numbers_to_local_nets: boolean;
  power_port_names_take_priority: boolean;
  higher_level_names_take_priority: boolean;
  auto_sheet_numbering: boolean;
  ...Record<unknown>;
}

model DesignPnpPlacement {
  designator: string;
  ...Record<unknown>;
}

model DesignPnp {
  units: "mm";
  position_mode: PositionMode;
  source_pcbdoc: string;
  placements: DesignPnpPlacement[];
}

model DesignCompile {
  schema?: string;
  summary?: JsonObject;
  options?: JsonObject;
  annotation?: JsonObject;
  stats?: JsonObject;
  ...Record<unknown>;
}

model DesignTerminal {
  designator: string;
  pin: string;
  pin_name?: string;
  pin_type?: string;
  ...Record<unknown>;
}

model DesignNameSource {
  name: string;
  role: string;
  source: string;
  ...Record<unknown>;
}

model DesignNet {
  uid: string;
  name: string;
  auto_named: boolean;
  source_sheets: string[];
  terminals: DesignTerminal[];
  graphical: JsonObject;
  aliases: string[];
  endpoints?: JsonObject[];
  hierarchy_paths?: JsonObject[];
  name_sources?: DesignNameSource[];
  ...Record<unknown>;
}

model DesignIndexes {
  svg_to_component?: StringMap;
  svg_to_components?: StringListMap;
  component_to_nets?: StringListMap;
  net_to_components?: StringListMap;
}

model PhysicalPageMetadata {
  @minLength(1) page_occurrence_ref: string;
  physical_instance_path: string;
  channel_index: SignedInt64;
  channel_prefix: string;
  channel_alpha: string;
  room_name: string;
  physical_room_name: string;
  document_number: string;
}

/** Required downstream Altium Design b0 payload. */
@jsonSchema
@id("urn:altium-monkey:schema:design_b0")
@extension("title", "Altium Monkey design b0")
model DesignB0 {
  schema: "altium_monkey.design.b0";
  generator: "altium_monkey";
  project: DesignProject;
  variants: DesignVariant[];
  options: DesignOptions;
  sheets: DesignSheet[];
  components: DesignComponent[];
  schematic_hierarchy: SchematicHierarchy;
  pnp?: DesignPnp;
  compile?: DesignCompile;
  diagnostics?: JsonObject[];
  nets: DesignNet[];
  indexes?: DesignIndexes;
  compiled_schematic_graph: CompiledSchematicGraph;
  physical_page_metadata: PhysicalPageMetadata[];
}
