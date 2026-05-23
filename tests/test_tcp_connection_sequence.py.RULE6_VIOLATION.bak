"""
test_tcp_connection_sequence.py
测试目标：验证 TCP 连接时序问题（不依赖真实 Pluto）

场景：模拟完整的 start_session 流程，检查 CollectorIOClient.connect()
是否在 Collector TCP server 真正就绪后才被调用。

预期（当前代码有bug）：CollectorIOClient.connect() 在 Collector 启动前执行 → 连接失败
修复后：先启动 Collector，再连接 → 连接成功
"""

import asyncio
import socket
import threading
import time
import sys
import os

# ── 模拟 Collector 侧 ──────────────────────────────────────────────────────

def make_tcp_server_that_waits_for_signal(port, start_event):
    """TCP server 只在 start_event.set() 后才真正 listen。模拟 Collector 慢启动。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(1.0)
    s.bind(("0.0.0.0", port))
    s.listen(5)
    print(f"[Collector] TCP server bound to {port}（但尚未 accept——等待 start 信号）")
    start_event.wait()  # ← 等待 start 信号才进入 accept
    print("[Collector] start 信号收到，开始 accept")
    try:
        c, addr = s.accept()
        print(f"[Collector] 收到客户端连接: {addr}")
        c.close()
    except Exception as e:
        print(f"[Collector] accept error: {e}")
    finally:
        s.close()

def start_collector_side(port=6103):
    """模拟 Collector.start() → _start_socketio_once() 流程"""
    start_event = threading.Event()

    t = threading.Thread(target=make_tcp_server_that_waits_for_signal, args=(port, start_event), daemon=True)
    t.start()
    time.sleep(0.1)
    print("[Collector] 模拟 Collector 初始化完成（TCP server 已绑定但未 accept）")
    return start_event  # caller 持有 start_event，等 ready 后 set()

# ── 模拟 Platform 侧 ────────────────────────────────────────────────────────

async def platform_side_connect_tcp(port=6103):
    """模拟 CollectorIOClient.connect()"""
    await asyncio.sleep(0.05)  # 模拟一点延迟
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(("localhost", port))
        print(f"[Platform] TCP 连接成功!")
        sock.close()
        return True
    except Exception as e:
        print(f"[Platform] TCP 连接失败: {e}")
        return False

# ── 主测试：复现当前 buggy 顺序 ──────────────────────────────────────────

async def test_buggy_sequence():
    """
    当前 buggy 顺序（CollectorIOClient 在 Collector.start 前连接）：
    1. platform_side_connect_tcp()  ← 太早，连不上
    2. start_event.set()            ← Collector 这时才 accept
    """
    print("\n=== 测试：有 Bug 的顺序（先连后启动）===")
    start_event = start_collector_side(6103)

    # 模拟 buggy 顺序：先 connect，再 start
    result = await platform_side_connect_tcp(6103)
    print(f"[结果] 连接{'成功' if result else '失败'} ← 预期：失败（证明 bug 存在）")
    return result

async def test_fixed_sequence():
    """
    修复后顺序：先启动 Collector，再连接
    1. start_event.set()            ← Collector 开始 accept
    2. platform_side_connect_tcp()  ← 现在能连上
    """
    print("\n=== 测试：修复后的顺序（先启动后连）===")
    start_event = start_collector_side(6104)  # 不同端口避免复用

    # 模拟 fixed 顺序：先 start，再 connect
    start_event.set()  # ← 先启动
    await asyncio.sleep(0.1)  # 给一点时间让 accept 真正发生
    result = await platform_side_connect_tcp(6104)
    print(f"[结果] 连接{'成功' if result else '失败'} ← 预期：成功（证明修复有效）")
    return result

if __name__ == "__main__":
    print("=" * 60)
    print("TCP 连接时序测试（不依赖 Pluto）")
    print("=" * 60)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 测试1：复现有 bug 的顺序
    buggy_result = loop.run_until_complete(test_buggy_sequence())

    # 测试2：验证修复方案有效
    fixed_result = loop.run_until_complete(test_fixed_sequence())

    print("\n" + "=" * 60)
    print("结论:")
    print(f"  Buggy 顺序:  {'连接成功 ⚠️（意外）' if buggy_result else '连接失败 ✓（证实 bug）'}")
    print(f"  Fixed 顺序:  {'连接成功 ✓（修复有效）' if fixed_result else '连接失败 ⚠️（修复失败）'}")
    print("=" * 60)

    if not buggy_result and fixed_result:
        print("\n✅ 验证通过：需要调整 start_session 中的调用顺序")
        print("   当前：collector_io.connect() 先于 _collector_start()")
        print("   修复：_collector_start() 先于 collector_io.connect()")
        sys.exit(0)
    else:
        print("\n⚠️ 测试结果不符合预期，请检查测试代码")
        sys.exit(1)
