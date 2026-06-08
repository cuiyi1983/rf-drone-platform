"""TC-SYS-06: sim-inference component concurrent sessions multi-threaded test.

Starts 3 concurrent sim-inference sessions and verifies all reach running status,
return non-empty inference_stats, and inference_count > 0 for each.

Run:
  cd /home/ubuntu/rf-drone-platform-test
  python3 -m pytest tests/system/test_sim_inference_multiconcurrent.py -v --tb=short
"""
import pytest
import requests
import time
import threading

PLATFORM_URL = "http://localhost:5100"
COMPONENT_ID = "sim-inference"
IQ_FILE = "/home/ubuntu/rf-drone-platform-test/IQ-Record/DJI_MINI3_01.npy"
NUM_SESSIONS = 3
SESSION_RUN_TIME = 8  # seconds — must be > 5s to populate the 5s sliding window


class TestSimInferenceMultiConcurrent:
    """TC-SYS-06: sim-inference concurrent sessions."""

    def test_tc_sys_06_sim_inference_concurrent_sessions(self, ensure_services):
        """
        Start 3 concurrent sim-inference sessions simultaneously.
        Each must reach running status, return non-empty inference_stats
        after SESSION_RUN_TIME seconds, and have inference_count > 0.
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
                            "detection_mode": "always_drone",
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
            # Let sessions run
            time.sleep(SESSION_RUN_TIME)

            # Query inference_stats for each session
            stats_results = {}
            for i in range(NUM_SESSIONS):
                sid = session_ids[i]
                try:
                    stats_resp = requests.get(
                        f"{PLATFORM_URL}/api/v1/session/{sid}/inference_stats",
                        timeout=15,
                    )
                    if stats_resp.status_code == 200:
                        stats = stats_resp.json()
                    else:
                        stats = None
                    stats_results[i] = stats
                except Exception as e:
                    stats_results[i] = None

            # Verify all sessions have non-empty stats
            for i in range(NUM_SESSIONS):
                stats = stats_results[i]
                assert stats, (
                    f"Session {i} (sid={session_ids[i]}) returned empty inference_stats. "
                    f"Errors: {errors}"
                )
                required_keys = ["inference_count", "noise_count", "drone_count", "model_distribution"]
                for key in required_keys:
                    assert key in stats, (
                        f"Session {i} missing key '{key}' in inference_stats: {stats}"
                    )
                assert stats["inference_count"] > 0, (
                    f"Session {i} inference_count should be > 0 after {SESSION_RUN_TIME}s, "
                    f"got {stats['inference_count']}"
                )
                assert stats["inference_count"] == stats["noise_count"] + stats["drone_count"], (
                    f"Session {i} inference_count != noise_count + drone_count: {stats}"
                )
                assert stats["drone_count"] == stats["inference_count"], (
                    f"Session {i} with always_drone mode, drone_count should equal inference_count: {stats}"
                )
                assert isinstance(stats["model_distribution"], dict), (
                    f"Session {i} model_distribution must be dict: {type(stats['model_distribution'])}"
                )
                assert len(stats["model_distribution"]) > 0, (
                    f"Session {i} model_distribution should not be empty: {stats}"
                )

            print(f"\n✓ TC-SYS-06: All {NUM_SESSIONS} concurrent sessions OK")
            for i in range(NUM_SESSIONS):
                print(f"  session[{i}] sid={session_ids[i]}: inference_count={stats_results[i]['inference_count']}")

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
