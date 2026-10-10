"""Bench DSP on measured arrays; independent delay is used ONLY for validation.

Single stationary delayed path qualification. Reference and echo share a stable
LTI response within a subchirp, apart from a scalar and delay. This hypothesis is
tested by the residual, reference pre/post shape, and independent phase/range.
It is not a general multipath decomposition or an absolute TX/RX epoch proof.
"""

from dataclasses import replace

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.signal import correlate, find_peaks

from . import C
from .diagnostics import phase_statistics
from .metrics import peak_metrics
from .stacking import stack
from .types import AcquisitionRecord
from .waveforms import chirp, encode_sc16


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def extract_pulses(config, records, repeats, criteria):
    """Concatenate contiguous buffers before correlation, including boundary pulses."""
    require(bool(records), "No RX records")
    x = chirp(config)
    for a, b in zip(records, records[1:], strict=False):
        require(
            a.timestamp is not None and b.timestamp == a.timestamp + a.actual_count,
            "RX timestamp gap inside frequency visit",
        )
    for r in records:
        require(
            r.status == 0 and r.actual_count == r.raw.size // 2,
            "RX status/count invalid",
        )
        require(r.timestamp is not None, "RX timestamp missing")
        require(np.max(np.abs(r.raw.astype(np.int32))) < 2028, "ADC near full scale")
    y = np.concatenate([r.iq for r in records])
    corr = correlate(y, x, mode="valid", method="fft")
    candidates, _ = find_peaks(
        np.abs(corr),
        distance=2 * x.size,
        height=np.max(np.abs(corr)) * criteria["detection_peak_fraction"],
    )
    guard = x.size // 4
    extracted = []
    energy = np.vdot(x, x).real
    for lag in candidates:
        start = int(lag) - guard
        if start < 0 or start + config.capture_samples > y.size:
            continue
        segment = y[lag : lag + x.size]
        confidence = abs(corr[lag]) / max(
            np.sqrt(energy * np.vdot(segment, segment).real), 1e-30
        )
        if confidence < criteria["min_detection_confidence"]:
            continue
        extracted.append(
            {
                "iq": y[start : start + config.capture_samples].copy(),
                "timestamp": records[0].timestamp + start,
                "lag_samples": int(lag),
                "confidence": float(confidence),
                "raw_reference_phase_rad": float(np.angle(corr[lag])),
            }
        )
    require(
        len(extracted) == repeats,
        f"Detected {len(extracted)} complete pulses, expected {repeats}; no padding/fallback",
    )
    return extracted


def align_reference(pulses, criteria):
    """Measured reference mean after scalar phase alignment, no ideal replacement."""
    seed = pulses[0]["iq"]
    energy = np.vdot(seed, seed).real
    aligned = []
    shape_errors = []
    for p in pulses:
        coef = np.vdot(seed, p["iq"]) / energy
        require(abs(coef) > 1e-12, "Missing reference")
        a = p["iq"] / coef
        error = float(np.linalg.norm(a - seed) / np.linalg.norm(seed))
        shape_errors.append(error)
        require(
            error <= criteria["max_reference_shape_error"],
            "Reference shape changes: timing drift, dispersion, interference or SNR",
        )
        aligned.append(a)
    return np.mean(aligned, axis=0), shape_errors


def shifted(samples, lag_samples):
    """Bandlimited template shift; never used to synthesize an output echo."""
    n = 4 * len(samples)
    f = np.fft.fftfreq(n)
    return np.fft.ifft(np.fft.fft(samples, n) * np.exp(-2j * np.pi * f * lag_samples))[
        : len(samples)
    ]


def fit_independent_echo(y, reference, config, criteria):
    """Blind positive-delay search. The known delay is NOT an input to this fit."""
    maximum = 2 * config.max_range_m / C * config.fs_sps
    require(maximum >= 1, "Echo search interval too short")

    def fit(lag):
        echo = shifted(reference, lag)
        design = np.column_stack([reference, echo])
        coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
        residual = y - design @ coefficients
        return float(np.vdot(residual, residual).real), coefficients, echo, residual

    grid = np.linspace(0.1, maximum, max(32, int(2 * maximum) + 1))
    require(len(grid) <= 4096, "Blind delay search exceeds bounded bench budget")
    costs = np.array([fit(lag)[0] for lag in grid])
    best = int(np.argmin(costs))
    require(0 < best < len(grid) - 1, "Echo delay at search boundary/unresolved")
    result = minimize_scalar(
        lambda lag: fit(lag)[0],
        bounds=(grid[best - 1], grid[best + 1]),
        method="bounded",
        options={"xatol": 1e-7},
    )
    cost, coefficients, echo, residual = fit(result.x)
    er = np.vdot(reference, reference).real
    ee = np.vdot(echo, echo).real
    rho = float(abs(np.vdot(reference, echo)) / np.sqrt(er * ee))
    gram_condition = float((1 + rho) / max(1 - rho, 1e-15))
    noise = max(cost / max(len(y) - 2, 1), 1e-30)
    snr_ref = float(
        10 * np.log10(max(abs(coefficients[0]) ** 2 * er * (1 - rho**2), 1e-30) / noise)
    )
    snr_echo = float(
        10 * np.log10(max(abs(coefficients[1]) ** 2 * ee * (1 - rho**2), 1e-30) / noise)
    )
    fraction = float(np.linalg.norm(residual) / max(np.linalg.norm(y), 1e-30))
    projection = np.vdot(reference, y) / er
    epsilon = float(abs(projection / coefficients[0] - 1))
    bias = float(np.angle(projection / coefficients[0]))
    return {
        "coefficient": complex(coefficients[0]),
        "echo_coefficient": complex(coefficients[1]),
        "delay_s": float(result.x / config.fs_sps),
        "rho": rho,
        "gram_condition": gram_condition,
        "noise_variance_inflation": float(1 / max(1 - rho**2, 1e-15)),
        "reference_fit_snr_db": snr_ref,
        "echo_fit_snr_db": snr_echo,
        "model_residual_fraction": fraction,
        "projection_contamination_epsilon": epsilon,
        "projection_phase_bias_rad": bias,
        "projection_phase_bound_rad": float(np.arcsin(epsilon))
        if epsilon < 1
        else None,
        "ratio_phase_rad": float(np.angle(coefficients[1] / coefficients[0])),
    }


def validate_rows(rows, known, criteria, phase_origin=None, enforce=True):
    require(bool(rows), "No independent observations")
    failures = []

    def check(ok, message):
        if not ok:
            failures.append(message)

    for row in rows:
        check(
            row["gram_condition"] <= criteria["max_gram_condition"],
            "Reference/echo not separable: ill-conditioned Gram matrix",
        )
        check(
            row["reference_fit_snr_db"] >= criteria["min_reference_fit_snr_db"],
            "Reference fit SNR too low",
        )
        check(
            row["echo_fit_snr_db"] >= criteria["min_echo_fit_snr_db"],
            "Independent echo fit SNR too low",
        )
        check(
            row["model_residual_fraction"] <= criteria["max_model_residual_fraction"],
            "Two-path model invalid: multipath/dispersion/timing",
        )
        check(
            row["projection_phase_bound_rad"] is not None,
            "Reference contamination has no finite arcsin phase bound",
        )
        check(
            row["projection_phase_bound_rad"] is not None
            and row["projection_phase_bound_rad"]
            <= criteria["max_projection_phase_bound_rad"],
            "Summed reference contamination budget FAIL",
        )
    delay = known["relative_delay_s"]
    # Only ONE constant reflection/coupler phase nuisance; never fit the delay,
    # one phase per frequency, or a phase slope to the independently known path.
    phases = np.array(
        [r["ratio_phase_rad"] + 2 * np.pi * r["fc_hz"] * delay for r in rows]
    )
    if phase_origin is None:
        phase_origin = float(np.angle(np.mean(np.exp(1j * phases))))
    residual = np.angle(np.exp(1j * (phases - phase_origin)))
    errors = np.array([r["delay_s"] - delay for r in rows])
    metrics = {
        "phase_origin_rad": phase_origin,
        "phase_residual_rad": residual.tolist(),
        "phase_rms_rad": float(np.sqrt(np.mean(residual**2))),
        "phase_p95_abs_rad": float(np.percentile(np.abs(residual), 95)),
        "max_delay_error_s": float(np.max(np.abs(errors))),
        "range_errors_m": (errors * C / 2).tolist(),
        "observations": len(rows),
    }
    check(
        metrics["phase_rms_rad"] <= criteria["max_phase_rms_rad"],
        "Independent echo phase RMS FAIL",
    )
    check(
        metrics["phase_p95_abs_rad"] <= criteria["max_phase_p95_rad"],
        "Independent echo phase percentile FAIL",
    )
    check(
        metrics["max_delay_error_s"]
        <= known["delay_uncertainty_s"] + criteria["max_delay_error_s"],
        "Blind echo delay differs from independently known delay",
    )
    metrics["failures"] = failures
    metrics["status"] = "FAIL" if failures else "PASS"
    if enforce:
        require(not failures, "; ".join(failures))
    return metrics


def measured_profiles(config, pulses, references, fits):
    """Prager stack of actual residual I/Q, NOT the fitted echo templates."""
    records = []
    measured = {}
    quantization_error = []
    for step, fc in enumerate(config.centers_hz):
        iq = pulses[step]["iq"]
        raw = encode_sc16(iq)
        quantization_error.append(
            float(np.max(np.abs(iq - (raw[0::2] + 1j * raw[1::2]) / 2048)))
        )
        records.append(
            AcquisitionRecord(
                step,
                "RADAR",
                fc,
                raw,
                pulses[step]["timestamp"],
                0,
                config.capture_samples,
                "HARDWARE",
                {"reference_relative_alignment": True, "digital_epoch_known": False},
            )
        )
        measured[step] = references[fc], fits[step]["coefficient"]
    wide = stack(
        config, records, calibration="measured_summed", measured_reference=measured
    )
    single_config = replace(config, centers_hz=(config.centers_hz[0],), step_weights=())
    single = stack(
        single_config,
        records[:1],
        calibration="measured_summed",
        measured_reference={0: measured[0]},
    )
    require(
        np.any(wide.support) and np.any(single.support), "No measured spectral support"
    )
    return wide, single, quantization_error


def profile_metrics(result, expected_delay_s, max_range_m):
    # Restrict to positive relative range; absolute RF/cable epoch stays unknown.
    keep = (result.range_m >= 0) & (result.range_m <= max_range_m)
    require(np.count_nonzero(keep) > 4, "Known echo outside profile axis")
    return peak_metrics(
        result.range_m[keep], result.profile[keep], C * expected_delay_s / 2
    )


def raw_phase_stats(pulses):
    return phase_statistics([p["raw_reference_phase_rad"] for p in pulses])
