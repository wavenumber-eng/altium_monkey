from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from altium_monkey import AltiumDesign, ComponentKind
from altium_monkey.altium_component_kind import component_kind_includes_in_bom


SAMPLE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SAMPLE_DIR.parent / "assets" / "projects" / "rt_super_c1"
PROJECT_FILE = PROJECT_DIR / "RT_SUPER_C1.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"

MILS_TO_MM = 0.0254


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _natural_designator_key(designator: str) -> tuple[str, int, str]:
    match = re.match(r"([A-Za-z]+)(\d+)(.*)", designator.strip())
    if match is None:
        return (designator.upper(), -1, designator)
    prefix, number, suffix = match.groups()
    return (prefix.upper(), int(number), suffix.upper())


def _clean_parameters(parameters: dict[str, object] | None) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in (parameters or {}).items()
        if value is not None
    }


def _first_parameter(parameters: dict[str, str], *names: str) -> str:
    lowered = {key.lower(): value for key, value in parameters.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value
    return ""


def _component_kind(raw_kind: object) -> ComponentKind | None:
    try:
        return ComponentKind(raw_kind)
    except (TypeError, ValueError):
        return None


def _component_kind_name(kind: ComponentKind | None, raw_kind: object) -> str:
    if kind is None:
        return f"unknown:{raw_kind}"
    return kind.name.lower()


def _include_in_bom(kind: ComponentKind | None) -> bool:
    if kind is None:
        return True
    return component_kind_includes_in_bom(kind)


def _component_row(
    *,
    index: int,
    component: object,
    board_origin_x_mils: float,
    board_origin_y_mils: float,
) -> dict[str, object]:
    parameters = _clean_parameters(getattr(component, "parameters", None))
    kind = _component_kind(getattr(component, "component_kind", None))
    x_mils = float(component.get_x_mils(board_origin_x_mils))
    y_mils = float(component.get_y_mils(board_origin_y_mils))
    description = str(getattr(component, "description", "") or "")
    value = _first_parameter(parameters, "Value", "Comment") or description

    return {
        "index": index,
        "designator": str(getattr(component, "designator", "") or ""),
        "unique_id": str(getattr(component, "unique_id", "") or ""),
        "value": value,
        "footprint": str(getattr(component, "footprint", "") or ""),
        "description": description,
        "manufacturer": _first_parameter(parameters, "Manufacturer"),
        "manufacturer_part_number": _first_parameter(
            parameters,
            "Manufacturer Part Number",
            "MPN",
            "cad-reference",
        ),
        "jlcpcb_part": _first_parameter(parameters, "JLCPCB Part #", "jlc_pn"),
        "category": _first_parameter(parameters, "Category", "classification"),
        "source_lib_reference": str(
            getattr(component, "source_lib_reference", "") or ""
        ),
        "source_footprint_library": str(
            getattr(component, "source_footprint_library_name", "") or ""
        ),
        "layer": str(component.get_layer_normalized()),
        "x_mils": round(x_mils, 4),
        "y_mils": round(y_mils, 4),
        "x_mm": round(x_mils * MILS_TO_MM, 4),
        "y_mm": round(y_mils * MILS_TO_MM, 4),
        "rotation_degrees": round(float(component.get_rotation_degrees()), 4),
        "component_kind": _component_kind_name(
            kind, getattr(component, "component_kind", None)
        ),
        "include_in_bom": _include_in_bom(kind),
        "parameters": parameters,
    }


def _group_bom_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = (
            row["value"],
            row["footprint"],
            row["description"],
            row["manufacturer"],
            row["manufacturer_part_number"],
            row["jlcpcb_part"],
        )
        groups[key].append(row)

    grouped_rows: list[dict[str, object]] = []
    for (
        value,
        footprint,
        description,
        manufacturer,
        manufacturer_part_number,
        jlcpcb_part,
    ), members in groups.items():
        sorted_members = sorted(
            members,
            key=lambda row: _natural_designator_key(str(row["designator"])),
        )
        grouped_rows.append(
            {
                "quantity": len(sorted_members),
                "designators": [str(row["designator"]) for row in sorted_members],
                "value": value,
                "footprint": footprint,
                "description": description,
                "manufacturer": manufacturer,
                "manufacturer_part_number": manufacturer_part_number,
                "jlcpcb_part": jlcpcb_part,
                "component_indices": [row["index"] for row in sorted_members],
            }
        )

    return sorted(
        grouped_rows,
        key=lambda row: _natural_designator_key(str(row["designators"][0])),
    )


def _make_outputs() -> tuple[dict[str, object], dict[str, object]]:
    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    pcbdoc = design.load_pcbdoc()
    board_origin_x_mils = pcbdoc.board.origin_x if pcbdoc.board else 0.0
    board_origin_y_mils = pcbdoc.board.origin_y if pcbdoc.board else 0.0

    all_rows = [
        _component_row(
            index=index,
            component=component,
            board_origin_x_mils=board_origin_x_mils,
            board_origin_y_mils=board_origin_y_mils,
        )
        for index, component in enumerate(pcbdoc.components)
    ]
    all_rows.sort(key=lambda row: _natural_designator_key(str(row["designator"])))
    bom_rows = [row for row in all_rows if bool(row["include_in_bom"])]
    skipped_rows = [row for row in all_rows if not bool(row["include_in_bom"])]
    grouped_bom_rows = _group_bom_rows(bom_rows)

    source = {
        "project": "assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb",
        "pcbdoc": "assets/projects/rt_super_c1/RT_SUPER_C1.PCBdoc",
        "component_source": "PcbDoc Components6/Data with resolved designator text",
    }
    component_payload = {
        "source": source,
        "units": {
            "placement": "mils and mm",
            "rotation": "degrees",
        },
        "component_count": len(all_rows),
        "bom_component_count": len(bom_rows),
        "skipped_no_bom_component_count": len(skipped_rows),
        "components": all_rows,
    }
    bom_payload = {
        "source": source,
        "component_count": len(bom_rows),
        "group_count": len(grouped_bom_rows),
        "components": bom_rows,
        "groups": grouped_bom_rows,
    }
    return component_payload, bom_payload


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    component_payload, bom_payload = _make_outputs()

    _write_json(OUTPUT_DIR / "pcbdoc_components.json", component_payload)
    _write_json(OUTPUT_DIR / "pcbdoc_bom.json", bom_payload)

    print(f"Loaded project: {PROJECT_FILE.name}")
    print(f"PCB components: {component_payload['component_count']}")
    print(f"BOM components: {bom_payload['component_count']}")
    print(
        "Skipped no-BOM components: "
        f"{component_payload['skipped_no_bom_component_count']}"
    )
    print(f"BOM groups: {bom_payload['group_count']}")
    print("Wrote:")
    print("  output/pcbdoc_components.json")
    print("  output/pcbdoc_bom.json")


if __name__ == "__main__":
    main()
