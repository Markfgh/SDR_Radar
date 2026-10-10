"""Monday acceptance runner. No simulated backend and no implicit TX authorization.

python -m sww.bench --session captures/monday/session.json --run captures/monday/run --through rx
The plan and bench result are distinct: exit 0 through RX does NOT approve phase.
"""

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import C
from .bench_analysis import (
    align_reference,
    extract_pulses,
    fit_independent_echo,
    measured_profiles,
    profile_metrics,
    raw_phase_stats,
    require,
    validate_rows,
)
from .config import from_dict
from .storage import replay, sha, source_hash, write_json

STAGES = (
    "offline",
    "probe",
    "rx",
    "reference_pre",
    "fixed",
    "aba",
    "sweep",
    "reference_post",
    "validate",
    "stack",
)
TX_STAGES = STAGES[3:8]
CRITERIA = (
    "detection_peak_fraction",
    "min_detection_confidence",
    "max_reference_shape_error",
    "max_gram_condition",
    "min_reference_fit_snr_db",
    "min_echo_fit_snr_db",
    "max_model_residual_fraction",
    "max_phase_rms_rad",
    "max_phase_p95_rad",
    "max_delay_error_s",
    "max_projection_phase_bound_rad",
    "min_coverage_fraction",
    "max_range_error_m",
    "max_width_ratio",
    "min_repeats",
)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_session(path):
    session = json.loads(Path(path).read_text(encoding="utf-8"))
    require(
        session.get("schema_version") == 1 and bool(session.get("session_id")),
        "Invalid session schema/id",
    )
    config = from_dict(session["config"])
    require(
        not config.targets and config.motion_reference_point_m is None,
        "Bench config must not contain synthetic targets or motion",
    )
    require(len(config.centers_hz) >= 2, "At least two actual RF centers required")
    require(
        config.pulse_samples <= 8192 and config.capture_samples <= 32768,
        "Bench capture size budget exceeded",
    )
    for name in ("fixed_repeats", "aba_cycles", "sweep_repeats", "reference_repeats"):
        require(
            type(session.get(name)) is int and 2 <= session[name] <= 100,
            f"{name} must be 2..100",
        )
    return session, config, fingerprint(session)


def qualification_inputs(session, config):
    criteria = session["criteria"]
    for key in CRITERIA:
        value = criteria.get(key)
        require(
            type(value) in (int, float) and math.isfinite(value) and value > 0,
            f"BLOCKED: approved numeric criterion missing/invalid: {key}",
        )
    for key in (
        "detection_peak_fraction",
        "min_detection_confidence",
        "max_reference_shape_error",
        "max_model_residual_fraction",
        "min_coverage_fraction",
        "max_width_ratio",
    ):
        require(criteria[key] < 1, f"BLOCKED: {key} must be <1")
    require(
        criteria["max_gram_condition"] > 1
        and criteria["max_phase_p95_rad"] >= criteria["max_phase_rms_rad"],
        "Invalid condition/phase criteria",
    )
    require(
        criteria["max_projection_phase_bound_rad"] < np.pi / 2,
        "Invalid contamination phase budget",
    )
    require(
        type(criteria["min_repeats"]) is int and criteria["min_repeats"] >= 2,
        "min_repeats must be integer >=2",
    )
    require(
        min(session["fixed_repeats"], session["aba_cycles"], session["sweep_repeats"])
        >= criteria["min_repeats"],
        "Insufficient repeat budget",
    )
    require(
        bool(session.get("criteria_approved_by"))
        and bool(session.get("criteria_evidence")),
        "BLOCKED: criteria approval and error-budget evidence missing",
    )
    known = session["independent_path"]
    require(
        known.get("independently_measured") is True and bool(known.get("evidence")),
        "BLOCKED: independent delay/path evidence missing",
    )
    for key in ("relative_delay_s", "delay_uncertainty_s"):
        require(
            type(known.get(key)) in (int, float)
            and math.isfinite(known[key])
            and known[key] > 0,
            f"BLOCKED: independent {key} missing",
        )
    require(
        known["relative_delay_s"] + known["delay_uncertainty_s"]
        < 2 * config.max_range_m / C,
        "Known path outside blind delay search",
    )
    require(
        known["delay_uncertainty_s"] < criteria["max_delay_error_s"],
        "Independent delay uncertainty exceeds allowed delay error",
    )
    span = max(config.centers_hz) - min(config.centers_hz) + config.bs_hz
    require(
        2 * np.pi * span * known["delay_uncertainty_s"]
        <= criteria["max_phase_rms_rad"],
        "Independent delay uncertainty consumes phase budget across sweep",
    )
    require(
        known.get("stationary_single_path") is True,
        "BLOCKED: this qualification requires one stationary independent path",
    )
    return criteria, known


def visits(session, config, stage):
    if stage == "fixed":
        return [config.centers_hz[0]], session["fixed_repeats"]
    if stage == "aba":
        a, b = config.centers_hz[:2]
        return [f for _ in range(session["aba_cycles"]) for f in (a, b, a)], 1
    if stage in ("reference_pre", "reference_post"):
        return list(config.centers_hz), session["reference_repeats"]
    return list(config.centers_hz), session["sweep_repeats"]


def capture_pulses(run, stage, session, config, session_hash):
    cfg, records, manifest = replay(Path(run) / stage / "capture")
    require(
        manifest["origin"] == "HARDWARE" and manifest["state"] == "COMPLETED",
        "Only completed REAL hardware captures accepted; no simulation fallback",
    )
    require(
        cfg.digest == config.digest
        and manifest["environment"]["session_sha256"] == session_hash,
        "Capture/session configuration mismatch",
    )
    require(manifest["environment"]["stage"] == stage, "Wrong acquisition stage")
    frequencies, repeats = visits(session, config, stage)
    output = []
    for visit, fc in enumerate(frequencies):
        group = [r for r in records if r.step == visit]
        require(
            all(r.origin == "HARDWARE" and r.fc_hz == fc for r in group),
            "Mixed origin/frequency",
        )
        pulses = extract_pulses(config, group, repeats, session["criteria"])
        for p in pulses:
            p.update(fc_hz=fc, visit=visit)
            output.append(p)
    require(
        set(r.step for r in records) == set(range(len(frequencies))),
        "Unexpected/missing frequency visits",
    )
    return output


def references_for(run, session, config, session_hash, stage="reference_pre"):
    pulses = capture_pulses(run, stage, session, config, session_hash)
    refs = {}
    stats = []
    for fc in config.centers_hz:
        group = [p for p in pulses if p["fc_hz"] == fc]
        reference, errors = align_reference(group, session["criteria"])
        refs[fc] = reference
        stats.append(
            {"fc_hz": fc, "shape_errors": errors, "raw_phase": raw_phase_stats(group)}
        )
    return refs, stats


def save_reference_transfer(folder, config, references):
    from scipy.fft import next_fast_len

    from .waveforms import chirp

    n = next_fast_len(config.capture_samples + config.pulse_samples - 1)
    x = np.fft.fft(chirp(config), n)
    baseband = np.fft.fftfreq(n, 1 / config.fs_sps)
    mask = (np.abs(baseband) < config.bs_hz / 2) & (
        np.abs(x) > config.spectral_floor * np.max(np.abs(x))
    )
    h = np.zeros((len(references), n), complex)
    measured = []
    for step, fc in enumerate(config.centers_hz):
        measured.append(references[fc])
        r = np.fft.fft(references[fc], n)
        h[step, mask] = r[mask] / x[mask]
    np.savez_compressed(
        folder / "measured_reference_transfer.npz",
        centers_hz=config.centers_hz,
        baseband_hz=baseband,
        support=mask,
        transfer=h,
        reference_iq=np.array(measured),
        origin="HARDWARE",
        absolute_phase_qualified=False,
    )
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(9, 7), layout="constrained")
    order = np.argsort(baseband[mask])
    for step, fc in enumerate(config.centers_hz):
        label = f"{fc / 1e6:g} MHz"
        axes[0].plot(
            baseband[mask][order] / 1e6,
            20 * np.log10(np.maximum(abs(h[step, mask][order]), 1e-15)),
            label=label,
        )
        axes[1].plot(
            baseband[mask][order] / 1e6, np.unwrap(np.angle(h[step, mask][order]))
        )
    axes[0].set(
        ylabel="Measured |R/X| (dB)",
        title="Reference transfer; arbitrary per-tune phase is NOT coherence proof",
    )
    axes[0].legend()
    axes[1].set(xlabel="Baseband (MHz)", ylabel="Reference transfer phase (rad)")
    fig.savefig(folder / "reference_transfer.png", dpi=130)
    plt.close(fig)


def evaluate_stage(run, stage, session, config, session_hash, phase_origin=None):
    criteria, known = qualification_inputs(session, config)
    references, _ = references_for(run, session, config, session_hash)
    pulses = capture_pulses(run, stage, session, config, session_hash)
    rows = []
    fits = []
    for p in pulses:
        fit = fit_independent_echo(p["iq"], references[p["fc_hz"]], config, criteria)
        fits.append(fit)
        row = {
            k: v for k, v in fit.items() if k not in ("coefficient", "echo_coefficient")
        }
        row.update(
            fc_hz=p["fc_hz"],
            visit=p["visit"],
            timestamp=p["timestamp"],
            coefficient=[fit["coefficient"].real, fit["coefficient"].imag],
            raw_reference_phase_rad=p["raw_reference_phase_rad"],
        )
        rows.append(row)
    write_json(Path(run) / stage / "observations.json", rows)
    metrics = validate_rows(rows, known, criteria, phase_origin, enforce=False)
    metrics["raw_phase_by_visit"] = [
        {
            "visit": v,
            "fc_hz": next(p["fc_hz"] for p in pulses if p["visit"] == v),
            **raw_phase_stats([p for p in pulses if p["visit"] == v]),
        }
        for v in sorted(set(p["visit"] for p in pulses))
    ]
    if stage == "aba":
        raw_a = [p for p in pulses if p["fc_hz"] == config.centers_hz[0]]
        metrics["raw_A_return_statistics"] = raw_phase_stats(raw_a)
        corrected = metrics["phase_residual_rad"]
        metrics["corrected_A_return_delta_rad"] = [
            float(np.angle(np.exp(1j * (corrected[i + 2] - corrected[i]))))
            for i in range(0, len(corrected), 3)
        ]
        if not (
            max(abs(v) for v in metrics["corrected_A_return_delta_rad"])
            <= criteria["max_phase_p95_rad"]
        ):
            metrics["failures"].append("A-B-A independent corrected return phase FAIL")
            metrics["status"] = "FAIL"
    write_json(Path(run) / stage / "phase_metrics.json", metrics)
    plot_phase(Path(run) / stage, rows, metrics)
    require(not metrics["failures"], "; ".join(metrics["failures"]))
    return metrics, pulses, references, fits


def plot_phase(path, rows, metrics):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(9, 9), layout="constrained")
    axes[0].plot([r["raw_reference_phase_rad"] for r in rows], ".-")
    axes[0].set(
        ylabel="Raw reference phase (rad)",
        title="HARDWARE I/Q; raw PLL phase is diagnostic",
    )
    axes[1].plot(metrics["phase_residual_rad"], ".-")
    axes[1].set(ylabel="Independent echo residual (rad)")
    axes[2].plot([r["gram_condition"] for r in rows], label="Gram condition")
    axes[2].plot(
        [r["projection_contamination_epsilon"] for r in rows],
        label="Projection epsilon",
    )
    axes[2].set(xlabel="Acquisition", ylabel="Separation diagnostics")
    axes[2].legend()
    fig.savefig(path / "phase_and_separation.png", dpi=130)
    plt.close(fig)


def validate_all(run, session, config, session_hash):
    criteria, _ = qualification_inputs(session, config)
    pre, pre_stats = references_for(run, session, config, session_hash)
    post, post_stats = references_for(
        run, session, config, session_hash, "reference_post"
    )
    drift = []
    for fc in config.centers_hz:
        coefficient = np.vdot(pre[fc], post[fc]) / np.vdot(pre[fc], pre[fc])
        error = float(
            np.linalg.norm(post[fc] / coefficient - pre[fc]) / np.linalg.norm(pre[fc])
        )
        drift.append(
            {
                "fc_hz": fc,
                "shape_change": error,
                "scalar_phase_change_rad": float(np.angle(coefficient)),
            }
        )
        require(
            error <= criteria["max_reference_shape_error"],
            "Pre/post reference transfer shape drift FAIL",
        )
    fixed, _, _, _ = evaluate_stage(run, "fixed", session, config, session_hash)
    origin = fixed["phase_origin_rad"]
    aba, _, _, _ = evaluate_stage(run, "aba", session, config, session_hash, origin)
    sweep, pulses, references, fits = evaluate_stage(
        run, "sweep", session, config, session_hash, origin
    )
    require(
        max(f["projection_phase_bound_rad"] for f in fits)
        <= criteria["max_projection_phase_bound_rad"],
        "Summed reference contamination budget FAIL",
    )
    metrics = {
        "status": "PASS",
        "scope": "STATIONARY_SINGLE_KNOWN_PATH_REFERENCE_RELATIVE_ONLY",
        "absolute_tx_rx_epoch_verified": False,
        "reference_pre": pre_stats,
        "reference_post": post_stats,
        "reference_drift": drift,
        "fixed": fixed,
        "aba": aba,
        "sweep": sweep,
    }
    return metrics, pulses, references, fits


def plot_profiles(folder, config, known, wide, single):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .metrics import db

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), layout="constrained")
    for name, result in (("Single", single), ("SWW", wide)):
        keep = (result.range_m >= 0) & (result.range_m <= config.max_range_m)
        axes[0].plot(result.range_m[keep], db(result.profile)[keep], label=name)
    axes[0].axvline(C * known["relative_delay_s"] / 2, color="gray", linestyle=":")
    axes[0].set(
        xlabel="Reference-relative equivalent monostatic range (m)",
        ylabel="Amplitude (dB)",
        title="Measured HARDWARE I/Q only; one known isolated path",
    )
    axes[0].legend()
    axes[1].plot(
        wide.rf_hz[wide.support] / 1e6, np.angle(wide.spectrum[wide.support]), "."
    )
    axes[1].set(xlabel="RF (MHz)", ylabel="Measured spectrum phase (rad)")
    fig.savefig(folder / "single_vs_synthetic.png", dpi=140)
    plt.close(fig)


def stacking_result(run, session, config, session_hash):
    # Recalculate qualification from HASH-VERIFIED RAW on every stacking call;
    # a manually edited PASS JSON or a normalized reference cannot unlock it.
    qualification, pulses, refs, fits = validate_all(run, session, config, session_hash)
    criteria, known = qualification_inputs(session, config)
    folder = Path(run) / "stack"
    comparisons = []
    for repeat in range(session["sweep_repeats"]):
        selected = [
            i
            for fc in config.centers_hz
            for i, p in enumerate(pulses)
            if p["fc_hz"] == fc and sum(q["fc_hz"] == fc for q in pulses[:i]) == repeat
        ]
        require(len(selected) == len(config.centers_hz), "Incomplete measured sweep")
        wide, single, quantization = measured_profiles(
            config, [pulses[i] for i in selected], refs, [fits[i] for i in selected]
        )
        wide_metrics = profile_metrics(
            wide, known["relative_delay_s"], config.max_range_m
        )
        single_metrics = profile_metrics(
            single, known["relative_delay_s"], config.max_range_m
        )
        metrics = {
            "repeat": repeat,
            "wide": wide_metrics,
            "single": single_metrics,
            "coverage": wide.metrics["coverage_fraction"],
            "crop_requantization_error": quantization,
            "calibration": wide.metrics["calibration"],
        }
        comparisons.append(metrics)
        # Persist measured output before any KPI can reject it.
        for name, result in (("wide", wide), ("single", single)):
            np.savez_compressed(
                folder / f"{name}_{repeat:03d}.npz",
                rf_hz=result.rf_hz,
                spectrum=result.spectrum,
                support=result.support,
                range_m=result.range_m,
                profile=result.profile,
                origin="HARDWARE",
                absolute_epoch_verified=False,
            )
        write_json(folder / "resolution_metrics.json", comparisons)
        plot_profiles(folder, config, known, wide, single)
        require(
            wide.metrics["coverage_fraction"] >= criteria["min_coverage_fraction"],
            "Measured spectral coverage FAIL",
        )
        require(
            abs(wide_metrics["range_error_m"]) <= criteria["max_range_error_m"],
            "Independent measured stacked range FAIL",
        )
        ws, ww = single_metrics["width_6db_m"], wide_metrics["width_6db_m"]
        require(
            ws is not None
            and ww is not None
            and ww / ws <= criteria["max_width_ratio"],
            "Measured single/SWW width improvement FAIL or echo too close to reference",
        )
    write_json(folder / "qualification.json", qualification)
    return {
        "status": "PASS",
        "scope": qualification["scope"],
        "sweeps": len(comparisons),
        "two_target_resolution": "NOT_TESTED",
        "absolute_range": "UNVERIFIED",
    }


def run_sequence(
    session,
    config,
    session_hash,
    run,
    through,
    authorized,
    permits,
    confirm_tx,
    resume=False,
    confirm=input,
):
    """Dependency order is fixed; no skip-gates or continue-on-error option."""
    run = Path(run)
    state_file = run / "sequence.json"
    if resume:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        require(
            state["session_sha256"] == session_hash
            and state["source_hash"] == source_hash(),
            "Resume session/code hash mismatch; use a new run",
        )
    else:
        run.mkdir(parents=True, exist_ok=False)
        state = {
            "session_sha256": session_hash,
            "source_hash": source_hash(),
            "origin": "HARDWARE_BENCH",
            "hardware_phase": "NOT_VERIFIED",
            "stages": [],
        }
        write_json(run / "session.json", session)
    for stage in STAGES[: STAGES.index(through) + 1]:
        folder = run / stage
        try:
            existing = next((r for r in state["stages"] if r["stage"] == stage), None)
            if existing and existing["status"] == "PASS":
                # Raw payloads are re-verified when consumed; status alone never
                # unlocks the final qualification. Verify receipts for other files.
                for name, digest in existing.get("files_sha256", {}).items():
                    require(
                        sha(run / name) == digest, "Completed stage evidence changed"
                    )
                continue
            if stage in TX_STAGES:
                qualification_inputs(session, config)
                if stage not in authorized or not confirm_tx or not permits:
                    raise PermissionError(
                        f"BLOCKED: {stage} needs --confirm-tx --authorize-tx {stage} --permits and operator connection confirmation"
                    )
                from .bench_acquisition import capture_stage, load_permit

                permit = load_permit(
                    Path(permits) / (stage + ".json"), session_hash, stage, config
                )
                token = f"TX {session['session_id']} {stage}"
                layout = (
                    "echo RX branch terminated (REFERENCE ONLY)"
                    if stage.startswith("reference_")
                    else "reference + independent stationary known path on RX"
                )
                if (
                    confirm(
                        f"TX OFF. Check {layout}; analog levels and antennas per permit. Type '{token}': "
                    )
                    != token
                ):
                    raise PermissionError(
                        "Operator did not authorize this physical connection and TX stage"
                    )
            # A failed stage directory is never reused: preserve partial IQ.
            folder.mkdir(exist_ok=False)
            if stage == "offline":
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/validate_offline.py",
                        "--report",
                        str(folder.resolve()),
                    ],
                    check=False,
                )
                require(result.returncode == 0, "Offline regression suite FAIL")
                detail = {"suite": "tests + tests_sww", "hardware_phase": "NOT_TESTED"}
            elif stage == "probe":
                from .hardware_tests import run_probe

                detail = run_probe(folder)
            elif stage == "rx":
                from .hardware_tests import run_rx

                detail = run_rx(config, folder, session["rx_blocks"], metadata=True)
            elif stage in TX_STAGES:
                frequencies, repeats = visits(session, config, stage)
                detail = capture_stage(
                    config, folder, frequencies, repeats, permit, session_hash, stage
                )
                if stage.startswith("reference_"):
                    reference_iq, stats = references_for(
                        run, session, config, session_hash, stage
                    )
                    write_json(folder / "reference_metrics.json", stats)
                    save_reference_transfer(folder, config, reference_iq)
                else:
                    origin = None
                    if stage != "fixed":
                        fixed, _, _, _ = evaluate_stage(
                            run, "fixed", session, config, session_hash
                        )
                        origin = fixed["phase_origin_rad"]
                    metrics, _, _, _ = evaluate_stage(
                        run, stage, session, config, session_hash, origin
                    )
                    detail["independent_phase"] = metrics
            elif stage == "validate":
                detail, _, _, _ = validate_all(run, session, config, session_hash)
            else:
                detail = stacking_result(run, session, config, session_hash)
            receipts = {
                str(p.relative_to(run)): sha(p)
                for p in folder.rglob("*")
                if p.is_file()
            }
            entry = {
                "stage": stage,
                "status": "PASS",
                "detail": detail,
                "files_sha256": receipts,
            }
            state["stages"] = [r for r in state["stages"] if r["stage"] != stage] + [
                entry
            ]
            if stage in ("validate", "stack"):
                state["hardware_phase"] = (
                    "PASS_STATIONARY_KNOWN_PATH_REFERENCE_RELATIVE_ONLY"
                )
            state["status"] = "COMPLETED_REQUESTED_STAGES"
            write_json(state_file, state)
            print(f"PASS {stage}: {folder.resolve()}")
        except BaseException as error:
            blocked = isinstance(
                error, (PermissionError, FileNotFoundError)
            ) or "BLOCKED" in str(error)
            state["stages"] = [r for r in state["stages"] if r["stage"] != stage] + [
                {
                    "stage": stage,
                    "status": "BLOCKED" if blocked else "FAIL",
                    "error": str(error),
                }
            ]
            state["status"] = "STOPPED"
            write_json(state_file, state)
            print(f"STOP {stage}: {error}", file=sys.stderr)
            return 2 if blocked else 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--run")
    parser.add_argument("--through", choices=STAGES, default="rx")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--confirm-tx", action="store_true")
    parser.add_argument("--authorize-tx", nargs="+", choices=TX_STAGES, default=[])
    parser.add_argument("--permits")
    args = parser.parse_args(argv)
    try:
        session, config, digest = load_session(args.session)
        if args.dry_run:
            try:
                qualification_inputs(session, config)
                qualification = "INPUTS_COMPLETE_NOT_HARDWARE_VERIFIED"
            except RuntimeError as error:
                qualification = str(error)
            print(
                json.dumps(
                    {
                        "session_sha256": digest,
                        "source_hash": source_hash(),
                        "selected_stages": list(
                            STAGES[: STAGES.index(args.through) + 1]
                        ),
                        "tx_enabled": False,
                        "radio_opened": False,
                        "tx_authorization_is_separate": True,
                        "qualification_inputs": qualification,
                    },
                    indent=2,
                )
            )
            return 0
        if not args.run:
            parser.error("--run required except for --dry-run")
        return run_sequence(
            session,
            config,
            digest,
            args.run,
            args.through,
            args.authorize_tx,
            args.permits,
            args.confirm_tx,
            args.resume,
        )
    except (Exception, KeyboardInterrupt) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return (
            2
            if isinstance(error, (PermissionError, FileNotFoundError))
            or "BLOCKED" in str(error)
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
