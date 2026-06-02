"""
System test fixtures - service lifecycle management.
"""
import pytest
import requests
import subprocess
import time
import sys
import os
import signal

PLATFORM_URL = "http://localhost:5100"
COLLECTOR_URL = "http://localhost:5101"


def is_service_ready(url, path="/health", timeout=3):
    try:
        r = requests.get(f"{url}{path}", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def ensure_services():
    """
    Ensure collector and platform are running.
    If not already running, start them. Track ownership and teardown.
    """
    started = {"collector": False, "platform": False}
    pids = {}

    # Check if already running
    collector_ready = is_service_ready(COLLECTOR_URL, "/api/v1/collector/health")
    platform_ready = is_service_ready(PLATFORM_URL, "/health")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_python = os.path.join(base_dir, ".venv", "bin", "python3")

    if not collector_ready:
        print("\n[conftest] Starting Collector (5101) with COLLECTOR_DEVICE_IMPL=mock...")
        col_proc = subprocess.Popen(
            [venv_python, -m, collector.api, "--port", "5101"],
            cwd=base_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "COLLECTOR_DEVICE_IMPL": "mock"},
        )
        pids["collector"] = col_proc
        started["collector"] = True
        # Wait for collector
        for _ in range(15):
            time.sleep(1)
            if is_service_ready(COLLECTOR_URL, "/api/v1/collector/health"):
                print("[conftest] Collector ready")
                break

    if not platform_ready:
        print("\n[conftest] Starting Platform (5100)...")
        plt_proc = subprocess.Popen(
            [venv_python, -m, uvicorn, "backend.main:app", "--host", "0.0.0.0", "--port", "5100"],
            cwd=base_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "COLLECTOR_DEVICE_IMPL": "mock"},
        )
        pids["platform"] = plt_proc
        started["platform"] = True
        # Wait for platform
        for _ in range(15):
            time.sleep(1)
            if is_service_ready(PLATFORM_URL, "/health"):
                print("[conftest] Platform ready")
                break

    yield

    # Teardown: only kill processes WE started
    for name, proc in pids.items():
        if started.get(name) and proc.poll() is None:
            print(f"\n[conftest] Stopping {name} (pid={proc.pid})")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(autouse=True)
def cleanup_sessions():
    """Stop all running sessions before and after each test."""
    def _cleanup():
        try:
            r = requests.get(f"{PLATFORM_URL}/api/v1/session/status", timeout=5)
            if r.status_code == 200:
                for sess in r.json().get("sessions", []):
                    if sess.get("status") == "running":
                        requests.post(
                            f"{PLATFORM_URL}/api/v1/session/stop",
                            json={"session_id": sess["session_id"]},
                            timeout=10
                        )
        except Exception:
            pass

    _cleanup()
    yield
    _cleanup()
