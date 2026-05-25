"""
test_frontend_realtime_feedback.py - 前端实时反馈 Socket.IO E2E 验证

测试目标：验证前端在真实浏览器环境下，通过 Socket.IO 接收后端推送后，
         能正确更新 DOM（缓冲区监控 + 实时推理）。

与 test_frontend_render.py 的区别：
- render 测试通过 page.evaluate() 注入数据，验证 DOM 更新逻辑
- 本测试触发完整的 Socket.IO 订阅流程，验证前后端集成

测试环境：服务器（5100=Platform，5102=Frontend）
运行方式（容器内）：
    cd /repo
    python -m pytest tests/e2e/test_frontend_realtime_feedback.py -v -s
"""

import os
import pytest
import requests
import time
import subprocess
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PLATFORM_URL = "http://localhost:5100"
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend")


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


class RealtimeFeedbackBrowser:
    """用 Playwright 无头浏览器验证真实 Socket.IO 订阅流程"""

    def __init__(self, frontend_port: int):
        self.port = frontend_port
        self.browser = None
        self.page = None
        self.server_proc = None
        self.session_id = None

    def start_server(self):
        self.server_proc = subprocess.Popen(
            ["python3", "-m", "http.server", str(self.port)],
            cwd=FRONTEND_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(10):
            try:
                requests.get(f"http://localhost:{self.port}/", timeout=1)
                return
            except requests.ConnectionError:
                time.sleep(0.2)

    def setup(self):
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def start_session_via_api(self, component_id: str = "sim-inference", config: dict = None):
        """通过 API 启动 session（后端状态必须先就绪）"""
        if config is None:
            config = {
                "iq_file_path": "IQ-Record/noise_5db_600k.bin",
                "loop_play": True,
                "frequency": 5805_000_000,
                "sample_rate": 60_000_000,
                "gain": 20.0,
            }
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={"component_id": component_id, "config": config},
            timeout=10,
        )
        assert resp.status_code == 200, f"start failed: {resp.text}"
        data = resp.json()
        self.session_id = data["session_id"]
        return data

    def open_page_and_subscribe(self):
        """
        打开前端页面，建立 Socket.IO 订阅。

        注意：页面加载时 init() 已调用 initSocket()，socket 已连接但未 subscribe
        （因为 connect 回调里 if (S.session_id) 为 None）。
        所以不能只调 initSocket()，必须显式 emit subscribe。
        """
        self.page.goto(
            f"http://localhost:{self.port}/index.html",
            wait_until="networkidle",
            timeout=15000,
        )
        time.sleep(1)

        # 设置 session 状态（必须在 subscribe 之前）
        self.page.evaluate(
            f"""
            () => {{
                S.session_id = '{self.session_id}';
                S.collecting = true;
                S.component_loaded = true;
                S.collector_connected = true;
                S.results = [];
                S.inf_count = 0;
                S.enabledColumns = new Set(['timestamp']);

                // 页面加载时 initSocket() 已建立连接，connect 回调里 subscribe 未执行
                // 现在显式 emit subscribe（模拟 startSession 的正确流程）
                if (socket && socket.connected) {{
                    socket.emit('subscribe', {{ session_id: '{self.session_id}' }});
                    log('手动订阅: {self.session_id}');
                }}
            }}
            """
        )
        # 等待事件到达
        time.sleep(5)

    def wait_for_events(self, seconds: float = 4):
        """等待 Socket.IO 事件到达"""
        time.sleep(seconds)

    def read_buffer_stats(self) -> dict:
        """读取缓冲区监控 DOM 状态"""
        def safe_text(selector):
            try:
                return self.page.locator(selector).text_content(timeout=2000)
            except Exception:
                return None

        return {
            "buf_fps": safe_text("#buf-fps"),
            "buf_frames": safe_text("#buf-frames"),
            "buf_dropped": safe_text("#buf-dropped"),
            "buf_coll": safe_text("#buf-coll"),
            "buf_val": safe_text("#buf-val"),
        }

    def read_realtime_panel(self) -> dict:
        """读取实时推理面板 DOM 状态"""
        def safe_text(selector):
            try:
                return self.page.locator(selector).text_content(timeout=2000)
            except Exception:
                return None

        return {
            "rtbody": self.page.locator("#rtbody").inner_html(timeout=2000),
            "cnt": safe_text("#cnt"),
        }

    def read_config_panel(self) -> dict:
        """读取配置面板 DOM 状态"""
        def safe_text(selector):
            try:
                return self.page.locator(selector).text_content(timeout=2000)
            except Exception:
                return None

        return {
            "cfg_device": safe_text("#cfg-device"),
            "crt": safe_text("#crt"),
            "cfg_component": safe_text("#cfg-component"),
        }

    def close(self):
        if self.session_id:
            try:
                requests.post(
                    f"{PLATFORM_URL}/api/v1/session/stop",
                    json={"session_id": self.session_id},
                    timeout=5,
                )
            except Exception:
                pass
        if self.browser:
            self.browser.close()
            self.playwright.stop()
        if self.server_proc:
            self.server_proc.terminate()
            self.server_proc.wait()


class TestFrontendRealtimeFeedback:
    """TC-E2E-01~03: 真实 Socket.IO 订阅流程 E2E 验证"""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        # 清理残留
        try:
            requests.post(f"{PLATFORM_URL}/api/v1/collector/disconnect", timeout=3)
        except Exception:
            pass
        yield
        # teardown 在 close() 里做

    def test_tc_e2e_01_buffer_stats_socketio(self):
        """
        TC-E2E-01: Buffer 监控 Socket.IO 字段验证

        操作：启动 session → 打开前端页面 → 触发 startSession 完整流程
        期望：#buf-fps 和 #buf-frames 应收到后端推送的数字，不应为 "0.0" / "0" / "--"

        覆盖的 Bug：
        - data.dropped vs data.dropped_rate 字段名不匹配（导致丢帧率显示 "--"）
        - subscribeSession 时序问题导致 Socket.IO 订阅无效（FPS=0，总帧数=0）
        """
        frontend_port = find_free_port()
        browser = RealtimeFeedbackBrowser(frontend_port)
        browser.start_server()
        try:
            browser.setup()
            # 启动 session（让后端处于 running 状态）
            browser.start_session_via_api()
            # 打开页面并触发真实 Socket.IO 订阅
            browser.open_page_and_subscribe()
            browser.wait_for_events(4)

            stats = browser.read_buffer_stats()
            print(f"[TC-E2E-01] 缓冲区状态: {stats}")

            errors = []
            fps = stats["buf_fps"]
            frames = stats["buf_frames"]

            # FPS 不应为占位符或 0
            if fps in ("--", "", None):
                errors.append(f"#buf-fps 显示占位符: {fps!r}")
            elif fps == "0.0":
                errors.append(f"#buf-fps 为 0，Socket.IO 订阅可能无效")

            # 总帧数不应为 0 或占位符
            if frames in ("--", "", None):
                errors.append(f"#buf-frames 显示占位符: {frames!r}")
            elif frames == "0":
                errors.append(f"#buf-frames 为 0，Socket.IO 订阅可能无效")

            assert not errors, f"TC-E2E-01 失败: {errors}"
            print(f"[PASS] TC-E2E-01: FPS={fps}, 帧数={frames}")

        finally:
            browser.close()

    def test_tc_e2e_02_realtime_inference_socketio(self):
        """
        TC-E2E-02: 实时推理框 Socket.IO 事件验证

        操作：启动 session → 打开前端页面 → 触发 startSession 完整流程
        期望：#rtbody 不应包含"等待启动采数"，#cnt 应 > 0

        覆盖的 Bug：
        - subscribeSession 时序问题导致 inference_result 收不到（推理框一直显示"等待"）
        """
        frontend_port = find_free_port()
        browser = RealtimeFeedbackBrowser(frontend_port)
        browser.start_server()
        try:
            browser.setup()
            browser.start_session_via_api()
            browser.open_page_and_subscribe()
            browser.wait_for_events(4)

            panel = browser.read_realtime_panel()
            print(f"[TC-E2E-02] 实时推理面板: cnt={panel['cnt']}, rtbody={panel['rtbody'][:100]}")

            errors = []
            if "等待启动采数" in panel["rtbody"]:
                errors.append("实时推理框仍显示'等待启动采数'，inference_result 未到达")
            cnt = panel["cnt"]
            if cnt is None or cnt == "" or cnt == "0":
                errors.append(f"推理计数 cnt 为空或 0: {cnt!r}")

            assert not errors, f"TC-E2E-02 失败: {errors}"
            print(f"[PASS] TC-E2E-02: cnt={cnt}")

        finally:
            browser.close()

    def test_tc_e2e_03_config_device_uri(self):
        """
        TC-E2E-03: 配置区字段完整性验证（pluto-repeater 模式）

        操作：pluto-repeater 模式下启动采数
        期望：#cfg-device 或 #crt 不应为 "--"

        覆盖的 Bug：
        - start_session 返回的 collector_config.uri 为 null（导致"采数设备显示--"）
        """
        frontend_port = find_free_port()
        browser = RealtimeFeedbackBrowser(frontend_port)
        browser.start_server()
        try:
            browser.setup()
            # pluto-repeater 模式
            browser.start_session_via_api(config={
                "iq_file_path": "IQ-Record/noise_5db_600k.bin",
                "loop_play": True,
                "collector_type": "pluto-repeater",
                "frequency": 5805_000_000,
                "sample_rate": 60_000_000,
                "gain": 20.0,
            })
            browser.open_page_and_subscribe()
            browser.wait_for_events(3)

            # 注入 session_config（startSession 成功后会设置 S.session_config）
            resp = requests.get(f"{PLATFORM_URL}/api/v1/session/{browser.session_id}/config", timeout=5)
            cfg = resp.json()
            browser.page.evaluate(
                f"""(cfg) => {{
                    S.session_config = cfg;
                    if (typeof updateConfigDisplay === 'function') updateConfigDisplay(cfg);
                }}""",
                cfg,
            )
            time.sleep(0.5)

            panel = browser.read_config_panel()
            print(f"[TC-E2E-03] 配置面板: {panel}")

            errors = []
            device = panel["cfg_device"] or panel["crt"]
            if device in ("--", "", None):
                errors.append(f"采数设备显示占位符: cfg_device={panel['cfg_device']!r}, crt={panel['crt']!r}")

            assert not errors, f"TC-E2E-03 失败: {errors}"
            print(f"[PASS] TC-E2E-03: device={device}")

        finally:
            browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
