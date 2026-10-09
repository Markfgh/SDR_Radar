"""Entry point for the guarded real-hardware FMCW demo."""
from __future__ import annotations

import argparse
import logging
import sys

from bladerf_interface import BladeRFInterface


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="Open the real device and print verified hardware information, then close it.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.probe:
        radio = BladeRFInterface()
        try:
            print(radio.open_device())
            return 0
        finally:
            radio.close_device()
    from PySide6.QtWidgets import QApplication
    from gui import RadarWindow
    app = QApplication(sys.argv)
    window = RadarWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

