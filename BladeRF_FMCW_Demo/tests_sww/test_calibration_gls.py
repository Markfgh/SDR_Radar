import unittest

import numpy as np

from sww.calibration import contamination, estimate_transfer, reference_projection
from sww.config import Config
from sww.experiments import contamination_study, suppression_study
from sww.gls import gls_filter
from sww.waveforms import chirp, fractional_delay


class CalibrationGlsTests(unittest.TestCase):
    def test_complex_transfer_deembeds_cable_and_interpolates_drift(self):
        rf = np.linspace(2.4e9, 2.412e9, 400)
        x = np.ones(400, complex)
        h = (1 + 0.1 * np.sin(rf / 1e6)) * np.exp(0.4j - 2j * np.pi * rf * 12e-9)
        cable = 0.8 * np.exp(-2j * np.pi * rf * 7e-9)
        r = estimate_transfer(
            h * cable, h * cable * np.exp(0.1j), x, rf, 7e-9, 0.8, 0.001, 0.2, 40, 20
        )
        self.assertTrue(r.valid)
        np.testing.assert_allclose(r.transfer, h * np.exp(0.05j), atol=1e-12)

    def test_echo_reference_bias_matches_independent_projection(self):
        c = Config()
        x = chirp(c)
        ref = fractional_delay(x, 1e-8, c.fs_sps, c.capture_samples)
        echo = 0.2j * fractional_delay(x, 1e-8 + 1e-8, c.fs_sps, c.capture_samples)
        row = contamination(ref, [echo])
        coef, _ = reference_projection(ref + echo, ref)
        self.assertAlmostEqual(row["phase_bias_rad"], np.angle(coef), places=12)
        self.assertLessEqual(abs(row["phase_bias_rad"]), row["phase_bound_rad"] + 1e-12)

    def test_colocated_echo_is_not_identifiable_as_reference(self):
        rows = contamination_study(Config())
        colocated = next(
            r
            for r in rows
            if r["delay_in_inverse_bandwidth"] == 0 and r["echo_reference_ratio"] == 0.5
        )
        self.assertFalse(colocated["identifiable"])
        self.assertGreater(abs(colocated["independent_echo_phase_residual_rad"]), 0.1)
        distant = next(
            r
            for r in rows
            if r["delay_in_inverse_bandwidth"] == 8 and r["echo_reference_ratio"] == 0.5
        )
        self.assertLess(
            distant["relative_contamination"], colocated["relative_contamination"]
        )

    def test_gls_null_mask_and_bounded_gain(self):
        w = np.array([0, 1e-20, 1e-4, 1, 0.1, 1, 0], complex)
        h, mask, m = gls_filter(
            w, np.ones(7), np.ones(7, bool), regularization=1e-8, floor=1e-5, max_gain=3
        )
        self.assertTrue(np.all(np.isfinite(h)))
        self.assertLessEqual(np.max(abs(h)), 3 + 1e-12)
        self.assertEqual(h[0], 0)
        self.assertEqual(h[1], 0)
        self.assertFalse(mask[0])
        self.assertGreater(m["capped_bins"], 0)

    def test_paper_gls_exact_on_valid_support(self):
        w = np.linspace(0.3, 1, 101)
        m = np.hamming(101)
        h, mask, _ = gls_filter(w, m, np.ones(101, bool), mode="paper")
        np.testing.assert_allclose(h * w, m / (m[50] / w[50]), atol=1e-12)

    def test_gls_empty_support_finite(self):
        h, mask, _ = gls_filter(np.zeros(100), np.ones(100), np.zeros(100, bool))
        self.assertFalse(np.any(mask))
        self.assertTrue(np.all(h == 0))

    def test_window_and_overlap_tradeoffs_are_separate(self):
        study = suppression_study(Config())
        self.assertGreater(
            study["gls_hamming"]["psf"]["width_6db_m"],
            study["gls_rect"]["psf"]["width_6db_m"],
        )
        self.assertLess(
            study["gls_hamming"]["psf"]["psl_db"], study["uniform_raw"]["psf"]["psl_db"]
        )
        self.assertGreater(study["overlap"]["steps"], study["uniform_raw"]["steps"])
        for row in study.values():
            self.assertEqual(row["span_hz"], 96e6)
