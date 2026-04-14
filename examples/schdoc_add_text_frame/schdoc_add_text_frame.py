from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchFontSpec,
    SchHorizontalAlign,
    SchRectMils,
    make_sch_text_frame,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_text_frames.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    frame_specs = [
        {
            "bounds_mils": SchRectMils.from_corners_mils(800, 5800, 3200, 7000),
            "text": "Left aligned\nSmall border\nArial 10",
            "font": SchFontSpec(name="Arial", size=10),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#FFF4CC"),
            "text_color": ColorValue.from_hex("#000000"),
            "alignment": SchHorizontalAlign.LEFT,
            "line_width": LineWidth.SMALL,
        },
        {
            "bounds_mils": SchRectMils.from_corners_mils(3400, 5800, 5800, 7000),
            "text": "Center aligned\nMedium border\nCourier New 12",
            "font": SchFontSpec(name="Courier New", size=12),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#E8F7EC"),
            "text_color": ColorValue.from_hex("#102040"),
            "alignment": SchHorizontalAlign.CENTER,
            "line_width": LineWidth.MEDIUM,
        },
        {
            "bounds_mils": SchRectMils.from_corners_mils(6000, 5800, 8400, 7000),
            "text": "Right aligned\nLarge border\nTimes New Roman 14",
            "font": SchFontSpec(name="Times New Roman", size=14),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#E8F0FF"),
            "text_color": ColorValue.from_hex("#330000"),
            "alignment": SchHorizontalAlign.RIGHT,
            "line_width": LineWidth.LARGE,
        },
    ]

    for spec in frame_specs:
        frame = make_sch_text_frame(
            bounds_mils=spec["bounds_mils"],
            text=spec["text"],
            font=spec["font"],
            border_color=spec["border_color"],
            fill_color=spec["fill_color"],
            text_color=spec["text_color"],
            alignment=spec["alignment"],
            line_width=spec["line_width"],
            word_wrap=True,
            clip_to_rect=True,
            text_margin_mils=5,
            show_border=True,
            fill_background=True,
        )
        schdoc.add_object(frame)

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Text frames written: {len(reopened.text_frames)}")
    for index, frame in enumerate(reopened.text_frames, start=1):
        font_info = reopened.font_manager.get_font_info(frame.font_id)
        alignment_name = SchHorizontalAlign(int(frame.alignment)).name
        print(
            f"Frame {index}: {alignment_name}, {frame.line_width.name}, "
            f"{font_info['name']} {font_info['size']}, "
            f"bounds=({frame.bounds_mils.x1_mils:.0f}, {frame.bounds_mils.y1_mils:.0f})"
            f" -> ({frame.bounds_mils.x2_mils:.0f}, {frame.bounds_mils.y2_mils:.0f})"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
