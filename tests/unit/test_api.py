"""
test_api.py - Unit tests for collector REST API

Uses Flask's test client – no real hardware, no real network needed.
"""

import json
import os
import tempfile

import numpy as np
import pytest

# Must set MOCK before importing collector modules
os.environ["COLLECTOR_DEVICE_IMPL"] = "mock"

from collector.api import CollectorAPI, create_app


@pytest.fixture
def app():
    """Create a test Flask app with mock device."""
    # Patch DEVICE_IMPL before creating the app
    import collector.devices
    collector.devices.DEVICE_IMPL = "mock"
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------
class TestHealth:
    def test_health_ok(self, client):
        res = client.get("/api/v1/collector/health")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0


# ------------------------------------------------------------------
# POST /collector/start
# ------------------------------------------------------------------
class TestStart:
    def test_start_simulator(self, client):
        res = client.post(
            "/api/v1/collector/start",
            json={
                "mode": "simulator",
                "config": {
                    "frequencies": [5_805_000_000],
                    "buffer_size": 4096,
                    "gain": 20.0,
                },
            },
        )
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0
        assert data["message"] == "采集已开始"
        assert "session_id" in data

    def test_start_pluto(self, client):
        res = client.post(
            "/api/v1/collector/start",
            json={
                "mode": "pluto",
                "config": {
                    "frequencies": [5_805_000_000],
                    "buffer_size": 4096,
                    "gain": 20.0,
                },
            },
        )
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0
        assert "session_id" in data

    def test_start_invalid_mode(self, client):
        res = client.post(
            "/api/v1/collector/start",
            json={"mode": "bad_mode", "config": {}},
        )
        assert res.status_code == 400
        data = json.loads(res.data)
        assert data["code"] == 400

    def test_start_default_config(self, client):
        """Empty config should use defaults without crashing."""
        res = client.post(
            "/api/v1/collector/start",
            json={"mode": "simulator", "config": {}},
        )
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0


# ------------------------------------------------------------------
# POST /collector/stop
# ------------------------------------------------------------------
class TestStop:
    def test_stop_valid_session(self, client):
        # Start first
        start_res = client.post(
            "/api/v1/collector/start",
            json={"mode": "simulator", "config": {"frequencies": [5_805_000_000]}},
        )
        sid = json.loads(start_res.data)["session_id"]

        # Stop
        res = client.post(
            "/api/v1/collector/stop",
            json={"session_id": sid},
        )
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0
        assert data["message"] == "采集已停止"
        assert "stats" in data
        assert data["stats"]["total_frames"] >= 0

    def test_stop_unknown_session(self, client):
        res = client.post(
            "/api/v1/collector/stop",
            json={"session_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert res.status_code == 404
        data = json.loads(res.data)
        assert data["code"] == 404

    def test_stop_missing_session_id(self, client):
        res = client.post("/api/v1/collector/stop", json={})
        assert res.status_code == 400


# ------------------------------------------------------------------
# GET /collector/status
# ------------------------------------------------------------------
class TestStatus:
    def test_status_idle(self, client):
        res = client.get("/api/v1/collector/status")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0
        assert data["status"] == "idle"

    def test_status_running(self, client):
        start_res = client.post(
            "/api/v1/collector/start",
            json={"mode": "simulator", "config": {"frequencies": [5_805_000_000]}},
        )
        sid = json.loads(start_res.data)["session_id"]

        res = client.get("/api/v1/collector/status")
        data = json.loads(res.data)
        assert data["status"] == "running"
        assert data["session_id"] == sid

        client.post("/api/v1/collector/stop", json={"session_id": sid})


# ------------------------------------------------------------------
# GET /collector/devices
# ------------------------------------------------------------------
class TestDevices:
    def test_devices_list(self, client):
        res = client.get("/api/v1/collector/devices")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0
        assert "devices" in data
        assert isinstance(data["devices"], list)
        assert len(data["devices"]) >= 1
        for d in data["devices"]:
            assert "id" in d
            assert d["type"] == "pluto"


# ------------------------------------------------------------------
# POST /collector/discover
# ------------------------------------------------------------------
class TestDiscover:
    def test_discover(self, client):
        res = client.post("/api/v1/collector/discover")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert data["code"] == 0
        assert "capabilities" in data
        caps = data["capabilities"]
        assert caps["sample_rate"]["fixed"] == 60_000_000
        assert caps["gain"]["default"] == 20.0
        assert "frequency" in caps
        assert "buffer_size" in caps


# ------------------------------------------------------------------
# POST /collector/simulator/load
# ------------------------------------------------------------------
class TestSimulatorLoad:
    def test_load_file_not_found(self, client):
        res = client.post(
            "/api/v1/collector/simulator/load",
            json={"file_path": "/nonexistent/file.npy"},
        )
        assert res.status_code == 404

    def test_load_missing_file_path(self, client):
        res = client.post("/api/v1/collector/simulator/load", json={})
        assert res.status_code == 400

    def test_load_npy_success(self, client):
        """Create a temp .npy with valid complex64 data and load it."""
        iq = np.random.randn(1000 * 2).astype(np.float32)
        iq_complex = iq[0::2] + 1j * iq[1::2]
        iq_complex = iq_complex.astype(np.complex64)

        with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as f:
            np.save(f, iq_complex)
            fpath = f.name

        try:
            res = client.post(
                "/api/v1/collector/simulator/load",
                json={"file_path": fpath},
            )
            assert res.status_code == 200
            data = json.loads(res.data)
            assert data["code"] == 0
            assert data["metadata"]["sample_count"] == 1000
            assert data["metadata"]["sample_rate"] == 60_000_000
        finally:
            os.unlink(fpath)

    def test_load_bin_success(self, client):
        """Create a temp .bin with interleaved float32 and load it."""
        iq = np.random.randn(2000).astype(np.float32)  # 1000 complex pairs
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            iq.tofile(f)
            fpath = f.name

        try:
            res = client.post(
                "/api/v1/collector/simulator/load",
                json={"file_path": fpath},
            )
            assert res.status_code == 200
            data = json.loads(res.data)
            assert data["code"] == 0
            assert data["metadata"]["sample_count"] == 1000
        finally:
            os.unlink(fpath)