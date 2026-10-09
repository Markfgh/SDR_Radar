"""Offline monostatic time-domain backprojection, adapted eq.25-31.

Each profile retains exp(-j4*pi*f_ref*R/c); focusing multiplies by the conjugate
propagation phase. This image uses full carrier compensation rather than the
paper's residual ground-range phase convention. Both are documented in DSP.md.
Requires known 3-D antenna positions and inter-sweep complex phase calibration.
"""

import numpy as np

from . import C


def backproject(
    profiles,
    range_m,
    positions_m,
    pixels_m,
    f_ref_hz,
    coherent=True,
    phase_verified=False,
):
    profiles = np.asarray(profiles)
    ranges = np.asarray(range_m, float)
    positions = np.asarray(positions_m, float)
    pixels = np.asarray(pixels_m, float)
    if not phase_verified and coherent:
        raise ValueError("PHASE_UNVERIFIED: inter-sweep phase required")
    if (
        profiles.shape != (len(positions), len(ranges))
        or positions.shape[1:] != (3,)
        or pixels.shape[-1] != 3
    ):
        raise ValueError("Profile/position/pixel shape mismatch")
    if not np.all(np.diff(ranges) > 0) or not all(
        np.all(np.isfinite(v)) for v in (profiles, ranges, positions, pixels)
    ):
        raise ValueError("Finite profiles and increasing range required")
    shape = pixels.shape[:-1]
    flat = pixels.reshape(-1, 3)
    out = np.zeros(len(flat), complex if coherent else float)
    for profile, position in zip(profiles, positions, strict=True):
        distance = np.linalg.norm(flat - position, axis=1)
        v = np.interp(distance, ranges, profile.real, left=0, right=0) + 1j * np.interp(
            distance, ranges, profile.imag, left=0, right=0
        )
        out += (
            v * np.exp(4j * np.pi * f_ref_hz * distance / C) if coherent else np.abs(v)
        )
    return out.reshape(shape)
