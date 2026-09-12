"""
Text width, baseline, and height helpers for schematic text rendering.

This module is the public entry point for text metrics. It prefers resolved
TrueType font metrics and falls back to simpler approximations when a font
cannot be loaded.
"""

import logging
import platform

log = logging.getLogger(__name__)


# Family-specific width correction for missing bold faces.
SYNTHETIC_BOLD_FALLBACK_GLYPH_FACTORS: dict[str, float] = {
    "microsoft sans serif": 0.020018,
}


# Some named instances need the Windows GDI+ path to preserve face-specific widths.
GDIPLUS_NAMED_INSTANCE_FAMILIES: set[str] = {
    "bahnschrift light condensed",
}

GDIPLUS_COMPATIBLE_TTF_TEXT_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
    }
)


def _normalize_text_for_gdiplus_compatible_ttf_metrics(text: str) -> str:
    """
    Normalize characters that Windows GDI+ maps to legacy glyph fallbacks.
    """
    return text.translate(GDIPLUS_COMPATIBLE_TTF_TEXT_TRANSLATION)


def _measure_ttf_pure(
    text: str,
    font_name: str,
    font_size_px: float,
    bold: bool = False,
    italic: bool = False,
    use_altium_algorithm: bool = True,
    include_rsb: bool = False,
) -> float | None:
    """
    Measure text width using the resolved TrueType face.
    """
    try:
        from .altium_font_resolver import (
            FontResolutionStatus,
            resolve_font_with_style,
        )
        from .altium_ttf_metrics import get_font

        resolution = resolve_font_with_style(font_name, bold=bold, italic=italic)
        font_path = None if resolution.path is None else str(resolution.path)

        if font_path is None:
            return None

        font = get_font(font_path)
        measurement_text = _normalize_text_for_gdiplus_compatible_ttf_metrics(text)

        if use_altium_algorithm:
            width = font.measure_text_altium(
                measurement_text, font_size_px, include_rsb=include_rsb
            )
        else:
            width = font.measure_text(
                measurement_text, font_size_px, include_rsb=include_rsb
            )

        synthetic_bold_factor = SYNTHETIC_BOLD_FALLBACK_GLYPH_FACTORS.get(
            font_name.lower()
        )
        if (
            width is not None
            and synthetic_bold_factor is not None
            and bold
            and not italic
            and resolution.status == FontResolutionStatus.STYLE_FALLBACK
            and resolution.resolved_family == font_name
        ):
            measured_text = text.rstrip()
            visible_glyph_count = sum(1 for char in measured_text if not char.isspace())
            if visible_glyph_count > 0:
                width += visible_glyph_count * font_size_px * synthetic_bold_factor

        return width
    except Exception as e:
        log.debug("TTF parser error: %s", e)
        return None


def _measure_gdiplus_named_instance(
    text: str,
    font_name: str,
    font_size_px: float,
    *,
    bold: bool = False,
    italic: bool = False,
    use_altium_algorithm: bool = True,
    include_rsb: bool = False,
) -> float | None:
    """
    Use the Windows named-instance path when TTF metrics are insufficient.
    """
    family = font_name.strip().lower()
    if platform.system() != "Windows" or family not in GDIPLUS_NAMED_INSTANCE_FAMILIES:
        return None

    try:
        from .altium_windows_gdiplus import get_gdiplus_text_width

        width = float(
            get_gdiplus_text_width(
                text.rstrip(),
                font_size_px,
                font_name,
                bold=bold,
                italic=italic,
            )
        )
        if width <= 0.0:
            return None

        if include_rsb:
            return width

        include_width = _measure_ttf_pure(
            text,
            font_name,
            font_size_px,
            bold=bold,
            italic=italic,
            use_altium_algorithm=use_altium_algorithm,
            include_rsb=True,
        )
        exclude_width = _measure_ttf_pure(
            text,
            font_name,
            font_size_px,
            bold=bold,
            italic=italic,
            use_altium_algorithm=use_altium_algorithm,
            include_rsb=False,
        )
        if include_width is not None and exclude_width is not None:
            width -= max(0.0, include_width - exclude_width)
        return width
    except Exception as e:
        log.debug("GDI+ named-instance fallback failed: %s", e)
        return None


# Fallback width table for Arial at 8px.
ARIAL_8PX_CHAR_WIDTHS_GDI_RAW = {
    "A": 5.34,
    "B": 5.34,
    "C": 5.78,
    "D": 5.78,
    "E": 5.34,
    "F": 4.89,
    "G": 6.22,
    "H": 5.78,
    "I": 2.22,
    "J": 4.00,
    "K": 5.34,
    "L": 4.45,
    "M": 6.66,
    "N": 5.78,
    "O": 6.22,
    "P": 5.34,
    "Q": 6.22,
    "R": 5.78,
    "S": 5.34,
    "T": 4.89,
    "U": 5.78,
    "V": 5.34,
    "W": 7.55,
    "X": 5.34,
    "Y": 5.34,
    "Z": 4.89,
    "0": 4.45,
    "1": 4.45,
    "2": 4.45,
    "3": 4.45,
    "4": 4.45,
    "5": 4.45,
    "6": 4.45,
    "7": 4.45,
    "8": 4.45,
    "9": 4.45,
    "_": 4.45,
}

# Default width for unknown characters.
DEFAULT_CHAR_WIDTH_RAW = 5.4


def _measure_table_fallback(text: str, font_size_px: float = 8.0) -> float:
    """
    Approximate width with the fallback character table.
    """
    if not text:
        return 0.0

    text = _normalize_text_for_gdiplus_compatible_ttf_metrics(text).rstrip()

    total_width = 0.0
    for char in text:
        upper = char.upper()
        if upper in ARIAL_8PX_CHAR_WIDTHS_GDI_RAW:
            total_width += ARIAL_8PX_CHAR_WIDTHS_GDI_RAW[upper]
        else:
            total_width += DEFAULT_CHAR_WIDTH_RAW

    if font_size_px != 8:
        total_width *= font_size_px / 8.0

    return total_width


def measure_text_width(
    text: str,
    font_size_px: float = 8.0,
    font_name: str = "Arial",
    bold: bool = False,
    italic: bool = False,
    use_altium_algorithm: bool = True,
    include_rsb: bool = False,
) -> float:
    """
    Measure text width for rendering and layout.

        Args:
            text: Text to measure.
            font_size_px: Font size in pixels.
            font_name: Requested font family.
            bold: Whether to request the bold face.
            italic: Whether to request the italic face.
            use_altium_algorithm: Use the rendering-oriented width path.
            include_rsb: Include the trailing side bearing in the result.

        Returns:
            Text width in pixels.
    """
    if not text:
        return 0.0

    gdiplus_width = _measure_gdiplus_named_instance(
        text,
        font_name,
        float(font_size_px),
        bold=bold,
        italic=italic,
        use_altium_algorithm=use_altium_algorithm,
        include_rsb=include_rsb,
    )
    if gdiplus_width is not None:
        return gdiplus_width

    ttf_width = _measure_ttf_pure(
        text,
        font_name,
        float(font_size_px),
        bold=bold,
        italic=italic,
        use_altium_algorithm=use_altium_algorithm,
        include_rsb=include_rsb,
    )
    if ttf_width is not None:
        return ttf_width

    log.debug("Using table fallback for text measurement")
    return _measure_table_fallback(text, font_size_px)


def _measure_text_prefix_widths(
    text: str,
    font_size_px: float,
    font_name: str,
    *,
    bold: bool = False,
    italic: bool = False,
) -> list[tuple[float, float]]:
    """Return cumulative widths with and without each final glyph's RSB."""
    if not text:
        return []
    try:
        from .altium_font_resolver import resolve_font_with_style
        from .altium_ttf_metrics import get_font

        resolution = resolve_font_with_style(font_name, bold=bold, italic=italic)
        if resolution.path is None:
            raise ValueError("font is unresolved")
        font = get_font(str(resolution.path))
        measurement_text = _normalize_text_for_gdiplus_compatible_ttf_metrics(text)
        scale = font_size_px / font.units_per_em
        total = 0.0
        widths: list[tuple[float, float]] = []
        for character in measurement_text:
            glyph_id = font.cmap.get(ord(character), 0)
            total += font.get_advance(glyph_id) * scale
            widths.append((total, total - font.get_rsb(glyph_id) * font_size_px))
        return widths
    except (OSError, ValueError):
        total = 0.0
        widths = []
        for character in text:
            include_width = measure_text_width(
                character,
                font_size_px,
                font_name,
                bold=bold,
                italic=italic,
                include_rsb=True,
            )
            exclude_width = measure_text_width(
                character,
                font_size_px,
                font_name,
                bold=bold,
                italic=italic,
                include_rsb=False,
            )
            total += include_width
            widths.append((total, total - (include_width - exclude_width)))
        return widths


def _measure_text_geometry_glyph_box(
    text: str,
    font_size_px: float,
    font_name: str,
    *,
    bold: bool = False,
    italic: bool = False,
) -> tuple[float, float]:
    """Measure the unrotated TextGeometryItem glyph box, including spaces."""
    if not text:
        return 0.0, 0.0
    try:
        from ._altium_record_sch__harness_layout import _f32
        from .altium_font_resolver import resolve_font_with_style
        from .altium_ttf_metrics import get_font

        resolution = resolve_font_with_style(font_name, bold=bold, italic=italic)
        if resolution.path is None:
            raise ValueError("font is unresolved")
        font = get_font(str(resolution.path))
        encoded = text.encode("utf-16-le", errors="surrogatepass")
        code_units = [
            int.from_bytes(encoded[index : index + 2], "little")
            for index in range(0, len(encoded), 2)
        ]
        transformed_size = _f32(font_size_px)
        width = 0.0
        height = 0.0
        for index, code_unit in enumerate(code_units):
            glyph_id = font.cmap.get(code_unit)
            if glyph_id is None:
                continue
            advance = _f32(font.get_advance(glyph_id) / font.units_per_em)
            glyph_width = _f32(advance * transformed_size)
            advance_height = _f32(font.get_advance_height(glyph_id) / font.units_per_em)
            height = max(height, _f32(advance_height * transformed_size))
            if index == len(code_units) - 1:
                bearing = _f32(font.get_rsb(glyph_id))
                glyph_width = _f32(glyph_width - _f32(bearing * transformed_size))
            width = _f32(width + glyph_width)
        return width, height
    except (OSError, ValueError):
        widths = _measure_text_prefix_widths(
            text,
            font_size_px,
            font_name,
            bold=bold,
            italic=italic,
        )
        return (widths[-1][1], font_size_px) if widths else (0.0, 0.0)


def measure_gdi_typographic_bounds(
    text: str,
    font_size_px: float,
    font_name: str,
    *,
    bold: bool = False,
    italic: bool = False,
) -> tuple[float, float]:
    """Measure a managed ``GenericTypographic`` text box in pixel units."""
    if not text:
        return 0.0, 0.0

    try:
        from .altium_font_resolver import resolve_font_with_style
        from .altium_ttf_metrics import get_font

        resolution = resolve_font_with_style(font_name, bold=bold, italic=italic)
        if resolution.path is None:
            raise ValueError("font is unresolved")
        font = get_font(str(resolution.path))
        logical_lines = _gdi_typographic_lines(text)
        raw_width = max(
            (
                measure_text_width(
                    line.replace("\r", "").replace("\t", ""),
                    font_size_px,
                    font_name,
                    bold=bold,
                    italic=italic,
                    include_rsb=True,
                )
                + line.count("\t") * font_size_px / font.units_per_em
                for line in logical_lines
            ),
            default=0.0,
        )
        final_character = _last_non_space_character(text)
        if final_character is not None and final_character not in "\r\n\t":
            raw_width -= _right_side_bearing(
                final_character,
                font_size_px,
                font_name,
                bold=bold,
                italic=italic,
            )
        cell_height = font.ascender + font.descender
        height_units = (
            font.gdi_line_spacing
            if any(character in text for character in "\r\n\t")
            else cell_height
        )
        height = len(logical_lines) * font_size_px * height_units / font.units_per_em
        return raw_width, height
    except (OSError, ValueError):
        logical_lines = _gdi_typographic_lines(text)
        width = max(
            (
                measure_text_width(
                    line.replace("\r", "").replace("\t", ""),
                    font_size_px,
                    font_name,
                    bold=bold,
                    italic=italic,
                )
                for line in logical_lines
            ),
            default=0.0,
        )
        return width, len(logical_lines) * font_size_px


def _gdi_typographic_lines(text: str) -> tuple[str, ...]:
    lines: list[str] = []
    start = 0
    while start < len(text):
        line_break = text.find("\n", start)
        if line_break < 0:
            line_break = len(text)
        lines.append(text[start:line_break])
        start = line_break + 1
    return tuple(lines)


def _last_non_space_character(text: str) -> str | None:
    index = len(text) - 1
    while index >= 0 and text[index] == " ":
        index -= 1
    return None if index < 0 else text[index]


def _right_side_bearing(
    character: str,
    font_size_px: float,
    font_name: str,
    *,
    bold: bool,
    italic: bool,
) -> float:
    included = measure_text_width(
        character,
        font_size_px,
        font_name,
        bold=bold,
        italic=italic,
        include_rsb=True,
    )
    excluded = measure_text_width(
        character,
        font_size_px,
        font_name,
        bold=bold,
        italic=italic,
        include_rsb=False,
    )
    return included - excluded


def measure_text_width_accurate(text: str, font_size_px: int = 8) -> float:
    """
    Alias for :func:`measure_text_width`.
    """
    return measure_text_width(text, font_size_px)


# Width cache for hot-path repeated lookups.
_width_cache: dict[tuple[str, int], float] = {}


def get_text_width(text: str, font_size_px: int = 8) -> float:
    """
    Measure text width with a small in-process cache.
    """
    cache_key = (text, font_size_px)
    if cache_key in _width_cache:
        return _width_cache[cache_key]

    width = measure_text_width(text, font_size_px)
    _width_cache[cache_key] = width
    return width


def _get_baseline_offset_ttf(font_size_px: float, font_name: str) -> float | None:
    """
    Compute baseline offset from the resolved font metrics.
    """
    try:
        from .altium_ttf_metrics import get_font, get_font_path

        font_path = get_font_path(font_name)
        if not font_path:
            return None

        font = get_font(font_path)

        factor = font.units_per_em / (font.ascender + abs(font.descender))

        pt_size = font_size_px / factor

        offset = pt_size - int(font_size_px)

        return offset
    except Exception as e:
        log.debug("TTF baseline error: %s", e)
        return None


def _get_baseline_offset_fallback(font_size_px: float) -> float:
    """
    Approximate baseline offset when font metrics are unavailable.
    """
    px = float(font_size_px)

    if px <= 7:
        return 1.0
    elif px <= 16:
        return 2.0
    else:
        return float(int(px / 7))


def get_baseline_offset(font_size_px: float, font_name: str = "Arial") -> float:
    """
    Get the baseline offset used for text placement.
    """
    ttf_offset = _get_baseline_offset_ttf(font_size_px, font_name)
    if ttf_offset is not None:
        return ttf_offset

    log.debug("Using fallback formula for baseline offset")
    return _get_baseline_offset_fallback(font_size_px)


def _measure_text_height_ttf(
    font_size_px: float,
    font_name: str = "Arial",
    bold: bool = False,
    italic: bool = False,
    use_altium_algorithm: bool = True,
) -> float | None:
    """
    Measure text height using the resolved TrueType face.
    """
    try:
        from .altium_font_resolver import resolve_font_with_style
        from .altium_ttf_metrics import get_font

        # Resolve with explicit style flags so single-file families
        # (e.g. Arial Black, Bahnschrift) keep their own metrics via the
        # resolver's style fallback instead of generic-fallbacking through
        # a guessed "<family> Bold/Italic" name. This keeps the text-box
        # height equal to the FONT record size, which the SVG baseline
        # formula (anchor - record_size + trunc(px_size)) depends on.
        resolution = resolve_font_with_style(font_name, bold=bold, italic=italic)
        if resolution.path is None:
            return None

        font = get_font(str(resolution.path))

        if use_altium_algorithm:
            factor = font.get_factor()
            measure_size = 100.0 * factor
            height_at_measure = (font.ascender + font.descender) * (
                measure_size / font.units_per_em
            )
            return height_at_measure * (font_size_px / 100.0)
        else:
            return (font.ascender + font.descender) * (font_size_px / font.units_per_em)
    except Exception as e:
        log.debug("TTF height error: %s", e)
        return None


def measure_text_height(
    font_size_px: float = 8.0,
    font_name: str = "Arial",
    bold: bool = False,
    italic: bool = False,
    use_altium_algorithm: bool = True,
) -> float:
    """
    Measure text height for layout and rendering.
    """
    ttf_height = _measure_text_height_ttf(
        float(font_size_px),
        font_name,
        bold=bold,
        italic=italic,
        use_altium_algorithm=use_altium_algorithm,
    )
    if ttf_height is not None:
        return ttf_height

    log.debug("Using fallback for text height")
    return font_size_px * 1.1
