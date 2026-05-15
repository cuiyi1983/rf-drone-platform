"""
devices.py - Pluto SDR device management

All Pluto hardware operations go through this module.
Real hardware is swapped for a mock via DEVICE_IMPL config.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Device configuration
# -----------------------------------------------------------------------------
# Override with "mock" for unit testing only (never in production)
DEVICE_IMPL: str = "pluto"


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------
@dataclass
class DeviceInfo:
    id: str
    type: str
    name: str
    connected: bool
    fw_version: Optional[str] = None
    temperature: Optional[float] = None
    serial: Optional[str] = None


@dataclass
class DeviceCapabilities:
    frequency_range: tuple[int, int] = (325_000_000, 6_000_000_000)
    buffer_size_range: tuple[int, int] = (1024, 1_048_576)
    gain_range: tuple[float, float] = (0.0, 60.0)
    sample_rate_fixed: int = 60_000_000
    rf_bandwidth_fixed: int = 56_000_000
    default_frequency: int = 5_805_000_000
    default_buffer_size: int = 524_288
    default_gain: float = 20.0


# -----------------------------------------------------------------------------
# Abstract base (allows mock injection)
# -----------------------------------------------------------------------------
class IDevice(ABC):
    """Abstract Pluto device interface."""

    @abstractmethod
    def discover() -> list[DeviceInfo]:
        """Scan and return list of available Pluto devices."""
        raise NotImplementedError

    @abstractmethod
    def connect(uri: str) -> "IDevice":
        """Connect to a specific device by URI."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect and release the device."""
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if currently connected."""
        raise NotImplementedError

    @abstractmethod
    def set_frequency(self, freq_hz: int) -> None:
        """Set centre frequency in Hz."""
        raise NotImplementedError

    @abstractmethod
    def set_gain(self, gain_db: float) -> None:
        """Set RF gain in dB."""
        raise NotImplementedError

    @abstractmethod
    def set_buffer_size(self, size: int) -> None:
        """Set rx buffer size in samples."""
        raise NotImplementedError

    @abstractmethod
    def set_sample_rate(self, rate: int) -> None:
        """Set sample rate in Hz (informational – Pluto fixes to 60 MHz)."""
        raise NotImplementedError

    @abstractmethod
    def read_samples(self, num_samples: Optional[int] = None) -> bytes:
        """Read IQ samples from the device. Returns raw bytes (complex interleaved)."""
        raise NotImplementedError

    @abstractmethod
    def get_temperature(self) -> Optional[float]:
        """Query device temperature in Celsius."""
        raise NotImplementedError

    @abstractmethod
    def get_device_info(self) -> DeviceInfo:
        """Return DeviceInfo for the connected device."""
        raise NotImplementedError

    @abstractmethod
    def get_capabilities(self) -> DeviceCapabilities:
        """Return hardware capabilities."""
        raise NotImplementedError


# -----------------------------------------------------------------------------
# Mock implementation (used when DEVICE_IMPL = "mock")
# -----------------------------------------------------------------------------
class MockPlutoDevice(IDevice):
    """In-memory mock for unit testing without hardware."""

    def __init__(self):
        self._connected = False
        self._uri: str = ""
        self._frequency = 5_805_000_000
        self._gain = 20.0
        self._buffer_size = 524_288
        self._sample_rate = 60_000_000
        self._temperature = 42.0

    @classmethod
    def discover(cls) -> list[DeviceInfo]:
        # Return two mock devices for discover test coverage
        return [
            DeviceInfo(
                id="usb:2.6.5",
                type="pluto",
                name="ADALM PLUTO",
                connected=False,
                fw_version="v0.34",
            ),
            DeviceInfo(
                id="usb:2.10.5",
                type="pluto",
                name="ADALM PLUTO",
                connected=False,
                fw_version="v0.34",
            ),
        ]

    @classmethod
    def connect(cls, uri: str) -> "MockPlutoDevice":
        dev = cls()
        dev._connected = True
        dev._uri = uri
        logger.debug("Mock Pluto connected: %s", uri)
        return dev

    def disconnect(self) -> None:
        self._connected = False
        self._uri = ""

    def is_connected(self) -> bool:
        return self._connected

    def set_frequency(self, freq_hz: int) -> None:
        self._frequency = freq_hz
        logger.debug("Mock freq set: %d Hz", freq_hz)

    def set_gain(self, gain_db: float) -> None:
        self._gain = gain_db
        logger.debug("Mock gain set: %.1f dB", gain_db)

    def set_buffer_size(self, size: int) -> None:
        self._buffer_size = size
        logger.debug("Mock buffer_size set: %d", size)

    def set_sample_rate(self, rate: int) -> None:
        # Pluto ignores external sample rate; always 60 MHz
        self._sample_rate = rate
        logger.debug("Mock sample_rate set (informational): %d Hz", rate)

    def read_samples(self, num_samples: Optional[int] = None) -> bytes:
        if num_samples is None:
            num_samples = self._buffer_size
        # Return num_samples complex float32 pairs (real, imag) as bytes
        import numpy as np

        iq = np.random.randn(num_samples * 2).astype(np.float32)
        return iq.tobytes()

    def get_temperature(self) -> Optional[float]:
        return self._temperature

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            id=self._uri or "mock",
            type="pluto",
            name="ADALM PLUTO",
            connected=self._connected,
            fw_version="v0.34",
            temperature=self._temperature,
            serial="MOCKSN001",
        )

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities()


# -----------------------------------------------------------------------------
# Real Pluto implementation (used when DEVICE_IMPL = "pluto")
# -----------------------------------------------------------------------------
class PlutoDevice(IDevice):
    """Wrapper around adi.Pluto for real hardware."""

    def __init__(self, uri: str):
        import adi

        self._uri = uri
        self._sdr: adi.Pluto = adi.Pluto(uri)
        self._frequency = 5_805_000_000
        self._gain = 20.0
        self._buffer_size = 524_288

    @classmethod
    def discover(cls) -> list[DeviceInfo]:
        import adi

        try:
            ctx = adi.context()
            devices = []
            for uri in ctx.uri():
                try:
                    sdr = adi.Pluto(uri)
                    fw = getattr(sdr, "fw_version", "unknown")
                    SN = getattr(sdr, "serial", None)
                    devices.append(
                        DeviceInfo(
                            id=uri,
                            type="pluto",
                            name="ADALM PLUTO",
                            connected=True,
                            fw_version=fw,
                            serial=SN,
                        )
                    )
                except Exception:
                    pass
            return devices
        except Exception as e:
            logger.warning("Pluto discover failed: %s", e)
            return []

    @classmethod
    def connect(cls, uri: str) -> "PlutoDevice":
        dev = cls(uri)
        dev._connected = True
        return dev

    def disconnect(self) -> None:
        if hasattr(self, "_sdr"):
            del self._sdr

    def is_connected(self) -> bool:
        return getattr(self, "_connected", False)

    def set_frequency(self, freq_hz: int) -> None:
        self._sdr.rx_lo = int(freq_hz)
        self._frequency = freq_hz

    def set_gain(self, gain_db: float) -> None:
        self._sdr.rx_gain = float(gain_db)
        self._gain = gain_db

    def set_buffer_size(self, size: int) -> None:
        self._sdr.rx_buffer_size = size
        self._buffer_size = size

    def set_sample_rate(self, rate: int) -> None:
        # Pluto ignores – always 60 MHz – but record for info
        self._sdr.sample_rate = int(rate)
        self._sample_rate = rate

    def read_samples(self, num_samples: Optional[int] = None) -> bytes:
        import numpy as np

        if num_samples is None:
            num_samples = self._buffer_size
        raw = self._sdr.rx(num_samples)
        # Convert complex to interleaved float32 bytes
        iq = np.empty(raw.nbytes, dtype=np.float32)
        iq[0::2] = raw.real.astype(np.float32)
        iq[1::2] = raw.imag.astype(np.float32)
        return iq.tobytes()

    def get_temperature(self) -> Optional[float]:
        try:
            return float(self._sdr.temperature)
        except Exception:
            return None

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            id=self._uri,
            type="pluto",
            name="ADALM PLUTO",
            connected=self.is_connected(),
            fw_version=getattr(self._sdr, "fw_version", "unknown"),
            serial=getattr(self._sdr, "serial", None),
            temperature=self.get_temperature(),
        )

    def get_capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities()


# -----------------------------------------------------------------------------
# Factory
# -----------------------------------------------------------------------------
def get_device_class() -> type[IDevice]:
    if DEVICE_IMPL == "pluto":
        return PlutoDevice
    return MockPlutoDevice


def discover_devices() -> list[DeviceInfo]:
    """Scan for available Pluto devices."""
    return get_device_class().discover()


def connect_device(uri: str) -> IDevice:
    """Connect to a Pluto device by URI."""
    return get_device_class().connect(uri)