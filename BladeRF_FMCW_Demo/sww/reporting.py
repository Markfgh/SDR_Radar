"""Self-contained JSON/Markdown/PNG/NPZ reports. Captures remain local."""

import platform
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import __version__
from .metrics import db
from .storage import write_json


def new_report(path=None):
    path = Path(
        path
        or (
            "reports/run_"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            + "_"
            + uuid.uuid4().hex[:6]
        )
    )
    path.mkdir(parents=True, exist_ok=False)
    write_json(
        path / "environment.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "software": __version__,
            "numpy": np.__version__,
            "origin": "OFFLINE_UNLESS_EXPLICIT_HARDWARE_COMMAND",
        },
    )
    return path


def save_results(path, config, results, metrics, capture_path, extra=None):
    import yaml

    path = Path(path)
    (path / "plots").mkdir(exist_ok=True)
    (path / "config_applied.yaml").write_text(
        yaml.safe_dump(config.to_dict()), encoding="utf-8"
    )
    for name, r in results.items():
        np.savez_compressed(
            path / (name + ".npz"),
            rf_hz=r.rf_hz,
            spectrum=r.spectrum,
            reference_spectrum=r.reference_spectrum,
            support=r.support,
            range_m=r.range_m,
            profile=r.profile,
            f_ref_hz=r.f_ref_hz,
            absolute_range_valid=r.absolute_range_valid,
            relative_range_valid=r.relative_range_valid,
            phase_state=r.phase_state,
        )
    write_json(path / "metrics.json", metrics)
    if extra:
        write_json(path / "experiments.json", extra)
    simulated = all(r.metrics["origin"] == ["SIMULATED"] for r in results.values())
    gates = [
        {
            "test_id": "OFFLINE_PROCESSING",
            "status": "PASS",
            "evidence": "metrics.json; raw capture hashes verified",
            "origin": "SIMULATED" if simulated else "HARDWARE_OFFLINE",
        },
        {
            "test_id": "G1_HARDWARE_PHASE",
            "status": "BLOCKED",
            "expected": "Independent known-delay echo validation and approved KPIs",
            "observed": "No qualified hardware phase evidence",
            "next_action": "MONDAY_TEST_PLAN.md",
        },
    ]
    if "independent_validation" in metrics:
        gates.append(
            {"test_id": "SIM_INDEPENDENT_PHASE", **metrics["independent_validation"]}
        )
    if "corrected" in metrics and "two_targets" in metrics["corrected"]:
        gates.append(
            {
                "test_id": "SIM_TWO_TARGETS",
                "status": "PASS"
                if metrics["corrected"]["two_targets"]["resolved"]
                and not metrics["single"]["two_targets"]["resolved"]
                else "FAIL",
                "expected": "Corrected resolves two known targets; single chirp does not",
                "observed": metrics["corrected"]["two_targets"],
            }
        )
    write_json(path / "gate_results.json", gates)
    write_json(
        path / "manifest.json",
        {
            "capture_path": str(Path(capture_path).resolve()),
            "config_hash": config.digest,
            "origin": "SIMULATED" if simulated else "HARDWARE_OFFLINE",
            "command": sys.argv,
        },
    )
    import json

    previous = []
    for folder in path.parent.glob("run_*"):
        if (
            folder.resolve() == path.resolve()
            or not (folder / "metrics.json").is_file()
            or not (folder / "manifest.json").is_file()
        ):
            continue
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("config_hash") == config.digest and manifest.get("origin") == (
            "SIMULATED" if simulated else "HARDWARE_OFFLINE"
        ):
            previous.append(folder)
    if previous:
        prior = max(previous, key=lambda p: (p / "metrics.json").stat().st_mtime_ns)
        old = json.loads((prior / "metrics.json").read_text(encoding="utf-8"))
        write_json(
            path / "comparison_previous.json",
            {
                "previous_report": str(prior),
                "same_config": True,
                "previous_metrics": old,
                "current_metrics": metrics,
            },
        )
    else:
        write_json(
            path / "comparison_previous.json",
            {
                "status": "BLOCKED",
                "reason": "No previous completed run with same configuration and origin",
            },
        )
    lines = [
        "# SWW diagnostic report",
        "",
        "Origin: **SIMULATED**"
        if simulated
        else "Origin: HARDWARE_OFFLINE; phase unverified",
        "",
        "No hardware gate is approved by this report.",
        f"Capture: `{Path(capture_path).resolve()}`",
        f"Config SHA256: `{config.digest}`",
        "",
        "Reproduce from BladeRF_FMCW_Demo:",
        f'`python -m sww.cli replay --capture "{Path(capture_path).resolve()}"`',
        "",
        "| Profile | Two targets resolved | PSF -6dB width (m) | PSL (dB) |",
        "|---|---|---|---|",
    ]
    for name, data in metrics.items():
        if isinstance(data, dict) and "psf" in data:
            lines.append(
                f"| {name} | {data.get('two_targets', {}).get('resolved', 'N/A')} | {data['psf']['width_6db_m']} | {data['psf']['psl_db']} |"
            )
    lines.extend(
        [
            "",
            f"Corrected spectral phase residual: `{metrics.get('phase_residual_corrected')}`",
            "",
            "PSL/PGL are PSF metrics; two-target scene peaks are reported separately. All widths are amplitude -6dB.",
        ]
    )
    (path / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    plot_results(path, config, results)


def plot_results(path, config, results):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), layout="constrained")
    for name, result in results.items():
        keep = (result.range_m >= 0) & (result.range_m <= config.max_range_m)
        axes[0].plot(result.range_m[keep], db(result.profile)[keep], label=name)
    for t in config.targets:
        axes[0].axvline(t.range_m, color="gray", linestyle=":")
    axes[0].set(
        xlabel="Apparent monostatic range (m)",
        ylabel="Amplitude (dB relative peak)",
        ylim=(-60, 3),
        title="SIMULATED SWW comparison"
        if results["corrected"].metrics["origin"] == ["SIMULATED"]
        else "Phase-unverified offline profile",
    )
    r = results["corrected"]
    use = np.abs(r.rf_hz - r.f_ref_hz) <= r.metrics["synthetic_span_hz"] * 0.55
    axes[1].plot((r.rf_hz[use] - r.f_ref_hz) / 1e6, np.abs(r.spectrum[use]))
    axes[1].set(
        xlabel="RF offset from f_ref (MHz)",
        ylabel="Complex spectrum magnitude",
        title="Valid synthetic spectrum; gaps remain gaps",
    )
    axes[0].legend()
    axes[0].grid()
    axes[1].grid()
    fig.savefig(Path(path) / "plots" / "comparison.png", dpi=140)
    plt.close(fig)
