import unittest
from dataclasses import replace

import numpy as np
from scipy.signal import convolve

from bladerf_interface import sc16_q11_to_complex
from sww.config import Config
from sww.matched_filter import dtft_uniform, matched_filter
from sww.waveforms import chirp, encode_sc16, fractional_delay


class MathematicalTests(unittest.TestCase):
    def test_instantaneous_frequency(self):
        c = Config()
        x = chirp(c)
        f = np.angle(x[1:] * np.conj(x[:-1])) * c.fs_sps / (2 * np.pi)
        t = (np.arange(len(x) - 1) + 0.5) / c.fs_sps - c.tp_s / 2
        np.testing.assert_allclose(f, c.bi_hz / c.tp_s * t, atol=1e-5)
        self.assertEqual(len(x), 1024)
        np.testing.assert_allclose(abs(x), 0.05, atol=1e-14)

    def test_component_sc16_roundtrip_and_saturation(self):
        x = np.array([0.9 + 0.95j, -0.9 - 0.95j, 2 - 2j, 0.123 + 0.234j])
        y = sc16_q11_to_complex(encode_sc16(x))
        np.testing.assert_allclose(y[:2], x[:2], atol=1 / 2048)
        self.assertEqual(y[2], 2047 / 2048 - 1j)

    def test_fractional_delay_against_sinc_sum(self):
        rng = np.random.default_rng(7)
        x = rng.normal(size=40) + 1j * rng.normal(size=40)
        shift = 2.35
        y = fractional_delay(x, shift / 1000, 1000, 512)
        # Independent Shannon sum, finite DFT guard has a small approximation error.
        m = np.arange(100)
        expected = np.sinc(m[:, None] - shift - np.arange(40)) @ x
        np.testing.assert_allclose(y[:100], expected, atol=0.002)

    def test_integer_delay_no_wrap(self):
        x = np.arange(1, 30) + 1j * np.arange(29)
        y = fractional_delay(x, 7 / 1000, 1000, 128)
        np.testing.assert_allclose(y[:7], 0, atol=1e-12)
        np.testing.assert_allclose(y[7:36], x, atol=1e-12)

    def test_linear_correlation_against_direct_convolution(self):
        rng = np.random.default_rng(8)
        z = rng.normal(size=83) + 1j * rng.normal(size=83)
        v = rng.normal(size=29) + 1j * rng.normal(size=29)
        lags, d = matched_filter(z, v)
        np.testing.assert_allclose(
            d, convolve(z, np.conj(v[::-1]), method="direct"), atol=1e-12
        )
        np.testing.assert_array_equal(lags, np.arange(-28, 83))

    def test_czt_non_bin_frequencies_against_direct_dft(self):
        rng = np.random.default_rng(1)
        x = rng.normal(size=103) + 1j * rng.normal(size=103)
        f = -3.223e6 + np.arange(51) * 19733.3
        expected = np.exp(-2j * np.pi * f[:, None] * np.arange(len(x)) / 40e6) @ x
        np.testing.assert_allclose(
            dtft_uniform(x, f[0], f[1] - f[0], len(f), 40e6), expected, atol=1e-10
        )

    def test_invalid_configs(self):
        for changes in (
            {"fs_sps": 0},
            {"bi_hz": 40e6},
            {"centers_hz": (float("nan"),)},
            {"amplitude": 0.1},
            {"tp_s": 1e-9},
            {"capture_samples": 20},
            {"regularization": 0},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(Config(), **changes).validate()
