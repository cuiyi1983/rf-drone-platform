import sys, time, os
sys.path.insert(0, "/repo")

print("=" * 60)
print("STARTUP VERIFICATION")
print("=" * 60)

# Step 1: Start Collector and confirm TCP server on 6103
print("\n[Step 1] Starting Collector...")
from collector.api import CollectorAPI
from collector.tcp_data_server import TCPDataServer

collector_api = CollectorAPI()

collector_api._tcp_server = TCPDataServer(host="0.0.0.0", port=6103)
collector_api._tcp_server.start()
time.sleep(0.3)
print("TCP server _running = %s" % collector_api._tcp_server._running)

# Step 2: Simulate Platform._collector_start() -> HTTP POST to /api/v1/collector/start
print("\n[Step 2] Collector start HTTP POST...")
import requests
try:
    resp = requests.post("http://localhost:5101/api/v1/collector/start", json={
        "mode": "simulator",
        "config": {"frequencies": [5805000000], "buffer_size": 524288, "sample_rate": 60000000}
    }, timeout=5)
    print("Collector start HTTP: %s %s" % (resp.status_code, resp.json()))
except Exception as e:
    print("Collector start HTTP ERROR: %s" % e)

# Step 3: Simulate Platform start_session connect sequence
print("\n[Step 3] Simulating Platform.start_session sequence...")
from backend.collector_io_client import CollectorIOClient

collector_host = "localhost"
collector_port = 6103

print("  _collector_start() -> Collector notified to start")
print("  CollectorIOClient.connect(%s, %s)..." % (collector_host, collector_port))

async def test_connect():
    client = CollectorIOClient(collector_host=collector_host, collector_port=collector_port)
    class FakeF:
        def put_frame(self, f): pass
    result = await client.connect(FakeF(), "test_session")
    print("  -> connect() returned: %s" % result)
    if result:
        await client.disconnect()
    return result

import asyncio
result = asyncio.get_event_loop().run_until_complete(test_connect())
print("\n[RESULT] Platform -> Collector TCP: %s" % ("SUCCESS" if result else "FAILURE"))
print("=" * 60)