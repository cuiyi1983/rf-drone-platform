"""
test_tcp_connection_timing.py
目标：验证真实时序下 CollectorIOClient 能否连接成功。

测试场景：
1. Collector.start() 阻塞（等设备）→ Platform connect() 在 accept() 之前
2. Collector.start() 非阻塞（当前代码）→ Platform connect() 在 listen() 之后
3. 修复后顺序 → 先 collector.start，再 connect
"""

import sys
import os
import time
import threading
import socket
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================
# Helper functions
# ============================================================

def make_server(port, accept_delay=0.0):
    """创建一个 TCP server，在 accept 前可选延迟。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(3.0)
    s.bind(("0.0.0.0", port))
    s.listen(5)
    print(f"[Collector] TCP listening on {port}")

    def accepter():
        if accept_delay > 0:
            time.sleep(accept_delay)
            print(f"[Collector] accept 延迟 {accept_delay}s 后才执行")
        try:
            c, addr = s.accept()
            print(f"[Collector] accept 收到: {addr}")
            c.close()
        except Exception as e:
            print(f"[Collector] accept error: {e}")
        finally:
            s.close()

    t = threading.Thread(target=accepter, daemon=True)
    t.start()
    return s, t


async def platform_connect(port, delay=0.1):
    """模拟 CollectorIOClient.connect()"""
    await asyncio.sleep(delay)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("localhost", port))
        print(f"[Platform] ✅ 连接成功!")
        sock.close()
        return True
    except Exception as e:
        print(f"[Platform] ❌ 连接失败: {e}")
        return False

# ============================================================
# Test 1: Collector.start() 阻塞（等2秒才 accept）
# ============================================================

def test_collector_blocks_before_accept():
    port = 6150
    print("\n" + "="*60)
    print("测试1: Collector.start() 阻塞（等2秒才 accept）")
    print("="*60)

    # Server 延迟 2 秒才 accept（模拟 Pluto 设备初始化慢）
    make_server(port, accept_delay=2.0)
    time.sleep(0.1)  # 确保 server 已 listen

    # Platform 在 accept 之前就 connect（模拟当前 buggy 顺序）
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(platform_connect(port, delay=0.0))
    print(f"\n结果: {'✅ 连接成功' if result else '❌ 连接失败'}")
    return result

# ============================================================
# Test 2: 当前代码时序（_collector.start() 不阻塞）
# ============================================================

def test_current_code_timing():
    port = 6151
    print("\n" + "="*60)
    print("测试2: 当前代码时序（Collector.start() 不阻塞，立即 listen）")
    print("="*60)

    # Server 不延迟，立即 listen（非阻塞）
    make_server(port, accept_delay=0.0)
    time.sleep(0.05)  # listen 后立即返回

    # Platform 收到 HTTP 响应后立即 connect
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(platform_connect(port, delay=0.0))
    print(f"\n结果: {'✅ 连接成功' if result else '❌ 连接失败'}")
    return result

# ============================================================
# Test 3: 修复后顺序（先 collector.start，再 connect）
# ============================================================

def test_fixed_order():
    port = 6152
    print("\n" + "="*60)
    print("测试3: 修复后顺序（先确保 server ready，再 connect）")
    print("="*60)

    make_server(port, accept_delay=0.0)
    time.sleep(0.1)  # 确保 accept 线程已准备好

    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(platform_connect(port, delay=0.0))
    print(f"\n结果: {'✅ 连接成功' if result else '❌ 连接失败'}")
    return result

# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("="*60)
    print("TCP 连接时序测试（Linux 单机）")
    print("目标：验证 connect() 在 accept() 前/后是否成功")
    print("="*60)

    r1 = test_collector_blocks_before_accept()
    time.sleep(0.5)
    r2 = test_current_code_timing()
    time.sleep(0.5)
    r3 = test_fixed_order()

    print("\n" + "="*60)
    print("结果汇总:")
    print(f"  阻塞(等2s accept):  {'✅ 成功' if r1 else '❌ 失败'} — accept前connect也能成功")
    print(f"  非阻塞(立即listen):{'✅ 成功' if r2 else '❌ 失败'} — listen后connect")
    print(f"  修复后顺序:        {'✅ 成功' if r3 else '❌ 失败'} — 确保server就绪")
    print("="*60)

    if r1 and r2 and r3:
        print("\n结论：✅ 在 Linux 上，无论 accept 何时发生，只要 listen() 后 connect() 就能成功。")
        print("→ TCP 连接不依赖 Pluto 硬件。")
        print("→ 连接被拒绝的原因在 Windows 上有差异（防火墙/端口状态）。")
        print("→ 请确认 Windows 上 6103 端口无其他进程占用。")
        print("→ 建议在 Platform 增加重试逻辑（connect 失败时等待 500ms 重试）。")
    else:
        print("\n结论：发现时序 bug，connect() 在 listen() 完成前被拒绝。")
        print("→ 修复：将 collector_io.connect() 移到 _collector_start() 之后。")

    print(f"\n退出码: {0 if (r1 and r2 and r3) else 1}")
    sys.exit(0 if (r1 and r2 and r3) else 1)
