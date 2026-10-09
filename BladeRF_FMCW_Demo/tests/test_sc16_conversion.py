import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bladerf_interface import sc16_q11_to_complex


class Sc16ConversionTests(unittest.TestCase):
    def test_sc16_q11_conversion_and_scale(self) -> None:
        raw = np.array([0, 0, 2047, -2048, -1024, 1024], dtype=np.int16)
        result = sc16_q11_to_complex(raw)
        np.testing.assert_allclose(result, [0j, 2047 / 2048 - 1j, -0.5 + 0.5j])


if __name__ == "__main__":
    unittest.main()

