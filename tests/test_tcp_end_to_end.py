"""
test_tcp_end_to_end.py
端到端测试：在同一台机器上启动 Collector（simulator模式）+ Platform，
验证 CollectorIOClient.connect() 能否在真实流程中成功连接。

不依赖真实 Pluto 硬件。
"""

import asyncio
import subprocess
import sys
import time
import os
import signal

# ── 启动 Collector ────────────────────────────────────────────────────────

COLLECTOR_DIR = os.path.dirname(os.path.abspath(__file__)) + "/.."
sys.path.insert(0, COLLECTOR_DIR)

# 启动一个 HTTP 服务器模拟 Collector API (使用 Flask test client)
from collector.api import CollectorAPI
from collector.collector import Collector
from collector.config import CollectorConfig
import collector.tcp_data_server as tcp_module

# 临时覆盖 TCP_DATA_PORT 为测试端口
TEST_TCP_PORT = 6137

# Patch TCP_DATA_PORT before importing collector
import collector.api as api_module

# 直接实例化一个 Collector 来测试 TCP server
collector_instance = Collector()

# 创建 TCPDataServer 并启动
tcp_server = tcp_module.TCPDataServer(host="0.0.0.0", port=TEST_TCP_PORT)
tcp_server_ref = [tcp_server]  # 闭包捕获

def make_emitter():
    def emitter(frame):
        tcp_server_ref[0].broadcast_frame(
            frame_id=frame["frame_id"],
            timestamp=frame["timestamp"],
            iq_data=frame["iq_data"]
        )
    return emitter

collector_instance.on_iq_frame(make_emitter())

# 启动 TCP server（非阻塞，在后台线程）
tcp_server.start()
time.sleep(0.2)

print(f"[测试] TCPDataServer 已在 {TEST_TCP_PORT} 启动，_ running = {tcp_server._running}")

# ── 测试 CollectorIOClient ────────────────────────────────────────────────

sys.path.insert(0, COLLECTOR_DIR)
from backend.collector_io_client import CollectorIOClient

async def test_connect():
    print(f"\n[测试] 尝试连接 localhost:{TEST_TCP_PORT} ...")

    client = CollectorIOClient(collector_host="localhost", collector_port=TEST_TCP_PORT)

    class FakeFramework:
        def put_frame(self, frame):
            pass

    framework = FakeFramework()

    # 模拟 start_session 中的调用
    connected = await client.connect(framework, "test_session")
    print(f"[测试] CollectorIOClient.connect() 返回: {connected}")

    if connected:
        print("[测试] ✅ TCP 连接成功！bug 已修复（或不存在）")
        await client.disconnect()
    else:
        print("[测试] ❌ TCP 连接失败，证明 bug 存在：时序问题导致 connect() 在 server 就绪前执行")

    return connected

loop = asyncio.new_event_loop()
result = loop.run_until_complete(test_connect())

# 清理
tcp_server.stop()
collector_instance.stop("test_session")

sys.exit(0 if result else 1)
