from __future__ import annotations

import json
from pathlib import Path

from altium_monkey import AltiumDesign


SAMPLE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SAMPLE_DIR.parent / "assets" / "projects" / "rt_super_c1"
PROJECT_FILE = PROJECT_DIR / "RT_SUPER_C1.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _pnp_payload(
    *,
    source_pcbdoc: object,
    placements: list[object],
    units: str = "mm",
) -> dict[str, object]:
    return {
        "units": units,
        "source_pcbdoc": source_pcbdoc,
        "placements": [
            placement.to_json() if hasattr(placement, "to_json") else placement
            for placement in placements
        ],
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stale_csv = OUTPUT_DIR / "pick_and_place.csv"
    if stale_csv.exists():
        stale_csv.unlink()

    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    design_json = design.to_json()
    pnp_data = design_json.get("pnp")
    if not isinstance(pnp_data, dict):
        raise RuntimeError(f"Project has no PCB-backed PNP data: {PROJECT_FILE}")

    placements = pnp_data["placements"]
    if not isinstance(placements, list):
        raise RuntimeError("Design JSON PNP block does not contain placements")

    bom_only_pnp = _pnp_payload(
        source_pcbdoc=pnp_data["source_pcbdoc"],
        placements=design.to_pnp(units="mm", exclude_no_bom=True),
    )
    bom_only_placements = bom_only_pnp["placements"]
    if not isinstance(bom_only_placements, list):
        raise RuntimeError("Filtered PNP payload does not contain placements")

    _write_json(OUTPUT_DIR / "altium_design.json", design_json)
    _write_json(OUTPUT_DIR / "pick_and_place.json", pnp_data)
    _write_json(OUTPUT_DIR / "pick_and_place_exclude_no_bom.json", bom_only_pnp)

    print(f"Loaded project: {PROJECT_FILE.name}")
    print(f"Source PcbDoc: {pnp_data['source_pcbdoc']}")
    print(f"PNP placements: {len(placements)}")
    print(f"PNP placements excluding no-BOM components: {len(bom_only_placements)}")
    print("Wrote:")
    print("  output/altium_design.json")
    print("  output/pick_and_place.json")
    print("  output/pick_and_place_exclude_no_bom.json")


if __name__ == "__main__":
    main()
