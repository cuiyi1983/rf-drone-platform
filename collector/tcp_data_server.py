"""
tcp_data_server.py - Collector 侧 TCP/UDP 二进制数据通道

启动时在 6103 端口监听 TCP 连接，在 6104 端口监听 UDP 注册。
Collector._run_loop 每帧通过此通道向已连接客户端发送二进制 IQ 数据。

TCP 传输格式（每帧，28 bytes 头）：
  8 bytes  - frame_id    (uint64, big-endian)
  8 bytes  - timestamp   (float64, Unix epoch)
  4 bytes  - data_len    (uint32, IQ samples count)
  8 bytes  - center_freq (uint64, Hz, 从 Pluto 硬件回读)
  8*N bytes - IQ 数据    (float32 实部, float32 虚部 交织，N = data_len)

UDP 首分片格式（36 bytes 头）：
  8 bytes  - frame_id    (uint64)
  4 bytes  - frag_idx    (uint32)
  4 bytes  - total_frags (uint32)
  8 bytes  - timestamp   (float64)
  4 bytes  - data_len    (uint32)
  8 bytes  - center_freq (uint64, Hz)
  8*N bytes - IQ 数据

UDP 后续分片格式（28 bytes 头，无 center_freq）：
  8 bytes  - frame_id    (uint64)
  4 bytes  - frag_idx    (uint32)
  4 bytes  - total_frags (uint32)
  8 bytes  - timestamp   (float64)
  4 bytes  - data_len    (uint32)
  8*N bytes - IQ 数据
"""

from __future__ import annotations

import struct
import logging
import threading
import socket
import time
import queue
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 端口：数据通道（与 HTTP 5101 分离）
TCP_DATA_PORT = 6103
UDP_DATA_PORT = 6104  # UDP 数据端口（无 TCP 流量控制问题）

# UDP 分片参数
_UDP_MAX_PAYLOAD = 8192  # 每 UDP 包 payload 8KB（避免云服务器 208KB buffer 溢出丢包）
# _UDP_FRAG_HDR_SIZE removed - use _UDP_FRAG_HDR_SIZE or _UDP_FIRST_FRAG_HDR_SIZE
_UDP_MAX_FRAGS = 256  # 每帧最大分片数

# TCP 帧头格式：frame_id(8) + timestamp(8) + data_len(4) + center_freq(8) = 28 bytes
_TCP_FRAME_HEADER_FMT = "!QdIQ"  # big-endian: uint64, double, uint32, uint64
_TCP_FRAME_HEADER_SIZE = struct.calcsize(_TCP_FRAME_HEADER_FMT)

# UDP 首分片头格式：公共头(28) + center_freq(8) = 36 bytes
_UDP_FIRST_FRAG_FMT = "!QII dIQ"  # frame_id + frag_idx + total_frags + timestamp + data_len + center_freq
_UDP_FIRST_FRAG_HDR_SIZE = struct.calcsize(_UDP_FIRST_FRAG_FMT)

# UDP 后续分片头格式（无 center_freq）：28 bytes
_UDP_FRAG_FMT = "!QII dI"  # frame_id + frag_idx + total_frags + timestamp + data_len
_UDP_FRAG_HDR_SIZE = struct.calcsize(_UDP_FRAG_FMT)

# 帧队列最大长度（超过则丢帧）
_MAX_FRAME_QUEUE = 100


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
        # 发送队列 + 独立发送线程（解耦 collector 线程和 TCP 发送）
        self._frame_queue: queue.Queue = queue.Queue(maxsize=_MAX_FRAME_QUEUE)
        self._sender_thread: Optional[threading.Thread] = None
        # 发送统计
        self._total_bytes_sent: int = 0
        self._total_frames_sent: int = 0
        self._total_dropped_frames: int = 0
        self._total_queued_frames: int = 0
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
        self._sender_thread = threading.Thread(target=self._send_loop, name="tcp-sender", daemon=True)
        self._thread.start()
        self._sender_thread.start()
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
        if self._sender_thread:
            self._sender_thread.join(timeout=3.0)
            self._sender_thread = None
        with self._clients_lock:
            for client in self._clients:
                try:
                    client.close()
                except Exception:
                    pass
            self._clients.clear()
        logger.info("TCP data server stopped")

    def broadcast_frame(self, frame_id: int, timestamp: float, iq_data: np.ndarray, center_freq: int) -> None:
        """
        将 IQ 帧加入发送队列（解耦 collector 线程和 TCP 发送）。
        由 Collector._run_loop 调用（每次新帧到达时）。
        center_freq: 当前中心频率（Hz），从 Pluto 硬件回读。
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

        # 打包帧头（包含 center_freq）
        header = struct.pack(_TCP_FRAME_HEADER_FMT, frame_id, timestamp, data_len, center_freq)
        packet = header + raw_bytes

        # 入队列（非阻塞，队列满则丢帧）
        try:
            self._frame_queue.put_nowait(packet)
            self._total_queued_frames += 1
        except queue.Full:
            self._total_dropped_frames += 1
            if self._total_dropped_frames == 1 or self._total_dropped_frames % 1000 == 0:
                logger.warning(
                    f"[TCPDataServer] 发送队列满（已丢 {self._total_dropped_frames} 帧），"
                    f"TCP 发送速度跟不上帧产生速度"
                )

    def _send_loop(self) -> None:
        """
        后台发送线程：从队列取帧，向所有已连接客户端发送。
        这个线程负责所有 TCP 发送，不会阻塞 collector 线程。
        """
        while not self._stop_event.is_set() or not self._frame_queue.empty():
            try:
                # 等待队列中的帧，有帧则立即处理，无帧则等待
                packet = self._frame_queue.get(timeout=0.5)
                packet_len = len(packet)
            except queue.Empty:
                continue

            now_warn = time.monotonic()

            # 节流：只每 10 秒警告一次"无客户端"
            clients_to_remove = []
            with self._clients_lock:
                if not self._clients and (now_warn - self._last_no_client_warn_time >= 10.0):
                    logger.warning(f"[TCPDataServer] 无已连接客户端，{self._total_frames_sent} 帧已发送（platform 的 TCP 客户端是否连接到 6103？）")
                    self._last_no_client_warn_time = now_warn

                for client in self._clients:
                    try:
                        # 设置大 send buffer + 1s 超时
                        client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16 * 1024 * 1024)
                        client.settimeout(1.0)
                        sent = client.send(packet)
                        if sent < packet_len:
                            self._total_dropped_frames += 1
                    except socket.timeout:
                        self._total_dropped_frames += 1
                        if self._total_dropped_frames == 1 or self._total_dropped_frames % 1000 == 0:
                            logger.warning(
                                f"[TCPDataServer] 发送超时（已丢 {self._total_dropped_frames} 帧），"
                                f"接收端消费速度跟不上"
                            )
                    except Exception:
                        clients_to_remove.append(client)

            # 清理断开的客户端
            for client in clients_to_remove:
                try:
                    client.close()
                except Exception:
                    pass
                if client in self._clients:
                    self._clients.remove(client)

            # 统计（所有客户端共享同一 packet，只统计一次）
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
                    f"累计 {self._total_frames_sent} 帧 {self._total_bytes_sent/1_048_576:.2f} MB（丢 {self._total_dropped_frames} 帧）"
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


class UDPDataServer:
    """
    Collector 侧 UDP 二进制数据通道。
    无 TCP 流量控制，适合 localhost 大帧高速传输。
    客户端先发送注册包（包含自己的 UDP 端口），然后 Collector 向该端口发送数据。
    """

    # 注册消息：客户端通知自己的 UDP 接收端口
    _REGISTER_FMT = "!IH"  # magic(4) + port(2)
    _REGISTER_SIZE = struct.calcsize(_REGISTER_FMT)
    _REG_MAGIC = 0x55544450  # 'UDP1'

    def __init__(self, host: str = "0.0.0.0", port: int = UDP_DATA_PORT):
        self._host = host
        self._port = port
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()
        self._clients: list[Tuple[str, int]] = []  # [(ip, port), ...]
        self._clients_lock = threading.Lock()
        # 统计
        self._total_bytes_sent: int = 0
        self._total_frames_sent: int = 0
        self._total_dropped_frames: int = 0
        self._total_udp_packets_sent: int = 0
        self._last_stats_time: float = time.monotonic()
        self._last_stats_bytes: int = 0
        self._last_stats_frames: int = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="udp-data-server", daemon=True)
        self._thread.start()
        logger.info(f"UDP data server started on {self._host}:{self._port}")

    def stop(self) -> None:
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
            self._clients.clear()
        logger.info("UDP data server stopped")

    def broadcast_frame(self, frame_id: int, timestamp: float, iq_data: np.ndarray, center_freq: int) -> None:
        """
        将 IQ 帧通过 UDP 广播到所有已注册客户端。
        自动分片：每帧拆成多个 UDP 包（每包最多 _UDP_MAX_PAYLOAD 字节）。
        center_freq: 当前中心频率（Hz），从 Pluto 硬件回读，仅在首分片中传输。
        """
        if not self._running:
            return
        if iq_data.size == 0:
            return

        # 序列化
        iq_float = np.empty(iq_data.size * 2, dtype=np.float32)
        iq_float[0::2] = iq_data.real.astype(np.float32)
        iq_float[1::2] = iq_data.imag.astype(np.float32)
        raw_bytes = iq_float.tobytes()
        data_len = iq_data.size

        # 分片（后续分片用 28 字节头，首分片用 36 字节头含 center_freq）
        max_payload = _UDP_MAX_PAYLOAD - _UDP_FIRST_FRAG_HDR_SIZE
        samples_per_frag = max_payload // 8  # 8 bytes per complex sample
        total_frags = (len(raw_bytes) + max_payload - 1) // max_payload
        if total_frags > _UDP_MAX_FRAGS:
            self._total_dropped_frames += 1
            logger.warning(f"[UDPDataServer] 帧 %d 太大（%d frags > %d），丢弃",
                          frame_id, total_frags, _UDP_MAX_FRAGS)
            return

        with self._clients_lock:
            clients_snapshot = list(self._clients)

        if not clients_snapshot:
            return

        # 发送分片
        for frag_idx in range(total_frags):
            start = frag_idx * max_payload
            end = min(start + max_payload, len(raw_bytes))
            frag_data = raw_bytes[start:end]

            # 分片头：首分片含 center_freq，后续分片不含
            if frag_idx == 0:
                frag_hdr = struct.pack(
                    _UDP_FIRST_FRAG_FMT,  # frame_id(8) + frag_idx(4) + total_frags(4) + timestamp(8) + data_len(4) + center_freq(8)
                    frame_id, frag_idx, total_frags, timestamp, data_len, center_freq
                )
            else:
                frag_hdr = struct.pack(
                    _UDP_FRAG_FMT,  # frame_id(8) + frag_idx(4) + total_frags(4) + timestamp(8) + data_len(4)
                    frame_id, frag_idx, total_frags, timestamp, data_len
                )
            packet = frag_hdr + frag_data

            for addr in clients_snapshot:
                try:
                    self._socket.sendto(packet, addr)
                    self._total_udp_packets_sent += 1
                except Exception:
                    pass

        self._total_bytes_sent += len(raw_bytes)
        self._total_frames_sent += 1

        # 每 10 秒打印统计
        now = time.monotonic()
        elapsed = now - self._last_stats_time
        if elapsed >= 10.0:
            bytes_delta = self._total_bytes_sent - self._last_stats_bytes
            frames_delta = self._total_frames_sent - self._last_stats_frames
            mbps = (bytes_delta / elapsed) / 1_048_576 if elapsed > 0 else 0
            fps = frames_delta / elapsed if elapsed > 0 else 0
            logger.info(
                f"[UDPDataServer 发送统计] 耗时 {elapsed:.1f}s | "
                f"{frames_delta} 帧 ({fps:.1f} fps) | "
                f"{bytes_delta/1_048_576:.2f} MB ({mbps:.2f} MB/s) | "
                f"累计 {self._total_frames_sent} 帧 {self._total_udp_packets_sent} UDP包（丢 {self._total_dropped_frames} 帧）"
            )
            self._last_stats_time = now
            self._last_stats_bytes = self._total_bytes_sent
            self._last_stats_frames = self._total_frames_sent

    def _run(self) -> None:
        """后台线程：处理注册消息"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 增大接收缓冲区，避免注册包被丢
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        try:
            self._socket.bind((self._host, self._port))
            logger.info(f"UDP data server listening on {self._host}:{self._port}")
            self._socket.settimeout(1.0)
        except Exception as e:
            logger.error(f"UDP data server bind error: {e}")
            self._running = False
            return

        while not self._stop_event.is_set():
            try:
                data, addr = self._socket.recvfrom(1024)
                if len(data) >= self._REGISTER_SIZE:
                    magic, client_port = struct.unpack(self._REGISTER_FMT, data[:self._REGISTER_SIZE])
                    if magic == self._REG_MAGIC:
                        client_addr = (addr[0], client_port)
                        with self._clients_lock:
                            if client_addr not in self._clients:
                                self._clients.append(client_addr)
                                logger.info(f"[UDPDataServer] 客户端注册: {client_addr}")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug(f"UDP recv error: {e}")
                continue