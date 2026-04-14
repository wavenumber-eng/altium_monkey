"""
Pin text positioning helpers for schematic rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .altium_sch_enums import PinTextAnchor, Rotation90


class TextAlignment(IntEnum):
    """
    Text alignment flags used by the pin text placement helpers.
    """
    UNKNOWN = 0
    LEFT = 1
    RIGHT = 2
    CENTER = 4
    TOP = 8
    BOTTOM = 16
    BASELINE = 32


@dataclass
class TextPositionResult:
    """
    Result of pin text position calculation.
    """
    x: float
    y: float
    rotation: Rotation90
    h_align: str  # 'left', 'center', 'right'
    v_align: str  # 'top', 'bottom'


def is_rotation_vertical(rotation: Rotation90) -> bool:
    """
    Check if rotation is vertical (90 or 270 degrees).
    
    Args:
        rotation: The rotation to check
    
    Returns:
        True if rotation is 90 or 270 degrees (vertical)
    """
    return rotation in (Rotation90.DEG_90, Rotation90.DEG_270)


def calculate_text_rotation(
    custom_rotation: Rotation90,
    anchor: PinTextAnchor,
    pin_orientation: Rotation90,
    component_orientation: Rotation90 | None = None
) -> Rotation90:
    """
    Calculate final text rotation from the text anchor and pin orientation.
    
    Args:
        custom_rotation: The custom rotation setting from pin text settings
        anchor: Whether rotation is relative to pin or component
        pin_orientation: The pin's orientation (world-space)
        component_orientation: The component's orientation (if anchor is COMPONENT)
    
    Returns:
        DEG_90 if text should be rotated (perpendicular), DEG_0 otherwise
    """
    if anchor == PinTextAnchor.PIN:
        anchor_rotation = pin_orientation
    else:
        anchor_rotation = component_orientation if component_orientation else Rotation90.DEG_0

    # XOR of vertical status determines if text is perpendicular
    custom_is_vertical = is_rotation_vertical(custom_rotation)
    anchor_is_vertical = is_rotation_vertical(anchor_rotation)

    if custom_is_vertical != anchor_is_vertical:
        return Rotation90.DEG_90  # Text is perpendicular to anchor
    return Rotation90.DEG_0  # Text is aligned with anchor


def calculate_pin_text_position(
    pin_orientation: Rotation90,
    inner_bounds: tuple[float, float, float, float],  # (left, top, right, bottom)
    font_size: float,
    margin: float,
    custom_rotation: Rotation90 = Rotation90.DEG_0,
    anchor: PinTextAnchor = PinTextAnchor.PIN,
    component_orientation: Rotation90 | None = None
) -> TextPositionResult:
    """
    Calculate pin text position and alignment.
    
    Args:
        pin_orientation: The pin's orientation (world-space, already transformed)
        inner_bounds: Pin's bounding rectangle (left, top, right, bottom) in SVG coords
        font_size: Font size in pixels
        margin: Margin from pin in pixels
        custom_rotation: Custom rotation setting (from pin text settings)
        anchor: Whether rotation is relative to pin or component
        component_orientation: Component orientation (if anchor is COMPONENT)
    
    Returns:
        TextPositionResult with x, y, rotation, and alignment
    """
    left, top, right, bottom = inner_bounds

    # Calculate text rotation
    text_rotation = calculate_text_rotation(
        custom_rotation, anchor, pin_orientation, component_orientation
    )

    # "flag" = true when text rotates WITH the pin (same vertical status after XOR)
    # flag = IsRotationVertical(rotationBy90) ^ IsRotationVertical(state_Orientation)
    text_is_vertical = is_rotation_vertical(text_rotation)
    pin_is_vertical = is_rotation_vertical(pin_orientation)
    rotates_with_pin = text_is_vertical != pin_is_vertical

    # Base position from innerBounds (native line 49-67)
    if pin_orientation == Rotation90.DEG_0:  # Right-pointing pin (case 0)
        x = left - margin
        y = (top + bottom) / 2
    elif pin_orientation == Rotation90.DEG_90:  # Up-pointing pin (case 1)
        x = (left + right) / 2
        y = top - margin
    elif pin_orientation == Rotation90.DEG_180:  # Left-pointing pin (case 2)
        x = right + margin
        y = (top + bottom) / 2
    else:  # DEG_270 - Down-pointing pin (case 3)
        x = (left + right) / 2
        y = bottom + margin

    # fontSize/2 offset when text doesn't rotate with pin (native line 69-80)
    if not rotates_with_pin:
        offset = font_size / 2
        if pin_is_vertical:
            x += offset
        else:
            y -= offset

    # Determine alignment (native line 81-100)
    # Vertical alignment
    if rotates_with_pin and pin_orientation in (Rotation90.DEG_90, Rotation90.DEG_180):
        v_align = 'top'
    else:
        v_align = 'bottom'

    # Horizontal alignment
    if rotates_with_pin:
        h_align = 'center'
    elif pin_orientation in (Rotation90.DEG_0, Rotation90.DEG_90):
        h_align = 'right'
    else:
        h_align = 'left'

    return TextPositionResult(x, y, text_rotation, h_align, v_align)


def apply_text_alignment(
    x: float, y: float,
    text_width: float, text_height: float,
    h_align: str, v_align: str,
    rotation: Rotation90
) -> tuple[float, float, str | None]:
    """
    Apply text alignment and return the final position and SVG transform.
    
    Args:
        x: Base X position
        y: Base Y position
        text_width: Measured text width in pixels
        text_height: Measured text height in pixels
        h_align: Horizontal alignment ('left', 'center', 'right')
        v_align: Vertical alignment ('top', 'bottom')
        rotation: Text rotation
    
    Returns:
        Tuple of (draw_x, draw_y, transform_string or None)
    """
    x_offset = 0.0
    y_offset = 0.0

    # Calculate alignment offsets (native line 1923-1935)
    if h_align == 'center':
        x_offset = text_width / 2
    elif h_align == 'right':
        x_offset = text_width

    if v_align == 'bottom':
        y_offset = text_height

    # Apply offset
    draw_x = x - x_offset
    draw_y = y + y_offset

    # Create rotation transform around ORIGINAL anchor (not offset position)
    # This is the key insight from DrawGraphObjectBase
    if rotation == Rotation90.DEG_90:
        original_x = x  # draw_x + x_offset
        original_y = y  # draw_y - y_offset
        transform = f"rotate(-90 {original_x:.4f} {original_y:.4f})"
    elif rotation == Rotation90.DEG_180:
        original_x = x
        original_y = y
        transform = f"rotate(180 {original_x:.4f} {original_y:.4f})"
    elif rotation == Rotation90.DEG_270:
        original_x = x
        original_y = y
        transform = f"rotate(90 {original_x:.4f} {original_y:.4f})"
    else:
        transform = None

    return draw_x, draw_y, transform


def rotate_by_90_transform(
    x: float, y: float,
    origin_x: float, origin_y: float,
    rotation: Rotation90
) -> tuple[float, float]:
    """
    Rotate a point around an origin using 90-degree steps.
    
    Args:
        x: Input X coordinate
        y: Input Y coordinate
        origin_x: Rotation origin X
        origin_y: Rotation origin Y
        rotation: Rotation angle
    
    Returns:
        Transformed (x, y) coordinates
    """
    dx = x - origin_x
    dy = y - origin_y

    if rotation == Rotation90.DEG_90:
        x_new = -dy
        y_new = dx
    elif rotation == Rotation90.DEG_180:
        x_new = -dx
        y_new = -dy
    elif rotation == Rotation90.DEG_270:
        x_new = dy
        y_new = -dx
    else:
        x_new = dx
        y_new = dy

    return origin_x + x_new, origin_y + y_new
