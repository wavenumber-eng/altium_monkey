from __future__ import annotations

import json
from pathlib import Path

from altium_monkey import (
    AltiumPcbFootprint,
    AltiumPcbLib,
    AltiumPcbViaStructureFeature,
    PcbIpc4761ViaType,
    PcbLayer,
    PcbViaStructureFeatureSide,
    PcbViaStructureFeatureType,
)

SAMPLE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_PCBLIB = OUTPUT_DIR / "pcblib_add_via_ipc4761_matrix.PcbLib"
OUTPUT_MANIFEST = OUTPUT_DIR / "pcblib_add_via_ipc4761_matrix.json"

FOOTPRINT_NAME = "PCBLIB_VIA_IPC4761_MATRIX"
VIA_X_STEP_MILS = 300.0
LABEL_Y_OFFSET_MILS = 115.0

IPC_TYPES = [
    PcbIpc4761ViaType.NONE,
    PcbIpc4761ViaType.TYPE_1A_TENTING,
    PcbIpc4761ViaType.TYPE_1B_TENTING,
    PcbIpc4761ViaType.TYPE_2A_TENTING_AND_COVERING,
    PcbIpc4761ViaType.TYPE_2B_TENTING_AND_COVERING,
    PcbIpc4761ViaType.TYPE_3A_PLUGGING,
    PcbIpc4761ViaType.TYPE_3B_PLUGGING,
    PcbIpc4761ViaType.TYPE_4A_PLUGGING_AND_COVERING,
    PcbIpc4761ViaType.TYPE_4B_PLUGGING_AND_COVERING,
    PcbIpc4761ViaType.TYPE_5_FILLING,
    PcbIpc4761ViaType.TYPE_6A_FILLING_AND_COVERING,
    PcbIpc4761ViaType.TYPE_6B_FILLING_AND_COVERING,
    PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING,
]

IPC_LABELS = {
    PcbIpc4761ViaType.NONE: "None",
    PcbIpc4761ViaType.TYPE_1A_TENTING: "Type1a",
    PcbIpc4761ViaType.TYPE_1B_TENTING: "Type1b",
    PcbIpc4761ViaType.TYPE_2A_TENTING_AND_COVERING: "Type2a",
    PcbIpc4761ViaType.TYPE_2B_TENTING_AND_COVERING: "Type2b",
    PcbIpc4761ViaType.TYPE_3A_PLUGGING: "Type3a",
    PcbIpc4761ViaType.TYPE_3B_PLUGGING: "Type3b",
    PcbIpc4761ViaType.TYPE_4A_PLUGGING_AND_COVERING: "Type4a",
    PcbIpc4761ViaType.TYPE_4B_PLUGGING_AND_COVERING: "Type4b",
    PcbIpc4761ViaType.TYPE_5_FILLING: "Type5",
    PcbIpc4761ViaType.TYPE_6A_FILLING_AND_COVERING: "Type6a",
    PcbIpc4761ViaType.TYPE_6B_FILLING_AND_COVERING: "Type6b",
    PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING: "Type7",
}


def _add_label(
    footprint: AltiumPcbFootprint,
    text: str,
    x_mils: float,
    y_mils: float,
) -> None:
    footprint.add_text(
        text=text,
        position_mils=(x_mils, y_mils),
        height_mils=35.0,
        stroke_width_mils=5.0,
        layer=PcbLayer.TOP_OVERLAY,
    )


def _via_summary(via) -> dict[str, object]:
    filling = via.get_ipc4761_feature(PcbViaStructureFeatureType.FILLING)
    capping = via.get_ipc4761_feature(PcbViaStructureFeatureType.CAPPING)
    return {
        "ipc4761_via_type": int(via.ipc4761_via_type),
        "propagation_delay_ps": via.propagation_delay_ps,
        "is_tent_top": via.is_tent_top,
        "is_tent_bottom": via.is_tent_bottom,
        "is_test_fab_top": via.is_test_fab_top,
        "is_assy_testpoint_bottom": via.is_assy_testpoint_bottom,
        "filling_side": None if filling is None else int(filling.side),
        "filling_material": None if filling is None else filling.material,
        "capping_side": None if capping is None else int(capping.side),
        "capping_material": None if capping is None else capping.material,
    }


def _type7_epoxy_copper_features() -> tuple[AltiumPcbViaStructureFeature, ...]:
    return (
        AltiumPcbViaStructureFeature(
            PcbViaStructureFeatureType.TENTING,
            PcbViaStructureFeatureSide.BOTH,
        ),
        AltiumPcbViaStructureFeature(
            PcbViaStructureFeatureType.COVERING,
            PcbViaStructureFeatureSide.BOTH,
        ),
        AltiumPcbViaStructureFeature(
            PcbViaStructureFeatureType.PLUGGING,
            PcbViaStructureFeatureSide.BOTH,
        ),
        AltiumPcbViaStructureFeature(
            PcbViaStructureFeatureType.FILLING,
            PcbViaStructureFeatureSide.BOTH,
            "EPOXY",
        ),
        AltiumPcbViaStructureFeature(
            PcbViaStructureFeatureType.CAPPING,
            PcbViaStructureFeatureSide.BOTH,
            "COPPER",
        ),
    )


def _build_manifest(pcblib_path: Path) -> dict[str, object]:
    parsed = AltiumPcbLib.from_file(pcblib_path)
    footprint = parsed.footprints[0]
    return {
        "pcblib": pcblib_path.name,
        "footprint": footprint.name,
        "footprint_primitive_parameters": dict(
            footprint.footprint_primitive_parameters
        ),
        "via_count": len(footprint.vias),
        "via_structure_count": len(footprint.via_structures),
        "via_structure_link_count": len(footprint.via_structure_links),
        "ipc4761_via_types": [int(via.ipc4761_via_type) for via in footprint.vias],
        "custom_type7": _via_summary(footprint.vias[-1]),
    }


def build_pcblib(output_path: Path = OUTPUT_PCBLIB) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcblib = AltiumPcbLib()
    footprint = pcblib.add_footprint(
        FOOTPRINT_NAME,
        description="PcbLib IPC-4761 via structure sample",
    )
    footprint.set_footprint_primitive_parameter(
        "TEST_PARAMETER",
        "pcblib_add_via_ipc4761_matrix",
    )

    x0 = 0.0
    y = 0.0
    for index, ipc_type in enumerate(IPC_TYPES):
        x = x0 + index * VIA_X_STEP_MILS
        _add_label(footprint, IPC_LABELS[ipc_type], x - 75.0, y + LABEL_Y_OFFSET_MILS)
        footprint.add_via(
            position_mils=(x, y),
            diameter_mils=24.0,
            hole_size_mils=10.0,
            ipc4761_via_type=ipc_type,
        )

    custom_x = x0 + len(IPC_TYPES) * VIA_X_STEP_MILS
    _add_label(footprint, "Type7 epoxy", custom_x - 110.0, y + LABEL_Y_OFFSET_MILS)
    footprint.add_via(
        position_mils=(custom_x, y),
        diameter_mils=30.0,
        hole_size_mils=12.0,
        ipc4761_via_type=PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING,
        ipc4761_features=_type7_epoxy_copper_features(),
        propagation_delay_ps=12.5,
        is_tent_top=True,
        is_tent_bottom=True,
        is_test_fab_top=True,
        is_assy_testpoint_bottom=True,
    )

    pcblib.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcblib()
    manifest = _build_manifest(output_path)
    OUTPUT_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    print(f"Wrote {OUTPUT_MANIFEST}")


if __name__ == "__main__":
    main()
