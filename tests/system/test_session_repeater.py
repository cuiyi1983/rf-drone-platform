"""
TC-SYS-01: Repeater Session Full Lifecycle
"""
import pytest
import requests

PLATFORM_URL = "http://localhost:5100"


class TestRepeaterSession:

    def test_tc_sys01_services_health(self, ensure_services):
        """TC-SYS-01.1: Platform and collector are healthy."""
        resp = requests.get(f"{PLATFORM_URL}/health", timeout=5)
        assert resp.status_code == 200
        col_resp = requests.get(f"{PLATFORM_URL}/api/v1/collector/health", timeout=5)
        assert col_resp.status_code == 200

    def test_tc_sys02_device_list_has_repeater(self, ensure_services):
        """TC-SYS-01.2: Device list includes pluto-repeater."""
        resp = requests.get(f"{PLATFORM_URL}/api/v1/devices", timeout=10)
        resp.raise_for_status()
        devices = resp.json().get("devices", [])
        repeater = [d for d in devices if d.get("type") == "pluto-repeater"]
        assert len(repeater) > 0, f"pluto-repeater not found in {devices}"

    def test_tc_sys03_start_stop_session(self, ensure_services):
        """TC-SYS-01.3: Start repeater session, verify running, stop, verify stats."""
        # Start session
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "sim-inference",
                "config": {"iq_file_path": "IQ-Record/noise_5db_600k.bin", "loop_play": True}
            },
            timeout=10
        )
        assert resp.status_code == 200, f"Start failed: {resp.status_code} {resp.text}"
        data = resp.json()
        session_id = data["session_id"]
        assert data.get("status") == "running", f"Expected running, got: {data.get('status')}"

        # Status API returns the session directly (not wrapped in sessions array)
        status_resp = requests.get(
            f"{PLATFORM_URL}/api/v1/session/status?session_id={session_id}", timeout=5)
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        # When queried with session_id, API returns the session directly
        if "sessions" in status_data:
            sessions = status_data["sessions"]
            assert len(sessions) > 0
            assert sessions[0].get("status") == "running"
        else:
            # API returns session directly
            assert status_data.get("session_id") == session_id
            assert status_data.get("status") == "running"

        # Stop session
        stop_resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/stop",
            json={"session_id": session_id}, timeout=10)
        assert stop_resp.status_code == 200
        stop_data = stop_resp.json()
        assert stop_data.get("status") == "stopped"
        assert "stats" in stop_data, f"Stats missing: {stop_data}"

    def test_tc_sys04_invalid_iq_file(self, ensure_services):
        """TC-SYS-01.4: Invalid IQ file returns HTTP 400."""
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "sim-inference",
                "config": {"iq_file_path": "IQ-Record/nonexistent_file.bin", "loop_play": True}
            },
            timeout=10
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
