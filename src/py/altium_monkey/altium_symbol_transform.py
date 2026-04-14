"""
Coordinate transforms between schematic-space and symbol-space objects.
"""

from __future__ import annotations

import random
import string
from collections.abc import Callable
from copy import deepcopy
from typing import Protocol, TypeGuard, TypeVar, cast

from .altium_record_types import CoordPoint

T = TypeVar("T")


class _HasValue(Protocol):
    value: int


class _TransformComponent(Protocol):
    location: CoordPoint
    orientation: int | _HasValue
    is_mirrored: bool


class _HasLocation(Protocol):
    location: CoordPoint


class _HasCorner(Protocol):
    corner: CoordPoint


class _HasRectangle(Protocol):
    location: CoordPoint
    corner: CoordPoint


class _HasVertices(Protocol):
    vertices: list[object]


class _HasPoints(Protocol):
    points: list[object]


class _HasOrientation(Protocol):
    orientation: int | _HasValue


class _HasPinConglomerate(Protocol):
    pin_conglomerate: int


class _HasRawRecord(Protocol):
    _raw_record: object | None


class _HasUniqueId(Protocol):
    unique_id: str


def _has_location(obj: object) -> TypeGuard[_HasLocation]:
    location_obj = cast(_HasLocation, obj)
    return hasattr(obj, "location") and isinstance(location_obj.location, CoordPoint)


def _has_corner(obj: object) -> TypeGuard[_HasCorner]:
    corner_obj = cast(_HasCorner, obj)
    return hasattr(obj, "corner") and isinstance(corner_obj.corner, CoordPoint)


def _has_rectangle(obj: object) -> TypeGuard[_HasRectangle]:
    rectangle_obj = cast(_HasRectangle, obj)
    return (
        hasattr(obj, "location")
        and hasattr(obj, "corner")
        and isinstance(rectangle_obj.location, CoordPoint)
        and isinstance(rectangle_obj.corner, CoordPoint)
    )


def _has_vertices(obj: object) -> TypeGuard[_HasVertices]:
    return hasattr(obj, "vertices")


def _has_points(obj: object) -> TypeGuard[_HasPoints]:
    return hasattr(obj, "points")


def _has_orientation(obj: object) -> TypeGuard[_HasOrientation]:
    return hasattr(obj, "orientation")


def _has_pin_conglomerate(obj: object) -> TypeGuard[_HasPinConglomerate]:
    return hasattr(obj, "pin_conglomerate")


def _has_raw_record(obj: object) -> TypeGuard[_HasRawRecord]:
    return hasattr(obj, "_raw_record")


def _has_unique_id(obj: object) -> TypeGuard[_HasUniqueId]:
    return hasattr(obj, "unique_id")


def _orientation_to_int(orientation: int | _HasValue) -> int:
    if isinstance(orientation, int):
        return int(orientation)
    return int(orientation.value)


def _component_transform(component: _TransformComponent) -> tuple[float, float, int, bool]:
    return (
        component.location.x_mils,
        component.location.y_mils,
        _orientation_to_int(component.orientation),
        component.is_mirrored,
    )


def _transform_coord_point(
    point: CoordPoint,
    point_transform: Callable[[float, float, float, float, int, bool], tuple[float, float]],
    comp_x: float,
    comp_y: float,
    orient: int,
    mirror: bool,
) -> CoordPoint:
    x, y = point_transform(point.x_mils, point.y_mils, comp_x, comp_y, orient, mirror)
    return CoordPoint.from_mils(x, y)


def _transform_coord_list(
    values: list[object],
    point_transform: Callable[[float, float, float, float, int, bool], tuple[float, float]],
    comp_x: float,
    comp_y: float,
    orient: int,
    mirror: bool,
) -> list[object]:
    transformed: list[object] = []
    for value in values:
        if isinstance(value, CoordPoint):
            transformed.append(
                _transform_coord_point(value, point_transform, comp_x, comp_y, orient, mirror)
            )
        else:
            transformed.append(value)
    return transformed


def _is_pin_like(obj: object) -> bool:
    from .altium_record_sch__pin import AltiumSchPin

    return isinstance(obj, AltiumSchPin) or _has_pin_conglomerate(obj)


def _transform_pin_like_orientation(
    obj: object,
    comp_orient: int,
    mirror: bool,
    orientation_transform: Callable[[int, int, bool], int],
) -> None:
    from .altium_sch_enums import Rotation90
    from .altium_record_sch__pin import AltiumSchPin

    if not _is_pin_like(obj) or not _has_orientation(obj):
        return

    new_orient = orientation_transform(_orientation_to_int(obj.orientation), comp_orient, mirror)
    if isinstance(obj, AltiumSchPin):
        obj.orientation = Rotation90(new_orient)
    else:
        obj.orientation = new_orient

    if _has_pin_conglomerate(obj):
        obj.pin_conglomerate = (obj.pin_conglomerate & ~0x03) | new_orient


def generate_unique_id() -> str:
    """
    Generate an 8-character alphanumeric unique ID.
    
    Altium uses unique IDs to identify objects. When cloning objects
    (e.g., from library to schematic), always regenerate IDs.
    
    Returns:
        8-character string of uppercase letters and digits
    """
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def transform_point_to_symbol_space(
    x_mils: float,
    y_mils: float,
    comp_x_mils: float,
    comp_y_mils: float,
    orientation: int,
    is_mirrored: bool,
) -> tuple[float, float]:
    """
    Transform a point from schematic-space to symbol-space.
    
    Args:
        x_mils: Point X coordinate in mils (schematic space)
        y_mils: Point Y coordinate in mils (schematic space)
        comp_x_mils: Component location X in mils
        comp_y_mils: Component location Y in mils
        orientation: Component rotation (0=0deg, 1=90deg, 2=180deg, 3=270deg CCW)
        is_mirrored: Component is horizontally mirrored (Y-axis flip)
    
    Returns:
        Tuple (x, y) in symbol-space mils
    
    Note:
        Altium applies transformations in order: Rotate -> Mirror
        To reverse: Translate -> Un-mirror -> Un-rotate
    """
    x = x_mils - comp_x_mils
    y = y_mils - comp_y_mils

    if is_mirrored:
        x = -x

    if orientation == 1:
        x, y = y, -x
    elif orientation == 2:
        x, y = -x, -y
    elif orientation == 3:
        x, y = -y, x

    return x, y


def transform_point_to_schematic_space(
    x_mils: float,
    y_mils: float,
    comp_x_mils: float,
    comp_y_mils: float,
    orientation: int,
    is_mirrored: bool,
) -> tuple[float, float]:
    """
    Transform a point from symbol-space to schematic-space.
    
    This is the inverse of transform_point_to_symbol_space().
    
    Args:
        x_mils: Point X coordinate in mils (symbol space)
        y_mils: Point Y coordinate in mils (symbol space)
        comp_x_mils: Component location X in mils
        comp_y_mils: Component location Y in mils
        orientation: Component rotation (0=0deg, 1=90deg, 2=180deg, 3=270deg CCW)
        is_mirrored: Component is horizontally mirrored (Y-axis flip)
    
    Returns:
        Tuple (x, y) in schematic-space mils
    
    Note:
        Altium applies transformations in order: Rotate -> Mirror -> Translate
        We apply: Rotate -> Mirror -> Translate
    """
    x, y = x_mils, y_mils

    if orientation == 1:
        x, y = -y, x
    elif orientation == 2:
        x, y = -x, -y
    elif orientation == 3:
        x, y = y, -x

    if is_mirrored:
        x = -x

    x += comp_x_mils
    y += comp_y_mils
    return x, y


def transform_pin_orientation(pin_orient: int, comp_orient: int, is_mirrored: bool) -> int:
    """
    Transform PIN orientation from schematic-space to symbol-space.
    
    PIN orientations: 0=RIGHT, 1=UP, 2=LEFT, 3=DOWN
    
    Args:
        pin_orient: PIN orientation in schematic (0-3)
        comp_orient: Component rotation (0-3)
        is_mirrored: Component is horizontally mirrored
    
    Returns:
        PIN orientation in symbol-space (0-3)
    """
    if is_mirrored:
        if pin_orient == 0:
            pin_orient = 2
        elif pin_orient == 2:
            pin_orient = 0

    return (pin_orient - comp_orient) % 4


def transform_pin_orientation_to_schematic(
    pin_orient: int,
    comp_orient: int,
    is_mirrored: bool,
) -> int:
    """
    Transform PIN orientation from symbol-space to schematic-space.
    
    This is the inverse of transform_pin_orientation().
    
    PIN orientations: 0=RIGHT, 1=UP, 2=LEFT, 3=DOWN
    
    Args:
        pin_orient: PIN orientation in symbol (0-3)
        comp_orient: Component rotation (0-3)
        is_mirrored: Component is horizontally mirrored
    
    Returns:
        PIN orientation in schematic-space (0-3)
    """
    result = (pin_orient + comp_orient) % 4

    if is_mirrored:
        if result == 0:
            result = 2
        elif result == 2:
            result = 0

    return result


def to_symbol_space(obj: T, component: _TransformComponent) -> T:
    """
    Transform an OOP object from schematic-space to symbol-space.
    
    Returns a deep copy with transformed coordinates. Works with any object
    that has location/corner/vertices attributes (AltiumSchPin,
    AltiumSchRectangle, AltiumSchLine, AltiumSchPolyline, etc.).
    
    Args:
        obj: OOP record object with coordinate attributes
        component: AltiumSchComponent with location, orientation, is_mirrored
    
    Returns:
        Deep copy of obj with coordinates transformed to symbol-space
    """
    result: T = deepcopy(obj)
    comp_x, comp_y, orient, mirror = _component_transform(component)

    if _has_location(result):
        result.location = _transform_coord_point(
            result.location,
            transform_point_to_symbol_space,
            comp_x,
            comp_y,
            orient,
            mirror,
        )

    if _has_corner(result):
        result.corner = _transform_coord_point(
            result.corner,
            transform_point_to_symbol_space,
            comp_x,
            comp_y,
            orient,
            mirror,
        )

    if _has_vertices(result) and result.vertices:
        result.vertices = _transform_coord_list(
            result.vertices,
            transform_point_to_symbol_space,
            comp_x,
            comp_y,
            orient,
            mirror,
        )

    _transform_pin_like_orientation(result, orient, mirror, transform_pin_orientation)
    return cast(T, result)


def normalize_rectangle_coords(rect: object) -> None:
    """
    Normalize rectangle coordinates (ConvertToPositiveSlope convention).
    
    Altium convention: Location.X <= Corner.X and Location.Y <= Corner.Y
    After un-mirror transformation, coordinates may be swapped.
    
    Modifies rect in-place.
    
    Args:
        rect: Object with location and corner CoordPoint attributes
    """
    if not _has_rectangle(rect):
        return

    rectangle = cast(_HasRectangle, rect)

    loc_x = rectangle.location.x_mils
    loc_y = rectangle.location.y_mils
    corner_x = rectangle.corner.x_mils
    corner_y = rectangle.corner.y_mils

    needs_update = False
    new_loc_x, new_loc_y = loc_x, loc_y
    new_corner_x, new_corner_y = corner_x, corner_y

    if loc_x > corner_x:
        new_loc_x, new_corner_x = corner_x, loc_x
        needs_update = True

    if loc_y > corner_y:
        new_loc_y, new_corner_y = corner_y, loc_y
        needs_update = True

    if needs_update:
        rectangle.location = CoordPoint.from_mils(new_loc_x, new_loc_y)
        rectangle.corner = CoordPoint.from_mils(new_corner_x, new_corner_y)


def to_schematic_space(
    obj: T,
    component: _TransformComponent,
    regenerate_id: bool = True,
) -> T:
    """
    Transform an OOP object from symbol-space to schematic-space.
    
    Returns a deep copy with transformed coordinates. Works with any object
    that has location/corner/vertices attributes (AltiumSchPin,
    AltiumSchRectangle, AltiumSchLine, AltiumSchPolyline, etc.).
    
    This is the inverse of to_symbol_space() and is used when inserting
    library symbols into schematics.
    
    Args:
        obj: OOP record object with coordinate attributes
        component: AltiumSchComponent with location, orientation, is_mirrored
        regenerate_id: If True, generate a new unique_id for the clone (default True)
    
    Returns:
        Deep copy of obj with coordinates transformed to schematic-space
    """
    result: T = deepcopy(obj)

    if _has_raw_record(result):
        result._raw_record = None

    if regenerate_id and _has_unique_id(result):
        result.unique_id = generate_unique_id()

    comp_x, comp_y, orient, mirror = _component_transform(component)

    if _has_location(result):
        result.location = _transform_coord_point(
            result.location,
            transform_point_to_schematic_space,
            comp_x,
            comp_y,
            orient,
            mirror,
        )

    if _has_corner(result):
        result.corner = _transform_coord_point(
            result.corner,
            transform_point_to_schematic_space,
            comp_x,
            comp_y,
            orient,
            mirror,
        )

    if _has_vertices(result) and result.vertices:
        result.vertices = _transform_coord_list(
            result.vertices,
            transform_point_to_schematic_space,
            comp_x,
            comp_y,
            orient,
            mirror,
        )

    if _has_points(result) and result.points:
        result.points = _transform_coord_list(
            result.points,
            transform_point_to_schematic_space,
            comp_x,
            comp_y,
            orient,
            mirror,
        )

    _transform_pin_like_orientation(result, orient, mirror, transform_pin_orientation_to_schematic)
    return cast(T, result)
