"""
SCH IR payload helpers.

This module owns the Python-side schema constants for the
`x2.sch_onscreen_geometry_oracle.v1` payload used by the AD25 geometry oracle.

Within the Python renderer pipeline, this module is the schema-aligned SCH IR
layer, even though the historical API names still use "geometry".
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .altium_record_types import color_to_hex


SCH_GEOMETRY_ORACLE_SCHEMA = "x2.sch_onscreen_geometry_oracle.v1"


class SchIrRenderProfile(str, Enum):
    """
    High-level IR intent profiles.
    """

    ORACLE = "oracle"
    ONSCREEN = "onscreen"


def normalize_sch_ir_render_profile(
    profile: SchIrRenderProfile | str,
) -> SchIrRenderProfile:
    if isinstance(profile, SchIrRenderProfile):
        return profile
    normalized = str(profile or "").strip().lower()
    if normalized == SchIrRenderProfile.ORACLE.value:
        return SchIrRenderProfile.ORACLE
    if normalized == SchIrRenderProfile.ONSCREEN.value:
        return SchIrRenderProfile.ONSCREEN
    raise ValueError(f"Unknown schematic IR render profile: {profile!r}")


class SchGeometryOpKind(str, Enum):
    """
    Oracle-aligned schematic geometry operation kinds.
    """

    STRING = "gotString"
    LINES = "gotLines"
    ARC = "gotArc"
    ELLIPSE = "gotEllipse"
    ROUNDED_RECTANGLE = "gotRoundedRectangle"
    PUSH_TRANSFORM = "gotPushTransform"
    POP_TRANSFORM = "gotPopTransform"
    PUSH_CLIP = "gotPushClip"
    POP_CLIP = "gotPopClip"
    BEGIN_GROUP = "gotBeginGroup"
    END_GROUP = "gotEndGroup"
    IMAGE = "gotImage"
    POLYGONS = "gotPolygons"


def _signed_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _oracle_color_raw_from_win32(color_raw: int, *, alpha: int = 0xFF) -> int:
    """
    Convert parsed schematic Win32 color (0x00BBGGRR) to GeometryMaker color raw.
    
    The geometry oracle serializes colors as signed 32-bit integers whose low
    24 bits match the rendered RGB hex form.
    """
    rgb_hex = color_to_hex(int(color_raw))[1:]
    alpha_byte = max(0, min(255, int(alpha)))
    return _signed_int32((alpha_byte << 24) | int(rgb_hex, 16))


def _oracle_color_hex(color_raw: int) -> str:
    return f"#{(int(color_raw) & 0xFFFFFF):06X}"


def make_solid_brush(color_raw: int, *, alpha: int = 0xFF) -> dict[str, Any]:
    """
    Create a solid brush payload from a parsed schematic Win32 color.
    """
    geometry_color_raw = _oracle_color_raw_from_win32(color_raw, alpha=alpha)
    return {
        "brush_type": "gbtSolid",
        "color_raw": geometry_color_raw,
        "color_hex": _oracle_color_hex(geometry_color_raw),
        "color_to_raw": 0,
        "color_to_hex": "#000000",
        "from_x": 0,
        "from_y": 0,
        "to_x": 0,
        "to_y": 0,
        "pattern_width": 0,
        "pattern_height": 0,
    }


def make_pen(
    color_raw: int,
    *,
    width: float = 0,
    min_width: int = 1,
    line_join: str = "pljRound",
    dash_style: str = "pdsSolid",
    dash_values: list[float] | None = None,
) -> dict[str, Any]:
    """
    Create a pen payload from a parsed schematic Win32 color.
    """
    geometry_color_raw = _oracle_color_raw_from_win32(color_raw)
    width_value = float(width)
    if abs(width_value - round(width_value)) <= 1e-9:
        width_payload: int | float = int(round(width_value))
    else:
        width_payload = width_value

    return {
        "color_raw": geometry_color_raw,
        "color_hex": _oracle_color_hex(geometry_color_raw),
        "width": width_payload,
        "min_width": int(min_width),
        "line_join": str(line_join),
        "dash_style": str(dash_style),
        "dash_values": list(dash_values or []),
    }


def make_font_payload(
    *,
    name: str,
    size_px: float,
    units_per_px: int = 64,
    rotation: float = 0.0,
    underline: bool = False,
    italic: bool = False,
    bold: bool = False,
    strikeout: bool = False,
) -> dict[str, Any]:
    """
    Create an oracle-aligned font payload from screen-space font metrics.
    """
    return {
        "name": str(name),
        "size": float(size_px) * int(units_per_px),
        "rotation": float(rotation),
        "underline": bool(underline),
        "italic": bool(italic),
        "bold": bool(bold),
        "strikeout": bool(strikeout),
    }


def wrap_record_operations(
    unique_id: str,
    operations: list["SchGeometryOp"],
    *,
    units_per_px: int = 64,
    workspace_height_px: int = 1000,
) -> list["SchGeometryOp"]:
    """
    Wrap record-local operations in the standard GeometryMaker group envelope.
    """
    return [
        SchGeometryOp.push_transform([1, 0, 0, 1, 0, -(units_per_px * workspace_height_px)]),
        SchGeometryOp.begin_group(),
        SchGeometryOp.begin_group("DocumentMainGroup"),
        SchGeometryOp.begin_group(unique_id),
        *operations,
        SchGeometryOp.end_group(),
        SchGeometryOp.end_group(),
        SchGeometryOp.end_group(),
        SchGeometryOp.pop_transform(),
    ]


def unwrap_record_operations(
    record_or_operations: "SchGeometryRecord | list[SchGeometryOp]",
    *,
    unique_id: str | None = None,
) -> list["SchGeometryOp"]:
    """
    Return record-local operations from a wrapped geometry record or raw ops list.
    """
    if isinstance(record_or_operations, list):
        return record_or_operations

    operations = list(getattr(record_or_operations, "operations", []) or [])
    if len(operations) < 8:
        return operations

    if (
        operations[0].kind_str() == SchGeometryOpKind.PUSH_TRANSFORM.value
        and operations[1].kind_str() == SchGeometryOpKind.BEGIN_GROUP.value
        and operations[2].kind_str() == SchGeometryOpKind.BEGIN_GROUP.value
        and operations[2].payload.get("parameters") == "DocumentMainGroup"
        and operations[-4].kind_str() == SchGeometryOpKind.END_GROUP.value
        and operations[-3].kind_str() == SchGeometryOpKind.END_GROUP.value
        and operations[-2].kind_str() == SchGeometryOpKind.END_GROUP.value
        and operations[-1].kind_str() == SchGeometryOpKind.POP_TRANSFORM.value
    ):
        wrapped_id = operations[3].payload.get("parameters")
        if unique_id is None or wrapped_id == unique_id:
            return operations[4:-4]

    return operations


def svg_coord_to_geometry(
    x_px: float,
    y_px: float,
    *,
    sheet_height_px: float,
    units_per_px: int = 64,
    workspace_height_px: int = 1000,
) -> tuple[float, float]:
    """
    Convert a screen-space SVG coordinate to oracle geometry units.
    """
    x_units = float(x_px) * int(units_per_px)
    y_units = (float(y_px) - float(sheet_height_px)) * int(units_per_px) + int(units_per_px) * int(workspace_height_px)
    return (x_units, y_units)


def split_overline_text(text: str) -> tuple[str, list[int]]:
    """
    Return clean text plus character indexes that carry an overline.
    """
    clean_chars: list[str] = []
    overline_segments: list[int] = []

    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            if clean_chars:
                overline_segments.append(len(clean_chars) - 1)
            i += 1
            continue
        clean_chars.append(char)
        i += 1

    return ("".join(clean_chars), overline_segments)


def make_text_with_overline_operations(
    *,
    text: str,
    baseline_x_px: float,
    baseline_y_px: float,
    sheet_height_px: float,
    font_payload: dict[str, Any],
    font_size_px: float,
    font_name: str,
    bold: bool = False,
    italic: bool = False,
    brush_color_raw: int = 0,
    pen_color_raw: int | None = None,
    rotation_deg: float = 0.0,
    units_per_px: int = 64,
    geometry_step_px: float | None = None,
) -> list["SchGeometryOp"]:
    """
    Build oracle-aligned text operations, including individual overline segments.
    
    GeometryMaker stores text coordinates offset from the rendered baseline by the
    truncated baseline font size. Overline segments live on that same geometry Y.
    """
    from .altium_text_metrics import measure_text_width

    clean_text, overline_segments = split_overline_text(text)
    if not clean_text:
        return []

    baseline_font_size = float(int(font_size_px))
    geometry_step = float(geometry_step_px) if geometry_step_px is not None else baseline_font_size
    theta = 0.017453292519943295 * float(rotation_deg)
    geometry_x_px = float(baseline_x_px) + math.sin(theta) * geometry_step
    geometry_y_px = float(baseline_y_px) - math.cos(theta) * geometry_step
    geometry_x, geometry_y = svg_coord_to_geometry(
        geometry_x_px,
        geometry_y_px,
        sheet_height_px=sheet_height_px,
        units_per_px=units_per_px,
    )

    operations: list[SchGeometryOp] = []
    if overline_segments:
        pen = make_pen(
            brush_color_raw if pen_color_raw is None else pen_color_raw,
            width=units_per_px,
        )
        overline_y_px = float(baseline_y_px) - geometry_step
        for char_idx in overline_segments:
            text_before = clean_text[:char_idx]
            text_including = clean_text[: char_idx + 1]
            width_before = (
                measure_text_width(
                    text_before,
                    font_size_px,
                    font_name,
                    bold=bold,
                    italic=italic,
                    include_rsb=True,
                )
                if text_before
                else 0.0
            )
            include_rsb = char_idx < len(clean_text) - 1
            width_including = measure_text_width(
                text_including,
                font_size_px,
                font_name,
                bold=bold,
                italic=italic,
                include_rsb=include_rsb,
            )
            line_start = svg_coord_to_geometry(
                float(baseline_x_px) + width_including,
                overline_y_px,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            line_end = svg_coord_to_geometry(
                float(baseline_x_px) + width_before,
                overline_y_px,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            operations.append(SchGeometryOp.lines([line_start, line_end], pen=pen))

    operations.append(
        SchGeometryOp.string(
            x=geometry_x,
            y=geometry_y,
            text=clean_text,
            font=font_payload,
            brush=make_solid_brush(brush_color_raw),
        )
    )
    return operations


@dataclass(frozen=True)
class SchGeometryBounds:
    """
    Record bounds block from the geometry oracle payload.
    """

    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SchGeometryBounds | None:
        if not isinstance(data, dict):
            return None
        return cls(
            left=int(data.get("Left", 0)),
            top=int(data.get("Top", 0)),
            right=int(data.get("Right", 0)),
            bottom=int(data.get("Bottom", 0)),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "Left": int(self.left),
            "Top": int(self.top),
            "Right": int(self.right),
            "Bottom": int(self.bottom),
        }


@dataclass(frozen=True)
class SchGeometryOp:
    """
    One schematic geometry operation in oracle-aligned form.
    """

    kind: SchGeometryOpKind | str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchGeometryOp:
        payload = copy.deepcopy(data)
        kind = str(payload.pop("type", "")).strip()
        payload.pop("index", None)
        return cls(kind=kind, payload=payload)

    @classmethod
    def push_transform(cls, matrix: list[float] | tuple[float, ...]) -> SchGeometryOp:
        return cls(kind=SchGeometryOpKind.PUSH_TRANSFORM, payload={"matrix": [float(v) for v in matrix]})

    @classmethod
    def pop_transform(cls) -> SchGeometryOp:
        return cls(kind=SchGeometryOpKind.POP_TRANSFORM)

    @classmethod
    def begin_group(cls, parameters: str = "") -> SchGeometryOp:
        return cls(kind=SchGeometryOpKind.BEGIN_GROUP, payload={"parameters": str(parameters)})

    @classmethod
    def end_group(cls) -> SchGeometryOp:
        return cls(kind=SchGeometryOpKind.END_GROUP)

    @classmethod
    def push_clip(
        cls,
        *,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> "SchGeometryOp":
        return cls(
            kind=SchGeometryOpKind.PUSH_CLIP,
            payload={
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
            },
        )

    @classmethod
    def pop_clip(cls) -> "SchGeometryOp":
        return cls(kind=SchGeometryOpKind.POP_CLIP)

    @classmethod
    def rounded_rectangle(
        cls,
        *,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        corner_x_radius: float = 0,
        corner_y_radius: float = 0,
        brush: dict[str, Any] | None = None,
        pen: dict[str, Any] | None = None,
    ) -> SchGeometryOp:
        payload: dict[str, Any] = {
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "corner_x_radius": corner_x_radius,
            "corner_y_radius": corner_y_radius,
        }
        if brush is not None:
            payload["brush"] = copy.deepcopy(brush)
        if pen is not None:
            payload["pen"] = copy.deepcopy(pen)
        return cls(kind=SchGeometryOpKind.ROUNDED_RECTANGLE, payload=payload)

    @classmethod
    def lines(
        cls,
        points: list[list[float] | tuple[float, float]],
        *,
        pen: dict[str, Any] | None = None,
    ) -> SchGeometryOp:
        payload: dict[str, Any] = {
            "points": [[float(p[0]), float(p[1])] for p in points],
        }
        if pen is not None:
            payload["pen"] = copy.deepcopy(pen)
        return cls(kind=SchGeometryOpKind.LINES, payload=payload)

    @classmethod
    def string(
        cls,
        *,
        x: float,
        y: float,
        text: str,
        font: dict[str, Any],
        brush: dict[str, Any] | None = None,
    ) -> SchGeometryOp:
        payload: dict[str, Any] = {
            "x": x,
            "y": y,
            "text": str(text),
            "font": copy.deepcopy(font),
        }
        if brush is not None:
            payload["brush"] = copy.deepcopy(brush)
        return cls(kind=SchGeometryOpKind.STRING, payload=payload)

    @classmethod
    def arc(
        cls,
        *,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        start_angle: float,
        end_angle: float,
        pen: dict[str, Any] | None = None,
    ) -> "SchGeometryOp":
        payload: dict[str, Any] = {
            "center_x": float(center_x),
            "center_y": float(center_y),
            "width": float(width),
            "height": float(height),
            "start_angle": float(start_angle),
            "end_angle": float(end_angle),
        }
        if pen is not None:
            payload["pen"] = copy.deepcopy(pen)
        return cls(kind=SchGeometryOpKind.ARC, payload=payload)

    @classmethod
    def polygons(
        cls,
        polygons: list[list[list[float] | tuple[float, float]]],
        *,
        brush: dict[str, Any] | None = None,
        pen: dict[str, Any] | None = None,
    ) -> "SchGeometryOp":
        payload: dict[str, Any] = {
            "polygons": [
                {
                    "index": index,
                    "points": [[float(point[0]), float(point[1])] for point in polygon],
                }
                for index, polygon in enumerate(polygons)
            ]
        }
        if brush is not None:
            payload["brush"] = copy.deepcopy(brush)
        if pen is not None:
            payload["pen"] = copy.deepcopy(pen)
        return cls(kind=SchGeometryOpKind.POLYGONS, payload=payload)

    @classmethod
    def image(
        cls,
        *,
        dest_x1: float,
        dest_y1: float,
        dest_x2: float,
        dest_y2: float,
        source_x1: float = 0,
        source_y1: float = 0,
        source_x2: float,
        source_y2: float,
        alpha: float = 1.0,
    ) -> "SchGeometryOp":
        return cls(
            kind=SchGeometryOpKind.IMAGE,
            payload={
                "dest_x1": float(dest_x1),
                "dest_y1": float(dest_y1),
                "dest_x2": float(dest_x2),
                "dest_y2": float(dest_y2),
                "source_x1": float(source_x1),
                "source_y1": float(source_y1),
                "source_x2": float(source_x2),
                "source_y2": float(source_y2),
                "alpha": float(alpha),
            },
        )

    def kind_str(self) -> str:
        return self.kind.value if isinstance(self.kind, SchGeometryOpKind) else str(self.kind)

    def to_dict(self, *, index: int | None = None) -> dict[str, Any]:
        data = {"type": self.kind_str()}
        if index is not None:
            data["index"] = int(index)
        data.update(copy.deepcopy(self.payload))
        return data


@dataclass(frozen=True)
class SchGeometryRecord:
    """
    One record entry from the schematic geometry document.
    """

    handle: str
    unique_id: str
    kind: str
    object_id: str
    bounds: SchGeometryBounds | None = None
    operations: list[SchGeometryOp] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchGeometryRecord:
        extras = copy.deepcopy(data)
        operations_data = extras.pop("operations", []) or []
        bounds = SchGeometryBounds.from_dict(extras.pop("bounds", None))
        extras.pop("operation_count", None)
        return cls(
            handle=str(extras.pop("handle", "")),
            unique_id=str(extras.pop("unique_id", "")),
            kind=str(extras.pop("kind", "")),
            object_id=str(extras.pop("object_id", "")),
            bounds=bounds,
            operations=[SchGeometryOp.from_dict(op) for op in operations_data if isinstance(op, dict)],
            extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "handle": self.handle,
            "unique_id": self.unique_id,
            "kind": self.kind,
            "object_id": self.object_id,
        }
        if self.bounds is not None:
            data["bounds"] = self.bounds.to_dict()
        data["operation_count"] = len(self.operations)
        data["operations"] = [op.to_dict(index=index) for index, op in enumerate(self.operations)]
        data.update(copy.deepcopy(self.extras))
        return data


@dataclass(frozen=True)
class SchGeometryDocument:
    """
    Typed, oracle-aligned schematic geometry document.
    """

    records: list[SchGeometryRecord] = field(default_factory=list)
    source_path: str | None = None
    source_kind: str = "SCH"
    include_kinds: list[str] = field(default_factory=lambda: ["all"])
    generated_utc: str | None = None
    failed_renders: int = 0
    coordinate_space: dict[str, Any] | None = None
    canvas: dict[str, Any] | None = None
    document_id: str | None = None
    workspace_background_color: str | None = None
    export_provenance: dict[str, Any] | None = None
    render_hints: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchGeometryDocument:
        schema = str(data.get("schema", "")).strip()
        if schema != SCH_GEOMETRY_ORACLE_SCHEMA:
            raise ValueError(f"Unexpected geometry oracle schema: {schema!r}")

        extras = copy.deepcopy(data)
        extras.pop("schema", None)
        extras.pop("source_path", None)
        extras.pop("source_kind", None)
        extras.pop("include_kinds", None)
        extras.pop("generated_utc", None)
        extras.pop("total_operations", None)
        extras.pop("failed_renders", None)
        extras.pop("records", None)
        coordinate_space = extras.pop("coordinate_space", None)
        canvas = extras.pop("canvas", None)
        document_id = extras.pop("document_id", None)
        workspace_background_color = extras.pop("workspace_background_color", None)
        export_provenance = extras.pop("export_provenance", None)
        render_hints = extras.pop("render_hints", None)

        records = []
        for record in data.get("records", []):
            if isinstance(record, dict):
                records.append(SchGeometryRecord.from_dict(record))

        return cls(
            records=records,
            source_path=str(data.get("source_path")) if data.get("source_path") is not None else None,
            source_kind=str(data.get("source_kind", "SCH")),
            include_kinds=[str(kind) for kind in (data.get("include_kinds") or ["all"])],
            generated_utc=str(data.get("generated_utc")) if data.get("generated_utc") is not None else None,
            failed_renders=int(data.get("failed_renders", 0) or 0),
            coordinate_space=copy.deepcopy(coordinate_space) if isinstance(coordinate_space, dict) else None,
            canvas=copy.deepcopy(canvas) if isinstance(canvas, dict) else None,
            document_id=str(document_id) if document_id is not None else None,
            workspace_background_color=str(workspace_background_color) if workspace_background_color else None,
            export_provenance=copy.deepcopy(export_provenance) if isinstance(export_provenance, dict) else None,
            render_hints=copy.deepcopy(render_hints) if isinstance(render_hints, dict) else None,
            extras=extras,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> SchGeometryDocument:
        geometry_path = Path(path)
        data = json.loads(geometry_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError(f"Geometry payload must be a JSON object: {geometry_path}")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": SCH_GEOMETRY_ORACLE_SCHEMA,
            "source_kind": self.source_kind,
            "include_kinds": [str(kind) for kind in self.include_kinds],
            "total_operations": sum(len(record.operations) for record in self.records),
            "failed_renders": int(self.failed_renders),
            "records": [record.to_dict() for record in self.records],
        }
        if self.source_path is not None:
            data["source_path"] = self.source_path
        if self.generated_utc is not None:
            data["generated_utc"] = self.generated_utc
        if self.coordinate_space is not None:
            data["coordinate_space"] = copy.deepcopy(self.coordinate_space)
        if self.canvas is not None:
            data["canvas"] = copy.deepcopy(self.canvas)
        if self.document_id is not None:
            data["document_id"] = self.document_id
        if self.workspace_background_color is not None:
            data["workspace_background_color"] = self.workspace_background_color
        if self.export_provenance is not None:
            data["export_provenance"] = copy.deepcopy(self.export_provenance)
        if self.render_hints is not None:
            data["render_hints"] = copy.deepcopy(self.render_hints)
        data.update(copy.deepcopy(self.extras))
        return data

    def to_normalized_dict(self, *, source_path: str | None = None) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("generated_utc", None)

        if source_path is None:
            data.pop("source_path", None)
        else:
            data["source_path"] = source_path.replace("\\", "/")

        include_kinds = data.get("include_kinds")
        if isinstance(include_kinds, list):
            data["include_kinds"] = [str(kind) for kind in include_kinds]

        records = data.get("records")
        if not isinstance(records, list):
            data["records"] = []
            data["total_operations"] = 0
            return data

        total_operations = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            operations = record.get("operations")
            if isinstance(operations, list):
                record["operation_count"] = len(operations)
                total_operations += len(operations)
        data["total_operations"] = total_operations
        return data

    def write_json(self, path: str | Path) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path

    def write_normalized_json(
        self,
        path: str | Path,
        *,
        source_path: str | None = None,
    ) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_normalized_dict(source_path=source_path)
        output_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path


class SchGeometryOracle(SchGeometryDocument):
    """
    Alias for the external oracle payload wrapper.
    """

