"""
TC-SYS-02: Repeater Mode UI System Test
Tests full browser user flow via Playwright headless Chrome.
"""
import pytest
import subprocess
import sys
import time
import os
import re
import socket

import requests
from playwright.sync_api import sync_playwright


PLATFORM_URL = "http://localhost:5100"
FRONTEND_URL = "http://localhost:5102"
COLLECTOR_URL = "http://localhost:5101"


def is_ready(url, path="/health", timeout=3):
    try:
        return requests.get(f"{url}{path}", timeout=timeout).status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def ensure_services():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print("\n[conftest] Killing existing services...")
    os.system("fuser -k 5100/tcp 2>/dev/null; fuser -k 5101/tcp 2>/dev/null; fuser -k 5102/tcp 2>/dev/null")
    time.sleep(1)
    subprocess.Popen([sys.executable, "-m", "collector.api", "--port", "5101"],
        cwd=base, stdout=open("/tmp/collector.log","w"), stderr=subprocess.STDOUT)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "5100"],
        cwd=base, stdout=open("/tmp/platform.log","w"), stderr=subprocess.STDOUT)
    frontend_root = os.path.dirname(base)
    print(f"[conftest] Frontend root: {frontend_root}, exists: {os.path.exists(frontend_root)}")
    p = subprocess.Popen([sys.executable, "-m", "http.server", "5102"],
        cwd=frontend_root, stdout=open("/tmp/frontend.log","w"), stderr=subprocess.STDOUT)
    print(f"[conftest] http.server PID={p.pid}")
    time.sleep(8)
    for url, path, name in [
        (COLLECTOR_URL, "/api/v1/collector/health", "Collector"),
        (PLATFORM_URL, "/health", "Platform"),
    ]:
        for _ in range(20):
            if is_ready(url, path): break
            time.sleep(1)
        else: assert False, f"{name} did not become ready"
    for _ in range(20):
        try:
            r = requests.get(FRONTEND_URL, timeout=2)
            if r.status_code == 200: break
        except:
            pass
        time.sleep(1)
    else:
        assert False, "Frontend did not become ready"
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