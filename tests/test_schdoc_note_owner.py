from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    SchPointMils,
    SchRectMils,
    make_sch_note,
    make_sch_parameter,
)


BLANK_SCHDOC = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "schdoc_add_harness_connector"
    / "input"
    / "blank.SchDoc"
)


def test_schdoc_loads_parameter_owned_by_note(tmp_path: Path) -> None:
    # Altium attaches a placed probe directive to the object under the cursor,
    # which can be a note.
    schdoc = AltiumSchDoc(BLANK_SCHDOC)
    note = make_sch_note(
        bounds_mils=SchRectMils.from_corners_mils(1000, 2000, 3000, 1500),
        text="probe note",
    )
    schdoc.add_object(note)
    schdoc.add_object(
        make_sch_parameter(
            location_mils=SchPointMils.from_mils(1000, 2100),
            name="InstrumentProbe",
            text="NetName",
        ),
        owner=note,
    )
    output = tmp_path / "note_owned_parameter.SchDoc"
    schdoc.save(output)

    reopened = AltiumSchDoc(output)
    assert [note.text for note in reopened.notes] == ["probe note"]
