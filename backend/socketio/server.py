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
            await self.enter_room(sid, f"session:{session_id}")
            logger.info(f"Socket.IO: {sid} 订阅会话 {session_id}")
        return {
            "success": True,
            "subscribed_events": ["inference_result", "collector_stats", "device_status", "error"]
        }

    async def on_unsubscribe(self, sid: str, data: dict) -> dict:
        """取消订阅"""
        session_id = data.get("session_id")
        if session_id:
            await self.leave_room(sid, f"session:{session_id}")
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

    def init_app(self, fastapi_app: Any = None, platform_ref: Any = None) -> None:
        """
        保留接口但不做实际挂载（挂载已移至 main.py create_socketio_app）
        """
        pass

    async def _emit(self, event: str, data: dict, room: Optional[str] = None) -> None:
        """
        统一的 emit 内部实现。
        优先使用 _sio 全局 emit；fallback 到命名空间直接 emit（不依赖 _sio）。
        """
        if self._sio is not None:
            await self._sio.emit(event, data, room=room, namespace="/")
        elif self._namespace is not None:
            # Fallback: 直接注入到 session room，不依赖 _sio 全局句柄
            payload = dict(data)
            if room:
                for sid in list(self._namespace.rooms("").get(room, set()) or []):
                    asyncio.create_task(self._namespace.emit(event, payload))
            else:
                asyncio.create_task(self._namespace.emit(event, payload))

    async def emit_inference_result(self, session_id: str, result: dict) -> None:
        """推送推理结果到会话房间"""
        await self._emit("inference_result", result, room=f"session:{session_id}")

    async def emit_collector_stats(self, session_id: str, stats: dict) -> None:
        """推送采集统计到会话房间"""
        await self._emit("collector_stats", stats, room=f"session:{session_id}")

    async def emit_device_status(self, event: str, device_id: str, detail: str = "") -> None:
        """推送设备状态"""
        await self._emit("device_status", {
            "event": event,
            "device_id": device_id,
            "timestamp": self._platform_time(),
            "detail": detail
        })

    async def emit_error(self, code: int, message: str, session_id: Optional[str] = None) -> None:
        """推送错误"""
        payload = {
            "code": code,
            "message": message,
            "timestamp": self._platform_time()
        }
        if session_id:
            payload["session_id"] = session_id
        await self._emit("error", payload, room=f"session:{session_id}" if session_id else None)

    @staticmethod
    def _platform_time() -> float:
        import time
        return time.time()