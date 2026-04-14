"""
Parse `.SchLib` schematic library files into an object model.
"""

from __future__ import annotations

import logging
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import (
    AltiumSchDesignator,
    AltiumSchImplementation,
    AltiumSchImplementationList,
    AltiumSchImplParams,
    AltiumSchLabel,
    AltiumSchMapDefiner,
    AltiumSchMapDefinerList,
    AltiumSchParameter,
    AltiumSchPin,
    AltiumSchTextFrame,
)
from .altium_api_markers import public_api
from .altium_font_manager import FontIDManager
from .altium_json_apply_helpers import JsonApplyMixin
from .altium_object_collection import ObjectCollection, ObjectCollectionView
from .altium_ole import AltiumOleFile, AltiumOleWriter
from .altium_record_types import CoordPoint, LineWidth, SchRecordType, TextOrientation
from .altium_sch_binding import SchematicBindingContext
from .altium_sch_implementation_helpers import (
    build_footprint_implementation_payload,
    clean_implementation_child_record_fields,
    clean_implementation_record_fields,
)
from .altium_sch_json_object_types import sch_json_object_type_from_record
from .altium_sch_record_factory import (
    create_record_from_record,
    create_record_from_type,
)
from .altium_schlib_aux_streams import (
    build_pinfrac_stream_for_pins,
    build_pintextdata_stream_for_pins,
)
from .altium_utilities import (
    as_dynamic as _as_dynamic,
    create_storage_stream,
    create_stream_from_records,
    get_records_in_section,
    parse_storage_stream,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryDocument
    from .altium_sch_svg_renderer import SchSvgRenderOptions


def _parse_pinfunctiondata_from_ole(
    ole: Any, symbol_storage: str
) -> dict[str, Any] | None:
    """
    Parse PinFunctionData from OLE stream for a symbol.

    PinFunctionData contains alternate function definitions for multi-functional
    pins (e.g., GPIO/UART/SPI modes).

    Note: migrated from the older altium_pin_parser.py implementation.
    PinFunctionData is only used during SchLib parsing, so it belongs here.

    Args:
        ole: AltiumOleFile object
        symbol_storage: Symbol storage name (truncated to 31 chars if longer!)

    Returns:
        Dict with 'functions' key containing list of alternate function names,
        or None if no alternate functions defined
    """
    stream_path = f"{symbol_storage}/PinFunctionData"

    # Check if PinFunctionData exists
    if not ole.exists(stream_path):
        return None

    try:
        # Read OLE stream
        records = get_records_in_section(ole, stream_path)

        # Find binary record
        binary_data = None
        for rec in records:
            if rec.get("__BINARY_RECORD__"):
                binary_data = rec["__BINARY_DATA__"]
                break

        if binary_data is None:
            return None

        # Find zlib compressed data (starts with 0x78 0x9C)
        compressed_offset = None
        for i in range(len(binary_data) - 1):
            if binary_data[i : i + 2] == b"\x78\x9c":
                compressed_offset = i
                break

        if compressed_offset is None:
            return None

        # Decompress
        decompressed = zlib.decompress(binary_data[compressed_offset:])

        # Decode as UTF-16 LE (skip first 2 bytes - likely BOM or padding)
        text = decompressed[2:].decode("utf-16-le", errors="replace")

        # Parse pipe-delimited format
        # Format: |PINDEFINEDFUNCTIONSCOUNT=N|PINDEFINEDFUNCTION1=name|PINDEFINEDFUNCTION2=name|...
        parts = text.split("|")
        functions = []
        count = 0

        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                if key == "PINDEFINEDFUNCTIONSCOUNT":
                    count = int(value)
                elif key.startswith("PINDEFINEDFUNCTION"):
                    functions.append(value)

        return {"count": count, "functions": functions}

    except Exception as e:
        log.debug(f"Error parsing PinFunctionData: {e}")
        return None


def _create_record_object(record: dict[str, Any]) -> Any | None:
    """
    Factory function to create appropriate record object from raw record.

    Args:
        record: Raw record dictionary

    Returns:
        Parsed record object or None if type not supported
    """
    if "RECORD" not in record:
        return None

    try:
        SchRecordType(int(record["RECORD"]))
    except (ValueError, KeyError):
        return None

    # Create record object using raw-record-aware factory
    record_obj = create_record_from_record(record)

    if record_obj:
        # Parse the record data into the object
        record_obj.parse_from_record(record)

    return record_obj


def _cp1252_fallback(value: str) -> str:
    """
    Return a Windows-1252-safe fallback for an Altium text-record value.
    """
    return value.encode("cp1252", errors="replace").decode("cp1252")


def _needs_utf8_field(value: str) -> bool:
    """
    Return True when a value cannot be represented in native cp1252 fields.
    """
    try:
        value.encode("cp1252")
    except UnicodeEncodeError:
        return True
    return False


def _set_header_text_field(
    header: dict[str, str],
    key: str,
    value: str,
    *,
    fallback: str | None = None,
) -> None:
    """
    Set an Altium FileHeader field, adding a %UTF8% companion when required.
    """
    if _needs_utf8_field(value):
        header[f"%UTF8%{key}"] = value
        header[key] = _cp1252_fallback(fallback or value)
        return
    header[key] = value


def _header_symbol_fallback_name(symbol: "AltiumSymbol") -> str:
    """
    Return the cp1252 FileHeader LibRef fallback for a symbol.
    """
    component_record = symbol.component_record or {}
    return str(
        component_record.get("LibReference")
        or component_record.get("LIBREFERENCE")
        or component_record.get("DesignItemId")
        or component_record.get("DESIGNITEMID")
        or symbol.original_name
        or symbol.name
    )


class AltiumSymbol:
    """
    Represents a single symbol in a SchLib.

        New code should create symbols through ``AltiumSchLib.add_symbol(...)``
        instead of constructing ``AltiumSymbol(...)`` directly. Direct
        construction is a low-level parser/authoring detail and should generally
        be reserved for ``altium_monkey`` internals and tightly scoped
        conversion code.

        Objects are stored in a single ``ObjectCollection`` accessible via
        ``.objects``. Typed convenience properties (``.pins``,
        ``.graphic_primitives``, ``.rectangles``, etc.) return live read-only
        filtered views. Use the symbol's explicit add/remove APIs or ``.objects``
        to change membership.
    """

    # Graphics types used by the .graphic_primitives aggregate view
    _GRAPHICS_TYPES: tuple[type, ...] | None = None

    def __init__(self, name: str, *, original_name: str | None = None) -> None:
        self.name = name
        self.original_name = original_name or name
        self.component_record = None
        self.objects: ObjectCollection = ObjectCollection()
        self.raw_records: list[dict[str, str]] = []
        self._schematic_binding_context: SchematicBindingContext | None = None

        # Component metadata
        self.description = ""
        self.part_count = 1
        self.display_mode = 0
        self.display_mode_count = 1

        # Original auxiliary streams for round-trip (PinTextData, PinFrac, etc.)
        self._original_streams: dict[str, bytes] = {}

    def _bind_to_schematic_library(self, schlib: "AltiumSchLib") -> None:
        """
        Bind this symbol and its records to a schematic library context.
        """
        self._schematic_binding_context = schlib._binding_context()
        self._bind_all_objects_to_context()

    def _bind_object_to_context(self, obj: Any) -> None:
        bind_hook = getattr(obj, "_bind_to_schematic_context", None)
        if callable(bind_hook) and self._schematic_binding_context is not None:
            bind_hook(self._schematic_binding_context)

    def _bind_all_objects_to_context(self) -> None:
        for obj in self.objects:
            self._bind_object_to_context(obj)

    def add_object(self, obj: Any) -> Any:
        """
        Add an object to this symbol and bind document-scoped context if present.
        """
        self._bind_object_to_context(obj)
        self.objects.append(obj)
        self._register_embedded_image_object(obj)
        return obj

    def _register_embedded_image_object(self, obj: Any) -> None:
        """
        Mirror image payloads into the owning SchLib storage table when possible.
        """
        from .altium_record_sch__image import AltiumSchImage

        if not isinstance(obj, AltiumSchImage):
            return
        if not obj.filename or not getattr(obj, "image_data", None):
            return
        owner = (
            self._schematic_binding_context.owner
            if self._schematic_binding_context is not None
            else None
        )
        if owner is None or not hasattr(owner, "embedded_images"):
            return
        owner.embedded_images[obj.filename] = obj.image_data

    def set_description(self, description: str) -> AltiumSymbol:
        """
        Set the symbol/component description.
        """
        self.description = description
        return self

    def set_part_count(self, count: int) -> AltiumSymbol:
        """
        Set the logical schematic part count.

        Args:
            count: Number of parts. Must be >= 1.
        """
        if count < 1:
            raise ValueError("part count must be >= 1")
        self.part_count = count
        return self

    # -- Typed convenience properties --
    # Each returns an ObjectCollection filtered from self.objects.

    @property
    def pins(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchPin)

    @property
    def labels(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchLabel)

    @property
    def images(self) -> ObjectCollection:
        from .altium_record_sch__image import AltiumSchImage

        return self.objects.of_type(AltiumSchImage)

    @property
    def parameters(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchParameter)

    @property
    def designators(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchDesignator)

    @property
    def text_frames(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchTextFrame)

    @property
    def implementation_lists(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchImplementationList)

    @property
    def implementations(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchImplementation)

    @property
    def map_definer_lists(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchMapDefinerList)

    @property
    def map_definers(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchMapDefiner)

    @property
    def impl_params(self) -> ObjectCollection:
        return self.objects.of_type(AltiumSchImplParams)

    @property
    def graphic_primitives(self) -> ObjectCollection:
        """
        All graphical primitive objects (lines, rectangles, arcs, etc.).

                This aggregate excludes pins, parameters, labels, images, designators,
                and text frames. Prefer specific typed properties like ``.rectangles``
                or ``.lines`` when a caller needs one known primitive class.
        """
        if AltiumSymbol._GRAPHICS_TYPES is None:
            from . import (
                AltiumSchArc,
                AltiumSchBezier,
                AltiumSchEllipse,
                AltiumSchEllipticalArc,
                AltiumSchIeeeSymbol,
                AltiumSchLine,
                AltiumSchPieChart,
                AltiumSchPolygon,
                AltiumSchPolyline,
                AltiumSchRectangle,
                AltiumSchRoundedRectangle,
            )

            AltiumSymbol._GRAPHICS_TYPES = (
                AltiumSchArc,
                AltiumSchBezier,
                AltiumSchEllipse,
                AltiumSchEllipticalArc,
                AltiumSchIeeeSymbol,
                AltiumSchLine,
                AltiumSchPieChart,
                AltiumSchPolygon,
                AltiumSchPolyline,
                AltiumSchRectangle,
                AltiumSchRoundedRectangle,
            )
        return ObjectCollectionView(
            self.objects,
            lambda o: isinstance(o, AltiumSymbol._GRAPHICS_TYPES),
        )

    # -- Specific shape properties --

    @property
    def rectangles(self) -> ObjectCollection:
        from .altium_record_sch__rectangle import AltiumSchRectangle

        return self.objects.of_type(AltiumSchRectangle)

    @property
    def lines(self) -> ObjectCollection:
        from .altium_record_sch__line import AltiumSchLine

        return self.objects.of_type(AltiumSchLine)

    @property
    def arcs(self) -> ObjectCollection:
        from .altium_record_sch__arc import AltiumSchArc

        return self.objects.of_type(AltiumSchArc)

    @property
    def polylines(self) -> ObjectCollection:
        from .altium_record_sch__polyline import AltiumSchPolyline

        return self.objects.of_type(AltiumSchPolyline)

    @property
    def polygons(self) -> ObjectCollection:
        from .altium_record_sch__polygon import AltiumSchPolygon

        return self.objects.of_type(AltiumSchPolygon)

    @property
    def beziers(self) -> ObjectCollection:
        from .altium_record_sch__bezier import AltiumSchBezier

        return self.objects.of_type(AltiumSchBezier)

    @property
    def ellipses(self) -> ObjectCollection:
        from .altium_record_sch__ellipse import AltiumSchEllipse

        return self.objects.of_type(AltiumSchEllipse)

    @property
    def pie_charts(self) -> ObjectCollection:
        from .altium_record_sch__piechart import AltiumSchPieChart

        return self.objects.of_type(AltiumSchPieChart)

    @property
    def ieee_symbols(self) -> ObjectCollection:
        from .altium_record_sch__ieee_symbol import AltiumSchIeeeSymbol

        return self.objects.of_type(AltiumSchIeeeSymbol)

    # -- Convenience add_* methods --

    def add_pin(self, pin: AltiumSchPin) -> AltiumSchPin:
        """
        Add a pin to this symbol.

                Args:
                    pin: AltiumSchPin object.

                Returns:
                    The added pin.
        """
        if not isinstance(pin, AltiumSchPin):
            raise TypeError("AltiumSchLib.add_pin() requires an AltiumSchPin instance")
        return self.add_object(pin)

    def add_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALL,
        is_solid: bool = True,
        transparent: bool = False,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a rectangle to this symbol.

                Args:
                    x1, y1: First corner in 10-mil units.
                    x2, y2: Opposite corner in 10-mil units.
                    color: Border color (Win32 0x00BBGGRR).
                    area_color: Fill color.
                    is_solid: Whether rectangle is filled.
                    owner_part_id: Part ID (-1 for all parts).

                Returns:
                    The added AltiumSchRectangle.
        """
        from .altium_record_sch__rectangle import AltiumSchRectangle

        rect = AltiumSchRectangle()
        rect.location = CoordPoint.from_mils(x1, y1)
        rect.corner = CoordPoint.from_mils(x2, y2)
        rect.color = color
        rect.area_color = area_color
        rect.line_width = line_width
        rect.is_solid = is_solid
        rect.transparent = transparent
        rect.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        rect.is_not_accessible = True
        return self.add_object(rect)

    def add_line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a line to this symbol.
        """
        from .altium_record_sch__line import AltiumSchLine

        line = AltiumSchLine()
        line.location = CoordPoint.from_mils(x1, y1)
        line.corner = CoordPoint.from_mils(x2, y2)
        line.color = color
        line.line_width = line_width
        line.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        line.is_not_accessible = True
        return self.add_object(line)

    def add_arc(
        self,
        x: int,
        y: int,
        radius: int,
        *,
        start_angle: float = 0.0,
        end_angle: float = 360.0,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an arc to this symbol.
        """
        from .altium_record_sch__arc import AltiumSchArc

        arc = AltiumSchArc()
        arc.location = CoordPoint.from_mils(x, y)
        arc.radius = int(radius // 10)
        arc.start_angle = start_angle
        arc.end_angle = end_angle
        arc.color = color
        arc.line_width = line_width
        arc.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        arc.is_not_accessible = True
        return self.add_object(arc)

    def add_elliptical_arc(
        self,
        x: int,
        y: int,
        radius: int,
        secondary_radius: int,
        *,
        start_angle: float = 0.0,
        end_angle: float = 360.0,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an elliptical arc to this symbol.
        """
        from .altium_record_sch__elliptical_arc import AltiumSchEllipticalArc

        earc = AltiumSchEllipticalArc()
        earc.location = CoordPoint.from_mils(x, y)
        earc.radius = int(radius // 10)
        earc.secondary_radius = int(secondary_radius // 10)
        earc.start_angle = start_angle
        earc.end_angle = end_angle
        earc.color = color
        earc.line_width = line_width
        earc.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        earc.is_not_accessible = True
        return self.add_object(earc)

    def add_polyline(
        self,
        vertices: list[tuple[int, int]],
        *,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a polyline to this symbol.
        """
        from .altium_record_sch__polyline import AltiumSchPolyline

        poly = AltiumSchPolyline()
        poly.vertices = [CoordPoint.from_mils(v[0], v[1]) for v in vertices]
        poly.location = poly.vertices[0] if poly.vertices else CoordPoint()
        poly.color = color
        poly.line_width = line_width
        poly.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        poly.is_not_accessible = True
        return self.add_object(poly)

    def add_polygon(
        self,
        vertices: list[tuple[int, int]],
        *,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALLEST,
        is_solid: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a polygon to this symbol.
        """
        from .altium_record_sch__polygon import AltiumSchPolygon

        poly = AltiumSchPolygon()
        poly.vertices = [CoordPoint.from_mils(v[0], v[1]) for v in vertices]
        poly.location = poly.vertices[0] if poly.vertices else CoordPoint()
        poly.color = color
        poly.area_color = area_color
        poly.line_width = line_width
        poly.is_solid = is_solid
        poly.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        poly.is_not_accessible = True
        return self.add_object(poly)

    def add_ellipse(
        self,
        x: int,
        y: int,
        radius_x: int,
        radius_y: int,
        *,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALLEST,
        is_solid: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an ellipse to this symbol.
        """
        from .altium_record_sch__ellipse import AltiumSchEllipse

        ellipse = AltiumSchEllipse()
        ellipse.location = CoordPoint.from_mils(x, y)
        ellipse.radius = int(radius_x // 10)
        ellipse.secondary_radius = int(radius_y // 10)
        ellipse.color = color
        ellipse.area_color = area_color
        ellipse.line_width = line_width
        ellipse.is_solid = is_solid
        ellipse.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        ellipse.is_not_accessible = True
        return self.add_object(ellipse)

    def add_rounded_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        corner_x_radius: int = 50,
        corner_y_radius: int = 50,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALL,
        is_solid: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a rounded rectangle to this symbol.
        """
        from .altium_record_sch__rounded_rectangle import AltiumSchRoundedRectangle

        rrect = AltiumSchRoundedRectangle()
        rrect.location = CoordPoint.from_mils(x1, y1)
        rrect.corner = CoordPoint.from_mils(x2, y2)
        rrect.corner_x_radius = int(corner_x_radius // 10)
        rrect.corner_y_radius = int(corner_y_radius // 10)
        rrect.color = color
        rrect.area_color = area_color
        rrect.line_width = line_width
        rrect.is_solid = is_solid
        rrect.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        rrect.is_not_accessible = True
        return self.add_object(rrect)

    def add_bezier(
        self,
        vertices: list[tuple[int, int]],
        *,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a Bezier curve to this symbol.
        """
        from .altium_record_sch__bezier import AltiumSchBezier

        if len(vertices) != 4:
            raise ValueError(f"Bezier requires exactly 4 vertices, got {len(vertices)}")

        bezier = AltiumSchBezier()
        bezier.vertices = [CoordPoint.from_mils(v[0], v[1]) for v in vertices]
        bezier.location = bezier.vertices[0]
        bezier.color = color
        bezier.line_width = line_width
        bezier.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        bezier.is_not_accessible = True
        return self.add_object(bezier)

    def add_label(
        self,
        text: str,
        x: int,
        y: int,
        *,
        color: int = 0x000000,
        font_id: int = 1,
        orientation: "TextOrientation" = 0,
        owner_part_id: int = -1,
    ) -> AltiumSchLabel:
        """
        Add a text label to this symbol.
        """
        label = AltiumSchLabel()
        label.text = text
        label.location = CoordPoint.from_mils(x, y)
        label.color = color
        label.font_id = font_id
        label.orientation = orientation
        label.owner_part_id = None if owner_part_id == -1 else owner_part_id
        return self.add_object(label)

    def add_parameter(
        self,
        name: str,
        text: str = "",
        *,
        x: int = 0,
        y: int = 0,
        is_hidden: bool = False,
        font_id: int = 1,
        read_only: bool = False,
        index_in_sheet: int | None = None,
        owner_part_id: int = -1,
        owner_index: int | None = None,
        unique_id: str = "",
    ) -> AltiumSchParameter:
        """
        Add a parameter to this symbol.
        """
        param = AltiumSchParameter()
        param.name = name
        param.text = text
        param.location = CoordPoint.from_mils(x, y)
        param.is_hidden = is_hidden
        param.font_id = font_id
        param.owner_part_id = None if owner_part_id == -1 else owner_part_id
        if owner_index is not None:
            param.owner_index = owner_index
        param.unique_id = unique_id
        _as_dynamic(param)._extra_fields = {}
        if read_only:
            _as_dynamic(param)._extra_fields["ReadOnlyState"] = "1"
        if index_in_sheet is not None:
            _as_dynamic(param)._extra_fields["IndexInSheet"] = str(index_in_sheet)
        return self.add_object(param)

    def add_designator(
        self,
        text: str = "U?",
        x: int = 0,
        y: int = 0,
        *,
        color: int = 0x000000,
        font_id: int = 1,
        owner_part_id: int = -1,
    ) -> AltiumSchDesignator:
        """
        Add a designator to this symbol.
        """
        desig = AltiumSchDesignator()
        desig.text = text
        desig.location = CoordPoint.from_mils(x, y)
        desig.color = color
        desig.font_id = font_id
        desig.owner_part_id = None if owner_part_id == -1 else owner_part_id
        return self.add_object(desig)

    def add_image(
        self,
        filename: str,
        image_data: bytes,
        x: int,
        y: int,
        corner_x: int,
        corner_y: int,
        *,
        keep_aspect: bool = True,
        embedded: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an embedded image to this symbol.
        """
        from .altium_record_sch__image import AltiumSchImage

        image = AltiumSchImage()
        image.filename = filename
        image.image_data = image_data
        image.embed_image = embedded
        image.keep_aspect = keep_aspect
        image.location = CoordPoint.from_mils(x, y)
        image.corner = CoordPoint.from_mils(corner_x, corner_y)
        image.owner_part_id = None if owner_part_id == -1 else owner_part_id
        return self.add_object(image)

    def add_implementation(
        self,
        impl_record: dict[str, object] | AltiumSchImplementation,
        children: list[dict[str, object] | Any] | None = None,
    ) -> AltiumSchImplementation:
        """
        Add an implementation record with optional child records.

        Args:
            impl_record: IMPLEMENTATION payload or typed implementation record.
            children: Optional child records (MAP_DEFINER_LIST, MAP_DEFINER,
                IMPL_PARAMS) as raw dicts or typed objects.
        """
        implementation = self._coerce_implementation_record(impl_record)
        self.add_object(implementation)
        for child in children or []:
            self.add_object(self._coerce_implementation_child_record(child))
        self._ensure_implementation_list_marker()
        self._rebuild_implementation_structure()
        return implementation

    def add_footprint(
        self,
        model_name: str,
        *,
        description: str = "",
        is_current: bool = True,
        library_name: str = "",
    ) -> AltiumSchImplementation:
        """
        Add a PCB footprint implementation to this symbol.
        """
        impl_record, children = build_footprint_implementation_payload(
            model_name,
            description=description,
            is_current=is_current,
            library_name=library_name,
        )
        return self.add_implementation(impl_record, children)

    @staticmethod
    def _is_implementation_related_object(obj: Any) -> bool:
        return isinstance(
            obj,
            (
                AltiumSchImplementationList,
                AltiumSchImplementation,
                AltiumSchMapDefinerList,
                AltiumSchMapDefiner,
                AltiumSchImplParams,
            ),
        )

    @staticmethod
    def _coerce_implementation_record(
        record: dict[str, object] | AltiumSchImplementation,
    ) -> AltiumSchImplementation:
        if isinstance(record, AltiumSchImplementation):
            return record
        if not isinstance(record, dict):
            raise TypeError(
                "add_implementation() requires an AltiumSchImplementation or raw record dict"
            )
        record_obj = _create_record_object(record)
        if not isinstance(record_obj, AltiumSchImplementation):
            raise TypeError("implementation record must resolve to an Implementation")
        return record_obj

    @staticmethod
    def _coerce_implementation_child_record(record: dict[str, object] | Any) -> Any:
        if isinstance(
            record,
            (AltiumSchMapDefinerList, AltiumSchMapDefiner, AltiumSchImplParams),
        ):
            return record
        if not isinstance(record, dict):
            raise TypeError(
                "implementation child records must be typed objects or raw record dicts"
            )
        record_obj = _create_record_object(record)
        if not isinstance(
            record_obj,
            (AltiumSchMapDefinerList, AltiumSchMapDefiner, AltiumSchImplParams),
        ):
            raise TypeError("implementation child must resolve to RECORD 46, 47, or 48")
        return record_obj

    def _ensure_implementation_list_marker(self) -> AltiumSchImplementationList:
        existing = next(iter(self.implementation_lists), None)
        if existing is not None:
            return existing
        marker = AltiumSchImplementationList()
        self.add_object(marker)
        return marker

    def _rebuild_implementation_structure(self) -> None:
        """
        Rebuild runtime implementation grouping from the symbol object list.
        """
        implementation_lists = list(self.implementation_lists)
        implementations: list[AltiumSchImplementation] = []

        for implementation_list in implementation_lists:
            implementation_list.children = []
        for implementation in self.implementations:
            implementation.children = []

        current_impl: AltiumSchImplementation | None = None
        for obj in self.objects:
            if isinstance(obj, AltiumSchImplementationList):
                current_impl = None
                continue
            if isinstance(obj, AltiumSchImplementation):
                current_impl = obj
                implementations.append(obj)
                continue
            if current_impl is None:
                continue
            if isinstance(
                obj,
                (AltiumSchMapDefinerList, AltiumSchMapDefiner, AltiumSchImplParams),
            ):
                current_impl.children.append(obj)
                if hasattr(obj, "parent"):
                    _as_dynamic(obj).parent = current_impl

        if implementation_lists:
            marker = implementation_lists[0]
            marker.children = implementations
            for implementation in implementations:
                if hasattr(implementation, "parent"):
                    _as_dynamic(implementation).parent = marker

    def _collect_implementation_groups_for_synthesis(
        self,
    ) -> tuple[
        AltiumSchImplementationList | None,
        list[tuple[AltiumSchImplementation, list[Any]]],
    ]:
        implementation_list = next(iter(self.implementation_lists), None)
        groups: list[tuple[AltiumSchImplementation, list[Any]]] = []
        current_impl: AltiumSchImplementation | None = None
        current_children: list[Any] = []

        for obj in self.objects:
            if isinstance(obj, AltiumSchImplementationList):
                continue
            if isinstance(obj, AltiumSchImplementation):
                if current_impl is not None:
                    groups.append((current_impl, current_children))
                current_impl = obj
                current_children = []
                continue
            if current_impl is not None and isinstance(
                obj,
                (AltiumSchMapDefinerList, AltiumSchMapDefiner, AltiumSchImplParams),
            ):
                current_children.append(obj)

        if current_impl is not None:
            groups.append((current_impl, current_children))

        return implementation_list, groups

    @staticmethod
    def _update_bounds_point(bounds: dict[str, float], x: float, y: float) -> None:
        bounds["min_x"] = min(bounds["min_x"], x)
        bounds["min_y"] = min(bounds["min_y"], y)
        bounds["max_x"] = max(bounds["max_x"], x)
        bounds["max_y"] = max(bounds["max_y"], y)

    def _update_graphic_bounds(self, bounds: dict[str, float], graphic: Any) -> None:
        if hasattr(graphic, "location"):
            self._update_bounds_point(bounds, graphic.location.x, graphic.location.y)
        if hasattr(graphic, "corner"):
            self._update_bounds_point(bounds, graphic.corner.x, graphic.corner.y)
        if hasattr(graphic, "vertices"):
            for vertex in graphic.vertices:
                if hasattr(vertex, "x"):
                    self._update_bounds_point(bounds, vertex.x, vertex.y)
        if hasattr(graphic, "radius") and hasattr(graphic, "location"):
            cx, cy = graphic.location.x, graphic.location.y
            rx = graphic.radius
            ry = getattr(graphic, "secondary_radius", rx)
            self._update_bounds_point(bounds, cx - rx, cy - ry)
            self._update_bounds_point(bounds, cx + rx, cy + ry)

    def _update_pin_bounds(self, bounds: dict[str, float], pin: Any) -> None:
        if not hasattr(pin, "location"):
            return

        loc = pin.location
        self._update_bounds_point(bounds, loc.x, loc.y)
        if not (hasattr(pin, "length") and hasattr(pin, "orientation")):
            return

        pin_len = pin.length
        orient = (
            pin.orientation.value
            if hasattr(pin.orientation, "value")
            else pin.orientation
        )
        if orient == 0:
            self._update_bounds_point(bounds, loc.x + pin_len, loc.y)
        elif orient == 1:
            self._update_bounds_point(bounds, loc.x, loc.y + pin_len)
        elif orient == 2:
            self._update_bounds_point(bounds, loc.x - pin_len, loc.y)
        elif orient == 3:
            self._update_bounds_point(bounds, loc.x, loc.y - pin_len)

    def _update_corner_bounds(self, bounds: dict[str, float], obj: Any) -> None:
        if hasattr(obj, "location") and hasattr(obj, "corner"):
            self._update_bounds_point(bounds, obj.location.x, obj.location.y)
            self._update_bounds_point(bounds, obj.corner.x, obj.corner.y)

    def get_bounds(
        self, part_id: int | None = None
    ) -> tuple[int, int, int, int] | None:
        """
        Calculate the bounding box for this symbol in internal units (10-mil).

        Args:
            part_id: For multi-part symbols, calculate bounds only for this part.
                     If None, calculates bounds for all parts combined.
                     Records with owner_part_id=0 or -1 are shared across all parts.

        Returns:
            Tuple of (min_x, min_y, max_x, max_y) in internal units, or None if
            the symbol has no graphics/pins with calculable bounds (e.g., image-only symbols).

        Note:
            Internal units are 10-mil (0.254mm). Multiply by 10 to get mils.
        """
        from .altium_schlib import AltiumSchLib

        bounds = {
            "min_x": float("inf"),
            "min_y": float("inf"),
            "max_x": float("-inf"),
            "max_y": float("-inf"),
        }

        for graphic in self.graphic_primitives:
            if not AltiumSchLib._record_belongs_to_part(graphic, part_id):
                continue
            self._update_graphic_bounds(bounds, graphic)

        for pin in self.pins:
            if not AltiumSchLib._record_belongs_to_part(pin, part_id):
                continue
            self._update_pin_bounds(bounds, pin)

        for img in self.images:
            if not AltiumSchLib._record_belongs_to_part(img, part_id):
                continue
            self._update_corner_bounds(bounds, img)

        for label in self.labels:
            if not AltiumSchLib._record_belongs_to_part(label, part_id):
                continue
            if hasattr(label, "location"):
                self._update_bounds_point(bounds, label.location.x, label.location.y)

        for text_frame in self.text_frames:
            if not AltiumSchLib._record_belongs_to_part(text_frame, part_id):
                continue
            self._update_corner_bounds(bounds, text_frame)

        if bounds["min_x"] == float("inf"):
            return None

        return (
            int(bounds["min_x"]),
            int(bounds["min_y"]),
            int(bounds["max_x"]),
            int(bounds["max_y"]),
        )

    def _apply_component_record(self, record: dict[str, Any]) -> None:
        self.component_record = record
        self.original_name = (
            record.get("LibReference")
            or record.get("LIBREFERENCE")
            or record.get("DesignItemId")
            or record.get("DESIGNITEMID")
            or self.original_name
        )
        self.description = (
            record.get("%UTF8%ComponentDescription")
            or record.get("%UTF8%COMPONENTDESCRIPTION")
            or record.get("ComponentDescription")
            or record.get("COMPONENTDESCRIPTION", "")
        )
        # Altium stores the part count field as actual_count + 1.
        part_count_stored = int(record.get("PartCount", record.get("PARTCOUNT", 1)))
        self.part_count = part_count_stored - 1 if part_count_stored > 1 else 1
        self.display_mode = int(record.get("DisplayMode", record.get("DISPLAYMODE", 0)))

    def _add_image_record(self, record: dict[str, Any]) -> None:
        image_obj = create_record_from_type(SchRecordType.IMAGE)
        if image_obj is None:
            return
        image_obj.parse_from_record(record)
        self.add_object(image_obj)

    def _raw_record_is_pin(self, record_index: int) -> bool:
        if record_index >= len(self.raw_records):
            return False
        owner_record = self.raw_records[record_index]
        if not owner_record.get("__BINARY_RECORD__"):
            return False
        binary_data = owner_record.get("__BINARY_DATA__")
        return bool(binary_data and len(binary_data) > 0 and binary_data[0] == 0x02)

    def _store_pin_parameter(
        self,
        owner_index: int,
        parameter: AltiumSchParameter,
    ) -> None:
        if not hasattr(self, "_pin_parameters"):
            self._pin_parameters = {}
        self._pin_parameters.setdefault(owner_index, []).append(parameter)

    def _add_parameter_record_object(self, parameter: AltiumSchParameter) -> None:
        owner_index = getattr(parameter, "_owner_index", None)
        if isinstance(owner_index, int) and owner_index > 0:
            if self._raw_record_is_pin(owner_index):
                self._store_pin_parameter(owner_index, parameter)
                return
            self.add_object(parameter)
            return
        self.add_object(parameter)

    def _add_text_record_object(self, record_obj: object) -> None:
        if isinstance(record_obj, AltiumSchParameter):
            self._add_parameter_record_object(record_obj)
            return
        if isinstance(record_obj, AltiumSchPin):
            # PINs are synchronized via sync_pins_to_raw_records(); do not track
            # them in graphics to avoid stale-index overwrite bugs.
            return
        self.add_object(record_obj)

    def _add_typed_record_object(
        self,
        record_type_int: int,
        record: dict[str, Any],
        record_index: int,
    ) -> None:
        if record_type_int == SchRecordType.COMPONENT:
            self._apply_component_record(record)
            return
        if record_type_int == SchRecordType.IMAGE:
            self._add_image_record(record)
            return

        record_obj = _create_record_object(record)
        if record_obj is None:
            return
        _as_dynamic(record_obj)._record_index = record_index
        self._add_text_record_object(record_obj)

    def _add_binary_pin_record(
        self,
        record: dict[str, Any],
        record_index: int,
        font_manager: FontIDManager | None,
    ) -> None:
        binary_data = record.get("__BINARY_DATA__")
        if not record.get("__BINARY_RECORD__") or not binary_data:
            return
        if len(binary_data) == 0 or binary_data[0] != 0x02:
            return

        pin = AltiumSchPin()
        pin.parse_from_record(record, font_manager=font_manager)
        _as_dynamic(pin)._record_index = record_index
        self.add_object(pin)

    def add_record(
        self,
        record: dict[str, Any],
        font_manager: FontIDManager | None = None,
    ) -> None:
        """
        Add a parsed record to the symbol.

                Args:
                    record: Raw record dictionary
                    font_manager: FontIDManager for PIN font handling (optional)
        """
        record_index = len(self.raw_records)
        self.raw_records.append(record)

        record_type = record.get("RECORD")
        if record_type:
            self._add_typed_record_object(int(record_type), record, record_index)

        self._add_binary_pin_record(record, record_index, font_manager)

    def sync_pins_to_raw_records(self) -> int:
        """
        Sync OOP PIN objects back to raw_records for serialization.

        This method updates the __BINARY_DATA__ and __ORIGINAL_LENGTH_BYTES__
        in raw_records from the corresponding OOP AltiumSchPin objects.
        Called before save() to ensure modifications to PIN objects are persisted.

        Returns:
            Number of PINs synced.
        """
        import struct

        synced_count = 0
        for pin in self.pins:
            record_index = getattr(pin, "_record_index", None)
            if record_index is not None and record_index < len(self.raw_records):
                raw_record = self.raw_records[record_index]
                if raw_record.get("__BINARY_RECORD__"):
                    # Serialize OOP PIN to binary and update raw_record
                    new_binary = pin._serialize_binary()
                    raw_record["__BINARY_DATA__"] = new_binary
                    # Update length bytes to match new data length
                    # Binary records use high byte 0x01 as mode indicator
                    new_length = len(new_binary) | 0x01000000
                    raw_record["__ORIGINAL_LENGTH_BYTES__"] = struct.pack(
                        "<I", new_length
                    )
                    synced_count += 1
        return synced_count

    def sync_graphics_to_raw_records(self) -> int:
        """
        Sync OOP graphical/text records back to raw_records.

        Includes `graphic_primitives`, `labels`, `designators`, and `parameters` so style
        edits on symbol text records are persisted during SchLib saves.

        Returns:
            Number of records synced.
        """
        synced_count = 0
        seen_record_indices: set[int] = set()
        for record_obj in [
            *self.graphic_primitives,
            *self.labels,
            *self.designators,
            *self.parameters,
        ]:
            # PIN objects are handled by sync_pins_to_raw_records().
            if isinstance(record_obj, AltiumSchPin):
                continue
            record_index = getattr(record_obj, "_record_index", None)
            if record_index is None or record_index >= len(self.raw_records):
                continue
            if record_index in seen_record_indices:
                continue
            raw_record = self.raw_records[record_index]
            if raw_record.get("__BINARY_RECORD__"):
                continue
            self.raw_records[record_index] = record_obj.serialize_to_record()
            seen_record_indices.add(record_index)
            synced_count += 1
        return synced_count

    def _get_record_name(self, record_type: int) -> str:
        """
        Get human-readable name for record type.
        """
        names = {
            4: "LABEL",
            5: "BEZIER",
            6: "POLYLINE",
            7: "POLYGON",
            8: "ELLIPSE",
            10: "ROUND_RECTANGLE",
            11: "ELLIPTICAL_ARC",
            12: "ARC",
            13: "LINE",
            14: "RECTANGLE",
            30: "IMAGE",
            41: "PARAMETER",
        }
        return names.get(record_type, f"UNKNOWN({record_type})")

    def get_summary(self) -> dict[str, Any]:
        """
        Get summary statistics for this symbol.
        """
        return {
            "name": self.name,
            "description": self.description,
            "part_count": self.part_count,
            "pin_count": len(self.pins),
            "graphic_count": len(self.graphic_primitives),
            "label_count": len(self.labels),
            "image_count": len(self.images),
            "parameter_count": len(self.parameters),
            "designator_count": len(self.designators),
            "text_frame_count": len(self.text_frames),
            "implementation_count": len(self.implementations),
            "total_records": len(self.raw_records),
        }

    def get_all_records(self) -> list:
        """
        Get all parsed record objects (excluding raw records and pins).

        Returns list of OOP record objects that can be serialized.
        """
        records = []
        records.extend(self.labels)
        records.extend(self.parameters)
        records.extend(self.designators)
        records.extend(self.text_frames)
        records.extend(self.graphic_primitives)
        return records

    def synthesize_raw_records(self) -> list[dict[str, str]]:
        """
        Generate raw_records from OOP objects in self.objects.

                This bridges authored symbol objects to the binary serialization path.
                When symbols are built via add_symbol() + add_object(), raw_records
                is empty. This method synthesizes the raw records needed for save().

                Returns:
                    List of raw record dicts ready for create_stream_from_records().
        """
        import struct

        records: list[dict[str, str]] = []

        # Component record must be first
        comp_record = self.component_record
        if comp_record is None:
            comp_record = {
                "RECORD": str(SchRecordType.COMPONENT),
                "LibReference": self.original_name,
                "ComponentDescription": self.description or "",
                "PartCount": str(
                    self.part_count + 1
                ),  # Altium quirk: stored as actual+1
                "CurrentPartId": "1",
                "DisplayModeCount": str(self.display_mode_count),
                "OwnerPartId": "-1",
                "LibraryPath": "*",
                "SourceLibraryName": "*",
                "TargetFileName": "*",
                "DesignItemId": self.original_name,
            }
            if self.display_mode != 0:
                comp_record["DisplayMode"] = str(self.display_mode)
        records.append(comp_record)

        # Serialize non-implementation OOP objects in insertion order.
        for obj in self.objects:
            if self._is_implementation_related_object(obj):
                continue
            if hasattr(obj, "serialize_to_record"):
                raw = obj.serialize_to_record()
                if raw:
                    # PIN records produce binary records with __BINARY_DATA__
                    if raw.get("__BINARY_RECORD__"):
                        # Ensure length bytes are set for binary records
                        if (
                            "__ORIGINAL_LENGTH_BYTES__" not in raw
                            and "__BINARY_DATA__" in raw
                        ):
                            binary_data = raw["__BINARY_DATA__"]
                            new_length = len(binary_data) | 0x01000000
                            raw["__ORIGINAL_LENGTH_BYTES__"] = struct.pack(
                                "<I", new_length
                            )
                    records.append(raw)

        implementation_list, implementation_groups = (
            self._collect_implementation_groups_for_synthesis()
        )
        marker_record = (
            implementation_list.serialize_to_record()
            if implementation_list is not None
            else {"RECORD": "44"}
        )
        records.append(clean_implementation_record_fields(marker_record))
        implementation_list_index = len(records) - 1

        for implementation, children in implementation_groups:
            implementation_record = clean_implementation_record_fields(
                implementation.serialize_to_record()
            )
            implementation_record["OwnerIndex"] = str(implementation_list_index)
            records.append(implementation_record)
            implementation_index = len(records) - 1
            for child in children:
                child_record = clean_implementation_child_record_fields(
                    child.serialize_to_record(),
                    owner_index=implementation_index,
                )
                records.append(child_record)

        return records

    def __repr__(self) -> str:
        return (
            f"AltiumSymbol('{self.name}', {len(self.pins)} pins, "
            f"{len(self.graphic_primitives)} graphics, {len(self.labels)} labels, "
            f"{len(self.images)} images)"
        )


@public_api
class AltiumSchLib(JsonApplyMixin):
    """
    Complete parser for Altium .SchLib files.
    """

    def __init__(self, filepath: Path | str | None = None, debug: bool = False) -> None:
        """
        Create an AltiumSchLib.

                Args:
                    filepath: Path to .SchLib binary file to parse.
                              If None, creates an empty library for authoring.
                    debug: Enable debug output.
        """
        self.symbols: list[AltiumSymbol] = []
        self.font_manager: FontIDManager | None = None
        self.file_header = None
        self.embedded_images = {}
        self.debug = debug

        if filepath is not None:
            self.filepath = Path(filepath)
            self.filename = self.filepath.name
            self._parse()
        else:
            self.filepath = None
            self.filename = ""

    def _ensure_font_manager(self) -> FontIDManager:
        """
        Ensure this library has a font manager for object-bound font resolution.
        """
        if self.font_manager is None:
            self.font_manager = FontIDManager.from_font_dict({})
        return self.font_manager

    def _binding_context(self) -> SchematicBindingContext:
        """
        Get the narrow schematic binding context for this library.
        """
        context = getattr(self, "_schematic_binding_context", None)
        if context is None:
            context = SchematicBindingContext(self, kind="schlib")
            self._schematic_binding_context = context
        return context

    def _bind_all_symbols_to_context(self) -> None:
        """
        Bind every symbol and object in this library to the library context.
        """
        context = self._binding_context()
        for symbol in self.symbols:
            symbol._schematic_binding_context = context
            symbol._bind_all_objects_to_context()

    @staticmethod
    def get_symbol_names(filepath: Path) -> list[str]:
        """
        Get symbol names without full parse.

        This is a lightweight method that reads the symbol OLE storage names,
        avoiding the overhead of parsing all symbol data. Storage names are used
        instead of FileHeader LibRef fields because some split libraries retain
        stale FileHeader symbol lists from their source library.

        Args:
            filepath: Path to .SchLib file

        Returns:
            List of symbol names in the library
        """
        ole = AltiumOleFile(str(filepath))
        try:
            return [
                str(entry[0])
                for entry in ole.listdir(streams=False, storages=True)
                if str(entry[0]) != "FileHeader"
            ]
        finally:
            ole.close()

    def _load_file_header(self, ole: AltiumOleFile) -> dict[str, Any]:
        """
        Load the library file header when present.
        """
        try:
            header_records = get_records_in_section(ole, "FileHeader")
            return header_records[0] if header_records else {}
        except Exception:
            return {}

    def _iter_symbol_names(self, ole: AltiumOleFile) -> list[str]:
        """
        Return OLE storage names that represent symbol entries.
        """
        return [
            str(entry[0])
            for entry in ole.listdir(streams=False, storages=True)
            if str(entry[0]) != "FileHeader"
        ]

    def _attach_pin_parameters(self, symbol: AltiumSymbol) -> None:
        """
        Attach deferred pin parameter records to their pin objects.
        """
        if not hasattr(symbol, "_pin_parameters"):
            return

        pins_by_record_index = {
            getattr(pin, "_record_index", None): pin for pin in symbol.pins
        }
        for record_idx, param_list in symbol._pin_parameters.items():
            pin = pins_by_record_index.get(record_idx)
            if pin is None:
                continue
            if not hasattr(pin, "pin_parameters"):
                pin.pin_parameters = []
            pin.pin_parameters.extend(param_list)

    def _apply_pin_functions(
        self,
        ole: AltiumOleFile,
        symbol_name: str,
        symbol: AltiumSymbol,
    ) -> None:
        """
        Apply alternate pin functions from PinFunctionData when present.
        """
        pin_functions = _parse_pinfunctiondata_from_ole(ole, symbol_name)
        if pin_functions and pin_functions["functions"] and symbol.pins:
            _as_dynamic(symbol.pins[0]).alternate_names = pin_functions["functions"]

    def _resolve_pintext_entry(
        self,
        modifier: Any,
        pin: AltiumSchPin,
        index: int,
    ) -> Any:
        """
        Resolve the best PinTextData entry for a pin across known key schemes.
        """
        ptd_keys = {designator for designator, _ in modifier.entries}
        n_entries = len(modifier.entries)
        zero_based = ptd_keys == {str(j) for j in range(n_entries)}
        one_based = (not zero_based) and ptd_keys == {
            str(j + 1) for j in range(n_entries)
        }

        if zero_based:
            return modifier.get_entry(str(index))
        if one_based:
            return modifier.get_entry(str(index + 1))

        candidate_keys: list[str] = []
        if getattr(pin, "designator", ""):
            candidate_keys.append(str(pin.designator))
        candidate_keys.append(str(index))
        candidate_keys.append(str(index + 1))

        for candidate in candidate_keys:
            pin_text_data = modifier.get_entry(candidate)
            if pin_text_data is not None:
                return pin_text_data
        return None

    def _apply_pin_position_settings(
        self,
        settings: Any,
        pin_text_position: Any,
        margin_attr: str,
        pin: AltiumSchPin,
        *,
        pin_item_mode: Any,
        pin_text_anchor: Any,
        rotation90: Any,
    ) -> None:
        """
        Apply custom PinTextData position settings to a pin text surface.
        """
        settings.position_mode = pin_item_mode.CUSTOM
        rot_value = pin_text_position.orientation // 90
        settings.rotation = rotation90(rot_value & 0x03)

        margin_mils = pin_text_position.margin_mils
        internal_coord = int(round(margin_mils * 10000))
        settings.position_margin = internal_coord // 100000
        margin_frac = internal_coord % 100000
        if margin_frac != 0:
            settings.position_margin_frac = margin_frac

        settings.rotation_anchor = (
            pin_text_anchor.COMPONENT
            if pin_text_position.reference_to_component
            else pin_text_anchor.PIN
        )
        setattr(pin, margin_attr, margin_mils)

    def _apply_pin_font_settings(self, pin: AltiumSchPin, pin_text_data: Any) -> None:
        """
        Apply font and color overrides from PinTextData.
        """
        if pin_text_data.name_font_id is not None:
            pin.name_settings.font_mode = pin.name_settings.font_mode.CUSTOM
            raw_font_id = pin_text_data.name_font_id
            pin.name_settings.font_id = (
                self.font_manager.translate_in(raw_font_id)
                if self.font_manager
                else raw_font_id
            )
        if pin_text_data.name_color is not None:
            pin.name_settings.font_mode = pin.name_settings.font_mode.CUSTOM
            pin.name_settings.color = int(pin_text_data.name_color)
        if pin_text_data.designator_font_id is not None:
            pin.designator_settings.font_mode = pin.designator_settings.font_mode.CUSTOM
            raw_font_id = pin_text_data.designator_font_id
            pin.designator_settings.font_id = (
                self.font_manager.translate_in(raw_font_id)
                if self.font_manager
                else raw_font_id
            )
        if pin_text_data.designator_color is not None:
            pin.designator_settings.font_mode = pin.designator_settings.font_mode.CUSTOM
            pin.designator_settings.color = int(pin_text_data.designator_color)

    def _apply_pintextdata(
        self, ole: AltiumOleFile, symbol_name: str, symbol: AltiumSymbol
    ) -> None:
        """
        Apply PinTextData stream settings to parsed pin objects when present.
        """
        from .altium_pintextdata_modifier import PinTextDataModifier
        from .altium_sch_enums import PinItemMode, PinTextAnchor, Rotation90

        pintextdata_path = f"{symbol_name}/PinTextData"
        if not ole.exists(pintextdata_path):
            return

        try:
            pintextdata_stream = ole.openstream(pintextdata_path)
            modifier = PinTextDataModifier()
            if not modifier.parse(pintextdata_stream):
                return

            for index, pin in enumerate(symbol.pins):
                pin_text_data = self._resolve_pintext_entry(modifier, pin, index)
                if not pin_text_data:
                    continue
                if pin_text_data.name_position:
                    self._apply_pin_position_settings(
                        pin.name_settings,
                        pin_text_data.name_position,
                        "_name_margin_mils",
                        pin,
                        pin_item_mode=PinItemMode,
                        pin_text_anchor=PinTextAnchor,
                        rotation90=Rotation90,
                    )
                if pin_text_data.designator_position:
                    self._apply_pin_position_settings(
                        pin.designator_settings,
                        pin_text_data.designator_position,
                        "_designator_margin_mils",
                        pin,
                        pin_item_mode=PinItemMode,
                        pin_text_anchor=PinTextAnchor,
                        rotation90=Rotation90,
                    )
                self._apply_pin_font_settings(pin, pin_text_data)
        except Exception as e:
            if self.debug:
                log.info(f"Warning: Could not parse PinTextData for {symbol_name}: {e}")

    def _attach_embedded_images(self, symbol: AltiumSymbol) -> None:
        """
        Match embedded image payloads onto IMAGE records.
        """
        for img in symbol.images:
            if img.filename in self.embedded_images:
                img.image_data = self.embedded_images[img.filename]
                img.detect_format()
                continue
            if not img.embedded:
                continue
            img_basename = Path(img.filename).name
            for full_path, data in self.embedded_images.items():
                if Path(full_path).name == img_basename:
                    img.image_data = data
                    img.detect_format()
                    break

    def _preserve_symbol_streams(
        self,
        ole: AltiumOleFile,
        symbol_name: str,
        symbol: AltiumSymbol,
    ) -> None:
        """
        Preserve non-Data symbol streams for round-trip saves.
        """
        for stream_path in ole.listdir(streams=True, storages=False):
            if len(stream_path) != 2 or stream_path[0] != symbol_name:
                continue
            stream_name = stream_path[1]
            if stream_name == "Data":
                continue
            full_path = f"{symbol_name}/{stream_name}"
            symbol._original_streams[stream_name] = ole.openstream(full_path)

    def _parse_symbol(self, ole: AltiumOleFile, symbol_name: str) -> AltiumSymbol:
        """
        Parse a single symbol storage into an `AltiumSymbol`.
        """
        symbol = AltiumSymbol(symbol_name)
        records = get_records_in_section(ole, f"{symbol_name}/Data")
        for record in records:
            symbol.add_record(record, self.font_manager)

        self._attach_pin_parameters(symbol)
        self._apply_pin_functions(ole, symbol_name, symbol)
        self._apply_pintextdata(ole, symbol_name, symbol)
        self._attach_embedded_images(symbol)
        self._preserve_symbol_streams(ole, symbol_name, symbol)
        symbol._rebuild_implementation_structure()
        return symbol

    def _parse(self) -> None:
        """
        Parse the SchLib file.
        """
        with AltiumOleFile(str(self.filepath)) as ole:
            self.embedded_images = parse_storage_stream(ole, debug=self.debug)
            self.font_manager = FontIDManager.load_from_ole_header(ole)
            self.file_header = self._load_file_header(ole)

            for symbol_name in self._iter_symbol_names(ole):
                try:
                    self.symbols.append(self._parse_symbol(ole, symbol_name))
                except Exception as e:
                    log.info(f"Error parsing symbol '{symbol_name}': {e}")
                    continue
            self._bind_all_symbols_to_context()

    def _count_pin_synthetic_params(self, pins: ObjectCollection) -> int:
        count = 0
        for pin in pins:
            if getattr(pin, "hidden_net_name", ""):
                count += 1
        return count

    def _get_polygon_vertex_count(self, graphic: object) -> int | None:
        if hasattr(graphic, "vertices"):
            return len(graphic.vertices)
        if isinstance(graphic, dict):
            record_type = graphic.get("RECORD")
            if record_type == str(SchRecordType.POLYGON.value):
                return sum(
                    1 for key in graphic if key.startswith("X") and key[1:].isdigit()
                )
        return None

    def _count_graphics_weight(self, graphics: ObjectCollection) -> int:
        import math

        total = 0
        for graphic in graphics:
            vertex_count = self._get_polygon_vertex_count(graphic)
            if vertex_count is None or vertex_count < 50:
                total += 1
                continue
            additional_records = math.ceil((vertex_count - 50) / 49)
            total += 1 + additional_records
        return total

    def _calculate_weight(self, minimal: bool = False) -> int:
        del minimal

        total = 1
        for symbol in self.symbols:
            total += 1
            total += 1
            total += len(symbol.pins)
            total += self._count_pin_synthetic_params(symbol.pins)
            total += self._count_graphics_weight(symbol.graphic_primitives)
            total += len(symbol.labels)
            total += len(symbol.designators)
            total += len(symbol.parameters)
            total += len(symbol.images)
            for implementation in symbol.implementations:
                total += 1
                total += 1
                total += 1
                total += len(getattr(implementation, "children", []))
        return total

    def _synthesize_file_header(self, *, minimal: bool = False) -> dict[str, str]:
        """
        Create FileHeader for SchLib builds.

                Builds a complete font table from the font_manager (if available
                from a parsed source) or defaults to Times New Roman 10pt.
        """
        import random
        import string

        unique_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        sheet_size = "18000" if minimal else "20000"
        header: dict[str, str] = {
            "HEADER": "Protel for Windows - Schematic Library Editor Binary File Version 5.0",
            "Weight": str(self._calculate_weight(minimal=minimal)),
            "MinorVersion": "9",
            "UniqueID": unique_id,
            "UseMBCS": "T",
            "IsBOC": "T",
            "SheetStyle": "9",
            "BorderOn": "T",
            "SheetNumberSpaceSize": "12",
            "AreaColor": "16317695",
            "SnapGridOn": "T",
            "SnapGridSize": "10",
            "VisibleGridOn": "T",
            "VisibleGridSize": "10",
            "CustomX": sheet_size,
            "CustomY": sheet_size,
            "UseCustomSheet": "T",
            "ReferenceZonesOn": "T",
            "Display_Unit": "0",
            "CompCount": str(len(self.symbols)),
        }

        # Build font table from font_manager or default
        if self.font_manager and self.font_manager.fonts:
            fonts = self.font_manager.fonts
            header["FontIdCount"] = str(len(fonts))
            for font_id, info in fonts.items():
                header[f"FontName{font_id}"] = info.get("name", "Times New Roman")
                header[f"Size{font_id}"] = str(info.get("size", 10))
                if info.get("bold"):
                    header[f"Bold{font_id}"] = "T"
                if info.get("italic"):
                    header[f"Italic{font_id}"] = "T"
                if info.get("underline"):
                    header[f"Underline{font_id}"] = "T"
                if info.get("strikeout"):
                    header[f"Strikeout{font_id}"] = "T"
        else:
            header["FontIdCount"] = "1"
            header["FontName1"] = "Times New Roman"
            header["Size1"] = "10"

        for i, symbol in enumerate(self.symbols):
            _set_header_text_field(
                header,
                f"LibRef{i}",
                symbol.name,
                fallback=_header_symbol_fallback_name(symbol),
            )
            header[f"PartCount{i}"] = str(symbol.part_count + 1)
            if symbol.description:
                _set_header_text_field(
                    header,
                    f"CompDescr{i}",
                    symbol.description,
                )

        return header

    def _sync_file_header_font_table(self) -> None:
        """
        Synchronize FileHeader font fields from the current FontIDManager table.
        """
        if not self.font_manager:
            return
        if not self.file_header:
            self.file_header = {
                "HEADER": "Protel for Windows - Schematic Library Editor Binary File Version 5.0",
            }

        def _is_font_field(key: str) -> bool:
            if key == "FontIdCount":
                return True
            prefixes = (
                "FontName",
                "Size",
                "Bold",
                "Italic",
                "Underline",
                "Strikeout",
                "Rotation",
            )
            for prefix in prefixes:
                if key.startswith(prefix):
                    suffix = key[len(prefix) :]
                    return suffix.isdigit()
            return False

        for key in list(self.file_header.keys()):
            if _is_font_field(key):
                self.file_header.pop(key, None)

        fonts = self.font_manager.fonts or {}
        self.file_header["FontIdCount"] = str(len(fonts))
        for font_id, font_data in sorted(fonts.items()):
            idx = str(int(font_id))
            self.file_header[f"FontName{idx}"] = str(
                font_data.get("name", "Times New Roman")
            )
            self.file_header[f"Size{idx}"] = str(int(font_data.get("size", 10)))
            if font_data.get("bold"):
                self.file_header[f"Bold{idx}"] = "T"
            if font_data.get("italic"):
                self.file_header[f"Italic{idx}"] = "T"
            if font_data.get("underline"):
                self.file_header[f"Underline{idx}"] = "T"
            if font_data.get("strikeout"):
                self.file_header[f"Strikeout{idx}"] = "T"
            rotation = int(font_data.get("rotation", 0))
            if rotation:
                self.file_header[f"Rotation{idx}"] = str(rotation)

    def _build_pintextdata_stream_for_symbol(
        self,
        symbol: AltiumSymbol,
        *,
        original_stream: bytes | None = None,
    ) -> bytes | None:
        """
        Build PinTextData stream bytes from current OOP PIN settings for one symbol.

        Returns None when no pins in this symbol require PinTextData customization.
        """
        if original_stream is None:
            self._ensure_font_manager()

            def _resolve_font_id(settings: object) -> int:
                font_id = getattr(settings, "font_id", None)
                if font_id is not None:
                    return (
                        self.font_manager.translate_out(int(font_id))
                        if self.font_manager
                        else int(font_id)
                    )

                font_name = getattr(settings, "font_name", None) or "Arial"
                font_size = getattr(settings, "font_size", None)
                resolved_font_id = (
                    self.font_manager.get_or_create_font(
                        font_name=font_name,
                        font_size=int(font_size) if font_size is not None else 10,
                        bold=bool(getattr(settings, "font_bold", False)),
                        italic=bool(getattr(settings, "font_italic", False)),
                    )
                    if self.font_manager
                    else 1
                )
                return (
                    self.font_manager.translate_out(resolved_font_id)
                    if self.font_manager
                    else resolved_font_id
                )

            return build_pintextdata_stream_for_pins(
                symbol.pins,
                resolve_font_id=_resolve_font_id,
            )

        from .altium_pintextdata_modifier import (
            PinTextData,
            PinTextDataModifier,
            PinTextPosition,
        )
        from .altium_sch_enums import PinItemMode, PinTextAnchor

        def _margin_mils(pin: AltiumSchPin, *, for_name: bool) -> float | None:
            if for_name:
                cached = getattr(pin, "_name_margin_mils", None)
                settings = pin.name_settings
            else:
                cached = getattr(pin, "_designator_margin_mils", None)
                settings = pin.designator_settings
            if cached is not None:
                return float(cached)
            if settings.position_margin is None:
                return None
            return float(
                settings.position_margin * 10
                + (settings.position_margin_frac or 0) / 10000.0
            )

        entries: list[tuple[str, PinTextData]] = []
        for pin_index, pin in enumerate(symbol.pins):
            name_settings = pin.name_settings
            des_settings = pin.designator_settings

            name_position: PinTextPosition | None = None
            if name_settings.position_mode == PinItemMode.CUSTOM:
                margin_mils = _margin_mils(pin, for_name=True) or 0.0
                name_position = PinTextPosition(
                    margin_mils=margin_mils,
                    orientation=int(name_settings.rotation.value) * 90,
                    reference_to_component=(
                        name_settings.rotation_anchor == PinTextAnchor.COMPONENT
                    ),
                )

            designator_position: PinTextPosition | None = None
            if des_settings.position_mode == PinItemMode.CUSTOM:
                margin_mils = _margin_mils(pin, for_name=False) or 0.0
                designator_position = PinTextPosition(
                    margin_mils=margin_mils,
                    orientation=int(des_settings.rotation.value) * 90,
                    reference_to_component=(
                        des_settings.rotation_anchor == PinTextAnchor.COMPONENT
                    ),
                )

            name_font_id: int | None = None
            name_color: int | None = None
            if name_settings.font_mode == PinItemMode.CUSTOM:
                raw_font_id = int(name_settings.font_id or 1)
                name_font_id = (
                    self.font_manager.translate_out(raw_font_id)
                    if self.font_manager
                    else raw_font_id
                )
                name_color = (
                    int(name_settings.color)
                    if name_settings.color is not None
                    else int(getattr(pin, "color", 0))
                )

            designator_font_id: int | None = None
            designator_color: int | None = None
            if des_settings.font_mode == PinItemMode.CUSTOM:
                raw_font_id = int(des_settings.font_id or 1)
                designator_font_id = (
                    self.font_manager.translate_out(raw_font_id)
                    if self.font_manager
                    else raw_font_id
                )
                designator_color = (
                    int(des_settings.color)
                    if des_settings.color is not None
                    else int(getattr(pin, "color", 0))
                )

            has_custom_data = (
                name_position is not None
                or designator_position is not None
                or name_font_id is not None
                or designator_font_id is not None
                or name_color is not None
                or designator_color is not None
            )
            if not has_custom_data:
                continue

            pin_data = PinTextData(
                format_type="AUTO",
                raw_data=bytearray(),
                name_font_id=name_font_id,
                name_color=name_color,
                designator_font_id=designator_font_id,
                designator_color=designator_color,
                position=name_position,
                name_position=name_position,
                designator_position=designator_position,
            )
            entries.append((str(pin_index), pin_data))

        if not entries:
            return None

        modifier = PinTextDataModifier()
        modifier.entries = entries
        return modifier.serialize(original_data=original_stream)

    def _build_pinfrac_stream_for_symbol(self, symbol: AltiumSymbol) -> bytes | None:
        """
        Build PinFrac stream bytes from current OOP PIN settings for one symbol.
        """
        return build_pinfrac_stream_for_pins(symbol.pins)

    def _copy_original_ole_structure(
        self,
        ole_writer: AltiumOleWriter,
        *,
        sync_pin_text_data: bool,
    ) -> dict[str, bytes]:
        original_pintextdata_streams: dict[str, bytes] = {}
        if not self.filepath or not Path(self.filepath).exists():
            return original_pintextdata_streams

        with AltiumOleFile(str(self.filepath)) as ole:
            ole_writer.fromOleFile(ole)
            if not sync_pin_text_data:
                return original_pintextdata_streams
            for symbol in self.symbols:
                pintext_path = f"{symbol.name}/PinTextData"
                if ole.exists(pintext_path):
                    original_pintextdata_streams[symbol.name] = ole.openstream(
                        pintext_path
                    )
        return original_pintextdata_streams

    def _records_for_symbol_save(
        self,
        symbol: AltiumSymbol,
        *,
        debug: bool,
    ) -> tuple[list[dict[str, Any]], bool]:
        is_oop_built_symbol = bool(symbol.objects) and not bool(symbol.raw_records)
        if symbol.raw_records:
            pins_synced = symbol.sync_pins_to_raw_records()
            graphics_synced = symbol.sync_graphics_to_raw_records()
            if debug and pins_synced > 0:
                log.info(f"  Synced {pins_synced} PINs for {symbol.name}")
            if debug and graphics_synced > 0:
                log.info(f"  Synced {graphics_synced} graphics for {symbol.name}")
            return symbol.raw_records, is_oop_built_symbol

        records_to_write = symbol.synthesize_raw_records()
        if debug:
            source = "OOP objects" if symbol.objects else "empty symbol"
            log.info(
                f"  Synthesized {len(records_to_write)} records for {symbol.name} from {source}"
            )
        return records_to_write, is_oop_built_symbol

    def _write_pintextdata_stream(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        pintextdata: bytes | None,
        *,
        debug: bool,
        action: str,
    ) -> None:
        if pintextdata is None:
            return
        pintext_path = f"{symbol.name}/PinTextData"
        ole_writer.editEntry(pintext_path, data=pintextdata)
        if debug:
            log.info(
                f"  {action} PinTextData for {symbol.name}: {len(pintextdata)} bytes"
            )

    def _write_oop_symbol_aux_streams(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        *,
        debug: bool,
    ) -> None:
        self._write_pintextdata_stream(
            ole_writer,
            symbol,
            self._build_pintextdata_stream_for_symbol(symbol),
            debug=debug,
            action="Wrote",
        )

        pinfrac = self._build_pinfrac_stream_for_symbol(symbol)
        if pinfrac is None:
            return
        pinfrac_path = f"{symbol.name}/PinFrac"
        ole_writer.editEntry(pinfrac_path, data=pinfrac)
        if debug:
            log.info(f"  Wrote PinFrac for {symbol.name}: {len(pinfrac)} bytes")

    def _write_synced_pintextdata_stream(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        original_pintextdata_streams: dict[str, bytes],
        *,
        debug: bool,
    ) -> None:
        pintextdata = self._build_pintextdata_stream_for_symbol(
            symbol,
            original_stream=original_pintextdata_streams.get(symbol.name),
        )
        self._write_pintextdata_stream(
            ole_writer,
            symbol,
            pintextdata,
            debug=debug,
            action="Synced",
        )

    @staticmethod
    def _write_preserved_symbol_streams(
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        *,
        skip_pintextdata: bool,
    ) -> None:
        for stream_name, stream_data in symbol._original_streams.items():
            if stream_name == "PinTextData" and skip_pintextdata:
                continue
            stream_path = f"{symbol.name}/{stream_name}"
            ole_writer.editEntry(stream_path, data=stream_data)

    def _write_symbol_to_ole(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        original_pintextdata_streams: dict[str, bytes],
        *,
        debug: bool,
        sync_pin_text_data: bool,
    ) -> None:
        stream_path = f"{symbol.name}/Data"
        records_to_write, is_oop_built_symbol = self._records_for_symbol_save(
            symbol,
            debug=debug,
        )
        serialized = create_stream_from_records(records_to_write)
        ole_writer.editEntry(stream_path, data=serialized)
        if debug:
            log.info(
                f"  Serialized {symbol.name}: {len(records_to_write)} records -> {len(serialized)} bytes"
            )

        if is_oop_built_symbol:
            self._write_oop_symbol_aux_streams(ole_writer, symbol, debug=debug)
        elif sync_pin_text_data:
            self._write_synced_pintextdata_stream(
                ole_writer,
                symbol,
                original_pintextdata_streams,
                debug=debug,
            )

        self._write_preserved_symbol_streams(
            ole_writer,
            symbol,
            skip_pintextdata=sync_pin_text_data,
        )

    def _write_file_header_to_ole(
        self,
        ole_writer: AltiumOleWriter,
        *,
        sync_pin_text_data: bool,
        minimal: bool,
    ) -> None:
        file_header = self.file_header or self._synthesize_file_header(minimal=minimal)
        if not file_header:
            return
        if sync_pin_text_data:
            self._sync_file_header_font_table()
        serialized = create_stream_from_records([file_header])
        ole_writer.editEntry("FileHeader", data=serialized)

    def _write_embedded_images_to_ole(self, ole_writer: AltiumOleWriter) -> None:
        if self.embedded_images:
            storage_data = create_storage_stream(self.embedded_images)
            ole_writer.editEntry("Storage", data=storage_data)

    def to_schlib(
        self,
        filepath: Path,
        debug: bool = False,
        *,
        sync_pin_text_data: bool = False,
        minimal: bool = False,
    ) -> None:
        """
        Serialize SchLib back to file.

        This is the primary API method for saving SchLib files.
        Use this method for all file writing operations.

        IMPORTANT: This method ALWAYS creates a new OLE file from scratch,
        using AltiumOleWriter. It does NOT patch the original file in-place.
        This allows handling stream size changes (e.g., adding/removing records).

        Args:
            filepath: Output path for .SchLib file
            debug: Enable debug output
            sync_pin_text_data: Regenerate each symbol's PinTextData stream from
                OOP pin settings and synchronize FileHeader font table.
        """
        filepath = Path(filepath)
        log.info(f"Saving SchLib to: {filepath}")

        try:
            self._ensure_font_manager()
            self._bind_all_symbols_to_context()
            ole_writer = AltiumOleWriter()
            original_pintextdata_streams = self._copy_original_ole_structure(
                ole_writer,
                sync_pin_text_data=sync_pin_text_data,
            )

            for symbol in self.symbols:
                self._write_symbol_to_ole(
                    ole_writer,
                    symbol,
                    original_pintextdata_streams,
                    debug=debug,
                    sync_pin_text_data=sync_pin_text_data,
                )

            self._write_file_header_to_ole(
                ole_writer,
                sync_pin_text_data=sync_pin_text_data,
                minimal=minimal,
            )
            self._write_embedded_images_to_ole(ole_writer)
            ole_writer.write(str(filepath))

            log.info(f"  Saved successfully: {len(self.symbols)} symbols")

        except Exception as e:
            log.error(f"  Error saving {filepath.name}: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            raise

    def save(
        self,
        filepath: Path | str,
        debug: bool = False,
        *,
        sync_pin_text_data: bool = False,
        minimal: bool = False,
    ) -> None:
        """
        Save to binary SchLib format.

        This is the canonical public write path. Prefer `save()` over
        format-specific helpers such as `to_schlib()`.

                Args:
                    filepath: Output file path.
                    debug: Enable debug output.
                    sync_pin_text_data: Rebuild PinTextData streams from OOP pin objects.
        """
        self.to_schlib(
            filepath=Path(filepath),
            debug=debug,
            sync_pin_text_data=sync_pin_text_data,
            minimal=minimal,
        )

    def add_symbol(
        self,
        name: str,
        description: str = "",
        *,
        original_name: str | None = None,
    ) -> AltiumSymbol:
        """
        Add a new empty symbol to this library.

                Args:
                    name: Symbol name (must be unique within the library).
                    description: Optional component description.

                Returns:
                    The new AltiumSymbol, ready for adding objects.
        """
        symbol = AltiumSymbol(name, original_name=original_name)
        symbol.description = description
        symbol._bind_to_schematic_library(self)
        self.symbols.append(symbol)
        return symbol

    def get_symbol(self, name: str) -> AltiumSymbol | None:
        """
        Get a symbol by name.
        """
        for symbol in self.symbols:
            if symbol.name == name:
                return symbol
        return None

    def get_summary(self) -> dict[str, object]:
        """
        Get summary statistics for the entire library.
        """
        return {
            "filename": self.filename,
            "filepath": str(self.filepath),
            "symbol_count": len(self.symbols),
            "font_count": len(self.font_manager.fonts) if self.font_manager else 0,
            "symbols": [s.get_summary() for s in self.symbols],
        }

    def split(
        self,
        output_dir: Path,
        name_pattern: str = "{symbol_name}.SchLib",
        symbol_filter: list[str] | None = None,
        verbose: bool = True,
    ) -> dict[str, Path]:
        """
        Split this multi-symbol SchLib into individual files.

                Copies symbols with their objects and preserves font tables
                and embedded images via save().

                Args:
                    output_dir: Directory to write individual SchLib files.
                    name_pattern: Filename pattern with {symbol_name} placeholder.
                    symbol_filter: Optional list of symbol names to extract (None = all).
                    verbose: Print progress messages.

                Returns:
                    Dict mapping symbol names to output file paths.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if verbose:
            log.info(f"Splitting: {self.filename}")

        symbols_to_process = self.symbols
        if symbol_filter is not None:
            symbols_to_process = [s for s in self.symbols if s.name in symbol_filter]

        results: dict[str, Path | None] = {}

        for symbol in symbols_to_process:
            try:
                single = AltiumSchLib()
                single.font_manager = self.font_manager

                new_sym = single.add_symbol(
                    symbol.name,
                    symbol.description,
                    original_name=symbol.original_name,
                )
                new_sym.part_count = symbol.part_count
                new_sym.component_record = symbol.component_record
                for obj in symbol.objects:
                    new_sym.objects.append(obj)
                new_sym.raw_records = symbol.raw_records
                new_sym._original_streams = dict(symbol._original_streams)

                # Copy embedded images referenced by this symbol
                for img in symbol.images:
                    filename = getattr(img, "filename", None)
                    if filename and filename in self.embedded_images:
                        single.embedded_images[filename] = self.embedded_images[
                            filename
                        ]

                output_filename = name_pattern.format(symbol_name=symbol.name)
                output_path = output_dir / output_filename
                single.save(output_path, sync_pin_text_data=True)

                if verbose:
                    log.info(f"  Split: {symbol.name} -> {output_filename}")

                results[symbol.name] = output_path

            except Exception as e:
                log.error(f"Failed to split {symbol.name}: {e}")
                results[symbol.name] = None

        if verbose:
            successes = sum(1 for v in results.values() if v is not None)
            log.info(f"Split complete: {successes}/{len(results)} symbols")

        return results

    @classmethod
    def merge(
        cls,
        input_paths: Path | list[Path],
        output_path: Path,
        handle_conflicts: str = "rename",
        verbose: bool = True,
    ) -> AltiumSchLib:
        """
        Merge multiple SchLib files into a single multi-symbol SchLib file.

        This is the primary high-level interface for merging SchLib files.
        It combines multiple individual SchLib files into one file while handling
        font table merging, image deduplication, and symbol name conflicts.

        This performs a vanilla merge that preserves all fonts, graphics, and
        formatting exactly as they appear in the input files.

        Args:
            input_paths: Either:
                - Path to a directory containing SchLib files (*.SchLib, *.Schlib)
                - List of Path objects pointing to individual SchLib files
            output_path: Destination path for the merged SchLib file
            handle_conflicts: How to handle duplicate symbol names:
                - "rename": Append _1, _2, etc. to duplicate names (default)
                - "skip": Skip duplicate symbol names
                - "error": Raise error on conflicts
            verbose: Print progress messages

        Returns:
            AltiumSchLib instance of the merged file

        Raises:
            ValueError: If input_paths is invalid or merge fails
        """
        from .altium_schlib_merger import merge_directory, merge_schlibs

        # Handle input_paths
        if isinstance(input_paths, Path):
            # Directory path - use merge_directory
            if input_paths.is_dir():
                success = merge_directory(
                    input_paths,
                    output_path,
                    pattern="*.SchLib",
                    handle_conflicts=handle_conflicts,
                    verbose=verbose,
                )
            else:
                raise ValueError(f"Path is not a directory: {input_paths}")
        elif isinstance(input_paths, list):
            # List of files - use merge_schlibs
            if not input_paths:
                raise ValueError("input_paths list is empty")

            # Convert to Path objects if needed
            file_paths = [
                Path(p) if not isinstance(p, Path) else p for p in input_paths
            ]

            # Verify all files exist
            for p in file_paths:
                if not p.exists():
                    raise ValueError(f"File not found: {p}")

            success = merge_schlibs(
                file_paths,
                output_path,
                handle_conflicts=handle_conflicts,
                verbose=verbose,
            )
        else:
            raise ValueError(
                f"input_paths must be Path (directory) or list[Path] (files), "
                f"got {type(input_paths)}"
            )

        if not success:
            raise ValueError("Merge operation failed")

        # Parse and return the merged file
        return cls(output_path, debug=False)

    def _find_symbol(self, symbol_name: str) -> AltiumSymbol:
        """
        Return a symbol by name or raise ValueError.
        """
        for symbol in self.symbols:
            if symbol.name == symbol_name:
                return symbol
        raise ValueError(f"Symbol '{symbol_name}' not found in library")

    @staticmethod
    def _record_belongs_to_part(record: Any, part_id: int | None) -> bool:
        """
        Return whether a symbol child record belongs to the selected part.
        """
        if part_id is None:
            return True
        if not hasattr(record, "owner_part_id"):
            return True
        record_part = record.owner_part_id
        if record_part is None or record_part == 0 or record_part == -1:
            return True
        return record_part == part_id

    @staticmethod
    def _build_symbol_viewport(
        symbol: AltiumSymbol,
        *,
        width: int | None,
        height: int | None,
        padding: int,
        auto_fit: bool,
        part_id: int | None,
    ) -> tuple[int, int, float, float]:
        """
        Return (width, height, offset_x, offset_y) for symbol rendering.
        """
        bounds = symbol.get_bounds(part_id=part_id)
        if bounds is None:
            min_x, min_y, max_x, max_y = -10, -10, 10, 10
        else:
            min_x, min_y, max_x, max_y = bounds

        symbol_width = max_x - min_x
        symbol_height = max_y - min_y
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0

        if auto_fit:
            render_width = max(int(symbol_width + 2 * padding), 100)
            render_height = max(int(symbol_height + 2 * padding), 100)
        else:
            render_width = width if width is not None else 800
            render_height = height if height is not None else 600

        offset_x = render_width / 2.0 - center_x
        offset_y = center_y - render_height / 2.0
        return (render_width, render_height, offset_x, offset_y)

    def _append_symbol_geometry_records(
        self,
        records: list[Any],
        objects: Any,
        ctx: Any,
        *,
        document_id: str,
        part_id: int | None,
        should_skip: Any | None = None,
    ) -> None:
        """
        Append symbol child records that expose a callable to_geometry surface.
        """
        for obj in objects:
            if not self._record_belongs_to_part(obj, part_id):
                continue
            if should_skip is not None and should_skip(obj):
                continue
            to_geometry = getattr(obj, "to_geometry", None)
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                ctx,
                document_id=document_id,
                units_per_px=64,
            )
            if geometry_record is not None:
                records.append(geometry_record)

    def _append_symbol_image_geometry_records(
        self,
        records: list[Any],
        symbol: AltiumSymbol,
        ctx: Any,
        *,
        document_id: str,
        part_id: int | None,
    ) -> dict[str, str]:
        """
        Append image geometry records and return runtime image hrefs.
        """
        import base64

        from .altium_record_types import color_to_hex

        runtime_image_hrefs: dict[str, str] = {}
        for image in symbol.images:
            if not self._record_belongs_to_part(image, part_id):
                continue
            geometry_record = image.to_geometry(
                ctx,
                document_id=document_id,
                units_per_px=64,
            )
            if geometry_record is not None:
                records.append(geometry_record)
            if not getattr(image, "image_data", None):
                continue
            background_color = None
            alpha_tolerance = 5
            if ctx.options.image_background_to_alpha:
                background_color = color_to_hex(
                    int(getattr(ctx, "sheet_area_color", 0xFFFFFF) or 0xFFFFFF)
                )
                alpha_tolerance = ctx.options.image_alpha_tolerance
            png_data = image._convert_to_png(
                background_color=background_color,
                alpha_tolerance=alpha_tolerance,
                document_path=str(self.filepath) if self.filepath else None,
            )
            if not png_data:
                continue
            runtime_image_hrefs[str(getattr(image, "unique_id", "") or "")] = (
                "data:image/png;base64," + base64.b64encode(png_data).decode("ascii")
            )
        return runtime_image_hrefs

    def symbol_to_ir(
        self,
        symbol_name: str,
        width: int | None = None,
        height: int | None = None,
        padding: int = 50,
        background: str = "#FFFFFF",
        auto_fit: bool = True,
        part_id: int | None = None,
        *,
        profile: str = "onscreen",
        render_options: SchSvgRenderOptions | None = None,
    ) -> SchGeometryDocument:
        """
        Build a symbol-scoped IR document for a standalone SchLib symbol render.

        The symbol renderer now follows the same IR -> SVG pipeline as SchDoc.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryDocument,
            normalize_sch_ir_render_profile,
        )
        from .altium_sch_svg_renderer import SchSvgRenderContext, SchSvgRenderOptions

        symbol = self._find_symbol(symbol_name)
        render_width, render_height, offset_x, offset_y = self._build_symbol_viewport(
            symbol,
            width=width,
            height=height,
            padding=padding,
            auto_fit=auto_fit,
            part_id=part_id,
        )

        if render_options is None:
            resolved_profile = normalize_sch_ir_render_profile(profile)
            render_options = (
                SchSvgRenderOptions.onscreen()
                if resolved_profile.value == "onscreen"
                else SchSvgRenderOptions.native_altium()
            )

        ctx = SchSvgRenderContext(
            scale=1.0,
            stroke_scale=1.0,
            offset_x=offset_x,
            offset_y=offset_y,
            flip_y=True,
            sheet_height=render_height,
            sheet_width=render_width,
            show_pins=True,
            show_pin_names=True,
            show_pin_numbers=True,
            font_manager=self.font_manager,
            background_color=background,
            document_path=str(self.filepath) if self.filepath else None,
            schlib_mode=True,
            options=render_options,
            sheet_area_color=0xFFFFFF,
        )

        part_suffix = f"-part{part_id}" if part_id is not None else ""
        doc_unique_id = f"symbol-{symbol.name}{part_suffix}"
        records = []

        self._append_symbol_geometry_records(
            records,
            symbol.graphic_primitives,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            should_skip=lambda graphic: (
                "Designator" in type(graphic).__name__
                or "Parameter" in type(graphic).__name__
                or "Pin" in type(graphic).__name__
            ),
        )
        self._append_symbol_geometry_records(
            records,
            symbol.pins,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
        )
        runtime_image_hrefs = self._append_symbol_image_geometry_records(
            records,
            symbol,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
        )
        self._append_symbol_geometry_records(
            records,
            symbol.labels,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
        )
        self._append_symbol_geometry_records(
            records,
            symbol.text_frames,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
        )

        document = SchGeometryDocument(
            records=records,
            source_path=str(self.filepath) if self.filepath else None,
            source_kind="SCHLIB",
            include_kinds=["all"],
            coordinate_space={
                "kind": "screen_px_fixed",
                "units_per_px": 64,
                "y_axis_down": True,
            },
            canvas={
                "width_px": render_width,
                "height_px": render_height,
            },
            document_id=doc_unique_id,
            workspace_background_color=background,
            render_hints={
                "ir_profile": normalize_sch_ir_render_profile(profile).value,
                "schlib_mode": True,
            },
            extras={
                "symbol_name": symbol.name,
                "part_id": part_id,
                "background_color": background,
            },
        )
        object.__setattr__(document, "_runtime_image_hrefs", runtime_image_hrefs)
        return document

    def symbol_to_svg(
        self,
        symbol_name: str,
        width: int | None = None,
        height: int | None = None,
        padding: int = 50,
        background: str = "#FFFFFF",
        auto_fit: bool = True,
        part_id: int | None = None,
    ) -> str:
        """
        Render a single symbol from this library as a standalone SVG.

        Uses the same IR -> SVG rendering path as SchDoc for consistency.

        Args:
            symbol_name: Name of the symbol to render
            width: SVG width in pixels. If None and auto_fit=True, computed from bounds.
            height: SVG height in pixels. If None and auto_fit=True, computed from bounds.
            padding: Padding around symbol in pixels (used when auto_fit=True)
            background: Background color
            auto_fit: If True, derive viewport size from symbol bounding box + padding.
                      If False, use width/height (defaults to 800x600 if not specified).
            part_id: For multipart symbols, render only graphics/pins belonging to this part.
                     If None, renders all parts (original behavior). Part IDs start at 1.

        Returns:
            Complete SVG document as string

        Raises:
            ValueError: If symbol_name not found in library
        """
        from .altium_sch_geometry_renderer import (
            SchGeometrySvgRenderer,
            SchGeometrySvgRenderOptions,
        )

        ir_document = self.symbol_to_ir(
            symbol_name,
            width=width,
            height=height,
            padding=padding,
            background=background,
            auto_fit=auto_fit,
            part_id=part_id,
            profile="onscreen",
        )
        return SchGeometrySvgRenderer(
            SchGeometrySvgRenderOptions(
                include_workspace_background=True,
                workspace_background_color=background,
                text_mode="onscreen",
            )
        ).render(ir_document)

    def to_svg(
        self,
        output_dir: Path | None = None,
        width: int = 800,
        height: int = 600,
        padding: int = 50,
        background: str = "#FFFFFF",
    ) -> dict[str, dict[int, str]]:
        """
        Render all symbols in the library to SVG.

        Each symbol is rendered as a standalone SVG document.
        For multipart symbols (part_count > 1), generates a separate SVG for each part.

        Args:
            output_dir: If provided, write SVG files to this directory.
                        Naming convention:
                        - Single-part: "{symbol_name}.svg"
                        - Multipart: "{symbol_name}_part{n}.svg"
            width: SVG width in pixels
            height: SVG height in pixels
            padding: Padding around symbol in pixels
            background: Background color

        Returns:
            Nested dict: {symbol_name: {part_id: svg_content}}
            - symbol_name: Name of the symbol
            - part_id: Part number (1-based), always present even for single-part
            - svg_content: SVG document as string

            Example structure:
            {
                "LED": {1: "<svg>...</svg>"},
                "MCXA156VMP": {1: "<svg>...</svg>", 2: "<svg>...</svg>", ...}
            }
        """
        from pathlib import Path

        results: dict[str, dict[int, str]] = {}

        for symbol in self.symbols:
            # Check if multipart symbol
            part_count = getattr(symbol, "part_count", 1) or 1
            results[symbol.name] = {}

            for part_id in range(1, part_count + 1):
                try:
                    svg = self.symbol_to_svg(
                        symbol.name,
                        width=width,
                        height=height,
                        padding=padding,
                        background=background,
                        part_id=part_id if part_count > 1 else None,
                    )
                    results[symbol.name][part_id] = svg

                    # Write to file if output_dir provided
                    if output_dir is not None:
                        output_dir = Path(output_dir)
                        output_dir.mkdir(parents=True, exist_ok=True)
                        # Naming: single-part = "SYMBOL_ir.svg", multipart = "SYMBOL_part1_ir.svg"
                        if part_count > 1:
                            filename = f"{symbol.name}_part{part_id}_ir.svg"
                        else:
                            filename = f"{symbol.name}_ir.svg"
                        output_path = output_dir / filename
                        output_path.write_text(svg, encoding="utf-8")

                except Exception as e:
                    log.warning(
                        f"Failed to render symbol '{symbol.name}' part {part_id}: {e}"
                    )
                    continue

        return results

    def to_json(self, filepath: Path | str | None = None) -> dict:
        """
        Export SchLib to JSON format for interoperability testing.

        Creates a JSON structure compatible with native AltiumInterop.JsonTests.
        Format:
            {
                "Header": {...},
                "Symbols": [
                    {
                        "Name": "symbol_name",
                        "Description": "...",
                        "Objects": [...]  # All records as JSON
                    },
                ]
            }

        Args:
            filepath: Optional path to write JSON file.
                     If provided, writes to file and returns dict.
                     If None, just returns the dict.

        Returns:
            JSON-serializable dict of the library structure
        """
        import base64
        import json
        import zlib
        from pathlib import Path

        def record_to_json_object(
            record: dict[str, object], object_index: int
        ) -> dict[str, object] | None:
            """
            Convert a single raw record dict to JSON format.
            """
            # Handle binary records (like PIN)
            if record.get("__BINARY_RECORD__"):
                binary_data = record.get("__BINARY_DATA__", b"")
                if binary_data and len(binary_data) > 0:
                    record_type = binary_data[0]
                    object_type = sch_json_object_type_from_record(
                        {
                            "__BINARY_RECORD__": True,
                            "__BINARY_DATA__": binary_data,
                        }
                    )
                    if object_type is None:
                        object_type_name = f"Unknown_{record_type}"
                    else:
                        object_type_name = object_type.value
                    # Compress and base64 encode binary data
                    compressed = zlib.compress(binary_data)
                    encoded = base64.b64encode(compressed).decode("ascii")
                    return {
                        "ObjectType": object_type_name,
                        "ObjectIndex": object_index,
                        "BinaryData": encoded,
                        "_binary_record_type": record_type,
                    }
                return None

            # Normal text record
            record_num = record.get("RECORD")
            if record_num is None:
                return None

            object_type = sch_json_object_type_from_record(record)
            if object_type is None:
                record_type_int = int(record_num)
                object_type_name = f"Unknown_{record_type_int}"
            else:
                object_type_name = object_type.value

            json_obj = {
                "ObjectType": object_type_name,
                "ObjectIndex": object_index,
            }

            # Convert record fields to JSON
            for key, value in record.items():
                if key == "RECORD":
                    continue

                # Convert boolean strings to booleans
                if value == "T":
                    json_obj[key] = True
                elif value == "F":
                    json_obj[key] = False
                # Try to convert numeric strings to numbers
                elif isinstance(value, str):
                    try:
                        # Check if it's an integer
                        if value.lstrip("-").isdigit():
                            json_obj[key] = int(value)
                        else:
                            # Try float
                            json_obj[key] = float(value)
                    except ValueError:
                        json_obj[key] = value
                else:
                    json_obj[key] = value

            return json_obj

        result = {
            "Header": {
                "Filename": self.filename,
                "SymbolCount": len(self.symbols),
                "FontCount": len(self.font_manager.fonts) if self.font_manager else 0,
            },
            "Symbols": [],
        }

        # Add font table if present
        if self.font_manager and self.font_manager.fonts:
            fonts_list = []
            for font_id, font_info in sorted(self.font_manager.fonts.items()):
                font_entry = {
                    "FontID": font_id,
                    "FontName": font_info["name"],
                    "FontSize": font_info["size"],
                }
                # Only include optional attributes if not default
                if font_info.get("bold"):
                    font_entry["Bold"] = True
                if font_info.get("italic"):
                    font_entry["Italic"] = True
                if font_info.get("underline"):
                    font_entry["Underline"] = True
                if font_info.get("strikeout"):
                    font_entry["Strikeout"] = True
                if font_info.get("rotation"):
                    font_entry["Rotation"] = font_info["rotation"]
                fonts_list.append(font_entry)
            result["Header"]["Fonts"] = fonts_list

        # Export each symbol
        for symbol in self.symbols:
            symbol_json = {
                "Name": symbol.name,
                "Description": symbol.description,
                "PartCount": symbol.part_count,
                "Objects": [],
            }

            # Export all raw records
            for i, record in enumerate(symbol.raw_records):
                json_obj = record_to_json_object(record, i)
                if json_obj:
                    symbol_json["Objects"].append(json_obj)

            result["Symbols"].append(symbol_json)

        # Write to file if path provided
        if filepath is not None:
            filepath = Path(filepath)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            log.info(f"Exported SchLib to JSON: {filepath}")

        return result

    @classmethod
    def from_json(
        cls,
        source: Path | str | dict,
    ) -> AltiumSchLib:
        """
        Create a new SchLib from JSON data.

                This is the public alternate-format ingest path. The returned library
                is created in empty authoring mode and then populated from the JSON payload.
                To mutate an existing binary-backed library or template, instantiate
                ``AltiumSchLib`` first and then call ``apply_json()``.

                Args:
                    source: JSON file path (Path or str) or parsed dict.

                Returns:
                    AltiumSchLib instance with data loaded from JSON.

                Raises:
                    ValueError: If JSON format is invalid.
        """
        instance = cls()
        instance.apply_json(source)
        return instance

    @staticmethod
    def _load_json_source(source: Path | str | dict) -> dict:
        """
        Load and validate a SchLib JSON payload.
        """
        import json as json_mod

        if isinstance(source, dict):
            data = source
        else:
            source_path = Path(source)
            with open(source_path, encoding="utf-8") as f:
                data = json_mod.load(f)

        if "Symbols" not in data:
            raise ValueError("Invalid JSON format: missing 'Symbols' key")

        return data

    def _update_from_json(self, data: dict) -> None:
        """
        Update this SchLib's data from a JSON dict.

        Internal object-population step used by ``from_json()`` and
        ``apply_json()`` after source loading and validation.
        """
        import base64
        import zlib

        # Map existing symbols by name for template-based update
        symbol_map = {s.name: s for s in self.symbols}

        for sym_json in data.get("Symbols", []):
            name = sym_json.get("Name")

            if name in symbol_map:
                # Template mode: update existing symbol
                symbol = symbol_map[name]
            else:
                # From-scratch mode: create new symbol
                symbol = AltiumSymbol(name)
                self.symbols.append(symbol)

            # Update basic metadata
            symbol.description = sym_json.get("Description", symbol.description)
            symbol.part_count = sym_json.get("PartCount", symbol.part_count)

            # Update raw_records from JSON objects
            json_objects = sym_json.get("Objects", [])
            if symbol.raw_records and len(json_objects) != len(symbol.raw_records):
                log.warning(
                    f"Symbol '{name}': Object count mismatch "
                    f"(JSON: {len(json_objects)}, template: {len(symbol.raw_records)})"
                )
                continue

            for i, (json_obj, raw_record) in enumerate(
                zip(json_objects, symbol.raw_records, strict=False)
            ):
                # Update raw record from JSON object
                if json_obj.get("BinaryData"):
                    # Binary record - decode and update
                    try:
                        encoded = json_obj["BinaryData"]
                        compressed = base64.b64decode(encoded)
                        binary_data = zlib.decompress(compressed)
                        raw_record["__BINARY_DATA__"] = binary_data
                    except Exception as e:
                        log.warning(f"Failed to decode binary data for record {i}: {e}")
                else:
                    # Text record - update fields
                    for key, value in json_obj.items():
                        if key in ("ObjectType", "ObjectIndex"):
                            continue
                        # Convert JSON types back to record format
                        if isinstance(value, bool):
                            raw_record[key] = "T" if value else "F"
                        else:
                            raw_record[key] = str(value)

    def __repr__(self) -> str:
        return f"AltiumSchLib('{self.filename}', {len(self.symbols)} symbols)"
