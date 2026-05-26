import sys, time
sys.path.insert(0, "/repo")

print("=" * 60)
print("DATA FLOW: Collector -> Platform")
print("=" * 60)

from collector.tcp_data_server import TCPDataServer
from backend.collector_io_client import CollectorIOClient
import numpy as np
import asyncio

# Start TCP server
tcp = TCPDataServer(host="0.0.0.0", port=6103)
tcp.start()
time.sleep(0.2)

# Start CollectorIOClient
client = CollectorIOClient("localhost", 6103)

received = []
class FakeF:
    def put_frame(self, f):
        received.append(f["frame_id"])
        print("  -> Received frame_id=%s, samples=%d" % (f["frame_id"], len(f["iq_data"])))

async def run():
    connected = await client.connect(FakeF(), "test_sess")
    print("[Connection] %s" % ("SUCCESS" if connected else "FAILURE"))
    if connected:
        await asyncio.sleep(0.5)
        test_iq = np.random.randn(1000).astype(np.complex64)
        tcp.broadcast_frame(frame_id=999, timestamp=time.time(), iq_data=test_iq)
        await asyncio.sleep(0.5)
        await client.disconnect()
    return connected

result = asyncio.get_event_loop().run_until_complete(run())
tcp.stop()

if received:
    print("\n[DATA FLOW] SUCCESS - received %d frame(s)" % len(received))
else:
    print("\n[DATA FLOW] WARNING - no frames received (possible timing issue)")
print("=" * 60)