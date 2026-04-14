from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    SchFontSpec,
    SchPointMils,
    SchRectMils,
    make_sch_embedded_image,
    make_sch_text_string,
)

SAMPLE_DIR = Path(__file__).resolve().parent
ASSET_PATH = SAMPLE_DIR / "assets" / "monkey.png"
OUTPUT_PATH = SAMPLE_DIR / "output" / "hello_schdoc.SchDoc"


def build_schdoc(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc()
    schdoc.add_object(
        make_sch_text_string(
            location_mils=SchPointMils.from_mils(1000, 1000),
            text="altium-monkey wuz here",
            font=SchFontSpec(name="Arial", size=18, bold=True),
            color=ColorValue.from_hex("#000000"),
        )
    )
    schdoc.add_object(
        make_sch_embedded_image(
            bounds_mils=SchRectMils.from_corners_mils(1000, 1200, 1800, 2000),
            source_path=ASSET_PATH,
            filename="monkey.png",
            keep_aspect=True,
        )
    )
    schdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_schdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
