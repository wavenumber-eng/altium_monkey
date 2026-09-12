"""
Shared helpers for schematic component insertion from SchLib symbols.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__label import AltiumSchLabel
from .altium_record_sch__pin import AltiumSchPin
from .altium_record_sch__text_frame import AltiumSchTextFrame
from .altium_record_types import CoordPoint
from .altium_symbol_transform import generate_unique_id, to_schematic_space

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_record_sch__component import AltiumSchComponent
    from .altium_schlib import AltiumSchLib


@dataclass(frozen=True)
class ClonedSymbolChildren:
    """
    Cloned SchLib children grouped for bookkeeping and ordered for placement.
    """

    ordered_children: list[object]
    graphics: list[object]
    pins: list[AltiumSchPin]
    parameters: list[object]
    images: list[object]
    labels: list[AltiumSchLabel]
    text_frames: list[AltiumSchTextFrame]


_CloneKind = Literal[
    "graphic",
    "pin",
    "parameter",
    "image",
    "label",
    "text_frame",
]


@dataclass(frozen=True)
class _SymbolChildIndex:
    graphics: set[int]
    pins: set[int]
    parameters: set[int]
    designators: set[int]
    images: set[int]
    labels: set[int]
    text_frames: set[int]
    implementation_lists: set[int]


@dataclass
class _CloneBuckets:
    ordered_children: list[object]
    graphics: list[object]
    pins: list[AltiumSchPin]
    parameters: list[object]
    images: list[object]
    labels: list[AltiumSchLabel]
    text_frames: list[AltiumSchTextFrame]

    @classmethod
    def empty(cls) -> "_CloneBuckets":
        return cls(
            ordered_children=[],
            graphics=[],
            pins=[],
            parameters=[],
            images=[],
            labels=[],
            text_frames=[],
        )

    def append(self, kind: _CloneKind, child: object) -> None:
        self.ordered_children.append(child)
        if kind == "graphic":
            self.graphics.append(child)
        elif kind == "pin":
            self.pins.append(cast(AltiumSchPin, child))
        elif kind == "parameter":
            self.parameters.append(child)
        elif kind == "image":
            self.images.append(child)
        elif kind == "label":
            self.labels.append(cast(AltiumSchLabel, child))
        elif kind == "text_frame":
            self.text_frames.append(cast(AltiumSchTextFrame, child))

    def to_public(self) -> ClonedSymbolChildren:
        return ClonedSymbolChildren(
            ordered_children=self.ordered_children,
            graphics=self.graphics,
            pins=self.pins,
            parameters=self.parameters,
            images=self.images,
            labels=self.labels,
            text_frames=self.text_frames,
        )


def remap_font_ids(obj: object, font_id_map: dict[int, int]) -> None:
    """
    Remap record-local font IDs into the destination document font table.
    """
    if hasattr(obj, "font_id"):
        fid = getattr(obj, "font_id", None)
        if fid is not None and fid in font_id_map:
            setattr(obj, "font_id", font_id_map[fid])

    if isinstance(obj, AltiumSchPin):
        for settings in (obj.name_settings, obj.designator_settings):
            fid = settings.font_id
            if fid is not None and fid in font_id_map:
                settings.font_id = font_id_map[fid]

    if hasattr(obj, "text_font_id"):
        fid = getattr(obj, "text_font_id", None)
        if fid is not None and fid in font_id_map:
            setattr(obj, "text_font_id", font_id_map[fid])


def load_or_cache_schlib(
    cache: dict[Path, "AltiumSchLib"],
    library_path: Path,
) -> "AltiumSchLib":
    """
    Load a SchLib once and reuse the parsed instance.
    """
    from .altium_schlib import AltiumSchLib

    resolved = library_path.resolve()
    if resolved not in cache:
        cache[resolved] = AltiumSchLib(resolved)
    return cache[resolved]


def merge_schlib_fonts(
    target_font_manager: "FontIDManager",
    schlib: "AltiumSchLib",
) -> dict[int, int]:
    """
    Merge library fonts into the destination schematic font table.
    """
    font_id_map: dict[int, int] = {}
    if schlib.font_manager and hasattr(schlib.font_manager, "_sheet"):
        for lib_id, font_data in schlib.font_manager._sheet.fonts.items():
            doc_id = target_font_manager.get_or_create_font(
                font_name=font_data.get("name", "Times New Roman"),
                font_size=font_data.get("size", 10),
                bold=font_data.get("bold", False),
                italic=font_data.get("italic", False),
                rotation=font_data.get("rotation", 0),
                underline=font_data.get("underline", False),
                strikeout=font_data.get("strikeout", False),
            )
            font_id_map[int(lib_id)] = doc_id
    return font_id_map


def belongs_to_part(obj: object, part_id: int) -> bool:
    """
    Return whether a symbol child belongs to the requested multipart section.
    """
    owner_part_id = getattr(obj, "owner_part_id", -1)
    return owner_part_id == -1 or owner_part_id == part_id


def _ordered_symbol_children(symbol: object) -> list[object]:
    """
    Return symbol children in source order with typed-view fallbacks.
    """
    ordered: list[object] = list(getattr(symbol, "objects", []) or [])
    seen = {id(child) for child in ordered}
    fallback_children: list[object] = []

    for collection_name in (
        "graphic_primitives",
        "pins",
        "parameters",
        "designators",
        "images",
        "labels",
        "text_frames",
    ):
        for child in getattr(symbol, collection_name, []) or []:
            child_id = id(child)
            if child_id in seen:
                continue
            seen.add(child_id)
            fallback_children.append(child)

    fallback_children.sort(
        key=lambda child: int(getattr(child, "_record_index", 999999999) or 999999999)
    )
    return ordered + fallback_children


def _symbol_child_index(symbol: object) -> _SymbolChildIndex:
    return _SymbolChildIndex(
        graphics={
            id(graphic)
            for graphic in getattr(symbol, "graphic_primitives", [])
            if not isinstance(graphic, AltiumSchPin)
        },
        pins={id(pin) for pin in getattr(symbol, "pins", [])},
        parameters={id(param) for param in getattr(symbol, "parameters", [])},
        designators={id(param) for param in getattr(symbol, "designators", [])},
        images={id(image) for image in getattr(symbol, "images", [])},
        labels={id(label) for label in getattr(symbol, "labels", [])},
        text_frames={
            id(text_frame) for text_frame in getattr(symbol, "text_frames", [])
        },
        implementation_lists={
            id(implementation_list)
            for implementation_list in getattr(symbol, "implementation_lists", [])
        },
    )


def _child_clone_kind(source_id: int, index: _SymbolChildIndex) -> _CloneKind | None:
    if source_id in index.graphics:
        return "graphic"
    if source_id in index.pins:
        return "pin"
    if (
        source_id in index.parameters
        or source_id in index.designators
        or source_id in index.implementation_lists
    ):
        return "parameter"
    if source_id in index.images:
        return "image"
    if source_id in index.labels:
        return "label"
    if source_id in index.text_frames:
        return "text_frame"
    return None


def _clone_child_in_schematic_space(
    source_child: object,
    component: "AltiumSchComponent",
    *,
    font_id_map: dict[int, int],
) -> object:
    transformed = to_schematic_space(source_child, component, regenerate_id=True)
    _prepare_cloned_tree(transformed, font_id_map=font_id_map)
    return transformed


def _prepare_cloned_tree(
    root: object,
    *,
    font_id_map: dict[int, int],
) -> None:
    """Detach cloned records and refresh descendant serialization identity."""
    pending = [(root, True)]
    seen: set[int] = set()
    while pending:
        current, is_root = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        _prepare_cloned_record(
            current,
            is_root=is_root,
            font_id_map=font_id_map,
        )
        children = getattr(current, "children", None)
        if isinstance(children, list):
            pending.extend((child, False) for child in children)


def _prepare_cloned_record(
    record: object,
    *,
    is_root: bool,
    font_id_map: dict[int, int],
) -> None:
    if hasattr(record, "_bound_schematic_context"):
        setattr(record, "_bound_schematic_context", None)
    if not is_root and hasattr(record, "_raw_record"):
        setattr(record, "_raw_record", None)
    unique_id = getattr(record, "unique_id", None)
    if not is_root and unique_id is not None:
        unlock = getattr(record, "_set_unique_id_locked", None)
        if callable(unlock):
            unlock(False)
        setattr(record, "unique_id", generate_unique_id())
    remap_font_ids(record, font_id_map)


def _is_designator_child(source_child: object) -> bool:
    if isinstance(source_child, AltiumSchDesignator):
        return True
    name = getattr(source_child, "name", "")
    return bool(name) and str(name).upper() == "DESIGNATOR"


def _clone_symbol_child(
    kind: _CloneKind,
    source_child: object,
    component: "AltiumSchComponent",
    *,
    designator: str,
    part_id: int,
    font_id_map: dict[int, int],
) -> object | None:
    if kind != "parameter" and not belongs_to_part(source_child, part_id):
        return None

    if kind == "image":
        transformed_image = to_schematic_space(
            source_child,
            component,
            regenerate_id=True,
        )
        _prepare_cloned_tree(transformed_image, font_id_map=font_id_map)
        return transformed_image

    transformed = _clone_child_in_schematic_space(
        source_child,
        component,
        font_id_map=font_id_map,
    )
    if kind == "pin":
        setattr(transformed, "_source_is_binary", False)
    elif kind == "parameter" and _is_designator_child(source_child):
        setattr(transformed, "text", designator)
    return transformed


def _ensure_designator(
    buckets: _CloneBuckets,
    component: "AltiumSchComponent",
    designator: str,
) -> None:
    if _contains_designator(buckets.parameters):
        return

    designator_record = AltiumSchDesignator()
    designator_record.text = designator
    designator_record.location = CoordPoint.from_mils(
        component.location.x_mils,
        component.location.y_mils + 100,
    )
    designator_record.unique_id = generate_unique_id()
    buckets.parameters.append(designator_record)
    buckets.ordered_children.append(designator_record)


def _contains_designator(parameters: list[object]) -> bool:
    return any(_is_designator_child(param) for param in parameters)


def clone_symbol_children(
    symbol: object,
    component: "AltiumSchComponent",
    *,
    designator: str,
    part_id: int,
    font_id_map: dict[int, int],
) -> ClonedSymbolChildren:
    """
    Clone and transform library symbol children into schematic space.
    """
    index = _symbol_child_index(symbol)
    buckets = _CloneBuckets.empty()

    for source_child in _ordered_symbol_children(symbol):
        kind = _child_clone_kind(id(source_child), index)
        if kind is None:
            continue
        transformed = _clone_symbol_child(
            kind,
            source_child,
            component,
            designator=designator,
            part_id=part_id,
            font_id_map=font_id_map,
        )
        if transformed is not None:
            buckets.append(kind, transformed)

    _ensure_designator(buckets, component, designator)
    return buckets.to_public()
