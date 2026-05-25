"""
tcp_data_server.py - Collector 侧 TCP 二进制数据通道

启动时在 6103 端口监听，接收 Platform Backend 的 TCP 连接请求。
Collector._run_loop 每帧通过此通道向已连接客户端发送二进制 IQ 数据。

传输格式（每帧）：
  8 bytes  - frame_id  (uint64, big-endian)
  8 bytes  - timestamp (float64, Unix epoch)
  4 bytes  - data_len  (uint32, IQ samples count)
  8*N bytes - IQ 数据 (float32 实部, float32 虚部 交织，N = data_len)
"""

from __future__ import annotations

import struct
import logging
import threading
import socket
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 端口：数据通道（与 HTTP 5101 分离）
TCP_DATA_PORT = 6103

# 帧头格式：frame_id(8) + timestamp(8) + data_len(4) = 20 bytes
_FRAME_HEADER_FMT = "!QdI"  # big-endian: unsigned long long, double, unsigned int
_FRAME_HEADER_SIZE = struct.calcsize(_FRAME_HEADER_FMT)


class TCPDataServer:
    """
    Collector 侧 TCP 数据通道服务端。
    管理客户端连接列表，广播 IQ 帧到所有已连接客户端。
    """

    def __init__(self, host: str = "0.0.0.0", port: int = TCP_DATA_PORT):
        self._host = host
        self._port = port
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._stop_event = threading.Event()
        # 发送统计
        self._total_bytes_sent: int = 0
        self._total_frames_sent: int = 0
        self._last_stats_time: float = time.monotonic()
        self._last_stats_bytes: int = 0
        self._last_stats_frames: int = 0
        self._last_no_client_warn_time: float = time.monotonic()

    def start(self) -> None:
        """启动 TCP 数据服务器（后台线程）"""
        if self._running:
            logger.warning("TCP data server already running")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="tcp-data-server", daemon=True)
        self._thread.start()
        logger.info(f"TCP data server started on {self._host}:{self._port}")

    def stop(self) -> None:
        """停止 TCP 数据服务器"""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._clients_lock:
            for client in self._clients:
                try:
                    client.close()
                except Exception:
                    pass
            self._clients.clear()
        logger.info("TCP data server stopped")

    def broadcast_frame(self, frame_id: int, timestamp: float, iq_data: np.ndarray) -> None:
        """
        将 IQ 帧广播到所有已连接客户端。
        由 Collector._run_loop 调用（每次新帧到达时）。
        """
        if not self._running:
            return

        # 序列化 IQ 数据：实部虚部交织 float32
        if iq_data.size == 0:
            return

        iq_float = np.empty(iq_data.size * 2, dtype=np.float32)
        iq_float[0::2] = iq_data.real.astype(np.float32)
        iq_float[1::2] = iq_data.imag.astype(np.float32)
        raw_bytes = iq_float.tobytes()
        data_len = iq_data.size

        # 打包帧头
        header = struct.pack(_FRAME_HEADER_FMT, frame_id, timestamp, data_len)
        packet = header + raw_bytes
        packet_len = len(packet)

        # 节流：只每 10 秒警告一次"无客户端"
        now_warn = time.monotonic()

        clients_to_remove = []
        with self._clients_lock:
            if not self._clients and (now_warn - self._last_no_client_warn_time >= 10.0):
                logger.warning(f"[TCPDataServer] 无已连接客户端，当前 {self._total_frames_sent} 帧均被丢弃（platform 的 TCP 客户端是否连接到 6103？）")
                self._last_no_client_warn_time = now_warn
            for client in self._clients:
                try:
                    client.sendall(packet)
                except Exception:
                    clients_to_remove.append(client)

        # 清理断开的客户端
        for client in clients_to_remove:
            self._clients.remove(client)
            try:
                client.close()
            except Exception:
                pass

        # 统计
        self._total_bytes_sent += packet_len
        self._total_frames_sent += 1

        # 每 10 秒打印一次统计
        now = time.monotonic()
        elapsed = now - self._last_stats_time
        if elapsed >= 10.0:
            bytes_delta = self._total_bytes_sent - self._last_stats_bytes
            frames_delta = self._total_frames_sent - self._last_stats_frames
            mbps = (bytes_delta / elapsed) / 1_048_576 if elapsed > 0 else 0
            fps = frames_delta / elapsed if elapsed > 0 else 0
            logger.info(
                f"[TCPDataServer 发送统计] 耗时 {elapsed:.1f}s | "
                f"发送 {frames_delta} 帧 ({fps:.1f} fps) | "
                f"{bytes_delta/1_048_576:.2f} MB ({mbps:.2f} MB/s) | "
                f"累计 {self._total_frames_sent} 帧 {self._total_bytes_sent/1_048_576:.2f} MB"
            )
            self._last_stats_time = now
            self._last_stats_bytes = self._total_bytes_sent
            self._last_stats_frames = self._total_frames_sent

    def _run(self) -> None:
        """后台线程：接受客户端连接"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.settimeout(1.0)  # 允许定期检查 _stop_event

        try:
            self._socket.bind((self._host, self._port))
            self._socket.listen(5)
            logger.info(f"TCP data server listening on {self._host}:{self._port}")

            while not self._stop_event.is_set():
                try:
                    client, addr = self._socket.accept()
                    logger.info(f"TCP data client connected: {addr}")
                    with self._clients_lock:
                        self._clients.append(client)
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._running:
                        logger.warning(f"TCP accept error: {e}")
                    continue

        except Exception as e:
            logger.error(f"TCP data server error: {e}")
        finally:
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
            self._running = False