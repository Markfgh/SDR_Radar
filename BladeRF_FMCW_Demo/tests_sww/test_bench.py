"""OFFLINE fixtures only. A mocked HARDWARE label is never bench evidence."""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from sww.bench import (
    fingerprint,
    load_session,
    qualification_inputs,
    run_sequence,
    stacking_result,
    visits,
)
from sww.bench_acquisition import BenchPermit, capture_stage
from sww.bench_analysis import (
    extract_pulses,
    fit_independent_echo,
    shifted,
    validate_rows,
)
from sww.config import Config
from sww.storage import CaptureWriter
from sww.types import AcquisitionRecord
from sww.waveforms import chirp, encode_sc16


def fixture_session():
    config = Config(
        fs_sps=4e6,
        bi_hz=1e6,
        bs_hz=1e6,
        tp_s=128e-6,
        capture_samples=2048,
        centers_hz=tuple(2.4e9 + n * 1e6 for n in range(4)),
        max_range_m=1000,
        targets=(),
        topology="summed",
    )
    criteria = dict(
        detection_peak_fraction=0.5,
        min_detection_confidence=0.7,
        max_reference_shape_error=0.12,
        max_gram_condition=15,
        min_reference_fit_snr_db=30,
        min_echo_fit_snr_db=25,
        max_model_residual_fraction=0.08,
        max_phase_rms_rad=0.06,
        max_phase_p95_rad=0.1,
        max_delay_error_s=15e-9,
        max_projection_phase_bound_rad=0.1,
        min_coverage_fraction=0.95,
        max_range_error_m=8,
        max_width_ratio=0.5,
        min_repeats=2,
    )
    session = dict(
        schema_version=1,
        session_id="OFFLINE_FIXTURE_NOT_RF_AUTHORIZATION",
        config=config.to_dict(),
        rx_blocks=3,
        fixed_repeats=3,
        aba_cycles=2,
        sweep_repeats=2,
        reference_repeats=2,
        criteria_approved_by="OFFLINE TEST",
        criteria_evidence="test only",
        criteria=criteria,
        independent_path=dict(
            independently_measured=True,
            stationary_single_path=True,
            relative_delay_s=4e-6,
            delay_uncertainty_s=1e-11,
            evidence="ANALYTIC OFFLINE FIXTURE",
        ),
    )
    return session, config


def permit_data(digest, stage):
    return dict(
        session_sha256=digest,
        authorized_stages=[stage],
        operator="OFFLINE TEST",
        operator_approved=True,
        expires_utc=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        topology="summed_antennas",
        connections_checked=True,
        power_budget_verified=True,
        environment_verified=True,
        emergency_stop_available=True,
        antenna_tx_explicitly_authorized=True,
        both_combiner_inputs_budgeted=True,
        splitter_combiner_isolation_checked=True,
        rx_echo_branch_terminated=stage.startswith("reference_"),
        independent_path_present=True,
        evidence="OFFLINE TEST ONLY",
        digital_amplitude_max=0.05,
        tx_gain_db=-10,
        estimated_max_rx_dbm=-50,
        verified_rx_limit_dbm=-10,
        required_rx_margin_db=10,
        allowed_band_hz=[2.3e9, 2.5e9],
        max_duration_s=60,
        max_bursts=50,
        settle_s=0.001,
    )


def write_fixture(
    run, session, config, digest, stage, phase_fault=0.0, origin="HARDWARE"
):
    folder = Path(run) / stage
    folder.mkdir(exist_ok=True)
    writer = CaptureWriter(
        folder / "capture",
        config,
        origin,
        {"session_sha256": digest, "stage": stage, "transport": "OFFLINE MOCK"},
    )
    rng = np.random.default_rng(58)
    x = chirp(config)
    frequencies, repeats = visits(session, config, stage)
    for visit, fc in enumerate(frequencies):
        n = 4096 * repeats + 4096
        y = 0.00003 * (rng.normal(size=n) + 1j * rng.normal(size=n))
        for repeat in range(repeats):
            start = 1024 + repeat * 4096
            reference = np.zeros(2048, complex)
            reference[4 : 4 + len(x)] = x * 0.7
            phi = rng.uniform(-np.pi, np.pi)
            pulse = reference * np.exp(1j * phi)
            if not stage.startswith("reference_"):
                tau = session["independent_path"]["relative_delay_s"]
                pulse += (
                    shifted(reference, tau * config.fs_sps)
                    * 0.3
                    * np.exp(1j * (phi + 0.4 - 2 * np.pi * fc * tau + phase_fault))
                )
            y[start : start + 2048] += pulse
        raw = encode_sc16(y)
        for block in range(n // 4096):
            writer.append(
                AcquisitionRecord(
                    visit,
                    "RADAR",
                    fc,
                    raw[block * 8192 : (block + 1) * 8192].copy(),
                    10000 + 4096 * block,
                    0,
                    4096,
                    origin,
                    {"transport": "OFFLINE MOCK"},
                )
            )
    writer.finish()


class BenchTests(unittest.TestCase):
    def test_initial_40_msps_plan_fractional_echo_and_measured_resolution(self):
        from sww.bench_analysis import C

        s, _ = fixture_session()
        cfg = Config(
            centers_hz=(2414e6, 2426e6, 2438e6, 2450e6), targets=(), topology="summed"
        )
        s["config"] = cfg.to_dict()
        s["independent_path"]["relative_delay_s"] = 40 / C - 7e-9
        digest = fingerprint(s)
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            for stage in ("reference_pre", "fixed", "aba", "sweep", "reference_post"):
                write_fixture(run, s, cfg, digest, stage)
            (run / "stack").mkdir()
            result = stacking_result(run, s, cfg, digest)
            self.assertEqual(result["status"], "PASS")
            metrics = json.loads((run / "stack/resolution_metrics.json").read_text())
            self.assertLess(abs(metrics[0]["wide"]["range_error_m"]), 0.5)
            self.assertLess(
                metrics[0]["wide"]["width_6db_m"],
                0.4 * metrics[0]["single"]["width_6db_m"],
            )

    def test_mainlobe_cut_at_axis_boundary_is_not_a_measured_width(self):
        from sww.metrics import peak_metrics

        ranges = np.arange(5, dtype=float)
        profile = np.array([0.8, 1, 0.8, 0.4, 0.1])
        self.assertIsNone(peak_metrics(ranges, profile)["width_6db_m"])

    def test_rx_only_persists_after_stop_and_rejects_partial_buffer(self):
        from bladerf_interface import DeviceInfo, RxConfiguration
        from sww.hardware_tests import run_rx

        s, cfg = fixture_session()
        info = DeviceInfo(
            "bladerf1",
            "OFFLINE",
            "MOCK",
            "MOCK",
            True,
            40,
            "MOCK",
            "MOCK",
            "2.6.0",
            (1, 6_000_000_000),
            (1, 40_000_000),
            (1, 28_000_000),
            (0, 60),
            (1, 6_000_000_000),
            (1, 40_000_000),
            (1, 28_000_000),
            (-10, 60),
        )
        for short in (False, True):
            with self.subTest(short=short), tempfile.TemporaryDirectory() as temp:
                radio = MagicMock()
                active = [False]
                radio.open_device.return_value = info
                radio.configure_rx.return_value = RxConfiguration(
                    int(cfg.centers_hz[0]),
                    4_000_000,
                    4_000_000,
                    1_000_000,
                    1_000_000,
                    0,
                )
                radio.prepare_metadata.return_value = {"test": "MOCK ABI"}
                radio.start_metadata_rx.side_effect = lambda active=active, **kwargs: (
                    active.__setitem__(0, True)
                )
                radio.stop_streaming.side_effect = lambda active=active: (
                    active.__setitem__(0, False)
                )
                records = [
                    AcquisitionRecord(
                        i,
                        "RX_ONLY",
                        cfg.centers_hz[0],
                        np.zeros(2 * count, np.int16),
                        100 + i * 32768,
                        0,
                        count,
                        "HARDWARE",
                    )
                    for i, count in enumerate((32768, 32760 if short else 32768))
                ]
                radio.read_metadata.side_effect = records

                def writer_factory(*args, active=active, **kwargs):
                    writer = CaptureWriter(*args, **kwargs)
                    original = writer.append

                    def append(record):
                        self.assertFalse(active[0], "No disk writes while RX is active")
                        original(record)

                    writer.append = append
                    return writer

                with (
                    patch("sww.hardware_tests.MetadataBladeRF", return_value=radio),
                    patch(
                        "sww.hardware_tests.CaptureWriter", side_effect=writer_factory
                    ),
                ):
                    if short:
                        with self.assertRaises(RuntimeError):
                            run_rx(cfg, temp, blocks=2, metadata=True)
                    else:
                        run_rx(cfg, temp, blocks=2, metadata=True)
                manifest = json.loads(
                    (Path(temp) / "capture/manifest.json").read_text()
                )
                self.assertEqual(len(manifest["records"]), 2)
                self.assertEqual(manifest["state"], "FAULT" if short else "COMPLETED")

    def test_template_dry_run_never_opens_radio(self):
        from sww.bench import main

        with patch(
            "sww.hardware.MetadataBladeRF.open_device",
            side_effect=AssertionError("no USB"),
        ):
            self.assertEqual(
                main(
                    [
                        "--session",
                        "configs/monday_session_TEMPLATE.json",
                        "--dry-run",
                        "--through",
                        "stack",
                    ]
                ),
                0,
            )

    def test_missing_evidence_and_numeric_criteria_block(self):
        s, cfg, _ = load_session("configs/monday_session_TEMPLATE.json")
        with self.assertRaisesRegex(RuntimeError, "BLOCKED"):
            qualification_inputs(s, cfg)
        s, cfg = fixture_session()
        s["independent_path"]["independently_measured"] = False
        with self.assertRaisesRegex(RuntimeError, "independent"):
            qualification_inputs(s, cfg)

    def test_antenna_permit_does_not_inherit_closed_loopback_authorization(self):
        s, cfg = fixture_session()
        digest = fingerprint(s)
        data = permit_data(digest, "fixed")
        BenchPermit(data, digest, "fixed").check(cfg)
        for key in (
            "antenna_tx_explicitly_authorized",
            "both_combiner_inputs_budgeted",
            "splitter_combiner_isolation_checked",
        ):
            bad = dict(data, **{key: False})
            with self.assertRaises(PermissionError):
                BenchPermit(bad, digest, "fixed").check(cfg)
        with self.assertRaises(PermissionError):
            BenchPermit(data, digest, "aba").check(cfg)
        data["expires_utc"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        with self.assertRaises(PermissionError):
            BenchPermit(data, digest, "fixed").check(cfg)

    def test_boundary_pulse_is_recovered_and_gap_short_count_clipping_reject(self):
        s, cfg = fixture_session()
        y = np.zeros(8192, complex)
        x = chirp(cfg)
        y[4000 : 4000 + len(x)] = x
        raw = encode_sc16(y)
        records = [
            AcquisitionRecord(0, "RADAR", cfg.centers_hz[0], raw[:8192], 0, 0, 4096),
            AcquisitionRecord(0, "RADAR", cfg.centers_hz[0], raw[8192:], 4096, 0, 4096),
        ]
        pulses = extract_pulses(cfg, records, 1, s["criteria"])
        self.assertEqual(pulses[0]["lag_samples"], 4000)
        for fault in (
            replace(records[1], timestamp=4097),
            replace(records[1], actual_count=4000),
            replace(records[1], status=1),
        ):
            with self.assertRaises(RuntimeError):
                extract_pulses(cfg, [records[0], fault], 1, s["criteria"])
        corrupted = records[1].raw.copy()
        corrupted[0] = -2048
        with self.assertRaises(RuntimeError):
            extract_pulses(
                cfg, [records[0], replace(records[1], raw=corrupted)], 1, s["criteria"]
            )
        with self.assertRaises(RuntimeError):
            extract_pulses(cfg, records, 2, s["criteria"])

    def test_blind_delay_and_single_rx_bias_not_reference_normalization(self):
        s, cfg = fixture_session()
        reference = np.zeros(cfg.capture_samples, complex)
        reference[128 : 128 + cfg.pulse_samples] = chirp(cfg)
        echo = shifted(reference, 16)
        measured = np.exp(1.2j) * (reference + 0.3 * np.exp(0.4j) * echo)
        fit = fit_independent_echo(measured, reference, cfg, s["criteria"])
        self.assertAlmostEqual(fit["delay_s"], 4e-6, delta=1e-10)
        self.assertLess(abs(fit["ratio_phase_rad"] - 0.4), 1e-5)
        row = dict(fit, fc_hz=cfg.centers_hz[0])
        metrics = validate_rows([row, row], s["independent_path"], s["criteria"])
        self.assertLess(metrics["phase_rms_rad"], 1e-5)
        wrong = dict(s["independent_path"], relative_delay_s=3e-6)
        with self.assertRaisesRegex(RuntimeError, "delay"):
            validate_rows([row, row], wrong, s["criteria"])
        # A normalized reference does not hide an independent echo phase jump.
        bad = dict(row, ratio_phase_rad=row["ratio_phase_rad"] + 0.5)
        with self.assertRaisesRegex(RuntimeError, "phase"):
            validate_rows(
                [row, bad],
                s["independent_path"],
                s["criteria"],
                metrics["phase_origin_rad"],
            )
        with self.assertRaisesRegex(RuntimeError, "boundary|unresolved"):
            fit_independent_echo(
                reference + 0.3j * shifted(reference, 0.3),
                reference,
                cfg,
                s["criteria"],
            )
        near = dict(row, gram_condition=1000)
        with self.assertRaisesRegex(RuntimeError, "separable"):
            validate_rows(
                [dict(near, fc_hz=cfg.centers_hz[0])],
                s["independent_path"],
                dict(s["criteria"], max_gram_condition=2),
            )

    def test_fail_fast_no_following_rx_or_tx(self):
        s, cfg = fixture_session()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("sww.bench.subprocess.run", return_value=MagicMock(returncode=0)),
            patch(
                "sww.hardware_tests.run_probe", side_effect=RuntimeError("USB fault")
            ),
            patch("sww.hardware_tests.run_rx") as rx,
            patch("sww.bench_acquisition.capture_stage") as tx,
        ):
            run = Path(temp) / "run"
            self.assertEqual(
                run_sequence(s, cfg, fingerprint(s), run, "stack", [], None, False), 1
            )
            rx.assert_not_called()
            tx.assert_not_called()
            self.assertEqual(
                json.loads((run / "sequence.json").read_text())["stages"][-1]["stage"],
                "probe",
            )

    def test_resume_corruption_records_stop_before_hardware(self):
        s, cfg = fixture_session()
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"

            def offline(*args, **kwargs):
                (run / "offline/evidence.txt").write_text("original")
                return MagicMock(returncode=0)

            with patch("sww.bench.subprocess.run", side_effect=offline):
                self.assertEqual(
                    run_sequence(
                        s, cfg, fingerprint(s), run, "offline", [], None, False
                    ),
                    0,
                )
            (run / "offline/evidence.txt").write_text("corrupted")
            with (
                patch("sww.hardware_tests.run_probe") as probe,
                patch("sww.bench_acquisition.capture_stage") as tx,
            ):
                self.assertEqual(
                    run_sequence(
                        s,
                        cfg,
                        fingerprint(s),
                        run,
                        "stack",
                        [],
                        None,
                        False,
                        resume=True,
                    ),
                    1,
                )
                probe.assert_not_called()
                tx.assert_not_called()
            state = json.loads((run / "sequence.json").read_text())
            self.assertEqual(state["status"], "STOPPED")
            self.assertEqual(state["stages"][-1]["status"], "FAIL")

    def test_no_tx_flags_no_permit_no_autorun(self):
        s, cfg = fixture_session()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("sww.bench.subprocess.run", return_value=MagicMock(returncode=0)),
            patch("sww.hardware_tests.run_probe", return_value={}),
            patch("sww.hardware_tests.run_rx", return_value={}),
            patch("sww.bench_acquisition.capture_stage") as tx,
        ):
            run = Path(temp) / "run"
            self.assertEqual(
                run_sequence(s, cfg, fingerprint(s), run, "stack", [], None, False), 2
            )
            tx.assert_not_called()
            self.assertFalse((run / "reference_pre").exists())

    def test_independent_phase_fail_stops_before_sweep(self):
        s, cfg = fixture_session()
        digest = fingerprint(s)
        called = []

        def mock_capture(
            config, folder, frequencies, repeats, permit, session_hash, stage
        ):
            called.append(stage)
            write_fixture(
                folder.parent,
                s,
                cfg,
                digest,
                stage,
                phase_fault=0.5 if stage == "aba" else 0,
            )
            return {"transport": "OFFLINE MOCK"}

        def confirm(prompt):
            return prompt.split("Type '", 1)[1].split("':", 1)[0]

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("sww.bench.subprocess.run", return_value=MagicMock(returncode=0)),
            patch("sww.hardware_tests.run_probe", return_value={}),
            patch("sww.hardware_tests.run_rx", return_value={}),
            patch("sww.bench_acquisition.load_permit", return_value=MagicMock()),
            patch("sww.bench_acquisition.capture_stage", side_effect=mock_capture),
        ):
            run = Path(temp) / "run"
            code = run_sequence(
                s,
                cfg,
                digest,
                run,
                "stack",
                ["reference_pre", "fixed", "aba", "sweep", "reference_post"],
                "unused",
                True,
                confirm=confirm,
            )
            self.assertEqual(code, 1)
            self.assertEqual(called, ["reference_pre", "fixed", "aba"])
            self.assertFalse((run / "sweep").exists())
            self.assertEqual(
                json.loads((run / "aba/phase_metrics.json").read_text())["status"],
                "FAIL",
            )

    def test_acquisition_preflight_failure_never_opens_radio(self):
        s, cfg = fixture_session()
        permit = BenchPermit(permit_data("wrong", "fixed"), fingerprint(s), "fixed")
        with patch("sww.bench_acquisition.MetadataBladeRF") as radio:
            with self.assertRaises(PermissionError):
                capture_stage(
                    cfg,
                    Path("unused"),
                    [cfg.centers_hz[0]],
                    2,
                    permit,
                    fingerprint(s),
                    "fixed",
                )
            radio.assert_not_called()

    def test_rx_fault_stops_before_tx_and_preserves_fault_iq(self):
        from bladerf_interface import RxConfiguration, TxConfiguration

        s, cfg = fixture_session()
        digest = fingerprint(s)
        permit = BenchPermit(permit_data(digest, "fixed"), digest, "fixed")
        radio = MagicMock()
        # asdict needs real dataclasses for device information too.
        from bladerf_interface import DeviceInfo

        info = DeviceInfo(
            "bladerf1",
            "OFFLINE",
            "MOCK",
            "MOCK",
            True,
            40,
            "MOCK",
            "MOCK",
            "2.6.0",
            (1, 6_000_000_000),
            (1, 40_000_000),
            (1, 28_000_000),
            (0, 60),
            (1, 6_000_000_000),
            (1, 40_000_000),
            (1, 28_000_000),
            (-10, 60),
        )
        radio.open_device.return_value = info
        radio.prepare_metadata.return_value = {"test": "MOCK ABI"}
        radio.configure_rx.return_value = RxConfiguration(
            int(cfg.centers_hz[0]), 4_000_000, 4_000_000, 1_000_000, 1_000_000, 0
        )
        radio.configure_tx.return_value = TxConfiguration(
            int(cfg.centers_hz[0]), 4_000_000, 4_000_000, 1_000_000, 1_000_000, -10
        )
        radio.actual_frequency.return_value = cfg.centers_hz[0]
        rec = AcquisitionRecord(
            0,
            "RADAR",
            cfg.centers_hz[0],
            np.zeros(65536, np.int16),
            100,
            1,
            32768,
            "HARDWARE",
        )
        radio.read_metadata.return_value = rec
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("sww.bench_acquisition.MetadataBladeRF", return_value=radio),
        ):
            folder = Path(temp)
            with self.assertRaises(RuntimeError):
                capture_stage(
                    cfg, folder, [cfg.centers_hz[0]], 2, permit, digest, "fixed"
                )
            radio.start_permitted_tx.assert_not_called()
            radio.scheduled_burst.assert_not_called()
            radio.force_tx_off.assert_called()
            radio.close_device.assert_called_once()
            manifest = json.loads((folder / "capture/manifest.json").read_text())
            self.assertEqual(manifest["state"], "FAULT")
            self.assertTrue(manifest["records"])

    def test_bounded_bursts_keep_fixed_stream_and_tx_error_disables_radio(self):
        import time

        from bladerf_interface import DeviceInfo, RxConfiguration, TxConfiguration

        s, cfg = fixture_session()
        digest = fingerprint(s)
        info = DeviceInfo(
            "bladerf1",
            "OFFLINE",
            "MOCK",
            "MOCK",
            True,
            40,
            "MOCK",
            "MOCK",
            "2.6.0",
            (1, 6_000_000_000),
            (1, 40_000_000),
            (1, 28_000_000),
            (0, 60),
            (1, 6_000_000_000),
            (1, 40_000_000),
            (1, 28_000_000),
            (-10, 60),
        )
        for fail in (False, True):
            with (
                self.subTest(tx_api_failure=fail),
                tempfile.TemporaryDirectory() as temp,
            ):
                radio = MagicMock()
                radio.open_device.return_value = info
                radio.prepare_metadata.return_value = {"test": "MOCK ABI"}
                radio.configure_rx.return_value = RxConfiguration(
                    int(cfg.centers_hz[0]),
                    4_000_000,
                    4_000_000,
                    1_000_000,
                    1_000_000,
                    0,
                )
                radio.configure_tx.return_value = TxConfiguration(
                    int(cfg.centers_hz[0]),
                    4_000_000,
                    4_000_000,
                    1_000_000,
                    1_000_000,
                    -10,
                )
                radio.actual_frequency.return_value = cfg.centers_hz[0]
                counter = [0]

                def receive(*args, counter=counter, **kwargs):
                    time.sleep(0.001)
                    index = counter[0]
                    counter[0] += 1
                    return AcquisitionRecord(
                        0,
                        "RADAR",
                        cfg.centers_hz[0],
                        np.zeros(65536, np.int16),
                        32768 * index,
                        0,
                        32768,
                        "HARDWARE",
                    )

                radio.read_metadata.side_effect = receive
                event = {
                    "scheduled_timestamp": None,
                    "flags": 7,
                    "status": 0,
                    "epoch_relation_verified": False,
                }
                radio.scheduled_burst.side_effect = [
                    event,
                    RuntimeError("MOCK TX API failure") if fail else event,
                ]
                permit = BenchPermit(permit_data(digest, "fixed"), digest, "fixed")
                with patch("sww.bench_acquisition.MetadataBladeRF", return_value=radio):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "TX API"):
                            capture_stage(
                                cfg,
                                temp,
                                [cfg.centers_hz[0]],
                                2,
                                permit,
                                digest,
                                "fixed",
                            )
                    else:
                        capture_stage(
                            cfg, temp, [cfg.centers_hz[0]], 2, permit, digest, "fixed"
                        )
                radio.start_metadata_rx.assert_called_once()
                radio.start_permitted_tx.assert_called_once()
                self.assertEqual(radio.scheduled_burst.call_count, 2)
                radio.force_tx_off.assert_called()
                radio.close_device.assert_called_once()
                raw = np.load(Path(temp) / "tx_waveform.npy")
                self.assertEqual(raw.size, 2 * (cfg.pulse_samples + 2))
                self.assertTrue(np.all(raw[-4:] == 0))
                manifest = json.loads(
                    (Path(temp) / "capture/manifest.json").read_text()
                )
                self.assertEqual(manifest["state"], "FAULT" if fail else "COMPLETED")

    def test_end_to_end_raw_mock_reference_relative_stack_no_simulator(self):
        s, cfg = fixture_session()
        digest = fingerprint(s)
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "sww.simulation.simulate",
                side_effect=AssertionError("no simulation fallback"),
            ),
        ):
            run = Path(temp)
            for stage in ("reference_pre", "fixed", "aba", "sweep", "reference_post"):
                write_fixture(run, s, cfg, digest, stage)
            (run / "stack").mkdir()
            result = stacking_result(run, s, cfg, digest)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["absolute_range"], "UNVERIFIED")
            self.assertEqual(result["two_target_resolution"], "NOT_TESTED")
            metrics = json.loads((run / "stack/resolution_metrics.json").read_text())
            self.assertTrue(
                all(
                    m["wide"]["width_6db_m"] < 0.5 * m["single"]["width_6db_m"]
                    for m in metrics
                )
            )
            first = np.load(run / "stack/wide_000.npz")["profile"].copy()
            stacking_result(run, s, cfg, digest)
            np.testing.assert_array_equal(
                first, np.load(run / "stack/wide_000.npz")["profile"]
            )
            # Corruption cannot be bypassed by the previously successful result.
            manifest = json.loads((run / "sweep/capture/manifest.json").read_text())
            iq_path = run / "sweep/capture" / manifest["records"][0]["path"]
            iq_path.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "hash"):
                stacking_result(run, s, cfg, digest)

    def test_simulated_capture_is_rejected(self):
        from sww.bench import capture_pulses

        s, cfg = fixture_session()
        with tempfile.TemporaryDirectory() as temp:
            write_fixture(temp, s, cfg, fingerprint(s), "fixed", origin="SIMULATED")
            with self.assertRaisesRegex(RuntimeError, "REAL"):
                capture_pulses(temp, "fixed", s, cfg, fingerprint(s))


if __name__ == "__main__":
    unittest.main()
