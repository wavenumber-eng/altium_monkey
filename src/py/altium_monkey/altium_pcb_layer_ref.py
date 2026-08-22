"""V7-aware PCB layer identity values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import TypeAlias, TypeGuard

from .altium_pcb_enums import (
    PCB_USER_MECHANICAL_LAYER_AUTHORING_MAX,
    PcbV7SavedLayerId,
)
from .altium_record_types import PcbLayer

_SIGNAL_V7_SAVED_PREFIX = 0x01000000
_SIGNAL_BOTTOM_V7_SAVED_ID = 0x0100FFFF
_INTERNAL_PLANE_V7_SAVED_PREFIX = 0x01010000
_MECHANICAL_V7_SAVED_PREFIX = 0x01020000
_SYSTEM_V7_SAVED_PREFIX = 0x01030000
_MAX_AD26_MID_SIGNAL_LAYER = 126
_MAX_USER_AUTHORING_MECHANICAL_LAYER = PCB_USER_MECHANICAL_LAYER_AUTHORING_MAX
# Reserved system mechanical numbers used by native board region / split-line
# region rows (legacy layer byte 0). Captured boards allocate these upward
# from MECHANICAL64530 (e.g. 64530..64535, 64540, 64541), so the reserved
# window spans from that base to the mechanical token cap.
_RESERVED_SYSTEM_MECHANICAL_MIN = 64530
_RESERVED_SYSTEM_MECHANICAL_MAX = 0xFFFF


class PcbLayerResolutionError(ValueError):
    """Raised when a layer reference cannot be resolved without data loss."""


class PcbLayerFamily(str, Enum):
    """Broad PCB layer family for V7-aware layer references."""

    NO_LAYER = "no_layer"
    SIGNAL = "signal"
    INTERNAL_PLANE = "internal_plane"
    MECHANICAL = "mechanical"
    MISC = "misc"
    DIELECTRIC = "dielectric"
    DOCUMENT = "document"
    FAMILY = "family"
    UNKNOWN = "unknown"


class PcbSignalLayerKind(str, Enum):
    """Signal-layer role independent of encoded native integers."""

    TOP = "top"
    MID = "mid"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class _PcbLayerIdentityKey:
    family: PcbLayerFamily
    token: str
    native_id: int | None = None


@dataclass(frozen=True, eq=False)
class PcbLayerRef:
    """Stable identity for one PCB layer."""

    family: PcbLayerFamily
    token: str
    signal_kind: PcbSignalLayerKind | None = None
    number: int | None = None
    legacy_layer: PcbLayer | None = None
    v7_saved_layer_id: int | None = None
    runtime_v7_layer_id: int | None = None

    def __post_init__(self) -> None:
        token = str(self.token or "").strip().upper()
        if not token:
            raise PcbLayerResolutionError("Layer reference token must not be empty")
        object.__setattr__(self, "token", token)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PcbLayerRef):
            return NotImplemented
        return self._identity_key == other._identity_key

    def __hash__(self) -> int:
        return hash(self._identity_key)

    @property
    def _identity_key(self) -> _PcbLayerIdentityKey:
        if self.family == PcbLayerFamily.UNKNOWN:
            return _PcbLayerIdentityKey(
                self.family,
                self.token,
                self.v7_saved_layer_id or self.runtime_v7_layer_id,
            )
        return _PcbLayerIdentityKey(self.family, self.token)

    @property
    def legacy_layer_id(self) -> int | None:
        """Return the legacy TV6 layer id when this ref is losslessly legacy."""

        if self.legacy_layer is None:
            return None
        return int(self.legacy_layer)

    @classmethod
    def top(cls) -> "PcbLayerRef":
        return cls(
            family=PcbLayerFamily.SIGNAL,
            token="TOP",
            signal_kind=PcbSignalLayerKind.TOP,
            legacy_layer=PcbLayer.TOP,
            v7_saved_layer_id=_SIGNAL_V7_SAVED_PREFIX + 1,
        )

    @classmethod
    def bottom(cls) -> "PcbLayerRef":
        return cls(
            family=PcbLayerFamily.SIGNAL,
            token="BOTTOM",
            signal_kind=PcbSignalLayerKind.BOTTOM,
            legacy_layer=PcbLayer.BOTTOM,
            v7_saved_layer_id=_SIGNAL_BOTTOM_V7_SAVED_ID,
        )

    @classmethod
    def mid_layer(cls, number: int) -> "PcbLayerRef":
        mid_number = _positive_int(number, field_name="mid signal layer number")
        if mid_number > _MAX_AD26_MID_SIGNAL_LAYER:
            raise PcbLayerResolutionError(
                "AD 26.8 signal refs support Mid1 through Mid126"
            )
        legacy_layer = (
            PcbLayer(PcbLayer.TOP.value + mid_number) if mid_number <= 30 else None
        )
        return cls(
            family=PcbLayerFamily.SIGNAL,
            token=f"MID{mid_number}",
            signal_kind=PcbSignalLayerKind.MID,
            number=mid_number,
            legacy_layer=legacy_layer,
            v7_saved_layer_id=_SIGNAL_V7_SAVED_PREFIX + mid_number + 1,
        )

    @classmethod
    def internal_plane(cls, number: int) -> "PcbLayerRef":
        plane_number = _bounded_positive_int(
            number,
            field_name="internal plane number",
            maximum=16,
        )
        return cls(
            family=PcbLayerFamily.INTERNAL_PLANE,
            token=f"PLANE{plane_number}",
            number=plane_number,
            legacy_layer=PcbLayer(PcbLayer.INTERNAL_PLANE_1.value + plane_number - 1),
            v7_saved_layer_id=_INTERNAL_PLANE_V7_SAVED_PREFIX + plane_number,
        )

    @classmethod
    def mechanical(cls, number: int) -> "PcbLayerRef":
        mechanical_number = _bounded_positive_int(
            number,
            field_name="mechanical layer number",
            maximum=0xFFFF,
        )
        legacy_layer = (
            PcbLayer(PcbLayer.MECHANICAL_1.value + mechanical_number - 1)
            if mechanical_number <= 16
            else None
        )
        return cls(
            family=PcbLayerFamily.MECHANICAL,
            token=f"MECHANICAL{mechanical_number}",
            number=mechanical_number,
            legacy_layer=legacy_layer,
            v7_saved_layer_id=_MECHANICAL_V7_SAVED_PREFIX + mechanical_number,
        )

    @classmethod
    def from_legacy(cls, layer: int | PcbLayer) -> "PcbLayerRef":
        legacy_layer = _coerce_legacy_layer(layer)
        if legacy_layer == PcbLayer.TOP:
            return cls.top()
        if legacy_layer == PcbLayer.BOTTOM:
            return cls.bottom()
        if PcbLayer.MID1 <= legacy_layer <= PcbLayer.MID30:
            return cls.mid_layer(int(legacy_layer) - PcbLayer.TOP.value)
        if PcbLayer.INTERNAL_PLANE_1 <= legacy_layer <= PcbLayer.INTERNAL_PLANE_16:
            return cls.internal_plane(
                int(legacy_layer) - PcbLayer.INTERNAL_PLANE_1.value + 1
            )
        if PcbLayer.MECHANICAL_1 <= legacy_layer <= PcbLayer.MECHANICAL_16:
            return cls.mechanical(int(legacy_layer) - PcbLayer.MECHANICAL_1.value + 1)
        return cls(
            family=PcbLayerFamily.MISC,
            token=legacy_layer.to_json_name(),
            legacy_layer=legacy_layer,
            v7_saved_layer_id=_legacy_misc_v7_saved_layer_id(legacy_layer),
        )

    @classmethod
    def from_v7_saved_layer_id(cls, value: int) -> "PcbLayerRef":
        saved_layer_id = _native_int(value, field_name="v7_saved_layer_id")
        if saved_layer_id == _SIGNAL_BOTTOM_V7_SAVED_ID:
            return cls.bottom()

        prefix = saved_layer_id & 0xFFFF0000
        species = saved_layer_id & 0x0000FFFF
        if prefix == _SIGNAL_V7_SAVED_PREFIX and 1 <= species <= 0x7F:
            if species == 1:
                return cls.top()
            return cls.mid_layer(species - 1)
        if prefix == _INTERNAL_PLANE_V7_SAVED_PREFIX and 1 <= species <= 16:
            return cls.internal_plane(species)
        if prefix == _MECHANICAL_V7_SAVED_PREFIX and species > 0:
            return cls.mechanical(species)

        legacy_layer = _legacy_layer_from_system_v7_saved_layer_id(saved_layer_id)
        if legacy_layer is not None:
            return cls.from_legacy(legacy_layer)

        return cls(
            family=PcbLayerFamily.UNKNOWN,
            token=f"UNKNOWN_SAVED_{saved_layer_id:08X}",
            v7_saved_layer_id=saved_layer_id,
        )

    @classmethod
    def parse(
        cls,
        token: str,
        *,
        allow_native_tokens: bool = False,
    ) -> "PcbLayerRef":
        parsed = _parse_native_token(token, allow_native_tokens=allow_native_tokens)
        if parsed is not None:
            return parsed

        normalized = _normalize_layer_token(token)
        if normalized in {"TOP", "TOPLAYER"}:
            return cls.top()
        if normalized in {"BOTTOM", "BOTTOMLAYER", "BOT", "BOTLAYER"}:
            return cls.bottom()

        mid_match = re.fullmatch(r"MID(?:LAYER)?(\d+)", normalized)
        if mid_match:
            return cls.mid_layer(int(mid_match.group(1)))

        plane_match = re.fullmatch(r"(?:INTERNAL)?PLANE(\d+)", normalized)
        if plane_match:
            return cls.internal_plane(int(plane_match.group(1)))

        mechanical_match = re.fullmatch(r"MECHANICAL(\d+)", normalized)
        if mechanical_match:
            return cls.mechanical(int(mechanical_match.group(1)))

        try:
            return cls.from_legacy(PcbLayer.from_json_name(normalized))
        except ValueError as exc:
            raise PcbLayerResolutionError(
                f"Unknown PCB layer token: {token!r}"
            ) from exc

    def require_legacy_layer(self) -> PcbLayer:
        """Return the legacy TV6 enum or raise when this ref is V7-only."""

        if self.legacy_layer is None:
            raise PcbLayerResolutionError(
                f"Layer {self.token} cannot be represented by legacy PcbLayer"
            )
        return self.legacy_layer

    @classmethod
    def _from_runtime_v7_layer_id(cls, value: int) -> "PcbLayerRef":
        runtime_id = _native_int(value, field_name="runtime_v7_layer_id")
        return cls(
            family=PcbLayerFamily.UNKNOWN,
            token=f"UNKNOWN_TV7_{runtime_id:08X}",
            runtime_v7_layer_id=runtime_id,
        )


@dataclass(frozen=True)
class PcbLayerRegistryEntry:
    """Board/library layer context paired with a stable layer ref."""

    ref: PcbLayerRef
    display_name: str
    custom_name: str | None = None
    default_name: str | None = None
    semantic_key: str | None = None
    enabled: bool | None = None
    used_by_primitives: bool | None = None
    color: str | None = None
    stack_index: int | None = None
    signal_stack_position: int | None = None
    signal_layer_count: int | None = None
    side: str | None = None
    source_aliases: tuple[str, ...] = ()
    source_keys: tuple[str, ...] = ()


PcbLayerLike: TypeAlias = PcbLayer | PcbLayerRef | PcbLayerRegistryEntry | str | int


@dataclass(frozen=True)
class PcbPrimitiveLayerState:
    """Resolved primitive layer identity plus serialized storage diagnostics."""

    ref: PcbLayerRef
    stored_legacy_layer_id: int | None = None
    stored_v7_saved_layer_id: int | None = None
    stored_runtime_v7_layer_id: int | None = None
    stored_v7_layer_token: str | None = None
    source_field: str = ""


@dataclass(frozen=True)
class _PcbAuthoringLayerStorage:
    ref: PcbLayerRef
    legacy_layer_id: int
    v7_saved_layer_id: int


@dataclass(frozen=True)
class PcbLayerRegistry:
    """Query facade for board/library layer refs and display names."""

    entries: tuple[PcbLayerRegistryEntry, ...] = ()

    def entry_for_ref(self, ref: PcbLayerRef) -> PcbLayerRegistryEntry | None:
        for entry in self.entries:
            if entry.ref == ref:
                return entry
        return None

    def ref_for_token(self, token: str) -> PcbLayerRef | None:
        try:
            ref = PcbLayerRef.parse(token)
        except PcbLayerResolutionError:
            return None
        entry = self.entry_for_ref(ref)
        if entry is None:
            return ref
        return entry.ref

    def ref_for_v7_saved_layer_id(self, value: int) -> PcbLayerRef | None:
        saved_layer_id = _native_int(value, field_name="v7_saved_layer_id")
        for entry in self.entries:
            if entry.ref.v7_saved_layer_id == saved_layer_id:
                return entry.ref
        return PcbLayerRef.from_v7_saved_layer_id(saved_layer_id)

    def ref_for_runtime_v7_layer_id(self, value: int) -> PcbLayerRef | None:
        runtime_id = _native_int(value, field_name="runtime_v7_layer_id")
        for entry in self.entries:
            if entry.ref.runtime_v7_layer_id == runtime_id:
                return entry.ref
        return None

    def ref_for_legacy_layer(self, layer: int | PcbLayer) -> PcbLayerRef | None:
        try:
            ref = PcbLayerRef.from_legacy(layer)
        except PcbLayerResolutionError:
            return None
        entry = self.entry_for_ref(ref)
        if entry is None:
            return ref
        return entry.ref

    def entries_for_display_name(self, name: str) -> tuple[PcbLayerRegistryEntry, ...]:
        normalized = _normalize_display_name(name)
        return tuple(
            entry
            for entry in self.entries
            if _normalize_display_name(entry.display_name) == normalized
            or _normalize_display_name(entry.custom_name or "") == normalized
        )

    def coerce(
        self,
        value: PcbLayerLike,
        *,
        allow_native_tokens: bool = False,
    ) -> PcbLayerRef:
        return coerce_pcb_layer_ref(
            value,
            registry=self,
            allow_native_tokens=allow_native_tokens,
        )


def coerce_pcb_layer_ref(
    value: PcbLayerLike,
    *,
    registry: PcbLayerRegistry | None = None,
    allow_native_tokens: bool = False,
) -> PcbLayerRef:
    """Coerce public layer input to a stable `PcbLayerRef`."""

    if isinstance(value, PcbLayerRef):
        return value
    if isinstance(value, PcbLayerRegistryEntry):
        return value.ref
    if isinstance(value, PcbLayer):
        return PcbLayerRef.from_legacy(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return PcbLayerRef.from_legacy(value)
    if isinstance(value, str):
        try:
            return PcbLayerRef.parse(value, allow_native_tokens=allow_native_tokens)
        except PcbLayerResolutionError:
            if _has_native_token_prefix(value):
                raise
            if registry is None:
                raise
            matches = registry.entries_for_display_name(value)
            if len(matches) == 1:
                return matches[0].ref
            if matches:
                tokens = ", ".join(entry.ref.token for entry in matches)
                raise PcbLayerResolutionError(
                    f"Display name {value!r} matches more than one PCB layer: {tokens}"
                )
            raise

    raise PcbLayerResolutionError(
        f"Unsupported PCB layer reference input: {type(value).__name__}"
    )


def resolve_pcb_primitive_layer_state(
    primitive: object,
    *,
    registry: PcbLayerRegistry | None = None,
    legacy_layer_field: str = "layer",
    v7_saved_layer_id_field: str | None = "v7_layer_id",
    runtime_v7_layer_id_field: str | None = None,
    v7_layer_token_field: str | None = None,
) -> PcbPrimitiveLayerState:
    """Resolve a primitive layer from V7 side fields before legacy storage."""

    stored_legacy_layer_id = _optional_field_int(
        primitive,
        legacy_layer_field,
        field_name="stored_legacy_layer_id",
    )
    stored_v7_saved_layer_id = _optional_field_int(
        primitive,
        v7_saved_layer_id_field,
        field_name="stored_v7_saved_layer_id",
    )
    stored_runtime_v7_layer_id = _optional_field_int(
        primitive,
        runtime_v7_layer_id_field,
        field_name="stored_runtime_v7_layer_id",
    )
    stored_v7_layer_token = _optional_field_str(primitive, v7_layer_token_field)

    if stored_v7_saved_layer_id is not None and stored_v7_saved_layer_id > 0:
        ref = (
            registry.ref_for_v7_saved_layer_id(stored_v7_saved_layer_id)
            if registry is not None
            else PcbLayerRef.from_v7_saved_layer_id(stored_v7_saved_layer_id)
        )
        if ref is None:
            ref = PcbLayerRef.from_v7_saved_layer_id(stored_v7_saved_layer_id)
        return PcbPrimitiveLayerState(
            ref=ref,
            stored_legacy_layer_id=stored_legacy_layer_id,
            stored_v7_saved_layer_id=stored_v7_saved_layer_id,
            stored_runtime_v7_layer_id=stored_runtime_v7_layer_id,
            stored_v7_layer_token=stored_v7_layer_token,
            source_field=str(v7_saved_layer_id_field),
        )

    if stored_runtime_v7_layer_id is not None and stored_runtime_v7_layer_id > 0:
        ref = (
            registry.ref_for_runtime_v7_layer_id(stored_runtime_v7_layer_id)
            if registry is not None
            else None
        )
        if ref is None:
            ref = PcbLayerRef._from_runtime_v7_layer_id(stored_runtime_v7_layer_id)
        return PcbPrimitiveLayerState(
            ref=ref,
            stored_legacy_layer_id=stored_legacy_layer_id,
            stored_v7_saved_layer_id=stored_v7_saved_layer_id,
            stored_runtime_v7_layer_id=stored_runtime_v7_layer_id,
            stored_v7_layer_token=stored_v7_layer_token,
            source_field=str(runtime_v7_layer_id_field),
        )

    if stored_v7_layer_token:
        ref = (
            registry.coerce(stored_v7_layer_token, allow_native_tokens=True)
            if registry is not None
            else PcbLayerRef.parse(
                stored_v7_layer_token,
                allow_native_tokens=True,
            )
        )
        return PcbPrimitiveLayerState(
            ref=ref,
            stored_legacy_layer_id=stored_legacy_layer_id,
            stored_v7_saved_layer_id=stored_v7_saved_layer_id,
            stored_runtime_v7_layer_id=stored_runtime_v7_layer_id,
            stored_v7_layer_token=stored_v7_layer_token,
            source_field=str(v7_layer_token_field),
        )

    if stored_legacy_layer_id is not None:
        ref = (
            registry.ref_for_legacy_layer(stored_legacy_layer_id)
            if registry is not None
            else None
        )
        if ref is None:
            ref = PcbLayerRef.from_legacy(stored_legacy_layer_id)
        return PcbPrimitiveLayerState(
            ref=ref,
            stored_legacy_layer_id=stored_legacy_layer_id,
            stored_v7_saved_layer_id=stored_v7_saved_layer_id,
            stored_runtime_v7_layer_id=stored_runtime_v7_layer_id,
            stored_v7_layer_token=stored_v7_layer_token,
            source_field=legacy_layer_field,
        )

    raise PcbLayerResolutionError(
        f"{primitive.__class__.__name__} has no resolvable PCB layer fields"
    )


def _coerce_pcb_authoring_layer_storage(
    layer: PcbLayerLike,
    *,
    explicit_v7_saved_layer_id: int | None = None,
    registry: PcbLayerRegistry | None = None,
) -> _PcbAuthoringLayerStorage:
    explicit_override_allowed = _is_legacy_layer_input(layer)
    ref = coerce_pcb_layer_ref(layer, registry=registry)
    explicit_id = _coerce_explicit_v7_saved_layer_id(explicit_v7_saved_layer_id)
    if explicit_id is not None and not explicit_override_allowed:
        raise PcbLayerResolutionError(
            "Explicit v7_layer_id overrides are only supported with legacy "
            "integer or PcbLayer inputs"
        )
    if explicit_id is not None:
        _validate_explicit_v7_saved_layer_override(ref, explicit_id)
    saved_layer_id = explicit_id or ref.v7_saved_layer_id
    if saved_layer_id is None:
        raise PcbLayerResolutionError(
            f"Layer {ref.token} has no serialized V7 saved-layer id"
        )

    legacy_layer_id = ref.legacy_layer_id
    if legacy_layer_id is None:
        legacy_layer_id = _legacy_fallback_for_v7_only_authoring_ref(
            ref,
            registry=registry,
        )

    return _PcbAuthoringLayerStorage(
        ref=ref,
        legacy_layer_id=legacy_layer_id,
        v7_saved_layer_id=saved_layer_id,
    )


def _coerce_region_authoring_layer_storage(
    layer: PcbLayerLike,
    explicit_v7_layer: str | None,
    *,
    registry: PcbLayerRegistry | None = None,
) -> tuple[_PcbAuthoringLayerStorage, str | None]:
    """
    Resolve region authoring layer storage with an optional V7_LAYER override.

    Native region rows may carry a legacy layer byte with no layer meaning
    while the authoritative identity lives in V7_LAYER text: board region and
    split-line rows store legacy byte 0 with reserved system mechanical
    tokens (MECHANICAL64530 and above). Accept exactly that replay shape
    here; all other inputs go through the standard coercion and
    override-consistency checks.
    """
    if (
        explicit_v7_layer is not None
        and _is_legacy_layer_input(layer)
        and int(layer) == 0
    ):
        token = str(explicit_v7_layer).strip()
        if not token:
            raise PcbLayerResolutionError("v7_layer must be a non-empty layer token")
        ref = PcbLayerRef.parse(token)
        is_reserved_system_mechanical = (
            ref.family == PcbLayerFamily.MECHANICAL
            and ref.number is not None
            and _RESERVED_SYSTEM_MECHANICAL_MIN
            <= ref.number
            <= _RESERVED_SYSTEM_MECHANICAL_MAX
        )
        if not is_reserved_system_mechanical or ref.v7_saved_layer_id is None:
            raise PcbLayerResolutionError(
                "Legacy layer 0 is only supported with a reserved system "
                "mechanical v7_layer token (MECHANICAL64530 and above)"
            )
        storage = _PcbAuthoringLayerStorage(
            ref=ref,
            legacy_layer_id=0,
            v7_saved_layer_id=ref.v7_saved_layer_id,
        )
        return storage, ref.token
    storage = _coerce_pcb_authoring_layer_storage(layer, registry=registry)
    explicit_token = _coerce_explicit_v7_layer_token_override(
        layer,
        storage.ref,
        explicit_v7_layer,
    )
    return storage, explicit_token


def _coerce_explicit_v7_layer_token_override(
    layer: PcbLayerLike,
    layer_ref: PcbLayerRef,
    explicit_v7_layer: str | None,
) -> str | None:
    if explicit_v7_layer is None:
        return None
    if not _is_legacy_layer_input(layer):
        raise PcbLayerResolutionError(
            "Explicit v7_layer overrides are only supported with legacy "
            "integer or PcbLayer inputs"
        )
    token = str(explicit_v7_layer).strip()
    if not token:
        raise PcbLayerResolutionError("v7_layer must be a non-empty layer token")
    explicit_ref = PcbLayerRef.parse(token)
    if _explicit_layer_override_matches_legacy_input(layer_ref, explicit_ref):
        return explicit_ref.token
    raise PcbLayerResolutionError(
        "Explicit v7_layer is inconsistent with the legacy layer input"
    )


def _coerce_pcb_pad_authoring_layer_storage(
    layer: PcbLayerLike,
) -> _PcbAuthoringLayerStorage:
    ref = coerce_pcb_layer_ref(layer)
    if ref.family == PcbLayerFamily.MECHANICAL and ref.legacy_layer is None:
        raise PcbLayerResolutionError(
            "Pad authoring on V7-only mechanical layers is not supported"
        )
    return _coerce_pcb_authoring_layer_storage(layer)


def _coerce_pcb_via_span_layer_storage(
    layer: PcbLayerLike,
    *,
    field_name: str,
) -> _PcbAuthoringLayerStorage:
    ref = coerce_pcb_layer_ref(layer)
    if ref.family != PcbLayerFamily.SIGNAL:
        raise PcbLayerResolutionError(f"{field_name} must be a signal layer")
    if ref.legacy_layer_id is None:
        raise PcbLayerResolutionError(
            f"{field_name} {ref.token} requires registry-backed V7 via-span "
            "authoring support"
        )
    return _coerce_pcb_authoring_layer_storage(layer)


def _validate_explicit_v7_saved_layer_override(
    legacy_ref: PcbLayerRef,
    explicit_id: int,
) -> None:
    explicit_ref = PcbLayerRef.from_v7_saved_layer_id(explicit_id)
    if _explicit_layer_override_matches_legacy_input(legacy_ref, explicit_ref):
        return
    raise PcbLayerResolutionError(
        "Explicit v7_layer_id is inconsistent with the legacy layer input"
    )


def _explicit_layer_override_matches_legacy_input(
    legacy_ref: PcbLayerRef,
    explicit_ref: PcbLayerRef,
) -> bool:
    if explicit_ref == legacy_ref:
        return True
    if (
        legacy_ref.family == PcbLayerFamily.MECHANICAL
        and explicit_ref.family == PcbLayerFamily.MECHANICAL
        and legacy_ref.number == 16
        and explicit_ref.number is not None
        and explicit_ref.number <= _MAX_USER_AUTHORING_MECHANICAL_LAYER
    ):
        return True
    # Native board-cutout region rows store legacy layer KEEPOUT with
    # authoritative V7_LAYER text MULTILAYER.
    return (
        legacy_ref.legacy_layer is PcbLayer.KEEPOUT
        and explicit_ref.legacy_layer is PcbLayer.MULTI_LAYER
    )


def _is_legacy_layer_input(layer: PcbLayerLike) -> TypeGuard[int]:
    return isinstance(layer, int) and not isinstance(layer, bool)


def _positive_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise PcbLayerResolutionError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PcbLayerResolutionError(
            f"{field_name} must be a positive integer"
        ) from exc
    if parsed <= 0:
        raise PcbLayerResolutionError(f"{field_name} must be a positive integer")
    return parsed


def _bounded_positive_int(value: int, *, field_name: str, maximum: int) -> int:
    parsed = _positive_int(value, field_name=field_name)
    if parsed > maximum:
        raise PcbLayerResolutionError(f"{field_name} must be <= {maximum}")
    return parsed


def _native_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise PcbLayerResolutionError(f"{field_name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PcbLayerResolutionError(f"{field_name} must be an integer") from exc
    if parsed < 0:
        raise PcbLayerResolutionError(f"{field_name} must be non-negative")
    return parsed


def _coerce_legacy_layer(layer: int | PcbLayer) -> PcbLayer:
    if isinstance(layer, bool):
        raise PcbLayerResolutionError("Raw integer layer values must be legacy TV6 IDs")
    if isinstance(layer, PcbLayer):
        return layer
    try:
        return PcbLayer(int(layer))
    except ValueError as exc:
        raise PcbLayerResolutionError(
            "Raw integer layer values are legacy TV6 IDs. Use "
            "PcbLayerRef.from_v7_saved_layer_id(...) for native saved-layer ids."
        ) from exc


def _legacy_misc_v7_saved_layer_id(layer: PcbLayer) -> int | None:
    mapping = {
        PcbLayer.TOP_OVERLAY: int(PcbV7SavedLayerId.TOP_OVERLAY),
        PcbLayer.BOTTOM_OVERLAY: int(PcbV7SavedLayerId.BOTTOM_OVERLAY),
        PcbLayer.TOP_PASTE: int(PcbV7SavedLayerId.TOP_PASTE),
        PcbLayer.BOTTOM_PASTE: int(PcbV7SavedLayerId.BOTTOM_PASTE),
        PcbLayer.TOP_SOLDER: int(PcbV7SavedLayerId.TOP_SOLDER),
        PcbLayer.BOTTOM_SOLDER: int(PcbV7SavedLayerId.BOTTOM_SOLDER),
        PcbLayer.DRILL_GUIDE: int(PcbV7SavedLayerId.DRILL_GUIDE),
        PcbLayer.KEEPOUT: int(PcbV7SavedLayerId.KEEPOUT),
        PcbLayer.DRILL_DRAWING: int(PcbV7SavedLayerId.DRILL_DRAWING),
        PcbLayer.MULTI_LAYER: int(PcbV7SavedLayerId.MULTI_LAYER),
        PcbLayer.CONNECT: int(PcbV7SavedLayerId.CONNECT),
    }
    return mapping.get(layer)


def _legacy_layer_from_system_v7_saved_layer_id(value: int) -> PcbLayer | None:
    mapping = {
        int(PcbV7SavedLayerId.TOP_OVERLAY): PcbLayer.TOP_OVERLAY,
        int(PcbV7SavedLayerId.BOTTOM_OVERLAY): PcbLayer.BOTTOM_OVERLAY,
        int(PcbV7SavedLayerId.TOP_PASTE): PcbLayer.TOP_PASTE,
        int(PcbV7SavedLayerId.BOTTOM_PASTE): PcbLayer.BOTTOM_PASTE,
        int(PcbV7SavedLayerId.TOP_SOLDER): PcbLayer.TOP_SOLDER,
        int(PcbV7SavedLayerId.BOTTOM_SOLDER): PcbLayer.BOTTOM_SOLDER,
        int(PcbV7SavedLayerId.DRILL_GUIDE): PcbLayer.DRILL_GUIDE,
        int(PcbV7SavedLayerId.KEEPOUT): PcbLayer.KEEPOUT,
        int(PcbV7SavedLayerId.DRILL_DRAWING): PcbLayer.DRILL_DRAWING,
        int(PcbV7SavedLayerId.MULTI_LAYER): PcbLayer.MULTI_LAYER,
        int(PcbV7SavedLayerId.CONNECT): PcbLayer.CONNECT,
    }
    return mapping.get(value)


def _parse_native_token(
    token: str,
    *,
    allow_native_tokens: bool,
) -> PcbLayerRef | None:
    raw = str(token or "").strip()
    native_match = re.fullmatch(r"(?i)(legacy|saved|v7|tv7|kind):(.+)", raw)
    if native_match is None:
        return None
    prefix = native_match.group(1).lower()
    if not allow_native_tokens:
        raise PcbLayerResolutionError(
            f"Native layer token {prefix!r} is diagnostic/import-only"
        )
    if prefix == "kind":
        raise PcbLayerResolutionError(
            "LayerKindMapping tokens require an explicit registry lookup"
        )
    native_value = _parse_int_token(native_match.group(2), field_name=prefix)
    if prefix == "legacy":
        return PcbLayerRef.from_legacy(native_value)
    if prefix in {"saved", "v7"}:
        return PcbLayerRef.from_v7_saved_layer_id(native_value)
    return PcbLayerRef._from_runtime_v7_layer_id(native_value)


def _has_native_token_prefix(token: str) -> bool:
    raw = str(token or "").strip()
    return re.match(r"(?i)^(legacy|saved|v7|tv7|kind):", raw) is not None


def _parse_int_token(value: str, *, field_name: str) -> int:
    token = str(value or "").strip()
    try:
        return int(token, 0)
    except ValueError as exc:
        raise PcbLayerResolutionError(f"{field_name} token is not an integer") from exc


def _normalize_layer_token(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _normalize_display_name(value: str) -> str:
    return str(value or "").strip().casefold()


def _optional_field_int(
    owner: object,
    attr_name: str | None,
    *,
    field_name: str,
) -> int | None:
    if attr_name is None or not hasattr(owner, attr_name):
        return None
    value = getattr(owner, attr_name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise PcbLayerResolutionError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PcbLayerResolutionError(f"{field_name} must be an integer") from exc


def _optional_field_str(owner: object, attr_name: str | None) -> str | None:
    if attr_name is None or not hasattr(owner, attr_name):
        return None
    value = getattr(owner, attr_name)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _coerce_explicit_v7_saved_layer_id(value: int | None) -> int | None:
    if value is None:
        return None
    saved_layer_id = _native_int(value, field_name="v7_saved_layer_id")
    if saved_layer_id <= 0:
        raise PcbLayerResolutionError("v7_saved_layer_id must be positive")
    return saved_layer_id


def _legacy_fallback_for_v7_only_authoring_ref(
    ref: PcbLayerRef,
    *,
    registry: PcbLayerRegistry | None = None,
) -> int:
    placeholder_id = _v7_only_legacy_placeholder_id(ref)
    if ref.family == PcbLayerFamily.MECHANICAL and placeholder_id is not None:
        return placeholder_id
    if ref.family == PcbLayerFamily.MECHANICAL:
        raise PcbLayerResolutionError(
            "Mechanical user-layer authoring supports Mechanical1 through "
            f"Mechanical{_MAX_USER_AUTHORING_MECHANICAL_LAYER}"
        )
    if placeholder_id is not None and _registry_supports_v7_only_signal_authoring(
        registry, ref
    ):
        return placeholder_id
    raise PcbLayerResolutionError(
        f"Layer {ref.token} requires registry-backed V7 authoring support"
    )


def _v7_only_legacy_placeholder_id(ref: PcbLayerRef) -> int | None:
    if ref.legacy_layer is not None:
        return None
    if (
        ref.family == PcbLayerFamily.MECHANICAL
        and ref.number is not None
        and 17 <= ref.number <= _MAX_USER_AUTHORING_MECHANICAL_LAYER
    ):
        return PcbLayer.MECHANICAL_16.value
    if (
        ref.family == PcbLayerFamily.SIGNAL
        and ref.signal_kind == PcbSignalLayerKind.MID
        and ref.number is not None
        and 31 <= ref.number <= _MAX_AD26_MID_SIGNAL_LAYER
    ):
        return PcbLayer.MID30.value
    return None


def _same_pcb_layer_ref_representation(
    first: PcbLayerRef,
    second: PcbLayerRef,
) -> bool:
    return (
        first.family == second.family
        and first.token == second.token
        and first.signal_kind == second.signal_kind
        and first.number == second.number
        and first.legacy_layer == second.legacy_layer
        and first.v7_saved_layer_id == second.v7_saved_layer_id
        and first.runtime_v7_layer_id == second.runtime_v7_layer_id
    )


def _registry_supports_v7_only_signal_authoring(
    registry: PcbLayerRegistry | None,
    ref: PcbLayerRef,
) -> bool:
    entry = _matching_registry_entry(registry, ref)
    if entry is None or entry.enabled is False or entry.used_by_primitives is False:
        return False
    expected_position = _expected_signal_stack_position(ref)
    if expected_position is None:
        return False
    if entry.signal_stack_position != expected_position:
        return False
    return (
        entry.signal_layer_count is not None
        and entry.signal_layer_count >= expected_position + 1
    )


def _matching_registry_entry(
    registry: PcbLayerRegistry | None,
    ref: PcbLayerRef,
) -> PcbLayerRegistryEntry | None:
    if registry is None:
        return None
    entry = registry.entry_for_ref(ref)
    if entry is None:
        return None
    if entry.ref.v7_saved_layer_id is None or ref.v7_saved_layer_id is None:
        return entry
    if entry.ref.v7_saved_layer_id == ref.v7_saved_layer_id:
        return entry
    return None


def _expected_signal_stack_position(ref: PcbLayerRef) -> int | None:
    if ref.signal_kind == PcbSignalLayerKind.TOP:
        return 1
    if ref.signal_kind == PcbSignalLayerKind.MID and ref.number is not None:
        return ref.number + 1
    return None


__all__ = [
    "PcbLayerFamily",
    "PcbLayerLike",
    "PcbLayerRef",
    "PcbLayerRegistry",
    "PcbLayerRegistryEntry",
    "PcbLayerResolutionError",
    "PcbPrimitiveLayerState",
    "PcbSignalLayerKind",
    "coerce_pcb_layer_ref",
    "resolve_pcb_primitive_layer_state",
]
