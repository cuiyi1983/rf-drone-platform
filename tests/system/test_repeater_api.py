"""
TC-SYS-02: Repeater Mode System Test
Tests full session lifecycle via HTTP API (browser-equivalent calls).
"""
import pytest
import subprocess
import sys
import time
import os

import requests


PLATFORM_URL = "http://localhost:5100"
COLLECTOR_URL = "http://localhost:5101"


def is_ready(url, path="/health", timeout=3):
    try:
        return requests.get(f"{url}{path}", timeout=timeout).status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def ensure_services():
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    venv_python = os.path.join(base, ".venv", "bin", "python3")
    if not is_ready(COLLECTOR_URL, "/api/v1/collector/health") or not is_ready(PLATFORM_URL):
        print("\n[conftest] Starting services...")
        os.system("fuser -k 5100/tcp 2>/dev/null; fuser -k 5101/tcp 2>/dev/null")
        time.sleep(1)
        env = os.environ.copy()
        subprocess.Popen(
            [venv_python, "-m", "collector.api", "--port", "5101"],
            cwd=base, stdout=open("/tmp/collector.log","w"), stderr=subprocess.STDOUT, env=env)
        subprocess.Popen(
            [venv_python, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "5100"],
            cwd=base, stdout=open("/tmp/platform.log","w"), stderr=subprocess.STDOUT)
        time.sleep(8)

    for url, path, name in [
        (PLATFORM_URL, "/health", "Platform"),
        (COLLECTOR_URL, "/api/v1/collector/health", "Collector"),
    ]:
        for i in range(20):
            if is_ready(url, path): break
            time.sleep(1)
        else:
            pytest.fail(f"{name} did not become ready")
    print("\n[conftest] All services ready")
    yield


@pytest.fixture(scope="function")
def stop_active_sessions():
    def cleanup():
        try:
            r = requests.get(f"{PLATFORM_URL}/api/v1/session/status", timeout=5)
            if r.status_code == 200:
                for s in r.json().get("sessions", []):
                    if s.get("status") == "running":
                        requests.post(f"{PLATFORM_URL}/api/v1/session/stop",
                            json={"session_id": s["session_id"]}, timeout=10)
        except Exception: pass
    cleanup(); yield; cleanup()


def test_collector_health(ensure_services):
    r = requests.get(f"{COLLECTOR_URL}/api/v1/collector/health", timeout=5)
    assert r.status_code == 200
    assert r.json().get("code") == 0


def test_platform_health(ensure_services):
    r = requests.get(f"{PLATFORM_URL}/health", timeout=5)
    assert r.status_code == 200


def test_repeater_session_lifecycle(ensure_services, stop_active_sessions):
    # Step 1: Load component + apply collector config
    r = requests.post(f"{PLATFORM_URL}/api/v1/collector/apply_component_config", json={
        "source": "ui",
        "component_id": "sim-inference",
        "requirements": {},
        "config": {
            "sample_rate": 2e6,
            "gain": 40,
            "buffer_size": 524288,
        },
    }, timeout=10)
    assert r.status_code == 200, f"apply_component_config failed: {r.text}"
    assert r.json().get("code") == 0, f"apply_component_config error: {r.json()}"

    # Step 2: Connect collector with pluto-repeater
    r = requests.post(f"{PLATFORM_URL}/api/v1/collector/connect", json={
        "device_uri": "file:iq_recording.bin",
    }, timeout=10)
    assert r.status_code == 200, f"collector connect failed: {r.text}"
    assert r.json().get("code") == 0, f"collector connect error: {r.json()}"
    time.sleep(1)

    # Step 3: Start session
    r = requests.post(f"{PLATFORM_URL}/api/v1/session/start",
        json={"component_id": "sim-inference", "config": {"iq_file_path": "IQ-Record/noise_5db_600k.bin"}}, timeout=10)
    assert r.status_code == 200, f"session start failed: {r.text}"
    assert r.json().get("status") == "running", f"session start error: {r.json()}"
    session_id = r.json().get("session_id")
    assert session_id, f"No session_id returned: {r.json()}"

    # Step 4: Poll /latest_result for up to 10 seconds
    result_found = False
    for _ in range(20):
        time.sleep(0.5)
        r = requests.get(f"{PLATFORM_URL}/api/v1/session/{session_id}/latest_result", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if (data.get("result") or {}).get("debug", {}).get("total_inference_count", 0) > 0:
                result_found = True
                assert data.get("result") is not None
                break

    assert result_found, f"No inference results after 10s. Last: {r.text}"

    # Step 5: Stop session
    r = requests.post(f"{PLATFORM_URL}/api/v1/session/stop",
        json={"session_id": session_id}, timeout=10)
    assert r.status_code == 200, f"session stop failed: {r.text}"

    # Step 6: Verify stopped
    r = requests.get(f"{PLATFORM_URL}/api/v1/session/status", timeout=5)
    assert r.status_code == 200
    active = [s for s in r.json().get("sessions", [])
              if s.get("status") == "running" and s.get("session_id") == session_id]
    assert len(active) == 0, f"Session {session_id} still running: {r.json()}"
