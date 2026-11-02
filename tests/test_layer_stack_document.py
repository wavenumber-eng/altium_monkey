from __future__ import annotations

import re
from pathlib import Path

import pytest

from altium_monkey.altium_layer_stack_document import (
    AltiumLayerStackDocument,
    AltiumRigidCopperLayerSpec,
    AltiumRigidDielectricLayerSpec,
)


def test_from_stackup_parses_dielectric_height_written_in_mm(tmp_path: Path) -> None:
    """Altium writes each length in the unit the author typed, so one .stackup can
    mix "3.3098mil" and "0.127mm". A height in mm must load, not become None."""
    doc = AltiumLayerStackDocument.from_rigid_stack(
        name="mixed-units",
        copper_layers=[
            AltiumRigidCopperLayerSpec(name="Top", copper_thickness_mils=1.4),
            AltiumRigidCopperLayerSpec(name="Bottom", copper_thickness_mils=1.4),
        ],
        dielectrics_between=[
            AltiumRigidDielectricLayerSpec(
                name="Dielectric 1",
                thickness_mils=5.0,
                dielectric_constant=4.29,
                material="Prepreg 2116",
            ),
        ],
    )
    text = doc.to_stackup_text()
    # the writer emits mil; restate the same height in mm, as Altium itself may
    text, replaced = re.subn(
        r"(LAYER_V8_\d+DIELHEIGHT=)5mil\b", r"\g<1>0.127mm", text, count=1
    )
    assert replaced == 1
    path = tmp_path / "mixed_units.stackup"
    path.write_text(text, encoding="utf-8")

    layers = AltiumLayerStackDocument.from_stackup(path).physical_stacks[0].layers
    heights = {
        layer.display_name: layer.dielectric_height_mils
        for layer in layers
        if (layer.family or "").lower() == "dielectric"
    }
    assert heights == {"Dielectric 1": pytest.approx(5.0)}
