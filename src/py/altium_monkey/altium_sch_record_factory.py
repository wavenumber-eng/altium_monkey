"""
Low-level schematic record factory helpers.
"""

from typing import Any, cast

from .altium_record_sch__arc import AltiumSchArc
from .altium_record_sch__bezier import AltiumSchBezier
from .altium_record_sch__blanket import AltiumSchBlanket
from .altium_record_sch__bus import AltiumSchBus
from .altium_record_sch__bus_entry import AltiumSchBusEntry
from .altium_record_sch__compile_mask import AltiumSchCompileMask
from .altium_record_sch__component import (
    AltiumSchComponent,
    AltiumSchHarnessComponent,
)
from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__ellipse import AltiumSchEllipse
from .altium_record_sch__elliptical_arc import AltiumSchEllipticalArc
from .altium_record_sch__file_name import AltiumSchFileName

# Harness records
from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
from .altium_record_sch__harness_entry import AltiumSchHarnessEntry
from .altium_record_sch__harness_type import AltiumSchHarnessType
from ._altium_record_sch__harness_layout import (
    AltiumSchHarnessBundle,
    AltiumSchHarnessLayoutConnectionPoint,
    AltiumSchHarnessLayoutCovering,
    AltiumSchHarnessLayoutLabel,
    AltiumSchHarnessSplice,
)
from ._altium_record_sch__high_level_code import _AltiumSchHighLevelCodeSymbol

# SchDoc-specific records
from .altium_record_sch__header import AltiumSchHeader
from .altium_record_sch__hyperlink import AltiumSchHyperlink
from .altium_record_sch__ieee_symbol import AltiumSchIeeeSymbol
from .altium_record_sch__image import AltiumSchImage
from .altium_record_sch__implementation import (
    AltiumSchImplementation,
    AltiumSchImplementationList,
    AltiumSchImplParams,
    AltiumSchMapDefiner,
    AltiumSchMapDefinerList,
)
from .altium_record_sch__junction import AltiumSchJunction

# Import all record classes
from .altium_record_sch__label import AltiumSchLabel
from .altium_record_sch__line import AltiumSchLine
from .altium_record_sch__net_label import AltiumSchNetLabel
from .altium_record_sch__no_erc import AltiumSchNoErc
from .altium_record_sch__note import AltiumSchNote
from .altium_record_sch__object_definition import AltiumSchObjectDefinition
from .altium_record_sch__parameter import (
    AltiumSchImageParameter,
    AltiumSchParameter,
)
from ._altium_record_sch__physical_model import (
    _AltiumSchHarnessCavity,
    _AltiumSchHarnessCavityComponent,
    _AltiumSchLineView,
)
from ._altium_record_sch__reuse_block import _AltiumSchReuseBlockImplementationInfo
from ._altium_record_sch__rich_text import (
    _AltiumSchRichTextDocument,
    _AltiumSchRtfLink,
)
from .altium_record_sch__parameter_set import AltiumSchParameterSet
from .altium_record_sch__piechart import AltiumSchPieChart
from .altium_record_sch__pin import AltiumSchPin
from .altium_record_sch__polygon import AltiumSchPolygon
from .altium_record_sch__polyline import AltiumSchPolyline
from .altium_record_sch__port import AltiumSchPort
from .altium_record_sch__cross_sheet_connector import AltiumSchCrossSheetConnector
from .altium_record_sch__power_port import AltiumSchPowerPort
from .altium_record_sch__rectangle import AltiumSchRectangle
from .altium_record_sch__rounded_rectangle import AltiumSchRoundedRectangle
from .altium_record_sch__sheet import AltiumSchSheet
from .altium_record_sch__sheet_entry import AltiumSchSheetEntry
from .altium_record_sch__sheet_name import AltiumSchSheetName
from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol
from .altium_record_sch__signal_harness import AltiumSchSignalHarness
from .altium_record_sch__template import AltiumSchTemplate
from .altium_record_sch__text_frame import AltiumSchTextFrame
from .altium_record_sch__wire import AltiumSchWire
from .altium_sch_json_object_types import (
    SchJsonObjectType,
    normalize_sch_json_object_type,
)
from .altium_record_types import Primitive, SchRecordType
from .altium_serializer import AltiumSerializer, Fields

# Map canonical JSON ObjectType names to Python classes.
OBJECT_TYPE_TO_CLASS: dict[SchJsonObjectType, type[Primitive]] = {
    # Core records
    SchJsonObjectType.COMPONENT: AltiumSchComponent,
    SchJsonObjectType.PIN: AltiumSchPin,
    SchJsonObjectType.IEEE_SYMBOL: AltiumSchIeeeSymbol,
    SchJsonObjectType.LABEL: AltiumSchLabel,
    SchJsonObjectType.PARAMETER: AltiumSchParameter,
    SchJsonObjectType.DESIGNATOR: AltiumSchDesignator,
    SchJsonObjectType.TEXT_FRAME: AltiumSchTextFrame,
    SchJsonObjectType.TEMPLATE: AltiumSchTemplate,
    SchJsonObjectType.PARAMETER_SET: AltiumSchParameterSet,
    # Graphical primitives
    SchJsonObjectType.LINE: AltiumSchLine,
    SchJsonObjectType.RECTANGLE: AltiumSchRectangle,
    SchJsonObjectType.ROUND_RECTANGLE: AltiumSchRoundedRectangle,
    SchJsonObjectType.ELLIPSE: AltiumSchEllipse,
    SchJsonObjectType.PIE_CHART: AltiumSchPieChart,
    SchJsonObjectType.ELLIPTICAL_ARC: AltiumSchEllipticalArc,
    SchJsonObjectType.ARC: AltiumSchArc,
    SchJsonObjectType.BEZIER: AltiumSchBezier,
    SchJsonObjectType.POLYLINE: AltiumSchPolyline,
    SchJsonObjectType.POLYGON: AltiumSchPolygon,
    SchJsonObjectType.IMAGE: AltiumSchImage,
    # SchDoc-specific records
    SchJsonObjectType.SHEET: AltiumSchSheet,
    SchJsonObjectType.WIRE: AltiumSchWire,
    SchJsonObjectType.BUS: AltiumSchBus,
    SchJsonObjectType.BUS_ENTRY: AltiumSchBusEntry,
    SchJsonObjectType.NET_LABEL: AltiumSchNetLabel,
    SchJsonObjectType.POWER_PORT: AltiumSchPowerPort,
    SchJsonObjectType.JUNCTION: AltiumSchJunction,
    SchJsonObjectType.PORT: AltiumSchPort,
    SchJsonObjectType.NO_ERC: AltiumSchNoErc,
    SchJsonObjectType.SHEET_SYMBOL: AltiumSchSheetSymbol,
    SchJsonObjectType.SHEET_ENTRY: AltiumSchSheetEntry,
    # Implementation records
    SchJsonObjectType.IMPLEMENTATION_LIST: AltiumSchImplementationList,
    SchJsonObjectType.IMPLEMENTATION: AltiumSchImplementation,
    SchJsonObjectType.MAP_DEFINER_LIST: AltiumSchMapDefinerList,
    SchJsonObjectType.MAP_DEFINER: AltiumSchMapDefiner,
    SchJsonObjectType.IMPL_PARAMS: AltiumSchImplParams,
    # SchDoc header and metadata
    SchJsonObjectType.FILE_HEADER: AltiumSchHeader,
    SchJsonObjectType.SHEET_NAME: AltiumSchSheetName,
    SchJsonObjectType.FILE_NAME: AltiumSchFileName,
    # Annotations
    SchJsonObjectType.NOTE: AltiumSchNote,
    SchJsonObjectType.COMPILE_MASK: AltiumSchCompileMask,
    SchJsonObjectType.BLANKET: AltiumSchBlanket,
    SchJsonObjectType.HYPERLINK: AltiumSchHyperlink,
    # Harness records
    SchJsonObjectType.HARNESS_CONNECTOR: AltiumSchHarnessConnector,
    SchJsonObjectType.HARNESS_ENTRY: AltiumSchHarnessEntry,
    SchJsonObjectType.HARNESS_TYPE: AltiumSchHarnessType,
    SchJsonObjectType.SIGNAL_HARNESS: AltiumSchSignalHarness,
    SchJsonObjectType.HARNESS_SPLICE: AltiumSchHarnessSplice,
    SchJsonObjectType.HARNESS_LAYOUT_LABEL: AltiumSchHarnessLayoutLabel,
    SchJsonObjectType.HARNESS_LAYOUT_CONNECTION_POINT: (
        AltiumSchHarnessLayoutConnectionPoint
    ),
    SchJsonObjectType.HARNESS_BUNDLE: AltiumSchHarnessBundle,
    SchJsonObjectType.HARNESS_COVERING: AltiumSchHarnessLayoutCovering,
}


def get_record_class(object_type: str) -> type[Primitive] | None:
    """
    Get record class by native ObjectType name.

    Args:
        object_type: Object type name (e.g., "Pin", "Component", "Wire")

    Returns:
        Record class or None if not found
    """
    if object_type == "Cross Sheet Connector":
        return AltiumSchCrossSheetConnector
    normalized = normalize_sch_json_object_type(object_type)
    if normalized is None:
        return None
    return OBJECT_TYPE_TO_CLASS.get(normalized)


# Map record-type enums to Python classes (module constant so the factory does
# not rebuild the table on every record instantiation).
RECORD_TYPE_TO_CLASS: dict[SchRecordType, type[Primitive]] = {
    # Core records (SchLib + SchDoc)
    SchRecordType.COMPONENT: AltiumSchComponent,
    SchRecordType.PIN: AltiumSchPin,
    SchRecordType.IEEE_SYMBOL: AltiumSchIeeeSymbol,
    SchRecordType.LABEL: AltiumSchLabel,
    SchRecordType.PARAMETER: AltiumSchParameter,
    SchRecordType.DESIGNATOR: AltiumSchDesignator,
    SchRecordType.TEXT_FRAME: AltiumSchTextFrame,
    SchRecordType.TEMPLATE: AltiumSchTemplate,
    SchRecordType.PARAMETER_SET: AltiumSchParameterSet,
    # Graphical primitives
    SchRecordType.LINE: AltiumSchLine,
    SchRecordType.RECTANGLE: AltiumSchRectangle,
    SchRecordType.ROUND_RECTANGLE: AltiumSchRoundedRectangle,
    SchRecordType.ELLIPSE: AltiumSchEllipse,
    SchRecordType.PIECHART: AltiumSchPieChart,
    SchRecordType.ELLIPTICAL_ARC: AltiumSchEllipticalArc,
    SchRecordType.ARC: AltiumSchArc,
    SchRecordType.BEZIER: AltiumSchBezier,
    SchRecordType.POLYLINE: AltiumSchPolyline,
    SchRecordType.POLYGON: AltiumSchPolygon,
    SchRecordType.IMAGE: AltiumSchImage,
    # SchDoc-specific records
    SchRecordType.SHEET: AltiumSchSheet,
    SchRecordType.WIRE: AltiumSchWire,
    SchRecordType.BUS: AltiumSchBus,
    SchRecordType.BUS_ENTRY: AltiumSchBusEntry,
    SchRecordType.NET_LABEL: AltiumSchNetLabel,
    SchRecordType.POWER_PORT: AltiumSchPowerPort,
    SchRecordType.JUNCTION: AltiumSchJunction,
    SchRecordType.PORT: AltiumSchPort,
    SchRecordType.NO_ERC: AltiumSchNoErc,
    SchRecordType.SHEET_SYMBOL: AltiumSchSheetSymbol,
    SchRecordType.SHEET_ENTRY: AltiumSchSheetEntry,
    # Implementation records
    SchRecordType.IMPLEMENTATION_LIST: AltiumSchImplementationList,
    SchRecordType.IMPLEMENTATION: AltiumSchImplementation,
    SchRecordType.MAP_DEFINER_LIST: AltiumSchMapDefinerList,
    SchRecordType.MAP_DEFINER: AltiumSchMapDefiner,
    SchRecordType.IMPL_PARAMS: AltiumSchImplParams,
    # SchDoc header and metadata
    SchRecordType.HEADER: AltiumSchHeader,
    SchRecordType.SHEET_NAME: AltiumSchSheetName,
    SchRecordType.FILE_NAME: AltiumSchFileName,
    # Annotations
    SchRecordType.NOTE: AltiumSchNote,
    SchRecordType.COMPILE_MASK: AltiumSchCompileMask,
    SchRecordType.BLANKET: AltiumSchBlanket,
    SchRecordType.HYPERLINK: AltiumSchHyperlink,
    SchRecordType.OBJECT_DEFINITION: AltiumSchObjectDefinition,
    # Harness records
    SchRecordType.HARNESS_COMPONENT: AltiumSchHarnessComponent,
    SchRecordType.HARNESS_CONNECTOR: AltiumSchHarnessConnector,
    SchRecordType.HARNESS_ENTRY: AltiumSchHarnessEntry,
    SchRecordType.HARNESS_TYPE: AltiumSchHarnessType,
    SchRecordType.SIGNAL_HARNESS: AltiumSchSignalHarness,
    SchRecordType.HIGH_LEVEL_CODE_SYMBOL: _AltiumSchHighLevelCodeSymbol,
    SchRecordType.HIGH_LEVEL_CODE_ENTRY: AltiumSchSheetEntry,
    SchRecordType.HIGH_LEVEL_CODE_NAME: AltiumSchSheetName,
    SchRecordType.HIGH_LEVEL_CODE_FILE_NAME: AltiumSchFileName,
    SchRecordType.HARNESS_SPLICE: AltiumSchHarnessSplice,
    SchRecordType.HARNESS_LAYOUT_LABEL: AltiumSchHarnessLayoutLabel,
    SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT: (
        AltiumSchHarnessLayoutConnectionPoint
    ),
    SchRecordType.HARNESS_BUNDLE: AltiumSchHarnessBundle,
    SchRecordType.HARNESS_LAYOUT_COVERING: AltiumSchHarnessLayoutCovering,
    SchRecordType.REUSE_BLOCK_IMPLEMENTATION_INFO: (
        _AltiumSchReuseBlockImplementationInfo
    ),
    SchRecordType.RICH_TEXT_DOCUMENT: _AltiumSchRichTextDocument,
    SchRecordType.RTF_LINK: _AltiumSchRtfLink,
    SchRecordType.LINE_VIEW: _AltiumSchLineView,
    SchRecordType.HARNESS_CAVITY: _AltiumSchHarnessCavity,
    SchRecordType.HARNESS_CAVITY_COMPONENT: _AltiumSchHarnessCavityComponent,
}


def create_record_from_type(record_type: SchRecordType) -> Primitive | None:
    """
    Factory function to create record object from type.

    Args:
        record_type: Record type enum

    Returns:
        New record object or None if type not implemented
    """
    record_class = RECORD_TYPE_TO_CLASS.get(record_type)
    if record_class:
        return record_class()
    return None


def create_record_from_record(record: dict[str, object]) -> Primitive | None:
    """
    Create a record object using raw-record discriminators when needed.

    RECORD 17 is shared by power ports and cross-sheet connectors, so the raw
    record content decides which object-model class should be instantiated.
    """
    record_value = record.get("RECORD")
    if record_value is None:
        return None

    try:
        record_type = SchRecordType(int(cast(Any, record_value)))
    except (TypeError, ValueError):
        return None

    if record_type == SchRecordType.POWER_PORT:
        is_cross_sheet, _ = AltiumSerializer().read_bool(
            record, Fields.IS_CROSS_SHEET_CONNECTOR, default=False
        )
        if is_cross_sheet:
            return AltiumSchCrossSheetConnector()

    if record_type == SchRecordType.PARAMETER:
        is_image_parameter, _ = AltiumSerializer().read_bool(
            record, Fields.IS_IMAGE_PARAMETER, default=False
        )
        if is_image_parameter:
            return AltiumSchImageParameter()

    return create_record_from_type(record_type)
