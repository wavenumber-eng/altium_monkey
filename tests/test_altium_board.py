from __future__ import annotations

import pytest

from altium_monkey.altium_board import AltiumBoard


@pytest.mark.parametrize(
    ("text", "mils"),
    [
        ("3.3098mil", 3.3098),
        ("0.127mm", 5.0),
        ("127um", 5.0),
        ("5", 5.0),
    ],
)
def test_make_mils_honours_length_unit(text: str, mils: float) -> None:
    """Board-record lengths carry the unit the author typed; str.strip("mil")
    stripped characters, so "0.127mm" read as 0.127 mil."""
    assert AltiumBoard._make_mils(text) == pytest.approx(mils)
