"""
单元测试：FrameQueue 环形缓冲
"""
import pytest
import threading
import time
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.inference.frame_queue import FrameQueue, FrameStats


class TestFrameQueue:
    """FrameQueue 测试"""

    @pytest.fixture
    def fq(self):
        return FrameQueue(capacity=5)

    def test_put_get(self, fq):
        """基本 put/get"""
        fq.put({"frame_id": 1})
        fq.put({"frame_id": 2})
        assert fq.size() == 2

        frame = fq.get_nowait()
        assert frame["frame_id"] == 1
        assert fq.size() == 1

    def test_full_queue_drops_oldest(self, fq):
        """队列满时丢弃最旧帧"""
        for i in range(10):
            fq.put({"frame_id": i})

        assert fq.size() == 5
        stats = fq.stats()
        assert stats.frames_dropped == 5
        assert stats.frames_received == 10

        # 队列中应该是 frame_id 5-9
        oldest = fq.get_nowait()
        assert oldest["frame_id"] == 5

    def test_get_with_wait(self, fq):
        """get 等待"""
        result = [None]
        def producer():
            time.sleep(0.1)
            fq.put({"frame_id": 42})
        t = threading.Thread(target=producer)
        t.start()

        frame = fq.get(timeout=2.0)
        assert frame["frame_id"] == 42
        t.join()

    def test_get_timeout_returns_none(self, fq):
        """get 超时返回 None"""
        frame = fq.get(timeout=0.1)
        assert frame is None

    def test_reset(self, fq):
        """重置队列"""
        for i in range(5):
            fq.put({"frame_id": i})
        fq.reset()
        assert fq.size() == 0
        stats = fq.stats()
        assert stats.frames_received == 0
        assert stats.frames_dropped == 0

    def test_stop(self, fq):
        """stop 停止等待"""
        result = [None]
        def getter():
            result[0] = fq.get(timeout=5.0)
        t = threading.Thread(target=getter)
        t.start()
        time.sleep(0.05)
        fq.stop()
        t.join(timeout=1.0)
        assert result[0] is None  # 已停止，返回 None

    def test_dropped_rate(self, fq):
        """丢弃率计算"""
        for i in range(100):
            fq.put({"frame_id": i})
        rate = fq.dropped_rate
        assert rate == pytest.approx(95 / 100)  # 5 个留下，95 个丢弃

    def test_last_frame_id(self, fq):
        """最后帧序号"""
        for i in [10, 20, 30]:
            fq.put({"frame_id": i})
        stats = fq.stats()
        assert stats.last_frame_id == 30

    def test_capacity_property(self, fq):
        """capacity 属性"""
        assert fq.capacity == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])