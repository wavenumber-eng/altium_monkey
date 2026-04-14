from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from altium_monkey import (
    AltiumBoardOutline,
    AltiumPcbDoc,
    AltiumSchDoc,
    BoardOutlineVertex,
    ColorValue,
    LineWidth,
    PcbLayer,
    SchFontSpec,
    SchHorizontalAlign,
    SchPointMils,
    SchRectMils,
    make_sch_embedded_image,
    make_sch_polyline,
    make_sch_text_frame,
    make_sch_text_string,
)
from altium_monkey.altium_outjob import AltiumOutJob
from altium_monkey.altium_prjpcb import AltiumPrjPcb


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"

INPUT_SCHDOC = ASSETS_DIR / "schdoc" / "blank.SchDoc"
TITLE_BLOCK_IMAGE = SAMPLE_DIR / "assets" / "logo.png"

PROJECT_NAME = "prjpcb_make_project"
VARIANT_NAME = "A"

OUTPUT_DIR = SAMPLE_DIR / "output"
PROJECT_DIR = OUTPUT_DIR / PROJECT_NAME
PROJECT_OUTPUTS_DIR = PROJECT_DIR / "outputs"

SCHDOC_NAME = f"{PROJECT_NAME}.SchDoc"
PCBDOC_NAME = f"{PROJECT_NAME}.PcbDoc"
OUTJOB_NAME = f"{PROJECT_NAME}.OutJob"
PRJPCB_NAME = f"{PROJECT_NAME}.PrjPcb"

ANSI_D_SHEET_STYLE = 8
ANSI_D_BORDER_CLEARANCE_MILS = 300
TITLE_BLOCK_WIDTH_MILS = 5200
TITLE_BLOCK_HEIGHT_MILS = 1200

BOARD_WIDTH_MILS = 4000.0
BOARD_HEIGHT_MILS = 2500.0

PROJECT_PARAMETERS = {
    "PROJECT_TITLE": "Generated Project Example",
    "PCB_PART_NUMBER": "WN-PRJPCB-001",
    "PCB_CODENAME": "prjpcb-make-project",
    "PCB_MIXDOWN": "A",
    "STATUS": "Draft",
    "ENGINEER": "altium-monkey",
    "SCH_DATE": "2026-04-13",
    "SheetNumber": "1",
    "SheetTotal": "1",
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
    *,
    line_width: LineWidth = LineWidth.SMALL,
) -> None:
    records.append(
        make_sch_polyline(
            points_mils=[point(x, y) for x, y in points_mils],
            line_width=line_width,
        )
    )


def add_label(
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
    font_size: int = 12,
) -> None:
    records.append(
        make_sch_text_frame(
            bounds_mils=rect(left_mils, bottom_mils, right_mils, top_mils),
            text=text,
            font=SchFontSpec(name="Arial", size=font_size, bold=True),
            alignment=SchHorizontalAlign.LEFT,
            line_width=LineWidth.SMALLEST,
            text_margin_mils=0,
            show_border=False,
            fill_background=False,
            word_wrap=True,
            clip_to_rect=True,
        )
    )


def set_ansi_d_sheet(schdoc: AltiumSchDoc) -> None:
    sheet = schdoc.sheet
    sheet.sheet_style = ANSI_D_SHEET_STYLE
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


def make_title_block(
    *,
    sheet_width_mils: int,
    sheet_height_mils: int,
    title_block_image: Path,
    right_offset_mils: int,
    bottom_offset_mils: int,
) -> list[object]:
    """
    Create lower-right title-block records anchored from the sheet corner.
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
    col_2 = logo_right + 1900
    col_3 = col_2 + 900
    row_1 = bottom + 300
    row_2 = bottom + 600
    row_3 = bottom + 900
    label_offset_mils = 115
    value_top_margin_mils = 140

    records: list[object] = []
    add_polyline(records, [(left, bottom), (right, bottom), (right, top)])
    add_polyline(records, [(right, top), (left, top), (left, bottom)])
    add_polyline(records, [(logo_right, bottom), (logo_right, top)])
    add_polyline(records, [(logo_right, row_1), (right, row_1)])
    add_polyline(records, [(logo_right, row_2), (right, row_2)])
    add_polyline(records, [(logo_right, row_3), (right, row_3)])
    add_polyline(records, [(col_2, bottom), (col_2, row_3)])
    add_polyline(records, [(col_3, bottom), (col_3, row_2)])

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

    add_label(
        records,
        text="Project",
        x_mils=logo_right + 20,
        y_mils=row_3 + label_offset_mils,
    )
    add_label(
        records,
        text="Part Number",
        x_mils=logo_right + 20,
        y_mils=row_2 + label_offset_mils,
    )
    add_label(
        records, text="Codename", x_mils=col_2 + 20, y_mils=row_2 + label_offset_mils
    )
    add_label(
        records, text="Mixdown", x_mils=col_3 + 20, y_mils=row_2 + label_offset_mils
    )
    add_label(
        records, text="Status", x_mils=logo_right + 20, y_mils=row_1 + label_offset_mils
    )
    add_label(
        records, text="Engineer", x_mils=col_2 + 20, y_mils=row_1 + label_offset_mils
    )
    add_label(
        records, text="Variant", x_mils=col_3 + 20, y_mils=row_1 + label_offset_mils
    )
    add_label(
        records, text="Date", x_mils=logo_right + 20, y_mils=bottom + label_offset_mils
    )
    add_label(
        records, text="Page", x_mils=col_2 + 20, y_mils=bottom + label_offset_mils
    )

    add_value_frame(
        records,
        text="=PROJECT_TITLE",
        left_mils=logo_right + 30,
        bottom_mils=row_3 + 20,
        right_mils=right - 30,
        top_mils=top - value_top_margin_mils,
        font_size=14,
    )
    add_value_frame(
        records,
        text="=PCB_PART_NUMBER",
        left_mils=logo_right + 30,
        bottom_mils=row_2 + 20,
        right_mils=col_2 - 30,
        top_mils=row_3 - value_top_margin_mils,
    )
    add_value_frame(
        records,
        text="=PCB_CODENAME",
        left_mils=col_2 + 30,
        bottom_mils=row_2 + 20,
        right_mils=col_3 - 30,
        top_mils=row_3 - value_top_margin_mils,
    )
    add_value_frame(
        records,
        text="=PCB_MIXDOWN",
        left_mils=col_3 + 30,
        bottom_mils=row_2 + 20,
        right_mils=right - 30,
        top_mils=row_3 - value_top_margin_mils,
    )
    add_value_frame(
        records,
        text="=STATUS",
        left_mils=logo_right + 30,
        bottom_mils=row_1 + 20,
        right_mils=col_2 - 30,
        top_mils=row_2 - value_top_margin_mils,
    )
    add_value_frame(
        records,
        text="=ENGINEER",
        left_mils=col_2 + 30,
        bottom_mils=row_1 + 20,
        right_mils=col_3 - 30,
        top_mils=row_2 - value_top_margin_mils,
    )
    add_value_frame(
        records,
        text="=VariantName",
        left_mils=col_3 + 30,
        bottom_mils=row_1 + 20,
        right_mils=right - 30,
        top_mils=row_2 - value_top_margin_mils,
    )
    add_value_frame(
        records,
        text="=SCH_DATE",
        left_mils=logo_right + 30,
        bottom_mils=bottom + 20,
        right_mils=col_2 - 30,
        top_mils=row_1 - value_top_margin_mils,
    )
    add_value_frame(
        records,
        text="=SheetNumber of =SheetTotal",
        left_mils=col_2 + 30,
        bottom_mils=bottom + 20,
        right_mils=right - 30,
        top_mils=row_1 - value_top_margin_mils,
    )
    return records


def build_dynamic_template(temp_dir: Path) -> Path:
    template_path = temp_dir / "generated_ansi_d_title_block.SchDot"
    template_doc = AltiumSchDoc()
    set_ansi_d_sheet(template_doc)
    sheet_width_mils, sheet_height_mils = template_doc.sheet.get_sheet_size_mils()

    for record in make_title_block(
        sheet_width_mils=sheet_width_mils,
        sheet_height_mils=sheet_height_mils,
        title_block_image=TITLE_BLOCK_IMAGE,
        right_offset_mils=ANSI_D_BORDER_CLEARANCE_MILS,
        bottom_offset_mils=ANSI_D_BORDER_CLEARANCE_MILS,
    ):
        template_doc.add_object(record)

    template_doc.save(template_path)
    return template_path


def build_schdoc(project_dir: Path, temp_dir: Path) -> Path:
    template_path = build_dynamic_template(temp_dir)
    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    set_ansi_d_sheet(schdoc)
    schdoc.apply_template(
        template_path,
        template_filename=template_path.name,
    )
    schdoc.add_object(
        make_sch_text_string(
            text="Project generated by altium-monkey",
            location_mils=point(10000, 10000),
            font=SchFontSpec(name="Arial", size=72, bold=True),
            color=ColorValue.from_hex("#FFFFFF"),
        )
    )

    output_path = project_dir / SCHDOC_NAME
    schdoc.save(output_path)
    return output_path


def make_rectangular_outline_mils(
    width_mils: float,
    height_mils: float,
) -> AltiumBoardOutline:
    return AltiumBoardOutline(
        vertices=[
            BoardOutlineVertex.line(0.0, 0.0),
            BoardOutlineVertex.line(width_mils, 0.0),
            BoardOutlineVertex.line(width_mils, height_mils),
            BoardOutlineVertex.line(0.0, height_mils),
        ]
    )


def build_pcbdoc(project_dir: Path) -> Path:
    pcbdoc = AltiumPcbDoc()
    pcbdoc.set_board_outline(
        make_rectangular_outline_mils(BOARD_WIDTH_MILS, BOARD_HEIGHT_MILS)
    )
    pcbdoc.set_origin_to_outline_lower_left()
    pcbdoc.add_text(
        text="generated by altium-monkey",
        position_mils=(300.0, BOARD_HEIGHT_MILS - 350.0),
        height_mils=120.0,
        stroke_width_mils=14.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    output_path = project_dir / PCBDOC_NAME
    pcbdoc.save(output_path)
    return output_path


def build_outjob(project_dir: Path, schdoc_path: Path, pcbdoc_path: Path) -> Path:
    outjob = AltiumOutJob.create_minimal(PROJECT_NAME)
    outjob.config.set("OutputGroup1", "VariantName", VARIANT_NAME)
    generated_medium = outjob.add_generated_files_medium(
        "Generated Files",
        "outputs/generated",
    )
    publish_medium = outjob.add_publish_medium(
        "PDF",
        "outputs/published",
        PROJECT_NAME,
    )

    outjob.add_schematic_print(schdoc_path.name, enabled_medium=publish_medium)
    outjob.add_pcb_drawing(
        pcbdoc_path.name,
        enabled_medium=publish_medium,
        name="PCB Drawing",
    )
    outjob.add_gerber(pcbdoc_path.name, enabled_medium=generated_medium)
    outjob.add_gerber_x2(pcbdoc_path.name, enabled_medium=generated_medium)
    outjob.add_nc_drill(pcbdoc_path.name, enabled_medium=generated_medium)
    outjob.add_odb(pcbdoc_path.name, enabled_medium=generated_medium)
    outjob.add_ipc2581(pcbdoc_path.name, enabled_medium=generated_medium)
    outjob.add_pick_place(pcbdoc_path.name, enabled_medium=generated_medium)
    outjob.add_wirelist_netlist(pcbdoc_path.name, enabled_medium=generated_medium)
    outjob.add_bom_part_type(enabled_medium=generated_medium)
    outjob.add_export_step(pcbdoc_path.name, enabled_medium=generated_medium)

    output_path = project_dir / OUTJOB_NAME
    outjob.to_outjob(output_path)
    return output_path


def build_project(project_dir: Path, document_paths: list[Path]) -> Path:
    project = AltiumPrjPcb.create_minimal(PROJECT_NAME)
    project.set_parameters(PROJECT_PARAMETERS)
    project.add_variant(VARIANT_NAME, current=True)
    for document_path in document_paths:
        project.add_document(document_path.name)

    output_path = project_dir / PRJPCB_NAME
    project.save(output_path)
    return output_path


def write_manifest(
    *,
    project_dir: Path,
    schdoc_path: Path,
    pcbdoc_path: Path,
    outjob_path: Path,
    prjpcb_path: Path,
) -> Path:
    manifest_path = project_dir / "project_manifest.json"
    manifest = {
        "project": prjpcb_path.name,
        "variant": VARIANT_NAME,
        "parameters": PROJECT_PARAMETERS,
        "documents": [
            schdoc_path.name,
            pcbdoc_path.name,
            outjob_path.name,
        ],
        "outputs_dir": PROJECT_OUTPUTS_DIR.relative_to(project_dir).as_posix(),
        "outjob": {
            "file": outjob_path.name,
            "enabled_outputs": [
                "Schematic Print",
                "PCB Drawing",
                "Gerber",
                "Gerber X2",
                "NC Drill",
                "ODB++",
                "IPC-2581",
                "Pick and Place",
                "WireList Netlist",
                "BOM",
                "STEP 3D",
            ],
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def reset_project_dir() -> None:
    PROJECT_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    reset_project_dir()
    with TemporaryDirectory() as temp_root:
        temp_dir = Path(temp_root)
        schdoc_path = build_schdoc(PROJECT_DIR, temp_dir)
    pcbdoc_path = build_pcbdoc(PROJECT_DIR)
    outjob_path = build_outjob(PROJECT_DIR, schdoc_path, pcbdoc_path)
    prjpcb_path = build_project(
        PROJECT_DIR,
        [schdoc_path, pcbdoc_path, outjob_path],
    )
    manifest_path = write_manifest(
        project_dir=PROJECT_DIR,
        schdoc_path=schdoc_path,
        pcbdoc_path=pcbdoc_path,
        outjob_path=outjob_path,
        prjpcb_path=prjpcb_path,
    )

    print(f"Wrote project: {prjpcb_path.relative_to(SAMPLE_DIR)}")
    print(f"Wrote schematic: {schdoc_path.relative_to(SAMPLE_DIR)}")
    print(f"Wrote PCB: {pcbdoc_path.relative_to(SAMPLE_DIR)}")
    print(f"Wrote OutJob: {outjob_path.relative_to(SAMPLE_DIR)}")
    print(f"Wrote manifest: {manifest_path.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
