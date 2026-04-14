"""
Internal schematic binding helpers for document/library-scoped resources.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Protocol

from .altium_record_types import SchFontSpec

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager


class _FontResolutionContext(Protocol):
    def get_font_manager(self) -> "FontIDManager | None": ...

    def font_spec_from_id(self, font_id: int | None) -> SchFontSpec | None: ...

    def resolve_font_spec(
        self, *, current_font_id: int | None, spec: SchFontSpec
    ) -> int: ...


class SchematicBindingContext:
    """
    Narrow bound context for schematic records.

    Records should not depend on the full ``AltiumSchDoc`` / ``AltiumSchLib``
    API surface just to resolve document-scoped resources such as fonts. This
    context exposes only the minimal document/library-owned services the record
    layer needs, while keeping the owning container referenced via ``weakref``.
    """

    def __init__(self, owner: object, *, kind: str) -> None:
        self._owner_ref = weakref.ref(owner)
        self.kind = kind

    @property
    def owner(self) -> object | None:
        """
        Owning schematic container, if it still exists.
        """
        return self._owner_ref()

    def get_font_manager(self) -> "FontIDManager | None":
        """
        Return the active font manager, if available.
        """
        owner = self.owner
        if owner is None:
            return None
        try:
            return getattr(owner, "font_manager", None)
        except ValueError:
            return None

    def font_spec_from_id(self, font_id: int | None) -> SchFontSpec | None:
        """
        Build a public ``SchFontSpec`` from a resolved font ID.
        """
        font_manager = self.get_font_manager()
        if font_manager is None or font_id is None or int(font_id) <= 0:
            return None
        font_info = font_manager.get_font_info(int(font_id))
        if not font_info:
            return None
        return SchFontSpec(
            name=str(font_info.get("name", "")),
            size=int(font_info.get("size", 10)),
            bold=bool(font_info.get("bold", False)),
            italic=bool(font_info.get("italic", False)),
            underline=bool(font_info.get("underline", False)),
            strikeout=bool(font_info.get("strikeout", False)),
        )

    def resolve_font_spec(
        self,
        *,
        current_font_id: int | None,
        spec: SchFontSpec,
    ) -> int:
        """
        Resolve a public font specification to a concrete font ID.

        Hidden font-table state such as rotation, underline, and strikeout is
        preserved from the current or default font entry where possible. This
        mirrors the established ``altium_cruncher clean`` behavior so bulk
        mutation can later move to the public record API without losing those
        details.
        """
        font_manager = self.get_font_manager()
        if font_manager is None:
            raise ValueError(
                "Cannot resolve SchFontSpec without a bound schematic font manager"
            )

        default_font_id = font_manager.get_default_font_id()
        effective_font_id = int(current_font_id or default_font_id)
        default_info = font_manager.get_font_info(default_font_id) or {}
        current_info = font_manager.get_font_info(effective_font_id) or {}
        effective_info = current_info or default_info

        return font_manager.get_or_create_font(
            font_name=spec.name,
            font_size=spec.size,
            bold=spec.bold,
            italic=spec.italic,
            rotation=int(
                effective_info.get("rotation", default_info.get("rotation", 0))
            ),
            underline=bool(
                effective_info.get("underline", default_info.get("underline", False))
            ),
            strikeout=bool(
                effective_info.get("strikeout", default_info.get("strikeout", False))
            ),
        )


class SingleFontBindableRecordMixin:
    """
    Shared public-font behavior for schematic records with one ``font_id`` slot.
    """

    _public_font_spec: SchFontSpec | None

    def _font_binding_slot_name(self) -> str:
        """
        Attribute name that stores the native font-table ID for this record.
        """
        return "font_id"

    def _init_single_font_binding(self) -> None:
        self._public_font_spec = None

    def _get_bound_schematic_context(self) -> SchematicBindingContext | None:
        return getattr(self, "_bound_schematic_context", None)

    def _get_fallback_font_manager(self) -> "FontIDManager | None":
        return getattr(self, "_font_manager", None)

    def _get_font_resolution_context(self) -> _FontResolutionContext | None:
        context = self._get_bound_schematic_context()
        if context is not None and context.get_font_manager() is not None:
            return context

        font_manager = self._get_fallback_font_manager()
        if font_manager is None:
            return None

        class _DetachedFontContext:
            def __init__(self, manager: "FontIDManager") -> None:
                self._manager = manager

            def get_font_manager(self) -> "FontIDManager":
                return self._manager

            def font_spec_from_id(self, font_id: int | None) -> SchFontSpec | None:
                if font_id is None or int(font_id) <= 0:
                    return None
                font_info = self._manager.get_font_info(int(font_id))
                if not font_info:
                    return None
                return SchFontSpec(
                    name=str(font_info.get("name", "")),
                    size=int(font_info.get("size", 10)),
                    bold=bool(font_info.get("bold", False)),
                    italic=bool(font_info.get("italic", False)),
                    underline=bool(font_info.get("underline", False)),
                    strikeout=bool(font_info.get("strikeout", False)),
                )

            def resolve_font_spec(
                self, *, current_font_id: int | None, spec: SchFontSpec
            ) -> int:
                default_font_id = self._manager.get_default_font_id()
                effective_font_id = int(current_font_id or default_font_id)
                default_info = self._manager.get_font_info(default_font_id) or {}
                current_info = self._manager.get_font_info(effective_font_id) or {}
                effective_info = current_info or default_info
                return self._manager.get_or_create_font(
                    font_name=spec.name,
                    font_size=spec.size,
                    bold=spec.bold,
                    italic=spec.italic,
                    rotation=int(
                        effective_info.get("rotation", default_info.get("rotation", 0))
                    ),
                    underline=bool(
                        effective_info.get(
                            "underline", default_info.get("underline", False)
                        )
                    ),
                    strikeout=bool(
                        effective_info.get(
                            "strikeout", default_info.get("strikeout", False)
                        )
                    ),
                )

        return _DetachedFontContext(font_manager)

    def _resolve_pending_public_font_spec(self) -> None:
        spec = self._public_font_spec
        if spec is None:
            return
        context = self._get_font_resolution_context()
        if context is None:
            return
        resolved_font_id = context.resolve_font_spec(
            current_font_id=getattr(self, self._font_binding_slot_name(), None),
            spec=spec,
        )
        setattr(self, self._font_binding_slot_name(), resolved_font_id)
        self._public_font_spec = None

    def _set_public_font_spec(self, font: SchFontSpec) -> None:
        if not isinstance(font, SchFontSpec):
            raise TypeError("font must be a SchFontSpec value")
        self._public_font_spec = font
        self._resolve_pending_public_font_spec()
        if self._public_font_spec is not None:
            setattr(self, self._font_binding_slot_name(), 0)

    def _bind_to_schematic_context(self, context: SchematicBindingContext) -> None:
        super()._bind_to_schematic_context(context)
        self._resolve_pending_public_font_spec()

    def _ensure_bound_public_font_ready(self) -> None:
        self._resolve_pending_public_font_spec()
        if (
            self._public_font_spec is not None
            and int(getattr(self, self._font_binding_slot_name(), 0)) <= 0
        ):
            raise ValueError(
                "Detached text objects with unresolved SchFontSpec must be added to a document or library before serialization"
            )

    @property
    def font(self) -> SchFontSpec | None:
        """
        Public font specification for this record.

        When the record is bound to a schematic document or library, assigning a
        font resolves ``font_id`` immediately via the owning font table.
        Detached objects may keep a pending ``SchFontSpec`` until they are added
        to a container.
        """
        if self._public_font_spec is not None:
            return self._public_font_spec
        context = self._get_font_resolution_context()
        if context is None:
            return None
        return context.font_spec_from_id(
            getattr(self, self._font_binding_slot_name(), None)
        )

    @font.setter
    def font(self, value: SchFontSpec) -> None:
        self._set_public_font_spec(value)
