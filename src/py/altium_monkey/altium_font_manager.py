"""
Font ID Manager for Altium SchDoc/SchLib documents.

Provides document-level font table management for font lookup and creation
by name instead of requiring hardcoded font IDs.

Works with both SchDoc (fonts in Sheet record) and SchLib (fonts in FileHeader).
The font table format is identical - only the OLE storage location differs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, TypedDict

if TYPE_CHECKING:
    from .altium_ole import AltiumOleFile
    from .altium_record_sch__sheet import AltiumSchSheet


class _FontSpec(TypedDict):
    """Typed shape retained behind the public dictionary-facing font API."""

    name: str
    size: int
    rotation: int
    underline: bool
    italic: bool
    bold: bool
    strikeout: bool


class _FontStorageLike(Protocol):
    fonts: dict[int, _FontSpec]

    @property
    def font_id_count(self) -> int: ...

    @font_id_count.setter
    def font_id_count(self, value: int) -> None: ...


_FontTableInput = Mapping[int, Mapping[str, object]]


def _font_value_error(field: str, detail: str) -> ValueError:
    return ValueError(f"{field}: {detail}")


def _validated_font_id(font_id: object, *, field: str) -> int:
    if isinstance(font_id, bool) or not isinstance(font_id, int):
        raise _font_value_error(field, "expected an integer font ID")
    if not 1 <= font_id <= FontIDManager.MAX_FONTS:
        raise _font_value_error(
            field, f"expected a stored font ID from 1 through {FontIDManager.MAX_FONTS}"
        )
    return font_id


def _validated_translator_id(font_id: object, *, field: str) -> int:
    if isinstance(font_id, bool) or not isinstance(font_id, int):
        raise _font_value_error(field, "expected an integer font ID")
    if not 1 <= font_id <= 1000:
        raise _font_value_error(
            field, "expected a translator font ID from 1 through 1000"
        )
    return font_id


def _optional_str(font_data: Mapping[str, object], field: str, default: str) -> str:
    value = font_data.get(field, default)
    if not isinstance(value, str):
        raise _font_value_error(field, "expected a string")
    return value


def _optional_int(font_data: Mapping[str, object], field: str, default: int) -> int:
    value = font_data.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _font_value_error(field, "expected an integer")
    if not -(1 << 15) <= value <= (1 << 15) - 1:
        raise _font_value_error(field, "expected a signed 16-bit integer")
    return value


def _optional_bool(font_data: Mapping[str, object], field: str, default: bool) -> bool:
    value = font_data.get(field, default)
    if not isinstance(value, bool):
        raise _font_value_error(field, "expected a boolean")
    return value


def _copy_font_spec(font_data: Mapping[str, object]) -> _FontSpec:
    return {
        "name": _optional_str(font_data, "name", FontIDManager.DEFAULT_FONT_NAME)
        or FontIDManager.DEFAULT_FONT_NAME,
        "size": _optional_int(font_data, "size", FontIDManager.DEFAULT_FONT_SIZE),
        "rotation": _optional_int(font_data, "rotation", 0),
        "underline": _optional_bool(font_data, "underline", False),
        "italic": _optional_bool(font_data, "italic", False),
        "bold": _optional_bool(font_data, "bold", False),
        "strikeout": _optional_bool(font_data, "strikeout", False),
    }


def _copy_font_table_with_ids(
    fonts: _FontTableInput | None, *, translator_ids: bool
) -> dict[int, _FontSpec]:
    if fonts is None:
        return {}
    result: dict[int, _FontSpec] = {}
    for raw_font_id, font_data in fonts.items():
        validator = _validated_translator_id if translator_ids else _validated_font_id
        font_id = validator(raw_font_id, field="font ID")
        if not isinstance(font_data, Mapping):
            raise _font_value_error(f"font {font_id}", "expected a mapping")
        result[font_id] = _copy_font_spec(font_data)
    if result and sorted(result) != list(range(1, max(result) + 1)):
        raise _font_value_error("font IDs", "expected a dense table from 1 through N")
    return result


def _copy_font_table(fonts: _FontTableInput | None) -> dict[int, _FontSpec]:
    return _copy_font_table_with_ids(fonts, translator_ids=False)


def _copy_translator_font_table(fonts: _FontTableInput) -> dict[int, _FontSpec]:
    return _copy_font_table_with_ids(fonts, translator_ids=True)


def _record_value(record: Mapping[str, object], field: str) -> object | None:
    if field in record:
        return record[field]
    normalized = field.casefold()
    return next(
        (value for key, value in record.items() if key.casefold() == normalized), None
    )


def _record_int(record: Mapping[str, object], field: str, default: int) -> int:
    value = _record_value(record, field)
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        digits = text[1:] if text.startswith(("+", "-")) else text
        if digits.isascii() and digits.isdigit():
            return int(text)
    raise _font_value_error(field, "expected an integer")


def _record_i16(record: Mapping[str, object], field: str, default: int) -> int:
    value = _record_int(record, field, default)
    if not -(1 << 15) <= value <= (1 << 15) - 1:
        raise _font_value_error(field, "expected a signed 16-bit integer")
    return value


def _record_bool(record: Mapping[str, object], field: str) -> bool:
    return _record_value(record, field) == "T"


class _FontStorage:
    """
    Internal font storage adapter for standalone FontIDManager use.

    Provides the same interface as AltiumSchSheet for font storage,
    allowing FontIDManager to work without a full sheet record.
    Used by SchLib which stores fonts in FileHeader, not Sheet.
    """

    def __init__(self, fonts: _FontTableInput | None = None) -> None:
        self.fonts = _copy_font_table(fonts)
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
    - Portable stored font ID allocation (1-999)
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

    # Deliberate portable stored-table policy; AD's managed list can reach ID 1000.
    MAX_FONTS = 999
    DEFAULT_FONT_NAME = "Times New Roman"
    DEFAULT_FONT_SIZE = 10

    def __init__(self, sheet: AltiumSchSheet | _FontStorageLike) -> None:
        """
        Initialize manager with font storage.

        Args:
            sheet: Font storage - either AltiumSchSheet (SchDoc) or
                   _FontStorage adapter (SchLib, standalone use)

        Use from_font_dict() for standalone SchLib use.
        """
        if not 0 <= sheet.font_id_count <= self.MAX_FONTS:
            raise _font_value_error(
                "font_id_count", f"expected a value from 0 through {self.MAX_FONTS}"
            )
        validated_fonts = _copy_font_table(sheet.fonts)
        if sheet.font_id_count != len(validated_fonts):
            raise _font_value_error(
                "font_id_count", "expected the exact size of the dense font table"
            )
        sheet.fonts.clear()
        sheet.fonts.update(validated_fonts)
        self._sheet = sheet
        # Translation tables for file <-> internal ID mapping.
        self._in_translator: dict[int, int] = {}  # file ID -> internal ID
        self._out_translator: dict[int, int] = {}  # internal ID -> output file ID
        self._out_translator_ready = False
        self._save_flags: set[int] = set()  # internal IDs marked for export
        self._export_font_ids: tuple[int, ...] = ()

    @classmethod
    def from_font_dict(cls, fonts: _FontTableInput | None = None) -> FontIDManager:
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

        records = get_records_in_section(ole, header_section)
        for record in records:
            if (
                _record_value(record, "HEADER") is not None
                or _record_value(record, "FontIdCount") is not None
            ):
                return cls.load_from_record(record)
        raise _font_value_error(
            header_section, "expected a FileHeader record with HEADER or FontIdCount"
        )

    @classmethod
    def load_from_record(cls, record: Mapping[str, object]) -> FontIDManager:
        """
        Load FontIDManager from a parsed record dict.

        Parses FontIdCount, FontName1, Size1, Bold1, Italic1, etc.
        from a record dict (e.g., SHEET record in SchDoc).

        Args:
            record: Dict containing font fields (FontIdCount, FontName1, etc.)

        Returns:
            FontIDManager instance with fonts from record
        """
        fonts_dict: dict[int, _FontSpec] = {}

        font_count = _record_int(record, "FontIdCount", 0)
        if not 0 <= font_count <= cls.MAX_FONTS:
            raise _font_value_error(
                "FontIdCount", f"expected a value from 0 through {cls.MAX_FONTS}"
            )

        for i in range(1, font_count + 1):
            font_name_key = f"FontName{i}"
            font_size_key = f"Size{i}"

            font_name_value = _record_value(record, font_name_key)
            if font_name_value is not None and not isinstance(font_name_value, str):
                raise _font_value_error(font_name_key, "expected a string")
            fonts_dict[i] = {
                "name": font_name_value or cls.DEFAULT_FONT_NAME,
                "size": _record_i16(record, font_size_key, 0),
                "bold": _record_bool(record, f"Bold{i}"),
                "italic": _record_bool(record, f"Italic{i}"),
                "underline": _record_bool(record, f"Underline{i}"),
                "strikeout": _record_bool(record, f"StrikeOut{i}"),
                "rotation": _record_i16(record, f"Rotation{i}", 0),
            }

        return cls.from_font_dict(fonts_dict)

    @property
    def fonts(self) -> dict[int, _FontSpec]:
        """
        Direct access to font table dict.
        """
        return {font_id: font.copy() for font_id, font in self._sheet.fonts.items()}

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
        requested = _copy_font_spec(
            {
                "name": font_name,
                "size": font_size,
                "rotation": rotation,
                "underline": underline,
                "italic": italic,
                "bold": bold,
                "strikeout": strikeout,
            }
        )
        font_name = requested["name"] or self.DEFAULT_FONT_NAME
        font_size = requested["size"]
        rotation = requested["rotation"]
        underline = requested["underline"]
        italic = requested["italic"]
        bold = requested["bold"]
        strikeout = requested["strikeout"]

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

    def get_font_info(self, font_id: int) -> _FontSpec | None:
        """
        Get font attributes for ID.

        Args:
            font_id: Font ID to look up

        Returns:
            Font data dict with keys: name, size, rotation, underline, italic, bold, strikeout
            Returns None if font not found
        """
        font = self._sheet.fonts.get(font_id)
        return font.copy() if font is not None else None

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
        font_data: _FontSpec,
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

        return next_id

    @property
    def font_count(self) -> int:
        """
        Current number of fonts in table.
        """
        return self._sheet.font_id_count

    @property
    def sheet(self) -> AltiumSchSheet | _FontStorageLike:
        """
        Get the underlying live storage object.

        This is the legacy escape hatch for callers that intentionally need
        backing-object identity. Other public font dictionaries are copies.
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

        Before setup, valid IDs retain the historical identity adaptation.
        After setup, marked fonts use compact IDs and every unmarked or invalid
        ID resolves to default output font 1.

        Args:
            internal_font_id: Internal font ID from OOP object

        Returns:
            File font ID for writing to records
        """
        internal_font_id = self._normalize_translator_id(internal_font_id)
        if internal_font_id in self._out_translator:
            return self._out_translator[internal_font_id]
        return 1 if self._out_translator_ready else internal_font_id

    def mark_for_save(self, internal_font_id: int) -> None:
        """
        Mark a font as used, ensuring it will be exported.

        Called by the serializer when writing font IDs.
        Fonts not marked will be excluded from the output font table.

        Args:
            internal_font_id: Internal font ID to mark
        """
        if (
            1 <= internal_font_id <= self.MAX_FONTS
            and internal_font_id in self._sheet.fonts
        ):
            self._save_flags.add(internal_font_id)

    def setup_in_translator(self, file_fonts: _FontTableInput) -> None:
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
        normalized_fonts = _copy_translator_font_table(file_fonts)
        candidate_storage = _FontStorage(self._sheet.fonts)
        candidate_storage.font_id_count = self._sheet.font_id_count
        candidate = FontIDManager(candidate_storage)
        translator: dict[int, int] = {}

        # Build on a detached table so malformed or overflowing input is transactional.
        for file_id, font_data in sorted(normalized_fonts.items()):
            internal_id = candidate.get_or_create_font(
                font_name=font_data.get("name", self.DEFAULT_FONT_NAME),
                font_size=font_data.get("size", self.DEFAULT_FONT_SIZE),
                bold=font_data.get("bold", False),
                italic=font_data.get("italic", False),
                rotation=font_data.get("rotation", 0),
                underline=font_data.get("underline", False),
                strikeout=font_data.get("strikeout", False),
            )
            translator[file_id] = internal_id

        self._sheet.fonts.clear()
        self._sheet.fonts.update(candidate.fonts)
        self._sheet.font_id_count = candidate.font_count
        self._in_translator = translator

    def setup_out_translator(self) -> None:
        """
        Set up output translation for file writing.

        Called before file save to build the output font table.
        Only fonts marked with mark_for_save() will be included.
        Output IDs are sequential (1, 2, 3...) with no gaps.
        """
        self._out_translator.clear()
        self._out_translator_ready = True
        output_id = 0
        valid_ids = sorted(
            internal_id
            for internal_id in self._save_flags
            if internal_id in self._sheet.fonts
        )
        self._export_font_ids = tuple(valid_ids)
        self._save_flags.clear()
        for internal_id in valid_ids:
            output_id += 1
            self._out_translator[internal_id] = output_id

    def get_export_font_table(self) -> list[_FontSpec]:
        """
        Get font table for export (only flagged fonts, in output order).

        Returns list of font dicts in the order they should be written,
        corresponding to output IDs 1, 2, 3, etc.

        Returns:
            List of font data dicts for writing to file
        """
        result: list[_FontSpec] = []
        for internal_id in self._export_font_ids:
            font_info = self._sheet.fonts.get(internal_id)
            if font_info:
                result.append(font_info.copy())
        return result

    @staticmethod
    def _normalize_translator_id(font_id: int) -> int:
        return font_id if 1 <= font_id <= 1000 else 1
