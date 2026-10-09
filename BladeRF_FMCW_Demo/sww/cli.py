"""Headless SWW entrypoint; offline and --dry-run never import/open the radio."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import load_config
from .reporting import new_report, save_results
from .storage import CaptureWriter, replay, write_json


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in (
        "simulate",
        "motion-demo",
        "sar-demo",
        "sar-replay",
        "plan",
        "probe",
        "rx-selftest",
        "rx-only",
        "tx-only",
        "loopback",
        "phase-stability",
        "frequency-sweep",
        "stacking",
        "replay",
        "gui",
    ):
        q = sub.add_parser(name)
        q.add_argument("--config")
        q.add_argument("--report")
        if name == "plan":
            q.add_argument("--dry-run", action="store_true", required=True)
        if name in (
            "simulate",
            "stacking",
            "loopback",
            "phase-stability",
            "frequency-sweep",
        ):
            q.add_argument(
                "--backend",
                choices=("simulated",)
                if name == "simulate"
                else ("simulated", "hardware"),
                default="simulated",
            )
        if name in ("loopback", "phase-stability", "frequency-sweep", "tx-only"):
            q.add_argument("--confirm-tx", action="store_true")
            q.add_argument("--permit")
            q.add_argument("--repeats", type=int, default=10)
            q.add_argument("--aba", action="store_true")
        if name in ("rx-selftest", "rx-only"):
            q.add_argument("--blocks", type=int, default=100)
            q.add_argument("--metadata", action="store_true")
        if name in ("replay", "stacking", "gui", "sar-replay"):
            q.add_argument("--capture")
        if name == "simulate":
            q.add_argument("--study", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    path = None
    try:
        config = load_config(args.config)
        if args.command == "gui":
            from .gui import launch

            return launch(args.capture, args.config)
        path = new_report(args.report)
        import yaml

        (path / "config_requested.yaml").write_text(
            yaml.safe_dump(config.to_dict()), encoding="utf-8"
        )
        if args.command == "phase-stability" and len(config.centers_hz) != 1:
            raise ValueError("Phase stability requires a single fixed frequency config")
        if args.command == "sar-demo":
            from .offline_demos import sar_demo

            sar_demo(config, path)
            print(path.resolve())
            return 0
        if args.command == "sar-replay":
            if not args.capture:
                raise ValueError("sar-replay requires --capture")
            from .sar_replay import process

            process(args.capture, path)
            print(path.resolve())
            return 0
        if args.command == "plan":
            write_json(
                path / "plan.json",
                {
                    "origin": "PLAN_ONLY",
                    "tx_enabled": False,
                    "config": config.to_dict(),
                    "sequence": [
                        {
                            "step": i,
                            "fc_hz": f,
                            "modes": ["CAL_PRE", "RADAR", "CAL_POST"],
                        }
                        for i, f in enumerate(config.centers_hz)
                    ],
                    "hardware_state": "BLOCKED_PENDING_PROBE_RF_BUDGET_AND_PHASE_GATE",
                },
            )
        elif args.command == "probe":
            from .hardware_tests import run_probe

            run_probe(path)
        elif args.command in ("rx-selftest", "rx-only"):
            from .hardware_tests import run_rx

            run_rx(config, path, args.blocks, args.metadata)
        elif args.command == "tx-only":
            raise PermissionError(
                "BLOCKED: preparation TX-only has no RX overload monitor. Use explicitly permitted closed-loopback diagnostics."
            )
        elif (
            args.command in ("loopback", "phase-stability", "frequency-sweep")
            and args.backend == "hardware"
        ):
            if args.command == "phase-stability" and len(config.centers_hz) != 1:
                raise ValueError(
                    "Phase stability requires a single fixed frequency config"
                )
            from .hardware_tests import run_loopback

            run_loopback(
                config, path, args.permit, args.confirm_tx, args.repeats, args.aba
            )
        elif (
            args.command == "stacking"
            and args.backend == "hardware"
            and not args.capture
        ):
            raise RuntimeError(
                "BLOCKED: coherent hardware capture requires qualified G1, digital epoch and switch controller; use replay after qualification"
            )
        else:
            from .experiments import compare, contamination_study, suppression_study
            from .simulation import simulate

            if args.command in ("replay", "stacking") and args.capture:
                config, records, manifest = replay(args.capture)
                capture = Path(args.capture)
                if manifest["state"] != "COMPLETED":
                    raise ValueError(
                        "Capture incomplete/aborted; inspect raw diagnostically before stacking"
                    )
                if manifest["origin"] != "SIMULATED":
                    from .stacking import stack

                    result = stack(config, records, calibration=config.topology)
                    save_results(
                        path,
                        config,
                        {"corrected": result},
                        {"processing": result.metrics},
                        capture,
                    )
                    print(path.resolve())
                    return 0
            elif args.command == "replay":
                raise ValueError("replay requires --capture")
            else:
                if args.command == "motion-demo":
                    from .offline_demos import motion_dataset

                    config, records, truth = motion_dataset(config)
                else:
                    records, truth = simulate(config)
                capture = path / "capture"
                from .acquisition import FakeRadioBackend
                from .rf_switch import MockSwitchController
                from .scheduler import Sequencer

                writer = CaptureWriter(capture, config, "SIMULATED")
                records = Sequencer(
                    FakeRadioBackend(records), MockSwitchController(), writer
                ).run(config)
                writer.finish(truth=truth)
            results, metrics = compare(config, records)
            extra = {"contamination": contamination_study(config)}
            if getattr(args, "study", False):
                extra["suppression"] = suppression_study(config)
            if args.command in ("loopback", "phase-stability", "frequency-sweep"):
                extra["repeated_sweeps"] = simulated_repeats(
                    config, path, args.repeats, args.aba
                )
            save_results(path, config, results, metrics, capture, extra)
            if metrics["independent_validation"]["status"] == "FAIL":
                print(path.resolve())
                return 1
        print(path.resolve())
        return 0
    except (Exception, KeyboardInterrupt) as error:
        if path:
            status = (
                "BLOCKED"
                if isinstance(error, (PermissionError, FileNotFoundError))
                or "BLOCKED" in str(error)
                else "FAIL"
            )
            write_json(
                path / "error.json",
                {"type": type(error).__name__, "message": str(error), "status": status},
            )
            write_json(
                path / "gate_results.json",
                [
                    {
                        "test_id": args.command,
                        "status": status,
                        "exception": type(error).__name__,
                        "error": str(error),
                        "raw_capture_path": str(path / "capture"),
                        "next_action": "Inspect evidence; dependent gates stopped",
                    }
                ],
            )
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return (
            2
            if isinstance(error, (PermissionError, FileNotFoundError))
            or "BLOCKED" in str(error)
            else 1
        )


def simulated_repeats(config, path, repeats, aba):
    from .experiments import compare
    from .simulation import simulate

    if not 1 <= repeats <= 100:
        raise ValueError("Repeat budget must be 1..100")
    if aba and len(config.centers_hz) != 2:
        raise ValueError("A-B-A requires two center frequencies")
    rows = []
    for repeat in range(repeats):
        cfg = replace(config, seed=config.seed + repeat)
        records, truth = simulate(cfg)
        folder = Path(path) / "repeats" / f"sweep_{repeat:03d}"
        writer = CaptureWriter(folder, cfg, "SIMULATED")
        for record in records:
            writer.append(record)
        writer.finish(truth=truth)
        _, metrics = compare(cfg, records)
        rows.append(
            {
                "repeat": repeat,
                "phase_residual": metrics["phase_residual_corrected"],
                "capture": str(folder),
            }
        )
        if aba:
            a = replace(
                cfg,
                centers_hz=(cfg.centers_hz[0],),
                step_weights=cfg.step_weights[:1],
                seed=cfg.seed + 10000,
            )
            ret, truth = simulate(a)
            writer = CaptureWriter(
                Path(path) / "repeats" / f"return_A_{repeat:03d}", a, "SIMULATED"
            )
            for record in ret:
                writer.append(record)
            writer.finish(truth=truth)
    return {
        "origin": "SIMULATED",
        "random_pll_phase_each_sweep": config.random_phase,
        "aba": aba,
        "sweeps": rows,
    }


if __name__ == "__main__":
    raise SystemExit(main())
