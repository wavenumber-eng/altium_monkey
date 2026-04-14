from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    NoErcSymbol,
    Rotation90,
    SchPointMils,
    make_sch_no_erc,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_no_ercs.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    # Row 1: native symbol gallery.
    for x_mils, symbol in (
        (1100, NoErcSymbol.CROSS_THIN),
        (2500, NoErcSymbol.CROSS),
        (3900, NoErcSymbol.CROSS_SMALL),
        (5300, NoErcSymbol.CHECKBOX),
        (6700, NoErcSymbol.TRIANGLE),
    ):
        schdoc.add_object(
            make_sch_no_erc(
                location_mils=SchPointMils.from_mils(x_mils, 5600),
                symbol=symbol,
                orientation=Rotation90.DEG_90,
                color=ColorValue.from_hex("#FF0000"),
            )
        )

    # Row 2: orientation gallery with the same native symbol.
    for x_mils, orientation in (
        (1500, Rotation90.DEG_0),
        (3000, Rotation90.DEG_90),
        (4500, Rotation90.DEG_180),
        (6000, Rotation90.DEG_270),
    ):
        schdoc.add_object(
            make_sch_no_erc(
                location_mils=SchPointMils.from_mils(x_mils, 3600),
                symbol=NoErcSymbol.TRIANGLE,
                orientation=orientation,
                color=ColorValue.from_hex("#FF0000"),
            )
        )

    # Row 3: active-state and partial-suppression examples.
    schdoc.add_object(
        make_sch_no_erc(
            location_mils=SchPointMils.from_mils(2200, 1800),
            symbol=NoErcSymbol.CHECKBOX,
            orientation=Rotation90.DEG_180,
            color=ColorValue.from_hex("#FF0000"),
            is_active=False,
        )
    )
    schdoc.add_object(
        make_sch_no_erc(
            location_mils=SchPointMils.from_mils(4600, 1800),
            symbol=NoErcSymbol.CROSS,
            orientation=Rotation90.DEG_90,
            color=ColorValue.from_hex("#FF0000"),
            suppress_all=False,
            error_kind_set_to_suppress="1,2",
            connection_pairs_to_suppress="0,1",
        )
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"No ERC directives written: {len(reopened.no_ercs)}")
    for index, marker in enumerate(reopened.no_ercs, start=1):
        point = marker.location_mils
        print(
            "No ERC "
            f"{index}: "
            f"location=({point.x_mils:.0f}, {point.y_mils:.0f}), "
            f"symbol={marker.symbol.name}, "
            f"orientation={marker.orientation.name}, "
            f"is_active={marker.is_active}, "
            f"suppress_all={marker.suppress_all}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
