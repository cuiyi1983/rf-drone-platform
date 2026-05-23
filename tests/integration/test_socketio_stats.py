"""
test_socketio_stats.py - Socket.IO 推送验证（P0）

测试场景：
1. TC-INV-02: collector_stats 推送（buffer_level, total_frames, frames_per_second）
2. TC-INV-03: inference_result 推送
3. TC-INV-04: REST stats 端点

运行方式（容器内）：
    cd /repo
    python -m pytest tests/integration/test_socketio_stats.py -v -s

关键断言：
- collector_stats 事件字段完整性（前端 handleCollectorStats 依赖这些字段）
- 推送次数 ≥ 2（证明每秒推送在工作）
- buffer_level 递增（IQ 文件循环播放）
"""

import pytest
import requests
import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from utils.assertions import (
    assert_collector_stats_event,
    assert_inference_result_event,
    assert_session_stats_response,
    StatsCollector,
)

PLATFORM_URL = "http://localhost:5100"


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def ensure_platform():
    """确保 Platform 在线"""
    try:
        r = requests.get(f"{PLATFORM_URL}/health", timeout=3)
        assert r.status_code == 200
    except Exception:
        pytest.skip(f"Platform not ready ({PLATFORM_URL}/health)")


@pytest.fixture(autouse=True)
def cleanup_sessions():
    """每个测试前清理残留 session"""
    try:
        r = requests.get(f"{PLATFORM_URL}/api/v1/session/list", timeout=3)
        if r.status_code == 200:
            for sess in r.json().get("sessions", []):
                if sess.get("status") == "running":
                    requests.post(
                        f"{PLATFORM_URL}/api/v1/session/stop",
                        json={"session_id": sess["session_id"]},
                        timeout=5
                    )
    except Exception:
        pass
    yield


# ── Test Cases ──────────────────────────────────────────────────

class TestSocketIOCollectorStats:
    """TC-INV-02: collector_stats Socket.IO 推送验证"""

    def test_collector_stats_pushed_every_second(self):
        """
        验证每秒推送一次 collector_stats，字段与前端期望一致。

        崔老板观测点：缓冲区监控（#buf-fill, #buf-frames, #buf-fps）
        """
        # 启动 session（repeater 模式，IQ 文件循环播放）
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "sim-inference",
                "config": {
                    "iq_file_path": "IQ-Record/noise_5db_600k.bin",
                    "loop_play": True,
                    "frequency": 5805_000_000,
                    "sample_rate": 60_000_000,
                    "gain": 20.0,
                }
            },
            timeout=15
        )
        assert resp.status_code == 200, f"start failed: {resp.text}"
        session_id = resp.json()["session_id"]
        print(f"\n[INFO] Session started: {session_id}")

        try:
            # 收集 3 秒数据
            collector = StatsCollector(session_id)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(
                collector.connect_and_wait(PLATFORM_URL, duration=3.0)
            )

            # 连接验证
            assert collector.error is None, (
                f"Socket.IO 连接失败: {collector.error}"
            )
            assert collector.connected, "Socket.IO 未连接成功"

            # 推送次数验证（每秒一次，3 秒应 ≥ 2 次）
            stats_count = len(collector.stats_events)
            print(f"[INFO] collector_stats 收到 {stats_count} 次")
            assert stats_count >= 2, (
                f"collector_stats 推送次数不足：期望 ≥2 次，实际 {stats_count} 次。"
                f"收到的事件: {collector.events}"
            )

            # 字段完整性验证（前端 handleCollectorStats 依赖这些字段）
            for event in collector.stats_events:
                assert_collector_stats_event(event)

            # buffer_level 递增验证（IQ 文件循环播放）
            buffer_levels = [e["buffer_level"] for e in collector.stats_events]
            assert buffer_levels[-1] > buffer_levels[0], (
                f"buffer_level 未递增，可能数据通道断了。levels: {buffer_levels}"
            )
            print(f"[PASS] buffer_level 递增: {buffer_levels}")

            # total_frames 增长验证
            total_frames = [e["total_frames"] for e in collector.stats_events]
            assert total_frames[-1] > total_frames[0], \
                f"total_frames 未增长: {total_frames}"
            print(f"[PASS] total_frames 增长: {total_frames}")

        finally:
            requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=10
            )


class TestSocketIOInferenceResult:
    """TC-INV-03: inference_result Socket.IO 推送验证"""

    def test_inference_result_pushed(self):
        """
        验证推理结果推送正常，字段与前端 handleInferenceResult 期望一致。
        """
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "sim-inference",
                "config": {"confidence_threshold": 0.5}
            },
            timeout=15
        )
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        try:
            collector = StatsCollector(session_id)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(
                collector.connect_and_wait(PLATFORM_URL, duration=3.0)
            )

            inference_events = collector.inference_events
            print(f"[INFO] inference_result 收到 {len(inference_events)} 次")
            assert len(inference_events) > 0, \
                "未收到任何 inference_result 事件"

            for event in inference_events:
                assert_inference_result_event(event)
            print(f"[PASS] inference_result 字段验证通过")

        finally:
            requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=10
            )


class TestRESTSessionStats:
    """TC-INV-04: REST stats 端点验证"""

    def test_stats_endpoint_returns_buffer_metrics(self):
        """
        验证 GET /api/v1/session/{id}/stats 返回正确的字段。

        崔老板观测点：缓冲区监控（buffer_level, total_frames）
        """
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "sim-inference",
                "config": {"iq_file_path": "IQ-Record/noise_5db_600k.bin"}
            },
            timeout=15
        )
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        try:
            # 等待数据积累
            time.sleep(2)

            # 轮询 3 次
            for i in range(3):
                stats_resp = requests.get(
                    f"{PLATFORM_URL}/api/v1/session/{session_id}/stats",
                    timeout=5
                )
                assert stats_resp.status_code == 200, \
                    f"stats 端点失败 ({stats_resp.status_code}): {stats_resp.text}"
                stats = stats_resp.json()
                assert_session_stats_response(stats)
                assert stats["buffer_level"] >= 0
                assert stats["frames_received"] > 0, \
                    f"repeater 模式应有 frames_received > 0，实际: {stats}"
                time.sleep(1)

            print(f"[PASS] stats 端点验证通过: frames_received={stats['frames_received']}")

        finally:
            requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=10
            )


# ── Main ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "-s"],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    sys.exit(result.returncode)
