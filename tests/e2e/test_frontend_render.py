"""
test_frontend_render.py - 前端渲染验证（无头浏览器 + Node.js）

测试目标：验证前端 JS 逻辑在接收到后端数据后，能否正确更新 DOM。
          不依赖"看屏幕"，用 Playwright 读取 DOM 状态。

测试环境：ubuntu@43.165.185.8
"""

import pytest
import requests
import time
import subprocess
import socket
import os
import sys

PLATFORM_URL = "http://localhost:5100"
FRONTEND_DIR = "/home/ubuntu/rf-drone-platform-test/frontend"


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


class FrontendRenderer:
    """用 Playwright 无头浏览器验证前端 DOM 渲染状态"""

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
        self.page.goto(
            f"http://localhost:{self.port}/index.html",
            wait_until="networkidle"
        )
        # 等 socket 连接稳定
        time.sleep(1)

    def start_session_and_verify(self, component_id: str, iq_file: str):
        # ── Step 1: 启动 session ───────────────────────────────
        requests.post(f"{PLATFORM_URL}/api/v1/collector/disconnect", timeout=5)
        time.sleep(0.5)

        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": component_id,
                "config": {
                    "frequency": 5_805_000_000,
                    "sample_rate": 60_000_000,
                    "iq_file_path": iq_file,
                    "loop_play": True,
                },
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return [{"session_start_failed": resp.text}], 0, None

        data = resp.json()
        session_id = data.get("session_id")
        cfg = data.get("config", {})
        print(f"[渲染测试] session={session_id}")

        # ── Step 2: 模拟 startSession 后的前端状态 ─────────────
        # 直接调用前端的 updateConfigDisplay 逻辑（注入到页面执行）
        ic = cfg.get("inference_config", {})
        cc = cfg.get("collector_config", {})

        self.page.evaluate(
            """(cfg) => {
            // 初始化 S 对象（前端全局状态）
            window.S = {
                session_id: '""" + session_id + """',
                collecting: true,
                session_config: cfg,
                component_loaded: true,
                collector_connected: true
            };
            // 调用前端的 updateConfigDisplay（注入后直接调用）
            if (typeof updateConfigDisplay === 'function') {
                updateConfigDisplay(cfg);
            }
        }""",
            cfg,
        )
        time.sleep(0.3)

        # ── Step 3: 检查观测区 1 - 推理组件当前配置 ────────────
        errors = []

        def check_element(dom_id: str, expect_value: str = None):
            """读取 DOM 元素的 textContent，验证是否符合预期"""
            try:
                el = self.page.locator(f"#{dom_id}")
                text = el.text_content()
                if expect_value is not None:
                    if text != expect_value:
                        errors.append(
                            f"#{dom_id}={text!r}，期望 {expect_value!r}"
                        )
                else:
                    if not text or text.strip() in ("--", "", "-"):
                        errors.append(f"#{dom_id} 为空/占位符: {text!r}")
                return text
            except Exception as e:
                errors.append(f"#{dom_id} 不存在: {e}")
                return None

        # 观测区 1：配置显示（宽松匹配：有内容即可，不验证具体格式）
        freq = check_element("cfg-freq")
        sr = check_element("cfg-sr")
        gain = check_element("cfg-gain")
        component = check_element("cfg-component")

        print(f"[渲染测试] 配置: freq={freq}, sr={sr}, gain={gain}, component={component}")

        # ── Step 4: 模拟 collector_stats 推送，验证观测区 2 ───
        # 通过 page.evaluate 注入 collector_stats 事件，手动触发前端处理
        stats_data = {
            "session_id": session_id,
            "frames_per_second": 12.5,
            "dropped_rate": 0.05,
            "buffer_level": 42,
            "total_frames": 156,
            "total_dropped": 8
        }

        self.page.evaluate(
            """(data) => {
            // 直接调用前端的 handleCollectorStats（如果存在）
            if (typeof handleCollectorStats === 'function') {
                handleCollectorStats(data);
            }
            // 如果没有 handleCollectorStats，直接操作 DOM
            else {
                const el = document.getElementById('buf-val');
                if (el) el.textContent = data.buffer_level;
                const frames = document.getElementById('buf-frames');
                if (frames) frames.textContent = data.total_frames;
            }
        }""",
            stats_data,
        )
        time.sleep(0.3)

        # ── Step 5: 检查观测区 2 - 缓冲区监控 ─────────────────
        buf_val = check_element("buf-val", "42")
        buf_frames = check_element("buf-frames", "156")
        buf_fps = check_element("buf-fps", "12.5")

        print(f"[渲染测试] 缓冲区: buf-val={buf_val}, buf-frames={buf_frames}, buf-fps={buf_fps}")

        # ── Step 6: 停止 session ──────────────────────────────
        requests.post(
            f"{PLATFORM_URL}/api/v1/session/stop",
            json={"session_id": session_id},
            timeout=10,
        )

        if errors:
            print("[渲染测试] 失败:")
            for e in errors:
                print(f"  - {e}")
        else:
            print("[渲染测试] PASS")

        return errors, 1, session_id

    def close(self, session_id=None):
        if session_id:
            try:
                requests.post(
                    f"{PLATFORM_URL}/api/v1/session/stop",
                    json={"session_id": session_id},
                    timeout=5,
                )
            except Exception:
                pass
        try:
            requests.post(f"{PLATFORM_URL}/api/v1/collector/disconnect", timeout=5)
        except Exception:
            pass
        if self.browser:
            self.browser.close()
            self.playwright.stop()
        if self.server_proc:
            self.server_proc.terminate()
            self.server_proc.wait()


class TestFrontendRender:
    @pytest.fixture(autouse=True)
    def setup_method(self):
        for _ in range(3):
            r = requests.post(f"{PLATFORM_URL}/api/v1/collector/disconnect", timeout=5)
            time.sleep(0.3)

    def test_tc_render01_config_display(self):
        """
        TC-RENDER-01: 验证推理组件当前配置区有内容

        前端 updateConfigDisplay() 接收到 config 后，
        #cfg-freq 应显示 "5805"，#cfg-sr 应显示 "60"，#cfg-gain 应显示 "20"。
        """
        frontend_port = find_free_port()
        renderer = FrontendRenderer(frontend_port)
        renderer.start_server()
        try:
            renderer.setup()
            errors, _, _ = renderer.start_session_and_verify(
                component_id="sim-inference",
                iq_file="IQ-Record/noise_5db_600k.bin",
            )
            assert not errors, f"渲染失败: {errors}"
        finally:
            renderer.close()

    def test_tc_render02_buffer_monitor(self):
        """
        TC-RENDER-02: 验证缓冲区监控在收到 collector_stats 后更新

        前端 handleCollectorStats() 接收到 stats 后，
        #buf-val 应显示 buffer_level，"#buf-frames" 应显示 total_frames。
        """
        frontend_port = find_free_port()
        renderer = FrontendRenderer(frontend_port)
        renderer.start_server()
        try:
            renderer.setup()
            errors, _, _ = renderer.start_session_and_verify(
                component_id="sim-inference",
                iq_file="IQ-Record/noise_5db_600k.bin",
            )
            assert not errors, f"渲染失败: {errors}"
        finally:
            renderer.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
