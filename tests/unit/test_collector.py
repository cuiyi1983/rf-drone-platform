"""
test_collector.py - Unit tests for collector.py
"""

import threading
import time

import numpy as np
import pytest

from collector.collector import Collector, CollectorConfig, CollectorState, IQFrame
from collector.devices import DEVICE_IMPL, MockPlutoDevice


class TestCollectorConfig:
    def test_default_values(self):
        cfg = CollectorConfig()
        assert cfg.sample_rate == 60_000_000
        assert cfg.buffer_size == 524_288
        assert cfg.gain == 20.0
        assert cfg.hop_interval_ms == 100

    def test_custom_values(self):
        cfg = CollectorConfig(
            frequencies=[5_805_000_000, 2_450_000_000],
            sample_rate=60_000_000,
            buffer_size=262_144,
            gain=30.0,
            hop_interval_ms=200,
        )
        assert len(cfg.frequencies) == 2
        assert cfg.gain == 30.0


class TestCollectorState:
    def test_state_constants(self):
        assert CollectorState.IDLE == "idle"
        assert CollectorState.RUNNING == "running"
        assert CollectorState.ERROR == "error"


class TestCollectorStartStop:
    """Test start/stop session lifecycle."""

    @pytest.fixture
    def collector(self):
        # Force mock device for all collector tests
        import collector.devices
        original = collector.devices.DEVICE_IMPL
        collector.devices.DEVICE_IMPL = "mock"
        c = Collector()
        yield c
        # Cleanup: stop if still running
        if c._state == CollectorState.RUNNING and c._session_id:
            c.stop(c._session_id)
        collector.devices.DEVICE_IMPL = original

    def test_start_pluto_mode(self, collector):
        cfg = CollectorConfig(frequencies=[5_805_000_000])
        session_id = collector.start(mode="pluto", config=cfg)
        assert session_id is not None
        assert len(session_id) == 36  # UUID4 length
        assert collector._state == CollectorState.RUNNING
        collector.stop(session_id)

    def test_start_simulator_mode(self, collector):
        cfg = CollectorConfig(frequencies=[5_805_000_000])
        session_id = collector.start(mode="simulator", config=cfg)
        assert session_id is not None
        assert collector._state == CollectorState.RUNNING
        collector.stop(session_id)

    def test_start_already_running_raises(self, collector):
        cfg = CollectorConfig(frequencies=[5_805_000_000])
        sid = collector.start(mode="simulator", config=cfg)
        with pytest.raises(RuntimeError, match="already running"):
            collector.start(mode="simulator", config=cfg)
        collector.stop(sid)

    def test_stop_returns_stats(self, collector):
        cfg = CollectorConfig(frequencies=[5_805_000_000])
        sid = collector.start(mode="simulator", config=cfg)
        # Let a frame or two fire
        time.sleep(0.05)
        stats = collector.stop(sid)
        assert stats.total_frames >= 0
        assert stats.duration_ms >= 0
        assert collector._state == CollectorState.IDLE

    def test_stop_wrong_session_id_raises(self, collector):
        cfg = CollectorConfig(frequencies=[5_805_000_000])
        sid = collector.start(mode="simulator", config=cfg)
        with pytest.raises(ValueError, match="Unknown session_id"):
            collector.stop("not-the-right-session-id")
        collector.stop(sid)


class TestCollectorStatus:
    @pytest.fixture
    def collector(self):
        import collector.devices
        original = collector.devices.DEVICE_IMPL
        collector.devices.DEVICE_IMPL = "mock"
        c = Collector()
        yield c
        if c._state == CollectorState.RUNNING and c._session_id:
            c.stop(c._session_id)
        collector.devices.DEVICE_IMPL = original

    def test_status_idle(self, collector):
        status = collector.get_status()
        assert status["status"] == CollectorState.IDLE
        assert status["mode"] == "idle"
        assert status["session_id"] is None

    def test_status_running(self, collector):
        cfg = CollectorConfig(frequencies=[5_805_000_000])
        sid = collector.start(mode="simulator", config=cfg)
        status = collector.get_status()
        assert status["status"] == CollectorState.RUNNING
        assert status["mode"] == "simulator"
        assert status["session_id"] == sid
        collector.stop(sid)

    def test_status_device_info_fields(self, collector):
        cfg = CollectorConfig(frequencies=[5_805_000_000])
        sid = collector.start(mode="pluto", config=cfg)
        status = collector.get_status()
        di = status["device_info"]
        assert "uri" in di
        assert "connected" in di
        assert "temperature" in di
        collector.stop(sid)


class TestCollectorDevices:
    @pytest.fixture
    def collector(self):
        import collector.devices
        original = collector.devices.DEVICE_IMPL
        collector.devices.DEVICE_IMPL = "mock"
        c = Collector()
        yield c
        collector.devices.DEVICE_IMPL = original

    def test_get_devices_returns_list(self, collector):
        devs = collector.get_devices()
        assert isinstance(devs, list)
        assert len(devs) >= 1
        for d in devs:
            assert "id" in d
            assert d["type"] == "pluto"

    def test_get_capabilities(self, collector):
        caps = collector.get_capabilities()
        assert "frequency" in caps
        assert "sample_rate" in caps
        assert caps["sample_rate"]["fixed"] == 60_000_000
        assert caps["gain"]["default"] == 20.0


class TestCollectorCallbacks:
    @pytest.fixture
    def collector(self):
        import collector.devices
        original = collector.devices.DEVICE_IMPL
        collector.devices.DEVICE_IMPL = "mock"
        c = Collector()
        yield c
        if c._state == CollectorState.RUNNING and c._session_id:
            c.stop(c._session_id)
        collector.devices.DEVICE_IMPL = original

    def test_frame_callback_invoked(self, collector):
        received: list[IQFrame] = []

        def on_frame(frame: IQFrame):
            received.append(frame)

        collector.on_iq_frame(on_frame)
        cfg = CollectorConfig(frequencies=[5_805_000_000], buffer_size=4096)
        sid = collector.start(mode="simulator", config=cfg)
        # Wait for at least one frame
        time.sleep(0.1)
        collector.stop(sid)

        assert len(received) >= 1
        for f in received:
            assert isinstance(f.frame_id, int)
            assert isinstance(f.timestamp, float)
            assert isinstance(f.iq_data, np.ndarray)


class TestIQFrame:
    def test_iq_frame_structure(self):
        iq = np.array([1 + 2j, 3 + 4j], dtype=np.complex64)
        frame = IQFrame(
            frame_id=1,
            burst_id=0,
            timestamp=123456.0,
            center_freq=5_805_000_000,
            sample_rate=60_000_000,
            iq_data=iq,
            metadata={"rx_buffer_size": 524_288},
        )
        assert frame.frame_id == 1
        assert frame.burst_id == 0
        assert frame.iq_data.shape == (2,)
        assert frame.metadata["rx_buffer_size"] == 524_288