"""
test_components_auto_discover.py - 验证 WebUI 能看到 rfuav-two-stage 组件（规则 6：只调 5100）

本测试为 E2E 测试，启动真实 Platform 进程，验证：
1. 组件列表包含 rfuav-two-stage
2. 组件列表包含 sim-inference
3. 至少有 2 个组件

运行方式（需要 docker 环境）：
    cd /repo
    python -m pytest tests/e2e/test_components_auto_discover.py -v

注意：本测试需要真实 docker 容器环境，在宿主机上运行会自动 skip
"""
import pytest
import requests
import subprocess
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PLATFORM_URL = "http://localhost:5100"
SETUP_TIMEOUT = 30


def is_port_open(url, timeout=5):
    """检查服务端口是否开放"""
    try:
        resp = requests.get(f"{url}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def platform_process():
    """启动真实 Platform 进程（从项目根目录）"""
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # 检查是否在容器内（/repo 存在）
    if not os.path.isdir("/repo") and not is_port_open(PLATFORM_URL):
        pytest.skip("需要 docker 容器环境（/repo 不存在且 5100 端口未开放）")

    # 已有服务在运行，不需要重复启动
    if is_port_open(PLATFORM_URL):
        proc = None
        yield None
        return

    # 启动 Platform
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "0.0.0.0", "--port", "5100"],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    # 等待服务就绪
    for _ in range(SETUP_TIMEOUT):
        if is_port_open(PLATFORM_URL):
            break
        time.sleep(1)
    yield proc
    if proc:
        proc.terminate()
        proc.wait(timeout=5)


def test_components_list_includes_rfuav_two_stage(platform_process):
    """验证组件列表包含 rfuav-two-stage"""
    resp = requests.get(f"{PLATFORM_URL}/api/v1/components", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    component_ids = [c["id"] for c in resp.json()["components"]]
    assert "rfuav-two-stage" in component_ids, \
        f"rfuav-two-stage not found in components: {component_ids}"
    print(f"[PASS] rfuav-two-stage found in component list: {component_ids}")


def test_components_list_includes_sim_inference(platform_process):
    """验证组件列表包含 sim-inference"""
    resp = requests.get(f"{PLATFORM_URL}/api/v1/components", timeout=10)
    assert resp.status_code == 200
    component_ids = [c["id"] for c in resp.json()["components"]]
    assert "sim-inference" in component_ids, \
        f"sim-inference not found in components: {component_ids}"
    print(f"[PASS] sim-inference found in component list: {component_ids}")


def test_components_count_at_least_two(platform_process):
    """验证至少有 2 个组件"""
    resp = requests.get(f"{PLATFORM_URL}/api/v1/components", timeout=10)
    assert resp.status_code == 200
    components = resp.json()["components"]
    assert len(components) >= 2, \
        f"Expected >=2 components, got {len(components)}: {[c['id'] for c in components]}"
    print(f"[PASS] Component count: {len(components)}, ids: {[c['id'] for c in components]}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])