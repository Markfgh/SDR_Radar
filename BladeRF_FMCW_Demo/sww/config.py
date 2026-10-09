"""Versioned SI configuration. All defaults describe simulation, never RF permission."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Target:
    range_m: float
    amplitude: float = 0.5
    phase_rad: float = 0.0


@dataclass(frozen=True)
class Config:
    schema_version: int = 1
    seed: int = 20261012
    fs_sps: float = 40e6
    bi_hz: float = 12e6
    bs_hz: float = 12e6
    tp_s: float = 25.6e-6
    capture_samples: int = 2048
    centers_hz: tuple[float, ...] = tuple(2.4e9 + n * 12e6 for n in range(8))
    excluded_bands_hz: tuple[tuple[float, float], ...] = ()
    step_weights: tuple[float, ...] = ()
    motion_reference_point_m: tuple[float, float, float] | None = None
    amplitude: float = 0.05
    pulse_window: str = "rect"
    range_window: str = "rect"
    overlap_normalize: bool = False
    gls_mode: str = "stable"
    regularization: float = 1e-4
    max_inverse_gain: float = 10.0
    spectral_floor: float = 1e-3
    upsample: int = 4
    max_range_m: float = 80.0
    targets: tuple[Target, ...] = (Target(20.0), Target(24.0, 0.45))
    noise_rms: float = 1e-4
    random_phase: bool = True
    rf_ripple: float = 0.15
    rf_delay_s: float = 17e-9
    reference_delay_s: float = 7e-9
    reference_amplitude: float = 0.8
    drift_rad: float = 0.04
    max_cal_drift_rad: float = 0.3  # simulation rejection threshold, NOT a hardware KPI
    min_reference_snr_db: float = 20.0  # simulation setting, NOT gate approval
    topology: str = "switched"

    @property
    def pulse_samples(self) -> int:
        return round(self.fs_sps * self.tp_s)

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported schema_version")
        for key, value in asdict(self).items():
            if isinstance(value, (float, int)) and not math.isfinite(value):
                raise ValueError(f"Non-finite {key}")
        if (
            not 0 < self.bi_hz <= 0.9 * self.fs_sps
            or not 0 < self.bs_hz <= 0.9 * self.fs_sps
        ):
            raise ValueError("Bi and Bs require a 10% digital Nyquist guard")
        if (
            self.tp_s <= 0
            or self.pulse_samples < 32
            or self.capture_samples < 2 * self.pulse_samples
        ):
            raise ValueError("Require >=32 pulse samples and capture >=2 pulse lengths")
        if abs(self.fs_sps * self.tp_s - self.pulse_samples) > 1e-6:
            raise ValueError("Tp*fs must be an integer")
        if not self.centers_hz or any(
            not math.isfinite(f) or f <= 0 for f in self.centers_hz
        ):
            raise ValueError("Positive finite actual RF center frequencies required")
        if len(set(self.centers_hz)) != len(self.centers_hz):
            raise ValueError(
                "Duplicate centers are separate repeats, not stacking steps"
            )
        if self.step_weights and (
            len(self.step_weights) != len(self.centers_hz)
            or any(not math.isfinite(v) or v <= 0 for v in self.step_weights)
        ):
            raise ValueError("Positive finite step weights required, one per center")
        if any(
            len(b) != 2 or not all(math.isfinite(v) for v in b) or b[0] >= b[1]
            for b in self.excluded_bands_hz
        ):
            raise ValueError("Excluded RF bands require ordered finite endpoints")
        if self.motion_reference_point_m is not None and (
            len(self.motion_reference_point_m) != 3
            or not all(math.isfinite(v) for v in self.motion_reference_point_m)
        ):
            raise ValueError("Known Cartesian motion reference required")
        if not 0 < self.amplitude <= 0.05:
            raise ValueError("SWW preparation amplitude must be in (0, 0.05]")
        if self.pulse_window not in (
            "rect",
            "tukey",
            "hamming",
        ) or self.range_window not in ("rect", "tukey", "hamming"):
            raise ValueError("Unknown window")
        if self.gls_mode not in ("none", "paper", "stable") or self.topology not in (
            "switched",
            "summed",
        ):
            raise ValueError("Unknown GLS mode/topology")
        if (
            self.upsample < 1
            or self.upsample > 16
            or int(self.upsample) != self.upsample
        ):
            raise ValueError("upsample must be an integer in 1..16")
        if (
            self.noise_rms < 0
            or not 0 <= self.rf_ripple < 1
            or self.regularization <= 0
        ):
            raise ValueError("Invalid noise, ripple or regularization")
        if (
            not 0 < self.spectral_floor < 1
            or self.max_inverse_gain < 1
            or self.max_cal_drift_rad <= 0
        ):
            raise ValueError("Invalid inverse/calibration limits")
        if (
            self.reference_delay_s < 0
            or self.reference_amplitude <= 0
            or self.rf_delay_s < 0
        ):
            raise ValueError("Invalid reference/channel delay")
        from . import C

        guard_s = (self.capture_samples - self.pulse_samples) / self.fs_sps
        if (
            self.max_range_m <= 0
            or 2 * self.max_range_m / C + self.rf_delay_s >= guard_s
        ):
            raise ValueError("Requested delay support exceeds capture guard")
        for target in self.targets:
            if (
                not all(math.isfinite(v) for v in asdict(target).values())
                or not 0 <= target.range_m <= self.max_range_m
                or target.amplitude < 0
            ):
                raise ValueError("Invalid target")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True).encode()
        ).hexdigest()


def from_dict(data: dict) -> Config:
    data = dict(data)
    if "centers_hz" in data:
        data["centers_hz"] = tuple(data["centers_hz"])
    if "targets" in data:
        data["targets"] = tuple(Target(**t) for t in data["targets"])
    for key in ("step_weights", "motion_reference_point_m"):
        if data.get(key) is not None:
            data[key] = tuple(data[key])
    if "excluded_bands_hz" in data:
        data["excluded_bands_hz"] = tuple(tuple(b) for b in data["excluded_bands_hz"])
    result = Config(**data)
    result.validate()
    return result


def load_config(path: str | Path | None) -> Config:
    if path is None:
        result = Config()
        result.validate()
        return result
    import yaml

    return from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
