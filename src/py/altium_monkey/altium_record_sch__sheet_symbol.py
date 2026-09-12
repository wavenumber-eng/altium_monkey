"""Schematic record model for SchRecordType.SHEET_SYMBOL."""

import re
from enum import IntEnum
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_record_sch__sheet_entry import AltiumSchSheetEntry
    from .altium_sch_geometry_oracle import SchGeometryOp, SchGeometryRecord

from .altium_sch_record_helpers import (
    bound_schematic_owner,
    detect_case_mode_method_from_dotted_uppercase_fields,
    remove_named_entry,
)
from .altium_dotnet_ordinal import dotnet_trim
from ._sch_managed_defaults import (
    SHEET_SYMBOL_BORDER_COLOR,
    SHEET_SYMBOL_FILL_COLOR,
)
from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import LineWidth, SchGraphicalObject, SchRecordType
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)


def _is_repeat_name(value: str) -> bool:
    opening = value.find("(")
    if opening < 0 or dotnet_trim(value[:opening]).lower() != "repeat":
        return False
    closing = value.find(")", opening + 1)
    if closing < 0:
        return False
    parts = value[opening + 1 : closing].split(",", 2)
    if len(parts) != 3 or "," in parts[2]:
        return False
    begin = _repeat_name_int32(parts[1])
    end = _repeat_name_int32(parts[2])
    if begin is None or end is None or begin < 0:
        return False
    # IsRepeatName tests the unchecked Int32 subtraction, not sorted endpoints.
    difference = (end - begin + (1 << 31)) % (1 << 32) - (1 << 31)
    return difference >= 0


def _repeat_name_int32(value: str) -> int | None:
    clean = value.rstrip("\0").strip(" \t\n\v\f\r")
    if re.fullmatch(r"[+-]?[0-9]+", clean) is None:
        return None
    # Avoid arbitrary-precision parsing and Python's digit limit while retaining
    # managed acceptance of arbitrarily many leading zeroes.
    digits = clean.lstrip("+-").lstrip("0") or "0"
    if len(digits) > 10:
        return None
    parsed = int(digits)
    if clean.startswith("-"):
        parsed = -parsed
    return parsed if -(1 << 31) <= parsed < (1 << 31) else None


class SchSheetSymbolType(IntEnum):
    """
    Sheet symbol type (affects visual appearance).
    """

    NORMAL = 0
    DEVICE_SHEET = 1
    DESIGN_ITEM = 2


_SHEET_SYMBOL_TYPE_BY_TEXT: dict[str, str] = {
    "normal": "Normal",
    "device sheet": "Device Sheet",
    "design item": "Design Item",
}


def _normalize_sheet_symbol_type(value: str) -> str:
    return _SHEET_SYMBOL_TYPE_BY_TEXT.get(str(value).lower(), "Normal")


def _sheet_symbol_type_for_write(value: str) -> str:
    normalized = _SHEET_SYMBOL_TYPE_BY_TEXT.get(str(value).lower())
    if normalized is None:
        raise ValueError("SymbolType must be Normal, Device Sheet, or Design Item")
    return normalized


def _sheet_symbol_geometry_points(
    points: list[tuple[float, float]],
    *,
    sheet_height_px: float,
    units_per_px: int,
) -> list[list[float]]:
    from .altium_sch_geometry_oracle import svg_coord_to_geometry

    return [
        list(
            svg_coord_to_geometry(
                point_x,
                point_y,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
        )
        for point_x, point_y in points
    ]


def _sheet_symbol_open_stack_operations(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    scale: float,
    sheet_height_px: float,
    units_per_px: int,
    pen: dict[str, object],
) -> list["SchGeometryOp"]:
    from .altium_sch_geometry_oracle import SchGeometryOp

    operations: list[SchGeometryOp] = []
    for index in (1, 2):
        previous = (index - 1) * 2 * scale
        current = index * 2 * scale
        first = (x + width + previous, y + current)
        second = (x + width + current, y + current)
        third = (x + width + current, y + height + current)
        fourth = (x + current, y + height + current)
        fifth = (x + current, y + height + previous)
        for start, end in (
            (first, second),
            (second, third),
            (third, fourth),
            (fourth, fifth),
        ):
            operations.append(
                SchGeometryOp.lines(
                    _sheet_symbol_geometry_points(
                        [start, end],
                        sheet_height_px=sheet_height_px,
                        units_per_px=units_per_px,
                    ),
                    pen=pen,
                )
            )
    return operations


class AltiumSchSheetSymbol(SchGraphicalObject):
    """
    Sheet symbol record.

    Hierarchical sheet symbol (box representing sub-schematic).
    Container for sheet entries, sheet name, and filename.

    Inheritance matches native: SchDataSheetSymbol -> SchDataRectangularEntryContainer
    -> SchDataRectangularGroup -> SchDataParametrizedGroup -> SchDataGraphicalObject
    """

    def __init__(self) -> None:
        super().__init__()
        # Dimensions from SchDataRectangularGroup base class
        self.x_size: int = 80
        self.x_size_frac: int = 0
        self.y_size: int = 50
        self.y_size_frac: int = 0
        # SheetSymbol-specific fields
        self.is_solid: bool = True  # Fill interior
        self.line_width: LineWidth = LineWidth.SMALLEST
        self.color = SHEET_SYMBOL_BORDER_COLOR
        self.area_color = SHEET_SYMBOL_FILL_COLOR
        self.symbol_type: str = "Normal"
        self.show_hidden_fields: bool = False
        self.design_item_id: str = ""
        self.source_library_name: str = ""
        self.vault_guid: str = ""
        self.item_guid: str = ""
        self.revision_guid: str = ""
        self.revision_name: str = ""
        # Children (entries, name, filename) - populated during hierarchy building
        self.children: list = []  # All child objects
        self.entries: list = []  # AltiumSchSheetEntry objects only
        self.sheet_name = None  # AltiumSchSheetName  record - may contain REPEAT()
        self.file_name = None  # AltiumSchFileName  record
        # Track field presence for round-trip fidelity
        self._has_x_size: bool = False
        self._has_x_size_frac: bool = False
        self._has_y_size: bool = False
        self._has_y_size_frac: bool = False
        self._has_is_solid: bool = False
        self._has_line_width: bool = False
        self._has_symbol_type: bool = False
        self._has_show_hidden_fields: bool = False
        self._has_design_item_id: bool = False
        self._has_source_library_name: bool = False
        self._has_vault_guid: bool = False
        self._has_item_guid: bool = False
        self._has_revision_guid: bool = False
        self._has_revision_name: bool = False
        self._used_utf8_symbol_type: bool = False
        self._used_utf8_design_item_id: bool = False
        self._used_utf8_source_library_name: bool = False
        self._used_utf8_vault_guid: bool = False
        self._used_utf8_item_guid: bool = False
        self._used_utf8_revision_guid: bool = False
        self._used_utf8_revision_name: bool = False
        self._source_x_size_frac: int = self.x_size_frac
        self._source_y_size_frac: int = self.y_size_frac
        self._source_symbol_type: str = self.symbol_type
        self._source_design_item_id: str = self.design_item_id
        self._source_source_library_name: str = self.source_library_name
        self._source_vault_guid: str = self.vault_guid
        self._source_item_guid: str = self.item_guid
        self._source_revision_guid: str = self.revision_guid
        self._source_revision_name: str = self.revision_name
        self._source_unique_id: str | None = self.unique_id
        self._raw_unique_id: tuple[str, str] | None = None

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.SHEET_SYMBOL

    @property
    def is_device_sheet(self) -> bool:
        """
        Check if this is a device sheet (rounded corners).
        """
        return self.symbol_type == "Device Sheet"

    def is_multichannel(self) -> bool:
        """
        Check if this sheet symbol represents a multichannel (repeated) sheet.

        Detection based on REPEAT(designator,start,end) pattern in sheet name.
        This matches Altium's SheetSymbolUtils.IsRepeatName() logic.

        Returns:
            True if sheet name matches REPEAT(...) pattern
        """
        if self.sheet_name is None:
            return False
        text = getattr(self.sheet_name, "text", "")
        return _is_repeat_name(text)

    def _bound_schematic_owner(self) -> object | None:
        return bound_schematic_owner(self)

    def _notify_owner_structure_changed(self) -> None:
        owner = self._bound_schematic_owner()
        if owner is None:
            return
        sync_hook = getattr(owner, "_sync_sheet_symbol_group_objects", None)
        if callable(sync_hook):
            sync_hook(self)

    def _refresh_children_list(self) -> None:
        children: list[object] = list(self.entries)
        if self.sheet_name is not None:
            children.append(self.sheet_name)
        if self.file_name is not None:
            children.append(self.file_name)
        self.children[:] = children

    @staticmethod
    def _clear_detached_child_state(child: object) -> None:
        if getattr(child, "parent", None) is not None:
            setattr(child, "parent", None)
        if hasattr(child, "_bound_schematic_context"):
            setattr(child, "_bound_schematic_context", None)

    @staticmethod
    def _normalized_entry_name(name: str) -> str:
        return str(name or "").strip().lower()

    def get_entry(self, name: str) -> "AltiumSchSheetEntry | None":
        """
        Return the first sheet entry whose name matches ``name``.

        Lookup is case-insensitive. Missing names return ``None``.
        """
        normalized_name = self._normalized_entry_name(name)
        for entry in self.entries:
            if (
                self._normalized_entry_name(getattr(entry, "name", ""))
                == normalized_name
            ):
                return entry
        return None

    def add_entry(self, entry: object) -> None:
        """
        Attach a sheet entry to this symbol.
        """
        from .altium_record_sch__sheet_entry import AltiumSchSheetEntry

        if not isinstance(entry, AltiumSchSheetEntry):
            raise TypeError("entry must be an AltiumSchSheetEntry")
        if entry in self.entries:
            raise ValueError("entry is already attached to this sheet symbol")
        parent = getattr(entry, "parent", None)
        if parent is not None and parent is not self:
            raise ValueError("entry is already attached to a different sheet symbol")

        entry.parent = self
        self.entries.append(entry)
        self._refresh_children_list()
        self._notify_owner_structure_changed()

    def remove_entry(self, entry: object) -> bool:
        """
        Detach a sheet entry from this symbol.
        """
        if entry not in self.entries:
            return False

        self.entries.remove(entry)
        self._refresh_children_list()
        self._notify_owner_structure_changed()
        self._clear_detached_child_state(entry)
        return True

    def remove_entry_by_name(self, name: str) -> bool:
        """
        Remove the first entry whose name matches ``name`` case-insensitively.
        """
        return remove_named_entry(self, name)

    def move_entry(self, entry_or_name: object | str, *, index: int) -> None:
        """
        Reorder an attached sheet entry within this symbol.
        """
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("index must be an integer")
        if index < 0 or index >= len(self.entries):
            raise IndexError("index is out of range for sheet entries")

        if isinstance(entry_or_name, str):
            entry = self.get_entry(entry_or_name)
            if entry is None:
                raise ValueError(f"No sheet entry named {entry_or_name!r}")
        else:
            entry = entry_or_name
            if entry not in self.entries:
                raise ValueError("entry is not attached to this sheet symbol")

        current_index = self.entries.index(entry)
        if current_index == index:
            return

        self.entries.pop(current_index)
        self.entries.insert(index, entry)
        self._refresh_children_list()
        self._notify_owner_structure_changed()

    def set_sheet_name(self, label: object) -> None:
        """
        Attach or replace the sheet-name label for this symbol.
        """
        from .altium_record_sch__sheet_name import AltiumSchSheetName

        if not isinstance(label, AltiumSchSheetName):
            raise TypeError("label must be an AltiumSchSheetName")
        parent = getattr(label, "parent", None)
        if parent is not None and parent is not self:
            raise ValueError("label is already attached to a different sheet symbol")

        existing = self.sheet_name
        if existing is label:
            return

        label.parent = self
        self.sheet_name = label
        self._refresh_children_list()
        self._notify_owner_structure_changed()
        if existing is not None:
            self._clear_detached_child_state(existing)

    def clear_sheet_name(self) -> bool:
        """
        Remove the current sheet-name label when present.
        """
        if self.sheet_name is None:
            return False

        existing = self.sheet_name
        self.sheet_name = None
        self._refresh_children_list()
        self._notify_owner_structure_changed()
        self._clear_detached_child_state(existing)
        return True

    def set_file_name(self, label: object) -> None:
        """
        Attach or replace the file-name label for this symbol.
        """
        from .altium_record_sch__file_name import AltiumSchFileName

        if not isinstance(label, AltiumSchFileName):
            raise TypeError("label must be an AltiumSchFileName")
        parent = getattr(label, "parent", None)
        if parent is not None and parent is not self:
            raise ValueError("label is already attached to a different sheet symbol")

        existing = self.file_name
        if existing is label:
            return

        label.parent = self
        self.file_name = label
        self._refresh_children_list()
        self._notify_owner_structure_changed()
        if existing is not None:
            self._clear_detached_child_state(existing)

    def clear_file_name(self) -> bool:
        """
        Remove the current file-name label when present.
        """
        if self.file_name is None:
            return False

        existing = self.file_name
        self.file_name = None
        self._refresh_children_list()
        self._notify_owner_structure_changed()
        self._clear_detached_child_state(existing)
        return True

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        s = AltiumSerializer()
        self._raw_unique_id = next(
            (
                (key, str(value))
                for key, value in record.items()
                if key.lower() == "uniqueid"
            ),
            None,
        )
        if not self.unique_id:
            self.unique_id = "$$$"
        self._source_unique_id = self.unique_id

        # Parse dimensions
        self.x_size, self._has_x_size = s.read_int(record, Fields.X_SIZE, default=0)
        self.x_size_frac, self._has_x_size_frac = s.read_int(
            record, "XSize_Frac", default=0
        )
        self.y_size, self._has_y_size = s.read_int(record, Fields.Y_SIZE, default=0)
        self.y_size_frac, self._has_y_size_frac = s.read_int(
            record, "YSize_Frac", default=0
        )
        self._source_x_size_frac = self.x_size_frac
        self._source_y_size_frac = self.y_size_frac

        # Parse IsSolid
        self.is_solid, self._has_is_solid = s.read_bool(
            record, Fields.IS_SOLID, default=False
        )

        # Parse LineWidth
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

        self.color, _ = s.read_color(record, Fields.COLOR, default=0)
        self.area_color, _ = s.read_color(record, Fields.AREA_COLOR, default=0)
        self._capture_graphical_source_state()

        # Parse SymbolType
        symbol_type, self._has_symbol_type, self._used_utf8_symbol_type = (
            read_dynamic_string_field(
                s,
                record,
                self._record,
                Fields.SYMBOL_TYPE,
                default="",
            )
        )
        self.symbol_type = _normalize_sheet_symbol_type(symbol_type)
        self.show_hidden_fields, self._has_show_hidden_fields = s.read_bool(
            record,
            Fields.SHOW_HIDDEN_FIELDS,
            default=False,
        )
        (
            self.design_item_id,
            self._has_design_item_id,
            self._used_utf8_design_item_id,
        ) = read_dynamic_string_field(
            s, record, self._record, Fields.DESIGN_ITEM_ID, default=""
        )
        (
            self.source_library_name,
            self._has_source_library_name,
            self._used_utf8_source_library_name,
        ) = read_dynamic_string_field(
            s, record, self._record, Fields.SOURCE_LIBRARY_NAME, default=""
        )
        self.vault_guid, self._has_vault_guid, self._used_utf8_vault_guid = (
            read_dynamic_string_field(
                s, record, self._record, Fields.VAULT_GUID, default=""
            )
        )
        self.item_guid, self._has_item_guid, self._used_utf8_item_guid = (
            read_dynamic_string_field(
                s, record, self._record, Fields.ITEM_GUID, default=""
            )
        )
        (
            self.revision_guid,
            self._has_revision_guid,
            self._used_utf8_revision_guid,
        ) = read_dynamic_string_field(
            s, record, self._record, Fields.REVISION_GUID, default=""
        )
        (
            self.revision_name,
            self._has_revision_name,
            self._used_utf8_revision_name,
        ) = read_dynamic_string_field(
            s, record, self._record, Fields.REVISION_NAME, default=""
        )
        self._source_symbol_type = self.symbol_type
        self._source_design_item_id = self.design_item_id
        self._source_source_library_name = self.source_library_name
        self._source_vault_guid = self.vault_guid
        self._source_item_guid = self.item_guid
        self._source_revision_guid = self.revision_guid
        self._source_revision_name = self.revision_name

    def serialize_to_record(self) -> dict[str, Any]:
        if not self.unique_id:
            self.unique_id = "$$$"
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        raw = self._raw_record
        if raw is not None and self.unique_id == self._source_unique_id:
            if self._raw_unique_id is None:
                serializer.remove_field(record, "UniqueID")
            else:
                key, value = self._raw_unique_id
                record[key] = value

        self._write_size_fields(serializer, record, raw)
        self._serialize_managed_family_bool(
            record,
            serializer,
            Fields.IS_SOLID.canonical,
            self.is_solid,
        )
        self._serialize_managed_family_int(
            record,
            serializer,
            Fields.LINE_WIDTH.canonical,
            self.line_width.value,
        )
        self._serialize_managed_family_color(
            record, serializer, Fields.COLOR.canonical, int(self.color or 0)
        )
        self._serialize_managed_family_color(
            record, serializer, Fields.AREA_COLOR.canonical, int(self.area_color or 0)
        )
        self.symbol_type = _sheet_symbol_type_for_write(self.symbol_type)
        write_dynamic_string_field(
            serializer,
            record,
            Fields.SYMBOL_TYPE,
            self.symbol_type,
            raw_record=raw,
            used_utf8_sidecar=self._used_utf8_symbol_type,
            was_present=self._has_symbol_type,
            force=self.symbol_type != self._source_symbol_type,
        )
        self._serialize_managed_family_bool(
            record,
            serializer,
            Fields.SHOW_HIDDEN_FIELDS.canonical,
            self.show_hidden_fields,
        )
        self._write_dynamic_metadata(serializer, record, raw)
        serializer.remove_field(record, Fields.TRANSPARENT)
        serializer.remove_field(record, Fields.LINE_STYLE)
        serializer.remove_field(record, Fields.LINE_STYLE_EXT)
        if raw is None:
            return self._authored_managed_order(record)
        return record

    def _write_size_fields(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        for field_name, frac_name, whole, frac, had_frac, source_frac in (
            (
                Fields.X_SIZE,
                "XSize_Frac",
                self.x_size,
                self.x_size_frac,
                self._has_x_size_frac,
                self._source_x_size_frac,
            ),
            (
                Fields.Y_SIZE,
                "YSize_Frac",
                self.y_size,
                self.y_size_frac,
                self._has_y_size_frac,
                self._source_y_size_frac,
            ),
        ):
            self._serialize_managed_family_int(
                record, serializer, field_name.canonical, whole
            )
            self._write_size_fraction(
                serializer, record, raw, frac_name, frac, had_frac, source_frac
            )

    @staticmethod
    def _write_size_fraction(
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
        field_name: str,
        value: int,
        was_present: bool,
        source_value: int,
    ) -> None:
        if value != 0 or (was_present and value == source_value):
            serializer.write_int(record, field_name, value, raw, force=True)
        else:
            serializer.remove_field(record, field_name)

    def _write_dynamic_metadata(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
        raw: dict[str, object] | None,
    ) -> None:
        for field_name, value, was_present, used_utf8, source_value in (
            (
                Fields.DESIGN_ITEM_ID,
                self.design_item_id,
                self._has_design_item_id,
                self._used_utf8_design_item_id,
                self._source_design_item_id,
            ),
            (
                Fields.SOURCE_LIBRARY_NAME,
                self.source_library_name,
                self._has_source_library_name,
                self._used_utf8_source_library_name,
                self._source_source_library_name,
            ),
            (
                Fields.VAULT_GUID,
                self.vault_guid,
                self._has_vault_guid,
                self._used_utf8_vault_guid,
                self._source_vault_guid,
            ),
            (
                Fields.ITEM_GUID,
                self.item_guid,
                self._has_item_guid,
                self._used_utf8_item_guid,
                self._source_item_guid,
            ),
            (
                Fields.REVISION_GUID,
                self.revision_guid,
                self._has_revision_guid,
                self._used_utf8_revision_guid,
                self._source_revision_guid,
            ),
            (
                Fields.REVISION_NAME,
                self.revision_name,
                self._has_revision_name,
                self._used_utf8_revision_name,
                self._source_revision_name,
            ),
        ):
            if was_present or value:
                write_dynamic_string_field(
                    serializer,
                    record,
                    field_name,
                    value,
                    raw_record=raw,
                    used_utf8_sidecar=used_utf8,
                    was_present=was_present,
                    force=value != source_value,
                )

    @staticmethod
    def _authored_managed_order(record: dict[str, object]) -> dict[str, object]:
        family_order = (
            "Location.X",
            "Location.X_Frac",
            "Location.Y",
            "Location.Y_Frac",
            "XSize",
            "XSize_Frac",
            "YSize",
            "YSize_Frac",
            "LineWidth",
            "Color",
            "AreaColor",
            "IsSolid",
            "ShowHiddenFields",
            "UniqueID",
            "SymbolType",
            "%UTF8%SymbolType",
            "DesignItemId",
            "%UTF8%DesignItemId",
            "SourceLibraryName",
            "%UTF8%SourceLibraryName",
            "VaultGUID",
            "%UTF8%VaultGUID",
            "ItemGUID",
            "%UTF8%ItemGUID",
            "RevisionGUID",
            "%UTF8%RevisionGUID",
            "RevisionName",
            "%UTF8%RevisionName",
        )
        normalized_order = {name.lower(): name for name in family_order}
        family_values: dict[str, tuple[str, object]] = {}
        result: dict[str, object] = {}
        for key, value in record.items():
            family_name = normalized_order.get(key.lower())
            if family_name is None:
                result[key] = value
            else:
                family_values[family_name] = (key, value)
        for family_name in family_order:
            if family_name in family_values:
                key, value = family_values[family_name]
                result[key] = value
        return result

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        """
        Build an oracle-aligned geometry record for this sheet symbol.
        """
        import math
        from .altium_record_sch__sheet_entry import AltiumSchSheetEntry

        from ._sch_source_projection import (
            _hierarchy_bound_children,
            _hierarchy_bound_field_slots,
        )

        children = _hierarchy_bound_children(
            self,
            ctx._source_admission.children(self, self.children),
            parent_by_source_id=ctx._source_admission.parent_by_source_id,
        )
        entries = [
            child for child in children if isinstance(child, AltiumSchSheetEntry)
        ]
        fields = _hierarchy_bound_field_slots(
            self,
            children,
            parent_by_source_id=ctx._source_admission.parent_by_source_id,
        )
        repeated = _is_repeat_name(getattr(fields.get("sheet_name"), "text", ""))

        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_rounded_rectangle_operation,
            make_pen,
            make_solid_brush,
            unwrap_record_operations,
            wrap_record_operations,
        )

        x, y = ctx.transform_coord_precise(self.location)
        width = (self.x_size + self.x_size_frac / 100000) * ctx.scale
        height = (self.y_size + self.y_size_frac / 100000) * ctx.scale
        fill_color_raw = (
            int(self.area_color) if self.area_color is not None else 0xFFFFFF
        )
        border_color_raw = int(self.color) if self.color is not None else 0x000000

        line_width_mils = {
            LineWidth.SMALLEST: 0.0,
            LineWidth.SMALL: 1.0,
            LineWidth.MEDIUM: 3.0,
            LineWidth.LARGE: 5.0,
        }
        stroke_width = _geometry_item_length(
            line_width_mils.get(self.line_width, 1.0),
            units_per_px=units_per_px,
        )
        corner_radius_px = 5 * ctx.scale if self.is_device_sheet else 0
        offsets = [4 * ctx.scale, 2 * ctx.scale, 0] if repeated else [0]

        operations: list[SchGeometryOp] = []
        min_svg_x = math.inf
        max_svg_x = -math.inf
        min_svg_y = math.inf
        max_svg_y = -math.inf

        rectangle_offsets = [0] if repeated and not self.is_solid else offsets
        for offset in rectangle_offsets:
            rect_x = x + offset
            rect_y = y + offset
            rect_right = rect_x + width
            rect_bottom = rect_y + height
            if self.is_solid:
                operations.append(
                    make_rounded_rectangle_operation(
                        x1_px=rect_x,
                        y1_px=rect_y,
                        x2_px=rect_right,
                        y2_px=rect_bottom,
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                        corner_x_radius_px=corner_radius_px,
                        corner_y_radius_px=corner_radius_px,
                        brush=make_solid_brush(fill_color_raw),
                    )
                )
            operations.append(
                make_rounded_rectangle_operation(
                    x1_px=rect_x,
                    y1_px=rect_y,
                    x2_px=rect_right,
                    y2_px=rect_bottom,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                    corner_x_radius_px=corner_radius_px,
                    corner_y_radius_px=corner_radius_px,
                    pen=make_pen(border_color_raw, width=stroke_width),
                )
            )
            min_svg_x = min(min_svg_x, rect_x)
            max_svg_x = max(max_svg_x, rect_right)
            min_svg_y = min(min_svg_y, rect_y)
            max_svg_y = max(max_svg_y, rect_bottom)

        if repeated and not self.is_solid:
            operations.extend(
                _sheet_symbol_open_stack_operations(
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    scale=ctx.scale,
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                    pen=make_pen(border_color_raw, width=stroke_width),
                )
            )
            min_svg_x = min(min_svg_x, x)
            max_svg_x = max(max_svg_x, x + width + 4 * ctx.scale)
            min_svg_y = min(min_svg_y, y)
            max_svg_y = max(max_svg_y, y + height + 4 * ctx.scale)

        child_bounds: list[SchGeometryBounds] = []
        for entry in entries:
            entry_record = entry.to_geometry(
                ctx,
                document_id=document_id,
                parent_x=x,
                parent_y=y,
                parent_width=width,
                parent_height=height,
                units_per_px=units_per_px,
            )
            if entry_record is None:
                continue
            operations.append(
                SchGeometryOp.begin_group(
                    entry.unique_id,
                    render_group_id=ctx.render_group_id(entry) or None,
                    render_group_identity=ctx.render_group_identity(entry),
                    render_source_id=id(entry),
                )
            )
            operations.extend(unwrap_record_operations(entry_record))
            operations.append(SchGeometryOp.end_group())
            if entry_record.bounds is not None:
                child_bounds.append(entry_record.bounds)

        for child in [
            child for child in children if not isinstance(child, AltiumSchSheetEntry)
        ]:
            to_geometry = getattr(child, "to_geometry", None)
            if not callable(to_geometry):
                continue
            child_record = cast(
                SchGeometryRecord,
                to_geometry(
                    ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                ),
            )
            if child_record is None:
                continue
            operations.append(
                SchGeometryOp.begin_group(
                    str(getattr(child, "unique_id", "") or ""),
                    render_group_id=ctx.render_group_id(child) or None,
                    render_group_identity=ctx.render_group_identity(child),
                    render_source_id=id(child),
                )
            )
            operations.extend(unwrap_record_operations(child_record))
            operations.append(SchGeometryOp.end_group())
            if child_record.bounds is not None:
                child_bounds.append(child_record.bounds)

        sheet_height = float(ctx.sheet_height or 0.0)
        bounds_left = int(math.floor(min_svg_x * 100000))
        bounds_top = int(math.floor((sheet_height - min_svg_y) * 100000))
        bounds_right = int(math.ceil(max_svg_x * 100000))
        bounds_bottom = int(math.ceil((sheet_height - max_svg_y) * 100000))
        for child_bounds_item in child_bounds:
            bounds_left = min(bounds_left, child_bounds_item.left)
            bounds_top = max(bounds_top, child_bounds_item.top)
            bounds_right = max(bounds_right, child_bounds_item.right)
            bounds_bottom = min(bounds_bottom, child_bounds_item.bottom)

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="sheetsymbol",
            object_id="eSheetSymbol",
            bounds=SchGeometryBounds(
                left=bounds_left,
                top=bounds_top,
                right=bounds_right,
                bottom=bounds_bottom,
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        symbol_type = "DeviceSheet" if self.is_device_sheet else "Normal"
        return f"<AltiumSchSheetSymbol type={symbol_type} size=({self.x_size}x{self.y_size})>"
