"""
collector.py - IQ acquisition engine

Manages the collection loop:
  - Configures Pluto or simulator based on mode
  - Runs a background thread that continuously reads IQ frames
  - Fires on_iq_frame(frame_dict) callbacks for each captured frame
  - Supports frequency hopping across the configured frequency list
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from collector.devices import DeviceCapabilities, IDevice, MockPlutoDevice, connect_device, discover_devices
from collector.simulator import IQSimulator

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------
@dataclass
class CollectorConfig:
    device_uri: Optional[str] = None   # e.g. "usb:2.6.5"; None = auto-discover
    frequencies: list[int] = field(default_factory=lambda: [5_805_000_000])
    sample_rate: int = 60_000_000
    buffer_size: int = 524_288
    gain: float = 20.0
    hop_interval_ms: int = 100
    iq_file_path: Optional[str] = None  # IQ file path (for repeater mode)
    loop_play: bool = False              # Loop IQ file playback


@dataclass
class IQFrame:
    """
    Single IQ frame emitted to downstream consumers.

    Fields match the collector-api.yaml iq_frame schema.
    """

    frame_id: int
    burst_id: int
    timestamp: float
    center_freq: int
    sample_rate: int
    iq_data: np.ndarray  # complex64
    metadata: dict


@dataclass
class SessionStats:
    total_frames: int = 0
    dropped_frames: int = 0
    duration_ms: float = 0.0


# ------------------------------------------------------------------
# Collector state machine
# ------------------------------------------------------------------
class CollectorState:
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


# ------------------------------------------------------------------
# Collector Engine
# ------------------------------------------------------------------
class Collector:
    """
    IQ acquisition engine.

    Usage:
        collector = Collector()
        collector.on_iq_frame(my_callback)
        session_id = collector.start(mode="pluto", config=cfg)
        collector.stop(session_id)
    """

    def __init__(self):
        # Device handle
        self._device: Optional[IDevice] = None
        # Simulator handle
        self._simulator: Optional[IQSimulator] = None
        # Active session
        self._session_id: Optional[str] = None
        self._state = CollectorState.IDLE
        self._mode: str = "idle"
        self._config: Optional[CollectorConfig] = None
        # Background thread
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Stats
        self._stats = SessionStats()
        # Callbacks: list[Callable[[IQFrame], None]]
        self._callbacks: list[Callable[[IQFrame], None]] = []
        # Frame counters
        self._frame_id: int = 0
        self._burst_id: int = 0
        self._freq_index: int = 0
        self._start_time: Optional[float] = None
        # Device info snapshot
        self._device_info: Optional[dict] = None
        # Persisted IQ file path for pluto-repeater device listing
        self._iq_file_path: Optional[str] = None

    # ------------------------------------------------------------------
    # IQ File path tracking (for pluto-repeater device listing)
    # ------------------------------------------------------------------
    def set_iq_file_path(self, iq_file_path: str) -> None:
        """Set the IQ file path so pluto-repeater appears in get_devices()."""
        self._iq_file_path = iq_file_path
        logger.info(f"Collector: IQ file path set for device listing: {iq_file_path}")

    # ------------------------------------------------------------------
    # Device connection management
    # ------------------------------------------------------------------
    def connect_device(self, uri: str) -> dict:
        """
        Connect to a Pluto device by URI.

        Args:
            uri: Device URI (e.g. "usb:2.6.5")

        Returns:
            dict with device info {uri, type, name, connected, fw_version, temperature}

        Raises:
            RuntimeError: if connection fails
        """
        if self._device is not None:
            logger.info("Device already connected: %s", self._device.get_device_info().id)
            di = self._device.get_device_info()
            return {
                "uri": di.id,
                "type": di.type,
                "name": di.name,
                "connected": di.connected,
                "fw_version": di.fw_version,
                "temperature": di.temperature,
            }

        try:
            self._device = connect_device(uri)
            self._device_info = {
                "uri": uri,
                "connected": True,
                "temperature": self._device.get_temperature(),
            }
            di = self._device.get_device_info()
            logger.info("Connected to device: %s", uri)
            return {
                "uri": di.id,
                "type": di.type,
                "name": di.name,
                "connected": di.connected,
                "fw_version": di.fw_version,
                "temperature": di.temperature,
            }
        except Exception as e:
            logger.error("Device connection failed: %s", e)
            raise RuntimeError(f"连接失败: {e}") from e

    def disconnect_device(self) -> None:
        """Disconnect the currently connected device."""
        if self._device is not None:
            try:
                self._device.disconnect()
            except Exception as e:
                logger.warning("Device disconnect error: %s", e)
            self._device = None
            self._device_info = None
            logger.info("Device disconnected")

    def get_connected_device(self) -> Optional[dict]:
        """Return info dict for the currently connected device, or None."""
        if self._device is None:
            return None
        di = self._device.get_device_info()
        return {
            "uri": di.id,
            "type": di.type,
            "name": di.name,
            "connected": di.connected,
            "fw_version": di.fw_version,
            "temperature": di.temperature,
        }

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def on_iq_frame(self, cb: Callable[[IQFrame], None]) -> None:
        """Register a callback to receive each IQ frame."""
        self._callbacks.append(cb)

    def _emit_frame(self, frame: IQFrame) -> None:
        for cb in self._callbacks:
            try:
                cb(frame)
            except Exception as e:
                logger.warning("IQ frame callback error: %s", e)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    def _apply_config(self, device: IDevice, config: CollectorConfig) -> None:
        """Apply configuration to the device (first frequency only for now)."""
        device.set_frequency(config.frequencies[0])
        device.set_gain(config.gain)
        device.set_buffer_size(config.buffer_size)
        device.set_sample_rate(config.sample_rate)
        logger.info(
            "Collector configured: freq=%d Hz  buffer=%d  gain=%.1f dB",
            config.frequencies[0],
            config.buffer_size,
            config.gain,
        )

    # ------------------------------------------------------------------
    # Public API (matches collector-api.yaml)
    # ------------------------------------------------------------------
    def start(self, mode: str, config: CollectorConfig, force: bool = False) -> str:
        """
        Start acquisition.

        Args:
            mode: "pluto" or "simulator"
            config: CollectorConfig with frequencies / buffer_size / gain / etc.
            force: if True and already running, force-reset before starting.

        Returns:
            session_id (UUID string).

        Raises:
            RuntimeError: if already running (unless force=True)
        """
        if self._state == CollectorState.RUNNING:
            if force:
                self.force_reset()
            else:
                raise RuntimeError("Collector already running")

        self._session_id = str(uuid.uuid4())
        self._mode = mode
        self._config = config
        self._stats = SessionStats()
        self._frame_id = 0
        self._burst_id = 0
        self._freq_index = 0
        self._stop_event.clear()
        self._state = CollectorState.RUNNING
        self._start_time = time.monotonic()

        if mode == "simulator":
            self._simulator = IQSimulator()
            self._device = None
            iq_file = config.iq_file_path if hasattr(config, 'iq_file_path') else None
            if iq_file:
                try:
                    self._simulator.load(iq_file)
                    logger.info(f"Collector: IQ file pre-loaded from config: {iq_file}")
                except Exception as e:
                    logger.warning(f"IQ file pre-load failed (may already be loaded by API): {e}")
            logger.info("Collector session %s started in SIMULATOR mode", self._session_id)
        else:
            # mode == "pluto"
            # Auto-connect if device not yet connected
            if self._device is None:
                if config.device_uri:
                    uri = config.device_uri
                elif len(config.frequencies) > 0:
                    uri = config.frequencies[0]  # URI encoded as first freq (temporary)
                else:
                    uri = "usb:2.6.5"
                try:
                    self._device = connect_device(uri)
                except Exception as e:
                    self._state = CollectorState.ERROR
                    logger.error("Failed to connect to Pluto: %s", e)
                    raise RuntimeError(f"Pluto connection failed: {e}") from e

            try:
                self._apply_config(self._device, config)
                di = self._device.get_device_info()
                self._device_info = {
                    "uri": di.id,
                    "connected": di.connected,
                    "temperature": self._device.get_temperature(),
                }
            except Exception as e:
                self._state = CollectorState.ERROR
                logger.error("Failed to configure Pluto: %s", e)
                raise RuntimeError(f"Pluto configuration failed: {e}") from e
            logger.info("Collector session %s started in PLUTO mode", self._session_id)

        self._thread = threading.Thread(target=self._run_loop, name="collector-loop", daemon=True)
        self._thread.start()
        return self._session_id

    def force_reset(self) -> None:
        """Force reset collector state without session_id check. Used to clear stuck state."""
        logger.info("Force resetting collector")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._device:
            try:
                self._device.disconnect()
            except Exception as e:
                logger.warning("Device disconnect error during force_reset: %s", e)
            self._device = None
        self._simulator = None
        self._state = CollectorState.IDLE
        self._session_id = None
        self._stop_event.clear()
        logger.info("Force reset complete")

    def stop(self, session_id: str) -> SessionStats:
        """
        Stop acquisition for the given session_id.

        Returns SessionStats.
        Raises ValueError if session_id does not match.
        """
        if self._session_id != session_id:
            raise ValueError(f"Unknown session_id: {session_id}")

        logger.info("Stopping collector session %s", session_id)
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

        self._stats.duration_ms = (time.monotonic() - self._start_time) * 1000.0 if self._start_time else 0.0

        if self._device:
            try:
                self._device.disconnect()
            except Exception as e:
                logger.warning("Device disconnect error: %s", e)
            self._device = None

        self._simulator = None
        self._state = CollectorState.IDLE
        self._session_id = None
        return self._stats

    def get_status(self) -> dict:
        """
        Return current status dict (matches collector-api.yaml /collector/status).
        """
        device_info: dict = {}
        current_freq: int = 0
        if self._config:
            current_freq = self._config.frequencies[self._freq_index] if self._config else 0

        if self._device:
            di = self._device.get_device_info()
            device_info = {
                "uri": di.id,
                "connected": di.connected,
                "temperature": di.temperature,
            }
        elif self._mode == "simulator":
            device_info = {"uri": "simulator", "connected": True, "temperature": None}
        else:
            device_info = {"uri": "", "connected": False, "temperature": None}

        return {
            "status": self._state,
            "mode": self._mode,
            "device_info": device_info,
            "current_config": {
                "center_freq": current_freq,
                "sample_rate": (self._config.sample_rate if self._config else 60_000_000),
                "buffer_size": (self._config.buffer_size if self._config else 524_288),
                "gain": (self._config.gain if self._config else 20.0),
            },
            "session_id": self._session_id,
        }

    def get_devices(self) -> list[dict]:
        """Return list of discovered devices (matches /collector/devices)."""
        logger.info("Collector: 触发扫描，来源=get_devices()")
        try:
            infos = discover_devices()
            devices = [
                {
                    "id": d.id,
                    "type": d.type,
                    "name": d.name,
                    "connected": d.connected,
                    "fw_version": d.fw_version,
                }
                for d in infos
            ]
            # If simulator has IQ file loaded OR _iq_file_path is set, include pluto-repeater
            simulator_has_file = (
                self._simulator is not None and hasattr(self._simulator, 'is_loaded')
                and self._simulator.is_loaded()
            )
            if simulator_has_file or self._iq_file_path:
                devices.append({
                    "id": "file:iq_recording.bin",
                    "type": "pluto-repeater",
                    "name": "Pluto-Repeater (IQ File)",
                    "connected": True,
                    "fw_version": "v0.34",
                    "capabilities": {"iq_file_supported": True, "default_iq_dir": "/repo/IQ-Record"},
                })
            return devices
        except Exception as e:
            logger.error("Device discovery failed: %s", e)
            return []

    def get_capabilities(self) -> dict:
        """Return hardware capabilities (matches /collector/discover)."""
        caps = DeviceCapabilities()
        return {
            "frequency": {
                "type": "int",
                "range": [caps.frequency_range[0], caps.frequency_range[1]],
                "default": caps.default_frequency,
            },
            "buffer_size": {
                "type": "int",
                "range": [caps.buffer_size_range[0], caps.buffer_size_range[1]],
                "default": caps.default_buffer_size,
            },
            "gain": {
                "type": "float",
                "range": [caps.gain_range[0], caps.gain_range[1]],
                "default": caps.default_gain,
            },
            "sample_rate": {
                "type": "int",
                "fixed": caps.sample_rate_fixed,
            },
            "rf_bandwidth": {
                "type": "int",
                "fixed": caps.rf_bandwidth_fixed,
            },
        }

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Background thread: reads IQ frames from device or simulator."""
        session_id = self._session_id
        config = self._config
        device = self._device
        simulator = self._simulator
        assert config is not None

        num_freqs = len(config.frequencies)
        last_hop = time.monotonic()
        hop_interval_s = config.hop_interval_ms / 1000.0

        while not self._stop_event.is_set():
            # ---- Hop frequency if needed ----
            if num_freqs > 1 and (time.monotonic() - last_hop) >= hop_interval_s:
                self._freq_index = (self._freq_index + 1) % num_freqs
                next_freq = config.frequencies[self._freq_index]
                if device:
                    device.set_frequency(next_freq)
                logger.debug("Hopped to frequency %d Hz", next_freq)
                last_hop = time.monotonic()

            # ---- Read samples ----
            try:
                if simulator:
                    iq_bytes = simulator.read_chunk_as_bytes(config.buffer_size)
                else:
                    iq_bytes = device.read_samples(config.buffer_size) if device else b""
            except Exception as e:
                logger.error("Sample read error: %s", e)
                self._state = CollectorState.ERROR
                break

            # ---- Parse into complex np.ndarray ----
            raw = np.frombuffer(iq_bytes, dtype=np.float32)
            if raw.size % 2 != 0:
                logger.warning("Incomplete IQ sample count %d, skipping frame", raw.size)
                continue
            iq_data = raw[0::2] + 1j * raw[1::2]

            # ---- Non-looping: stop when file is exhausted ----
            if not config.loop_play and iq_data.size == 0:
                logger.info("IQ file playback complete (loop_play=False)")
                break

            # ---- Build frame dict (to match collector-api.yaml schema) ----
            frame = IQFrame(
                frame_id=self._frame_id,
                burst_id=self._burst_id,
                timestamp=time.time(),
                center_freq=config.frequencies[self._freq_index],
                sample_rate=config.sample_rate,
                iq_data=iq_data.astype(np.complex64),
                metadata={"rx_buffer_size": config.buffer_size},
            )
            self._frame_id += 1
            if self._frame_id % 200 == 0:
                self._burst_id += 1
                self._frame_id = 0

            self._stats.total_frames += 1
            self._emit_frame(frame)

            # Throttle: sleep just enough to avoid busy-spinning.
            # Real Pluto rx() is blocking; simulator needs a small sleep.
            if simulator:
                time.sleep(0.01)