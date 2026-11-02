from pathlib import Path

from altium_monkey import (
    AltiumDesign,
    AltiumSchDoc,
    SchHarnessConnectorSide,
    SchPointMils,
    SchRectMils,
    SheetEntrySide,
    make_sch_file_name,
    make_sch_harness_connector,
    make_sch_harness_entry,
    make_sch_harness_type,
    make_sch_port,
    make_sch_sheet_entry,
    make_sch_sheet_name,
    make_sch_sheet_symbol,
    make_sch_signal_harness,
    make_sch_wire,
)
from altium_monkey.altium_prjpcb import AltiumPrjPcb


EXAMPLES_ROOT = Path(__file__).resolve().parents[1] / "examples"
BLANK_SCHDOC = EXAMPLES_ROOT / "schdoc_add_harness_connector" / "input" / "blank.SchDoc"
RESISTOR_LIB = EXAMPLES_ROOT / "assets" / "projects" / "hydroscope" / "Hydroscope.SCHLIB"
MASTER_LEFT = SchHarnessConnectorSide.LEFT
MASTER_RIGHT = SchHarnessConnectorSide.RIGHT
PAIR_ENTRIES = (("A", 100, ""), ("B", 300, ""))
WRAP_ENTRIES = (("X", 200, "PAIR"),)


def _point(x_mils: float, y_mils: float) -> SchPointMils:
    return SchPointMils.from_mils(x_mils, y_mils)


def _harness(schdoc: AltiumSchDoc, *points: tuple[int, int]) -> None:
    schdoc.add_object(
        make_sch_signal_harness(points_mils=[_point(*point) for point in points])
    )


def _wire(schdoc: AltiumSchDoc, *points: tuple[int, int]) -> None:
    schdoc.add_object(make_sch_wire(points_mils=[_point(*point) for point in points]))


def _connector(
    schdoc: AltiumSchDoc,
    type_name: str,
    left: int,
    top: int,
    master_side: SchHarnessConnectorSide,
    entries: tuple[tuple[str, int, str], ...],
) -> None:
    # 400 x 400 mil body; the master entry sits 200 mils below the top edge.
    connector = make_sch_harness_connector(
        bounds_mils=SchRectMils.from_corners_mils(left, top, left + 400, top - 400),
        side=master_side,
        primary_position_mils=200,
    )
    for name, distance_mils, harness_type in entries:
        connector.add_entry(
            make_sch_harness_entry(
                name=name,
                distance_from_top_mils=distance_mils,
                harness_type=harness_type,
            )
        )
    connector.set_type_label(
        make_sch_harness_type(text=type_name, location_mils=_point(left, top - 450))
    )
    schdoc.add_object(connector)


def _resistor(schdoc: AltiumSchDoc, designator: str, x_mils: int, y_mils: int) -> None:
    # ERJ-3EKF1002V pin 1 ends at the placement point, pin 2 400 mils right of it.
    schdoc.add_component_from_library(
        RESISTOR_LIB, "ERJ-3EKF1002V", designator, x_mils, y_mils
    )


def _write_relay_child(path: Path) -> None:
    # IN -> WRAP connector, X entry -> X entry of a second WRAP connector -> OUT.
    # PAIR, the nested type of X, is only defined on the parent sheet.
    child = AltiumSchDoc(BLANK_SCHDOC)
    child.add_object(
        make_sch_port(
            location_mils=_point(1000, 5000),
            name="IN",
            width_mils=600,
            harness_type="WRAP",
        )
    )
    _harness(child, (1600, 5000), (2000, 5000))
    _connector(child, "WRAP", 2000, 5200, MASTER_LEFT, WRAP_ENTRIES)
    _harness(child, (2400, 5000), (3000, 5000))
    _connector(child, "WRAP", 3000, 5200, MASTER_RIGHT, WRAP_ENTRIES)
    _harness(child, (3400, 5000), (3800, 5000))
    child.add_object(
        make_sch_port(
            location_mils=_point(3800, 5000),
            name="OUT",
            width_mils=600,
            harness_type="WRAP",
        )
    )
    child.save(path)


def _add_relay_instance(parent: AltiumSchDoc, index: int, y: int) -> None:
    symbol = make_sch_sheet_symbol(
        bounds_mils=SchRectMils.from_corners_mils(5000, y + 200, 6000, y - 400)
    )
    for name, side in (("IN", SheetEntrySide.LEFT), ("OUT", SheetEntrySide.RIGHT)):
        symbol.add_entry(
            make_sch_sheet_entry(
                name=name,
                side=side,
                distance_from_top_mils=200,
                harness_type="WRAP",
            )
        )
    symbol.set_sheet_name(
        make_sch_sheet_name(text=f"RELAY{index}", location_mils=_point(5000, y + 250))
    )
    symbol.set_file_name(
        make_sch_file_name(text="relay.SchDoc", location_mils=_point(5000, y - 450))
    )
    parent.add_object(symbol)
    _harness(parent, (4600, y), (5000, y))
    _connector(parent, "WRAP", 4200, y + 200, MASTER_RIGHT, WRAP_ENTRIES)
    _harness(parent, (3800, y), (4200, y))
    _connector(parent, "PAIR", 3400, y + 200, MASTER_RIGHT, PAIR_ENTRIES)
    _harness(parent, (6000, y), (6400, y))
    _connector(parent, "WRAP", 6400, y + 200, MASTER_LEFT, WRAP_ENTRIES)
    _harness(parent, (6800, y), (7200, y))
    _connector(parent, "PAIR", 7200, y + 200, MASTER_LEFT, PAIR_ENTRIES)
    for member, pin_y in enumerate((y + 100, y - 100)):
        _resistor(parent, f"R{4 * index + member + 1}", 2800, pin_y)
        _wire(parent, (3200, pin_y), (3400, pin_y))
        output = f"R{4 * index + member + 3}"
        if index == 1 and member == 1:
            _resistor(parent, output, 7600, pin_y)  # pin touches the entry, no wire
        else:
            _resistor(parent, output, 7800, pin_y)
            _wire(parent, (7600, pin_y), (7800, pin_y))


def test_harness_members_connect_by_signal_harness_geometry(tmp_path: Path) -> None:
    _write_relay_child(tmp_path / "relay.SchDoc")
    parent = AltiumSchDoc(BLANK_SCHDOC)
    _add_relay_instance(parent, 0, 6000)
    _add_relay_instance(parent, 1, 4000)
    # A differently named port on the second instance's OUT harness.
    parent.add_object(
        make_sch_port(
            location_mils=_point(5600, 3400),
            name="LINK",
            width_mils=600,
            harness_type="WRAP",
        )
    )
    _harness(parent, (6200, 3400), (6200, 4000))
    parent.save(tmp_path / "parent.SchDoc")
    project = AltiumPrjPcb.create_minimal("relay")
    project.add_document("parent.SchDoc")
    project.add_document("relay.SchDoc")
    project.save(tmp_path / "relay.PrjPcb")

    compiled = AltiumDesign.from_prjpcb(tmp_path / "relay.PrjPcb").compile()

    multi_pin_nets = sorted(
        sorted(f"{terminal.designator}.{terminal.pin}" for terminal in net.terminals)
        for net in compiled.nets
        if net.scope == "compiled_flat" and len(net.terminals) > 1
    )
    # Each instance relays A and B on its own; the two instances never meet.
    assert multi_pin_nets == [
        ["R1.2", "R3.1"],
        ["R2.2", "R4.1"],
        ["R5.2", "R7.1"],
        ["R6.2", "R8.1"],
    ]
