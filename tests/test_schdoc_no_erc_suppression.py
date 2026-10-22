import pytest

from altium_monkey.altium_record_sch__no_erc import (
    _DEFAULT_CONNECTION_PAIRS,
    _DEFAULT_ERROR_KIND_SET,
    AltiumSchNoErc,
)


def _record(**suppression: str) -> dict[str, str]:
    return {
        "RECORD": "22",
        "IndexInSheet": "180",
        "OwnerPartId": "-1",
        "Location.X": "625",
        "Location.Y": "585",
        "Color": "255",
        "Orientation": "1",
        "Symbol": "Thin Cross",
        "IsActive": "T",
        **suppression,
        "UniqueID": "KBEHWUUU",
    }


# The four shapes Altium Designer writes: generic, and specific markers that
# list only connection pairs, only error kinds, or both.
@pytest.mark.parametrize(
    ("record", "suppress_all", "error_kinds", "connection_pairs"),
    [
        (
            _record(SuppressAll="T"),
            True,
            _DEFAULT_ERROR_KIND_SET,
            _DEFAULT_CONNECTION_PAIRS,
        ),
        (
            _record(SuppressAll="F", ConnectionPairsToSuppress="PNO_PNO,PNO_PNR"),
            False,
            "",
            "PNO_PNO,PNO_PNR",
        ),
        (
            _record(
                SuppressAll="F", ErrorKindSetToSuppress="Nets with no driving source"
            ),
            False,
            "Nets with no driving source",
            "",
        ),
        (
            _record(
                SuppressAll="F",
                ErrorKindSetToSuppress="Nets with no driving source",
                ConnectionPairsToSuppress="SEO_SEB",
            ),
            False,
            "Nets with no driving source",
            "SEO_SEB",
        ),
    ],
    ids=["generic", "pairs-only", "error-kinds-only", "both"],
)
def test_no_erc_suppression_reads_and_round_trips(
    record: dict[str, str],
    suppress_all: bool,
    error_kinds: str,
    connection_pairs: str,
) -> None:
    marker = AltiumSchNoErc()
    marker.parse_from_record(dict(record))

    assert marker.suppress_all is suppress_all
    assert marker.error_kind_set_to_suppress == error_kinds
    assert marker.connection_pairs_to_suppress == connection_pairs
    assert marker.serialize_to_record() == record
