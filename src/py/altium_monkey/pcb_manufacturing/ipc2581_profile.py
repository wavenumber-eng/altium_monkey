"""Lower normalized walking-slice geometry to a minimal IPC-2581B document."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re

import lxml.etree as etree
from msgspec import UNSET

from .affine import identity_affine
from .generated import (
    CircleGeometry,
    CircularArcSegment,
    DrillSpan,
    HoleFeature,
    LayerInstance,
    LineSegment,
    MaterialFeature,
    ManufacturingDocument,
    OrientedRectangleGeometry,
    PathSegment,
    Point2d,
    ProfileFeature,
    SourceNet,
    StrokeGeometry,
)
from .validation import validate_manufacturing_document

IPC2581_REVISION_B_NAMESPACE = "http://webstds.ipc.org/2581"
_PROFILE_LAYER_NAME = "BOARD_PROFILE"
_QUALIFIED_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_\-#]*")
_QUALIFIED_NET_NAME = re.compile(
    r"[A-Za-z][A-Za-z0-9_\-#]*(?::[A-Za-z][A-Za-z0-9_\-#]*)*"
)
_HISTORY_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)*")


@dataclass(frozen=True)
class Ipc2581ProfileLoweringError(ValueError):
    """One stable failure while lowering a profile-only IPC document."""

    code: str
    affected_ref: str


@dataclass(frozen=True)
class _IpcLayer:
    source: LayerInstance
    name: str


@dataclass(frozen=True)
class _FlashGroup:
    lands: tuple[MaterialFeature, ...]
    hole: HoleFeature | None


def ipc2581_profile_xml(
    document: ManufacturingDocument,
    *,
    step_name: str,
    generated_at: datetime,
    history_number: str = "1",
) -> bytes:
    """Emit deterministic, XSD-valid IPC-2581B XML from normalized profiles."""

    validate_manufacturing_document(document)
    _validate_metadata(step_name, generated_at, history_number)
    profiles = _profile_only_features(document)
    return _render_document(
        document,
        profiles=profiles,
        strokes=(),
        flash_groups=(),
        ipc_layers=(),
        net_names={},
        step_name=step_name,
        generated_at=generated_at,
        history_number=history_number,
    )


def ipc2581_walking_xml(
    document: ManufacturingDocument,
    *,
    step_name: str,
    generated_at: datetime,
    history_number: str = "1",
) -> bytes:
    """Emit the profile plus simple route walking slice as IPC-2581B XML."""

    validate_manufacturing_document(document)
    _validate_metadata(step_name, generated_at, history_number)
    profiles, strokes, flash_groups = _walking_features(document)
    _validate_board_occurrence(document, profiles)
    materials = tuple(
        feature for feature in document.features if isinstance(feature, MaterialFeature)
    )
    ipc_layers = _walking_layers(document, materials)
    net_names = _walking_net_names(document, materials)
    _validate_flash_spans(document, flash_groups, ipc_layers)
    return _render_document(
        document,
        profiles=profiles,
        strokes=strokes,
        flash_groups=flash_groups,
        ipc_layers=ipc_layers,
        net_names=net_names,
        step_name=step_name,
        generated_at=generated_at,
        history_number=history_number,
    )


def _render_document(
    document: ManufacturingDocument,
    *,
    profiles: tuple[ProfileFeature, ...],
    strokes: tuple[MaterialFeature, ...],
    flash_groups: tuple[_FlashGroup, ...],
    ipc_layers: tuple[_IpcLayer, ...],
    net_names: dict[str, str],
    step_name: str,
    generated_at: datetime,
    history_number: str,
) -> bytes:
    root = _document_skeleton(
        document,
        ipc_layers=ipc_layers,
        step_name=step_name,
        generated_at=generated_at,
        history_number=history_number,
    )
    _append_dictionary_entries(root, flash_groups)
    step = root.find(f".//{{{IPC2581_REVISION_B_NAMESPACE}}}Step")
    if step is None:
        raise AssertionError("IPC profile skeleton omitted its Step")
    _append_pad_stacks(
        step,
        flash_groups,
        ipc_layers,
        document.drill_spans,
        net_names,
    )
    _append_profile(step, profiles)
    _append_layer_features(step, strokes, ipc_layers, net_names)
    return etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )


def _validate_metadata(
    step_name: str,
    generated_at: datetime,
    history_number: str,
) -> None:
    if _QUALIFIED_NAME.fullmatch(step_name) is None:
        raise Ipc2581ProfileLoweringError("invalid_step_name", step_name)
    if _HISTORY_NUMBER.fullmatch(history_number) is None:
        raise Ipc2581ProfileLoweringError("invalid_history_number", history_number)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise Ipc2581ProfileLoweringError("naive_generation_time", step_name)


def _profile_only_features(
    document: ManufacturingDocument,
) -> tuple[ProfileFeature, ...]:
    _assert_profile_only_facts(document)
    profiles = _profile_rows(document)
    outer = _single_outer(profiles, document.generator_revision)
    ordered = (outer, *(row for row in profiles if row.operation == "cutout"))
    _validate_board_occurrence(document, ordered)
    return ordered


def _walking_features(
    document: ManufacturingDocument,
) -> tuple[
    tuple[ProfileFeature, ...],
    tuple[MaterialFeature, ...],
    tuple[_FlashGroup, ...],
]:
    _assert_walking_facts(document)
    profiles, materials, holes = _partition_walking_features(document)
    outer = _single_outer(profiles, document.generator_revision)
    ordered_profiles = (
        outer,
        *(row for row in profiles if row.operation == "cutout"),
    )
    strokes: list[MaterialFeature] = []
    flashes: list[MaterialFeature] = []
    for feature in materials:
        if isinstance(feature.geometry, StrokeGeometry):
            _validate_walking_stroke(feature)
            strokes.append(feature)
        else:
            _validate_walking_flash(feature)
            flashes.append(feature)
    flash_groups = _flash_groups(tuple(flashes), holes)
    return ordered_profiles, tuple(strokes), flash_groups


def _assert_walking_facts(document: ManufacturingDocument) -> None:
    if document.diagnostics:
        raise Ipc2581ProfileLoweringError(
            "unsupported_diagnostics", document.generator_revision
        )


def _walking_net_names(
    document: ManufacturingDocument,
    materials: tuple[MaterialFeature, ...],
) -> dict[str, str]:
    nets_by_id = {net.id: net for net in document.nets}
    if len(nets_by_id) != len(document.nets):
        raise Ipc2581ProfileLoweringError(
            "duplicate_net_identity", document.generator_revision
        )
    used_refs = tuple(
        dict.fromkeys(
            feature.source_net_ref
            for feature in materials
            if feature.source_net_ref is not UNSET
        )
    )
    result: dict[str, str] = {}
    used_names: set[str] = set()
    for net_ref in used_refs:
        net = nets_by_id.get(net_ref)
        if net is None:
            raise Ipc2581ProfileLoweringError("missing_source_net", net_ref)
        name = _source_net_name(net)
        if name in used_names:
            raise Ipc2581ProfileLoweringError("ambiguous_net_name", net_ref)
        result[net_ref] = name
        used_names.add(name)
    return result


def _source_net_name(net: SourceNet) -> str:
    name = net.display_name
    if not isinstance(name, str) or not name:
        raise Ipc2581ProfileLoweringError("missing_net_name", net.id)
    if _QUALIFIED_NET_NAME.fullmatch(name) is None:
        raise Ipc2581ProfileLoweringError("invalid_net_name", net.id)
    return name


def _partition_walking_features(
    document: ManufacturingDocument,
) -> tuple[
    tuple[ProfileFeature, ...],
    tuple[MaterialFeature, ...],
    tuple[HoleFeature, ...],
]:
    profiles = tuple(
        feature for feature in document.features if isinstance(feature, ProfileFeature)
    )
    strokes = tuple(
        feature for feature in document.features if isinstance(feature, MaterialFeature)
    )
    holes = tuple(
        feature for feature in document.features if isinstance(feature, HoleFeature)
    )
    if len(profiles) + len(strokes) + len(holes) != len(document.features):
        raise Ipc2581ProfileLoweringError(
            "unsupported_walking_feature", document.generator_revision
        )
    return profiles, strokes, holes


def _validate_walking_stroke(feature: MaterialFeature) -> None:
    if feature.feature_kind != "route":
        raise Ipc2581ProfileLoweringError(
            "unsupported_material_feature_kind", feature.id
        )
    if feature.material_role != "conductor" or feature.polarity != "add":
        raise Ipc2581ProfileLoweringError("unsupported_route_material", feature.id)
    if not isinstance(feature.geometry, StrokeGeometry):
        raise Ipc2581ProfileLoweringError("unsupported_route_geometry", feature.id)
    if feature.geometry.path.closed or len(feature.geometry.path.segments) != 1:
        raise Ipc2581ProfileLoweringError("unsupported_compound_route", feature.id)


def _validate_walking_flash(feature: MaterialFeature) -> None:
    if feature.feature_kind != "land":
        raise Ipc2581ProfileLoweringError(
            "unsupported_material_feature_kind", feature.id
        )
    if feature.material_role != "conductor" or feature.polarity != "add":
        raise Ipc2581ProfileLoweringError("unsupported_land_material", feature.id)
    _flash_geometry(feature)


def _flash_geometry(
    feature: MaterialFeature,
) -> CircleGeometry | OrientedRectangleGeometry:
    geometry = feature.geometry
    if isinstance(geometry, CircleGeometry):
        return geometry
    if isinstance(geometry, OrientedRectangleGeometry):
        if geometry.affine != identity_affine():
            raise Ipc2581ProfileLoweringError("unsupported_land_affine", feature.id)
        return geometry
    raise Ipc2581ProfileLoweringError("unsupported_land_geometry", feature.id)


def _flash_groups(
    lands: tuple[MaterialFeature, ...],
    holes: tuple[HoleFeature, ...],
) -> tuple[_FlashGroup, ...]:
    result: list[_FlashGroup] = []
    allocated_holes: set[str] = set()
    allocated_lands: set[str] = set()
    for land in lands:
        if land.id in allocated_lands:
            continue
        group = _one_flash_group(land, lands, holes)
        if group.hole is not None:
            allocated_holes.add(group.hole.id)
        allocated_lands.update(row.id for row in group.lands)
        result.append(group)
    if allocated_holes != {row.id for row in holes}:
        raise Ipc2581ProfileLoweringError("unowned_walking_hole", holes[0].id)
    return tuple(result)


def _one_flash_group(
    land: MaterialFeature,
    lands: tuple[MaterialFeature, ...],
    holes: tuple[HoleFeature, ...],
) -> _FlashGroup:
    matching_lands = tuple(row for row in lands if row.source == land.source)
    if len({row.source_net_ref for row in matching_lands}) != 1:
        raise Ipc2581ProfileLoweringError("conflicting_land_net", land.id)
    matching_holes = tuple(row for row in holes if row.source == land.source)
    if len(matching_holes) > 1:
        raise Ipc2581ProfileLoweringError("multiple_source_holes", land.id)
    hole = matching_holes[0] if matching_holes else None
    if hole is not None:
        _validate_walking_hole(hole, matching_lands)
    return _FlashGroup(lands=matching_lands, hole=hole)


def _validate_walking_hole(
    hole: HoleFeature,
    lands: tuple[MaterialFeature, ...],
) -> None:
    if not isinstance(hole.geometry, CircleGeometry):
        raise Ipc2581ProfileLoweringError("unsupported_hole_geometry", hole.id)
    if hole.parent_feature_ref is UNSET or hole.parent_feature_ref not in {
        row.id for row in lands
    }:
        raise Ipc2581ProfileLoweringError("unsupported_hole_parent", hole.id)
    centers = {_flash_center(_flash_geometry(row)) for row in lands}
    if centers != {hole.geometry.center}:
        raise Ipc2581ProfileLoweringError("misaligned_hole_land", hole.id)


def _walking_layers(
    document: ManufacturingDocument,
    materials: tuple[MaterialFeature, ...],
) -> tuple[_IpcLayer, ...]:
    by_id = {layer.id: layer for layer in document.layers}
    if len(by_id) != len(document.layers):
        raise Ipc2581ProfileLoweringError(
            "duplicate_layer_identity", document.generator_revision
        )
    used_ids = tuple(dict.fromkeys(feature.layer_ref for feature in materials))
    result: list[_IpcLayer] = []
    for index, layer_id in enumerate(used_ids):
        layer = by_id.get(layer_id)
        if layer is None:
            raise Ipc2581ProfileLoweringError("missing_route_layer", layer_id)
        if layer.material_role != "conductor" or layer.film_baseline != "empty":
            raise Ipc2581ProfileLoweringError("unsupported_route_layer", layer_id)
        result.append(
            _IpcLayer(
                source=layer,
                name=f"LAYER_{index}_{_qualified_fragment(layer.pcb_layer_ref)}",
            )
        )
    return tuple(result)


def _validate_flash_spans(
    document: ManufacturingDocument,
    groups: tuple[_FlashGroup, ...],
    ipc_layers: tuple[_IpcLayer, ...],
) -> None:
    spans = {row.id: row for row in document.drill_spans}
    if len(spans) != len(document.drill_spans):
        raise Ipc2581ProfileLoweringError(
            "duplicate_drill_span", document.generator_revision
        )
    layer_ids = {row.source.id for row in ipc_layers}
    used_spans: set[str] = set()
    for group in groups:
        if group.hole is None:
            continue
        span = spans.get(group.hole.drill_span_ref)
        if span is None:
            raise Ipc2581ProfileLoweringError("missing_hole_span", group.hole.id)
        if span.backdrill or {span.start_layer_ref, span.end_layer_ref} - layer_ids:
            raise Ipc2581ProfileLoweringError("unsupported_hole_span", span.id)
        used_spans.add(span.id)
    if used_spans != set(spans):
        affected = next(iter(set(spans) - used_spans))
        raise Ipc2581ProfileLoweringError("unused_drill_span", affected)


def _flash_center(
    geometry: CircleGeometry | OrientedRectangleGeometry,
) -> Point2d:
    return geometry.center


def _assert_profile_only_facts(document: ManufacturingDocument) -> None:
    nonprofile_facts = any(
        (
            document.stack_regions,
            document.layers,
            document.nets,
            document.drill_spans,
        )
    )
    if nonprofile_facts:
        raise Ipc2581ProfileLoweringError(
            "unsupported_nonprofile_facts", document.generator_revision
        )
    if document.diagnostics:
        raise Ipc2581ProfileLoweringError(
            "unsupported_diagnostics", document.generator_revision
        )


def _profile_rows(document: ManufacturingDocument) -> tuple[ProfileFeature, ...]:
    result = tuple(
        feature for feature in document.features if isinstance(feature, ProfileFeature)
    )
    if len(result) != len(document.features):
        raise Ipc2581ProfileLoweringError(
            "unsupported_nonprofile_feature", document.generator_revision
        )
    return result


def _single_outer(
    profiles: tuple[ProfileFeature, ...],
    affected_ref: str,
) -> ProfileFeature:
    outers = tuple(profile for profile in profiles if profile.operation == "outer")
    if len(outers) != 1:
        raise Ipc2581ProfileLoweringError(
            "unsupported_profile_owner_count", affected_ref
        )
    return outers[0]


def _validate_board_occurrence(
    document: ManufacturingDocument,
    profiles: tuple[ProfileFeature, ...],
) -> None:
    owner_ref = profiles[0].board_occurrence_ref
    if any(profile.board_occurrence_ref != owner_ref for profile in profiles):
        raise Ipc2581ProfileLoweringError("multiple_profile_owners", owner_ref)
    owners = tuple(row for row in document.board_occurrences if row.id == owner_ref)
    if len(document.board_occurrences) != 1 or len(owners) != 1:
        raise Ipc2581ProfileLoweringError("unsupported_board_occurrences", owner_ref)
    if owners[0].affine != identity_affine():
        raise Ipc2581ProfileLoweringError("unsupported_board_affine", owner_ref)


def _document_skeleton(
    document: ManufacturingDocument,
    *,
    ipc_layers: tuple[_IpcLayer, ...],
    step_name: str,
    generated_at: datetime,
    history_number: str,
) -> etree._Element:
    namespace = IPC2581_REVISION_B_NAMESPACE
    root = etree.Element(_tag("IPC-2581"), nsmap={None: namespace})
    root.set("revision", "B")
    _append_content(root, step_name, ipc_layers)
    _append_logistic_header(root)
    _append_history(
        root,
        generator_revision=document.generator_revision,
        generated_at=generated_at,
        history_number=history_number,
    )
    ecad = _sub(root, "Ecad", name=f"{step_name}_ECAD")
    _sub(ecad, "CadHeader", units="MILLIMETER")
    cad_data = _sub(ecad, "CadData")
    _sub(
        cad_data,
        "Layer",
        name=_PROFILE_LAYER_NAME,
        layerFunction="BOARD_OUTLINE",
        side="NONE",
        polarity="POSITIVE",
    )
    for ipc_layer in ipc_layers:
        _append_layer(cad_data, ipc_layer)
    step = _sub(cad_data, "Step", name=step_name)
    _sub(step, "Datum", x="0", y="0")
    return root


def _append_content(
    root: etree._Element,
    step_name: str,
    ipc_layers: tuple[_IpcLayer, ...],
) -> None:
    content = _sub(root, "Content", roleRef="OWNER")
    _sub(
        content,
        "FunctionMode",
        mode="USERDEF",
        level="1",
        comment="Normalized board profile",
    )
    _sub(content, "StepRef", name=step_name)
    _sub(content, "LayerRef", name=_PROFILE_LAYER_NAME)
    for ipc_layer in ipc_layers:
        _sub(content, "LayerRef", name=ipc_layer.name)
    _sub(content, "DictionaryStandard", units="MILLIMETER")


def _append_layer(parent: etree._Element, ipc_layer: _IpcLayer) -> None:
    _sub(
        parent,
        "Layer",
        name=ipc_layer.name,
        layerFunction="SIGNAL",
        side=ipc_layer.source.side.upper(),
        polarity="POSITIVE",
    )


def _append_logistic_header(root: etree._Element) -> None:
    header = _sub(root, "LogisticHeader")
    _sub(header, "Role", id="OWNER", roleFunction="OWNER")
    _sub(header, "Enterprise", id="WAVENUMBER", code="WAVENUMBER")
    _sub(
        header,
        "Person",
        name="altium_monkey",
        enterpriseRef="WAVENUMBER",
        roleRef="OWNER",
    )


def _append_history(
    root: etree._Element,
    *,
    generator_revision: str,
    generated_at: datetime,
    history_number: str,
) -> None:
    timestamp = _xsd_datetime(generated_at)
    history = _sub(
        root,
        "HistoryRecord",
        number=history_number,
        origination=timestamp,
        software="altium_monkey",
        lastChange=timestamp,
    )
    revision = _sub(
        history,
        "FileRevision",
        fileRevisionId=history_number,
        comment="Generated from normalized manufacturing IR",
    )
    package = _sub(
        revision,
        "SoftwarePackage",
        name="altium_monkey",
        vendor="Wavenumber",
        revision=generator_revision,
    )
    _sub(package, "Certification", certificationStatus="SELFTEST")


def _append_dictionary_entries(
    root: etree._Element,
    groups: tuple[_FlashGroup, ...],
) -> None:
    dictionary = root.find(f".//{{{IPC2581_REVISION_B_NAMESPACE}}}DictionaryStandard")
    if dictionary is None:
        raise AssertionError("IPC skeleton omitted DictionaryStandard")
    seen: set[str] = set()
    for group in groups:
        for land in group.lands:
            geometry = _flash_geometry(land)
            shape_id = _shape_id(geometry)
            if shape_id in seen:
                continue
            seen.add(shape_id)
            entry = _sub(dictionary, "EntryStandard", id=shape_id)
            _append_standard_shape(entry, geometry)


def _append_standard_shape(
    parent: etree._Element,
    geometry: CircleGeometry | OrientedRectangleGeometry,
) -> None:
    if isinstance(geometry, CircleGeometry):
        _sub(parent, "Circle", diameter=_format_mm(geometry.radius_nm * 2))
        return
    _sub(
        parent,
        "RectCenter",
        width=_format_mm(geometry.width_nm),
        height=_format_mm(geometry.height_nm),
    )


def _append_pad_stacks(
    step: etree._Element,
    groups: tuple[_FlashGroup, ...],
    ipc_layers: tuple[_IpcLayer, ...],
    drill_spans: list[DrillSpan],
    net_names: dict[str, str],
) -> None:
    datum = step.find(f"{{{IPC2581_REVISION_B_NAMESPACE}}}Datum")
    if datum is None:
        raise AssertionError("IPC skeleton omitted Datum")
    layer_names = {row.source.id: row.name for row in ipc_layers}
    spans = {row.id: row for row in drill_spans}
    for index, group in enumerate(groups):
        pad_stack = etree.Element(_tag("PadStack"))
        net_name = _material_net_name(group.lands[0], net_names)
        if net_name is not None:
            pad_stack.set("net", net_name)
        datum.addprevious(pad_stack)
        if group.hole is not None:
            _append_layer_hole(
                pad_stack,
                group.hole,
                spans[group.hole.drill_span_ref],
                layer_names,
                index,
            )
        for land in group.lands:
            _append_layer_pad(pad_stack, land, layer_names[land.layer_ref])


def _append_layer_hole(
    parent: etree._Element,
    hole: HoleFeature,
    span: DrillSpan,
    layer_names: dict[str, str],
    index: int,
) -> None:
    geometry = hole.geometry
    if not isinstance(geometry, CircleGeometry):
        raise AssertionError("walking hole validation was bypassed")
    row = _sub(
        parent,
        "LayerHole",
        name=f"HOLE_{index}",
        diameter=_format_mm(geometry.radius_nm * 2),
        platingStatus="PLATED" if hole.plated else "NONPLATED",
        plusTol="0",
        minusTol="0",
        x=_format_mm(geometry.center.x_nm),
        y=_format_mm(geometry.center.y_nm),
    )
    _sub(
        row,
        "Span",
        fromLayer=layer_names[span.start_layer_ref],
        toLayer=layer_names[span.end_layer_ref],
    )


def _append_layer_pad(
    parent: etree._Element,
    land: MaterialFeature,
    layer_name: str,
) -> None:
    geometry = _flash_geometry(land)
    row = _sub(parent, "LayerPad", layerRef=layer_name)
    center = _flash_center(geometry)
    _sub(row, "Location", x=_format_mm(center.x_nm), y=_format_mm(center.y_nm))
    _sub(row, "StandardPrimitiveRef", id=_shape_id(geometry))


def _shape_id(geometry: object) -> str:
    if isinstance(geometry, CircleGeometry):
        return f"CIRCLE_D{geometry.radius_nm * 2}"
    if isinstance(geometry, OrientedRectangleGeometry):
        return f"RECT_W{geometry.width_nm}_H{geometry.height_nm}"
    raise Ipc2581ProfileLoweringError("unsupported_standard_shape", str(geometry))


def _append_profile(
    step: etree._Element,
    profiles: tuple[ProfileFeature, ...],
) -> None:
    profile = _sub(step, "Profile")
    _append_polygon(profile, "Polygon", profiles[0])
    for cutout in profiles[1:]:
        _append_polygon(profile, "Cutout", cutout)


def _append_layer_features(
    step: etree._Element,
    strokes: tuple[MaterialFeature, ...],
    ipc_layers: tuple[_IpcLayer, ...],
    net_names: dict[str, str],
) -> None:
    layer_names = {layer.source.id: layer.name for layer in ipc_layers}
    for layer in ipc_layers:
        layer_strokes = tuple(
            feature for feature in strokes if feature.layer_ref == layer.source.id
        )
        if not layer_strokes:
            continue
        layer_feature = _sub(step, "LayerFeature", layerRef=layer.name)
        _append_layer_strokes(layer_feature, layer_strokes, net_names)
    if {feature.layer_ref for feature in strokes} - set(layer_names):
        raise AssertionError("walking layer allocation lost a route")


def _append_layer_strokes(
    layer_feature: etree._Element,
    strokes: tuple[MaterialFeature, ...],
    net_names: dict[str, str],
) -> None:
    for feature in strokes:
        feature_set = _sub(layer_feature, "Set", polarity="POSITIVE")
        net_name = _material_net_name(feature, net_names)
        if net_name is not None:
            feature_set.set("net", net_name)
        features = _sub(feature_set, "Features")
        geometry = feature.geometry
        if not isinstance(geometry, StrokeGeometry):
            raise AssertionError("walking stroke validation was bypassed")
        _append_stroke(features, geometry)


def _material_net_name(
    feature: MaterialFeature, net_names: dict[str, str]
) -> str | None:
    if feature.source_net_ref is UNSET:
        return None
    try:
        return net_names[feature.source_net_ref]
    except KeyError as exc:
        raise AssertionError("walking net allocation lost a material feature") from exc


def _append_stroke(parent: etree._Element, geometry: StrokeGeometry) -> None:
    segment = geometry.path.segments[0]
    attributes = {
        "startX": _format_mm(segment.start.x_nm),
        "startY": _format_mm(segment.start.y_nm),
        "endX": _format_mm(segment.end.x_nm),
        "endY": _format_mm(segment.end.y_nm),
    }
    if isinstance(segment, LineSegment):
        row = _sub(parent, "Line", **attributes)
    elif isinstance(segment, CircularArcSegment):
        row = _sub(
            parent,
            "Arc",
            **attributes,
            centerX=_format_mm(segment.center.x_nm),
            centerY=_format_mm(segment.center.y_nm),
            clockwise="true" if segment.clockwise else "false",
        )
    else:
        raise Ipc2581ProfileLoweringError("unsupported_route_segment", str(segment))
    _sub(
        row,
        "LineDesc",
        lineEnd="ROUND",
        lineWidth=_format_mm(geometry.width_nm),
        lineProperty="SOLID",
    )


def _append_polygon(
    parent: etree._Element,
    tag: str,
    profile: ProfileFeature,
) -> None:
    polygon = _sub(parent, tag)
    first = profile.geometry.segments[0].start
    _sub(polygon, "PolyBegin", x=_format_mm(first.x_nm), y=_format_mm(first.y_nm))
    for segment in profile.geometry.segments:
        _append_segment(polygon, segment)


def _append_segment(parent: etree._Element, segment: PathSegment) -> None:
    if isinstance(segment, LineSegment):
        _sub(
            parent,
            "PolyStepSegment",
            x=_format_mm(segment.end.x_nm),
            y=_format_mm(segment.end.y_nm),
        )
        return
    if isinstance(segment, CircularArcSegment):
        _sub(
            parent,
            "PolyStepCurve",
            x=_format_mm(segment.end.x_nm),
            y=_format_mm(segment.end.y_nm),
            centerX=_format_mm(segment.center.x_nm),
            centerY=_format_mm(segment.center.y_nm),
            clockwise="true" if segment.clockwise else "false",
        )
        return
    raise Ipc2581ProfileLoweringError("unsupported_profile_segment", str(segment))


def _format_mm(value_nm: int) -> str:
    value = Decimal(value_nm) / Decimal(1_000_000)
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered if rendered not in {"", "-0"} else "0"


def _xsd_datetime(value: datetime) -> str:
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def _qualified_fragment(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9_\-#]", "_", value)
    return rendered if rendered and rendered[0].isalpha() else f"REF_{rendered}"


def _tag(local_name: str) -> str:
    return f"{{{IPC2581_REVISION_B_NAMESPACE}}}{local_name}"


def _sub(
    parent: etree._Element,
    local_name: str,
    **attributes: str,
) -> etree._Element:
    return etree.SubElement(parent, _tag(local_name), attributes)


__all__ = [
    "IPC2581_REVISION_B_NAMESPACE",
    "Ipc2581ProfileLoweringError",
    "ipc2581_profile_xml",
    "ipc2581_walking_xml",
]
