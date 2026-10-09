"""Automated preparation: tests, simulator, replay, motion, SAR and exact comparisons.

Never opens a radio. Use --report with a NEW output directory.
"""

import argparse
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report")
    args = parser.parse_args()
    output = Path(
        args.report
        or (
            ROOT
            / "reports"
            / (
                "run_preparation_"
                + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
                + "_"
                + uuid.uuid4().hex[:6]
            )
        )
    ).resolve()
    output.mkdir(parents=True, exist_ok=False)
    logs = []

    def run(arguments):
        command = [sys.executable, *arguments]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        logs.append(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        (output / "commands.json").write_text(
            json.dumps(logs, indent=2), encoding="utf-8"
        )
        if result.returncode:
            raise RuntimeError(
                f"Offline preparation failed: {arguments}: {result.stderr}"
            )

    def cli(command, destination, *arguments):
        run(
            [
                "-m",
                "sww.cli",
                command,
                "--report",
                str(output / destination),
                *arguments,
            ]
        )

    run(["scripts/validate_offline.py", "--report", str(output / "tests")])
    cli("simulate", "simulation", "--config", "configs/simulator.yaml", "--study")
    cli("replay", "replay", "--capture", str(output / "simulation" / "capture"))
    cli("motion-demo", "motion", "--config", "configs/simulator.yaml")
    cli("replay", "motion_replay", "--capture", str(output / "motion" / "capture"))
    cli("sar-demo", "sar", "--config", "configs/simulator.yaml")
    cli("sar-replay", "sar_replay", "--capture", str(output / "sar" / "capture"))
    comparisons = []
    for source, target, names in (
        ("simulation", "replay", ("corrected", "uncorrected", "ideal", "single")),
        ("motion", "motion_replay", ("corrected", "uncorrected", "ideal", "single")),
        ("sar", "sar_replay", ("sar",)),
    ):
        for name in names:
            with (
                np.load(output / source / (name + ".npz")) as before,
                np.load(output / target / (name + ".npz")) as after,
            ):
                for key in before.files:
                    np.testing.assert_array_equal(before[key], after[key])
            comparisons.append(
                {
                    "test_id": source + "_" + name + "_replay",
                    "status": "PASS",
                    "comparison": "array_equal, all NPZ fields",
                }
            )
    (output / "replay_verification.json").write_text(
        json.dumps(comparisons, indent=2), encoding="utf-8"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
