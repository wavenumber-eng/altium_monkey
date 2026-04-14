"""Public PCB surface view vocabulary and layer mapping helpers."""

from __future__ import annotations

from enum import StrEnum

from .altium_api_markers import public_api
from .altium_record_types import PcbLayer


@public_api
class PCB_SurfaceSide(StrEnum):
    """Board surface side for PCB rendering workflows."""

    TOP = "top"
    BOTTOM = "bottom"


@public_api
class PCB_SurfaceRole(StrEnum):
    """Logical layer role within one board surface view."""

    COPPER = "copper"
    SILKSCREEN = "silkscreen"
    SOLDER_MASK = "solder_mask"
    PASTE_MASK = "paste_mask"


DEFAULT_PCB_SURFACE_ROLES: tuple[PCB_SurfaceRole, ...] = (
    PCB_SurfaceRole.COPPER,
    PCB_SurfaceRole.SILKSCREEN,
)


@public_api
def pcb_surface_layer(side: PCB_SurfaceSide, role: PCB_SurfaceRole) -> PcbLayer:
    """Return the concrete PCB layer for one logical surface role."""

    mapping = {
        (PCB_SurfaceSide.TOP, PCB_SurfaceRole.COPPER): PcbLayer.TOP,
        (PCB_SurfaceSide.TOP, PCB_SurfaceRole.SILKSCREEN): PcbLayer.TOP_OVERLAY,
        (PCB_SurfaceSide.TOP, PCB_SurfaceRole.SOLDER_MASK): PcbLayer.TOP_SOLDER,
        (PCB_SurfaceSide.TOP, PCB_SurfaceRole.PASTE_MASK): PcbLayer.TOP_PASTE,
        (PCB_SurfaceSide.BOTTOM, PCB_SurfaceRole.COPPER): PcbLayer.BOTTOM,
        (PCB_SurfaceSide.BOTTOM, PCB_SurfaceRole.SILKSCREEN): PcbLayer.BOTTOM_OVERLAY,
        (PCB_SurfaceSide.BOTTOM, PCB_SurfaceRole.SOLDER_MASK): PcbLayer.BOTTOM_SOLDER,
        (PCB_SurfaceSide.BOTTOM, PCB_SurfaceRole.PASTE_MASK): PcbLayer.BOTTOM_PASTE,
    }
    return mapping[(side, role)]


@public_api
def pcb_surface_layers(
    side: PCB_SurfaceSide,
    *,
    role_order: list[PCB_SurfaceRole] | tuple[PCB_SurfaceRole, ...] | None = None,
    include_missing_roles: bool = True,
) -> list[PcbLayer]:
    """Resolve an ordered surface-layer set for one board side."""

    ordered_roles = list(role_order or DEFAULT_PCB_SURFACE_ROLES)
    if include_missing_roles:
        for role in DEFAULT_PCB_SURFACE_ROLES:
            if role not in ordered_roles:
                ordered_roles.append(role)
    return [pcb_surface_layer(side, role) for role in ordered_roles]
