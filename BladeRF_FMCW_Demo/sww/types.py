"""Data contracts: SI, exp(+j2*pi*f*t), raw SC16_Q11 is never overwritten."""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class AcquisitionRecord:
    step: int
    mode: str
    fc_hz: float
    raw: np.ndarray
    timestamp: int | None = None
    status: int = 0
    actual_count: int | None = None
    origin: str = "SIMULATED"
    metadata: dict = field(default_factory=dict)

    @property
    def iq(self) -> np.ndarray:
        from bladerf_interface import sc16_q11_to_complex

        return sc16_q11_to_complex(self.raw)


@dataclass
class CalibrationResult:
    transfer: np.ndarray
    valid_bins: np.ndarray
    valid: bool
    snr_db: float
    drift_rms_rad: float
    reason: str


@dataclass
class SwwResult:
    rf_hz: np.ndarray
    spectrum: np.ndarray
    reference_spectrum: np.ndarray
    support: np.ndarray
    range_m: np.ndarray
    profile: np.ndarray
    f_ref_hz: float
    metrics: dict = field(default_factory=dict)
    absolute_range_valid: bool = False
    relative_range_valid: bool = False
    phase_state: str = "PHASE_UNVERIFIED"
