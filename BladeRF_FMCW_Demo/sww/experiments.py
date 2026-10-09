"""Reproducible ablations. Truth is simulation-only; no hardware gate is approved."""

from dataclasses import replace

import numpy as np

from . import C
from .calibration import contamination, reference_projection
from .config import Target
from .metrics import peak_metrics, phase_residual, two_target_metrics
from .simulation import simulate
from .stacking import stack
from .waveforms import chirp, fractional_delay


def compare(config, records):
    poses = None
    if config.motion_reference_point_m is not None:
        poses = [r.metadata.get("position_m") for r in records if r.mode == "RADAR"]
        if len(poses) != len(config.centers_hz) or any(p is None for p in poses):
            raise ValueError("Missing recorded motion positions")
    corrected = stack(
        config, records, calibration=config.topology, motion_positions_m=poses
    )
    raw = stack(config, records, calibration="none")
    ideal_records, _ = simulate(config, ideal=True)
    ideal = stack(
        config,
        ideal_records,
        calibration="switched" if config.topology == "switched" else "summed",
    )
    one = replace(
        config, centers_hz=(config.centers_hz[0],), step_weights=config.step_weights[:1]
    )
    single = stack(
        one, [r for r in records if r.step == 0], calibration=config.topology
    )
    results = {
        "corrected": corrected,
        "uncorrected": raw,
        "ideal": ideal,
        "single": single,
    }
    metrics = {}
    for name, result in results.items():
        psf = np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(result.reference_spectrum)))
        period = (
            C / (2 * np.median(np.diff(sorted(config.centers_hz))))
            if len(config.centers_hz) > 1
            else None
        )
        metrics[name] = {
            "processing": result.metrics,
            "psf": peak_metrics(result.range_m, psf, 0, grating_period_m=period),
        }
        if len(config.targets) == 2:
            metrics[name]["two_targets"] = two_target_metrics(
                result,
                [t.range_m for t in config.targets],
                min(
                    abs(config.targets[1].range_m - config.targets[0].range_m) / 3, 0.4
                ),
            )
    metrics["phase_residual_corrected"] = phase_residual(corrected, ideal)
    metrics["phase_residual_uncorrected"] = phase_residual(raw, ideal)
    passed = (
        metrics["phase_residual_corrected"]["rms_rad"] is not None
        and metrics["phase_residual_corrected"]["rms_rad"] < 0.03
    )
    corrected.absolute_range_valid = passed
    corrected.phase_state = (
        "SIMULATED_VERIFIED" if passed else "SIMULATED_VALIDATION_FAIL"
    )
    metrics["independent_validation"] = {
        "status": ("PASS" if passed else "FAIL") if config.targets else "BLOCKED",
        "threshold_rad": 0.03,
        "scope": "Spectral phase against static ideal SIMULATION ONLY; not a hardware KPI",
    }
    return results, metrics


def contamination_study(config):
    """Scalar reference extraction bias vs known echo delay and echo/reference ratio."""
    x = chirp(config)
    count = config.capture_samples
    base = fractional_delay(x, config.reference_delay_s, config.fs_sps, count)
    rows = []
    for delay_bw in (0, 0.1, 0.5, 1, 2, 4, 8):
        for ratio in (0.01, 0.1, 0.5, 1.0):
            echo = (
                ratio
                * np.exp(0.7j)
                * fractional_delay(
                    x,
                    config.reference_delay_s + delay_bw / config.bi_hz,
                    config.fs_sps,
                    count,
                )
            )
            row = contamination(base, [echo])
            coef, snr = reference_projection(base + echo, base)
            # Independent validation path has a different, known delay.
            probe = fractional_delay(
                x, config.reference_delay_s + 3 / config.bi_hz, config.fs_sps, count
            )
            corrected = probe / coef
            row.update(
                delay_in_inverse_bandwidth=delay_bw,
                echo_reference_ratio=ratio,
                projection_snr_db=snr,
                independent_echo_phase_residual_rad=float(
                    np.angle(np.vdot(probe, corrected))
                ),
                independent_echo_amplitude_ratio=float(1 / abs(coef)),
                identifiable=False if delay_bw == 0 else None,
            )
            rows.append(row)
    return rows


def suppression_study(config):
    """Separate knobs, same total span for window/nonuniform/overlap comparisons."""
    base = replace(
        config,
        targets=(Target(20),),
        noise_rms=0,
        rf_ripple=0,
        rf_delay_s=0,
        random_phase=False,
        drift_rad=0,
    )
    rng = np.random.default_rng(config.seed)
    centers = np.array(sorted(config.centers_hz))
    jitter = rng.uniform(-0.02, 0.02, len(centers)) * config.bi_hz
    jitter[0] = jitter[-1] = 0
    variants = {
        "uniform_raw": replace(base, gls_mode="none"),
        "pulse_tukey": replace(base, pulse_window="tukey", gls_mode="none"),
        "nonuniform": replace(
            base, centers_hz=tuple(centers + jitter), gls_mode="none"
        ),
        "overlap": replace(
            base,
            centers_hz=tuple(
                np.linspace(centers[0], centers[-1], 2 * len(centers) - 1)
            ),
            gls_mode="none",
        ),
        "gls_rect": replace(base, gls_mode="stable"),
        "gls_hamming": replace(base, range_window="hamming", gls_mode="stable"),
    }
    output = {}
    for name, cfg in variants.items():
        rec, _ = simulate(cfg, ideal=True)
        result = stack(cfg, rec)
        psf = np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(result.reference_spectrum)))
        output[name] = {
            "psf": peak_metrics(
                result.range_m,
                psf,
                0,
                grating_period_m=C / (2 * np.median(np.diff(sorted(cfg.centers_hz)))),
            ),
            "span_hz": result.metrics["synthetic_span_hz"],
            "steps": len(cfg.centers_hz),
            "coverage": result.metrics["coverage_fraction"],
        }
    return output
