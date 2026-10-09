"""Reprocess saved synthetic-aperture raw captures with recorded positions."""

import json
from pathlib import Path

import numpy as np

from .sar import backproject
from .stacking import stack
from .storage import replay, write_json


def process(capture_path, report_path):
    source = Path(capture_path).resolve()
    report = Path(report_path)
    geometry = json.loads((source / "sar_geometry.json").read_text(encoding="utf-8"))
    if geometry.get("origin") != "SIMULATED":
        raise RuntimeError(
            "BLOCKED: measured SAR positions and inter-sweep phase qualification required"
        )
    positions = []
    profiles = []
    baseline_range = None
    baseline_fref = None
    for pose in geometry["poses"]:
        folder = (source / pose["capture"]).resolve()
        if not folder.is_relative_to(source):
            raise ValueError("SAR capture path escapes directory")
        config, records, manifest = replay(folder)
        if manifest["state"] != "COMPLETED" or manifest["origin"] != "SIMULATED":
            raise ValueError("Invalid SAR pose capture")
        result = stack(config, records)
        keep = (result.range_m >= 0) & (result.range_m <= config.max_range_m)
        ranges = result.range_m[keep]
        if baseline_range is not None and (
            not np.array_equal(ranges, baseline_range)
            or baseline_fref != result.f_ref_hz
        ):
            raise ValueError("Inconsistent SAR grids or carrier phase convention")
        baseline_range = ranges
        baseline_fref = result.f_ref_hz
        positions.append(pose["position_m"])
        profiles.append(result.profile[keep])
    xx, yy = np.meshgrid(np.linspace(-1, 1, 81), np.linspace(18, 22, 101))
    pixels = np.stack((xx, yy, np.zeros_like(xx)), axis=-1)
    focused = backproject(
        profiles, ranges, positions, pixels, baseline_fref, phase_verified=True
    )
    incoherent = backproject(
        profiles, ranges, positions, pixels, baseline_fref, coherent=False
    )
    np.savez_compressed(
        report / "sar.npz",
        coherent=focused,
        incoherent=incoherent,
        x_m=xx,
        y_m=yy,
        profiles=profiles,
        range_m=ranges,
        positions_m=positions,
        f_ref_hz=baseline_fref,
    )
    write_json(
        report / "manifest.json",
        {"origin": "SIMULATED", "source_capture": str(source), "poses": len(positions)},
    )
    write_json(
        report / "gate_results.json",
        [
            {
                "test_id": "SAR_RAW_REPLAY",
                "status": "PASS",
                "evidence": "All raw SHA256 and source hashes verified",
            },
            {
                "test_id": "G10_HARDWARE",
                "status": "BLOCKED",
                "reason": "No measured phase/position qualification",
            },
        ],
    )
