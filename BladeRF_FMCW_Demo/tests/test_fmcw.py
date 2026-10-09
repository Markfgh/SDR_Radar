import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fmcw import FmcwParameters, generate_baseband_chirp, process_fmcw_block


class FmcwTests(unittest.TestCase):
    def setUp(self) -> None:
        self.params = FmcwParameters(2.4e9, 10e6, 1e-3, 20e6)

    def test_chirp_has_expected_length_and_headroom(self) -> None:
        chirp = generate_baseband_chirp(self.params)
        self.assertEqual(chirp.size, 20_000)
        self.assertLessEqual(float(np.abs(chirp).max()), 0.401)

    def test_dechirp_recovers_known_positive_beat_range(self) -> None:
        reference = generate_baseband_chirp(self.params)
        # A delayed up-chirp produces a positive beat with rx * conj(tx).
        delay = 100
        time_s = np.arange(reference.size) / self.params.sample_rate_sps
        beat_hz = self.params.slope_hz_per_s * delay / self.params.sample_rate_sps
        rx = reference * np.exp(2j * np.pi * beat_hz * time_s)
        result = process_fmcw_block(rx, reference, self.params, 1000.0)
        expected = 299_792_458.0 * (delay / self.params.sample_rate_sps) / 2.0
        peak = result.range_m[np.argmax(result.spectrum_db)]
        self.assertAlmostEqual(float(peak), expected, delta=self.params.range_resolution_m)


if __name__ == "__main__":
    unittest.main()
