"""
Shared helpers for schematic implementation/footprint records.
"""

from __future__ import annotations

_IMPLEMENTATION_FIELDS_TO_REMOVE = {
    "__ORIGINAL_INDEX__",
    "__CHILDREN__",
    "DatabaseDatalinksLocked",
    "DatalinksLocked",
    "UseComponentLibrary",
}


def build_footprint_implementation_payload(
    model_name: str,
    *,
    description: str = "",
    is_current: bool = True,
    library_name: str = "",
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """
    Build the raw-record payload for a PCB footprint implementation chain.
    """
    resolved_library_name = library_name or model_name
    implementation_record: dict[str, object] = {
        "RECORD": "45",
        "ModelName": model_name,
        "ModelType": "PCBLIB",
        "IsCurrent": "T" if is_current else "F",
        "DatafileCount": "1",
        "ModelDatafileEntity0": resolved_library_name,
        "ModelDatafileKind0": "PCBLib",
    }
    if description:
        implementation_record["Description"] = description

    children: list[dict[str, object]] = [
        {"RECORD": "46"},
        {"RECORD": "48"},
    ]
    return implementation_record, children


def clean_implementation_record_fields(
    record: dict[str, object],
) -> dict[str, object]:
    """
    Remove builder/private helper fields from an IMPLEMENTATION-family record.
    """
    return {
        key: value
        for key, value in record.items()
        if key not in _IMPLEMENTATION_FIELDS_TO_REMOVE and not key.startswith("__")
    }


def clean_implementation_child_record_fields(
    record: dict[str, object],
    *,
    owner_index: int,
) -> dict[str, object]:
    """
    Prepare an IMPLEMENTATION child record for serialization.
    """
    cleaned = clean_implementation_record_fields(record)
    cleaned["OwnerIndex"] = str(owner_index)
    return cleaned
