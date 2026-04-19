from __future__ import annotations

from dataclasses import dataclass
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
    SheetStyle,
    make_sch_embedded_image,
    make_sch_parameter,
    make_sch_polyline,
    make_sch_text_frame,
    make_sch_text_string,
)
from altium_monkey.altium_outjob import AltiumOutJob
from altium_monkey.altium_prjpcb import AltiumPrjPcb


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent

TITLE_BLOCK_IMAGE = SAMPLE_DIR / "assets" / "logo.png"

PROJECT_NAME = "ultra-monkey"
VARIANT_NAME = "A"

OUTPUT_DIR = SAMPLE_DIR / "output"
PROJECT_DIR = OUTPUT_DIR / PROJECT_NAME
PROJECT_OUTPUTS_DIR = PROJECT_DIR / "outputs"

SCHDOC_NAME = f"{PROJECT_NAME}.SchDoc"
PCBDOC_NAME = f"{PROJECT_NAME}.PcbDoc"
OUTJOB_NAME = f"{PROJECT_NAME}.OutJob"
PRJPCB_NAME = f"{PROJECT_NAME}.PrjPcb"

ANSI_D_BORDER_CLEARANCE_MILS = 300
TITLE_BLOCK_WIDTH_MILS = 5200
TITLE_BLOCK_HEIGHT_MILS = 1200
TITLE_BLOCK_COLUMN_UNIT_MILS = 100
TITLE_BLOCK_ROW_HEIGHT_MILS = 300
TITLE_BLOCK_COLUMN_UNITS = (12, 14, 13, 7, 6)
TITLE_BLOCK_ROW_COUNT = 4
TITLE_BLOCK_VALUE_X_MARGIN_MILS = 30

BOARD_WIDTH_MILS = 4000.0
BOARD_HEIGHT_MILS = 2500.0

PROJECT_PARAMETERS = {
    "PROJECT_TITLE": "ULTRA-MONKEY",
    "CCA_PART_NUMBER": "10078",
    "CCA_CODENAME": "ULTRA-MONKEY CCA",
    "CCA_MIXDOWN": "A",
    "PCB_PART_NUMBER": "10079",
    "PCB_CODENAME": "ULTRAMONKEY",
    "PCB_MIXDOWN": "A",
    "STATUS": "DRAFT",
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


@dataclass(frozen=True)
class TitleBlockCellStyle:
    label_font: SchFontSpec
    value_font: SchFontSpec
    label_color: ColorValue
    value_color: ColorValue | None = None
    value_alignment: SchHorizontalAlign = SchHorizontalAlign.LEFT
    label_left_margin_mils: int = 20
    label_bottom_margin_mils: int = 200
    value_left_margin_mils: int = TITLE_BLOCK_VALUE_X_MARGIN_MILS
    value_right_margin_mils: int = TITLE_BLOCK_VALUE_X_MARGIN_MILS
    value_bottom_margin_mils: int = 20
    value_top_margin_mils: int = 140


@dataclass(frozen=True)
class TitleBlockGrid:
    left_mils: float
    bottom_mils: float
    column_unit_mils: int
    column_units: tuple[int, ...]
    row_height_mils: int
    row_count: int

    @property
    def width_mils(self) -> int:
        return sum(self.column_units) * self.column_unit_mils

    @property
    def height_mils(self) -> int:
        return self.row_count * self.row_height_mils

    @property
    def right_mils(self) -> float:
        return self.left_mils + self.width_mils

    @property
    def top_mils(self) -> float:
        return self.bottom_mils + self.height_mils

    def x_at_column(self, column: int) -> float:
        return self.left_mils + sum(self.column_units[:column]) * self.column_unit_mils

    def y_at_row(self, row: int) -> float:
        return self.top_mils - row * self.row_height_mils

    def cell_rect(
        self,
        *,
        row: int,
        column: int,
        row_span: int = 1,
        column_span: int = 1,
    ) -> tuple[float, float, float, float]:
        left = self.x_at_column(column)
        right = self.x_at_column(column + column_span)
        top = self.y_at_row(row)
        bottom = self.y_at_row(row + row_span)
        return left, bottom, right, top


TITLE_BLOCK_CELL_STYLE = TitleBlockCellStyle(
    label_font=SchFontSpec(name="Arial", size=8, bold=True),
    value_font=SchFontSpec(name="Arial", size=12, bold=True),
    label_color=ColorValue.from_hex("#434343"),
)

TITLE_BLOCK_PROJECT_CELL_STYLE = TitleBlockCellStyle(
    label_font=SchFontSpec(name="Arial", size=8, bold=True),
    value_font=SchFontSpec(name="Arial", size=14, bold=True),
    label_color=ColorValue.from_hex("#434343"),
)


def add_title_block_cell(
    records: list[object],
    grid: TitleBlockGrid,
    *,
    row: int,
    column: int,
    label: str,
    value: str,
    row_span: int = 1,
    column_span: int = 1,
    style: TitleBlockCellStyle = TITLE_BLOCK_CELL_STYLE,
    value_alignment: SchHorizontalAlign | None = None,
) -> None:
    left, bottom, right, top = grid.cell_rect(
        row=row,
        column=column,
        row_span=row_span,
        column_span=column_span,
    )
    records.append(
        make_sch_text_string(
            text=label,
            location_mils=point(
                left + style.label_left_margin_mils,
                bottom + style.label_bottom_margin_mils,
            ),
            font=style.label_font,
            color=style.label_color,
        )
    )
    records.append(
        make_sch_text_frame(
            bounds_mils=rect(
                left + style.value_left_margin_mils,
                bottom + style.value_bottom_margin_mils,
                right - style.value_right_margin_mils,
                top - style.value_top_margin_mils,
            ),
            text=value,
            font=style.value_font,
            text_color=style.value_color,
            alignment=(
                style.value_alignment
                if value_alignment is None
                else value_alignment
            ),
            line_width=LineWidth.SMALLEST,
            text_margin_mils=0,
            show_border=False,
            fill_background=False,
            word_wrap=True,
            clip_to_rect=True,
        )
    )


def add_title_block_grid(records: list[object], grid: TitleBlockGrid) -> None:
    add_polyline(
        records,
        [
            (grid.left_mils, grid.bottom_mils),
            (grid.right_mils, grid.bottom_mils),
            (grid.right_mils, grid.top_mils),
        ],
    )
    add_polyline(
        records,
        [
            (grid.right_mils, grid.top_mils),
            (grid.left_mils, grid.top_mils),
            (grid.left_mils, grid.bottom_mils),
        ],
    )

    logo_right = grid.x_at_column(1)
    add_polyline(records, [(logo_right, grid.bottom_mils), (logo_right, grid.top_mils)])
    for row in range(1, grid.row_count):
        y = grid.y_at_row(row)
        add_polyline(records, [(logo_right, y), (grid.right_mils, y)])

    y_row_1 = grid.y_at_row(1)
    y_row_3 = grid.y_at_row(3)
    x_col_2 = grid.x_at_column(2)
    x_col_3 = grid.x_at_column(3)
    x_col_4 = grid.x_at_column(4)
    add_polyline(records, [(x_col_2, y_row_3), (x_col_2, y_row_1)])
    add_polyline(records, [(x_col_3, grid.bottom_mils), (x_col_3, y_row_3)])
    add_polyline(records, [(x_col_3, y_row_1), (x_col_3, grid.top_mils)])
    add_polyline(records, [(x_col_4, grid.bottom_mils), (x_col_4, y_row_1)])


def set_ansi_d_sheet(schdoc: AltiumSchDoc) -> None:
    sheet = schdoc.sheet
    sheet.sheet_style = SheetStyle.D
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
    title_block_width_mils = (
        sum(TITLE_BLOCK_COLUMN_UNITS) * TITLE_BLOCK_COLUMN_UNIT_MILS
    )
    title_block_height_mils = TITLE_BLOCK_ROW_COUNT * TITLE_BLOCK_ROW_HEIGHT_MILS
    if title_block_width_mils != TITLE_BLOCK_WIDTH_MILS:
        raise ValueError("title block column units do not match the configured width")
    if title_block_height_mils != TITLE_BLOCK_HEIGHT_MILS:
        raise ValueError("title block row count does not match the configured height")
    if title_block_width_mils + right_offset_mils > sheet_width_mils:
        raise ValueError("title block is too wide for the selected sheet")
    if title_block_height_mils + bottom_offset_mils > sheet_height_mils:
        raise ValueError("title block is too tall for the selected sheet")

    right = sheet_width_mils - right_offset_mils
    left = right - title_block_width_mils
    bottom = bottom_offset_mils
    grid = TitleBlockGrid(
        left_mils=left,
        bottom_mils=bottom,
        column_unit_mils=TITLE_BLOCK_COLUMN_UNIT_MILS,
        column_units=TITLE_BLOCK_COLUMN_UNITS,
        row_height_mils=TITLE_BLOCK_ROW_HEIGHT_MILS,
        row_count=TITLE_BLOCK_ROW_COUNT,
    )

    records: list[object] = []
    add_title_block_grid(records, grid)

    logo_size = 900
    logo_left, logo_bottom, logo_right, logo_top = grid.cell_rect(
        row=0,
        column=0,
        row_span=TITLE_BLOCK_ROW_COUNT,
    )
    logo_image_left = logo_left + (logo_right - logo_left - logo_size) / 2
    logo_image_bottom = logo_bottom + (logo_top - logo_bottom - logo_size) / 2
    records.append(
        make_sch_embedded_image(
            bounds_mils=rect(
                logo_image_left,
                logo_image_bottom,
                logo_image_left + logo_size,
                logo_image_bottom + logo_size,
            ),
            source_path=title_block_image,
            filename=title_block_image.name,
            keep_aspect=True,
        )
    )

    add_title_block_cell(
        records,
        grid,
        row=0,
        column=1,
        column_span=2,
        label="PAGE TITLE",
        value='="Schematic Page"',
        style=TITLE_BLOCK_PROJECT_CELL_STYLE,
    )
    add_title_block_cell(
        records,
        grid,
        row=0,
        column=3,
        column_span=2,
        label="STATUS",
        value="=STATUS",
        style=TITLE_BLOCK_PROJECT_CELL_STYLE,
    )
    add_title_block_cell(
        records,
        grid,
        row=1,
        column=1,
        label="CCA PART NUMBER",
        value="=CCA_PART_NUMBER",
    )
    add_title_block_cell(
        records,
        grid,
        row=1,
        column=2,
        column_span=2,
        label="CCA CODENAME",
        value="=CCA_CODENAME",
    )
    add_title_block_cell(
        records,
        grid,
        row=1,
        column=4,
        label="CCA MIXDOWN",
        value="=CCA_MIXDOWN",
        value_alignment=SchHorizontalAlign.CENTER,
    )
    add_title_block_cell(
        records,
        grid,
        row=2,
        column=1,
        label="PCB PART NUMBER",
        value="=PCB_PART_NUMBER",
    )
    add_title_block_cell(
        records,
        grid,
        row=2,
        column=2,
        column_span=2,
        label="PCB CODENAME",
        value="=PCB_CODENAME",
    )
    add_title_block_cell(
        records,
        grid,
        row=2,
        column=4,
        label="PCB MIXDOWN",
        value="=PCB_MIXDOWN",
        value_alignment=SchHorizontalAlign.CENTER,
    )
    add_title_block_cell(
        records,
        grid,
        row=3,
        column=1,
        column_span=2,
        label="ENGINEER",
        value="=ENGINEER",
    )
    add_title_block_cell(
        records,
        grid,
        row=3,
        column=3,
        label="DATE",
        value="=SCH_DATE",
        value_alignment=SchHorizontalAlign.CENTER,
    )
    add_title_block_cell(
        records,
        grid,
        row=3,
        column=4,
        label="PAGE NUMBER",
        value="=SheetNumber of =SheetTotal",
        value_alignment=SchHorizontalAlign.CENTER,
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
    schdoc = AltiumSchDoc()
    schdoc.apply_template(
        template_path,
        template_filename=template_path.name,
        apply_visual_sheet_settings=True,
    )
    schdoc.add_object(
        make_sch_parameter(
            location_mils=point(0, 0),
            name="Schematic Page",
            text=PROJECT_NAME + " Top Level",
            font=SchFontSpec(name="Arial", size=10),
            hidden=True,
        )
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
