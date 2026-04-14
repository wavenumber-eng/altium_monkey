"""
Altium BoardRegion record model for BoardRegions/Data.

BoardRegions/Data reuses the binary REGION record format, but its property payload
encodes rigid-flex board-region semantics such as substack linkage and bend lines.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .altium_pcb_property_helpers import clean_pcb_property_text as _clean_text
from .altium_record_pcb__region import AltiumPcbRegion


@dataclass
class AltiumPcbBendingLine:
    """
    Typed board-region bend-line payload from BENDINGLINE{N}=... properties.
    """

    angle_deg: float | None = None
    radius_raw: int | None = None
    fold_index: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    raw_value: str = ""

    @classmethod
    def from_property_value(cls, raw_value: object) -> "AltiumPcbBendingLine":
        text = _clean_text(raw_value)
        tokens = [token.strip() for token in text.split(";")]

        def _parse_float(token: str) -> float | None:
            if not token:
                return None
            try:
                return float(token)
            except (TypeError, ValueError):
                return None

        def _parse_int(token: str) -> int | None:
            if not token:
                return None
            try:
                return int(float(token))
            except (TypeError, ValueError):
                return None

        return cls(
            angle_deg=_parse_float(tokens[0]) if len(tokens) >= 1 else None,
            radius_raw=_parse_int(tokens[1]) if len(tokens) >= 2 else None,
            fold_index=_parse_int(tokens[2]) if len(tokens) >= 3 else None,
            x1=_parse_int(tokens[3]) if len(tokens) >= 4 else None,
            y1=_parse_int(tokens[4]) if len(tokens) >= 5 else None,
            x2=_parse_int(tokens[5]) if len(tokens) >= 6 else None,
            y2=_parse_int(tokens[6]) if len(tokens) >= 7 else None,
            raw_value=text,
        )

    def to_property_value(self) -> str:
        if self.raw_value and self._matches_raw_value():
            return self.raw_value
        tokens = [
            self._format_float(self.angle_deg),
            self._format_int(self.radius_raw),
            self._format_int(self.fold_index),
            self._format_int(self.x1),
            self._format_int(self.y1),
            self._format_int(self.x2),
            self._format_int(self.y2),
        ]
        return ";".join(tokens)

    def _matches_raw_value(self) -> bool:
        reparsed = self.from_property_value(self.raw_value)
        return (
            reparsed.angle_deg == self.angle_deg
            and reparsed.radius_raw == self.radius_raw
            and reparsed.fold_index == self.fold_index
            and reparsed.x1 == self.x1
            and reparsed.y1 == self.y1
            and reparsed.x2 == self.x2
            and reparsed.y2 == self.y2
        )

    @staticmethod
    def _format_float(value: float | None) -> str:
        if value is None:
            return ""
        return format(float(value), "g")

    @staticmethod
    def _format_int(value: int | None) -> str:
        if value is None:
            return ""
        return str(int(value))


class AltiumPcbBoardRegion(AltiumPcbRegion):
    """
    Typed BoardRegion wrapper over the binary REGION record format.
    """

    def __init__(self) -> None:
        super().__init__()
        self.object_kind: str = "BoardRegion"
        self.name: str = ""
        self.layerstack_id: str = ""
        self.bending_line_count: int = 0
        self.bending_lines: list[AltiumPcbBendingLine] = []
        self.locked_3d: bool = False
        self.cavity_height: str = ""
        self.v7_layer: str = ""
        self.layer_token: str = ""
        self.arc_resolution: str = ""
        self._typed_signature_at_parse: tuple | None = None

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        consumed = super().parse_from_binary(data, offset)
        self._load_typed_fields_from_properties()
        self._typed_signature_at_parse = self._typed_signature()
        return consumed

    def serialize_to_binary(self) -> bytes:
        if self._typed_signature_at_parse != self._typed_signature():
            self._sync_typed_fields_to_properties()
        return super().serialize_to_binary()

    def _load_typed_fields_from_properties(self) -> None:
        props = self.properties or {}
        self.object_kind = _clean_text(
            props.get("OBJECTKIND", "BoardRegion") or "BoardRegion"
        )
        self.name = _clean_text(props.get("NAME", "") or "")
        self.layerstack_id = _clean_text(props.get("LAYERSTACKID", "") or "")
        self.locked_3d = (
            _clean_text(props.get("LOCKED3D", "FALSE") or "").upper() == "TRUE"
        )
        self.cavity_height = _clean_text(props.get("CAVITYHEIGHT", "") or "")
        self.v7_layer = _clean_text(props.get("V7_LAYER", "") or "")
        self.layer_token = _clean_text(props.get("LAYER", "") or "")
        self.arc_resolution = _clean_text(props.get("ARCRESOLUTION", "") or "")

        indexed_lines: list[tuple[int, AltiumPcbBendingLine]] = []
        for key, raw_value in props.items():
            match = re.match(
                r"^BENDINGLINE(\d+)$", str(key or "").strip(), re.IGNORECASE
            )
            if match is None:
                continue
            indexed_lines.append(
                (
                    int(match.group(1)),
                    AltiumPcbBendingLine.from_property_value(raw_value),
                )
            )
        indexed_lines.sort(key=lambda item: item[0])
        self.bending_lines = [item for _, item in indexed_lines]
        self.bending_line_count = int(
            props.get("BENDINGLINECOUNT", len(self.bending_lines))
            or len(self.bending_lines)
        )

    def _sync_typed_fields_to_properties(self) -> None:
        props = {str(key): str(value) for key, value in (self.properties or {}).items()}
        props["OBJECTKIND"] = self.object_kind or "BoardRegion"
        props["NAME"] = self.name
        props["LAYERSTACKID"] = self.layerstack_id
        props["LOCKED3D"] = "TRUE" if self.locked_3d else "FALSE"
        props["CAVITYHEIGHT"] = self.cavity_height
        props["V7_LAYER"] = self.v7_layer
        props["LAYER"] = self.layer_token
        if self.arc_resolution:
            props["ARCRESOLUTION"] = self.arc_resolution
        else:
            props.pop("ARCRESOLUTION", None)

        for key in list(props):
            if re.match(r"^BENDINGLINE\d+$", key, re.IGNORECASE):
                props.pop(key, None)

        bend_lines = list(self.bending_lines or [])
        props["BENDINGLINECOUNT"] = str(
            self.bending_line_count if self.bending_line_count else len(bend_lines)
        )
        for index, bend_line in enumerate(bend_lines):
            props[f"BENDINGLINE{index}"] = bend_line.to_property_value()

        self.properties = props

    def _typed_signature(self) -> tuple:
        return (
            self.object_kind,
            self.name,
            self.layerstack_id,
            self.bending_line_count,
            tuple(
                (
                    line.angle_deg,
                    line.radius_raw,
                    line.fold_index,
                    line.x1,
                    line.y1,
                    line.x2,
                    line.y2,
                    line.raw_value,
                )
                for line in self.bending_lines
            ),
            self.locked_3d,
            self.cavity_height,
            self.v7_layer,
            self.layer_token,
            self.arc_resolution,
        )
