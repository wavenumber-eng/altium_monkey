from __future__ import annotations

import json
import shutil
from collections import Counter
from filecmp import cmp as files_equal
from pathlib import Path
from typing import TypeVar

from altium_monkey import (
    AltiumSchArc,
    AltiumSchDesignator,
    AltiumSchDoc,
    AltiumSchEllipse,
    AltiumSchHarnessType,
    AltiumSchLabel,
    AltiumSchLine,
    AltiumSchNoErc,
    AltiumSchNote,
    AltiumSchParameter,
    AltiumSchPolygon,
    AltiumSchPolyline,
    AltiumSchRectangle,
    ColorValue,
    LineStyle,
    LineWidth,
    NoErcSymbol,
    PinItemMode,
    SchFontSpec,
    SchSheetEntryArrowKind,
    AltiumSchSignalHarness,
)


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
HYDROSCOPE_PROJECT_DIR = ASSETS_DIR / "projects" / "hydroscope"
INPUT_PRJPCB = HYDROSCOPE_PROJECT_DIR / "Hydroscope.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC_DIR = OUTPUT_DIR / "hydroscope_clean"
OUTPUT_PRJPCB = OUTPUT_SCHDOC_DIR / INPUT_PRJPCB.name
OUTPUT_MANIFEST = OUTPUT_DIR / "clean_manifest.json"
T = TypeVar("T")

FONT_NAME = "Courier New"
BLACK = ColorValue.from_hex("#000000").win32
WHITE = ColorValue.from_hex("#FFFFFF").win32
WIRE_GRAY = ColorValue.from_hex("#434343").win32
HARNESS_CONNECTOR_FILL = ColorValue.from_hex("#D9D9D9").win32
NOTE_FILL = ColorValue.from_hex("#F3F3F3").win32
SHEET_SYMBOL_FILL = WHITE


def _font_with_size(
    record: object,
    *,
    default_size: int = 10,
    bold: bool | None = None,
) -> SchFontSpec:
    current = getattr(record, "font", None)
    current_size = current.size if current is not None else default_size
    current_bold = current.bold if current is not None else False
    current_italic = current.italic if current is not None else False
    current_underline = current.underline if current is not None else False
    current_strikeout = current.strikeout if current is not None else False
    return SchFontSpec(
        name=FONT_NAME,
        size=current_size,
        bold=current_bold if bold is None else bold,
        italic=current_italic,
        underline=current_underline,
        strikeout=current_strikeout,
    )


def _fixed_font(*, size: int = 10, bold: bool = False) -> SchFontSpec:
    return SchFontSpec(name=FONT_NAME, size=size, bold=bold)


def _set_text_font(
    record: object,
    *,
    size: int | None = None,
    default_size: int = 10,
    bold: bool | None = None,
) -> None:
    if size is None:
        setattr(
            record,
            "font",
            _font_with_size(record, default_size=default_size, bold=bold),
        )
    else:
        setattr(record, "font", _fixed_font(size=size, bold=bool(bold)))


def _replace_underscores(value: str) -> str:
    return value.replace("_", "-")


def _normalize_power_name(value: str) -> str:
    text = _replace_underscores(value)
    if "." not in text:
        return text
    text = text.replace(".", "v")
    while text.lower().endswith("v"):
        text = text[:-1]
    return text


def _set_document_style(schdoc: AltiumSchDoc) -> Counter[str]:
    counts: Counter[str] = Counter()
    if schdoc.sheet is not None:
        schdoc.sheet.area_color = WHITE
        schdoc.sheet.system_font = schdoc.font_manager.get_or_create_font(
            font_name=FONT_NAME,
            font_size=10,
        )
        counts["document_sheet_backgrounds"] += 1
        counts["document_system_fonts"] += 1
    return counts


def _objects_of_exact_type(schdoc: AltiumSchDoc, record_type: type[T]) -> list[T]:
    """
    Demonstrate the generic object query API while excluding subclasses.
    """
    return [obj for obj in schdoc.objects.of_type(record_type) if type(obj) is record_type]


def _set_pin_text_settings(schdoc: AltiumSchDoc, pin: object) -> None:
    font_id = schdoc.font_manager.get_or_create_font(
        font_name=FONT_NAME,
        font_size=10,
    )
    for settings_name in ("name_settings", "designator_settings"):
        settings = getattr(pin, settings_name)
        settings.font_mode = PinItemMode.CUSTOM
        settings.font_id = font_id
        settings.font_name = FONT_NAME
        settings.font_size = 10
        settings.font_bold = False
        settings.font_italic = False
        settings.color = BLACK


def _polygon_bounds_mils(polygon: AltiumSchPolygon) -> tuple[float, float]:
    if not polygon.vertices:
        return 0.0, 0.0
    x_values = [vertex.x_mils for vertex in polygon.vertices]
    y_values = [vertex.y_mils for vertex in polygon.vertices]
    return max(x_values) - min(x_values), max(y_values) - min(y_values)


def _is_filled_circle(ellipse: AltiumSchEllipse) -> bool:
    if not ellipse.is_solid:
        return False
    return abs(float(ellipse.radius_mils) - float(ellipse.secondary_radius_mils)) < 0.001


def _clean_component(component: object, schdoc: AltiumSchDoc) -> Counter[str]:
    counts: Counter[str] = Counter()

    for pin in getattr(component, "pins", []):
        _set_pin_text_settings(schdoc, pin)
        counts["component_pins"] += 1

    for graphic in getattr(component, "graphics", []):
        if isinstance(graphic, AltiumSchRectangle):
            bounds = graphic.bounds_mils
            if bounds.width_mils > 100 or bounds.height_mils > 100:
                graphic.color = BLACK
                graphic.area_color = WHITE
                graphic.line_width = LineWidth.SMALL
                graphic.line_style = LineStyle.SOLID
                graphic.is_solid = True
                graphic.transparent = False
                counts["component_body_rectangles"] += 1
        elif isinstance(graphic, AltiumSchLine | AltiumSchPolyline):
            graphic.color = BLACK
            counts["component_lines"] += 1
        elif isinstance(graphic, AltiumSchArc):
            graphic.color = BLACK
            counts["component_arcs"] += 1
        elif isinstance(graphic, AltiumSchPolygon):
            width_mils, height_mils = _polygon_bounds_mils(graphic)
            if width_mils <= 200 and height_mils <= 200:
                graphic.color = BLACK
                graphic.area_color = BLACK
                graphic.is_solid = True
                graphic.transparent = False
                counts["component_small_polygons"] += 1
        elif isinstance(graphic, AltiumSchEllipse) and _is_filled_circle(graphic):
            graphic.color = BLACK
            graphic.area_color = BLACK
            graphic.transparent = False
            counts["component_filled_circles"] += 1

    for parameter in getattr(component, "parameters", []):
        if isinstance(parameter, AltiumSchDesignator):
            _set_text_font(parameter, default_size=10, bold=True)
            parameter.color = BLACK
            counts["component_designators"] += 1
        elif isinstance(parameter, AltiumSchParameter) and not parameter.is_hidden:
            _set_text_font(parameter, size=10)
            parameter.color = BLACK
            counts["visible_component_parameters"] += 1

    return counts


def clean_schdoc(input_path: Path, output_path: Path) -> dict[str, int | str]:
    schdoc = AltiumSchDoc(input_path)
    counts: Counter[str] = Counter()
    counts.update(_set_document_style(schdoc))

    for component in schdoc.components:
        counts.update(_clean_component(component, schdoc))
        counts["components"] += 1

    for wire in schdoc.wires:
        wire.color = WIRE_GRAY
        counts["wires"] += 1

    for power_port in schdoc.power_ports:
        power_port.text = _normalize_power_name(power_port.text)
        power_port.override_display_string = _normalize_power_name(
            power_port.override_display_string
        )
        power_port.color = BLACK
        power_port.font = _fixed_font(size=10, bold=True)
        counts["power_ports"] += 1

    for net_label in schdoc.net_labels:
        net_label.text = _replace_underscores(net_label.text)
        net_label.color = BLACK
        net_label.font = _fixed_font(size=9, bold=True)
        counts["net_labels"] += 1

    for port in schdoc.ports:
        port.name = _replace_underscores(port.name)
        port.text = _replace_underscores(port.text)
        port.override_display_string = _replace_underscores(
            port.override_display_string
        )
        port.color = BLACK
        port.area_color = BLACK
        port.text_color = WHITE
        port.font = _fixed_font(size=10, bold=True)
        counts["ports"] += 1

    # Generic object queries are useful for broad type-based style rules.
    # Query views are read-only for membership, but returned objects are still
    # mutated in place through their normal record properties.
    for text_string in _objects_of_exact_type(schdoc, AltiumSchLabel):
        _set_text_font(text_string, default_size=10)
        counts["text_strings"] += 1

    for note in schdoc.objects.of_type(AltiumSchNote):
        _set_text_font(note, size=10)
        note.area_color = NOTE_FILL
        note.is_solid = True
        counts["notes"] += 1

    for no_erc in schdoc.objects.of_type(AltiumSchNoErc):
        no_erc.color = BLACK
        no_erc.symbol = NoErcSymbol.CROSS_SMALL
        counts["no_ercs"] += 1

    for signal_harness in schdoc.objects.of_type(AltiumSchSignalHarness):
        signal_harness.color = BLACK
        counts["signal_harnesses"] += 1

    for connector in schdoc.harness_connectors:
        connector.color = BLACK
        connector.area_color = HARNESS_CONNECTOR_FILL
        counts["harness_connectors"] += 1

    for entry in schdoc.harness_entries:
        entry.font = _fixed_font(size=10, bold=True)
        entry.text_color = BLACK
        counts["harness_entries"] += 1

    for harness_type in schdoc.objects.of_type(AltiumSchHarnessType):
        harness_type.color = BLACK
        harness_type.font = _fixed_font(size=10, bold=True)
        counts["harness_types"] += 1

    for sheet_symbol in schdoc.sheet_symbols:
        sheet_symbol.area_color = SHEET_SYMBOL_FILL
        sheet_symbol.line_width = LineWidth.SMALL
        counts["sheet_symbols"] += 1

        for entry in getattr(sheet_symbol, "entries", []):
            entry.name = _replace_underscores(entry.name)
            entry.color = BLACK
            entry.area_color = BLACK
            entry.text_color = BLACK
            entry.font = _fixed_font(size=10, bold=True)
            entry.arrow_kind = SchSheetEntryArrowKind.TRIANGLE.value
            counts["sheet_entries"] += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    schdoc.save(output_path)

    reopened = AltiumSchDoc(output_path)
    return {
        "source": str(input_path.relative_to(EXAMPLES_DIR)).replace("\\", "/"),
        "output": str(output_path.relative_to(SAMPLE_DIR)).replace("\\", "/"),
        "components": len(reopened.components),
        "wires": len(reopened.wires),
        "ports": len(reopened.ports),
        "power_ports": len(reopened.power_ports),
        "net_labels": len(reopened.net_labels),
        "signal_harnesses": len(reopened.signal_harnesses),
        "harness_connectors": len(reopened.harness_connectors),
        "sheet_symbols": len(reopened.sheet_symbols),
        "mutations": dict(sorted(counts.items())),
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_SCHDOC_DIR.mkdir(parents=True, exist_ok=True)

    input_schdocs = sorted(HYDROSCOPE_PROJECT_DIR.glob("*.SchDoc"))
    if not input_schdocs:
        raise RuntimeError(f"No SchDoc files found in {HYDROSCOPE_PROJECT_DIR}")

    documents = [
        clean_schdoc(input_path, OUTPUT_SCHDOC_DIR / input_path.name)
        for input_path in input_schdocs
    ]
    if not OUTPUT_PRJPCB.exists() or not files_equal(
        INPUT_PRJPCB, OUTPUT_PRJPCB, shallow=False
    ):
        shutil.copy2(INPUT_PRJPCB, OUTPUT_PRJPCB)
    manifest = {
        "source_project": str(HYDROSCOPE_PROJECT_DIR.relative_to(EXAMPLES_DIR)).replace(
            "\\", "/"
        ),
        "output_project": str(OUTPUT_PRJPCB.relative_to(SAMPLE_DIR)).replace("\\", "/"),
        "document_count": len(documents),
        "documents": documents,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Loaded project asset: {HYDROSCOPE_PROJECT_DIR.relative_to(EXAMPLES_DIR)}")
    print(f"Cleaned SchDocs: {len(documents)}")
    print(f"Wrote SchDocs: {OUTPUT_SCHDOC_DIR.relative_to(SAMPLE_DIR)}")
    print(f"Wrote project: {OUTPUT_PRJPCB.relative_to(SAMPLE_DIR)}")
    print(f"Wrote manifest: {OUTPUT_MANIFEST.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
