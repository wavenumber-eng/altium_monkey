import "@typespec/json-schema";
import "./common.tsp";

using TypeSpec.JsonSchema;

namespace Wavenumber.AltiumMonkey.SchCompiledDesign;

/** Closed electrical pin-type inventory retained by the rich model. */
union NetlistB0PinType {
  input: "INPUT",
  io: "IO",
  output: "OUTPUT",
  openCollector: "OPEN_COLLECTOR",
  passive: "PASSIVE",
  tristate: "TRISTATE",
  openEmitter: "OPEN_EMITTER",
  power: "POWER",
  unknownPin: "UNKNOWN",
}

/** Required endpoint pin type whose value may be JSON null. */
union NetlistB0NullablePinType {
  value: NetlistB0PinType,
  nullValue: null,
}

@minValue(0)
scalar NetlistB0NonNegativeInt64 extends SignedInt64;

/** Required non-negative channel index whose value may be JSON null. */
union NetlistB0NullableChannelIndex {
  value: NetlistB0NonNegativeInt64,
  nullValue: null,
}

/** Required source page whose value may be JSON null. */
union NetlistB0NullableSourcePage {
  value: SchematicSourcePage,
  nullValue: null,
}

model NetlistB0Component {
  component_id: SchematicIdentityString;
  designator: SchematicIdentityString;
  logical_designator: NullableString;
  physical_designator: NullableString;
  source_page: NetlistB0NullableSourcePage;
  value: string;
  footprint: string;
  library_ref: string;
  description: string;
  parameters: SchematicStringParameterMap;
}

model NetlistB0Terminal {
  component_id: SchematicIdentityString;
  designator: SchematicIdentityString;
  pin: SchematicIdentityString;
  pin_name: string;
  pin_type: NetlistB0PinType;
}

model NetlistB0ConnectionPoint {
  x: SignedInt64;
  y: SignedInt64;
  units: "altium_coord";
}

/** Required connection point whose value may be JSON null. */
union NetlistB0NullableConnectionPoint {
  value: NetlistB0ConnectionPoint,
  nullValue: null,
}

model NetlistB0Endpoint {
  endpoint_id: SchematicIdentityString;
  role: SchematicIdentityString;
  element_id: string;
  object_id: string;
  name: string;
  source_page: NetlistB0NullableSourcePage;
  component_id: NullableString;
  designator: NullableString;
  pin: NullableString;
  pin_name: NullableString;
  pin_type: NetlistB0NullablePinType;
  connection_point: NetlistB0NullableConnectionPoint;
}

model NetlistB0GraphicalPin {
  component_id: SchematicIdentityString;
  designator: SchematicIdentityString;
  pin: SchematicIdentityString;
  svg_id: SchematicIdentityString;
}

model NetlistB0Graphical {
  wires: string[];
  junctions: string[];
  labels: string[];
  power_ports: string[];
  ports: string[];
  sheet_entries: string[];
  pins: NetlistB0GraphicalPin[];
}

model NetlistB0HierarchyLevel {
  sheet_symbol_uid: SchematicIdentityString;
  child_filename: SchematicIdentityString;
  designator: string;
  channel_name: string;
  channel_index: NetlistB0NullableChannelIndex;
  repeat_value: NullableInt64;
}

model NetlistB0Net {
  uid: SchematicIdentityString;
  name: string;
  auto_named: boolean;
  source_pages: SchematicSourcePage[];
  terminals: NetlistB0Terminal[];
  graphical: NetlistB0Graphical;
  aliases: string[];
  endpoints: NetlistB0Endpoint[];
  hierarchy_paths: NetlistB0HierarchyLevel[][];
}

/** Current semantic netlist transport. */
@jsonSchema
@id("urn:altium-monkey:schema:netlist_b0")
@extension("title", "Altium Monkey netlist b0")
model NetlistB0 {
  schema: "altium_monkey.netlist.b0";
  generator: "altium_monkey";
  components: NetlistB0Component[];
  nets: NetlistB0Net[];
}
