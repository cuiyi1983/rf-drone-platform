"""
Mock Inference Component - 模拟推理组件

实现 IInferenceComponent 接口，对 Platform 透明（与真实组件无区别）。
推理过程直接跳过，随机输出推理结果，支撑各接口测试。

使用方式（Platform 内置注册，无需 .zip）：
    from backend.components.sim_component import SimComponent
    component = SimComponent()
"""

import random
import time
from typing import Any

from backend.inference.framework import IInferenceComponent


# ── 内置机型库 ────────────────────────────────────────────────
_DJIMODELS = [
    "DJI_MAVIC3_PRO",
    "DJI_PHANTOM4_PRO",
    "DJI_MINI3_PRO",
    "DJI_AIR3",
    "DJI_AVATA2",
]

_FREQ_LIST = [5760_000_000, 5775_000_000, 5800_000_000, 5825_000_000,
              5850_000_000, 5875_000_000, 5900_000_000]


# ── Manifest ──────────────────────────────────────────────────
_MANIFEST = {
    "component": {
        "id": "sim-inference",
        "name": "模拟推理组件",
        "version": "1.0.0",
        "author": "platform-internal",
        "type": "inference",
    },
    "capability": {
        "device_support": ["cpu", "gpu", "npu"],
        "async_inference": False,
    },
    "collector_requirements": {
        "min_data_points": 600_000,
        "frequency": 5_805_000_000,
        "buffer_size": 524_288,
        "gain": 20,
        "scan": {
            "enabled": True,
            "frequencies": [5_805_000_000, 2_450_000_000],
            "hop_interval_ms": 100,
        },
    },
    "io": {
        "input": [
            {"name": "iq_frame", "type": "dict"},
        ],
        "output": [
            {"name": "detections", "type": "list"},
            {"name": "debug", "type": "dict"},
        ],
    },
    "config_schema": {
        "confidence_threshold": {
            "type": "number",
            "title": "置信度阈值",
            "default": 0.5,
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "detection_mode": {
            "type": "string",
            "title": "检测模式",
            "default": "random",
            "enum": ["always_drone", "always_noise", "random", "alternating"],
        },
        "drone_models": {
            "type": "array",
            "title": "机型列表",
            "items": {"type": "string"},
            "default": _DJIMODELS,
        },
        "noise_threshold_dbm": {
            "type": "number",
            "title": "噪声阈值(dBm)",
            "default": -60.0,
        },
        "inference_delay_ms": {
            "type": "number",
            "title": "推理时延模拟(ms)",
            "default": 10.0,
        },
        "drone_probability": {
            "type": "number",
            "title": "有无人机概率(%)",
            "default": 30.0,
            "minimum": 0.0,
            "maximum": 100.0,
        },
    },
}


# ── Component ─────────────────────────────────────────────────
class SimComponent(IInferenceComponent):
    """
    模拟推理组件。

    与真实组件的差异：
    - infer() 不做真实推理，直接随机返回结果
    - initialize() 不加载模型文件
    - 其余接口与真实组件完全一致
    """

    def __init__(self):
        self._config: dict = {}
        self._device: str = "cpu"
        self._infer_count: int = 0
        self._last_result_is_drone: bool = False

    # ── IInferenceComponent 实现 ───────────────────────────────

    def get_manifest(self) -> dict:
        """返回组件自描述信息。"""
        return _MANIFEST

    def initialize(self, config: dict, device: str) -> None:
        """初始化组件（无真实资源加载）。"""
        self._config = config
        self._device = device
        self._infer_count = 0
        self._last_result_is_drone = False

    def infer(self, iq_frame: dict) -> dict:
        """
        推理：跳过真实推理，随机输出结果。

        根据 detection_mode 决定输出：
        - always_drone :  100% 返回检测结果
        - always_noise  :  100% 返回空检测
        - random         :  根据 drone_probability 随机决定
        - alternating    :  奇偶帧交替
        """
        self._infer_count += 1
        mode = self._config.get("detection_mode", "random")
        drone_prob = self._config.get("drone_probability", 30.0) / 100.0
        threshold = self._config.get("confidence_threshold", 0.5)
        models = self._config.get("drone_models", _DJIMODELS)
        delay_ms = self._config.get("inference_delay_ms", 10.0)

        # 模拟推理时延
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        # 决定是否有检测
        if mode == "always_drone":
            has_drone = True
        elif mode == "always_noise":
            has_drone = False
        elif mode == "alternating":
            has_drone = (self._infer_count % 2 == 1)
        else:  # random
            has_drone = (random.random() < drone_prob)

        self._last_result_is_drone = has_drone

        # 构建结果
        detections = []
        debug_info = {
            "inference_time_ms": round(random.uniform(5.0, 20.0), 2),
            "stft_time_ms": round(random.uniform(1.0, 5.0), 2),
            "total_inference_count": self._infer_count,
            "device": self._device,
        }

        if has_drone:
            confidence = round(random.uniform(threshold + 0.01, 0.995), 3)
            detections.append({
                "model": random.choice(models),
                "confidence": confidence,
                "frequency": random.choice(_FREQ_LIST),
                "power_dbm": round(random.uniform(-50.0, -30.0), 1),
            })

        return {
            "frame_id": iq_frame.get("frame_id", 0),
            "timestamp": iq_frame.get("timestamp", time.time()),
            "center_freq": iq_frame.get("center_freq", 5_805_000_000),
            "sample_rate": iq_frame.get("sample_rate", 60_000_000),
            "detections": detections,
            "debug": debug_info,
        }

    def release(self) -> None:
        """释放资源（无真实资源）。"""
        self._config = {}
        self._infer_count = 0

    def health_check(self) -> bool:
        """健康检查，始终返回 True。"""
        return True

    # ── 扩展接口（供 Platform 查询当前配置）────────────────────

    def get_config(self) -> dict:
        """返回当前生效的配置。"""
        return dict(self._config)

    def get_current_config_schema(self) -> dict:
        """返回 config_schema（同 manifest 中的定义）。"""
        return _MANIFEST.get("config_schema", {})


# ── 注册到 Platform ───────────────────────────────────────────
# Platform 在 _register_sim_components() 中调用此函数完成注册
COMPONENT_ENTRY = {
    "id": "sim-inference",
    "name": "模拟推理组件",
    "version": "1.0.0",
    "component_class": SimComponent,
    "manifest": _MANIFEST,
}