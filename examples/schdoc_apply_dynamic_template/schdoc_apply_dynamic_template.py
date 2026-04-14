from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchFontSpec,
    SchHorizontalAlign,
    SchPointMils,
    SchRectMils,
    make_sch_embedded_image,
    make_sch_polyline,
    make_sch_text_frame,
    make_sch_text_string,
)
from altium_monkey.altium_prjpcb import AltiumPrjPcb


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHDOC = ASSETS_DIR / "schdoc" / "blank.SchDoc"
TITLE_BLOCK_IMAGE = SAMPLE_DIR / "assets" / "logo.png"
OUTPUT_DIR = SAMPLE_DIR / "output"
PROJECT_NAME = "schdoc_apply_dynamic_template"
OUTPUT_PROJECT = OUTPUT_DIR / f"{PROJECT_NAME}.PrjPcb"
PROJECT_VARIANT = "A"

ANSI_SHEET_STYLES = {
    "B": 6,
    "D": 8,
}
ANSI_BORDER_CLEARANCE_MILS = {
    "B": 200,
    "D": 300,
}

TITLE_BLOCK_WIDTH_MILS = 4400
TITLE_BLOCK_HEIGHT_MILS = 1000
PROJECT_PARAMETERS = {
    "PCB_PART_NUMBER": "WN-DEMO-001",
    "PCB_CODENAME": "dynamic-template",
    "PCB_MIXDOWN": "A",
    "STATUS": "Draft",
    "ENGINEER": "altium-monkey",
    "SCH_DATE": "2026-04-11",
    "SheetNumber": "1",
    "SheetTotal": "2",
}


def point(x_mils: float, y_mils: float) -> SchPointMils:
    return SchPointMils.from_mils(x_mils, y_mils)


def rect(
    left_mils: float,
    bottom_mils: float,
    right_mils: float,
    top_mils: float,
) -> SchRectMils:
    return SchRectMils.from_corners_mils(
        left_mils,
        bottom_mils,
        right_mils,
        top_mils,
    )


def add_polyline(
    records: list[object],
    points_mils: list[tuple[float, float]],
) -> None:
    records.append(
        make_sch_polyline(
            points_mils=[point(x, y) for x, y in points_mils],
            line_width=LineWidth.SMALL,
        )
    )


def add_header_label(
    records: list[object],
    *,
    text: str,
    x_mils: float,
    y_mils: float,
) -> None:
    records.append(
        make_sch_text_string(
            text=text,
            location_mils=point(x_mils, y_mils),
            font=SchFontSpec(name="Arial", size=8, bold=True, italic=True),
            color=ColorValue.from_hex("#434343"),
        )
    )


def add_value_frame(
    records: list[object],
    *,
    text: str,
    left_mils: float,
    bottom_mils: float,
    right_mils: float,
    top_mils: float,
) -> None:
    records.append(
        make_sch_text_frame(
            bounds_mils=rect(left_mils, bottom_mils, right_mils, top_mils),
            text=text,
            font=SchFontSpec(name="Arial", size=14, bold=True),
            alignment=SchHorizontalAlign.LEFT,
            line_width=LineWidth.SMALLEST,
            text_margin_mils=0,
            show_border=False,
            fill_background=False,
            word_wrap=True,
            clip_to_rect=True,
        )
    )


def make_title_block(
    *,
    sheet_width_mils: int,
    sheet_height_mils: int,
    title_block_image: Path,
    right_offset_mils: int,
    bottom_offset_mils: int,
) -> list[object]:
    """
    Create lower-right title-block graphics.

    The geometry is anchored from the sheet's lower-right corner so it can be
    reused across ANSI sheet sizes.
    """
    if not title_block_image.is_file():
        raise FileNotFoundError(f"Title-block image not found: {title_block_image}")
    if TITLE_BLOCK_WIDTH_MILS + right_offset_mils > sheet_width_mils:
        raise ValueError("title block is too wide for the selected sheet")
    if TITLE_BLOCK_HEIGHT_MILS + bottom_offset_mils > sheet_height_mils:
        raise ValueError("title block is too tall for the selected sheet")

    right = sheet_width_mils - right_offset_mils
    left = right - TITLE_BLOCK_WIDTH_MILS
    bottom = bottom_offset_mils
    top = bottom + TITLE_BLOCK_HEIGHT_MILS

    logo_right = left + 1200
    col_2 = logo_right + 1600
    col_3 = col_2 + 800

    row_1 = bottom + 250
    row_2 = bottom + 500
    row_3 = bottom + 750

    records: list[object] = []

    add_polyline(records, [(left, bottom), (right, bottom), (right, top)])
    add_polyline(records, [(right, top), (left, top), (left, bottom)])
    add_polyline(records, [(logo_right, bottom), (logo_right, top)])
    add_polyline(records, [(logo_right, row_1), (right, row_1)])
    add_polyline(records, [(logo_right, row_2), (right, row_2)])
    add_polyline(records, [(logo_right, row_3), (right, row_3)])
    add_polyline(records, [(col_2, bottom), (col_2, row_3)])
    add_polyline(records, [(col_3, bottom), (col_3, row_1)])
    add_polyline(records, [(col_3, row_2), (col_3, row_3)])

    logo_size = 900
    logo_left = left + (logo_right - left - logo_size) / 2
    logo_bottom = bottom + (TITLE_BLOCK_HEIGHT_MILS - logo_size) / 2
    records.append(
        make_sch_embedded_image(
            bounds_mils=rect(
                logo_left,
                logo_bottom,
                logo_left + logo_size,
                logo_bottom + logo_size,
            ),
            source_path=title_block_image,
            filename=title_block_image.name,
            keep_aspect=True,
        )
    )

    add_header_label(
        records,
        text="Document Type",
        x_mils=logo_right + 20,
        y_mils=row_3 + 160,
    )
    add_header_label(
        records,
        text="Part Number",
        x_mils=logo_right + 20,
        y_mils=row_2 + 160,
    )
    add_header_label(
        records,
        text="PCB Mixdown",
        x_mils=col_2 + 20,
        y_mils=row_2 + 160,
    )
    add_header_label(
        records,
        text="CCA Mixdown",
        x_mils=col_3 + 20,
        y_mils=row_2 + 160,
    )
    add_header_label(
        records,
        text="Codename",
        x_mils=logo_right + 20,
        y_mils=row_1 + 160,
    )
    add_header_label(
        records,
        text="Status",
        x_mils=col_2 + 20,
        y_mils=row_1 + 160,
    )
    add_header_label(
        records,
        text="Engineer",
        x_mils=logo_right + 20,
        y_mils=bottom + 160,
    )
    add_header_label(
        records,
        text="Date",
        x_mils=col_2 + 20,
        y_mils=bottom + 160,
    )
    add_header_label(
        records,
        text="Page",
        x_mils=col_3 + 20,
        y_mils=bottom + 160,
    )

    add_value_frame(
        records,
        text="SCHEMATIC DRAWING",
        left_mils=logo_right + 30,
        bottom_mils=row_3 + 20,
        right_mils=right - 30,
        top_mils=top - 80,
    )
    add_value_frame(
        records,
        text="=PCB_PART_NUMBER",
        left_mils=logo_right + 30,
        bottom_mils=row_2 + 10,
        right_mils=col_2 - 30,
        top_mils=row_3 - 90,
    )
    add_value_frame(
        records,
        text="=PCB_MIXDOWN",
        left_mils=col_2 + 30,
        bottom_mils=row_2 + 10,
        right_mils=col_3 - 30,
        top_mils=row_3 - 90,
    )
    add_value_frame(
        records,
        text="=VariantName",
        left_mils=col_3 + 30,
        bottom_mils=row_2 + 10,
        right_mils=right - 30,
        top_mils=row_3 - 90,
    )
    add_value_frame(
        records,
        text="=PCB_CODENAME",
        left_mils=logo_right + 30,
        bottom_mils=row_1 + 10,
        right_mils=col_2 - 30,
        top_mils=row_2 - 90,
    )
    add_value_frame(
        records,
        text="=STATUS",
        left_mils=col_2 + 20,
        bottom_mils=row_1 + 20,
        right_mils=right - 50,
        top_mils=row_2 - 80,
    )
    add_value_frame(
        records,
        text="=ENGINEER",
        left_mils=logo_right + 30,
        bottom_mils=bottom + 20,
        right_mils=col_2 - 30,
        top_mils=row_1 - 80,
    )
    add_value_frame(
        records,
        text="=SCH_DATE",
        left_mils=col_2 + 20,
        bottom_mils=bottom + 20,
        right_mils=col_3 - 40,
        top_mils=row_1 - 80,
    )
    add_value_frame(
        records,
        text="=SheetNumber of  =SheetTotal",
        left_mils=col_3 + 20,
        bottom_mils=bottom + 20,
        right_mils=right - 40,
        top_mils=row_1 - 80,
    )
    return records


def set_ansi_sheet(schdoc: AltiumSchDoc, sheet_size: str) -> None:
    sheet = schdoc.sheet
    sheet.sheet_style = ANSI_SHEET_STYLES[sheet_size]
    sheet.use_custom_sheet = False
    sheet.border_on = True
    sheet.title_block_on = False
    sheet.reference_zones_on = True
    sheet.reference_zone_style = 1
    sheet.custom_x_zones = 0
    sheet.custom_y_zones = 0
    sheet.system_font = schdoc.font_manager.get_or_create_font(
        font_name="Arial",
        font_size=10,
    )


def build_dynamic_template_doc(
    *,
    sheet_size: str,
    title_block_image: Path,
) -> AltiumSchDoc:
    template_doc = AltiumSchDoc()
    set_ansi_sheet(template_doc, sheet_size)
    sheet_width_mils, sheet_height_mils = template_doc.sheet.get_sheet_size_mils()
    offset_mils = ANSI_BORDER_CLEARANCE_MILS[sheet_size]

    for record in make_title_block(
        sheet_width_mils=sheet_width_mils,
        sheet_height_mils=sheet_height_mils,
        title_block_image=title_block_image,
        right_offset_mils=offset_mils,
        bottom_offset_mils=offset_mils,
    ):
        template_doc.add_object(record)

    return template_doc


def apply_dynamic_template(sheet_size: str, temp_dir: Path) -> Path:
    dynamic_template_path = temp_dir / f"dynamic_title_block_ansi_{sheet_size}.SchDot"
    template_doc = build_dynamic_template_doc(
        sheet_size=sheet_size,
        title_block_image=TITLE_BLOCK_IMAGE,
    )
    template_doc.save(dynamic_template_path)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    set_ansi_sheet(schdoc, sheet_size)
    schdoc.apply_template(
        dynamic_template_path,
        template_filename=f"dynamic_title_block_ansi_{sheet_size}.SchDot",
    )

    output_path = OUTPUT_DIR / f"blank_with_dynamic_ansi_{sheet_size}.SchDoc"
    schdoc.save(output_path)
    return output_path


def write_project(schdoc_paths: list[Path]) -> Path:
    project = AltiumPrjPcb.create_minimal(PROJECT_NAME)
    project.set_parameters(PROJECT_PARAMETERS)
    project.add_variant(PROJECT_VARIANT, current=True)
    for schdoc_path in schdoc_paths:
        project.add_document(schdoc_path.name)
    project.save(OUTPUT_PROJECT)
    return OUTPUT_PROJECT


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    with TemporaryDirectory() as temp_root:
        temp_dir = Path(temp_root)
        for sheet_size in ("B", "D"):
            output_path = apply_dynamic_template(sheet_size, temp_dir)
            output_paths.append(output_path)
            print(
                f"Wrote ANSI {sheet_size} SchDoc: {output_path.relative_to(SAMPLE_DIR)}"
            )
    project_path = write_project(output_paths)
    print(f"Wrote project: {project_path.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
