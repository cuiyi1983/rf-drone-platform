"""
CollectorIOClient - Platform 侧数据客户端

支持两种协议：
  TCP模式（collector_type="tcp"）：连接到 Collector 的 6103 端口
  UDP模式（collector_type="udp"）：向 Collector 的 6104 端口注册，接收 UDP 分片帧

数据流（TCP）：Collector TCP Server:6103 → CollectorIOClient.recv_loop() → put_frame(framework)
数据流（UDP）：Collector UDP Server:6104 → CollectorIOClient.recv_loop() → reassemble → put_frame(framework)
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

# TCP 帧头格式
_TCP_FRAME_FMT = "!QdI"  # frame_id(8) + timestamp(8) + data_len(4)
_TCP_FRAME_SIZE = struct.calcsize(_TCP_FRAME_FMT)

# UDP 帧分片格式（与 Collector 侧一致）
_UDP_FRAG_FMT = "!QII dI"  # frame_id(8) + frag_idx(4) + total_frags(4) + timestamp(8) + data_len(4)
_UDP_FRAG_HDR_SIZE = struct.calcsize(_UDP_FRAG_FMT)

# UDP 注册消息
_UDP_REGISTER_FMT = "!IH"
_UDP_REGISTER_SIZE = struct.calcsize(_UDP_REGISTER_FMT)
_UDP_REG_MAGIC = 0x55544450  # 'UDP1'

# 传输格式：实部虚部交织 float32，每样本 8 bytes
_SAMPLE_SIZE = 8


class CollectorIOClient:
    """
    Platform 侧的数据客户端。
    支持 TCP 或 UDP 模式，接收 Collector 发来的 IQ 数据帧并注入 InferenceFramework。
    """

    def __init__(
        self,
        collector_host: str = "localhost",
        tcp_port: int = 6103,
        udp_port: int = 6104,
        collector_type: str = "tcp",
    ):
        self._host = collector_host
        self._tcp_port = tcp_port
        self._udp_port = udp_port
        self._collector_type = collector_type  # "tcp" or "udp"
        self._sock: Optional[socket.socket] = None
        self._framework_ref = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._local_udp_port: int = 0  # 本端 UDP 端口（注册用）

    async def connect(self, framework, session_id: str) -> bool:
        self._framework_ref = framework

        if self._collector_type == "udp":
            return await self._connect_udp()
        else:
            return await self._connect_tcp()

    async def _connect_tcp(self) -> bool:
        """连接到 Collector 的 TCP 数据端口（6103）"""
        for attempt in range(1, 4):
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(10.0)
                self._sock.connect((self._host, self._tcp_port))
                logger.info(f"CollectorIOClient[TCP]: 已连接到 {self._host}:{self._tcp_port}")
                break
            except Exception as e:
                logger.warning(f"CollectorIOClient[TCP]: 连接失败 (尝试 {attempt}/3): {e}")
                if attempt < 3:
                    await asyncio.sleep(0.2)
                self._sock = None

        if self._sock is None:
            logger.error("CollectorIOClient[TCP]: 3 次连接尝试均失败")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._tcp_recv_loop, name="tcp-io-client", daemon=True)
        self._thread.start()
        return True

    async def _connect_udp(self) -> bool:
        """创建 UDP socket 并向 Collector 注册本端端口"""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)  # 16MB recv buffer
            self._sock.bind(("0.0.0.0", 0))  # 自动分配端口
            self._local_udp_port = self._sock.getsockname()[1]
            logger.info(f"CollectorIOClient[UDP]: 已绑定到端口 {self._local_udp_port}")

            # 发送注册包到 Collector 的 UDP 端口
            register_packet = struct.pack(_UDP_REGISTER_FMT, _UDP_REG_MAGIC, self._local_udp_port)
            self._sock.sendto(register_packet, (self._host, self._udp_port))
            logger.info(f"CollectorIOClient[UDP]: 已发送注册包到 {self._host}:{self._udp_port}")

            self._running = True
            self._thread = threading.Thread(target=self._udp_recv_loop, name="udp-io-client", daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            logger.error(f"CollectorIOClient[UDP]: 初始化失败: {e}")
            return False

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

    def _tcp_recv_loop(self) -> None:
        """从 TCP socket 持续读取二进制 IQ 数据帧"""
        while self._running:
            try:
                header = self._sock.recv(_TCP_FRAME_SIZE, socket.MSG_WAITALL)
                if not header or len(header) < _TCP_FRAME_SIZE:
                    continue

                frame_id, timestamp, data_len = struct.unpack(_TCP_FRAME_FMT, header)
                byte_count = data_len * _SAMPLE_SIZE
                data = b""
                while len(data) < byte_count:
                    chunk = self._sock.recv(byte_count - len(data), socket.MSG_WAITALL)
                    if not chunk:
                        break
                    data += chunk

                if len(data) < byte_count:
                    logger.warning("CollectorIOClient[TCP]: 数据不完整，丢弃帧 %d", frame_id)
                    continue

                self._deliver_frame(frame_id, timestamp, data)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug(f"CollectorIOClient[TCP]: recv error: {e}")
                continue

    def _udp_recv_loop(self) -> None:
        """从 UDP socket 持续接收分片 IQ 数据帧"""
        # 分片缓存：frame_id → {frag_idx → data, received_frags, total_frags, timestamp, data_len}
        fragments: dict = {}

        while self._running:
            try:
                self._sock.settimeout(0.5)
                packet, addr = self._sock.recvfrom(65536 + _UDP_FRAG_HDR_SIZE)
                if len(packet) < _UDP_FRAG_HDR_SIZE:
                    continue

                frame_id, frag_idx, total_frags, timestamp, data_len = struct.unpack(
                    _UDP_FRAG_FMT, packet[:_UDP_FRAG_HDR_SIZE]
                )
                frag_data = packet[_UDP_FRAG_HDR_SIZE:]

                # 初始化或更新分片缓存
                if frame_id not in fragments:
                    # 计算该帧总字节数
                    total_bytes = data_len * _SAMPLE_SIZE
                    fragments[frame_id] = {
                        "total_frags": total_frags,
                        "chunks": {},  # frag_idx → bytes
                        "timestamp": timestamp,
                        "data_len": data_len,
                    }

                f = fragments[frame_id]
                f["chunks"][frag_idx] = frag_data

                # 如果收到完整帧，重组并交付
                if len(f["chunks"]) == f["total_frags"]:
                    # 按分片序号拼接
                    full_data = b"".join(f["chunks"][i] for i in range(f["total_frags"]))
                    del fragments[frame_id]
                    self._deliver_frame(frame_id, f["timestamp"], full_data)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug(f"CollectorIOClient[UDP]: recv error: {e}")
                continue

    def _deliver_frame(self, frame_id: int, timestamp: float, data: bytes) -> None:
        """将原始字节数据解码为复数数组并注入框架"""
        try:
            arr = np.frombuffer(data, dtype=np.float32)
            iq_complex = arr[0::2] + 1j * arr[1::2]
            if self._framework_ref:
                iq_frame = {
                    "frame_id": frame_id,
                    "timestamp": timestamp,
                    "iq_data": iq_complex.astype(np.complex64),
                }
                self._framework_ref.put_frame(iq_frame)
        except Exception as e:
            logger.warning("CollectorIOClient: 帧解码失败 frame_id=%d: %s", frame_id, e)

    def is_connected(self) -> bool:
        return self._sock is not None and self._running
