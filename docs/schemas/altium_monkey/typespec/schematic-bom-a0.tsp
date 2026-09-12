import "@typespec/json-schema";
import "./common.tsp";

using TypeSpec.JsonSchema;

namespace Wavenumber.AltiumMonkey.SchCompiledDesign;

model SchematicBomA0Component {
  component_id: SchematicIdentityString;
  designator: SchematicIdentityString;
  logical_designator: NullableString;
  physical_designator: NullableString;
  source_page: SchematicSourcePage;
  value: string;
  footprint: string;
  library_ref: string;
  description: string;
  parameters: SchematicStringParameterMap;
  fitted: boolean;
  dnp: boolean;
}

/** Versioned schematic BOM transport. */
@jsonSchema
@id("urn:altium-monkey:schema:schematic_bom_a0")
@extension("title", "Altium Monkey schematic BOM a0")
model SchematicBomA0 {
  schema: "altium_monkey.schematic_bom.a0";
  generator: "altium_monkey";
  selected_variant: NullableString;
  components: SchematicBomA0Component[];
}
