"""
Altium PCB Polygon Record (Polygon Pour)

Parse polygon pour definitions from Polygons6/Data stream.

Polygons are stored as:
- Outline vertices (VX0-VXn, VY0-VYn)
- Optional cutout vertices (CX0-CXn, CY0-CYn)
- Arc/curve information (KIND, EA, SA, R for curved segments)
- Pour settings (track width, thermal relief, etc.)

The polygon definition is NOT rendered - it's just the outline and settings.
The actual rendered copper is stored in ShapeBasedRegions6/Data (binary).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
import html
import math

from .altium_record_types import PcbLayer
from .altium_svg_arc_helpers import choose_svg_sweep_flag_for_center

if TYPE_CHECKING:
    from .altium_pcb_svg_renderer import PcbSvgRenderContext

log = logging.getLogger(__name__)


@dataclass
class PcbPolygonVertex:
    """
    Single vertex in polygon outline or cutout.
    """
    x_mils: float
    y_mils: float
    kind: int = 0  # 0=line, 1=arc (to next vertex)
    radius_mils: float = 0.0  # Arc radius if kind=1
    start_angle: float = 0.0  # Arc start angle (degrees)
    end_angle: float = 0.0    # Arc end angle (degrees)
    center_x_mils: float = 0.0
    center_y_mils: float = 0.0
    has_center: bool = False

@dataclass
class AltiumPcbPolygon:
    """
    PCB polygon pour definition.
    
    Represents a copper pour region with outline, cutouts, and pour settings.
    The polygon is NOT rendered - just the definition. Actual copper fill
    is computed by Altium based on these settings and stored in ShapeBasedRegions6.
    
    Attributes:
        # Geometry
        outline: List of vertices defining polygon outline
        cutouts: List of vertex lists defining cutout regions
    
        # Properties
        net: Net index (0 = no net)
        layer: Layer name ("TOP", "BOTTOM", "MID1", etc.)
        polygon_type: "Polygon" or "Cutout"
        name: Polygon name
    
        # Pour settings
        track_width_mils: Copper track width for hatched fills
        hatch_style: "Solid", "Hatched", "None"
        arc_resolution_mils: Arc approximation resolution
    
        # Thermal relief settings
        min_prim_length_mils: Minimum primitive length
        neck_width_threshold_mils: Neck width threshold
        remove_dead: Remove dead copper
        remove_necks: Remove narrow necks
        remove_islands_by_area: Remove small islands
        area_threshold: Area threshold for island removal
    
        # Advanced settings
        pour_index: Pour priority/order
        union_index: Union group index
        keepout: Is this a keepout region
        locked: Is polygon locked
        shelf: Is polygon shelved
    
        # Raw record for passthrough
        _raw_record: Complete raw record dict
    
        # Access geometry
        for v in poly.outline:
            print(f"Vertex: ({v.x_mils}, {v.y_mils})")
    
        # Serialize back
        record = poly.to_record()
    """

    # Geometry
    outline: list[PcbPolygonVertex] = field(default_factory=list)
    cutouts: list[list[PcbPolygonVertex]] = field(default_factory=list)

    # Properties
    net: int = 0
    layer: str = "TOP"
    polygon_type: str = "Polygon"
    name: str = ""

    # Pour settings
    track_width_mils: float = 10.0
    hatch_style: str = "Solid"
    arc_resolution_mils: float = 0.5
    grid_size_mils: float = 20.0

    # Thermal relief
    min_prim_length_mils: float = 3.0
    neck_width_threshold_mils: float = 5.0
    remove_dead: bool = True
    remove_necks: bool = True
    remove_islands_by_area: bool = True
    area_threshold: float = 250000000000.0

    # Advanced
    pour_index: int = 0
    union_index: int = 0
    keepout: bool = False
    locked: bool = False
    shelved: bool = False
    obey_polygon_cutout: bool = True
    user_routed: bool = True
    pour_over: bool = True

    # Raw record
    _raw_record: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> 'AltiumPcbPolygon':
        """
        Parse polygon from Polygons6/Data text record.
        
        Args:
            record: Text record dict from get_records_in_section()
        
        Returns:
            AltiumPcbPolygon instance
        """
        poly = cls(_raw_record=record.copy())

        # Parse basic properties
        poly.net = int(record.get('NET', 0))
        poly.layer = record.get('LAYER', 'TOP')
        poly.polygon_type = record.get('POLYGONTYPE', 'Polygon')

        # Decode name (comma-separated ASCII values)
        name_str = record.get('NAME', '')
        if name_str and ',' in name_str:
            try:
                name_bytes = bytes(int(x) for x in name_str.split(','))
                poly.name = name_bytes.decode('ascii', errors='ignore')
            except (ValueError, UnicodeDecodeError):
                poly.name = name_str
        else:
            poly.name = name_str

        # Parse pour settings
        poly.track_width_mils = cls._parse_dimension(record.get('TRACKWIDTH', '10mil'))
        poly.hatch_style = record.get('HATCHSTYLE', 'Solid')
        poly.arc_resolution_mils = cls._parse_dimension(record.get('ARCRESOLUTION', '0.5mil'))
        poly.grid_size_mils = cls._parse_dimension(record.get('GRIDSIZE', '20mil'))

        # Parse thermal relief settings
        poly.min_prim_length_mils = cls._parse_dimension(record.get('MINPRIMLENGTH', '3mil'))
        poly.neck_width_threshold_mils = cls._parse_dimension(record.get('NECKWIDTHTHRESHOLD', '5mil'))
        poly.remove_dead = record.get('REMOVEDEAD', 'TRUE') == 'TRUE'
        poly.remove_necks = record.get('REMOVENECKS', 'TRUE') == 'TRUE'
        poly.remove_islands_by_area = record.get('REMOVEISLANDSBYAREA', 'TRUE') == 'TRUE'

        area_str = record.get('AREATHRESHOLD', '250000000000.000000')
        try:
            poly.area_threshold = float(area_str)
        except ValueError:
            poly.area_threshold = 250000000000.0

        # Parse advanced settings
        poly.pour_index = int(record.get('POURINDEX', 0))
        poly.union_index = int(record.get('UNIONINDEX', 0))
        poly.keepout = record.get('KEEPOUT', 'FALSE') == 'TRUE'
        poly.locked = record.get('LOCKED', 'FALSE') == 'TRUE'
        poly.shelved = record.get('SHELVED', 'FALSE') == 'TRUE'
        poly.obey_polygon_cutout = record.get('OBEYPOLYGONCUTOUT', 'TRUE') == 'TRUE'
        poly.user_routed = record.get('USERROUTED', 'TRUE') == 'TRUE'
        poly.pour_over = record.get('POUROVER', 'TRUE') == 'TRUE'

        # Parse outline vertices (VX0-VXn, VY0-VYn, KIND0-KINDn, etc.)
        poly.outline = cls._parse_vertices(record, prefix='V')

        # Parse cutout vertices (CX0-CXn, CY0-CYn)
        cutout_verts = cls._parse_vertices(record, prefix='C')
        if cutout_verts:
            poly.cutouts = [cutout_verts]  # Single cutout for now

        return poly

    @staticmethod
    def _parse_dimension(value: str) -> float:
        """
        Parse dimension string to mils (e.g., '10mil' -> 10.0).
        """
        if not value:
            return 0.0

        # Remove 'mil' suffix
        value = value.replace('mil', '').strip()

        try:
            return float(value)
        except ValueError:
            return 0.0

    @classmethod
    def _parse_vertices(cls, record: dict[str, Any], prefix: str) -> list[PcbPolygonVertex]:
        """
        Parse vertices from record.
        
        Args:
            record: Text record dict
            prefix: 'V' for outline, 'C' for cutout
        
        Returns:
            List of PcbPolygonVertex objects
        """
        vertices = []

        # Find how many vertices we have
        i = 0
        while f'{prefix}X{i}' in record:
            x_str = record[f'{prefix}X{i}']
            y_str = record[f'{prefix}Y{i}']

            x_mils = cls._parse_dimension(x_str)
            y_mils = cls._parse_dimension(y_str)

            # Skip zero vertices (unused slots)
            if prefix == 'C' and x_mils == 0.0 and y_mils == 0.0:
                i += 1
                continue

            # Parse arc information if present
            kind = int(record.get(f'KIND{i}', 0))
            radius_mils = cls._parse_dimension(record.get(f'R{i}', '0mil'))

            # Parse angles (format: " 0.00000000000000E+0000")
            sa_str = record.get(f'SA{i}', '0.0')
            ea_str = record.get(f'EA{i}', '0.0')

            try:
                start_angle = float(sa_str.strip())
            except ValueError:
                start_angle = 0.0

            try:
                end_angle = float(ea_str.strip())
            except ValueError:
                end_angle = 0.0

            cx_key = f'CX{i}'
            cy_key = f'CY{i}'
            has_center = cx_key in record and cy_key in record
            center_x_mils = cls._parse_dimension(record.get(cx_key, '0mil'))
            center_y_mils = cls._parse_dimension(record.get(cy_key, '0mil'))

            vertex = PcbPolygonVertex(
                x_mils=x_mils,
                y_mils=y_mils,
                kind=kind,
                radius_mils=radius_mils,
                start_angle=start_angle,
                end_angle=end_angle,
                center_x_mils=center_x_mils,
                center_y_mils=center_y_mils,
                has_center=has_center,
            )
            vertices.append(vertex)

            i += 1

        return vertices

    def to_record(self) -> dict[str, Any]:
        """
        Serialize polygon to Polygons6/Data text record format.
        
        Returns:
            Text record dict
        """
        record = self._raw_record.copy()

        # Update basic properties
        record['NET'] = str(self.net)
        record['LAYER'] = self.layer
        record['POLYGONTYPE'] = self.polygon_type

        # Encode name (to comma-separated ASCII values)
        if self.name:
            name_bytes = self.name.encode('ascii', errors='ignore')
            record['NAME'] = ','.join(str(b) for b in name_bytes)

        # Update pour settings
        record['TRACKWIDTH'] = f'{self.track_width_mils}mil'
        record['HATCHSTYLE'] = self.hatch_style
        record['ARCRESOLUTION'] = f'{self.arc_resolution_mils}mil'
        record['GRIDSIZE'] = f'{self.grid_size_mils}mil'

        # Update thermal relief
        record['MINPRIMLENGTH'] = f'{self.min_prim_length_mils}mil'
        record['NECKWIDTHTHRESHOLD'] = f'{self.neck_width_threshold_mils}mil'
        record['REMOVEDEAD'] = 'TRUE' if self.remove_dead else 'FALSE'
        record['REMOVENECKS'] = 'TRUE' if self.remove_necks else 'FALSE'
        record['REMOVEISLANDSBYAREA'] = 'TRUE' if self.remove_islands_by_area else 'FALSE'
        record['AREATHRESHOLD'] = f'{self.area_threshold:.14E}'

        # Update advanced settings
        record['POURINDEX'] = str(self.pour_index)
        record['UNIONINDEX'] = str(self.union_index)
        record['KEEPOUT'] = 'TRUE' if self.keepout else 'FALSE'
        record['LOCKED'] = 'TRUE' if self.locked else 'FALSE'
        record['SHELVED'] = 'TRUE' if self.shelved else 'FALSE'
        record['OBEYPOLYGONCUTOUT'] = 'TRUE' if self.obey_polygon_cutout else 'FALSE'
        record['USERROUTED'] = 'TRUE' if self.user_routed else 'FALSE'
        record['POUROVER'] = 'TRUE' if self.pour_over else 'FALSE'

        # Serialize outline vertices
        self._serialize_vertices(record, self.outline, prefix='V')

        # Serialize cutout vertices
        if self.cutouts:
            for cutout in self.cutouts:
                self._serialize_vertices(record, cutout, prefix='C')

        return record

    @staticmethod
    def _serialize_vertices(
        record: dict[str, Any],
        vertices: list[PcbPolygonVertex],
        prefix: str,
    ) -> None:
        """
        Serialize vertices to record.
        
        Args:
            record: Text record dict to update
            vertices: List of PcbPolygonVertex objects
            prefix: 'V' for outline, 'C' for cutout
        """
        for i, v in enumerate(vertices):
            record[f'{prefix}X{i}'] = f'{v.x_mils}mil'
            record[f'{prefix}Y{i}'] = f'{v.y_mils}mil'
            record[f'KIND{i}'] = str(v.kind)
            record[f'R{i}'] = f'{v.radius_mils}mil'
            record[f'SA{i}'] = f' {v.start_angle:.14E}'
            record[f'EA{i}'] = f' {v.end_angle:.14E}'
            record[f'CX{i}'] = f'{v.center_x_mils}mil'
            record[f'CY{i}'] = f'{v.center_y_mils}mil'

    @staticmethod
    def _vertices_without_closing_duplicate(vertices: list[PcbPolygonVertex]) -> list[PcbPolygonVertex]:
        """
        Remove an explicit duplicate closing vertex if present.
        """
        if len(vertices) < 2:
            return vertices

        first = vertices[0]
        last = vertices[-1]
        if (
            math.isclose(first.x_mils, last.x_mils, abs_tol=1e-6)
            and math.isclose(first.y_mils, last.y_mils, abs_tol=1e-6)
        ):
            return vertices[:-1]

        return vertices

    @classmethod
    def _arc_segment_commands(
        cls,
        ctx: "PcbSvgRenderContext",
        previous: PcbPolygonVertex,
        current: PcbPolygonVertex,
    ) -> list[str]:
        """
        Build SVG arc command(s) for one polygon segment (previous -> current).
        """
        sx_svg = ctx.x_to_svg(previous.x_mils)
        sy_svg = ctx.y_to_svg(previous.y_mils)
        ex_svg = ctx.x_to_svg(current.x_mils)
        ey_svg = ctx.y_to_svg(current.y_mils)

        radius_mm = current.radius_mils * 0.0254
        if radius_mm <= 0.0:
            return [f"L {ctx.fmt(ex_svg)} {ctx.fmt(ey_svg)}"]

        start_deg = float(current.start_angle)
        end_deg = float(current.end_angle)
        sweep_ccw = (end_deg - start_deg) % 360.0
        raw_delta = end_deg - start_deg
        full_circle = (
            math.isclose(sweep_ccw, 0.0, abs_tol=1e-9)
            and not math.isclose(raw_delta, 0.0, abs_tol=1e-9)
            and math.hypot(ex_svg - sx_svg, ey_svg - sy_svg) <= 1e-6
        )

        if full_circle and current.has_center:
            mid_deg = start_deg + 180.0
            mx_mils = current.center_x_mils + current.radius_mils * math.cos(math.radians(mid_deg))
            my_mils = current.center_y_mils + current.radius_mils * math.sin(math.radians(mid_deg))
            mx_svg = ctx.x_to_svg(mx_mils)
            my_svg = ctx.y_to_svg(my_mils)
            sweep_flag = "1" if raw_delta >= 0.0 else "0"
            radius = ctx.fmt(radius_mm)
            return [
                f"A {radius} {radius} 0 1 {sweep_flag} {ctx.fmt(mx_svg)} {ctx.fmt(my_svg)}",
                f"A {radius} {radius} 0 1 {sweep_flag} {ctx.fmt(ex_svg)} {ctx.fmt(ey_svg)}",
            ]

        large_arc_int = 1 if sweep_ccw > 180.0 else 0
        sweep_flag = "1" if raw_delta >= 0.0 else "0"

        if current.has_center:
            cx_svg = ctx.x_to_svg(current.center_x_mils)
            cy_svg = ctx.y_to_svg(current.center_y_mils)
            sweep_flag = str(
                choose_svg_sweep_flag_for_center(
                    sx_svg,
                    sy_svg,
                    ex_svg,
                    ey_svg,
                    radius_mm,
                    large_arc_int,
                    cx_svg,
                    cy_svg,
                    default_sweep_flag=1 if raw_delta >= 0.0 else 0,
                )
            )

        radius = ctx.fmt(radius_mm)
        return [
            (
                f"A {radius} {radius} 0 {large_arc_int} {sweep_flag} "
                f"{ctx.fmt(ex_svg)} {ctx.fmt(ey_svg)}"
            )
        ]

    @classmethod
    def _path_from_vertices(cls, ctx: "PcbSvgRenderContext", vertices: list[PcbPolygonVertex]) -> str:
        """
        Build a closed SVG path from polygon vertices, preserving arc segments.
        """
        vertices = cls._vertices_without_closing_duplicate(vertices)
        if len(vertices) < 3:
            return ""

        parts = []
        first = vertices[0]
        parts.append(f"M {ctx.fmt(ctx.x_to_svg(first.x_mils))} {ctx.fmt(ctx.y_to_svg(first.y_mils))}")

        previous = first
        for current in vertices[1:]:
            if int(current.kind) == 1 and current.radius_mils > 0.0:
                parts.extend(cls._arc_segment_commands(ctx, previous, current))
            else:
                parts.append(f"L {ctx.fmt(ctx.x_to_svg(current.x_mils))} {ctx.fmt(ctx.y_to_svg(current.y_mils))}")
            previous = current

        # Closing edge is implied by Z unless the first vertex itself carries
        # an arc segment descriptor.
        if int(first.kind) == 1 and first.radius_mils > 0.0:
            parts.extend(cls._arc_segment_commands(ctx, previous, first))

        parts.append("Z")
        return " ".join(parts)

    def _resolve_layer(self) -> PcbLayer | None:
        """
        Resolve polygon layer name string to a PcbLayer enum.
        """
        key = self.layer.replace(" ", "").upper()
        try:
            return PcbLayer.from_json_name(key)
        except ValueError:
            if key.startswith("INTERNALPLANE"):
                key = key.replace("INTERNALPLANE", "PLANE")
                try:
                    return PcbLayer.from_json_name(key)
                except ValueError:
                    return None
        return None

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | None = None,
    ) -> list[str]:
        """
        Render polygon pour definition outline (debug view).
        
        Note:
            This renders definition outlines only. Filled copper should use
            REGION/ShapeBasedRegion primitives.
        """
        if ctx is None or len(self.outline) < 3:
            return []

        layer_enum = self._resolve_layer()
        if for_layer is not None and layer_enum is not None and for_layer.value != layer_enum.value:
            return []
        if for_layer is not None and layer_enum is None:
            return []

        color = stroke
        if color is None:
            if layer_enum is not None:
                color = ctx.layer_color(layer_enum)
            elif for_layer is not None:
                color = ctx.layer_color(for_layer)
            else:
                color = "#808080"

        main_path = self._path_from_vertices(ctx, self.outline)
        if not main_path:
            return []

        attrs = [
            f'd="{main_path}"',
            'fill="none"',
            f'stroke="{html.escape(color)}"',
            'stroke-width="0.05"',
            'stroke-dasharray="0.4 0.25"',
        ]
        if include_metadata:
            attrs.append('data-primitive="polygon-outline"')
            attrs.append(f'data-polygon-net="{self.net}"')
            attrs.append(f'data-polygon-layer="{html.escape(self.layer)}"')
            if self.name:
                attrs.append(f'data-polygon-name="{html.escape(self.name)}"')

        elements = [f"<path {' '.join(attrs)}/>"]

        for cutout in self.cutouts:
            cutout_path = self._path_from_vertices(ctx, cutout)
            if cutout_path:
                elements.append(
                    f'<path d="{cutout_path}" fill="none" stroke="{html.escape(color)}" '
                    'stroke-width="0.05" stroke-dasharray="0.4 0.25" '
                    'data-primitive="polygon-cutout"/>'
                )

        return elements

    def __repr__(self) -> str:
        """
        String representation.
        """
        return (f"AltiumPcbPolygon(name='{self.name}', net={self.net}, "
                f"layer='{self.layer}', vertices={len(self.outline)}, "
                f"cutouts={len(self.cutouts)})")

