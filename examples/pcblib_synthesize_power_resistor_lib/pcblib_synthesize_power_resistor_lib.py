from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from uuid import NAMESPACE_URL, uuid5

from altium_monkey import (
    AltiumPcbFootprint,
    AltiumPcbLib,
    PadShape,
    PcbBodyProjection,
    PcbLayer,
)

SAMPLE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SAMPLE_DIR / "output"
STEP_OUTPUT_DIR = OUTPUT_DIR / "step_models"
PCBLIB_OUTPUT_DIR = OUTPUT_DIR / "pcblib"
MANIFEST_PATH = OUTPUT_DIR / "synthesis_manifest.json"

MM_TO_MILS = 1000.0 / 25.4
DEFAULT_PART_NUMBERS = ("SQP20AJB-10R", "SQP20AJB-47R", "SQP20AJB-150R")
ALL_SQP20_PART_NUMBERS = (
    "SQP20AJB-47R",
    "SQP20AJB-20R",
    "SQP20AJB-150R",
    "SQP20AJB-51R",
    "SQP20AJB-0R75",
    "SQP20AJB-3K3",
    "SQP20AJB-33R",
    "SQP20AJB-30R",
    "SQP20AJB-10R",
    "SQP20AJB-39R",
    "SQP20AJB-8K2",
    "SQP20AJB-20K",
    "SQP20AJB-1K",
    "SQP20AJB-2R2",
    "SQP20AJB-1R",
    "SQP20AJB-15R",
    "SQP20AJB-8R2",
    "SQP20AJB-0R15",
    "SQP20AJB-0R16",
    "SQP20AJB-0R18",
    "SQP20AJB-0R2",
    "SQP20AJB-0R22",
    "SQP20AJB-0R24",
    "SQP20AJB-0R27",
    "SQP20AJB-0R3",
    "SQP20AJB-0R33",
    "SQP20AJB-0R36",
    "SQP20AJB-0R39",
    "SQP20AJB-0R43",
    "SQP20AJB-0R47",
    "SQP20AJB-0R51",
    "SQP20AJB-0R56",
    "SQP20AJB-0R62",
    "SQP20AJB-0R68",
    "SQP20AJB-0R82",
    "SQP20AJB-0R91",
    "SQP20AJB-100R",
    "SQP20AJB-10K",
    "SQP20AJB-110R",
    "SQP20AJB-11K",
    "SQP20AJB-11R",
    "SQP20AJB-120R",
    "SQP20AJB-12K",
    "SQP20AJB-12R",
    "SQP20AJB-130R",
    "SQP20AJB-13K",
    "SQP20AJB-13R",
    "SQP20AJB-15K",
    "SQP20AJB-160R",
    "SQP20AJB-16K",
    "SQP20AJB-16R",
    "SQP20AJB-180R",
    "SQP20AJB-18K",
    "SQP20AJB-18R",
    "SQP20AJB-1K1",
    "SQP20AJB-1K2",
    "SQP20AJB-1K3",
    "SQP20AJB-1K5",
    "SQP20AJB-1K6",
    "SQP20AJB-1K8",
    "SQP20AJB-1R1",
    "SQP20AJB-1R2",
    "SQP20AJB-1R3",
    "SQP20AJB-1R5",
    "SQP20AJB-1R6",
    "SQP20AJB-1R8",
    "SQP20AJB-220R",
    "SQP20AJB-22K",
    "SQP20AJB-22R",
    "SQP20AJB-240R",
    "SQP20AJB-24K",
    "SQP20AJB-24R",
    "SQP20AJB-270R",
    "SQP20AJB-27K",
    "SQP20AJB-27R",
    "SQP20AJB-2K",
    "SQP20AJB-2K2",
    "SQP20AJB-2K4",
    "SQP20AJB-2K7",
    "SQP20AJB-2R",
    "SQP20AJB-2R4",
    "SQP20AJB-2R7",
    "SQP20AJB-300R",
    "SQP20AJB-30K",
    "SQP20AJB-330R",
    "SQP20AJB-33K",
    "SQP20AJB-360R",
    "SQP20AJB-36K",
    "SQP20AJB-36R",
    "SQP20AJB-390R",
    "SQP20AJB-39K",
    "SQP20AJB-3K",
    "SQP20AJB-3K6",
    "SQP20AJB-3K9",
    "SQP20AJB-3R",
    "SQP20AJB-3R3",
    "SQP20AJB-3R6",
    "SQP20AJB-3R9",
    "SQP20AJB-430R",
    "SQP20AJB-43K",
    "SQP20AJB-43R",
    "SQP20AJB-470R",
    "SQP20AJB-47K",
    "SQP20AJB-4K3",
    "SQP20AJB-4K7",
    "SQP20AJB-4R3",
    "SQP20AJB-4R7",
    "SQP20AJB-510R",
    "SQP20AJB-51K",
    "SQP20AJB-560R",
    "SQP20AJB-56K",
    "SQP20AJB-56R",
    "SQP20AJB-5K1",
    "SQP20AJB-5K6",
    "SQP20AJB-5R1",
    "SQP20AJB-5R6",
    "SQP20AJB-620R",
    "SQP20AJB-62K",
    "SQP20AJB-62R",
    "SQP20AJB-680R",
    "SQP20AJB-68R",
    "SQP20AJB-6K2",
    "SQP20AJB-6K8",
    "SQP20AJB-6R2",
    "SQP20AJB-6R8",
    "SQP20AJB-750R",
    "SQP20AJB-75R",
    "SQP20AJB-7K5",
    "SQP20AJB-7R5",
    "SQP20AJB-820R",
    "SQP20AJB-82R",
    "SQP20AJB-910R",
    "SQP20AJB-91R",
    "SQP20AJB-9K1",
    "SQP20AJB-9R1",
)

FONT_5X7 = {
    " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    "0": [0x0E, 0x11, 0x19, 0x15, 0x13, 0x11, 0x0E],
    "1": [0x04, 0x06, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x10, 0x08, 0x04, 0x02, 0x1F],
    "3": [0x0E, 0x11, 0x10, 0x0E, 0x10, 0x11, 0x0E],
    "4": [0x08, 0x0C, 0x0A, 0x09, 0x1F, 0x08, 0x08],
    "5": [0x1F, 0x01, 0x0F, 0x10, 0x10, 0x11, 0x0E],
    "6": [0x0E, 0x11, 0x01, 0x0F, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x10, 0x08, 0x04, 0x02, 0x02, 0x02],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x1E, 0x10, 0x11, 0x0E],
    "A": [0x0E, 0x11, 0x11, 0x11, 0x1F, 0x11, 0x11],
    "E": [0x1F, 0x01, 0x01, 0x0F, 0x01, 0x01, 0x1F],
    "G": [0x0E, 0x11, 0x01, 0x1D, 0x11, 0x11, 0x1E],
    "J": [0x1C, 0x08, 0x08, 0x08, 0x08, 0x09, 0x06],
    "K": [0x11, 0x09, 0x05, 0x03, 0x05, 0x09, 0x11],
    "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "R": [0x0F, 0x11, 0x11, 0x0F, 0x05, 0x09, 0x11],
    "S": [0x0E, 0x11, 0x01, 0x0E, 0x10, 0x11, 0x0E],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A],
    "Y": [0x11, 0x11, 0x11, 0x0A, 0x04, 0x04, 0x04],
}


@dataclass(frozen=True)
class Sqp20Dimensions:
    body_length_mm: float = 65.0
    body_width_mm: float = 13.5
    body_height_mm: float = 13.5
    lead_diameter_mm: float = 0.8
    lead_egress_length_mm: float = 1.5
    lead_vertical_length_mm: float = 8.75
    lead_bend_radius_mm: float = 1.0
    dot_diameter_mm: float = 0.35
    dot_height_mm: float = 0.1
    dot_spacing_mm: float = 0.55
    text_line_spacing_mm: float = 5.5
    pad_diameter_mm: float = 1.75
    hole_diameter_mm: float = 1.0


SQP20 = Sqp20Dimensions()


def _mm_to_mils(value_mm: float) -> float:
    return value_mm * MM_TO_MILS


def _parse_resistance_ohms(part_number: str) -> float:
    value = part_number.split("-")[-1].upper()
    if value.startswith("R"):
        return float("0." + value[1:])
    if "R" in value:
        return float(value.replace("R", ".").rstrip("."))
    if "K" in value:
        return float(value.replace("K", ".").rstrip(".")) * 1000.0
    if "M" in value:
        return float(value.replace("M", ".").rstrip(".")) * 1_000_000.0
    return float(value)


def _format_resistance_marking(ohms: float) -> str:
    if ohms < 1.0:
        return f"R{int(round(ohms * 1000.0)):03d}"
    if ohms < 1000.0:
        if ohms.is_integer():
            return f"{int(ohms)}R"
        return f"{ohms:g}".replace(".", "R")
    kiloohms = ohms / 1000.0
    if kiloohms.is_integer():
        return f"{int(kiloohms)}K"
    return f"{kiloohms:g}".replace(".", "K")


def _render_text_to_dot_pixels(text: str) -> list[tuple[int, int]]:
    dots: list[tuple[int, int]] = []
    cursor_x = 0
    for char in text.upper():
        bitmap = FONT_5X7.get(char, FONT_5X7[" "])
        for row_index, row_byte in enumerate(bitmap):
            for column_index in range(5):
                if row_byte & (1 << column_index):
                    dots.append((cursor_x + column_index, 6 - row_index))
        cursor_x += 6
    return dots


def _load_cadquery() -> tuple[object, object, object]:
    try:
        import cadquery as cq
        from cadquery import Assembly, Color
    except ImportError as exc:
        raise SystemExit(
            "This example requires CadQuery for STEP synthesis. "
            "Install altium-monkey with its declared runtime dependencies."
        ) from exc
    return cq, Assembly, Color


def _normalize_step_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="strict")
    normalized = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
    path.write_text(normalized, encoding="utf-8", newline="\n")


def synthesize_sqp20_step_model(part_number: str, output_path: Path) -> Path:
    cq, assembly_class, color_class = _load_cadquery()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resistance_marking = _format_resistance_marking(_parse_resistance_ohms(part_number))
    text_line_1 = "   YAGEO    2511"
    text_line_2 = f"   20W   {resistance_marking}   J"

    body_z_offset = SQP20.body_height_mm / 2.0
    body = (
        cq.Workplane("XY")
        .box(SQP20.body_length_mm, SQP20.body_width_mm, SQP20.body_height_mm)
        .translate((0.0, 0.0, body_z_offset))
    )
    body = body.edges("|Z").fillet(0.3)

    dots = []
    text_start_x = -SQP20.body_length_mm / 2.0 + 2.0
    text_start_y = SQP20.body_width_mm / 2.0 - 2.0
    for line_index, line_text in enumerate((text_line_1, text_line_2)):
        y_offset = line_index * SQP20.text_line_spacing_mm
        for pixel_x, pixel_y in _render_text_to_dot_pixels(line_text):
            dot_x = text_start_x + pixel_x * SQP20.dot_spacing_mm
            dot_y = text_start_y - y_offset - (6 - pixel_y) * SQP20.dot_spacing_mm
            dot = (
                cq.Workplane("XY")
                .workplane(offset=SQP20.body_height_mm)
                .center(dot_x, dot_y)
                .circle(SQP20.dot_diameter_mm / 2.0)
                .extrude(SQP20.dot_height_mm)
            )
            dots.append(dot)

    left_x_start = -SQP20.body_length_mm / 2.0
    left_x_end = left_x_start - SQP20.lead_egress_length_mm
    left_path = (
        cq.Workplane("XZ")
        .moveTo(left_x_start, body_z_offset)
        .lineTo(left_x_end + SQP20.lead_bend_radius_mm, body_z_offset)
        .radiusArc(
            (left_x_end, body_z_offset - SQP20.lead_bend_radius_mm),
            -SQP20.lead_bend_radius_mm,
        )
        .lineTo(left_x_end, body_z_offset - SQP20.lead_vertical_length_mm)
    )
    left_lead = (
        cq.Workplane("YZ", origin=(left_x_start, 0.0, body_z_offset))
        .circle(SQP20.lead_diameter_mm / 2.0)
        .sweep(left_path)
    )

    right_x_start = SQP20.body_length_mm / 2.0
    right_x_end = right_x_start + SQP20.lead_egress_length_mm
    right_path = (
        cq.Workplane("XZ")
        .moveTo(right_x_end, body_z_offset - SQP20.lead_vertical_length_mm)
        .lineTo(right_x_end, body_z_offset - SQP20.lead_bend_radius_mm)
        .radiusArc(
            (right_x_end - SQP20.lead_bend_radius_mm, body_z_offset),
            -SQP20.lead_bend_radius_mm,
        )
        .lineTo(right_x_start, body_z_offset)
    )
    right_lead = (
        cq.Workplane(
            "XY",
            origin=(right_x_end, 0.0, body_z_offset - SQP20.lead_vertical_length_mm),
        )
        .circle(SQP20.lead_diameter_mm / 2.0)
        .sweep(right_path)
    )

    assembly = assembly_class()
    assembly.add(body, name="ceramic_body", color=color_class(0.95, 0.95, 0.95))
    for index, dot in enumerate(dots):
        assembly.add(dot, name=f"ink_dot_{index:03d}", color=color_class(0.0, 0.0, 0.0))
    assembly.add(left_lead, name="left_lead", color=color_class(0.7, 0.7, 0.7))
    assembly.add(right_lead, name="right_lead", color=color_class(0.7, 0.7, 0.7))
    assembly.save(str(output_path))
    _normalize_step_file(output_path)
    return output_path


def _add_box_tracks(
    footprint: AltiumPcbFootprint,
    *,
    left_mils: float,
    bottom_mils: float,
    right_mils: float,
    top_mils: float,
    width_mils: float,
    layer: PcbLayer,
) -> None:
    footprint.add_track(
        (left_mils, bottom_mils),
        (right_mils, bottom_mils),
        width_mils=width_mils,
        layer=layer,
    )
    footprint.add_track(
        (right_mils, bottom_mils),
        (right_mils, top_mils),
        width_mils=width_mils,
        layer=layer,
    )
    footprint.add_track(
        (right_mils, top_mils),
        (left_mils, top_mils),
        width_mils=width_mils,
        layer=layer,
    )
    footprint.add_track(
        (left_mils, top_mils),
        (left_mils, bottom_mils),
        width_mils=width_mils,
        layer=layer,
    )


def _ascii_identifier(text: str) -> str:
    return ",".join(str(ord(char)) for char in text)


def synthesize_sqp20_pcblib(
    part_number: str,
    *,
    step_path: Path,
    output_path: Path,
) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcblib = AltiumPcbLib()
    model = pcblib.add_embedded_model(
        name=step_path.name,
        model_data=step_path.read_bytes(),
        # model_id is optional. This generator uses a deterministic UUID so the
        # same part number produces stable Altium model metadata on every run.
        model_id=uuid5(NAMESPACE_URL, f"altium-monkey:sqp20-step:{part_number}"),
    )
    footprint = pcblib.add_footprint(
        part_number,
        height=f"{_mm_to_mils(SQP20.body_height_mm + SQP20.dot_height_mm):.4f}mil",
        description=f"YAGEO SQP20A 20W through-hole resistor, {part_number}",
    )

    pad_x_mils = _mm_to_mils(SQP20.body_length_mm / 2.0 + SQP20.lead_egress_length_mm)
    for designator, x_mils in (("1", -pad_x_mils), ("2", pad_x_mils)):
        footprint.add_pad(
            designator=designator,
            position_mils=(x_mils, 0.0),
            width_mils=_mm_to_mils(SQP20.pad_diameter_mm),
            height_mils=_mm_to_mils(SQP20.pad_diameter_mm),
            layer=PcbLayer.MULTI_LAYER,
            shape=PadShape.CIRCLE,
            hole_size_mils=_mm_to_mils(SQP20.hole_diameter_mm),
            plated=True,
        )

    body_left_mils = _mm_to_mils(-SQP20.body_length_mm / 2.0)
    body_right_mils = _mm_to_mils(SQP20.body_length_mm / 2.0)
    body_bottom_mils = _mm_to_mils(-SQP20.body_width_mm / 2.0)
    body_top_mils = _mm_to_mils(SQP20.body_width_mm / 2.0)
    _add_box_tracks(
        footprint,
        left_mils=body_left_mils,
        bottom_mils=body_bottom_mils,
        right_mils=body_right_mils,
        top_mils=body_top_mils,
        width_mils=20.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    lead_stub_mils = _mm_to_mils(0.25)
    footprint.add_track(
        (body_right_mils, 0.0),
        (body_right_mils + lead_stub_mils, 0.0),
        width_mils=20.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    footprint.add_track(
        (body_left_mils - lead_stub_mils, 0.0),
        (body_left_mils, 0.0),
        width_mils=20.0,
        layer=PcbLayer.TOP_OVERLAY,
    )

    model_outline_half_x = _mm_to_mils(
        SQP20.body_length_mm / 2.0
        + SQP20.lead_egress_length_mm
        + SQP20.lead_diameter_mm / 2.0
    )
    body = footprint.add_embedded_3d_model(
        model,
        bounds_mils=(
            -model_outline_half_x,
            body_bottom_mils,
            model_outline_half_x,
            body_top_mils,
        ),
        layer=PcbLayer.MECHANICAL_1,
        overall_height_mils=_mm_to_mils(SQP20.body_height_mm + SQP20.dot_height_mm),
        side=PcbBodyProjection.TOP,
        standoff_height_mils=0.0,
        identifier=_ascii_identifier(part_number),
    )
    body.texture_size_x = 1
    body.texture_size_y = 1

    pcblib.save(output_path)
    return {
        "part_number": part_number,
        "pcblib": str(output_path.relative_to(SAMPLE_DIR)).replace("\\", "/"),
        "step_model": str(step_path.relative_to(SAMPLE_DIR)).replace("\\", "/"),
        "model_checksum_unsigned": int(model.checksum) & 0xFFFFFFFF,
        "model_checksum_hex": f"0x{int(model.checksum) & 0xFFFFFFFF:08X}",
        "step_byte_count": step_path.stat().st_size,
    }


def _select_part_numbers(args: argparse.Namespace) -> tuple[str, ...]:
    if args.part:
        return tuple(args.part)
    part_numbers = ALL_SQP20_PART_NUMBERS if args.all else DEFAULT_PART_NUMBERS
    if args.limit is not None:
        return tuple(part_numbers[: args.limit])
    return tuple(part_numbers)


def build_library_set(part_numbers: Sequence[str]) -> list[dict[str, object]]:
    STEP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PCBLIB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generated: list[dict[str, object]] = []
    for part_number in part_numbers:
        step_path = STEP_OUTPUT_DIR / f"{part_number}.step"
        pcblib_path = PCBLIB_OUTPUT_DIR / f"{part_number}.PcbLib"
        synthesize_sqp20_step_model(part_number, step_path)
        generated.append(
            synthesize_sqp20_pcblib(
                part_number,
                step_path=step_path,
                output_path=pcblib_path,
            )
        )
    return generated


def _write_manifest(
    generated: list[dict[str, object]], part_numbers: Sequence[str]
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "series": "SQP20A",
        "generated_count": len(generated),
        "requested_part_numbers": list(part_numbers),
        "available_full_series_count": len(ALL_SQP20_PART_NUMBERS),
        "checksum_note": (
            "Native Altium model checksums are generated from the synthesized "
            "STEP bytes and written consistently to model and component-body metadata."
        ),
        "parts": generated,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthesize SQP20A STEP models and matching Altium PcbLibs."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate the full built-in SQP20AJB value list instead of the small demo subset.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Generate only the first N selected parts.",
    )
    parser.add_argument(
        "--part",
        action="append",
        help="Generate one explicit part number. Can be passed more than once.",
    )
    args = parser.parse_args()
    part_numbers = _select_part_numbers(args)
    generated = build_library_set(part_numbers)
    _write_manifest(generated, part_numbers)
    print(f"Wrote {len(generated)} SQP20 PcbLibs to {PCBLIB_OUTPUT_DIR}")
    print(f"Wrote STEP models to {STEP_OUTPUT_DIR}")
    print(f"Wrote manifest to {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
