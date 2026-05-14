"""
单元测试：ConfigManager 配置合并逻辑
"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from backend.config_manager import ConfigManager


class TestConfigManager:
    """ConfigManager 合并规则测试"""

    @pytest.fixture
    def cm(self):
        return ConfigManager()

    @pytest.fixture
    def standard_caps(self):
        return {
            "frequency": {"type": "int", "range": [325_000_000, 6_000_000_000], "default": 5_805_000_000},
            "buffer_size": {"type": "int", "range": [1024, 1_048_576], "default": 524_288},
            "gain": {"type": "float", "range": [0.0, 60.0], "default": 20.0},
            "sample_rate": {"type": "int", "fixed": 60_000_000},
            "rf_bandwidth": {"type": "int", "fixed": 56_000_000}
        }

    # ── 基本合并测试 ─────────────────────────────────────────────

    def test_merge_normal(self, cm, standard_caps):
        """正常情况：建议值在范围内，直接采纳"""
        reqs = {
            "frequency": 5_805_000_000,
            "buffer_size": 524_288,
            "gain": 20.0
        }
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["frequency"] == 5_805_000_000
        assert merged["buffer_size"] == 524_288
        assert merged["gain"] == 20.0
        assert merged["sample_rate"] == 60_000_000  # fixed
        assert merged["rf_bandwidth"] == 56_000_000  # fixed
        assert warnings == []

    def test_merge_fixed_params(self, cm, standard_caps):
        """fixed 参数不参与合并，直接使用 fixed 值"""
        reqs = {
            "frequency": 5_805_000_000,
            "sample_rate": 40_000_000  # 错误建议
        }
        merged, warnings = cm.merge(reqs, standard_caps)
        assert merged["sample_rate"] == 60_000_000  # 忽略 reqs，使用 fixed
        assert merged["rf_bandwidth"] == 56_000_000

    def test_merge_frequency_too_low(self, cm, standard_caps):
        """frequency 低于范围下限，降级到下限"""
        reqs = {"frequency": 100_000_000}  # < 325_000_000
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["frequency"] == 325_000_000
        assert any("frequency" in w and "降级" in w for w in warnings)

    def test_merge_frequency_too_high(self, cm, standard_caps):
        """frequency 高于范围上限，降级到上限"""
        reqs = {"frequency": 10_000_000_000}  # > 6_000_000_000
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["frequency"] == 6_000_000_000
        assert any("frequency" in w and "降级" in w for w in warnings)

    def test_merge_buffer_size_too_high(self, cm, standard_caps):
        """buffer_size 超出上限，降级"""
        reqs = {"buffer_size": 2_000_000}  # > 1_048_576
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["buffer_size"] == 1_048_576
        assert any("buffer_size" in w and "降级" in w for w in warnings)

    def test_merge_gain_too_high(self, cm, standard_caps):
        """gain 高于上限，降级"""
        reqs = {"gain": 80.0}  # > 60.0
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["gain"] == 60.0
        assert any("gain" in w and "降级" in w for w in warnings)

    def test_merge_missing_param_uses_default(self, cm, standard_caps):
        """缺失参数，使用 capability default"""
        reqs = {"frequency": 5_805_000_000}  # 缺少 buffer_size 和 gain
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["buffer_size"] == 524_288  # default
        assert merged["gain"] == 20.0  # default
        assert any("buffer_size" in w for w in warnings)
        assert any("gain" in w for w in warnings)

    def test_merge_missing_all_params(self, cm, standard_caps):
        """所有参数都缺失，全部用 default"""
        reqs = {}
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["frequency"] == 5_805_000_000
        assert merged["buffer_size"] == 524_288
        assert merged["gain"] == 20.0
        assert len(warnings) >= 3  # 3 个参数都有 WARNING

    def test_merge_scan_config_pass_through(self, cm, standard_caps):
        """scan 配置透传"""
        reqs = {
            "frequency": 5_805_000_000,
            "scan": {"enabled": True, "frequencies": [5_805_000_000, 2_450_000_000]}
        }
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["scan"]["enabled"] is True
        assert merged["scan"]["frequencies"] == [5_805_000_000, 2_450_000_000]

    def test_merge_min_data_points_pass_through(self, cm, standard_caps):
        """min_data_points 透传"""
        reqs = {"frequency": 5_805_000_000, "min_data_points": 600_000}
        merged, warnings = cm.merge(reqs, standard_caps)

        assert merged["min_data_points"] == 600_000

    def test_merge_no_caps_uses_default(self, cm):
        """无能力范围时，使用内部默认值"""
        reqs = {"frequency": 5_805_000_000}
        merged, warnings = cm.merge(reqs, None)

        # 应使用 DEFAULT_CAPABILITIES
        assert merged["frequency"] == 5_805_000_000
        assert "buffer_size" in merged
        assert "gain" in merged

    # ── 验证测试 ─────────────────────────────────────────────────

    def test_validate_final_config_valid(self, cm, standard_caps):
        """验证有效配置"""
        cm.set_collector_capabilities(standard_caps)
        config = {"frequency": 5_805_000_000, "buffer_size": 524_288, "gain": 20.0}
        valid, err = cm.validate_final_config(config)
        assert valid is True
        assert err is None

    def test_validate_final_config_missing_field(self, cm, standard_caps):
        """验证缺少字段"""
        cm.set_collector_capabilities(standard_caps)
        config = {"frequency": 5_805_000_000}  # 缺 buffer_size 和 gain
        valid, err = cm.validate_final_config(config)
        assert valid is False
        assert "buffer_size" in err or "gain" in err

    def test_validate_final_config_out_of_range(self, cm, standard_caps):
        """验证超出范围"""
        cm.set_collector_capabilities(standard_caps)
        config = {"frequency": 5_805_000_000, "buffer_size": 99_999_999, "gain": 20.0}
        valid, err = cm.validate_final_config(config)
        assert valid is False
        assert "buffer_size" in err


if __name__ == "__main__":
    pytest.main([__file__, "-v"])