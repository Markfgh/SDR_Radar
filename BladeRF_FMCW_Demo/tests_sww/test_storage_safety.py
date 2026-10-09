import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from sww.acquisition import FakeRadioBackend
from sww.config import Config
from sww.hardware import LoopbackPermit, MetadataBladeRF
from sww.rf_switch import ManualSwitchController, MockSwitchController
from sww.scheduler import Sequencer
from sww.simulation import simulate
from sww.stacking import stack
from sww.storage import CaptureWriter, replay


class StorageSafetyTests(unittest.TestCase):
    def setUp(self):
        self.c = replace(Config(), centers_hz=(2.4e9, 2.412e9))
        self.records, self.truth = simulate(self.c)

    def writer(self, path):
        return CaptureWriter(path, self.c, "SIMULATED")

    def test_replay_raw_byte_stable_and_results_identical(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture"
            writer = self.writer(path)
            for r in self.records:
                writer.append(r)
            writer.finish(truth=self.truth)
            c, records, m = replay(path)
            for a, b in zip(self.records, records, strict=True):
                self.assertEqual(a.raw.tobytes(), b.raw.tobytes())
            np.testing.assert_array_equal(
                stack(c, records).spectrum, stack(self.c, self.records).spectrum
            )
            self.assertEqual(m["origin"], "SIMULATED")

    def test_capture_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            self.writer(Path(temp) / "capture")
            with self.assertRaises(FileExistsError):
                self.writer(Path(temp) / "capture")

    def test_windows_manifest_lock_has_bounded_atomic_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            writer = self.writer(Path(temp) / "capture")
            original = Path.replace
            attempts = []

            def replace_once(path, target):
                attempts.append(1)
                if len(attempts) == 1:
                    raise PermissionError("transient indexer lock")
                return original(path, target)

            with patch.object(Path, "replace", replace_once):
                writer.flush()
            self.assertEqual(len(attempts), 2)
            manifest = json.loads((writer.path / "manifest.json").read_text())
            self.assertEqual(manifest["state"], "INCOMPLETE")

    def test_corruption_and_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "capture"
            w = self.writer(path)
            w.append(self.records[0])
            w.finish()
            raw = next((path / "iq").glob("*.npy"))
            data = bytearray(raw.read_bytes())
            data[-1] ^= 1
            raw.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                replay(path)
            m = json.loads((path / "manifest.json").read_text())
            m["records"][0]["path"] = "../escape.npy"
            (path / "manifest.json").write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError, "escapes"):
                replay(path)

    def test_mock_triplets_have_no_inner_retune(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = FakeRadioBackend(self.records)
            seq = Sequencer(
                backend, MockSwitchController(), self.writer(Path(temp) / "capture")
            )
            seq.run(self.c)
            self.assertEqual(seq.state, "COMPLETED")
            self.assertFalse(backend.tx_enabled)
            actual = [
                e[0] for e in backend.events if e[0] != "MUTE" and e[0] != "TX_OFF"
            ]
            self.assertEqual(actual, ["TUNE", "CAL_PRE", "RADAR", "CAL_POST"] * 2)

    def test_faults_always_disable_tx_and_save_abort(self):
        for mode in ("CAL_PRE", "RADAR", "CAL_POST"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                backend = FakeRadioBackend(self.records, fault_mode=mode)
                w = self.writer(Path(temp) / "capture")
                with self.assertRaises(TimeoutError):
                    Sequencer(backend, MockSwitchController(), w).run(self.c)
                self.assertFalse(backend.tx_enabled)
                self.assertEqual(w.manifest["state"], "ABORTED")

    def test_switch_failure_disables_tx(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = FakeRadioBackend(self.records)
            w = self.writer(Path(temp) / "capture")
            with self.assertRaises(RuntimeError):
                Sequencer(backend, MockSwitchController(fault=True), w).run(self.c)
            self.assertFalse(backend.tx_enabled)

    def test_manual_requires_independent_readback(self):
        with self.assertRaises(ValueError):
            ManualSwitchController(None)
        switch = ManualSwitchController(lambda: "CAL", lambda prompt: "RADAR")
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            switch.select("RADAR")

    def test_disk_failure_disables_tx(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = FakeRadioBackend(self.records)
            w = self.writer(Path(temp) / "capture")
            with patch.object(w, "append", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    Sequencer(backend, MockSwitchController(), w).run(self.c)
            self.assertFalse(backend.tx_enabled)
            self.assertEqual(w.manifest["state"], "FAULT")

    def test_missing_dll_has_no_simulation_fallback(self):
        r = MetadataBladeRF(dll_path="Z:/definitely_missing/bladeRF.dll")
        with self.assertRaises(FileNotFoundError):
            r.open_device()
        self.assertFalse(r._tx_enabled)

    def test_real_switch_sequence_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = FakeRadioBackend(self.records)
            backend.simulated = False
            with self.assertRaisesRegex(RuntimeError, "BLOCKED_SWITCH"):
                Sequencer(
                    backend, MockSwitchController(), self.writer(Path(temp) / "capture")
                ).run(self.c)
            self.assertFalse(backend.tx_enabled)

    def permit(self):
        return {
            "operator_approved": True,
            "closed_terminated_loopback": True,
            "power_budget_verified": True,
            "environment_verified": True,
            "topology": "closed_loopback",
            "evidence": "test fixture only",
            "allowed_band_hz": [2.39e9, 2.43e9],
            "digital_amplitude_max": 0.05,
            "max_duration_s": 10,
            "max_bursts": 10,
            "tx_gain_db": -25,
            "estimated_max_rx_dbm": -50,
            "verified_rx_limit_dbm": -15,
        }

    def test_permit_rejects_ota_missing_preflight_band_and_overload(self):
        for changes in (
            {"operator_approved": False},
            {"topology": "OTA"},
            {"allowed_band_hz": [1, 2]},
            {"estimated_max_rx_dbm": -10},
            {"max_duration_s": 0},
            {"digital_amplitude_max": 0.1},
            {"estimated_max_rx_dbm": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(PermissionError):
                LoopbackPermit({**self.permit(), **changes}).check(self.c)

    def test_burst_requires_arm_and_budget(self):
        r = MetadataBladeRF()
        with self.assertRaises(RuntimeError):
            r.scheduled_burst(np.zeros(100, np.int16))
        permit = LoopbackPermit({**self.permit(), "max_bursts": 1})
        permit.check(self.c)
        permit.consume()
        with self.assertRaises(PermissionError):
            permit.consume()

    def test_timeout_and_ctrl_c_disable_tx(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = FakeRadioBackend(self.records)
            w = self.writer(Path(temp) / "capture")
            with self.assertRaises(TimeoutError):
                Sequencer(backend, MockSwitchController(), w, max_seconds=-1).run(
                    self.c
                )
            self.assertFalse(backend.tx_enabled)
        with tempfile.TemporaryDirectory() as temp:
            backend = FakeRadioBackend(self.records)
            w = self.writer(Path(temp) / "capture")
            with patch.object(backend, "capture", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    Sequencer(backend, MockSwitchController(), w).run(self.c)
            self.assertFalse(backend.tx_enabled)
