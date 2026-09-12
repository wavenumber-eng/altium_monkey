import "@typespec/json-schema";

using TypeSpec.JsonSchema;

namespace Wavenumber.AltiumMonkey.SchCompiledDesign;

/** Signed 64-bit JSON integer shared by schema and Rust boundaries. */
@extension("x-altium-monkey-signed-integer-width", 64)
scalar SignedInt64 extends integer;

/** Arbitrary JSON object retained at an explicitly open compatibility seam. */
model JsonObject is Record<unknown>;

/** Arbitrary JSON parameter map retained in insertion-insensitive key order. */
model ParameterMap is Record<unknown>;

/** String-valued map. */
model StringMap is Record<string>;

/** String-list-valued map. */
model StringListMap is Record<string[]>;

/** Non-empty identity used by current schematic transport contracts. */
@minLength(1)
scalar SchematicIdentityString extends string;

/** String-only component parameters for current schematic transports. */
model SchematicStringParameterMap is Record<string>;

/** Correlated physical-page and logical-source identity. */
model SchematicSourcePage {
  physical_document_id: NullableString;
  source_sheet_file: NullableString;
}

/** Required field whose value may be a string or JSON null. */
union NullableString {
  value: string,
  nullValue: null,
}

/** Required field whose value may be a signed integer or JSON null. */
union NullableInt64 {
  value: SignedInt64,
  nullValue: null,
}

/** Compatibility sheet number: canonical decimal integer or exact source string. */
union SheetNumber {
  integerValue: SignedInt64,
  stringValue: string,
}

/** Pick-and-place position convention. */
union PositionMode {
  altiumPickPlace: "altium-pick-place",
  componentOrigin: "component-origin",
}
