"""Separate PySide6 diagnostics for simulation/replay. No RF enable controls."""

import sys
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import load_config
from .experiments import compare
from .metrics import db
from .simulation import simulate
from .storage import replay


class SwwWindow(QMainWindow):
    def __init__(self, capture=None, config_path=None):
        super().__init__()
        self.setWindowTitle("Prager SWW — simulation / offline diagnostics")
        self.resize(1200, 850)
        # Bundled font also works with Qt's offscreen Windows platform, whose
        # system-font lookup may be unavailable in restricted environments.
        import matplotlib

        font_path = (
            Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"
        )
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            QApplication.instance().setFont(QFont(families[0], 10))
        self.config_path = config_path
        self.records = []
        self.results = {}
        self.capture = capture
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        buttons = QHBoxLayout()
        for text, callback in (
            ("Simulate", self.run_simulation),
            ("Open capture", self.open_capture),
            ("Load YAML", self.open_config),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        self.step = QComboBox()
        self.step.currentIndexChanged.connect(self.draw_iq)
        buttons.addWidget(self.step)
        layout.addLayout(buttons)
        self.status = QLabel("PHASE_UNVERIFIED — no hardware acquisition performed")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.plots = {}
        for title, x, y in (
            ("Raw I/Q", "Sample index", "SC16_Q11 / 2048"),
            ("Reference phase", "Step", "Transfer phase (rad)"),
            ("Single chirp", "Range (m)", "Amplitude (dB)"),
            ("Synthetic spectrum", "RF (MHz)", "Magnitude"),
            ("SWW range", "Range (m)", "Amplitude (dB)"),
            ("Single chirp dechirp", "Relative range (m)", "Amplitude (dB)"),
        ):
            plot = pg.PlotWidget()
            plot.setLabel("bottom", x)
            plot.setLabel("left", y)
            plot.addLegend()
            plot.showGrid(x=True, y=True, alpha=0.25)
            self.tabs.addTab(plot, title)
            self.plots[title] = plot
        if capture:
            self.load_capture(capture)

    def open_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Configuration", "configs", "YAML (*.yaml *.yml)"
        )
        if path:
            self.config_path = path

    def run_simulation(self):
        try:
            self.config = load_config(self.config_path)
            self.records, _ = simulate(self.config)
            self.process()
        except Exception as error:
            self.failed(error)

    def open_capture(self):
        path = QFileDialog.getExistingDirectory(
            self, "Capture containing manifest.json"
        )
        if path:
            self.load_capture(path)

    def load_capture(self, path):
        try:
            self.config, self.records, manifest = replay(path)
            if manifest["state"] != "COMPLETED":
                raise ValueError(
                    "Capture aborted/incomplete: inspect raw and error.json"
                )
            if manifest["origin"] != "SIMULATED":
                self.step.blockSignals(True)
                self.step.clear()
                for r in self.records:
                    self.step.addItem(f"{r.step} {r.mode}")
                self.step.blockSignals(False)
                self.status.setText(
                    "HARDWARE — PHASE_UNVERIFIED; raw I/Q only until G1 is qualified"
                )
                for p in self.plots.values():
                    p.clear()
                self.draw_iq(0)
                return
            self.process()
        except Exception as error:
            self.failed(error)

    def failed(self, error):
        self.status.setText(f"FAIL: {error}")
        QMessageBox.critical(self, "SWW processing", str(error))

    def process(self):
        self.results, metrics = compare(self.config, self.records)
        self.step.blockSignals(True)
        self.step.clear()
        for r in self.records:
            self.step.addItem(f"{r.step} {r.mode}")
        self.step.blockSignals(False)
        self.draw_iq(0)
        self.status.setText(
            f"SIMULATED / TESTED_OFFLINE — hardware PHASE_UNVERIFIED; residual {metrics['phase_residual_corrected']['rms_rad']:.4f} rad"
        )
        for title in self.plots:
            if title != "Raw I/Q":
                self.plots[title].clear()
        for name, color in (
            ("single", "#ffa657"),
            ("uncorrected", "#ff6464"),
            ("ideal", "#87d787"),
            ("corrected", "#58bfff"),
        ):
            r = self.results[name]
            use = (r.range_m >= 0) & (r.range_m <= self.config.max_range_m)
            self.plots["SWW range"].plot(
                r.range_m[use], db(r.profile)[use], pen=color, name=name
            )
        r = self.results["single"]
        use = (r.range_m >= 0) & (r.range_m <= self.config.max_range_m)
        self.plots["Single chirp"].plot(
            r.range_m[use], db(r.profile)[use], pen="#ffa657"
        )
        r = self.results["corrected"]
        use = r.support
        self.plots["Synthetic spectrum"].plot(
            r.rf_hz[use] / 1e6, np.abs(r.spectrum[use]), pen="#58bfff"
        )
        cal = r.metrics["calibration"]
        self.plots["Reference phase"].plot(
            [c["step"] for c in cal],
            [c["transfer_phase_rad"] for c in cal],
            symbol="o",
            pen="#58bfff",
        )
        from .waveforms import chirp

        x = chirp(self.config)
        y = next(r.iq for r in self.records if r.mode == "RADAR" and r.step == 0)
        beat = y[: len(x)] * np.conj(x)
        freq = np.fft.fftfreq(8 * len(x), 1 / self.config.fs_sps)
        ranges = -299792458 * freq / (2 * self.config.bi_hz / self.config.tp_s)
        fft = np.fft.fft(beat * np.hanning(len(x)), n=8 * len(x))
        use = (ranges >= 0) & (ranges <= self.config.max_range_m)
        order = np.argsort(ranges[use])
        self.plots["Single chirp dechirp"].plot(
            ranges[use][order],
            db(fft)[use][order],
            pen="#ffa657",
            name="Simulated known digital epoch; uncalibrated RF delay",
        )

    def draw_iq(self, index):
        if not self.records or index < 0:
            return
        p = self.plots["Raw I/Q"]
        p.clear()
        iq = self.records[index].iq
        p.plot(iq.real, pen="#58bfff", name="I")
        p.plot(iq.imag, pen="#ffa657", name="Q")


def launch(capture=None, config_path=None):
    app = QApplication.instance() or QApplication(sys.argv)
    window = SwwWindow(capture, config_path)
    window.show()
    return app.exec()
