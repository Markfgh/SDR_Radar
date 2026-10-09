import ctypes as ct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from sww.config import Config
from sww.experiments import compare
from sww.hardware import MetadataBladeRF, verify_header
from sww.offline_demos import motion_dataset
from sww.simulation import simulate
from sww.stacking import stack
from sww.timing import TX_BURST_END, TX_BURST_START, TX_NOW, Metadata


class HardwareMockTests(unittest.TestCase):
    def radio(self):
        radio = MetadataBladeRF()
        radio._lib = MagicMock()
        radio._lib.bladerf_sync_tx.return_value = 0
        radio._tx_enabled = True
        radio.permit = MagicMock()
        radio.permit.data = {"digital_amplitude_max": 0.05}
        return radio

    def test_scheduled_metadata_flags_and_timestamp(self):
        radio = self.radio()
        seen = {}

        def tx(dev, raw, count, mp, timeout):
            meta = ct.cast(mp, ct.POINTER(Metadata)).contents
            seen.update(timestamp=meta.timestamp, flags=meta.flags)
            return 0

        radio._lib.bladerf_sync_tx.side_effect = tx
        radio.scheduled_burst(np.zeros(100, np.int16), timestamp=123456)
        self.assertEqual(
            seen, {"timestamp": 123456, "flags": TX_BURST_START | TX_BURST_END}
        )
        radio.scheduled_burst(np.zeros(100, np.int16))
        self.assertEqual(seen["flags"], TX_BURST_START | TX_BURST_END | TX_NOW)
        radio.permit.consume.assert_called()

    def test_invalid_burst_and_tx_api_failure_force_off(self):
        radio = self.radio()
        with self.assertRaises(ValueError):
            radio.scheduled_burst(np.ones(100, np.int16))
        self.assertFalse(radio._tx_enabled)
        radio = self.radio()
        radio._lib.bladerf_sync_tx.return_value = -6
        radio._lib.bladerf_strerror.return_value = b"timeout"
        with self.assertRaisesRegex(RuntimeError, "-6"):
            radio.scheduled_burst(np.zeros(100, np.int16))
        self.assertFalse(radio._tx_enabled)

    def test_metadata_rx_setup_never_enables_tx(self):
        radio = MetadataBladeRF()
        radio._lib = MagicMock()
        radio._rx_configured = True
        radio.metadata_enabled = True
        radio._lib.bladerf_sync_config.return_value = 0
        radio._lib.bladerf_enable_module.return_value = 0
        radio.start_metadata_rx()
        self.assertFalse(radio._tx_enabled)
        radio._lib.bladerf_sync_config.assert_called_once_with(
            radio._dev, 0, 2, 16, 8192, 8, 1000
        )
        self.assertFalse(
            any(
                call.args[1:] == (1, True)
                for call in radio._lib.bladerf_enable_module.call_args_list
            )
        )

    def test_header_enum_is_checked_instead_of_guessed(self):
        header = "Sample format\ntypedef enum { BLADERF_FORMAT_SC16_Q11, BLADERF_FORMAT_SC16_Q11_PACKED, BLADERF_FORMAT_SC16_Q11_META } format;\nuint8_t reserved[32]; uint32_t flags; uint32_t status; unsigned int actual_count;"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "libbladeRF.h"
            path.write_text(header)
            self.assertEqual(verify_header(path)["abi"]["format_enum"], 2)
            path.write_text(header.replace("BLADERF_FORMAT_SC16_Q11_PACKED,", ""))
            with self.assertRaises(RuntimeError):
                verify_header(path)

    def test_cli_dry_run_does_not_open_radio_and_real_stacking_blocks(self):
        from sww.cli import main

        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "sww.hardware.MetadataBladeRF.open_device",
                side_effect=AssertionError("must not open"),
            ),
        ):
            self.assertEqual(
                main(["plan", "--dry-run", "--report", str(Path(temp) / "plan")]), 0
            )
            self.assertEqual(
                main(
                    [
                        "stacking",
                        "--backend",
                        "hardware",
                        "--report",
                        str(Path(temp) / "blocked"),
                    ]
                ),
                2,
            )
            self.assertEqual(
                main(["tx-only", "--report", str(Path(temp) / "txblocked")]), 2
            )

    def test_motion_pipeline_and_independent_phase_validation(self):
        cfg, records, truth = motion_dataset(Config(noise_rms=0.0003))
        results, metrics = compare(cfg, records)
        self.assertLess(metrics["phase_residual_corrected"]["rms_rad"], 0.03)
        self.assertGreater(metrics["phase_residual_uncorrected"]["rms_rad"], 1)
        self.assertTrue(results["corrected"].metrics["motion_corrected"])

    def test_overlap_weights_and_excluded_bands(self):
        cfg = Config(
            centers_hz=(2.4e9, 2.406e9),
            step_weights=(1, 3),
            overlap_normalize=True,
            excluded_bands_hz=((2.399e9, 2.401e9),),
        )
        records, _ = simulate(cfg, ideal=True)
        result = stack(cfg, records)
        excluded = (result.rf_hz >= 2.399e9) & (result.rf_hz < 2.401e9)
        self.assertFalse(np.any(result.support[excluded]))
        self.assertTrue(np.all(result.spectrum[excluded] == 0))
        self.assertLess(result.metrics["coverage_fraction"], 1)
