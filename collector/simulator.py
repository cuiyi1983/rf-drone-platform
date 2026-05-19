"""
simulator.py - IQ data file simulator

Loads pre-recorded IQ data from .npy or .bin files and exposes it
as a numpy array for use by the collector loop.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SimulatorMetadata:
    sample_count: int
    sample_rate: float
    duration_ms: float
    center_freq: Optional[float] = None


class IQSimulator:
    """
    Loads IQ data from a file and provides it in chunks.

    Supported formats:
      *.npy   – numpy uncompressed array (complex64)
      *.bin   – raw interleaved float32 (real/imag pairs)
    """

    def __init__(self):
        self._data: Optional[np.ndarray] = None
        self._metadata: Optional[SimulatorMetadata] = None
        self._pos: int = 0
        self._sample_rate: float = 60e6

    # ------------------------------------------------------------------
    # Public API (matches collector-api.yaml)
    # ------------------------------------------------------------------
    def load(self, file_path: str) -> SimulatorMetadata:
        """
        Load IQ data from file_path.

        Returns SimulatorMetadata on success.
        Raises FileNotFoundError / ValueError on failure.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Simulator file not found: {file_path}")

        if path.suffix.lower() == ".npy":
            self._data = np.load(path)
        elif path.suffix.lower() == ".bin":
            raw = np.fromfile(path, dtype=np.float32)
            if raw.size % 2 != 0:
                raise ValueError(f"BIN file size {raw.size} is not even (real+imag pairs)")
            self._data = raw[0::2] + 1j * raw[1::2]
        else:
            raise ValueError(f"Unsupported simulator format: {path.suffix}")

        if self._data.dtype != np.complex64:
            self._data = self._data.astype(np.complex64)

        total_samples = self._data.shape[0]
        self._pos = 0

        # Infer sample rate and center frequency from config file if available
        config_path = path.with_suffix("").parent / (path.stem + ".config")
        if not config_path.exists():
            config_path = path.with_suffix(".config")
        if config_path.exists():
            try:
                import json
                cfg = json.loads(config_path.read_text())
                self._sample_rate = cfg.get("sample_rate", 60e6)
                self._metadata.center_freq = cfg.get("center_freq")
            except Exception:
                pass

        duration_ms = (total_samples / self._sample_rate) * 1000.0
        self._metadata = SimulatorMetadata(
            sample_count=total_samples,
            sample_rate=self._sample_rate,
            duration_ms=duration_ms,
        )
        logger.info(
            "Simulator loaded: %s  samples=%d  rate=%.1f MHz  duration=%.1f ms",
            file_path,
            total_samples,
            self._sample_rate / 1e6,
            duration_ms,
        )
        return self._metadata

    def read_chunk(self, num_samples: int) -> np.ndarray:
        """
        Return the next num_samples complex samples.
        Wraps around to the start when EOF is reached (looping playback).
        """
        if self._data is None:
            return np.array([], dtype=np.complex64)

        if self._pos >= self._data.shape[0]:
            self._pos = 0  # Loop

        end = min(self._pos + num_samples, self._data.shape[0])
        chunk = self._data[self._pos:end]
        self._pos = end
        return chunk

    def read_chunk_as_bytes(self, num_samples: int) -> bytes:
        """Same as read_chunk but returns interleaved float32 bytes."""
        chunk = self.read_chunk(num_samples)
        iq = np.empty(chunk.size * 2, dtype=np.float32)
        iq[0::2] = chunk.real.astype(np.float32)
        iq[1::2] = chunk.imag.astype(np.float32)
        return iq.tobytes()

    @property
    def metadata(self) -> Optional[SimulatorMetadata]:
        return self._metadata

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value: float) -> None:
        self._sample_rate = value

    def reset(self) -> None:
        """Reset read position to start."""
        self._pos = 0

    def is_loaded(self) -> bool:
        return self._data is not None