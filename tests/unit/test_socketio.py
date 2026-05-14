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

    def test_init_app_creates_sio(self, sio_server):
        """init_app 初始化 AsyncServer"""
        mock_app = MagicMock()
        mock_platform = MagicMock()
        sio = sio_server.init_app(mock_app, mock_platform)
        assert sio is not None
        assert sio_server._sio is not None

    def test_emit_inference_result_no_crash(self, sio_server):
        """emit_inference_result 不崩溃（无 sio 时）"""
        # 不挂载时不崩溃
        sio_server.emit_inference_result("sess_123", {"frame_id": 1})

    def test_emit_inference_result_routes_to_room(self, sio_server):
        """emit_inference_result 发送到 session 房间"""
        mock_sio = MagicMock()
        sio_server._sio = mock_sio
        sio_server.emit_inference_result("sess_abc", {"frame_id": 99})
        mock_sio.emit.assert_called_once()
        call_args = mock_sio.emit.call_args
        assert call_args[0][0] == "inference_result"
        assert call_args[1]["room"] == "session:sess_abc"
        assert call_args[1]["namespace"] == "/"

    def test_emit_collector_stats_routes_to_room(self, sio_server):
        """emit_collector_stats 发送到 session 房间"""
        mock_sio = MagicMock()
        sio_server._sio = mock_sio
        sio_server.emit_collector_stats("sess_xyz", {"fps": 6.5})
        mock_sio.emit.assert_called_once()
        call_args = mock_sio.emit.call_args
        assert call_args[0][0] == "collector_stats"
        assert call_args[1]["room"] == "session:sess_xyz"

    def test_emit_device_status_broadcasts(self, sio_server):
        """emit_device_status 广播（无 room）"""
        mock_sio = MagicMock()
        sio_server._sio = mock_sio
        sio_server.emit_device_status("connected", "pluto_usb_2.6.5", "USB connected")
        mock_sio.emit.assert_called_once()
        call_args = mock_sio.emit.call_args
        assert call_args[0][0] == "device_status"
        assert "room" not in call_args[1]

    def test_emit_error_broadcasts(self, sio_server):
        """emit_error 广播（无 room）"""
        mock_sio = MagicMock()
        sio_server._sio = mock_sio
        sio_server.emit_error(2001, "组件异常", "sess_123")
        mock_sio.emit.assert_called_once()
        call_args = mock_sio.emit.call_args
        assert call_args[0][0] == "error"
        assert "room" not in call_args[1]

    def test_platform_time(self, sio_server):
        """_platform_time 返回 Unix 时间戳"""
        t = sio_server._platform_time()
        assert isinstance(t, float)
        assert t > 1_700_000_000  # 大约 2020 年后


if __name__ == "__main__":
    pytest.main([__file__, "-v"])