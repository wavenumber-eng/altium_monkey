"""PcbLib custom pads: a region's PADINDEX counts all primitives, not only pads.

A custom pad is an anchor pad plus a copper region, and the region names its pad by
PADINDEX. Altium counts every primitive of the footprint to find that pad. Two custom
pads are stored as:

    1 = pad 1   2 = region of pad 1   3 = pad 2   4 = region of pad 2

so the region of pad 2 must say PADINDEX 3. With PADINDEX 2 Altium finds the region
of pad 1 and draws no copper for pad 2.
"""

from __future__ import annotations

from pathlib import Path

from altium_monkey import AltiumPcbLib

ARROW_RIGHT = [(-30, -10), (0, -10), (0, -20), (30, 0), (0, 20), (0, 10), (-30, 10)]
ARROW_LEFT = [(-x, y) for x, y in reversed(ARROW_RIGHT)]


def test_custom_pad_padindex_counts_all_primitives(tmp_path: Path) -> None:
    pcblib = AltiumPcbLib()
    footprint = pcblib.add_footprint("TWO_CUSTOM_PADS")
    footprint.add_custom_pad(
        designator="1", position_mils=(-50, 0), outline_points_mils=ARROW_RIGHT
    )
    footprint.add_custom_pad(
        designator="2", position_mils=(50, 0), outline_points_mils=ARROW_LEFT
    )
    pcblib.save(tmp_path / "two_custom_pads.PcbLib")

    parsed = AltiumPcbLib.from_file(tmp_path / "two_custom_pads.PcbLib").footprints[0]

    assert [region.properties["PADINDEX"] for region in parsed.regions] == ["1", "3"]


def test_reader_finds_custom_pad_by_primitive_position(tmp_path: Path) -> None:
    # Written the way Altium writes it: the region of pad 2 says PADINDEX 3.
    # A regular pad 3 follows; counting pads only would give it pad 2's arrow.
    pcblib = AltiumPcbLib()
    footprint = pcblib.add_footprint("ALTIUM_PADINDEX")
    footprint.add_custom_pad(
        designator="1",
        position_mils=(-50, 0),
        outline_points_mils=ARROW_RIGHT,
        pad_index=1,
    )
    footprint.add_custom_pad(
        designator="2",
        position_mils=(50, 0),
        outline_points_mils=ARROW_LEFT,
        pad_index=3,
    )
    footprint.add_pad(
        designator="3", position_mils=(0, 60), width_mils=20, height_mils=20
    )
    pcblib.save(tmp_path / "altium_padindex.PcbLib")

    parsed = AltiumPcbLib.from_file(tmp_path / "altium_padindex.PcbLib").footprints[0]
    pads = {pad.designator: pad for pad in parsed.pads}

    assert pads["2"].custom_shape is not None
    assert pads["3"].custom_shape is None
