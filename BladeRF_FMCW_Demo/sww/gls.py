"""Eq.22-24 inversion: literal ideal mode and bounded Tikhonov physical mode."""

import numpy as np


def gls_filter(
    reference,
    desired,
    support,
    mode="stable",
    regularization=1e-4,
    floor=1e-3,
    max_gain=10,
):
    w = np.asarray(reference, complex)
    desired = np.asarray(desired, float)
    valid = (
        np.asarray(support, bool)
        & np.isfinite(w)
        & (np.abs(w) >= floor * max(float(np.max(np.abs(w))), 1e-30))
    )
    h = np.zeros_like(w)
    if mode == "none":
        h[valid] = 1
    elif mode == "paper":
        h[valid] = desired[valid] / w[valid]
    elif mode == "stable":
        scale = max(float(np.max(np.abs(w))), 1e-30)
        h[valid] = (
            desired[valid]
            * np.conj(w[valid])
            / (np.abs(w[valid]) ** 2 + regularization * scale**2)
        )
    else:
        raise ValueError("Unknown GLS mode")
    # Paper normalization at synthetic DC, falling back only with explicit report.
    dc = w.size // 2
    normalization = (
        h[dc] if valid[dc] and abs(h[dc]) > 0 else max(float(np.max(np.abs(h))), 1e-30)
    )
    h /= normalization
    capped = np.abs(h) > max_gain
    if mode == "stable":
        h[capped] *= max_gain / np.abs(h[capped])
    return (
        h,
        valid,
        {
            "mode": mode,
            "masked_fraction": float(1 - np.mean(valid)),
            "max_gain": float(np.max(np.abs(h))),
            "capped_bins": int(np.sum(capped)) if mode == "stable" else 0,
            "dc_normalized": bool(valid[dc]),
        },
    )
