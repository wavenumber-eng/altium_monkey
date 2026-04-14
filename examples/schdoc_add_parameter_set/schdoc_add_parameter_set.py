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
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_parameter_sets.SchDoc"


def _add_net_class_parameter_set(
    schdoc: AltiumSchDoc,
    *,
    location_mils: SchPointMils,
    class_name: str,
    style: ParameterSetStyle,
    orientation: Rotation90,
) -> None:
    parameter_set = make_sch_parameter_set(
        location_mils=location_mils,
        name=class_name,
        style=style,
        orientation=orientation,
        color=ColorValue.from_hex("#FF0000"),
    )
    schdoc.add_object(parameter_set)
    schdoc.add_object(
        make_sch_parameter(
            location_mils=location_mils,
            name="ClassName",
            text=class_name,
            font=SchFontSpec(name="Arial", size=10),
            hidden=True,
        ),
        owner=parameter_set,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    _add_net_class_parameter_set(
        schdoc,
        location_mils=SchPointMils.from_mils(1300, 5600),
        class_name="USB_CLASS",
        style=ParameterSetStyle.LARGE,
        orientation=Rotation90.DEG_0,
    )
    _add_net_class_parameter_set(
        schdoc,
        location_mils=SchPointMils.from_mils(3800, 5600),
        class_name="ETH_CLASS",
        style=ParameterSetStyle.TINY,
        orientation=Rotation90.DEG_90,
    )
    _add_net_class_parameter_set(
        schdoc,
        location_mils=SchPointMils.from_mils(6300, 5600),
        class_name="ADC_CLASS",
        style=ParameterSetStyle.LARGE,
        orientation=Rotation90.DEG_180,
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Parameter sets written: {len(reopened.parameter_sets)}")
    for index, directive in enumerate(reopened.parameter_sets, start=1):
        child_names = (
            ", ".join(param.name for param in directive.parameters) or "<none>"
        )
        print(
            "Parameter set "
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
