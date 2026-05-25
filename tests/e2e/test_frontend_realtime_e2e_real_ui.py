"""
test_frontend_realtime_e2e_real_ui.py - 前端实时推理真实 UI E2E 验证

测试目标：验证前端在真实浏览器环境下，通过真实的 UI 点击操作
         （切换 Tab → 选择组件 → 选择设备 → 连接 → 启动采数）
         能正确触发 startSession() 完整流程，并通过 Socket.IO 接收后端推送。

与 test_frontend_realtime_feedback.py 的区别：
- test_frontend_realtime_feedback.py: 用 start_session_via_api() 直接调 API，
  用 page.evaluate() 手动注入 S 属性和手动 emit socket.subscribe，
  绕过了真实 UI 流程（没有点击任何按钮）
- 本测试：真实点击每个 UI 元素，模拟用户完整操作路径

真实 UI 流程：
  1. 点击 [data-pg="cfg"] 切换到配置页面
  2. 选择 #msel = sim-inference（加载推理组件）
  3. 点击 #scanBtn 扫描设备
  4. 选择 #deviceSel = pluto-repeater
  5. 点击 #connBtn 连接采集器（S.collector_connected = true）
  6. 点击 #btnS 启动采数（触发真实 startSession() 调用）
  7. 等待 4-5 秒让数据到达
  8. 检查 #buf-fps, #buf-frames 不为 "--"
  9. 检查 #rtbody 不包含"等待启动采数"

测试环境：服务器（5100=Platform，5102=Frontend）
运行方式（容器内）：
    cd /repo
    python -m pytest tests/e2e/test_frontend_realtime_e2e_real_ui.py -v -s
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


class RealUIBrowser:
    """通过真实 UI 点击操作验证完整流程"""

    def __init__(self, frontend_port: int):
        self.port = frontend_port
        self.browser = None
        self.page = None
        self.server_proc = None

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

    def open_page(self):
        """打开前端页面（停在初始状态，等待用户操作）"""
        self.page.goto(
            f"http://localhost:{self.port}/index.html",
            wait_until="networkidle",
            timeout=15000,
        )
        time.sleep(1)

    def switch_to_config_page(self):
        """切换到配置页面 Tab"""
        self.page.click('[data-pg="cfg"]')
        time.sleep(0.5)

    def select_inference_component(self, component_id: str = "sim-inference"):
        """选择推理组件（触发 loadComponentSchema，设置 S.component_loaded = true）"""
        self.page.select_option('#msel', component_id)
        # Wait for component schema to load
        time.sleep(1)

    def scan_devices(self):
        """扫描设备"""
        self.page.click('#scanBtn')
        # Wait for device list to populate
        time.sleep(2)

    def select_device(self, device_id: str = "pluto-repeater"):
        """选择采集设备（触发 repeater-panel 显示 + loadDeviceCapabilities）"""
        self.page.select_option('#deviceSel', device_id)
        # Wait for UI update (repeater-panel shows, iqFilePath auto-fills)
        time.sleep(1)

    def connect_collector(self):
        """连接采集器（设置 S.collector_connected = true）"""
        self.page.click('#connBtn')
        # Wait for connection to establish
        time.sleep(2)

    def start_session_via_ui(self):
        """
        通过 UI 点击启动采数（触发真实 startSession() 调用）。

        重要：#btnS 在观测页面（#pg-obs），不在配置页面（#pg-cfg）。
        必须在配置页面操作完成后，切换回观测页面再点击 #btnS。
        """
        # 先切换到观测页面（#btnS 在观测页面，不在配置页面）
        self.page.evaluate('() => document.querySelector("[data-pg=obs]").click()')
        time.sleep(0.5)
        # btnS should be enabled now: !S.collecting && S.component_loaded && S.collector_connected
        self.page.click('#btnS')
        # Wait for session to start and data to flow
        time.sleep(1)

    def wait_for_data(self, seconds: float = 5):
        """等待 Socket.IO 数据到达"""
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

    def read_session_state(self) -> dict:
        """读取前端 S 对象状态（用于调试）"""
        return self.page.evaluate("""() => {
            return {
                session_id: S.session_id,
                collecting: S.collecting,
                component_loaded: S.component_loaded,
                collector_connected: S.collector_connected,
                inf_count: S.inf_count,
            };
        }""")

    def stop_session_via_api(self):
        """通过 API 停止 session（清理环境）"""
        state = self.read_session_state()
        if state.get("session_id"):
            try:
                requests.post(
                    f"{PLATFORM_URL}/api/v1/session/stop",
                    json={"session_id": state["session_id"]},
                    timeout=5,
                )
            except Exception:
                pass

    def close(self):
        self.stop_session_via_api()
        if self.browser:
            self.browser.close()
            self.playwright.stop()
        if self.server_proc:
            self.server_proc.terminate()
            self.server_proc.wait()


class TestFrontendRealtimeE2ERealUI:
    """TC-E2E-REAL-01~02: 真实 UI 点击流程 E2E 验证"""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        # 清理残留
        try:
            requests.post(f"{PLATFORM_URL}/api/v1/collector/disconnect", timeout=3)
        except Exception:
            pass
        yield
        # teardown 在 close() 里做

    def test_tc_e2e_real_01_full_ui_flow(self):
        """
        TC-E2E-REAL-01: 完整 UI 流程 Buffer 监控验证

        操作：真实 UI 点击流程（Tab → 组件 → 设备 → 连接 → 启动）
        期望：#buf-fps 和 #buf-frames 应收到后端推送的数字，不应为 "--"

        这个测试能发现：
        - Socket.IO 连接时序问题（initSocket 异步导致 subscribeSession 跳过）
        - UI 状态管理问题（S.component_loaded / S.collector_connected 未正确设置）
        - startSession 完整流程中的任何 API 或 Socket.IO 问题
        """
        frontend_port = find_free_port()
        browser = RealUIBrowser(frontend_port)
        browser.start_server()
        try:
            browser.setup()
            browser.open_page()

            # 真实 UI 操作流程
            browser.switch_to_config_page()
            browser.select_inference_component("sim-inference")
            browser.scan_devices()
            browser.select_device("pluto-repeater")
            browser.connect_collector()
            browser.start_session_via_ui()

            # 等待数据到达
            browser.wait_for_data(5)

            # 读取 S 状态（调试用）
            state = browser.read_session_state()
            print(f"[TC-E2E-REAL-01] S 状态: {state}")

            # 读取 Buffer 监控
            stats = browser.read_buffer_stats()
            print(f"[TC-E2E-REAL-01] 缓冲区状态: {stats}")

            errors = []
            fps = stats["buf_fps"]
            frames = stats["buf_frames"]

            # 验证 S 状态
            if not state.get("session_id"):
                errors.append(f"session_id 未设置: {state}")
            if not state.get("collecting"):
                errors.append(f"S.collecting 未设置: {state}")
            if not state.get("component_loaded"):
                errors.append(f"S.component_loaded 未设置: {state}")
            if not state.get("collector_connected"):
                errors.append(f"S.collector_connected 未设置: {state}")

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

            assert not errors, f"TC-E2E-REAL-01 失败: {errors}"
            print(f"[PASS] TC-E2E-REAL-01: FPS={fps}, 帧数={frames}")

        finally:
            browser.close()

    def test_tc_e2e_real_02_full_ui_flow_realtime_panel(self):
        """
        TC-E2E-REAL-02: 完整 UI 流程实时推理面板验证

        操作：真实 UI 点击流程
        期望：#rtbody 不应包含"等待启动采数"，#cnt 应 > 0

        这个测试能发现：
        - inference_result 事件未到达（订阅失败或时序问题）
        - 实时推理框状态未更新（前端 JS 问题）
        """
        frontend_port = find_free_port()
        browser = RealUIBrowser(frontend_port)
        browser.start_server()
        try:
            browser.setup()
            browser.open_page()

            # 真实 UI 操作流程
            browser.switch_to_config_page()
            browser.select_inference_component("sim-inference")
            browser.scan_devices()
            browser.select_device("pluto-repeater")
            browser.connect_collector()
            browser.start_session_via_ui()

            # 等待数据到达
            browser.wait_for_data(5)

            # 读取 S 状态
            state = browser.read_session_state()
            print(f"[TC-E2E-REAL-02] S 状态: {state}")

            # 读取实时推理面板
            panel = browser.read_realtime_panel()
            print(f"[TC-E2E-REAL-02] 实时推理面板: cnt={panel['cnt']}, rtbody={panel['rtbody'][:100]}")

            errors = []

            # 验证 S 状态
            if not state.get("session_id"):
                errors.append(f"session_id 未设置: {state}")
            if not state.get("collecting"):
                errors.append(f"S.collecting 未设置: {state}")

            # 实时推理框不应显示"等待启动采数"
            if "等待启动采数" in panel["rtbody"]:
                errors.append("实时推理框仍显示'等待启动采数'，inference_result 未到达")

            # 推理计数应 > 0
            cnt = panel["cnt"]
            if cnt is None or cnt == "" or cnt == "0":
                errors.append(f"推理计数 cnt 为空或 0: {cnt!r}")

            assert not errors, f"TC-E2E-REAL-02 失败: {errors}"
            print(f"[PASS] TC-E2E-REAL-02: cnt={cnt}")

        finally:
            browser.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
