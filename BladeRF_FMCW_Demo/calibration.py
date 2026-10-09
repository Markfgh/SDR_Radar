"""Relative leakage-background calibration for experimentally aligned FMCW data."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fmcw import ProcessingResult


@dataclass
class LeakageCalibration:
    spectrum: np.ndarray | None = None
    offset_samples: int | None = None

    @property
    def active(self) -> bool:
        return self.spectrum is not None

    def capture(self, result: ProcessingResult) -> None:
        self.spectrum = result.raw_spectrum.copy()
        self.offset_samples = result.offset_samples

    def clear(self) -> None:
        self.spectrum = None
        self.offset_samples = None
