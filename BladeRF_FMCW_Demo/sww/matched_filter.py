"""Complex linear matched filtering, eq.9. FFT uses numpy's unscaled forward DFT."""

import numpy as np
from scipy.fft import next_fast_len
from scipy.signal import czt


def matched_filter(
    echo: np.ndarray, reference: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    if echo.ndim != 1 or reference.ndim != 1 or not echo.size or not reference.size:
        raise ValueError("Nonempty 1-D arrays required")
    n = next_fast_len(echo.size + reference.size - 1)
    corr = np.fft.ifft(np.fft.fft(echo, n) * np.conj(np.fft.fft(reference, n)))
    values = np.concatenate((corr[n - reference.size + 1 :], corr[: echo.size]))
    return np.arange(1 - reference.size, echo.size), values


def dtft_uniform(
    samples: np.ndarray, start_hz: float, spacing_hz: float, count: int, fs_sps: float
) -> np.ndarray:
    """Exact DFT at requested frequencies using chirp-z, with no rounded RF bins.

    This is the zero-padded correlation spectrum Z(f)*conj(V(f)) evaluated
    directly on the common grid. No interpolation phase error is introduced.
    """
    return czt(
        samples,
        m=count,
        w=np.exp(-2j * np.pi * spacing_hz / fs_sps),
        a=np.exp(2j * np.pi * start_hz / fs_sps),
    )
