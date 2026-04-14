from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2

from altium_monkey import (
    AltiumPrjPcbBuilder,
    AltiumSchDoc,
    AltiumSchDesignator,
    AltiumSchLib,
    Rotation90,
)


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHDOC = ASSETS_DIR / "schdoc" / "blank.SchDoc"
INPUT_SCHLIB = ASSETS_DIR / "schlib" / "R_2P.Schlib"
INPUT_PCBLIB = ASSETS_DIR / "pcblib" / "R0603_0.55MM_MD.PcbLib"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "schdoc_insert_dblib_style.SchDoc"
OUTPUT_PCBLIB = OUTPUT_DIR / INPUT_PCBLIB.name
OUTPUT_PROJECT = OUTPUT_DIR / "schdoc_insert_dblib_style.PrjPcb"
SYMBOL_NAME = "R_2P"
FOOTPRINT_MODEL_NAME = "R0603_0.55MM_MD"
FOOTPRINT_LIBRARY_NAME = "R0603_0.55MM_MD"
PROJECT_NAME = "schdoc_insert_dblib_style"
OHM = "\N{GREEK CAPITAL LETTER OMEGA}"


@dataclass(frozen=True)
class ResistorDbRow:
    """Application-level DB data resolved before schematic placement."""

    designator: str
    symbol_name: str
    value: str
    manufacturer_part_number: str
    description: str
    jlcpcb_part_number: str
    model_name: str
    library_name: str


@dataclass(frozen=True)
class ResistorSchematicPlacement:
    """Schematic placement and visible text locations, expressed in mils."""

    x_mils: int
    y_mils: int
    designator_x_mils: int
    designator_y_mils: int
    comment_x_mils: int
    comment_y_mils: int
    orientation: Rotation90 = Rotation90.DEG_90


RESISTORS: tuple[ResistorDbRow, ...] = (
    ResistorDbRow(
        designator="R1",
        symbol_name=SYMBOL_NAME,
        value=f"1k{OHM}",
        manufacturer_part_number="RC0603FR-071KL",
        description=f"1k{OHM} 1% 1/10W 0603",
        jlcpcb_part_number="C22548",
        model_name=FOOTPRINT_MODEL_NAME,
        library_name=FOOTPRINT_LIBRARY_NAME,
    ),
    ResistorDbRow(
        designator="R2",
        symbol_name=SYMBOL_NAME,
        value=f"10k{OHM}",
        manufacturer_part_number="RC0603FR-0710KL",
        description=f"10k{OHM} 1% 1/10W 0603",
        jlcpcb_part_number="C98220",
        model_name=FOOTPRINT_MODEL_NAME,
        library_name=FOOTPRINT_LIBRARY_NAME,
    ),
    ResistorDbRow(
        designator="R3",
        symbol_name=SYMBOL_NAME,
        value=f"100k{OHM}",
        manufacturer_part_number="RC0603FR-07100KL",
        description=f"100k{OHM} 1% 1/10W 0603",
        jlcpcb_part_number="C14675",
        model_name=FOOTPRINT_MODEL_NAME,
        library_name=FOOTPRINT_LIBRARY_NAME,
    ),
)


SCHEMATIC_PLACEMENTS: tuple[ResistorSchematicPlacement, ...] = (
    ResistorSchematicPlacement(
        x_mils=3000,
        y_mils=3600,
        designator_x_mils=3030,
        designator_y_mils=3810,
        comment_x_mils=3030,
        comment_y_mils=3710,
    ),
    ResistorSchematicPlacement(
        x_mils=4000,
        y_mils=3600,
        designator_x_mils=4040,
        designator_y_mils=3810,
        comment_x_mils=4030,
        comment_y_mils=3710,
    ),
    ResistorSchematicPlacement(
        x_mils=5000,
        y_mils=3600,
        designator_x_mils=5050,
        designator_y_mils=3800,
        comment_x_mils=5030,
        comment_y_mils=3710,
    ),
)


def _component_designator(component: object) -> str:
    for parameter in getattr(component, "parameters", []):
        if isinstance(parameter, AltiumSchDesignator):
            return parameter.text or ""
    return ""


def _component_parameters(component: object) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for parameter in getattr(component, "parameters", []):
        name = getattr(parameter, "name", "")
        if not name or isinstance(parameter, AltiumSchDesignator):
            continue
        parameters[name] = getattr(parameter, "text", "")
    return parameters


def _insert_resistor(
    schdoc: AltiumSchDoc,
    row: ResistorDbRow,
    schematic_placement: ResistorSchematicPlacement,
) -> None:
    # The symbol pins land on 100 mil schematic-grid hotspots with these origins.
    component = schdoc.add_component_from_library(
        library_path=INPUT_SCHLIB,
        symbol_name=row.symbol_name,
        designator=row.designator,
        x=schematic_placement.x_mils,
        y=schematic_placement.y_mils,
        orientation=schematic_placement.orientation,
    )
    component.design_item_id = row.symbol_name
    component.part_id_locked = False
    component.add_parameter("Value", row.value)
    component.add_parameter("Manufacturer", "Yageo")
    component.add_parameter("Manufacturer Part Number", row.manufacturer_part_number)
    component.add_parameter("Description", row.description)
    component.add_parameter("JLCPCB Part #", row.jlcpcb_part_number)
    component.add_footprint(
        model_name=row.model_name,
        description="0603 resistor footprint",
        library_name=row.library_name,
    )
    component.set_designator_style(
        x=schematic_placement.designator_x_mils,
        y=schematic_placement.designator_y_mils,
        font_name="Arial",
        font_size=12,
        bold=True,
    )
    component.set_comment_style(
        x=schematic_placement.comment_x_mils,
        y=schematic_placement.comment_y_mils,
        font_name="Arial",
        font_size=10,
        bold=False,
    )

    for pin in component.pins:
        x_mils, y_mils = component.get_pin_hotspot(pin.designator)
        if x_mils % 100 != 0 or y_mils % 100 != 0:
            raise RuntimeError(
                f"{row.designator} pin {pin.designator} hotspot is off-grid: "
                f"({x_mils}, {y_mils}) mil"
            )


def _write_project() -> None:
    AltiumPrjPcbBuilder(PROJECT_NAME).add_schdoc(OUTPUT_SCHDOC.name).add_pcblib(
        OUTPUT_PCBLIB.name
    ).save(OUTPUT_PROJECT)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    source_library = AltiumSchLib(INPUT_SCHLIB)
    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    for row, schematic_placement in zip(RESISTORS, SCHEMATIC_PLACEMENTS, strict=True):
        _insert_resistor(
            schdoc,
            row,
            schematic_placement=schematic_placement,
        )

    schdoc.save(OUTPUT_SCHDOC)
    copy2(INPUT_PCBLIB, OUTPUT_PCBLIB)
    _write_project()

    reopened_schdoc = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Input SchLib symbols: {[symbol.name for symbol in source_library.symbols]}")
    print(f"Inserted components: {len(reopened_schdoc.components)}")
    for component in reopened_schdoc.components:
        params = _component_parameters(component)
        print(
            f"{_component_designator(component)} {component.lib_reference} "
            f"value={params.get('Value', '')} "
            f"mpn={params.get('Manufacturer Part Number', '')} "
            f"footprint={component.footprint}"
        )
    print(f"Wrote SchDoc: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")
    print(f"Copied PcbLib: {OUTPUT_PCBLIB.relative_to(SAMPLE_DIR)}")
    print(f"Wrote project: {OUTPUT_PROJECT.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
