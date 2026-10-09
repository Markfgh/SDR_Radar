"""ADC diagnostics reuse the legacy monitor; phase statistics are circular, in radians."""

from dataclasses import asdict

import numpy as np

from fmcw import rx_adc_metrics


def adc(raw):
    from bladerf_interface import sc16_q11_to_complex

    return asdict(rx_adc_metrics(sc16_q11_to_complex(raw)))


def phase_statistics(values):
    values = np.asarray(values, float)
    if values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("No finite phase observations")
    mean = np.angle(np.mean(np.exp(1j * values)))
    residual = np.angle(np.exp(1j * (values - mean)))
    return {
        "mean_rad": float(mean),
        "rms_about_mean_rad": float(np.sqrt(np.mean(residual**2))),
        "count": int(values.size),
        "residual_rad": residual.tolist(),
    }


def reference_snr(iq, pulse_samples):
    """Signal/noise energy per sample; tail noise includes quantization noise.

    Requires a quiet tail (a simulation assumption, to verify on real scenes).
    """
    noise = float(np.mean(np.abs(iq[-max(32, iq.size // 8) :]) ** 2))
    signal = float(np.mean(np.abs(iq[:pulse_samples]) ** 2))
    return float(10 * np.log10(max(signal - noise, 1e-30) / max(noise, 1e-30)))
