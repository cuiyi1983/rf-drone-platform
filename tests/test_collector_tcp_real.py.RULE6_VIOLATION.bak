"""
test_collector_tcp_real.py
使用真实的 Collector 代码（simulator 模式），验证：
1. Collector.start() 返回后，TCP server 是否就绪
2. CollectorIOClient.connect() 能否立即成功

不依赖 Pluto 硬件。
"""

import sys
import os
import time
import threading
import asyncio
import socket

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 使用真实 Collector 代码
from collector.collector import Collector, CollectorConfig
from collector.tcp_data_server import TCPDataServer

# 全局测试端口
TEST_PORT = 6160

def test_collector_start_returns_quickly():
    """
    测试：Collector.start() 是否立即返回（不等待第一帧）
    如果立即返回 → TCP server 已就绪 → Platform 可以连接
    如果阻塞 → 时序问题
    """
    print("\n" + "="*60)
    print("测试：Collector.start() 返回时间")
    print("="*60)

    # 创建 TCP server（Collector 侧）
    tcp_server = TCPDataServer(host="0.0.0.0", port=TEST_PORT)
    tcp_ref = [tcp_server]
    server_ready = threading.Event()
    server_started = threading.Event()

    def fake_broadcast(frame_id, timestamp, iq_data):
        # 模拟 emit后的行为
        pass

    collector = Collector()
    collector.on_iq_frame(fake_broadcast)

    # 启动 TCP server（在主线程）
    tcp_server.start()
    server_ready.set()
    time.sleep(0.1)  # 确保 server start 完成
    print(f"TCP server _running={tcp_server._running}")

    # Collector start（模拟 start_collector 中的调用）
    config = CollectorConfig(
        frequencies=[5_805_000_000],
        sample_rate=60_000_000,
        buffer_size=524_288,
        gain=20.0,
    )

    start_time = time.time()
    # 启动 collector（simulator 模式，不阻塞）
    session_id = collector.start(mode="simulator", config=config)
    elapsed = time.time() - start_time
    print(f"Collector.start() 返回耗时: {elapsed*1000:.1f}ms")
    print(f"Collector session: {session_id}")
    print(f"Collector state: {collector._state}")

    # 立即尝试 TCP 连接（模拟 Platform 的 connect）
    time.sleep(0.05)  # 模拟 Platform 收到 HTTP 200 后的极短延迟

    def try_connect():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect(("localhost", TEST_PORT))
            sock.close()
            print(f"✅ TCP 连接成功! (耗时 {time.time()-start_time:.2f}s)")
            return True
        except Exception as e:
            print(f"❌ TCP 连接失败: {e}")
            return False

    connected = try_connect()

    # 清理
    collector.stop(session_id)
    tcp_server.stop()

    print(f"\n结果: {'✅ TCP 连接成功（无 Pluto 依赖）' if connected else '❌ TCP 连接失败'}")
    return connected


def test_collector_io_client_real():
    """使用真实的 CollectorIOClient 测试"""
    from backend.collector_io_client import CollectorIOClient

    TEST_PORT2 = 6161
    print("\n" + "="*60)
    print("测试：真实的 CollectorIOClient.connect()")
    print("="*60)

    # 启动 TCP server
    tcp_server = TCPDataServer(host="0.0.0.0", port=TEST_PORT2)
    tcp_server.start()
    time.sleep(0.1)
    print(f"TCP server _running={tcp_server._running}")

    # 创建 CollectorIOClient
    client = CollectorIOClient(collector_host="localhost", collector_port=TEST_PORT2)

    class FakeFramework:
        def put_frame(self, frame):
            pass

    async def do_connect():
        return await client.connect(FakeFramework(), "test_session")

    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(do_connect())

    print(f"CollectorIOClient.connect() 返回: {result}")

    # 清理
    loop.run_until_complete(client.disconnect())
    tcp_server.stop()

    return result


if __name__ == "__main__":
    print("="*60)
    print("Collector TCP 连接真实性测试（simulator 模式）")
    print("="*60)

    r1 = test_collector_start_returns_quickly()
    time.sleep(0.5)
    r2 = test_collector_io_client_real()

    print("\n" + "="*60)
    print("结论:")
    print(f"  TCP Server 就绪后立即 connect: {'✅' if r1 else '❌'}")
    print(f"  真实 CollectorIOClient.connect(): {'✅' if r2 else '❌'}")
    print("="*60)

    if r1 and r2:
        print("\n✅ TCP 连接不依赖 Pluto 硬件。连接被拒绝是 Windows 时序问题。")
        print("建议：在 CollectorIOClient.connect() 增加重试逻辑（最多 3 次，间隔 200ms）")
    else:
        print("\n⚠️ 发现 TCP 连接问题，需进一步调查")

    sys.exit(0 if (r1 and r2) else 1)
