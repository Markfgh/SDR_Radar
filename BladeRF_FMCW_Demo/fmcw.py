"""Waveform generation and explicitly-labelled, experimental FMCW processing."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import correlate, find_peaks, stft


SPEED_OF_LIGHT = 299_792_458.0
RANGE_FFT_ZERO_PAD_FACTOR = 8  # visual interpolation only; never improves c/(2B) resolution


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
    beat: np.ndarray
    alignment_confidence: float
    alignment_peak_to_median_db: float
    synced: bool
    leakage_range_m: float


@dataclass(frozen=True)
class ChirpAlignment:
    """Generated-TX-template chirp boundary estimate for one real RX block."""
    offset_samples: int
    confidence: float
    peak_to_median_db: float
    reliable: bool


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
    if not 0.0 < amplitude <= 1.0:
        raise ValueError("Amplitude must be in the inclusive range (0, 1] full scale")
    time_s = np.arange(params.samples_per_chirp, dtype=np.float64) / params.sample_rate_sps
    phase = 2.0 * np.pi * (-params.bandwidth_hz * time_s / 2.0 + params.slope_hz_per_s * time_s**2 / 2.0)
    # SC16_Q11's positive limit is +2047/2048. A GUI request for 100% is
    # therefore represented by that exact maximum rather than overflowing or
    # rejecting the requested control range.
    effective_amplitude = min(amplitude, 2047.0 / 2048.0)
    return (effective_amplitude * np.exp(1j * phase)).astype(np.complex64)


def generated_rf_frequency_hz(params: FmcwParameters) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates for the GUI's generated-reference TX frequency plot."""
    time_s = np.arange(params.samples_per_chirp, dtype=np.float64) / params.sample_rate_sps
    return time_s, params.start_frequency_hz + params.bandwidth_hz * time_s / params.duration_s


def estimate_chirp_alignment(rx: np.ndarray, reference: np.ndarray) -> ChirpAlignment:
    """Locate a generated-TX chirp in RX I/Q without declaring absolute time."""
    if rx.size < reference.size:
        raise ValueError("RX block is shorter than one chirp")
    values = correlate(rx, reference, mode="valid", method="fft")
    magnitude = np.abs(values)
    offset = int(np.argmax(magnitude))
    segment = rx[offset:offset + reference.size]
    denominator = np.sqrt(float(np.vdot(segment, segment).real * np.vdot(reference, reference).real))
    confidence = float(magnitude[offset] / denominator) if denominator else 0.0
    median = max(float(np.median(magnitude)), np.finfo(float).tiny)
    peak_to_median_db = 20.0 * np.log10(max(float(magnitude[offset]), np.finfo(float).tiny) / median)
    # These conservative thresholds avoid presenting host-buffer noise as a
    # chirp boundary. They are not a substitute for timestamped hardware sync.
    reliable = magnitude.size >= 2 and confidence >= 0.12 and peak_to_median_db >= 6.0
    return ChirpAlignment(offset, confidence, peak_to_median_db, reliable)


def rx_spectrogram(samples: np.ndarray, sample_rate_sps: float, window_s: float,
                   dynamic_range_db: float, nperseg: int = 2048) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return a centred, real-RX complex-I/Q spectrogram in relative dB."""
    if samples.ndim != 1 or samples.size < 128:
        raise ValueError("Spectrogram requires at least 128 complex RX samples")
    if window_s <= 0 or dynamic_range_db <= 0:
        raise ValueError("Spectrogram window and dynamic range must be positive")
    count = min(samples.size, max(128, round(window_s * sample_rate_sps)))
    observed = samples[-count:]
    segment_length = min(nperseg, observed.size)
    frequency_hz, time_s, values = stft(observed, fs=sample_rate_sps, window="hann", nperseg=segment_length,
                                        noverlap=segment_length * 3 // 4, return_onesided=False,
                                        boundary=None, padded=False)
    frequency_hz = np.fft.fftshift(frequency_hz)
    values = np.fft.fftshift(values, axes=0)
    db = 20.0 * np.log10(np.maximum(np.abs(values), 1e-12))
    db -= float(db.max())
    db = np.maximum(db, -dynamic_range_db)
    return time_s * 1000.0, frequency_hz / 1e6, db


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
    alignment = estimate_chirp_alignment(rx, reference)
    # The generated TX template defines t=0 for this chirp only. Target propagation
    # delays remain relative to it; they are not subtracted by this segmentation.
    aligned = rx[alignment.offset_samples:alignment.offset_samples + reference.size].astype(np.complex64, copy=True)
    if dc_remove:
        aligned -= np.mean(aligned)
    beat = aligned * np.conj(reference)
    window = np.hanning(beat.size)
    nfft = beat.size * RANGE_FFT_ZERO_PAD_FACTOR
    # Zero padding gives a readable interpolated range curve; physical range
    # resolution remains c/(2B) and is reported separately in the GUI.
    spectrum = np.fft.fft(beat * window, n=nfft) / np.sum(window)
    frequencies = np.fft.fftfreq(nfft, d=1.0 / params.sample_rate_sps)
    # For an up-chirp with rx * conj(tx), a delayed echo lies on the negative
    # frequency branch: f_b = -S*tau. Do not use the positive image as a target.
    negative = frequencies < 0
    range_m = -SPEED_OF_LIGHT * frequencies[negative] / (2.0 * params.slope_hz_per_s)
    selected_spectrum = spectrum[negative]
    order = np.argsort(range_m)
    range_m, selected_spectrum = range_m[order], selected_spectrum[order]
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
    noise_floor = float(np.median(db))
    # The low-range peak is the TX-to-RX leakage diagnostic, not a target.
    peaks = peaks[(range_m[peaks] >= params.range_resolution_m) & (db[peaks] >= noise_floor + 6.0)]
    if not alignment.reliable:
        peaks = np.array([], dtype=int)
    strongest = peaks[np.argsort(db[peaks])[-5:]] if peaks.size else peaks
    leakage_range_m = float(range_m[np.argmax(np.abs(raw_spectrum))]) if raw_spectrum.size else float("nan")
    return ProcessingResult(range_m, db, raw_spectrum, aligned, alignment.offset_samples, range_m[strongest], raw,
                            beat, alignment.confidence, alignment.peak_to_median_db, alignment.reliable, leakage_range_m)
