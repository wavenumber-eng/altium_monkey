from __future__ import annotations


def compute_altium_model_checksum(model_data: bytes) -> int:
    """
    Compute Altium's native embedded PCB 3D model checksum.

    The return value is the unsigned 32-bit value reported by
    `IPCB_Model.GetChecksum()` and written to component-body model references.
    `Models/Data` stores the same bits as a signed 32-bit integer when needed.
    """
    checksum = 0
    weight_state = 1
    for byte in bytes(model_data):
        weight = 1 if weight_state == 1 else weight_state - 1
        checksum = (checksum + byte * weight) & 0xFFFFFFFF
        weight_state += 1
    return checksum
