"""Metadata ABI from installed Nuand 2.6.0 libbladeRF.h, SHA in SOURCES.md.

This header includes SC16_Q11_PACKED before SC16_Q11_META (enum value 2).
ABI and DLL matching are checked before using the optional metadata backend.
"""

import ctypes as ct
from dataclasses import dataclass

SC16_Q11_META = 2
RX_NOW = 1 << 31
TX_BURST_START = 1 << 0
TX_BURST_END = 1 << 1
TX_NOW = 1 << 2
OVERRUN = 1 << 0
UNDERRUN = 1 << 1


class Metadata(ct.Structure):
    _fields_ = [
        ("timestamp", ct.c_uint64),
        ("flags", ct.c_uint32),
        ("status", ct.c_uint32),
        ("actual_count", ct.c_uint),
        ("reserved", ct.c_uint8 * 32),
    ]


def check_abi():
    observed = {name: getattr(Metadata, name).offset for name, _ in Metadata._fields_}
    expected = {
        "timestamp": 0,
        "flags": 8,
        "status": 12,
        "actual_count": 16,
        "reserved": 20,
    }
    if observed != expected or ct.sizeof(Metadata) != 56:
        raise RuntimeError(
            f"Unexpected metadata ABI: {observed}, size={ct.sizeof(Metadata)}"
        )
    return {
        "size": ct.sizeof(Metadata),
        "offsets": observed,
        "format_enum": SC16_Q11_META,
    }


@dataclass
class Continuity:
    next_timestamp: int | None = None

    def observe(self, timestamp, actual_count, requested_count, status):
        gap = (
            None
            if self.next_timestamp is None or timestamp is None
            else timestamp - self.next_timestamp
        )
        valid = (
            timestamp is not None
            and actual_count == requested_count
            and not status
            and gap in (None, 0)
        )
        self.next_timestamp = None if timestamp is None else timestamp + actual_count
        return {
            "valid": valid,
            "gap_samples": gap,
            "status": status,
            "actual_count": actual_count,
            "timestamp": timestamp,
            "phase_verified": False,
        }
