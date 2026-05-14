"""
Frame Queue - 环形缓冲
消费不了就扔，队列满丢弃最旧帧
"""
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class FrameStats:
    """帧统计信息"""
    frames_received: int = 0
    frames_dropped: int = 0
    last_frame_id: int = -1
    dropped_reasons: list[str] = field(default_factory=list)


class FrameQueue:
    """
    环形缓冲帧队列

    - 固定容量（默认 100 帧 ≈ 3-5 秒 @ 6fps）
    - 队列满时丢弃最旧帧（drop oldest）
    - 线程安全
    """

    def __init__(self, capacity: int = 100):
        self._capacity = capacity
        self._queue: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._stats = FrameStats()
        self._running = True

    def put(self, frame: dict[str, Any]) -> None:
        """
        放入一帧，队列满时丢弃最旧帧
        """
        with self._lock:
            frame_id = frame.get("frame_id", -1)

            # 丢弃原因记录（超过容量才丢弃）
            if len(self._queue) >= self._capacity:
                self._stats.frames_dropped += 1
                dropped_frame = self._queue[0]
                dropped_id = dropped_frame.get("frame_id", "?")
                reason = f"queue_full: dropped frame_id={dropped_id} to make room for {frame_id}"
                self._stats.dropped_reasons.append(reason)
                logger.debug(f"FrameQueue: {reason}")

            self._queue.append(frame)
            self._stats.frames_received += 1
            self._stats.last_frame_id = frame_id

            self._not_empty.notify()

    def get(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """
        取出一帧，队列空则阻塞等待

        Returns:
            frame dict 或 None（超时）
        """
        with self._not_empty:
            while len(self._queue) == 0 and self._running:
                if not self._not_empty.wait(timeout=timeout or 1.0):
                    return None
            if not self._running:
                return None
            return self._queue.popleft()

    def get_nowait(self) -> Optional[dict[str, Any]]:
        """非阻塞取帧，队列空返回 None"""
        with self._lock:
            if len(self._queue) == 0:
                return None
            return self._queue.popleft()

    def size(self) -> int:
        """当前队列深度"""
        with self._lock:
            return len(self._queue)

    def stats(self) -> FrameStats:
        """返回统计信息"""
        with self._lock:
            return FrameStats(
                frames_received=self._stats.frames_received,
                frames_dropped=self._stats.frames_dropped,
                last_frame_id=self._stats.last_frame_id,
                dropped_reasons=self._stats.dropped_reasons[-10:]  # 最近10条
            )

    def reset(self) -> None:
        """清空队列和统计"""
        with self._lock:
            self._queue.clear()
            self._stats = FrameStats()
            logger.info("FrameQueue: 已重置")

    def stop(self) -> None:
        """停止等待"""
        with self._lock:
            self._running = False
            self._not_empty.notify_all()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def dropped_rate(self) -> float:
        """丢弃率"""
        total = self._stats.frames_received
        if total == 0:
            return 0.0
        return self._stats.frames_dropped / total