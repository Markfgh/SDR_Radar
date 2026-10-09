"""Eq.5 echoes, fractional delays, independent PLL phase, RF dispersion and AWGN.

The cable reference has a known *one-way* delay; range labels use c*tau/2.
CAL and ECHO share the simulated RF transfer, a hypothesis tested separately on hardware.
"""

import numpy as np

from . import C
from .types import AcquisitionRecord
from .waveforms import chirp, encode_sc16, fractional_delay


def simulate(
    config, ideal: bool = False, positions_m=None
) -> tuple[list[AcquisitionRecord], dict]:
    config.validate()
    rng = np.random.default_rng(config.seed)
    x = chirp(config)
    phases = (
        rng.uniform(-np.pi, np.pi, len(config.centers_hz))
        if config.random_phase and not ideal
        else np.zeros(len(config.centers_hz))
    )
    records = []
    if positions_m is not None:
        positions_m = np.asarray(positions_m, float)
        if positions_m.shape != (len(config.centers_hz), 3) or not np.all(
            np.isfinite(positions_m)
        ):
            raise ValueError("One known Cartesian position per subpulse required")
    for step, fc in enumerate(config.centers_hz):
        radar = np.zeros(config.capture_samples, complex)
        for target in config.targets:
            distance = (
                target.range_m
                if positions_m is None
                else np.linalg.norm(
                    np.array([0, target.range_m, 0]) - positions_m[step]
                )
            )
            tau = 2 * distance / C
            radar += (
                target.amplitude
                * np.exp(1j * target.phase_rad - 2j * np.pi * fc * tau)
                * fractional_delay(x, tau, config.fs_sps, config.capture_samples)
            )
        tau_ref = config.reference_delay_s
        reference = (
            config.reference_amplitude
            * np.exp(-2j * np.pi * fc * tau_ref)
            * fractional_delay(x, tau_ref, config.fs_sps, config.capture_samples)
        )
        if config.topology == "summed":
            radar += reference
        for mode, signal, drift in (
            ("CAL_PRE", reference, 0),
            ("RADAR", radar, 0.5 * config.drift_rad),
            ("CAL_POST", reference, config.drift_rad),
        ):
            n = 4 * config.capture_samples
            f = np.fft.fftfreq(n, 1 / config.fs_sps)
            if ideal:
                h = np.ones(n, complex)
            else:
                amp = 1 + config.rf_ripple * np.cos(2 * np.pi * (fc + f) / 37e6)
                dispersion = 0.08 * np.sin(2 * np.pi * (fc + f) / 23e6)
                h = amp * np.exp(
                    1j * (phases[step] + drift + dispersion)
                    - 2j * np.pi * (fc + f) * config.rf_delay_s
                )
            y = np.fft.ifft(np.fft.fft(signal, n) * h)[: config.capture_samples]
            if not ideal:
                y += (
                    config.noise_rms
                    / np.sqrt(2)
                    * (rng.normal(size=y.size) + 1j * rng.normal(size=y.size))
                )
            raw = encode_sc16(y)
            records.append(
                AcquisitionRecord(
                    step,
                    mode,
                    fc,
                    raw,
                    timestamp=(3 * step + len(records) % 3) * config.capture_samples,
                    actual_count=y.size,
                    metadata={
                        "fs_sps": config.fs_sps,
                        "timestamp_source": "SIMULATED",
                        "digital_epoch_known": True,
                        "clipped_components_before_quantization": int(
                            np.sum((np.abs(y.real) >= 1) | (np.abs(y.imag) >= 1))
                        ),
                        "config_hash": config.digest,
                        "switch": "RADAR" if mode == "RADAR" else "CAL",
                    },
                )
            )
            if positions_m is not None:
                records[-1].metadata["position_m"] = positions_m[step].tolist()
    return records, {
        "origin": "SIMULATED",
        "seed": config.seed,
        "phase_injected_rad": phases.tolist(),
        "ideal": ideal,
        "rf_delay_s": 0 if ideal else config.rf_delay_s,
        "targets": [vars(t) for t in config.targets],
        "positions_m": None if positions_m is None else positions_m.tolist(),
    }
