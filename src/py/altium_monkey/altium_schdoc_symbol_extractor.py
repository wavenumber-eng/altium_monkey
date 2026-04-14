"""
Extract symbol definitions from placed SchDoc components into SchLib files.
"""

import logging
import random
import string
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from .altium_font_manager import FontIDManager
from .altium_record_sch__implementation import (
    AltiumSchImplementation,
    AltiumSchImplementationList,
)
from .altium_record_sch__parameter import AltiumSchParameter
from .altium_record_types import SchRecordType
from .altium_symbol_transform import normalize_rectangle_coords, to_symbol_space

log = logging.getLogger(__name__)


def extract_symbols_from_schdoc_file(
    schdoc_path: Path,
    output_dir: Path,
    debug: bool = False,
    strip_parameters: bool = True,
    strip_implementations: bool = True,
) -> dict[str, bool]:
    """
    Extract all symbols from a SchDoc file and save as individual SchLib files.

    Components are deduplicated by their symbol identifier: DesignItemId when
    present, otherwise LibReference. If multiple placed components share that
    identifier, extraction writes one symbol using a representative placement.
    This intentionally does not reproduce Altium's interactive duplicate-symbol
    dialog, which can emit suffixed variants for components that are graphically
    equivalent but differ in raw Comment expressions, source library metadata, or
    other component parameters.

    Args:
        schdoc_path: Path to SchDoc file
        output_dir: Directory to save SchLib files
        debug: Enable debug output
        strip_parameters: If True (default), don't include component PARAMETER
            records. This matches the historical extractor behavior and is
            useful when preparing symbols for database-library workflows.
        strip_implementations: If True (default), don't include IMPLEMENTATION records.
            This matches Altium's Library Splitter "Remove Models" option.

    Returns:
        Dict mapping symbol name -> success status
    """
    # Import here to avoid circular imports
    from .altium_schdoc import AltiumSchDoc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Extracting symbols from: {schdoc_path.name}")

    # Parse SchDoc using the modern parser (handles OLE, hierarchy, embedded images)
    schdoc = AltiumSchDoc(schdoc_path)

    # Group components by DesignItemId (actual part number) or LibReference
    components_by_symbol = _group_components_by_symbol(schdoc.components)

    if debug:
        log.info(f"Found {len(components_by_symbol)} unique symbol types")
        for symbol_key, instances in components_by_symbol.items():
            log.info(f"  {symbol_key}: {len(instances)} instance(s)")

    results: dict[str, bool] = {}

    for symbol_key, instances in components_by_symbol.items():
        try:
            # Select best instance (prefer non-mirrored, non-rotated)
            template = _select_best_instance(instances)

            if debug and len(instances) > 1:
                log.info(
                    f"  Selected instance for {symbol_key}: "
                    f"orientation={template.orientation.value}, "
                    f"mirrored={template.is_mirrored}"
                )

            schlib, safe_name, symbol = _create_schlib_symbol_from_template(
                symbol_key,
                template,
                schdoc,
                current_part_id=_first_instance_current_part_id(instances),
                strip_parameters=strip_parameters,
                strip_implementations=strip_implementations,
                debug=debug,
            )
            output_path = output_dir / f"{safe_name}.SchLib"
            schlib.save(output_path, sync_pin_text_data=True)
            results[symbol_key] = True

            if debug:
                log.info(
                    f"  Saved: {output_path.name} "
                    f"({len(template.pins)} pins, {len(template.graphics)} graphics)"
                )

        except Exception as e:
            log.error(f"Failed to extract {symbol_key}: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            results[symbol_key] = False

    log.info(f"Extracted {sum(results.values())}/{len(results)} symbols successfully")
    return results


def _group_components_by_symbol(components: list) -> dict[str, list]:
    """
    Group components by their symbol identifier.

    Uses DesignItemId (actual part number) if available, otherwise LibReference.
    This matches how database library components are identified.

    This is a deliberate first-release behavior: functionally equivalent
    placements with the same symbol identifier collapse to one extracted symbol
    even when Altium's interactive exporter might offer to create suffixed
    variants based on raw Comment/source-library/parameter differences.

    Args:
        components: List of AltiumSchComponent objects

    Returns:
        Dict mapping symbol key -> list of component instances
    """
    components_by_symbol = defaultdict(list)

    for comp in components:
        # Prefer DesignItemId (database library component)
        # Fallback to LibReference (direct library component)
        symbol_key = comp.design_item_id or comp.lib_reference

        if symbol_key and symbol_key not in ("*", ""):
            components_by_symbol[symbol_key].append(comp)

    return dict(components_by_symbol)


def _select_best_instance(instances: list) -> Any:
    """
    Select the best component instance for symbol extraction.

    Prefers non-mirrored, non-rotated instances to minimize transformation errors.

    Args:
        instances: List of AltiumSchComponent objects

    Returns:
        The best component instance to use as template
    """

    def score(comp: object) -> int:
        # Lower is better: no mirror (0) + no rotation (0)
        mirror_penalty = 10 if comp.is_mirrored else 0
        rotation = (
            comp.orientation.value
            if hasattr(comp.orientation, "value")
            else int(comp.orientation)
        )
        return mirror_penalty + rotation

    return min(instances, key=score)


def _copy_component_metadata(source: object, target_symbol: object) -> None:
    """
    Copy metadata from a component to a symbol.

    Preserves DbLib-related fields for round-trip compatibility.

    Args:
        source: AltiumSchComponent object
        target_symbol: AltiumSymbol object
    """
    # Description (regular and UTF-8)
    if source.component_description:
        target_symbol.description = source.component_description
    if source.utf8_component_description:
        target_symbol.utf8_description = source.utf8_component_description

    # Database library fields - only copy if they were present in original
    # (component parser tracks _has_* flags for fields that may default to '*')
    if source.database_table_name:
        target_symbol.database_table_name = source.database_table_name
    # SourceLibraryName: copy if present and not '*'
    if (
        hasattr(source, "_has_source_library_name")
        and source._has_source_library_name
        or source.source_library_name
        and source.source_library_name != "*"
    ):
        target_symbol.source_library_name = source.source_library_name
    # LibraryPath: copy if present in original (even if '*')
    if hasattr(source, "_has_library_path") and source._has_library_path:
        target_symbol.library_path = source.library_path
    # TargetFileName: copy if present in original (even if '*')
    if hasattr(source, "_has_target_filename") and source._has_target_filename:
        target_symbol.target_file_name = source.target_filename

    # Component classification - only set if not default (0 = Standard)
    # Altium's Library Splitter doesn't output ComponentKind=0
    if hasattr(source, "component_kind"):
        kind_val = (
            source.component_kind.value
            if hasattr(source.component_kind, "value")
            else int(source.component_kind)
        )
        if kind_val != 0:
            target_symbol.component_kind = kind_val

    # ComponentKindVersion2 (5=Standard_NoBOM)
    if (
        hasattr(source, "component_kind_version2")
        and source.component_kind_version2 is not None
    ):
        target_symbol.component_kind_version2 = source.component_kind_version2

    # PartIDLocked (uses Export_Boolean_WithDefault in Altium)
    if hasattr(source, "part_id_locked"):
        target_symbol.part_id_locked = source.part_id_locked

    # Component colors - copy if present in original
    # Note: AreaColor is copied if present (even if white)
    # Color is NOT copied if 0 (Altium's Library Splitter omits Color=0)
    if getattr(source, "_has_area_color", False):
        target_symbol.area_color = source.area_color
    if getattr(source, "_has_color", False) and source.color != 0:
        target_symbol.color = source.color
    # Only set override_colors if True - False is the default and shouldn't be written
    if getattr(source, "override_colors", False):
        target_symbol.override_colors = source.override_colors

    # Display mode count for multi-display-mode symbols
    if hasattr(source, "display_mode_count") and source.display_mode_count > 1:
        target_symbol.display_mode_count = source.display_mode_count


def _create_schlib_symbol_from_template(
    symbol_key: str,
    template: object,
    schdoc: object,
    *,
    current_part_id: int | None = None,
    strip_parameters: bool = True,
    strip_implementations: bool = True,
    debug: bool = False,
) -> tuple[object, str, object]:
    from .altium_schlib import AltiumSchLib

    schlib = AltiumSchLib()
    _copy_font_registry_from_schdoc(schlib, schdoc)

    display_name = _symbol_display_name(symbol_key, template)
    safe_name = _sanitize_filename(display_name)
    symbol = schlib.add_symbol(safe_name)
    symbol.original_name = symbol_key

    _copy_component_metadata(template, symbol)
    _set_symbol_part_count(template, symbol)
    symbol.component_record = _build_symbol_component_record(
        symbol_key=symbol_key,
        safe_name=safe_name,
        template=template,
        current_part_id=current_part_id,
    )

    _add_transformed_component_children(
        template,
        symbol,
        schlib,
        schdoc,
        strip_parameters=strip_parameters,
        debug=debug,
    )
    if not strip_implementations:
        _add_implementations(template, symbol)

    return schlib, safe_name, symbol


def _ordered_component_children(template: object) -> list[object]:
    """
    Return component children in source file order, with defensive fallbacks.
    """
    ordered_children = list(getattr(template, "children", []) or [])
    seen = {id(child) for child in ordered_children}

    missing_children: list[object] = []
    for collection_name in ("pins", "parameters", "graphics"):
        for child in getattr(template, collection_name, []) or []:
            if id(child) in seen:
                continue
            seen.add(id(child))
            missing_children.append(child)

    missing_children.sort(
        key=lambda child: int(getattr(child, "_record_index", 999999999) or 999999999)
    )
    return ordered_children + missing_children


def _clear_extracted_record_state(obj: object) -> None:
    """
    Detach a cloned schematic child from its SchDoc ownership state.

    Extracted SchLib records are re-owned by the symbol serializer, so stale
    SchDoc owner indexes and raw records must not leak into the generated
    symbol.
    """
    if hasattr(obj, "parent"):
        obj.parent = None
    if hasattr(obj, "owner_index"):
        obj.owner_index = 0
    if hasattr(obj, "index_in_sheet"):
        obj.index_in_sheet = None
    if hasattr(obj, "_raw_record"):
        obj._raw_record = None
    if hasattr(obj, "_record_index"):
        obj._record_index = None


def _add_transformed_component_children(
    template: object,
    symbol: object,
    schlib: object,
    schdoc: object,
    *,
    strip_parameters: bool,
    debug: bool = False,
) -> None:
    """
    Copy pins, graphics, and optionally parameters in source child order.
    """
    pin_ids = {id(pin) for pin in getattr(template, "pins", [])}
    parameter_ids = {
        id(param)
        for param in getattr(template, "parameters", [])
        if isinstance(param, AltiumSchParameter)
    }
    graphic_ids = {id(graphic) for graphic in getattr(template, "graphics", [])}

    for child in _ordered_component_children(template):
        child_id = id(child)
        if child_id in pin_ids:
            symbol.add_pin(_transform_pin_for_symbol(child, template, schdoc))
            continue

        if child_id in graphic_ids:
            transformed = _transform_graphic_for_symbol(child, template)
            if _is_transformed_image(transformed):
                _add_embedded_image_transformed(
                    transformed,
                    symbol,
                    schlib,
                    getattr(schdoc, "embedded_images", {}) or {},
                    debug=debug,
                )
            else:
                symbol.add_object(transformed)
            continue

        if strip_parameters or child_id not in parameter_ids:
            continue
        transformed = to_symbol_space(child, template)
        _clear_extracted_record_state(transformed)
        symbol.add_object(transformed)


def _is_transformed_image(obj: object) -> bool:
    from .altium_record_sch__image import AltiumSchImage

    return isinstance(obj, AltiumSchImage)


def _transform_graphic_for_symbol(graphic: object, template: object) -> object:
    transformed = to_symbol_space(graphic, template)
    if hasattr(transformed, "record_type") and transformed.record_type in (
        SchRecordType.RECTANGLE,
        SchRecordType.ROUND_RECTANGLE,
    ):
        normalize_rectangle_coords(transformed)
    if hasattr(transformed, "owner_index"):
        transformed.owner_index = 0
    for attr in (
        "_has_location_x",
        "_has_location_y",
        "_has_corner_x",
        "_has_corner_y",
    ):
        if hasattr(transformed, attr):
            setattr(transformed, attr, False)

    if _is_transformed_image(transformed):
        if hasattr(transformed, "_raw_record") and isinstance(
            transformed._raw_record, dict
        ):
            transformed._raw_record.pop("OwnerIndex", None)
            transformed._raw_record.pop("OWNERINDEX", None)
        return transformed

    if hasattr(transformed, "_raw_record"):
        transformed._raw_record = None
    if (
        getattr(transformed, "record_type", None) == SchRecordType.ARC
        and getattr(transformed, "radius", 0) == 0
    ):
        setattr(transformed, "_has_radius", False)
    return transformed


def _clone_implementation_child(child: object) -> object:
    cloned = deepcopy(child)
    _clear_extracted_record_state(cloned)
    return cloned


def _add_implementations(template: object, symbol: object) -> None:
    """
    Copy component implementation lists and model children into the symbol.
    """
    for impl_list in getattr(template, "parameters", []):
        if not isinstance(impl_list, AltiumSchImplementationList):
            continue

        for implementation in getattr(impl_list, "children", []):
            if not isinstance(implementation, AltiumSchImplementation):
                continue
            cloned_implementation = deepcopy(implementation)
            _clear_extracted_record_state(cloned_implementation)
            children = [
                _clone_implementation_child(child)
                for child in getattr(implementation, "children", [])
            ]
            symbol.add_implementation(cloned_implementation, children)


def _build_symbol_component_record(
    *,
    symbol_key: str,
    safe_name: str,
    template: object,
    current_part_id: int | None = None,
) -> dict[str, str]:
    actual_part_count = _get_actual_part_count(template)
    resolved_current_part_id = (
        int(current_part_id)
        if current_part_id is not None
        else int(getattr(template, "current_part_id", 1) or 1)
    )
    unique_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

    record: dict[str, str] = {
        "RECORD": "1",
        "LibReference": symbol_key,
        "PartCount": str(actual_part_count + 1),
        "DisplayModeCount": str(int(getattr(template, "display_mode_count", 1) or 1)),
        "IndexInSheet": "-1",
        "OwnerPartId": "-1",
        "CurrentPartId": str(resolved_current_part_id),
        "UniqueID": unique_id,
        "AreaColor": str(_resolve_component_area_color(template)),
        "PartIDLocked": "T"
        if bool(getattr(template, "part_id_locked", False))
        else "F",
        "NotUseDBTableName": "T",
        "DesignItemId": symbol_key,
    }

    color_value = _resolve_component_color(template)
    if color_value is not None:
        record["Color"] = str(color_value)

    override_colors = _resolve_component_override_colors(template)
    if override_colors is not None:
        record["OverideColors"] = "T" if override_colors else "F"

    if len(getattr(template, "pins", [])) > 0:
        record["AllPinCount"] = str(len(template.pins))

    component_description = getattr(template, "component_description", "")
    if component_description:
        record["ComponentDescription"] = component_description

    utf8_description = getattr(template, "utf8_component_description", "")
    if utf8_description:
        record["%UTF8%ComponentDescription"] = utf8_description

    component_kind = getattr(template, "component_kind", None)
    if component_kind is not None:
        kind_value = (
            component_kind.value
            if hasattr(component_kind, "value")
            else int(component_kind)
        )
        record["ComponentKind"] = str(kind_value)

    component_kind_version2 = getattr(template, "component_kind_version2", None)
    if component_kind_version2 is not None:
        record["ComponentKindVersion2"] = str(component_kind_version2)

    display_mode = int(getattr(template, "display_mode", 0) or 0)
    if display_mode != 0:
        record["DisplayMode"] = str(display_mode)

    database_table_name = getattr(template, "database_table_name", "")
    if database_table_name:
        record["DatabaseTableName"] = database_table_name

    source_library_name = getattr(template, "source_library_name", "")
    if getattr(template, "_has_source_library_name", False) or (
        source_library_name and source_library_name != "*"
    ):
        record["SourceLibraryName"] = source_library_name
    else:
        record["SourceLibraryName"] = f"{safe_name}.SchLib"

    if getattr(template, "_has_library_path", False):
        record["LibraryPath"] = str(getattr(template, "library_path", "*"))

    if getattr(template, "_has_target_filename", False):
        record["TargetFileName"] = str(getattr(template, "target_filename", "*"))

    raw_record = getattr(template, "_raw_record", None)
    if isinstance(raw_record, dict):
        _copy_matching_utf8_identity_fields(record, raw_record, symbol_key)
        for key, value in raw_record.items():
            if key.upper().startswith("UNHANDLED"):
                record[key] = value

    return record


def _first_instance_current_part_id(instances: list[object]) -> int:
    """
    Return the CurrentPartId Altium uses for extracted component metadata.

    The geometry template may be a later placement selected to avoid mirrored
    or rotated source graphics, but Altium keeps CurrentPartId from the first
    source instance in the grouped symbol set.
    """
    if not instances:
        return 1
    return int(getattr(instances[0], "current_part_id", 1) or 1)


def _copy_matching_utf8_identity_fields(
    record: dict[str, str],
    raw_record: dict[str, object],
    symbol_key: str,
) -> None:
    """
    Preserve UTF-8 identity fields only when they correspond to the symbol key.
    """
    identity_fields = (
        ("LibReference", "%UTF8%LibReference", "%UTF8%LIBREFERENCE"),
        ("DesignItemId", "%UTF8%DesignItemId", "%UTF8%DESIGNITEMID"),
    )
    for ascii_key, *utf8_keys in identity_fields:
        ascii_value = raw_record.get(ascii_key) or raw_record.get(ascii_key.upper())
        if str(ascii_value or "") != symbol_key:
            continue
        for utf8_key in utf8_keys:
            value = raw_record.get(utf8_key)
            if value:
                record[utf8_key] = str(value)


def _symbol_display_name(symbol_key: str, template: object) -> str:
    """
    Resolve the SchLib storage/header name for an extracted symbol.

    Altium stores ASCII identity fields and optional ``%UTF8%`` display
    variants. When present, the UTF-8 LibReference is used as the SchLib symbol
    storage name while the ASCII LibReference/DesignItemId fields remain in the
    component record.
    """
    raw_record = getattr(template, "_raw_record", None)
    if isinstance(raw_record, dict):
        for ascii_key, utf8_keys in (
            ("DesignItemId", ("%UTF8%DesignItemId", "%UTF8%DESIGNITEMID")),
            ("LibReference", ("%UTF8%LibReference", "%UTF8%LIBREFERENCE")),
        ):
            ascii_value = raw_record.get(ascii_key) or raw_record.get(ascii_key.upper())
            if str(ascii_value or "") != symbol_key:
                continue
            for utf8_key in utf8_keys:
                value = raw_record.get(utf8_key)
                if value:
                    return str(value)
    return symbol_key


def _get_actual_part_count(template: object) -> int:
    part_count_stored = int(getattr(template, "part_count", 1) or 1)
    return part_count_stored - 1 if part_count_stored > 1 else 1


def _resolve_component_area_color(template: object) -> int:
    if getattr(template, "_has_area_color", False):
        return int(getattr(template, "area_color", 11599871) or 11599871)
    return 11599871


def _resolve_component_color(template: object) -> int | None:
    if getattr(template, "_has_color", False):
        color = int(getattr(template, "color", 0) or 0)
        if color != 0:
            return color
    return None


def _resolve_component_override_colors(template: object) -> bool | None:
    if bool(getattr(template, "override_colors", False)):
        return True
    return None


def _copy_font_registry_from_schdoc(schlib: object, schdoc: object) -> None:
    if schdoc.font_manager is None:
        schlib.font_manager = FontIDManager.from_font_dict({})
        return
    fonts = {
        int(font_id): dict(font_info)
        for font_id, font_info in schdoc.font_manager.fonts.items()
    }
    schlib.font_manager = FontIDManager.from_font_dict(fonts)


def _set_symbol_part_count(template: object, symbol: object) -> None:
    actual_part_count = _get_actual_part_count(template)
    if actual_part_count > 1:
        symbol.set_part_count(actual_part_count)


def _transform_pin_for_symbol(pin: object, template: object, schdoc: object) -> object:
    transformed_pin = to_symbol_space(pin, template)
    if hasattr(transformed_pin, "owner_index"):
        transformed_pin.owner_index = 0
    if hasattr(transformed_pin, "index_in_sheet"):
        transformed_pin.index_in_sheet = None
    if hasattr(transformed_pin, "_raw_record"):
        transformed_pin._raw_record = None
    if hasattr(transformed_pin, "_source_is_binary"):
        transformed_pin._source_is_binary = True
    for attr in ("_has_location_x", "_has_location_y"):
        if hasattr(transformed_pin, attr):
            setattr(transformed_pin, attr, False)

    if (
        hasattr(transformed_pin, "name_settings")
        and transformed_pin.name_settings.font_id is not None
    ):
        font_info = schdoc.font_manager.get_font_info(
            transformed_pin.name_settings.font_id
        )
        if font_info:
            transformed_pin.name_settings.font_name = font_info.get("name", "Arial")
            transformed_pin.name_settings.font_size = font_info.get("size", 10)

    if (
        hasattr(transformed_pin, "designator_settings")
        and transformed_pin.designator_settings.font_id is not None
    ):
        font_info = schdoc.font_manager.get_font_info(
            transformed_pin.designator_settings.font_id
        )
        if font_info:
            transformed_pin.designator_settings.font_name = font_info.get(
                "name", "Arial"
            )
            transformed_pin.designator_settings.font_size = font_info.get("size", 10)

    return transformed_pin


def _add_embedded_image_transformed(
    image: object,
    symbol: object,
    schlib: object,
    embedded_images: dict[str, bytes],
    debug: bool = False,
) -> None:
    """
    Add an embedded image at its source-order position with its payload.

    Args:
        image: Transformed AltiumSchImage object
        symbol: Symbol to add images to
        schlib: Parent AltiumSchLib to receive embedded image payloads
        embedded_images: Dict of filename -> image data from SchDoc
        debug: Enable debug output
    """
    filename = image.filename
    if filename and filename in embedded_images:
        image_data = embedded_images[filename]
        if debug:
            log.info(f"    Found embedded image: {filename} ({len(image_data)} bytes)")
        image.image_data = image_data
        symbol.add_object(image)
        schlib.embedded_images[filename] = image_data
    elif debug and filename:
        log.warning(f"    Image {filename} not found in embedded images")


def _sanitize_filename(name: str) -> str:
    """
    Remove illegal filename characters.

    Args:
        name: Original symbol name

    Returns:
        Sanitized name safe for use in OLE storage and filesystem
    """
    for char in r'\/:*?"<>|':
        name = name.replace(char, "_")
    return name
