from __future__ import annotations

import json
from pathlib import Path

from altium_monkey import AltiumDesign


SAMPLE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SAMPLE_DIR.parent / "assets" / "projects" / "rt_super_c1"
PROJECT_FILE = PROJECT_DIR / "RT_SUPER_C1.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"
VARIANT_BOM_DIR = OUTPUT_DIR / "variant_boms"
PHYSICAL_SVG_DIR = OUTPUT_DIR / "physical_svgs"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _safe_variant_filename(variant: str) -> str:
    safe = [
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in variant.strip()
    ]
    return "".join(safe) or "default"


def _safe_output_stem(value: str) -> str:
    safe = [
        char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip()
    ]
    return "".join(safe) or "page"


def _compiled_net_name_examples(design_json: dict) -> list[dict]:
    examples: list[dict] = []
    for net in design_json.get("nets", []):
        aliases = list(net.get("aliases") or [])
        name_sources = list(net.get("name_sources") or [])
        if not aliases and len(name_sources) <= 1:
            continue
        examples.append(
            {
                "uid": net.get("uid", ""),
                "winning_name": net.get("name", ""),
                "alternate_names": aliases,
                "name_sources": name_sources,
            }
        )
    return examples


def _compiled_schematic_graph_summary(design_json: dict) -> dict:
    graph = design_json["compiled_schematic_graph"]
    metadata_by_page = {
        row["page_occurrence_ref"]: row for row in design_json["physical_page_metadata"]
    }
    component_counts: dict[str, int] = {}
    net_counts: dict[str, int] = {}
    terminal_counts: dict[str, int] = {}
    drawing_link_counts: dict[str, int] = {}
    for collection, counts in (
        (graph["component_occurrences"], component_counts),
        (graph["local_net_occurrences"], net_counts),
        (graph["terminal_occurrences"], terminal_counts),
        (graph["graphical_artifact_links"], drawing_link_counts),
    ):
        for row in collection:
            page_ref = row["page_occurrence_ref"]
            counts[page_ref] = counts.get(page_ref, 0) + 1

    pages: list[dict] = []
    for page in graph["page_occurrences"]:
        page_ref = page["id"]
        metadata = metadata_by_page.get(page_ref, {})
        pages.append(
            {
                "page_occurrence_ref": page_ref,
                "display_name": page.get("display_name", ""),
                "sheet_number": page.get("sheet_number", ""),
                "instance_order": page.get("instance_order", 0),
                "unit_occurrence_ref": page.get("unit_occurrence_ref", ""),
                "page_definition_ref": page.get("page_definition_ref", ""),
                "physical_instance_path": metadata.get("physical_instance_path", ""),
                "channel_index": metadata.get("channel_index", 0),
                "room_name": metadata.get("physical_room_name", ""),
                "component_count": component_counts.get(page_ref, 0),
                "local_net_count": net_counts.get(page_ref, 0),
                "terminal_count": terminal_counts.get(page_ref, 0),
                "drawing_link_count": drawing_link_counts.get(page_ref, 0),
            }
        )
    return {
        "schema": design_json.get("schema", ""),
        "graph_schema": graph["schema"],
        "identity_namespace": graph["identity_namespace"],
        "drawing_identity_rule": ("page_occurrence_ref + artifact_key + element_id"),
        "page_occurrence_count": len(pages),
        "pages": pages,
    }


def _write_physical_svgs(design: AltiumDesign, design_json: dict) -> dict:
    PHYSICAL_SVG_DIR.mkdir(parents=True, exist_ok=True)
    for stale_svg in PHYSICAL_SVG_DIR.glob("*.svg"):
        stale_svg.unlink()

    pages: list[dict] = []
    graph = design_json["compiled_schematic_graph"]
    metadata_by_page = {
        row["page_occurrence_ref"]: row for row in design_json["physical_page_metadata"]
    }
    for ordinal, page in enumerate(graph["page_occurrences"]):
        page_id = str(page["id"])
        if not page_id:
            continue
        metadata = metadata_by_page.get(page_id, {})
        page_name = str(
            metadata.get("physical_instance_path")
            or page.get("display_name")
            or f"page_{ordinal}"
        )
        output_name = f"{ordinal:02d}_{_safe_output_stem(page_name)}.svg"
        output_path = PHYSICAL_SVG_DIR / output_name
        output_path.write_text(design.to_physical_svg(page_id), encoding="utf-8")
        pages.append(
            {
                "page_occurrence_ref": page_id,
                "physical_instance_path": metadata.get("physical_instance_path", ""),
                "display_name": page.get("display_name", ""),
                "svg_path": f"physical_svgs/{output_name}",
                "identity_rule": ("page_occurrence_ref + artifact_key + element_id"),
            }
        )

    return {
        "schema": design_json.get("schema", ""),
        "physical_svg_count": len(pages),
        "pages": pages,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VARIANT_BOM_DIR.mkdir(parents=True, exist_ok=True)
    for stale_variant_bom in VARIANT_BOM_DIR.glob("*.json"):
        stale_variant_bom.unlink()
    stale_variant_bundle = OUTPUT_DIR / "variant_boms.json"
    if stale_variant_bundle.exists():
        stale_variant_bundle.unlink()
    stale_wirelist = OUTPUT_DIR / "wirelist.txt"
    if stale_wirelist.exists():
        stale_wirelist.unlink()

    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    design_json = design.to_json(include_indexes=True)
    graph_summary = _compiled_schematic_graph_summary(design_json)
    physical_svg_manifest = _write_physical_svgs(design, design_json)
    project = design.project
    if project is None:
        raise RuntimeError(
            "AltiumDesign.from_prjpcb(...) did not retain project context"
        )

    netlist = design.to_netlist()
    variants = design.get_variants()
    current_variant = project.get_current_variant()
    schdoc_paths = project.get_schdoc_paths()
    pcbdoc_paths = design.get_pcbdoc_paths()

    print(f"Loaded project: {PROJECT_FILE.name}")
    print(f"Current variant: {current_variant or '(none)'}")
    print(
        f"Variants ({len(variants)}): {', '.join(variants) if variants else '(none)'}"
    )

    print(f"Schematic documents ({len(schdoc_paths)}):")
    for path in schdoc_paths:
        print(f"  {path.name}")

    print(f"PCB documents ({len(pcbdoc_paths)}):")
    for path in pcbdoc_paths:
        print(f"  {path.name}")

    print(f"Components: {len(netlist.components)}")
    print(f"Nets: {len(netlist.nets)}")

    summary = {
        "project_file": PROJECT_FILE.name,
        "current_variant": current_variant,
        "variants": variants,
        "schematic_documents": [path.name for path in schdoc_paths],
        "pcb_documents": [path.name for path in pcbdoc_paths],
        "pcb_project_parameters": design.get_pcb_project_parameters(),
        "component_count": len(netlist.components),
        "net_count": len(netlist.nets),
    }

    _write_json(OUTPUT_DIR / "project_summary.json", summary)
    _write_json(OUTPUT_DIR / "altium_design.json", design_json)
    _write_json(OUTPUT_DIR / "compiled_schematic_graph_summary.json", graph_summary)
    _write_json(OUTPUT_DIR / "physical_svg_manifest.json", physical_svg_manifest)
    _write_json(OUTPUT_DIR / "netlist.json", netlist.to_json())
    _write_json(
        OUTPUT_DIR / "compiled_net_name_examples.json",
        _compiled_net_name_examples(design_json),
    )
    _write_json(OUTPUT_DIR / "bom_all.json", design.to_bom())
    for variant in variants:
        variant_filename = _safe_variant_filename(variant) + ".json"
        _write_json(VARIANT_BOM_DIR / variant_filename, design.to_bom(variant=variant))

    print("Wrote:")
    print("  output/project_summary.json")
    print("  output/altium_design.json")
    print("  output/compiled_schematic_graph_summary.json")
    print("  output/physical_svg_manifest.json")
    print("  output/netlist.json")
    print("  output/compiled_net_name_examples.json")
    print("  output/bom_all.json")
    for page in physical_svg_manifest["pages"]:
        print(f"  output/{page['svg_path']}")
    for variant in variants:
        variant_filename = _safe_variant_filename(variant) + ".json"
        print(f"  output/variant_boms/{variant_filename}")


if __name__ == "__main__":
    main()
