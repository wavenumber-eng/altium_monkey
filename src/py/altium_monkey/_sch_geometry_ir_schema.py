"""Embedded handwritten schematic geometry-IR schema. Do not edit."""

from __future__ import annotations

SCH_GEOMETRY_IR_SCHEMA_JSON = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://wavenumber.dev/schemas/altium-monkey/sch-onscreen-geometry-oracle-v1.schema.json",
  "title": "Altium Monkey schematic on-screen geometry IR v1",
  "description": "Oracle-aligned schematic geometry transport retained from the AD25 GeometryMaker payload.",
  "type": "object",
  "required": [
    "schema",
    "source_kind",
    "include_kinds",
    "total_operations",
    "failed_renders",
    "records"
  ],
  "properties": {
    "schema": {
      "const": "x2.sch_onscreen_geometry_oracle.v1"
    },
    "source_path": {
      "type": ["string", "null"]
    },
    "source_kind": {
      "type": "string"
    },
    "include_kinds": {
      "type": "array",
      "items": {"type": "string"}
    },
    "generated_utc": {
      "type": ["string", "null"]
    },
    "total_operations": {
      "type": "integer",
      "minimum": 0
    },
    "failed_renders": {
      "type": "integer",
      "minimum": 0
    },
    "coordinate_space": {
      "$ref": "#/$defs/coordinateSpace"
    },
    "canvas": {
      "$ref": "#/$defs/canvas"
    },
    "document_id": {
      "type": ["string", "null"]
    },
    "workspace_background_color": {
      "type": "string"
    },
    "export_provenance": {
      "type": "object"
    },
    "render_hints": {
      "type": "object"
    },
    "native_svg_hidden_image_ids": {
      "type": "array",
      "items": {"type": "string"}
    },
    "symbol_name": {
      "type": "string"
    },
    "part_id": {
      "type": "integer"
    },
    "display_mode": {
      "type": "integer"
    },
    "background_color": {
      "type": "string"
    },
    "physical_page": {
      "type": "object"
    },
    "compiled_schematic_graphical_links": {
      "type": "array",
      "items": {"type": "object"}
    },
    "physical_designator_text_overrides": {
      "type": "object",
      "additionalProperties": {"type": "string"}
    },
    "records": {
      "type": "array",
      "items": {"$ref": "#/$defs/record"}
    }
  },
  "not": {
    "anyOf": [
      {"required": ["format"]},
      {"required": ["document"]}
    ]
  },
  "additionalProperties": true,
  "$defs": {
    "number": {
      "type": "number"
    },
    "point": {
      "type": "array",
      "prefixItems": [
        {"$ref": "#/$defs/number"},
        {"$ref": "#/$defs/number"}
      ],
      "items": false,
      "minItems": 2,
      "maxItems": 2
    },
    "bounds": {
      "type": "object",
      "required": ["Left", "Top", "Right", "Bottom"],
      "properties": {
        "Left": {"type": "integer"},
        "Top": {"type": "integer"},
        "Right": {"type": "integer"},
        "Bottom": {"type": "integer"}
      },
      "additionalProperties": false
    },
    "coordinateSpace": {
      "type": "object",
      "properties": {
        "kind": {"type": "string"},
        "units_per_px": {"$ref": "#/$defs/number"},
        "y_axis_down": {"type": "boolean"}
      },
      "additionalProperties": true
    },
    "canvas": {
      "type": "object",
      "properties": {
        "width_px": {"$ref": "#/$defs/number"},
        "height_px": {"$ref": "#/$defs/number"}
      },
      "additionalProperties": true
    },
    "pen": {
      "type": "object",
      "required": [
        "color_raw",
        "color_hex",
        "width",
        "min_width",
        "line_join",
        "dash_style",
        "dash_values"
      ],
      "properties": {
        "color_raw": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
        "color_hex": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
        "width": {"$ref": "#/$defs/number"},
        "min_width": {"$ref": "#/$defs/number"},
        "line_join": {"type": "string"},
        "dash_style": {"type": "string"},
        "dash_values": {
          "type": "array",
          "items": {"$ref": "#/$defs/number"}
        }
      },
      "additionalProperties": false
    },
    "brush": {
      "type": "object",
      "required": [
        "brush_type",
        "color_raw",
        "color_hex",
        "color_to_raw",
        "color_to_hex",
        "from_x",
        "from_y",
        "to_x",
        "to_y",
        "pattern_width",
        "pattern_height"
      ],
      "properties": {
        "brush_type": {"type": "string"},
        "color_raw": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
        "color_hex": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
        "color_to_raw": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
        "color_to_hex": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
        "from_x": {"$ref": "#/$defs/number"},
        "from_y": {"$ref": "#/$defs/number"},
        "to_x": {"$ref": "#/$defs/number"},
        "to_y": {"$ref": "#/$defs/number"},
        "pattern_width": {"$ref": "#/$defs/number"},
        "pattern_height": {"$ref": "#/$defs/number"}
      },
      "additionalProperties": false
    },
    "font": {
      "type": "object",
      "required": ["name", "size", "rotation", "underline", "italic", "bold", "strikeout"],
      "properties": {
        "name": {"type": "string"},
        "size": {"$ref": "#/$defs/number"},
        "rotation": {"$ref": "#/$defs/number"},
        "underline": {"type": "boolean"},
        "italic": {"type": "boolean"},
        "bold": {"type": "boolean"},
        "strikeout": {"type": "boolean"}
      },
      "additionalProperties": false
    },
    "polygon": {
      "type": "object",
      "required": ["index", "points"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "points": {
          "type": "array",
          "items": {"$ref": "#/$defs/point"}
        }
      },
      "additionalProperties": false
    },
    "connectionPoint": {
      "type": "object",
      "required": ["id", "kind", "role", "point"],
      "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string"},
        "role": {"type": "string"},
        "point": {"$ref": "#/$defs/point"}
      },
      "additionalProperties": true
    },
    "record": {
      "type": "object",
      "required": ["handle", "unique_id", "kind", "object_id", "operation_count", "operations"],
      "properties": {
        "handle": {"type": "string"},
        "unique_id": {"type": ["string", "null"]},
        "kind": {"type": "string"},
        "object_id": {"type": "string"},
        "bounds": {"$ref": "#/$defs/bounds"},
        "operation_count": {"type": "integer", "minimum": 0},
        "operations": {
          "type": "array",
          "items": {"$ref": "#/$defs/operation"}
        },
        "error": {"type": "string"},
        "skip_svg": {"type": "boolean"},
        "connection_points": {
          "type": "array",
          "items": {"$ref": "#/$defs/connectionPoint"}
        }
      },
      "additionalProperties": true
    },
    "operation": {
      "oneOf": [
        {"$ref": "#/$defs/stringOperation"},
        {"$ref": "#/$defs/linesOperation"},
        {"$ref": "#/$defs/arcOperation"},
        {"$ref": "#/$defs/ellipseOperation"},
        {"$ref": "#/$defs/roundedRectangleOperation"},
        {"$ref": "#/$defs/pushTransformOperation"},
        {"$ref": "#/$defs/popTransformOperation"},
        {"$ref": "#/$defs/pushClipOperation"},
        {"$ref": "#/$defs/popClipOperation"},
        {"$ref": "#/$defs/beginGroupOperation"},
        {"$ref": "#/$defs/endGroupOperation"},
        {"$ref": "#/$defs/imageOperation"},
        {"$ref": "#/$defs/polygonsOperation"}
      ]
    },
    "stringOperation": {
      "type": "object",
      "required": ["index", "type", "x", "y", "text"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotString"},
        "x": {"$ref": "#/$defs/number"},
        "y": {"$ref": "#/$defs/number"},
        "text": {"type": "string"},
        "font": {"$ref": "#/$defs/font"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"},
        "text_width": {"$ref": "#/$defs/number"},
        "text_width_source": {"type": "string"},
        "text_height": {"$ref": "#/$defs/number"},
        "text_height_source": {"type": "string"},
        "debug_member_names": {"type": "array", "items": {"type": "string"}}
      },
      "additionalProperties": true
    },
    "linesOperation": {
      "type": "object",
      "required": ["index", "type", "points"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotLines"},
        "points": {"type": "array", "items": {"$ref": "#/$defs/point"}},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "arcOperation": {
      "type": "object",
      "required": ["index", "type", "center_x", "center_y", "width", "height", "start_angle", "end_angle"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotArc"},
        "center_x": {"$ref": "#/$defs/number"},
        "center_y": {"$ref": "#/$defs/number"},
        "width": {"$ref": "#/$defs/number"},
        "height": {"$ref": "#/$defs/number"},
        "start_angle": {"$ref": "#/$defs/number"},
        "end_angle": {"$ref": "#/$defs/number"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "ellipseOperation": {
      "type": "object",
      "required": ["index", "type", "center_x", "center_y", "width", "height"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotEllipse"},
        "center_x": {"$ref": "#/$defs/number"},
        "center_y": {"$ref": "#/$defs/number"},
        "width": {"$ref": "#/$defs/number"},
        "height": {"$ref": "#/$defs/number"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "roundedRectangleOperation": {
      "type": "object",
      "required": ["index", "type", "x1", "y1", "x2", "y2", "corner_x_radius", "corner_y_radius"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotRoundedRectangle"},
        "x1": {"$ref": "#/$defs/number"},
        "y1": {"$ref": "#/$defs/number"},
        "x2": {"$ref": "#/$defs/number"},
        "y2": {"$ref": "#/$defs/number"},
        "corner_x_radius": {"$ref": "#/$defs/number"},
        "corner_y_radius": {"$ref": "#/$defs/number"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "pushTransformOperation": {
      "type": "object",
      "required": ["index", "type", "matrix"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotPushTransform"},
        "matrix": {
          "type": "array",
          "items": {"$ref": "#/$defs/number"},
          "minItems": 6,
          "maxItems": 6
        },
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "popTransformOperation": {
      "type": "object",
      "required": ["index", "type"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotPopTransform"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "pushClipOperation": {
      "type": "object",
      "required": ["index", "type", "x1", "y1", "x2", "y2"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotPushClip"},
        "x1": {"$ref": "#/$defs/number"},
        "y1": {"$ref": "#/$defs/number"},
        "x2": {"$ref": "#/$defs/number"},
        "y2": {"$ref": "#/$defs/number"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "popClipOperation": {
      "type": "object",
      "required": ["index", "type"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotPopClip"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "beginGroupOperation": {
      "type": "object",
      "required": ["index", "type", "parameters"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotBeginGroup"},
        "parameters": {"type": "string"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "endGroupOperation": {
      "type": "object",
      "required": ["index", "type"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotEndGroup"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "imageOperation": {
      "type": "object",
      "required": ["index", "type", "dest_x1", "dest_y1", "dest_x2", "dest_y2", "source_x1", "source_y1", "source_x2", "source_y2", "alpha"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotImage"},
        "dest_x1": {"$ref": "#/$defs/number"},
        "dest_y1": {"$ref": "#/$defs/number"},
        "dest_x2": {"$ref": "#/$defs/number"},
        "dest_y2": {"$ref": "#/$defs/number"},
        "source_x1": {"$ref": "#/$defs/number"},
        "source_y1": {"$ref": "#/$defs/number"},
        "source_x2": {"$ref": "#/$defs/number"},
        "source_y2": {"$ref": "#/$defs/number"},
        "alpha": {"$ref": "#/$defs/number"},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    },
    "polygonsOperation": {
      "type": "object",
      "required": ["index", "type", "polygons"],
      "properties": {
        "index": {"type": "integer", "minimum": 0},
        "type": {"const": "gotPolygons"},
        "polygons": {"type": "array", "items": {"$ref": "#/$defs/polygon"}},
        "pen": {"$ref": "#/$defs/pen"},
        "brush": {"$ref": "#/$defs/brush"}
      },
      "additionalProperties": true
    }
  }
}
"""

__all__ = ("SCH_GEOMETRY_IR_SCHEMA_JSON",)
