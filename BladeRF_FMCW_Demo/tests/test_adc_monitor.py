import os
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fmcw import rx_adc_metrics


class RxAdcMetricsTests(unittest.TestCase):
    def test_half_scale_component_has_six_db_headroom(self) -> None:
        metrics = rx_adc_metrics(np.array([0.5 + 0j, -0.5 + 0j], dtype=np.complex64))
        self.assertAlmostEqual(metrics.peak_dbfs, -6.0206, places=3)
        self.assertAlmostEqual(metrics.headroom_db, 6.0206, places=3)
        self.assertEqual(metrics.clipping_percent, 0.0)

    def test_near_full_scale_component_reports_clipping(self) -> None:
        metrics = rx_adc_metrics(np.array([1.0 + 0j, 0j], dtype=np.complex64))
        self.assertTrue(metrics.clipping_detected)
        self.assertAlmostEqual(metrics.clipping_percent, 25.0)
        self.assertAlmostEqual(metrics.headroom_db, 0.0)

    def test_safe_gui_defaults_do_not_enable_tx(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from fmcw_gui import RadarWindow
        app = QApplication.instance() or QApplication([])
        window = RadarWindow()
        try:
            self.assertFalse(window.tx_active)
            self.assertFalse(window.radio._tx_enabled)
            self.assertEqual(window.tx_amplitude.value(), 5)
            self.assertEqual(window.rx_gain.value(), window.rx_gain.minimum())
            self.assertEqual(window.chirp_bw.value(), 28.0)
            self.assertEqual(window.sample_rate.value(), 40_000_000)
        finally:
            window.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
