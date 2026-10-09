"""Executable motion and SAR demonstrations with raw I/Q and explicit pose truth."""

from dataclasses import replace
from pathlib import Path

import numpy as np

from .config import Target
from .sar import backproject
from .simulation import simulate
from .stacking import stack
from .storage import CaptureWriter, write_json


def motion_dataset(config):
    rng = np.random.default_rng(config.seed)
    positions = rng.normal(0, 0.15, (len(config.centers_hz), 3))
    positions[0] = 0
    config = replace(config, motion_reference_point_m=(0, config.targets[0].range_m, 0))
    records, truth = simulate(config, positions_m=positions)
    return config, records, truth


def sar_demo(config, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    profiles = []
    capture_root = path / "capture"
    capture_root.mkdir()
    positions = np.column_stack((np.linspace(-1, 1, 31), np.zeros(31), np.ones(31)))
    target = np.array([0.2, 20, 0])
    config = replace(config, targets=(Target(20, 0.5),), upsample=16)
    geometries = []
    for index, position in enumerate(positions):
        slant = float(np.linalg.norm(target - position))
        cfg = replace(config, targets=(Target(slant, 0.5),), seed=config.seed + index)
        rec, truth = simulate(cfg)
        writer = CaptureWriter(capture_root / f"pose_{index:03d}", cfg, "SIMULATED")
        for r in rec:
            r.metadata.update(position_m=position.tolist(), pose_id=index)
            writer.append(r)
        truth["position_m"] = position.tolist()
        writer.finish(truth=truth)
        result = stack(cfg, rec)
        keep = (result.range_m >= 0) & (result.range_m <= config.max_range_m)
        profiles.append(result.profile[keep])
        geometries.append(
            {
                "pose_id": index,
                "position_m": position.tolist(),
                "capture": f"pose_{index:03d}",
            }
        )
    # Profiles retain target carrier phase; range envelopes are interpolated.
    profiles = np.array(profiles)
    ranges = result.range_m[keep]
    xx, yy = np.meshgrid(np.linspace(-1, 1, 81), np.linspace(18, 22, 101))
    pixels = np.stack((xx, yy, np.zeros_like(xx)), axis=-1)
    focused = backproject(
        profiles, ranges, positions, pixels, result.f_ref_hz, phase_verified=True
    )
    incoherent = backproject(
        profiles, ranges, positions, pixels, result.f_ref_hz, coherent=False
    )
    peak = np.unravel_index(np.argmax(abs(focused)), focused.shape)
    write_json(
        capture_root / "sar_geometry.json",
        {"origin": "SIMULATED", "target_m": target.tolist(), "poses": geometries},
    )
    np.savez_compressed(
        path / "sar.npz",
        coherent=focused,
        incoherent=incoherent,
        x_m=xx,
        y_m=yy,
        positions_m=positions,
        profiles=profiles,
        range_m=ranges,
        f_ref_hz=result.f_ref_hz,
    )
    metrics = {
        "origin": "SIMULATED",
        "peak_m": pixels[peak].tolist(),
        "target_m": target.tolist(),
        "peak_error_m": float(np.linalg.norm(pixels[peak] - target)),
        "hardware_state": "BLOCKED_POSITION_AND_INTERSWEEP_PHASE",
    }
    write_json(path / "sar_metrics.json", metrics)
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), layout="constrained")
    for ax, data, title in zip(
        axes,
        (abs(focused), incoherent),
        ("Coherent backprojection", "Incoherent magnitude sum"),
        strict=True,
    ):
        im = ax.imshow(data, extent=[-1, 1, 18, 22], origin="lower", aspect="auto")
        ax.plot(0.2, 20, "rx")
        ax.set(
            xlabel="Cross range (m)",
            ylabel="Ground range (m)",
            title="SIMULATED: " + title,
        )
        fig.colorbar(im, ax=ax)
    (path / "plots").mkdir(exist_ok=True)
    fig.savefig(path / "plots" / "sar.png", dpi=140)
    plt.close(fig)
    write_json(
        path / "gate_results.json",
        [
            {
                "test_id": "SAR_SIMULATION",
                "status": "PASS" if metrics["peak_error_m"] < 0.1 else "FAIL",
                "observed": metrics,
            },
            {
                "test_id": "G10_HARDWARE",
                "status": "BLOCKED",
                "reason": "No measured positions or inter-sweep phase data",
            },
        ],
    )
    return metrics
