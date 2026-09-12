import "@typespec/json-schema";
import "./common.tsp";

using TypeSpec.JsonSchema;

namespace Wavenumber.AltiumMonkey.SchCompiledDesign;

model NetlistComponent {
  designator: string;
  value: string;
  footprint: string;
  library_ref: string;
  description: string;
  parameters: ParameterMap;
}

model NetlistTerminal {
  designator: string;
  pin: string;
  pin_name: string;
  pin_type: string;
}

model ConnectionPoint {
  x: SignedInt64;
  y: SignedInt64;
  units: "altium_coord";
}

model NetlistEndpoint {
  endpoint_id: string;
  role: string;
  element_id: string;
  object_id: string;
  name: string;
  source_sheet: string;
  designator?: string;
  pin?: string;
  pin_name?: string;
  pin_type?: string;
  sheet_index?: SignedInt64;
  compiled_sheet_index?: SignedInt64;
  connection_point?: ConnectionPoint;
}

model GraphicalPin {
  designator: string;
  pin: string;
  svg_id: string;
}

model NetlistGraphical {
  wires: string[];
  junctions: string[];
  labels: string[];
  power_ports: string[];
  ports: string[];
  sheet_entries: string[];
  pins: GraphicalPin[];
}

model NetlistHierarchyLevel {
  sheet_symbol_uid: string;
  child_filename: string;
  designator?: string;
  channel_name?: string;
  @minValue(0) channel_index?: SignedInt64;
  repeat_value?: SignedInt64;
}

model NetlistNet {
  uid: string;
  name: string;
  auto_named: boolean;
  source_sheets: string[];
  terminals: NetlistTerminal[];
  graphical: NetlistGraphical;
  aliases: string[];
  endpoints?: NetlistEndpoint[];
  hierarchy_paths?: NetlistHierarchyLevel[][];
}

/** Generic netlist a0 transport. */
@jsonSchema
@id("urn:altium-monkey:schema:netlist_a0")
@extension("title", "Altium Monkey netlist a0")
model NetlistA0 {
  schema: "altium_monkey.netlist.a0";
  generator: "altium_monkey";
  components: NetlistComponent[];
  nets: NetlistNet[];
}
