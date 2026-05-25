"""
完整 E2E 诊断 - Playwright 模拟真实 UI 操作
不走 API 注入，走真实 HTTP 请求
"""
import time
import json
import requests
import subprocess
import sys
sys.path.insert(0, '/root/.openclaw/workspace/rf-drone-platform')

PLATFORM = "http://localhost:5100"

def find_free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]

def main():
    from playwright.sync_api import sync_playwright

    port = find_free_port()
    print(f"[1] 前端服务器 port={port}")
    server = subprocess.Popen(
        ["python3", "-m", "http.server", str(port)],
        cwd="/root/.openclaw/workspace/rf-drone-platform/frontend",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1.5)

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        console_msgs = []
        def on_console(msg):
            console_msgs.append(f"[{msg.type}] {msg.text}")
        page.on("console", on_console)
        page_errors = []
        def on_error(err):
            page_errors.append(str(err))
        page.on("pageerror", on_error)

        print("[2] 访问前端页面...")
        page.goto(f"http://localhost:{port}/index.html", wait_until="load", timeout=15000)
        time.sleep(2)
        s_defined = page.evaluate("() => typeof S !== 'undefined'")
        print(f"    S defined: {s_defined}")
        sock_conn = page.evaluate("() => typeof socket !== 'undefined' && socket ? socket.connected : null")
        print(f"    socket connected: {sock_conn}")

        print("\n[3] 选择推理组件 (sim-inference)...")
        page.select_option('#msel', 'sim-inference')
        time.sleep(0.3)

        print("[4] 点击加载组件 (mlBtn)...")
        page.click('#mlBtn')
        time.sleep(1.5)

        # 检查 mlBtn 文本
        mlbtn_text = page.locator('#mlBtn').text_content()
        print(f"    mlBtn text: {repr(mlbtn_text)}")

        print("\n[5] 选择 pluto-repeater 设备...")
        # 先检查 deviceSel 有哪些选项
        options = page.locator('#deviceSel').evaluate('el => Array.from(el.options).map(o => o.value)')
        print(f"    deviceSel options: {options[:5]}")
        page.select_option('#deviceSel', 'pluto-repeater')
        time.sleep(0.5)

        print("\n[6] 点击启动采数 (btnS)...")
        page.click('#btnS')
        print("    已点击，等待数据...")

        # 等待数据到来
        time.sleep(8)

        print("\n[7] DOM 状态:")
        for el_id in ['cnt', 'buf-frames', 'buf-fps', 'buf-dropped', 'buf-fill', 'buf-val',
                       'cfg-device', 'crt', 'cfg-component', 'cfg-freq', 'cfg-sr', 'cfg-gain',
                       'rtbody', 'stat']:
            try:
                val = page.locator(f'#{el_id}').text_content(timeout=1000)
                val = (val or '').strip()
                status = '✅' if val and val not in ('--', '-', '') else '❌'
                print(f"    {status} #{el_id}: {repr(val[:60])}")
            except Exception as e:
                print(f"    ❌ #{el_id}: NOT_FOUND ({e})")

        print("\n[8] S 状态:")
        s_state = page.evaluate("() => ({"
            "session_id: window.S ? window.S.session_id : null, "
            "inf_count: window.S ? window.S.inf_count : null, "
            "results_len: window.S && window.S.results ? window.S.results.length : 0, "
            "collecting: window.S ? window.S.collecting : null, "
            "socket_connected: window.socket ? window.socket.connected : null, "
            "component_loaded: window.S ? window.S.component_loaded : null, "
            "session_config_keys: window.S && window.S.session_config ? Object.keys(window.S.session_config) : null"
        "})")
        print(f"    {json.dumps(s_state, indent=4)}")

        # 手动注入 collector_stats 验证 DOM 更新能力
        print("\n[9] 手动注入 collector_stats 验证 DOM...")
        page.evaluate("""() => {
            if (typeof handleCollectorStats === 'function') {
                handleCollectorStats({
                    session_id: window.S ? window.S.session_id : 'test',
                    frames_per_second: 12.5,
                    dropped_rate: 0.03,
                    buffer_level: 55,
                    total_frames: 123,
                    total_dropped: 4
                });
            } else {
                console.log('handleCollectorStats not found!');
            }
        }""")
        time.sleep(0.3)

        print("    注入后 DOM:")
        for el_id in ['buf-frames', 'buf-fps', 'buf-dropped', 'buf-fill', 'buf-val']:
            try:
                val = page.locator(f'#{el_id}').text_content(timeout=500)
                print(f"    #{el_id}: {repr((val or '').strip())}")
            except:
                print(f"    #{el_id}: ERR")

        # 截图
        page.screenshot(path="/tmp/frontend_diag.png", full_page=True)
        print("\n[10] 截图: /tmp/frontend_diag.png")

        if page_errors:
            print(f"\n[11] Page errors ({len(page_errors)}):")
            for e in page_errors[:5]:
                print(f"    {e}")

        if console_msgs:
            err_console = [m for m in console_msgs if 'error' in m.lower() or 'Error' in m]
            print(f"\n[12] Console errors ({len(err_console)}):")
            for m in err_console[:5]:
                print(f"    {m}")

        browser.close()
        playwright.stop()
    finally:
        server.terminate()
        server.wait()

if __name__ == "__main__":
    main()
