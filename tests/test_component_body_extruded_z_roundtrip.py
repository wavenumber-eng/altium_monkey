"""Regression test: add_component_body() must set MODEL.EXTRUDED.MINZ/MAXZ.

Altium's "Extruded" 3D model type reads its render height from
MODEL.EXTRUDED.MINZ/MAXZ, not from OVERALLHEIGHT (that field is legacy/BOM
metadata that Altium's own PCB Library editor still displays, but does not
use to drive the actual 3D geometry for this model type). Both
`AltiumPcbFootprint.add_component_body()` (library) and
`AltiumPcbDoc.add_component_body()` (board) previously left
`model_extruded_min_z`/`model_extruded_max_z` at their class default of 0,
and the property-export logic only emits a property when the value is
truthy -- so a plain extruded box (no `model=` STEP) silently omitted both
properties from the saved file. Altium then fell back to an undocumented
~1000mil extrusion height regardless of the authored overall_height_mils,
making the body render dramatically taller than intended.

Found 2026-08-31 via a real ECS-271.2-18-30B-GM-TR crystal footprint: this
library's own reader reported a consistent, correct-looking 0.85mm height
(both the numeric field and the OVERALLHEIGHT property text), yet Altium's
PCB Library editor showed Overall Height as 25.4mm (1000mil) for the same
body -- confirmed by diffing the saved file's properties before/after a
user manually re-entering the height in Altium, which is what added the
previously-absent MODEL.EXTRUDED.MAXZ property.

`add_extruded_3d_body()` (both the library and board variants) already
worked around this by manually setting the two fields after calling
`add_component_body()` -- this test locks in the fix at the lower-level
function directly, so any other caller of `add_component_body()` gets
correct behavior too.
"""

from pathlib import Path

from altium_monkey import AltiumPcbDoc, AltiumPcbLib

_OUTLINE_MILS = [(-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)]


def test_pcblib_component_body_sets_extruded_minz_maxz(tmp_path: Path) -> None:
    pcblib = AltiumPcbLib()
    footprint = pcblib.add_footprint("EXTRUDED_BODY_TEST")
    footprint.add_component_body(
        outline_points_mils=_OUTLINE_MILS,
        overall_height_mils=33.4646,
        standoff_height_mils=0.0,
    )

    out_path = tmp_path / "extruded_body_test.PcbLib"
    pcblib.save(out_path)

    reloaded = AltiumPcbLib.from_file(out_path)
    body = reloaded.find_footprint("EXTRUDED_BODY_TEST").component_bodies[0]

    assert body.properties.get("MODEL.EXTRUDED.MAXZ", "").strip("\x00") == (
        "33.4646mil"
    )
    assert body.model_extruded_max_z == 334646
    assert body.model_extruded_min_z == 0


def test_pcbdoc_component_body_sets_extruded_minz_maxz(tmp_path: Path) -> None:
    pcbdoc = AltiumPcbDoc()
    pcbdoc.add_component_body(
        outline_points_mils=_OUTLINE_MILS,
        overall_height_mils=33.4646,
        standoff_height_mils=0.0,
    )

    out_path = tmp_path / "extruded_body_test.PcbDoc"
    pcbdoc.save(out_path)

    reloaded = AltiumPcbDoc.from_file(out_path)
    body = reloaded.component_bodies[0]

    assert body.properties.get("MODEL.EXTRUDED.MAXZ", "").strip("\x00") == (
        "33.4646mil"
    )
    assert body.model_extruded_max_z == 334646
    assert body.model_extruded_min_z == 0
