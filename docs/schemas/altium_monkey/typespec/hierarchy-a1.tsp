import "@typespec/json-schema";
import "./common.tsp";

using TypeSpec.JsonSchema;

namespace Wavenumber.AltiumMonkey.SchCompiledDesign;

model HierarchyDocument {
  sheet_index: SignedInt64;
  filename: string;
  path: string;
  is_top_level: boolean;
  metadata: JsonObject;
  ...Record<unknown>;
}

model HierarchySheetSymbol {
  id: string;
  parent_sheet_index: SignedInt64;
  designator: string;
  child_filename: string;
  child_sheet_indices: SignedInt64[];
  repeat?: JsonObject;
  entries: JsonObject[];
  metadata: JsonObject;
  ...Record<unknown>;
}

model HierarchyLevel {
  ...Record<unknown>;
}

model HierarchyPathRecord {
  id: string;
  source_sheet_index?: NullableInt64;
  compiled_sheet_index?: NullableInt64;
  levels: HierarchyLevel[];
  ...Record<unknown>;
}

model HierarchyChannel {
  id: string;
  sheet_symbol_id: string;
  parent_sheet_index: SignedInt64;
  child_sheet_index: SignedInt64;
  compiled_child_sheet_index?: NullableInt64;
  instance_index: SignedInt64;
  channel_name?: string;
  hierarchy_path_id: string;
  ...Record<unknown>;
}

model HierarchyLink {
  id: string;
  kind: string;
  parent: JsonObject;
  child: JsonObject;
  match_kind: string;
  hierarchy_path_id: string;
  metadata: JsonObject;
  ...Record<unknown>;
}

model HarnessBundleLink {
  id: string;
  kind: "harness_bundle";
  topology: string;
  name: string;
  parent: JsonObject;
  child: JsonObject;
  bundle: JsonObject;
  match_kind: string;
  metadata: JsonObject;
  ...Record<unknown>;
}

/** Embedded compatibility hierarchy body used by Design b0. */
model SchematicHierarchy {
  schema: "altium_monkey.schematic_hierarchy.a1";
  requested_scope: string;
  effective_scope: string;
  documents: HierarchyDocument[];
  sheet_symbols: HierarchySheetSymbol[];
  hierarchy_paths: HierarchyPathRecord[];
  channels: HierarchyChannel[];
  links: HierarchyLink[];
  harness_bundle_links?: HarnessBundleLink[];
  unresolved: JsonObject[];
}

/** Standalone schematic hierarchy a1 transport. */
@jsonSchema
@id("urn:altium-monkey:schema:schematic_hierarchy_a1")
@extension("title", "Altium Monkey schematic hierarchy a1")
model SchematicHierarchyA1 {
  ...SchematicHierarchy;
}
