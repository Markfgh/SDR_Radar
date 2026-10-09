"""Guarded real-hardware FMCW GUI. TX remains off until user confirmation."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox, QTextEdit,
                               QVBoxLayout, QWidget)

from bladerf_interface import BladeRFError, BladeRFInterface, DeviceInfo, RxBlock, complex_to_sc16_q11
from calibration import LeakageCalibration
from fmcw import (FmcwParameters, ProcessingResult, generate_baseband_chirp,
                  generated_rf_frequency_hz, process_fmcw_block, rx_adc_metrics)

LOG = logging.getLogger(__name__)


class RxWorker(QThread):
    block_available = Signal()
    hardware_error = Signal(str)

    def __init__(self, radio: BladeRFInterface, sink: queue.Queue[RxBlock], sample_count: int) -> None:
        super().__init__()
        self.radio, self.sink, self.sample_count = radio, sink, sample_count
        self.stop_event = threading.Event()

    def request_stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                block = self.radio.read_rx_samples(self.sample_count)
                try:
                    self.sink.put_nowait(block)
                except queue.Full:
                    self.sink.get_nowait()
                    self.sink.put_nowait(block)
                self.block_available.emit()
        except (BladeRFError, RuntimeError) as error:
            if not self.stop_event.is_set():
                self.hardware_error.emit(str(error))


class TxWorker(QThread):
    hardware_error = Signal(str)

    def __init__(self, radio: BladeRFInterface, waveform: np.ndarray, chunk_samples: int = 8192) -> None:
        super().__init__()
        self.radio, self.waveform, self.chunk_samples = radio, waveform, chunk_samples
        self.stop_event = threading.Event()
        self.waveform_lock = threading.Lock()

    def request_stop(self) -> None:
        self.stop_event.set()

    def update_waveform(self, waveform: np.ndarray) -> None:
        """Atomically replace the SC16_Q11 waveform between TX transfers."""
        with self.waveform_lock:
            self.waveform = waveform

    def run(self) -> None:
        position = 0
        try:
            while not self.stop_event.is_set():
                with self.waveform_lock:
                    waveform = self.waveform
                end = position + self.chunk_samples
                chunk = waveform[position:end] if end <= waveform.size else np.concatenate((waveform[position:], waveform[:end % waveform.size]))
                self.radio.write_tx_samples(chunk)
                position = end % waveform.size
        except (BladeRFError, RuntimeError) as error:
            if not self.stop_event.is_set():
                self.hardware_error.emit(str(error))


class RadarWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("bladeRF FMCW Demo — Experimental")
        self.resize(1400, 900)
        self.radio = BladeRFInterface()
        self.info: DeviceInfo | None = None
        self.rx_worker: RxWorker | None = None
        self.tx_worker: TxWorker | None = None
        self.rx_queue: queue.Queue[RxBlock] = queue.Queue(maxsize=4)
        self.reference: np.ndarray | None = None
        self.params: FmcwParameters | None = None
        self.calibration = LeakageCalibration()
        self.use_calibration = False
        self.latest_rx: np.ndarray | None = None
        self.latest_result: ProcessingResult | None = None
        self.last_sample_rate = 20_000_000
        self.block_counter = 0
        self.tx_active = False
        self._last_adc_update = 0.0
        self._consecutive_clipping_updates = 0
        self._build_ui()
        self._update_derived()

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
        control = QSpinBox(); control.setRange(minimum, maximum); control.setValue(value)
        control.setSingleStep(max(1, (maximum - minimum) // 100)); control.setSuffix(suffix); control.setGroupSeparatorShown(True)
        return control

    @staticmethod
    def _float(minimum: float, maximum: float, value: float, decimals: int, suffix: str) -> QDoubleSpinBox:
        control = QDoubleSpinBox(); control.setRange(minimum, maximum); control.setValue(value)
        control.setDecimals(decimals); control.setSingleStep((maximum - minimum) / 200.0); control.setSuffix(suffix)
        return control

    @staticmethod
    def _control_with_value(control: QWidget, value: QLabel) -> QWidget:
        row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(control); layout.addWidget(value)
        return row

    def _on_tx_gain_changed(self, gain_db: int) -> None:
        if self.tx_active:
            try:
                applied = self.radio.set_tx_gain(gain_db)
                self.tx_gain_value.setText(f"Applied: {applied} dB (hardware gain; not dBm)")
            except Exception as error:
                self._hardware_error("TX gain update", error)
        elif self.info is not None:
            self.tx_gain_value.setText(f"Selected: {gain_db} dB; applied when TX starts")
        else:
            self.tx_gain_value.setText("Device range pending")

    def _on_tx_amplitude_changed(self, percent: int) -> None:
        amplitude = percent / 100.0
        self.tx_amplitude_value.setText(f"Applied: {percent}% ({amplitude:.2f} FS)")
        if self.tx_active and self.params is not None and self.tx_worker is not None:
            try:
                self.reference = generate_baseband_chirp(self.params, amplitude=amplitude)
                self.tx_worker.update_waveform(complex_to_sc16_q11(self.reference))
                self.calibration.clear(); self.use_calibration = False
                self.status.setText("TX digital amplitude updated; leakage calibration cleared. Ranges remain UNSYNCED.")
            except Exception as error:
                self._hardware_error("TX digital amplitude update", error)

    def _set_adc_no_data(self) -> None:
        self.adc_meter.setValue(0); self.adc_meter.setStyleSheet("QProgressBar::chunk { background: #777777; }")
        self.adc_values.setText("NO DATA")

    def _update_adc_monitor(self, samples: np.ndarray) -> None:
        now = time.monotonic()
        if now - self._last_adc_update < 0.125:
            return
        self._last_adc_update = now
        metrics = rx_adc_metrics(samples)
        display_peak = max(-60.0, min(0.0, metrics.peak_dbfs))
        self.adc_meter.setValue(int((display_peak + 60.0) * 100))
        if metrics.clipping_detected:
            color, state = "#c62828", "CRITICAL"
        elif metrics.peak_dbfs > -3.0:
            color, state = "#e53935", "RED"
        elif metrics.peak_dbfs >= -12.0:
            color, state = "#f9a825", "YELLOW"
        else:
            color, state = "#43a047", "GREEN"
        self.adc_meter.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}")
        self.adc_values.setText(f"{state}  Peak: {metrics.peak_dbfs:.1f} dBFS | RMS: {metrics.rms_dbfs:.1f} dBFS | Headroom: {metrics.headroom_db:.1f} dB | Clipping: {metrics.clipping_percent:.3f}%")
        self._consecutive_clipping_updates = self._consecutive_clipping_updates + 1 if metrics.clipping_detected else 0
        if self.tx_active and self._consecutive_clipping_updates >= 8:
            self._disable_tx_for_clipping(metrics.clipping_percent)

    def _disable_tx_for_clipping(self, clipping_percent: float) -> None:
        if self.tx_worker:
            self.tx_worker.request_stop()
        try:
            self.radio.disable_tx()
        except BladeRFError as error:
            self._hardware_error("Automatic TX disable after clipping", error)
            return
        self.tx_active = False
        self.calibrate_button.setEnabled(False); self.raw_button.setEnabled(False)
        self.status.setText(f"CRITICAL RX clipping persisted; TX was automatically disabled. Clipping: {clipping_percent:.3f}%. RX remains active.")
        QMessageBox.warning(self, "TX disabled: RX clipping", "Persistent near-full-scale RX ADC components were detected. TX has been disabled; reduce TX/RX gain or increase attenuation before continuing.")

    def _build_ui(self) -> None:
        root = QWidget(); self.setCentralWidget(root); layout = QGridLayout(root)
        box = QGroupBox("Configuration — no automatic TX"); controls = QVBoxLayout(box); form = QFormLayout()
        self.rf_start = self._float(237.5, 3790.0, 2400.0, 3, " MHz")
        self.chirp_bw = self._float(0.1, 28.0, 10.0, 3, " MHz")
        self.chirp_duration = self._float(0.05, 100.0, 1.0, 3, " ms")
        self.sample_rate = self._spin(80_000, 40_000_000, 20_000_000, " S/s")
        self.tx_gain = QSlider(Qt.Orientation.Horizontal); self.tx_gain.setRange(-89, 89); self.tx_gain.setValue(self.tx_gain.minimum())
        self.tx_gain_value = QLabel("Device range pending")
        self.tx_amplitude = QSlider(Qt.Orientation.Horizontal); self.tx_amplitude.setRange(1, 100); self.tx_amplitude.setValue(5)
        self.tx_amplitude_value = QLabel()
        self.rx_gain = self._spin(-1, 60, -1, " dB")
        self.max_range = self._float(0.1, 10000.0, 100.0, 1, " m")
        for widget in (self.rf_start, self.chirp_bw, self.chirp_duration, self.sample_rate, self.rx_gain, self.max_range):
            widget.valueChanged.connect(self._update_derived)
        self.tx_gain.valueChanged.connect(self._on_tx_gain_changed)
        self.tx_amplitude.valueChanged.connect(self._on_tx_amplitude_changed)
        for label, widget in (("RF start frequency", self.rf_start), ("Chirp bandwidth", self.chirp_bw), ("Chirp duration", self.chirp_duration), ("Sample rate", self.sample_rate), ("TX Hardware Gain [dB]", self._control_with_value(self.tx_gain, self.tx_gain_value)), ("TX Digital Amplitude [%]", self._control_with_value(self.tx_amplitude, self.tx_amplitude_value)), ("RX gain", self.rx_gain), ("Maximum range display", self.max_range)):
            form.addRow(label, widget)
        controls.addLayout(form); self.derived = QLabel(); self.derived.setWordWrap(True); controls.addWidget(self.derived)
        self.tx_confirm = QCheckBox("I verified legal TX authorization, compatible antennas/band, and safe separation; enable low-power TX.")
        self.tx_confirm.setToolTip("This explicit acknowledgement is required before TX can be enabled."); controls.addWidget(self.tx_confirm)
        self.adc_box = QGroupBox("RX ADC Monitor — pre-DSP SC16_Q11")
        adc_layout = QVBoxLayout(self.adc_box)
        self.adc_meter = QProgressBar(); self.adc_meter.setRange(0, 6000); self.adc_meter.setValue(0); self.adc_meter.setTextVisible(False)
        self.adc_values = QLabel("NO DATA")
        self.adc_note = QLabel("0 dBFS = one full-scale I or Q ADC component. This is not RF power in dBm or guaranteed hardware protection.")
        self.adc_note.setWordWrap(True)
        adc_layout.addWidget(self.adc_meter); adc_layout.addWidget(self.adc_values); adc_layout.addWidget(self.adc_note)
        controls.addWidget(self.adc_box); self._set_adc_no_data()
        self._on_tx_amplitude_changed(self.tx_amplitude.value())
        buttons = QGridLayout(); self.connect_button = QPushButton("CONNECT"); self.rx_button = QPushButton("START RX"); self.txrx_button = QPushButton("START TX/RX"); self.stop_button = QPushButton("STOP"); self.calibrate_button = QPushButton("CALIBRATE LEAKAGE"); self.raw_button = QPushButton("SHOW RAW"); self.save_button = QPushButton("SAVE")
        for button in (self.rx_button, self.txrx_button, self.stop_button, self.calibrate_button, self.raw_button, self.save_button): button.setEnabled(False)
        self.connect_button.clicked.connect(self.connect_device); self.rx_button.clicked.connect(self.start_rx_only); self.txrx_button.clicked.connect(self.start_tx_rx); self.stop_button.clicked.connect(self.stop_all); self.calibrate_button.clicked.connect(self.capture_calibration); self.raw_button.clicked.connect(self.toggle_raw); self.save_button.clicked.connect(self.save_capture)
        for row, col, button in ((0,0,self.connect_button),(0,1,self.rx_button),(1,0,self.txrx_button),(1,1,self.stop_button),(2,0,self.calibrate_button),(2,1,self.raw_button),(3,0,self.save_button)):
            buttons.addWidget(button, row, col, 1, 2 if row == 3 else 1)
        controls.addLayout(buttons); self.status = QLabel("DISCONNECTED — TX disabled"); self.status.setWordWrap(True); controls.addWidget(self.status)
        self.details = QTextEdit(); self.details.setReadOnly(True); controls.addWidget(self.details); layout.addWidget(box, 0, 0, 2, 1)
        self.tx_plot = pg.PlotWidget(title="Generated reference waveform — not measured RF output"); self.tx_plot.setLabel("bottom", "Time", units="ms"); self.tx_plot.setLabel("left", "RF frequency", units="MHz"); self.tx_plot.showGrid(x=True, y=True, alpha=0.25); self.tx_curve = self.tx_plot.plot(pen=pg.mkPen("#ab47bc", width=2)); layout.addWidget(self.tx_plot, 0, 1)
        self.rx_plot = pg.PlotWidget(title="Real RX complex I/Q (latest block)"); self.rx_plot.addLegend(); self.rx_plot.setLabel("bottom", "Time", units="ms"); self.rx_plot.setLabel("left", "Normalized amplitude"); self.rx_plot.showGrid(x=True, y=True, alpha=0.25); self.i_curve = self.rx_plot.plot(pen=pg.mkPen("#4fc3f7", width=1), name="I"); self.q_curve = self.rx_plot.plot(pen=pg.mkPen("#ffcc80", width=1), name="Q"); layout.addWidget(self.rx_plot, 1, 1)
        self.range_plot = pg.PlotWidget(title="Range FFT — experimental"); self.range_plot.setLabel("bottom", "Range", units="m"); self.range_plot.setLabel("left", "Relative magnitude", units="dB"); self.range_plot.showGrid(x=True, y=True, alpha=0.25); self.range_curve = self.range_plot.plot(pen=pg.mkPen("#66bb6a", width=2)); layout.addWidget(self.range_plot, 0, 2, 2, 1)
        self.measurement = QLabel("No samples acquired. Range output is UNSYNCED until experimentally validated."); self.measurement.setWordWrap(True); layout.addWidget(self.measurement, 2, 0, 1, 3)

    def _parameters(self) -> FmcwParameters:
        params = FmcwParameters(self.rf_start.value() * 1e6, self.chirp_bw.value() * 1e6, self.chirp_duration.value() / 1000.0, float(self.sample_rate.value())); params.validate(); return params

    def _update_derived(self) -> None:
        try:
            p = self._parameters(); self.derived.setText(f"Stop: {p.stop_frequency_hz / 1e6:.3f} MHz\nLO center: {p.lo_frequency_hz / 1e6:.3f} MHz\nSlope: {p.slope_hz_per_s / 1e12:.3f} THz/s\nSamples/chirp: {p.samples_per_chirp:,}\nTheoretical range resolution: {p.range_resolution_m:.3f} m")
            time_s, frequency_hz = generated_rf_frequency_hz(p); self.tx_curve.setData(time_s * 1000.0, frequency_hz / 1e6)
        except ValueError as error: self.derived.setText(f"INVALID: {error}")

    @staticmethod
    def _format_info(info: DeviceInfo) -> str:
        return "\n".join((f"Board: {info.board} ({info.fpga_size_kle} KLE)", f"Serial: {info.serial}", f"Firmware: {info.firmware}", f"FPGA: {info.fpga} — configured: {info.fpga_configured}", f"libbladeRF: {info.library}", f"RX: frequency {info.rx_frequency_range_hz}, sample rate {info.rx_sample_rate_range_sps}, bandwidth {info.rx_bandwidth_range_hz}, gain {info.rx_gain_range_db}", f"TX: frequency {info.tx_frequency_range_hz}, sample rate {info.tx_sample_rate_range_sps}, bandwidth {info.tx_bandwidth_range_hz}, gain {info.tx_gain_range_db}", "Timing: SC16_Q11, no scheduled metadata transfer; absolute range is UNSYNCED."))

    def connect_device(self) -> None:
        try:
            self.info = self.radio.open_device()
            if not self.info.fpga_configured: raise RuntimeError("Device opened but FPGA is not configured.")
            self.rx_gain.setRange(*self.info.rx_gain_range_db); self.rx_gain.setValue(self.info.rx_gain_range_db[0]); self.tx_gain.setRange(*self.info.tx_gain_range_db); self.tx_gain.setValue(self.info.tx_gain_range_db[0])
            self.details.setPlainText(self._format_info(self.info)); self.status.setText("CONNECTED — TX disabled. RX-only diagnostics are available."); self.rx_button.setEnabled(True); self.txrx_button.setEnabled(True)
        except Exception as error: self._hardware_error("Device connection", error)

    def _start_workers(self, tx_waveform: np.ndarray | None) -> None:
        sample_count = max(32_768, self.params.samples_per_chirp if self.params else 0); self.rx_queue = queue.Queue(maxsize=4)
        self.rx_worker = RxWorker(self.radio, self.rx_queue, sample_count); self.rx_worker.block_available.connect(self.consume_rx); self.rx_worker.hardware_error.connect(lambda message: self._hardware_error("RX acquisition", message)); self.rx_worker.start()
        if tx_waveform is not None:
            self.tx_worker = TxWorker(self.radio, tx_waveform); self.tx_worker.hardware_error.connect(lambda message: self._hardware_error("TX streaming", message)); self.tx_worker.start()

    def start_rx_only(self) -> None:
        try:
            self.params = self._parameters(); filter_bw = max(int(self.params.bandwidth_hz * 1.25), 1_500_000)
            cfg = self.radio.configure_rx(int(self.params.lo_frequency_hz), int(self.params.sample_rate_sps), filter_bw, self.rx_gain.value()); self.last_sample_rate = cfg.actual_sample_rate_sps
            self.radio.start_streaming(enable_tx=False); self.tx_active = False; self._start_workers(None); self._set_running(True); self.status.setText(f"RX RUNNING — actual {cfg.actual_sample_rate_sps:,} S/s, {cfg.actual_bandwidth_hz:,} Hz; TX disabled. No range processing is presented in RX-only mode.")
        except Exception as error: self._hardware_error("RX start", error)

    def start_tx_rx(self) -> None:
        if not self.tx_confirm.isChecked():
            QMessageBox.warning(self, "TX confirmation required", "Confirm RF authorization, antenna compatibility, and safe separation before enabling TX."); return
        if QMessageBox.question(self, "Enable real TX?", "Enable low-power real bladeRF TX now? This transmits the generated chirp through TX0.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        try:
            self.params = self._parameters(); reference = generate_baseband_chirp(self.params, amplitude=self.tx_amplitude.value() / 100.0); filter_bw = max(int(self.params.bandwidth_hz * 1.25), 1_500_000)
            rx_cfg = self.radio.configure_rx(int(self.params.lo_frequency_hz), int(self.params.sample_rate_sps), filter_bw, self.rx_gain.value()); tx_cfg = self.radio.configure_tx(int(self.params.lo_frequency_hz), int(self.params.sample_rate_sps), filter_bw, self.tx_gain.value())
            if rx_cfg.actual_sample_rate_sps != tx_cfg.actual_sample_rate_sps or rx_cfg.actual_sample_rate_sps != self.params.sample_rate_sps: raise RuntimeError("RX/TX actual sample rates differ from requested rate; no waveform will be transmitted.")
            self.reference = reference; self.last_sample_rate = rx_cfg.actual_sample_rate_sps; self.radio.start_streaming(enable_tx=True); self.tx_active = True; self.tx_gain_value.setText(f"Applied: {tx_cfg.gain_db} dB (hardware gain; not dBm)"); self._on_tx_amplitude_changed(self.tx_amplitude.value()); self.calibration.clear(); self.use_calibration = False; self._start_workers(complex_to_sc16_q11(reference)); self._set_running(True); self.status.setText(f"TX/RX RUNNING — actual {rx_cfg.actual_sample_rate_sps:,} S/s, RX/TX bandwidth {rx_cfg.actual_bandwidth_hz:,}/{tx_cfg.actual_bandwidth_hz:,} Hz. UNSYNCED: displayed ranges are experimental/relative until validated.")
        except Exception as error: self._hardware_error("TX/RX start", error)

    def _set_running(self, running: bool) -> None:
        self.connect_button.setEnabled(not running); self.rx_button.setEnabled(self.info is not None and not running); self.txrx_button.setEnabled(self.info is not None and not running); self.stop_button.setEnabled(running); self.calibrate_button.setEnabled(running and self.tx_active); self.raw_button.setEnabled(running and self.tx_active); self.save_button.setEnabled(running)

    def consume_rx(self) -> None:
        newest: RxBlock | None = None
        while True:
            try: newest = self.rx_queue.get_nowait()
            except queue.Empty: break
        if newest is None: return
        self.latest_rx = newest.samples; time_ms = np.arange(newest.samples.size) * 1000.0 / self.last_sample_rate; self.i_curve.setData(time_ms, newest.samples.real); self.q_curve.setData(time_ms, newest.samples.imag)
        self._update_adc_monitor(newest.samples)
        message = f"{newest.samples.size:,} real RX samples; saturated I/Q components: {newest.saturated_components:,}/{newest.samples.size * 2:,}."; self.block_counter += 1
        if self.tx_active and self.reference is not None and self.params is not None and self.block_counter % 5 == 0:
            try:
                cal = self.calibration.spectrum if self.use_calibration else None; self.latest_result = process_fmcw_block(newest.samples, self.reference, self.params, self.max_range.value(), cal); self.range_curve.setData(self.latest_result.range_m, self.latest_result.spectrum_db)
                peaks = ", ".join(f"{value:.2f} m" for value in self.latest_result.peak_ranges_m) or "none"; mode = "calibrated background-subtracted" if self.use_calibration else "raw"; message += f" UNSYNCED {mode} profile; alignment offset {self.latest_result.offset_samples} samples; peaks: {peaks}."
            except ValueError as error: message += f" FMCW processing skipped: {error}"
        self.measurement.setText(message)

    def capture_calibration(self) -> None:
        if self.latest_result is None: QMessageBox.information(self, "Calibration pending", "Wait for an acquired and processed TX/RX block, then capture leakage calibration."); return
        self.calibration.capture(self.latest_result); self.use_calibration = True; self.raw_button.setText("SHOW RAW"); self.status.setText(f"Leakage reference captured at offset {self.calibration.offset_samples}; subtraction enabled. Ranges remain UNSYNCED.")

    def toggle_raw(self) -> None:
        if not self.calibration.active: QMessageBox.information(self, "No calibration", "Capture a leakage reference first."); return
        self.use_calibration = not self.use_calibration; self.raw_button.setText("SHOW CALIBRATED" if not self.use_calibration else "SHOW RAW")

    def save_capture(self) -> None:
        if self.latest_rx is None: return
        output = Path(__file__).resolve().parent / "captures"; output.mkdir(exist_ok=True); target = output / f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.npz"; config = {"parameters": vars(self.params) if self.params else {}, "tx_active": self.tx_active, "calibration_active": self.use_calibration, "unsynced": True}
        np.savez_compressed(target, rx_iq=self.latest_rx, range_m=self.latest_result.range_m if self.latest_result else np.array([]), range_db=self.latest_result.spectrum_db if self.latest_result else np.array([]), configuration=json.dumps(config)); self.status.setText(f"Saved real RX I/Q and current result: {target}")

    def stop_all(self) -> None:
        for worker in (self.tx_worker, self.rx_worker):
            if worker: worker.request_stop()
        try: self.radio.stop_streaming()  # implementation disables TX before RX
        except BladeRFError as error: self.status.setText(f"HARDWARE/API ERROR WHILE STOPPING: {error}")
        for worker in (self.tx_worker, self.rx_worker):
            if worker: worker.wait(1500)
        self.tx_worker = self.rx_worker = None; self.tx_active = False; self._set_running(False)
        self._consecutive_clipping_updates = 0; self._set_adc_no_data()
        if self.info is not None: self.status.setText("CONNECTED — streams stopped; TX disabled.")

    def _hardware_error(self, context: str, error: object) -> None:
        text = str(error); LOG.error("%s: %s", context, text)
        if self.tx_active or self.rx_worker: self.stop_all()
        self.status.setText(f"HARDWARE/API ERROR — {context}: {text}"); QMessageBox.critical(self, "bladeRF error", f"{context}\n\n{text}")

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.stop_all(); self.radio.close_device(); event.accept()
