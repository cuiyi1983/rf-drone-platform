import sys, time, os
sys.path.insert(0, "/repo")

print("=" * 60)
print("启动流程验证")
print("=" * 60)

# 1. 启动 Collector 并确认 TCP server 在 6103 监听
print("\n[Step 1] 启动 Collector...")
from collector.api import CollectorAPI
from collector.tcp_data_server import TCPDataServer

collector_api = CollectorAPI()

# 检查 TCP server 是否在 6103
collector_api._tcp_server = TCPDataServer(host="0.0.0.0", port=6103)
collector_api._tcp_server.start()
time.sleep(0.3)
print(f"TCP server _running = {collector_api._tcp_server._running}")

# 2. 模拟 Platform._collector_start() → HTTP POST 到 /api/v1/collector/start
import requests
resp = requests.post("http://localhost:5101/api/v1/collector/start", json={
    "mode": "simulator",
    "config": {"frequencies": [5805000000], "buffer_size": 524288, "sample_rate": 60000000}
}, timeout=5)
print(f"[Step 2] Collector start HTTP: {resp.status_code} {resp.json()}")

# 3. 模拟 Platform start_session 中的 connect() jq�序
print("\n[Step 3] 模拟 Platform.start_session 顺序...")
from backend.collector_io_client import CollectorIOClient

collector_host = "localhost"
collector_port = 6103

print(f"  _collector_start() → 已通知 Collector 开始采集")
print(f"  CollectorIOClient.connect({collector_host}, {collector_port})...")

async def test_connect():
    client = CollectorIOClient(collector_host=collector_host, collector_port=collector_port)
    class FakeF:
        def put_frame(self, f): pass
    result = await client.connect(FakeF(), "test_session")
    print(f"  → connect() 返回: {result}")
    if result:
        await client.disconnect()
    return result

import asyncio
result = asyncio.get_event_loop().run_until_complete(test_connect())
print(f"\n[结果] Platform → Collector TCP 连接: {'✅ 成功' if result else '❌ 失败'}")
print("=" * 60)