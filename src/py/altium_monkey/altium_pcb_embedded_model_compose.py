"""
Helpers for composing PCB footprints together with embedded model payloads.

These helpers keep 3D model metadata and payload streams aligned when copying
footprints between PcbLib and PcbDoc builder flows.
"""

from __future__ import annotations

import copy
import zlib
from typing import TYPE_CHECKING, Iterable

from .altium_record_pcb__model import AltiumPcbModel

if TYPE_CHECKING:
    from .altium_pcblib import AltiumPcbFootprint
    from .altium_pcblib_builder import PcbLibBuilder


ModelEntry = tuple[AltiumPcbModel, bytes]
ResolvedBodyModelEntry = tuple[object, AltiumPcbModel, bytes]


def _try_decompress_model_payload(payload: bytes) -> bytes:
    try:
        return zlib.decompress(payload)
    except zlib.error:
        return bytes(payload)


def parse_model_records_from_bytes(data: bytes | None) -> list[AltiumPcbModel]:
    if not data:
        return []

    records: list[AltiumPcbModel] = []
    offset = 0
    while offset + 4 <= len(data):
        model = AltiumPcbModel()
        bytes_consumed = model.parse_from_binary(data, offset)
        if bytes_consumed <= 0:
            break
        records.append(model)
        offset += bytes_consumed
    return records


def collect_pcblib_embedded_model_entries(
    raw_models_data: bytes | None,
    raw_models: dict[int, bytes],
) -> list[ModelEntry]:
    model_records = parse_model_records_from_bytes(raw_models_data)
    entries: list[ModelEntry] = []
    for index, model in enumerate(model_records):
        payload = raw_models.get(index)
        if payload is None:
            continue
        model.embedded_data = _try_decompress_model_payload(payload)
        entries.append((model, payload))
    return entries


def collect_pcbdoc_embedded_model_entries(
    raw_streams: dict[str, bytes],
    models: Iterable[AltiumPcbModel],
) -> list[ModelEntry]:
    models_data = raw_streams.get("Models/Data")
    model_records = (
        parse_model_records_from_bytes(models_data)
        if models_data
        else [model for model in models if model.is_embedded]
    )

    entries: list[ModelEntry] = []
    for index, model in enumerate(model_records):
        payload = raw_streams.get(f"Models/{index}")
        if payload is None:
            continue
        model.embedded_data = _try_decompress_model_payload(payload)
        entries.append((model, payload))
    return entries


def resolve_footprint_body_model_entries(
    footprint: "AltiumPcbFootprint",
    model_entries: list[ModelEntry],
) -> list[ResolvedBodyModelEntry]:
    entries_by_id: dict[
        str, list[tuple[tuple[str, float, float, float, int], AltiumPcbModel, bytes]]
    ] = {}
    for model, payload in model_entries:
        model_signature = (
            str(model.id).upper(),
            float(model.rotation_x),
            float(model.rotation_y),
            float(model.rotation_z),
            int(round(float(model.z_offset))),
        )
        entries_by_id.setdefault(model_signature[0], []).append(
            (model_signature, model, payload)
        )

    resolved: list[ResolvedBodyModelEntry] = []
    for body in footprint.component_bodies:
        model_id = str(getattr(body, "model_id", "") or "").upper()
        if not model_id:
            continue

        desired_signature = (
            model_id,
            float(getattr(body, "model_3d_rotx", 0.0)),
            float(getattr(body, "model_3d_roty", 0.0)),
            float(getattr(body, "model_3d_rotz", 0.0)),
            int(round(float(getattr(body, "model_3d_dz", 0.0)))),
        )
        candidates = entries_by_id.get(model_id, [])

        resolved_entry: tuple[AltiumPcbModel, bytes] | None = None
        for candidate_signature, model, payload in candidates:
            if candidate_signature == desired_signature:
                resolved_entry = (model, payload)
                break
        if resolved_entry is None and candidates:
            resolved_entry = (candidates[0][1], candidates[0][2])
        if resolved_entry is None:
            continue

        model, payload = resolved_entry
        if not str(getattr(body, "model_name", "") or "").strip("\x00"):
            body.model_name = str(getattr(model, "name", "") or "")
        if not str(getattr(body, "model_source", "") or "").strip("\x00"):
            model_source = str(getattr(model, "model_source", "") or "")
            body.model_source = model_source + (
                "\x00" if model_source and not model_source.endswith("\x00") else ""
            )
        if not getattr(body, "model_checksum", None):
            body.model_checksum = int(getattr(model, "checksum", 0)) & 0xFFFFFFFF
        body.model_is_embedded = bool(getattr(model, "is_embedded", True))

        resolved.append((body, model, payload))

    return resolved


def copy_footprint_with_models_into_builder(
    builder: "PcbLibBuilder",
    footprint: "AltiumPcbFootprint",
    model_entries: list[ModelEntry],
    *,
    seen_model_signatures: set[tuple] | None = None,
    height: str | None = None,
    description: str | None = None,
    item_guid: str | None = None,
    revision_guid: str | None = None,
    copy_footprint: bool = True,
) -> "AltiumPcbFootprint":
    owned_footprint = copy.deepcopy(footprint) if copy_footprint else footprint
    resolved_entries = resolve_footprint_body_model_entries(
        owned_footprint, model_entries
    )

    for body, model, payload in resolved_entries:
        signature = (
            str(getattr(body, "model_id", "") or "").upper(),
            float(getattr(body, "model_3d_rotx", 0.0)),
            float(getattr(body, "model_3d_roty", 0.0)),
            float(getattr(body, "model_3d_rotz", 0.0)),
            int(round(float(getattr(body, "model_3d_dz", 0.0)))),
            str(getattr(model, "name", "") or ""),
            str(getattr(model, "model_source", "") or ""),
            int(getattr(model, "checksum", 0)) & 0xFFFFFFFF,
            bytes(payload),
        )
        if seen_model_signatures is not None and signature in seen_model_signatures:
            continue
        if seen_model_signatures is not None:
            seen_model_signatures.add(signature)
        builder.add_embedded_model(
            name=model.name,
            model_data=payload,
            model_id=model.id,
            rotation_x_degrees=float(signature[1]),
            rotation_y_degrees=float(signature[2]),
            rotation_z_degrees=float(signature[3]),
            z_offset_mil=float(signature[4]) / 10000.0,
            checksum=int(model.checksum) & 0xFFFFFFFF,
            model_source=model.model_source,
            data_is_compressed=True,
        )

    return builder.add_existing_footprint(
        owned_footprint,
        height=height
        if height is not None
        else owned_footprint.parameters.get("HEIGHT", "0mil"),
        description=(
            description
            if description is not None
            else owned_footprint.parameters.get("DESCRIPTION", "")
        ),
        item_guid=item_guid
        if item_guid is not None
        else owned_footprint.parameters.get("ITEMGUID", ""),
        revision_guid=(
            revision_guid
            if revision_guid is not None
            else owned_footprint.parameters.get("REVISIONGUID", "")
        ),
        copy_footprint=False,
    )
