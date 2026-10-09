import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fmcw import FmcwParameters, estimate_chirp_alignment, generate_baseband_chirp, process_fmcw_block


class FmcwTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = FmcwParameters(2.4e9, 10e6, 1e-3, 20e6)

    def test_chirp_has_expected_length_and_headroom(self) -> None:
        chirp = generate_baseband_chirp(self.params)
        self.assertEqual(chirp.size, 20_000)
        self.assertLessEqual(float(np.abs(chirp).max()), 0.401)

    def test_one_hundred_percent_chirp_is_limited_to_sc16_positive_full_scale(self) -> None:
        chirp = generate_baseband_chirp(self.params, amplitude=1.0)
        self.assertLessEqual(float(np.abs(chirp).max()), 2047.0 / 2048.0 + 1e-6)

    def _received_block(self, delays: list[int], amplitudes: list[float], boundary: int = 137,
                        dc_offset: complex = 0j) -> tuple[np.ndarray, np.ndarray]:
        reference = generate_baseband_chirp(self.params, amplitude=0.4)
        time_s = np.arange(reference.size) / self.params.sample_rate_sps
        # Direct wired reference sets the chirp boundary. Delayed targets have
        # negative beat frequency with rx * conj(tx) for an up-chirp.
        segment = 0.8 * reference
        for delay, amplitude in zip(delays, amplitudes):
            beat_hz = self.params.slope_hz_per_s * delay / self.params.sample_rate_sps
            segment += amplitude * reference * np.exp(-2j * np.pi * beat_hz * time_s)
        segment += dc_offset
        rx = np.concatenate((np.zeros(boundary, dtype=np.complex64), segment.astype(np.complex64),
                             np.zeros(200, dtype=np.complex64)))
        return rx, reference

    def test_single_delayed_chirp_has_negative_beat_and_correct_range(self) -> None:
        delay = 100
        rx, reference = self._received_block([delay], [0.30])
        result = process_fmcw_block(rx, reference, self.params, 1000.0, dc_remove=False)
        expected = 299_792_458.0 * delay / (2.0 * self.params.sample_rate_sps)
        self.assertTrue(result.synced)
        self.assertAlmostEqual(result.offset_samples, 137, delta=1)
        self.assertTrue(np.any(np.isclose(result.peak_ranges_m, expected, atol=self.params.range_resolution_m)))
        self.assertGreater(float(np.angle(np.sum(result.beat * np.exp(2j * np.pi * (self.params.slope_hz_per_s * delay / self.params.sample_rate_sps) * np.arange(reference.size) / self.params.sample_rate_sps)))), -np.pi)

    def test_two_resolvable_delayed_targets_are_reported(self) -> None:
        delays = [80, 220]
        rx, reference = self._received_block(delays, [0.35, 0.28])
        result = process_fmcw_block(rx, reference, self.params, 2000.0, dc_remove=False)
        expected = np.array([299_792_458.0 * delay / (2.0 * self.params.sample_rate_sps) for delay in delays])
        for target in expected:
            self.assertTrue(np.any(np.isclose(result.peak_ranges_m, target, atol=self.params.range_resolution_m)))

    def test_dc_offset_is_removed_before_dechirping(self) -> None:
        rx, reference = self._received_block([120], [0.35], dc_offset=0.2 + 0.15j)
        result = process_fmcw_block(rx, reference, self.params, 1500.0, dc_remove=True)
        expected = 299_792_458.0 * 120 / (2.0 * self.params.sample_rate_sps)
        self.assertTrue(result.synced)
        self.assertTrue(np.any(np.isclose(result.peak_ranges_m, expected, atol=self.params.range_resolution_m)))

    def test_boundary_alignment_uses_wired_reference(self) -> None:
        rx, reference = self._received_block([90], [0.2], boundary=431)
        alignment = estimate_chirp_alignment(rx, reference)
        self.assertTrue(alignment.reliable)
        self.assertAlmostEqual(alignment.offset_samples, 431, delta=1)
        self.assertGreater(alignment.confidence, 0.12)

    def test_noise_only_input_is_unsynced_and_has_no_target_peaks(self) -> None:
        rng = np.random.default_rng(5)
        reference = generate_baseband_chirp(self.params, amplitude=0.4)
        noise = (rng.standard_normal(reference.size * 2) + 1j * rng.standard_normal(reference.size * 2)).astype(np.complex64) * 0.02
        result = process_fmcw_block(noise, reference, self.params, 1000.0)
        self.assertFalse(result.synced)
        self.assertEqual(result.peak_ranges_m.size, 0)


if __name__ == "__main__":
    unittest.main()
