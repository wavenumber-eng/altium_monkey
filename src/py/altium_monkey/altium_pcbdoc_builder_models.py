"""
Model/body helpers for `PcbDocBuilder`.

Board-side 3D component placement has two storage layers:

- `Models/*` carries model metadata plus embedded payload streams.
- `ComponentBodies6/*` and `ShapeBasedComponentBodies6/*` carry the component-
  owned body placement and model linkage.

The first builder slice here is intentionally limited to embedded models. Linked
`ModelsNoEmbed/Data` handling is preserved structurally when it already exists,
but new builder-owned model authoring currently targets the embedded path.
"""

from __future__ import annotations

import copy
import uuid
import zlib
from dataclasses import dataclass
from typing import Sequence

from .altium_pcb_embedded_model_compose import parse_model_records_from_bytes
from .altium_pcb_enums import PcbBodyProjection
from .altium_pcb_model_checksum import compute_altium_model_checksum
from .altium_record_pcb__component_body import AltiumPcbComponentBody
from .altium_record_pcb__model import AltiumPcbModel
from .altium_record_pcb__shapebased_region import PcbExtendedVertex


@dataclass(frozen=True)
class PcbDocEmbeddedModelSpec:
    model: AltiumPcbModel
    embedded_payload: bytes

    @property
    def signature(self) -> tuple:
        return (
            str(self.model.id).upper(),
            str(self.model.name),
            bool(self.model.is_embedded),
            str(self.model.model_source),
            float(self.model.rotation_x),
            float(self.model.rotation_y),
            float(self.model.rotation_z),
            float(self.model.z_offset),
            int(self.model.checksum) & 0xFFFFFFFF,
            bytes(self.embedded_payload),
        )


def format_model_id(value: uuid.UUID | str | None = None) -> str:
    if value is None:
        return "{" + str(uuid.uuid4()).upper() + "}"
    text = str(value).strip()
    if not text:
        return "{" + str(uuid.uuid4()).upper() + "}"
    if text.startswith("{") and text.endswith("}"):
        return text.upper()
    try:
        return "{" + str(uuid.UUID(text)).upper() + "}"
    except ValueError:
        return text.upper()


def parse_component_body_stream(
    data: bytes,
    *,
    shapebased: bool,
) -> tuple[AltiumPcbComponentBody, ...]:
    bodies: list[AltiumPcbComponentBody] = []
    offset = 0
    while offset < len(data):
        body = AltiumPcbComponentBody()
        body._force_extended_vertices = bool(shapebased)
        consumed = body.parse_from_binary(data, offset)
        bodies.append(body)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in component body stream")
    return tuple(bodies)


def build_component_body_stream(bodies: Sequence[AltiumPcbComponentBody]) -> bytes:
    return b"".join(body.serialize_to_binary() for body in bodies)


def build_model_stream(models: Sequence[AltiumPcbModel]) -> bytes:
    return b"".join(model.serialize_to_binary() for model in models)


def collect_existing_embedded_model_specs(
    raw_streams: dict[str, bytes],
    models: Sequence[AltiumPcbModel],
) -> tuple[PcbDocEmbeddedModelSpec, ...]:
    specs: list[PcbDocEmbeddedModelSpec] = []
    metadata_records = (
        list(parse_model_records_from_bytes(raw_streams.get("Models/Data")))
        if raw_streams.get("Models/Data")
        else [model for model in models if model.is_embedded]
    )
    for index, model in enumerate(metadata_records):
        payload = raw_streams.get(f"Models/{index}")
        if payload is None:
            continue
        try:
            model.embedded_data = zlib.decompress(payload)
        except zlib.error:
            model.embedded_data = bytes(payload)
        specs.append(
            PcbDocEmbeddedModelSpec(
                model=copy.deepcopy(model),
                embedded_payload=bytes(payload),
            )
        )
    return tuple(specs)


def add_embedded_model_spec(
    specs: list[PcbDocEmbeddedModelSpec],
    *,
    name: str,
    model_data: bytes,
    model_id: uuid.UUID | str | None = None,
    rotation_x_degrees: float = 0.0,
    rotation_y_degrees: float = 0.0,
    rotation_z_degrees: float = 0.0,
    z_offset_mil: float = 0.0,
    checksum: int | None = None,
    model_source: str = "Undefined",
    data_is_compressed: bool = False,
) -> AltiumPcbModel:
    """
    Create or reuse a board embedded-model spec.

    When `checksum` is omitted, the checksum is computed with Altium's native
    byte-weighted model checksum algorithm. Pass `checksum` only when preserving
    source metadata exactly during a copy workflow.

    Args:
        specs: Mutable builder-owned embedded model spec list.
        name: Model filename stored in `Models/Data`.
        model_data: Model payload bytes. Pass uncompressed bytes by default, or
            zlib-compressed payload stream bytes when `data_is_compressed=True`.
        model_id: Optional model GUID. A new GUID is generated when omitted;
            pass a deterministic GUID only for repeatable generated output.
        rotation_x_degrees: Default model X-axis rotation in degrees.
        rotation_y_degrees: Default model Y-axis rotation in degrees.
        rotation_z_degrees: Default model Z-axis rotation in degrees.
        z_offset_mil: Default model Z offset in mils.
        checksum: Optional native checksum override for metadata-preserving copy
            workflows.
        model_source: Altium model source string.
        data_is_compressed: True when `model_data` is already a compressed
            `Models/<n>` payload.

    Returns:
        The authored or reused embedded model metadata object.
    """
    model = AltiumPcbModel()
    model.name = str(name)
    model.id = format_model_id(model_id)
    model.is_embedded = True
    model.model_source = str(model_source or "Undefined")
    model.rotation_x = float(rotation_x_degrees)
    model.rotation_y = float(rotation_y_degrees)
    model.rotation_z = float(rotation_z_degrees)
    model.z_offset = float(z_offset_mil) * 10000.0

    if data_is_compressed:
        embedded_payload = bytes(model_data)
        try:
            checksum_source = zlib.decompress(embedded_payload)
        except zlib.error:
            checksum_source = embedded_payload
    else:
        checksum_source = bytes(model_data)
        embedded_payload = zlib.compress(checksum_source)

    model.checksum = (
        compute_altium_model_checksum(checksum_source)
        if checksum is None
        else int(checksum)
    )
    model.embedded_data = checksum_source
    spec = PcbDocEmbeddedModelSpec(model=model, embedded_payload=embedded_payload)
    for existing in specs:
        if existing.signature == spec.signature:
            return existing.model
    specs.append(spec)
    return model


def _clone_extended_vertex(vertex: PcbExtendedVertex) -> PcbExtendedVertex:
    clone = PcbExtendedVertex()
    clone.is_round = bool(vertex.is_round)
    clone.x = int(vertex.x)
    clone.y = int(vertex.y)
    clone.center_x = int(vertex.center_x)
    clone.center_y = int(vertex.center_y)
    clone.radius = int(vertex.radius)
    clone.start_angle = float(vertex.start_angle)
    clone.end_angle = float(vertex.end_angle)
    return clone


def clone_body_for_shapebased_stream(
    body: AltiumPcbComponentBody,
) -> AltiumPcbComponentBody:
    shape_body = copy.deepcopy(body)
    outline = list(shape_body.outline)
    if outline:
        first = outline[0]
        last = outline[-1]
        if int(first.x) != int(last.x) or int(first.y) != int(last.y):
            outline.append(_clone_extended_vertex(first))
    shape_body.outline = outline
    shape_body._force_extended_vertices = True
    shape_body._geometry_variant = (True, True)
    return shape_body


def make_authored_component_body_guid(
    body: AltiumPcbComponentBody, ordinal: int
) -> uuid.UUID:
    seed = (
        "pcbdoc-builder-component-body|"
        f"{ordinal}|{body.layer}|{body.component_index}|"
        f"{body.model_id}|{body.model_name}|{body.model_checksum}|"
        f"{body.model_2d_x}|{body.model_2d_y}|{body.model_2d_rotation}|"
        f"{body.model_3d_rotx}|{body.model_3d_roty}|{body.model_3d_rotz}|{body.model_3d_dz}|"
        f"{tuple((int(v.x), int(v.y), int(v.center_x), int(v.center_y), int(v.radius), float(v.start_angle), float(v.end_angle), bool(v.is_round)) for v in body.outline)}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)


def swap_body_projection_for_bottom(
    body_projection: PcbBodyProjection,
) -> PcbBodyProjection:
    if body_projection == PcbBodyProjection.TOP:
        return PcbBodyProjection.BOTTOM
    if body_projection == PcbBodyProjection.BOTTOM:
        return PcbBodyProjection.TOP
    return body_projection
