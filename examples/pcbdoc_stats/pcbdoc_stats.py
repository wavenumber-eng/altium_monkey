from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from altium_monkey import (
    AltiumDesign,
    PcbLayer,
)
from altium_monkey.altium_resolved_layer_stack import (
    ResolvedLayer,
    ResolvedLayerStack,
    resolved_layer_stack_from_pcbdoc,
)


MILS_TO_MM = 0.0254
ONE_OUNCE_COPPER_MILS = 1.378

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
PROJECT_DIR = EXAMPLES_DIR / "assets" / "projects" / "loz-old-man"
PROJECT_FILE = PROJECT_DIR / "loz-old-man.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_JSON = OUTPUT_DIR / "pcbdoc_stats.json"
OUTPUT_TEXT = OUTPUT_DIR / "pcbdoc_stats.txt"


@dataclass(frozen=True)
class WidthStats:
    track_mils: float | None
    arc_mils: float | None

    @property
    def overall_mils(self) -> float | None:
        values = [value for value in (self.track_mils, self.arc_mils) if value]
        return min(values) if values else None


@dataclass(frozen=True)
class HoleStats:
    plated_pad_holes: int
    plated_through_vias: int
    non_plated_pad_holes: int
    plated_slotted_pad_holes: int
    non_plated_slotted_pad_holes: int

    @property
    def plated_through_holes(self) -> int:
        return self.plated_pad_holes + self.plated_through_vias


@dataclass(frozen=True)
class DrillTableEntry:
    plated: bool
    slotted: bool
    diameter_mils: float
    slot_length_mils: float | None
    pad_count: int
    via_count: int

    @property
    def total_count(self) -> int:
        return self.pad_count + self.via_count

    @property
    def plating_label(self) -> str:
        return "plated" if self.plated else "non-plated"

    @property
    def drill_type(self) -> str:
        return "slot" if self.slotted else "round"


def _mils_to_mm(mils: float) -> float:
    return round(mils * MILS_TO_MM, 6)


def _format_mils_mm(mils: float | None) -> str:
    if mils is None:
        return "n/a"
    return f"{mils:.3f} mil ({_mils_to_mm(mils):.3f} mm)"


def _format_size(width_mils: float, height_mils: float) -> str:
    return (
        f"{width_mils:.3f} mil x {height_mils:.3f} mil "
        f"({_mils_to_mm(width_mils):.3f} mm x {_mils_to_mm(height_mils):.3f} mm)"
    )


def _is_top_to_bottom_via(via: object) -> bool:
    start = int(getattr(via, "layer_start", 0) or 0)
    end = int(getattr(via, "layer_end", 0) or 0)
    return {start, end} == {PcbLayer.TOP.value, PcbLayer.BOTTOM.value}


def _pad_is_slot(pad: object) -> bool:
    return (
        int(getattr(pad, "hole_shape", 0) or 0) == 2
        or int(getattr(pad, "slot_size", 0) or 0) > 0
    )


def _pad_hole_size_mils(pad: object) -> float:
    return float(getattr(pad, "hole_size_mils", 0.0) or 0.0)


def _pad_slot_length_mils(pad: object) -> float | None:
    if not _pad_is_slot(pad):
        return None
    slot_size = int(getattr(pad, "slot_size", 0) or 0)
    if slot_size <= 0:
        return None
    return slot_size / 10000.0


def _collect_hole_stats(pcbdoc: object) -> HoleStats:
    pads = list(getattr(pcbdoc, "pads", []) or [])
    vias = list(getattr(pcbdoc, "vias", []) or [])

    plated_pad_holes = sum(
        1
        for pad in pads
        if int(getattr(pad, "hole_size", 0) or 0) > 0
        and bool(getattr(pad, "is_plated", False))
    )
    non_plated_pad_holes = sum(
        1
        for pad in pads
        if int(getattr(pad, "hole_size", 0) or 0) > 0
        and not bool(getattr(pad, "is_plated", False))
    )
    plated_through_vias = sum(
        1
        for via in vias
        if int(getattr(via, "hole_size", 0) or 0) > 0 and _is_top_to_bottom_via(via)
    )
    plated_slotted_pad_holes = sum(
        1
        for pad in pads
        if int(getattr(pad, "hole_size", 0) or 0) > 0
        and bool(getattr(pad, "is_plated", False))
        and _pad_is_slot(pad)
    )
    non_plated_slotted_pad_holes = sum(
        1
        for pad in pads
        if int(getattr(pad, "hole_size", 0) or 0) > 0
        and not bool(getattr(pad, "is_plated", False))
        and _pad_is_slot(pad)
    )

    return HoleStats(
        plated_pad_holes=plated_pad_holes,
        plated_through_vias=plated_through_vias,
        non_plated_pad_holes=non_plated_pad_holes,
        plated_slotted_pad_holes=plated_slotted_pad_holes,
        non_plated_slotted_pad_holes=non_plated_slotted_pad_holes,
    )


def _rounded_drill_key(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def _collect_drill_table(pcbdoc: object) -> list[DrillTableEntry]:
    counts: dict[tuple[bool, bool, float, float | None], dict[str, int]] = {}

    for pad in list(getattr(pcbdoc, "pads", []) or []):
        diameter_mils = _pad_hole_size_mils(pad)
        if diameter_mils <= 0.0:
            continue
        slotted = _pad_is_slot(pad)
        key = (
            bool(getattr(pad, "is_plated", False)),
            slotted,
            _rounded_drill_key(diameter_mils) or 0.0,
            _rounded_drill_key(_pad_slot_length_mils(pad)) if slotted else None,
        )
        counts.setdefault(key, {"pad_count": 0, "via_count": 0})["pad_count"] += 1

    for via in list(getattr(pcbdoc, "vias", []) or []):
        diameter_mils = float(getattr(via, "hole_size_mils", 0.0) or 0.0)
        if diameter_mils <= 0.0:
            continue
        key = (True, False, _rounded_drill_key(diameter_mils) or 0.0, None)
        counts.setdefault(key, {"pad_count": 0, "via_count": 0})["via_count"] += 1

    entries = [
        DrillTableEntry(
            plated=plated,
            slotted=slotted,
            diameter_mils=diameter_mils,
            slot_length_mils=slot_length_mils,
            pad_count=item["pad_count"],
            via_count=item["via_count"],
        )
        for (plated, slotted, diameter_mils, slot_length_mils), item in counts.items()
    ]
    return sorted(
        entries,
        key=lambda entry: (
            0 if entry.plated else 1,
            0 if not entry.slotted else 1,
            entry.diameter_mils,
            entry.slot_length_mils or 0.0,
        ),
    )


def _primitive_is_on_copper_layer(primitive: object) -> bool:
    try:
        return PcbLayer(int(getattr(primitive, "layer"))).is_copper()
    except (TypeError, ValueError):
        return False


def _positive_copper_widths_mils(primitives: list[object]) -> list[float]:
    return [
        width
        for primitive in primitives
        if _primitive_is_on_copper_layer(primitive)
        if (width := float(getattr(primitive, "width_mils", 0.0) or 0.0)) > 0.0
    ]


def _collect_width_stats(pcbdoc: object) -> WidthStats:
    track_widths = _positive_copper_widths_mils(
        list(getattr(pcbdoc, "tracks", []) or [])
    )
    arc_widths = _positive_copper_widths_mils(list(getattr(pcbdoc, "arcs", []) or []))
    return WidthStats(
        track_mils=min(track_widths) if track_widths else None,
        arc_mils=min(arc_widths) if arc_widths else None,
    )


def _is_copper_layer(layer: ResolvedLayer) -> bool:
    legacy_id = layer.legacy_id
    return (
        legacy_id is not None
        and PcbLayer.TOP.value <= legacy_id <= PcbLayer.BOTTOM.value
    )


def _layer_kind(layer: ResolvedLayer) -> str:
    if _is_copper_layer(layer):
        return "copper"
    material = (layer.material or "").lower()
    if "solder" in material:
        return "mask"
    if layer.thickness_mils > 0.0:
        return "dielectric"
    return "surface"


def _copper_weight_oz(thickness_mils: float) -> float | None:
    if thickness_mils <= 0.0:
        return None
    return round(thickness_mils / ONE_OUNCE_COPPER_MILS, 3)


def _physical_stack_layers(stack: ResolvedLayerStack) -> list[ResolvedLayer]:
    return sorted(
        [layer for layer in stack.layers if layer.stack_index is not None],
        key=lambda layer: -1 if layer.stack_index is None else layer.stack_index,
    )


def _v9_layers_by_stack_index(pcbdoc: object) -> dict[int, object]:
    board = getattr(pcbdoc, "board", None)
    return {
        int(getattr(layer, "stack_index")): layer
        for layer in list(getattr(board, "v9_stack", []) or [])
        if getattr(layer, "stack_index", None) is not None
    }


def _build_physical_stack_summary(
    pcbdoc: object,
    stack: ResolvedLayerStack,
) -> list[dict[str, object]]:
    v9_by_index = _v9_layers_by_stack_index(pcbdoc)
    summary: list[dict[str, object]] = []
    for layer in _physical_stack_layers(stack):
        stack_index = int(layer.stack_index) if layer.stack_index is not None else -1
        v9_layer = v9_by_index.get(stack_index)
        kind = _layer_kind(layer)
        dk = (
            float(getattr(v9_layer, "diel_constant", 0.0) or 0.0)
            if v9_layer is not None
            else 0.0
        )
        df = (
            float(getattr(v9_layer, "diel_loss_tangent", 0.0) or 0.0)
            if v9_layer is not None
            else 0.0
        )
        copper_weight = (
            _copper_weight_oz(layer.thickness_mils) if kind == "copper" else None
        )
        summary.append(
            {
                "stack_index": stack_index,
                "name": layer.display_name,
                "kind": kind,
                "thickness_mils": layer.thickness_mils,
                "thickness_mm": _mils_to_mm(layer.thickness_mils),
                "dk": dk if dk > 0.0 else None,
                "df": df if df > 0.0 else None,
                "copper_weight_oz": copper_weight,
                "material": layer.material,
                "legacy_id": layer.legacy_id,
            }
        )
    return summary


def _total_stack_thickness_mils(physical_stack: list[dict[str, object]]) -> float:
    return round(
        sum(float(item["thickness_mils"]) for item in physical_stack),
        6,
    )


def _stack_ascii_lines(physical_stack: list[dict[str, object]]) -> list[str]:
    if not physical_stack:
        return ["  (no physical stack metadata found)"]

    name_width = max(len(str(layer["name"])) for layer in physical_stack)
    thickness_values = [
        _format_mils_mm(float(layer["thickness_mils"])) for layer in physical_stack
    ]
    thickness_width = max(
        len("thickness"),
        *(len(value) for value in thickness_values),
    )
    dk_values = [
        "-" if layer["dk"] is None else f"{float(layer['dk']):.3f}"
        for layer in physical_stack
    ]
    df_values = [
        "-" if layer["df"] is None else f"{float(layer['df']):.3f}"
        for layer in physical_stack
    ]
    copper_weight_values = [
        "-"
        if layer["copper_weight_oz"] is None
        else f"{float(layer['copper_weight_oz']):.3f} oz"
        for layer in physical_stack
    ]
    copper_weight_width = max(
        len("copper wt"),
        *(len(value) for value in copper_weight_values),
    )
    lines = [
        "  idx  kind        layer"
        + " " * max(1, name_width - len("layer") + 2)
        + f"{'thickness':<{thickness_width}}  "
        + "Dk     Df     "
        + f"{'copper wt':<{copper_weight_width}}  "
        + "material",
        "  ---  ----------  "
        + "-" * name_width
        + "  "
        + "-" * thickness_width
        + "  -----  -----  "
        + "-" * copper_weight_width
        + "  ----------------",
    ]
    for layer, thickness, dk, df, copper_weight in zip(
        physical_stack,
        thickness_values,
        dk_values,
        df_values,
        copper_weight_values,
        strict=True,
    ):
        lines.append(
            f"  {int(layer['stack_index']):>3}  "
            f"{str(layer['kind']):<10}  "
            f"{str(layer['name']):<{name_width}}  "
            f"{thickness:<{thickness_width}}  "
            f"{dk:>5}  "
            f"{df:>5}  "
            f"{copper_weight:<{copper_weight_width}}  "
            f"{layer['material'] or '-'}"
        )
    total_mils = _total_stack_thickness_mils(physical_stack)
    lines.append(f"  Total thickness: {_format_mils_mm(total_mils)}")
    return lines


def _drill_size_label(entry: DrillTableEntry) -> str:
    diameter = _format_mils_mm(entry.diameter_mils)
    if entry.slot_length_mils is None:
        return diameter
    return f"{diameter} x {_format_mils_mm(entry.slot_length_mils)}"


def _drill_table_lines(entries: list[DrillTableEntry]) -> list[str]:
    if not entries:
        return ["  (no drilled holes found)"]

    size_labels = [_drill_size_label(entry) for entry in entries]
    size_width = max(len("size"), *(len(label) for label in size_labels))
    lines = [
        "  plating     type   " + f"{'size':<{size_width}}  pads  vias  total",
        "  ----------  -----  " + "-" * size_width + "  ----  ----  -----",
    ]
    for entry, size_label in zip(entries, size_labels, strict=True):
        lines.append(
            f"  {entry.plating_label:<10}  "
            f"{entry.drill_type:<5}  "
            f"{size_label:<{size_width}}  "
            f"{entry.pad_count:>4}  "
            f"{entry.via_count:>4}  "
            f"{entry.total_count:>5}"
        )
    return lines


def _outline_summary(pcbdoc: object) -> dict[str, object]:
    board = getattr(pcbdoc, "board", None)
    outline = getattr(board, "outline", None)
    if outline is None or getattr(outline, "vertex_count", 0) <= 0:
        raise RuntimeError("PcbDoc has no parsed board outline")

    min_x, min_y, max_x, max_y = outline.bounding_box
    width_mils = max_x - min_x
    height_mils = max_y - min_y
    return {
        "min_x_mils": min_x,
        "min_y_mils": min_y,
        "max_x_mils": max_x,
        "max_y_mils": max_y,
        "width_mils": width_mils,
        "height_mils": height_mils,
        "width_mm": _mils_to_mm(width_mils),
        "height_mm": _mils_to_mm(height_mils),
        "vertex_count": int(getattr(outline, "vertex_count", 0)),
    }


def _build_summary(
    *,
    design: AltiumDesign,
    pcbdoc: object,
    stack: ResolvedLayerStack,
) -> dict[str, object]:
    outline = _outline_summary(pcbdoc)
    hole_stats = _collect_hole_stats(pcbdoc)
    drill_table = _collect_drill_table(pcbdoc)
    width_stats = _collect_width_stats(pcbdoc)
    physical_stack = _build_physical_stack_summary(pcbdoc, stack)
    total_thickness_mils = _total_stack_thickness_mils(physical_stack)

    return {
        "project": PROJECT_FILE.name,
        "pcbdoc": Path(getattr(pcbdoc, "filepath")).name,
        "project_parameters": design.get_pcb_project_parameters(),
        "board_outline": outline,
        "holes": {
            "plated_through_holes": hole_stats.plated_through_holes,
            "plated_pad_holes": hole_stats.plated_pad_holes,
            "plated_through_vias": hole_stats.plated_through_vias,
            "non_plated_pad_holes": hole_stats.non_plated_pad_holes,
            "plated_slotted_pad_holes": hole_stats.plated_slotted_pad_holes,
            "non_plated_slotted_pad_holes": hole_stats.non_plated_slotted_pad_holes,
        },
        "drill_table": [
            {
                "plating": entry.plating_label,
                "type": entry.drill_type,
                "diameter_mils": entry.diameter_mils,
                "diameter_mm": _mils_to_mm(entry.diameter_mils),
                "slot_length_mils": entry.slot_length_mils,
                "slot_length_mm": _mils_to_mm(entry.slot_length_mils)
                if entry.slot_length_mils is not None
                else None,
                "pad_count": entry.pad_count,
                "via_count": entry.via_count,
                "total_count": entry.total_count,
            }
            for entry in drill_table
        ],
        "minimum_widths": {
            "track_mils": width_stats.track_mils,
            "arc_mils": width_stats.arc_mils,
            "overall_track_or_arc_mils": width_stats.overall_mils,
        },
        "primitives": {
            "pads": len(getattr(pcbdoc, "pads", []) or []),
            "vias": len(getattr(pcbdoc, "vias", []) or []),
            "tracks": len(getattr(pcbdoc, "tracks", []) or []),
            "arcs": len(getattr(pcbdoc, "arcs", []) or []),
        },
        "layers": {
            "resolved_layer_count": len(stack.layers),
            "physical_stack_layer_count": len(physical_stack),
            "copper_layer_count": sum(
                1 for layer in physical_stack if layer["kind"] == "copper"
            ),
            "total_thickness_mils": total_thickness_mils,
            "total_thickness_mm": _mils_to_mm(total_thickness_mils),
            "top_layer": stack.top_layer_name,
            "bottom_layer": stack.bottom_layer_name,
        },
        "physical_stack": physical_stack,
    }


def _summary_report_lines(summary: dict[str, object]) -> list[str]:
    outline = summary["board_outline"]
    holes = summary["holes"]
    drill_table = summary["drill_table"]
    widths = summary["minimum_widths"]
    layers = summary["layers"]
    primitives = summary["primitives"]
    physical_stack = summary["physical_stack"]

    if not isinstance(outline, dict):
        raise TypeError("board_outline summary must be a dict")
    if not isinstance(holes, dict):
        raise TypeError("holes summary must be a dict")
    if not isinstance(drill_table, list):
        raise TypeError("drill_table summary must be a list")
    if not isinstance(widths, dict):
        raise TypeError("minimum_widths summary must be a dict")
    if not isinstance(layers, dict):
        raise TypeError("layers summary must be a dict")
    if not isinstance(primitives, dict):
        raise TypeError("primitives summary must be a dict")
    if not isinstance(physical_stack, list):
        raise TypeError("physical_stack summary must be a list")

    lines = [
        f"Loaded project: {PROJECT_FILE.relative_to(EXAMPLES_DIR)}",
        f"Loaded PCB: {summary['pcbdoc']}",
        "",
        "Board Outline",
        (
            "  Bounding box: "
            f"({outline['min_x_mils']:.3f}, {outline['min_y_mils']:.3f}) mil "
            f"to ({outline['max_x_mils']:.3f}, {outline['max_y_mils']:.3f}) mil"
        ),
        "  Overall size: "
        + _format_size(
            float(outline["width_mils"]),
            float(outline["height_mils"]),
        ),
        f"  Outline vertices: {outline['vertex_count']}",
        "",
        "Drilled Holes",
        (
            "  Plated through holes: "
            f"{holes['plated_through_holes']} "
            f"({holes['plated_pad_holes']} pad holes + "
            f"{holes['plated_through_vias']} top-to-bottom vias)"
        ),
        f"  Non-plated through holes: {holes['non_plated_pad_holes']}",
        f"  Plated slotted pad holes: {holes['plated_slotted_pad_holes']}",
        f"  Non-plated slotted pad holes: {holes['non_plated_slotted_pad_holes']}",
        "",
        "Drill Size Table",
    ]
    drill_entries = [
        DrillTableEntry(
            plated=item["plating"] == "plated",
            slotted=item["type"] == "slot",
            diameter_mils=float(item["diameter_mils"]),
            slot_length_mils=float(item["slot_length_mils"])
            if item["slot_length_mils"] is not None
            else None,
            pad_count=int(item["pad_count"]),
            via_count=int(item["via_count"]),
        )
        for item in drill_table
        if isinstance(item, dict)
    ]
    lines.extend(_drill_table_lines(drill_entries))
    lines.extend(
        [
            "",
            "Primitive Counts",
            (
                f"  Pads: {primitives['pads']}  "
                f"Vias: {primitives['vias']}  "
                f"Tracks: {primitives['tracks']}  "
                f"Arcs: {primitives['arcs']}"
            ),
            "",
            "Minimum Copper Track/Arc Widths",
            f"  Tracks: {_format_mils_mm(widths['track_mils'])}",
            f"  Arcs: {_format_mils_mm(widths['arc_mils'])}",
            f"  Overall: {_format_mils_mm(widths['overall_track_or_arc_mils'])}",
            "",
            "Resolved Layer Stack",
            f"  Resolved layers: {layers['resolved_layer_count']}",
            f"  Physical stack layers: {layers['physical_stack_layer_count']}",
            f"  Copper layers: {layers['copper_layer_count']}",
            f"  Top/Bottom: {layers['top_layer']} / {layers['bottom_layer']}",
            "  Total thickness: "
            + _format_mils_mm(float(layers["total_thickness_mils"])),
            "",
            "ASCII Stack",
        ]
    )
    stack_entries = [item for item in physical_stack if isinstance(item, dict)]
    lines.extend(_stack_ascii_lines(stack_entries))
    return lines


def _print_summary(summary: dict[str, object]) -> None:
    for line in _summary_report_lines(summary):
        print(line)


def _write_text_report(path: Path, summary: dict[str, object]) -> None:
    path.write_text("\n".join(_summary_report_lines(summary)) + "\n", encoding="utf-8")


def main() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    pcbdoc = design.load_pcbdoc()
    stack = resolved_layer_stack_from_pcbdoc(pcbdoc)

    summary = _build_summary(design=design, pcbdoc=pcbdoc, stack=stack)
    _print_summary(summary)

    OUTPUT_JSON.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_text_report(OUTPUT_TEXT, summary)
    print()
    print(f"Wrote: {OUTPUT_JSON.relative_to(SAMPLE_DIR)}")
    print(f"Wrote: {OUTPUT_TEXT.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
