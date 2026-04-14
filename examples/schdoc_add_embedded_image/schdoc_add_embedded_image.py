from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    Rotation90,
    SchRectMils,
    make_sch_embedded_image,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
ASSET_PATH = SAMPLE_DIR / "assets" / "monkey.png"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_embedded_image.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    image = make_sch_embedded_image(
        bounds_mils=SchRectMils.from_corners_mils(1500, 2500, 3900, 4900),
        source_path=ASSET_PATH,
        keep_aspect=True,
        filename="monkey.png",
        orientation=Rotation90.DEG_0,
        draw_border=True,
        line_width=LineWidth.MEDIUM,
        border_color=ColorValue.from_hex("#000000"),
    )
    schdoc.add_object(image)
    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    reopened_image = reopened.images[0]

    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Images after: {len(reopened.images)}")
    print(f"Image filename: {reopened_image.filename}")
    print(f"Image format: {reopened_image.image_format}")
    print(
        "Image bounds mils: "
        f"({reopened_image.bounds_mils.x1_mils:.0f}, {reopened_image.bounds_mils.y1_mils:.0f})"
        f" -> ({reopened_image.bounds_mils.x2_mils:.0f}, {reopened_image.bounds_mils.y2_mils:.0f})"
    )
    print(f"Image keep_aspect: {reopened_image.keep_aspect}")
    print(f"Image border width: {reopened_image.line_width.name}")
    print(f"Embedded storage entries: {len(reopened.embedded_images)}")
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
