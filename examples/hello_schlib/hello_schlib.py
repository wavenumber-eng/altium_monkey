from pathlib import Path

from altium_monkey import (
    AltiumSchLib,
    PinElectrical,
    PinTextRotation,
    Rotation90,
    SchFontSpec,
    SchPointMils,
    make_sch_pin,
)

SAMPLE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SAMPLE_DIR / "output" / "hello_schlib.SchLib"
PIN_TEXT_FONT = SchFontSpec(name="Arial", size=10)
BODY_LEFT_MILS = -500
BODY_RIGHT_MILS = 500
BODY_TOP_MILS = 300
BODY_BOTTOM_MILS = -300
PIN_LENGTH_MILS = 200


def _is_100_mil_grid(value_mils: float) -> bool:
    return abs((value_mils / 100.0) - round(value_mils / 100.0)) < 1e-9


def _assert_pin_grid(symbol: object) -> None:
    for pin in getattr(symbol, "pins", []):
        hot_spot = pin.get_hot_spot()
        for field_name, value_mils in (
            ("x", pin.x_mils),
            ("y", pin.y_mils),
            ("length", pin.length_mils),
            ("hot spot x", hot_spot.x_mils),
            ("hot spot y", hot_spot.y_mils),
        ):
            if not _is_100_mil_grid(float(value_mils)):
                raise RuntimeError(
                    f"Pin {pin.designator} {field_name} is off-grid: {value_mils} mil"
                )


def build_schlib(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    schlib = AltiumSchLib()
    symbol = schlib.add_symbol("HELLO_IC_6PIN")
    symbol.set_description("Hello world 6-pin example")
    symbol.add_rectangle(
        BODY_LEFT_MILS, BODY_BOTTOM_MILS, BODY_RIGHT_MILS, BODY_TOP_MILS
    )
    symbol.add_pin(
        make_sch_pin(
            designator="1",
            name="IN_A",
            location_mils=SchPointMils.from_mils(BODY_LEFT_MILS, 100),
            orientation=Rotation90.DEG_180,
            length_mils=PIN_LENGTH_MILS,
            electrical_type=PinElectrical.PASSIVE,
            name_font=PIN_TEXT_FONT,
            designator_font=PIN_TEXT_FONT,
        )
    )
    symbol.add_pin(
        make_sch_pin(
            designator="2",
            name="IN_B",
            location_mils=SchPointMils.from_mils(BODY_LEFT_MILS, -100),
            orientation=Rotation90.DEG_180,
            length_mils=PIN_LENGTH_MILS,
            electrical_type=PinElectrical.PASSIVE,
            name_font=PIN_TEXT_FONT,
            designator_font=PIN_TEXT_FONT,
        )
    )
    symbol.add_pin(
        make_sch_pin(
            designator="3",
            name="OUT_A",
            location_mils=SchPointMils.from_mils(BODY_RIGHT_MILS, 100),
            orientation=Rotation90.DEG_0,
            length_mils=PIN_LENGTH_MILS,
            electrical_type=PinElectrical.PASSIVE,
            name_font=PIN_TEXT_FONT,
            designator_font=PIN_TEXT_FONT,
        )
    )
    symbol.add_pin(
        make_sch_pin(
            designator="4",
            name="OUT_B",
            location_mils=SchPointMils.from_mils(BODY_RIGHT_MILS, -100),
            orientation=Rotation90.DEG_0,
            length_mils=PIN_LENGTH_MILS,
            electrical_type=PinElectrical.PASSIVE,
            name_font=PIN_TEXT_FONT,
            designator_font=PIN_TEXT_FONT,
        )
    )
    symbol.add_pin(
        make_sch_pin(
            designator="5",
            name="VCC",
            location_mils=SchPointMils.from_mils(0, BODY_TOP_MILS),
            orientation=Rotation90.DEG_90,
            length_mils=PIN_LENGTH_MILS,
            electrical_type=PinElectrical.POWER,
            name_font=PIN_TEXT_FONT,
            name_rotation=PinTextRotation.VERTICAL,
            designator_font=PIN_TEXT_FONT,
            designator_rotation=PinTextRotation.VERTICAL,
        )
    )
    symbol.add_pin(
        make_sch_pin(
            designator="6",
            name="GND",
            location_mils=SchPointMils.from_mils(0, BODY_BOTTOM_MILS),
            orientation=Rotation90.DEG_270,
            length_mils=PIN_LENGTH_MILS,
            electrical_type=PinElectrical.POWER,
            name_font=PIN_TEXT_FONT,
            name_rotation=PinTextRotation.VERTICAL,
            designator_font=PIN_TEXT_FONT,
            designator_rotation=PinTextRotation.VERTICAL,
        )
    )
    symbol.add_designator("U?", 0, 520)
    symbol.add_parameter("Comment", "Hello 6-pin IC", x=0, y=-520)
    _assert_pin_grid(symbol)
    schlib.save(output_path)
    return output_path


def main() -> None:
    output_path = build_schlib()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
