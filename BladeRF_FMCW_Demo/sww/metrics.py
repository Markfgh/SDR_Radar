"""Metrics have explicit masks; zero-padding does not change physical resolution."""

import numpy as np
from scipy.signal import find_peaks


def db(values):
    a = np.abs(values)
    return 20 * np.log10(np.maximum(a, 1e-15) / max(float(np.max(a)), 1e-15))


def peak_metrics(
    range_m, profile, expected_m=None, mainlobe_half_width_m=None, grating_period_m=None
):
    a = np.abs(profile)
    i = int(np.argmax(a))
    normalized = db(a)
    # Width at amplitude -6dB, linearly interpolated at the two crossings.
    level = a[i] * 10 ** (-6 / 20)
    left = i
    right = i
    while left > 0 and a[left] > level:
        left -= 1
    while right < a.size - 1 and a[right] > level:
        right += 1

    def crossing(k, j):
        return (
            float(
                range_m[k] + (range_m[j] - range_m[k]) * (level - a[k]) / (a[j] - a[k])
            )
            if a[j] != a[k]
            else float(range_m[k])
        )

    width = (
        crossing(right - 1, right) - crossing(left, left + 1)
        if left < i < right and a[left] <= level and a[right] <= level
        else None
    )
    if mainlobe_half_width_m is None:
        minima, _ = find_peaks(-a)
        left_minima = minima[minima < i]
        right_minima = minima[minima > i]
        outside = np.ones(a.size, bool)
        outside[
            left_minima[-1] if left_minima.size else 0 : (right_minima[0] + 1)
            if right_minima.size
            else a.size
        ] = False
    else:
        outside = np.abs(range_m - range_m[i]) > mainlobe_half_width_m
    psl = float(np.max(normalized[outside])) if np.any(outside) else None
    pgl = None
    if grating_period_m is not None:
        delta = np.abs(range_m - range_m[i])
        orders = np.rint(delta / grating_period_m)
        near = (
            (orders >= 1)
            & (np.abs(delta - orders * grating_period_m) < 0.15 * grating_period_m)
            & outside
        )
        if np.any(near):
            pgl = float(np.max(normalized[near]))
    return {
        "peak_range_m": float(range_m[i]),
        "range_error_m": None if expected_m is None else float(range_m[i] - expected_m),
        "width_6db_m": width,
        "psl_db": psl,
        "pgl_db": pgl,
    }


def two_target_metrics(
    result, target_ranges, search_half_width_m, min_valley_db=3, min_peak_db=-6
):
    """Two distinct local maxima near known ranges, with configured valley depth.

    The 3dB default defines this deterministic simulation experiment only.
    """
    r = result.range_m
    a = np.abs(result.profile)
    peaks, _ = find_peaks(a)
    peaks = peaks[a[peaks] >= a.max() * 10 ** (min_peak_db / 20)]
    chosen = []
    for target in sorted(target_ranges):
        eligible = peaks[np.abs(r[peaks] - target) <= search_half_width_m]
        if not eligible.size:
            return {"resolved": False, "peak_ranges_m": [], "valley_db": None}
        chosen.append(int(eligible[np.argmax(a[eligible])]))
    if len(set(chosen)) != 2 or chosen[1] <= chosen[0]:
        return {
            "resolved": False,
            "peak_ranges_m": r[chosen].tolist(),
            "valley_db": None,
        }
    valley = float(np.min(a[chosen[0] : chosen[1] + 1]))
    depth = float(20 * np.log10(max(min(a[chosen]), 1e-30) / max(valley, 1e-30)))
    return {
        "resolved": depth >= min_valley_db,
        "peak_ranges_m": r[chosen].tolist(),
        "valley_db": depth,
        "errors_m": (r[chosen] - np.sort(target_ranges)).tolist(),
    }


def phase_residual(measured, ideal):
    mask = (
        measured.support
        & ideal.support
        & (np.abs(ideal.spectrum) > 0.1 * np.max(np.abs(ideal.spectrum)))
    )
    if not np.any(mask):
        return {"valid": False, "rms_rad": None}
    residual = np.angle(measured.spectrum[mask] * np.conj(ideal.spectrum[mask]))
    return {
        "valid": True,
        "rms_rad": float(np.sqrt(np.mean(residual**2))),
        "samples": int(np.sum(mask)),
    }
