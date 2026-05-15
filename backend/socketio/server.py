"""
Socket.IO Server - 数据面推送
推送推理结果、采集统计、设备状态、错误
"""
import asyncio
import logging
from typing import Any, Optional

from socketio import AsyncNamespace, AsyncServer

logger = logging.getLogger(__name__)


class PlatformNamespace(AsyncNamespace):
    """
    Socket.IO 命名空间 /
    负责推送 inference_result / collector_stats / device_status / error
    """

    def __init__(self, namespace: str, platform_ref: Any):
        super().__init__(namespace)
        self._platform_ref = platform_ref  # 后向引用 Platform 实例

    async def on_connect(self, sid: str, environ: dict, auth: Optional[dict] = None) -> bool:
        logger.info(f"Socket.IO: client connected {sid}")
        return True

    async def on_disconnect(self, sid: str) -> None:
        logger.info(f"Socket.IO: client disconnected {sid}")

    async def on_subscribe(self, sid: str, data: dict) -> dict:
        """订阅会话"""
        session_id = data.get("session_id")
        if session_id:
            self.enter_room(sid, f"session:{session_id}")
            logger.info(f"Socket.IO: {sid} 订阅会话 {session_id}")
        return {
            "success": True,
            "subscribed_events": ["inference_result", "collector_stats", "device_status", "error"]
        }

    async def on_unsubscribe(self, sid: str, data: dict) -> dict:
        """取消订阅"""
        session_id = data.get("session_id")
        if session_id:
            self.leave_room(sid, f"session:{session_id}")
            logger.info(f"Socket.IO: {sid} 取消订阅会话 {session_id}")
        return {"success": True}

    async def on_get_history(self, sid: str, data: dict) -> dict:
        """请求历史推理结果"""
        session_id = data.get("session_id", "")
        limit = min(data.get("limit", 100), 1000)
        history = self._platform_ref.get_inference_history(session_id, limit)
        return {
            "success": True,
            "results": history,
            "total": len(history),
            "returned": len(history)
        }


class SocketIOServer:
    """
    Socket.IO Server 封装
    """

    def __init__(self):
        self._sio: Optional[AsyncServer] = None
        self._namespace: Optional[PlatformNamespace] = None

    def init_app(self, app: Any, platform_ref: Any) -> AsyncServer:
        import socketio

        sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins="*",
            logger=False,
            engineio_logger=False
        )

        namespace = PlatformNamespace("/", platform_ref)
        sio.register_namespace(namespace)

        self._sio = sio
        self._namespace = namespace

        # 挂载到 ASGI app
        socketio.ASGIApp(sio, app)

        logger.info("Socket.IO Server 初始化完成")
        return sio

    def emit_inference_result(self, session_id: str, result: dict) -> None:
        """推送推理结果到会话房间"""
        if self._sio is None:
            return
        self._sio.emit(
            "inference_result",
            result,
            room=f"session:{session_id}",
            namespace="/"
        )

    def emit_collector_stats(self, session_id: str, stats: dict) -> None:
        """推送采集统计到会话房间"""
        if self._sio is None:
            return
        self._sio.emit(
            "collector_stats",
            stats,
            room=f"session:{session_id}",
            namespace="/"
        )

    def emit_device_status(self, event: str, device_id: str, detail: str = "") -> None:
        """推送设备状态"""
        if self._sio is None:
            return
        self._sio.emit(
            "device_status",
            {
                "event": event,
                "device_id": device_id,
                "timestamp": self._platform_time(),
                "detail": detail
            },
            namespace="/"
        )

    def emit_error(self, code: int, message: str, session_id: Optional[str] = None) -> None:
        """推送错误"""
        if self._sio is None:
            return
        payload = {
            "code": code,
            "message": message,
            "timestamp": self._platform_time()
        }
        if session_id:
            payload["session_id"] = session_id
        self._sio.emit("error", payload, namespace="/")

    @staticmethod
    def _platform_time() -> float:
        import time
        return time.time()