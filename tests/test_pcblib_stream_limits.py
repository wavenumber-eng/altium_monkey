"""A real passive library needs more than 4096 OLE streams (gh #78)."""

from __future__ import annotations

import pytest

from altium_monkey.altium_pcblib import AltiumPcbLib, _PcbLibReadLimits


class _FakeOle:
    def get_size(self, _path: list[str]) -> int:
        return 1


def _validate(stream_count: int) -> None:
    limits = _PcbLibReadLimits()
    fake_library = type("_LimitsOnly", (), {"_read_limits": limits})()
    streams = [[f"stream-{index}"] for index in range(stream_count)]
    AltiumPcbLib._validate_source_inventory(fake_library, _FakeOle(), streams, [])


def test_default_allows_a_four_hundred_part_passive_library():
    assert _PcbLibReadLimits().max_streams == 65_536
    _validate(5_000)


def test_the_limit_is_still_a_limit():
    with pytest.raises(ValueError, match="stream count exceeds"):
        _validate(65_537)
