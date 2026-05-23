"""
test_platform_flow.py - 平台流程集成测试（E2E：仅调 5100，规则 6）

本测试为 E2E 测试，通过 WebUI 可访问接口（http://localhost:5100）
模拟用户在前端页面的完整操作路径。

测试场景：
1. 设备扫描（GET /api/v1/devices）
2. 组件发现（GET /api/v1/components）- 验证 rfuav-two-stage 可见
3. 启动会话（POST /api/v1/session/start）
4. 查询会话状态（GET /api/v1/session/status）
5. 停止会话（POST /api/v1/session/stop）

禁止使用 ASGITransport/TestClient，必须用 requests 调真实 HTTP。

运行方式（容器内）：
    cd /repo
    python -m pytest tests/integration/test_platform_flow.py -v
"""

import pytest
import requests
import subprocess
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

PLATFORM_URL = "http://localhost:5100"
COLLECTOR_URL = "http://localhost:5101"


def is_port_open(url, timeout=3):
    """检查服务端口是否开放"""
    try:
        resp = requests.get(f"{url}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def all_services():
    """启动真实 Collector + Platform，测试结束后关闭"""
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 容器内 /repo 存在才启动子进程；否则假设服务已由 start_all_services.sh 启动
    if os.path.isdir("/repo"):
        collector_proc = None
        platform_proc = None

        if not is_port_open(COLLECTOR_URL):
            collector_proc = subprocess.Popen(
                [sys.executable, "-m", "collector.api", "--port", "5101"],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(3)

        if not is_port_open(PLATFORM_URL):
            platform_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn",
                 "backend.main:app", "--host", "0.0.0.0", "--port", "5100"],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(5)

        yield {"collector": collector_proc, "platform": platform_proc}

        if platform_proc:
            platform_proc.terminate()
            platform_proc.wait(timeout=5)
        if collector_proc:
            collector_proc.terminate()
            collector_proc.wait(timeout=5)
    else:
        # 宿主机：假设服务已由 start_all_services.sh 启动
        if not is_port_open(PLATFORM_URL):
            pytest.skip("Platform not running (start_all_services.sh first)")
        yield {}


@pytest.fixture(autouse=True)
def cleanup_sessions():
    """每个测试前清理残留会话"""
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

class TestPlatformFlow:
    """平台端到端流程集成测试（E2E：仅调 5100，规则 6）"""

    @pytest.mark.parametrize("component_id", ["sim-inference", "rfuav-two-stage"])
    def test_1_components_list(self, all_services, component_id):
        """验证组件列表包含指定组件（sim-inference 和 rfuav-two-stage）"""
        resp = requests.get(f"{PLATFORM_URL}/api/v1/components", timeout=10)
        assert resp.status_code == 200, f"响应体: {resp.text}"
        component_ids = [c["id"] for c in resp.json()["components"]]
        assert component_id in component_ids, \
            f"{component_id} not found in components: {component_ids}"
        print(f"[PASS] {component_id} found: {component_ids}")

    def test_2_device_scan(self, all_services):
        """场景1：设备扫描（模拟前端点击"刷新设备"按钮）"""
        resp = requests.get(f"{PLATFORM_URL}/api/v1/devices", timeout=10)
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert "devices" in data
        device_ids = [d["id"] for d in data["devices"]]
        assert "file:iq_recording.bin" in device_ids, \
            f"Pluto-Repeater 设备未找到: {data['devices']}"
        print(f"[PASS] devices found: {device_ids}")

    @pytest.mark.parametrize("component_id", ["sim-inference", "rfuav-two-stage"])
    def test_3_start_session(self, all_services, component_id):
        """场景2：启动会话（模拟前端点击"启动采集"）"""
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": component_id,
                "config": {"confidence_threshold": 0.5}
            },
            timeout=15
        )
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert "session_id" in data, f"no session_id: {data}"
        assert data["session_id"].startswith("sess_")
        assert data.get("status") in ("running", "started"), \
            f"expected status=running/started, got {data.get('status')}"
        print(f"[PASS] session {data['session_id']} started with {component_id}")

        # 停止
        stop_resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/stop",
            json={"session_id": data["session_id"]},
            timeout=10
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json().get("status") == "stopped"
        print(f"[PASS] session {data['session_id']} stopped")

    def test_4_query_session_status(self, all_services):
        """场景4：查询会话状态"""
        # 启动一个 session
        start_resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={"component_id": "sim-inference", "config": {}},
            timeout=15
        )
        assert start_resp.status_code == 200
        session_id = start_resp.json()["session_id"]

        resp = requests.get(
            f"{PLATFORM_URL}/api/v1/session/status?session_id={session_id}",
            timeout=10
        )
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert data["session_id"] == session_id
        assert data.get("status") in ("running", "started", "stopped")

        # 清理
        requests.post(
            f"{PLATFORM_URL}/api/v1/session/stop",
            json={"session_id": session_id},
            timeout=10
        )
        print(f"[PASS] session status checked: {data}")

    def test_5_stop_session(self, all_services):
        """场景5：停止会话（模拟前端点击"停止采集"）"""
        start_resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={"component_id": "sim-inference", "config": {}},
            timeout=15
        )
        assert start_resp.status_code == 200
        session_id = start_resp.json()["session_id"]

        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/stop",
            json={"session_id": session_id},
            timeout=10
        )
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert data.get("status") == "stopped"
        assert "stats" in data
        print(f"[PASS] session stopped: {data}")


class TestPlatformFlowRepeatable:
    """可重复运行验证：连续执行两次完整流程，确保无状态残留"""

    @pytest.mark.parametrize("component_id", ["sim-inference", "rfuav-two-stage"])
    def test_repeatable_start_stop(self, all_services, component_id):
        """连续两次 start + stop，验证第二次不受第一次影响"""
        for i in range(2):
            start_resp = requests.post(
                f"{PLATFORM_URL}/api/v1/session/start",
                json={
                    "component_id": component_id,
                    "config": {"confidence_threshold": 0.5}
                },
                timeout=15
            )
            assert start_resp.status_code == 200, \
                f"[run {i}] start 失败: {start_resp.text}"
            session_id = start_resp.json()["session_id"]

            stop_resp = requests.post(
                f"{PLATFORM_URL}/api/v1/session/stop",
                json={"session_id": session_id},
                timeout=10
            )
            assert stop_resp.status_code == 200, \
                f"[run {i}] stop 失败: {stop_resp.text}"
            assert stop_resp.json().get("status") == "stopped"
            print(f"[PASS] run {i} with {component_id} OK")


if __name__ == "__main__":
    print("=" * 60)
    print("平台流程集成测试（E2E：仅调 5100）")
    print(f"Platform  : {PLATFORM_URL}")
    print("=" * 60)

    try:
        r = requests.get(f"{PLATFORM_URL}/health", timeout=5)
        print(f"[OK] Platform 在线: {r.status_code}")
    except Exception as e:
        print(f"[WARN] Platform 不可达: {e}")
        print("提示：在容器内运行，或先启动服务：./start_all_services.sh")

    print()
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))