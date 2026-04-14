"""Schematic record model for off-sheet connectors backed by record 17."""

from __future__ import annotations

from typing import Any

from .altium_record_sch__power_port import AltiumSchPowerPort
from .altium_sch_enums import OffSheetConnectorStyle


class AltiumSchCrossSheetConnector(AltiumSchPowerPort):
    """
    Cross-sheet connector record.

    This is a distinct schematic object-model surface, but it persists through
    the same underlying record family as power objects.
    """

    def __init__(self) -> None:
        super().__init__()
        self.is_cross_sheet_connector = True
        self.style: OffSheetConnectorStyle | int = OffSheetConnectorStyle.LEFT

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager=font_manager)
        self.is_cross_sheet_connector = True
        try:
            self.style = OffSheetConnectorStyle(int(self.style))
        except (TypeError, ValueError):
            pass
