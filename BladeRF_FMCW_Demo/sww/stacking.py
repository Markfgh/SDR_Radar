"""Prager Algorithm 1 / eq.13-16 with common RF grid and explicit zero padding.

DFT: X(f)=sum x[m] exp(-j2*pi*f*m/fs). Linear correlation requires
Lfft>=Lrx+Ltx-1. CZT evaluates that same spectrum at real RF offsets, including
nonuniform centers; it avoids interpolation and rounding of center frequencies.
Output carries exp(-j2*pi*f_ref*tau) phase. Range is signed, periodic at c/(2*df).
"""

import numpy as np
from scipy.fft import next_fast_len

from . import C
from .calibration import estimate_transfer, reference_projection
from .diagnostics import reference_snr
from .gls import gls_filter
from .matched_filter import dtft_uniform
from .types import SwwResult
from .waveforms import chirp, fractional_delay, window


def stack(
    config, records, calibration="switched", apply_gls=True, motion_positions_m=None
):
    config.validate()
    if calibration not in ("switched", "summed", "none"):
        raise ValueError("Unknown calibration mode")
    x = chirp(config)
    lfft = next_fast_len(config.capture_samples + x.size - 1)
    df = config.fs_sps / lfft
    low = min(config.centers_hz) - config.bs_hz / 2
    high = max(config.centers_hz) + config.bs_hz / 2
    f_ref = (low + high) / 2
    nout = next_fast_len(int(np.ceil((high - low) / df) + 2) * config.upsample)
    if nout > 2_000_000:
        raise ValueError("Synthetic grid exceeds preparation memory budget")
    rf = f_ref + (np.arange(nout) - nout // 2) * df
    d = np.zeros(nout, complex)
    w = d.copy()
    coverage = np.zeros(nout)
    cal_metrics = []
    indexed = {(r.step, r.mode): r for r in records}
    if len(indexed) != len(records):
        raise ValueError("Duplicate acquisition record")
    energy = np.vdot(x, x).real
    motion_errors = None
    if motion_positions_m is not None:
        from .motion import path_errors

        if config.motion_reference_point_m is None:
            raise ValueError("Explicit motion reference point required")
        if len(motion_positions_m) != len(config.centers_hz):
            raise ValueError("One position per step required")
        motion_errors = path_errors(
            motion_positions_m, config.motion_reference_point_m, [0, 0, 0]
        )
    for step, fc in enumerate(config.centers_hz):
        radar = indexed[step, "RADAR"]
        for mode in (
            ("CAL_PRE", "RADAR", "CAL_POST")
            if calibration == "switched"
            else ("RADAR",)
        ):
            rec = indexed[step, mode]
            if (
                rec.fc_hz != fc
                or rec.status
                or rec.actual_count != config.capture_samples
            ):
                raise ValueError("Invalid step: retune, stream status or short capture")
            if rec.origin != "SIMULATED" and not rec.metadata.get(
                "digital_epoch_known", False
            ):
                raise ValueError("PHASE_UNVERIFIED: RX/TX digital epoch not qualified")
            if rec.metadata.get("clipped_components_before_quantization", 0) or np.any(
                np.abs(rec.raw.astype(np.int32)) >= 2028
            ):
                raise ValueError("Clipped/near-full-scale capture")
        indices = np.flatnonzero(
            (rf >= fc - config.bs_hz / 2) & (rf < fc + config.bs_hz / 2)
        )
        start = rf[indices[0]] - fc
        local_x = dtft_uniform(x, start, df, indices.size, config.fs_sps)
        observed = radar.iq
        summed = None
        if calibration == "summed":
            template = (
                config.reference_amplitude
                * np.exp(-2j * np.pi * fc * config.reference_delay_s)
                * fractional_delay(
                    x, config.reference_delay_s, config.fs_sps, config.capture_samples
                )
            )
            coefficient, snr = reference_projection(observed, template)
            if (
                snr < config.min_reference_snr_db
                or abs(coefficient) < config.spectral_floor
            ):
                raise ValueError(
                    "INVALID_PHASE: summed reference absent/degraded/contaminated"
                )
            observed = observed - coefficient * template
            summed = coefficient
        local_z = dtft_uniform(observed, start, df, indices.size, config.fs_sps)
        valid = np.abs(local_x) > config.spectral_floor * np.max(np.abs(local_x))
        for lower, upper in config.excluded_bands_hz:
            valid &= ~((rf[indices] >= lower) & (rf[indices] < upper))
        if calibration == "switched":
            pre, post = indexed[step, "CAL_PRE"], indexed[step, "CAL_POST"]
            cp = dtft_uniform(pre.iq, start, df, indices.size, config.fs_sps)
            cq = dtft_uniform(post.iq, start, df, indices.size, config.fs_sps)
            # Simulated equal spacing; real records require measured acquisition times.
            if radar.origin == "SIMULATED":
                alpha = 0.5
            else:
                times = [
                    r.metadata.get("acquisition_time_s") for r in (pre, radar, post)
                ]
                if any(t is None for t in times) or not times[0] < times[1] < times[2]:
                    raise ValueError("Missing/invalid CAL/RADAR acquisition times")
                alpha = (times[1] - times[0]) / (times[2] - times[0])
            cal = estimate_transfer(
                cp,
                cq,
                local_x,
                rf[indices],
                config.reference_delay_s,
                config.reference_amplitude,
                config.spectral_floor,
                config.max_cal_drift_rad,
                min(reference_snr(pre.iq, x.size), reference_snr(post.iq, x.size)),
                config.min_reference_snr_db,
                alpha,
            )
            if not cal.valid:
                raise ValueError(
                    f"INVALID_PHASE step {step}: SNR={cal.snr_db}, drift={cal.drift_rms_rad}"
                )
            valid &= cal.valid_bins & (
                np.abs(cal.transfer) >= 1 / config.max_inverse_gain
            )
            local_z[valid] /= cal.transfer[valid]
            cal_metrics.append(
                {
                    "step": step,
                    "snr_db": cal.snr_db,
                    "drift_rms_rad": cal.drift_rms_rad,
                    "transfer_phase_rad": float(
                        np.angle(cal.transfer[len(local_x) // 2])
                    ),
                }
            )
        elif summed is not None:
            local_z /= summed
            cal_metrics.append(
                {
                    "step": step,
                    "projection_snr_db": snr,
                    "transfer_phase_rad": float(np.angle(summed)),
                    "warning": "ECHO_BIAS_AND_DISPERSION_UNOBSERVABLE",
                }
            )
        if motion_errors is not None:
            from .motion import compensate

            local_z = compensate(local_z, rf[indices], motion_errors[step])
        local_d = local_z * np.conj(local_x) / energy
        local_w = np.abs(local_x) ** 2 / energy
        local_d[~valid] = 0
        local_w[~valid] = 0
        weight = config.step_weights[step] if config.step_weights else 1
        d[indices] += weight * local_d
        w[indices] += weight * local_w
        coverage[indices] += weight * valid
    support = coverage > 0
    if config.overlap_normalize:
        d[support] /= coverage[support]
        w[support] /= coverage[support]
    desired = np.zeros(nout)
    inside = (rf >= low) & (rf < high)
    desired[inside] = window(config.range_window, np.sum(inside))
    h, valid, gm = gls_filter(
        w,
        desired,
        support,
        config.gls_mode if apply_gls else "none",
        config.regularization,
        config.spectral_floor,
        config.max_inverse_gain,
    )
    output = d * h
    profile = np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(output))) * nout / lfft
    tau = (np.arange(nout) - nout // 2) / (nout * df)
    origins = sorted(set(r.origin for r in records))
    simulation = origins == ["SIMULATED"]
    result = SwwResult(
        rf,
        output,
        w * h,
        valid,
        C * tau / 2,
        profile,
        f_ref,
        {
            "df_hz": df,
            "synthetic_span_hz": high - low,
            "coverage_fraction": float(np.mean(support[inside])),
            "resolution_ideal_m": C / (2 * (high - low)),
            "single_resolution_ideal_m": C / (2 * config.bi_hz),
            "unambiguous_period_m": C / (2 * df),
            "range_sample_spacing_m": C / (2 * nout * df),
            "ifft_scale": nout / lfft,
            "overlap_normalized": config.overlap_normalize,
            "gls": gm,
            "calibration": cal_metrics,
            "origin": origins,
            "motion_corrected": motion_errors is not None,
        },
        absolute_range_valid=False,
        relative_range_valid=simulation,
        phase_state="SIMULATED_ENDPOINTS_ONLY"
        if simulation and calibration != "none"
        else "PHASE_UNVERIFIED",
    )
    return result
