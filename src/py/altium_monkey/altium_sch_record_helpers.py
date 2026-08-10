"""Shared helpers for schematic record serialization and indicator styling."""

from __future__ import annotations

from typing import Any, Protocol

from .altium_record_types import CoordPoint, SchPointMils, SchRectMils, color_to_hex
from .altium_serializer import CaseMode
from .altium_sch_geometry_oracle import svg_coord_to_geometry


def detect_case_mode_from_uppercase_fields(
    raw_record: dict[str, Any] | None,
    *,
    ignore_record: bool = False,
    require_dot: bool = False,
) -> CaseMode:
    """
    Detect uppercase-vs-Pascal field casing from raw schematic record keys.
    """
    if raw_record is None:
        return CaseMode.PASCALCASE
    for key in raw_record:
        if ignore_record and key == "RECORD":
            continue
        if require_dot and "." not in key:
            continue
        if key.isupper() and len(key) > 2:
            return CaseMode.UPPERCASE
    return CaseMode.PASCALCASE


def detect_case_mode_method_from_uppercase_fields(self: Any) -> CaseMode:
    """Bound-method adapter for common schematic record case detection."""
    return detect_case_mode_from_uppercase_fields(
        getattr(self, "_raw_record", None),
        ignore_record=True,
    )


def detect_case_mode_method_from_dotted_uppercase_fields(self: Any) -> CaseMode:
    """Bound-method adapter for dotted uppercase schematic field names."""
    return detect_case_mode_from_uppercase_fields(
        getattr(self, "_raw_record", None),
        require_dot=True,
    )


def bound_schematic_owner(record: object) -> object | None:
    """Return the owner stored in a bound schematic context, when present."""
    context = getattr(record, "_bound_schematic_context", None)
    if context is None:
        return None
    return context.owner


class _NamedEntryContainer(Protocol):
    def get_entry(self, name: str) -> object | None: ...

    def remove_entry(self, entry: object) -> bool: ...


def remove_named_entry(container: _NamedEntryContainer, name: str) -> bool:
    """Remove the first named child entry from a connector-like container."""
    entry = container.get_entry(name)
    if entry is None:
        return False
    return container.remove_entry(entry)


class CornerMilsMixin:
    corner: CoordPoint

    @property
    def corner_mils(self) -> SchPointMils:
        """
        Public corner helper expressed in mils.
        """
        return SchPointMils.from_mils(self.corner.x_mils, self.corner.y_mils)

    @corner_mils.setter
    def corner_mils(self, value: SchPointMils) -> None:
        if not isinstance(value, SchPointMils):
            raise TypeError("corner_mils must be a SchPointMils value")
        self.corner = value.to_coord_point()


class RectangularBoundsMilsMixin(CornerMilsMixin):
    location: CoordPoint

    @property
    def location_mils(self) -> SchPointMils:
        """
        Public location helper expressed in mils.
        """
        return SchPointMils.from_mils(self.location.x_mils, self.location.y_mils)

    @location_mils.setter
    def location_mils(self, value: SchPointMils) -> None:
        if not isinstance(value, SchPointMils):
            raise TypeError("location_mils must be a SchPointMils value")
        self.location = value.to_coord_point()

    @property
    def bounds_mils(self) -> SchRectMils:
        """
        Public bounds helper expressed in mils.
        """
        return SchRectMils.from_points(
            self.location_mils,
            self.corner_mils,
        ).normalized()

    @bounds_mils.setter
    def bounds_mils(self, value: SchRectMils) -> None:
        if not isinstance(value, SchRectMils):
            raise TypeError("bounds_mils must be a SchRectMils value")
        self.location, self.corner = value.to_coord_points()


class PrimaryRadiusMilsMixin:
    radius: int
    radius_frac: int

    @property
    def radius_mils(self) -> float:
        """
        Public primary radius in mils.

        Use this property for normal mutation and inspection. It hides the
        underlying Altium coord-style whole/fraction storage fields
        (``radius`` / ``radius_frac``).
        """
        return _coord_scalar_to_public_mils(self.radius, self.radius_frac)

    @radius_mils.setter
    def radius_mils(self, value: float) -> None:
        """
        Set the public primary radius in mils.

        Callers should use this property instead of assigning raw
        ``radius`` / ``radius_frac`` values directly.
        """
        whole, frac = _public_mils_to_coord_scalar(value)
        self.radius = whole
        self.radius_frac = frac


class SecondaryRadiusMilsMixin:
    secondary_radius: int
    secondary_radius_frac: int

    @property
    def secondary_radius_mils(self) -> float:
        """
        Public secondary radius in mils.

        Use this property for normal mutation and inspection. It hides the
        underlying Altium coord-style whole/fraction storage fields
        (``secondary_radius`` / ``secondary_radius_frac``).
        """
        return _coord_scalar_to_public_mils(
            self.secondary_radius,
            self.secondary_radius_frac,
        )

    @secondary_radius_mils.setter
    def secondary_radius_mils(self, value: float) -> None:
        """
        Set the public secondary radius in mils.

        Callers should use this property instead of assigning raw
        ``secondary_radius`` / ``secondary_radius_frac`` values directly.
        """
        whole, frac = _public_mils_to_coord_scalar(value)
        self.secondary_radius = whole
        self.secondary_radius_frac = frac


def _coord_scalar_to_public_mils(
    value: int | float | str, frac: int | float | str
) -> float:
    """
    Convert Altium coord-style whole/fraction scalar storage to public mils.
    """
    return int(value) * 10.0 + int(frac) / 10000.0


def _coord_scalar_to_native_units(
    value: int | float | str, frac: int | float | str
) -> float:
    """
    Convert coord-style whole/fraction storage to native schematic units.

    Public schematic APIs expose mils, but native SchDoc SVG/geometry export
    uses the persisted drawing units directly. One native schematic unit is
    10 mils for these scalar radius fields.
    """
    return int(value) + int(frac) / 100000.0


def _rounded_native_numerator(numerator: int) -> int:
    magnitude = (abs(numerator) + 50_000) // 100_000
    return int(magnitude if numerator >= 0 else -magnitude)


def _coord_scalar_to_rounded_native_units(
    value: int | float | str,
    frac: int | float | str = 0,
) -> int:
    """Reduce a full coord-style scalar to the integer connectivity grid."""
    numerator = int(value) * 100_000 + int(frac)
    return _rounded_native_numerator(numerator)


def _coord_numerator_to_parts(numerator: int) -> tuple[int, int]:
    """Return a normalized whole/fraction pair for a coordinate numerator."""
    return divmod(int(numerator), 100_000)


def _coord_scalar_to_native_parts(
    value: int | float | str,
    frac: int | float | str = 0,
) -> tuple[int, int]:
    """Compose stored whole/fraction fields without reducing their precision."""
    return _coord_numerator_to_parts(int(value) * 100_000 + int(frac))


def _coord_scalar_with_basic_entry_distance_to_native_parts(
    value: int | float | str,
    frac: int | float | str,
    distance_from_top: int | float | str,
    distance_from_top_frac1: int | float | str = 0,
    *,
    direction: int,
) -> tuple[int, int]:
    """Compose a stored coordinate and basic-entry offset without grid reduction."""
    coord_numerator = int(value) * 100_000 + int(frac)
    distance_numerator = int(distance_from_top) * 1_000_000 + int(
        distance_from_top_frac1
    )
    return _coord_numerator_to_parts(coord_numerator + direction * distance_numerator)


def _effective_basic_entry_distance_frac1(value: object) -> int:
    """Return AD's internal fractional distance for a basic entry record."""
    frac = int(getattr(value, "distance_from_top_frac", 0))
    if frac != 0:
        return frac * 10
    return int(getattr(value, "distance_from_top_frac1", 0))


def _coord_scalar_with_basic_entry_distance_to_rounded_native_units(
    value: int | float | str,
    frac: int | float | str,
    distance_from_top: int | float | str,
    distance_from_top_frac1: int | float | str = 0,
    *,
    direction: int,
) -> int:
    """Apply a full-precision basic-entry offset before grid reduction."""
    coord_numerator = int(value) * 100_000 + int(frac)
    distance_numerator = int(distance_from_top) * 1_000_000 + int(
        distance_from_top_frac1
    )
    return _rounded_native_numerator(coord_numerator + direction * distance_numerator)


def _public_mils_to_coord_scalar(value: float) -> tuple[int, int]:
    """
    Convert a public mil value to Altium coord-style whole/fraction storage.
    """
    whole = int(value / 10.0)
    frac = round((value - whole * 10.0) * 10000.0)
    return whole, frac


def _basic_entry_distance_to_public_mils(
    value: int | float | str,
    frac1: int | float | str = 0,
) -> float:
    """
    Convert basic-entry DistanceFromTop storage to public mils.

    Altium's sheet-entry and harness-entry distance field is not a normal
    CoordPoint scalar. The whole field stores 100-mil steps and Frac1 stores
    millionths of one step.
    """
    return int(value) * 100.0 + int(frac1) / 10000.0


def _basic_entry_distance_to_native_units(
    value: int | float | str,
    frac1: int | float | str = 0,
) -> float:
    """
    Convert basic-entry DistanceFromTop storage to native SVG units.

    Native schematic SVG coordinates use the persisted 10-mil drawing units,
    so the 100-mil DistanceFromTop step must be converted to ten native units.
    """
    return int(value) * 10.0 + int(frac1) / 100000.0


def _basic_entry_distance_to_rounded_native_units(
    value: int | float | str | object,
    frac1: int | float | str = 0,
) -> int:
    """
    Convert basic-entry DistanceFromTop storage to an integer hotspot offset.

    Netlist endpoint matching uses integer schematic coordinates. Use explicit
    half-away-from-zero rounding here; Python's built-in ``round()`` uses
    tie-to-even behavior and must not define this public connectivity path.
    """
    if not isinstance(value, (int, float, str)):
        frac1 = _effective_basic_entry_distance_frac1(value)
        value = getattr(value, "distance_from_top", 0)
    value_scalar = value if isinstance(value, (int, float, str)) else 0
    frac_scalar = frac1 if isinstance(frac1, (int, float, str)) else 0
    numerator = int(value_scalar) * 1_000_000 + int(frac_scalar)
    magnitude = (abs(numerator) + 50_000) // 100_000
    return int(magnitude if numerator >= 0 else -magnitude)


def _public_mils_to_basic_entry_distance(value: float) -> tuple[int, int]:
    """
    Convert public mils to Altium basic-entry DistanceFromTop storage.
    """
    whole_steps = int(value / 100.0)
    frac1 = round((value - whole_steps * 100.0) * 10000.0)
    if frac1 >= 1_000_000:
        whole_steps += 1
        frac1 = 0
    return whole_steps, frac1


class _BasicEntryDistanceRecord(Protocol):
    distance_from_top: int
    distance_from_top_frac: int
    distance_from_top_frac1: int
    _has_distance_from_top_frac: bool


def _set_basic_entry_distance_mils(
    record: _BasicEntryDistanceRecord,
    value: float,
) -> None:
    """Store a public distance using AD's canonical Frac1 representation."""
    record.distance_from_top, record.distance_from_top_frac1 = (
        _public_mils_to_basic_entry_distance(value)
    )
    record.distance_from_top_frac = 0
    record._has_distance_from_top_frac = False


class BasicEntryDistanceMilsMixin:
    """Public and internal distance accessors shared by basic entry records."""

    distance_from_top: int
    distance_from_top_frac: int
    distance_from_top_frac1: int
    _has_distance_from_top_frac: bool

    @property
    def distance_from_top_mils(self) -> float:
        """Distance from the owning symbol or connector edge in mils."""
        return _basic_entry_distance_to_public_mils(
            self.distance_from_top,
            _effective_basic_entry_distance_frac1(self),
        )

    @distance_from_top_mils.setter
    def distance_from_top_mils(self, value: float) -> None:
        _set_basic_entry_distance_mils(self, value)

    def _distance_from_top_native_units(self) -> float:
        return _basic_entry_distance_to_native_units(
            self.distance_from_top,
            _effective_basic_entry_distance_frac1(self),
        )

    def _rounded_distance_from_top_native_units(self) -> int:
        return _basic_entry_distance_to_rounded_native_units(
            self.distance_from_top,
            _effective_basic_entry_distance_frac1(self),
        )


class CornerXRadiusMilsMixin:
    corner_x_radius: int
    corner_x_radius_frac: int

    @property
    def corner_x_radius_mils(self) -> float:
        """
        Public rounded-corner X radius in mils.

        Use this property for normal mutation and inspection. It hides the
        underlying Altium coord-style whole/fraction storage fields
        (``corner_x_radius`` / ``corner_x_radius_frac``).
        """
        return _coord_scalar_to_public_mils(
            self.corner_x_radius,
            self.corner_x_radius_frac,
        )

    @corner_x_radius_mils.setter
    def corner_x_radius_mils(self, value: float) -> None:
        """
        Set the public rounded-corner X radius in mils.
        """
        whole, frac = _public_mils_to_coord_scalar(value)
        self.corner_x_radius = whole
        self.corner_x_radius_frac = frac


class CornerYRadiusMilsMixin:
    corner_y_radius: int
    corner_y_radius_frac: int

    @property
    def corner_y_radius_mils(self) -> float:
        """
        Public rounded-corner Y radius in mils.

        Use this property for normal mutation and inspection. It hides the
        underlying Altium coord-style whole/fraction storage fields
        (``corner_y_radius`` / ``corner_y_radius_frac``).
        """
        return _coord_scalar_to_public_mils(
            self.corner_y_radius,
            self.corner_y_radius_frac,
        )

    @corner_y_radius_mils.setter
    def corner_y_radius_mils(self, value: float) -> None:
        """
        Set the public rounded-corner Y radius in mils.
        """
        whole, frac = _public_mils_to_coord_scalar(value)
        self.corner_y_radius = whole
        self.corner_y_radius_frac = frac


class RotatedLocalPointMixin:
    def _rotate_svg_offset(self, dx: float, dy: float) -> tuple[float, float]:
        raise NotImplementedError

    def _transform_local_point(
        self, x: float, y: float, dx: float, dy: float
    ) -> tuple[float, float]:
        offset_x, offset_y = self._rotate_svg_offset(dx, dy)
        return x + offset_x, y + offset_y


def geometry_coord_tuple(
    px: float,
    py: float,
    *,
    sheet_height_px: float,
    units_per_px: int,
) -> tuple[float, float]:
    """Convert an SVG-space point into schematic geometry coordinates."""
    return svg_coord_to_geometry(
        px,
        py,
        sheet_height_px=sheet_height_px,
        units_per_px=units_per_px,
    )


def geometry_coord_list(
    px: float,
    py: float,
    *,
    sheet_height_px: float,
    units_per_px: int,
) -> list[float]:
    """Convert an SVG-space point into a list-form schematic geometry point."""
    gx, gy = geometry_coord_tuple(
        px,
        py,
        sheet_height_px=sheet_height_px,
        units_per_px=units_per_px,
    )
    return [gx, gy]


def rotate_point_about_origin(
    px: float,
    py: float,
    *,
    origin_x: float,
    origin_y: float,
    cos_theta: float,
    sin_theta: float,
) -> tuple[float, float]:
    """Rotate a point around an origin using precomputed trig values."""
    dx = px - origin_x
    dy = py - origin_y
    return (
        origin_x + dx * cos_theta - dy * sin_theta,
        origin_y + dx * sin_theta + dy * cos_theta,
    )


def fill_indicator_color_from_area_color(area_color: int | None) -> str:
    """
    Derive the collapse-indicator fill color from the record area color.
    """
    if area_color is not None:
        area_hex = color_to_hex(area_color).upper()
        if area_hex in ("#FAFAFA", "#FFFFFF"):
            return "#FEC8C8"
        if area_hex == "#FFD966":
            return "#FFAE52"
        if area_hex == "#CCCCCC":
            return "#F5A3A3"
    return "#FEC8C8"


def derive_triangle_indicator_colors(
    border_color: str,
    *,
    area_color: int | None,
) -> tuple[str, str]:
    """
    Derive collapse-indicator stroke and fill colors from the border color.
    """
    border_upper = border_color.upper()
    if border_upper == "#800000":
        return "#990000", fill_indicator_color_from_area_color(area_color)
    if border_upper in ("#000000", "#434343"):
        return "#330000", "#F5A3A3"
    return border_color, fill_indicator_color_from_area_color(area_color)
