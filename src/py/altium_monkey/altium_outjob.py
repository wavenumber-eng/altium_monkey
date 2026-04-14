"""
Altium OutJob (Output Job Configuration) File Builder

Altium output job files (.OutJob) are INI-style configuration files that define
output generators, mediums, and paths. This module provides a composable builder
API to programmatically create OutJob files for:

- Gerber fabrication files (RS-274X)
- NC Drill files (Excellon)
- ODB++ manufacturing data
- Schematic/PCB PDF documentation
- Pick & Place files
- STEP/Parasolid 3D export
- IPC-2581 manufacturing data

File Format:
- INI-style with [Section] headers
- Key=Value pairs within sections
- Sections: [OutputJobFile], [OutputGroup1], [PublishSettings], [GeneratedFilesSettings]

Project Variable Substitution:
    Altium path fields support project variable expressions. Values starting with
    '=' are evaluated as expressions at generation time. Use altium_expr() and
    altium_literal() helpers to build these expressions cleanly.

    # Composable: build custom combinations
    outjob = AltiumOutJob.create_minimal("outputs")
    gerber_mid = outjob.add_generated_files_medium("GERBERS", "../reference_output/gerbers")
    drill_mid = outjob.add_generated_files_medium("DRILLS", "../reference_output/drills")
    outjob.add_gerber("test.PcbDoc", enabled_medium=gerber_mid)
    outjob.add_nc_drill("test.PcbDoc", enabled_medium=drill_mid)
    outjob.to_outjob("test.OutJob")

    # With project variable substitution
    outjob = AltiumOutJob.create_minimal("production")
    mid = outjob.add_publish_medium(
        "PCB_FAB_PDF",
        output_dir="../../mixdowns",
        path_media=altium_expr("PCB_MIXDOWN", altium_literal("\\PCB")),
        filename_special=altium_expr(
            altium_literal("PCB"), "PCB_PART_NUMBER",
            altium_literal("__FAB__"), "PCB_MIXDOWN",
        ),
    )
    outjob.add_gerber("board.PcbDoc", enabled_medium=mid)

    # Public typed output specs are composable via add_output_spec()
    # and profile-specific presets can be defined in separate modules.
"""

import configparser
import re
from dataclasses import dataclass
from pathlib import Path

from .altium_pcb_enums import (
    PcbV7SavedLayerId,
    pcb_internal_plane_v7_saved_layer_ids,
    pcb_mechanical_v7_saved_layer_ids,
    pcb_signal_v7_saved_layer_ids,
)

__all__ = [
    "altium_literal",
    "altium_expr",
    "OutJobConfigRecord",
    "OutJobOutputSpec",
    "IPC2581Output",
    "GerberOutput",
    "GerberX2Output",
    "NCDrillOutput",
    "ODBOutput",
    "PickPlaceOutput",
    "WireListNetlistOutput",
    "BomPartTypeOutput",
    "ExportStepOutput",
    "SchematicPrintOutput",
    "PcbDrawingOutput",
    "AltiumOutJob",
]


# ------------------------------------------------------------------ #
# Project variable expression helpers
# ------------------------------------------------------------------ #

def altium_literal(text: str) -> str:
    """
    Wrap a literal string for use in an Altium path expression.
    
        Returns the text wrapped in single quotes, as required by Altium's
        expression evaluator.
    
        Args:
            text: Literal text (e.g. a path separator or fixed prefix).
    
        Returns:
            Single-quoted string, e.g. ``'\\PCB'``.

            altium_literal("\\PCB")   # -> "'\\PCB'"
    """
    return f"'{text}'"


def altium_expr(*parts: str) -> str:
    """
    Build an Altium project variable expression for path/filename fields.
    
        In Altium OutJob files, values starting with ``=`` are evaluated as
        expressions at output generation time. This function combines variable
        names and quoted literals with ``+`` concatenation and prefixes the
        result with ``=``.
    
        Bare strings are treated as project variable names. Use
        :func:`altium_literal` to wrap literal path segments.
    
        Args:
            parts: Variable names (bare) and/or quoted literals from
                :func:`altium_literal`.
    
        Returns:
            Expression string prefixed with ``=``.
    
            # Simple variable reference
            altium_expr("PCB_MIXDOWN")
            # -> "=PCB_MIXDOWN"
    
            # Variable + literal path segment
            altium_expr("PCB_MIXDOWN", altium_literal("\\PCB"))
            # -> "=PCB_MIXDOWN + '\\PCB'"
    
            # Multiple variables with literal separators
            altium_expr(
                "PCB_PART_NUMBER", altium_literal("__"),
                "PCB_CODENAME", altium_literal("__"),
                "PCB_MIXDOWN",
            )
            # -> "=PCB_PART_NUMBER + '__' + PCB_CODENAME + '__' + PCB_MIXDOWN"
    """
    if not parts:
        return ''
    return '=' + ' + '.join(parts)


def _is_altium_expr(value: str) -> bool:
    """
    Check if a string is an Altium variable expression (starts with =).
    """
    return isinstance(value, str) and value.startswith('=')


def _normalize_path(path: str, is_directory: bool = False) -> str:
    """
    Normalize a path to Altium's Windows-style format.
    
        Altium requires backslashes, and directory paths must end with backslash.
        Variable expressions (starting with '=') are passed through unchanged.
    """
    if not path:
        return path
    # Variable expressions are passed through unchanged
    if _is_altium_expr(path):
        return path
    normalized = path.replace('/', '\\')
    if is_directory and normalized and not normalized.endswith('\\'):
        normalized += '\\'
    return normalized


def _bool_str(value: bool) -> str:
    return "True" if value else "False"


def _default_gerber_plot_layers() -> str:
    # Emit a broad "all layers on" selection by default.
    # Altium will skip layers that do not exist for a given board.
    layer_ids: list[int] = [
        *pcb_signal_v7_saved_layer_ids(),
        *pcb_internal_plane_v7_saved_layer_ids(),
        int(PcbV7SavedLayerId.TOP_OVERLAY),
        int(PcbV7SavedLayerId.BOTTOM_OVERLAY),
        int(PcbV7SavedLayerId.TOP_PASTE),
        int(PcbV7SavedLayerId.BOTTOM_PASTE),
        int(PcbV7SavedLayerId.TOP_SOLDER),
        int(PcbV7SavedLayerId.BOTTOM_SOLDER),
        int(PcbV7SavedLayerId.DRILL_GUIDE),
        int(PcbV7SavedLayerId.KEEPOUT),
        int(PcbV7SavedLayerId.DRILL_DRAWING),
        int(PcbV7SavedLayerId.TOP_PAD_MASTER),
        int(PcbV7SavedLayerId.BOTTOM_PAD_MASTER),
        *pcb_mechanical_v7_saved_layer_ids(count=32),
    ]

    seen: set[int] = set()
    ordered_unique = [lid for lid in layer_ids if not (lid in seen or seen.add(lid))]
    serialized = ",".join(f"{lid}~1" for lid in ordered_unique)
    return f"SerializeLayerHash.Version~2,ClassName~TPlotLayerStateArray,{serialized}"


_DEFAULT_SCH_PAGE_OPTIONS = (
    "Record=PageOptions|CenterHorizontal=True|CenterVertical=True|PrintScale=1.00"
    "|XCorrection=1.00|YCorrection=1.00|PrintKind=0|BorderSize=5000000|LeftOffset=0"
    "|BottomOffset=0|Orientation=2|PaperLength=1000|PaperWidth=1000|Scale=100"
    "|PaperSource=7|PrintQuality=-3|MediaType=1|DitherType=10|PrintScaleMode=0"
    "|PaperKind=Letter|PaperIndex=1"
)

_DEFAULT_PCB_PAGE_OPTIONS = (
    "Record=PageOptions|CenterHorizontal=True|CenterVertical=True|PrintScale=1.00"
    "|XCorrection=1.00|YCorrection=1.00|PrintKind=1|BorderSize=5000000|LeftOffset=0"
    "|BottomOffset=0|Orientation=2|PaperLength=1000|PaperWidth=1000|Scale=100"
    "|PaperSource=7|PrintQuality=-3|MediaType=1|DitherType=10|PrintScaleMode=1"
    "|PaperKind=Letter|PaperIndex=1"
)

_DEFAULT_BOM_GENERAL_PREFIX = (
    "OpenExported=False|AddToProject=False|ReportBOMViolationsInMessages=False"
    "|ForceFit=False|NotFitted=False|Database=False|DatabasePriority=False"
    "|IncludePcbData=False|IncludeVaultData=False|IncludeCloudData=False"
    "|IncludeDocumentData=True|IncludeAlternatives=False|ShowExportOptions=True"
    "|TemplateFilename=|TemplateVaultGuid=|TemplateItemGuid=|TemplateRevisionGuid="
)

_DEFAULT_BOM_GENERAL_SUFFIX = (
    "|FormWidth=1200|FormHeight=710|SupplierProdQty=1"
    "|SupplierAutoQty=False|SupplierUseCachedPricing=False|SupplierCurrency=USD"
    "|SolutionsPerItem=1|SuppliersPerSolution=1|BomSetName="
)

_DEFAULT_BOM_VISIBLE_ORDER = (
    "Comment=120|Description=120|LibRef=120|Quantity=120|Designator=100"
)


def _build_bom_general_config(*, batch_mode: int, view_type: int) -> str:
    return (
        f"{_DEFAULT_BOM_GENERAL_PREFIX}"
        f"|BatchMode={batch_mode}"
        f"{_DEFAULT_BOM_GENERAL_SUFFIX}"
        f"|ViewType={view_type}"
    )


@dataclass(frozen=True)
class OutJobConfigRecord:
    """
    Strongly typed representation of an OutJob config payload.
    """

    record_name: str | None = None
    fields: tuple[tuple[str, str], ...] = ()
    leading_separator: bool = False

    def to_string(self) -> str:
        parts: list[str] = []
        if self.leading_separator:
            parts.append("")
        if self.record_name:
            parts.append(f"Record={self.record_name}")
        for key, value in self.fields:
            parts.append(f"{key}={value}")
        return "|".join(parts)


@dataclass(frozen=True)
class OutJobOutputSpec:
    """
    Output specification consumed by AltiumOutJob.add_output_spec().
    """

    output_type: str
    output_name: str
    category: str
    document_path: str = ""
    variant_name: str = ""
    page_options: str = ""
    configuration_items: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class IPC2581Output:
    """
    Typed IPC-2581 output definition.
    """

    document_path: str = ""
    output_name: str = "IPC-2581B"
    version: str = "B"
    units: str = "Metric"
    precision: int = 6
    wise_support: bool = False
    merge_net_tie_nets: bool = False
    oem_design_number_ref: str = "DesignItemID"
    different_footprints: bool = True

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        config = OutJobConfigRecord(
            record_name="IPC2581View",
            fields=(
                ("IPC2581Version", self.version),
                ("MeasurementSystem", self.units),
                ("FloatingPointPrecision", str(self.precision)),
                ("IsWiseSupport", _bool_str(self.wise_support)),
                ("MergeNetTieNets", _bool_str(self.merge_net_tie_nets)),
                ("OEMDesignNumberRef", self.oem_design_number_ref),
                ("DifferentFootprints", _bool_str(self.different_footprints)),
                ("DocumentPath", doc),
            ),
        )
        return OutJobOutputSpec(
            output_type="IPC2581",
            output_name=self.output_name,
            category="Fabrication",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class GerberOutput:
    """
    Typed Gerber (RS-274X) output definition.
    """

    document_path: str = ""
    output_name: str = "Gerber Files"
    units: str = "Imperial"
    decimals: int = 5
    embed_apertures: bool = True
    software_arcs: bool = False
    flash_pad_shapes: bool = True
    generate_drc_rules_file: bool = True
    generate_relief_shapes: bool = True
    generate_reports: bool = True
    optimize_change_location_commands: bool = True
    sorted_output: bool = False
    origin_position: str = "Relative"
    output_format: str = "Different"
    plot_layers: str = ""

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        layers = self.plot_layers or _default_gerber_plot_layers()
        config = OutJobConfigRecord(
            record_name="GerberView",
            fields=(
                ("EmbeddedApertures", _bool_str(self.embed_apertures)),
                ("FlashPadShapes", _bool_str(self.flash_pad_shapes)),
                ("GenerateDRCRulesFile", _bool_str(self.generate_drc_rules_file)),
                ("GenerateReliefShapes", _bool_str(self.generate_relief_shapes)),
                ("GenerateReports", _bool_str(self.generate_reports)),
                ("GerberUnit", self.units),
                ("NumberOfDecimals", str(self.decimals)),
                ("OptimizeChangeLocationCommands", _bool_str(self.optimize_change_location_commands)),
                ("OriginPosition", self.origin_position),
                ("OutputFormat", self.output_format),
                ("Plot.Set", layers),
                ("SoftwareArcs", _bool_str(self.software_arcs)),
                ("Sorted", _bool_str(self.sorted_output)),
                ("DocumentPath", doc),
            ),
        )
        return OutJobOutputSpec(
            output_type="Gerber",
            output_name=self.output_name,
            category="Fabrication",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class GerberX2Output:
    """
    Typed Gerber X2 output definition.
    """

    document_path: str = ""
    output_name: str = "Gerber X2 Files"
    units: str = "Metric"
    decimals: int = 5
    embed_apertures: bool = True
    software_arcs: bool = False
    flash_pad_shapes: bool = True
    generate_drc_rules_file: bool = True
    generate_relief_shapes: bool = True
    generate_reports: bool = True
    optimize_change_location_commands: bool = True
    sorted_output: bool = False
    origin_position: str = "Relative"
    output_format: str = "Different"
    plot_layers: str = ""

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        layers = self.plot_layers or _default_gerber_plot_layers()
        config = OutJobConfigRecord(
            record_name="GerberX2View",
            fields=(
                ("EmbeddedApertures", _bool_str(self.embed_apertures)),
                ("FlashPadShapes", _bool_str(self.flash_pad_shapes)),
                ("GenerateDRCRulesFile", _bool_str(self.generate_drc_rules_file)),
                ("GenerateReliefShapes", _bool_str(self.generate_relief_shapes)),
                ("GenerateReports", _bool_str(self.generate_reports)),
                ("GerberUnit", self.units),
                ("NumberOfDecimals", str(self.decimals)),
                ("OptimizeChangeLocationCommands", _bool_str(self.optimize_change_location_commands)),
                ("OriginPosition", self.origin_position),
                ("OutputFormat", self.output_format),
                ("Plot.Set", layers),
                ("SoftwareArcs", _bool_str(self.software_arcs)),
                ("Sorted", _bool_str(self.sorted_output)),
                ("DocumentPath", doc),
            ),
        )
        return OutJobOutputSpec(
            output_type="Gerber X2",
            output_name=self.output_name,
            category="Fabrication",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class NCDrillOutput:
    """
    Typed NC Drill output definition.
    """

    document_path: str = ""
    output_name: str = "NC Drill Files"
    units: str = "Imperial"
    decimals: int = 5
    number_of_units: int = 2
    optimize_change_location_commands: bool = True
    origin_position: str = "Relative"
    zeroes_mode: str = "SuppressTrailingZeroes"
    generate_board_edge_rout: bool = False
    generate_drilled_slots_g85: bool = False
    generate_eia_drill_file: bool = False
    separate_plated: bool = False
    generate_separate_via_type_files: bool | None = None
    generate_tools_by_drill_symbols: bool | None = None
    board_edge_rout_tool_dia: int = 2000000

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        fields: list[tuple[str, str]] = [
            ("Units", self.units),
            ("NumberOfDecimals", str(self.decimals)),
            ("NumberOfUnits", str(self.number_of_units)),
            ("OptimizeChangeLocationCommands", _bool_str(self.optimize_change_location_commands)),
            ("OriginPosition", self.origin_position),
            ("ZeroesMode", self.zeroes_mode),
            ("GenerateBoardEdgeRout", _bool_str(self.generate_board_edge_rout)),
            ("GenerateDrilledSlotsG85", _bool_str(self.generate_drilled_slots_g85)),
            ("GenerateEIADrillFile", _bool_str(self.generate_eia_drill_file)),
            ("GenerateSeparatePlatedNonPlatedFiles", _bool_str(self.separate_plated)),
        ]
        if self.generate_separate_via_type_files is not None:
            fields.append(("GenerateSeparateViaTypeFiles", _bool_str(self.generate_separate_via_type_files)))
        if self.generate_tools_by_drill_symbols is not None:
            fields.append(("GenerateToolsByDrillSymbols", _bool_str(self.generate_tools_by_drill_symbols)))
        fields.extend(
            (
                ("BoardEdgeRoutToolDia", str(self.board_edge_rout_tool_dia)),
                ("DocumentPath", doc),
            )
        )
        config = OutJobConfigRecord(record_name="DrillView", fields=tuple(fields))
        return OutJobOutputSpec(
            output_type="NC Drill",
            output_name=self.output_name,
            category="Fabrication",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class ODBOutput:
    """
    Typed ODB++ output definition.
    """

    document_path: str = ""
    output_name: str = "ODB++ Files"

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        config = OutJobConfigRecord(
            record_name="ODBView",
            fields=(
                ("ExportPositivePlaneLayers", "False"),
                ("GenerateDRCRulesFile", "False"),
                ("IncludeUnconnectedMidLayerPads", "False"),
                ("ObjsInsideBoardOutlineOnly", "False"),
                ("ODBProfileLayer", "-1000"),
                ("PlotTopLayerPlot", "True"),
                ("PlotBottomLayerPlot", "True"),
                ("PlotTopOverlayPlot", "True"),
                ("PlotBottomOverlayPlot", "True"),
                ("PlotTopPastePlot", "True"),
                ("PlotBottomPastePlot", "True"),
                ("PlotTopSolderPlot", "True"),
                ("PlotBottomSolderPlot", "True"),
                ("PlotKeepOutLayerPlot", "True"),
                ("DocumentPath", doc),
            ),
        )
        return OutJobOutputSpec(
            output_type="ODB",
            output_name=self.output_name,
            category="Fabrication",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class PickPlaceOutput:
    """
    Typed Pick and Place output definition.
    """

    document_path: str = ""
    output_name: str = "Pick and Place"
    units: str = "Metric"
    generate_csv_format: bool = True
    generate_text_format: bool = False
    show_units: bool = False
    separator: str = "."
    exclude_filter_param: bool | None = None
    include_variations: bool | None = None
    include_standard_no_bom: bool | None = None
    filter_text: str | None = None
    filter_active: bool | None = None
    y_flip: bool = False
    different_footprints: bool = False
    columns: tuple[str, ...] = ()

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        fields: list[tuple[str, str]] = [
            ("Units", self.units),
            ("GenerateCSVFormat", _bool_str(self.generate_csv_format)),
            ("GenerateTextFormat", _bool_str(self.generate_text_format)),
            ("ShowUnits", _bool_str(self.show_units)),
            ("Separator", self.separator),
            ("YFlip", _bool_str(self.y_flip)),
            ("DifferentFootprints", _bool_str(self.different_footprints)),
        ]
        if self.exclude_filter_param is not None:
            fields.append(("ExcludeFilterParam", _bool_str(self.exclude_filter_param)))
        if self.include_variations is not None:
            fields.append(("IncludeVariations", _bool_str(self.include_variations)))
        if self.include_standard_no_bom is not None:
            fields.append(("IncludeStandardNoBOM", _bool_str(self.include_standard_no_bom)))
        if self.filter_text is not None:
            fields.append(("Filter", self.filter_text))
        if self.filter_active is not None:
            fields.append(("FilterActive", _bool_str(self.filter_active)))
        for idx, column in enumerate(self.columns, start=1):
            fields.append((f"Column#{idx}", column))
        fields.append(("DocumentPath", doc))

        config = OutJobConfigRecord(record_name="PickPlaceView", fields=tuple(fields))
        return OutJobOutputSpec(
            output_type="Pick Place",
            output_name=self.output_name,
            category="Assembly",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class WireListNetlistOutput:
    """
    Typed wirelist netlist output definition.
    """

    document_path: str = ""
    output_name: str = "NETLIST"
    units: str = "Imperial"
    generate_csv_format: bool = False
    generate_text_format: bool = True
    show_units: bool = False
    separator: str = "."
    exclude_filter_param: bool = False
    include_variations: bool = False
    include_standard_no_bom: bool = False
    filter_text: str = " "
    filter_active: bool = False
    y_flip: bool = False
    different_footprints: bool = False
    columns: tuple[str, ...] = ()

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        fields: list[tuple[str, str]] = [
            ("Units", self.units),
            ("GenerateCSVFormat", _bool_str(self.generate_csv_format)),
            ("GenerateTextFormat", _bool_str(self.generate_text_format)),
            ("ShowUnits", _bool_str(self.show_units)),
            ("Separator", self.separator),
            ("ExcludeFilterParam", _bool_str(self.exclude_filter_param)),
            ("IncludeVariations", _bool_str(self.include_variations)),
            ("IncludeStandardNoBOM", _bool_str(self.include_standard_no_bom)),
            ("Filter", self.filter_text),
            ("FilterActive", _bool_str(self.filter_active)),
            ("YFlip", _bool_str(self.y_flip)),
            ("DifferentFootprints", _bool_str(self.different_footprints)),
        ]
        for idx, column in enumerate(self.columns, start=1):
            fields.append((f"Column#{idx}", column))
        fields.append(("DocumentPath", doc))

        config = OutJobConfigRecord(
            record_name=None,
            fields=tuple(fields),
            leading_separator=True,
        )
        return OutJobOutputSpec(
            output_type="WireListNetlist",
            output_name=self.output_name,
            category="Netlist",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class BomPartTypeOutput:
    """
    Typed BOM report output definition.
    """

    output_name: str = "CSV-BOM"
    page_options: str = _DEFAULT_PCB_PAGE_OPTIONS
    column_name_format: str = "CaptionAsName"
    general: str | None = None
    batch_mode: int = 0
    view_type: int = 1
    group_order: str = ""
    sort_order: str = "Comment=Up"
    visible_order: str = _DEFAULT_BOM_VISIBLE_ORDER
    visible_order_flat: str = _DEFAULT_BOM_VISIBLE_ORDER

    def to_spec(self) -> OutJobOutputSpec:
        general = self.general
        if general is None:
            general = _build_bom_general_config(
                batch_mode=self.batch_mode,
                view_type=self.view_type,
            )
        return OutJobOutputSpec(
            output_type="BOM_PartType",
            output_name=self.output_name,
            category="Report",
            page_options=self.page_options,
            configuration_items=(
                ("ColumnNameFormat", self.column_name_format),
                ("General", general),
                ("GroupOrder", self.group_order),
                ("SortOrder", self.sort_order),
                ("VisibleOrder", self.visible_order),
                ("VisibleOrder_Flat", self.visible_order_flat),
            ),
        )


@dataclass(frozen=True)
class ExportStepOutput:
    """
    Typed STEP export output definition.
    """

    document_path: str = ""
    output_name: str = "Export STEP"

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        config = OutJobConfigRecord(
            record_name="ExportSTEPView",
            fields=(
                ("ExportComponentOptions", "0"),
                ("ExportModelsOption", "2"),
                ("ExportHolesOption", "0"),
                ("CanSelectPrimitives", "False"),
                ("IncludeMechanicalPadHoles", "True"),
                ("IncludeElectricalPadHoles", "True"),
                ("IncludeFreePadHoles", "True"),
                ("ExportFoldedBoard", "False"),
                ("ExportCopperOption", "0"),
                ("ExportAsSinglePart", "False"),
                ("SkipFreeBodies", "False"),
                ("SkipHidden", "False"),
                ("DocumentPath", doc),
            ),
        )
        return OutJobOutputSpec(
            output_type="ExportSTEP",
            output_name=self.output_name,
            category="Export",
            document_path=doc,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class SchematicPrintOutput:
    """
    Typed schematic print output definition.
    """

    document_path: str = ""
    output_name: str = "Schematic Prints"
    page_options: str = _DEFAULT_SCH_PAGE_OPTIONS

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        config = OutJobConfigRecord(
            record_name="SchPrintView",
            fields=(
                ("ShowNoERC", "True"),
                ("ShowParamSet", "True"),
                ("ShowProbe", "True"),
                ("ShowBlanket", "True"),
                ("NoERCSymbolsToShow", '"Thin Cross","Thick Cross","Small Cross",Checkbox,Triangle'),
                ("ShowNote", "True"),
                ("ShowNoteCollapsed", "True"),
                ("ShowOpenEnds", "True"),
                ("ExpandDesignator", "True"),
                ("ExpandNetLabel", "False"),
                ("ExpandPort", "False"),
                ("ExpandSheetNum", "False"),
                ("ExpandDocNum", "False"),
                ("PrintArea", "0"),
                ("PrintAreaRect.X1", "0"),
                ("PrintAreaRect.Y1", "0"),
                ("PrintAreaRect.X2", "0"),
                ("PrintAreaRect.Y2", "0"),
                ("DocumentPath", doc),
            ),
        )
        return OutJobOutputSpec(
            output_type="Schematic Print",
            output_name=self.output_name,
            category="Documentation",
            document_path=doc,
            page_options=self.page_options,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


@dataclass(frozen=True)
class PcbDrawingOutput:
    """
    Typed PCB drawing output definition.
    """

    document_path: str = ""
    output_name: str = "PCB Drawing"
    page_options: str = _DEFAULT_PCB_PAGE_OPTIONS

    def to_spec(self) -> OutJobOutputSpec:
        doc = _normalize_path(self.document_path)
        config = OutJobConfigRecord(
            record_name=None,
            fields=(("DocumentPath", doc),),
        )
        return OutJobOutputSpec(
            output_type="PCBDrawing",
            output_name=self.output_name,
            category="Documentation",
            document_path=doc,
            page_options=self.page_options,
            configuration_items=(("OutputConfigurationParameter1", config.to_string()),),
        )


class AltiumOutJob:
    """
    Parser and builder for Altium .OutJob output job files.
    
        The OutJob file has three main sections:
        - [OutputGroup1]: Defines mediums (where output goes) and output types (what to generate)
        - [PublishSettings]: PDF/publish medium configuration (paths, filenames, options)
        - [GeneratedFilesSettings]: Generated files medium configuration (paths)
    
        Each output type can be independently enabled/disabled per medium.
    """

    def __init__(self) -> None:
        self.config = configparser.ConfigParser()
        self.config.optionxform = str  # Preserve case (critical for Altium)
        self._medium_count = 0
        self._output_count = 0
        # Track medium names -> indices for the enable flag system
        self._medium_indices: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # File I/O
    # ------------------------------------------------------------------ #

    @classmethod
    def from_outjob(cls, filepath: Path | str) -> 'AltiumOutJob':
        """
        Load an existing OutJob file.
        """
        filepath = Path(filepath)
        outjob = cls()
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                outjob.config.read_file(f)
        except UnicodeDecodeError:
            with open(filepath, 'r', encoding='latin-1') as f:
                outjob.config.read_file(f)

        # Count existing mediums/outputs so add_* methods continue from the right index
        section = 'OutputGroup1'
        if outjob.config.has_section(section):
            i = 1
            while outjob.config.has_option(section, f'OutputMedium{i}'):
                name = outjob.config.get(section, f'OutputMedium{i}')
                outjob._medium_indices[name] = i
                i += 1
            outjob._medium_count = i - 1

            i = 1
            while outjob.config.has_option(section, f'OutputType{i}'):
                i += 1
            outjob._output_count = i - 1

        return outjob

    def to_outjob(self, filepath: Path | str, *, encoding: str = "latin-1") -> None:
        """
        Write OutJob to file (CRLF line endings).
        
                Args:
                    filepath: Destination .OutJob path.
                    encoding: Text encoding. Defaults to ``latin-1`` for compatibility
                        with Altium-authored OutJob files.
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w', encoding=encoding, newline='\r\n') as f:
            self.config.write(f, space_around_delimiters=False)

    # ------------------------------------------------------------------ #
    # Factory: minimal / convenience
    # ------------------------------------------------------------------ #

    @classmethod
    def create_minimal(cls, name: str = "job") -> 'AltiumOutJob':
        """
        Create a minimal OutJob with empty sections ready for add_* calls.
        """
        outjob = cls()
        c = outjob.config

        c.add_section('OutputJobFile')
        for key in ('Version', 'Caption', 'Description', 'VaultGUID', 'ItemGUID',
                     'ItemHRID', 'RevisionGUID', 'RevisionId', 'VaultHRID',
                     'AutoItemHRID', 'NextRevId', 'FolderGUID',
                     'LifeCycleDefinitionGUID', 'RevisionNamingSchemeGUID'):
            c.set('OutputJobFile', key, '1.0' if key == 'Version' else '')

        c.add_section('OutputGroup1')
        c.set('OutputGroup1', 'Name', f'{name}.OutJob')
        c.set('OutputGroup1', 'Description', '')
        c.set('OutputGroup1', 'VariantName', '[No Variations]')
        c.set('OutputGroup1', 'VariantScope', '1')
        c.set('OutputGroup1', 'CurrentConfigurationName', '')
        c.set('OutputGroup1', 'TargetPrinter', 'Microsoft Print to PDF')
        c.set('OutputGroup1', 'PrinterOptions',
              'Record=PrinterOptions|Copies=1|Duplex=1|TrueTypeOptions=3|Collate=1|PrintJobKind=1|PrintWhat=1')

        c.add_section('PublishSettings')
        c.add_section('GeneratedFilesSettings')

        return outjob

    @classmethod
    def create_pcb_fabrication(
        cls,
        pcbdoc_path: str,
        output_dir: str = "../reference_output",
        name: str = "pcb_fab",
        *,
        gerber: bool = True,
        nc_drill: bool = True,
        odb: bool = False,
    ) -> 'AltiumOutJob':
        """
        Create an OutJob for PCB fabrication outputs (Gerber + Drill + optional ODB++).
        
                Args:
                    pcbdoc_path: Path to the PcbDoc (relative to OutJob location).
                    output_dir: Base output directory (relative to OutJob location).
                    name: OutJob name.
                    gerber: Include Gerber output.
                    nc_drill: Include NC Drill output.
                    odb: Include ODB++ output.
        """
        outjob = cls.create_minimal(name)

        if gerber:
            mid = outjob.add_generated_files_medium(
                "GERBERS", f"{output_dir}/gerbers")
            outjob.add_gerber(pcbdoc_path, enabled_medium=mid)

        if nc_drill:
            mid = outjob.add_generated_files_medium(
                "DRILLS", f"{output_dir}/drills")
            outjob.add_nc_drill(pcbdoc_path, enabled_medium=mid)

        if odb:
            mid = outjob.add_generated_files_medium(
                "ODB++", f"{output_dir}/odb")
            outjob.add_odb(pcbdoc_path, enabled_medium=mid)

        # Don't set TargetOutputMedium - let all mediums run when
        # GenerateReport(Action=Run) is called from the runner script

        return outjob

    @classmethod
    def create_schematic_pdf(
        cls,
        name: str,
        schdoc_path: str,
        output_dir: str = "../reference_output",
        output_filename: str | None = None,
    ) -> 'AltiumOutJob':
        """
        Create an OutJob for schematic PDF export.
        
                Args:
                    name: Base name for the OutJob and output file.
                    schdoc_path: Path to the SchDoc file (relative or absolute).
                    output_dir: Output directory path (relative to OutJob location).
                    output_filename: Output filename without extension (defaults to name).
        """
        outjob = cls.create_minimal(name)
        output_filename = output_filename or name

        mid = outjob.add_publish_medium("pdf", output_dir, output_filename)
        outjob.add_schematic_print(schdoc_path, enabled_medium=mid)
        outjob.config.set('OutputGroup1', 'TargetOutputMedium', 'pdf')

        return outjob

    # ------------------------------------------------------------------ #
    # Medium builders
    # ------------------------------------------------------------------ #

    def add_generated_files_medium(
        self,
        name: str,
        output_dir: str = "",
    ) -> int:
        """
        Add a GeneratedFiles output medium (for Gerber, Drill, ODB++, STEP, etc.).
        
                Args:
                    name: Display name for this medium (e.g. "GERBERS", "DRILLS").
                    output_dir: Output directory (relative to OutJob or absolute).
                        Can be a variable expression from :func:`altium_expr` for
                        dynamic paths based on project variables.
        
                Returns:
                    Medium index (use as enabled_medium param in add_* methods).
        
                Example with project variables::
        
                    mid = outjob.add_generated_files_medium(
                        "GERBERS",
                        output_dir=altium_expr(
                            altium_literal("../../mixdowns"),
                            "PCB_MIXDOWN", altium_literal("\\PCB"),
                        ),
                    )
        """
        self._medium_count += 1
        idx = self._medium_count
        self._medium_indices[name] = idx
        sec = 'OutputGroup1'

        self.config.set(sec, f'OutputMedium{idx}', name)
        self.config.set(sec, f'OutputMedium{idx}_Type', 'GeneratedFiles')

        # GeneratedFilesSettings - _normalize_path passes through expressions unchanged
        output_dir = _normalize_path(output_dir, is_directory=True)
        gs = 'GeneratedFilesSettings'
        self.config.set(gs, f'RelativeOutputPath{idx}', output_dir)
        self.config.set(gs, f'OpenOutputs{idx}', '0')
        self.config.set(gs, f'AddToProject{idx}', '0')
        self.config.set(gs, f'TimestampFolder{idx}', '0')
        self.config.set(gs, f'UseOutputName{idx}', '0')
        self.config.set(gs, f'OpenODBOutput{idx}', '0')
        self.config.set(gs, f'OpenGerberOutput{idx}', '0')
        self.config.set(gs, f'OpenNCDrillOutput{idx}', '0')
        self.config.set(gs, f'OpenIPCOutput{idx}', '0')
        self.config.set(gs, f'EnableReload{idx}', '0')

        return idx

    def add_publish_medium(
        self,
        name: str,
        output_dir: str = "",
        output_filename: str = "",
        *,
        path_media: str = "",
        filename_multi: str = "",
        filename_special: str = "",
    ) -> int:
        """
        Add a Publish output medium (for PDF export).
        
                Args:
                    name: Display name (e.g. "pdf", "PCB_PDF").
                    output_dir: Base output directory (relative to OutJob or absolute).
                    output_filename: Base filename without extension (literal).
                    path_media: Subdirectory path, can be a variable expression from
                        :func:`altium_expr` (e.g. ``altium_expr("PCB_MIXDOWN", altium_literal("\\PCB"))``).
                        Sets both OutputPathMedia and OutputPathMediaValue.
                    filename_multi: Filename for multi-output, can be a variable expression.
                        Used when output generates multiple files (e.g. per-layer Gerber).
                    filename_special: Special filename override, can be a variable expression.
                        Used for complex naming with project variables.
        
                Returns:
                    Medium index.
        
                Example with project variables::
        
                    mid = outjob.add_publish_medium(
                        "PCB_FAB_PDF",
                        output_dir="../../mixdowns",
                        path_media=altium_expr("PCB_MIXDOWN", altium_literal("\\PCB")),
                        filename_special=altium_expr(
                            altium_literal("PCB"), "PCB_PART_NUMBER",
                            altium_literal("__FAB__"), "PCB_MIXDOWN",
                        ),
                    )
        """
        self._medium_count += 1
        idx = self._medium_count
        self._medium_indices[name] = idx
        sec = 'OutputGroup1'

        self.config.set(sec, f'OutputMedium{idx}', name)
        self.config.set(sec, f'OutputMedium{idx}_Type', 'Publish')

        # PublishSettings
        output_dir = _normalize_path(output_dir, is_directory=True)
        ps = 'PublishSettings'
        self.config.set(ps, f'OutputFilePath{idx}', '')
        self.config.set(ps, f'ReleaseManaged{idx}', '0')
        self.config.set(ps, f'OutputBasePath{idx}', output_dir)
        self.config.set(ps, f'OutputPathMedia{idx}', path_media)
        self.config.set(ps, f'OutputPathMediaValue{idx}', path_media)
        self.config.set(ps, f'OutputPathOutputer{idx}', '[Output Type]')
        self.config.set(ps, f'OutputPathOutputerPrefix{idx}', '')
        self.config.set(ps, f'OutputPathOutputerValue{idx}', '')
        self.config.set(ps, f'OutputFileName{idx}', output_filename)
        self.config.set(ps, f'OutputFileNameMulti{idx}', filename_multi)
        self.config.set(ps, f'UseOutputNameForMulti{idx}', '1' if filename_multi else '0')
        self.config.set(ps, f'OutputFileNameSpecial{idx}', filename_special)
        self.config.set(ps, f'OpenOutput{idx}', '0')
        self.config.set(ps, f'PromptOverwrite{idx}', '0')
        self.config.set(ps, f'PublishMethod{idx}', '0')
        self.config.set(ps, f'ZoomLevel{idx}', '50')
        self.config.set(ps, f'FitSCHPrintSizeToDoc{idx}', '1')
        self.config.set(ps, f'FitPCBPrintSizeToDoc{idx}', '1')
        self.config.set(ps, f'GenerateNetsInfo{idx}', '1')
        self.config.set(ps, f'MarkPins{idx}', '1')
        self.config.set(ps, f'MarkNetLabels{idx}', '1')
        self.config.set(ps, f'MarkPortsId{idx}', '1')
        self.config.set(ps, f'GenerateTOC{idx}', '0')
        self.config.set(ps, f'ShowComponentParameters{idx}', '0')
        self.config.set(ps, f'GlobalBookmarks{idx}', '0')
        self.config.set(ps, f'PDFACompliance{idx}', 'Disabled')
        self.config.set(ps, f'PDFVersion{idx}', 'Default')

        return idx

    # ------------------------------------------------------------------ #
    # Output type builders
    # ------------------------------------------------------------------ #

    def add_output_spec(
        self,
        output: OutJobOutputSpec,
        enabled_medium: int | None = None,
        *,
        use_output_index_enable_flag: bool = False,
    ) -> int:
        """
        Add an output from a typed OutJobOutputSpec object.
        """
        return self._add_output_type(
            output_type=output.output_type,
            output_name=output.output_name,
            category=output.category,
            enabled_medium=enabled_medium,
            doc_path=_normalize_path(output.document_path),
            variant=output.variant_name,
            page_options=output.page_options,
            config_items=output.configuration_items,
            use_output_index_enable_flag=use_output_index_enable_flag,
        )

    def _add_output_type(
        self,
        output_type: str,
        output_name: str,
        category: str,
        config_record: str | None = None,
        enabled_medium: int | None = None,
        *,
        doc_path: str = "",
        variant: str = "",
        page_options: str = "",
        config_items: tuple[tuple[str, str], ...] | None = None,
        use_output_index_enable_flag: bool = False,
    ) -> int:
        """
        Add an output type entry to OutputGroup1.
        
                Args:
                    output_type: Altium type string (e.g. "Gerber", "NC Drill").
                    output_name: Display name.
                    category: Category (e.g. "Fabrication", "Documentation").
                    config_record: Pipe-delimited configuration string.
                    enabled_medium: Medium index to enable this output for (None = disabled).
                    doc_path: Document path override.
                    variant: Variant name.
                    page_options: Page options string (for printable outputs).
                    config_items: Optional multi-item configuration payload where each
                        tuple is ``(configuration_name, configuration_value)``.
                    use_output_index_enable_flag: Use output index for per-medium
                        enable value (matches certain Altium-authored templates).
        
                Returns:
                    Output type index.
        """
        self._output_count += 1
        idx = self._output_count
        sec = 'OutputGroup1'

        self.config.set(sec, f'OutputType{idx}', output_type)
        self.config.set(sec, f'OutputName{idx}', output_name)
        self.config.set(sec, f'OutputCategory{idx}', category)
        self.config.set(sec, f'OutputDocumentPath{idx}', doc_path)
        self.config.set(sec, f'OutputVariantName{idx}', variant)

        # Global enable: 1 if any medium is enabled, 0 otherwise
        global_enabled = '1' if enabled_medium else '0'
        self.config.set(sec, f'OutputEnabled{idx}', global_enabled)

        # Per-medium enable flags
        for mid in range(1, self._medium_count + 1):
            if mid == enabled_medium:
                val = str(idx) if use_output_index_enable_flag else '1'
            else:
                val = '0'
            self.config.set(sec, f'OutputEnabled{idx}_OutputMedium{mid}', val)

        self.config.set(sec, f'OutputDefault{idx}', '0')

        if page_options:
            self.config.set(sec, f'PageOptions{idx}', page_options)

        # Configuration
        if config_items:
            for conf_idx, (conf_name, conf_value) in enumerate(config_items, start=1):
                self.config.set(sec, f'Configuration{idx}_Name{conf_idx}', conf_name)
                self.config.set(sec, f'Configuration{idx}_Item{conf_idx}', conf_value)
        elif config_record is not None:
            self.config.set(sec, f'Configuration{idx}_Name1', 'OutputConfigurationParameter1')
            self.config.set(sec, f'Configuration{idx}_Item1', config_record)

        return idx

    def add_gerber(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
        *,
        units: str = "Imperial",
        decimals: int = 5,
        embed_apertures: bool = True,
        software_arcs: bool = False,
        flash_pad_shapes: bool = True,
        plot_layers: str = "",
    ) -> int:
        """
        Add Gerber (RS-274X) output type.
        
                Args:
                    pcbdoc_path: Path to PcbDoc (relative to OutJob, or empty for auto).
                    enabled_medium: Medium index to enable for.
                    units: "Imperial" or "Metric".
                    decimals: Number of decimal places (typically 5 for imperial, 4 for metric).
                    embed_apertures: Embed aperture definitions in Gerber file.
                    software_arcs: Use software arcs (interpolated line segments).
                    flash_pad_shapes: Flash pad shapes vs draw them.
                    plot_layers: Serialized layer plot state. Empty = Altium default (all copper + mask + silk).
        """
        output = GerberOutput(
            document_path=pcbdoc_path,
            units=units,
            decimals=decimals,
            embed_apertures=embed_apertures,
            software_arcs=software_arcs,
            flash_pad_shapes=flash_pad_shapes,
            plot_layers=plot_layers,
        )
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_gerber_x2(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
        *,
        units: str = "Metric",
        decimals: int = 5,
        embed_apertures: bool = True,
        software_arcs: bool = False,
        flash_pad_shapes: bool = True,
        plot_layers: str = "",
    ) -> int:
        """
        Add Gerber X2 output type (IPC-D-356 extended attributes).
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
                    units: "Imperial" or "Metric".
                    decimals: Number of decimal places.
                    embed_apertures: Embed aperture definitions.
                    software_arcs: Use interpolated line segments for arcs.
                    flash_pad_shapes: Flash pad shapes vs draw them.
                    plot_layers: Serialized layer plot state.
        """
        output = GerberX2Output(
            document_path=pcbdoc_path,
            units=units,
            decimals=decimals,
            embed_apertures=embed_apertures,
            software_arcs=software_arcs,
            flash_pad_shapes=flash_pad_shapes,
            plot_layers=plot_layers,
        )
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_nc_drill(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
        *,
        units: str = "Imperial",
        decimals: int = 5,
        separate_plated: bool = False,
    ) -> int:
        """
        Add NC Drill (Excellon) output type.
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
                    units: "Imperial" or "Metric".
                    decimals: Decimal places.
                    separate_plated: Generate separate plated/non-plated files.
        """
        output = NCDrillOutput(
            document_path=pcbdoc_path,
            units=units,
            decimals=decimals,
            separate_plated=separate_plated,
        )
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_odb(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
    ) -> int:
        """
        Add ODB++ output type.
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
        """
        output = ODBOutput(document_path=pcbdoc_path)
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_ipc2581(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
        *,
        version: str = "B",
        units: str = "Metric",
        precision: int = 6,
        different_footprints: bool = True,
    ) -> int:
        """
        Add IPC-2581 output type.
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
                    version: IPC-2581 revision ("B" or "C").
                    units: "Metric" or "Imperial".
                    precision: Floating point decimal precision (1-8).
                    different_footprints: Treat identical footprints as different packages.
        """
        output = IPC2581Output(
            document_path=pcbdoc_path,
            version=version,
            units=units,
            precision=precision,
            different_footprints=different_footprints,
        )
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_pick_place(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
        *,
        units: str = "Metric",
        csv_format: bool = True,
    ) -> int:
        """
        Add Pick & Place output type.
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
                    units: "Metric" or "Imperial".
                    csv_format: Generate CSV format (vs text).
        """
        output = PickPlaceOutput(
            document_path=pcbdoc_path,
            units=units,
            generate_csv_format=csv_format,
        )
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_wirelist_netlist(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
        *,
        units: str = "Imperial",
        csv_format: bool = False,
        columns: tuple[str, ...] = (),
    ) -> int:
        """
        Add WireList netlist output type.
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
                    units: "Metric" or "Imperial".
                    csv_format: Generate CSV format (if False, generate text format).
                    columns: Optional explicit column definitions ("Name:...,Fixed:...,").
        """
        output = WireListNetlistOutput(
            document_path=pcbdoc_path,
            units=units,
            generate_csv_format=csv_format,
            generate_text_format=not csv_format,
            columns=columns,
        )
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_bom_part_type(
        self,
        enabled_medium: int | None = None,
        *,
        output_name: str = "CSV-BOM",
        batch_mode: int = 0,
        view_type: int = 1,
        general: str | None = None,
    ) -> int:
        """
        Add BOM report output type (BOM_PartType).
        
                Args:
                    enabled_medium: Medium index to enable for. Leave as None to keep
                        output disabled in template-style OutJobs.
                    output_name: Display name for this output.
                    batch_mode: Altium BOM export mode selector.
                    view_type: Altium BOM view selector.
                    general: Full General configuration override (optional).
        """
        output = BomPartTypeOutput(
            output_name=output_name,
            batch_mode=batch_mode,
            view_type=view_type,
            general=general,
        )
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_export_step(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
    ) -> int:
        """
        Add STEP 3D export output type.
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
        """
        output = ExportStepOutput(document_path=pcbdoc_path)
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_schematic_print(
        self,
        schdoc_path: str = "",
        enabled_medium: int | None = None,
    ) -> int:
        """
        Add Schematic Print output type (for PDF export).
        
                Args:
                    schdoc_path: Path to SchDoc file, or "[Project Physical Documents]" for all.
                    enabled_medium: Medium index to enable for.
        """
        output = SchematicPrintOutput(document_path=schdoc_path)
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    def add_pcb_drawing(
        self,
        pcbdoc_path: str = "",
        enabled_medium: int | None = None,
        *,
        name: str = "PCB Drawing",
    ) -> int:
        """
        Add PCB Drawing output type (for PDF export of PCB layers).
        
                Args:
                    pcbdoc_path: Path to PcbDoc.
                    enabled_medium: Medium index to enable for.
                    name: Display name for this output.
        """
        output = PcbDrawingOutput(document_path=pcbdoc_path, output_name=name)
        return self.add_output_spec(output.to_spec(), enabled_medium=enabled_medium)

    # ------------------------------------------------------------------ #
    # Mutators
    # ------------------------------------------------------------------ #

    def set_generated_files_path(self, medium_index: int, output_dir: str) -> None:
        """
        Update the output directory for a GeneratedFiles medium.
        """
        output_dir = _normalize_path(output_dir, is_directory=True)
        self.config.set('GeneratedFilesSettings',
                        f'RelativeOutputPath{medium_index}', output_dir)

    def set_publish_path(
        self,
        medium_index: int,
        output_dir: str = "",
        filename: str = "",
        *,
        path_media: str | None = None,
        filename_multi: str | None = None,
        filename_special: str | None = None,
    ) -> None:
        """
        Update path settings for a Publish medium.
        
                Only provided arguments are updated; others are left unchanged.
        
                Args:
                    medium_index: Medium index (from add_publish_medium()).
                    output_dir: Base output directory.
                    filename: Base filename without extension (literal).
                    path_media: Subdirectory path, can be a variable expression.
                    filename_multi: Multi-output filename, can be a variable expression.
                    filename_special: Special filename, can be a variable expression.
        """
        ps = 'PublishSettings'
        if output_dir:
            output_dir = _normalize_path(output_dir, is_directory=True)
            self.config.set(ps, f'OutputBasePath{medium_index}', output_dir)
        if filename:
            self.config.set(ps, f'OutputFileName{medium_index}', filename)
        if path_media is not None:
            self.config.set(ps, f'OutputPathMedia{medium_index}', path_media)
            self.config.set(ps, f'OutputPathMediaValue{medium_index}', path_media)
        if filename_multi is not None:
            self.config.set(ps, f'OutputFileNameMulti{medium_index}', filename_multi)
            self.config.set(ps, f'UseOutputNameForMulti{medium_index}',
                            '1' if filename_multi else '0')
        if filename_special is not None:
            self.config.set(ps, f'OutputFileNameSpecial{medium_index}', filename_special)

    def set_document_path(self, output_index: int, doc_path: str) -> None:
        """
        Update the DocumentPath in an output type's configuration.
        
                Args:
                    output_index: Output type index (1-based).
                    doc_path: New document path.
        """
        doc_path = _normalize_path(doc_path)
        sec = 'OutputGroup1'
        self.config.set(sec, f'OutputDocumentPath{output_index}', doc_path)

        item_prefix = f'Configuration{output_index}_Item'
        for key in list(self.config.options(sec)):
            if not key.startswith(item_prefix):
                continue
            current = self.config.get(sec, key, fallback="")
            if "DocumentPath=" not in current:
                continue
            updated = re.sub(r'DocumentPath=[^|]*', f'DocumentPath={doc_path}', current)
            self.config.set(sec, key, updated)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def get_output_types(self) -> list[dict[str, str]]:
        """
        Get list of output types defined in this OutJob.
        """
        result = []
        sec = 'OutputGroup1'
        if not self.config.has_section(sec):
            return result
        idx = 1
        while self.config.has_option(sec, f'OutputType{idx}'):
            result.append({
                'type': self.config.get(sec, f'OutputType{idx}'),
                'name': self.config.get(sec, f'OutputName{idx}', fallback=''),
                'category': self.config.get(sec, f'OutputCategory{idx}', fallback=''),
                'enabled': self.config.get(sec, f'OutputEnabled{idx}', fallback='0'),
            })
            idx += 1
        return result

    def get_output_mediums(self) -> list[dict[str, str]]:
        """
        Get list of output mediums defined in this OutJob.
        """
        result = []
        sec = 'OutputGroup1'
        if not self.config.has_section(sec):
            return result
        idx = 1
        while self.config.has_option(sec, f'OutputMedium{idx}'):
            result.append({
                'name': self.config.get(sec, f'OutputMedium{idx}'),
                'type': self.config.get(sec, f'OutputMedium{idx}_Type', fallback=''),
                'index': idx,
            })
            idx += 1
        return result

    @property
    def medium_count(self) -> int:
        return self._medium_count

    @property
    def output_count(self) -> int:
        return self._output_count

    def __repr__(self) -> str:
        return (f"AltiumOutJob(mediums={self._medium_count}, "
                f"outputs={self._output_count})")
