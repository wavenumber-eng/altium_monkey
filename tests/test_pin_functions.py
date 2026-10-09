from altium_monkey import AltiumSchLib, Rotation90, SchPointMils, make_sch_pin
import pytest
from pathlib import Path
from altium_monkey.altium_sch_auxiliary_codec import encode_auxiliary_stream
from altium_monkey.altium_schlib import _SchLibBudget, SchLibContainerError
from altium_monkey.altium_sch_auxiliary_codec import decode_auxiliary_stream
from altium_monkey.altium_pin_functions import decode_function_payload


def _make_library() -> AltiumSchLib:
    library = AltiumSchLib()
    symbol = library.add_symbol("STM32C031KxT")
    symbol.add_pin(
        make_sch_pin(
            designator="15",
            name="PB0",
            location_mils=SchPointMils.from_mils(0, 0),
            orientation=Rotation90.DEG_0,
            defined_functions=["SPI1_NSS", "I2S1_WS", "TIM3_CH3"],
            selected_functions=["SPI1_NSS"],
        )
    )
    symbol.add_pin(
        make_sch_pin(
            designator="16",
            name="PB1",
            location_mils=SchPointMils.from_mils(0, 100),
            orientation=Rotation90.DEG_0,
            defined_functions=["TIM14_CH1", "TIM3_CH4", "EVENTOUT"],
        )
    )
    return library


def test_pin_functions_round_trip_per_pin(tmp_path):
    path = tmp_path / "pin_functions.SchLib"
    _make_library().save(path)

    symbol = AltiumSchLib(path).get_symbol("STM32C031KxT")
    assert symbol is not None
    assert symbol.pins[0].defined_functions == [
        "SPI1_NSS",
        "I2S1_WS",
        "TIM3_CH3",
    ]
    assert symbol.pins[0].selected_functions == ["SPI1_NSS"]
    assert symbol.pins[1].defined_functions == [
        "TIM14_CH1",
        "TIM3_CH4",
        "EVENTOUT",
    ]
    assert symbol.pins[1].selected_functions == []


def test_pin_functions_can_be_changed_on_parsed_library(tmp_path):
    source = tmp_path / "source.SchLib"
    updated = tmp_path / "updated.SchLib"
    _make_library().save(source)

    library = AltiumSchLib(source)
    symbol = library.get_symbol("STM32C031KxT")
    assert symbol is not None
    symbol.pins[0].defined_functions = ["ADC_IN17"]
    symbol.pins[0].selected_functions = ["ADC_IN17"]
    symbol.pins[1].defined_functions = []
    library.save(updated)

    symbol = AltiumSchLib(updated).get_symbol("STM32C031KxT")
    assert symbol is not None
    assert symbol.pins[0].defined_functions == ["ADC_IN17"]
    assert symbol.pins[0].selected_functions == ["ADC_IN17"]
    assert symbol.pins[1].defined_functions == []


@pytest.mark.parametrize("indices", [(0,), (0, 9, 10), (100,)])
def test_native_framing_and_pin_indices(tmp_path, indices):
    library = AltiumSchLib()
    symbol = library.add_symbol("INDEX_TEST")
    for index in range(max(indices) + 1):
        symbol.add_pin(make_sch_pin(
            designator=str(index + 1), location_mils=SchPointMils.from_mils(0, 0),
            defined_functions=[f"F{index}"] if index in indices else [],
        ))
    path = tmp_path / "indices.SchLib"
    library.save(path)
    reopened = AltiumSchLib(path)
    entries = decode_auxiliary_stream(
        reopened._source_streams["INDEX_TEST/PinFunctionData"],
        expected_header="PinFunctionData",
    )
    assert [entry.name for entry in entries] == [str(i) for i in indices]
    assert [decode_function_payload(entry.data)[0] for entry in entries] == [[f"F{i}"] for i in indices]


def test_clearing_all_functions_removes_stream(tmp_path):
    path = tmp_path / "clear.SchLib"
    library = _make_library()
    library.save(path)
    for pin in library.get_symbol("STM32C031KxT").pins:
        pin.defined_functions.clear()
        pin.selected_functions.clear()
    library.save(path)
    reopened = AltiumSchLib(path)
    assert "STM32C031KxT/PinFunctionData" not in reopened._source_streams
    assert all(not pin.defined_functions for pin in reopened.get_symbol("STM32C031KxT").pins)


@pytest.mark.parametrize("bad", ["A|B", "", "A\x00B", "A\nB", 42])
def test_mutated_function_lists_are_validated_at_save(tmp_path, bad):
    library = _make_library()
    library.get_symbol("STM32C031KxT").pins[0].defined_functions.append(bad)
    with pytest.raises((ValueError, TypeError)):
        library.save(tmp_path / "invalid.SchLib")


def test_native_reference_is_preserved_and_editable(tmp_path):
    library = AltiumSchLib(Path(__file__).parent / "fixtures" / "pin_functions_native.SchLib")
    symbol = library.get_symbol("STM32C031KxT")
    assert symbol.pins[14].defined_functions == ["SPI1_NSS", "I2S1_WS", "TIM3_CH3", "TIM1_CH2N", "ADC_IN17"]
    assert symbol.pins[15].defined_functions == ["TIM14_CH1", "TIM3_CH4", "TIM1_CH3N", "TIM1_CH2N", "EVENTOUT", "ADC_IN18"]
    original = library._source_streams["STM32C031KxT/PinFunctionData"]
    path = tmp_path / "native.SchLib"
    library.save(path)
    assert AltiumSchLib(path)._source_streams["STM32C031KxT/PinFunctionData"] == original
    symbol.pins[14].defined_functions = ["ADC_IN17"]
    library.save(path)
    reopened = AltiumSchLib(path).get_symbol("STM32C031KxT")
    assert reopened.pins[14].defined_functions == ["ADC_IN17"]
    assert reopened.pins[15].defined_functions == symbol.pins[15].defined_functions


def test_malformed_payload_is_reported():
    library = _make_library()
    path = "STM32C031KxT/PinFunctionData"
    library._source_streams[path] = encode_auxiliary_stream("PinFunctionData", [("0", b"bad")])
    library._source_stream_paths_by_fold[path.casefold()] = path
    with pytest.raises(SchLibContainerError, match="text length"):
        library._apply_pin_functions("STM32C031KxT", library.get_symbol("STM32C031KxT"), _SchLibBudget(library._read_limits))


def test_decompression_limits_are_enforced():
    from dataclasses import replace
    library = _make_library()
    path = "STM32C031KxT/PinFunctionData"
    library._source_streams[path] = encode_auxiliary_stream("PinFunctionData", [("0", b"a" * 1000)])
    library._source_stream_paths_by_fold[path.casefold()] = path
    library._read_limits = replace(library._read_limits, max_decompressed_blob_bytes=10)
    with pytest.raises(SchLibContainerError, match="decompressed payload exceeds"):
        library._apply_pin_functions("STM32C031KxT", library.get_symbol("STM32C031KxT"), _SchLibBudget(library._read_limits))
