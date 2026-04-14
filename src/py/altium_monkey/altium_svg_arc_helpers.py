"""Shared SVG arc helpers used by schematic and PCB emitters."""

from __future__ import annotations

import math


def svg_circle_arc_center_for_flags(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    large_arc_flag: int,
    sweep_flag: int,
) -> tuple[float, float]:
    """Recover the SVG arc center (phi=0, rx=ry) from endpoint form."""
    rx = radius
    ry = radius
    if rx <= 0.0 or ry <= 0.0:
        return (math.nan, math.nan)

    x1p = (x1 - x2) * 0.5
    y1p = (y1 - y2) * 0.5

    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    if den <= 0.0:
        return (math.nan, math.nan)

    coef = math.sqrt(max(0.0, num / den))
    sign = -1.0 if large_arc_flag == sweep_flag else 1.0
    cxp = sign * coef * (rx * y1p / ry)
    cyp = sign * coef * (-ry * x1p / rx)

    mx = (x1 + x2) * 0.5
    my = (y1 + y2) * 0.5
    return (cxp + mx, cyp + my)


def choose_svg_sweep_flag_for_center(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    large_arc_flag: int,
    center_x: float,
    center_y: float,
    default_sweep_flag: int,
) -> int:
    """Choose sweep flag whose implied SVG center best matches known center."""
    best_sweep = int(default_sweep_flag)
    best_error: float | None = None

    for sweep_int in (0, 1):
        cx_candidate, cy_candidate = svg_circle_arc_center_for_flags(
            x1,
            y1,
            x2,
            y2,
            radius,
            large_arc_flag,
            sweep_int,
        )
        if math.isnan(cx_candidate) or math.isnan(cy_candidate):
            continue
        error = math.hypot(cx_candidate - center_x, cy_candidate - center_y)
        if best_error is None or error < best_error:
            best_error = error
            best_sweep = sweep_int

    return best_sweep
