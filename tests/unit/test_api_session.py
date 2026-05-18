"""
单元测试：Session API
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from httpx import AsyncClient, ASGITransport
from backend.main import app, platform


class TestSessionAPI:
    """Session API 测试"""

    @pytest.fixture(autouse=True)
    async def setup(self):
        """每个测试前初始化，测试隔离用mock设备注册"""
        await platform.startup(app)
        # 测试隔离：注册最小化测试设备（仅用于单元测试隔离）
        if not platform._devices:
            platform._devices["pluto_usb_2.6.5"] = {
                "id": "pluto_usb_2.6.5",
                "type": "PlutoSDR",
                "connected": False,
                "uri": "usb:2.6.5",
                "firmware_version": "0.34",
            }

    @pytest.fixture
    async def client(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_start_session(self, client):
        """启动会话"""
        resp = await client.post("/api/v1/session/start", json={
            "component_id": "rfuav-two-stage",
            "config": {"confidence_threshold": 0.5}
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_start_session_missing_component(self, client):
        """启动会话 - 组件不存在"""
        resp = await client.post("/api/v1/session/start", json={
            "component_id": "nonexistent"
        })
        assert resp.status_code in (400, 500)  # 组件不存在 → 400

    @pytest.mark.asyncio
    async def test_start_session_missing_component_id(self, client):
        """启动会话 - 缺少 component_id"""
        resp = await client.post("/api/v1/session/start", json={})
        assert resp.status_code == 400  # FastAPI 400

    @pytest.mark.asyncio
    async def test_stop_session(self, client):
        """停止会话"""
        # 先启动
        start_resp = await client.post("/api/v1/session/start", json={
            "component_id": "rfuav-two-stage"
        })
        session_id = start_resp.json()["session_id"]

        # 再停止
        resp = await client.post("/api/v1/session/stop", json={
            "session_id": session_id
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopped"
        assert "stats" in data

    @pytest.mark.asyncio
    async def test_stop_session_not_found(self, client):
        """停止会话 - 不存在"""
        resp = await client.post("/api/v1/session/stop", json={
            "session_id": "nonexistent"
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_session_status_all(self, client):
        """查询所有会话"""
        resp = await client.get("/api/v1/session/status")
        assert resp.status_code == 200
        assert "sessions" in resp.json()

    @pytest.mark.asyncio
    async def test_session_status_one(self, client):
        """查询单个会话"""
        # 启动
        start_resp = await client.post("/api/v1/session/start", json={
            "component_id": "rfuav-two-stage"
        })
        session_id = start_resp.json()["session_id"]

        # 查询
        resp = await client.get(f"/api/v1/session/status?session_id={session_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_session_status_not_found(self, client):
        """查询不存在的会话"""
        resp = await client.get("/api/v1/session/status?session_id=nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_session_config(self, client):
        """更新会话配置"""
        # 启动
        start_resp = await client.post("/api/v1/session/start", json={
            "component_id": "rfuav-two-stage"
        })
        session_id = start_resp.json()["session_id"]

        # 更新配置
        resp = await client.patch(
            f"/api/v1/session/{session_id}/config",
            json={"frequency": 2_450_000_000}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id
        assert "updated_config" in data


class TestComponentsAPI:
    """Components API 测试"""

    @pytest.fixture
    async def client(self):
        await platform.startup(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_list_components(self, client):
        """列出组件"""
        resp = await client.get("/api/v1/components")
        assert resp.status_code == 200
        data = resp.json()
        assert "components" in data
        assert len(data["components"]) >= 1

    @pytest.mark.asyncio
    async def test_get_component_detail(self, client):
        """获取组件详情"""
        resp = await client.get("/api/v1/components/rfuav-two-stage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "rfuav-two-stage"
        assert "collector_requirements" in data

    @pytest.mark.asyncio
    async def test_get_component_detail_not_found(self, client):
        """组件不存在"""
        resp = await client.get("/api/v1/components/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_component_config_schema(self, client):
        """获取配置 Schema"""
        resp = await client.get("/api/v1/components/rfuav-two-stage/config-schema")
        assert resp.status_code == 200
        data = resp.json()
        assert "config_schema" in data
        assert "confidence_threshold" in data["config_schema"]


class TestDevicesAPI:
    """Devices API 测试"""

    @pytest.fixture
    async def client(self):
        await platform.startup(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_options_devices_cors_preflight(self, client):
        """验证 CORS preflight OPTIONS /api/v1/devices 返回 200"""
        resp = await client.options("/api/v1/devices")
        assert resp.status_code == 200, f"CORS preflight failed: {resp.status_code} {resp.text}"
        assert "access-control-allow-origin" in resp.headers

    @pytest.mark.asyncio
    async def test_list_devices(self, client):
        """列出设备"""
        resp = await client.get("/api/v1/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert "devices" in data

    @pytest.mark.asyncio
    async def test_get_device_capabilities(self, client):
        """获取设备能力"""
        resp = await client.get("/api/v1/devices/pluto_usb_2.6.5/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data
        assert "frequency" in data["capabilities"]

    @pytest.mark.asyncio
    async def test_get_device_capabilities_not_found(self, client):
        """设备不存在"""
        resp = await client.get("/api/v1/devices/nonexistent/capabilities")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_refresh_devices(self, client):
        """刷新设备列表：重新调用 Collector 扫描"""
        resp = await client.post("/api/v1/devices/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert "devices" in data
    """Simulator API 测试"""

    @pytest.fixture
    async def client(self):
        await platform.startup(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_options_session_start_cors_preflight(self, client):
        """CORS preflight: OPTIONS /api/v1/session/start 返回 200"""
        resp = await client.options("/api/v1/session/start")
        assert resp.status_code == 200, f"CORS preflight failed: {resp.status_code}"
        assert "access-control-allow-origin" in resp.headers

    @pytest.mark.asyncio
    async def test_health(self, client):
        """健康检查"""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_simulator_metadata(self, client):
        """模拟器元数据"""
        resp = await client.get("/api/v1/simulator/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert data["simulator"]["enabled"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])