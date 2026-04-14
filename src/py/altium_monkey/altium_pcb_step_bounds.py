"""STEP geometry helpers for PCB embedded model authoring."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

MM_TO_MILS = 1000.0 / 25.4


@dataclass(frozen=True)
class PcbStepModelBounds:
    """
    Axis-aligned STEP model bounds converted to PCB public mil units.

    `bounds_mils` is the footprint-plane XY projection after model rotations
    and X/Y placement are applied. `overall_height_mils` follows Altium's 3D
    Body field and stores the transformed model `zmax` above the board plane.
    """

    bounds_mils: tuple[float, float, float, float]
    overall_height_mils: float
    min_z_mils: float
    max_z_mils: float


def _coerce_location_mils(location_mils: tuple[float, float]) -> tuple[float, float]:
    if len(location_mils) != 2:
        raise ValueError("location_mils must contain exactly two values")
    return float(location_mils[0]), float(location_mils[1])


def _sanitize_step_filename(filename_hint: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(filename_hint or "").strip())
    if not name:
        return "model.step"
    if not name.lower().endswith((".step", ".stp")):
        return f"{name}.step"
    return name


def _compute_step_bounds_from_path(
    path: Path,
    *,
    rotation_x_degrees: float,
    rotation_y_degrees: float,
    rotation_z_degrees: float,
    location_mils: tuple[float, float],
    z_offset_mils: float,
) -> PcbStepModelBounds:
    try:
        import cadquery as cq
    except ImportError as exc:
        raise RuntimeError(
            "STEP bounds inference requires cadquery/OCCT, which is an "
            "altium-monkey runtime dependency."
        ) from exc

    try:
        workplane = cq.importers.importStep(str(path))
        # Altium stores rotations independently from the STEP payload. Native
        # PcbLib bodies match CadQuery right-hand rotations applied X, then Y,
        # then Z around the model origin before the 2D and Z offsets are added.
        if abs(rotation_x_degrees) > 1e-9:
            workplane = workplane.rotate(
                (0, 0, 0),
                (1, 0, 0),
                float(rotation_x_degrees),
            )
        if abs(rotation_y_degrees) > 1e-9:
            workplane = workplane.rotate(
                (0, 0, 0),
                (0, 1, 0),
                float(rotation_y_degrees),
            )
        if abs(rotation_z_degrees) > 1e-9:
            workplane = workplane.rotate(
                (0, 0, 0),
                (0, 0, 1),
                float(rotation_z_degrees),
            )
        shape = workplane.val()
        bbox = shape.BoundingBox()
    except Exception as exc:
        raise ValueError(f"Could not infer STEP model bounds from {path.name}") from exc

    location_x_mils, location_y_mils = location_mils
    z_offset = float(z_offset_mils)
    bounds_mils = (
        float(bbox.xmin) * MM_TO_MILS + location_x_mils,
        float(bbox.ymin) * MM_TO_MILS + location_y_mils,
        float(bbox.xmax) * MM_TO_MILS + location_x_mils,
        float(bbox.ymax) * MM_TO_MILS + location_y_mils,
    )
    min_z_mils = float(bbox.zmin) * MM_TO_MILS + z_offset
    max_z_mils = float(bbox.zmax) * MM_TO_MILS + z_offset
    overall_height_mils = max_z_mils

    if bounds_mils[2] <= bounds_mils[0] or bounds_mils[3] <= bounds_mils[1]:
        raise ValueError(f"STEP model has invalid XY bounds: {path.name}")
    if max_z_mils < min_z_mils:
        raise ValueError(f"STEP model has invalid Z bounds: {path.name}")

    return PcbStepModelBounds(
        bounds_mils=bounds_mils,
        overall_height_mils=overall_height_mils,
        min_z_mils=min_z_mils,
        max_z_mils=max_z_mils,
    )


def compute_step_model_bounds_mils(
    model_data: bytes | bytearray | memoryview | str | Path,
    *,
    filename_hint: str = "model.step",
    rotation_x_degrees: float = 0.0,
    rotation_y_degrees: float = 0.0,
    rotation_z_degrees: float = 0.0,
    location_mils: tuple[float, float] = (0.0, 0.0),
    z_offset_mils: float = 0.0,
) -> PcbStepModelBounds:
    """
    Compute an Altium-style STEP model bounding box in PCB public mil units.

    The helper imports the STEP payload through CadQuery/OCCT, applies model
    rotations around the STEP origin in Altium order (X, then Y, then Z), then
    applies `location_mils` to XY and `z_offset_mils` to Z. The returned
    projection is an axis-aligned bounding rectangle, not an HLR outline.

    Args:
        model_data: Uncompressed STEP payload bytes, or a path to a STEP file.
        filename_hint: Filename used when bytes must be staged for OCCT import.
        rotation_x_degrees: X-axis model rotation in degrees.
        rotation_y_degrees: Y-axis model rotation in degrees.
        rotation_z_degrees: Z-axis model rotation in degrees.
        location_mils: Footprint-plane model location `(x_mils, y_mils)`.
        z_offset_mils: 3D model Z offset in mils.

    Returns:
        Bounds as `(left, bottom, right, top)`, `zmin`, `zmax`, and Altium
        Overall Height (`zmax` above the board plane), all in mils.
    """
    location = _coerce_location_mils(location_mils)
    if isinstance(model_data, (str, Path)):
        return _compute_step_bounds_from_path(
            Path(model_data),
            rotation_x_degrees=float(rotation_x_degrees),
            rotation_y_degrees=float(rotation_y_degrees),
            rotation_z_degrees=float(rotation_z_degrees),
            location_mils=location,
            z_offset_mils=float(z_offset_mils),
        )

    payload = bytes(model_data)
    if not payload:
        raise ValueError("STEP model payload is empty")

    with tempfile.TemporaryDirectory() as temp_dir:
        step_path = Path(temp_dir) / _sanitize_step_filename(filename_hint)
        step_path.write_bytes(payload)
        return _compute_step_bounds_from_path(
            step_path,
            rotation_x_degrees=float(rotation_x_degrees),
            rotation_y_degrees=float(rotation_y_degrees),
            rotation_z_degrees=float(rotation_z_degrees),
            location_mils=location,
            z_offset_mils=float(z_offset_mils),
        )
