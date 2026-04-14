from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    ParameterSetStyle,
    Rotation90,
    SchFontSpec,
    SchPointMils,
    make_sch_parameter,
    make_sch_parameter_set,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_differential_pairs.SchDoc"


def _add_differential_pair_directive(
    schdoc: AltiumSchDoc,
    *,
    location_mils: SchPointMils,
    name: str,
    style: ParameterSetStyle,
    orientation: Rotation90,
) -> None:
    parameter_set = make_sch_parameter_set(
        location_mils=location_mils,
        name=name,
        style=style,
        orientation=orientation,
        color=ColorValue.from_hex("#FF0000"),
    )
    schdoc.add_object(parameter_set)
    schdoc.add_object(
        make_sch_parameter(
            location_mils=location_mils,
            name="DifferentialPair",
            text="True",
            font=SchFontSpec(name="Arial", size=10),
            hidden=True,
        ),
        owner=parameter_set,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    _add_differential_pair_directive(
        schdoc,
        location_mils=SchPointMils.from_mils(1800, 5200),
        name="USB_DPAIR",
        style=ParameterSetStyle.LARGE,
        orientation=Rotation90.DEG_0,
    )
    _add_differential_pair_directive(
        schdoc,
        location_mils=SchPointMils.from_mils(4300, 5200),
        name="ETH_DPAIR",
        style=ParameterSetStyle.TINY,
        orientation=Rotation90.DEG_90,
    )
    _add_differential_pair_directive(
        schdoc,
        location_mils=SchPointMils.from_mils(6800, 5200),
        name="ADC_DPAIR",
        style=ParameterSetStyle.LARGE,
        orientation=Rotation90.DEG_270,
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(
        f"Differential-pair directives written: {len(reopened.differential_pair_directives)}"
    )
    for index, directive in enumerate(reopened.differential_pair_directives, start=1):
        child_names = (
            ", ".join(param.name for param in directive.parameters) or "<none>"
        )
        print(
            "Differential pair "
            f"{index}: "
            f"name={directive.name!r}, "
            f"style={directive.style.name}, "
            f"orientation={directive.orientation.name}, "
            f"is_differential_pair={directive.is_differential_pair()}, "
            f"children=[{child_names}]"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
