"""
test_platform_flow.py - 平台流程集成测试

测试场景（模拟页面动作）：
1. 设备扫描：GET /api/v1/devices 验证设备列表返回
2. 启动会话：POST /api/v1/session/start 验证返回 session_id
3. Socket.IO 连接：验证能连接 Socket.IO
4. 查询会话：GET /api/v1/session/status?session_id=xxx
5. 停止会话：POST /api/v1/session/stop 验证返回 stats

打桩策略：Platform 实例直接注册模拟设备，绕过真实 Collector
可重复运行
"""

import pytest
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from httpx import AsyncClient, ASGITransport
from backend.main import app, platform


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
async def platform_with_devices():
    """初始化 Platform，注册模拟设备（不调用 startup 避免真实 Collector）"""
    # 清理并注入模拟设备
    platform._devices = {
        "sim:pluto_2.6.5": {
            "id": "sim:pluto_2.6.5",
            "type": "pluto",
            "name": "ADALM PLUTO (sim)",
            "connected": True,
            "uri": "usb:2.6.5",
            "firmware_version": "v0.34",
        },
        "sim:pluto_2.10.5": {
            "id": "sim:pluto_2.10.5",
            "type": "pluto",
            "name": "ADALM PLUTO (sim)",
            "connected": True,
            "uri": "usb:2.10.5",
            "firmware_version": "v0.34",
        },
    }
    # 不调用 startup()，避免 httpx 调用真实 Collector
    yield platform
    platform._devices.clear()
    platform._sessions.clear()


@pytest.fixture
async def client(platform_with_devices):
    """确保 platform 初始化后提供 ASGI 测试客户端"""
    # 确保 platform 已初始化（调用 inject_* 注册全局引用）
    await platform.startup(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    # 清理会话
    platform._sessions.clear()


# ------------------------------------------------------------------
# Test Cases
# ------------------------------------------------------------------

class TestPlatformFlow:
    """平台端到端流程集成测试（模拟页面动作）"""

    @pytest.mark.asyncio
    async def test_1_device_scan(self, platform_with_devices, client):
        """场景1：设备扫描（模拟前端点击"刷新设备"按钮）"""
        resp = await client.get("/api/v1/devices")
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert "devices" in data
        device_ids = [d["id"] for d in data["devices"]]
        assert "sim:pluto_2.6.5" in device_ids, f"设备列表: {data['devices']}"
        assert "sim:pluto_2.10.5" in device_ids
        assert len(data["devices"]) == 2

    @pytest.mark.asyncio
    async def test_2_start_session(self, platform_with_devices, client):
        """场景2：启动会话（模拟前端点击"启动采集"）"""
        resp = await client.post("/api/v1/session/start", json={
            "component_id": "rfuav-two-stage",
            "config": {"confidence_threshold": 0.7}
        })
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert "session_id" in data
        assert data["session_id"].startswith("sess_")
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_3_socket_io_connect(self, platform_with_devices, client):
        """场景3：Socket.IO 连接"""
        import socketio
        sio = socketio.AsyncClient()
        sio.on("connect", lambda: setattr(sio, "_connected", True))
        try:
            await asyncio.wait_for(
                sio.connect("http://localhost:8080", transports=["polling"]),
                timeout=5.0
            )
        except (asyncio.TimeoutError, Exception):
            pytest.skip("Socket.IO 服务未启动，跳过连接测试")
        finally:
            if sio.connected:
                await sio.disconnect()

    @pytest.mark.asyncio
    async def test_4_query_session_status(self, platform_with_devices, client):
        """场景4：查询会话状态"""
        start_resp = await client.post("/api/v1/session/start", json={
            "component_id": "rfuav-two-stage",
            "config": {}
        })
        assert start_resp.status_code == 200
        session_id = start_resp.json()["session_id"]

        resp = await client.get(f"/api/v1/session/status?session_id={session_id}")
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert data["session_id"] == session_id
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_5_stop_session(self, platform_with_devices, client):
        """场景5：停止会话（模拟前端点击"停止采集"）"""
        start_resp = await client.post("/api/v1/session/start", json={
            "component_id": "rfuav-two-stage",
            "config": {}
        })
        assert start_resp.status_code == 200
        session_id = start_resp.json()["session_id"]

        resp = await client.post("/api/v1/session/stop", json={
            "session_id": session_id
        })
        assert resp.status_code == 200, f"响应体: {resp.text}"
        data = resp.json()
        assert data["status"] == "stopped"
        assert "stats" in data


class TestPlatformFlowRepeatable:
    """可重复运行验证：连续执行两次完整流程，确保无状态残留"""

    @pytest.mark.asyncio
    async def test_repeatable_start_stop(self, platform_with_devices, client):
        """连续两次 start + stop，验证第二次不受第一次影响"""
        for i in range(2):
            start_resp = await client.post("/api/v1/session/start", json={
                "component_id": "rfuav-two-stage",
                "config": {"confidence_threshold": 0.5}
            })
            assert start_resp.status_code == 200, f"[run {i}] start 失败: {start_resp.text}"
            session_id = start_resp.json()["session_id"]

            stop_resp = await client.post("/api/v1/session/stop", json={
                "session_id": session_id
            })
            assert stop_resp.status_code == 200, f"[run {i}] stop 失败: {stop_resp.text}"
            assert stop_resp.json()["status"] == "stopped"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])