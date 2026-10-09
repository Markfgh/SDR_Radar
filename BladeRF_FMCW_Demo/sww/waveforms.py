"""Centered LFM eq.1, indexed from t=0 with physical center at Tp/2.

Both echo and reference use the same index origin, so it cancels in Z*conj(V).
Fractional delay uses Shannon bandlimited interpolation on a zero-guarded record.
"""

import numpy as np
from scipy.signal.windows import tukey


def window(name: str, count: int) -> np.ndarray:
    if name == "rect":
        return np.ones(count)
    if name == "hamming":
        return np.hamming(count)
    if name == "tukey":
        return tukey(count, alpha=0.1)
    raise ValueError(f"Unknown window {name}")


def chirp(config) -> np.ndarray:
    config.validate()
    t = np.arange(config.pulse_samples) / config.fs_sps - config.tp_s / 2
    return (
        config.amplitude
        * window(config.pulse_window, t.size)
        * np.exp(1j * np.pi * config.bi_hz / config.tp_s * t * t)
    )


def fractional_delay(
    samples: np.ndarray, delay_s: float, fs_sps: float, output_count: int
) -> np.ndarray:
    if delay_s < 0 or output_count < samples.size or fs_sps <= 0:
        raise ValueError(
            "Require nonnegative delay, positive fs and sufficient capture"
        )
    if delay_s * fs_sps + samples.size >= output_count:
        raise ValueError("Echo extends beyond capture guard")
    # 16x guard bounds finite-DFT/Nyquist interpolation error against Shannon sum.
    n = 16 * output_count
    f = np.fft.fftfreq(n, 1 / fs_sps)
    return np.fft.ifft(np.fft.fft(samples, n) * np.exp(-2j * np.pi * f * delay_s))[
        :output_count
    ]


def encode_sc16(samples: np.ndarray) -> np.ndarray:
    """Independent component clipping (complex np.clip is not component clipping)."""
    samples = np.asarray(samples)
    if samples.ndim != 1 or not np.all(np.isfinite(samples)):
        raise ValueError("Finite 1-D I/Q required")
    raw = np.empty(2 * samples.size, dtype=np.int16)
    raw[0::2] = np.rint(np.clip(samples.real * 2048, -2048, 2047)).astype(np.int16)
    raw[1::2] = np.rint(np.clip(samples.imag * 2048, -2048, 2047)).astype(np.int16)
    return raw
