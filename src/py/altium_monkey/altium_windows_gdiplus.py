"""Windows GDI+ text measurement helper for named-instance fallback widths."""

import ctypes
from ctypes import Structure, byref, c_float, c_int, c_void_p, c_wchar_p
import logging

log = logging.getLogger(__name__)


class RectF(Structure):
    """
    GDI+ RectF structure.
    """
    _fields_ = [('X', c_float), ('Y', c_float), ('Width', c_float), ('Height', c_float)]


class GdiplusStartupInput(Structure):
    """
    GDI+ startup input structure.
    """
    _fields_ = [
        ('GdiplusVersion', ctypes.c_uint32),
        ('DebugEventCallback', c_void_p),
        ('SuppressBackgroundThread', c_int),
        ('SuppressExternalCodecs', c_int)
    ]


class GdiplusTextMeasurer:
    """
    Wrapper for GDI+ text measurement.
    
    Uses Windows GDI+ MeasureString with GenericTypographic format,
    which is what Altium uses for pin names and designators.
    """

    def __init__(
        self,
        font_family: str = "Arial",
        font_size_px: float = 8.0,
        *,
        bold: bool = False,
        italic: bool = False,
    ) -> None:
        """
        Initialize GDI+ text measurer.
        
        Args:
            font_family: Font family name (default "Arial")
            font_size_px: Font size in pixels (default 8.0)
            bold: Use GDI+ bold style bit
            italic: Use GDI+ italic style bit
        """
        self._gdiplus = None
        self._token = None
        self._font = None
        self._font_family = None
        self._graphics = None
        self._string_format = None
        self._hdc = None
        self._font_family_name = font_family
        self._font_size = font_size_px
        self._bold = bool(bold)
        self._italic = bool(italic)
        self._initialized = False

        self._initialize()

    def _initialize(self) -> bool:
        """
        Initialize GDI+ resources.
        """
        try:
            self._gdiplus = ctypes.windll.gdiplus
            gdi32 = ctypes.windll.gdi32

            # GDI+ Startup
            self._token = ctypes.c_ulong()
            startup = GdiplusStartupInput(1, None, 0, 0)
            status = self._gdiplus.GdiplusStartup(byref(self._token), byref(startup), None)
            if status != 0:
                log.warning("GdiplusStartup failed with status %d", status)
                return False

            # Create font family
            self._font_family = c_void_p()
            status = self._gdiplus.GdipCreateFontFamilyFromName(
                c_wchar_p(self._font_family_name), None, byref(self._font_family))
            if status != 0:
                log.warning("GdipCreateFontFamilyFromName failed with status %d", status)
                return False

            # Create font (Unit 2 = UnitPixel, style bits: 1=Bold, 2=Italic)
            self._font = c_void_p()
            style = 0
            if self._bold:
                style |= 1
            if self._italic:
                style |= 2
            status = self._gdiplus.GdipCreateFont(
                self._font_family,
                c_float(self._font_size),
                style,
                2,
                byref(self._font),
            )
            if status != 0:
                log.warning("GdipCreateFont failed with status %d", status)
                return False

            # Create graphics from DC
            self._hdc = gdi32.CreateCompatibleDC(None)
            self._graphics = c_void_p()
            status = self._gdiplus.GdipCreateFromHDC(self._hdc, byref(self._graphics))
            if status != 0:
                log.warning("GdipCreateFromHDC failed with status %d", status)
                return False

            # Get GenericTypographic string format
            self._string_format = c_void_p()
            status = self._gdiplus.GdipStringFormatGetGenericTypographic(byref(self._string_format))
            if status != 0:
                log.warning("GdipStringFormatGetGenericTypographic failed with status %d", status)
                return False

            self._initialized = True
            return True

        except Exception as e:
            log.warning("Failed to initialize GDI+: %s", e)
            return False

    def measure(self, text: str) -> tuple[float, float]:
        """
        Measure text width and height using GDI+.
        
        Args:
            text: Text string to measure
        
        Returns:
            Tuple of (width, height) in pixels
        """
        if not self._initialized or not text:
            return (0.0, 0.0)

        layout_rect = RectF(0, 0, 10000, 100)
        bounding_box = RectF()
        codepoints_fitted = c_int()
        lines_filled = c_int()

        status = self._gdiplus.GdipMeasureString(
            self._graphics,
            c_wchar_p(text),
            len(text),
            self._font,
            byref(layout_rect),
            self._string_format,
            byref(bounding_box),
            byref(codepoints_fitted),
            byref(lines_filled)
        )

        if status != 0:
            log.warning("GdipMeasureString failed with status %d for '%s'", status, text)
            return (0.0, 0.0)

        return (bounding_box.Width, bounding_box.Height)

    def measure_width(self, text: str) -> float:
        """
        Measure just the width of text.
        """
        return self.measure(text)[0]

    def __del__(self) -> None:
        """
        Cleanup GDI+ resources.
        """
        if self._gdiplus is None:
            return

        try:
            if self._font:
                self._gdiplus.GdipDeleteFont(self._font)
            if self._font_family:
                self._gdiplus.GdipDeleteFontFamily(self._font_family)
            if self._graphics:
                self._gdiplus.GdipDeleteGraphics(self._graphics)
            if self._hdc:
                ctypes.windll.gdi32.DeleteDC(self._hdc)
            if self._token:
                self._gdiplus.GdiplusShutdown(self._token)
        except Exception:
            pass  # Ignore cleanup errors


# Global singleton for default Arial 8px measurements
_default_measurer: GdiplusTextMeasurer | None = None


def get_gdiplus_text_width(
    text: str,
    font_size_px: float = 8.0,
    font_name: str = "Arial",
    *,
    bold: bool = False,
    italic: bool = False,
) -> float:
    """
    Get text width using GDI+ MeasureString.
    
    Args:
        text: Text to measure
        font_size_px: Font size in pixels (default 8.0)
        font_name: Font family name (default "Arial")
        bold: Use GDI+ bold style bit
        italic: Use GDI+ italic style bit
    
    Returns:
        Text width in pixels (GDI+ raw units, not scaled for SVG)
    """
    global _default_measurer

    if font_size_px == 8.0 and font_name == "Arial" and not bold and not italic:
        if _default_measurer is None:
            _default_measurer = GdiplusTextMeasurer("Arial", 8.0)
        return _default_measurer.measure_width(text)
    else:
        # For non-default sizes/fonts, create a temporary measurer
        measurer = GdiplusTextMeasurer(
            font_name,
            font_size_px,
            bold=bold,
            italic=italic,
        )
        return measurer.measure_width(text)

