"""
Altium PCB Component

Represents a component instance from Altium PcbDoc files.
This is an Altium-specific data structure that stores raw parsed data.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import TypeVar

from .altium_api_markers import public_api
from .altium_common_enums import ComponentKind
from .altium_pcb_enums import PcbLibIdentifierKind, PcbTextAutoposition

_EnumT = TypeVar("_EnumT", bound=IntEnum)


@public_api
@dataclass
class AltiumPcbComponent:
    """
    Component instance from Altium PcbDoc file.

    This is an Altium-specific representation that stores raw fields from the
    Components6/Data stream in a PcbDoc file.

    Attributes:
        designator: Component reference (e.g., "R1", "U1")
        footprint: Footprint name (PATTERN field)
        layer: Side of PCB ("TOP" or "BOTTOM" from LAYER field)
        x: X position with "mil" suffix (e.g., "27514.9995mil")
        y: Y position with "mil" suffix
        rotation: Rotation angle in scientific notation (e.g., "1.80000000000000E+0002" = 180 deg)
        unique_id: 8-character unique identifier (UNIQUEID field, linkage key for pads/parameters)
        union_index: User-union index from the component `UNIONINDEX` field.
        description: Component description (SOURCEDESCRIPTION field)
        parameters: Dict of component parameters from PrimitiveParameters/Data
        raw_record: Original text record dict from Components6/Data
        component_kind: ComponentKind enum value (parsed from COMPONENTKIND/VERSION2/VERSION3 fields)

    Position Format:
        - Stored as string with "mil" suffix (Altium format)
        - Example: x="27514.9995mil", y="15000.0000mil"
        - Use get_x_mils() / get_y_mils() to get position as float

    """

    designator: str
    footprint: str
    layer: str
    x: str  # "27514.9995mil" format
    y: str  # "27514.9995mil" format
    rotation: str = ""
    unique_id: str = ""
    union_index: int = 0
    description: str = ""
    parameters: dict[str, object] | None = None
    raw_record: dict[str, object] | None = None
    component_kind: ComponentKind = ComponentKind.STANDARD

    def __post_init__(self) -> None:
        """
        Initialize default mutable fields.
        """
        if self.parameters is None:
            self.parameters = {}
        if self.raw_record is None:
            self.raw_record = {}

    def to_record(self) -> dict[str, object]:
        """
        Return a copy of the source component record.
        """
        return dict(self.raw_record or {})

    def _raw_record_map(self) -> dict[str, object]:
        if self.raw_record is None:
            self.raw_record = {}
        return self.raw_record

    def _raw_text(self, key: str) -> str:
        value = self._raw_record_map().get(key)
        return "" if value is None else str(value)

    def _int_field(self, key: str) -> int | None:
        value = self._raw_record_map().get(key)
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(str(value).strip())
        except ValueError:
            return None

    def _enum_field(self, key: str, enum_type: type[_EnumT]) -> _EnumT | None:
        value = self._int_field(key)
        if value is None:
            return None
        try:
            return enum_type(value)
        except ValueError:
            return None

    def _bool_flag(self, key: str, default: bool) -> bool:
        value = self._raw_record_map().get(key)
        if value is None:
            return default
        return str(value).strip().upper() not in {"FALSE", "0", "NO", "OFF"}

    def _optional_bool_flag(self, key: str) -> bool | None:
        value = self._raw_record_map().get(key)
        if value is None:
            return None
        return str(value).strip().upper() not in {"FALSE", "0", "NO", "OFF"}

    @staticmethod
    def _altium_path_segments(value: str) -> tuple[str, ...]:
        return tuple(part for part in str(value).split("\\") if part)

    @property
    def name_on(self) -> bool:
        return self._bool_flag("NAMEON", True)

    @property
    def comment_on(self) -> bool:
        return self._bool_flag("COMMENTON", True)

    @property
    def value_on(self) -> bool:
        return self._bool_flag("VALUEON", False)

    @property
    def designator_on(self) -> bool:
        return self._bool_flag("DESIGNATORON", False)

    @property
    def name_auto_position(self) -> PcbTextAutoposition | None:
        return self._enum_field("NAMEAUTOPOSITION", PcbTextAutoposition)

    @property
    def comment_auto_position(self) -> PcbTextAutoposition | None:
        return self._enum_field("COMMENTAUTOPOSITION", PcbTextAutoposition)

    @property
    def channel_offset(self) -> int | None:
        """
        Numeric channel instance offset used by hierarchical/repeated projects.
        """

        return self._int_field("CHANNELOFFSET")

    @property
    def source_designator(self) -> str:
        return self._raw_text("SOURCEDESIGNATOR")

    @property
    def source_unique_id(self) -> str:
        return self._raw_text("SOURCEUNIQUEID")

    @property
    def source_unique_id_segments(self) -> tuple[str, ...]:
        return self._altium_path_segments(self.source_unique_id)

    @property
    def source_hierarchical_path(self) -> str:
        return self._raw_text("SOURCEHIERARCHICALPATH")

    @property
    def source_hierarchy_segments(self) -> tuple[str, ...]:
        return self._altium_path_segments(self.source_hierarchical_path)

    @property
    def source_footprint_library(self) -> str:
        return self._raw_text("SOURCEFOOTPRINTLIBRARY")

    @property
    def source_footprint_library_name(self) -> str:
        library = self.source_footprint_library
        if "\\" in library:
            library = library.rsplit("\\", 1)[-1]
        if "/" in library:
            library = library.rsplit("/", 1)[-1]
        return library

    @property
    def source_lib_reference(self) -> str:
        return self._raw_text("SOURCELIBREFERENCE")

    @property
    def source_component_library(self) -> str:
        return self._raw_text("SOURCECOMPONENTLIBRARY")

    @property
    def source_component_library_identifier_kind(
        self,
    ) -> PcbLibIdentifierKind | None:
        return self._enum_field("SOURCECOMPLIBIDENTIFIERKIND", PcbLibIdentifierKind)

    @property
    def source_component_library_identifier(self) -> str:
        return self._raw_text("SOURCECOMPLIBRARYIDENTIFIER")

    @property
    def footprint_description(self) -> str:
        return self._raw_text("FOOTPRINTDESCRIPTION")

    @property
    def height(self) -> str:
        return str(self._raw_record_map().get("HEIGHT", "0mil") or "0mil")

    @property
    def lock_strings(self) -> bool | None:
        return self._optional_bool_flag("LOCKSTRINGS")

    @property
    def enable_pin_swapping(self) -> bool | None:
        return self._optional_bool_flag("ENABLEPINSWAPPING")

    @property
    def enable_part_swapping(self) -> bool | None:
        return self._optional_bool_flag("ENABLEPARTSWAPPING")

    @property
    def jumpers_visible(self) -> bool | None:
        return self._optional_bool_flag("JUMPERSVISIBLE")

    @staticmethod
    def _make_mils(s: str) -> float:
        """
        Convert Altium position string to mils (float).

        Args:
            s: Position string (e.g., "27514.9995mil")

        Returns:
            Position in mils (e.g., 27514.9995)
        """
        return float(str(s).strip("mil"))

    @staticmethod
    def _normalize_layer(altium_layer: str) -> str:
        """
        Normalize Altium layer string to PcbLayer-compatible format.

        Args:
            altium_layer: Altium layer string (e.g., "TOP", "BOTTOM", "TOPLAYER", "BOTTOMLAYER")

        Returns:
            Normalized layer string ("top" or "bottom")
        """
        layer_upper = altium_layer.upper()
        if "TOP" in layer_upper:
            return "top"
        elif "BOTTOM" in layer_upper:
            return "bottom"
        else:
            # Unknown layer, return as-is lowercase
            return altium_layer.lower()

    @staticmethod
    def _clean_rotation(rotation_str: str) -> float:
        """
        Convert Altium rotation string to degrees (float).

        Args:
            rotation_str: Rotation in scientific notation (e.g., "1.80000000000000E+0002")

        Returns:
            Rotation in degrees (e.g., 180.0)
        """
        if not rotation_str:
            return 0.0
        try:
            return float(rotation_str)
        except ValueError:
            return 0.0

    def get_x_mils(self, board_origin_x: float = 0.0) -> float:
        """
        Get X position in mils (relative to board origin).

        Args:
            board_origin_x: Board origin X offset in mils (from Board6/ORIGINX)

        Returns:
            X position in mils
        """
        return self._make_mils(self.x) - board_origin_x

    def get_y_mils(self, board_origin_y: float = 0.0) -> float:
        """
        Get Y position in mils (relative to board origin).

        Args:
            board_origin_y: Board origin Y offset in mils (from Board6/ORIGINY)

        Returns:
            Y position in mils
        """
        return self._make_mils(self.y) - board_origin_y

    def get_rotation_degrees(self) -> float:
        """
        Get rotation in degrees.

        Returns:
            Rotation in degrees (0.0 if not set or invalid)
        """
        return self._clean_rotation(self.rotation)

    def get_layer_normalized(self) -> str:
        """
        Get normalized layer string.

        Returns:
            "top", "bottom", or original layer lowercase
        """
        return self._normalize_layer(self.layer)

    def __str__(self) -> str:
        """
        String representation.
        """
        return f"AltiumPcbComponent({self.designator}, {self.footprint}, layer={self.layer})"

    def __repr__(self) -> str:
        """
        Developer representation.
        """
        return (
            f"AltiumPcbComponent(designator='{self.designator}', "
            f"footprint='{self.footprint}', layer='{self.layer}', "
            f"x='{self.x}', y='{self.y}')"
        )
