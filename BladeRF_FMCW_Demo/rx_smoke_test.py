"""Explicit, RX-only real-hardware smoke test. This never enables TX."""
from __future__ import annotations

import sys

from bladerf_interface import BladeRFInterface


def main() -> int:
    radio = BladeRFInterface()
    try:
        radio.open_device()
        config = radio.configure_rx(
            center_frequency_hz=2_414_000_000,
            sample_rate_sps=40_000_000,
            bandwidth_hz=28_000_000,
            gain_db=20,
        )
        radio.start_streaming()
        block = radio.read_rx_samples(8192)
        print(f"RX configuration: {config}")
        print(
            f"Received {block.samples.size} real samples; "
            f"saturated I/Q components: {block.saturated_components}; "
            f"peak magnitude: {abs(block.samples).max():.6f}"
        )
        return 0
    finally:
        radio.close_device()


if __name__ == "__main__":
    raise SystemExit(main())
