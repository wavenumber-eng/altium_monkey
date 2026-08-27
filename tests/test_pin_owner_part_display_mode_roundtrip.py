"""Regression test: AltiumSchPin must round-trip OwnerPartDisplayMode.

AltiumSchPin.serialize_to_record() previously dropped OwnerPartDisplayMode
in its text-format (SchDoc) path, even though parse_from_record() reads the
field correctly via the SchPrimitive base class. Downstream tooling built on
this library (e.g. a compiled-netlist compiler) can treat a missing
OwnerPartDisplayMode as "belongs to every display mode" -- an intentionally
permissive fallback for fields historically omitted on the default mode.
That means any multi-display-mode pin whose text record is parsed and then
re-serialized silently loses its mode tag, making every one of that
component's obsolete-mode pins reappear as simultaneously active. This is
easy to trigger even for a component that was never intentionally modified:
saving a SchDoc calls serialize_to_record() on every component on the sheet,
not just ones that actually changed.
"""

from altium_monkey import AltiumSchPin


def _minimal_pin_record(**overrides: str) -> dict[str, str]:
    record = {
        "RECORD": "2",
        "OwnerPartId": "1",
        "OwnerPartDisplayMode": "1",
        "Designator": "1",
        "Name": "A",
        "Location.X": "0",
        "Location.Y": "0",
        "PinConglomerate": "0",
    }
    record.update(overrides)
    return record


def test_pin_owner_part_display_mode_survives_text_round_trip():
    pin = AltiumSchPin()
    pin.parse_from_record(_minimal_pin_record())

    assert pin.owner_part_display_mode == 1

    record = pin.serialize_to_record()

    assert record.get("OwnerPartDisplayMode") == "1"


def test_pin_owner_part_display_mode_none_stays_omitted():
    # A pin with no display-mode restriction (the common single-mode case)
    # must keep omitting the field, matching Altium's own sparse encoding.
    record_in = _minimal_pin_record()
    del record_in["OwnerPartDisplayMode"]

    pin = AltiumSchPin()
    pin.parse_from_record(record_in)

    assert pin.owner_part_display_mode is None

    record_out = pin.serialize_to_record()

    assert "OwnerPartDisplayMode" not in record_out
