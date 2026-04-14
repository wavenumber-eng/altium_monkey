"""
Shared helpers for schematic component insertion from SchLib symbols.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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


def clone_symbol_children(
    symbol: object,
    component: "AltiumSchComponent",
    *,
    designator: str,
    part_id: int,
    font_id_map: dict[int, int],
) -> tuple[
    list[object],
    list[AltiumSchPin],
    list[object],
    list[object],
    list[AltiumSchLabel],
    list[AltiumSchTextFrame],
]:
    """
    Clone and transform library symbol children into schematic space.
    """
    graphics: list[object] = []
    for graphic in getattr(symbol, "graphic_primitives", []):
        if isinstance(graphic, AltiumSchPin):
            continue
        if not belongs_to_part(graphic, part_id):
            continue
        transformed = to_schematic_space(graphic, component, regenerate_id=True)
        remap_font_ids(transformed, font_id_map)
        graphics.append(transformed)

    pins: list[AltiumSchPin] = []
    for pin in getattr(symbol, "pins", []):
        if not belongs_to_part(pin, part_id):
            continue
        transformed_pin = to_schematic_space(pin, component, regenerate_id=True)
        remap_font_ids(transformed_pin, font_id_map)
        transformed_pin._source_is_binary = False
        pins.append(transformed_pin)

    parameters: list[object] = []
    for param in getattr(symbol, "parameters", []):
        transformed = to_schematic_space(param, component, regenerate_id=True)
        remap_font_ids(transformed, font_id_map)
        if (
            hasattr(param, "name")
            and getattr(param, "name", "")
            and str(getattr(param, "name")).upper() == "DESIGNATOR"
        ):
            transformed.text = designator
        parameters.append(transformed)

    has_designator = any(
        isinstance(param, AltiumSchDesignator)
        or (
            hasattr(param, "name")
            and getattr(param, "name", "")
            and str(getattr(param, "name")).upper() == "DESIGNATOR"
        )
        for param in parameters
    )
    if not has_designator:
        designator_record = AltiumSchDesignator()
        designator_record.text = designator
        designator_record.location = CoordPoint.from_mils(
            component.location.x_mils,
            component.location.y_mils + 100,
        )
        designator_record.unique_id = generate_unique_id()
        parameters.append(designator_record)

    images: list[object] = []
    for image in getattr(symbol, "images", []):
        if not belongs_to_part(image, part_id):
            continue
        images.append(to_schematic_space(image, component, regenerate_id=True))

    labels: list[AltiumSchLabel] = []
    for label in getattr(symbol, "labels", []):
        if not belongs_to_part(label, part_id):
            continue
        transformed = to_schematic_space(label, component, regenerate_id=True)
        remap_font_ids(transformed, font_id_map)
        labels.append(transformed)

    text_frames: list[AltiumSchTextFrame] = []
    for text_frame in getattr(symbol, "text_frames", []):
        if not belongs_to_part(text_frame, part_id):
            continue
        transformed = to_schematic_space(
            text_frame,
            component,
            regenerate_id=True,
        )
        remap_font_ids(transformed, font_id_map)
        text_frames.append(transformed)

    return graphics, pins, parameters, images, labels, text_frames
