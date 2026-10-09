"""ctypes wrapper for the official libbladeRF 2.6 synchronous streaming API."""
from __future__ import annotations

import ctypes as ct
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


# Values from C:/Program Files/bladeRF/include/libbladeRF.h (libbladeRF 2.6.0).
BLADERF_CHANNEL_RX0 = 0
BLADERF_CHANNEL_TX0 = 1
BLADERF_RX_X1 = 0
BLADERF_TX_X1 = 1
BLADERF_FORMAT_SC16_Q11 = 0
BLADERF_DIRECTION_RX = 0


class BladeRFError(RuntimeError):
    """An error returned directly by libbladeRF; no fallback is attempted."""

    def __init__(self, operation: str, code: int, message: str) -> None:
        self.operation = operation
        self.code = code
        super().__init__(f"{operation} failed ({code}): {message}")


class _Version(ct.Structure):
    _fields_ = [
        ("major", ct.c_uint16),
        ("minor", ct.c_uint16),
        ("patch", ct.c_uint16),
        ("describe", ct.c_char_p),
    ]


class _DevInfo(ct.Structure):
    _fields_ = [
        ("backend", ct.c_int),
        ("serial", ct.c_char * 33),
        ("usb_bus", ct.c_uint8),
        ("usb_addr", ct.c_uint8),
        ("instance", ct.c_uint),
        ("manufacturer", ct.c_char * 33),
        ("product", ct.c_char * 33),
    ]


class _Range(ct.Structure):
    _fields_ = [("min", ct.c_int64), ("max", ct.c_int64), ("step", ct.c_int64)]


@dataclass(frozen=True)
class DeviceInfo:
    board: str
    serial: str
    manufacturer: str
    product: str
    fpga_configured: bool
    fpga_size_kle: int
    firmware: str
    fpga: str
    library: str
    rx_frequency_range_hz: tuple[int, int]
    rx_sample_rate_range_sps: tuple[int, int]
    rx_bandwidth_range_hz: tuple[int, int]
    rx_gain_range_db: tuple[int, int]
    tx_frequency_range_hz: tuple[int, int]
    tx_sample_rate_range_sps: tuple[int, int]
    tx_bandwidth_range_hz: tuple[int, int]
    tx_gain_range_db: tuple[int, int]


@dataclass(frozen=True)
class RxConfiguration:
    center_frequency_hz: int
    requested_sample_rate_sps: int
    actual_sample_rate_sps: int
    requested_bandwidth_hz: int
    actual_bandwidth_hz: int
    gain_db: int


@dataclass(frozen=True)
class RxBlock:
    samples: np.ndarray
    saturated_components: int


@dataclass(frozen=True)
class TxConfiguration:
    center_frequency_hz: int
    requested_sample_rate_sps: int
    actual_sample_rate_sps: int
    requested_bandwidth_hz: int
    actual_bandwidth_hz: int
    gain_db: int


def sc16_q11_to_complex(raw: np.ndarray) -> np.ndarray:
    """Convert interleaved signed SC16_Q11 I,Q values to normalized complex IQ."""
    if raw.dtype != np.int16 or raw.ndim != 1 or raw.size % 2:
        raise ValueError("raw must be a one-dimensional, even-length int16 array")
    return raw[0::2].astype(np.float32) / 2048.0 + 1j * raw[1::2].astype(np.float32) / 2048.0


def complex_to_sc16_q11(samples: np.ndarray) -> np.ndarray:
    """Encode normalized complex samples as interleaved signed SC16_Q11 values."""
    if samples.ndim != 1:
        raise ValueError("samples must be one-dimensional")
    clipped = np.clip(samples, -1.0, 2047.0 / 2048.0)
    raw = np.empty(samples.size * 2, dtype=np.int16)
    raw[0::2] = np.rint(clipped.real * 2048.0).astype(np.int16)
    raw[1::2] = np.rint(clipped.imag * 2048.0).astype(np.int16)
    return raw


class BladeRFInterface:
    """Real bladeRF 1.x interface backed by the vendor's libbladeRF DLL."""

    def __init__(self, dll_path: Optional[str] = None) -> None:
        self._dll_path = dll_path or os.environ.get(
            "BLADERF_DLL", r"C:\Program Files\bladeRF\x64\bladeRF.dll"
        )
        self._lib: Optional[ct.CDLL] = None
        self._dev = ct.c_void_p()
        self._rx_enabled = False
        self._tx_enabled = False
        self._rx_configured = False
        self._tx_configured = False

    def _load_library(self) -> None:
        if self._lib is not None:
            return
        path = Path(self._dll_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"libbladeRF DLL not found: {path}. Set BLADERF_DLL to the compatible DLL."
            )
        # libusb and the thunk DLL reside beside bladeRF.dll in the Nuand install.
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(path.parent))
        self._lib = ct.CDLL(str(path))
        lib = self._lib
        lib.bladerf_open.argtypes = [ct.POINTER(ct.c_void_p), ct.c_char_p]
        lib.bladerf_open.restype = ct.c_int
        lib.bladerf_close.argtypes = [ct.c_void_p]
        lib.bladerf_close.restype = None
        lib.bladerf_strerror.argtypes = [ct.c_int]
        lib.bladerf_strerror.restype = ct.c_char_p
        lib.bladerf_get_devinfo.argtypes = [ct.c_void_p, ct.POINTER(_DevInfo)]
        lib.bladerf_get_devinfo.restype = ct.c_int
        lib.bladerf_get_board_name.argtypes = [ct.c_void_p]
        lib.bladerf_get_board_name.restype = ct.c_char_p
        lib.bladerf_fw_version.argtypes = [ct.c_void_p, ct.POINTER(_Version)]
        lib.bladerf_fw_version.restype = ct.c_int
        lib.bladerf_fpga_version.argtypes = [ct.c_void_p, ct.POINTER(_Version)]
        lib.bladerf_fpga_version.restype = ct.c_int
        lib.bladerf_version.argtypes = [ct.POINTER(_Version)]
        lib.bladerf_version.restype = None
        lib.bladerf_is_fpga_configured.argtypes = [ct.c_void_p]
        lib.bladerf_is_fpga_configured.restype = ct.c_int
        lib.bladerf_get_fpga_size.argtypes = [ct.c_void_p, ct.POINTER(ct.c_int)]
        lib.bladerf_get_fpga_size.restype = ct.c_int
        lib.bladerf_get_frequency_range.argtypes = [ct.c_void_p, ct.c_int, ct.POINTER(ct.POINTER(_Range))]
        lib.bladerf_get_frequency_range.restype = ct.c_int
        lib.bladerf_get_sample_rate_range.argtypes = [ct.c_void_p, ct.c_int, ct.POINTER(ct.POINTER(_Range))]
        lib.bladerf_get_sample_rate_range.restype = ct.c_int
        lib.bladerf_get_bandwidth_range.argtypes = [ct.c_void_p, ct.c_int, ct.POINTER(ct.POINTER(_Range))]
        lib.bladerf_get_bandwidth_range.restype = ct.c_int
        lib.bladerf_get_gain_range.argtypes = [ct.c_void_p, ct.c_int, ct.POINTER(ct.POINTER(_Range))]
        lib.bladerf_get_gain_range.restype = ct.c_int
        lib.bladerf_set_frequency.argtypes = [ct.c_void_p, ct.c_int, ct.c_uint64]
        lib.bladerf_set_frequency.restype = ct.c_int
        lib.bladerf_set_sample_rate.argtypes = [ct.c_void_p, ct.c_int, ct.c_uint32, ct.POINTER(ct.c_uint32)]
        lib.bladerf_set_sample_rate.restype = ct.c_int
        lib.bladerf_set_bandwidth.argtypes = [ct.c_void_p, ct.c_int, ct.c_uint32, ct.POINTER(ct.c_uint32)]
        lib.bladerf_set_bandwidth.restype = ct.c_int
        lib.bladerf_set_gain.argtypes = [ct.c_void_p, ct.c_int, ct.c_int]
        lib.bladerf_set_gain.restype = ct.c_int
        lib.bladerf_sync_config.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_uint, ct.c_uint, ct.c_uint, ct.c_uint]
        lib.bladerf_sync_config.restype = ct.c_int
        lib.bladerf_enable_module.argtypes = [ct.c_void_p, ct.c_int, ct.c_bool]
        lib.bladerf_enable_module.restype = ct.c_int
        lib.bladerf_sync_rx.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_void_p, ct.c_uint]
        lib.bladerf_sync_rx.restype = ct.c_int
        lib.bladerf_sync_tx.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_void_p, ct.c_uint]
        lib.bladerf_sync_tx.restype = ct.c_int
        lib.bladerf_get_timestamp.argtypes = [ct.c_void_p, ct.c_int, ct.POINTER(ct.c_uint64)]
        lib.bladerf_get_timestamp.restype = ct.c_int

    def _check(self, operation: str, result: int) -> None:
        if result == 0:
            return
        assert self._lib is not None
        description = self._lib.bladerf_strerror(result)
        text = description.decode("utf-8", "replace") if description else "unknown libbladeRF error"
        raise BladeRFError(operation, result, text)

    @staticmethod
    def _text(value: bytes) -> str:
        return value.split(b"\0", 1)[0].decode("utf-8", "replace")

    @staticmethod
    def _version(value: _Version) -> str:
        base = f"{value.major}.{value.minor}.{value.patch}"
        return f"{base} ({value.describe.decode('utf-8', 'replace')})" if value.describe else base

    def _range(self, function_name: str, channel: int) -> tuple[int, int]:
        assert self._lib is not None
        pointer = ct.POINTER(_Range)()
        self._check(function_name, getattr(self._lib, function_name)(self._dev, channel, ct.byref(pointer)))
        if not pointer:
            raise RuntimeError(f"{function_name} returned a null range pointer")
        return int(pointer.contents.min), int(pointer.contents.max)

    def open_device(self, identifier: Optional[str] = None) -> DeviceInfo:
        self._load_library()
        assert self._lib is not None
        if self._dev.value:
            return self.device_info()
        encoded = identifier.encode("ascii") if identifier else None
        self._check("bladerf_open", self._lib.bladerf_open(ct.byref(self._dev), encoded))
        try:
            return self.device_info()
        except Exception:
            self.close_device()
            raise

    def device_info(self) -> DeviceInfo:
        if not self._dev.value:
            raise RuntimeError("Device is not open")
        assert self._lib is not None
        devinfo, firmware, fpga, library = _DevInfo(), _Version(), _Version(), _Version()
        self._check("bladerf_get_devinfo", self._lib.bladerf_get_devinfo(self._dev, ct.byref(devinfo)))
        self._check("bladerf_fw_version", self._lib.bladerf_fw_version(self._dev, ct.byref(firmware)))
        self._check("bladerf_fpga_version", self._lib.bladerf_fpga_version(self._dev, ct.byref(fpga)))
        self._lib.bladerf_version(ct.byref(library))
        configured = self._lib.bladerf_is_fpga_configured(self._dev)
        if configured < 0:
            self._check("bladerf_is_fpga_configured", configured)
        fpga_size = ct.c_int()
        self._check("bladerf_get_fpga_size", self._lib.bladerf_get_fpga_size(self._dev, ct.byref(fpga_size)))
        board_name = self._lib.bladerf_get_board_name(self._dev)
        return DeviceInfo(
            board=board_name.decode("utf-8", "replace") if board_name else "unknown",
            serial=self._text(bytes(devinfo.serial)), manufacturer=self._text(bytes(devinfo.manufacturer)),
            product=self._text(bytes(devinfo.product)), fpga_configured=bool(configured),
            fpga_size_kle=fpga_size.value, firmware=self._version(firmware), fpga=self._version(fpga),
            library=self._version(library), rx_frequency_range_hz=self._range("bladerf_get_frequency_range", BLADERF_CHANNEL_RX0),
            rx_sample_rate_range_sps=self._range("bladerf_get_sample_rate_range", BLADERF_CHANNEL_RX0),
            rx_bandwidth_range_hz=self._range("bladerf_get_bandwidth_range", BLADERF_CHANNEL_RX0),
            rx_gain_range_db=self._range("bladerf_get_gain_range", BLADERF_CHANNEL_RX0),
            tx_frequency_range_hz=self._range("bladerf_get_frequency_range", BLADERF_CHANNEL_TX0),
            tx_sample_rate_range_sps=self._range("bladerf_get_sample_rate_range", BLADERF_CHANNEL_TX0),
            tx_bandwidth_range_hz=self._range("bladerf_get_bandwidth_range", BLADERF_CHANNEL_TX0),
            tx_gain_range_db=self._range("bladerf_get_gain_range", BLADERF_CHANNEL_TX0),
        )

    def _configure_channel(self, channel: int, center_frequency_hz: int, sample_rate_sps: int,
                           bandwidth_hz: int, gain_db: int, limits: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]) -> tuple[int, int]:
        """Configure one channel after local validation of its queried capabilities."""
        frequency_range, rate_range, bandwidth_range, gain_range = limits
        requested = {
            "center frequency": (center_frequency_hz, frequency_range, "Hz"),
            "sample rate": (sample_rate_sps, rate_range, "S/s"),
            "bandwidth": (bandwidth_hz, bandwidth_range, "Hz"),
            "gain": (gain_db, gain_range, "dB"),
        }
        for label, (value, limits, unit) in requested.items():
            if not limits[0] <= value <= limits[1]:
                raise ValueError(f"Requested {label} {value} {unit} is outside hardware range {limits[0]}..{limits[1]} {unit}")
        assert self._lib is not None
        channel_name = "RX0" if channel == BLADERF_CHANNEL_RX0 else "TX0"
        self._check(f"bladerf_set_frequency({channel_name})", self._lib.bladerf_set_frequency(self._dev, channel, center_frequency_hz))
        actual_rate, actual_bandwidth = ct.c_uint32(), ct.c_uint32()
        self._check(f"bladerf_set_sample_rate({channel_name})", self._lib.bladerf_set_sample_rate(self._dev, channel, sample_rate_sps, ct.byref(actual_rate)))
        self._check(f"bladerf_set_bandwidth({channel_name})", self._lib.bladerf_set_bandwidth(self._dev, channel, bandwidth_hz, ct.byref(actual_bandwidth)))
        self._check(f"bladerf_set_gain({channel_name})", self._lib.bladerf_set_gain(self._dev, channel, gain_db))
        return actual_rate.value, actual_bandwidth.value

    def configure_rx(self, center_frequency_hz: int, sample_rate_sps: int, bandwidth_hz: int, gain_db: int) -> RxConfiguration:
        """Configure RX only after checking all user values against device ranges."""
        info = self.device_info()
        actual_rate, actual_bandwidth = self._configure_channel(
            BLADERF_CHANNEL_RX0, center_frequency_hz, sample_rate_sps, bandwidth_hz, gain_db,
            (info.rx_frequency_range_hz, info.rx_sample_rate_range_sps, info.rx_bandwidth_range_hz, info.rx_gain_range_db),
        )
        self._rx_configured = True
        return RxConfiguration(center_frequency_hz, sample_rate_sps, actual_rate, bandwidth_hz, actual_bandwidth, gain_db)

    def configure_tx(self, center_frequency_hz: int, sample_rate_sps: int, bandwidth_hz: int, gain_db: int) -> TxConfiguration:
        """Configure TX; this does not enable the transmitter."""
        info = self.device_info()
        actual_rate, actual_bandwidth = self._configure_channel(
            BLADERF_CHANNEL_TX0, center_frequency_hz, sample_rate_sps, bandwidth_hz, gain_db,
            (info.tx_frequency_range_hz, info.tx_sample_rate_range_sps, info.tx_bandwidth_range_hz, info.tx_gain_range_db),
        )
        self._tx_configured = True
        return TxConfiguration(center_frequency_hz, sample_rate_sps, actual_rate, bandwidth_hz, actual_bandwidth, gain_db)

    def configure_device(self, center_frequency_hz: int, sample_rate_sps: int, bandwidth_hz: int,
                         rx_gain_db: int, tx_gain_db: int | None = None) -> tuple[RxConfiguration, TxConfiguration | None]:
        """Configure RX and, only when a TX gain is supplied, TX as well.

        This configures channels only; it cannot enable RF transmission. Call
        ``start_streaming(enable_tx=True)`` separately after the application's
        explicit safety confirmation.
        """
        rx = self.configure_rx(center_frequency_hz, sample_rate_sps, bandwidth_hz, rx_gain_db)
        tx = self.configure_tx(center_frequency_hz, sample_rate_sps, bandwidth_hz, tx_gain_db) if tx_gain_db is not None else None
        return rx, tx

    def get_timestamp(self, direction: int = BLADERF_DIRECTION_RX) -> int:
        """Return the documented coarse FPGA timestamp for a configured direction.

        It is deliberately not used as a sample-alignment claim: the libbladeRF
        header specifies metadata timestamps for active-stream sample positions.
        """
        if not self._dev.value:
            raise RuntimeError("Device is not open")
        assert self._lib is not None
        timestamp = ct.c_uint64()
        self._check("bladerf_get_timestamp", self._lib.bladerf_get_timestamp(self._dev, direction, ct.byref(timestamp)))
        return timestamp.value

    def start_streaming(self, buffer_size: int = 8192, enable_tx: bool = False) -> None:
        if not self._rx_configured:
            raise RuntimeError("Configure RX before starting a stream")
        if enable_tx and not self._tx_configured:
            raise RuntimeError("Configure TX before enabling the transmitter")
        if buffer_size <= 0:
            raise ValueError("buffer_size must be positive")
        assert self._lib is not None
        self._check("bladerf_sync_config(RX)", self._lib.bladerf_sync_config(
            self._dev, BLADERF_RX_X1, BLADERF_FORMAT_SC16_Q11, 16, buffer_size, 8, 1000
        ))
        self._check("bladerf_enable_module(RX0, true)", self._lib.bladerf_enable_module(self._dev, BLADERF_CHANNEL_RX0, True))
        self._rx_enabled = True
        if enable_tx:
            self._check("bladerf_sync_config(TX)", self._lib.bladerf_sync_config(
                self._dev, BLADERF_TX_X1, BLADERF_FORMAT_SC16_Q11, 16, buffer_size, 8, 1000
            ))
            self._check("bladerf_enable_module(TX0, true)", self._lib.bladerf_enable_module(self._dev, BLADERF_CHANNEL_TX0, True))
            self._tx_enabled = True

    def read_rx_samples(self, sample_count: int) -> RxBlock:
        if not self._rx_enabled:
            raise RuntimeError("RX stream is not running")
        if sample_count <= 0:
            raise ValueError("sample_count must be positive")
        assert self._lib is not None
        raw = np.empty(sample_count * 2, dtype=np.int16)
        self._check("bladerf_sync_rx", self._lib.bladerf_sync_rx(
            self._dev, ct.c_void_p(raw.ctypes.data), sample_count, None, 1000
        ))
        saturated = int(np.count_nonzero(np.abs(raw.astype(np.int32)) >= 2047))
        return RxBlock(sc16_q11_to_complex(raw), saturated)

    def write_tx_samples(self, samples: np.ndarray) -> None:
        """Transmit one SC16_Q11 buffer. Caller must explicitly have enabled TX."""
        if not self._tx_enabled:
            raise RuntimeError("TX stream is not enabled")
        if samples.dtype != np.int16 or samples.ndim != 1 or samples.size % 2:
            raise ValueError("TX samples must be an even-length one-dimensional int16 array")
        assert self._lib is not None
        self._check("bladerf_sync_tx", self._lib.bladerf_sync_tx(
            self._dev, ct.c_void_p(samples.ctypes.data), samples.size // 2, None, 1000
        ))

    def stop_streaming(self) -> None:
        # Disable TX first to stop RF emission immediately.
        if self._tx_enabled and self._dev.value:
            assert self._lib is not None
            self._check("bladerf_enable_module(TX0, false)", self._lib.bladerf_enable_module(self._dev, BLADERF_CHANNEL_TX0, False))
        self._tx_enabled = False
        if self._rx_enabled and self._dev.value:
            assert self._lib is not None
            self._check("bladerf_enable_module(RX0, false)", self._lib.bladerf_enable_module(self._dev, BLADERF_CHANNEL_RX0, False))
        self._rx_enabled = False

    def close_device(self) -> None:
        if not self._dev.value:
            return
        try:
            self.stop_streaming()
        finally:
            assert self._lib is not None
            self._lib.bladerf_close(self._dev)
            self._dev = ct.c_void_p()
            self._rx_configured = False
            self._tx_configured = False

