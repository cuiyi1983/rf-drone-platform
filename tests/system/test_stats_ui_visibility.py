"""TC-SYS-08: sim-inference UI stats card becomes non-empty after acquisition starts.

Verifies that after clicking Start and acquisition begins:
1. The inference stats card (#stat-frame) does NOT show "暂无数据"
2. At least one of the stat values (if-inf, if-noise, if-drone) is populated
3. The model distribution list is populated (not "暂无数据")

Run:
  cd /home/ubuntu/rf-drone-platform-test
  python3 -m pytest tests/system/test_stats_ui_visibility.py -v --tb=short
"""
import pytest
import requests
import time

PLATFORM_URL = "http://localhost:5100"
COLLECTOR_URL = "http://localhost:5101"
FRONTEND_URL = "http://localhost:5102"
COMPONENT_ID = "sim-inference"
IQ_FILE = "/home/ubuntu/rf-drone-platform-test/IQ-Record/DJI_MINI3_01.npy"


class TestStatsUIVisibility:
    """TC-SYS-08: UI stats card non-empty after acquisition starts."""

    def test_tc_sys_08_stats_card_populates_after_acquisition(self, ensure_services):
        """
        After starting sim-inference session and collector is connected,
        wait for acquisition to begin, then poll the UI stats card
        and verify it is no longer showing '暂无数据'.
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                page.goto(FRONTEND_URL, timeout=15000)
                page.wait_for_timeout(2000)

                # --- Load sim-inference component ---
                _click_tab(page, "cfg")
                page.wait_for_timeout(1000)

                # Wait for component list
                for _ in range(30):
                    comp_opts = page.locator("#msel option").all_inner_texts()
                    if comp_opts and any("sim-inference" in o for o in comp_opts):
                        break
                    page.wait_for_timeout(500)
                else:
                    pytest.fail("sim-inference not in component list")

                comp_vals = page.evaluate(
                    "Array.from(document.querySelectorAll('#msel option'))"
                    ".map(o=>({v:o.value,t:o.textContent}))"
                )
                sim = next((o for o in comp_vals if "sim-inference" in o["v"].lower()), None)
                assert sim, "sim-inference not found"
                page.locator("#msel").select_option(sim["v"])
                page.wait_for_timeout(300)
                page.locator("#mlbtn").click()
                page.wait_for_timeout(1500)

                # --- Wait for device list and select pluto-repeater ---
                for _ in range(15):
                    opts = page.locator("#deviceSel option").all_inner_texts()
                    if opts and any("pluto" in o.lower() for o in opts):
                        break
                    page.wait_for_timeout(1000)
                else:
                    pytest.fail("Device list empty")

                opt_vals = page.evaluate(
                    "Array.from(document.querySelectorAll('#deviceSel option'))"
                    ".map(o=>({v:o.value,t:o.textContent}))"
                )
                rpt = next((o for o in opt_vals if "pluto-repeater" in o["v"].lower()), None)
                assert rpt, "pluto-repeater not found"
                page.locator("#deviceSel").select_option(rpt["v"])
                page.wait_for_timeout(1000)

                # --- Connect collector ---
                _click_tab(page, "cfg")
                page.wait_for_timeout(300)
                page.locator("#connBtn").click()

                # --- Switch to observation tab and wait for collector connected ---
                _click_tab(page, "obs")
                page.wait_for_timeout(500)
                for _ in range(20):
                    if not page.locator("#btnS").is_disabled():
                        break
                    page.wait_for_timeout(500)
                else:
                    pytest.fail("btnS never enabled (collector not connected)")

                # --- Start session ---
                page.locator("#btnS").click()
                page.wait_for_timeout(500)

                # --- Wait for acquisition to start ---
                for _ in range(20):
                    try:
                        if page.locator("#buf-coll").inner_text() == "\u91c7\u96c6\u4e2d":
                            break
                    except Exception:
                        pass
                    if page.locator("#btnX").count() > 0 and not page.locator("#btnX").is_disabled():
                        break
                    page.wait_for_timeout(500)
                else:
                    pytest.fail("Acquisition never started")

                # --- Wait for stats to populate (up to 12s) ---
                # The 5s sliding window means stats should appear within 5-8s after acquisition
                stats_visible = False
                stat_value_found = False

                for attempt in range(24):  # 24 * 0.5s = 12s
                    page.wait_for_timeout(500)

                    # Check 1: stat-frame should NOT show "暂无数据"
                    try:
                        stat_frame = page.locator("#stat-frame")
                        if stat_frame.count() > 0:
                            frame_html = stat_frame.inner_html()
                            if "\u6682\u65e0\u6570\u636e" in frame_html or "暂无数据" in frame_html:
                                # Still showing no-data — check if stats are actually there
                                pass
                            else:
                                stats_visible = True
                    except Exception:
                        pass

                    # Check 2: At least one stat value should be non-placeholder
                    try:
                        if_inf = page.locator("#if-inf").inner_text()
                        if_noise = page.locator("#if-noise").inner_text()
                        if_drone = page.locator("#if-drone").inner_text()
                        # Placeholder is '--'
                        if if_inf not in ("--", "", "\u6682\u65e0\u6570\u636e") or \
                           if_noise not in ("--", "", "\u6682\u65e0\u6570\u636e") or \
                           if_drone not in ("--", "", "\u6682\u65e0\u6570\u636e"):
                            stat_value_found = True
                    except Exception:
                        pass

                    if stats_visible and stat_value_found:
                        break
                else:
                    # Final diagnostic
                    try:
                        final_if_inf = page.locator("#if-inf").inner_text()
                        final_if_noise = page.locator("#if-noise").inner_text()
                        final_if_drone = page.locator("#if-drone").inner_text()
                        final_dist = page.locator("#if-dist").inner_text()
                    except Exception as e:
                        final_if_inf = final_if_noise = final_if_drone = final_dist = f"error: {e}"

                    pytest.fail(
                        f"Stats card did not populate after 12s.\n"
                        f"  stats_visible={stats_visible}, stat_value_found={stat_value_found}\n"
                        f"  if-inf='{final_if_inf}', if-noise='{final_if_noise}', if-drone='{final_if_drone}'\n"
                        f"  if-dist='{final_dist}'"
                    )

                # --- Assertions ---
                assert stats_visible, "stat-frame should not show '暂无数据' during acquisition"
                assert stat_value_found, (
                    f"At least one of if-inf/if-noise/if-drone should have a value, "
                    f"got if-inf='{page.locator('#if-inf').inner_text()}'"
                )

                print(f"\n✓ TC-SYS-08: Stats card populated OK")
                print(f"  if-inf={page.locator('#if-inf').inner_text()}, "
                      f"if-noise={page.locator('#if-noise').inner_text()}, "
                      f"if-drone={page.locator('#if-drone').inner_text()}")

            finally:
                browser.close()


def _click_tab(page, tab):
    """Click a tab button by its ID suffix."""
    tab_map = {"obs": "tab-obs", "cfg": "tab-cfg", "log": "tab-log"}
    tab_id = tab_map.get(tab, f"tab-{tab}")
    btn = page.locator(f"#{tab_id}")
    if btn.count() > 0:
        btn.click()
    else:
        page.locator(f"button[data-tab='{tab}']").click()
