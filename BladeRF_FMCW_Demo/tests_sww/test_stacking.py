import unittest
from dataclasses import replace

import numpy as np

from sww.config import Config, Target
from sww.experiments import compare
from sww.metrics import phase_residual
from sww.simulation import simulate
from sww.stacking import stack
from sww.waveforms import chirp, encode_sc16


class StackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = Config(noise_rms=0.0003)
        cls.records, _ = simulate(cls.c)
        cls.results, cls.metrics = compare(cls.c, cls.records)

    def test_two_targets_resolved_only_with_calibrated_synthetic_band(self):
        m = self.metrics
        self.assertTrue(m["corrected"]["two_targets"]["resolved"])
        self.assertTrue(m["ideal"]["two_targets"]["resolved"])
        self.assertFalse(m["single"]["two_targets"]["resolved"])
        self.assertFalse(m["uncorrected"]["two_targets"]["resolved"])
        self.assertGreater(m["corrected"]["two_targets"]["valley_db"], 10)
        self.assertLess(
            max(abs(v) for v in m["corrected"]["two_targets"]["errors_m"]), 0.4
        )

    def test_phase_residual_against_independent_ideal_echoes(self):
        self.assertLess(self.metrics["phase_residual_corrected"]["rms_rad"], 0.03)
        self.assertGreater(self.metrics["phase_residual_uncorrected"]["rms_rad"], 1)

    def test_full_spectrum_matches_independent_dft_sum(self):
        for centers in ((2.4e9, 2.412e9), (2.4e9, 2.407134567e9), (2.4e9, 2.43e9)):
            with self.subTest(centers=centers):
                c = replace(self.c, centers_hz=centers, gls_mode="none")
                records, _ = simulate(c, ideal=True)
                result = stack(c, records, calibration="none", apply_gls=False)
                # Independently compute selected spectral samples with explicit sums,
                # bypassing matched filter/CZT/stacking implementation.
                indices = np.flatnonzero(result.support)[::83]
                rf = result.rf_hz[indices]
                expected = np.zeros(len(indices), complex)
                x = chirp(c)
                energy = np.vdot(x, x).real
                for step, fc in enumerate(centers):
                    z = next(
                        r.iq for r in records if r.mode == "RADAR" and r.step == step
                    )
                    valid = (rf >= fc - c.bs_hz / 2) & (rf < fc + c.bs_hz / 2)
                    f = rf[valid] - fc
                    zz = (
                        np.exp(-2j * np.pi * f[:, None] * np.arange(len(z)) / c.fs_sps)
                        @ z
                    )
                    xx = (
                        np.exp(-2j * np.pi * f[:, None] * np.arange(len(x)) / c.fs_sps)
                        @ x
                    )
                    expected[valid] += zz * np.conj(xx) / energy
                np.testing.assert_allclose(
                    result.spectrum[indices], expected, atol=1e-7, rtol=1e-7
                )

    def test_fractional_target_range_without_oracle_stacking(self):
        c = replace(self.c, targets=(Target(31.234),))
        records, _ = simulate(c)
        r = stack(c, records)
        peak = r.range_m[np.argmax(abs(r.profile))]
        self.assertLess(abs(peak - 31.234), r.metrics["range_sample_spacing_m"])

    def test_zero_padding_does_not_change_bandwidth(self):
        a = self.results["corrected"]
        b = stack(replace(self.c, upsample=8), self.records)
        self.assertEqual(
            a.metrics["resolution_ideal_m"], b.metrics["resolution_ideal_m"]
        )
        self.assertLess(
            b.metrics["range_sample_spacing_m"], a.metrics["range_sample_spacing_m"]
        )
        self.assertAlmostEqual(max(abs(a.profile)), max(abs(b.profile)), delta=0.03)

    def test_missing_reference_is_rejected(self):
        records = [
            replace(r, raw=np.zeros_like(r.raw)) if r.mode.startswith("CAL") else r
            for r in self.records
        ]
        with self.assertRaisesRegex(ValueError, "INVALID_PHASE"):
            stack(self.c, records)

    def test_large_endpoint_drift_is_rejected(self):
        records = [
            replace(r, raw=encode_sc16(r.iq * np.exp(1j)))
            if r.mode == "CAL_POST"
            else r
            for r in self.records
        ]
        with self.assertRaisesRegex(ValueError, "INVALID_PHASE"):
            stack(self.c, records)

    def test_hidden_phase_jump_fails_independent_echo_validation(self):
        # CAL endpoints unchanged: a scalar reference normalization alone cannot detect this.
        records = [
            replace(r, raw=encode_sc16(r.iq * np.exp(1j * 1.2)))
            if r.mode == "RADAR"
            else r
            for r in self.records
        ]
        bad = stack(self.c, records)
        self.assertGreater(phase_residual(bad, self.results["ideal"])["rms_rad"], 1)
        self.assertNotEqual(bad.phase_state, "COHERENT_UWB")

    def test_wrong_phase_sign_fails_known_echo(self):
        records = [replace(r, raw=encode_sc16(np.conj(r.iq))) for r in self.records]
        try:
            bad = stack(self.c, records)
        except ValueError:
            return
        self.assertGreater(phase_residual(bad, self.results["ideal"])["rms_rad"], 0.5)

    def test_digital_reset_detected_by_independent_target(self):
        records = []
        for r in self.records:
            if r.mode == "RADAR":
                iq = np.concatenate((np.zeros(4), r.iq[:-4]))
                r = replace(r, raw=encode_sc16(iq))
            records.append(r)
        bad = stack(self.c, records)
        self.assertGreater(phase_residual(bad, self.results["ideal"])["rms_rad"], 0.5)
        # A corrupted waveform can produce plausible peaks by accident. Its
        # independent phase residual must reject it even when peaks look right.
        self.assertNotEqual(bad.phase_state, "COHERENT_UWB")

    def test_wrong_reference_delay_fails_absolute_range(self):
        bad = stack(
            replace(self.c, reference_delay_s=self.c.reference_delay_s + 40e-9),
            self.records,
        )
        self.assertGreater(phase_residual(bad, self.results["ideal"])["rms_rad"], 0.5)

    def test_stream_faults_and_hardware_epoch_rejected(self):
        for change in (
            {"status": 1},
            {"actual_count": 100},
            {"fc_hz": 2.3e9},
            {"origin": "HARDWARE", "metadata": {}},
        ):
            records = [replace(self.records[0], **change)] + self.records[1:]
            with self.subTest(change=change), self.assertRaises(ValueError):
                stack(self.c, records)

    def test_seed_is_reproducible(self):
        again, _ = simulate(self.c)
        for a, b in zip(again, self.records, strict=True):
            np.testing.assert_array_equal(a.raw, b.raw)
