"""
TC-SYS-03: sim-inference component inference_stats API test.
TC-SYS-04: rfuav-two-stage inference_stats API test.

Verifies that:
1. sim-inference component's get_inference_stats() is implemented
2. rfuav-two-stage inference_stats works (computed by Platform from _inference_history)
3. /inference_stats returns non-empty stats after 5+ seconds of session
4. The stats contain expected keys: inference_count, noise_count, drone_count, model_distribution

Run:
  cd /home/ubuntu/rf-drone-platform-test
  python3 -m pytest tests/system/test_sim_inference_stats.py -v --tb=short
"""
import pytest
import requests
import time

PLATFORM_URL = "http://localhost:5100"
COLLECTOR_URL = "http://localhost:5101"
COMPONENT_ID = "sim-inference"
IQ_FILE_ABS = "/home/ubuntu/rf-drone-platform-test/IQ-Record/DJI_MINI3_01.npy"
IQ_FILE = IQ_FILE_ABS if __import__("os").path.exists(IQ_FILE_ABS) else None
SESSION_TIMEOUT = 7  # seconds — must be > 5s to populate the 5s sliding window


class TestSimInferenceStats:
    """TC-SYS-03: sim-inference inference_stats API test."""

    def test_tc_sys03_inference_stats_non_empty(self, ensure_services):
        """
        After a sim-inference session runs for 5+ seconds,
        /inference_stats must return non-empty stats (not {}).
        """
        # Start session
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": COMPONENT_ID,
                "config": {
                    **({"iq_file_path": IQ_FILE} if IQ_FILE else {}),
                    "detection_mode": "always_drone",
                },
            },
            timeout=15,
        )
        assert resp.status_code == 200, f"Session start failed: {resp.status_code} {resp.text}"
        data = resp.json()
        session_id = data["session_id"]
        assert data.get("status") == "running", f"Expected running: {data}"

        try:
            # Wait for sliding window to accumulate stats (needs > 5s of inferences)
            time.sleep(SESSION_TIMEOUT)

            # Query inference_stats
            stats_resp = requests.get(
                f"{PLATFORM_URL}/api/v1/session/{session_id}/inference_stats",
                timeout=15,
            )
            assert stats_resp.status_code == 200, (
                f"inference_stats failed: {stats_resp.status_code} {stats_resp.text}"
            )
            stats = stats_resp.json()

            # Must not be empty
            assert stats, "inference_stats returned empty dict"

            # Must have required keys
            required_keys = ["inference_count", "noise_count", "drone_count", "model_distribution"]
            for key in required_keys:
                assert key in stats, f"Missing key '{key}' in inference_stats: {stats}"

            # inference_count must be > 0 after 5+ seconds
            assert stats["inference_count"] > 0, (
                f"inference_count should be > 0 after {SESSION_TIMEOUT}s, got {stats['inference_count']}"
            )

            # Counts must be consistent
            assert stats["inference_count"] == stats["noise_count"] + stats["drone_count"], (
                f"inference_count != noise_count + drone_count: {stats}"
            )

            # Since detection_mode=always_drone, drone_count should equal inference_count
            assert stats["drone_count"] == stats["inference_count"], (
                f"With always_drone mode, drone_count should equal inference_count: {stats}"
            )

            # model_distribution must be non-empty dict
            assert isinstance(stats["model_distribution"], dict), (
                f"model_distribution must be dict, got {type(stats['model_distribution'])}"
            )
            assert len(stats["model_distribution"]) > 0, (
                f"model_distribution should not be empty with always_drone mode: {stats}"
            )

            print(f"\n✓ inference_stats OK: {stats}")

        finally:
            stop_resp = requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=15,
            )
            assert stop_resp.status_code == 200, f"Stop failed: {stop_resp.status_code} {stop_resp.text}"

    def test_tc_sys03_rfuav_schema_has_device_field(self, ensure_services):
        """
        The rfuav-two-stage component config_schema must expose a 'device' field
        so the UI can show NPU/CPU/CUDA selection.
        """
        schema_resp = requests.get(
            f"{PLATFORM_URL}/api/v1/components/rfuav-two-stage/config-schema",
            timeout=15,
        )
        if schema_resp.status_code == 404:
            pytest.skip("rfuav-two-stage component not available")
        assert schema_resp.status_code == 200, (
            f"config-schema failed: {schema_resp.status_code} {schema_resp.text}"
        )
        schema = schema_resp.json()

        # API returns {"config_schema": {...}}
        if "config_schema" in schema:
            schema = schema["config_schema"]

        assert "device" in schema, (
            f"rfuav-two-stage config_schema missing 'device' field. Schema keys: {list(schema.keys())}"
        )
        device_cfg = schema["device"]
        assert device_cfg.get("type") == "string", f"device type should be string, got {device_cfg}"
        assert "enum" in device_cfg or "options" in device_cfg, (
            f"device field must have enum/options for UI dropdown: {device_cfg}"
        )
        print(f"\n✓ rfuav-two-stage device field OK: {device_cfg}")


class TestRfuavTwoStageInferenceStats:
    """TC-SYS-04: rfuav-two-stage inference_stats API test.

    Verifies that inference_stats works for rfuav-two-stage component.
    This was previously broken because the endpoint called component.get_inference_stats()
    which returns {} for rfuav-two-stage (by design: stats are computed by Platform).

    The fix: inference_stats now computes from Platform._inference_history.
    """

    def test_tc_sys04_rfuav_inference_stats_non_empty(self, ensure_services):
        """
        After an rfuav-two-stage session runs for 5+ seconds,
        /inference_stats must return non-empty stats (not just {session_id: ...}).

        This test was missing from CI — only sim-inference was tested, which has its
        own get_inference_stats() implementation that masks the bug in the rfuav path.
        """
        # Check if rfuav-two-stage is available
        schema_resp = requests.get(
            f"{PLATFORM_URL}/api/v1/components/rfuav-two-stage/config-schema",
            timeout=15,
        )
        if schema_resp.status_code == 404:
            pytest.skip("rfuav-two-stage component not available")

        IQ_FILE_RFUAV = "/home/ubuntu/rf-drone-platform-test/IQ-Record/DJI_MINI3_01.npy"
        if not __import__("os").path.exists(IQ_FILE_RFUAV):
            pytest.skip(f"IQ file not found: {IQ_FILE_RFUAV}")

        # Start session with rfuav-two-stage
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "rfuav-two-stage",
                "config": {
                    "iq_file_path": IQ_FILE_RFUAV,
                    "device": "cpu",
                    "sp_device": "dml",
                },
            },
            timeout=15,
        )
        assert resp.status_code == 200, f"Session start failed: {resp.status_code} {resp.text}"
        data = resp.json()
        session_id = data["session_id"]
        assert data.get("status") == "running", f"Expected running: {data}"

        try:
            # Wait for sliding window to accumulate stats (needs > 5s of inferences)
            time.sleep(SESSION_TIMEOUT)

            # Query inference_stats
            stats_resp = requests.get(
                f"{PLATFORM_URL}/api/v1/session/{session_id}/inference_stats",
                timeout=15,
            )
            assert stats_resp.status_code == 200, (
                f"inference_stats failed: {stats_resp.status_code} {stats_resp.text}"
            )
            stats = stats_resp.json()

            # Must not be empty (i.e. must have more than just session_id)
            assert len(stats) > 1, (
                f"inference_stats returned only session_id, no actual stats: {stats}"
            )

            # Must have required keys
            required_keys = ["inference_count", "noise_count", "drone_count"]
            for key in required_keys:
                assert key in stats, f"Missing key '{key}' in inference_stats: {stats}"

            # inference_count must be > 0 after 5+ seconds
            assert stats["inference_count"] > 0, (
                f"inference_count should be > 0 after {SESSION_TIMEOUT}s, got {stats['inference_count']}"
            )

            # Counts must be consistent
            assert stats["inference_count"] == stats["noise_count"] + stats["drone_count"], (
                f"inference_count != noise_count + drone_count: {stats}"
            )

            print(f"\n✓ rfuav inference_stats OK: {stats}")

        finally:
            stop_resp = requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=15,
            )
            assert stop_resp.status_code == 200, f"Stop failed: {stop_resp.status_code} {stop_resp.text}"


class TestEmptyHistoryStats:
    """验证空推理历史场景：session刚启动时 /inference_stats 必须返回有效零值，不能是 {} """

    def test_tc_sys03_inference_stats_empty_history(self, ensure_services):
        """
        新session启动后立即查询 /inference_stats，必须返回有效的零值stats，
        不能返回空dict {}（这会导致前端guard短路不更新UI）。

        预期返回：
        {
            inference_count: 0,
            noise_count: 0,
            drone_count: 0,
            noise_ratio: 0.0,
            drone_ratio: 0.0,
            model_distribution: {}
        }
        """
        # Start session
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": COMPONENT_ID,
                "config": {
                    **({"iq_file_path": IQ_FILE} if IQ_FILE else {}),
                    "detection_mode": "always_drone",
                },
            },
            timeout=15,
        )
        assert resp.status_code == 200, f"Session start failed: {resp.status_code} {resp.text}"
        data = resp.json()
        session_id = data["session_id"]
        assert data.get("status") == "running", f"Expected running: {data}"

        try:
            # 立即查询（无推理历史）
            stats_resp = requests.get(
                f"{PLATFORM_URL}/api/v1/session/{session_id}/inference_stats",
                timeout=15,
            )
            assert stats_resp.status_code == 200, (
                f"inference_stats failed: {stats_resp.status_code} {stats_resp.text}"
            )
            stats = stats_resp.json()

            # 核心断言：不能是空dict（会导致前端guard短路）
            assert stats, "inference_stats returned {} — frontend guard will短路不更新UI"
            assert isinstance(stats, dict), f"stats must be dict, got {type(stats)}"

            # 零值字段必须存在且正确
            assert "inference_count" in stats
            assert stats["inference_count"] == 0, f"Expected 0, got {stats['inference_count']}"
            assert "noise_count" in stats
            assert stats["noise_count"] == 0
            assert "drone_count" in stats
            assert stats["drone_count"] == 0
            assert "noise_ratio" in stats, f"Missing noise_ratio in {stats}"
            assert stats["noise_ratio"] == 0.0
            assert "drone_ratio" in stats, f"Missing drone_ratio in {stats}"
            assert stats["drone_ratio"] == 0.0
            assert "model_distribution" in stats
            assert stats["model_distribution"] == {}

            print(f"\n✓ empty history stats OK: {stats}")

        finally:
            stop_resp = requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=15,
            )
            assert stop_resp.status_code == 200, f"Stop failed: {stop_resp.status_code} {stop_resp.text}"

    def test_tc_sys04_rfuav_inference_stats_empty_history(self, ensure_services):
        """
        rfuav-two-stage 新session启动后立即查询 /inference_stats，
        必须返回有效的零值stats，不能是 {}。
        """
        # Check if rfuav-two-stage is available
        schema_resp = requests.get(
            f"{PLATFORM_URL}/api/v1/components/rfuav-two-stage/config-schema",
            timeout=15,
        )
        if schema_resp.status_code == 404:
            pytest.skip("rfuav-two-stage component not available")

        IQ_FILE_RFUAV = "/home/ubuntu/rf-drone-platform-test/IQ-Record/DJI_MINI3_01.npy"
        if not __import__("os").path.exists(IQ_FILE_RFUAV):
            pytest.skip(f"IQ file not found: {IQ_FILE_RFUAV}")

        # Start session
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "rfuav-two-stage",
                "config": {
                    "iq_file_path": IQ_FILE_RFUAV,
                    "device": "cpu",
                    "sp_device": "dml",
                },
            },
            timeout=15,
        )
        assert resp.status_code == 200, f"Session start failed: {resp.status_code} {resp.text}"
        data = resp.json()
        session_id = data["session_id"]
        assert data.get("status") == "running", f"Expected running: {data}"

        try:
            # 立即查询（无推理历史）
            stats_resp = requests.get(
                f"{PLATFORM_URL}/api/v1/session/{session_id}/inference_stats",
                timeout=15,
            )
            assert stats_resp.status_code == 200, (
                f"inference_stats failed: {stats_resp.status_code} {stats_resp.text}"
            )
            stats = stats_resp.json()

            # 核心断言：不能是空dict
            assert stats, "inference_stats returned {} — frontend guard will短路不更新UI"
            assert len(stats) > 1, f"stats must have more than session_id, got {stats}"

            # 零值字段必须存在
            assert "inference_count" in stats
            assert stats["inference_count"] == 0
            assert "noise_count" in stats
            assert stats["noise_count"] == 0
            assert "drone_count" in stats
            assert stats["drone_count"] == 0

            print(f"\n✓ rfuav empty history stats OK: {stats}")

        finally:
            stop_resp = requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=15,
            )
            assert stop_resp.status_code == 200, f"Stop failed: {stop_resp.status_code} {stop_resp.text}"
