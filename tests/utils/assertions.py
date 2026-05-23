"""
tests/utils/assertions.py - 跨测试复用的 schema assertion

崔老板要求：
- "后台所有打印是否有异常"
- "模拟前台观测的各个状态信息是否符合预期"

前端 updateConfigDisplay 读取的字段，必须在后端返回中存在。
这些 assertion 封装了前后端接口契约，便于各测试用例复用。
"""

import json
import time
import socketio
import asyncio


# ── start_session 断言 ───────────────────────────────────────────

def assert_start_session_response(data: dict, component_id: str = None) -> dict:
    """
    验证 start_session 返回值 schema 完整性。

    前端 updateConfigDisplay() 依赖：
      data.config.inference_config.component_id
      data.config.collector_config.center_freq_hz
      data.config.collector_config.sample_rate_hz
      data.config.collector_config.gain_db
      data.config.collector_config.bandwidth_hz
      data.config.collector_config.uri

    返回 parsed config 供后续使用。
    Raises: AssertionError
    """
    # 基础字段
    assert "session_id" in data, f"缺少 session_id: {data}"
    assert data["session_id"].startswith("sess_"), f"session_id 格式错误: {data['session_id']}"
    assert data.get("status") in ("running", "started"), \
        f"status 应为 running/started，实际: {data.get('status')}"

    # config 整体（updateConfigDisplay 第一行就检查 cfg）
    assert "config" in data, (
        f"start_session 返回缺 config 字段！"
        f"前端 updateConfigDisplay() 无法显示配置信息。"
        f"返回值: {json.dumps(data, indent=2)}"
    )
    assert data["config"] is not None, "config 不应为 None"

    cfg = data["config"]

    # inference_config
    ic = cfg.get("inference_config", {})
    assert "component_id" in ic, (
        f"config.inference_config.component_id 缺失。"
        f"前端 #cfg-component 无法显示。当前 inference_config: {ic}"
    )
    if component_id:
        assert ic["component_id"] == component_id, \
            f"component_id 应为 {component_id}，实际: {ic['component_id']}"

    # collector_config（updateConfigDisplay 读取这些字段显示配置面板）
    cc = cfg.get("collector_config", {})
    for field, frontend_dom in [
        ("center_freq_hz", "#cfg-freq"),
        ("sample_rate_hz", "#cfg-sr"),
        ("gain_db", "#cfg-gain"),
        ("bandwidth_hz", "#cfg-bw"),
        ("uri", "#cfg-device"),
    ]:
        assert field in cc, (
            f"config.collector_config.{field} 缺失！"
            f"前端 {frontend_dom} 无法显示。"
            f"当前 collector_config: {json.dumps(cc, indent=2)}"
        )

    return cfg


def assert_collector_stats_event(event: dict) -> None:
    """
    验证 collector_stats Socket.IO 事件字段完整性。

    前端 handleCollectorStats() 读取：
      data.buffer_level  → #buf-fill
      data.total_frames → #buf-frames
      data.frames_per_second → #buf-fps
      data.dropped_rate → #buf-drop
    """
    required = [
        ("session_id", str),
        ("buffer_level", (int, float)),
        ("total_frames", (int, float)),
        ("frames_per_second", (int, float)),
        ("dropped_rate", (int, float)),
    ]
    for field, expected_type in required:
        assert field in event, (
            f"collector_stats 事件缺少字段 {field}。"
            f"前端 handleCollectorStats() 无法更新监控面板。"
            f"收到的事件: {json.dumps(event, indent=2)}"
        )
        assert isinstance(event[field], expected_type), (
            f"collector_stats.{field} 类型错误：期望 {expected_type}，"
            f"实际 {type(event[field])}，值: {event[field]}"
        )


def assert_inference_result_event(event: dict) -> None:
    """验证 inference_result Socket.IO 事件字段"""
    assert "detections" in event, f"inference_result 缺 detections: {event}"
    assert isinstance(event["detections"], list), \
        f"detections 应为 list，实际: {type(event['detections'])}"


def assert_session_stats_response(stats: dict) -> None:
    """
    验证 GET /api/v1/session/{id}/stats 返回的字段。

    与 collector_stats Socket.IO 事件字段保持一致。
    """
    required = ["buffer_level", "total_frames", "frames_received"]
    for field in required:
        assert field in stats, (
            f"stats 端点缺少字段 {field}。"
            f"当前 stats: {json.dumps(stats, indent=2)}"
        )


# ── Socket.IO 测试辅助 ─────────────────────────────────────────

class StatsCollector:
    """
    Socket.IO collector_stats 收集器（AsyncClient 版本）。

    用法：
        collector = StatsCollector(session_id)
        asyncio.get_event_loop().run_until_complete(collector.connect_and_wait(base_url, duration=3))
        assert collector.count >= 2, f"collector_stats 推送次数不足: {collector.count}"
        assert_collector_stats_event(collector.events[0])
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.sio = socketio.AsyncClient(reconnection=False)
        self.events = []
        self.connected = False
        self.error = None

        @self.sio.on("connect")
        async def on_connect():
            self.connected = True
            await self.sio.emit("subscribe", {"session_id": self.session_id})

        @self.sio.on("collector_stats")
        def on_collector_stats(data):
            self.events.append(data)

        @self.sio.on("inference_result")
        def on_inference_result(data):
            self.events.append({"type": "inference_result", **data})

        @self.sio.on("disconnect")
        def on_disconnect():
            pass

    async def connect_and_wait(self, base_url: str, duration: float = 3.0):
        """连接并等待指定秒数，收集事件"""
        try:
            await self.sio.connect(
                f"{base_url}/socket.io/",
                namespaced=False,
                wait=True,
                wait_timeout=5,
            )
            await asyncio.sleep(duration)
        except Exception as e:
            self.error = str(e)
        finally:
            try:
                self.sio.disconnect()
            except Exception:
                pass

    @property
    def stats_events(self):
        return [e for e in self.events if "buffer_level" in e]

    @property
    def inference_events(self):
        return [e for e in self.events if e.get("type") == "inference_result"]
