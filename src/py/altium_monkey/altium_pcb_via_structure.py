"""Parse PcbDoc IPC-4761 via-structure side-table streams."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from .altium_pcb_enums import (
    PcbIpc4761ViaType,
    PcbViaStructureFeatureSide,
    PcbViaStructureFeatureType,
)
from .altium_pcb_property_helpers import (
    iter_pcb_length_prefixed_property_records,
    parse_pcb_int_token,
    parse_pcb_property_payload,
    serialize_pcb_length_prefixed_property_records,
)

if TYPE_CHECKING:
    from .altium_record_pcb__via import AltiumPcbVia


PcbIpc4761ViaTypeValue = PcbIpc4761ViaType | int
PcbViaStructureFeatureTypeValue = PcbViaStructureFeatureType | int
PcbViaStructureFeatureSideValue = PcbViaStructureFeatureSide | int

VIA_STRUCTURE_STREAM_NAMES = frozenset(
    {
        "ViaStructureManager/Header",
        "ViaStructureManager/Data",
        "ViaStructures/Header",
        "ViaStructures/Data",
    }
)

_ALL_FEATURE_TYPES = (
    PcbViaStructureFeatureType.TENTING,
    PcbViaStructureFeatureType.COVERING,
    PcbViaStructureFeatureType.PLUGGING,
    PcbViaStructureFeatureType.FILLING,
    PcbViaStructureFeatureType.CAPPING,
)

_DEFAULT_IPC4761_FEATURE_SIDES = {
    PcbIpc4761ViaType.TYPE_1A_TENTING: {
        PcbViaStructureFeatureType.TENTING: PcbViaStructureFeatureSide.TOP,
    },
    PcbIpc4761ViaType.TYPE_1B_TENTING: {},
    PcbIpc4761ViaType.TYPE_2A_TENTING_AND_COVERING: {
        PcbViaStructureFeatureType.TENTING: PcbViaStructureFeatureSide.TOP,
        PcbViaStructureFeatureType.COVERING: PcbViaStructureFeatureSide.TOP,
    },
    PcbIpc4761ViaType.TYPE_2B_TENTING_AND_COVERING: {},
    PcbIpc4761ViaType.TYPE_3A_PLUGGING: {
        PcbViaStructureFeatureType.PLUGGING: PcbViaStructureFeatureSide.TOP,
    },
    PcbIpc4761ViaType.TYPE_3B_PLUGGING: {},
    PcbIpc4761ViaType.TYPE_4A_PLUGGING_AND_COVERING: {
        PcbViaStructureFeatureType.COVERING: PcbViaStructureFeatureSide.TOP,
        PcbViaStructureFeatureType.PLUGGING: PcbViaStructureFeatureSide.TOP,
    },
    PcbIpc4761ViaType.TYPE_4B_PLUGGING_AND_COVERING: {},
    PcbIpc4761ViaType.TYPE_5_FILLING: {},
    PcbIpc4761ViaType.TYPE_6A_FILLING_AND_COVERING: {
        PcbViaStructureFeatureType.COVERING: PcbViaStructureFeatureSide.TOP,
    },
    PcbIpc4761ViaType.TYPE_6B_FILLING_AND_COVERING: {},
    PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING: {},
}


def _via_type_to_int(value: PcbIpc4761ViaTypeValue) -> int:
    return int(value)


def _feature_type_to_int(value: PcbViaStructureFeatureTypeValue) -> int:
    return int(value)


def _feature_side_to_int(value: PcbViaStructureFeatureSideValue) -> int:
    return int(value)


def _coerce_ipc4761_via_type(value: int) -> PcbIpc4761ViaTypeValue:
    try:
        return PcbIpc4761ViaType(value)
    except ValueError:
        return int(value)


def _coerce_feature_type(value: int) -> PcbViaStructureFeatureTypeValue:
    try:
        return PcbViaStructureFeatureType(value)
    except ValueError:
        return int(value)


def _coerce_feature_side(value: int) -> PcbViaStructureFeatureSideValue:
    try:
        return PcbViaStructureFeatureSide(value)
    except ValueError:
        return int(value)


def _required_int(props: dict[str, str], key: str) -> int:
    value = parse_pcb_int_token(props.get(key, ""))
    if value is None:
        raise ValueError(f"Missing integer property {key}")
    return int(value)


def _property_payload(items: Sequence[tuple[str, object]]) -> bytes:
    text = "".join(f"|{key}={value}" for key, value in items)
    return text.encode("ascii") + b"\x00"


@dataclass(frozen=True)
class AltiumPcbViaStructureFeature:
    """One IPC-4761 feature row from a via-structure record."""

    feature_type: PcbViaStructureFeatureTypeValue
    side: PcbViaStructureFeatureSideValue
    material: str = ""


@dataclass(frozen=True)
class AltiumPcbViaStructure:
    """One IPC-4761 via-structure record from `ViaStructureManager/Data`."""

    structure_type: PcbIpc4761ViaTypeValue
    features: tuple[AltiumPcbViaStructureFeature, ...]
    properties: dict[str, str]
    raw_payload: bytes

    @property
    def ipc4761_via_type(self) -> PcbIpc4761ViaTypeValue:
        return self.structure_type

    @property
    def metadata(self) -> str:
        return self.raw_payload.rstrip(b"\x00").decode("ascii", errors="replace")

    def get_feature(
        self,
        feature_type: PcbViaStructureFeatureTypeValue,
    ) -> AltiumPcbViaStructureFeature | None:
        """Return the row for an IPC-4761 feature type, if present."""
        feature_type_int = _feature_type_to_int(feature_type)
        for feature in self.features:
            if _feature_type_to_int(feature.feature_type) == feature_type_int:
                return feature
        return None

    def with_feature(
        self,
        feature_type: PcbViaStructureFeatureTypeValue,
        *,
        side: PcbViaStructureFeatureSideValue | None = None,
        material: str | None = None,
    ) -> "AltiumPcbViaStructure":
        """Return a copy with one feature-table row updated."""
        feature_type_int = _feature_type_to_int(feature_type)
        updated_features: list[AltiumPcbViaStructureFeature] = []
        found = False
        for feature in self.features:
            if _feature_type_to_int(feature.feature_type) != feature_type_int:
                updated_features.append(feature)
                continue
            found = True
            side_value = (
                feature.side if side is None else _coerce_feature_side(int(side))
            )
            material_value = feature.material if material is None else str(material)
            updated_features.append(
                AltiumPcbViaStructureFeature(
                    feature_type=feature.feature_type,
                    side=side_value,
                    material=material_value,
                )
            )
        if not found:
            raise ValueError(f"Missing IPC-4761 feature row: {feature_type_int}")

        updated = AltiumPcbViaStructure(
            structure_type=self.structure_type,
            features=tuple(updated_features),
            properties={},
            raw_payload=b"",
        )
        payload = updated.to_payload()
        return AltiumPcbViaStructure(
            structure_type=updated.structure_type,
            features=updated.features,
            properties=dict(parse_pcb_property_payload(payload)),
            raw_payload=payload,
        )

    def to_payload(self) -> bytes:
        return serialize_via_structure_payload(self)


@dataclass(frozen=True)
class AltiumPcbViaStructureLink:
    """Link from a VIA primitive index to a via-structure manager record."""

    primitive_index: int
    via_structure_index: int
    properties: dict[str, str]
    raw_payload: bytes

    def to_payload(self) -> bytes:
        return serialize_via_structure_link_payload(self)


def default_via_structure_for_type(
    via_type: PcbIpc4761ViaTypeValue,
) -> AltiumPcbViaStructure | None:
    """
    Build Altium's default feature rows for an IPC-4761 via type.

    `NONE` has no side-table structure. Non-`NONE` records include all five
    known feature rows because that is what Altium writes for a newly selected
    IPC-4761 type, with inactive rows set to `BOTH`.
    """
    via_type_int = _via_type_to_int(via_type)
    if via_type_int == int(PcbIpc4761ViaType.NONE):
        return None
    via_type_value = _coerce_ipc4761_via_type(via_type_int)
    if not isinstance(via_type_value, PcbIpc4761ViaType):
        raise ValueError(f"Unsupported IPC-4761 via type: {via_type_int}")
    overrides = _DEFAULT_IPC4761_FEATURE_SIDES[via_type_value]
    features = tuple(
        AltiumPcbViaStructureFeature(
            feature_type=feature_type,
            side=overrides.get(feature_type, PcbViaStructureFeatureSide.BOTH),
            material="",
        )
        for feature_type in _ALL_FEATURE_TYPES
    )
    structure = AltiumPcbViaStructure(
        structure_type=via_type_value,
        features=features,
        properties={},
        raw_payload=b"",
    )
    payload = structure.to_payload()
    props = parse_pcb_property_payload(payload)
    return AltiumPcbViaStructure(
        structure_type=via_type_value,
        features=features,
        properties=dict(props),
        raw_payload=payload,
    )


def authored_via_structure_for_type(
    via_type: PcbIpc4761ViaTypeValue,
    features: Sequence[AltiumPcbViaStructureFeature] | None = None,
) -> AltiumPcbViaStructure | None:
    """
    Build an authored via-structure record with optional explicit feature rows.

    Omitted features use Altium's default rows for the requested IPC-4761 type.
    Supplying rows with `PcbIpc4761ViaType.NONE` is rejected because Altium does
    not emit a side-table structure for unprotected vias.
    """
    structure = default_via_structure_for_type(via_type)
    if not features:
        return structure
    if structure is None:
        raise ValueError("IPC-4761 feature rows require a non-NONE via type")

    updated = AltiumPcbViaStructure(
        structure_type=structure.structure_type,
        features=tuple(features),
        properties={},
        raw_payload=b"",
    )
    payload = updated.to_payload()
    return AltiumPcbViaStructure(
        structure_type=updated.structure_type,
        features=updated.features,
        properties=dict(parse_pcb_property_payload(payload)),
        raw_payload=payload,
    )


def serialize_via_structure_payload(structure: AltiumPcbViaStructure) -> bytes:
    """Serialize one `ViaStructureManager/Data` property payload."""
    items: list[tuple[str, object]] = [
        ("STRUCTURETYPE", _via_type_to_int(structure.structure_type)),
        ("FEATURESCOUNT", len(structure.features)),
    ]
    for index, feature in enumerate(structure.features):
        items.extend(
            (
                (f"TYPE{index}", _feature_type_to_int(feature.feature_type)),
                (f"SIDE{index}", _feature_side_to_int(feature.side)),
                (f"MATERIAL{index}", feature.material),
            )
        )
    return _property_payload(items)


def serialize_via_structure_link_payload(link: AltiumPcbViaStructureLink) -> bytes:
    """Serialize one `ViaStructures/Data` property payload."""
    return _property_payload(
        (
            ("PRIMITIVEINDEX", int(link.primitive_index)),
            ("VIASTRUCTUREINDEX", int(link.via_structure_index)),
        )
    )


def serialize_via_structure_manager_stream(
    structures: Sequence[AltiumPcbViaStructure],
) -> bytes:
    """Serialize the `ViaStructureManager/Data` stream."""
    return serialize_pcb_length_prefixed_property_records(
        [structure.to_payload() for structure in structures]
    )


def serialize_via_structure_links_stream(
    links: Sequence[AltiumPcbViaStructureLink],
) -> bytes:
    """Serialize the `ViaStructures/Data` stream."""
    return serialize_pcb_length_prefixed_property_records(
        [link.to_payload() for link in links]
    )


def _matching_existing_structure_index(
    structures: Sequence[AltiumPcbViaStructure],
    via_type: int,
) -> int | None:
    for index, structure in enumerate(structures):
        if _via_type_to_int(structure.structure_type) == via_type:
            return index
    return None


def _append_unique_structure(
    structures: list[AltiumPcbViaStructure],
    structure: AltiumPcbViaStructure,
) -> int:
    payload = structure.to_payload()
    for index, existing in enumerate(structures):
        if existing.to_payload() == payload:
            return index
    structures.append(structure)
    return len(structures) - 1


def build_via_structure_model_for_vias(
    vias: Sequence["AltiumPcbVia"],
    *,
    existing_structures: Sequence[AltiumPcbViaStructure] = (),
    primitive_indexes: Sequence[int] | None = None,
) -> tuple[list[AltiumPcbViaStructure], list[AltiumPcbViaStructureLink]]:
    """
    Build side-table records from the current VIA objects.

    Existing structures are kept first and reused by `via_structure_index` when
    still compatible, which preserves Altium's structure ordering on no-op
    read/write saves. New authored IPC-4761 types append default structures.

    `primitive_indexes` is optional because PcbDoc `ViaStructures` indexes the
    board `Vias6/Data` stream, while PcbLib indexes the full mixed footprint
    `Data` stream. When omitted, indexes are the VIA list order.
    """
    if primitive_indexes is not None and len(primitive_indexes) != len(vias):
        raise ValueError("primitive_indexes length must match vias length")

    structures = list(existing_structures)
    existing_structure_count = len(structures)
    links: list[AltiumPcbViaStructureLink] = []
    existing_index_ref_counts: dict[int, int] = {}
    for via in vias:
        existing_index = getattr(via, "via_structure_index", None)
        if (
            isinstance(existing_index, int)
            and 0 <= existing_index < existing_structure_count
        ):
            existing_index_ref_counts[existing_index] = (
                existing_index_ref_counts.get(existing_index, 0) + 1
            )

    for via_ordinal, via in enumerate(vias):
        primitive_index = (
            via_ordinal
            if primitive_indexes is None
            else int(primitive_indexes[via_ordinal])
        )
        via_type = _via_type_to_int(
            getattr(via, "ipc4761_via_type", PcbIpc4761ViaType.NONE)
        )
        if via_type == int(PcbIpc4761ViaType.NONE):
            via.via_structure = None
            via.via_structure_index = None
            continue

        structure_index: int | None = None
        existing_index = getattr(via, "via_structure_index", None)
        via_structure = getattr(via, "via_structure", None)
        if (
            via_structure is not None
            and _via_type_to_int(via_structure.structure_type) == via_type
        ):
            if (
                isinstance(existing_index, int)
                and 0 <= existing_index < existing_structure_count
            ):
                existing_structure = structures[existing_index]
                if (
                    _via_type_to_int(existing_structure.structure_type) == via_type
                    and existing_structure.to_payload() == via_structure.to_payload()
                ):
                    structure_index = existing_index
                elif (
                    _via_type_to_int(existing_structure.structure_type) == via_type
                    and existing_index_ref_counts.get(existing_index, 0) <= 1
                ):
                    structures[existing_index] = via_structure
                    structure_index = existing_index
            if structure_index is None:
                structure_index = _append_unique_structure(structures, via_structure)

        if (
            structure_index is None
            and isinstance(existing_index, int)
            and 0 <= existing_index < existing_structure_count
        ):
            if _via_type_to_int(structures[existing_index].structure_type) == via_type:
                structure_index = existing_index

        if structure_index is None:
            structure_index = _matching_existing_structure_index(structures, via_type)

        if structure_index is None:
            default_structure = default_via_structure_for_type(via_type)
            if default_structure is None:
                continue
            structure_index = _append_unique_structure(structures, default_structure)

        via.via_structure = structures[structure_index]
        via.via_structure_index = structure_index
        link_payload = _property_payload(
            (
                ("PRIMITIVEINDEX", primitive_index),
                ("VIASTRUCTUREINDEX", structure_index),
            )
        )
        links.append(
            AltiumPcbViaStructureLink(
                primitive_index=primitive_index,
                via_structure_index=structure_index,
                properties=parse_pcb_property_payload(link_payload),
                raw_payload=link_payload,
            )
        )

    return structures, links


def build_via_structure_streams_for_vias(
    vias: Sequence["AltiumPcbVia"],
    *,
    existing_structures: Sequence[AltiumPcbViaStructure] = (),
    primitive_indexes: Sequence[int] | None = None,
) -> dict[str, bytes]:
    """Build all IPC-4761 side-table streams for the current VIA model."""
    structures, links = build_via_structure_model_for_vias(
        vias,
        existing_structures=existing_structures,
        primitive_indexes=primitive_indexes,
    )
    if not structures and not links:
        return {}
    return {
        "ViaStructureManager/Header": len(structures).to_bytes(4, byteorder="little"),
        "ViaStructureManager/Data": serialize_via_structure_manager_stream(structures),
        "ViaStructures/Header": len(links).to_bytes(4, byteorder="little"),
        "ViaStructures/Data": serialize_via_structure_links_stream(links),
    }


def via_model_owns_structure_streams(
    vias: Sequence["AltiumPcbVia"],
    *,
    via_structures: Sequence[AltiumPcbViaStructure],
    via_structure_links: Sequence[AltiumPcbViaStructureLink],
    parse_failed: bool,
) -> bool:
    """Return true when the OOP via model can author IPC-4761 side tables."""
    if parse_failed:
        return False
    if via_structures or via_structure_links:
        return True
    return any(
        int(getattr(via, "ipc4761_via_type", PcbIpc4761ViaType.NONE))
        != int(PcbIpc4761ViaType.NONE)
        for via in vias
    )


def parse_via_structure_manager_stream(
    data: bytes,
) -> tuple[AltiumPcbViaStructure, ...]:
    """Parse `ViaStructureManager/Data` records."""
    structures: list[AltiumPcbViaStructure] = []
    for payload, props in iter_pcb_length_prefixed_property_records(data):
        structure_type = _coerce_ipc4761_via_type(_required_int(props, "STRUCTURETYPE"))
        feature_count = _required_int(props, "FEATURESCOUNT")
        features: list[AltiumPcbViaStructureFeature] = []
        for index in range(feature_count):
            features.append(
                AltiumPcbViaStructureFeature(
                    feature_type=_coerce_feature_type(
                        _required_int(props, f"TYPE{index}")
                    ),
                    side=_coerce_feature_side(_required_int(props, f"SIDE{index}")),
                    material=props.get(f"MATERIAL{index}", ""),
                )
            )
        structures.append(
            AltiumPcbViaStructure(
                structure_type=structure_type,
                features=tuple(features),
                properties=dict(props),
                raw_payload=payload,
            )
        )
    return tuple(structures)


def parse_via_structure_links_stream(
    data: bytes,
) -> tuple[AltiumPcbViaStructureLink, ...]:
    """Parse `ViaStructures/Data` primitive-to-structure links."""
    links: list[AltiumPcbViaStructureLink] = []
    for payload, props in iter_pcb_length_prefixed_property_records(data):
        links.append(
            AltiumPcbViaStructureLink(
                primitive_index=_required_int(props, "PRIMITIVEINDEX"),
                via_structure_index=_required_int(props, "VIASTRUCTUREINDEX"),
                properties=dict(props),
                raw_payload=payload,
            )
        )
    return tuple(links)


def via_structure_header_count(data: bytes) -> int:
    """Parse a 4-byte via-structure stream header count."""
    if len(data) != 4:
        raise ValueError(f"Expected 4-byte via-structure header, got {len(data)} bytes")
    return int.from_bytes(data, byteorder="little")


def attach_via_structures_to_vias(
    vias: list["AltiumPcbVia"],
    structures: tuple[AltiumPcbViaStructure, ...],
    links: tuple[AltiumPcbViaStructureLink, ...],
    *,
    primitive_records: Sequence[object] | None = None,
) -> None:
    """Attach parsed IPC-4761 structures to VIA objects by primitive index.

    PcbDoc side-table links index the `Vias6/Data` table, so callers can omit
    `primitive_records`. PcbLib links index the full mixed footprint `Data`
    stream, so PcbLib callers pass the footprint record order.
    """
    for via in vias:
        via.ipc4761_via_type = PcbIpc4761ViaType.NONE
        via.via_structure = None
        via.via_structure_index = None

    via_by_identity = {id(via): via for via in vias}
    for link in links:
        if primitive_records is None:
            if link.primitive_index < 0 or link.primitive_index >= len(vias):
                continue
            via = vias[link.primitive_index]
        else:
            if link.primitive_index < 0 or link.primitive_index >= len(
                primitive_records
            ):
                continue
            primitive = primitive_records[link.primitive_index]
            via = via_by_identity.get(id(primitive))
            if via is None:
                continue

        if link.via_structure_index < 0 or link.via_structure_index >= len(structures):
            continue
        structure = structures[link.via_structure_index]
        via.via_structure = structure
        via.via_structure_index = link.via_structure_index
        via.ipc4761_via_type = structure.ipc4761_via_type
