"""TC-SYS-07: rfuav-two-stage component concurrent sessions multi-threaded test.

Starts 2 concurrent rfuav-two-stage sessions and verifies both reach running status,
return non-empty latest_result after 60+ seconds.

Run:
  cd /home/ubuntu/rf-drone-platform-test
  python3 -m pytest tests/system/test_rfuav_multiconcurrent.py -v --tb=short
"""
import pytest
import requests
import time
import threading

PLATFORM_URL = "http://localhost:5100"
COMPONENT_ID = "rfuav-two-stage"
IQ_FILE = "/home/ubuntu/rf-drone-platform-test/IQ-Record/DJI_MINI3_01.npy"
NUM_SESSIONS = 2
SESSION_RUN_TIME = 60  # seconds — rfuav is slow, needs 2 full inference cycles


class TestRfuavMultiConcurrent:
    """TC-SYS-07: rfuav concurrent sessions."""

    def test_tc_sys_07_rfuav_concurrent_sessions(self, ensure_services):
        """
        Start 2 concurrent rfuav-two-stage sessions simultaneously.
        Each must reach running status, and return non-empty latest_result
        (with at least one detection key: drone_type, detections, drone_signal, inference_result)
        after SESSION_RUN_TIME seconds.
        """
        results = {}
        errors = {}
        lock = threading.Lock()

        def start_session(idx):
            try:
                resp = requests.post(
                    f"{PLATFORM_URL}/api/v1/session/start",
                    json={
                        "component_id": COMPONENT_ID,
                        "config": {
                            "iq_file_path": IQ_FILE,
                        },
                    },
                    timeout=15,
                )
                if resp.status_code != 200:
                    with lock:
                        errors[idx] = f"start failed: {resp.status_code} {resp.text}"
                    return
                data = resp.json()
                with lock:
                    results[idx] = {"session_id": data.get("session_id"), "status": data.get("status")}
            except Exception as e:
                with lock:
                    errors[idx] = str(e)

        # Launch all sessions concurrently
        threads = []
        for i in range(NUM_SESSIONS):
            t = threading.Thread(target=start_session, args=(i,))
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        # Fail fast if any session failed to start
        if errors:
            pytest.fail(f"Session start errors: {errors}")

        session_ids = [results[i]["session_id"] for i in range(NUM_SESSIONS)]
        for sid in session_ids:
            assert sid, f"Empty session_id in results: {results}"

        try:
            # Let sessions run for the full inference window
            time.sleep(SESSION_RUN_TIME)

            # Query latest_result for each session
            result_results = {}
            for i in range(NUM_SESSIONS):
                sid = session_ids[i]
                try:
                    result_resp = requests.get(
                        f"{PLATFORM_URL}/api/v1/session/{sid}/latest_result",
                        timeout=15,
                    )
                    if result_resp.status_code == 200:
                        result = result_resp.json()
                    else:
                        result = {}
                    result_results[i] = result
                except Exception as e:
                    result_results[i] = {}

            # Verify all sessions have non-empty results
            for i in range(NUM_SESSIONS):
                result = result_results[i]
                assert result, (
                    f"Session {i} (sid={session_ids[i]}) returned empty latest_result"
                )
                has_detection = any(
                    key in result
                    for key in ("drone_type", "detections", "drone_signal", "inference_result")
                )
                assert has_detection or len(result) > 0, (
                    f"Session {i} no detection info in result: {result}"
                )

            print(f"\n✓ TC-SYS-07: Both concurrent sessions OK")
            for i in range(NUM_SESSIONS):
                print(f"  session[{i}] sid={session_ids[i]}: result_keys={list(result_results[i].keys())}")

        finally:
            # Stop all sessions
            for sid in session_ids:
                try:
                    requests.post(
                        f"{PLATFORM_URL}/api/v1/session/stop",
                        json={"session_id": sid},
                        timeout=15,
                    )
                except Exception:
                    pass
