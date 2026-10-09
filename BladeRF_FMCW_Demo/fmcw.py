"""Waveform generation and explicitly-labelled, experimental FMCW processing."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import correlate, find_peaks


SPEED_OF_LIGHT = 299_792_458.0


@dataclass(frozen=True)
class FmcwParameters:
    start_frequency_hz: float
    bandwidth_hz: float
    duration_s: float
    sample_rate_sps: float

    @property
    def stop_frequency_hz(self) -> float:
        return self.start_frequency_hz + self.bandwidth_hz

    @property
    def lo_frequency_hz(self) -> float:
        return self.start_frequency_hz + self.bandwidth_hz / 2.0

    @property
    def slope_hz_per_s(self) -> float:
        return self.bandwidth_hz / self.duration_s

    @property
    def samples_per_chirp(self) -> int:
        return round(self.sample_rate_sps * self.duration_s)

    @property
    def range_resolution_m(self) -> float:
        return SPEED_OF_LIGHT / (2.0 * self.bandwidth_hz)

    def validate(self) -> None:
        if self.bandwidth_hz <= 0 or self.duration_s <= 0 or self.sample_rate_sps <= 0:
            raise ValueError("Bandwidth, duration, and sample rate must be positive")
        if self.samples_per_chirp < 128:
            raise ValueError("A chirp must contain at least 128 samples")
        # A small guard band avoids demanding a baseband sweep all the way to Nyquist.
        if self.bandwidth_hz > 0.90 * self.sample_rate_sps:
            raise ValueError("Chirp bandwidth must not exceed 90% of the configured sample rate")


@dataclass(frozen=True)
class ProcessingResult:
    range_m: np.ndarray
    spectrum_db: np.ndarray
    raw_spectrum: np.ndarray
    aligned_rx: np.ndarray
    offset_samples: int
    peak_ranges_m: np.ndarray
    raw: bool


@dataclass(frozen=True)
class RxAdcMetrics:
    """Component-level SC16_Q11 ADC measurements, referenced to full scale."""
    peak_dbfs: float
    rms_dbfs: float
    headroom_db: float
    clipping_percent: float
    clipping_detected: bool


def rx_adc_metrics(samples: np.ndarray, near_full_scale: float = 0.99) -> RxAdcMetrics:
    """Measure pre-DSP RX I/Q values using per-component 0 dBFS = |2048|.

    ``samples`` must be normalized SC16_Q11 complex data. Peak and RMS are
    evaluated over the individual I and Q components, rather than complex
    magnitude, so a full-scale I component is 0 dBFS. Clipping is the percent
    of components whose magnitude is at least ``near_full_scale``.
    """
    if samples.ndim != 1 or samples.size == 0:
        raise ValueError("RX ADC monitor requires at least one complex sample")
    if not 0.0 < near_full_scale <= 1.0:
        raise ValueError("near_full_scale must be in (0, 1]")
    components = np.concatenate((samples.real, samples.imag)).astype(np.float64, copy=False)
    peak = float(np.max(np.abs(components)))
    rms = float(np.sqrt(np.mean(np.square(components))))
    floor = np.finfo(np.float64).tiny
    peak_dbfs = 20.0 * np.log10(max(peak, floor))
    rms_dbfs = 20.0 * np.log10(max(rms, floor))
    clipping_percent = 100.0 * float(np.count_nonzero(np.abs(components) >= near_full_scale)) / components.size
    return RxAdcMetrics(peak_dbfs, rms_dbfs, max(0.0, -peak_dbfs), clipping_percent, clipping_percent > 0.0)


def generate_baseband_chirp(params: FmcwParameters, amplitude: float = 0.05) -> np.ndarray:
    """Return a centered, complex up-chirp. It is a generated reference, not RF measurement."""
    params.validate()
    if not 0.0 < amplitude <= 2047.0 / 2048.0:
        raise ValueError("Amplitude must be > 0 and below the SC16_Q11 positive full scale")
    time_s = np.arange(params.samples_per_chirp, dtype=np.float64) / params.sample_rate_sps
    phase = 2.0 * np.pi * (-params.bandwidth_hz * time_s / 2.0 + params.slope_hz_per_s * time_s**2 / 2.0)
    return (amplitude * np.exp(1j * phase)).astype(np.complex64)


def generated_rf_frequency_hz(params: FmcwParameters) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates for the GUI's generated-reference TX frequency plot."""
    time_s = np.arange(params.samples_per_chirp, dtype=np.float64) / params.sample_rate_sps
    return time_s, params.start_frequency_hz + params.bandwidth_hz * time_s / params.duration_s


def _best_chirp_alignment(rx: np.ndarray, reference: np.ndarray) -> int:
    """Find strongest leakage-like copy of the reference within one acquired block."""
    if rx.size < reference.size:
        raise ValueError("RX block is shorter than one chirp")
    # Cross-correlation finds a timing reference, not a proof of absolute time.
    values = correlate(rx, reference, mode="valid", method="fft")
    return int(np.argmax(np.abs(values)))


def process_fmcw_block(
    rx: np.ndarray,
    reference: np.ndarray,
    params: FmcwParameters,
    max_range_m: float,
    calibration_spectrum: np.ndarray | None = None,
    dc_remove: bool = True,
) -> ProcessingResult:
    """Align a real RX block, dechirp it, and form a one-chirp experimental profile.

    This function intentionally gives relative/un-calibrated output unless a
    background spectrum is supplied. Host-side stream starts are not treated as
    a hardware time reference.
    """
    params.validate()
    if max_range_m <= 0:
        raise ValueError("Maximum range must be positive")
    if reference.size != params.samples_per_chirp:
        raise ValueError("Reference length does not equal samples per chirp")
    offset = _best_chirp_alignment(rx, reference)
    # The estimated correlation position is diagnostic only. Applying it as a
    # delay correction would erase the very propagation delay that creates the
    # FMCW beat. With no verified TX/RX hardware timing reference, retain the
    # acquired sample origin and label all displayed ranges UNSYNCED.
    aligned = rx[:reference.size].astype(np.complex64, copy=False)
    beat = aligned * np.conj(reference)
    if dc_remove:
        beat = beat - np.mean(beat)
    spectrum = np.fft.fft(beat * np.hanning(beat.size))
    frequencies = np.fft.fftfreq(beat.size, d=1.0 / params.sample_rate_sps)
    positive = frequencies >= 0
    range_m = SPEED_OF_LIGHT * frequencies[positive] / (2.0 * abs(params.slope_hz_per_s))
    selected_spectrum = spectrum[positive]
    raw_spectrum = selected_spectrum.copy()
    raw = calibration_spectrum is None
    if calibration_spectrum is not None:
        if calibration_spectrum.shape != selected_spectrum.shape:
            raise ValueError("Calibration spectrum shape does not match current chirp configuration")
        selected_spectrum = selected_spectrum - calibration_spectrum
    keep = range_m <= max_range_m
    range_m, selected_spectrum, raw_spectrum = range_m[keep], selected_spectrum[keep], raw_spectrum[keep]
    magnitude = np.abs(selected_spectrum)
    db = 20.0 * np.log10(np.maximum(magnitude, 1e-12) / max(float(magnitude.max()), 1e-12))
    peaks, _ = find_peaks(db, prominence=6.0)
    # Suppress DC/leakage bin from displayed targets; it remains in raw plot.
    peaks = peaks[range_m[peaks] >= params.range_resolution_m]
    strongest = peaks[np.argsort(db[peaks])[-5:]] if peaks.size else peaks
    return ProcessingResult(range_m, db, raw_spectrum, aligned, offset, range_m[strongest], raw)
