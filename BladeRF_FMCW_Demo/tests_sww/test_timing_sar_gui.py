import ctypes as ct
import os
import unittest
from unittest.mock import MagicMock

import numpy as np

from sww import C
from sww.hardware import MetadataBladeRF
from sww.motion import compensate, path_errors, scene_approximation_error
from sww.sar import backproject
from sww.timing import RX_NOW, Continuity, Metadata, check_abi


class TimingTests(unittest.TestCase):
    def test_metadata_abi(self):
        self.assertEqual(check_abi()["size"], 56)
        self.assertEqual(Metadata.reserved.offset, 20)
        meta = Metadata()
        self.assertEqual(bytes(meta), bytes(56))
        self.assertEqual(RX_NOW, 0x80000000)

    def test_continuity_gap_short_buffer_and_overrun(self):
        c = Continuity()
        self.assertTrue(c.observe(100, 20, 20, 0)["valid"])
        self.assertTrue(c.observe(120, 20, 20, 0)["valid"])
        self.assertFalse(c.observe(145, 20, 20, 0)["valid"])
        self.assertFalse(c.observe(165, 10, 20, 1)["valid"])
        self.assertFalse(c.observe(None, 20, 20, 0)["valid"])

    def test_metadata_mock_truncates_undefined_tail(self):
        radio = MetadataBladeRF()
        radio._lib = MagicMock()
        radio._rx_enabled = True
        radio.metadata_enabled = True
        radio.actual_frequency = lambda: 2.4e9

        def receive(dev, ptr, count, meta_ptr, timeout):
            raw = np.ctypeslib.as_array(
                ct.cast(ptr, ct.POINTER(ct.c_int16)), shape=(count * 2,)
            )
            raw[:8] = np.arange(8)
            meta = ct.cast(meta_ptr, ct.POINTER(Metadata)).contents
            meta.actual_count = 4
            meta.timestamp = 123
            meta.status = 1
            return 0

        radio._lib.bladerf_sync_rx.side_effect = receive
        record = radio.read_metadata(100)
        self.assertEqual(record.raw.size, 8)
        self.assertEqual(record.actual_count, 4)
        self.assertEqual(record.status, 1)
        self.assertEqual(record.timestamp, 123)
        self.assertFalse(record.metadata["digital_epoch_known"])
        radio._lib.bladerf_enable_module.assert_not_called()

    def test_api_errors_surfaced(self):
        radio = MetadataBladeRF()
        radio._lib = MagicMock()
        radio._rx_enabled = True
        radio.metadata_enabled = True
        radio._lib.bladerf_sync_rx.return_value = -6
        radio._lib.bladerf_strerror.return_value = b"injected timeout"
        with self.assertRaisesRegex(RuntimeError, "-6"):
            radio.read_metadata(100)


class MotionSarTests(unittest.TestCase):
    def test_motion_phase_sign_and_baseband_delay(self):
        rf = np.linspace(2.4e9, 2.496e9, 400)
        positions = np.array([[0, 0, 0], [0.1, 0.03, 0.02], [-0.2, -0.02, 0.01]])
        target = np.array([0, 20, 0])
        errors = path_errors(positions, target, np.zeros(3))
        reference = np.exp(-4j * np.pi * rf * 20 / C)
        for e in errors:
            moving = np.exp(-4j * np.pi * rf * (20 + e) / C)
            np.testing.assert_allclose(compensate(moving, rf, e), reference, atol=1e-11)
        approximate = scene_approximation_error(
            positions, [[0, 20, 0], [5, 20, 0]], target, [0, 0, 0]
        )
        np.testing.assert_allclose(approximate[:, 0], 0, atol=1e-14)
        self.assertGreater(np.max(abs(approximate[:, 1])), 0.001)

    def test_backprojection_focus_known_target_and_phase_gate(self):
        positions = np.column_stack((np.linspace(-1, 1, 51), np.zeros(51), np.ones(51)))
        target = np.array([0.2, 10, 0])
        ranges = np.linspace(0, 20, 20001)
        f = 2.442e9
        band = 96e6
        distances = np.linalg.norm(positions - target, axis=1)
        profiles = np.array(
            [
                np.sinc((ranges - r) / (C / (2 * band)))
                * np.exp(-4j * np.pi * f * r / C)
                for r in distances
            ]
        )
        xx, yy = np.meshgrid(np.linspace(-1, 1, 81), np.linspace(9, 11, 81))
        pixels = np.stack((xx, yy, np.zeros_like(xx)), axis=-1)
        with self.assertRaisesRegex(ValueError, "PHASE_UNVERIFIED"):
            backproject(profiles, ranges, positions, pixels, f)
        coherent = backproject(
            profiles, ranges, positions, pixels, f, phase_verified=True
        )
        index = np.unravel_index(np.argmax(abs(coherent)), coherent.shape)
        self.assertLess(np.linalg.norm(pixels[index] - target), 0.03)
        incoherent = backproject(profiles, ranges, positions, pixels, f, coherent=False)
        self.assertTrue(np.all(incoherent >= 0))
        self.assertGreater(
            np.count_nonzero(incoherent > 0.9 * incoherent.max()),
            np.count_nonzero(abs(coherent) > 0.9 * abs(coherent).max()),
        )


class GuiTests(unittest.TestCase):
    def test_diagnostic_gui_offscreen_without_radio(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from sww.gui import SwwWindow

        app = QApplication.instance() or QApplication([])
        window = SwwWindow()
        try:
            window.run_simulation()
            app.processEvents()
            self.assertIn("SIMULATED", window.status.text())
            self.assertEqual(window.tabs.count(), 6)
            self.assertNotIn("FAIL", window.status.text())
            self.assertFalse(hasattr(window, "radio"))
        finally:
            window.close()
            app.processEvents()
