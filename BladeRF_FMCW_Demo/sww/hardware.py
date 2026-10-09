"""Opt-in metadata backend, reusing the functioning legacy driver unchanged.

No constructor, import, probe or RX call enables TX. TX diagnostics require a
bounded, operator supplied closed-loopback permit. No OTA/switch TX implementation.
"""

import ctypes as ct
import hashlib
import re
import time
from pathlib import Path

import numpy as np

from bladerf_interface import BLADERF_CHANNEL_RX0, BLADERF_CHANNEL_TX0, BladeRFInterface

from .timing import (
    RX_NOW,
    SC16_Q11_META,
    TX_BURST_END,
    TX_BURST_START,
    Metadata,
    check_abi,
)
from .types import AcquisitionRecord


def verify_header(path=r"C:\Program Files\bladeRF\include\libbladeRF.h"):
    text = Path(path).read_text(encoding="utf-8")
    # Check actual enum order, not an assumed constant shared by all library releases.
    block = text[text.index("typedef enum {", text.index("Sample format")) :]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL).split("}", 1)[0]
    names = re.findall(r"\bBLADERF_FORMAT_[A-Z0-9_]+\b", block)
    if names.index("BLADERF_FORMAT_SC16_Q11_META") != SC16_Q11_META:
        raise RuntimeError("Header enum differs: metadata backend must be regenerated")
    required = (
        "uint8_t reserved[32]",
        "uint32_t flags",
        "uint32_t status",
        "unsigned int actual_count",
    )
    if not all(s in text for s in required):
        raise RuntimeError("Metadata header layout differs")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "abi": check_abi(),
    }


class MetadataBladeRF(BladeRFInterface):
    def __init__(self, dll_path=None, header_path=None):
        super().__init__(dll_path)
        self.header_path = header_path
        self.metadata_enabled = False
        self.permit = None

    def prepare_metadata(self):
        evidence = (
            verify_header(self.header_path) if self.header_path else verify_header()
        )
        if not self.device_info().library.startswith("2.6.0"):
            raise RuntimeError(
                "Metadata backend requires verified 2.6.0 DLL/header pair"
            )
        self.metadata_enabled = True
        self._lib.bladerf_get_frequency.argtypes = [
            ct.c_void_p,
            ct.c_int,
            ct.POINTER(ct.c_uint64),
        ]
        self._lib.bladerf_get_frequency.restype = ct.c_int
        return evidence

    def actual_frequency(self, channel=BLADERF_CHANNEL_RX0):
        result = ct.c_uint64()
        self._check(
            "bladerf_get_frequency",
            self._lib.bladerf_get_frequency(self._dev, channel, ct.byref(result)),
        )
        return result.value

    def force_tx_off(self):
        # Force an API disable even if the legacy local flag is false after opening.
        with self._control_lock:
            if self._dev.value:
                self._check(
                    "bladerf_enable_module(TX0,false)",
                    self._lib.bladerf_enable_module(
                        self._dev, BLADERF_CHANNEL_TX0, False
                    ),
                )
            self._tx_enabled = False

    def start_metadata_rx(self, buffer_size=8192, timeout_ms=1000):
        if not self._rx_configured or not self.metadata_enabled:
            raise RuntimeError("Configure RX and verify ABI first")
        if buffer_size % 1024:
            raise ValueError("Stream buffer must be a multiple of 1024")
        self.force_tx_off()
        self._check(
            "sync_config(RX META)",
            self._lib.bladerf_sync_config(
                self._dev, 0, SC16_Q11_META, 16, buffer_size, 8, timeout_ms
            ),
        )
        self._check("enable RX", self._lib.bladerf_enable_module(self._dev, 0, True))
        self._rx_enabled = True

    def read_metadata(
        self, count, step=0, mode="RX_ONLY", timestamp=None, timeout_ms=1000
    ):
        if not self._rx_enabled or not self.metadata_enabled:
            raise RuntimeError("Metadata RX is not active")
        if count <= 0:
            raise ValueError("Positive count required")
        raw = np.empty(count * 2, np.int16)
        meta = Metadata()
        meta.flags = RX_NOW if timestamp is None else 0
        meta.timestamp = 0 if timestamp is None else timestamp
        self._check(
            "sync_rx META",
            self._lib.bladerf_sync_rx(
                self._dev,
                ct.c_void_p(raw.ctypes.data),
                count,
                ct.byref(meta),
                timeout_ms,
            ),
        )
        if meta.actual_count > count:
            raise RuntimeError("Invalid metadata actual_count")
        # Undefined buffer tail is never exposed or saved.
        raw = raw[: 2 * meta.actual_count].copy()
        return AcquisitionRecord(
            step,
            mode,
            float(self.actual_frequency()),
            raw,
            int(meta.timestamp),
            int(meta.status),
            int(meta.actual_count),
            "HARDWARE",
            {
                "timestamp_source": "FPGA_RX_METADATA",
                "digital_epoch_known": False,
                "host_monotonic_s": time.monotonic(),
            },
        )

    def start_permitted_tx(self, permit, config, buffer_size=8192):
        permit.check(config)
        if not self.metadata_enabled or not self._tx_configured:
            raise RuntimeError("Configure metadata TX first")
        self.permit = permit
        try:
            self._check(
                "sync_config(TX META)",
                self._lib.bladerf_sync_config(
                    self._dev, 1, SC16_Q11_META, 16, buffer_size, 8, 1000
                ),
            )
            self._check(
                "enable permitted TX",
                self._lib.bladerf_enable_module(self._dev, 1, True),
            )
            self._tx_enabled = True
        except BaseException:
            self.force_tx_off()
            raise

    def scheduled_burst(self, raw, timestamp=None):
        if not self._tx_enabled or self.permit is None:
            raise RuntimeError("TX not armed")
        try:
            self.permit.check_time()
            self.permit.consume()
            if raw.dtype != np.int16 or raw.ndim != 1 or raw.size % 2 or raw.size < 4:
                raise ValueError("Invalid TX IQ")
            if np.any(raw[-4:] != 0):
                raise ValueError(
                    "Scheduled burst must end with two zero complex samples (Nuand)"
                )
            if np.max(np.abs(raw.astype(np.int32))) > np.ceil(
                self.permit.data["digital_amplitude_max"] * 2048
            ):
                raise ValueError("TX amplitude exceeds permit")
            from .timing import TX_NOW

            meta = Metadata()
            meta.timestamp = 0 if timestamp is None else int(timestamp)
            meta.flags = (
                TX_BURST_START | TX_BURST_END | (TX_NOW if timestamp is None else 0)
            )
            self._check(
                "sync_tx META",
                self._lib.bladerf_sync_tx(
                    self._dev,
                    ct.c_void_p(raw.ctypes.data),
                    raw.size // 2,
                    ct.byref(meta),
                    1000,
                ),
            )
            if meta.status:
                raise RuntimeError(f"TX metadata status {meta.status}")
        except BaseException:
            self.force_tx_off()
            raise
        return {
            "scheduled_timestamp": timestamp,
            "flags": int(meta.flags),
            "status": int(meta.status),
            "epoch_relation_verified": False,
        }


class LoopbackPermit:
    """Operator evidence is mandatory, never populated from simulation defaults."""

    def __init__(self, data):
        self.data = data
        self.started = time.monotonic()
        self.bursts = 0

    def check_time(self):
        if time.monotonic() - self.started > self.data["max_duration_s"]:
            raise TimeoutError("TX permit expired")

    def check(self, config):
        d = self.data
        required = (
            "operator_approved",
            "closed_terminated_loopback",
            "power_budget_verified",
            "environment_verified",
        )
        if not all(d.get(k) is True for k in required):
            raise PermissionError("RF preflight incomplete")
        if d.get("topology") != "closed_loopback" or not d.get("evidence"):
            raise PermissionError("Closed loopback evidence required")
        if (
            not 0 < d.get("digital_amplitude_max", 0) <= 0.05
            or config.amplitude > d["digital_amplitude_max"]
        ):
            raise PermissionError("Invalid digital amplitude budget")
        if (
            not 0 < d.get("max_duration_s", 0) <= 60
            or not 0 < d.get("max_bursts", 0) <= 1000
        ):
            raise PermissionError("Invalid finite RF budget")
        if not isinstance(d.get("tx_gain_db"), int):
            raise PermissionError("Exact TX gain required")
        import math

        powers = (d.get("estimated_max_rx_dbm"), d.get("verified_rx_limit_dbm"))
        if (
            not all(isinstance(v, (int, float)) and math.isfinite(v) for v in powers)
            or powers[0] >= powers[1]
        ):
            raise PermissionError("Verified analog RF margin required")
        band = d.get("allowed_band_hz", [])
        if len(band) != 2 or not all(
            band[0] <= fc - config.bi_hz / 2 and fc + config.bi_hz / 2 <= band[1]
            for fc in config.centers_hz
        ):
            raise PermissionError("RF band outside permit")
        self.check_time()

    def consume(self):
        self.check_time()
        self.bursts += 1
        if self.bursts > self.data["max_bursts"]:
            raise PermissionError("TX burst budget exceeded")
