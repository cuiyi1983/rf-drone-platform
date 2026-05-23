"""
test_socketio_stats.py - Socket.IO collector_stats 事件诊断

目标：在真实环境中启动 session，使用 Socket.IO 客户端监听 collector_stats 事件，
验证数据是否真正推送到来排查观测页面空白的根因。

运行方式：
    cd /root/.openclaw/workspace/rf-drone-platform
    python3 -m pytest tests/integration/test_socketio_stats.py -v -s
"""
import pytest
import requests
import socketio
import asyncio
import threading
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

PLATFORM_URL = "http://localhost:5100"
COLLECTOR_URL = "http://localhost:5101"

received_events = []
stop_listener = threading.Event()


def is_port_open(url, timeout=3):
    try:
        resp = requests.get(f"{url}/api/v1/collector/health", timeout=timeout)
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    try:
        resp = requests.get(f"{url}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


# ── Socket.IO Listener ──────────────────────────────────────────

class SocketIOListener:
    """在独立线程中运行 Socket.IO 客户端"""

    def __init__(self, url, session_id):
        self.url = url
        self.session_id = session_id
        self.sio = socketio.Client(reconnection=False, request_timeout=10)
        self.thread = None
        self.error = None

        @self.sio.on("connect", namespace="/")
        def on_connect():
            print(f"[SocketIO] Connected to {self.url}/socket.io/")
            # 订阅 session
            self.sio.emit("subscribe", {"session_id": self.session_id}, namespace="/")
            print(f"[SocketIO] Subscribed to session: {self.session_id}")

        @self.sio.on("collector_stats", namespace="/")
        def on_collector_stats(data):
            print(f"[SocketIO] *** collector_stats received: {data}")
            received_events.append(("collector_stats", data))

        @self.sio.on("inference_result", namespace="/")
        def on_inference_result(data):
            print(f"[SocketIO] inference_result received: {data}")
            received_events.append(("inference_result", data))

        @self.sio.on("device_status", namespace="/")
        def on_device_status(data):
            print(f"[SocketIO] device_status received: {data}")
            received_events.append(("device_status", data))

        @self.sio.on("error", namespace="/")
        def on_error(data):
            print(f"[SocketIO] error received: {data}")
            received_events.append(("error", data))

        @self.sio.on("disconnect", namespace="/")
        def on_disconnect():
            print("[SocketIO] Disconnected")
            stop_listener.set()

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        try:
            self.sio.connect(self.url + "/socket.io/", namespaces=["/"], wait=True, wait_timeout=10)
            self.sio.wait()
        except Exception as e:
            self.error = str(e)
            print(f"[SocketIO] Connection error: {e}")
            stop_listener.set()


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ensure_services():
    """确保服务运行"""
    if not is_port_open(COLLECTOR_URL):
        pytest.skip("Collector not running, start with: python3 -m collector.api --port 5101")
    if not is_port_open(PLATFORM_URL):
        pytest.skip("Platform not running, start with: python3 -m uvicorn backend.main:app --port 5100")
    yield


# ── Test Cases ──────────────────────────────────────────────────

class TestSocketIOStats:
    """Socket.IO collector_stats 事件诊断"""

    def test_start_session_with_listener(self, ensure_services):
        """启动 session 并同时监听 Socket.IO collector_stats"""
        global received_events
        received_events = []

        # 1. 启动 session（rfuav-two-stage，无 IQ file 路径）
        print("\n=== Step 1: 启动 session ===")
        resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/start",
            json={
                "component_id": "sim-inference",
                "config": {"confidence_threshold": 0.5}
            },
            timeout=15
        )
        assert resp.status_code == 200, f"Session start failed: {resp.text}"
        data = resp.json()
        session_id = data["session_id"]
        print(f"[PASS] Session started: {session_id}")

        # 2. 启动 Socket.IO listener
        print("\n=== Step 2: 启动 Socket.IO 监听器 ===")
        listener = SocketIOListener(PLATFORM_URL, session_id).start()
        time.sleep(2)  # 给 Socket.IO 连接和订阅留时间

        # 3. 等待 5 秒，收集事件
        print("\n=== Step 3: 等待 5 秒接收事件 ===")
        time.sleep(5)

        # 4. 检查是否收到任何事件
        print(f"\n=== Step 4: 检查收到的事件 ===")
        stats_events = [e for e in received_events if e[0] == "collector_stats"]
        inference_events = [e for e in received_events if e[0] == "inference_result"]
        error_events = [e for e in received_events if e[0] == "error"]

        print(f"Total events received: {len(received_events)}")
        print(f"  collector_stats:   {len(stats_events)}")
        print(f"  inference_result:  {len(inference_events)}")
        print(f"  error:             {len(error_events)}")

        if listener.error:
            print(f"Socket.IO listener error: {listener.error}")

        # 停止 listener
        stop_listener.set()
        try:
            listener.sio.disconnect()
        except Exception:
            pass

        # 停止 session
        print("\n=== Step 5: 停止 session ===")
        stop_resp = requests.post(
            f"{PLATFORM_URL}/api/v1/session/stop",
            json={"session_id": session_id},
            timeout=10
        )
        print(f"[PASS] Session stopped: {stop_resp.json()}")

        # 6. 验证
        assert len(stats_events) > 0, (
            f"❌ 没有收到任何 collector_stats 事件！"
            f"这说明 Platform 的 _run_stats_loop 没有正确推送。"
            f"收到的事件: {received_events}"
        )
        print(f"\n✅ 成功收到 {len(stats_events)} 个 collector_stats 事件")


if __name__ == "__main__":
    import subprocess
    result = subprocess.run([sys.executable, "-m", "pytest", __file__, "-v", "-s"], cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    sys.exit(result.returncode)
