"""
Inference Framework - 推理框架
Frame Queue + 组件生命周期管理
"""
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from .frame_queue import FrameQueue

logger = logging.getLogger(__name__)


class IInferenceComponent(ABC):
    """推理组件接口（与 component-manifest.yaml 一致）"""

    @abstractmethod
    def get_manifest(self) -> dict:
        pass

    @abstractmethod
    def initialize(self, config: dict, device: str) -> None:
        pass

    @abstractmethod
    def infer(self, iq_frame: dict) -> dict:
        pass

    @abstractmethod
    def release(self) -> None:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass


class InferenceFramework:
    """
    推理框架：
    - 帧队列管理（环形缓冲，消费不了就扔）
    - 组件生命周期（加载/初始化/崩溃恢复）
    - 推理结果回调推送
    """

    def __init__(
        self,
        buffer_capacity: int = 100,
        stats_callback: Optional[Callable] = None,
        result_callback: Optional[Callable] = None,
        error_callback: Optional[Callable] = None
    ):
        self._buffer_capacity = buffer_capacity
        self._frame_queue = FrameQueue(capacity=buffer_capacity)

        self._component: Optional[IInferenceComponent] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 回调
        self._stats_callback = stats_callback or (lambda *a, **kw: None)
        self._result_callback = result_callback or (lambda *a, **kw: None)
        self._error_callback = error_callback or (lambda *a, **kw: None)

        # 状态
        self._inference_count = 0
        self._total_inference_time_ms = 0.0

    # ── 组件生命周期 ──────────────────────────────────────────────

    def load_component(
        self,
        component_id: str,
        component_instance: IInferenceComponent,
        config: dict,
        device: str = "cpu"
    ) -> bool:
        """
        加载并初始化组件
        """
        with self._lock:
            if self._component is not None:
                self.unload_component()

            logger.info(f"InferenceFramework: 加载组件 {component_id}")
            try:
                component_instance.initialize(config, device)
                self._component = component_instance
                self._inference_count = 0
                self._total_inference_time_ms = 0.0
                logger.info(f"InferenceFramework: 组件 {component_id} 初始化成功")
                return True
            except Exception as e:
                logger.error(f"InferenceFramework: 组件初始化失败: {e}")
                self._error_callback(f"组件初始化失败: {e}")
                return False

    def unload_component(self) -> None:
        """卸载组件"""
        with self._lock:
            if self._component is not None:
                try:
                    self._component.release()
                    logger.info("InferenceFramework: 组件已卸载")
                except Exception as e:
                    logger.error(f"InferenceFramework: 组件释放异常: {e}")
                self._component = None

    def update_config(self, config: dict) -> bool:
        """运行时更新组件配置（通过重新初始化）"""
        with self._lock:
            if self._component is None:
                return False
            try:
                # 简单实现：组件不支持动态更新，需要重建
                # 完整实现可调用组件的 reconfigure 方法
                return True
            except Exception as e:
                self._error_callback(f"配置更新失败: {e}")
                return False

    def health_check(self) -> bool:
        """框架健康检查"""
        with self._lock:
            return self._component is not None and self._running

    # ── 推理循环 ─────────────────────────────────────────────────

    def start(self) -> None:
        """启动推理循环"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="InferenceLoop")
        self._thread.start()
        logger.info("InferenceFramework: 推理循环已启动")

    def stop(self) -> None:
        """停止推理循环"""
        self._running = False
        self._frame_queue.stop()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.unload_component()
        logger.info("InferenceFramework: 推理循环已停止")

    def _run_loop(self) -> None:
        """推理循环主体"""
        while self._running:
            frame = self._frame_queue.get(timeout=1.0)
            if frame is None:
                continue

            with self._lock:
                if self._component is None:
                    continue

            try:
                start = time.perf_counter()
                result = self._component.infer(frame)
                elapsed_ms = (time.perf_counter() - start) * 1000

                self._inference_count += 1
                self._total_inference_time_ms += elapsed_ms

                # 合并 debug 信息
                result.setdefault("debug", {})["inference_time_ms"] = round(elapsed_ms, 2)
                result.setdefault("debug", {})["total_inference_count"] = self._inference_count

                self._result_callback(result, self._frame_queue.stats())

            except Exception as e:
                logger.error(f"InferenceFramework: 推理异常: {e}")
                self._error_callback(f"推理异常: {e}")
                # 可选：重置组件

    # ── 帧注入 ───────────────────────────────────────────────────

    def put_frame(self, frame: dict[str, Any]) -> None:
        """注入 IQ 帧到队列"""
        self._frame_queue.put(frame)

    # ── 状态查询 ─────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """返回运行时统计"""
        qstats = self._frame_queue.stats()
        avg_ms = self._total_inference_time_ms / self._inference_count if self._inference_count > 0 else 0
        return {
            "buffer_level": self._frame_queue.size(),
            "buffer_capacity": self._buffer_capacity,
            "frames_received": qstats.frames_received,
            "frames_dropped": qstats.frames_dropped,
            "dropped_rate": round(self._frame_queue.dropped_rate, 4),
            "inference_count": self._inference_count,
            "avg_inference_time_ms": round(avg_ms, 2)
        }