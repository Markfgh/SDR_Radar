"""RX-only diagnostic GUI for Stage 2/3 of the bladeRF FMCW demo."""
from __future__ import annotations

import logging
import threading

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QPushButton,
                               QSpinBox, QTextEdit, QVBoxLayout, QWidget)

from bladerf_interface import BladeRFError, BladeRFInterface, DeviceInfo, RxBlock

LOG = logging.getLogger(__name__)


class RxWorker(QThread):
    block_ready = Signal(object)
    hardware_error = Signal(str)

    def __init__(self, radio: BladeRFInterface, sample_count: int = 8192) -> None:
        super().__init__()
        self._radio = radio
        self._sample_count = sample_count
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                self.block_ready.emit(self._radio.read_rx_samples(self._sample_count))
        except (BladeRFError, RuntimeError) as error:
            LOG.exception("RX acquisition stopped by hardware/API error")
            self.hardware_error.emit(str(error))


class RadarWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("bladeRF FMCW Demo — RX Diagnostics Only")
        self.resize(1050, 700)
        self.radio = BladeRFInterface()
        self.info: DeviceInfo | None = None
        self.worker: RxWorker | None = None
        self.last_sample_rate = 20_000_000
        self._build_ui()

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setSingleStep(max(1, (maximum - minimum) // 100))
        spin.setSuffix(suffix)
        spin.setGroupSeparatorShown(True)
        return spin

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QGridLayout(root)

        control_box = QGroupBox("Stage 2/3: Hardware detection and RX-only acquisition")
        controls = QVBoxLayout(control_box)
        controls.addWidget(QLabel("TX is intentionally unavailable. Receiver tuning does not validate antenna compatibility."))
        form = QFormLayout()
        self.frequency = self._spin(237, 3_800, 2_400, " MHz")
        self.sample_rate = self._spin(520_834, 40_000_000, 20_000_000, " S/s")
        self.bandwidth = self._spin(200_000, 28_000_000, 15_000_000, " Hz")
        self.gain = self._spin(0, 73, 20, " dB")
        form.addRow("RX center frequency", self.frequency)
        form.addRow("Sample rate", self.sample_rate)
        form.addRow("RX bandwidth", self.bandwidth)
        form.addRow("RX gain", self.gain)
        controls.addLayout(form)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton("CONNECT")
        self.start_button = QPushButton("START RX")
        self.stop_button = QPushButton("STOP RX")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.connect_button.clicked.connect(self.connect_device)
        self.start_button.clicked.connect(self.start_rx)
        self.stop_button.clicked.connect(self.stop_rx)
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        controls.addLayout(buttons)
        self.status = QLabel("DISCONNECTED — TX disabled")
        controls.addWidget(self.status)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        controls.addWidget(self.details)
        layout.addWidget(control_box, 0, 0)

        plot_box = QGroupBox("Real RX complex I/Q (latest acquired block)")
        plot_layout = QVBoxLayout(plot_box)
        self.plot = pg.PlotWidget()
        self.plot.addLegend()
        self.plot.setLabel("bottom", "Time", units="ms")
        self.plot.setLabel("left", "Normalized amplitude")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.i_curve = self.plot.plot(pen=pg.mkPen("#4fc3f7", width=1), name="I")
        self.q_curve = self.plot.plot(pen=pg.mkPen("#ffcc80", width=1), name="Q")
        plot_layout.addWidget(self.plot)
        self.measurement = QLabel("No samples acquired")
        plot_layout.addWidget(self.measurement)
        layout.addWidget(plot_box, 0, 1)

    @staticmethod
    def _format_info(info: DeviceInfo) -> str:
        return "\n".join([
            f"Board: {info.board} ({info.fpga_size_kle} KLE)",
            f"Serial: {info.serial}", f"Product: {info.manufacturer} / {info.product}",
            f"Firmware: {info.firmware}", f"FPGA: {info.fpga} — configured: {info.fpga_configured}",
            f"libbladeRF: {info.library}",
            f"RX frequency range: {info.rx_frequency_range_hz[0]}..{info.rx_frequency_range_hz[1]} Hz",
            f"RX sample rate range: {info.rx_sample_rate_range_sps[0]}..{info.rx_sample_rate_range_sps[1]} S/s",
            f"RX bandwidth range: {info.rx_bandwidth_range_hz[0]}..{info.rx_bandwidth_range_hz[1]} Hz",
            f"RX gain range: {info.rx_gain_range_db[0]}..{info.rx_gain_range_db[1]} dB",
        ])

    def connect_device(self) -> None:
        try:
            self.info = self.radio.open_device()
            if not self.info.fpga_configured:
                raise RuntimeError("Device opened, but FPGA is not configured. RX will not be started.")
            self.details.setPlainText(self._format_info(self.info))
            self.status.setText("CONNECTED — RX only; TX remains disabled")
            self.start_button.setEnabled(True)
            LOG.info("Connected to %s serial=%s", self.info.board, self.info.serial)
        except Exception as error:
            LOG.exception("Device connection failed")
            self.status.setText(f"HARDWARE/API ERROR: {error}")
            QMessageBox.critical(self, "bladeRF connection failed", str(error))

    def start_rx(self) -> None:
        try:
            cfg = self.radio.configure_rx(
                self.frequency.value() * 1_000_000, self.sample_rate.value(),
                self.bandwidth.value(), self.gain.value(),
            )
            self.radio.start_streaming()
            self.last_sample_rate = cfg.actual_sample_rate_sps
            self.status.setText(
                f"RX RUNNING — actual rate {cfg.actual_sample_rate_sps:,} S/s, bandwidth {cfg.actual_bandwidth_hz:,} Hz"
            )
            self.worker = RxWorker(self.radio)
            self.worker.block_ready.connect(self.draw_block)
            self.worker.hardware_error.connect(self.rx_failed)
            self.worker.finished.connect(self.rx_finished)
            self.worker.start()
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            LOG.info("Started real RX acquisition: %s", cfg)
        except Exception as error:
            LOG.exception("RX start failed")
            self.status.setText(f"HARDWARE/API ERROR: {error}")
            QMessageBox.critical(self, "RX start failed", str(error))

    def draw_block(self, block: RxBlock) -> None:
        t_ms = np.arange(block.samples.size) * 1000.0 / self.last_sample_rate
        self.i_curve.setData(t_ms, block.samples.real)
        self.q_curve.setData(t_ms, block.samples.imag)
        total = block.samples.size * 2
        self.measurement.setText(
            f"{block.samples.size:,} real RX samples; saturated I/Q components: {block.saturated_components:,}/{total:,}"
        )

    def stop_rx(self) -> None:
        if self.worker:
            self.worker.request_stop()
            self.worker.wait(1500)
            self.worker = None
        try:
            self.radio.stop_streaming()
            self.status.setText("CONNECTED — RX stopped; TX disabled")
        except BladeRFError as error:
            self.status.setText(f"HARDWARE/API ERROR WHILE STOPPING: {error}")
            QMessageBox.critical(self, "RX stop failed", str(error))
        self.start_button.setEnabled(self.info is not None)
        self.stop_button.setEnabled(False)

    def rx_failed(self, message: str) -> None:
        # A failed transfer is terminal for this acquisition. Disable RX and
        # surface both errors if the hardware also rejects the stop request.
        try:
            self.radio.stop_streaming()
        except BladeRFError as stop_error:
            message = f"{message}\n\nAdditionally, RX stop failed: {stop_error}"
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(self.info is not None)
        self.status.setText(f"HARDWARE/API ERROR: {message}")
        QMessageBox.critical(self, "RX acquisition stopped", message)

    def rx_finished(self) -> None:
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(self.info is not None)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.stop_rx()
        self.radio.close_device()
        event.accept()


# The Stage 4+ guarded FMCW window is re-exported here to retain the documented
# project entry-point module name while keeping the RX diagnostic implementation
# available for reference.
from fmcw_gui import RadarWindow as RadarWindow

