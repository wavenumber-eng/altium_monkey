"""PCB-facing enum definitions, saved-layer ids, and builder discriminator ids."""

from enum import Enum, IntEnum

from .altium_record_types import PcbLayer


class PcbV7LayerPartition(IntEnum):
    """V7 PCB layer partition ids used by saved-layer identifiers."""

    NO_LAYER = 0
    TOP_LAYER = 1
    MID_LAYERS = 2
    BOTTOM_LAYER = 3
    INTERNAL_PLANE_LAYERS = 4
    MECHANICAL_LAYERS = 5
    TOP_OVERLAY = 6
    BOTTOM_OVERLAY = 7
    TOP_PASTE = 8
    BOTTOM_PASTE = 9
    TOP_SOLDER = 10
    BOTTOM_SOLDER = 11
    DRILL_GUIDE = 12
    KEEPOUT_LAYER = 13
    DRILL_DRAWING = 14
    MULTI_LAYER = 15
    CONNECT_LAYER = 16
    BACKGROUND_LAYER = 17
    DRC_ERROR_LAYER = 18
    HIGHLIGHT_LAYER = 19
    GRID_COLOR_1 = 20
    GRID_COLOR_2 = 21
    PAD_HOLE_LAYER = 22
    VIA_HOLE_LAYER = 23
    TOP_PAD_MASTER_PLOT = 24
    BOTTOM_PAD_MASTER_PLOT = 25
    DRC_DETAIL_LAYER = 26
    MID_DIELECTRIC_LAYERS = 27
    TOP_COVERLAY_OUTLINE_LAYERS = 28
    BOTTOM_COVERLAY_OUTLINE_LAYERS = 29


class PcbV7SavedLayerId(IntEnum):
    """Named fixed V7 saved-layer ids used by PCB configs and outputs."""

    TOP_OVERLAY = 0x01030000 + PcbV7LayerPartition.TOP_OVERLAY
    BOTTOM_OVERLAY = 0x01030000 + PcbV7LayerPartition.BOTTOM_OVERLAY
    TOP_PASTE = 0x01030000 + PcbV7LayerPartition.TOP_PASTE
    BOTTOM_PASTE = 0x01030000 + PcbV7LayerPartition.BOTTOM_PASTE
    TOP_SOLDER = 0x01030000 + PcbV7LayerPartition.TOP_SOLDER
    BOTTOM_SOLDER = 0x01030000 + PcbV7LayerPartition.BOTTOM_SOLDER
    DRILL_GUIDE = 0x01030000 + PcbV7LayerPartition.DRILL_GUIDE
    KEEPOUT = 0x01030000 + PcbV7LayerPartition.KEEPOUT_LAYER
    DRILL_DRAWING = 0x01030000 + PcbV7LayerPartition.DRILL_DRAWING
    MULTI_LAYER = 0x01030000 + PcbV7LayerPartition.MULTI_LAYER
    CONNECT = 0x01030000 + PcbV7LayerPartition.CONNECT_LAYER
    BACKGROUND = 0x01030000 + PcbV7LayerPartition.BACKGROUND_LAYER
    DRC_ERROR_MARKERS = 0x01030000 + PcbV7LayerPartition.DRC_ERROR_LAYER
    HIGHLIGHT = 0x01030000 + PcbV7LayerPartition.HIGHLIGHT_LAYER
    VISIBLE_GRID_1 = 0x01030000 + PcbV7LayerPartition.GRID_COLOR_1
    VISIBLE_GRID_2 = 0x01030000 + PcbV7LayerPartition.GRID_COLOR_2
    PAD_HOLES = 0x01030000 + PcbV7LayerPartition.PAD_HOLE_LAYER
    VIA_HOLES = 0x01030000 + PcbV7LayerPartition.VIA_HOLE_LAYER
    TOP_PAD_MASTER = 0x01030000 + PcbV7LayerPartition.TOP_PAD_MASTER_PLOT
    BOTTOM_PAD_MASTER = 0x01030000 + PcbV7LayerPartition.BOTTOM_PAD_MASTER_PLOT
    DRC_DETAIL_MARKERS = 0x01030000 + PcbV7LayerPartition.DRC_DETAIL_LAYER


def _legacy_pcb_layer_to_v7_saved_layer_id(layer_id: int | PcbLayer) -> int:
    layer_id = int(layer_id)
    if PcbLayer.TOP.value <= layer_id <= PcbLayer.MID30.value:
        return 0x01000000 + layer_id
    if layer_id == PcbLayer.BOTTOM.value:
        return 0x0100FFFF
    if PcbLayer.INTERNAL_PLANE_1.value <= layer_id <= PcbLayer.INTERNAL_PLANE_16.value:
        return 0x01010000 + (layer_id - PcbLayer.INTERNAL_PLANE_1.value + 1)
    if PcbLayer.MECHANICAL_1.value <= layer_id <= PcbLayer.MECHANICAL_16.value:
        return 0x01020000 + (layer_id - PcbLayer.MECHANICAL_1.value + 1)

    fixed_layer_ids = {
        PcbLayer.TOP_OVERLAY.value: int(PcbV7SavedLayerId.TOP_OVERLAY),
        PcbLayer.BOTTOM_OVERLAY.value: int(PcbV7SavedLayerId.BOTTOM_OVERLAY),
        PcbLayer.TOP_PASTE.value: int(PcbV7SavedLayerId.TOP_PASTE),
        PcbLayer.BOTTOM_PASTE.value: int(PcbV7SavedLayerId.BOTTOM_PASTE),
        PcbLayer.TOP_SOLDER.value: int(PcbV7SavedLayerId.TOP_SOLDER),
        PcbLayer.BOTTOM_SOLDER.value: int(PcbV7SavedLayerId.BOTTOM_SOLDER),
        PcbLayer.DRILL_GUIDE.value: int(PcbV7SavedLayerId.DRILL_GUIDE),
        PcbLayer.KEEPOUT.value: int(PcbV7SavedLayerId.KEEPOUT),
        PcbLayer.DRILL_DRAWING.value: int(PcbV7SavedLayerId.DRILL_DRAWING),
        PcbLayer.MULTI_LAYER.value: int(PcbV7SavedLayerId.MULTI_LAYER),
        PcbLayer.CONNECT.value: int(PcbV7SavedLayerId.CONNECT),
    }
    fixed_layer_id = fixed_layer_ids.get(layer_id)
    if fixed_layer_id is not None:
        return fixed_layer_id

    raise ValueError(f"Unsupported legacy PCB layer for V7 save encoding: {layer_id}")


def pcb_signal_v7_saved_layer_ids() -> tuple[int, ...]:
    """Return the saved V7 ids for the standard signal copper stack."""

    layer_ids = [PcbLayer.TOP]
    layer_ids.extend(
        PcbLayer(layer_id)
        for layer_id in range(PcbLayer.MID1.value, PcbLayer.MID30.value + 1)
    )
    layer_ids.append(PcbLayer.BOTTOM)
    return tuple(
        _legacy_pcb_layer_to_v7_saved_layer_id(layer_id) for layer_id in layer_ids
    )


def pcb_internal_plane_v7_saved_layer_ids() -> tuple[int, ...]:
    """Return the saved V7 ids for internal plane layers 1 through 16."""

    return tuple(
        _legacy_pcb_layer_to_v7_saved_layer_id(PcbLayer(layer_id))
        for layer_id in range(
            PcbLayer.INTERNAL_PLANE_1.value,
            PcbLayer.INTERNAL_PLANE_16.value + 1,
        )
    )


def pcb_mechanical_v7_saved_layer_ids(*, count: int = 32) -> tuple[int, ...]:
    """Return the saved V7 ids for mechanical layers 1 through ``count``."""

    if count <= 0:
        return ()
    first_mechanical = _legacy_pcb_layer_to_v7_saved_layer_id(PcbLayer.MECHANICAL_1)
    return tuple(
        (first_mechanical - 1) + layer_index for layer_index in range(1, count + 1)
    )


def pcb_mechanical_layer_number_to_v7_saved_layer_id(
    mechanical_number: int,
) -> int | None:
    """Return the saved V7 id for a mechanical layer number, or ``None`` if invalid."""

    if not 1 <= int(mechanical_number) <= 32:
        return None
    first_mechanical = _legacy_pcb_layer_to_v7_saved_layer_id(PcbLayer.MECHANICAL_1)
    return first_mechanical + int(mechanical_number) - 1


class PcbGuidType(IntEnum):
    """Primitive GUID type ids used in `PrimitiveGuids/Data` records."""

    COMPONENT = 0x0309
    ARC = 0x0D01
    PAD = 0x0E02
    VIA = 0x0F03
    TRACK = 0x1004
    TEXT = 0x1105
    FILL = 0x1206
    REGION = 0x1359
    SHAPEBASED_REGION = 0x1459
    COMPONENT_BODY = 0x155A
    SHAPEBASED_COMPONENT_BODY = 0x165A


class PadShape(IntEnum):
    """
    PCB pad geometry kind used by `AltiumPcbDoc.add_pad(...)` and
    `AltiumPcbFootprint.add_pad(...)`.
    """

    CIRCLE = 1
    RECTANGLE = 2
    OCTAGONAL = 3
    ROUNDED_RECTANGLE = 4
    CUSTOM = 10


class PcbTextKind(str, Enum):
    """
    PCB text primitive rendering mode.

    `STROKE` and `TRUETYPE` create normal text primitives. `BARCODE` creates
    barcode text and is paired with `PcbBarcodeKind` and
    `PcbBarcodeRenderMode`.
    """

    STROKE = "stroke"
    TRUETYPE = "truetype"
    BARCODE = "barcode"


class PcbBarcodeKind(IntEnum):
    """
    PCB text barcode symbology for `PcbTextKind.BARCODE`.
    """

    CODE_39 = 0
    CODE_128 = 1


class PcbBarcodeRenderMode(IntEnum):
    """
    PCB barcode sizing mode.

    `BY_MIN_WIDTH` uses the minimum bar width, while `BY_FULL_WIDTH` fits the
    generated barcode inside the requested full width.
    """

    BY_MIN_WIDTH = 0
    BY_FULL_WIDTH = 1


class PcbTextJustification(IntEnum):
    """
    PCB text anchor and frame justification values.

    Used by text frames, inverted text, and other PCB text features that need a
    deterministic anchor point inside a rectangle.
    """

    MANUAL = 0
    LEFT_TOP = 1
    LEFT_CENTER = 2
    LEFT_BOTTOM = 3
    CENTER_TOP = 4
    CENTER_CENTER = 5
    CENTER_BOTTOM = 6
    RIGHT_TOP = 7
    RIGHT_CENTER = 8
    RIGHT_BOTTOM = 9


class PcbViaMode(IntEnum):
    """Via stack-mode encoding."""

    SIMPLE = 0
    LOCAL_STACK = 1
    EXTERNAL_STACK = 2
    RESERVED_3 = 3


class PcbNetClassKind(IntEnum):
    """PCB net-class family kind."""

    NET = 0
    COMPONENT = 1
    FROM_TO = 2
    PAD = 3
    LAYER = 4
    DIFF_PAIR = 6
    POLYGON = 7


class PcbRegionKind(IntEnum):
    """
    PCB region semantic kind used when authoring or interpreting region records.
    """

    COPPER = 0
    BOARD_CUTOUT = 1
    POLYGON_CUTOUT = 2
    DASHED_OUTLINE = 3
    UNKNOWN_3 = 4
    CAVITY_DEFINITION = 5
    UNKNOWN = 99


class PcbBodyProjection(IntEnum):
    """
    3D component-body side/projection mode.

    Used by extruded bodies and embedded STEP bodies to indicate whether the
    native component-body projection belongs to the top side, bottom side, both
    sides, or neither side.
    """

    TOP = 0
    BOTTOM = 1
    BOTH = 2
    NONE = 3
