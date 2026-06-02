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
    frontend_root = os.path.join(os.path.dirname(base), "frontend")
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


def test_page_loads(ensure_services):
    """TC-SYS-02.1: Web UI loads successfully."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        resp = page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=15000)
        assert resp and resp.status == 200
        assert page.locator("nav.topnav").count() > 0
        assert "RF Drone Platform" in page.locator(".brand").inner_text()
        browser.close()


def test_navbar_tabs(ensure_services):
    """TC-SYS-02.2: Tab navigation switches pages."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=15000)
        assert page.locator("#pg-obs").is_visible()
        page.evaluate('document.querySelector("#tabs .nav-link[data-pg=\\"cfg\\"]").click()')
        page.wait_for_timeout(500)
        assert page.locator("#pg-cfg").is_visible()
        page.evaluate('document.querySelector("#tabs .nav-link[data-pg=\\"obs\\"]").click()')
        page.wait_for_timeout(500)
        assert page.locator("#pg-obs").is_visible()
        browser.close()


def test_repeater_session_stats(ensure_services, stop_active_sessions):
    """
    TC-SYS-02.3: Start repeater session via UI with sim-inference + noise file,
    then verify ALL stats in the observation page:

      - 实时推理统计表: 检测结果=NOISE, Drone%%=0.0%%, 推理ms!=空
      - 缓冲区监控: 总帧数持续增加, 采集状态=采集中
      - 当前配置: 推理组件=sim-inference
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=15000)

        # === CONFIG PAGE: Select pluto-repeater, load sim-inference, connect ===
        page.evaluate('document.querySelector("#tabs .nav-link[data-pg=\\"cfg\\"]").click()')
        page.wait_for_timeout(1000)

        # Wait for device list
        for _ in range(15):
            opts = page.locator("#deviceSel option").all_inner_texts()
            if any("pluto-repeater" in o for o in opts): break
            page.wait_for_timeout(1000)
        else:
            pytest.fail(f"pluto-repeater not in device list: {opts}")

        # Select pluto-repeater
        opt_vals = page.evaluate(
            "Array.from(document.querySelectorAll('#deviceSel option'))"
            ".map(o=>({v:o.value,t:o.textContent}))"
        )
        rpt = next((o for o in opt_vals if "pluto-repeater" in o["v"].lower()), None)
        assert rpt, f"pluto-repeater not found in options: {opt_vals}"
        page.locator("#deviceSel").select_option(rpt["v"])
        page.wait_for_timeout(800)

        # IQ path auto-filled (noise file by default)
        iq = page.locator("#iqFilePath").input_value()
        assert iq and "IQ-Record" in iq, f"IQ path not auto-filled: {iq}"

        # Load sim-inference component
        for _ in range(10):
            comp_opts = page.locator("#msel option").all_inner_texts()
            if any("sim-inference" in o for o in comp_opts): break
            page.wait_for_timeout(500)
        else:
            pytest.fail(f"sim-inference not in component list: {comp_opts}")

        comp_vals = page.evaluate(
            "Array.from(document.querySelectorAll('#msel option'))"
            ".map(o=>({v:o.value,t:o.textContent}))"
        )
        sim = next((o for o in comp_vals if "sim-inference" in o["v"].lower()), None)
        assert sim, f"sim-inference not found: {comp_vals}"
        page.locator("#msel").select_option(sim["v"])
        page.wait_for_timeout(300)
        page.locator("#mlbtn").click()
        page.wait_for_timeout(1000)

        # Connect collector
        page.locator("#connBtn").click()
        page.wait_for_timeout(2000)

        # === OBSERVATION PAGE: Start acquisition ===
        page.evaluate('document.querySelector("#tabs .nav-link[data-pg=\\"obs\\"]").click()')
        page.wait_for_timeout(500)
        page.locator("#btnS").click()
        page.wait_for_timeout(1500)

        # Poll table for data rows (up to 12s)
        data_found = False
        for _ in range(24):
            page.wait_for_timeout(500)
            rows = page.locator("#rtbody tr").all()
            data_rows = [
                r for r in rows
                if "等待" not in r.inner_text() and "no-data" not in (r.get_attribute("class") or "")
            ]
            if data_rows:
                data_found = True
                break
        assert data_found, (
            f"No inference rows in 12s. Table: {page.locator('#rtbody').inner_text()[:300]}"
        )

        # 1. 推理次数 > 0
        cnt = page.locator("#cnt").inner_text()
        assert cnt not in ("0", "--"), f"推理次数 should be > 0, got: {cnt}"

        # 2. 实时推理统计: 检测结果=NOISE, Drone%%=0.0%%
        rows = page.locator("#rtbody tr").all()
        data_row = None
        for r in rows:
            txt = r.inner_text()
            if "等待" not in txt and "no-data" not in (r.get_attribute("class") or ""):
                data_row = r
                break
        assert data_row, "No data row found"
        cells = data_row.locator("td").all_inner_texts()
        is_drone_text = cells[3].strip() if len(cells) > 3 else ""
        drone_pct_text = cells[4].strip() if len(cells) > 4 else ""
        assert is_drone_text == "NOISE", (
            f"检测结果 should be NOISE (noise file), got: {is_drone_text}"
        )
        drone_pct_val = float(re.sub(r'[^0-9.]', '', drone_pct_text))
        assert drone_pct_val < 1.0, f"Drone%% should be ~0%% for noise, got: {drone_pct_text}"

        # 3. 推理ms != 空
        page.locator('button.col-toggle[data-col="process_time_ms"]').click()
        page.wait_for_timeout(300)
        headers = page.locator("#rthead th").all_inner_texts()
        try:
            proc_col_idx = headers.index("推理ms")
        except ValueError:
            pytest.fail(f"推理ms column not found in header: {headers}")
        proc_text = data_row.locator("td").nth(proc_col_idx).inner_text().strip()
        assert proc_text != "" and proc_text != "--", (
            f"推理ms should not be empty, got: '{proc_text}'"
        )
        assert "ms" in proc_text, f"推理ms should contain 'ms', got: {proc_text}"

        # 4. 缓冲区监控: 总帧数持续增加
        frames_el = page.locator("#buf-frames")
        f0 = int(frames_el.inner_text() or "0")
        page.wait_for_timeout(2000)
        f1 = int(frames_el.inner_text() or "0")
        assert f1 > f0, f"总帧数 should increase: before={f0}, after={f1}"

        # 5. 缓冲区监控: 采集状态=采集中
        coll_text = page.locator("#buf-coll").inner_text()
        assert coll_text == "采集中", f"采集状态 should be 采集中, got: {coll_text}"

        # 6. 当前配置: 推理组件=sim-inference
        cfg_comp = page.locator("#cfg-component").inner_text()
        assert cfg_comp == "sim-inference", (
            f"推理组件 should be sim-inference, got: {cfg_comp}"
        )

        # Stop session
        page.locator("#btnX").click()
        page.wait_for_timeout(1500)

        # Verify table reset
        tbody = page.locator("#rtbody").inner_text()
        assert "等待启动采数" in tbody, f"Table should reset, got: {tbody[:200]}"
        browser.close()
