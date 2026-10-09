"""Complex CAL_PRE/POST interpolation, cable deembedding, single-RX contamination.

No function establishes hardware phase validity merely by normalizing CAL.
External branch transfer and digital time origin must be independently measured.
"""

import numpy as np

from .types import CalibrationResult


def estimate_transfer(
    pre,
    post,
    x,
    rf_hz,
    tau_ref_s,
    ref_amplitude,
    floor,
    max_drift_rad,
    snr_db,
    min_snr_db,
    alpha=0.5,
):
    if not 0 <= alpha <= 1:
        raise ValueError("RADAR time must lie between CAL_PRE and CAL_POST")
    valid = np.abs(x) >= floor * np.max(np.abs(x))
    denominator = x * ref_amplitude * np.exp(-2j * np.pi * rf_hz * tau_ref_s)
    hp = np.zeros_like(x, complex)
    hq = hp.copy()
    hp[valid] = pre[valid] / denominator[valid]
    hq[valid] = post[valid] / denominator[valid]
    delta = np.angle(hq * np.conj(hp))
    valid &= (np.abs(hp) > floor) & (np.abs(hq) > floor)
    rms = float(np.sqrt(np.mean(delta[valid] ** 2))) if np.any(valid) else float("inf")
    # Endpoints cannot observe a phase jump that occurs and reverses between CALs.
    h = (np.abs(hp) * (1 - alpha) + np.abs(hq) * alpha) * np.exp(
        1j * (np.angle(hp) + alpha * delta)
    )
    ok = bool(
        np.any(valid)
        and np.all(np.isfinite(h))
        and snr_db >= min_snr_db
        and rms <= max_drift_rad
    )
    return CalibrationResult(
        h,
        valid,
        ok,
        float(snr_db),
        rms,
        "VALID_ENDPOINTS_ONLY" if ok else "INVALID_PHASE",
    )


def reference_projection(iq, template):
    """Least-squares scalar direct-path extraction; unresolved echoes bias it."""
    energy = float(np.vdot(template, template).real)
    if energy <= 0:
        raise ValueError("Missing reference template")
    coefficient = np.vdot(template, iq) / energy
    residual = iq - coefficient * template
    snr = 10 * np.log10(
        max(abs(coefficient) ** 2 * energy, 1e-30)
        / max(float(np.vdot(residual, residual).real), 1e-30)
    )
    return coefficient, float(snr)


def contamination(reference_template, echo_components):
    """Known-delay simulation diagnostic: projection bias including complex phase.

    Needs independent component truth; cannot infer contamination uniquely from
    an unresolved, single-channel hardware sum.
    """
    e = np.vdot(reference_template, reference_template).real
    if e <= 0:
        raise ValueError("Missing reference")
    bias = sum((np.vdot(reference_template, y) / e for y in echo_components), 0j)
    return {
        "complex_bias_real": float(bias.real),
        "complex_bias_imag": float(bias.imag),
        "relative_contamination": float(abs(bias)),
        "phase_bias_rad": float(np.angle(1 + bias)),
        "phase_bound_rad": float(np.arcsin(abs(bias))) if abs(bias) < 1 else None,
    }
