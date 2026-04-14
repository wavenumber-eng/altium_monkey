"""
Font ID Manager for Altium SchDoc/SchLib documents.

Provides document-level font table management for font lookup and creation
by name instead of requiring hardcoded font IDs.

Works with both SchDoc (fonts in Sheet record) and SchLib (fonts in FileHeader).
The font table format is identical - only the OLE storage location differs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_ole import AltiumOleFile
    from .altium_record_sch__sheet import AltiumSchSheet

log = logging.getLogger(__name__)


class _FontStorage:
    """
    Internal font storage adapter for standalone FontIDManager use.

    Provides the same interface as AltiumSchSheet for font storage,
    allowing FontIDManager to work without a full sheet record.
    Used by SchLib which stores fonts in FileHeader, not Sheet.
    """

    def __init__(self, fonts: dict[int, dict[str, Any]] | None = None) -> None:
        self.fonts: dict[int, dict] = fonts.copy() if fonts else {}
        self._font_id_count: int = max(self.fonts.keys()) if self.fonts else 0

    @property
    def font_id_count(self) -> int:
        return self._font_id_count

    @font_id_count.setter
    def font_id_count(self, value: int) -> None:
        self._font_id_count = value


class FontIDManager:
    """
    Document-level font table manager for SchDoc and SchLib files.

    Provides:
    - Font lookup by name, size, and style
    - Automatic font creation when needed
    - Font ID allocation (1-999)
    - Font ID translation (file ID <-> internal ID)

    Works with both document types:
    - SchDoc: Attach to AltiumSchSheet (fonts in Sheet record)
    - SchLib: Create with from_font_dict() (fonts in FileHeader)

    Usage (SchDoc):
        schdoc = AltiumSchDoc("design.SchDoc")
        font_id = schdoc.font_manager.get_or_create_font("Arial", 10)

    Usage (SchLib):
        fonts = parse_fonts_from_fileheader(ole)  # {id: {name, size, ...}}
        font_manager = FontIDManager.from_font_dict(fonts)
    """

    MAX_FONTS = 999  # Altium limit (Font IDs 1-999)
    DEFAULT_FONT_NAME = "Times New Roman"
    DEFAULT_FONT_SIZE = 10

    def __init__(self, sheet: AltiumSchSheet | _FontStorage) -> None:
        """
        Initialize manager with font storage.

        Args:
            sheet: Font storage - either AltiumSchSheet (SchDoc) or
                   _FontStorage adapter (SchLib, standalone use)

        Use from_font_dict() for standalone SchLib use.
        """
        self._sheet = sheet
        # Translation tables for file <-> internal ID mapping.
        self._in_translator: dict[int, int] = {}  # file ID -> internal ID
        self._out_translator: dict[int, int] = {}  # internal ID -> output file ID
        self._save_flags: set[int] = set()  # internal IDs marked for export

    @classmethod
    def from_font_dict(cls, fonts: dict[int, dict] | None = None) -> FontIDManager:
        """
        Create FontIDManager from a font dictionary.

        Use this for SchLib files or standalone font management without
        a full AltiumSchSheet.

        Args:
            fonts: Font table dict {font_id: {name, size, bold, italic, ...}}
                   If None, creates empty manager with default font.

        Returns:
            FontIDManager instance
        """
        storage = _FontStorage(fonts)

        # Ensure default font exists (font ID 1)
        if 1 not in storage.fonts:
            storage.fonts[1] = {
                "name": cls.DEFAULT_FONT_NAME,
                "size": cls.DEFAULT_FONT_SIZE,
                "rotation": 0,
                "underline": False,
                "italic": False,
                "bold": False,
                "strikeout": False,
            }
            storage.font_id_count = max(1, storage.font_id_count)

        return cls(storage)

    @classmethod
    def load_from_ole_header(
        cls,
        ole: AltiumOleFile,
        header_section: str = "FileHeader",
    ) -> FontIDManager:
        """
        Load FontIDManager from OLE FileHeader section.

        Parses FontIdCount, FontName1, Size1, Bold1, Italic1, etc.
        from the FileHeader record and creates a FontIDManager.

        Args:
            ole: AltiumOleFile object
            header_section: Name of header section (default: 'FileHeader')

        Returns:
            FontIDManager instance with fonts from header
        """
        from .altium_utilities import get_records_in_section

        fonts_dict: dict[int, dict] = {}

        try:
            records = get_records_in_section(ole, header_section)

            for record in records:
                if "HEADER" in record or "FontIdCount" in record:
                    # Extract font count
                    font_count = int(record.get("FontIdCount", 0))

                    # Load each font
                    for i in range(1, font_count + 1):
                        font_name_key = f"FontName{i}"
                        font_size_key = f"Size{i}"

                        if font_name_key in record:
                            font_name = record[font_name_key]
                            font_size = int(record.get(font_size_key, 10))

                            # Parse boolean attributes
                            bold = record.get(f"Bold{i}", "F") == "T"
                            italic = record.get(f"Italic{i}", "F") == "T"
                            underline = record.get(f"Underline{i}", "F") == "T"
                            strikeout = record.get(f"Strikeout{i}", "F") == "T"
                            rotation = int(record.get(f"Rotation{i}", 0))

                            fonts_dict[i] = {
                                "name": font_name,
                                "size": font_size,
                                "bold": bold,
                                "italic": italic,
                                "underline": underline,
                                "strikeout": strikeout,
                                "rotation": rotation,
                            }
                    break
        except Exception as e:
            log.warning(f"Failed to load font table from {header_section}: {e}")

        return cls.from_font_dict(fonts_dict)

    @classmethod
    def load_from_record(cls, record: dict) -> FontIDManager:
        """
        Load FontIDManager from a parsed record dict.

        Parses FontIdCount, FontName1, Size1, Bold1, Italic1, etc.
        from a record dict (e.g., SHEET record in SchDoc).

        Args:
            record: Dict containing font fields (FontIdCount, FontName1, etc.)

        Returns:
            FontIDManager instance with fonts from record
        """
        fonts_dict: dict[int, dict] = {}

        font_count = int(record.get("FontIdCount", 0))

        for i in range(1, font_count + 1):
            font_name_key = f"FontName{i}"
            font_size_key = f"Size{i}"

            if font_name_key in record:
                font_name = record[font_name_key]
                font_size = int(record.get(font_size_key, 10))

                # Parse boolean attributes
                bold = record.get(f"Bold{i}", "F") == "T"
                italic = record.get(f"Italic{i}", "F") == "T"
                underline = record.get(f"Underline{i}", "F") == "T"
                strikeout = record.get(f"Strikeout{i}", "F") == "T"
                rotation = int(record.get(f"Rotation{i}", 0))

                fonts_dict[i] = {
                    "name": font_name,
                    "size": font_size,
                    "bold": bold,
                    "italic": italic,
                    "underline": underline,
                    "strikeout": strikeout,
                    "rotation": rotation,
                }

        return cls.from_font_dict(fonts_dict)

    @property
    def fonts(self) -> dict[int, dict]:
        """
        Direct access to font table dict.
        """
        return self._sheet.fonts

    def get_font_name(self, font_id: int) -> str:
        """
        Get font name by ID. Returns 'Unknown(ID=N)' if not found.
        """
        font = self._sheet.fonts.get(font_id)
        return font["name"] if font else f"Unknown(ID={font_id})"

    def get_font_size(self, font_id: int) -> int:
        """
        Get font size by ID. Returns 0 if not found.
        """
        font = self._sheet.fonts.get(font_id)
        return font["size"] if font else 0

    def is_bold(self, font_id: int) -> bool:
        """
        Get whether font is bold.
        """
        font = self._sheet.fonts.get(font_id)
        return font.get("bold", False) if font else False

    def is_italic(self, font_id: int) -> bool:
        """
        Get whether font is italic.
        """
        font = self._sheet.fonts.get(font_id)
        return font.get("italic", False) if font else False

    def get_or_create_font(
        self,
        font_name: str = DEFAULT_FONT_NAME,
        font_size: int = DEFAULT_FONT_SIZE,
        bold: bool = False,
        italic: bool = False,
        rotation: int = 0,
        underline: bool = False,
        strikeout: bool = False,
    ) -> int:
        """
        Find existing font or create new one.

        Args:
            font_name: Font family name (e.g., "Arial", "Times New Roman")
            font_size: Font size in points
            bold: Bold flag
            italic: Italic flag
            rotation: Rotation in degrees (0, 90, 180, 270)
            underline: Underline flag
            strikeout: Strikeout flag

        Returns:
            Font ID (1-999) for use in record fields

        Raises:
            ValueError: If font table is full (999 fonts)
        """
        # Search existing fonts
        for font_id, font_data in self._sheet.fonts.items():
            if self._fonts_match(
                font_data,
                font_name,
                font_size,
                bold,
                italic,
                rotation,
                underline,
                strikeout,
            ):
                return font_id

        # Create new font
        return self._create_font(
            font_name, font_size, bold, italic, rotation, underline, strikeout
        )

    def get_font_info(self, font_id: int) -> dict[str, Any] | None:
        """
        Get font attributes for ID.

        Args:
            font_id: Font ID to look up

        Returns:
            Font data dict with keys: name, size, rotation, underline, italic, bold, strikeout
            Returns None if font not found
        """
        return self._sheet.fonts.get(font_id)

    def get_default_font_id(self) -> int:
        """
        Get the default font ID (always 1).

        Font ID 1 is guaranteed to exist in any valid Altium document.
        Font ID 0 in position formats means "use Font ID 1".

        Returns:
            1 (the default font ID)
        """
        return 1

    def _fonts_match(
        self,
        font_data: dict,
        font_name: str,
        font_size: int,
        bold: bool,
        italic: bool,
        rotation: int,
        underline: bool,
        strikeout: bool,
    ) -> bool:
        """
        Check if font_data matches the requested attributes.
        """
        # Case-insensitive font name comparison
        if font_data.get("name", "").lower() != font_name.lower():
            return False
        if font_data.get("size", 0) != font_size:
            return False
        if font_data.get("bold", False) != bold:
            return False
        if font_data.get("italic", False) != italic:
            return False
        if font_data.get("rotation", 0) != rotation:
            return False
        if font_data.get("underline", False) != underline:
            return False
        return font_data.get("strikeout", False) == strikeout

    def _create_font(
        self,
        font_name: str,
        font_size: int,
        bold: bool,
        italic: bool,
        rotation: int,
        underline: bool,
        strikeout: bool,
    ) -> int:
        """
        Create new font and add to sheet's font table.
        """
        # Find next available font ID
        next_id = self._sheet.font_id_count + 1

        if next_id > self.MAX_FONTS:
            raise ValueError(f"Font table full (max {self.MAX_FONTS} fonts)")

        # Add font to table
        self._sheet.fonts[next_id] = {
            "name": font_name,
            "size": font_size,
            "rotation": rotation,
            "underline": underline,
            "italic": italic,
            "bold": bold,
            "strikeout": strikeout,
        }

        # Update count
        self._sheet.font_id_count = next_id

        log.debug(f"Created font ID {next_id}: {font_name} {font_size}pt")

        return next_id

    @property
    def font_count(self) -> int:
        """
        Current number of fonts in table.
        """
        return self._sheet.font_id_count

    @property
    def sheet(self) -> AltiumSchSheet | _FontStorage:
        """
        Get the underlying sheet record.
        """
        return self._sheet

    def __repr__(self) -> str:
        return f"<FontIDManager fonts={self.font_count}>"

    # =========================================================================
    # Font ID Translation Methods
    # =========================================================================
    # These methods handle file-ID <-> internal-ID translation.

    def translate_in(self, file_font_id: int) -> int:
        """
        Translate file font ID to internal font ID.

        When reading font IDs from file records, this maps the file's
        font ID to the internal ID used in memory. Handles font deduplication
        where multiple file font IDs may map to the same internal ID.

        Args:
            file_font_id: Font ID from file record

        Returns:
            Internal font ID for use in OOP objects
        """
        # Clamp out-of-range to 1 (Altium behavior)
        if file_font_id < 1 or file_font_id > 1000:
            file_font_id = 1

        if file_font_id in self._in_translator:
            return self._in_translator[file_font_id]
        # Identity mapping when no translator set up
        return file_font_id

    def translate_out(self, internal_font_id: int) -> int:
        """
        Translate internal font ID to output file font ID.

        When writing font IDs to file records, this maps the internal
        ID to the ID that should be written to the file.

        For now, this is a pass-through (identity mapping).
        Future: Will use _out_translator populated before file save.

        Args:
            internal_font_id: Internal font ID from OOP object

        Returns:
            File font ID for writing to records
        """
        if internal_font_id in self._out_translator:
            return self._out_translator[internal_font_id]
        # Identity mapping when no translator set up
        return internal_font_id

    def mark_for_save(self, internal_font_id: int) -> None:
        """
        Mark a font as used, ensuring it will be exported.

        Called by the serializer when writing font IDs.
        Fonts not marked will be excluded from the output font table.

        Args:
            internal_font_id: Internal font ID to mark
        """
        self._save_flags.add(internal_font_id)

    def setup_in_translator(self, file_fonts: dict[int, dict]) -> None:
        """
        Set up input translation from file font table WITH DEDUPLICATION.

        Called during file parsing to build the translation table.
        Fonts with identical specs (name, size, rotation, bold, italic, etc.)
        are deduplicated to the same internal ID.

        CRITICAL: Rotation is part of font identity in Altium!
        Arial 10pt 0 deg and Arial 10pt 90 deg are DIFFERENT fonts.

        Args:
            file_fonts: Font table from file {file_id: {name, size, ...}}
        """
        self._in_translator.clear()

        # Build translation table with deduplication
        # get_or_create_font() handles deduplication via _fonts_match()
        for file_id, font_data in file_fonts.items():
            internal_id = self.get_or_create_font(
                font_name=font_data.get("name", self.DEFAULT_FONT_NAME),
                font_size=font_data.get("size", self.DEFAULT_FONT_SIZE),
                bold=font_data.get("bold", False),
                italic=font_data.get("italic", False),
                rotation=font_data.get("rotation", 0),
                underline=font_data.get("underline", False),
                strikeout=font_data.get("strikeout", False),
            )
            self._in_translator[file_id] = internal_id

        log.debug(
            f"Built in_translator: {len(file_fonts)} file fonts -> "
            f"{len(set(self._in_translator.values()))} unique internal fonts"
        )

    def setup_out_translator(self) -> None:
        """
        Set up output translation for file writing.

        Called before file save to build the output font table.
        Only fonts marked with mark_for_save() will be included.
        Output IDs are sequential (1, 2, 3...) with no gaps.
        """
        self._out_translator.clear()
        output_id = 0
        for internal_id in sorted(self._save_flags):
            output_id += 1
            self._out_translator[internal_id] = output_id

        log.debug(
            f"Built out_translator: {len(self._save_flags)} fonts -> "
            f"sequential IDs 1-{output_id}"
        )

    def get_export_font_table(self) -> list[dict]:
        """
        Get font table for export (only flagged fonts, in output order).

        Returns list of font dicts in the order they should be written,
        corresponding to output IDs 1, 2, 3, etc.

        Returns:
            List of font data dicts for writing to file
        """
        result = []
        for internal_id in sorted(self._save_flags):
            font_info = self.get_font_info(internal_id)
            if font_info:
                result.append(font_info)
        return result
