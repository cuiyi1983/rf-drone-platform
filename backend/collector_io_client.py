"""
CollectorIOClient - Platform 侧 Socket.IO 客户端

从 Collector（5101）接收 IQ 数据帧，转发到 InferenceFramework。
数据流：Collector Socket.IO → Platform Socket.IO Client → put_frame(framework) → infer()
"""

import asyncio
import logging
from typing import Optional

import socketio

logger = logging.getLogger(__name__)


class CollectorIOClient:
    """
    Platform 侧的 Socket.IO 客户端。
    连接到 Collector 的 Socket.IO 服务器，接收 IQ 数据帧并注入 InferenceFramework。
    """

    def __init__(self, collector_url: str = "http://localhost:5101"):
        self._collector_url = collector_url
        self._sio: Optional[socketio.AsyncClient] = None
        self._framework_ref = None  # InferenceFramework 实例
        self._session_id: Optional[str] = None
        self._running = False

    # ── 生命周期 ───────────────────────────────────────────────────

    async def connect(self, framework, session_id: str) -> bool:
        """
        连接到 Collector Socket.IO 并订阅 session_id 房间。
        framework: InferenceFramework 实例（用于 put_frame）
        """
        self._framework_ref = framework
        self._session_id = session_id

        sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=3,
            reconnection_delay=1.0,
        )

        @sio.on("connect", namespace="/")
        async def on_connect():
            logger.info("CollectorIOClient: 已连接到 Collector Socket.IO")
            # 订阅 session 房间
            await sio.emit("subscribe", {"session_id": session_id}, namespace="/")
            logger.info(f"CollectorIOClient: 已订阅 session {session_id}")

        @sio.on("disconnect", namespace="/")
        async def on_disconnect():
            logger.info("CollectorIOClient: 与 Collector Socket.IO 断开")

        @sio.on("message", namespace="/")
        async def on_message(data):
            """接收 Collector 推送的事件（iq_frame / collector_stats 等）"""
            if not data:
                return
            event_type = data.get("type", "")
            if event_type == "iq_frame":
                await self._handle_iq_frame(data.get("frame", {}))
            elif event_type == "collector_stats":
                # 透传给 Platform 的 stats 回调（通过 framework 统计）
                logger.debug(f"Collector stats: {data.get('stats')}")

        @sio.on("error", namespace="/")
        async def on_error(data):
            logger.warning(f"Collector Socket.IO error event: {data}")

        try:
            await sio.connect(
                self._collector_url,
                transports=["polling"],
                socketio_path="/socket.io",
            )
            self._sio = sio
            self._running = True
            return True
        except Exception as e:
            logger.error(f"CollectorIOClient: 连接失败: {e}")
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        if self._sio:
            try:
                await self._sio.disconnect()
            except Exception as e:
                logger.debug(f"CollectorIOClient: disconnect error: {e}")
            self._sio = None
        self._framework_ref = None
        self._session_id = None

    # ── 内部处理 ───────────────────────────────────────────────────

    async def _handle_iq_frame(self, frame: dict) -> None:
        """
        处理接收到的 IQ 帧。
        将帧转换为 InferenceFramework 期望的格式并注入。
        """
        if not self._framework_ref:
            return

        try:
            # frame 格式：{frame_id, burst_id, timestamp, center_freq, sample_rate, iq_data, metadata}
            iq_data = frame.get("iq_data", [])
            if not iq_data:
                return

            # 转换为复数形式 [real + j*imag]
            import numpy as np

            arr = np.array(iq_data, dtype=np.float32)
            if arr.ndim == 2 and arr.shape[1] == 2:
                iq_complex = arr[:, 0] + 1j * arr[:, 1]
            else:
                logger.warning(f"CollectorIOClient: iq_data 格式异常 shape={arr.shape}")
                return

            # 构造 iq_frame dict（InferenceFramework 期望的格式）
            iq_frame = {
                "frame_id": frame.get("frame_id", 0),
                "burst_id": frame.get("burst_id", 0),
                "timestamp": frame.get("timestamp", 0.0),
                "center_freq": frame.get("center_freq", 5_805_000_000),
                "sample_rate": frame.get("sample_rate", 60_000_000),
                "iq_data": iq_complex,
            }

            self._framework_ref.put_frame(iq_frame)

        except Exception as e:
            logger.error(f"CollectorIOClient: 处理 iq_frame 异常: {e}")