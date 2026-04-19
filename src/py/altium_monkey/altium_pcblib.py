"""
Parse and round-trip Altium PcbLib footprint libraries.
"""

import copy
import json
import logging
import struct
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from .altium_api_markers import public_api
from .altium_embedded_files import sanitize_embedded_asset_name
from .altium_pcb_stream_helpers import (
    count_length_prefixed_records as _count_length_prefixed_records,
)
from .altium_pcblib_sections import PcbLibSectionKeys
from .altium_pcb_embedded_model_compose import (
    collect_pcblib_embedded_model_entries,
    copy_footprint_with_models_into_builder,
    parse_model_records_from_bytes,
    resolve_footprint_body_model_entries,
)
from .altium_pcb_custom_shapes import resolve_pcblib_custom_pad_shapes
from .altium_pcb_extended_primitive_information import (
    AltiumPcbExtendedPrimitiveInformation,
    parse_extended_primitive_information_stream,
)
from .altium_ole import AltiumOleFile, AltiumOleWriter
from .altium_pcb_enums import PadShape, PcbBodyProjection
from .altium_pcb_step_bounds import compute_step_model_bounds_mils
from .altium_record_types import PcbLayer, PcbRecordType
from .altium_record_pcb__model import AltiumPcbModel
from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_pcb__track import AltiumPcbTrack
from .altium_record_pcb__arc import AltiumPcbArc
from .altium_record_pcb__text import AltiumPcbText
from .altium_record_pcb__fill import AltiumPcbFill
from .altium_record_pcb__region import AltiumPcbRegion
from .altium_record_pcb__via import AltiumPcbVia
from .altium_record_pcb__component_body import AltiumPcbComponentBody

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderOptions
    from .altium_pcbdoc import AltiumPcbDoc

log = logging.getLogger(__name__)

PcbPointMils = Sequence[float]
PcbBoundsMils = Sequence[float]


def _coerce_point_mils(point: PcbPointMils, name: str) -> tuple[float, float]:
    """
    Normalize a public PCB point argument into an `(x_mils, y_mils)` tuple.
    """
    if len(point) != 2:
        raise ValueError(f"{name} must contain exactly two mil values")
    return float(point[0]), float(point[1])


def _coerce_bounds_mils(
    bounds: PcbBoundsMils, name: str
) -> tuple[float, float, float, float]:
    """
    Normalize public PCB rectangular bounds into `(left, bottom, right, top)`.
    """
    if len(bounds) != 4:
        raise ValueError(f"{name} must contain exactly four mil values")
    left_mils, bottom_mils, right_mils, top_mils = (
        float(bounds[0]),
        float(bounds[1]),
        float(bounds[2]),
        float(bounds[3]),
    )
    if right_mils <= left_mils or top_mils <= bottom_mils:
        raise ValueError(
            f"{name} must be ordered as left, bottom, right, top with positive size"
        )
    return left_mils, bottom_mils, right_mils, top_mils


# ============================================================================
# Footprint Container
# ============================================================================


class AltiumPcbFootprint:
    """
    Single footprint within a PcbLib file.

    Contains all primitive types: pads, tracks, arcs, fills, text,
    vias, regions, and component bodies. Each primitive uses the same
    OOP record class as PcbDoc (e.g. AltiumPcbPad, AltiumPcbTrack).
    """

    def __init__(self, name: str = "") -> None:
        """
        Create an in-memory PCB footprint container.

        Use `AltiumPcbLib.add_footprint(...)` or
        `AltiumPcbLib.add_existing_footprint(...)` before calling public
        `add_*` primitive methods so ownership, ordering, and model streams can
        be managed by the parent library.

        Args:
            name: Footprint pattern name stored in the PcbLib.
        """
        self.name: str = name

        self.pads: list["AltiumPcbPad"] = []
        self.tracks: list["AltiumPcbTrack"] = []
        self.arcs: list["AltiumPcbArc"] = []
        self.fills: list["AltiumPcbFill"] = []
        self.texts: list["AltiumPcbText"] = []
        self.vias: list["AltiumPcbVia"] = []
        self.regions: list["AltiumPcbRegion"] = []
        self.component_bodies: list["AltiumPcbComponentBody"] = []

        self.parameters: dict[str, str] = {}

        # Ordered list of all primitives in parse order (for stream assembly)
        self._record_order: list = []

        # OLE storage name (may be truncated to 31 chars for long names)
        self._ole_storage_name: str = name

        # Raw binary data for round-trip
        self.raw_header: bytes | None = None
        self.raw_data: bytes | None = None
        self.raw_parameters: bytes | None = None
        self.raw_widestrings: bytes | None = None
        self.raw_primitive_guids: bytes | None = None
        self.raw_primitive_guids_header: bytes | None = None
        self.raw_extended_primitive_info: bytes | None = None
        self.raw_extended_primitive_info_header: bytes | None = None
        self.extended_primitive_information: list[
            AltiumPcbExtendedPrimitiveInformation
        ] = []
        self.raw_uniqueid_info: bytes | None = None
        self.raw_uniqueid_info_header: bytes | None = None
        self._authoring_builder: Any | None = None

    def __getstate__(self) -> dict[str, object]:
        state = dict(self.__dict__)
        state["_authoring_builder"] = None
        return state

    def _bind_authoring_builder(self, builder: Any) -> None:
        self._authoring_builder = builder

    def _require_authoring_builder(self) -> Any:
        if self._authoring_builder is None:
            raise RuntimeError(
                "Footprint is not attached to an authoring PcbLib. "
                "Create or attach it with AltiumPcbLib.add_footprint(...) or "
                "AltiumPcbLib.add_existing_footprint(...) before adding primitives."
            )
        return self._authoring_builder

    def add_pad(
        self,
        *,
        designator: str,
        position_mils: PcbPointMils,
        width_mils: float,
        height_mils: float,
        layer: int | PcbLayer = PcbLayer.TOP,
        shape: int | PadShape = PadShape.RECTANGLE,
        rotation_degrees: float = 0.0,
        hole_size_mils: float = 0.0,
        plated: bool | None = None,
        corner_radius_percent: int | None = None,
        slot_length_mils: float = 0.0,
        slot_rotation_degrees: float = 0.0,
    ) -> AltiumPcbPad:
        """
        Add a pad to this footprint using public mil units.

        Args:
            designator: Pad designator text, for example `"1"`.
            position_mils: Pad center as `(x_mils, y_mils)`.
            width_mils: Pad width in mils.
            height_mils: Pad height in mils.
            layer: Target PCB layer.
            shape: Pad shape.
            rotation_degrees: Pad rotation in degrees.
            hole_size_mils: Drill hole size in mils. Use 0 for SMT pads.
            plated: Optional plated-through flag.
            corner_radius_percent: Optional rounded-rectangle corner radius percentage.
            slot_length_mils: Optional total slotted-hole length in mils.
            slot_rotation_degrees: Optional slotted-hole rotation in degrees.

        Returns:
            The authored `AltiumPcbPad` record.
        """
        x_mils, y_mils = _coerce_point_mils(position_mils, "position_mils")
        return self._require_authoring_builder().add_pad(
            self,
            designator=designator,
            x_mil=x_mils,
            y_mil=y_mils,
            width_mil=width_mils,
            height_mil=height_mils,
            layer=layer,
            shape=int(shape),
            rotation_degrees=rotation_degrees,
            hole_size_mil=hole_size_mils,
            plated=plated,
            corner_radius_percent=corner_radius_percent,
            slot_length_mil=slot_length_mils,
            slot_rotation_degrees=slot_rotation_degrees,
        )

    def add_custom_pad(
        self,
        *,
        designator: str,
        position_mils: PcbPointMils,
        outline_points_mils: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.TOP,
        offset_mils: PcbPointMils = (0.0, 0.0),
        anchor_diameter_mils: float = 1.0,
        hole_points_mils: list[list[tuple[float, float]]] | None = None,
        outline_points_are_local: bool = True,
        paste_rule_expansion: bool = True,
        solder_rule_expansion: bool = True,
    ) -> AltiumPcbPad:
        """
        Add a custom pad using mil units and local or absolute polygon points.

        Args:
            designator: Pad designator text.
            position_mils: Anchor pad center as `(x_mils, y_mils)`.
            outline_points_mils: Custom pad outline points in mils.
            layer: Target PCB layer.
            offset_mils: Offset from anchor center to custom shape center.
            anchor_diameter_mils: Diameter of the small anchor pad in mils.
            hole_points_mils: Optional cutout polygons in mils.
            outline_points_are_local: Treat outline points as shape-local offsets.
            paste_rule_expansion: Enable rule-driven paste mask expansion.
            solder_rule_expansion: Enable rule-driven solder mask expansion.

        Returns:
            The authored custom `AltiumPcbPad` record.
        """
        x_mils, y_mils = _coerce_point_mils(position_mils, "position_mils")
        offset_x_mils, offset_y_mils = _coerce_point_mils(offset_mils, "offset_mils")
        return self._require_authoring_builder().add_custom_pad(
            self,
            designator=designator,
            x_mil=x_mils,
            y_mil=y_mils,
            outline_points_mil=outline_points_mils,
            layer=layer,
            offset_x_mil=offset_x_mils,
            offset_y_mil=offset_y_mils,
            anchor_diameter_mil=anchor_diameter_mils,
            hole_points_mil=hole_points_mils,
            outline_points_are_local=outline_points_are_local,
            paste_rule_expansion=paste_rule_expansion,
            solder_rule_expansion=solder_rule_expansion,
        )

    def add_track(
        self,
        start_mils: PcbPointMils,
        end_mils: PcbPointMils,
        *,
        width_mils: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        v7_layer_id: int | None = None,
    ) -> AltiumPcbTrack:
        """
        Add a straight track segment to this footprint using mil units.

        Args:
            start_mils: Track start as `(x_mils, y_mils)`.
            end_mils: Track end as `(x_mils, y_mils)`.
            width_mils: Track width in mils.
            layer: `PcbLayer` or native layer id.
            v7_layer_id: Optional explicit V7 layer-kind id for compatibility
                with source libraries that store it separately.

        Returns:
            The authored `AltiumPcbTrack` record.
        """
        start_x_mils, start_y_mils = _coerce_point_mils(start_mils, "start_mils")
        end_x_mils, end_y_mils = _coerce_point_mils(end_mils, "end_mils")
        return self._require_authoring_builder().add_track(
            self,
            start_x_mil=start_x_mils,
            start_y_mil=start_y_mils,
            end_x_mil=end_x_mils,
            end_y_mil=end_y_mils,
            width_mil=width_mils,
            layer=layer,
            v7_layer_id=v7_layer_id,
        )

    def add_arc(
        self,
        *,
        center_mils: PcbPointMils,
        radius_mils: float,
        start_angle_degrees: float,
        end_angle_degrees: float,
        width_mils: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        v7_layer_id: int | None = None,
    ) -> AltiumPcbArc:
        """
        Add a circular arc to this footprint using mil units and degree angles.

        Args:
            center_mils: Arc center as `(x_mils, y_mils)`.
            radius_mils: Arc radius in mils.
            start_angle_degrees: Start angle in degrees.
            end_angle_degrees: End angle in degrees.
            width_mils: Arc stroke width in mils.
            layer: `PcbLayer` or native layer id.
            v7_layer_id: Optional explicit V7 layer-kind id.

        Returns:
            The authored `AltiumPcbArc` record.
        """
        center_x_mils, center_y_mils = _coerce_point_mils(center_mils, "center_mils")
        return self._require_authoring_builder().add_arc(
            self,
            center_x_mil=center_x_mils,
            center_y_mil=center_y_mils,
            radius_mil=radius_mils,
            start_angle_degrees=start_angle_degrees,
            end_angle_degrees=end_angle_degrees,
            width_mil=width_mils,
            layer=layer,
            v7_layer_id=v7_layer_id,
        )

    def add_fill(
        self,
        corner1_mils: PcbPointMils,
        corner2_mils: PcbPointMils,
        *,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        rotation_degrees: float = 0.0,
        v7_layer_id: int | None = None,
    ) -> AltiumPcbFill:
        """
        Add a rectangular fill to this footprint using opposite mil corners.

        Args:
            corner1_mils: First fill corner as `(x_mils, y_mils)`.
            corner2_mils: Opposite fill corner as `(x_mils, y_mils)`.
            layer: `PcbLayer` or native layer id.
            rotation_degrees: Fill rotation in degrees.
            v7_layer_id: Optional explicit V7 layer-kind id.

        Returns:
            The authored `AltiumPcbFill` record.
        """
        pos1_x_mils, pos1_y_mils = _coerce_point_mils(corner1_mils, "corner1_mils")
        pos2_x_mils, pos2_y_mils = _coerce_point_mils(corner2_mils, "corner2_mils")
        return self._require_authoring_builder().add_fill(
            self,
            pos1_x_mil=pos1_x_mils,
            pos1_y_mil=pos1_y_mils,
            pos2_x_mil=pos2_x_mils,
            pos2_y_mil=pos2_y_mils,
            layer=layer,
            rotation_degrees=rotation_degrees,
            v7_layer_id=v7_layer_id,
        )

    def add_via(
        self,
        *,
        position_mils: PcbPointMils,
        diameter_mils: float,
        hole_size_mils: float,
        layer_start: int | PcbLayer = PcbLayer.TOP,
        layer_end: int | PcbLayer = PcbLayer.BOTTOM,
    ) -> AltiumPcbVia:
        """
        Add a via primitive to this footprint using mil units.

        Args:
            position_mils: Via center as `(x_mils, y_mils)`.
            diameter_mils: Via pad diameter in mils.
            hole_size_mils: Via drill diameter in mils.
            layer_start: Start layer as `PcbLayer` or native layer id.
            layer_end: End layer as `PcbLayer` or native layer id.

        Returns:
            The authored `AltiumPcbVia` record.
        """
        x_mils, y_mils = _coerce_point_mils(position_mils, "position_mils")
        return self._require_authoring_builder().add_via(
            self,
            x_mil=x_mils,
            y_mil=y_mils,
            diameter_mil=diameter_mils,
            hole_size_mil=hole_size_mils,
            layer_start=layer_start,
            layer_end=layer_end,
        )

    def add_region(
        self,
        *,
        outline_points_mils: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.TOP,
        hole_points_mils: list[list[tuple[float, float]]] | None = None,
        kind: int = 0,
        is_board_cutout: bool = False,
        is_shapebased: bool = False,
        is_keepout: bool = False,
        keepout_restrictions: int = 0,
        subpoly_index: int = 0,
    ) -> AltiumPcbRegion:
        """
        Add a region polygon to this footprint using mil-unit vertices.

        Args:
            outline_points_mils: Outer polygon vertices in mils.
            layer: `PcbLayer` or native layer id.
            hole_points_mils: Optional list of hole polygons in mils.
            kind: Native region kind. Prefer `PcbRegionKind` values when
                authoring new public examples.
            is_board_cutout: Mark the region as a board cutout.
            is_shapebased: Write as shape-based region metadata where supported.
            is_keepout: Mark the region as a keepout.
            keepout_restrictions: Native keepout restriction bitmask.
            subpoly_index: Native sub-polygon index.

        Returns:
            The authored `AltiumPcbRegion` record.
        """
        return self._require_authoring_builder().add_region(
            self,
            outline_points_mil=outline_points_mils,
            layer=layer,
            hole_points_mil=hole_points_mils,
            kind=kind,
            is_board_cutout=is_board_cutout,
            is_shapebased=is_shapebased,
            is_keepout=is_keepout,
            keepout_restrictions=keepout_restrictions,
            subpoly_index=subpoly_index,
        )

    def add_text(
        self,
        *,
        text: str,
        position_mils: PcbPointMils,
        height_mils: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        rotation_degrees: float = 0.0,
        stroke_width_mils: float = 10.0,
        font_name: str = "Arial",
        is_comment: bool = False,
        is_designator: bool = False,
        is_mirrored: bool = False,
    ) -> AltiumPcbText:
        """
        Add stroke text to this footprint using mil units.

        Args:
            text: Text content.
            position_mils: Text anchor position as `(x_mils, y_mils)`.
            height_mils: Text height in mils.
            layer: `PcbLayer` or native layer id.
            rotation_degrees: Text rotation in degrees.
            stroke_width_mils: Stroke font line width in mils.
            font_name: Native stroke/TrueType font name metadata.
            is_comment: Mark as component comment/value text.
            is_designator: Mark as component designator text.
            is_mirrored: Mirror text geometry.

        Returns:
            The authored `AltiumPcbText` record.
        """
        x_mils, y_mils = _coerce_point_mils(position_mils, "position_mils")
        return self._require_authoring_builder().add_text(
            self,
            text=text,
            x_mil=x_mils,
            y_mil=y_mils,
            height_mil=height_mils,
            layer=layer,
            rotation_degrees=rotation_degrees,
            stroke_width_mil=stroke_width_mils,
            font_name=font_name,
            is_comment=is_comment,
            is_designator=is_designator,
            is_mirrored=is_mirrored,
        )

    def add_component_body(
        self,
        *,
        outline_points_mils: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.MECHANICAL_1,
        overall_height_mils: float,
        standoff_height_mils: float = 0.5,
        cavity_height_mils: float = 0.0,
        body_projection: PcbBodyProjection = PcbBodyProjection.TOP,
        model: AltiumPcbModel | None = None,
        model_2d_mils: PcbPointMils = (0.0, 0.0),
        model_2d_rotation_degrees: float = 0.0,
        model_3d_rotx_degrees: float | None = None,
        model_3d_roty_degrees: float | None = None,
        model_3d_rotz_degrees: float | None = None,
        model_3d_dz_mils: float | None = None,
        model_checksum: int | None = None,
        identifier: str | None = None,
        name: str = " ",
        body_color_3d: int = 0x808080,
        body_opacity_3d: float = 1.0,
        model_type: int = 1,
        model_source: str | None = None,
    ) -> AltiumPcbComponentBody:
        """
        Add a component body outline using mil-unit vertices.

        This low-level helper maps directly to Altium's component-body record.
        Use `add_extruded_3d_body(...)` for a generic extruded solid and
        `add_embedded_3d_model(...)` for a STEP-backed model body.

        Args:
            outline_points_mils: Footprint-local 2D projection polygon vertices
                in mils.
            layer: `PcbLayer` or native layer id that owns the projection.
            overall_height_mils: Top Z height of the body in mils.
            standoff_height_mils: Bottom Z height of the body in mils.
            cavity_height_mils: Native cavity height field in mils.
            body_projection: `PcbBodyProjection` side/projection mode.
            model: Optional embedded or linked `AltiumPcbModel`.
            model_2d_mils: 2D model placement point as `(x_mils, y_mils)`.
            model_2d_rotation_degrees: 2D model placement rotation in degrees.
            model_3d_rotx_degrees: Optional 3D model X-axis rotation override.
            model_3d_roty_degrees: Optional 3D model Y-axis rotation override.
            model_3d_rotz_degrees: Optional 3D model Z-axis rotation override.
            model_3d_dz_mils: Optional 3D model Z offset override in mils.
            model_checksum: Optional native model checksum override.
            identifier: Optional native body identifier.
            name: Body name shown by Altium.
            body_color_3d: Native Win32 color integer for generic 3D bodies.
            body_opacity_3d: Body opacity from 0.0 to 1.0.
            model_type: Native model type. Use 0 for extruded bodies and 1 for
                STEP-backed bodies.
            model_source: Optional native model source string override.

        Returns:
            The authored `AltiumPcbComponentBody` record.
        """
        model_2d_x_mils, model_2d_y_mils = _coerce_point_mils(
            model_2d_mils, "model_2d_mils"
        )
        return self._require_authoring_builder().add_component_body(
            self,
            outline_points_mil=outline_points_mils,
            layer=layer,
            overall_height_mil=overall_height_mils,
            standoff_height_mil=standoff_height_mils,
            cavity_height_mil=cavity_height_mils,
            body_projection=body_projection,
            model=model,
            model_2d_x_mil=model_2d_x_mils,
            model_2d_y_mil=model_2d_y_mils,
            model_2d_rotation_degrees=model_2d_rotation_degrees,
            model_3d_rotx_degrees=model_3d_rotx_degrees,
            model_3d_roty_degrees=model_3d_roty_degrees,
            model_3d_rotz_degrees=model_3d_rotz_degrees,
            model_3d_dz_mil=model_3d_dz_mils,
            model_checksum=model_checksum,
            identifier=identifier,
            name=name,
            body_color_3d=body_color_3d,
            body_opacity_3d=body_opacity_3d,
            model_type=model_type,
            model_source=model_source,
        )

    def add_component_body_rectangle(
        self,
        *,
        left_mils: float,
        bottom_mils: float,
        right_mils: float,
        top_mils: float,
        **kwargs: object,
    ) -> AltiumPcbComponentBody:
        """
        Add a rectangular component body using mil-unit bounds.

        Args:
            left_mils: Left X coordinate of the projection rectangle in mils.
            bottom_mils: Bottom Y coordinate of the projection rectangle in mils.
            right_mils: Right X coordinate of the projection rectangle in mils.
            top_mils: Top Y coordinate of the projection rectangle in mils.
            **kwargs: Additional arguments accepted by `add_component_body(...)`,
                such as `overall_height_mils`, `standoff_height_mils`, `model`,
                `body_projection`, and model transform overrides.

        Returns:
            The authored `AltiumPcbComponentBody` record.
        """
        translated_kwargs = dict(kwargs)
        for public_name, builder_name in (
            ("overall_height_mils", "overall_height_mil"),
            ("standoff_height_mils", "standoff_height_mil"),
            ("cavity_height_mils", "cavity_height_mil"),
            ("model_3d_dz_mils", "model_3d_dz_mil"),
        ):
            if public_name in translated_kwargs:
                translated_kwargs[builder_name] = translated_kwargs.pop(public_name)
        if "model_2d_mils" in translated_kwargs:
            model_2d_x_mils, model_2d_y_mils = _coerce_point_mils(
                translated_kwargs.pop("model_2d_mils"),
                "model_2d_mils",
            )
            translated_kwargs["model_2d_x_mil"] = model_2d_x_mils
            translated_kwargs["model_2d_y_mil"] = model_2d_y_mils

        return self._require_authoring_builder().add_component_body_rectangle(
            self,
            left_mil=left_mils,
            bottom_mil=bottom_mils,
            right_mil=right_mils,
            top_mil=top_mils,
            **translated_kwargs,
        )

    def add_extruded_3d_body(
        self,
        *,
        outline_points_mils: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.MECHANICAL_1,
        overall_height_mils: float,
        standoff_height_mils: float = 0.0,
        side: PcbBodyProjection = PcbBodyProjection.TOP,
        name: str = "Extruded 3D Body",
        identifier: str | None = None,
        body_color_3d: int = 0x808080,
        opacity: float = 1.0,
    ) -> AltiumPcbComponentBody:
        """
        Add a generic extruded 3D body to this footprint.

        Args:
            outline_points_mils: Footprint-local polygon vertices for the 2D
                projection in mils.
            layer: Mechanical `PcbLayer` or native layer id that owns the 3D
                body projection.
            overall_height_mils: Top Z of the extruded body in mils.
            standoff_height_mils: Bottom Z of the extruded body in mils.
            side: `PcbBodyProjection` board side/projection for the body.
            name: Body name shown in Altium.
            identifier: Optional native identifier override.
            body_color_3d: Native Win32 color integer for the extruded body.
            opacity: 3D body opacity from 0.0 to 1.0.

        Returns:
            The authored `AltiumPcbComponentBody` record.
        """
        if not 0.0 <= float(opacity) <= 1.0:
            raise ValueError("opacity must be between 0.0 and 1.0")
        body = self.add_component_body(
            outline_points_mils=outline_points_mils,
            layer=layer,
            overall_height_mils=overall_height_mils,
            standoff_height_mils=standoff_height_mils,
            body_projection=side,
            identifier=identifier,
            name=name,
            body_color_3d=body_color_3d,
            body_opacity_3d=opacity,
            model_type=0,
        )
        body.model_extruded_min_z = int(round(float(standoff_height_mils) * 10000.0))
        body.model_extruded_max_z = int(round(float(overall_height_mils) * 10000.0))
        return body

    def _to_transient_pcbdoc_for_svg(self) -> "AltiumPcbDoc":
        """
        Adapt this footprint into the board-shaped object expected by the PCB SVG renderer.
        """
        from .altium_pcbdoc import AltiumPcbDoc

        pcbdoc = AltiumPcbDoc()
        pcbdoc.pads = self.pads
        pcbdoc.vias = self.vias
        pcbdoc.tracks = self.tracks
        pcbdoc.arcs = self.arcs
        pcbdoc.texts = self.texts
        pcbdoc.fills = self.fills
        pcbdoc.regions = self.regions
        pcbdoc.component_bodies = self.component_bodies
        return pcbdoc

    def to_svg(
        self,
        options: "PcbSvgRenderOptions | None" = None,
        project_parameters: dict[str, str] | None = None,
    ) -> str:
        """
        Render this PcbLib footprint to a single composed SVG.

        The footprint is rendered in its native footprint-local mil coordinate
        system through the same PCB SVG renderer used for `AltiumPcbDoc`.
        Footprints do not have a board outline, so the SVG viewBox is computed
        from the footprint primitives.

        Args:
            options: PCB SVG renderer options.
            project_parameters: Optional project-level parameters used for PCB
                text token substitution.

        Returns:
            SVG document text.
        """
        from .altium_pcb_svg_renderer import PcbSvgRenderer

        renderer = PcbSvgRenderer(options=options)
        return renderer.render_board(
            self._to_transient_pcbdoc_for_svg(),
            project_parameters=project_parameters,
        )

    def to_layer_svgs(
        self,
        options: "PcbSvgRenderOptions | None" = None,
        project_parameters: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Render this PcbLib footprint to one SVG per visible footprint layer.

        Args:
            options: PCB SVG renderer options.
            project_parameters: Optional project-level parameters used for PCB
                text token substitution.

        Returns:
            Dict mapping layer name to SVG document text.
        """
        from .altium_pcb_svg_renderer import PcbSvgRenderer

        renderer = PcbSvgRenderer(options=options)
        return renderer.render_layers(
            self._to_transient_pcbdoc_for_svg(),
            project_parameters=project_parameters,
        )

    def add_embedded_3d_model(
        self,
        model: AltiumPcbModel,
        *,
        overall_height_mils: float | None = None,
        bounds_mils: PcbBoundsMils | None = None,
        projection_outline_mils: Sequence[PcbPointMils] | None = None,
        layer: int | PcbLayer = PcbLayer.MECHANICAL_1,
        side: PcbBodyProjection = PcbBodyProjection.TOP,
        location_mils: PcbPointMils = (0.0, 0.0),
        rotation_x_degrees: float | None = None,
        rotation_y_degrees: float | None = None,
        rotation_z_degrees: float | None = None,
        standoff_height_mils: float | None = None,
        identifier: str | None = None,
        name: str = " ",
        opacity: float = 1.0,
    ) -> AltiumPcbComponentBody:
        """
        Place an embedded PCB 3D model using Altium 3D Body dialog concepts.

        Altium stores STEP placement on a component-body record, so this method
        authors that record while keeping the public API focused on the same
        controls exposed by the 3D Body properties dialog. If neither
        `bounds_mils` nor `projection_outline_mils` is supplied, the rectangular
        projection is inferred from the embedded STEP payload using OCCT:

        - `bounds_mils`: `(left, bottom, right, top)` rectangular projection.
        - `projection_outline_mils`: footprint-local polygon vertices for a
          non-rectangular projection. The outline does not need to be square.

        `overall_height_mils` maps to Altium's stored Overall Height. If it is
        omitted, it is inferred from the STEP `zmax` bound, and the body
        standoff is inferred from STEP `zmin`. Explicit `projection_outline_mils`
        can still be paired with inferred height.

        When projection geometry is inferred, `rotation_x_degrees`,
        `rotation_y_degrees`, and `rotation_z_degrees` are applied around the
        STEP origin in Altium order (X, then Y, then Z), `location_mils` is
        added to the inferred XY bounds, and `standoff_height_mils` is added to
        the inferred Z bounds. Leave these transform arguments as `None` to use
        defaults stored on the model metadata.

        Explicit `bounds_mils` and `projection_outline_mils` are already
        footprint-local projection geometry. They are written as supplied and
        are not auto-rotated or shifted by `location_mils`.

        Args:
            model: `AltiumPcbModel` returned by `AltiumPcbLib.add_embedded_model(...)`.
            overall_height_mils: Optional Overall Height in mils. When omitted,
                the height is inferred from STEP bounds.
            bounds_mils: Optional rectangular projection as `(left_mils,
                bottom_mils, right_mils, top_mils)`.
            projection_outline_mils: Optional non-rectangular projection polygon
                vertices in mils.
            layer: `PcbLayer` or native layer id that owns the projection.
            side: `PcbBodyProjection` side/projection mode.
            location_mils: Model XY placement point in mils.
            rotation_x_degrees: Optional X-axis rotation override in degrees.
            rotation_y_degrees: Optional Y-axis rotation override in degrees.
            rotation_z_degrees: Optional Z-axis rotation override in degrees.
            standoff_height_mils: Optional Z offset/standoff override in mils.
            identifier: Optional native body identifier.
            name: Body name shown by Altium.
            opacity: Body opacity from 0.0 to 1.0.

        Returns:
            The authored `AltiumPcbComponentBody` record.

        Raises:
            ValueError: If both `bounds_mils` and `projection_outline_mils` are
                supplied, if projection inference is requested without embedded
                STEP bytes, or if `opacity` is outside 0.0 through 1.0.
        """
        if not 0.0 <= float(opacity) <= 1.0:
            raise ValueError("opacity must be between 0.0 and 1.0")

        if bounds_mils is not None and projection_outline_mils is not None:
            raise ValueError(
                "Pass exactly one of bounds_mils or projection_outline_mils"
            )

        location_x_mils, location_y_mils = _coerce_point_mils(
            location_mils, "location_mils"
        )
        resolved_rotation_x_degrees = float(
            model.rotation_x if rotation_x_degrees is None else rotation_x_degrees
        )
        resolved_rotation_y_degrees = float(
            model.rotation_y if rotation_y_degrees is None else rotation_y_degrees
        )
        resolved_rotation_z_degrees = float(
            model.rotation_z if rotation_z_degrees is None else rotation_z_degrees
        )
        resolved_model_z_offset_mils = (
            float(model.z_offset) / 10000.0
            if standoff_height_mils is None
            else float(standoff_height_mils)
        )

        inferred_body_standoff_mils = 0.0
        needs_inferred_bounds = bounds_mils is None and projection_outline_mils is None
        needs_inferred_height = overall_height_mils is None
        if needs_inferred_bounds or needs_inferred_height:
            model_payload = getattr(model, "embedded_data", None)
            if not model_payload:
                raise ValueError(
                    "Cannot infer STEP model bounds/height because the model "
                    "does not carry uncompressed embedded STEP bytes. Pass "
                    "bounds_mils/projection_outline_mils and overall_height_mils "
                    "explicitly, or use a model returned by add_embedded_model(...)."
                )
            inferred = compute_step_model_bounds_mils(
                bytes(model_payload),
                filename_hint=str(getattr(model, "name", "") or "model.step"),
                rotation_x_degrees=resolved_rotation_x_degrees,
                rotation_y_degrees=resolved_rotation_y_degrees,
                rotation_z_degrees=resolved_rotation_z_degrees,
                location_mils=(location_x_mils, location_y_mils),
                z_offset_mils=resolved_model_z_offset_mils,
            )
            if needs_inferred_bounds:
                bounds_mils = inferred.bounds_mils
            if needs_inferred_height:
                overall_height_mils = inferred.overall_height_mils
            inferred_body_standoff_mils = inferred.min_z_mils

        assert overall_height_mils is not None

        if bounds_mils is not None:
            left_mils, bottom_mils, right_mils, top_mils = _coerce_bounds_mils(
                bounds_mils, "bounds_mils"
            )
            outline_points_mils = [
                (left_mils, bottom_mils),
                (right_mils, bottom_mils),
                (right_mils, top_mils),
                (left_mils, top_mils),
            ]
        else:
            assert projection_outline_mils is not None
            outline_points_mils = [
                _coerce_point_mils(point, "projection_outline_mils vertex")
                for point in projection_outline_mils
            ]
            if len(outline_points_mils) < 3:
                raise ValueError("projection_outline_mils requires at least 3 points")

        return self.add_component_body(
            outline_points_mils=outline_points_mils,
            layer=layer,
            overall_height_mils=overall_height_mils,
            standoff_height_mils=inferred_body_standoff_mils,
            body_projection=side,
            model=model,
            model_2d_mils=(location_x_mils, location_y_mils),
            model_2d_rotation_degrees=0.0,
            model_3d_rotx_degrees=resolved_rotation_x_degrees,
            model_3d_roty_degrees=resolved_rotation_y_degrees,
            model_3d_rotz_degrees=resolved_rotation_z_degrees,
            model_3d_dz_mils=resolved_model_z_offset_mils,
            identifier=identifier,
            name=name,
            body_opacity_3d=opacity,
        )

    def parse_binary_data(self, data: bytes, debug: bool = False) -> None:
        """
        Parse footprint from binary Data stream.

        Args:
            data: Binary data from [FootprintName]/Data stream
            debug: Enable debug output
        """
        # Store raw data for round-trip
        self.raw_data = data

        offset = 0

        # Skip footprint name header: [uint32 length] [Pascal string]
        # Every PcbLib Data stream starts with this header before primitives
        if len(data) >= 4:
            header_len = struct.unpack("<I", data[0:4])[0]
            if header_len > 0 and 4 + header_len <= len(data):
                offset = 4 + header_len
                if debug:
                    log.debug(f"Skipped {offset}-byte footprint name header")

        while offset < len(data):
            if offset >= len(data):
                break

            type_byte = data[offset]

            try:
                if type_byte == PcbRecordType.PAD:
                    pad = AltiumPcbPad()
                    bytes_consumed = pad.parse_from_binary(data, offset)
                    self.pads.append(pad)
                    self._record_order.append(pad)
                    offset += bytes_consumed

                elif type_byte == PcbRecordType.TRACK:
                    track = AltiumPcbTrack()
                    bytes_consumed = track.parse_from_binary(data, offset)
                    self.tracks.append(track)
                    self._record_order.append(track)
                    offset += bytes_consumed

                elif type_byte == PcbRecordType.ARC:
                    arc = AltiumPcbArc()
                    bytes_consumed = arc.parse_from_binary(data, offset)
                    self.arcs.append(arc)
                    self._record_order.append(arc)
                    offset += bytes_consumed

                elif type_byte == PcbRecordType.VIA:
                    via = AltiumPcbVia()
                    bytes_consumed = via.parse_from_binary(data, offset)
                    self.vias.append(via)
                    self._record_order.append(via)
                    offset += bytes_consumed

                elif type_byte == PcbRecordType.TEXT:
                    text = AltiumPcbText()
                    bytes_consumed = text.parse_from_binary(data, offset)
                    self.texts.append(text)
                    self._record_order.append(text)
                    offset += bytes_consumed

                elif type_byte == PcbRecordType.FILL:
                    fill = AltiumPcbFill()
                    bytes_consumed = fill.parse_from_binary(data, offset)
                    self.fills.append(fill)
                    self._record_order.append(fill)
                    offset += bytes_consumed

                elif type_byte == PcbRecordType.REGION:
                    region = AltiumPcbRegion()
                    bytes_consumed = region.parse_from_binary(data, offset)
                    self.regions.append(region)
                    self._record_order.append(region)
                    offset += bytes_consumed

                elif type_byte == PcbRecordType.COMPONENT_BODY:
                    body = AltiumPcbComponentBody()
                    bytes_consumed = body.parse_from_binary(data, offset)
                    self.component_bodies.append(body)
                    self._record_order.append(body)
                    offset += bytes_consumed

                else:
                    # Unknown type - skip 1 byte
                    if debug:
                        log.warning(
                            f"Unknown record type at offset {offset}: 0x{type_byte:02X}"
                        )
                    offset += 1

            except Exception as e:
                log.warning(
                    f"Error parsing footprint {self.name} at offset {offset}: {e}"
                )
                # Try to continue
                offset += 1

    def serialize_data_stream(self) -> bytes:
        """
        Assemble the footprint Data stream from OOP primitives.

        The stream contains the footprint header plus serialized primitives in
        parse/authoring order. `_record_order` must be populated, which happens
        during parsing and public authoring operations.

        Returns:
            Native binary bytes for the footprint `Data` stream.
        """
        # Build footprint name header: [uint32 pascal_len] [byte name_len] [name_bytes]
        name_bytes = self.name.encode("ascii")
        pascal_str = bytes([len(name_bytes)]) + name_bytes
        header = struct.pack("<I", len(pascal_str)) + pascal_str

        result = bytearray(header)
        for prim in self._record_order:
            result.extend(prim.serialize_to_binary())

        return bytes(result)

    @classmethod
    def from_data_stream(
        cls, name: str, data: bytes, debug: bool = False
    ) -> "AltiumPcbFootprint":
        """
        Parse footprint from binary Data stream.

        Args:
            name: Footprint name.
            data: Binary data from the `[FootprintName]/Data` stream.
            debug: Enable parser debug logging.

        Returns:
            Parsed `AltiumPcbFootprint` instance.
        """
        footprint = cls(name)
        footprint.parse_binary_data(data, debug)
        resolve_pcblib_custom_pad_shapes(footprint)
        return footprint

    def get_summary(self) -> str:
        """
        Return a human-readable footprint primitive count summary.

        Returns:
            Multiline summary string containing the footprint name and primitive
            family counts.
        """
        return (
            f"Footprint: {self.name}\n"
            f"  Pads: {len(self.pads)}\n"
            f"  Tracks: {len(self.tracks)}\n"
            f"  Arcs: {len(self.arcs)}\n"
            f"  Fills: {len(self.fills)}\n"
            f"  Texts: {len(self.texts)}\n"
            f"  Vias: {len(self.vias)}\n"
            f"  Regions: {len(self.regions)}\n"
            f"  Bodies: {len(self.component_bodies)}"
        )

    def __repr__(self) -> str:
        return (
            f"AltiumPcbFootprint('{self.name}', {len(self.pads)} pads, "
            f"{len(self.tracks)} tracks, {len(self.arcs)} arcs, "
            f"{len(self.fills)} fills, {len(self.texts)} texts, "
            f"{len(self.vias)} vias, {len(self.regions)} regions, "
            f"{len(self.component_bodies)} bodies)"
        )


# ============================================================================
# WideStrings Resolution for PcbLib
# ============================================================================


def _parse_pcblib_widestrings(data: bytes) -> dict[int, str]:
    """
    Parse PcbLib WideStrings stream into a string lookup table.

    PcbLib format differs from PcbDoc WideStrings6/Data:
    - uint32 length prefix
    - Pipe-delimited property string: |ENCODEDTEXT{N}=b1,b2,...|
    - Each byte value is a decimal ASCII character code

    Args:
        data: Raw bytes from [FootprintName]/WideStrings stream

    Returns:
        Dict mapping index -> decoded text string
    """
    if not data or len(data) < 4:
        return {}

    length = struct.unpack("<I", data[0:4])[0]
    if length == 0 or 4 + length > len(data):
        return {}

    props_str = data[4 : 4 + length].decode("ascii", errors="replace").rstrip("\x00")

    strings = {}
    for pair in props_str.split("|"):
        if "=" not in pair or not pair.startswith("ENCODEDTEXT"):
            continue
        key, val = pair.split("=", 1)
        # Extract index from key: "ENCODEDTEXT0" -> 0
        try:
            index = int(key[len("ENCODEDTEXT") :])
        except ValueError:
            continue
        # Decode CSV byte values to text
        try:
            byte_values = [int(b) for b in val.split(",") if b.strip()]
            strings[index] = "".join(chr(b) for b in byte_values)
        except (ValueError, OverflowError):
            continue

    return strings


def _resolve_pcblib_widestrings(footprint: "AltiumPcbFootprint") -> None:
    """
    Parse WideStrings and resolve text content on all text records.

    Args:
        footprint: Parsed footprint with raw_widestrings and texts
    """
    if not footprint.raw_widestrings or not footprint.texts:
        return

    string_table = _parse_pcblib_widestrings(footprint.raw_widestrings)
    if not string_table:
        return

    for text in footprint.texts:
        text.resolve_text_content(string_table)


def _parse_length_prefixed_properties(data: bytes) -> dict[str, str]:
    """
    Parse a length-prefixed pipe-delimited property blob.

    Used by PcbLib footprint `Parameters` streams and similar text streams:
    [uint32 body_len][|KEY=VALUE|KEY2=VALUE2|...]
    """
    if not data or len(data) < 4:
        return {}

    length = struct.unpack("<I", data[:4])[0]
    if length <= 0 or 4 + length > len(data):
        return {}

    body = data[4 : 4 + length].decode("latin-1", errors="replace").rstrip("\x00")
    result: dict[str, str] = {}
    for pair in body.split("|"):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        result[key] = value
    return result


def _parse_model_metadata_records(data: bytes | None) -> list[AltiumPcbModel]:
    return parse_model_records_from_bytes(data)


def _referenced_model_entries(
    lib: "AltiumPcbLib",
    footprint: AltiumPcbFootprint,
) -> list[tuple[AltiumPcbModel, bytes]]:
    model_entries = collect_pcblib_embedded_model_entries(
        lib.raw_models_data, lib.raw_models
    )
    return [
        (model, payload)
        for _body, model, payload in resolve_footprint_body_model_entries(
            footprint, model_entries
        )
    ]


def _sanitize_ole_name(name: str) -> str:
    """
    Replace OLE-illegal characters (``\\``, ``/``, ``:``, ``!``, ``*``) with
    underscore.

        OLE/CFB directory entries cannot contain path separator characters, so
        PcbLib model stream names replace these characters with '_'.
    """
    result = name
    for ch in ("\\", "/", ":", "!", "*"):
        result = result.replace(ch, "_")
    return result


def _unique_output_stem(candidate: str, existing_names: set[str]) -> str:
    """
    Return a unique output stem on a case-insensitive filesystem basis.
    """
    candidate_lower = candidate.lower()
    if candidate_lower not in existing_names:
        existing_names.add(candidate_lower)
        return candidate

    suffix = 2
    while True:
        alt = f"{candidate}_{suffix}"
        alt_lower = alt.lower()
        if alt_lower not in existing_names:
            existing_names.add(alt_lower)
            return alt
        suffix += 1


def _unique_combined_footprint_name(
    name: str,
    existing_names: set[str],
    counters: dict[str, int],
) -> str:
    """
    Return a stable output footprint name, suffixing on collision.
    """
    if name not in existing_names:
        counters.setdefault(name, 1)
        return name

    counter = max(2, counters.get(name, 1) + 1)
    candidate = f"{name}_{counter}"
    while candidate in existing_names:
        counter += 1
        candidate = f"{name}_{counter}"
    counters[name] = counter
    return candidate


def _altium_ole_truncate(
    name: str, max_key_length: int = 31, existing_keys: set[str] | None = None
) -> str:
    """
    Replicate Altium's OLE name truncation algorithm.

    Matches the legacy OLE key truncation behavior used by Altium:
    - Truncate to max_key_length chars
    - Avoid space at position 30 (index 30)
    - Append incrementing counter on collision

    Args:
        name: Full footprint name
        max_key_length: Maximum OLE directory name length (31)
        existing_keys: Set of OLE names already in use (for collision detection)

    Returns:
        Truncated OLE name (<= max_key_length chars)
    """
    if not name or len(name) < max_key_length:
        return name

    if existing_keys is None:
        existing_keys = set()

    base = name[:max_key_length]
    counter = 1
    candidate = base
    while candidate in existing_keys or (len(candidate) >= 30 and candidate[30] == " "):
        suffix = str(counter)
        if len(base) + len(suffix) > max_key_length:
            base = name[: max_key_length - len(suffix)]
        candidate = base + suffix
        counter += 1
    return candidate


# ============================================================================
# Library Container
# ============================================================================


@public_api
class AltiumPcbLib:
    """
    Complete PcbLib file containing multiple footprints.

    Author new footprint libraries with `add_footprint(...)`, attach parsed or
    synthesized footprints with `add_existing_footprint(...)`, and write with
    `save(...)`. Public PCB geometry arguments use mils by default.

    Attributes:
        filepath: Path to PcbLib file.
        footprints: List of `AltiumPcbFootprint` instances in the library.
        models_3d: Dict of embedded STEP model payloads found during parsing.
    """

    def __init__(
        self, filepath: Path | str | None = None, *, debug: bool = False
    ) -> None:
        """
        Create an AltiumPcbLib.

        The constructor creates an empty in-memory object and stores `filepath`
        metadata only. Use `AltiumPcbLib.from_file(...)` to parse an existing
        binary library.

        Args:
            filepath: Optional source or destination `.PcbLib` path metadata.
                If omitted, creates an empty library.
            debug: Reserved compatibility flag for older constructor call sites.
        """
        self.filepath: Path | None = Path(filepath) if filepath is not None else None
        self.footprints: list[AltiumPcbFootprint] = []
        self.models_3d: dict[str, bytes] = {}
        self.library_header: dict[str, str] = {}

        # Raw binary streams for round-trip
        self.raw_file_header: bytes | None = None
        self.raw_library_header: bytes | None = None
        self.raw_library_data: bytes | None = None
        self.raw_models_header: bytes | None = None
        self.raw_models_data: bytes | None = None
        self.raw_models: dict[int, bytes] = {}
        self.raw_pad_via_library_header: bytes | None = None
        self.raw_pad_via_library_data: bytes | None = None
        self.raw_layer_kind_mapping_header: bytes | None = None
        self.raw_layer_kind_mapping: bytes | None = None
        self.raw_embedded_fonts: bytes | None = None
        self.raw_textures_header: bytes | None = None
        self.raw_textures_data: bytes | None = None
        self.raw_models_noembed_header: bytes | None = None
        self.raw_models_noembed_data: bytes | None = None
        self.raw_component_params_toc_header: bytes | None = None
        self.raw_component_params_toc_data: bytes | None = None
        self.raw_file_version_info_header: bytes | None = None
        self.raw_file_version_info: bytes | None = None
        self.raw_section_keys: bytes | None = None
        self.combine_provenance: dict[str, object] | None = None
        self._authoring_builder: Any | None = None

    def _profile_for_authoring_builder(self) -> object:
        from .altium_pcblib_builder import PcbLibBuildProfile

        if self.filepath is not None and self.filepath.exists():
            return PcbLibBuildProfile.from_pcblib(self.filepath)
        return PcbLibBuildProfile.default()

    def _ensure_authoring_builder(self) -> Any:
        if self._authoring_builder is not None:
            return self._authoring_builder

        from .altium_pcblib_builder import PcbLibBuilder

        builder = PcbLibBuilder(profile=self._profile_for_authoring_builder())
        if self.footprints:
            model_entries = collect_pcblib_embedded_model_entries(
                self.raw_models_data,
                self.raw_models,
            )
            seen_model_signatures: set[tuple] = set()
            for footprint in self.footprints:
                copy_footprint_with_models_into_builder(
                    builder,
                    footprint,
                    model_entries,
                    seen_model_signatures=seen_model_signatures,
                    height=footprint.parameters.get("HEIGHT", "0mil"),
                    description=footprint.parameters.get("DESCRIPTION", ""),
                    item_guid=footprint.parameters.get("ITEMGUID", ""),
                    revision_guid=footprint.parameters.get("REVISIONGUID", ""),
                    copy_footprint=False,
                )
        self._authoring_builder = builder
        return builder

    def _sync_from_authored_library(self, authored: "AltiumPcbLib") -> None:
        self.footprints = authored.footprints
        self.models_3d = authored.models_3d
        self.library_header = authored.library_header
        self.raw_file_header = authored.raw_file_header
        self.raw_library_header = authored.raw_library_header
        self.raw_library_data = authored.raw_library_data
        self.raw_models_header = authored.raw_models_header
        self.raw_models_data = authored.raw_models_data
        self.raw_models = authored.raw_models
        self.raw_pad_via_library_header = authored.raw_pad_via_library_header
        self.raw_pad_via_library_data = authored.raw_pad_via_library_data
        self.raw_layer_kind_mapping_header = authored.raw_layer_kind_mapping_header
        self.raw_layer_kind_mapping = authored.raw_layer_kind_mapping
        self.raw_embedded_fonts = authored.raw_embedded_fonts
        self.raw_textures_header = authored.raw_textures_header
        self.raw_textures_data = authored.raw_textures_data
        self.raw_models_noembed_header = authored.raw_models_noembed_header
        self.raw_models_noembed_data = authored.raw_models_noembed_data
        self.raw_component_params_toc_header = authored.raw_component_params_toc_header
        self.raw_component_params_toc_data = authored.raw_component_params_toc_data
        self.raw_file_version_info_header = authored.raw_file_version_info_header
        self.raw_file_version_info = authored.raw_file_version_info
        self.raw_section_keys = authored.raw_section_keys

    def add_footprint(
        self,
        name: str,
        *,
        height: str = "0mil",
        description: str = "",
        item_guid: str = "",
        revision_guid: str = "",
    ) -> AltiumPcbFootprint:
        """
        Create a footprint owned by this PcbLib.

        Args:
            name: Footprint pattern name.
            height: Altium footprint height string, for example `"0mil"`.
            description: Footprint description stored in library parameters.
            item_guid: Optional Altium item GUID field.
            revision_guid: Optional Altium revision GUID field.

        Returns:
            The new `AltiumPcbFootprint` owned by this library.
        """
        return self._ensure_authoring_builder().add_footprint(
            name,
            height=height,
            description=description,
            item_guid=item_guid,
            revision_guid=revision_guid,
        )

    def add_existing_footprint(
        self,
        footprint: AltiumPcbFootprint,
        *,
        height: str | None = None,
        description: str | None = None,
        item_guid: str | None = None,
        revision_guid: str | None = None,
        copy_footprint: bool = True,
    ) -> AltiumPcbFootprint:
        """
        Attach an existing footprint to this PcbLib and return the owned instance.

        Args:
            footprint: Source footprint to attach.
            height: Optional replacement height parameter.
            description: Optional replacement description parameter.
            item_guid: Optional replacement item GUID.
            revision_guid: Optional replacement revision GUID.
            copy_footprint: Deep-copy the source before attaching it.

        Returns:
            The `AltiumPcbFootprint` instance now owned by this library.
        """
        return self._ensure_authoring_builder().add_existing_footprint(
            footprint,
            height=height,
            description=description,
            item_guid=item_guid,
            revision_guid=revision_guid,
            copy_footprint=copy_footprint,
        )

    def add_embedded_model(
        self,
        *,
        name: str,
        model_data: bytes,
        model_id: uuid.UUID | str | None = None,
        rotation_x_degrees: float = 0.0,
        rotation_y_degrees: float = 0.0,
        rotation_z_degrees: float = 0.0,
        z_offset_mils: float = 0.0,
        checksum: int | None = None,
        model_source: str = "Undefined",
        data_is_compressed: bool = False,
    ) -> AltiumPcbModel:
        """
        Add an embedded 3D model payload to this PcbLib.

        The returned model object can be passed to component-body creation APIs
        so body metadata references the embedded model ID and checksum. The
        returned model retains the uncompressed payload in memory so
        `AltiumPcbFootprint.add_embedded_3d_model(...)` can infer model bounds
        and height without the caller passing projection geometry explicitly.

        Args:
            name: Model filename stored in the library, commonly a `.step` or
                `.stp` filename.
            model_data: Model payload bytes. Pass uncompressed bytes by default.
                If `data_is_compressed=True`, pass the already zlib-compressed
                payload stream bytes.
            model_id: Optional model GUID. When omitted, a new GUID is generated
                for this model. Provide a deterministic GUID only when generated
                output must be stable across repeated runs.
            rotation_x_degrees: Default model X-axis rotation in degrees.
            rotation_y_degrees: Default model Y-axis rotation in degrees.
            rotation_z_degrees: Default model Z-axis rotation in degrees.
            z_offset_mils: Default model Z offset in mils. These rotation and
                Z-offset defaults are used by `add_embedded_3d_model(...)` when
                placement-specific overrides are omitted.
            checksum: Optional native model checksum to preserve from a source
                model record. If omitted, Altium's native byte-weighted model
                checksum is computed from the uncompressed model bytes.
            model_source: Altium model source string, usually `"Undefined"` for
                embedded STEP payloads authored by this API.
            data_is_compressed: Set true when `model_data` is already zlib-compressed.

        Returns:
            The authored embedded model metadata object.
        """
        return self._ensure_authoring_builder().add_embedded_model(
            name=name,
            model_data=model_data,
            model_id=model_id,
            rotation_x_degrees=rotation_x_degrees,
            rotation_y_degrees=rotation_y_degrees,
            rotation_z_degrees=rotation_z_degrees,
            z_offset_mil=z_offset_mils,
            checksum=checksum,
            model_source=model_source,
            data_is_compressed=data_is_compressed,
        )

    @staticmethod
    def _sanitize_embedded_asset_name(name: str, fallback: str) -> str:
        """
        Sanitize embedded asset names for stable filesystem extraction.
        """
        return sanitize_embedded_asset_name(name, fallback)

    def get_embedded_model_entries(self) -> list[tuple[AltiumPcbModel, bytes]]:
        """
        Return embedded model metadata plus compressed payload bytes.

        Returns:
            List of `(model, compressed_payload)` tuples. The payload is the
            zlib-compressed bytes stored in the native `Library/Models/<n>`
            streams.
        """
        return collect_pcblib_embedded_model_entries(
            self.raw_models_data,
            self.raw_models,
        )

    def extract_embedded_models(
        self,
        output_dir: Path | str,
        verbose: bool = False,
    ) -> list[Path]:
        """
        Extract embedded 3D model payloads to `output_dir`.

        Files are written as `<index:03d>__<model filename>` after zlib
        decompression.

        Args:
            output_dir: Directory where extracted model payloads will be written.
            verbose: Enable progress logging and decompression warnings.

        Returns:
            Paths to the model files written.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for index, (model, compressed_payload) in enumerate(
            self.get_embedded_model_entries()
        ):
            filename = self._sanitize_embedded_asset_name(
                str(getattr(model, "name", "") or ""),
                f"model_{index:03d}.bin",
            )
            model_path = output_path / f"{index:03d}__{filename}"
            try:
                payload = zlib.decompress(compressed_payload)
            except Exception:
                payload = b""
            if not payload:
                if verbose:
                    log.warning(
                        "Failed to decompress embedded model payload: %s", filename
                    )
                continue
            model_path.write_bytes(payload)
            written.append(model_path)
            if verbose:
                log.info("Extracted embedded model: %s", model_path.name)
        return written

    @staticmethod
    def _load_optional_stream(
        ole: AltiumOleFile,
        owner: "AltiumPcbLib | AltiumPcbFootprint",
        attribute_name: str,
        stream_path: str | list[str],
    ) -> bytes | None:
        """
        Load an optional OLE stream onto an object attribute.
        """
        if not ole.exists(stream_path):
            return None
        stream_data = ole.openstream(stream_path)
        setattr(owner, attribute_name, stream_data)
        return stream_data

    @classmethod
    def _load_raw_library_streams(
        cls,
        ole: AltiumOleFile,
        pcblib: "AltiumPcbLib",
    ) -> None:
        """
        Load library-level raw streams preserved for round-trip save.
        """
        for attr_name, stream_path in (
            ("raw_file_header", "FileHeader"),
            ("raw_library_header", ["Library", "Header"]),
            ("raw_embedded_fonts", ["Library", "EmbeddedFonts"]),
            ("raw_models_header", ["Library", "Models", "Header"]),
            ("raw_models_data", ["Library", "Models", "Data"]),
            ("raw_models_noembed_header", ["Library", "ModelsNoEmbed", "Header"]),
            ("raw_models_noembed_data", ["Library", "ModelsNoEmbed", "Data"]),
            ("raw_textures_header", ["Library", "Textures", "Header"]),
            ("raw_textures_data", ["Library", "Textures", "Data"]),
            (
                "raw_component_params_toc_header",
                ["Library", "ComponentParamsTOC", "Header"],
            ),
            (
                "raw_component_params_toc_data",
                ["Library", "ComponentParamsTOC", "Data"],
            ),
            ("raw_pad_via_library_header", ["Library", "PadViaLibrary", "Header"]),
            ("raw_pad_via_library_data", ["Library", "PadViaLibrary", "Data"]),
            ("raw_layer_kind_mapping", ["Library", "LayerKindMapping", "Data"]),
            (
                "raw_layer_kind_mapping_header",
                ["Library", "LayerKindMapping", "Header"],
            ),
            ("raw_file_version_info", ["FileVersionInfo", "Data"]),
            ("raw_file_version_info_header", ["FileVersionInfo", "Header"]),
        ):
            cls._load_optional_stream(ole, pcblib, attr_name, stream_path)

        model_num = 0
        while ole.exists(["Library", "Models", str(model_num)]):
            pcblib.raw_models[model_num] = ole.openstream(
                ["Library", "Models", str(model_num)]
            )
            model_num += 1

    @staticmethod
    def _load_section_key_map(
        ole: AltiumOleFile,
        pcblib: "AltiumPcbLib",
        debug: bool,
    ) -> dict[str, str]:
        """
        Parse the SectionKeys stream when present.
        """
        section_key_map: dict[str, str] = {}
        raw_section_keys = AltiumPcbLib._load_optional_stream(
            ole, pcblib, "raw_section_keys", "SectionKeys"
        )
        if raw_section_keys is None:
            return section_key_map

        section_key_map = PcbLibSectionKeys.from_bytes(raw_section_keys).to_mapping()
        if debug and section_key_map:
            log.info(f"  SectionKeys: {len(section_key_map)} truncated name(s)")
            for full, trunc in section_key_map.items():
                log.info(f"    {full!r} -> {trunc!r}")
        return section_key_map

    @staticmethod
    def _parse_library_data_header(pcblib: "AltiumPcbLib", lib_data: bytes) -> int:
        """
        Parse the Library/Data header and return the footprint count offset.
        """
        if len(lib_data) < 4:
            return 0

        header_len = struct.unpack("<I", lib_data[0:4])[0]
        if len(lib_data) < 4 + header_len:
            return 0

        header_text = lib_data[4 : 4 + header_len].decode("utf-8", errors="replace")
        for pair in header_text.split("|"):
            if "=" in pair:
                key, val = pair.split("=", 1)
                pcblib.library_header[key] = val
        return 4 + header_len

    @staticmethod
    def _read_library_footprint_names(lib_data: bytes, offset: int) -> list[str]:
        """
        Read footprint names from the Library/Data stream.
        """
        if len(lib_data) < offset + 4:
            return []

        footprint_count = struct.unpack("<I", lib_data[offset : offset + 4])[0]
        log.info(f"  Found {footprint_count} footprint(s)")
        offset += 4
        footprint_names: list[str] = []

        for _ in range(footprint_count):
            if offset + 4 > len(lib_data):
                break
            subrecord_len = struct.unpack("<I", lib_data[offset : offset + 4])[0]
            offset += 4
            if offset + subrecord_len > len(lib_data):
                break

            subrecord_content = lib_data[offset : offset + subrecord_len]
            if subrecord_content:
                string_len = subrecord_content[0]
                if len(subrecord_content) >= 1 + string_len:
                    footprint_name = subrecord_content[1 : 1 + string_len].decode(
                        "utf-8", errors="replace"
                    )
                    footprint_names.append(footprint_name)
            offset += subrecord_len

        return footprint_names

    @classmethod
    def _load_library_data_and_footprint_names(
        cls,
        ole: AltiumOleFile,
        pcblib: "AltiumPcbLib",
    ) -> list[str]:
        """
        Load Library/Data and return the declared footprint names.
        """
        if not ole.exists("Library/Data"):
            raise ValueError("No Library/Data stream found")

        lib_data = ole.openstream("Library/Data")
        pcblib.raw_library_data = lib_data
        offset = cls._parse_library_data_header(pcblib, lib_data)
        return cls._read_library_footprint_names(lib_data, offset)

    @staticmethod
    def _resolve_footprint_storage_name(
        ole: AltiumOleFile,
        footprint_name: str,
        section_key_map: dict[str, str],
    ) -> tuple[str, list[str], bool]:
        """
        Resolve the OLE storage name for a footprint.
        """
        ole_name = footprint_name
        data_entry = [ole_name, "Data"]
        stream_exists = data_entry in ole.listdir()

        if not stream_exists:
            sanitized = _sanitize_ole_name(footprint_name)
            if sanitized != footprint_name:
                ole_name = sanitized
                data_entry = [ole_name, "Data"]
                stream_exists = data_entry in ole.listdir()

        if not stream_exists and len(footprint_name) > 31:
            ole_name = section_key_map.get(
                footprint_name, _sanitize_ole_name(footprint_name[:31])
            )
            data_entry = [ole_name, "Data"]
            stream_exists = data_entry in ole.listdir()

        return ole_name, data_entry, stream_exists

    @classmethod
    def _load_footprint_side_streams(
        cls,
        ole: AltiumOleFile,
        footprint: "AltiumPcbFootprint",
        ole_name: str,
    ) -> None:
        """
        Load auxiliary streams for a parsed footprint.
        """
        cls._load_optional_stream(ole, footprint, "raw_header", [ole_name, "Header"])

        raw_parameters = cls._load_optional_stream(
            ole, footprint, "raw_parameters", [ole_name, "Parameters"]
        )
        if raw_parameters is not None:
            footprint.parameters.update(
                _parse_length_prefixed_properties(raw_parameters)
            )

        raw_widestrings = cls._load_optional_stream(
            ole, footprint, "raw_widestrings", [ole_name, "WideStrings"]
        )
        if raw_widestrings is not None:
            _resolve_pcblib_widestrings(footprint)

        cls._load_optional_stream(
            ole,
            footprint,
            "raw_primitive_guids_header",
            [ole_name, "PrimitiveGuids", "Header"],
        )
        cls._load_optional_stream(
            ole, footprint, "raw_primitive_guids", [ole_name, "PrimitiveGuids", "Data"]
        )

        raw_extended = cls._load_optional_stream(
            ole,
            footprint,
            "raw_extended_primitive_info",
            [ole_name, "ExtendedPrimitiveInformation", "Data"],
        )
        cls._load_optional_stream(
            ole,
            footprint,
            "raw_extended_primitive_info_header",
            [ole_name, "ExtendedPrimitiveInformation", "Header"],
        )
        if raw_extended is not None:
            footprint.extended_primitive_information = (
                parse_extended_primitive_information_stream(raw_extended)
            )

        cls._load_optional_stream(
            ole,
            footprint,
            "raw_uniqueid_info",
            [ole_name, "UniqueIDPrimitiveInformation", "Data"],
        )
        cls._load_optional_stream(
            ole,
            footprint,
            "raw_uniqueid_info_header",
            [ole_name, "UniqueIDPrimitiveInformation", "Header"],
        )
        footprint._ole_storage_name = ole_name

    @classmethod
    def _parse_footprints(
        cls,
        ole: AltiumOleFile,
        pcblib: "AltiumPcbLib",
        footprint_names: list[str],
        section_key_map: dict[str, str],
        debug: bool,
    ) -> None:
        """
        Parse all declared footprints from the library.
        """
        if debug:
            log.info("")
            log.info("  Available OLE streams:")
            for entry in ole.listdir():
                stream_path = "/".join(entry)
                log.info(f"    {stream_path}")
            log.info("")

        for footprint_name in footprint_names:
            log.info(f"  Parsing footprint: {footprint_name}")
            ole_name, data_entry, stream_exists = cls._resolve_footprint_storage_name(
                ole, footprint_name, section_key_map
            )
            if debug:
                log.info(f"    Looking for: {data_entry}")
                log.info(f"    Stream exists: {stream_exists}")
            if not stream_exists:
                continue

            try:
                fp_data = ole.openstream(data_entry)
                footprint = AltiumPcbFootprint.from_data_stream(
                    footprint_name, fp_data, debug
                )
                cls._load_footprint_side_streams(ole, footprint, ole_name)
                pcblib.footprints.append(footprint)
                log.info(
                    f"    Parsed: {len(footprint.pads)} pads, "
                    f"{len(footprint.tracks)} tracks, "
                    f"{len(footprint.arcs)} arcs, "
                    f"{len(footprint.fills)} fills, "
                    f"{len(footprint.texts)} texts, "
                    f"{len(footprint.vias)} vias, "
                    f"{len(footprint.regions)} regions, "
                    f"{len(footprint.component_bodies)} bodies"
                )
            except Exception as e:
                log.error(f"    Failed to parse footprint: {e}")
                if debug:
                    import traceback

                    traceback.print_exc()

    @staticmethod
    def _load_3d_models(ole: AltiumOleFile, pcblib: "AltiumPcbLib") -> None:
        """
        Load embedded STEP model payloads from the library.
        """
        if ole.exists("Library/Models/Data"):
            ole.openstream("Library/Models/Data")

        model_num = 0
        while ole.exists(f"Library/Models/{model_num}"):
            step_data = ole.openstream(f"Library/Models/{model_num}")
            pcblib.models_3d[f"model_{model_num}"] = step_data
            model_num += 1

        if pcblib.models_3d:
            log.info(f"  Found {len(pcblib.models_3d)} 3D model(s)")

    @classmethod
    def from_file(cls, filepath: Path, debug: bool = False) -> "AltiumPcbLib":
        """
        Parse a complete PcbLib file.

        Args:
            filepath: Path to a `.PcbLib` file.
            debug: Enable parser debug logging.

        Returns:
            Parsed `AltiumPcbLib` instance.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"PcbLib file not found: {filepath}")

        log.info(f"Parsing PcbLib file: {filepath.name}")
        pcblib = cls(filepath)
        with AltiumOleFile(str(filepath)) as ole:
            cls._load_raw_library_streams(ole, pcblib)
            section_key_map = cls._load_section_key_map(ole, pcblib, debug)
            footprint_names = cls._load_library_data_and_footprint_names(ole, pcblib)
            cls._parse_footprints(ole, pcblib, footprint_names, section_key_map, debug)
            cls._load_3d_models(ole, pcblib)

        log.info(f"Parsed successfully: {len(pcblib.footprints)} footprint(s)")
        return pcblib

    @staticmethod
    def get_footprint_names(filepath: Path) -> list[str]:
        """
        Get footprint names without full parse (fast storage scan).

        This is a lightweight method that only scans OLE storage directories
        to get footprint names, avoiding the overhead of parsing all footprint data.
        Useful for indexing large library collections.

        Args:
            filepath: Path to a `.PcbLib` file.

        Returns:
            Footprint names declared by the library storage.
        """
        ole = AltiumOleFile(str(filepath))
        try:
            names = [
                d[0]
                for d in ole.listdir(streams=False, storages=True)
                if d[0] not in ("FileHeader", "FileVersionInfo", "Library")
            ]
            # Dedupe preserving order
            return list(dict.fromkeys(names))
        finally:
            ole.close()

    def _write_pcblib(self, output_path: Path, debug: bool = False) -> None:
        """
        Write PcbLib file.

        Args:
            output_path: Path to output .PcbLib file
            debug: Enable debug output
        """
        writer = AltiumOleWriter()
        self._write_file_level_streams(writer)
        self._write_footprint_streams(writer)
        self._write_file_trailer_streams(writer)
        writer.write(output_path)
        self.filepath = Path(output_path)

        if debug:
            log.info(f"Wrote PcbLib to {output_path}")

    def _write_file_level_streams(self, writer: AltiumOleWriter) -> None:
        self._add_optional_stream(writer, "FileHeader", self.raw_file_header)
        self._add_optional_stream(writer, "Library/Header", self.raw_library_header)
        self._add_optional_stream(writer, "Library/Data", self.raw_library_data)
        self._add_optional_stream(
            writer, "Library/EmbeddedFonts", self.raw_embedded_fonts
        )
        self._add_optional_stream(
            writer, "Library/Models/Header", self.raw_models_header
        )
        self._add_optional_stream(writer, "Library/Models/Data", self.raw_models_data)

        for model_num, model_data in self.raw_models.items():
            writer.add_stream(f"Library/Models/{model_num}", model_data)

        self._add_optional_stream(
            writer,
            "Library/ModelsNoEmbed/Header",
            self.raw_models_noembed_header,
        )
        self._add_optional_stream(
            writer,
            "Library/ModelsNoEmbed/Data",
            self.raw_models_noembed_data,
        )
        self._add_optional_stream(
            writer, "Library/Textures/Header", self.raw_textures_header
        )
        self._add_optional_stream(
            writer, "Library/Textures/Data", self.raw_textures_data
        )
        self._add_optional_stream(
            writer,
            "Library/ComponentParamsTOC/Header",
            self.raw_component_params_toc_header,
        )
        self._add_optional_stream(
            writer,
            "Library/ComponentParamsTOC/Data",
            self.raw_component_params_toc_data,
        )
        self._add_optional_stream(
            writer,
            "Library/PadViaLibrary/Header",
            self.raw_pad_via_library_header,
        )
        self._add_optional_stream(
            writer,
            "Library/PadViaLibrary/Data",
            self.raw_pad_via_library_data,
        )

        if self.raw_layer_kind_mapping is not None:
            writer.add_stream(
                "Library/LayerKindMapping/Header",
                self.raw_layer_kind_mapping_header
                if self.raw_layer_kind_mapping_header is not None
                else b"\x00\x00\x00\x00",
            )
            writer.add_stream(
                "Library/LayerKindMapping/Data", self.raw_layer_kind_mapping
            )

    def _write_footprint_streams(self, writer: AltiumOleWriter) -> None:
        for footprint in self.footprints:
            storage_name = getattr(footprint, "_ole_storage_name", footprint.name)
            primitive_count = len(footprint._record_order)
            self._write_single_footprint_streams(
                writer,
                footprint,
                storage_name=storage_name,
                primitive_count=primitive_count,
            )

    def _write_single_footprint_streams(
        self,
        writer: AltiumOleWriter,
        footprint: AltiumPcbFootprint,
        *,
        storage_name: str,
        primitive_count: int,
    ) -> None:
        if footprint._record_order:
            writer.add_stream(f"{storage_name}/Data", footprint.serialize_data_stream())
        elif footprint.raw_data is not None:
            writer.add_stream(f"{storage_name}/Data", footprint.raw_data)

        if footprint.raw_header is not None:
            writer.add_stream(f"{storage_name}/Header", footprint.raw_header)
        else:
            writer.add_stream(
                f"{storage_name}/Header", struct.pack("<I", primitive_count)
            )

        self._add_optional_stream(
            writer, f"{storage_name}/Parameters", footprint.raw_parameters
        )
        self._add_optional_stream(
            writer, f"{storage_name}/WideStrings", footprint.raw_widestrings
        )

        self._write_counted_footprint_stream(
            writer,
            storage_name=storage_name,
            subdir="PrimitiveGuids",
            data=footprint.raw_primitive_guids,
            header=footprint.raw_primitive_guids_header,
            default_count=primitive_count + 1,
        )
        self._write_counted_footprint_stream(
            writer,
            storage_name=storage_name,
            subdir="ExtendedPrimitiveInformation",
            data=footprint.raw_extended_primitive_info,
            header=footprint.raw_extended_primitive_info_header,
            default_count=(
                _count_length_prefixed_records(footprint.raw_extended_primitive_info)
                if footprint.raw_extended_primitive_info is not None
                else None
            ),
        )
        self._write_counted_footprint_stream(
            writer,
            storage_name=storage_name,
            subdir="UniqueIDPrimitiveInformation",
            data=footprint.raw_uniqueid_info,
            header=footprint.raw_uniqueid_info_header,
            default_count=(
                _count_length_prefixed_records(footprint.raw_uniqueid_info)
                if footprint.raw_uniqueid_info is not None
                else None
            ),
        )

    def _write_counted_footprint_stream(
        self,
        writer: AltiumOleWriter,
        *,
        storage_name: str,
        subdir: str,
        data: bytes | None,
        header: bytes | None,
        default_count: int | None,
    ) -> None:
        if data is None:
            return
        if header is not None:
            header_data = header
        else:
            assert default_count is not None
            header_data = struct.pack("<I", default_count)
        writer.add_stream(f"{storage_name}/{subdir}/Header", header_data)
        writer.add_stream(f"{storage_name}/{subdir}/Data", data)

    def _write_file_trailer_streams(self, writer: AltiumOleWriter) -> None:
        self._add_optional_stream(writer, "SectionKeys", self.raw_section_keys)

        if self.raw_file_version_info is not None:
            writer.add_stream(
                "FileVersionInfo/Header",
                self.raw_file_version_info_header
                if self.raw_file_version_info_header is not None
                else b"\x00\x00\x00\x00",
            )
            writer.add_stream("FileVersionInfo/Data", self.raw_file_version_info)

    @staticmethod
    def _add_optional_stream(
        writer: AltiumOleWriter,
        path: str,
        data: bytes | None,
    ) -> None:
        if data is not None:
            writer.add_stream(path, data)

    def save(self, filepath: Path | str, debug: bool = False) -> None:
        """
        Save to binary PcbLib format.

        This is the canonical public write path for PcbLib files.

        Args:
            filepath: Destination `.PcbLib` path.
            debug: Enable serialization debug logging.
        """
        if self._authoring_builder is not None:
            self._sync_from_authored_library(self._authoring_builder.build())
        self._write_pcblib(output_path=Path(filepath), debug=debug)

    @staticmethod
    def combine_provenance_path(filepath: Path) -> Path:
        """
        Default JSON sidecar path for a joined/combine provenance manifest.

        Args:
            filepath: Output `.PcbLib` path.

        Returns:
            Matching `.provenance.json` path.
        """
        return Path(filepath).with_suffix(".provenance.json")

    def write_combine_provenance(self, filepath: Path | None = None) -> Path:
        """
        Write a JSON sidecar describing how this library was produced by combine().

        Args:
            filepath: Optional destination JSON path. When omitted, the sidecar
                is written next to this library's current filepath.

        Returns:
            Path to the written provenance JSON file.

        Raises:
            ValueError: If this library was not produced by `combine(...)`, or
                if no output path can be inferred.
        """
        if self.combine_provenance is None:
            raise ValueError("No combine provenance is attached to this PcbLib")
        if filepath is None:
            if self.filepath is None:
                raise ValueError(
                    "filepath is required when the PcbLib has not been saved yet"
                )
            filepath = self.combine_provenance_path(self.filepath)
        path = Path(filepath)
        path.write_text(
            json.dumps(self.combine_provenance, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    @classmethod
    def combine(
        cls,
        inputs: Path | str | Iterable[Path | str],
        *,
        verbose: bool = False,
    ) -> "AltiumPcbLib":
        """
        Combine one or more PcbLib files into a single builder-authored library.

        `inputs` may be:
        - a directory containing `*.PcbLib`
        - a single `.PcbLib` file
        - any iterable of file paths

        The combine path reuses the clean `PcbLibBuilder` flow rather than
        concatenating raw OLE streams. Each input footprint is copied into the
        output library, and embedded model payloads are deduplicated only when
        the full semantic model metadata and payload bytes match.

        Name collisions are handled by keeping the first footprint name and
        suffixing later conflicts as `_2`, `_3`, ... in input order. The
        returned library carries a provenance manifest that can be written as a
        sidecar JSON file with `write_combine_provenance()`.

        Args:
            inputs: Directory, single `.PcbLib` path, or iterable of `.PcbLib`
                paths to combine.
            verbose: Enable progress logging.

        Returns:
            Combined `AltiumPcbLib` with provenance metadata attached.

        Raises:
            ValueError: If no input libraries are found.
        """
        from .altium_pcblib_builder import PcbLibBuilder

        if isinstance(inputs, (str, Path)):
            input_path = Path(inputs)
            if input_path.is_dir():
                paths = sorted(input_path.glob("*.PcbLib"))
            else:
                paths = [input_path]
        else:
            paths = [Path(path) for path in inputs]

        paths = [path.resolve() for path in paths]
        if not paths:
            raise ValueError("No PcbLib inputs provided for combine()")

        builder = PcbLibBuilder()
        seen_model_signatures: set[tuple] = set()
        used_output_names: set[str] = set()
        collision_counters: dict[str, int] = {}
        provenance: dict[str, object] = {
            "kind": "pcblib_combine",
            "join_policy": "suffix",
            "created_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "inputs": [str(path) for path in paths],
            "footprints": [],
            "renamed_conflicts": [],
        }

        for path in paths:
            source = cls.from_file(path)
            source_model_entries = collect_pcblib_embedded_model_entries(
                source.raw_models_data,
                source.raw_models,
            )
            if verbose:
                log.info(
                    "Combining %s (%d footprint(s))", path.name, len(source.footprints)
                )

            for footprint in source.footprints:
                footprint_copy = copy.deepcopy(footprint)
                original_name = footprint_copy.name
                output_name = _unique_combined_footprint_name(
                    original_name,
                    used_output_names,
                    collision_counters,
                )
                renamed = output_name != original_name
                if renamed:
                    log.warning(
                        "combine(): renamed conflicting footprint %r from %s to %r",
                        original_name,
                        path.name,
                        output_name,
                    )
                    footprint_copy.name = output_name
                    footprint_copy._ole_storage_name = output_name
                used_output_names.add(output_name)

                copy_footprint_with_models_into_builder(
                    builder,
                    footprint_copy,
                    source_model_entries,
                    seen_model_signatures=seen_model_signatures,
                    height=footprint_copy.parameters.get("HEIGHT", "0mil"),
                    description=footprint_copy.parameters.get("DESCRIPTION", ""),
                    item_guid=footprint_copy.parameters.get("ITEMGUID", ""),
                    revision_guid=footprint_copy.parameters.get("REVISIONGUID", ""),
                    copy_footprint=False,
                )

                entry = {
                    "output_name": output_name,
                    "original_name": original_name,
                    "source_library": path.name,
                    "source_path": str(path),
                }
                if renamed:
                    entry["collision_group"] = original_name
                    provenance["renamed_conflicts"].append(entry.copy())
                provenance["footprints"].append(entry)

        combined = builder.build()
        combined.combine_provenance = provenance
        return combined

    def _get_library_data_header(self) -> bytes:
        """
        Extract the board config header from this library's Library/Data stream.

                The Library/Data stream is: [uint32 header_len][header_bytes][uint32 fp_count][fp names...]
                Returns the header_bytes portion (layer stack, grid, display config).
        """
        if not self.raw_library_data or len(self.raw_library_data) < 4:
            return b""
        header_len = struct.unpack("<I", self.raw_library_data[0:4])[0]
        if header_len == 0 or 4 + header_len > len(self.raw_library_data):
            return b""
        return self.raw_library_data[4 : 4 + header_len]

    def split(self, output_dir: Path, verbose: bool = False) -> dict[str, Path]:
        """
        Split this multi-footprint PcbLib into individual files.

        Each footprint becomes its own `.PcbLib` file in `output_dir`.
        File names are sanitized for filesystem safety.

        Args:
            output_dir: Directory to write individual `.PcbLib` files.
            verbose: Enable progress logging.

        Returns:
            Dict mapping source footprint names to output file paths.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if verbose:
            log.info(f"Splitting PcbLib with {len(self.footprints)} footprints")

        results = {}
        used_output_names: set[str] = set()
        model_entries = collect_pcblib_embedded_model_entries(
            self.raw_models_data, self.raw_models
        )
        for fp in self.footprints:
            safe_name = _sanitize_ole_name(fp.name)
            unique_name = _unique_output_stem(safe_name, used_output_names)
            out_path = output_dir / f"{unique_name}.PcbLib"
            from .altium_pcblib_builder import PcbLibBuilder

            builder = PcbLibBuilder()
            copy_footprint_with_models_into_builder(
                builder,
                fp,
                model_entries,
                height=fp.parameters.get("HEIGHT", "0mil"),
                description=fp.parameters.get("DESCRIPTION", ""),
                item_guid=fp.parameters.get("ITEMGUID", ""),
                revision_guid=fp.parameters.get("REVISIONGUID", ""),
                seen_model_signatures=set(),
            )
            builder.build().save(out_path)
            results[fp.name] = out_path

            if verbose:
                total = len(fp._record_order) if fp._record_order else 0
                log.info(f"  Wrote {out_path.name} ({total} primitives)")

        if verbose:
            log.info(f"Split {len(results)} footprints into {output_dir}")

        return results

    def __repr__(self) -> str:
        return f"<AltiumPcbLib '{self.filepath.name if self.filepath else 'Unknown'}': {len(self.footprints)} footprints>"
