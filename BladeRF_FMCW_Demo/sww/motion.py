"""Eq.17-20: stop-and-go motion correction around a specified reference point.

Input positions are measured/simulated Cartesian metres, NEVER inferred from IQ.
Correction is exp(+j2*pi*(fc+fbb)*2*dR/c) on compressed echo spectra.
"""

import numpy as np

from . import C


def path_errors(positions_m, reference_point_m, origin_m):
    positions = np.asarray(positions_m, float)
    point = np.asarray(reference_point_m, float)
    origin = np.asarray(origin_m, float)
    if (
        positions.ndim != 2
        or positions.shape[1] != 3
        or point.shape != (3,)
        or origin.shape != (3,)
        or not np.all(np.isfinite(positions))
        or not np.all(np.isfinite(point))
        or not np.all(np.isfinite(origin))
    ):
        raise ValueError("Finite 3-D positions required")
    return np.linalg.norm(positions - point, axis=1) - np.linalg.norm(origin - point)


def compensate(spectrum, rf_hz, delta_range_m):
    return np.asarray(spectrum) * np.exp(
        4j * np.pi * np.asarray(rf_hz) * delta_range_m / C
    )


def scene_approximation_error(positions_m, targets_m, reference_point_m, origin_m):
    delta = path_errors(positions_m, reference_point_m, origin_m)
    return np.stack(
        [path_errors(positions_m, t, origin_m) - delta for t in targets_m], axis=1
    )
