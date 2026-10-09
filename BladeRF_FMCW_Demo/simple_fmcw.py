"""Minimal continuous FMCW TX/RX application with a real range FFT."""
from __future__ import annotations

import queue
import sys

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from bladerf_interface import BladeRFError, BladeRFInterface, RxBlock, complex_to_sc16_q11
from fmcw import FmcwParameters, generate_baseband_chirp, generated_rf_frequency_hz, process_fmcw_block, rx_adc_metrics
from fmcw_gui import RxWorker, TxWorker


class SimpleFmcwWindow(QMainWindow):
    """Deliberately small UI: continuous chirp, real RX and one range FFT."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Simple bladeRF FMCW — Continuous Chirps")
        self.resize(1180, 720)
        self.radio = BladeRFInterface()
        self.rx_worker: RxWorker | None = None
        self.tx_worker: TxWorker | None = None
        self.blocks: queue.Queue[RxBlock] = queue.Queue(maxsize=3)
        self.params: FmcwParameters | None = None
        self.reference: np.ndarray | None = None
        self.sample_rate = 40_000_000
        self.tx_active = False
        self.clipping_updates = 0
        self._build()
        self._update_chirp()

    @staticmethod
    def _number(minimum: float, maximum: float, value: float, suffix: str) -> QDoubleSpinBox:
        box = QDoubleSpinBox(); box.setRange(minimum, maximum); box.setValue(value); box.setDecimals(3); box.setSuffix(suffix)
        return box

    def _build(self) -> None:
        root = QWidget(); self.setCentralWidget(root); grid = QGridLayout(root)
        controls = QGroupBox("Continuous FMCW controls"); form = QFormLayout(controls)
        self.start_mhz = self._number(237.5, 3790.0, 2400.0, " MHz")
        self.bandwidth_mhz = self._number(0.1, 28.0, 28.0, " MHz")
        self.duration_ms = self._number(0.05, 100.0, 1.0, " ms")
        self.tx_gain = QSlider(Qt.Orientation.Horizontal); self.tx_gain.setRange(-89, 89); self.tx_gain.setValue(-89)
        self.tx_gain_text = QLabel("Device range pending")
        self.amplitude = QSlider(Qt.Orientation.Horizontal); self.amplitude.setRange(1, 100); self.amplitude.setValue(5)
        self.amplitude_text = QLabel("5%")
        self.rx_gain = QSpinBox(); self.rx_gain.setRange(-1, 60); self.rx_gain.setValue(-1); self.rx_gain.setSuffix(" dB")
        self.max_range = self._number(1.0, 10000.0, 100.0, " m")
        for control in (self.start_mhz, self.bandwidth_mhz, self.duration_ms): control.valueChanged.connect(self._update_chirp)
        self.tx_gain.valueChanged.connect(self._set_tx_gain); self.amplitude.valueChanged.connect(self._set_amplitude)
        form.addRow("RF start", self.start_mhz); form.addRow("Bandwidth", self.bandwidth_mhz); form.addRow("Chirp duration", self.duration_ms)
        form.addRow("TX hardware gain", self._with_text(self.tx_gain, self.tx_gain_text)); form.addRow("TX digital amplitude", self._with_text(self.amplitude, self.amplitude_text))
        form.addRow("RX gain", self.rx_gain); form.addRow("Range display", self.max_range)
        self.confirm = QCheckBox("I confirm authorized TX, suitable antennas, and safe separation.")
        form.addRow(self.confirm)
        row = QHBoxLayout(); self.connect_button = QPushButton("CONNECT"); self.start_button = QPushButton("START CONTINUOUS TX/RX"); self.stop_button = QPushButton("STOP")
        self.start_button.setEnabled(False); self.stop_button.setEnabled(False)
        self.connect_button.clicked.connect(self.connect_radio); self.start_button.clicked.connect(self.start); self.stop_button.clicked.connect(self.stop)
        row.addWidget(self.connect_button); row.addWidget(self.start_button); row.addWidget(self.stop_button); form.addRow(row)
        self.status = QLabel("TX disabled"); self.status.setWordWrap(True); form.addRow(self.status)
        self.adc = QLabel("RX ADC: NO DATA"); self.adc.setWordWrap(True); form.addRow(self.adc)
        grid.addWidget(controls, 0, 0, 2, 1)
        self.chirp_plot = pg.PlotWidget(title="Generated TX Chirp — Not Measured RF")
        self.chirp_plot.setLabel("bottom", "Time", units="ms"); self.chirp_plot.setLabel("left", "RF frequency", units="GHz"); self.chirp_plot.showGrid(x=True, y=True, alpha=0.25)
        self.chirp_curve = self.chirp_plot.plot(pen=pg.mkPen("#ce93d8", width=2)); grid.addWidget(self.chirp_plot, 0, 1)
        self.range_plot = pg.PlotWidget(title="Range FFT — Real RX")
        self.range_plot.setLabel("bottom", "Range", units="m"); self.range_plot.setLabel("left", "Relative magnitude", units="dB"); self.range_plot.showGrid(x=True, y=True, alpha=0.25); self.range_plot.setYRange(-60, 0)
        self.range_curve = self.range_plot.plot(pen=pg.mkPen("#69f0ae", width=2)); grid.addWidget(self.range_plot, 1, 1)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 2)

    @staticmethod
    def _with_text(slider: QSlider, label: QLabel) -> QWidget:
        row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(slider); layout.addWidget(label); return row

    def _parameters(self) -> FmcwParameters:
        params = FmcwParameters(self.start_mhz.value() * 1e6, self.bandwidth_mhz.value() * 1e6,
                                self.duration_ms.value() / 1000.0, 40_000_000)
        params.validate(); return params

    def _update_chirp(self) -> None:
        try:
            params = self._parameters(); time_s, frequency_hz = generated_rf_frequency_hz(params)
            self.chirp_curve.setData(time_s * 1000.0, frequency_hz / 1e9)
        except ValueError:
            self.chirp_curve.clear()

    def _set_amplitude(self, percent: int) -> None:
        self.amplitude_text.setText(f"{percent}%")
        if self.tx_active and self.params and self.tx_worker:
            self.reference = generate_baseband_chirp(self.params, percent / 100.0)
            self.tx_worker.update_waveform(complex_to_sc16_q11(self.reference))

    def _set_tx_gain(self, gain: int) -> None:
        if self.tx_active:
            try:
                self.radio.set_tx_gain(gain); self.tx_gain_text.setText(f"Applied {gain} dB")
            except Exception as error:
                self.fail(error)
        elif self.radio.applied_tx_gain_db is not None:
            self.tx_gain_text.setText(f"Selected {gain} dB")

    def connect_radio(self) -> None:
        try:
            info = self.radio.open_device()
            if not info.fpga_configured: raise RuntimeError("FPGA is not configured")
            self.tx_gain.setRange(*info.tx_gain_range_db); self.tx_gain.setValue(info.tx_gain_range_db[0])
            self.rx_gain.setRange(*info.rx_gain_range_db); self.rx_gain.setValue(info.rx_gain_range_db[0])
            self.tx_gain_text.setText(f"Selected {info.tx_gain_range_db[0]} dB")
            self.status.setText("Connected. TX remains disabled until explicitly started.")
            self.start_button.setEnabled(True)
        except Exception as error: self.fail(error)

    def start(self) -> None:
        if not self.confirm.isChecked():
            QMessageBox.warning(self, "Confirmation required", "Confirm TX authorization and antenna safety first."); return
        if QMessageBox.question(self, "Enable TX?", "Transmit continuous real FMCW chirps now?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        try:
            self.params = self._parameters(); info = self.radio.device_info()
            if self.params.bandwidth_hz > min(info.rx_bandwidth_range_hz[1], info.tx_bandwidth_range_hz[1]): raise ValueError("Bandwidth exceeds hardware capability")
            self.reference = generate_baseband_chirp(self.params, self.amplitude.value() / 100.0)
            centre = int(self.params.lo_frequency_hz); bandwidth = int(self.params.bandwidth_hz)
            rx = self.radio.configure_rx(centre, 40_000_000, bandwidth, self.rx_gain.value())
            tx = self.radio.configure_tx(centre, 40_000_000, bandwidth, self.tx_gain.value())
            if rx.actual_sample_rate_sps != tx.actual_sample_rate_sps: raise RuntimeError("RX/TX sample-rate mismatch")
            self.sample_rate = rx.actual_sample_rate_sps; self.radio.start_streaming(enable_tx=True)
            self.blocks = queue.Queue(maxsize=3); count = 2 * self.params.samples_per_chirp
            self.rx_worker = RxWorker(self.radio, self.blocks, count); self.rx_worker.block_available.connect(self.consume); self.rx_worker.hardware_error.connect(self.fail); self.rx_worker.start()
            self.tx_worker = TxWorker(self.radio, complex_to_sc16_q11(self.reference)); self.tx_worker.hardware_error.connect(self.fail); self.tx_worker.start()
            self.tx_active = True; self.start_button.setEnabled(False); self.stop_button.setEnabled(True)
            self.status.setText(f"Continuous chirps running: {self.params.samples_per_chirp:,} samples/chirp; actual filter {rx.actual_bandwidth_hz:,} Hz.")
        except Exception as error: self.fail(error)

    def consume(self) -> None:
        block: RxBlock | None = None
        while True:
            try: block = self.blocks.get_nowait()
            except queue.Empty: break
        if block is None or self.params is None or self.reference is None: return
        metrics = rx_adc_metrics(block.samples)
        self.adc.setText(f"RX ADC: peak {metrics.peak_dbfs:.1f} dBFS | headroom {metrics.headroom_db:.1f} dB | clipping {metrics.clipping_percent:.3f}%")
        self.clipping_updates = self.clipping_updates + 1 if metrics.clipping_detected else 0
        if self.clipping_updates >= 8:
            self.status.setText("Persistent RX clipping: TX disabled."); self.stop(tx_only=True); return
        try:
            result = process_fmcw_block(block.samples, self.reference, self.params, self.max_range.value())
            # A relative dB FFT of uncorrelated RX noise can look like peaks.
            # It must not be displayed as a distance measurement.
            if result.synced:
                self.range_curve.setData(result.range_m, result.spectrum_db)
                self.range_plot.setTitle("Range FFT — Real RX")
            else:
                self.range_curve.clear()
                self.range_plot.setTitle("Range FFT — WAITING FOR DETECTABLE TX CHIRP IN RX (no range data)")
            sync = f"generated-TX chirp aligned ({result.alignment_confidence:.2f})" if result.synced else f"TX chirp not detectable in RX ({result.alignment_confidence:.2f})"
            peaks = ", ".join(f"{value:.1f} m" for value in result.peak_ranges_m) or "none"
            self.status.setText(f"{sync}; resolution {self.params.range_resolution_m:.2f} m; target peaks: {peaks}")
        except ValueError as error: self.status.setText(f"DSP: {error}")

    def stop(self, tx_only: bool = False) -> None:
        if self.tx_worker: self.tx_worker.request_stop()
        if tx_only:
            try: self.radio.disable_tx()
            except BladeRFError as error: self.fail(error)
            self.tx_active = False; return
        if self.rx_worker: self.rx_worker.request_stop()
        try: self.radio.stop_streaming()
        except BladeRFError as error: self.fail(error)
        for worker in (self.tx_worker, self.rx_worker):
            if worker: worker.wait(1500)
        self.tx_worker = self.rx_worker = None; self.tx_active = False; self.start_button.setEnabled(True); self.stop_button.setEnabled(False)
        self.status.setText("Stopped. TX disabled.")

    def fail(self, error: object) -> None:
        if self.tx_active: self.stop()
        self.status.setText(f"HARDWARE/API ERROR: {error}")
        QMessageBox.critical(self, "FMCW error", str(error))

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.stop(); self.radio.close_device(); event.accept()


if __name__ == "__main__":
    app = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication(sys.argv)
    window = SimpleFmcwWindow(); window.show()
    raise SystemExit(app.exec())
