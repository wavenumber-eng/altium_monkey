"""
Canonical schematic JSON ObjectType contract.

This module centralizes the JSON object-type strings used by schematic
`to_json()` / `from_json()` flows. The canonical export names are aligned with
the native interop contract used by the private oracle suite.

Supplemental entries like `FileHeader`, `Map Definer List`, and `Impl Params`
are included here because the Python JSON surface uses them even though they are
not backed by native schematic object identifiers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .altium_record_types import SchRecordType


class SchJsonObjectType(StrEnum):
    # Native object-id-backed names
    LINE = "Line"
    RECTANGLE = "Rectangle"
    ROUND_RECTANGLE = "RoundRectangle"
    ELLIPSE = "Ellipse"
    ARC = "Arc"
    ELLIPTICAL_ARC = "EllipticalArc"
    POLYGON = "Polygon"
    POLYLINE = "Polyline"
    BEZIER = "Bezier"
    PIE_CHART = "Pie Chart"
    COMPONENT = "Component"
    PIN = "Pin"
    IEEE_SYMBOL = "IEEE Symbol"
    LABEL = "Label"
    PARAMETER = "Parameter"
    DESIGNATOR = "Designator"
    IMAGE = "Image"
    NOTE = "Note"
    TEXT_FRAME = "Text Frame"
    WIRE = "Wire"
    BUS = "Bus"
    BUS_ENTRY = "Bus Entry"
    JUNCTION = "Junction"
    NET_LABEL = "Net Label"
    POWER_PORT = "Power Port"
    PORT = "Port"
    NO_ERC = "No ERC"
    SHEET_SYMBOL = "Sheet Symbol"
    SHEET_ENTRY = "Sheet Entry"
    SHEET = "Sheet"
    SHEET_NAME = "Sheet Name"
    FILE_NAME = "File Name"
    IMPLEMENTATION_LIST = "Implementation List"
    IMPLEMENTATION = "Implementation"
    MAP_DEFINER = "Map Definer"
    HARNESS_CONNECTOR = "Harness Connector"
    HARNESS_ENTRY = "Harness Entry"
    HARNESS_TYPE = "Harness Type"
    SIGNAL_HARNESS = "Signal Harness"
    TEMPLATE = "Template"
    PARAMETER_SET = "Parameter Set"
    COMPILE_MASK = "Compile Mask"
    BLANKET = "Blanket"
    HYPERLINK = "Hyperlink"

    # Supplemental Python JSON entries
    FILE_HEADER = "FileHeader"
    MAP_DEFINER_LIST = "Map Definer List"
    IMPL_PARAMS = "Impl Params"


@dataclass(frozen=True)
class SchJsonObjectTypeBinding:
    object_type: SchJsonObjectType
    record_type: SchRecordType | None
    object_id_name: str | None
    native_backed: bool


SCH_JSON_OBJECT_TYPE_BINDINGS: tuple[SchJsonObjectTypeBinding, ...] = (
    SchJsonObjectTypeBinding(SchJsonObjectType.LINE, SchRecordType.LINE, "eLine", True),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.RECTANGLE, SchRecordType.RECTANGLE, "eRectangle", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.ROUND_RECTANGLE,
        SchRecordType.ROUND_RECTANGLE,
        "eRoundRectangle",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.ELLIPSE, SchRecordType.ELLIPSE, "eEllipse", True
    ),
    SchJsonObjectTypeBinding(SchJsonObjectType.ARC, SchRecordType.ARC, "eArc", True),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.ELLIPTICAL_ARC,
        SchRecordType.ELLIPTICAL_ARC,
        "eEllipticalArc",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.POLYGON, SchRecordType.POLYGON, "ePolygon", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.POLYLINE, SchRecordType.POLYLINE, "ePolyline", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.BEZIER, SchRecordType.BEZIER, "eBezier", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.PIE_CHART, SchRecordType.PIECHART, "ePie", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.COMPONENT, SchRecordType.COMPONENT, "eSchComponent", True
    ),
    SchJsonObjectTypeBinding(SchJsonObjectType.PIN, SchRecordType.PIN, "ePin", True),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.IEEE_SYMBOL, SchRecordType.IEEE_SYMBOL, "eSymbol", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.LABEL, SchRecordType.LABEL, "eLabel", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.PARAMETER, SchRecordType.PARAMETER, "eParameter", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.DESIGNATOR,
        SchRecordType.DESIGNATOR,
        "eDesignator",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.IMAGE, SchRecordType.IMAGE, "eImage", True
    ),
    SchJsonObjectTypeBinding(SchJsonObjectType.NOTE, SchRecordType.NOTE, "eNote", True),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.TEXT_FRAME, SchRecordType.TEXT_FRAME, "eTextFrame", True
    ),
    SchJsonObjectTypeBinding(SchJsonObjectType.WIRE, SchRecordType.WIRE, "eWire", True),
    SchJsonObjectTypeBinding(SchJsonObjectType.BUS, SchRecordType.BUS, "eBus", True),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.BUS_ENTRY, SchRecordType.BUS_ENTRY, "eBusEntry", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.JUNCTION, SchRecordType.JUNCTION, "eJunction", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.NET_LABEL, SchRecordType.NET_LABEL, "eNetLabel", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.POWER_PORT, SchRecordType.POWER_PORT, "ePowerObject", True
    ),
    SchJsonObjectTypeBinding(SchJsonObjectType.PORT, SchRecordType.PORT, "ePort", True),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.NO_ERC, SchRecordType.NO_ERC, "eNoERC", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.SHEET_SYMBOL,
        SchRecordType.SHEET_SYMBOL,
        "eSheetSymbol",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.SHEET_ENTRY,
        SchRecordType.SHEET_ENTRY,
        "eSheetEntry",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.SHEET, SchRecordType.SHEET, "eSheet", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.SHEET_NAME, SchRecordType.SHEET_NAME, "eSheetName", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.FILE_NAME, SchRecordType.FILE_NAME, "eSheetFileName", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.IMPLEMENTATION_LIST,
        SchRecordType.IMPLEMENTATION_LIST,
        "eImplementationsList",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.IMPLEMENTATION,
        SchRecordType.IMPLEMENTATION,
        "eImplementation",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.MAP_DEFINER, SchRecordType.MAP_DEFINER, "eMapDefiner", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.HARNESS_CONNECTOR,
        SchRecordType.HARNESS_CONNECTOR,
        "eHarnessConnector",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.HARNESS_ENTRY,
        SchRecordType.HARNESS_ENTRY,
        "eHarnessEntry",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.HARNESS_TYPE,
        SchRecordType.HARNESS_TYPE,
        "eHarnessConnectorType",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.SIGNAL_HARNESS,
        SchRecordType.SIGNAL_HARNESS,
        "eSignalHarness",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.TEMPLATE, SchRecordType.TEMPLATE, "eTemplate", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.PARAMETER_SET,
        SchRecordType.PARAMETER_SET,
        "eParameterSet",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.COMPILE_MASK,
        SchRecordType.COMPILE_MASK,
        "eCompileMask",
        True,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.BLANKET, SchRecordType.BLANKET, "eBlanket", True
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.HYPERLINK, SchRecordType.HYPERLINK, "eHyperlink", True
    ),
    SchJsonObjectTypeBinding(SchJsonObjectType.FILE_HEADER, None, None, False),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.MAP_DEFINER_LIST,
        SchRecordType.MAP_DEFINER_LIST,
        None,
        False,
    ),
    SchJsonObjectTypeBinding(
        SchJsonObjectType.IMPL_PARAMS, SchRecordType.IMPL_PARAMS, None, False
    ),
)

NATIVE_SCH_OBJECT_ID_TO_JSON_OBJECT_TYPE: dict[str, SchJsonObjectType] = {
    binding.object_id_name: binding.object_type
    for binding in SCH_JSON_OBJECT_TYPE_BINDINGS
    if binding.native_backed and binding.object_id_name is not None
}

SCH_RECORD_TYPE_TO_JSON_OBJECT_TYPE: dict[SchRecordType, SchJsonObjectType] = {
    binding.record_type: binding.object_type
    for binding in SCH_JSON_OBJECT_TYPE_BINDINGS
    if binding.record_type is not None
}

SCH_JSON_OBJECT_TYPE_TO_RECORD_TYPE: dict[SchJsonObjectType, SchRecordType] = {
    binding.object_type: binding.record_type
    for binding in SCH_JSON_OBJECT_TYPE_BINDINGS
    if binding.record_type is not None
}

SCH_JSON_OBJECT_TYPE_ALIASES: dict[str, SchJsonObjectType] = {
    "Header": SchJsonObjectType.FILE_HEADER,
    "Rounded Rectangle": SchJsonObjectType.ROUND_RECTANGLE,
    "Elliptical Arc": SchJsonObjectType.ELLIPTICAL_ARC,
    "SheetName": SchJsonObjectType.SHEET_NAME,
    "SheetFileName": SchJsonObjectType.FILE_NAME,
    # Python now models cross-sheet connectors separately, but native JSON
    # still routes record 17 through the power-object contract.
    "Cross Sheet Connector": SchJsonObjectType.POWER_PORT,
}


def _coerce_record_type(
    record_type: SchRecordType | int | str | None,
) -> SchRecordType | None:
    if isinstance(record_type, SchRecordType):
        return record_type
    if record_type is None:
        return None
    try:
        return SchRecordType(int(record_type))
    except (TypeError, ValueError):
        return None


def sch_json_object_type_from_record_type(
    record_type: SchRecordType | int | str,
) -> SchJsonObjectType | None:
    coerced = _coerce_record_type(record_type)
    if coerced is None:
        return None
    return SCH_RECORD_TYPE_TO_JSON_OBJECT_TYPE.get(coerced)


def sch_json_object_type_from_record(
    record: Mapping[str, object],
) -> SchJsonObjectType | None:
    if "HEADER" in record and "RECORD" not in record:
        return SchJsonObjectType.FILE_HEADER

    if record.get("__BINARY_RECORD__"):
        binary_data = record.get("__BINARY_DATA__")
        if isinstance(binary_data, (bytes, bytearray)) and binary_data:
            return sch_json_object_type_from_record_type(binary_data[0])
        return None

    return sch_json_object_type_from_record_type(record.get("RECORD"))


def normalize_sch_json_object_type(
    object_type: str,
) -> SchJsonObjectType | None:
    try:
        return SchJsonObjectType(object_type)
    except ValueError:
        return SCH_JSON_OBJECT_TYPE_ALIASES.get(object_type)


def sch_record_type_from_json_object_type(
    object_type: str,
) -> SchRecordType | None:
    normalized = normalize_sch_json_object_type(object_type)
    if normalized is None:
        return None
    return SCH_JSON_OBJECT_TYPE_TO_RECORD_TYPE.get(normalized)
