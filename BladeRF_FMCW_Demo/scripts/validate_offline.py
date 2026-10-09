"""Run legacy + SWW tests and persist per-test statuses, errors and timings."""

import argparse
import io
import json
import sys
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RecordedResult(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def record(self, test, status, error=None):
        self.stream_records.append(
            {
                "test_id": test.id(),
                "status": status,
                "duration_s": time.perf_counter() - self.started,
                "error": error,
            }
        )

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "FAIL", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "FAIL", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "BLOCKED", reason)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream_records = []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="reports/offline_validation_20261009")
    args = parser.parse_args()
    suite = unittest.TestSuite()
    for directory in ("tests", "tests_sww"):
        suite.addTests(
            unittest.TestLoader().discover(
                str(ROOT / directory), top_level_dir=str(ROOT / directory)
            )
        )
    report = Path(args.report)
    report.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    result = unittest.TextTestRunner(
        stream=buffer, verbosity=2, resultclass=RecordedResult
    ).run(suite)
    log = buffer.getvalue()
    print(log)
    (report / "tests.txt").write_text(log, encoding="utf-8")
    (report / "test_results.json").write_text(
        json.dumps(
            {
                "utc": datetime.now(UTC).isoformat(),
                "origin": "OFFLINE",
                "tests_run": result.testsRun,
                "successful": result.wasSuccessful(),
                "tests": result.stream_records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
