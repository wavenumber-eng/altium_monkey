"""Schematic SVG rendering context, options, and helper utilities."""

import html
import math
from decimal import Decimal, ROUND_HALF_EVEN
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from .altium_font_resolver import resolve_font_with_style
from .altium_record_types import CoordPoint, LineStyle, LineWidth, color_to_hex
from .altium_ttf_metrics import (
    get_font_factor,
    get_font_path,
    get_font_path_with_style,
)
from .altium_ttf_metrics import (
    pt_to_px as ttf_pt_to_px,
)

# Font fallback constant - matches Altium's GenericFontFamilies.SansSerif
FONT_FALLBACK = "Microsoft Sans Serif"

# Native Altium SVG export resolves a narrow set of system/title-block
# placeholders through project context even when the schematic parameter table
# stores '*' as the value. Keep this narrower than the review/default
# project-fallback contract so ordinary schematic '*' parameters still remain
# literal on the strict native/on-screen lanes.
NATIVE_SYSTEM_STAR_PROJECT_FALLBACKS = {
    "currenttime",
    "currentdate",
    "time",
    "date",
    "documentfullnameandpath",
    "documentfullpathname",
    "documentfullpathandname",
    "documentname",
    "modifieddate",
    "page number",
    "page modify date",
    "pagenumber",
    "pagemodifydate",
}

# AD25 on-screen font resolution can diverge from native SVG export for fonts
# that are not available through the standard system font lookup path.
# Keep these overrides narrowly scoped to the oracle-backed on-screen path.
ONSCREEN_FONT_ORACLE_OVERRIDES: dict[str, dict[str, float | str]] = {
    "old stamper": {
        "display_name": "Microsoft Sans Serif",
        "factor": 0.8835202789306641,
    },
    "mooretronics": {
        "display_name": "Mooretronics",
        "factor": 0.9987808227539062,
    },
}

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager


# ============================================================================
# SVG RENDER OPTIONS
# ============================================================================


class SchJunctionZOrder(Enum):
    """
    Junction z-order rendering strategy.

    NATIVE: Match native Altium SVG export (junctions render inline, may be behind segments).
    ALWAYS_ON_TOP: Force junctions on top of all elements (matches screen rendering).
    """

    NATIVE = auto()
    ALWAYS_ON_TOP = auto()


class SchCompileMaskRenderMode(Enum):
    """
    How compile-mask visuals should be rendered.
    """

    ORACLE_RAW = auto()
    COMPILED_VISUAL = auto()


@dataclass
class SchSvgRenderOptions:
    """
    Options controlling schematic SVG output behavior and compatibility modes.
    """

    # Z-order options (default ALWAYS_ON_TOP for better visual quality)
    # Use native_altium() for comparison testing
    junction_z_order: SchJunctionZOrder = SchJunctionZOrder.ALWAYS_ON_TOP

    # Color overrides (None = use native colors per object type)
    junction_color_override: str | None = None

    # Font size truncation for baseline calculation
    # True = match native Altium SVG (truncate to int, causes ~0-0.9px Y error)
    # False = use float for visually correct rendering (default for better quality)
    # Use native_altium() for comparison testing
    truncate_font_size_for_baseline: bool = False

    # Apply narrow AD25-backed display-name overrides for specific custom fonts
    # on the on-screen/oracle compatibility path. Keep this enabled for the
    # legacy `onscreen` test/profile contract, but allow review/default output
    # to preserve real asset fonts such as Old Stamper.
    use_onscreen_font_oracle_overrides: bool = True

    # Bezier rendering mode
    # True = flatten to line segments (native Altium behavior, for test compatibility)
    # False = use SVG cubic bezier path commands (default, visually identical)
    bezier_as_lines: bool = False

    # Number of line segments when bezier_as_lines=True
    # Default 32 matches native Altium SVG export (GDI+ flattening)
    bezier_segment_count: int = 32

    # Parameter substitution mode
    # True = substitute =PARAM_NAME with resolved values
    # False = keep parameter names as-is
    substitute_parameters: bool = True

    # Project-parameter fallback mode for schematic values explicitly set to '*'
    # True = schematic '*' may be replaced by the project-level value
    # False = schematic '*' remains literal (matches on-screen/oracle behavior)
    fallback_project_parameters_for_star: bool = False

    # Compile-mask rendering mode.
    # ORACLE_RAW: match raw/oracle export behavior.
    # COMPILED_VISUAL: preserve normal object colors and apply a document-level
    # dimming overlay outside compile-mask regions. This is intended for the
    # compiled design view, not oracle/native export parity.
    compile_mask_render_mode: SchCompileMaskRenderMode = (
        SchCompileMaskRenderMode.ORACLE_RAW
    )

    # Image background-to-alpha conversion
    # True = convert pixels matching sheet background color to transparent (default)
    # False = keep embedded BMP colors as-is (may show white background for PNGs with alpha)
    #
    # Background: Altium stores images as BMP internally, losing PNG alpha channels.
    # When exporting SVG, native Altium reads the original file from disk (if available)
    # to preserve transparency. Since we only have the embedded BMP, we use the sheet
    # background color as a color key to restore transparency.
    image_background_to_alpha: bool = True

    # Tolerance for background color matching (0-255)
    # Pixels within this RGB distance from background color are made transparent
    # Default 5 handles minor JPEG artifacts and color rounding
    image_alpha_tolerance: int = 5

    # Text rendering mode for schematic SVG output.
    # False (default): emit standard SVG <text> elements.
    # True: emit polygon <path> geometry via TrueType tessellation.
    # Polygon text can be much slower and much larger on real schematics.
    text_as_polygons: bool = False

    # Flatten tolerance for polygon text in SVG units (1 unit == 1 mil).
    # Smaller values produce smoother outlines with more vertices.
    polygon_text_tolerance: float = 0.5

    @classmethod
    def native_altium(cls) -> "SchSvgRenderOptions":
        """
        Options matching native Altium SVG export exactly (with truncation bug).
        """
        return cls(
            junction_z_order=SchJunctionZOrder.NATIVE,
            truncate_font_size_for_baseline=True,
            bezier_as_lines=True,  # Native Altium flattens beziers to ~32 line segments
            fallback_project_parameters_for_star=False,
        )

    @classmethod
    def onscreen(
        cls,
        junction_color: str | None = None,
        *,
        compile_mask_render_mode: SchCompileMaskRenderMode = SchCompileMaskRenderMode.ORACLE_RAW,
    ) -> "SchSvgRenderOptions":
        """
        On-screen rendering: float font-sizes, junctions on top, optional color override.
        """
        return cls(
            junction_z_order=SchJunctionZOrder.ALWAYS_ON_TOP,
            junction_color_override=junction_color,
            truncate_font_size_for_baseline=False,  # Use float for better visual quality
            fallback_project_parameters_for_star=False,
            compile_mask_render_mode=compile_mask_render_mode,
        )

    @classmethod
    def review_default(
        cls,
        junction_color: str | None = None,
        *,
        compile_mask_render_mode: SchCompileMaskRenderMode = SchCompileMaskRenderMode.ORACLE_RAW,
    ) -> "SchSvgRenderOptions":
        """
        Project/default review output: onscreen geometry with real asset fonts preserved.
        """
        return cls(
            junction_z_order=SchJunctionZOrder.ALWAYS_ON_TOP,
            junction_color_override=junction_color,
            truncate_font_size_for_baseline=False,
            # Review/default is intended to reflect final project-context output,
            # so schematic '*' placeholders should fall through to project params.
            fallback_project_parameters_for_star=True,
            compile_mask_render_mode=compile_mask_render_mode,
            use_onscreen_font_oracle_overrides=False,
        )

    @classmethod
    def onscreen_compiled(
        cls, junction_color: str | None = None
    ) -> "SchSvgRenderOptions":
        """
        Compiled-design visual mode with compile-mask dimming applied by overlay.
        """
        return cls.onscreen(
            junction_color=junction_color,
            compile_mask_render_mode=SchCompileMaskRenderMode.COMPILED_VISUAL,
        )

    @classmethod
    def polytext(
        cls,
        junction_color: str | None = None,
        *,
        compile_mask_render_mode: SchCompileMaskRenderMode = SchCompileMaskRenderMode.ORACLE_RAW,
    ) -> "SchSvgRenderOptions":
        """
        On-screen rendering with text emitted as polygon paths.

        This mode resolves, shapes, flattens, and serializes glyph outlines
        instead of writing standard SVG `<text>` nodes. It is useful when SVG
        consumers need font-independent path geometry, but it can take
        substantially longer and produce much larger SVG files on text-heavy
        schematics. Prefer `review_default()` or `onscreen()` unless polygon
        text is specifically required.
        """
        return cls(
            junction_z_order=SchJunctionZOrder.ALWAYS_ON_TOP,
            junction_color_override=junction_color,
            truncate_font_size_for_baseline=False,
            text_as_polygons=True,
            fallback_project_parameters_for_star=False,
            compile_mask_render_mode=compile_mask_render_mode,
        )


COMPILED_COMPILE_MASK_OVERLAY_OPACITY = 0.75


def _format_svg_number(value: float) -> str:
    if abs(value - round(value)) <= 1e-9:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def build_compile_mask_visual_overlay_svg(
    *,
    canvas_width_px: float,
    canvas_height_px: float,
    compile_mask_bounds: list[
        tuple[int | float, int | float, int | float, int | float]
    ],
    background_color: str,
    scale: float = 1.0,
    opacity: float | None = None,
    group_id: str = "CompiledCompileMaskOverlay",
) -> list[str]:
    """
    Build an SVG overlay that dims everything inside compile-mask rectangles.

    This is intended for compiled-design visual mode only. Raw/oracle/native
    export paths should keep the record-local compile-mask behavior instead.
    """
    if not compile_mask_bounds:
        return []

    overlay_opacity = (
        COMPILED_COMPILE_MASK_OVERLAY_OPACITY if opacity is None else float(opacity)
    )
    mask_id = f"{group_id}Mask"
    lines = [
        "<defs>",
        f'<mask id = "{mask_id}" maskUnits="userSpaceOnUse">',
        (
            f'<rect x = "0" y="0" width="{_format_svg_number(canvas_width_px)}" '
            f'height="{_format_svg_number(canvas_height_px)}" fill="#000000" fill-opacity="1"/>'
        ),
    ]
    for index, (min_x, min_y, max_x, max_y) in enumerate(compile_mask_bounds):
        left = min(float(min_x), float(max_x)) * scale
        top = (canvas_height_px / scale - max(float(min_y), float(max_y))) * scale
        width = abs(float(max_x) - float(min_x)) * scale
        height = abs(float(max_y) - float(min_y)) * scale
        lines.append(
            (
                f'<rect id = "{group_id}Hole{index}" x = "{_format_svg_number(left)}" '
                f'y="{_format_svg_number(top)}" width="{_format_svg_number(width)}" '
                f'height="{_format_svg_number(height)}" fill="#FFFFFF" fill-opacity="1"/>'
            )
        )
    lines.extend(
        [
            "</mask>",
            "</defs>",
            f'<g id = "{group_id}" >',
            (
                f'<rect x = "0" y="0" width="{_format_svg_number(canvas_width_px)}" '
                f'height="{_format_svg_number(canvas_height_px)}" fill="{background_color}" '
                f'fill-opacity="{overlay_opacity}" mask="url(#{mask_id})"/>'
            ),
            "</g>",
        ]
    )
    return lines


# ============================================================================
# ============================================================================

# Line widths in internal units (100,000 = 1 mil)
#   SMALLEST: 0
#   SMALL: 100,000
#   MEDIUM: 300,000
#   LARGE: 500,000
LINE_WIDTH_INTERNAL = {
    LineWidth.SMALLEST: 0,  # 0 - hairline/zero width in Altium
    LineWidth.SMALL: 100000,  # 1 mil
    LineWidth.MEDIUM: 300000,  # 3 mils
    LineWidth.LARGE: 500000,  # 5 mils
}

# Line widths in mils (for SVG output)
# SMALLEST is 0 internally, but Altium renders it as the small width (1 mil)
LINE_WIDTH_MILS = {
    LineWidth.SMALLEST: 1.0,  # Rendered as eSmall (1 mil) per Altium source
    LineWidth.SMALL: 1.0,
    LineWidth.MEDIUM: 3.0,
    LineWidth.LARGE: 5.0,
}

# Default line width in mils
DEFAULT_LINE_WIDTH = 1.0

# Pin line width: PinLineWidth = 100000 = 1 mil
PIN_LINE_WIDTH = 1.0

# Bus width in mils (buses are thicker than wires)
BUS_WIDTH_MILS = 4.0

# Junction sizes in mils (diameter of the junction dot)
JUNCTION_SIZE_MILS = {
    0: 2.0,  # SMALLEST: 2 mils
    1: 3.0,  # SMALL: 3 mils
    2: 5.0,  # MEDIUM: 5 mils
    3: 10.0,  # LARGE: 10 mils
}
DEFAULT_JUNCTION_SIZE = 5.0  # Medium is default

# Wire crossover arc radius (for wire bridges/jumps)
WIRE_CROSSOVER_RADIUS_MILS = 3.0

# Default colors (Win32 format: BGR)
DEFAULT_STROKE_COLOR = 0x000000  # Black
DEFAULT_FILL_COLOR = 0x80FFFF  # Light yellow (BGR: 80,FF,FF = RGB: FF,FF,80)

# Altium default schematic colors (BGR format -> RGB hex strings)
ALTIUM_COLORS = {
    "wire": "#008000",  # Dark green (BGR: 0x008000)
    "bus": "#0000FF",  # Blue (BGR: 0xFF0000)
    "net_label": "#008000",  # Dark green
    "power_port": "#000000",  # Black
    "junction": "#008000",  # Dark green
    "component": "#800000",  # Dark red (BGR: 0x000080)
    "pin": "#000080",  # Navy blue (BGR: 0x800000)
    "sheet_border": "#000000",  # Black
    "sheet_fill": "#FFFFC0",  # Light yellow
}

# Semi-transparent alpha (used for transparent fills)
SEMI_TRANSPARENT_ALPHA = 125


# ============================================================================
# LINE STYLE MAPPINGS
# ============================================================================

# SVG stroke-dasharray values for shapes (rectangles, polygons, rounded rects)
# and Altium.GeometryMaker.SVG\svg graphics implementation PenToString() method
#
# Key files containing line/dash algorithms:
#   - Altium.GeometryMaker.SVG\svg graphics implementation - PenToString() for SVG output
#
SVG_STROKE_DASHARRAY = {
    LineStyle.SOLID: None,  # No dasharray
    LineStyle.DASHED: "4",  # stroke-dasharray="4" -> [4,4]
    LineStyle.DOTTED: "2",  # stroke-dasharray="2" -> [2,2]
    LineStyle.DASH_DOT: "4 2",  # stroke-dasharray="4 2" -> [4,2]
    # Note: DASH_DOT_DOT would be '4 2 2' but not used in schematic records
}


def get_stroke_dasharray(style: LineStyle) -> str | None:
    """
    Get SVG stroke-dasharray for line style.

    Returns None for solid lines.

    These values match the current SVG export contract for shapes such as
    rectangles and polygons.

    NOTE: For LINE/POLYLINE records, native Altium breaks dashed lines into
    individual <line> segment elements instead of using stroke-dasharray.
    Use compute_dash_segments() for those cases.
    """
    return SVG_STROKE_DASHARRAY.get(style)


# ============================================================================
# DASH SEGMENT CALCULATION
# ============================================================================
#
# Helpers for line-record dash, dot, dash-dot, and dash-dot-dot segment layout.
#
# ============================================================================

# Dash pattern parameters.
# Each style has: (divisor, [(draw_factor, offset_factor), ...])
# draw_factor: fraction of segment to draw
# offset_factor: position within segment (for multiple elements like dash+dot)
DASH_STYLE_PARAMS = {
    LineStyle.SOLID: None,
    LineStyle.DASHED: {
        "divisor": 5.0,
        "elements": [(1.0 / 1.6, 0.0)],  # ~62.5% dash at start
    },
    LineStyle.DOTTED: {
        "divisor": 2.0,
        "elements": [(1.0 / 100.0, 0.0)],  # 1% dot (very short)
    },
    LineStyle.DASH_DOT: {
        "divisor": 7.0,
        "elements": [
            (1.0 / 2.0, 0.0),  # 50% dash at start
            (1.0 / 100.0, 1.0 / 2.0 + 1.0 / 4.0),  # 1% dot after 75% position
        ],
    },
}


def compute_dash_segments(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    style: LineStyle,
    line_width_mils: float = 1.0,
    line_width_enum: "LineWidth | None" = None,
    is_polyline_segment: bool = False,
) -> list[tuple[float, float, float, float]]:
    """
    Compute individual dash/dot line segments for a dashed line.

    Native Altium SVG renders dashed lines as multiple individual <line>
    segments rather than using stroke-dasharray. This function calculates
    those segment positions with the same segment model.

    Algorithm:
        1. Calculate line length
        2. unitWidth = line_width if > 1.0 else 1.0 mil (default)
        3. numSegments = int(length / unitWidth / divisor)
        4. Each segment has specific dash/dot factors

    Note: The default unit_width of 100000 internal units = 1 mil
    (from LINE_WIDTH_INTERNAL: 100000 = SMALL = 1 mil)

    Native Altium adds a zero-length "endpoint marker" line at the end:
    - For POLYLINE segments: always added at each vertex (segment junction)
    - For simple LINEs: only for DASHED/DASH_DOT with LARGE width
    - DOTTED lines (non-polyline): never get endpoint markers

    Args:
        x1, y1: Start point (SVG coordinates, mils)
        x2, y2: End point (SVG coordinates, mils)
        style: Line style (SOLID, DASHED, DOTTED, DASH_DOT)
        line_width_mils: Line width in mils (default 1.0 for hairline/small)
        line_width_enum: LineWidth enum value (for endpoint marker logic)
        is_polyline_segment: True if this is a segment of a polyline

    Returns:
        List of (x1, y1, x2, y2) tuples for each dash/dot segment
    """
    if style == LineStyle.SOLID:
        return [(x1, y1, x2, y2)]

    params = DASH_STYLE_PARAMS.get(style)
    if params is None:
        return [(x1, y1, x2, y2)]

    # Calculate line length and direction
    dx = x2 - x1
    dy = y2 - y1
    length = math.sqrt(dx * dx + dy * dy)

    if length < 0.001:
        return [(x1, y1, x2, y2)]  # Degenerate line

    # Use 1 mil as default unit width (Altium's 100000 internal units = 1 mil)
    unit_width = line_width_mils if line_width_mils > 1.0 else 1.0

    # Calculate number of pattern segments
    divisor = params["divisor"]
    num_segments = int(length / unit_width / divisor)

    if num_segments < 1:
        # Line too short for pattern - draw as solid
        return [(x1, y1, x2, y2)]

    # Calculate segment size (full pattern repeat length)
    segment_dx = dx / num_segments
    segment_dy = dy / num_segments

    # Direction vector (normalized)
    nx = dx / length
    ny = dy / length

    segments = []
    elements = params["elements"]

    # Start position
    px, py = x1, y1

    for _ in range(num_segments):
        for draw_factor, offset_factor in elements:
            # Calculate element start position within segment
            start_x = px + segment_dx * offset_factor
            start_y = py + segment_dy * offset_factor

            # Calculate element length
            element_len = (
                math.sqrt(segment_dx * segment_dx + segment_dy * segment_dy)
                * draw_factor
            )

            # Calculate element end
            end_x = start_x + nx * element_len
            end_y = start_y + ny * element_len

            segments.append((start_x, start_y, end_x, end_y))

        # Move to next segment
        px += segment_dx
        py += segment_dy

    # Add zero-length endpoint marker (native Altium behavior)
    # Rules differ for polylines vs simple lines and by line style:
    # - POLYLINE with DASHED/DASH_DOT: endpoint markers at each vertex
    # - POLYLINE with DOTTED: NO endpoint markers
    # - Simple LINE with DASH_DOT (non-hairline): endpoint marker at end
    # - Simple LINE with DASHED (LARGE only): endpoint marker at end
    # - DOTTED simple lines: never get endpoint markers
    add_endpoint = False
    if is_polyline_segment:
        pattern_ratio = length / unit_width / divisor
        pattern_is_exact = abs(pattern_ratio - round(pattern_ratio)) <= 1e-6
        # Native schematic dashed polylines always emit the zero-length vertex
        # marker for the small-width variants. Medium-width cases only do so
        # when the segment length lands exactly on a full dash cycle.
        if style in (LineStyle.DASHED, LineStyle.DASH_DOT) and (
            line_width_enum == LineWidth.SMALL
            or (
                line_width_enum in {LineWidth.MEDIUM, LineWidth.LARGE}
                and pattern_is_exact
            )
        ):
            add_endpoint = True
    else:
        pattern_ratio = length / unit_width / divisor
        pattern_is_exact = abs(pattern_ratio - round(pattern_ratio)) <= 1e-6
        # Simple lines rules:
        # - DASH_DOT: endpoint marker for any non-hairline width
        # - DASHED: endpoint marker for LARGE width, and for exact-cycle SMALL
        #   lines where native emits a terminal zero-length stub.
        if (style == LineStyle.DASH_DOT and line_width_enum != LineWidth.SMALLEST) or (
            style == LineStyle.DASHED
            and (
                line_width_enum == LineWidth.LARGE
                or (line_width_enum == LineWidth.SMALL and pattern_is_exact)
            )
        ):
            add_endpoint = True

    if add_endpoint:
        segments.append((x2, y2, x2, y2))

    return segments


# ============================================================================
# COORDINATE CONVERSION
# ============================================================================


def altium_to_svg_x(x: int, offset_x: float = 0.0, scale: float = 1.0) -> float:
    """
    Convert Altium X coordinate to SVG.

    Args:
        x: Altium X coordinate (in 10-mil units)
        offset_x: Offset to add (in mils)
        scale: Scale factor

    Returns:
        SVG X coordinate (in mils)
    """
    # Altium unit = 10 mils
    x_mils = x * 10.0
    return (x_mils + offset_x) * scale


def altium_to_svg_y(
    y: int, offset_y: float = 0.0, scale: float = 1.0, flip: bool = True
) -> float:
    """
    Convert Altium Y coordinate to SVG.

    Args:
        y: Altium Y coordinate (in 10-mil units)
        offset_y: Offset to add (in mils) - applied BEFORE flip
        scale: Scale factor
        flip: If True, negate Y for SVG coordinate system

    Returns:
        SVG Y coordinate (in mils)
    """
    # Altium unit = 10 mils
    y_mils = y * 10.0
    if flip:
        return -(y_mils + offset_y) * scale
    return (y_mils + offset_y) * scale


def coord_to_svg(
    coord: CoordPoint,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    scale: float = 1.0,
    flip_y: bool = True,
) -> tuple[float, float]:
    """
    Convert CoordPoint to SVG coordinates.

    Args:
        coord: CoordPoint with x, y (in 10-mil units)
        offset_x, offset_y: Offsets to add (in mils)
        scale: Scale factor
        flip_y: If True, negate Y for SVG coordinate system

    Returns:
        (svg_x, svg_y) tuple in mils
    """
    svg_x = altium_to_svg_x(coord.x, offset_x, scale)
    svg_y = altium_to_svg_y(coord.y, offset_y, scale, flip_y)
    return (svg_x, svg_y)


# ============================================================================
# SVG ELEMENT BUILDERS
# ============================================================================


def svg_color(color: int | None, default: str = "#000000") -> str:
    """
    Convert Win32 color to SVG hex color string.
    """
    if color is None:
        return default
    return color_to_hex(color)


def apply_light(color: int, amount: int) -> int:
    """
    Add brightness to color (matches native ColorManager.ApplyLight).

        Adds 'amount' to each RGB channel, capped at 255.

        Args:
            color: Win32 BGR color (0x00BBGGRR format)
            amount: Amount to add to each channel

        Returns:
            Modified Win32 BGR color
    """
    r = min(255, (color & 0xFF) + amount)
    g = min(255, ((color >> 8) & 0xFF) + amount)
    b = min(255, ((color >> 16) & 0xFF) + amount)
    return (b << 16) | (g << 8) | r


def apply_dark(color: int, amount: int) -> int:
    """
    Subtract brightness from color (matches native ColorManager.ApplyDark).

        Subtracts 'amount' from each RGB channel, floored at 0.

        Args:
            color: Win32 BGR color (0x00BBGGRR format)
            amount: Amount to subtract from each channel

        Returns:
            Modified Win32 BGR color
    """
    r = max(0, (color & 0xFF) - amount)
    g = max(0, ((color >> 8) & 0xFF) - amount)
    b = max(0, ((color >> 16) & 0xFF) - amount)
    return (b << 16) | (g << 8) | r


def modify_color(percent: int, color: int, background_color: int) -> int:
    """
    Blend a Win32 BGR color toward a background color.

    Matches native ColorManager.ModifyColor(percent, color, backgroundColor).
    """
    r = (color & 0xFF) + int(
        ((background_color & 0xFF) - (color & 0xFF)) * percent / 100
    )
    g = ((color >> 8) & 0xFF) + int(
        (((background_color >> 8) & 0xFF) - ((color >> 8) & 0xFF)) * percent / 100
    )
    b = ((color >> 16) & 0xFF) + int(
        (((background_color >> 16) & 0xFF) - ((color >> 16) & 0xFF)) * percent / 100
    )
    return (b << 16) | (g << 8) | r


def svg_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    stroke: str = "#000000",
    stroke_width: float = 1.0,
    stroke_dasharray: str | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <line> element.
    """
    parts = [
        f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"',
        f'stroke="{stroke}" stroke-width="{stroke_width:.2f}px"',
    ]
    if stroke_dasharray:
        parts.append(f'stroke-dasharray="{stroke_dasharray}"')
    for k, v in attrs.items():
        parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def svg_rect(
    x: float,
    y: float,
    width: float,
    height: float,
    stroke: str | None = "#000000",
    stroke_width: float | None = 1.0,
    fill: str | None = "none",
    fill_opacity: float | None = None,
    stroke_dasharray: str | None = None,
    rx: float | None = None,
    ry: float | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <rect> element.

    Args:
        x, y: Position
        width, height: Dimensions
        stroke: Stroke color. None to omit stroke attribute.
        stroke_width: Stroke width. None to omit.
        fill: Fill color. None to omit fill attribute.
        fill_opacity: Fill opacity. None to omit.
        stroke_dasharray: Dash pattern. None to omit.
        rx, ry: Corner radii. None to omit (sharp corners).
        **attrs: Additional attributes (e.g., vector_effect)
    """
    parts = [f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}"']
    if stroke is not None:
        parts.append(f'stroke="{stroke}"')
    if stroke_width is not None and stroke is not None:
        parts.append(f'stroke-width="{stroke_width:.2f}px"')
    if fill is not None:
        parts.append(f'fill="{fill}"')
    if fill_opacity is not None:
        parts.append(f'fill-opacity="{fill_opacity:.2f}"')
    if stroke_dasharray:
        parts.append(f'stroke-dasharray="{stroke_dasharray}"')
    if rx is not None and rx > 0:
        parts.append(f'rx="{rx:.2f}"')
    if ry is not None and ry > 0:
        parts.append(f'ry="{ry:.2f}"')
    for k, v in attrs.items():
        if v is not None:  # Skip None values
            parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def svg_circle(
    cx: float,
    cy: float,
    r: float,
    stroke: str = "#000000",
    stroke_width: float = 1.0,
    fill: str = "none",
    fill_opacity: float | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <circle> element.
    """
    parts = [
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}"',
        f'stroke="{stroke}" stroke-width="{stroke_width:.2f}px" fill="{fill}"',
    ]
    if fill_opacity is not None:
        parts.append(f'fill-opacity="{fill_opacity:.2f}"')
    for k, v in attrs.items():
        parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def svg_ellipse(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    stroke: str | None = "#000000",
    stroke_width: float | None = 1.0,
    fill: str | None = "none",
    fill_opacity: float | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <ellipse> element.

    Args:
        cx, cy: Center coordinates
        rx, ry: Radii
        stroke: Stroke color. None to omit.
        stroke_width: Stroke width. None to omit.
        fill: Fill color. None to omit (native Altium style for arcs).
    """
    parts = [f'<ellipse cx="{cx:.2f}" cy="{cy:.2f}" rx="{rx:.2f}" ry="{ry:.2f}"']
    if stroke is not None:
        parts.append(f'stroke="{stroke}"')
    if stroke_width is not None and stroke is not None:
        parts.append(f'stroke-width="{stroke_width:.2f}px"')
    if fill is not None:
        parts.append(f'fill="{fill}"')
    if fill_opacity is not None:
        parts.append(f'fill-opacity="{fill_opacity:.2f}"')
    for k, v in attrs.items():
        if v is not None:
            parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def get_point_from_angle_for_ellipse(
    cx: float, cy: float, rx: float, ry: float, angle_deg: float
) -> tuple[float, float]:
    """
    Calculate point on ellipse at given angle using Altium's POLAR FORM.

    This is NOT the standard parametric form! Altium uses a polar representation
    that gives the actual distance from center at a given angle.

    Altium's formula (from GeometryUtils.GetRawVector2FromAngleForEllipse):
        r = sqrt(1 / ((cos^2(theta)/rx^2) + (sin^2(theta)/ry^2)))
        x = cx + r * cos(theta)
        y = cy + r * sin(theta)

    vs Standard parametric form (what most tutorials teach):
        x = cx + rx * cos(theta)
        y = cy + ry * sin(theta)

    For circles (rx == ry), both give identical results.
    For ellipses (rx != ry), they differ significantly!

    Example at 45 deg with rx=12, ry=6:
        Standard: x = cx + 8.49, y = cy + 4.24
        Polar:    x = cx + 5.37, y = cy + 5.37

    Args:
        cx, cy: Center coordinates (in Altium space, Y-up)
        rx, ry: Radii (primary/x and secondary/y)
        angle_deg: Angle in degrees

    Returns:
        (x, y) point on ellipse in Altium coordinate space (Y-up)
    """
    if rx == 0 or ry == 0:
        return (cx, cy)

    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    # Polar form of ellipse equation
    # r = sqrt(1 / ((cos^2(theta)/rx^2) + (sin^2(theta)/ry^2)))
    r = math.sqrt(1.0 / ((cos_a / rx) ** 2 + (sin_a / ry) ** 2))

    return (cx + r * cos_a, cy + r * sin_a)


def svg_arc(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    start_angle: float,
    end_angle: float,
    stroke: str = "#000000",
    stroke_width: float = 1.0,
    fill: str | None = None,
    use_altium_polar_form: bool = True,
    truncate_to_int: bool = False,
    **attrs: Any,
) -> str:
    """
    Generate SVG <path> arc element.

    Args:
        cx, cy: Center coordinates (already in SVG space with Y-flipped)
        rx, ry: Radii (x and y)
        start_angle, end_angle: Angles in degrees (0=right, CCW positive)
        stroke, stroke_width: Stroke attributes
        fill: Fill color. None to omit fill attribute (native Altium style).
        use_altium_polar_form: If True, use Altium's polar form for ellipse points.
            This is critical for matching native Altium SVG output for elliptical arcs.
            Default True for native Altium compatibility.
        truncate_to_int: If True, truncate endpoint coordinates to integers.
            Matches Altium's GetPointFromAngleForEllipse which returns Point (int).
            This affects large_arc flag calculation when endpoints are close.

    Native Altium SVG Arc Algorithm:
        1. Calculate endpoints using polar form (GetPointFromAngleForEllipse)
        2. Truncate to integers (Point type)
        3. Draw from END angle to START angle with sweep=1 (clockwise in SVG)
    """
    # Calculate arc endpoints using appropriate method
    if use_altium_polar_form and rx != ry:
        # Elliptical arc: use Altium's polar form
        # Note: get_point_from_angle_for_ellipse returns Y-up coordinates
        # We need to flip Y for SVG: y_svg = cy_svg - (y_altium - cy_altium)
        # But since cx/cy passed here are already in SVG space (cy_svg),
        # and we want the Y-offset from center, we compute:
        #   In Altium space: y_offset = r * sin(theta)
        #   In SVG space: y = cy_svg - y_offset = cy_svg - r * sin(theta)

        angle_rad = math.radians(end_angle)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        r = math.sqrt(1.0 / ((cos_a / rx) ** 2 + (sin_a / ry) ** 2))
        x1 = cx + r * cos_a
        y1 = cy - r * sin_a  # Negate for SVG Y-down

        angle_rad = math.radians(start_angle)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        r = math.sqrt(1.0 / ((cos_a / rx) ** 2 + (sin_a / ry) ** 2))
        x2 = cx + r * cos_a
        y2 = cy - r * sin_a  # Negate for SVG Y-down
    else:
        # Circular arc or legacy mode: use standard parametric form
        start_rad = math.radians(start_angle)
        end_rad = math.radians(end_angle)

        # Native Altium SVG draws from END angle point to START angle point
        # with clockwise sweep (sweep=1). This matches visual arc direction
        # when Y-axis is flipped from Altium Y-up to SVG Y-down.
        x1 = cx + rx * math.cos(end_rad)  # Move to END angle point
        y1 = cy - ry * math.sin(end_rad)  # Negate for SVG Y-down
        x2 = cx + rx * math.cos(start_rad)  # Arc to START angle point
        y2 = cy - ry * math.sin(start_rad)

    # Optional: truncate to integers (matches Altium's Point type)
    if truncate_to_int:
        x1, y1 = int(x1), int(y1)
        x2, y2 = int(x2), int(y2)

    # Determine arc flags
    # Calculate angle difference first (for large_arc logic)
    angle_diff = end_angle - start_angle
    if angle_diff < 0:
        angle_diff += 360

    # Check if endpoints are identical after truncation
    # When endpoints are the same point (after rounding), native Altium draws
    # a near-full circle using large_arc=1. This happens when small angle
    # differences round to the same integer coordinate.
    endpoints_identical = (x1 == x2 and y1 == y2) if truncate_to_int else False

    if endpoints_identical:
        # Force large_arc=1 to draw the "long way" around (near-full circle)
        large_arc = 1
    else:
        # Normal case: large_arc based on angle span
        large_arc = 1 if angle_diff > 180 else 0
    # Native Altium uses sweep=1 (clockwise in SVG coordinates)
    # This is because Altium Y-up becomes SVG Y-down, inverting the direction
    sweep = 1

    # Build path matching native Altium format:
    # - M point: 4 decimal places (preserves tiny offsets from near-180 deg arcs)
    # - A radii: integers when whole, fractional (4dp) when radius_frac != 0
    # - flags: integers with comma (0 0,1 format)
    # - endpoint: always 4dp (trig produces floats; native preserves fractional precision)
    #
    # CRITICAL: The 4 decimal precision for M point is essential for near-180 deg arcs
    # like relay coils (90 deg-269.592 deg). Without it, cos(269.592 deg)~=-0.007 gets rounded
    # away and the arc becomes degenerate (start.x == end.x), making it invisible.
    #
    # When radius has fractional parts (radius_frac != 0), native Altium preserves
    # float precision for radii (e.g., A4.964,4.964).
    # When radius is whole, native uses integers (e.g., A15,15).
    # Endpoints are always fractional since they come from trig calculations on
    # center coordinates that may have CoordPoint fractional parts.
    has_fractional_radius = (rx != int(rx)) or (ry != int(ry))
    rx_str = f"{rx:.4f}" if has_fractional_radius else str(int(rx))
    ry_str = f"{ry:.4f}" if has_fractional_radius else str(int(ry))
    d = f"M{x1:.4f},{y1:.4f} A{rx_str},{ry_str} 0 {large_arc},{sweep} {x2:.4f},{y2:.4f}"

    parts = [
        f'<path d="{d}"',
        f'stroke="{stroke}" stroke-width="{stroke_width:.2f}px"',
    ]
    # Only include fill if explicitly provided (native Altium omits it for arcs)
    if fill is not None:
        parts.append(f'fill="{fill}"')
    for k, v in attrs.items():
        parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def svg_polygon(
    points: list[tuple[float, float]],
    stroke: str = "#000000",
    stroke_width: float = 1.0,
    fill: str = "none",
    fill_opacity: float | None = None,
    stroke_dasharray: str | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <polygon> element.

        If stroke is empty string or stroke_width is 0, omits stroke attributes.
        This matches native Altium SVG output for fill-only polygons.
    """
    points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    parts = [f'<polygon points="{points_str}"']

    # Only include stroke attributes if stroke is specified and width > 0
    if stroke and stroke_width > 0:
        parts.append(f'stroke="{stroke}" stroke-width="{stroke_width:.2f}px"')

    parts.append(f'fill="{fill}"')

    if fill_opacity is not None:
        parts.append(f'fill-opacity="{fill_opacity:.2f}"')
    if stroke_dasharray:
        parts.append(f'stroke-dasharray="{stroke_dasharray}"')
    for k, v in attrs.items():
        parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def svg_polyline(
    points: list[tuple[float, float]],
    stroke: str = "#000000",
    stroke_width: float = 1.0,
    fill: str = "none",
    stroke_dasharray: str | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <polyline> element.
    """
    points_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    parts = [
        f'<polyline points="{points_str}"',
        f'stroke="{stroke}" stroke-width="{stroke_width:.2f}px" fill="{fill}"',
    ]
    if stroke_dasharray:
        parts.append(f'stroke-dasharray="{stroke_dasharray}"')
    for k, v in attrs.items():
        parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def svg_path(
    d: str,
    stroke: str = "#000000",
    stroke_width: float = 1.0,
    fill: str = "none",
    fill_opacity: float | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <path> element.
    """
    parts = [
        f'<path d="{d}"',
        f'stroke="{stroke}" stroke-width="{stroke_width:.2f}px" fill="{fill}"',
    ]
    if fill_opacity is not None:
        parts.append(f'fill-opacity="{fill_opacity:.2f}"')
    for k, v in attrs.items():
        parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + "/>"


def svg_text(
    x: float,
    y: float,
    text: str,
    font_size: float = 10.0,
    font_family: str = "Arial",
    fill: str = "#000000",
    text_anchor: str | None = None,
    dominant_baseline: str | None = None,
    transform: str | None = None,
    font_weight: str | None = None,
    font_style: str | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG <text> element.

        Native Altium SVG uses 'px' suffix for font-size and xml:space="preserve".
        Native Altium does NOT use text-anchor or dominant-baseline attributes -
        it calculates exact coordinates for the text baseline position.

        Args:
            x, y: Position coordinates (baseline, left-aligned by default in SVG)
            font_size: Font size in pixels
            font_family: Font family name
            fill: Fill color
            text_anchor: SVG text-anchor (None=omit, "start"/"middle"/"end")
            dominant_baseline: SVG dominant-baseline (None=omit)
            transform: Optional SVG transform
            **attrs: Additional attributes
    """
    escaped_text = html.escape(text)
    # Use float font-size to match GDI+ measurement (fixes right-aligned text gap)
    # Native Altium SVG truncates to int, causing measurement/render mismatch
    # Format font-size: integers without decimals (102px), floats with 4 decimals (102.1234px)
    if font_size == int(font_size):
        font_size_str = f"{int(font_size)}px"
    else:
        font_size_str = f"{font_size:.4f}px"

    # Native Altium frequently emits 3-4 decimal coordinate precision for text.
    # Keep higher precision here to avoid rounding half-pixel anchors across
    # integer boundaries during oracle comparisons.
    def _fmt_coord(v: float) -> str:
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f"{v:.4f}".rstrip("0").rstrip(".")

    parts = [
        f'<text x="{_fmt_coord(x)}" y="{_fmt_coord(y)}"',
        f'font-size="{font_size_str}" font-family="{font_family}"',
        f'fill="{fill}"',
    ]
    # Only add text-anchor if explicitly specified (native Altium omits it)
    if text_anchor is not None:
        parts.append(f'text-anchor="{text_anchor}"')
    # Only add dominant-baseline if explicitly specified (native Altium omits it)
    if dominant_baseline is not None:
        parts.append(f'dominant-baseline="{dominant_baseline}"')
    # Add font-weight if specified (e.g., "bold" for bold fonts)
    if font_weight is not None:
        parts.append(f'font-weight="{font_weight}"')
    # Add font-style if specified (e.g., "italic")
    if font_style is not None:
        parts.append(f'font-style="{font_style}"')
    parts.append('xml:space="preserve"')  # Match native Altium SVG
    if transform:
        parts.append(f'transform="{transform}"')
    for k, v in attrs.items():
        # Skip None values - don't output them as attributes
        if v is not None:
            parts.append(f'{k.replace("_", "-")}="{v}"')
    return " ".join(parts) + f">{escaped_text}</text>"


# Schematic SVG uses 1 unit == 1 mil.
_SVG_UNIT_TO_MM = 0.0254
_SCH_TEXT_POLY_RENDERER = None


def _normalize_poly_font_family(font_family: str) -> str:
    """
    Extract primary family name from an SVG font-family attribute value.
    """
    if not font_family:
        return "Arial"
    primary = font_family.split(",")[0].strip()
    if (
        primary.startswith(("'", '"'))
        and primary.endswith(("'", '"'))
        and len(primary) >= 2
    ):
        primary = primary[1:-1]
    return primary or "Arial"


def _font_weight_is_bold(font_weight: str | None) -> bool:
    if font_weight is None:
        return False
    weight = str(font_weight).strip().lower()
    if weight == "bold":
        return True
    try:
        return int(weight) >= 600
    except ValueError:
        return False


def _font_style_is_italic(font_style: str | None) -> bool:
    if font_style is None:
        return False
    style = str(font_style).strip().lower()
    return style in {"italic", "oblique"}


def _resolve_poly_font_path(font_name: str, bold: bool, italic: bool) -> str | None:
    """
    Resolve schematic polytext fonts via shared style-aware font mapping.
    """
    return get_font_path_with_style(font_name, bold=bold, italic=italic)


def _append_polygon_contour_path(
    d_parts: list[str],
    contour_mm: list[tuple[float, float]],
    *,
    precision: int = 4,
) -> bool:
    """
    Append one closed contour to SVG path parts (mm/y-up -> mil/y-down).
    """
    if len(contour_mm) < 3:
        return False

    points_mm = contour_mm[:-1] if contour_mm[0] == contour_mm[-1] else contour_mm
    if len(points_mm) < 3:
        return False

    def _fmt_coord(value: float) -> str:
        scale = 10**precision
        scaled = value * scale
        nearest_half = math.floor(scaled) + 0.5
        if abs(scaled - nearest_half) <= 1e-6:
            value = nearest_half / scale
        quantum = Decimal(1).scaleb(-precision)
        rounded = Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)
        return format(rounded, f".{precision}f")

    x0 = points_mm[0][0] / _SVG_UNIT_TO_MM
    y0 = -points_mm[0][1] / _SVG_UNIT_TO_MM
    d_parts.append(f"M {_fmt_coord(x0)} {_fmt_coord(y0)}")
    for px_mm, py_mm in points_mm[1:]:
        sx = px_mm / _SVG_UNIT_TO_MM
        sy = -py_mm / _SVG_UNIT_TO_MM
        d_parts.append(f"L {_fmt_coord(sx)} {_fmt_coord(sy)}")
    d_parts.append("Z")
    return True


def _polytext_bbox_mm(
    contour_mm: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    points_mm = (
        contour_mm[:-1]
        if contour_mm and contour_mm[0] == contour_mm[-1]
        else contour_mm
    )
    xs = [px for px, _ in points_mm]
    ys = [py for _, py in points_mm]
    return (min(xs), min(ys), max(xs), max(ys))


def _polytext_contour_sort_key(
    contour_mm: list[tuple[float, float]],
) -> tuple[float, float, float, float, int]:
    left, bottom, right, top = _polytext_bbox_mm(contour_mm)
    # Canonicalize synthetic polytext path ordering for parity tests and
    # review artifacts. Order contours visually from top-to-bottom, then
    # left-to-right, independent of floating-point jitter in glyph tessellation.
    return (-top, left, -bottom, right, len(contour_mm))


def _canonicalize_polytext_contour_mm(
    contour_mm: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    points_mm = (
        contour_mm[:-1]
        if contour_mm and contour_mm[0] == contour_mm[-1]
        else contour_mm
    )
    if len(points_mm) < 3:
        return list(contour_mm)

    def point_key(point: tuple[float, float]) -> tuple[float, float, float]:
        x_mm, y_mm = point
        return (-y_mm, x_mm, y_mm)

    def rotate_best(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        start_index = min(
            range(len(points)), key=lambda index: point_key(points[index])
        )
        return points[start_index:] + points[:start_index]

    forward = rotate_best(list(points_mm))
    reverse = rotate_best(list(reversed(points_mm)))
    forward_key = [point_key(point) for point in forward]
    reverse_key = [point_key(point) for point in reverse]
    canonical = forward if tuple(forward_key) <= tuple(reverse_key) else reverse
    return canonical + [canonical[0]]


def _get_sch_text_poly_renderer() -> Any:
    global _SCH_TEXT_POLY_RENDERER
    if _SCH_TEXT_POLY_RENDERER is None:
        from .altium_text_to_polygon import TrueTypeTextRenderer

        _SCH_TEXT_POLY_RENDERER = TrueTypeTextRenderer()
    return _SCH_TEXT_POLY_RENDERER


def svg_text_poly(
    ctx: "SchSvgRenderContext",
    x: float,
    y: float,
    text: str,
    font_size: float = 10.0,
    font_family: str = "Arial",
    fill: str = "#000000",
    text_anchor: str | None = None,
    dominant_baseline: str | None = None,
    transform: str | None = None,
    font_weight: str | None = None,
    font_style: str | None = None,
    poly_target_advance: float | None = None,
    **attrs: Any,
) -> str:
    """
    Generate SVG text as polygon path geometry.

    Falls back to svg_text() when unsupported layout hints are used or when
    font tessellation fails.
    """
    if not text:
        return svg_text(
            x,
            y,
            text,
            font_size=font_size,
            font_family=font_family,
            fill=fill,
            text_anchor=text_anchor,
            dominant_baseline=dominant_baseline,
            transform=transform,
            font_weight=font_weight,
            font_style=font_style,
            **attrs,
        )

    # For now, keep exact SVG semantics for anchor/baseline via fallback.
    if (
        text_anchor is not None and text_anchor != "start"
    ) or dominant_baseline is not None:
        return svg_text(
            x,
            y,
            text,
            font_size=font_size,
            font_family=font_family,
            fill=fill,
            text_anchor=text_anchor,
            dominant_baseline=dominant_baseline,
            transform=transform,
            font_weight=font_weight,
            font_style=font_style,
            **attrs,
        )

    renderer = _get_sch_text_poly_renderer()
    family = _normalize_poly_font_family(font_family)
    is_bold = _font_weight_is_bold(font_weight)
    is_italic = _font_style_is_italic(font_style)

    # Schematic SVG font_size is already em-like (post factor conversion from pt).
    # TrueTypeTextRenderer expects cell height and applies factor internally.
    # Compensate here to avoid double factor application (squashed/wide glyphs).
    font_factor = max(get_font_factor(family, bold=is_bold, italic=is_italic), 1e-6)
    height_mm = max(float(font_size), 0.01) * _SVG_UNIT_TO_MM / font_factor
    x_mm = float(x) * _SVG_UNIT_TO_MM
    y_mm = -float(y) * _SVG_UNIT_TO_MM
    tolerance_svg = max(float(ctx.options.polygon_text_tolerance), 0.01)
    tolerance_mm = tolerance_svg * _SVG_UNIT_TO_MM

    try:
        result = renderer.render(
            text=text,
            font_name=family,
            height_mm=height_mm,
            x_mm=x_mm,
            y_mm=y_mm,
            # Match SVG <text> start-anchor semantics for schematic polytext.
            # Ink-edge anchoring makes rotated pin labels/numbers appear to
            # intersect pin lines as strings get longer.
            anchor_to_ink_edge=False,
            is_bold=is_bold,
            is_italic=is_italic,
            flatten_tolerance=tolerance_mm,
            font_resolver=_resolve_poly_font_path,
            use_hb_positioning=True,
        )
    except Exception:
        return svg_text(
            x,
            y,
            text,
            font_size=font_size,
            font_family=font_family,
            fill=fill,
            text_anchor=text_anchor,
            dominant_baseline=dominant_baseline,
            transform=transform,
            font_weight=font_weight,
            font_style=font_style,
            **attrs,
        )

    # Optional schematic parity correction:
    # keep the canonical layout anchor from the caller, then normalize the
    # polygon run's horizontal advance to the same width used by layout math.
    if poly_target_advance is not None and poly_target_advance > 0.0:
        try:
            from .altium_text_to_polygon import (
                _REFERENCE_DPI,
                _REFERENCE_SIZE,
                _find_font_path,
            )

            font_path = _find_font_path(
                family,
                is_bold,
                is_italic,
                font_resolver=_resolve_poly_font_path,
            )
            if font_path:
                face = renderer._get_face(font_path)
                upem = face.units_per_EM or 2048
                factor = renderer._get_font_factor(face, font_path)
                face.set_char_size(0, _REFERENCE_SIZE * 64, _REFERENCE_DPI, 0)
                scale = (height_mm * factor) / _REFERENCE_SIZE
                actual_advance_mm = renderer._measure_text_advance_mm(
                    text,
                    font_path,
                    face,
                    upem,
                    scale,
                    use_hb_positioning=True,
                )
                actual_advance_svg = actual_advance_mm / _SVG_UNIT_TO_MM
                if actual_advance_svg > 1e-9:
                    scale_x = float(poly_target_advance) / actual_advance_svg
                    if 0.01 < scale_x < 100.0 and abs(scale_x - 1.0) > 1e-6:
                        anchor_x_mm = x_mm
                        for char_polys in result.characters:
                            for poly in char_polys:
                                poly.outline = [
                                    (anchor_x_mm + (px - anchor_x_mm) * scale_x, py)
                                    for px, py in poly.outline
                                ]
                                poly.holes = [
                                    [
                                        (anchor_x_mm + (px - anchor_x_mm) * scale_x, py)
                                        for px, py in hole
                                    ]
                                    for hole in poly.holes
                                ]
        except Exception:
            # Preserve rendering even if normalization fails.
            pass

    d_parts: list[str] = []
    for char_polys in result.characters:
        char_contours: list[list[tuple[float, float]]] = []
        for poly in char_polys:
            char_contours.append(poly.outline)
            char_contours.extend(poly.holes)

        for contour in sorted(char_contours, key=_polytext_contour_sort_key):
            _append_polygon_contour_path(
                d_parts,
                _canonicalize_polytext_contour_mm(contour),
                precision=3,
            )

    if not d_parts:
        return svg_text(
            x,
            y,
            text,
            font_size=font_size,
            font_family=font_family,
            fill=fill,
            text_anchor=text_anchor,
            dominant_baseline=dominant_baseline,
            transform=transform,
            font_weight=font_weight,
            font_style=font_style,
            **attrs,
        )

    d_attr = html.escape(" ".join(d_parts), quote=True)
    parts = [
        "<path",
        f'd="{d_attr}"',
        f'fill="{html.escape(str(fill), quote=True)}"',
        'fill-rule="evenodd"',
        'stroke="none"',
        'data-text-source="polytext"',
        f'data-text-anchor-x="{float(x):.4f}"',
        f'data-text-anchor-y="{float(y):.4f}"',
        f'data-text-value="{html.escape(text, quote=True)}"',
        f'data-text-font-family="{html.escape(str(font_family), quote=True)}"',
        f'data-text-font-size="{float(font_size):.4f}"',
    ]
    if transform:
        parts.append(f'transform="{html.escape(str(transform), quote=True)}"')
    for k, v in attrs.items():
        if v is not None:
            attr_key = k.replace("_", "-")
            parts.append(f'{attr_key}="{html.escape(str(v), quote=True)}"')
    return " ".join(parts) + "/>"


def svg_text_or_poly(
    ctx: "SchSvgRenderContext",
    x: float,
    y: float,
    text: str,
    font_size: float = 10.0,
    font_family: str = "Arial",
    fill: str = "#000000",
    text_anchor: str | None = None,
    dominant_baseline: str | None = None,
    transform: str | None = None,
    font_weight: str | None = None,
    font_style: str | None = None,
    poly_target_advance: float | None = None,
    **attrs: Any,
) -> str:
    """
    Dispatch schematic text rendering to SVG text or polygon path output.
    """
    if ctx.options.text_as_polygons:
        return svg_text_poly(
            ctx,
            x,
            y,
            text,
            font_size=font_size,
            font_family=font_family,
            fill=fill,
            text_anchor=text_anchor,
            dominant_baseline=dominant_baseline,
            transform=transform,
            font_weight=font_weight,
            font_style=font_style,
            poly_target_advance=poly_target_advance,
            **attrs,
        )
    return svg_text(
        x,
        y,
        text,
        font_size=font_size,
        font_family=font_family,
        fill=fill,
        text_anchor=text_anchor,
        dominant_baseline=dominant_baseline,
        transform=transform,
        font_weight=font_weight,
        font_style=font_style,
        **attrs,
    )


def svg_group(elements: list[str], transform: str | None = None, **attrs: Any) -> str:
    """
    Generate SVG <g> group element.
    """
    parts = ["<g"]
    if transform:
        parts.append(f'transform="{transform}"')
    for k, v in attrs.items():
        parts.append(f'{k.replace("_", "-")}="{v}"')
    inner = "\n  ".join(elements)
    return " ".join(parts) + f">\n  {inner}\n</g>"


def wrap_with_unique_id(
    elements: list[str], unique_id: str | None, generate_if_missing: bool = True
) -> list[str]:
    """
    Wrap SVG elements in a group with the record's unique_id.

    Native Altium SVG export wraps each record's elements in:
        <g id = "{unique_id}">...</g>

    This enables element-by-element comparison between Python and native SVG.

    Some record types do not store UniqueID in the file, but native Altium
    still emits IDs for them during SVG export. When generate_if_missing=True,
    this helper generates an ID to preserve the same SVG structure.

    Args:
        elements: List of SVG element strings from a legacy record rendering helper.
        unique_id: 8-character alphanumeric ID from record, or None
        generate_if_missing: If True, generate a unique_id when None is provided
                            (default True to match native Altium behavior)

    Returns:
        List containing a single wrapped group string, or original elements if no ID
    """
    if not elements:
        return []

    if not unique_id:
        if generate_if_missing:
            # Generate unique_id for records that don't have one (like Junction)
            # This matches native Altium SVG export behavior
            from .altium_record_types import generate_unique_id

            unique_id = generate_unique_id()
        else:
            return elements

    # Join elements and wrap in group with id
    # Note: Altium uses `id = "{id}"` format with space before =
    inner = "".join(elements)
    return [f'<g id = "{unique_id}">{inner}</g>']


# ============================================================================
# BEZIER CURVE RENDERING
# ============================================================================


def bezier_to_svg_path(points: list[tuple[float, float]]) -> str:
    """
    Convert bezier control points to SVG path data.

    Bezier curves in Altium use groups of 4 points:
    (endpoint1, control1, control2, endpoint2)

    Args:
        points: List of (x, y) tuples, must be 4n points where n >= 1

    Returns:
        SVG path d attribute string
    """
    if len(points) < 4:
        return ""

    d_parts = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]

    for i in range(0, len(points) - 3, 3):
        c1 = points[i + 1]
        c2 = points[i + 2]
        end = points[i + 3]
        d_parts.append(
            f"C {c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {end[0]:.2f} {end[1]:.2f}"
        )

    return " ".join(d_parts)


def flatten_bezier(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    segments: int = 32,
) -> list[tuple[float, float]]:
    """
    Flatten cubic Bezier curve to line segments using Bernstein polynomials.

    This matches Altium's internal algorithm from native implementation.
    Native Altium SVG export uses ~32 segments (via GDI+ flattening).

    The cubic Bezier formula is:
        P(t) = (1-t)^3 * P0 + 3(1-t)^2*t * P1 + 3(1-t)*t^2 * P2 + t^3 * P3

    Args:
        p0: Start point (x, y)
        p1: Control point 1 (x, y)
        p2: Control point 2 (x, y)
        p3: End point (x, y)
        segments: Number of line segments (default 32)

    Returns:
        List of (x, y) points including start and end (segments + 1 points)
    """
    points = []
    for i in range(segments + 1):
        t = i / segments
        t2 = t * t
        t3 = t2 * t

        # Bernstein basis polynomials
        b0 = (1 - t) ** 3  # (1-t)^3
        b1 = 3 * (1 - t) ** 2 * t  # 3(1-t)^2*t
        b2 = 3 * (1 - t) * t2  # 3(1-t)*t^2
        b3 = t3  # t^3

        x = b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0]
        y = b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1]
        points.append((x, y))

    return points


# ============================================================================
# TEXT WITH OVERLINE (BAR) RENDERING
# ============================================================================


def render_text_with_overline(
    text: str,
    x: float,
    y: float,
    font_size_px: float,
    font_family: str = "Arial",
    bold: bool = False,
    italic: bool = False,
    fill: str = "#000000",
    stroke_color: str | None = None,
    stroke_width: float = 1.0,
) -> tuple[str, list[str]]:
    """
    Render text with overlines (bars) for characters followed by backslash.

    Altium uses backslash notation for inverted/active-low signals:
    - ``'R\\S\\T'`` displays as RST with bars over all three characters
    - ``'C\\S'`` displays as CS with bars over both characters
    - ``'EN\\ABLE'`` displays as ENABLE with bar over just 'EN'

    Algorithm:
    1. If no backslash, return text as-is with no overlines
    2. Build clean string without backslashes
    3. For each backslash, measure text up to that position
    4. Draw line segment above the preceding character

    The overline is positioned at y - font_size (above the text baseline).
    Overline stroke width is always 1px (matching native Altium).

    Args:
        text: Text string potentially containing backslashes
        x: X coordinate for text position
        y: Y coordinate for text baseline
        font_size_px: Font size in pixels
        font_family: Font family name
        bold: Whether font is bold
        italic: Whether font is italic
        fill: Text color
        stroke_color: Overline stroke color (defaults to fill if None)
        stroke_width: Overline stroke width in pixels (default 1.0)

    Returns:
        Tuple of (clean_text, overline_elements):
        - clean_text: Text with backslashes removed
        - overline_elements: List of SVG line elements for overlines
    """
    from .altium_text_metrics import measure_text_width

    # No backslash = no overlines needed
    if "\\" not in text:
        return text, []

    # Use fill color for overlines if not specified
    line_color = stroke_color if stroke_color else fill

    # Mirror DrawOverLine(): iterate on the working string and decide whether
    # each segment is measured with trailing-side-bearing based on whether
    # characters remain after removing the current backslash.
    working_text = text
    overline_segments: list[tuple[str, str, bool]] = []

    while True:
        slash_idx = working_text.find("\\")
        if slash_idx < 0:
            break

        prefix = working_text[:slash_idx]
        working_text = working_text[:slash_idx] + working_text[slash_idx + 1 :]

        if slash_idx <= 0:
            continue

        include_rsb = slash_idx < len(working_text)
        overline_segments.append(
            (
                prefix,
                working_text[slash_idx - 1],
                include_rsb,
            )
        )

    clean_text = working_text

    if not overline_segments or not clean_text:
        return clean_text, []

    # Calculate overline line elements
    overline_elements = []
    # Altium uses INTEGER font size for overbar Y offset (truncated, not rounded).
    truncated_font_size = int(font_size_px)
    overline_y = y - truncated_font_size

    for prefix_text, char_text, include_rsb in overline_segments:
        width_to_segment_end = measure_text_width(
            prefix_text,
            font_size_px,
            font_family,
            bold,
            italic,
            include_rsb=include_rsb,
        )
        char_width = measure_text_width(
            char_text,
            font_size_px,
            font_family,
            bold,
            italic,
            include_rsb=include_rsb,
        )

        x1 = x + width_to_segment_end - char_width
        x2 = x + width_to_segment_end

        overline_elements.append(
            f'<line x1="{x2:.4f}" y1="{overline_y}" x2="{x1:.4f}" y2="{overline_y}" '
            f'stroke="{line_color}" stroke-width="{stroke_width}px"/>'
        )

    return clean_text, overline_elements


# ============================================================================
# SVG CONTEXT FOR RENDERING
# ============================================================================


@dataclass
class SchSvgRenderContext:
    """
    Rendering context for schematic SVG emitters.

    Carries transforms, options, placement state, and font access helpers.
    """

    # Transform options
    offset_x: float = 0.0  # Offset in mils
    offset_y: float = 0.0  # Offset in mils
    scale: float = 1.0  # Scale factor for coordinates
    stroke_scale: float | None = (
        None  # Scale factor for stroke widths (defaults to scale)
    )
    flip_y: bool = True  # Flip Y axis for SVG

    # Sheet dimensions (in mils)
    # For SchDoc: svg_y = sheet_height - altium_y (produces positive SVG coords)
    sheet_height: float = 0.0  # Sheet height in mils (from sheet record)
    sheet_width: float = 0.0  # Sheet width in mils (for port arrow direction)

    # Sheet area color for diffpair shadow calculation (Win32 BGR format)
    # Used by ParameterSet._render_diffpair() to calculate shadow colors
    # Default white (0xFFFFFF) matches most schematics
    sheet_area_color: int = 0xFFFFFF

    # Component placement transforms (in Altium 10-mil units)
    placement_x: float = 0.0  # Component origin X (Altium units)
    placement_y: float = 0.0  # Component origin Y (Altium units)
    rotation: int = 0  # Rotation in degrees (0, 90, 180, 270)
    mirror: bool = False  # Horizontal mirror (Y-axis flip)

    # Basic rendering options
    default_stroke: str = "#000000"
    default_fill: str = "#FFFFC0"
    show_pins: bool = True
    show_pin_names: bool = True
    show_pin_numbers: bool = True
    show_pin_direction: bool = (
        True  # Render electrical type glyphs (INPUT/OUTPUT/IO arrows)
    )

    # Metadata options (for future interactivity)
    include_metadata: bool = False  # Add data-* attributes (netnames, refs)
    include_ids: bool = False  # Add unique IDs for elements

    # Pixel-perfect rendering options
    use_altium_colors: bool = True  # Use exact Altium color scheme
    dpi: float = 96.0  # Output DPI for sizing calculations
    strict_mode: bool = True  # Match native Altium SVG exactly (rect for ellipse, etc.)

    # Layer/group tracking (for grouping by layer or net)
    current_layer: str = ""  # Current layer name
    current_net: str = ""  # Current net name (for wires)

    # Font manager for document-level font lookup
    # Replaces raw fonts dict - all font access goes through FontIDManager
    font_manager: "FontIDManager | None" = None

    # Parameter substitution (for TextFrame =PARAM_NAME strings)
    # Keys are parameter names (case-insensitive), values are the text to display
    parameters: dict[str, str] = field(default_factory=dict)

    # Connection points for junction rendering
    # Set of (x, y) tuples in Altium coordinates where wires/buses connect
    # Used by wire/bus to_svg to render junction dots at intersection points
    connection_points: set[tuple[int, int]] = field(default_factory=set)

    # Persisted manual junction record points in Altium coordinates.
    # Native SVG keeps these dots owned by the junction objects instead of
    # duplicating them inside wire groups. Transient imported/grid-generated
    # junctions (IndexInSheet < 0) still rely on the wire-local dots.
    explicit_junction_points: set[tuple[int, int]] = field(default_factory=set)

    # Signal harness junction points - where harness junctions should be rendered
    # Set of (x, y) tuples in Altium coordinates where one harness's start point
    # lies geometrically on another harness's line segment (T-junction)
    harness_junction_points: set[tuple[int, int]] = field(default_factory=set)

    # Compiled page-port harness state.
    # Native Altium treats a normal port as a harness object when one of its
    # connection endpoints touches a signal harness segment. The record itself
    # does not necessarily carry HarnessType/HarnessColor in that state.
    harness_port_colors: dict[str, int] = field(default_factory=dict)

    # Compiled sheet-entry harness state. Sheet entries connected to signal
    # harnesses are rendered with harness-object style even when the stored
    # sheet-entry record does not carry HarnessType.
    harness_sheet_entry_colors: dict[str, int] = field(default_factory=dict)

    # Rendering options (overrides, customizations)
    options: SchSvgRenderOptions = field(default_factory=SchSvgRenderOptions)

    # Deferred junction elements (for ALWAYS_ON_TOP z-order)
    # When options.junction_z_order is ALWAYS_ON_TOP, junction elements are
    # collected here instead of being rendered inline, then rendered at the
    # end of the SVG so they appear on top of ALL other elements.
    deferred_junctions: list[str] = field(default_factory=list)

    # Component color overrides (when component.override_colors=True)
    # Native Altium: Fills=AreaColor, Lines=Color, Pins=PinColor
    # When set, graphics should use these instead of their own colors
    area_color_override: int | None = None  # For fills (rectangles, polygons)
    line_color_override: int | None = None  # For lines/strokes
    pin_color_override: int | None = None  # For pins

    # ClipPath counter for text frame clipping
    # Native Altium SVG uses ClipRect1, ClipRect2, etc. for clip paths
    clip_rect_counter: int = 0

    # Project-level parameters (from PrjPcb file)
    # Used when schematic-level parameters don't have a match
    project_parameters: dict[str, str] = field(default_factory=dict)

    # Compile mask bounds for color blending
    # List of (min_x, min_y, max_x, max_y) tuples in Altium coordinates
    # Objects under compile masks have their colors blended toward background
    compile_mask_bounds: list[tuple[int, int, int, int]] = field(default_factory=list)

    # Background color for compile mask color blending (#RRGGBB format)
    # Default is Altium's cream sheet color
    background_color: str = "#FFFCF8"

    # Document path for resolving relative image paths
    # Used by image rendering to find original files on disk (preserving transparency)
    document_path: str | None = None

    # SchLib mode flag - affects pin text rendering
    # When True: Pin designator/name text is NOT rotated (matches native Altium SchLib SVG export)
    # When False (default): Pin text follows rotation rules based on pin orientation
    schlib_mode: bool = False

    # Component wrapping mode
    # When True: Wrap each component in a <g id="COMP_UNIQUE_ID" data-designator="R1">
    # When False (default): Flat SVG matching Altium's native output (for validation tests)
    # Use True for enriched output (HTML viewer, altium_cruncher CLI)
    wrap_components: bool = False

    # Object definitions for custom power port graphics
    # Dict mapping ObjectDefinitionId GUID -> list of child primitive raw records
    # Parsed from the ObjectDefinitions OLE stream in SchDoc files
    object_definitions: dict[str, list[dict]] = field(default_factory=dict)

    # Wire segments in Altium coordinates.
    # Used for compile-mask-aware net label rendering.
    wire_segments: list[tuple[int, int, int, int]] = field(default_factory=list)

    # Component-level compile mask state for child graphics/text.
    # None means "not rendering inside a component-specific context".
    component_compile_masked: bool | None = None

    # Native SVG export mode flag.
    # This captures quirks that exist in Altium's SVG export surface but are
    # intentionally not part of the on-screen renderer behavior.
    native_svg_export: bool = False

    def use_compile_mask_visual_overlay(self) -> bool:
        return (
            self.options.compile_mask_render_mode
            == SchCompileMaskRenderMode.COMPILED_VISUAL
            and bool(self.compile_mask_bounds)
        )

    def next_clip_rect_id(self) -> str:
        """
        Get next unique clip rect ID for text frame clipping.
        """
        self.clip_rect_counter += 1
        return f"ClipRect{self.clip_rect_counter}"

    def substitute_parameters(self, text: str) -> str:
        """
        Substitute parameter references in text.

        Altium uses =PARAM_NAME syntax for parameter placeholders.
        Supports:
        - Simple: "=ENGINEER" -> "John Smith"
        - Compound: "=SheetNumber of =SheetTotal" -> "1 of 3"
        - Special params: "=VariantName" -> "[No Variations]" when not set

        Priority order:
        1. Schematic-level parameters (self.parameters)
        2. Project-level parameters (self.project_parameters)
        3. System defaults (e.g., VariantName)
        4. Keep original text if not found

        Args:
            text: Text that may contain parameter references

        Returns:
            Text with parameter references replaced
        """
        import re

        if not self.options.substitute_parameters or not text or "=" not in text:
            return text

        # System parameters with special default values
        system_defaults = {
            "variantname": "[No Variations]",
        }

        def lookup_case_insensitive(
            values: dict[str, str], param_lower: str
        ) -> str | None:
            for key, value in values.items():
                if key.lower() == param_lower:
                    return value
            return None

        def allow_star_project_fallback(param_lower: str) -> bool:
            return (
                self.options.fallback_project_parameters_for_star
                or param_lower in NATIVE_SYSTEM_STAR_PROJECT_FALLBACKS
            )

        def build_expression_parameters() -> dict[str, str]:
            resolved: dict[str, str] = {}
            for key, value in self.parameters.items():
                key_lower = key.lower()
                if value == "*" and allow_star_project_fallback(key_lower):
                    project_value = lookup_case_insensitive(
                        self.project_parameters, key_lower
                    )
                    if project_value:
                        resolved[key] = project_value
                        continue
                resolved[key] = value

            for key, value in system_defaults.items():
                resolved.setdefault(key, value)

            return resolved

        if (
            text.startswith("=")
            and "=" not in text[1:]
            and "+" not in text
            and "'" not in text
        ):
            direct_name = text[1:].strip()
            if direct_name:
                direct_lower = direct_name.lower()
                schematic_value = lookup_case_insensitive(self.parameters, direct_lower)
                if schematic_value == text:
                    schematic_value = None
                if schematic_value == "*":
                    if (
                        allow_star_project_fallback(direct_lower)
                        and self.project_parameters
                    ):
                        project_value = lookup_case_insensitive(
                            self.project_parameters, direct_lower
                        )
                        if project_value is not None:
                            return project_value
                    return "*"
                if schematic_value is not None:
                    if (
                        isinstance(schematic_value, str)
                        and schematic_value.startswith("=")
                        and "=" not in schematic_value[1:]
                        and "+" not in schematic_value
                        and "'" not in schematic_value
                    ):
                        resolved_value = self.substitute_parameters(schematic_value)
                        if resolved_value != schematic_value:
                            return resolved_value
                    return schematic_value
                project_value = lookup_case_insensitive(
                    self.project_parameters, direct_lower
                )
                if project_value is not None:
                    return project_value
                if direct_lower in system_defaults:
                    return system_defaults[direct_lower]

        if (
            self.options.fallback_project_parameters_for_star
            and text.startswith("=")
            and ("+" in text or "'" in text)
        ):
            from .altium_netlist_common import _evaluate_altium_expression

            return _evaluate_altium_expression(text[1:], build_expression_parameters())

        def replace_param(match: re.Match) -> str:
            param_name = match.group(1)
            param_lower = param_name.lower()

            # 1. Look up in schematic parameters (case-insensitive)
            schematic_value = lookup_case_insensitive(self.parameters, param_lower)

            # 2. Handle '*' as optional fallback to project parameters.
            # Native SVG export resolves '*' through project parameters, while
            # the on-screen geometry/oracle path keeps the schematic '*' literal.
            if schematic_value == "*":
                if allow_star_project_fallback(param_lower) and self.project_parameters:
                    project_value = lookup_case_insensitive(
                        self.project_parameters, param_lower
                    )
                    if project_value:
                        return project_value
                return "*"

            # 3. Return schematic value if found.
            # Empty-string schematic parameters are authoritative in native Altium
            # and must not fall through to project-level values.
            if schematic_value is not None:
                if schematic_value == match.group(0):
                    schematic_value = None
                elif (
                    isinstance(schematic_value, str)
                    and schematic_value.startswith("=")
                    and "=" not in schematic_value[1:]
                    and "+" not in schematic_value
                    and "'" not in schematic_value
                ):
                    resolved_value = self.substitute_parameters(schematic_value)
                    if resolved_value != schematic_value:
                        return resolved_value
            if schematic_value is not None:
                return schematic_value

            # 4. Look up in project parameters (for params not in schematic)
            project_value = lookup_case_insensitive(
                self.project_parameters, param_lower
            )
            if project_value:
                return project_value

            # 5. Check for system default
            if param_lower in system_defaults:
                return system_defaults[param_lower]

            # 6. Return original text if no value found
            return match.group(0)

        # Match =PARAM_NAME patterns (alphanumeric + underscore)
        result = re.sub(r"=([A-Za-z_][A-Za-z0-9_]*)", replace_param, text)
        return result

    def is_under_compile_mask(self, x: int, y: int) -> bool:
        """
        Check if a point is covered by any compile mask.

        Args:
            x, y: Point coordinates in Altium coordinates

        Returns:
            True if point is within any compile mask bounds
        """
        for min_x, min_y, max_x, max_y in self.compile_mask_bounds:
            if min_x <= x <= max_x and min_y <= y <= max_y:
                return True
        return False

    def _compile_mask_contains_point(
        self,
        bounds: tuple[int, int, int, int],
        x: int | float,
        y: int | float,
    ) -> bool:
        min_x, min_y, max_x, max_y = bounds
        return min_x <= x <= max_x and min_y <= y <= max_y

    def is_fully_within_compile_mask(
        self,
        points: list[tuple[int | float, int | float]],
    ) -> bool:
        """
        Check whether all points lie within the same compile mask.

        This is intentionally stricter than simple intersection. Native
        compilation masking does not dim objects that merely straddle a mask.
        """
        if not points:
            return False

        for bounds in self.compile_mask_bounds:
            if all(self._compile_mask_contains_point(bounds, x, y) for x, y in points):
                return True

        return False

    def is_segment_fully_under_compile_mask(
        self,
        x1: int | float,
        y1: int | float,
        x2: int | float,
        y2: int | float,
    ) -> bool:
        return self.is_fully_within_compile_mask([(x1, y1), (x2, y2)])

    def is_rect_fully_under_compile_mask(
        self,
        min_x: int | float,
        min_y: int | float,
        max_x: int | float,
        max_y: int | float,
    ) -> bool:
        return self.is_fully_within_compile_mask(
            [
                (min_x, min_y),
                (min_x, max_y),
                (max_x, min_y),
                (max_x, max_y),
            ]
        )

    def _point_on_segment(
        self,
        px: int | float,
        py: int | float,
        x1: int | float,
        y1: int | float,
        x2: int | float,
        y2: int | float,
    ) -> bool:
        # Fast bounding-box reject.
        if px < min(x1, x2) or px > max(x1, x2) or py < min(y1, y2) or py > max(y1, y2):
            return False

        # Collinearity test.
        cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
        return abs(cross) < 1e-6

    def get_connected_wire_mask_state(self, x: int, y: int) -> bool | None:
        connected_found = False

        for x1, y1, x2, y2 in self.wire_segments:
            if not self._point_on_segment(x, y, x1, y1, x2, y2):
                continue

            connected_found = True
            if self.is_segment_fully_under_compile_mask(x1, y1, x2, y2):
                return True

        if connected_found:
            return False

        return None

    def apply_compile_mask_color(self, color: str, masked: bool) -> str:
        if masked:
            return self.get_masked_color(color)
        return color

    def get_masked_color(self, color: str = "#000000") -> str:
        """
        Compute the color for an object under a compile mask.

        From analyzed Altium ColorManager.GetCompilationMaskedColor:
        - Start with Gray (128, 128, 128)
        - Blend 50% toward background color

        Result = Gray + (Background - Gray) * 0.5

        Note: The original object color is IGNORED - all masked objects
        get the same masked color (blended gray toward background).

        Args:
            color: Original object color (ignored, kept for API consistency)

        Returns:
            Masked color in #RRGGBB format
        """
        if self.use_compile_mask_visual_overlay():
            return color

        # Parse background color
        bg = self.background_color
        if bg.startswith("#") and len(bg) == 7:
            bg_r = int(bg[1:3], 16)
            bg_g = int(bg[3:5], 16)
            bg_b = int(bg[5:7], 16)
        else:
            # Default to cream if invalid
            bg_r, bg_g, bg_b = 255, 252, 248

        # Compute masked color: Gray(128) + (BG - Gray) * 50%
        gray = 128
        r = int(gray + (bg_r - gray) * 0.5)
        g = int(gray + (bg_g - gray) * 0.5)
        b = int(gray + (bg_b - gray) * 0.5)

        return f"#{r:02X}{g:02X}{b:02X}"

    def transform_point(self, x: int | float, y: int | float) -> tuple[float, float]:
        """
        Transform Altium coordinates to SVG.

        If placement transforms are set (placement_x/y, rotation, mirror),
        the point is first transformed relative to component origin, then
        converted to SVG coordinates.

        Transform order (matches Altium):
        1. Apply mirror (negate X if mirrored)
        2. Apply rotation around origin
        3. Add placement offset
        4. Convert to SVG coordinates

        COORDINATE SYSTEM (from Altium native implementation):
        - SchDoc coordinates are already in mils (1:1 mapping, no 10x scale)
        - SchLib coordinates are in 10-mil units (need 10x conversion)
        - SVG Y transform: svg_y = sheet_height - altium_y (positive values)
        - X transform: svg_x = altium_x (direct mapping)
        """
        # Start with input coordinates
        px, py = float(x), float(y)

        # If we have component placement transforms, apply them
        if (
            self.placement_x != 0
            or self.placement_y != 0
            or self.rotation != 0
            or self.mirror
        ):
            # 1. Mirror (horizontal flip = negate X)
            if self.mirror:
                px = -px

            # 2. Rotate around origin
            if self.rotation != 0:
                px, py = self._rotate_point(px, py, self.rotation)

            # 3. Add placement offset (in Altium units)
            px += self.placement_x
            py += self.placement_y

        # Convert to SVG coordinates using Altium's InvertYTransform formula
        # SchDoc uses 1:1 mils mapping (no 10x multiplier)
        if self.sheet_height > 0:
            # Native Altium SVG: svg_y = sheet_height - altium_y
            svg_x = (px + self.offset_x) * self.scale
            svg_y = (self.sheet_height - py + self.offset_y) * self.scale
        else:
            # Fallback to old behavior if sheet_height not set
            svg_x = altium_to_svg_x(int(px), self.offset_x, self.scale)
            svg_y = altium_to_svg_y(int(py), self.offset_y, self.scale, self.flip_y)
        return (svg_x, svg_y)

    def _rotate_point(self, x: float, y: float, degrees: int) -> tuple[float, float]:
        """
        Rotate point around origin by degrees (0, 90, 180, 270).
        """
        if degrees == 0:
            return (x, y)
        elif degrees == 90:
            return (-y, x)
        elif degrees == 180:
            return (-x, -y)
        elif degrees == 270:
            return (y, -x)
        else:
            # General rotation for non-90-degree angles
            import math

            rad = math.radians(degrees)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)

    def transform_coord(self, coord: CoordPoint) -> tuple[float, float]:
        """
        Transform CoordPoint to SVG coordinates.
        """
        return self.transform_point(coord.x, coord.y)

    def transform_coord_precise(self, coord: CoordPoint) -> tuple[float, float]:
        """
        Transform CoordPoint to SVG coordinates with sub-pixel precision.

                Uses fractional coordinates (x_frac, y_frac) for smooth polygon edges.
                Native Altium SVG uses 4 decimal places for polygon points.
        """
        # Full precision: x + x_frac/100000 (frac is in 100000ths)
        x_precise = coord.x + coord.x_frac / 100000.0
        y_precise = coord.y + coord.y_frac / 100000.0
        return self.transform_point(x_precise, y_precise)

    def transform_length(self, length_units: int) -> float:
        """
        Transform length from Altium units to SVG units (mils).
        """
        return length_units * 10.0 * self.scale

    def get_stroke_scale(self) -> float:
        """
        Get scale factor for stroke widths.

                Returns stroke_scale if set, otherwise scale.
                For SchLib symbol rendering, stroke_scale should be set to 1.0
                to keep strokes at fixed pixel sizes regardless of viewport scale.
        """
        return self.stroke_scale if self.stroke_scale is not None else self.scale

    def with_placement(
        self,
        x: float = 0.0,
        y: float = 0.0,
        rotation: int = 0,
        mirror: bool = False,
    ) -> "SchSvgRenderContext":
        """
        Create a new context with component placement transforms.

        Use this when rendering symbol children (pins, graphics) that need
        to be positioned at a component's placement location.

        Args:
            x: Component origin X (in Altium 10-mil units)
            y: Component origin Y (in Altium 10-mil units)
            rotation: Rotation in degrees (0, 90, 180, 270)
            mirror: Whether component is horizontally mirrored

        Returns:
            New SchSvgRenderContext with placement transforms set
        """
        new_ctx = self.copy()
        new_ctx.placement_x = x
        new_ctx.placement_y = y
        new_ctx.rotation = rotation
        new_ctx.mirror = mirror
        return new_ctx

    def with_color_overrides(
        self,
        area_color: int | None = None,
        line_color: int | None = None,
        pin_color: int | None = None,
    ) -> "SchSvgRenderContext":
        """
        Create a new context with component color overrides.

        Use this when rendering component graphics that should use the
        component's colors instead of their own (when override_colors=True).

        Native Altium (native implementation:442-447):
            OverideAreaColor = component.AreaColor  # For fills
            OverideLineColor = component.Color      # For lines/strokes
            OveridePinColor = component.PinColor    # For pins

        Args:
            area_color: The component's area_color for fills
            line_color: The component's color for lines/strokes
            pin_color: The component's pin_color for pins

        Returns:
            New SchSvgRenderContext with color overrides set
        """
        new_ctx = self.copy()
        new_ctx.area_color_override = area_color
        new_ctx.line_color_override = line_color
        new_ctx.pin_color_override = pin_color
        return new_ctx

    def with_component_masking(self, masked: bool | None) -> "SchSvgRenderContext":
        new_ctx = self.copy()
        new_ctx.component_compile_masked = masked
        return new_ctx

    def copy(self) -> "SchSvgRenderContext":
        """
        Create a copy of this context.
        """
        return replace(self)

    def get_line_width(self, width: LineWidth) -> float:
        """
        Get SVG line width in mils.
        """
        return LINE_WIDTH_MILS.get(width, DEFAULT_LINE_WIDTH) * self.scale

    def get_junction_size(self, size: int = 2) -> float:
        """
        Get junction dot diameter in mils (scaled).
        """
        return JUNCTION_SIZE_MILS.get(size, DEFAULT_JUNCTION_SIZE) * self.scale

    def get_bus_width(self) -> float:
        """
        Get bus line width in mils (scaled).
        """
        return BUS_WIDTH_MILS * self.scale

    def _get_display_font_name(self, font_name: str) -> str:
        """
        Get the display font name, applying fallback if font is not available.

        Per Altium's native implementation: when the requested font is not installed,
        Altium catches the exception and falls back to GenericFontFamilies.SansSerif,
        which maps to "Microsoft Sans Serif" on Windows.

        Args:
            font_name: Original font name from the file

        Returns:
            font_name if available on system, or FONT_FALLBACK if not
        """
        resolution = resolve_font_with_style(font_name)
        if resolution.path is not None and resolution.resolved_family is not None:
            return resolution.resolved_family
        # Font not available - use fallback like Altium does
        return FONT_FALLBACK

    def _get_onscreen_font_override(
        self,
        font_name: str,
    ) -> dict[str, float | str] | None:
        """
        Return AD25-backed on-screen override metadata for known custom fonts.
        """
        if (
            self.options.truncate_font_size_for_baseline
            or not self.options.use_onscreen_font_oracle_overrides
        ):
            return None
        font_name_lower = font_name.lower()
        # Keep Old Stamper on the real asset font when it is available. The
        # legacy override is still useful as a fallback when the asset is
        # unresolved, but the AD25 geometry oracle for polling_station proves
        # the on-screen path uses Old Stamper when the font can actually load.
        if font_name_lower == "old stamper" and get_font_path(font_name) is not None:
            return None
        return ONSCREEN_FONT_ORACLE_OVERRIDES.get(font_name_lower)

    def get_font_info(self, font_id: int) -> tuple[str, float, bool, bool, bool]:
        """
        Resolve font information by ID via FontIDManager.

        Args:
            font_id: Font ID from record (1-based)

        Returns a display family name and rendered pixel size plus style flags.
        """
        if self.font_manager:
            resolved_font_id = self._resolve_font_id(font_id)
            font = self.font_manager.get_font_info(resolved_font_id)
            if font:
                pt_size = float(font.get("size", 10))
                font_name = font.get("name", "Times New Roman")
                is_bold = bool(font.get("bold", False))
                is_italic = bool(font.get("italic", False))
                override = self._get_onscreen_font_override(font_name)
                if override is not None:
                    return (
                        str(override["display_name"]),
                        pt_size * float(override["factor"]),
                        is_bold,
                        is_italic,
                        bool(font.get("underline", False)),
                    )
                display_font_name = self._get_display_font_name(font_name)
                # Issue 5 Fix: Use font-specific factor for pt-to-px conversion
                # Different fonts have different Em/Cell ratios:
                #   Arial: factor ~= 0.895 (close to 8/9)
                #   Arial Black: factor ~= 0.709 (significantly different!)
                font_size_px = self._pt_to_px(
                    pt_size,
                    display_font_name,
                    is_bold,
                    is_italic,
                )
                return (
                    display_font_name,
                    font_size_px,
                    is_bold,
                    is_italic,
                    bool(font.get("underline", False)),
                )
        return self.get_system_font_info()

    def _resolve_font_id(self, font_id: int) -> int:
        if int(font_id or 0) > 0:
            return int(font_id)
        if self.font_manager and hasattr(self.font_manager, "get_default_font_id"):
            return int(self.font_manager.get_default_font_id())
        return 1

    def get_font_size_for_width(self, font_id: int) -> float:
        """
        Get font size in pixels for text width measurement via FontIDManager.

        Uses font-specific factor for accurate width calculation (Issue 5 fix).

        Args:
            font_id: Font ID from record (1-based)

        Returns:
            Font size in pixels (float) for text width measurement

        Note: all font access goes through FontIDManager.
        """
        if self.font_manager:
            resolved_font_id = self._resolve_font_id(font_id)
            font = self.font_manager.get_font_info(resolved_font_id)
            if font:
                pt_size = float(font.get("size", 10))
                font_name = font.get("name", "Times New Roman")
                is_bold = bool(font.get("bold", False))
                is_italic = bool(font.get("italic", False))
                override = self._get_onscreen_font_override(font_name)
                if override is not None:
                    return pt_size * float(override["factor"])
                display_font_name = self._get_display_font_name(font_name)
                return self._pt_to_px(
                    pt_size,
                    display_font_name,
                    is_bold,
                    is_italic,
                )
        return self.get_system_font_size_for_width()

    def get_font_line_height(self, font_id: int) -> float:
        """
        Get line height for text frames using font's original point size.

        Line spacing uses the original point size, not the transformed pixel
        size. For example, Courier 12pt renders at about 10px but uses 12px
        line spacing.

        Args:
            font_id: Font ID from record (1-based)

        Returns:
            Line height in pixels = original pt_size (not scaled by factor)
        """
        if self.font_manager:
            resolved_font_id = self._resolve_font_id(font_id)
            font = self.font_manager.get_font_info(resolved_font_id)
            if font:
                # Return original pt_size - this IS the line height
                # (not converted using font factor like rendered size)
                return float(font.get("size", 10))
        return 10.0  # Default

    def get_system_font_info(self) -> tuple[str, float, bool, bool, bool]:
        """
        Get the default system-font info used for DEFAULT-mode pin text.
        """
        # System font is hardcoded Times New Roman 10pt
        # Convert 10pt to px using Times New Roman factor
        font_size_px = self._pt_to_px(10.0, "Times New Roman", False, False)
        return ("Times New Roman", font_size_px, False, False, False)

    def get_system_font_size_for_width(self) -> float:
        """
        Get system font size in pixels for text width measurement.

        Used when calculating text width for DEFAULT mode pin text.

        Returns:
            System font size in pixels (Times New Roman 10pt ~= 8.889px).
        """
        return self._pt_to_px(10.0, "Times New Roman", False, False)

    def _pt_to_px(
        self,
        pt_size: float,
        font_name: str = "Arial",
        bold: bool = False,
        italic: bool = False,
    ) -> float:
        """
        Convert font size from points to pixels using font-specific factor.

        Issue 5 Fix: Uses font-specific Em/Cell ratio instead of hardcoded 8/9.
        Different fonts have different factors:
            - Arial:       factor ~= 0.895 (close to 8/9 = 0.889)
            - Arial Black: factor ~= 0.709 (significantly different!)

        Formula:
            px = pt * (EmHeight / (CellAscent + CellDescent))

        Args:
            pt_size: Font size in points
            font_name: Font family name (e.g., "Arial", "Arial Black")
            bold: Whether font is bold
            italic: Whether font is italic

        Returns:
            Size in pixels as float
        """
        return ttf_pt_to_px(pt_size, font_name, bold, italic)

    def get_baseline_font_size(self, font_size_px: float) -> float:
        """
        Get the font size used for baseline Y-offset calculation.

        Args:
            font_size_px: Font size in pixels (float)

        Returns the configured baseline size, truncated or preserved as float.
        """
        if self.options.truncate_font_size_for_baseline:
            # Match native Altium SVG behavior (buggy)
            return float(int(font_size_px))
        else:
            # Use float for visually correct rendering
            return font_size_px

    def make_attrs(self, **kwargs: Any) -> dict[str, str]:
        """
        Create SVG attributes dict, optionally including metadata.

        Use this when creating SVG elements to conditionally add
        data-* attributes based on context settings.

        Args:
            **kwargs: Attributes to include if include_metadata is True
                     Keys should be like 'net', 'ref', 'type' which become
                     'data-net', 'data-ref', 'data-type'

        Returns:
            Dict of SVG attributes
        """
        attrs = {}
        if self.include_metadata:
            for key, value in kwargs.items():
                if value:  # Only include non-empty values
                    attrs[f"data-{key}"] = str(value)
        return attrs


# ============================================================================
# SYMBOL BOUNDING BOX CALCULATOR
# ============================================================================


@dataclass
class BoundingBox:
    """
    Axis-aligned bounding box in mils.
    """

    min_x: float = float("inf")
    min_y: float = float("inf")
    max_x: float = float("-inf")
    max_y: float = float("-inf")

    def expand(self, x: float, y: float) -> None:
        """
        Expand box to include point.
        """
        self.min_x = min(self.min_x, x)
        self.min_y = min(self.min_y, y)
        self.max_x = max(self.max_x, x)
        self.max_y = max(self.max_y, y)

    def expand_box(self, other: "BoundingBox") -> None:
        """
        Expand to include another box.
        """
        self.min_x = min(self.min_x, other.min_x)
        self.min_y = min(self.min_y, other.min_y)
        self.max_x = max(self.max_x, other.max_x)
        self.max_y = max(self.max_y, other.max_y)

    def expand_margin(self, margin: float) -> None:
        """
        Add margin to all sides.
        """
        self.min_x -= margin
        self.min_y -= margin
        self.max_x += margin
        self.max_y += margin

    @property
    def width(self) -> float:
        return max(0, self.max_x - self.min_x)

    @property
    def height(self) -> float:
        return max(0, self.max_y - self.min_y)

    @property
    def is_valid(self) -> bool:
        return self.min_x < float("inf") and self.max_x > float("-inf")
