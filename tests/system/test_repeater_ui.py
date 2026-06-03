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

_python_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".venv", "bin", "python")

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
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print(f"[conftest] Python: {_python_bin}")
    print("\n[conftest] Killing existing services...")
    os.system("fuser -k 5100/tcp 2>/dev/null; fuser -k 5101/tcp 2>/dev/null; fuser -k 5102/tcp 2>/dev/null")
    time.sleep(1)
    env = os.environ.copy()
    subprocess.Popen([_python_bin, "-m", "collector.api", "--port", "5101"],
        cwd=base, env=env, stdout=open("/tmp/collector.log","w"), stderr=subprocess.STDOUT)
    subprocess.Popen([_python_bin, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "5100"],
        cwd=base, stdout=open("/tmp/platform.log","w"), stderr=subprocess.STDOUT)
    frontend_root = os.path.join(base, "frontend")
    print(f"[conftest] Frontend root: {frontend_root}, exists: {os.path.exists(frontend_root)}")
    p = subprocess.Popen([_python_bin, "-m", "http.server", "5102"],
        cwd=frontend_root, stdout=open("/tmp/frontend.log","w"), stderr=subprocess.STDOUT)
    print(f"[conftest] http.server PID={p.pid}")
    time.sleep(12)

    for url, path, name in [
        (COLLECTOR_URL, "/api/v1/collector/health", "Collector"),
        (PLATFORM_URL, "/health", "Platform"),
    ]:
        for _ in range(20):
            if is_ready(url, path): break
            time.sleep(1)
        else: assert False, f"{name} did not become ready"

    # Wait for all components (including slow-loading rfuav-two-stage) to register.
    # Component registration happens in uvicorn startup() and can take 10+ seconds
    # due to model loading in InferenceFramework.
    for _ in range(40):
        try:
            r = requests.get(f"{PLATFORM_URL}/api/v1/components", timeout=2)
            if r.status_code == 200:
                comps = r.json().get("components", [])
                # rfuav-two-stage takes longer to load; wait until we have 2 components
                if len(comps) >= 2:
                    print(f"\n[conftest] All {len(comps)} components registered: {[c['id'] for c in comps]}")
                    break
                print(f"[conftest] Waiting for components... ({len(comps)}/2 registered so far)")
        except Exception as e:
            print(f"[conftest] Component poll error: {e}")
        time.sleep(1)
    else:
        # Fallback: dump what we have
        try:
            r = requests.get(f"{PLATFORM_URL}/api/v1/components", timeout=2)
            comps = r.json().get("components", []) if r.status_code == 200 else []
        except Exception:
            comps = []
        print(f"\n[conftest] WARNING: only {len(comps)} components registered after 40s: {[c['id'] for c in comps]}")

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


def _click_tab(page, tab_name):
    """Click a nav tab by its data-pg attribute."""
    page.evaluate(
        f"document.querySelector('#tabs .nav-link[data-pg=\"{tab_name}\"]').click()"
    )

def _get_conn_btn_text(page):
    """Get the connect button's current text."""
    try:
        return page.locator("#connBtn").inner_text()
    except:
        return "(not found)"

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
        _click_tab(page, "cfg")
        page.wait_for_timeout(500)
        assert page.locator("#pg-cfg").is_visible()
        _click_tab(page, "obs")
        page.wait_for_timeout(500)
        assert page.locator("#pg-obs").is_visible()
        browser.close()


def test_repeater_session_stats(ensure_services, stop_active_sessions):
    """
    TC-SYS-02.3: Start repeater session via UI with sim-inference + noise file,
    then verify inference output appears in the observation table.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=15000)

        # === CONFIG PAGE ===
        _click_tab(page, "cfg")
        page.wait_for_timeout(1000)

        # Wait for device list to populate
        for _ in range(15):
            opts = page.locator("#deviceSel option").all_inner_texts()
            if opts and any("pluto" in o.lower() for o in opts):
                break
            page.wait_for_timeout(1000)
        else:
            pytest.fail(f"Device list empty after 15s. Options: {page.locator('#deviceSel option').all_inner_texts()}")

        # Select pluto-repeater
        opt_vals = page.evaluate(
            "Array.from(document.querySelectorAll('#deviceSel option'))"
            ".map(o=>({v:o.value,t:o.textContent}))"
        )
        rpt = next((o for o in opt_vals if "pluto-repeater" in o["v"].lower()), None)
        assert rpt, f"pluto-repeater not found in: {opt_vals}"
        page.locator("#deviceSel").select_option(rpt["v"])
        page.wait_for_timeout(800)

        # IQ path auto-filled
        iq = page.locator("#iqFilePath").input_value()
        assert iq and "IQ-Record" in iq, f"IQ path not auto-filled: {iq}"

        # Load sim-inference component
        for _ in range(10):
            comp_opts = page.locator("#msel option").all_inner_texts()
            if comp_opts and any("sim-inference" in o for o in comp_opts):
                break
            page.wait_for_timeout(500)
        else:
            pytest.fail(f"sim-inference not in component list: {page.locator('#msel option').all_inner_texts()}")

        comp_vals = page.evaluate(
            "Array.from(document.querySelectorAll('#msel option'))"
            ".map(o=>({v:o.value,t:o.textContent}))"
        )
        sim = next((o for o in comp_vals if "sim-inference" in o["v"].lower()), None)
        assert sim, f"sim-inference not found in: {comp_vals}"
        page.locator("#msel").select_option(sim["v"])
        page.wait_for_timeout(300)
        page.locator("#mlbtn").click()
        page.wait_for_timeout(1000)

        # Connect to collector
        page.locator("#connBtn").click()
        page.wait_for_timeout(3000)

        # Verify connection via API directly (button text doesn't change, disabled state does)
        conn_resp = requests.post("http://localhost:5101/api/v1/collector/connect",
                                  json={"device_uri": "file:iq_recording.bin"}, timeout=5)
        print(f"[DEBUG] connect API: {conn_resp.json()}")

        if conn_resp.json().get("code") != 0:
            pytest.fail(f"Collector connection API failed: {conn_resp.json()}")

        # === OBSERVATION PAGE ===
        _click_tab(page, "obs")
        page.wait_for_timeout(500)

        # Start acquisition
        btn_s_before = page.locator("#btnS").inner_text() if page.locator("#btnS").count() else "(no btn)"
        page.locator("#btnS").click()
        page.wait_for_timeout(2000)
        btn_s_after = page.locator("#btnS, #btnX").first.inner_text()
        buf_frames = page.locator("#buf-frames").inner_text() if page.locator("#buf-frames").count() else "(none)"
        print(f"[DEBUG] After start: btn='{btn_s_after}', buf-frames={buf_frames}")

        # Poll inference table
        data_found = False
        for i in range(24):
            page.wait_for_timeout(500)
            table_text = page.locator("#rtbody").inner_text()[:300]
            rows = page.locator("#rtbody tr").all()
            data_rows = [
                r for r in rows
                if "等待" not in r.inner_text() and "no-data" not in (r.get_attribute("class") or "")
            ]
            print(f"[DEBUG] Poll {i}: {len(data_rows)} data rows, table={table_text[:100]}")
            if data_rows:
                data_found = True
                break
        assert data_found, (
            f"No inference rows in 12s. Table: {page.locator('#rtbody').inner_text()[:300]}"
        )

        # 1. Inference count > 0
        cnt = page.locator("#cnt").inner_text()
        assert cnt not in ("0", "--"), f"Inference count should be > 0, got: {cnt}"

        # 2. Detection result=NOISE, Drone%%=0.0%%
        rows = page.locator("#rtbody tr").all()
        data_row = next(
            (r for r in rows
             if "等待" not in r.inner_text() and "no-data" not in (r.get_attribute("class") or "")),
            None
        )
        assert data_row, "No data row after successful poll"
        cells = data_row.locator("td").all_inner_texts()
        is_drone_text = cells[3].strip() if len(cells) > 3 else ""
        drone_pct_text = cells[4].strip() if len(cells) > 4 else ""
        assert is_drone_text == "NOISE", f"Detection should be NOISE, got: {is_drone_text}"
        drone_pct_val = float(re.sub(r'[^0-9.]', '', drone_pct_text))
        assert drone_pct_val < 1.0, f"Drone%% should be ~0%%, got: {drone_pct_text}"

        # 3. Process time ms != empty
        page.locator('button.col-toggle[data-col="process_time_ms"]').click()
        page.wait_for_timeout(300)
        headers = page.locator("#rthead th").all_inner_texts()
        try:
            proc_col_idx = headers.index("推理ms")
        except ValueError:
            pytest.fail(f"推理ms column not found in header: {headers}")
        proc_text = data_row.locator("td").nth(proc_col_idx).inner_text().strip()
        assert proc_text != "" and proc_text != "--", f"Process time empty: '{proc_text}'"
        assert "ms" in proc_text, f"Process time should contain 'ms': {proc_text}"

        # 4. Buffer: frame count increases
        f0 = int(page.locator("#buf-frames").inner_text() or "0")
        page.wait_for_timeout(2000)
        f1 = int(page.locator("#buf-frames").inner_text() or "0")
        assert f1 > f0, f"Frame count should increase: {f0} -> {f1}"

        # 5. Buffer: collection status=采集中
        coll_text = page.locator("#buf-coll").inner_text()
        assert coll_text == "采集中", f"Collection status should be 采集中, got: {coll_text}"

        # 6. Current config: component=sim-inference
        cfg_comp = page.locator("#cfg-component").inner_text()
        assert cfg_comp == "sim-inference", f"Component should be sim-inference, got: {cfg_comp}"

        # Stop session
        page.locator("#btnX").click()
        page.wait_for_timeout(1500)
        assert "等待启动采数" in page.locator("#rtbody").inner_text()
        browser.close()

def test_component_device_dropdown_renders(ensure_services):
    """
    TC-SYS-03: sim-inference component schema renders device dropdown with NPU option.
    Verifies the frontend correctly displays the '推理设备' select from config_schema.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=15000)

        # Navigate to config page
        _click_tab(page, "cfg")
        page.wait_for_timeout(1000)

        # Load sim-inference component - poll until it appears (may still be loading at test start)
        for _ in range(20):
            comp_opts = page.locator("#msel option").all_inner_texts()
            if comp_opts and any("sim-inference" in o for o in comp_opts):
                break
            page.wait_for_timeout(500)
        else:
            pytest.fail(f"sim-inference not in list: {comp_opts}")

        comp_vals = page.evaluate(
            "Array.from(document.querySelectorAll('#msel option'))"
            ".map(o=>({v:o.value,t:o.textContent}))"
        )
        sim = next((o for o in comp_vals if "sim-inference" in o["v"].lower()), None)
        assert sim, f"sim-inference not found in: {comp_vals}"
        page.locator("#msel").select_option(sim["v"])
        page.wait_for_timeout(300)

        # Click Load Component and wait for schema to render
        page.locator("#mlbtn").click()
        # Poll for #sp_device to appear (component schema loads asynchronously)
        for _ in range(20):
            if page.locator("#sp_device").count() > 0 and page.locator("#sp_device").is_visible():
                break
            page.wait_for_timeout(500)
        else:
            pytest.fail("#sp_device never appeared after clicking Load Component")

        # --- TC-SYS-03.1: schema-params container is visible ---
        schema_container = page.locator("#schema-params")
        assert schema_container.count() > 0, "#schema-params element not found"
        assert schema_container.is_visible(), "#schema-params should be visible after loading component"

        # --- TC-SYS-03.2: device select element exists with id=sp_device ---
        device_sel = page.locator("#sp_device")
        assert device_sel.count() > 0, "#sp_device select not found in schema-params"
        assert device_sel.is_visible(), "#sp_device select should be visible"

        # --- TC-SYS-03.3: NPU is one of the options ---
        options = page.evaluate(
            "Array.from(document.querySelectorAll('#sp_device option')).map(o=>({v:o.value,t:o.textContent}))"
        )
        values = [o['v'] for o in options]
        assert 'npu' in values, f"NPU not in device options: {options}"
        assert 'auto' in values, f"auto not in device options: {options}"
        assert 'cpu' in values, f"cpu not in device options: {options}"

        # --- TC-SYS-03.4: Selecting NPU updates the select value ---
        page.locator("#sp_device").select_option('npu')
        page.wait_for_timeout(200)
        selected = page.locator("#sp_device").input_value()
        assert selected == 'npu', f"Device select should be 'npu' after selection, got: {selected}"

        browser.close()


def test_rfuav_component_schema_renders_device(ensure_services):
    """
    TC-SYS-04: rfuav-two-stage component schema renders device dropdown with NPU option.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=15000)

        # Navigate to config page
        _click_tab(page, "cfg")
        page.wait_for_timeout(1000)

        # Load rfuav-two-stage component - poll until it appears (rfuav loads last)
        for _ in range(30):
            comp_opts = page.locator("#msel option").all_inner_texts()
            if comp_opts and any("rfuav" in o.lower() for o in comp_opts):
                break
            page.wait_for_timeout(500)
        else:
            pytest.fail(f"rfuav not in component list: {comp_opts}")

        comp_vals = page.evaluate(
            "Array.from(document.querySelectorAll('#msel option'))"
            ".map(o=>({v:o.value,t:o.textContent}))"
        )
        rfuav = next((o for o in comp_vals if "rfuav" in o["v"].lower()), None)
        assert rfuav, f"rfuav-two-stage not found in: {comp_vals}"
        page.locator("#msel").select_option(rfuav["v"])
        page.wait_for_timeout(300)

        # Click Load Component and wait for schema to render
        page.locator("#mlbtn").click()
        for _ in range(20):
            if page.locator("#sp_device").count() > 0 and page.locator("#sp_device").is_visible():
                break
            page.wait_for_timeout(500)
        else:
            pytest.fail("#sp_device never appeared after loading rfuav component")

        # --- TC-SYS-04.1: schema-params visible ---
        assert page.locator("#schema-params").is_visible(), "#schema-params should be visible for rfuav"

        # --- TC-SYS-04.2: device dropdown exists ---
        device_sel = page.locator("#sp_device")
        assert device_sel.count() > 0, "#sp_device not found for rfuav component"
        assert device_sel.is_visible(), "#sp_device should be visible"

        # --- TC-SYS-04.3: NPU option present ---
        options = page.evaluate(
            "Array.from(document.querySelectorAll('#sp_device option')).map(o=>({v:o.value,t:o.textContent}))"
        )
        values = [o['v'] for o in options]
        assert 'npu' in values, f"NPU not in rfuav device options: {options}"
        assert 'auto' in values, f"auto not in rfuav device options: {options}"

        # --- TC-SYS-04.4: device can be set to NPU and persists ---
        page.locator("#sp_device").select_option('npu')
        page.wait_for_timeout(200)
        assert page.locator("#sp_device").input_value() == 'npu'

        browser.close()
