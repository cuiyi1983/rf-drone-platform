"""
单元测试：Socket.IO Server
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.socketio.server import SocketIOServer
from unittest.mock import MagicMock, AsyncMock


class TestSocketIOServer:
    """Socket.IO Server 测试"""

    @pytest.fixture
    def sio_server(self):
        return SocketIOServer()

    def test_init_app_does_not_crash(self, sio_server):
        """init_app 不崩溃（挂载逻辑已移至 create_socketio_app）"""
        mock_app = MagicMock()
        mock_platform = MagicMock()
        # init_app 是 no-op，不返回值也不设置_sio
        # Socket.IO 的真实挂载在 main.py 的 create_socketio_app 中
        sio_server.init_app(mock_app, mock_platform)
        # 不崩溃即通过
        assert True

    def test_emit_inference_result_no_crash(self, sio_server):
        """emit_inference_result 不崩溃（无 sio 时）"""
        # 不挂载时不崩溃
        sio_server.emit_inference_result("sess_123", {"frame_id": 1})

    def test_emit_collector_stats_no_crash(self, sio_server):
        """emit_collector_stats 不崩溃"""
        sio_server.emit_collector_stats("sess_123", {"fps": 6.5})

    def test_emit_device_status_no_crash(self, sio_server):
        """emit_device_status 不崩溃"""
        sio_server.emit_device_status("connected", "pluto_usb_2.6.5", "USB connected")

    def test_emit_error_no_crash(self, sio_server):
        """emit_error 不崩溃"""
        sio_server.emit_error(2001, "组件异常", "sess_123")

    def test_platform_time(self, sio_server):
        """_platform_time 返回 Unix 时间戳"""
        t = sio_server._platform_time()
        assert isinstance(t, float)
        assert t > 1_700_000_000  # 大约 2020 年后


if __name__ == "__main__":
    pytest.main([__file__, "-v"])