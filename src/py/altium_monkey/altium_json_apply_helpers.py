"""
Shared JSON apply helpers for schematic document and library models.
"""

from pathlib import Path
from typing import Protocol


class _SupportsJsonApply(Protocol):
    @staticmethod
    def _load_json_source(source: Path | str | dict) -> dict: ...

    def _update_from_json(self, data: dict) -> None: ...


class JsonApplyMixin:
    """
    Shared ``apply_json()`` behavior for binary-backed JSON update surfaces.
    """

    def apply_json(self: _SupportsJsonApply, source: Path | str | dict) -> None:
        """
        Mutate this object from a JSON payload.
        """
        data = self._load_json_source(source)
        self._update_from_json(data)
