"""
CollectorIOClient - Platform 侧 TCP 二进制数据客户端

从 Collector（6103端口）接收 IQ 数据帧，转发到 InferenceFramework。
数据流：Collector TCP Server:6103 → CollectorIOClient.recv_loop() → put_frame(framework) → infer()
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 帧头格式：frame_id(8) + timestamp(8) + data_len(4) = 20 bytes
_FRAME_HEADER_FMT = "!QdI"  # big-endian
_FRAME_HEADER_SIZE = struct.calcsize(_FRAME_HEADER_FMT)
# 传输格式：实部虚部交织 float32，每样本 8 bytes
_SAMPLE_SIZE = 8  # 4 bytes real + 4 bytes imag


class CollectorIOClient:
    """
    Platform 侧的 TCP 客户端。
    连接到 Collector 的 TCP 数据端口（6103），接收二进制 IQ 数据并注入 InferenceFramework。
    """

    def __init__(self, collector_host: str = "localhost", collector_port: int = 6103):
        self._host = collector_host
        self._port = collector_port
        self._sock: Optional[socket.socket] = None
        self._framework_ref = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    async def connect(self, framework, session_id: str) -> bool:
        """
        连接到 Collector 的 TCP 数据端口并启动接收线程。
        framework: InferenceFramework 实例

        连接失败时自动重试（最多 3 次，间隔 200ms），应对 Windows 时序问题。
        """
        self._framework_ref = framework

        # 重试机制：应对 Collector 侧 TCP server 尚未完全就绪的情况
        for attempt in range(1, 4):
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(10.0)
                self._sock.connect((self._host, self._port))
                logger.info(f"CollectorIOClient: 已连接到 {self._host}:{self._port}")
                break  # 连接成功，跳出重试
            except Exception as e:
                logger.warning(f"CollectorIOClient: 连接失败 (尝试 {attempt}/3): {e}")
                if attempt < 3:
                    await asyncio.sleep(0.2)  # 等待 200ms 后重试
                self._sock = None

        if self._sock is None:
            logger.error(f"CollectorIOClient: 3 次连接尝试均失败")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, name="tcp-io-client", daemon=True)
        self._thread.start()
        return True

    async def disconnect(self) -> None:
        """断开连接并停止接收线程"""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._framework_ref = None
        logger.info("CollectorIOClient: 已断开")

    def _recv_loop(self) -> None:
        """
        接收线程：从 TCP socket 持续读取二进制 IQ 数据帧，转发给框架。
        帧格式：frame_id(8) + timestamp(8) + data_len(4) + data_len×float32×2
        """
        while self._running:
            try:
                # 读取帧头
                header = self._sock.recv(_FRAME_HEADER_SIZE, socket.MSG_WAITALL)
                if not header or len(header) < _FRAME_HEADER_SIZE:
                    continue

                frame_id, timestamp, data_len = struct.unpack(_FRAME_HEADER_FMT, header)

                # 读取 IQ 数据
                byte_count = data_len * _SAMPLE_SIZE
                data = b""
                while len(data) < byte_count:
                    chunk = self._sock.recv(byte_count - len(data), socket.MSG_WAITALL)
                    if not chunk:
                        break
                    data += chunk

                if len(data) < byte_count:
                    logger.warning("CollectorIOClient: 数据不完整，丢弃帧 %d", frame_id)
                    continue

                # 解码为复数数组
                arr = np.frombuffer(data, dtype=np.float32)
                iq_complex = arr[0::2] + 1j * arr[1::2]

                # 构造 iq_frame dict 并注入框架
                if self._framework_ref:
                    iq_frame = {
                        "frame_id": frame_id,
                        "timestamp": timestamp,
                        "iq_data": iq_complex.astype(np.complex64),
                    }
                    self._framework_ref.put_frame(iq_frame)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug(f"CollectorIOClient: recv error: {e}")
                continue

    def is_connected(self) -> bool:
        """返回 TCP 连接是否存活"""
        return self._sock is not None and self._running
