"""
test_webui_session_flow.py - 验证完整 WebUI 操作路径（规则 6：只调 5100）

测试场景：
- 选组件 → 加载 → 连接采集器 → 启动采数 → 停止

本测试为 E2E 测试，需要 docker 容器环境（Platform + Collector --mock-devices）

运行方式（容器内）：
    cd /repo
    python -m pytest tests/e2e/test_webui_session_flow.py -v

注意：在宿主机上运行会自动 skip
"""
import pytest
import requests
import subprocess
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PLATFORM_URL = "http://localhost:5100"


def is_port_open(url, timeout=3):
    """检查服务端口是否开放"""
    try:
        resp = requests.get(f"{url}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def all_services():
    """启动 Platform + Collector（--mock-devices），测试结束后关闭"""
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # 检查是否在容器内
    if not os.path.isdir("/repo"):
        pytest.skip("需要 docker 容器环境（/repo 不存在）")

    collector_proc = None
    platform_proc = None

    # 启动 Collector（如果未运行）
    if not is_port_open("http://localhost:5101"):
        collector_proc = subprocess.Popen(
            [sys.executable, "-m", "collector.api", "--mock-devices"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(3)

    # 启动 Platform（如果未运行）
    if not is_port_open(PLATFORM_URL):
        platform_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app",
             "--host", "0.0.0.0", "--port", "5100"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(3)

    yield {"collector": collector_proc, "platform": platform_proc}

    if platform_proc:
        platform_proc.terminate()
        platform_proc.wait(timeout=5)
    if collector_proc:
        collector_proc.terminate()
        collector_proc.wait(timeout=5)


def test_webui_components_discovered_and_listed(all_services):
    """验证 WebUI 组件列表包含 rfuav-two-stage"""
    resp = requests.get(f"{PLATFORM_URL}/api/v1/components", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    component_ids = [c["id"] for c in resp.json()["components"]]
    assert "rfuav-two-stage" in component_ids, \
        f"rfuav-two-stage not found: {component_ids}"
    print(f"[PASS] rfuav-two-stage in components: {component_ids}")


def test_webui_start_session_with_rfuav_component(all_services):
    """验证用 rfuav-two-stage 组件启动采数"""
    resp = requests.post(
        f"{PLATFORM_URL}/api/v1/session/start",
        json={
            "component_id": "rfuav-two-stage",
            "config": {"confidence_threshold": 0.5}
        },
        timeout=15
    )
    assert resp.status_code == 200, f"session start failed: {resp.text}"
    data = resp.json()
    assert "session_id" in data, f"no session_id in response: {data}"
    assert data.get("status") in ("running", "started"), \
        f"expected status=running/started, got: {data.get('status')}"

    session_id = data["session_id"]
    print(f"[PASS] Session started with rfuav-two-stage: session_id={session_id}")

    # 停止采数
    stop_resp = requests.post(
        f"{PLATFORM_URL}/api/v1/session/stop",
        json={"session_id": session_id},
        timeout=10
    )
    assert stop_resp.status_code == 200, f"stop failed: {stop_resp.text}"
    assert stop_resp.json().get("status") == "stopped", \
        f"expected status=stopped, got: {stop_resp.json()}"
    print(f"[PASS] Session stopped: {session_id}")


def test_webui_start_session_with_sim_inference(all_services):
    """验证用 sim-inference 组件启动采数"""
    resp = requests.post(
        f"{PLATFORM_URL}/api/v1/session/start",
        json={
            "component_id": "sim-inference",
            "config": {}
        },
        timeout=15
    )
    assert resp.status_code == 200, f"session start failed: {resp.text}"
    data = resp.json()
    assert data.get("status") in ("running", "started"), \
        f"expected status=running/started, got: {data.get('status')}"

    session_id = data["session_id"]
    print(f"[PASS] Session started with sim-inference: session_id={session_id}")

    stop_resp = requests.post(
        f"{PLATFORM_URL}/api/v1/session/stop",
        json={"session_id": session_id},
        timeout=10
    )
    assert stop_resp.json().get("status") == "stopped", \
        f"expected status=stopped, got: {stop_resp.json()}"
    print(f"[PASS] Session stopped: {session_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])