"""Schematic record model for SchRecordType.IMAGE."""

import re
from typing import Any
from xml.etree import ElementTree

from .altium_sch_enums import Rotation90
from .altium_record_types import (
    ColorValue,
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchRectMils,
    SchRecordType,
    color_to_hex,
    rgb_to_win32_color,
)
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    CornerMilsMixin,
    detect_case_mode_method_from_dotted_uppercase_fields,
)
from .altium_sch_svg_renderer import SchSvgRenderContext


class AltiumSchImage(CornerMilsMixin, SchGraphicalObject):
    """
    IMAGE record.

    Represents an embedded or linked image.
    """

    def __init__(self) -> None:
        super().__init__()
        self.corner = CoordPoint()
        self.embed_image: bool = False
        self.filename: str = ""
        self.keep_aspect: bool = True
        self.orientation: Rotation90 = Rotation90.DEG_0

        # Border properties
        self.is_solid: bool = False  # Draw border when True
        self.line_width: LineWidth = LineWidth.SMALLEST  # Border thickness

        # Image data (loaded separately from Storage stream)
        self.image_data: bytes | None = None
        self.image_format: str | None = None  # 'png', 'bmp', 'jpg', etc.

        # Track which fields were present
        self._has_corner_x: bool = False
        self._has_corner_y: bool = False
        self._has_embed_image: bool = False
        self._has_keep_aspect: bool = False
        self._has_is_solid: bool = False
        self._has_line_width: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IMAGE

    @property
    def embedded(self) -> bool:
        """
        Alias for embed_image.
        """
        return self.embed_image

    @property
    def width(self) -> int:
        """
        Width of image in internal units.
        """
        return abs(self.corner.x - self.location.x)

    @property
    def height(self) -> int:
        """
        Height of image in internal units.
        """
        return abs(self.corner.y - self.location.y)

    @property
    def bounds_mils(self) -> SchRectMils:
        """
        Public image bounds helper expressed in mils.
        """
        return SchRectMils.from_points(self.location_mils, self.corner_mils)

    @bounds_mils.setter
    def bounds_mils(self, value: SchRectMils) -> None:
        if not isinstance(value, SchRectMils):
            raise TypeError("bounds_mils must be a SchRectMils value")
        location, corner = value.to_coord_points()
        self.location = location
        self.corner = corner

    @property
    def border_color(self) -> ColorValue | None:
        """
        Public border color helper.
        """
        if self.color is None:
            return None
        return ColorValue.from_win32(self.color)

    @border_color.setter
    def border_color(self, value: ColorValue | None) -> None:
        if value is None:
            self.color = None
            return
        if not isinstance(value, ColorValue):
            raise TypeError("border_color must be a ColorValue or None")
        self.color = value.win32

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading
        s = AltiumSerializer()

        # Parse corner coordinates with presence tracking
        corner_x, corner_x_frac, self._has_corner_x = s.read_coord(
            record, "Corner", "X"
        )
        corner_y, corner_y_frac, self._has_corner_y = s.read_coord(
            record, "Corner", "Y"
        )
        self.corner = CoordPoint(corner_x, corner_y, corner_x_frac, corner_y_frac)

        # Image properties
        self.embed_image, self._has_embed_image = s.read_bool(
            record, Fields.EMBED_IMAGE, default=False
        )
        self.filename, _ = s.read_str(record, Fields.FILENAME, default="")
        self.keep_aspect, self._has_keep_aspect = s.read_bool(
            record, Fields.KEEP_ASPECT, default=False
        )
        orient_val, _ = s.read_int(record, Fields.ORIENTATION, default=0)
        self.orientation = Rotation90(orient_val)

        # Existing SchDoc/SchLib image records can omit IsSolid while still
        # semantically behaving as borderless images in the Altium oracle.
        self.is_solid, self._has_is_solid = s.read_bool(
            record, Fields.IS_SOLID, default=False
        )
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Corner - only write if present or non-zero
        if self._has_corner_x or self.corner.x != 0:
            s.write_coord(record, "Corner", "X", self.corner.x, self.corner.x_frac, raw)
        if self._has_corner_y or self.corner.y != 0:
            s.write_coord(record, "Corner", "Y", self.corner.y, self.corner.y_frac, raw)

        # Remove fields that the base class may have written but Image
        # handles explicitly below.
        s.remove_field(record, Fields.IS_SOLID)
        s.remove_field(record, Fields.LINE_WIDTH)

        # Image core fields: only write if the field was explicitly present
        # in the original record (_has_* flag) or the value is non-default.
        s.write_bool(
            record,
            Fields.EMBED_IMAGE,
            self.embed_image,
            raw,
            force=(self._has_embed_image or self.embed_image),
        )
        if self.filename:
            s.write_str(record, Fields.FILENAME, self.filename, raw)
        s.write_bool(
            record,
            Fields.KEEP_ASPECT,
            self.keep_aspect,
            raw,
            force=(self._has_keep_aspect or self.keep_aspect),
        )
        s.write_int(
            record,
            Fields.ORIENTATION,
            self.orientation.value,
            raw,
            force=(self.orientation != Rotation90.DEG_0),
        )
        # Synthesized image records should emit the native default border fields,
        # while parsed sparse records stay sparse unless those fields were present
        # or intentionally changed.
        s.write_bool(
            record,
            Fields.IS_SOLID,
            self.is_solid,
            raw,
            force=(self._has_is_solid or self.is_solid),
        )
        s.write_int(
            record,
            Fields.LINE_WIDTH,
            self.line_width.value,
            raw,
            force=(self._has_line_width or self.line_width.value != 0),
        )

        # Image records do not own the inherited shape-only fields below.
        s.remove_field(record, Fields.AREA_COLOR)
        s.remove_field(record, Fields.TRANSPARENT)
        s.remove_field(record, Fields.LINE_STYLE)
        s.remove_field(record, Fields.LINE_STYLE_EXT)

        return record

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def detect_format(self) -> str | None:
        """
        Detect image format from image_data header bytes.

        Returns:
            Format string ('PNG', 'BMP', 'JPEG', 'GIF') or None
        """
        if not self.image_data or len(self.image_data) < 8:
            return None

        # Use normalized uppercase format names.
        if self.image_data[:8] == b"\x89PNG\r\n\x1a\n":
            self.image_format = "PNG"
        elif self.image_data[:2] == b"BM":
            self.image_format = "BMP"
        elif self.image_data[:2] == b"\xff\xd8":
            self.image_format = "JPEG"
        elif self.image_data[:6] in (b"GIF87a", b"GIF89a"):
            self.image_format = "GIF"
        else:
            self.image_format = None

        return self.image_format

    def __repr__(self) -> str:
        embedded_str = "embedded" if self.embed_image else "linked"
        return (
            f"<AltiumSchImage '{self.filename}' {embedded_str} "
            f"at=({self.location.x}, {self.location.y})>"
        )

    def _try_load_original_file(self, document_path: str | None = None) -> bytes | None:
        """
        Try to load the original image file from disk.

        Altium tries multiple paths to find the original file:
        1. The exact filename as stored in the record
        2. If starts with backslash, strip it and try again
        3. Try combining with document's ImagePath parameter
        4. Try relative to document location

        When the original file is found, Altium uses it instead of the embedded
        BMP data, preserving transparency for PNG files.

        Args:
            document_path: Optional path to the SchDoc file for relative resolution

        Returns:
            Original file data as bytes, or None if not found
        """
        from pathlib import Path

        if not self.filename:
            return None

        paths_to_try = []

        # 1. Try exact path as stored
        paths_to_try.append(self.filename)

        # 2. Strip leading backslash if present
        if self.filename.startswith("\\"):
            paths_to_try.append(self.filename[1:])

        # 3. Try relative to document location
        if document_path:
            doc_dir = Path(document_path).parent
            # Try just the filename
            paths_to_try.append(str(doc_dir / Path(self.filename).name))
            # Try the path relative to document
            if self.filename.startswith("\\"):
                paths_to_try.append(str(doc_dir / self.filename[1:]))

        # Try each path
        for path in paths_to_try:
            try:
                p = Path(path)
                if p.exists() and p.is_file():
                    return p.read_bytes()
            except (OSError, ValueError):
                continue

        return None

    def _convert_to_png(
        self,
        background_color: str | None = None,
        alpha_tolerance: int = 5,
        document_path: str | None = None,
    ) -> bytes | None:
        """
        Convert image data to PNG format for SVG embedding.

        SVG export converts all supported image formats to PNG before embedding,
        regardless of their original format. This includes JPG, BMP, GIF,
        EMF/WMF, and SVG inputs.

        This means the base64 data in the SVG xlink:href is ALWAYS image/png, never the
        original format. Different PNG encoders produce different (but visually equivalent)
        output, so the base64 data won't match byte-for-byte between native Altium and
        Python PIL encoding.

        When the original file is available, prefer it over embedded bytes so
        transparency and format metadata survive the conversion.

        BACKGROUND-TO-ALPHA CONVERSION:
        If the original file is not available, Altium stores images as BMP
        internally (losing PNG alpha). We use the sheet background color as
        a color key to restore transparency.

        Args:
            background_color: Optional background color to convert to transparency
                             (format: "#RRGGBB"). If provided, pixels matching this
                             color (within tolerance) will become transparent.
            alpha_tolerance: RGB distance tolerance for background matching (0-255).
                            Pixels within this distance are made transparent.
            document_path: Optional path to SchDoc for resolving relative image paths.

        Returns:
            PNG image data as bytes, or None if no image data
        """
        # Try loading original file first (like native Altium does).
        # The IR/direct SVG paths may still request sheet-color transparency
        # for visually correct rendering, so do not early-return opaque PNG
        # bytes when a background color key is active.
        original_data = self._try_load_original_file(document_path)
        if original_data:
            # Check if it's already PNG
            if original_data[:8] == b"\x89PNG\r\n\x1a\n" and not background_color:
                return original_data
            # Otherwise convert to PNG
            import io

            try:
                from PIL import Image

                img = Image.open(io.BytesIO(original_data))
                if img.mode not in ("RGB", "RGBA"):
                    img = (
                        img.convert("RGBA")
                        if "transparency" in img.info
                        else img.convert("RGB")
                    )
                if background_color:
                    img = self._apply_background_to_alpha(
                        img, background_color, alpha_tolerance
                    )
                output = io.BytesIO()
                img.save(output, format="PNG")
                return output.getvalue()
            except Exception:
                pass  # Fall through to embedded data

        # Fall back to embedded data
        if not self.image_data:
            return None

        # Detect format if not set
        if not self.image_format:
            self.detect_format()

        # Already PNG - check if we need to apply background-to-alpha
        if self.image_format == "PNG" and not background_color:
            return self.image_data

        # Convert using PIL
        import io

        try:
            from PIL import Image
        except ImportError:
            # PIL not available - return original data and hope browser can handle it
            return self.image_data

        try:
            img = Image.open(io.BytesIO(self.image_data))

            # Convert to RGB(A) mode for PNG export
            if img.mode not in ("RGB", "RGBA"):
                if img.mode == "P" and "transparency" in img.info:
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGB")

            # Apply background-to-alpha conversion if requested
            if background_color and img.mode in ("RGB", "RGBA"):
                img = self._apply_background_to_alpha(
                    img, background_color, alpha_tolerance
                )

            # Save as PNG
            output = io.BytesIO()
            img.save(output, format="PNG")
            return output.getvalue()
        except Exception:
            # Fallback: return original data
            return self.image_data

    def _apply_background_to_alpha(
        self,
        img: Any,
        background_color: str,
        tolerance: int,
    ) -> Any:
        """
        Convert pixels matching background color to transparent.

        Uses the sheet background color as a color key (like chroma key / green screen)
        to restore transparency for images that lost their alpha channel when
        Altium converted them to BMP.

        Args:
            img: PIL Image in RGB or RGBA mode
            background_color: Color to make transparent ("#RRGGBB" format)
            tolerance: RGB distance tolerance (0-255)

        Returns:
            PIL Image in RGBA mode with transparency applied. Existing alpha is
            preserved for non-background pixels.
        """
        import numpy as np
        from PIL import Image

        # Parse background color
        if background_color.startswith("#"):
            bg_r = int(background_color[1:3], 16)
            bg_g = int(background_color[3:5], 16)
            bg_b = int(background_color[5:7], 16)
        else:
            return img  # Can't parse, return unchanged

        # Convert to numpy array for fast processing
        rgba_or_rgb = np.array(img)
        if rgba_or_rgb.ndim != 3 or rgba_or_rgb.shape[2] not in (3, 4):
            return img
        rgb = rgba_or_rgb[:, :, :3]
        alpha = (
            rgba_or_rgb[:, :, 3].copy()
            if rgba_or_rgb.shape[2] == 4
            else np.full(rgb.shape[:2], 255, dtype=np.uint8)
        )

        # Calculate distance from background color for each pixel
        # Using simple RGB distance (faster than perceptual distance)
        r_diff = np.abs(rgb[:, :, 0].astype(np.int16) - bg_r)
        g_diff = np.abs(rgb[:, :, 1].astype(np.int16) - bg_g)
        b_diff = np.abs(rgb[:, :, 2].astype(np.int16) - bg_b)

        # Pixels within tolerance of background color
        is_background = (
            (r_diff <= tolerance) & (g_diff <= tolerance) & (b_diff <= tolerance)
        )

        # Create RGBA image with alpha channel
        rgba = np.zeros((rgb.shape[0], rgb.shape[1], 4), dtype=np.uint8)
        rgba[:, :, :3] = rgb
        rgba[:, :, 3] = alpha

        # Set matching pixels to transparent
        rgba[is_background, 3] = 0

        return Image.fromarray(rgba, "RGBA")

    def _get_stroke_width(self) -> tuple[str, bool]:
        """
        Get SVG stroke-width and whether to use vector-effect.

        The LineWidth enum maps to specific pixel values for SVG export:

        - SMALLEST (0): 0.5px with vector-effect="non-scaling-stroke"
          The vector-effect ensures the stroke remains 0.5px regardless of
          SVG scaling/zoom, matching Altium's "hairline" concept.
        - SMALL (1): 1px (default for new images)
        - MEDIUM (2): 3px
        - LARGE (3): 5px

        The mapping follows the schematic image border stroke values used by
        Altium's SVG export.

        Returns:
            Tuple of (stroke_width_str, use_vector_effect)
        """
        mapping = {
            LineWidth.SMALLEST: ("0.5", True),
            LineWidth.SMALL: ("1", False),
            LineWidth.MEDIUM: ("3", False),
            LineWidth.LARGE: ("5", False),
        }
        return mapping.get(self.line_width, ("1", False))

    def _format_number(self, value: float) -> str:
        """
        Format a number for SVG output, removing unnecessary decimals.
        """
        if value == int(value):
            return str(int(value))
        # Round to 4 decimal places to match Altium output precision
        rounded = round(value, 4)
        # Remove trailing zeros
        return f"{rounded:.4f}".rstrip("0").rstrip(".")

    def _get_embedded_image_size_px(self) -> tuple[int, int] | None:
        """
        Return embedded image pixel size when it can be determined cheaply.
        """
        if not self.image_data:
            return None
        return self._get_image_size_px_from_data(self.image_data)

    def _get_image_size_px_from_data(self, data: bytes) -> tuple[int, int] | None:
        """
        Return image pixel size from raw bytes for raster or SVG sources.
        """
        if not data:
            return None

        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            width = int.from_bytes(data[16:20], "big", signed=False)
            height = int.from_bytes(data[20:24], "big", signed=False)
            return (width, height)
        if data[:2] == b"BM" and len(data) >= 26:
            width = int.from_bytes(data[18:22], "little", signed=True)
            height = abs(int.from_bytes(data[22:26], "little", signed=True))
            return (width, height)
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            width = int.from_bytes(data[6:8], "little", signed=False)
            height = int.from_bytes(data[8:10], "little", signed=False)
            return (width, height)
        svg_size = self._get_svg_size_px_from_data(data)
        if svg_size is not None:
            return svg_size

        try:
            from io import BytesIO

            from PIL import Image

            with Image.open(BytesIO(data)) as img:
                return tuple(int(v) for v in img.size)
        except Exception:
            return None

    def _get_svg_size_px_from_data(self, data: bytes) -> tuple[int, int] | None:
        """
        Return intrinsic SVG size using width/height or viewBox when present.
        """
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="ignore")
        if "<svg" not in text[:2048]:
            return None

        def _parse_dimension(value: str | None) -> int | None:
            if not value:
                return None
            match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", value)
            if not match:
                return None
            return int(float(match.group(1)))

        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            root = None

        if root is not None:
            width = _parse_dimension(root.attrib.get("width"))
            height = _parse_dimension(root.attrib.get("height"))
            if width is not None and height is not None:
                return (width, height)
            view_box = root.attrib.get("viewBox")
            if view_box:
                parts = re.split(r"[,\s]+", view_box.strip())
                if len(parts) == 4:
                    try:
                        return (int(float(parts[2])), int(float(parts[3])))
                    except ValueError:
                        pass
        return None

    def _get_preferred_source_image_size_px(
        self,
        document_path: str | None = None,
    ) -> tuple[int, int] | None:
        """
        Return GeometryMaker-style source image size, preferring the original file.
        """
        original_data = self._try_load_original_file(document_path)
        if original_data:
            original_size = self._get_image_size_px_from_data(original_data)
            if original_size is not None:
                return original_size
        return self._get_embedded_image_size_px()

    def to_geometry(
        self,
        ctx: SchSvgRenderContext | None = None,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> Any:
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if ctx is None:
            ctx = SchSvgRenderContext()

        x1, y1 = ctx.transform_coord_precise(self.location)
        x2, y2 = ctx.transform_coord_precise(self.corner)
        left_px = min(x1, x2)
        top_px = min(y1, y2)
        right_px = max(x1, x2)
        bottom_px = max(y1, y2)
        if abs(right_px - left_px) <= 1e-9 or abs(bottom_px - top_px) <= 1e-9:
            return None

        dest_x1, dest_y1 = svg_coord_to_geometry(
            left_px,
            top_px,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )
        dest_x2, dest_y2 = svg_coord_to_geometry(
            right_px,
            bottom_px,
            sheet_height_px=float(ctx.sheet_height or 0.0),
            units_per_px=units_per_px,
        )

        image_size = self._get_preferred_source_image_size_px(ctx.document_path)
        if image_size is None:
            source_x2 = max(1, int(round(abs(right_px - left_px) * units_per_px / 5)))
            source_y2 = max(1, int(round(abs(bottom_px - top_px) * units_per_px / 5)))
        else:
            source_x2, source_y2 = image_size

        operations = [
            SchGeometryOp.image(
                dest_x1=dest_x1,
                dest_y1=dest_y1,
                dest_x2=dest_x2,
                dest_y2=dest_y2,
                source_x2=source_x2,
                source_y2=source_y2,
                alpha=1.0,
            )
        ]

        if self.is_solid:
            border_color_raw = int(self.color) if self.color is not None else 0
            border_hex = ctx.apply_compile_mask_color(
                color_to_hex(border_color_raw),
                ctx.component_compile_masked is True,
            )
            border_color_raw = rgb_to_win32_color(
                int(border_hex[1:3], 16),
                int(border_hex[3:5], 16),
                int(border_hex[5:7], 16),
            )
            pen_width = {
                LineWidth.SMALLEST: 0,
                LineWidth.SMALL: units_per_px,
                LineWidth.MEDIUM: units_per_px * 3,
                LineWidth.LARGE: units_per_px * 5,
            }.get(self.line_width, units_per_px)
            operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=dest_x1,
                    y1=dest_y1,
                    x2=dest_x2,
                    y2=dest_y2,
                    pen=make_pen(
                        border_color_raw,
                        width=pen_width,
                    ),
                )
            )

        left_units = min(dest_x1, dest_x2)
        right_units = max(dest_x1, dest_x2)
        top_units = min(dest_y1, dest_y2)
        bottom_units = max(dest_y1, dest_y2)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="image",
            object_id="eImage",
            bounds=SchGeometryBounds(
                left=int(round(left_units)),
                top=int(round(top_units)),
                right=int(round(right_units)),
                bottom=int(round(bottom_units)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
