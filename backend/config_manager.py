"""
Config Manager - 配置合并逻辑
合并 Capability Range ∩ Component 建议 → 最终配置
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    配置管理器：合并 collector 能力范围和 component 建议值
    """

    # 标准能力范围默认值
    DEFAULT_CAPABILITIES = {
        "frequency": {
            "type": "int",
            "range": [325_000_000, 6_000_000_000],
            "default": 5_805_000_000
        },
        "buffer_size": {
            "type": "int",
            "range": [1024, 1_048_576],
            "default": 524_288
        },
        "gain": {
            "type": "float",
            "range": [0.0, 60.0],
            "default": 20.0
        },
        "sample_rate": {
            "type": "int",
            "fixed": 60_000_000
        },
        "rf_bandwidth": {
            "type": "int",
            "fixed": 56_000_000
        }
    }

    def __init__(self):
        self._collector_capabilities: dict[str, Any] = {}
        self._collector_capabilities_loaded: bool = False

    def set_collector_capabilities(self, capabilities: dict[str, Any]) -> None:
        """
        设置采集器能力范围（从 collector /collector/discover 获取后缓存）
        """
        self._collector_capabilities = capabilities
        self._collector_capabilities_loaded = True
        logger.info("ConfigManager: Collector capabilities 已缓存")

    def get_collector_capabilities(self) -> dict[str, Any]:
        """返回缓存的能力范围"""
        return self._collector_capabilities.copy()

    def merge(
        self,
        component_requirements: dict[str, Any],
        collector_capabilities: Optional[dict[str, Any]] = None
    ) -> tuple[dict[str, Any], list[str]]:
        """
        合并配置

        Args:
            component_requirements: 组件推荐配置
                {
                    "frequency": 5805000000,
                    "buffer_size": 524288,
                    "gain": 20,
                    "scan": {"enabled": True, "frequencies": [...]}
                }
            collector_capabilities: 能力范围，不传则用缓存

        Returns:
            (merged_config, warnings)
        """
        caps = collector_capabilities or self._collector_capabilities
        if not caps:
            # 没有能力范围时用默认值，给 WARNING
            logger.warning("ConfigManager: 无 collector capabilities，使用默认范围")
            caps = self.DEFAULT_CAPABILITIES

        warnings: list[str] = []
        merged: dict[str, Any] = {}

        # sample_rate 和 rf_bandwidth 是 fixed，不参与合并
        for fixed_key in ["sample_rate", "rf_bandwidth"]:
            if fixed_key in caps:
                merged[fixed_key] = caps[fixed_key]["fixed"]

        # 可配置参数：frequency, buffer_size, gain
        for key in ["frequency", "buffer_size", "gain"]:
            merged[key] = self._merge_param(key, component_requirements, caps, warnings)

        # 透传 scan 配置（Component 自己决定）
        if "scan" in component_requirements:
            merged["scan"] = component_requirements["scan"]

        # 透传 min_data_points（参考用）
        if "min_data_points" in component_requirements:
            merged["min_data_points"] = component_requirements["min_data_points"]

        return merged, warnings

    def _merge_param(
        self,
        key: str,
        requirements: dict[str, Any],
        capabilities: dict[str, Any],
        warnings: list[str]
    ) -> Any:
        """
        单参数合并：
        - 取 component 建议值，不超出 capability 范围
        - 越界 → 降级到边界 + WARNING
        - 缺失 → 用 capability default 补齐
        """
        cap = capabilities.get(key, {})
        cap_range = cap.get("range")
        cap_default = cap.get("default")

        suggested = requirements.get(key)

        if suggested is None:
            # 缺失，用 default
            if cap_default is not None:
                warnings.append(f"参数 {key} 缺失，使用默认值 {cap_default}")
                return cap_default
            else:
                warnings.append(f"参数 {key} 缺失，且无默认值")
                return None

        # 越界检查
        if cap_range is not None:
            lo, hi = cap_range
            val = suggested
            if val < lo:
                warnings.append(f"参数 {key}={val} 低于范围 {cap_range}，降级到 {lo}")
                return lo
            if val > hi:
                warnings.append(f"参数 {key}={val} 高于范围 {cap_range}，降级到 {hi}")
                return hi

        return suggested

    def validate_final_config(self, config: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        验证最终配置是否有效（所有必需参数存在且在范围内）
        """
        required = ["frequency", "buffer_size", "gain"]
        for key in required:
            if key not in config:
                return False, f"缺少必需参数: {key}"

        caps = self._collector_capabilities or self.DEFAULT_CAPABILITIES
        for key in required:
            cap = caps.get(key, {})
            cap_range = cap.get("range")
            if cap_range is not None:
                lo, hi = cap_range
                val = config[key]
                if not (lo <= val <= hi):
                    return False, f"参数 {key}={val} 超出范围 {cap_range}"
        return True, None