"""
Public Altium Python package.

Core runtime code lives under ``src/py/altium_monkey``.
Private test helpers live in the repo-level ``tests`` package in the private
development checkout and remain importable as ``altium_monkey.tests`` for
existing private test and oracle scripts.
"""

# ruff: noqa: E402

import importlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .altium_api_markers import public_api
from ._version import __version__, __version_info__

if TYPE_CHECKING:
    from .altium_compiled_design_model import (
        AltiumCompileDiagnostic,
        AltiumCompiledAnnotationState,
        AltiumCompiledComponent,
        AltiumCompiledDesign,
        AltiumCompiledDesignSummary,
        AltiumCompiledLogicalDocument,
        AltiumCompiledNet,
        AltiumCompiledNetEndpoint,
        AltiumCompiledNetItem,
        AltiumCompiledNetTerminal,
        AltiumCompiledPhysicalDocument,
        AltiumCompiledPhysicalSheetSymbol,
        AltiumCompiledSheetSymbol,
        AltiumProjectCompileOptions,
    )
    from .altium_compiled_schematic_graph import (
        AltiumCompiledSchematicGraph,
        AltiumPhysicalPageMetadata,
    )
    from .altium_design import AltiumDesign
    from .altium_draftsman import (
        AltiumDraftsmanDocument,
        AltiumDraftsmanDocumentOptions,
        AltiumDraftsmanItem,
        AltiumDraftsmanNote,
        AltiumDraftsmanNoteElement,
        AltiumDraftsmanPage,
        AltiumDraftsmanPicture,
        AltiumDraftsmanText,
        DraftsmanColor,
        DraftsmanFontDecoration,
        DraftsmanFontStyle,
        DraftsmanHorizontalAlignment,
        DraftsmanMargin,
        DraftsmanNoteBorderStyle,
        DraftsmanPoint,
        DraftsmanRect,
        DraftsmanSize,
        DraftsmanStandardSheetSize,
        DraftsmanVerticalAlignment,
    )
    from .altium_pcbdoc import AltiumPcbDoc
    from .altium_pcbdoc_builder import PcbCustomPadLayerShapeSpec, PcbDocBuilder
    from .altium_pcblib import (
        AltiumPcbFootprint,
        AltiumPcbLib,
        AltiumPcbLibPrimitiveParameterGroup,
    )
    from .altium_layer_stack_document import (
        AltiumComponentPlacement,
        AltiumCopperMaterialSpec,
        AltiumDielectricLayerKind,
        AltiumDielectricMaterialSpec,
        AltiumImpedanceProfileSpec,
        AltiumLayerPair,
        AltiumLayerStackDocument,
        AltiumRigidCopperLayerSpec,
        AltiumRigidDielectricLayerSpec,
        AltiumRigidStackRowSpec,
        AltiumStackupRoughnessModel,
        AltiumStackupSettings,
        AltiumStackupType,
        AltiumTransmissionLineSpec,
    )
    from .altium_schdoc import AltiumSchDoc
    from .altium_schlib import AltiumSchLib
    from .altium_stackup import AltiumStackupDocument, StackupTextFeature
    from .altium_stackupx import (
        AltiumStackupXDocument,
        StackupXBranch,
        StackupXBranchSection,
        StackupXBranchSectionStack,
        StackupXFeature,
        StackupXFeatureId,
        StackupXImpedanceProfile,
        StackupXLayer,
        StackupXLayerType,
        StackupXProperty,
        StackupXSpan,
        StackupXStack,
        StackupXTransmissionLine,
    )

_ALTIUM_REPO_ROOT = Path(__file__).resolve().parents[3]


def _append_private_test_package_paths() -> None:
    """
    Keep private test helpers importable as ``altium_monkey.tests``.
    """
    candidates = [_ALTIUM_REPO_ROOT]

    legacy_suites_root = os.environ.get("WN_TEST_SUITES_ROOT")
    if legacy_suites_root:
        candidates.append(
            Path(legacy_suites_root).expanduser() / "suites" / "altium_monkey"
        )

    for candidate in candidates:
        if not (candidate / "tests").exists():
            continue
        candidate_str = str(candidate)
        if candidate_str not in __path__:
            __path__.append(candidate_str)


_append_private_test_package_paths()

# OLE file utilities (CFB/Compound Binary Format)
from .altium_ole import (
    ALTIUM_CLSID,
    ALTIUM_CLSID_BYTES,
    AltiumOleFile,
    AltiumOleWriter,
    DIR_ENTRY_SIZE,
    ENDOFCHAIN,
    FREESECT,
    HEADER_SIZE,
    MINI_SECTOR_SIZE,
    MINI_STREAM_CUTOFF,
    OLE_MAGIC,
    OleDirEntry,
)
from .altium_intlib import (
    AltiumIntLib,
    IntLibComponent,
    IntLibExtractionResult,
    IntLibModel,
    IntLibSource,
)

# Import enums
from .altium_common_enums import ComponentKind
from .altium_sch_enums import (
    IeeeSymbol,
    OffSheetConnectorStyle,
    ParameterSetStyle,
    PortIOType,
    PortStyle,
    PowerObjectStyle,
    PinElectrical,
    PinItemMode,
    PinOrientation,
    PinTextAnchor,
    SchHorizontalAlign,
    PinTextOrientation,
    PinTextRotation,
    Rotation90,
    StdLogicState,
    SymbolLineWidth,
    TextJustification,
    TextOrientation,
)
from .altium_record_sch__arc import AltiumSchArc
from .altium_record_sch__bezier import AltiumSchBezier
from .altium_record_sch__blanket import AltiumSchBlanket
from .altium_record_sch__bus import AltiumSchBus
from .altium_record_sch__bus_entry import AltiumSchBusEntry
from .altium_record_sch__compile_mask import AltiumSchCompileMask
from .altium_record_sch__component import AltiumSchComponent
from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__ellipse import AltiumSchEllipse
from .altium_record_sch__elliptical_arc import AltiumSchEllipticalArc
from .altium_record_sch__file_name import AltiumSchFileName

# Harness records
from .altium_record_sch__harness_connector import (
    AltiumSchHarnessConnector,
    SchHarnessConnectorSide,
)
from .altium_record_sch__harness_entry import AltiumSchHarnessEntry, BusTextStyle
from .altium_record_sch__harness_type import AltiumSchHarnessType

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
from .altium_record_sch__no_erc import AltiumSchNoErc, NoErcSymbol
from .altium_record_sch__note import AltiumSchNote
from .altium_record_sch__parameter import AltiumSchParameter
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
from .altium_record_sch__sheet import (
    AltiumSchSheet,
    DocumentBorderStyle,
    SheetStyle,
    WorkspaceOrientation,
)
from .altium_record_sch__sheet_entry import (
    AltiumSchSheetEntry,
    SchSheetEntryArrowKind,
    SchSheetEntryIOType,
    SheetEntrySide,
)
from .altium_record_sch__sheet_name import AltiumSchSheetName
from .altium_record_sch__sheet_symbol import (
    AltiumSchSheetSymbol,
    SchSheetSymbolType,
)
from .altium_record_sch__signal_harness import AltiumSchSignalHarness
from .altium_font_resolver import (
    ALTIUM_PORTABLE_FONT_REPLACEMENTS,
    FontReplacementRule,
    portable_font_replacements,
)
from .altium_font_manager import FontIDManager
from .altium_prjpcb_builder import (
    AltiumPrjPcbBuilder,
    AltiumPrjPcbDocumentEntry,
    AltiumPrjPcbDocumentKind,
)
from .altium_record_sch__template import AltiumSchTemplate
from .altium_record_sch__text_frame import AltiumSchTextFrame
from .altium_record_sch__wire import AltiumSchWire
from .altium_record_types import (
    CaseInsensitiveDict,
    ColorValue,
    CoordPoint,
    LineShape,
    LineStyle,
    LineWidth,
    PcbLayer,
    PcbRecordType,
    ReadOnlyState,
    SchFontSpec,
    SchPointMils,
    SchRectMils,
)
from .altium_pcb_layer_ref import (
    PcbLayerFamily,
    PcbLayerRef,
    PcbLayerRegistry,
    PcbLayerRegistryEntry,
    PcbLayerResolutionError,
    PcbPrimitiveLayerState,
    PcbSignalLayerKind,
    coerce_pcb_layer_ref,
    resolve_pcb_primitive_layer_state,
)
from .altium_sch_object_factory import (
    make_sch_arc,
    make_sch_bezier,
    make_sch_blanket,
    make_sch_bus,
    make_sch_bus_entry,
    make_sch_compile_mask,
    make_sch_embedded_image,
    make_sch_ellipse,
    make_sch_elliptical_arc,
    make_sch_file_name,
    make_sch_full_circle,
    make_sch_harness_connector,
    make_sch_harness_entry,
    make_sch_harness_type,
    make_sch_junction,
    make_sch_line,
    make_sch_net_label,
    make_sch_off_sheet_connector,
    make_sch_no_erc,
    make_sch_parameter,
    make_sch_parameter_set,
    make_sch_pin,
    make_sch_polygon,
    make_sch_polyline,
    make_sch_note,
    make_sch_port,
    make_sch_power_port,
    make_sch_rectangle,
    make_sch_rounded_rectangle,
    make_sch_sheet_entry,
    make_sch_sheet_name,
    make_sch_sheet_symbol,
    make_sch_signal_harness,
    make_sch_text_frame,
    make_sch_text_string,
    make_sch_wire,
)

# SVG rendering
from .altium_sch_svg_renderer import (
    SchCompileMaskRenderMode,
    SchJunctionZOrder,
    SchSvgRenderContext,
    SchSvgRenderOptions,
)
from .altium_pcb_svg_renderer import (
    PcbSvgRenderContext,
    PcbSvgRenderOptions,
    PcbSvgRenderer,
)
from .altium_board import (
    AltiumBoardOutline,
    BoardOutlineVertex,
    resolve_outline_arc_segment,
)
from .altium_pcb_model_checksum import compute_altium_model_checksum
from .altium_pcb_step_bounds import PcbStepModelBounds, compute_step_model_bounds_mils
from .altium_pcb_mask_expansion import PcbMaskExpansion, PcbMaskExpansionMode
from .altium_pcb_keepout import (
    PCB_KEEPOUT_RESTRICTION_ALL_MASK,
    PCB_KEEPOUT_RESTRICTION_KNOWN_MASK,
    PcbKeepoutRestriction,
    decode_pcb_keepout_restrictions,
    encode_pcb_keepout_restrictions,
    pcb_keepout_restriction_names,
    pcb_keepout_restriction_unknown_bits,
)

# PCB record classes
from .altium_record_pcb__arc import AltiumPcbArc
from .altium_record_pcb__fill import AltiumPcbFill
from .altium_record_pcb__model import AltiumPcbModel
from .altium_pcb_enums import (
    MechanicalLayerKind,
    PadHoleShape,
    PadShape,
    PcbBarcodeKind,
    PcbBarcodeRenderMode,
    PcbIpc4761ViaType,
    PcbLibIdentifierKind,
    PcbTextAutoposition,
    PcbTextJustification,
    PcbTextKind,
    PcbViaStructureFeatureSide,
    PcbViaStructureFeatureType,
)
from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_pcb__region import AltiumPcbRegion
from .altium_record_pcb__text import AltiumPcbText
from .altium_record_pcb__track import AltiumPcbTrack
from .altium_record_pcb__polygon import AltiumPcbPolygon, PcbPolygonVertex
from .altium_record_pcb__net import AltiumPcbNet
from .altium_pcb_enums import PcbNetClassKind
from .altium_record_pcb__netclass import AltiumPcbNetClass
from .altium_record_pcb__differential_pair import AltiumPcbDifferentialPair
from .altium_pcb_enums import PcbViaMode
from .altium_pcb_via_structure import (
    AltiumPcbViaStructure,
    AltiumPcbViaStructureFeature,
    AltiumPcbViaStructureLink,
)
from .altium_record_pcb__via import AltiumPcbVia
from .altium_pcb_enums import PcbRegionKind
from .altium_record_pcb__shapebased_region import (
    AltiumPcbShapeBasedRegion,
    PcbExtendedVertex,
    PcbSimpleVertex,
)
from .altium_pcb_enums import PcbBodyProjection
from .altium_record_pcb__component_body import (
    AltiumPcbComponentBody,
)

# Embedded file utilities
from .altium_embedded_files import (
    EmbeddedFont,
    EmbeddedModel,
)
from .altium_embedded_assets import (
    EmbeddedAssetInventory,
    EmbeddedAssetReference,
    EmbeddedOpaqueAssetSummary,
    EmbeddedPcbFontSummary,
    EmbeddedPcbModelSummary,
    embedded_asset_schema_id,
)
from .altium_extractable_assets import (
    EmbeddedFontAssetDetails,
    EmbeddedModelAssetDetails,
    OpaqueEmbeddedAssetDetails,
    AltiumAssetInventory,
    AltiumAssetRef,
    AltiumAssetSummary,
    AltiumExtractedAsset,
    PcbFootprintAssetDetails,
    SchSymbolAssetDetails,
    extractable_asset_schema_id,
)

# Export core public types.
__all__ = [
    "__version__",
    "__version_info__",
    # OLE file utilities
    "AltiumOleFile",
    "AltiumOleWriter",
    "OleDirEntry",
    "OLE_MAGIC",
    "ALTIUM_CLSID",
    "ALTIUM_CLSID_BYTES",
    "HEADER_SIZE",
    "DIR_ENTRY_SIZE",
    "MINI_SECTOR_SIZE",
    "MINI_STREAM_CUTOFF",
    "ENDOFCHAIN",
    "FREESECT",
    # Integrated library utilities
    "AltiumIntLib",
    "IntLibComponent",
    "IntLibExtractionResult",
    "IntLibModel",
    "IntLibSource",
    # Embedded PCB asset inventory
    "EmbeddedAssetInventory",
    "EmbeddedAssetReference",
    "EmbeddedOpaqueAssetSummary",
    "EmbeddedPcbFontSummary",
    "EmbeddedPcbModelSummary",
    "embedded_asset_schema_id",
    # Generic extractable asset inventory
    "AltiumAssetInventory",
    "AltiumAssetRef",
    "AltiumAssetSummary",
    "AltiumExtractedAsset",
    "EmbeddedFontAssetDetails",
    "EmbeddedModelAssetDetails",
    "OpaqueEmbeddedAssetDetails",
    "PcbFootprintAssetDetails",
    "SchSymbolAssetDetails",
    "extractable_asset_schema_id",
    # Enums
    "IeeeSymbol",
    "PinElectrical",
    "PinItemMode",
    "PinOrientation",
    "PinTextAnchor",
    "PinTextOrientation",
    "PinTextRotation",
    "OffSheetConnectorStyle",
    "PortIOType",
    "PortStyle",
    "Rotation90",
    "SchHorizontalAlign",
    "SheetStyle",
    "SymbolLineWidth",
    "StdLogicState",
    "BusTextStyle",
    "TextJustification",
    "TextOrientation",
    "ParameterSetStyle",
    "PowerObjectStyle",
    "DocumentBorderStyle",
    "WorkspaceOrientation",
    "ComponentKind",
    "NoErcSymbol",
    "ReadOnlyState",
    "CaseInsensitiveDict",
    "ColorValue",
    "CoordPoint",
    "LineShape",
    "LineStyle",
    "SchFontSpec",
    "SchPointMils",
    "SchRectMils",
    "make_sch_bus",
    "make_sch_bus_entry",
    "make_sch_arc",
    "make_sch_bezier",
    "make_sch_blanket",
    "make_sch_embedded_image",
    "make_sch_compile_mask",
    "make_sch_ellipse",
    "make_sch_elliptical_arc",
    "make_sch_file_name",
    "make_sch_full_circle",
    "make_sch_harness_connector",
    "make_sch_harness_entry",
    "make_sch_harness_type",
    "make_sch_junction",
    "make_sch_line",
    "make_sch_net_label",
    "make_sch_off_sheet_connector",
    "make_sch_no_erc",
    "make_sch_parameter",
    "make_sch_parameter_set",
    "make_sch_pin",
    "make_sch_polygon",
    "make_sch_polyline",
    "make_sch_port",
    "make_sch_power_port",
    "make_sch_rectangle",
    "make_sch_note",
    "make_sch_rounded_rectangle",
    "make_sch_sheet_entry",
    "make_sch_sheet_name",
    "make_sch_sheet_symbol",
    "make_sch_signal_harness",
    "make_sch_text_frame",
    "make_sch_text_string",
    "make_sch_wire",
    # Record classes
    "AltiumSchComponent",
    "AltiumSchPin",
    "AltiumSchIeeeSymbol",
    "AltiumSchLabel",
    "AltiumSchParameter",
    "AltiumSchDesignator",
    "AltiumSchTextFrame",
    "AltiumSchTemplate",
    "AltiumSchParameterSet",
    "AltiumSchLine",
    "AltiumSchRectangle",
    "AltiumSchRoundedRectangle",
    "AltiumSchEllipse",
    "AltiumSchArc",
    "AltiumSchBezier",
    "AltiumSchPolyline",
    "AltiumSchPolygon",
    "AltiumSchPieChart",
    "AltiumSchEllipticalArc",
    "AltiumSchImage",
    "AltiumSchSheet",
    "AltiumSchWire",
    "AltiumSchBus",
    "AltiumSchBusEntry",
    "AltiumSchNetLabel",
    "AltiumSchPowerPort",
    "AltiumSchCrossSheetConnector",
    "AltiumSchJunction",
    "AltiumSchPort",
    "AltiumSchNoErc",
    "AltiumSchSheetSymbol",
    "SchSheetSymbolType",
    "AltiumSchSheetEntry",
    "SchSheetEntryArrowKind",
    "SchSheetEntryIOType",
    "SheetEntrySide",
    # Implementation records
    "AltiumSchImplementationList",
    "AltiumSchImplementation",
    "AltiumSchMapDefinerList",
    "AltiumSchMapDefiner",
    "AltiumSchImplParams",
    # SchDoc header and metadata
    "AltiumSchHeader",
    "AltiumSchSheetName",
    "AltiumSchFileName",
    # Annotations
    "AltiumSchNote",
    "AltiumSchCompileMask",
    "AltiumSchBlanket",
    "AltiumSchHyperlink",
    # Harness records
    "AltiumSchHarnessConnector",
    "AltiumSchHarnessEntry",
    "AltiumSchHarnessType",
    "AltiumSchSignalHarness",
    "FontIDManager",
    "AltiumPrjPcbBuilder",
    "AltiumPrjPcbDocumentEntry",
    "AltiumPrjPcbDocumentKind",
    "ALTIUM_PORTABLE_FONT_REPLACEMENTS",
    "FontReplacementRule",
    "portable_font_replacements",
    "SchHarnessConnectorSide",
    "LineWidth",
    # PCB record classes
    "PcbLayer",
    "PcbRecordType",
    "MechanicalLayerKind",
    "AltiumPcbTrack",
    "AltiumPcbArc",
    "PadHoleShape",
    "PadShape",
    "PcbMaskExpansion",
    "PcbMaskExpansionMode",
    "PcbKeepoutRestriction",
    "PCB_KEEPOUT_RESTRICTION_KNOWN_MASK",
    "PCB_KEEPOUT_RESTRICTION_ALL_MASK",
    "decode_pcb_keepout_restrictions",
    "encode_pcb_keepout_restrictions",
    "pcb_keepout_restriction_names",
    "pcb_keepout_restriction_unknown_bits",
    "PcbTextKind",
    "PcbBarcodeKind",
    "PcbBarcodeRenderMode",
    "PcbIpc4761ViaType",
    "PcbLibIdentifierKind",
    "PcbTextAutoposition",
    "PcbTextJustification",
    "AltiumPcbPad",
    "AltiumPcbText",
    "AltiumPcbFill",
    "AltiumPcbRegion",
    "AltiumPcbModel",
    "AltiumPcbPolygon",
    "PcbPolygonVertex",
    "AltiumPcbNet",
    "AltiumPcbNetClass",
    "AltiumPcbDifferentialPair",
    "PcbNetClassKind",
    "AltiumPcbVia",
    "PcbViaMode",
    "PcbViaStructureFeatureType",
    "PcbViaStructureFeatureSide",
    "AltiumPcbViaStructure",
    "AltiumPcbViaStructureFeature",
    "AltiumPcbViaStructureLink",
    "AltiumPcbShapeBasedRegion",
    "PcbRegionKind",
    "PcbExtendedVertex",
    "PcbSimpleVertex",
    "AltiumPcbComponentBody",
    "PcbBodyProjection",
    "PcbLayerFamily",
    "PcbLayerRef",
    "PcbLayerRegistry",
    "PcbLayerRegistryEntry",
    "PcbLayerResolutionError",
    "PcbPrimitiveLayerState",
    "PcbSignalLayerKind",
    "coerce_pcb_layer_ref",
    "resolve_pcb_primitive_layer_state",
    "AltiumBoardOutline",
    "BoardOutlineVertex",
    "resolve_outline_arc_segment",
    # Embedded file utilities
    "EmbeddedFont",
    "EmbeddedModel",
    # High-level parsers (lazy loaded)
    "AltiumDesign",
    "AltiumCompileDiagnostic",
    "AltiumCompiledAnnotationState",
    "AltiumCompiledComponent",
    "AltiumCompiledDesign",
    "AltiumCompiledDesignSummary",
    "AltiumCompiledSchematicGraph",
    "AltiumPhysicalPageMetadata",
    "AltiumCompiledLogicalDocument",
    "AltiumCompiledNet",
    "AltiumCompiledNetEndpoint",
    "AltiumCompiledNetItem",
    "AltiumCompiledNetTerminal",
    "AltiumCompiledPhysicalDocument",
    "AltiumCompiledPhysicalSheetSymbol",
    "AltiumCompiledSheetSymbol",
    "AltiumProjectCompileOptions",
    "AltiumSchLib",
    "AltiumSchDoc",
    "AltiumPcbDoc",
    "AltiumPcbLib",
    "AltiumPcbFootprint",
    "AltiumPcbLibPrimitiveParameterGroup",
    "AltiumDraftsmanDocument",
    "AltiumDraftsmanPage",
    "AltiumDraftsmanItem",
    "AltiumDraftsmanNote",
    "AltiumDraftsmanNoteElement",
    "AltiumDraftsmanPicture",
    "AltiumDraftsmanText",
    "AltiumDraftsmanDocumentOptions",
    "DraftsmanColor",
    "DraftsmanFontDecoration",
    "DraftsmanFontStyle",
    "DraftsmanHorizontalAlignment",
    "DraftsmanMargin",
    "DraftsmanNoteBorderStyle",
    "DraftsmanPoint",
    "DraftsmanRect",
    "DraftsmanSize",
    "DraftsmanStandardSheetSize",
    "DraftsmanVerticalAlignment",
    # SVG rendering
    "SchSvgRenderContext",
    "SchSvgRenderOptions",
    "SchJunctionZOrder",
    "SchCompileMaskRenderMode",
    "PcbSvgRenderContext",
    "PcbSvgRenderOptions",
    "PcbSvgRenderer",
    "compute_altium_model_checksum",
    "PcbStepModelBounds",
    "compute_step_model_bounds_mils",
    "PcbDocBuilder",
    "PcbCustomPadLayerShapeSpec",
    "AltiumComponentPlacement",
    "AltiumCopperMaterialSpec",
    "AltiumDielectricLayerKind",
    "AltiumDielectricMaterialSpec",
    "AltiumLayerPair",
    "AltiumLayerStackDocument",
    "AltiumRigidCopperLayerSpec",
    "AltiumRigidDielectricLayerSpec",
    "AltiumRigidStackRowSpec",
    "AltiumStackupRoughnessModel",
    "AltiumStackupSettings",
    "AltiumStackupType",
    "AltiumImpedanceProfileSpec",
    "AltiumTransmissionLineSpec",
    "AltiumStackupDocument",
    "StackupTextFeature",
    "AltiumStackupXDocument",
    "StackupXBranch",
    "StackupXBranchSection",
    "StackupXBranchSectionStack",
    "StackupXFeature",
    "StackupXFeatureId",
    "StackupXImpedanceProfile",
    "StackupXLayer",
    "StackupXLayerType",
    "StackupXProperty",
    "StackupXSpan",
    "StackupXStack",
    "StackupXTransmissionLine",
]

_EXTRA_PUBLIC_SURFACES: dict[str, tuple[str, ...]] = {
    "altium_component_kind": (
        "parse_component_kind",
        "component_kind_includes_in_netlist",
        "component_kind_includes_in_bom",
    ),
    "altium_embedded_files": ("classify_embedded_model_format",),
    "altium_launcher": ("AltiumLauncher",),
    "altium_layer_stack_document": (
        "AltiumBoardBendLine",
        "AltiumComponentPlacement",
        "AltiumCopperMaterialSpec",
        "AltiumDielectricLayerKind",
        "AltiumDielectricMaterialSpec",
        "AltiumImpedanceProfile",
        "AltiumImpedanceProfileSpec",
        "AltiumLayerPair",
        "AltiumLayerRegistry",
        "AltiumLayerRegistryEntry",
        "AltiumLayerStackDocument",
        "AltiumNativePcbDocWriteSupport",
        "AltiumPhysicalStack",
        "AltiumRigidCopperLayerSpec",
        "AltiumRigidDielectricLayerSpec",
        "AltiumRigidStackRowSpec",
        "AltiumStackupRoughnessModel",
        "AltiumStackupSettings",
        "AltiumStackupType",
        "AltiumStackBendLine",
        "AltiumStackBranch",
        "AltiumStackBranchSection",
        "AltiumStackBranchStack",
        "AltiumStackLayer",
        "AltiumStackRegion",
        "AltiumStackSubstack",
        "AltiumTransmissionLine",
        "AltiumTransmissionLineSpec",
    ),
    "altium_stackup": (
        "AltiumStackupDocument",
        "StackupTextFeature",
    ),
    "altium_stackupx": (
        "AltiumStackupXDocument",
        "StackupXBranch",
        "StackupXBranchSection",
        "StackupXBranchSectionStack",
        "StackupXFeature",
        "StackupXFeatureId",
        "StackupXImpedanceProfile",
        "StackupXLayer",
        "StackupXLayerType",
        "StackupXProperty",
        "StackupXSpan",
        "StackupXStack",
        "StackupXTransmissionLine",
    ),
    "altium_netlist_options": ("NetlistOptions",),
    "altium_pcb_component": ("AltiumPcbComponent",),
    "altium_pcb_ipc2581_writer": ("write_ipc2581",),
    "altium_pcb_mask_paste_rules": (
        "get_pad_mask_expansion_iu",
        "get_via_mask_expansion_iu",
        "get_pad_paste_expansion_iu",
        "has_pad_paste_opening",
        "is_pad_solder_mask_only",
        "should_force_pad_copper_render",
    ),
    "altium_pcb_rule": ("AltiumPcbRule",),
    "altium_pcb_special_strings": (
        "normalize_project_parameters",
        "substitute_pcb_special_strings",
    ),
    "altium_pnp_position": (
        "PnpPositionMode",
        "PNP_POSITION_MODE_ALTIUM_PICK_PLACE",
        "PNP_POSITION_MODE_COMPONENT_ORIGIN",
        "PNP_POSITION_MODES",
        "normalize_pnp_position_mode",
    ),
    "altium_prjpcb": (
        "AltiumPrjPcb",
        "AltiumPrjPcbClassGenerationOptions",
        "AltiumPrjPcbDocumentClassGenerationOptions",
        "AltiumPrjPcbOutJob",
    ),
    "altium_prjscr": ("AltiumPrjScr",),
    "altium_record_types": (
        "PcbLayer",
        "SchRecordType",
        "rgb_to_win32_color",
    ),
    "altium_pcb_layer_ref": (
        "PcbLayerFamily",
        "PcbLayerLike",
        "PcbLayerRef",
        "PcbLayerRegistry",
        "PcbLayerRegistryEntry",
        "PcbLayerResolutionError",
        "PcbPrimitiveLayerState",
        "PcbSignalLayerKind",
        "coerce_pcb_layer_ref",
        "resolve_pcb_primitive_layer_state",
    ),
    "altium_resolved_layer_stack": (
        "ResolvedLayer",
        "ResolvedLayerEnvelopeRow",
        "ResolvedLayerStack",
        "ResolvedStackEnvelope",
        "resolved_layer_stack_from_board",
        "resolved_layer_stack_from_pcbdoc",
    ),
    "altium_schlib": ("AltiumSymbol",),
    "altium_schlib_merger": (
        "merge_schlibs",
        "merge_directory",
    ),
    "altium_symbol_transform": (
        "transform_point_to_symbol_space",
        "transform_point_to_schematic_space",
        "transform_pin_orientation",
        "transform_pin_orientation_to_schematic",
        "to_symbol_space",
        "normalize_rectangle_coords",
        "to_schematic_space",
    ),
    "altium_text_to_polygon": (
        "GlyphPolygon",
        "TextPolygonResult",
        "StrokeTextResult",
        "TrueTypeTextRenderer",
        "canonicalize_stroke_font_type",
        "stroke_font_type_from_label",
        "StrokeTextRenderer",
        "BarcodeRenderer",
        "render_pcb_text",
    ),
}


def _mark_declared_public_surfaces() -> None:
    """
    Mark the declared release surface as public API.
    """
    package = sys.modules[__name__]
    for name in __all__:
        public_api(getattr(package, name))

    for module_name, names in _EXTRA_PUBLIC_SURFACES.items():
        module = importlib.import_module(f".{module_name}", __name__)
        for name in names:
            public_api(getattr(module, name))


_LAZY_PUBLIC_EXPORTS = {
    # High-level parsers
    "AltiumDesign": "altium_design",
    "AltiumCompileDiagnostic": "altium_compiled_design_model",
    "AltiumCompiledAnnotationState": "altium_compiled_design_model",
    "AltiumCompiledComponent": "altium_compiled_design_model",
    "AltiumCompiledDesign": "altium_compiled_design_model",
    "AltiumCompiledDesignSummary": "altium_compiled_design_model",
    "AltiumCompiledSchematicGraph": "altium_compiled_schematic_graph",
    "AltiumPhysicalPageMetadata": "altium_compiled_schematic_graph",
    "AltiumCompiledLogicalDocument": "altium_compiled_design_model",
    "AltiumCompiledNet": "altium_compiled_design_model",
    "AltiumCompiledNetEndpoint": "altium_compiled_design_model",
    "AltiumCompiledNetItem": "altium_compiled_design_model",
    "AltiumCompiledNetTerminal": "altium_compiled_design_model",
    "AltiumCompiledPhysicalDocument": "altium_compiled_design_model",
    "AltiumCompiledPhysicalSheetSymbol": "altium_compiled_design_model",
    "AltiumCompiledSheetSymbol": "altium_compiled_design_model",
    "AltiumProjectCompileOptions": "altium_compiled_design_model",
    "AltiumSchLib": "altium_schlib",
    "AltiumPcbDoc": "altium_pcbdoc",
    "PcbDocBuilder": "altium_pcbdoc_builder",
    "PcbCustomPadLayerShapeSpec": "altium_pcbdoc_builder",
    "AltiumPcbLib": "altium_pcblib",
    "AltiumPcbFootprint": "altium_pcblib",
    "AltiumPcbLibPrimitiveParameterGroup": "altium_pcblib",
    "AltiumSchDoc": "altium_schdoc",
    # Draftsman
    "AltiumDraftsmanDocument": "altium_draftsman",
    "AltiumDraftsmanPage": "altium_draftsman",
    "AltiumDraftsmanItem": "altium_draftsman",
    "AltiumDraftsmanNote": "altium_draftsman",
    "AltiumDraftsmanNoteElement": "altium_draftsman",
    "AltiumDraftsmanPicture": "altium_draftsman",
    "AltiumDraftsmanText": "altium_draftsman",
    "AltiumDraftsmanDocumentOptions": "altium_draftsman",
    "DraftsmanColor": "altium_draftsman",
    "DraftsmanFontDecoration": "altium_draftsman",
    "DraftsmanFontStyle": "altium_draftsman",
    "DraftsmanHorizontalAlignment": "altium_draftsman",
    "DraftsmanMargin": "altium_draftsman",
    "DraftsmanNoteBorderStyle": "altium_draftsman",
    "DraftsmanPoint": "altium_draftsman",
    "DraftsmanRect": "altium_draftsman",
    "DraftsmanSize": "altium_draftsman",
    "DraftsmanStandardSheetSize": "altium_draftsman",
    "DraftsmanVerticalAlignment": "altium_draftsman",
    # Layer-stack authoring and interchange
    "AltiumComponentPlacement": "altium_layer_stack_document",
    "AltiumCopperMaterialSpec": "altium_layer_stack_document",
    "AltiumDielectricLayerKind": "altium_layer_stack_document",
    "AltiumDielectricMaterialSpec": "altium_layer_stack_document",
    "AltiumLayerPair": "altium_layer_stack_document",
    "AltiumLayerStackDocument": "altium_layer_stack_document",
    "AltiumRigidCopperLayerSpec": "altium_layer_stack_document",
    "AltiumRigidDielectricLayerSpec": "altium_layer_stack_document",
    "AltiumRigidStackRowSpec": "altium_layer_stack_document",
    "AltiumStackupRoughnessModel": "altium_layer_stack_document",
    "AltiumStackupSettings": "altium_layer_stack_document",
    "AltiumStackupType": "altium_layer_stack_document",
    "AltiumImpedanceProfileSpec": "altium_layer_stack_document",
    "AltiumTransmissionLineSpec": "altium_layer_stack_document",
    "AltiumStackupDocument": "altium_stackup",
    "StackupTextFeature": "altium_stackup",
    "AltiumStackupXDocument": "altium_stackupx",
    "StackupXBranch": "altium_stackupx",
    "StackupXBranchSection": "altium_stackupx",
    "StackupXBranchSectionStack": "altium_stackupx",
    "StackupXFeature": "altium_stackupx",
    "StackupXFeatureId": "altium_stackupx",
    "StackupXImpedanceProfile": "altium_stackupx",
    "StackupXLayer": "altium_stackupx",
    "StackupXLayerType": "altium_stackupx",
    "StackupXProperty": "altium_stackupx",
    "StackupXSpan": "altium_stackupx",
    "StackupXStack": "altium_stackupx",
    "StackupXTransmissionLine": "altium_stackupx",
}


def __getattr__(name: str) -> Any:
    """
    Lazy import for modules that cause circular imports if loaded eagerly.
    """
    module_name = _LAZY_PUBLIC_EXPORTS.get(name)
    if module_name is not None:
        module = importlib.import_module(f".{module_name}", __name__)
        value = getattr(module, name)
        public_api(value)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_mark_declared_public_surfaces()
