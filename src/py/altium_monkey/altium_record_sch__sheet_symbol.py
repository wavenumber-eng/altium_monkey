"""Schematic record model for SchRecordType.SHEET_SYMBOL."""

import re
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_record_sch__sheet_entry import AltiumSchSheetEntry
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_sch_record_helpers import (
    bound_schematic_owner,
    detect_case_mode_method_from_dotted_uppercase_fields,
    remove_named_entry,
)
from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import LineWidth, SchGraphicalObject, SchRecordType
from .altium_serializer import AltiumSerializer, Fields

# Regex to detect REPEAT(designator,start,end) pattern (case-insensitive)
_REPEAT_PATTERN = re.compile(
    r"^REPEAT\s*\(\s*([^,]+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE
)


class SchSheetSymbolType(IntEnum):
    """
    Sheet symbol type (affects visual appearance).
    """

    NORMAL = 0
    DEVICE_SHEET = 1


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
        self.x_size: int = 400  # Width in mils
        self.y_size: int = 300  # Height in mils
        # SheetSymbol-specific fields
        self.is_solid: bool = True  # Fill interior
        self.line_width: LineWidth = LineWidth.SMALL  # Border thickness
        self.symbol_type: str = "Normal"  # "Normal" or "DeviceSheet"
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
        self._has_y_size: bool = False
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

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.SHEET_SYMBOL

    @property
    def is_device_sheet(self) -> bool:
        """
        Check if this is a device sheet (rounded corners).
        """
        return self.symbol_type.lower() in ("devicesheet", "device_sheet", "device")

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
        return bool(_REPEAT_PATTERN.match(text.strip()))

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
            child.parent = None
        if hasattr(child, "_bound_schematic_context"):
            child._bound_schematic_context = None

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

        # Parse dimensions
        self.x_size, self._has_x_size = s.read_int(record, Fields.X_SIZE, default=400)
        self.y_size, self._has_y_size = s.read_int(record, Fields.Y_SIZE, default=300)

        # Parse IsSolid
        self.is_solid, self._has_is_solid = s.read_bool(
            record, Fields.IS_SOLID, default=True
        )

        # Parse LineWidth
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=1
        )
        self.line_width = LineWidth(line_width_val)

        # Parse SymbolType
        self.symbol_type, self._has_symbol_type = s.read_str(
            record, Fields.SYMBOL_TYPE, default="Normal"
        )
        self.show_hidden_fields, self._has_show_hidden_fields = s.read_bool(
            record,
            Fields.SHOW_HIDDEN_FIELDS,
            default=False,
        )
        self.design_item_id, self._has_design_item_id = s.read_str(
            record,
            Fields.DESIGN_ITEM_ID,
            default="",
        )
        self.source_library_name, self._has_source_library_name = s.read_str(
            record,
            Fields.SOURCE_LIBRARY_NAME,
            default="",
        )
        self.vault_guid, self._has_vault_guid = s.read_str(
            record, Fields.VAULT_GUID, default=""
        )
        self.item_guid, self._has_item_guid = s.read_str(
            record, Fields.ITEM_GUID, default=""
        )
        self.revision_guid, self._has_revision_guid = s.read_str(
            record,
            Fields.REVISION_GUID,
            default="",
        )
        self.revision_name, self._has_revision_name = s.read_str(
            record,
            Fields.REVISION_NAME,
            default="",
        )

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        s.write_int(record, Fields.X_SIZE, self.x_size, raw)
        s.write_int(record, Fields.Y_SIZE, self.y_size, raw)
        s.write_bool(record, Fields.IS_SOLID, self.is_solid, raw)
        s.write_int(record, Fields.LINE_WIDTH, self.line_width.value, raw)
        s.write_str(record, Fields.SYMBOL_TYPE, self.symbol_type, raw)
        if self._has_show_hidden_fields or self.show_hidden_fields:
            s.write_bool(
                record, Fields.SHOW_HIDDEN_FIELDS, self.show_hidden_fields, raw
            )
        if self._has_design_item_id or self.design_item_id:
            s.write_str(record, Fields.DESIGN_ITEM_ID, self.design_item_id, raw)
        if self._has_source_library_name or self.source_library_name:
            s.write_str(
                record, Fields.SOURCE_LIBRARY_NAME, self.source_library_name, raw
            )
        if self._has_vault_guid or self.vault_guid:
            s.write_str(record, Fields.VAULT_GUID, self.vault_guid, raw)
        if self._has_item_guid or self.item_guid:
            s.write_str(record, Fields.ITEM_GUID, self.item_guid, raw)
        if self._has_revision_guid or self.revision_guid:
            s.write_str(record, Fields.REVISION_GUID, self.revision_guid, raw)
        if self._has_revision_name or self.revision_name:
            s.write_str(record, Fields.REVISION_NAME, self.revision_name, raw)
        s.remove_field(record, Fields.TRANSPARENT)
        s.remove_field(record, Fields.LINE_STYLE)
        s.remove_field(record, Fields.LINE_STYLE_EXT)
        return record

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

        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            unwrap_record_operations,
            wrap_record_operations,
        )

        x, y = ctx.transform_point(self.location.x, self.location.y)
        width = self.x_size * ctx.scale
        height = self.y_size * ctx.scale
        fill_color_raw = (
            int(self.area_color) if self.area_color is not None else 0xFFFFFF
        )
        border_color_raw = int(self.color) if self.color is not None else 0x000000

        line_width_map = {
            LineWidth.SMALLEST: 0,
            LineWidth.SMALL: 0,
            LineWidth.MEDIUM: 3 * units_per_px,
            LineWidth.LARGE: 5 * units_per_px,
        }
        stroke_width = line_width_map.get(self.line_width, 1 * units_per_px)
        corner_radius = 50 * ctx.scale * units_per_px if self.is_device_sheet else 0
        offsets = [4 * ctx.scale, 2 * ctx.scale, 0] if self.is_multichannel() else [0]

        operations: list[SchGeometryOp] = []
        min_svg_x = math.inf
        max_svg_x = -math.inf
        min_svg_y = math.inf
        max_svg_y = -math.inf

        for offset in offsets:
            rect_x = x + offset
            rect_y = y + offset
            rect_right = rect_x + width
            rect_bottom = rect_y + height
            geo_left, geo_top = svg_coord_to_geometry(
                rect_x,
                rect_y,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            geo_right, geo_bottom = svg_coord_to_geometry(
                rect_right,
                rect_bottom,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            if self.is_solid:
                operations.append(
                    SchGeometryOp.rounded_rectangle(
                        x1=geo_left,
                        y1=geo_top,
                        x2=geo_right,
                        y2=geo_bottom,
                        corner_x_radius=corner_radius,
                        corner_y_radius=corner_radius,
                        brush=make_solid_brush(fill_color_raw),
                    )
                )
            operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=geo_left,
                    y1=geo_top,
                    x2=geo_right,
                    y2=geo_bottom,
                    corner_x_radius=corner_radius,
                    corner_y_radius=corner_radius,
                    pen=make_pen(border_color_raw, width=stroke_width),
                )
            )
            min_svg_x = min(min_svg_x, rect_x)
            max_svg_x = max(max_svg_x, rect_right)
            min_svg_y = min(min_svg_y, rect_y)
            max_svg_y = max(max_svg_y, rect_bottom)

        child_bounds: list[SchGeometryBounds] = []
        for entry in self.entries:
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
            operations.append(SchGeometryOp.begin_group(entry.unique_id))
            operations.extend(unwrap_record_operations(entry_record))
            operations.append(SchGeometryOp.end_group())
            if entry_record.bounds is not None:
                child_bounds.append(entry_record.bounds)

        for child in [child for child in self.children if child not in self.entries]:
            if not hasattr(child, "to_geometry"):
                continue
            child_record = child.to_geometry(
                ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if child_record is None:
                continue
            operations.append(SchGeometryOp.begin_group(child.unique_id))
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
