"""Schematic record model for SchRecordType.SHEET."""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, Any

from .altium_record_types import (
    SchPrimitive,
    SchRecordType,
    color_to_hex,
    parse_bool,
    serialize_bool,
)

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_svg_renderer import SchSvgRenderContext


def _sheet_x_to_svg(ctx: "SchSvgRenderContext", scale: float, x: float) -> float:
    return (x + ctx.offset_x) * scale


class SheetStyle(IntEnum):
    """
    Sheet size styles used by schematic documents.
    """

    A4 = 0
    A3 = 1
    A2 = 2
    A1 = 3
    A0 = 4
    A = 5
    B = 6  # Default: 17" x 11"
    C = 7
    D = 8
    E = 9
    LETTER = 10
    LEGAL = 11
    TABLOID = 12
    ORCAD_A = 13
    ORCAD_B = 14
    ORCAD_C = 15
    ORCAD_D = 16
    ORCAD_E = 17


# Sheet sizes in internal schematic units (10 mil per unit) for each SheetStyle.
# Public helpers convert these to mils.
SHEET_SIZES: dict[int, tuple[int, int]] = {
    # ISO A-series (metric origins)
    0: (1150, 760),  # A4: ~297mm x 210mm
    1: (1550, 1110),  # A3: ~420mm x 297mm
    2: (2230, 1570),  # A2: ~594mm x 420mm
    3: (3150, 2230),  # A1: ~841mm x 594mm
    4: (4460, 3150),  # A0: ~1189mm x 841mm
    # ANSI sizes (imperial)
    5: (950, 750),  # A: 9.5" x 7.5"
    6: (1500, 950),  # B: 15" x 9.5"
    7: (2000, 1500),  # C: 20" x 15"
    8: (3200, 2000),  # D: 32" x 20"
    9: (4200, 3200),  # E: 42" x 32"
    # Standard paper sizes
    10: (1100, 850),  # Letter: 11" x 8.5"
    11: (1400, 850),  # Legal: 14" x 8.5"
    12: (1700, 1100),  # Tabloid: 17" x 11"
    # OrCAD sizes
    13: (990, 790),  # OrCAD A
    14: (1540, 990),  # OrCAD B
    15: (2060, 1560),  # OrCAD C
    16: (3260, 2060),  # OrCAD D
    17: (4280, 3280),  # OrCAD E
}

# Sheet style descriptions (for title block "Size" field)
# Keyed by SheetStyle enum values (0-indexed)
SHEET_STYLE_DESCRIPTIONS: dict[int, str] = {
    0: "A4",
    1: "A3",
    2: "A2",
    3: "A1",
    4: "A0",
    5: "A",
    6: "B",
    7: "C",
    8: "D",
    9: "E",
    10: "Letter",
    11: "Legal",
    12: "Tabloid",
    13: "OrCAD A",
    14: "OrCAD B",
    15: "OrCAD C",
    16: "OrCAD D",
    17: "OrCAD E",
}

# Sheet zones (x_zones, y_zones, margin) for each SheetStyle
# `SHEET_ZONES` preserves the native SVG export lane.
# `ONSCREEN_SHEET_ZONES` captures known GeometryMaker/on-screen differences.
# margin is in internal schematic units (10 mil per unit).
SHEET_ZONES: dict[int, tuple[int, int, int]] = {
    # ISO A-series (estimated, need verification)
    0: (4, 4, 20),  # A4
    1: (5, 4, 20),  # A3
    2: (6, 5, 30),  # A2
    3: (8, 6, 30),  # A1
    4: (10, 7, 30),  # A0
    # ANSI sizes
    5: (4, 4, 20),  # A: 9.5" x 7.5"
    6: (6, 4, 20),  # B: 15" x 9.5"
    7: (6, 4, 30),  # C: 20" x 15"
    8: (8, 4, 30),  # D: 32" x 20"
    9: (16, 4, 40),  # E: 42" x 32"
    # Standard paper sizes
    10: (4, 4, 20),  # Letter
    11: (5, 4, 20),  # Legal
    12: (6, 4, 30),  # Tabloid
    # OrCAD sizes (estimated)
    13: (4, 4, 20),  # OrCAD A
    14: (6, 4, 30),  # OrCAD B
    15: (6, 4, 30),  # OrCAD C
    16: (8, 4, 30),  # OrCAD D
    17: (10, 5, 30),  # OrCAD E
}

# GeometryMaker/on-screen zone layout diverges from the native SVG export for
# some standard sheet styles. Keep the truth-lane override explicit instead of
# folding it into the native compatibility table.
ONSCREEN_SHEET_ZONES: dict[int, tuple[int, int, int]] = {
    **SHEET_ZONES,
    2: (8, 4, 30),  # A2 on-screen/oracle
}


class DocumentBorderStyle(IntEnum):
    """
    Title block style enum.

    Stored values:
    - 0: Standard Altium title block
    - 1: ANSI Y14.1 title block

    Note: Despite the enum name, this controls title block style, not border on/off.
    Border visibility is controlled by the separate 'border_on' property.
    """

    STANDARD = 0  # Default - Standard Altium title block
    ANSI = 1  # ANSI Y14.1 title block


class WorkspaceOrientation(IntEnum):
    """
    Sheet orientation.
    """

    LANDSCAPE = 0  # Default
    PORTRAIT = 1


class AltiumSchSheet(SchPrimitive):
    """
    Sheet properties record.

    Root container for schematic. Contains font table, grid settings,
    template references, and all sheet metadata.

    Complete sheet record implementation.
    """

    def __init__(self) -> None:
        super().__init__()

        # === Font Table ===
        self.font_id_count: int = 1
        # Fonts stored as dict: {font_id: {'name': str, 'size': int, 'rotation': int,
        #                                   'underline': bool, 'italic': bool, 'bold': bool, 'strikeout': bool}}
        # Default: Font 1 = Times New Roman 10pt (matches Altium File->New)
        self.fonts: dict[int, dict[str, Any]] = {
            1: {
                "name": "Times New Roman",
                "size": 10,
                "rotation": 0,
                "underline": False,
                "italic": False,
                "bold": False,
                "strikeout": False,
            }
        }

        # === Grid & Display Properties ===
        self.use_mbcs: bool = True
        self.is_boc: bool = False
        self.hot_spot_grid_on: bool = True
        self.hot_spot_grid_size: int = 4  # Internal units (8 / 2)
        self.hot_spot_grid_size_frac: int | None = (
            None  # Fractional sub-10000 precision
        )
        self.snap_grid_on: bool = True
        self.snap_grid_size: int = 10
        self.visible_grid_on: bool = True
        self.visible_grid_size: int = 10

        # === Sheet Style & Size ===
        self.sheet_style: int = SheetStyle.B  # Default: B size (6)
        self.system_font: int = 1
        self.document_border_style: int = DocumentBorderStyle.STANDARD
        self.workspace_orientation: int = WorkspaceOrientation.LANDSCAPE

        # === Border & Title Block ===
        self.border_on: bool = True
        # NOTE: Default is False per Altium source (sheet binary importer implementation)
        # When importing, argN11 is initialized to false and only set to true if file contains TitleBlockOn=T
        self.title_block_on: bool = False

        # === Colors (Win32 BBGGRR format) ===
        self.color: int = 0  # Sheet border color (from preferences)
        self.area_color: int = 16317695  # Sheet area color (light yellow default)

        # === Sheet Numbering ===
        self.sheet_number_space_size: int = 12  # Range: 1-16
        self.sheet_number: int = 1

        # === Custom Sheet Size ===
        self.custom_x: int = 1500  # Default width
        self.custom_y: int = 950  # Default height
        self.use_custom_sheet: bool = False
        self.custom_x_zones: int = 6
        self.custom_y_zones: int = 4
        self.custom_margin_width: int = 20

        # === Reference Zones ===
        self.reference_zones_on: bool = True
        self.reference_zone_style: int = 0  # eSheetReferenceZone_Default

        # === Template Graphics ===
        self.show_template_graphics: bool = False
        self.template_filename: str = ""
        self.template_vault_guid: str = ""
        self.template_item_guid: str = ""
        self.template_revision_guid: str = ""
        self.template_vault_hrid: str = ""
        self.template_revision_hrid: str = ""

        # === Display Unit ===
        self.display_unit: int = 0  # eMil after migration

        # === Version/Revision Tracking ===
        self.release_vault_guid: str = ""
        self.release_item_guid: str = ""
        self.item_revision_guid: str = ""
        self.props_vault_guid: str = ""
        self.props_revision_guid: str = ""
        self.file_version_info: str = ""

        # === Additional Properties ===
        self.show_hidden_pins: bool = False

        # === Legacy/Document Properties (from older implementation) ===
        self.sheet_name: str = ""
        self.file_name: str = ""
        self.title: str = ""
        self.organization: str = ""
        self.revision: str = ""
        self.date: str = ""

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.SHEET

    @property
    def hot_spot_grid_size_mils(self) -> float:
        """
        Hot spot grid size in mils, combining base and fractional parts.

                Formula: hot_spot_grid_size + (hot_spot_grid_size_frac or 0) / 100000.0

                Note: This is a UI setting for snap grid, not used in SVG rendering.

                Single source of truth for the combined grid-size value.
        """
        frac = self.hot_spot_grid_size_frac or 0
        return self.hot_spot_grid_size + frac / 100000.0

    @hot_spot_grid_size_mils.setter
    def hot_spot_grid_size_mils(self, value: float) -> None:
        """
        Set hot spot grid size from mils value, decomposing to base + frac.
        """
        internal = int(round(value * 100000))
        self.hot_spot_grid_size = internal // 100000
        self.hot_spot_grid_size_frac = internal % 100000

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        r = self._record  # Case-insensitive view

        # Store raw record for round-trip preservation
        self._raw_record = record.copy()

        # === Font Table ===
        self.font_id_count = int(r.get("FontIdCount", 1))
        self.fonts = {}
        for i in range(1, self.font_id_count + 1):
            font_data = {
                "name": record.get(
                    f"FontName{i}", record.get(f"FONTNAME{i}", "Times New Roman")
                ),
                "size": int(record.get(f"Size{i}", record.get(f"SIZE{i}", 10))),
                "rotation": int(
                    record.get(f"Rotation{i}", record.get(f"ROTATION{i}", 0))
                ),
                "underline": parse_bool(
                    record.get(f"Underline{i}", record.get(f"UNDERLINE{i}", False))
                ),
                "italic": parse_bool(
                    record.get(f"Italic{i}", record.get(f"ITALIC{i}", False))
                ),
                "bold": parse_bool(
                    record.get(f"Bold{i}", record.get(f"BOLD{i}", False))
                ),
                "strikeout": parse_bool(
                    record.get(f"StrikeOut{i}", record.get(f"STRIKEOUT{i}", False))
                ),
            }
            self.fonts[i] = font_data

        # === Grid & Display Properties ===
        self.use_mbcs = parse_bool(r.get("UseMBCS", True))
        self.is_boc = parse_bool(r.get("IsBOC", False))
        self.hot_spot_grid_on = parse_bool(r.get("HotSpotGridOn", True))
        # If _Frac exists but main field doesn't, the integer part is 0 (not default 4)
        if "HotSpotGridSize" in r:
            self.hot_spot_grid_size = int(r.get("HotSpotGridSize"))
        elif "HotSpotGridSize_Frac" in r:
            self.hot_spot_grid_size = 0  # Frac-only means integer part is 0
        else:
            self.hot_spot_grid_size = 4  # Altium default
        # Store _Frac separately for round-trip preservation
        if "HotSpotGridSize_Frac" in r:
            self.hot_spot_grid_size_frac = int(r.get("HotSpotGridSize_Frac"))
        self.snap_grid_on = parse_bool(r.get("SnapGridOn", True))
        self.snap_grid_size = int(r.get("SnapGridSize", 10))
        self.visible_grid_on = parse_bool(r.get("VisibleGridOn", True))
        self.visible_grid_size = int(r.get("VisibleGridSize", 10))

        # === Sheet Style & Size ===
        self.sheet_style = int(r.get("SheetStyle", SheetStyle.B))
        self.system_font = int(r.get("SystemFont", 1))
        self.document_border_style = int(r.get("DocumentBorderStyle", 0))
        self.workspace_orientation = int(r.get("WorkspaceOrientation", 0))

        # === Border & Title Block ===
        self.border_on = parse_bool(r.get("BorderOn", True))
        # Default is False per Altium source (sheet binary importer implementation)
        self.title_block_on = parse_bool(r.get("TitleBlockOn", False))

        # === Colors ===
        self.color = int(r.get("Color", 0))
        self.area_color = int(r.get("AreaColor", 16317695))

        # === Sheet Numbering ===
        self.sheet_number_space_size = int(r.get("SheetNumberSpaceSize", 12))
        self.sheet_number = int(r.get("SheetNumber", 1))

        # === Custom Sheet Size ===
        self.custom_x = int(r.get("CustomX", 1500))
        self.custom_y = int(r.get("CustomY", 950))
        self.use_custom_sheet = parse_bool(r.get("UseCustomSheet", False))
        # Track presence: native exports 0 for missing fields, but Altium defaults are 6, 4, 20
        # We need to match native export behavior: only write if present in original
        self._has_custom_x_zones = "CustomXZones" in record or "CUSTOMXZONES" in record
        self._has_custom_y_zones = "CustomYZones" in record or "CUSTOMYZONES" in record
        self._has_custom_margin_width = (
            "CustomMarginWidth" in record or "CUSTOMMARGINWIDTH" in record
        )
        self.custom_x_zones = int(
            r.get("CustomXZones", 0)
        )  # Default 0 to match native export
        self.custom_y_zones = int(
            r.get("CustomYZones", 0)
        )  # Default 0 to match native export
        self.custom_margin_width = int(
            r.get("CustomMarginWidth", 0)
        )  # Default 0 to match native export

        # === Reference Zones ===
        # NOTE: ReferenceZonesOn is INVERTED when stored in file
        # When reading, we need to invert it back
        raw_ref_zones = r.get("ReferenceZonesOn")
        if raw_ref_zones is not None:
            self.reference_zones_on = not parse_bool(raw_ref_zones)
        else:
            self.reference_zones_on = True  # Default
        self.reference_zone_style = int(r.get("ReferenceZoneStyle", 0))

        # === Template Graphics ===
        self.show_template_graphics = parse_bool(r.get("ShowTemplateGraphics", False))
        self.template_filename = r.get("TemplateFileName", "")
        self.template_vault_guid = r.get("TemplateVaultGUID", "")
        self.template_item_guid = r.get("TemplateItemGUID", "")
        self.template_revision_guid = r.get("TemplateRevisionGUID", "")
        self.template_vault_hrid = r.get("TemplateVaultHRID", "")
        self.template_revision_hrid = r.get("TemplateRevisionHRID", "")

        # === Display Unit ===
        # Try both naming conventions (DisplayUnit in JSON, Display_Unit in binary)
        self.display_unit = int(r.get("DisplayUnit", r.get("Display_Unit", 0)))

        # === Version/Revision Tracking ===
        self.release_vault_guid = r.get("ReleaseVaultGUID", "")
        self.release_item_guid = r.get("ReleaseItemGUID", "")
        self.item_revision_guid = r.get("ItemRevisionGUID", "")
        self.props_vault_guid = r.get("PropsVaultGUID", "")
        self.props_revision_guid = r.get("PropsRevisionGUID", "")
        self.file_version_info = r.get("FileVersionInfo", "")

        # === Additional Properties ===
        self.show_hidden_pins = parse_bool(r.get("ShowHiddenPins", False))

        # === Legacy/Document Properties ===
        self.sheet_name = r.get("SheetName", "")
        self.file_name = r.get("FileName", "")
        self.title = r.get("Title", "")
        self.organization = r.get("Organization", "")
        self.revision = r.get("Revision", "")
        self.date = r.get("Date", "")

    def serialize_to_record(self) -> dict[str, Any]:
        # IMPORTANT: Sheet record should NOT have OWNERINDEX, OWNERPARTID,
        # UNIQUEID, or INDEXINSHEET fields. These are primitive fields that only apply
        # to child objects, not the root Sheet object.
        # Therefore, we do NOT call super().serialize_to_record() here.

        # Start with just the RECORD type
        record: dict[str, Any] = {"RECORD": str(self.record_type.value)}
        self._serialize_font_table(record)
        self._serialize_grid_and_display(record)
        self._serialize_sheet_style_and_numbering(record)
        self._serialize_custom_sheet_fields(record)
        self._serialize_template_and_tracking_fields(record)
        self._serialize_legacy_document_fields(record)
        return record

    def _serialize_font_table(self, record: dict[str, Any]) -> None:
        # === Font Table ===
        record["FontIdCount"] = str(self.font_id_count)
        for font_id, font_data in self.fonts.items():
            record[f"Size{font_id}"] = str(font_data.get("size", 10))
            record[f"FontName{font_id}"] = font_data.get("name", "Times New Roman")
            if font_data.get("rotation", 0) != 0:
                record[f"Rotation{font_id}"] = str(font_data["rotation"])
            if font_data.get("underline", False):
                record[f"Underline{font_id}"] = "T"
            if font_data.get("italic", False):
                record[f"Italic{font_id}"] = "T"
            if font_data.get("bold", False):
                record[f"Bold{font_id}"] = "T"
            if font_data.get("strikeout", False):
                record[f"StrikeOut{font_id}"] = "T"

    def _serialize_grid_and_display(self, record: dict[str, Any]) -> None:
        # === Grid & Display Properties ===
        record["UseMBCS"] = serialize_bool(self.use_mbcs)
        record["IsBOC"] = serialize_bool(self.is_boc)
        record["HotSpotGridOn"] = serialize_bool(self.hot_spot_grid_on)
        record["HotSpotGridSize"] = str(self.hot_spot_grid_size)
        if self.hot_spot_grid_size_frac is not None:
            record["HotSpotGridSize_Frac"] = str(self.hot_spot_grid_size_frac)
        record["SnapGridOn"] = serialize_bool(self.snap_grid_on)
        record["SnapGridSize"] = str(self.snap_grid_size)
        record["VisibleGridOn"] = serialize_bool(self.visible_grid_on)
        record["VisibleGridSize"] = str(self.visible_grid_size)
        record["DisplayUnit"] = str(self.display_unit)

    def _serialize_sheet_style_and_numbering(self, record: dict[str, Any]) -> None:
        # === Sheet Style & Size ===
        record["SheetStyle"] = str(self.sheet_style)
        record["SystemFont"] = str(self.system_font)
        record["DocumentBorderStyle"] = str(self.document_border_style)
        record["WorkspaceOrientation"] = str(self.workspace_orientation)

        record["BorderOn"] = serialize_bool(self.border_on)
        record["TitleBlockOn"] = serialize_bool(self.title_block_on)

        record["Color"] = str(self.color)
        record["AreaColor"] = str(self.area_color)

        record["SheetNumberSpaceSize"] = str(self.sheet_number_space_size)
        record["SheetNumber"] = str(self.sheet_number)
        record["ReferenceZonesOn"] = serialize_bool(not self.reference_zones_on)
        record["ReferenceZoneStyle"] = str(self.reference_zone_style)

    def _serialize_custom_sheet_fields(self, record: dict[str, Any]) -> None:
        # === Custom Sheet Size ===
        record["CustomX"] = str(self.custom_x)
        record["CustomY"] = str(self.custom_y)
        if self.use_custom_sheet:
            record["UseCustomSheet"] = "T"
        if getattr(self, "_has_custom_x_zones", False) or self.custom_x_zones != 0:
            record["CustomXZones"] = str(self.custom_x_zones)
        if getattr(self, "_has_custom_y_zones", False) or self.custom_y_zones != 0:
            record["CustomYZones"] = str(self.custom_y_zones)
        if (
            getattr(self, "_has_custom_margin_width", False)
            or self.custom_margin_width != 0
        ):
            record["CustomMarginWidth"] = str(self.custom_margin_width)

    def _serialize_template_and_tracking_fields(self, record: dict[str, Any]) -> None:
        if self.show_template_graphics:
            record["ShowTemplateGraphics"] = "T"
        if self.template_filename:
            record["TemplateFileName"] = self.template_filename
        if self.template_vault_guid:
            record["TemplateVaultGUID"] = self.template_vault_guid
        if self.template_item_guid:
            record["TemplateItemGUID"] = self.template_item_guid
        if self.template_revision_guid:
            record["TemplateRevisionGUID"] = self.template_revision_guid
        if self.template_vault_hrid:
            record["TemplateVaultHRID"] = self.template_vault_hrid
        if self.template_revision_hrid:
            record["TemplateRevisionHRID"] = self.template_revision_hrid

        if self.release_vault_guid:
            record["ReleaseVaultGUID"] = self.release_vault_guid
        if self.release_item_guid:
            record["ReleaseItemGUID"] = self.release_item_guid
        if self.item_revision_guid:
            record["ItemRevisionGUID"] = self.item_revision_guid
        if self.props_vault_guid:
            record["PropsVaultGUID"] = self.props_vault_guid
        if self.props_revision_guid:
            record["PropsRevisionGUID"] = self.props_revision_guid
        if self.file_version_info:
            record["FileVersionInfo"] = self.file_version_info

        if self.show_hidden_pins:
            record["ShowHiddenPins"] = "T"

    def _serialize_legacy_document_fields(self, record: dict[str, Any]) -> None:
        if self.sheet_name:
            record["SheetName"] = self.sheet_name
        if self.file_name:
            record["FileName"] = self.file_name
        if self.title:
            record["Title"] = self.title
        if self.organization:
            record["Organization"] = self.organization
        if self.revision:
            record["Revision"] = self.revision
        if self.date:
            record["Date"] = self.date

    def clear_template_references(self) -> None:
        """
        Clear all template-related properties.

        Call this when removing sheet/template images to prevent
        Altium from trying to load a missing template.
        """
        self.show_template_graphics = False
        self.template_filename = ""
        self.template_vault_guid = ""
        self.template_item_guid = ""
        self.template_revision_guid = ""
        self.template_vault_hrid = ""
        self.template_revision_hrid = ""

    def __repr__(self) -> str:
        return (
            f"<AltiumSchSheet style={self.sheet_style} "
            f"custom={self.use_custom_sheet} "
            f"border={self.border_on} "
            f"fonts={self.font_id_count}>"
        )

    # =========================================================================
    # SVG RENDERING
    # =========================================================================

    def get_sheet_size_units(self) -> tuple[int, int]:
        """
        Get sheet dimensions in internal schematic document units.

        One stored sheet unit equals 10 mils.

        Returns:
            (width, height) in document units
        """
        if self.use_custom_sheet:
            width = int(self.custom_x)
            height = int(self.custom_y)
        else:
            size = SHEET_SIZES.get(self.sheet_style, (1000, 800))
            width = int(size[0])
            height = int(size[1])

        if self.workspace_orientation == WorkspaceOrientation.PORTRAIT:
            return (height, width)
        return (width, height)

    def get_sheet_size_mils(self) -> tuple[int, int]:
        """
        Get sheet dimensions in mils.

        Custom and standard sheet dimensions are stored in internal schematic
        units, where one stored unit equals 10 mils. This helper exposes the
        public mil-based contract.

        Returns:
            (width, height) in mils
        """
        width, height = self.get_sheet_size_units()
        return (width * 10, height * 10)

    def get_margin_units(self) -> int:
        """
        Get sheet margin in internal schematic document units.

        Returns:
            Margin in document units
        """
        if self.use_custom_sheet:
            return int(self.custom_margin_width)

        zones = SHEET_ZONES.get(self.sheet_style, (6, 4, 20))
        return int(zones[2])

    def get_margin_mils(self) -> int:
        """
        Get sheet margin in mils.

        For custom sheets, uses `custom_margin_width`.
        For standard sheets, uses the margin from `SHEET_ZONES`.

        Both are stored in internal schematic units and converted here to mils.

        Returns:
            Margin in mils
        """
        return self.get_margin_units() * 10

    def get_style_description(self) -> str:
        """
        Get sheet style description for title block.
        """
        return SHEET_STYLE_DESCRIPTIONS.get(self.sheet_style, "Custom")

    def get_system_font(self) -> tuple[str, int]:
        """
        Get the system font name and size from the font table.

        Altium uses the SystemFont property to index into the font table
        for reference zones and other document-level text.

        Returns:
            (font_name, font_size) tuple
        """
        font_id = self.system_font
        if font_id in self.fonts:
            font_data = self.fonts[font_id]
            return (font_data.get("name", "Times New Roman"), font_data.get("size", 10))
        # Default fallback
        return ("Times New Roman", 10)

    def _render_reference_zones(
        self,
        ctx: SchSvgRenderContext,
        sheet_w: float,
        sheet_h: float,
        margin: float,
        zones_x: int,
        zones_y: int,
        line_color: str,
        text_color: str,
    ) -> list[str]:
        """
        Render reference zone dividers and labels.

        Horizontal zones: numbered 1, 2, 3, ... from left to right
        Vertical zones: lettered A, B, C, ... from bottom to top

        Uses the document's SystemFont for zone labels (from font table).
        """
        from .altium_sch_svg_renderer import svg_line, svg_text
        from .altium_text_metrics import measure_text_height, measure_text_width

        elements: list[str] = []
        scale = ctx.scale

        # Native Altium renders zone labels using the document system font but positions
        # them from measured string extents, not SVG anchor/baseline centering.
        if self.system_font > 0:
            font_name, font_size_px, is_bold, is_italic, _ = ctx.get_font_info(
                self.system_font
            )
        else:
            font_name, font_size_px, is_bold, is_italic, _ = ctx.get_system_font_info()

        render_font_size = ctx.get_baseline_font_size(font_size_px)

        outer_left = (0.0 + ctx.offset_x) * scale
        outer_right = (sheet_w + ctx.offset_x) * scale
        outer_top = (
            (-sheet_h + ctx.offset_y) * scale if ctx.flip_y else ctx.offset_y * scale
        )
        outer_bottom = (
            ctx.offset_y * scale if ctx.flip_y else (sheet_h + ctx.offset_y) * scale
        )
        inner_left = (margin + ctx.offset_x) * scale
        inner_right = ((sheet_w - margin) + ctx.offset_x) * scale
        inner_top = (
            (-(sheet_h - margin) + ctx.offset_y) * scale
            if ctx.flip_y
            else (margin + ctx.offset_y) * scale
        )
        inner_bottom = (
            (-margin + ctx.offset_y) * scale
            if ctx.flip_y
            else ((sheet_h - margin) + ctx.offset_y) * scale
        )

        zone_w_svg = (outer_right - outer_left) / zones_x
        zone_h_svg = (outer_bottom - outer_top) / zones_y
        is_asme = self.reference_zone_style == 1

        # Horizontal reference zones (columns with numbers)
        for i in range(1, zones_x + 1):
            label = str(i)
            text_width = measure_text_width(
                label,
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )
            text_height = measure_text_height(
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )

            x_divider = (
                outer_right - i * zone_w_svg if is_asme else outer_left + i * zone_w_svg
            )
            x_text = (
                x_divider + zone_w_svg / 2.0 + text_width
                if is_asme
                else x_divider - zone_w_svg / 2.0 - text_width
            )
            top_gap = (inner_top - outer_top - text_height) / 2.0
            y_top_text = inner_top - top_gap
            y_bottom_text = outer_bottom - top_gap

            elements.append(
                svg_line(
                    x_divider,
                    outer_top,
                    x_divider,
                    inner_top,
                    stroke=line_color,
                    stroke_width=0.5 * scale,
                )
            )
            elements.append(
                svg_text(
                    x_text,
                    y_top_text,
                    label,
                    font_size=render_font_size,
                    font_family=font_name,
                    fill=text_color,
                )
            )

            elements.append(
                svg_line(
                    x_divider,
                    outer_bottom,
                    x_divider,
                    inner_bottom,
                    stroke=line_color,
                    stroke_width=0.5 * scale,
                )
            )
            elements.append(
                svg_text(
                    x_text,
                    y_bottom_text,
                    label,
                    font_size=render_font_size,
                    font_family=font_name,
                    fill=text_color,
                )
            )

        # Vertical reference zones (rows with letters, A at bottom)
        for i in range(1, zones_y + 1):
            letter = chr(ord("A") + zones_y - i)
            text_width = measure_text_width(
                letter,
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )
            text_height = measure_text_height(
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )

            y_divider = (
                outer_bottom - i * zone_h_svg if is_asme else outer_top + i * zone_h_svg
            )
            left_gap = (inner_left - outer_left - text_width) / 2.0
            y_text = (
                y_divider
                + (zone_h_svg / 2.0 - text_height / 2.0)
                - (0.0 if self.use_custom_sheet else 1.0)
                if is_asme
                else y_divider - (zone_h_svg / 2.0 - text_height / 2.0)
            )
            x_left_text = outer_left + left_gap
            x_right_text = inner_right + left_gap

            elements.append(
                svg_line(
                    outer_left,
                    y_divider,
                    inner_left,
                    y_divider,
                    stroke=line_color,
                    stroke_width=0.5 * scale,
                )
            )
            elements.append(
                svg_text(
                    x_left_text,
                    y_text,
                    letter,
                    font_size=render_font_size,
                    font_family=font_name,
                    fill=text_color,
                )
            )

            elements.append(
                svg_line(
                    outer_right,
                    y_divider,
                    inner_right,
                    y_divider,
                    stroke=line_color,
                    stroke_width=0.5 * scale,
                )
            )
            elements.append(
                svg_text(
                    x_right_text,
                    y_text,
                    letter,
                    font_size=render_font_size,
                    font_family=font_name,
                    fill=text_color,
                )
            )

        return elements

    def _render_title_block(
        self,
        ctx: SchSvgRenderContext,
        sheet_w: float,
        sheet_h: float,
        margin: float,
        line_color: str,
    ) -> list[str]:
        """
        Render title block based on document_border_style.

        Dispatches to:
        - _render_standard_title_block for DocumentBorderStyle.STANDARD
        - _render_ansi_title_block for DocumentBorderStyle.ANSI
        """
        if self.document_border_style == DocumentBorderStyle.ANSI:
            return self._render_ansi_title_block(
                ctx, sheet_w, sheet_h, margin, line_color
            )
        else:
            return self._render_standard_title_block(
                ctx, sheet_w, sheet_h, margin, line_color
            )

    def _render_standard_title_block(
        self,
        ctx: SchSvgRenderContext,
        sheet_w: float,
        sheet_h: float,
        margin: float,
        line_color: str,
    ) -> list[str]:
        """
        Render standard Altium title block.

        The title block is positioned at the bottom-right corner of the
        inner working area. Key dimensions are:
        - Width: 350 mils from right edge
        - Height: 80 mils from bottom edge
        - Horizontal lines at: +80, +50, +20, +10 mils from bottom
        - Vertical dividers at: -350, -300, -150, -100 from right edge
        """
        from .altium_sch_svg_renderer import svg_line, svg_text

        elements: list[str] = []
        scale = ctx.scale

        # Title block dimensions (in mils). Source values are stored in 10-mil units.
        tb_width = 3500.0  # 350 * 10 = 3500 mils (3.5 inches)
        tb_height = 800.0  # 80 * 10 = 800 mils (0.8 inches)

        # Title block position: bottom-right of inner area
        # In Altium coords: right edge at (sheet_w - margin), bottom at margin
        tb_right = sheet_w - margin
        tb_bottom = margin
        tb_left = tb_right - tb_width
        tb_top = tb_bottom + tb_height

        # Text properties - scale with title block
        # Altium uses Times New Roman for title block text
        label_font_size = 60.0 * scale  # 6 * 10 = 60 mils
        value_font_size = 80.0 * scale  # 8 * 10 = 80 mils
        title_font = "Times New Roman"
        text_color = line_color

        x_to_svg = lambda x: _sheet_x_to_svg(ctx, scale, x)

        def y_to_svg(y: float) -> float:
            if ctx.flip_y:
                return (-y + ctx.offset_y) * scale
            return (y + ctx.offset_y) * scale

        # Horizontal lines (from bottom to top: 0, 100, 200, 500, 800).
        h_lines = [
            (tb_bottom + 800, tb_left, tb_right),  # Top
            (tb_bottom + 500, tb_left, tb_right),  # Below title
            (tb_bottom + 200, tb_left, tb_right),  # Above file/drawn by
            (tb_bottom + 100, tb_left, tb_right),  # Above file/drawn by divider
        ]

        for y, x1, x2 in h_lines:
            elements.append(
                svg_line(
                    x_to_svg(x1),
                    y_to_svg(y),
                    x_to_svg(x2),
                    y_to_svg(y),
                    stroke=line_color,
                    stroke_width=1.0 * scale,
                )
            )

        # Vertical lines (scaled by 10x)
        v_lines = [
            (tb_left, tb_bottom, tb_top),  # Left edge
            (tb_right - 3000, tb_bottom + 200, tb_bottom + 500),  # Size/Number divider
            (
                tb_right - 1000,
                tb_bottom + 200,
                tb_bottom + 500,
            ),  # Number/Revision divider
            (tb_right - 1500, tb_bottom, tb_bottom + 200),  # File/Drawn By divider
        ]

        for x, y1, y2 in v_lines:
            elements.append(
                svg_line(
                    x_to_svg(x),
                    y_to_svg(y1),
                    x_to_svg(x),
                    y_to_svg(y2),
                    stroke=line_color,
                    stroke_width=1.0 * scale,
                )
            )

        # Labels and text positions.
        # Positions are relative to title block bottom-left
        # Y offsets adjusted to place text below the top line of each cell
        # (subtract ~20 mils from top of cell for proper text placement)
        labels = [
            # (label, x_offset_from_left, y_offset_from_bottom)
            ("Title", 50, 760),  # Just below 800 line
            ("Size", 50, 460),  # Just below 500 line
            ("Number", 550, 460),
            ("Revision", 2550, 460),
            ("Date:", 50, 160),  # Just below 200 line
            ("File:", 50, 60),  # Just below 100 line
            ("Sheet    of", 2050, 160),
            ("Drawn By:", 2050, 60),
        ]

        for label, x_off, y_off in labels:
            x = tb_left + x_off
            y = tb_bottom + y_off
            elements.append(
                svg_text(
                    x_to_svg(x),
                    y_to_svg(y),
                    label,
                    font_size=label_font_size,
                    font_family=title_font,
                    fill=text_color,
                    text_anchor="start",
                    dominant_baseline="hanging",
                )
            )

        # Add the sheet size value (centered in Size cell)
        size_desc = self.get_style_description()
        elements.append(
            svg_text(
                x_to_svg(tb_left + 100),
                y_to_svg(tb_bottom + 280),
                size_desc,
                font_size=value_font_size,
                font_family=title_font,
                fill=text_color,
                text_anchor="start",
                dominant_baseline="hanging",
            )
        )

        return elements

    def _render_ansi_title_block(
        self,
        ctx: SchSvgRenderContext,
        sheet_w: float,
        sheet_h: float,
        margin: float,
        line_color: str,
    ) -> list[str]:
        """
        Render ANSI Y14.1 title block.

        Coordinates are stored in internal units (100000 = 1 mil) and
        converted to mils here.

        Title block structure (from bottom-right corner):
        - Total width: 625 mils
        - Total height: 175 mils
        - Left section (company info): 200 mils wide (625-425)
        - Right section (drawing info): 425 mils wide

        Row heights (from bottom):
        - Row 1: 0-25 mils (Scale/Sheet row)
        - Row 2: 25-63 mils (Size/FCSM/DWG/Rev row)
        - Row 3: 63-125 mils (empty middle row)
        - Row 4: 125-175 mils (header row)
        """
        from .altium_sch_svg_renderer import svg_line, svg_text

        elements: list[str] = []
        scale = ctx.scale

        # ANSI title block dimensions (in mils).
        tb_height = 175.0  # 17500000 / 100000
        tb_width = 625.0  # 62500000 / 100000
        tb_right_section = 425.0  # 42500000 / 100000

        # Row heights from bottom
        row1_top = 25.0  # 2500000 / 100000
        row2_top = 63.0  # 6300000 / 100000
        row3_top = 125.0  # 12500000 / 100000

        # Title block position: bottom-right of inner area
        tb_right = sheet_w - margin
        tb_bottom = margin
        tb_left = tb_right - tb_width
        tb_top = tb_bottom + tb_height
        tb_middle = (
            tb_right - tb_right_section
        )  # Vertical divider between left/right sections

        # Text properties
        font_name, font_size_pt = self.get_system_font()
        label_font_size = font_size_pt * 0.8 * scale  # Slightly smaller for labels
        title_font = font_name
        text_color = line_color

        x_to_svg = lambda x: _sheet_x_to_svg(ctx, scale, x)

        def y_to_svg(y: float) -> float:
            # Use sheet_height for Y-flip: svg_y = sheet_height - altium_y
            if ctx.sheet_height > 0:
                return (ctx.sheet_height - y + ctx.offset_y) * scale
            elif ctx.flip_y:
                return (-y + ctx.offset_y) * scale
            return (y + ctx.offset_y) * scale

        # === HORIZONTAL LINES ===
        h_lines = [
            # Top of title block
            (tb_top, tb_left, tb_right),
            # Row dividers (right section only - from middle to right)
            (tb_bottom + row3_top, tb_middle, tb_right),
            (tb_bottom + row2_top, tb_middle, tb_right),
            (tb_bottom + row1_top, tb_middle, tb_right),
            # Extended row dividers (across to near-right edge)
            # The 625 internal units = 0.00625 mils offset from right (nearly at right edge)
            (tb_bottom + row2_top, tb_middle, tb_right - 0.00625),
            (tb_bottom + row1_top, tb_middle, tb_right - 0.00625),
        ]

        for y, x1, x2 in h_lines:
            elements.append(
                svg_line(
                    x_to_svg(x1),
                    y_to_svg(y),
                    x_to_svg(x2),
                    y_to_svg(y),
                    stroke=line_color,
                    stroke_width=1.0 * scale,
                )
            )

        # === VERTICAL LINES ===
        v_lines = [
            # Left edge of title block
            (tb_left, tb_bottom, tb_top),
            # Middle divider (between left and right sections)
            (tb_middle, tb_bottom, tb_top),
            # Dividers in bottom two rows (row1 and row2)
            # x-387: Size/FCSM divider
            (tb_right - 387.0, tb_bottom + row1_top, tb_bottom + row2_top),
            # x-325: Scale/Sheet divider in row1
            (tb_right - 325.0, tb_bottom, tb_bottom + row1_top),
            # x-175: Scale/Sheet divider in row1
            (tb_right - 175.0, tb_bottom, tb_bottom + row1_top),
            # x-276: FCSM/DWG divider in row2
            (tb_right - 276.0, tb_bottom + row1_top, tb_bottom + row2_top),
            # x-36: DWG/Rev divider in row2
            (tb_right - 36.0, tb_bottom + row1_top, tb_bottom + row2_top),
        ]

        for x, y1, y2 in v_lines:
            elements.append(
                svg_line(
                    x_to_svg(x),
                    y_to_svg(y1),
                    x_to_svg(x),
                    y_to_svg(y2),
                    stroke=line_color,
                    stroke_width=1.0 * scale,
                )
            )

        # === TEXT LABELS ===
        # Positions are offsets from the right edge of the title block.
        # Text is positioned relative to cell, using baseline positioning
        labels = [
            # Row 1 (Scale/Sheet row): y = row1_top + small offset
            ("Scale", tb_right - 420.0, tb_bottom + row1_top),
            ("Sheet", tb_right - 170.0, tb_bottom + row1_top),
            # Row 2 (Size/FCSM/DWG/Rev row): y = row2_top + small offset
            ("Size", tb_right - 420.0, tb_bottom + row2_top),
            ("FCSM No.", tb_right - 382.0, tb_bottom + row2_top),
            ("DWG No.", tb_right - 271.0, tb_bottom + row2_top),
            ("Rev", tb_right - 31.0, tb_bottom + row2_top),
        ]

        for label, x, y in labels:
            elements.append(
                svg_text(
                    x_to_svg(x),
                    y_to_svg(y),
                    label,
                    font_size=label_font_size,
                    font_family=title_font,
                    fill=text_color,
                    text_anchor="start",
                    dominant_baseline="hanging",
                )
            )

        # Sheet size value (in Size cell)
        size_desc = self.get_style_description()
        # Position below "Size" label
        elements.append(
            svg_text(
                x_to_svg(tb_right - 420.0),
                y_to_svg(tb_bottom + 50.0),
                size_desc,
                font_size=label_font_size * 1.2,
                font_family=title_font,
                fill=text_color,
                text_anchor="start",
                dominant_baseline="hanging",
            )
        )

        return elements
