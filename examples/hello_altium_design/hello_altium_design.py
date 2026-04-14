from __future__ import annotations

import json
from pathlib import Path

from altium_monkey import AltiumDesign


SAMPLE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SAMPLE_DIR.parent / "assets" / "projects" / "rt_super_c1"
PROJECT_FILE = PROJECT_DIR / "RT_SUPER_C1.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"
VARIANT_BOM_DIR = OUTPUT_DIR / "variant_boms"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _safe_variant_filename(variant: str) -> str:
    safe = [
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in variant.strip()
    ]
    return "".join(safe) or "default"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VARIANT_BOM_DIR.mkdir(parents=True, exist_ok=True)
    for stale_variant_bom in VARIANT_BOM_DIR.glob("*.json"):
        stale_variant_bom.unlink()
    stale_variant_bundle = OUTPUT_DIR / "variant_boms.json"
    if stale_variant_bundle.exists():
        stale_variant_bundle.unlink()

    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    design_json = design.to_json()
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
    _write_json(OUTPUT_DIR / "netlist.json", netlist.to_json())
    _write_json(OUTPUT_DIR / "bom_all.json", design.to_bom())
    for variant in variants:
        variant_filename = _safe_variant_filename(variant) + ".json"
        _write_json(VARIANT_BOM_DIR / variant_filename, design.to_bom(variant=variant))
    (OUTPUT_DIR / "wirelist.txt").write_text(design.to_wirelist(), encoding="utf-8")

    print("Wrote:")
    print("  output/project_summary.json")
    print("  output/altium_design.json")
    print("  output/netlist.json")
    print("  output/wirelist.txt")
    print("  output/bom_all.json")
    for variant in variants:
        variant_filename = _safe_variant_filename(variant) + ".json"
        print(f"  output/variant_boms/{variant_filename}")


if __name__ == "__main__":
    main()
