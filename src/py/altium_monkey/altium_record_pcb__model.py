"""
Parse PCB 3D model records from `Models/Data`.
"""

import logging
import struct
from typing import Any

from .altium_record_types import PcbRecordType, Primitive

log = logging.getLogger(__name__)


class AltiumPcbModel(Primitive):
    """
    PCB 3D model record.

    Represents a 3D model reference for PCB components.

    Attributes:
        name: Model filename (e.g., "resistor.step")
        id: Unique model identifier
        is_embedded: True if model data is embedded in OLE stream
        rotation_x: X-axis rotation (degrees)
        rotation_y: Y-axis rotation (degrees)
        rotation_z: Z-axis rotation (degrees)
        z_offset: Z-offset from board surface
        checksum: Altium native model checksum. Stored signed in `Models/Data`
            when the high bit is set.
        embedded_data: Raw embedded STEP file data (if embedded)

    Note:
        MODEL records use property-based format, not fixed binary structure.
        For round-trip, we store raw binary.
    """

    def __init__(self) -> None:
        super().__init__()
        self._raw_binary: bytes | None = None
        self._raw_binary_signature: tuple | None = None
        self.name: str = ""
        self.id: str = ""
        self.is_embedded: bool = False
        self.model_source: str = "Undefined"
        self.rotation_x: float = 0.0
        self.rotation_y: float = 0.0
        self.rotation_z: float = 0.0
        self.z_offset: float = 0.0
        self.checksum: int = 0
        self.embedded_data: bytes | None = None

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB model record discriminator.
        """
        return PcbRecordType.MODEL

    def serialize_to_record(self) -> dict[str, Any]:
        """
        MODEL primitives are stored in binary/property stream form.

        Primitive requires this interface, but PCB MODEL data should be
        serialized via serialize_to_binary().
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} is a binary PCB primitive - use serialize_to_binary() instead"
        )

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse MODEL record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short

        Note:
            MODEL records are length-prefixed property strings:
                [4-byte uint32 length][ASCII key=value|...|]
        """
        if len(data) < offset + 4:
            raise ValueError("Data too short for MODEL record")

        cursor = offset
        record_len = struct.unpack("<I", data[cursor : cursor + 4])[0]
        cursor += 4
        if len(data) < cursor + record_len:
            raise ValueError(
                f"MODEL record truncated: expected {record_len} bytes, got {len(data) - cursor}"
            )

        payload = data[cursor : cursor + record_len]
        cursor += record_len

        text = payload.rstrip(b"\x00").decode("ascii", errors="replace")
        props: dict[str, str] = {}
        for part in text.split("|"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            props[key.strip().upper()] = value.strip()

        self.is_embedded = props.get("EMBED", "FALSE").upper() == "TRUE"
        self.model_source = props.get("MODELSOURCE", "Undefined")
        self.id = props.get("ID", "")
        self.name = props.get("NAME", "")
        try:
            self.rotation_x = float(props.get("ROTX", "0"))
        except Exception:
            self.rotation_x = 0.0
        try:
            self.rotation_y = float(props.get("ROTY", "0"))
        except Exception:
            self.rotation_y = 0.0
        try:
            self.rotation_z = float(props.get("ROTZ", "0"))
        except Exception:
            self.rotation_z = 0.0
        try:
            self.z_offset = float(props.get("DZ", "0"))
        except Exception:
            self.z_offset = 0.0
        try:
            self.checksum = int(props.get("CHECKSUM", "0"))
        except Exception:
            self.checksum = 0

        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()
        return cursor - offset

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of semantically known MODEL fields.
        """
        return (
            bool(self.is_embedded),
            str(self.model_source),
            str(self.id),
            str(self.name),
            float(self.rotation_x),
            float(self.rotation_y),
            float(self.rotation_z),
            float(self.z_offset),
            int(self.checksum),
        )

    def serialize_to_binary(self) -> bytes:
        """
        Serialize MODEL record to binary format.

        Returns:
            Binary data ready to write to stream

        Strategy:
            Reuse raw binary only when semantic fields are unchanged.
            Otherwise emit length-prefixed property text.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        def _fmt_rotation(value: float) -> str:
            return f"{float(value):.3f}"

        def _fmt_distance(value: float) -> str:
            numeric = float(value)
            if numeric.is_integer():
                return str(int(numeric))
            return f"{numeric:.3f}"

        checksum = int(self.checksum) & 0xFFFFFFFF
        if checksum >= 0x80000000:
            checksum -= 0x100000000

        # Preserve AD-style property keys and ordering.
        payload_text = (
            f"EMBED={'TRUE' if self.is_embedded else 'FALSE'}|"
            f"MODELSOURCE={self.model_source or 'Undefined'}|"
            f"ID={self.id}|"
            f"ROTX={_fmt_rotation(self.rotation_x)}|"
            f"ROTY={_fmt_rotation(self.rotation_y)}|"
            f"ROTZ={_fmt_rotation(self.rotation_z)}|"
            f"DZ={_fmt_distance(self.z_offset)}|"
            f"CHECKSUM={checksum}|"
            f"NAME={self.name}"
        )
        payload_bytes = payload_text.encode("ascii", errors="replace") + b"\x00"
        record = struct.pack("<I", len(payload_bytes)) + payload_bytes

        self._raw_binary = record
        self._raw_binary_signature = state_sig
        return record

    def __repr__(self) -> str:
        return f"<AltiumPcbModel name='{self.name}' embedded={self.is_embedded}>"
