"""Explicit low-power real TX/RX smoke test for a permitted test band.

TX is never enabled unless the operator supplies --confirm-tx. This test sends
the application's 5%-full-scale FMCW reference through TX0 and records real
RX samples; it does not claim a calibrated radar range result.
"""
from __future__ import annotations

import argparse
import threading

import numpy as np

from bladerf_interface import BladeRFInterface, complex_to_sc16_q11
from fmcw import FmcwParameters, generate_baseband_chirp, rx_adc_metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-tx", action="store_true", help="Confirm permission for the real 2400–2428 MHz TX test.")
    args = parser.parse_args()
    if not args.confirm_tx:
        parser.error("Real transmission is disabled. Supply --confirm-tx only after verifying authorization, antennas, and attenuation.")

    params = FmcwParameters(2_400_000_000, 28_000_000, 0.001, 40_000_000)
    reference = generate_baseband_chirp(params, amplitude=0.05)
    waveform = complex_to_sc16_q11(reference)
    radio = BladeRFInterface()
    stop_writer = threading.Event()
    writer_error: list[Exception] = []

    def tx_writer() -> None:
        position = 0
        try:
            while not stop_writer.is_set():
                end = position + 8192
                chunk = waveform[position:end] if end <= waveform.size else np.concatenate((waveform[position:], waveform[:end % waveform.size]))
                radio.write_tx_samples(chunk)
                position = end % waveform.size
        except Exception as error:  # surfaced in the main thread after TX is disabled
            if not stop_writer.is_set():
                writer_error.append(error)

    try:
        info = radio.open_device()
        tx_gain = info.tx_gain_range_db[0]
        rx_gain = info.rx_gain_range_db[0]
        filter_bw = 28_000_000
        rx_config = radio.configure_rx(int(params.lo_frequency_hz), int(params.sample_rate_sps), filter_bw, rx_gain)
        tx_config = radio.configure_tx(int(params.lo_frequency_hz), int(params.sample_rate_sps), filter_bw, tx_gain)
        if rx_config.actual_sample_rate_sps != tx_config.actual_sample_rate_sps:
            raise RuntimeError("RX and TX actual sample rates differ; test aborted before TX enable.")
        radio.start_streaming(enable_tx=True)
        writer = threading.Thread(target=tx_writer, name="bladeRF-TX-smoke", daemon=True)
        writer.start()
        # Exercise the live, thread-safe control path without increasing RF level.
        applied_gain = radio.set_tx_gain(tx_gain)
        blocks = [radio.read_rx_samples(8192).samples for _ in range(100)]
        stop_writer.set()
        radio.disable_tx()  # RF is off before waiting for the TX thread.
        writer.join(timeout=2.0)
        if writer_error:
            raise writer_error[0]
        metrics = rx_adc_metrics(np.concatenate(blocks))
        print(f"TX configuration: {tx_config}")
        print(f"RX configuration: {rx_config}")
        print(f"TX applied hardware gain: {applied_gain} dB (setting, not dBm); digital amplitude: 5% (0.05 FS)")
        print(f"RX ADC: peak {metrics.peak_dbfs:.2f} dBFS, RMS {metrics.rms_dbfs:.2f} dBFS, headroom {metrics.headroom_db:.2f} dB, clipping {metrics.clipping_percent:.5f}%")
        return 0
    finally:
        stop_writer.set()
        radio.close_device()


if __name__ == "__main__":
    raise SystemExit(main())
