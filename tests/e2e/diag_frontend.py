"""
诊断 5102 前端实时推理框 + 缓冲区监控问题
使用 Playwright headless 浏览器拦截 Socket.IO 事件
"""
import asyncio
from playwright.async_api import async_playwright
import json

API_BASE = "http://localhost:5100"
FRONTEND = "http://localhost:5102"

async def run():
    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 拦截 Socket.IO 事件（WebSocket 和 polling）
        intercepted_events = []

        def on_websocket(ws):
            print("[WS] WebSocket intercept enabled")
            ws.on("frames", lambda d: intercepted_events.append(("ws-frames", d)))
            ws.on("inference_result", lambda d: intercepted_events.append(("ws-inference_result", d)))
            ws.on("collector_stats", lambda d: intercepted_events.append(("ws-collector_stats", d)))

        page.on("websocket", on_websocket)

        # 拦截 fetch/XHR（api 调用）
        api_calls = []
        def on_request(request):
            if "/api/v1/" in request.url or "socket.io" in request.url:
                api_calls.append({"url": request.url, "method": request.method})
        page.on("request", on_request)

        async def on_response(response):
            if "/api/v1/" in response.url:
                try:
                    body = await response.json()
                    api_calls.append({"url": response.url, "status": response.status, "body": body})
                except:
                    pass
        page.on("response", on_response)

        print("[1] 访问前端页面...")
        await page.goto(FRONTEND, wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(1000)
        print(f"    页面标题: {await page.title()}")

        # 检查关键 DOM 元素初始状态
        print("\n[2] 初始 DOM 状态:")
        for el_id in ['cnt', 'rtbody', 'rthead', 'buf-frames', 'buf-fps', 'buf-dropped',
                       'buf-fill', 'buf-val', 'buf-coll', 'cfg-device', 'crt', 'stat']:
            try:
                val = await page.eval_on_selector(f'#{el_id}', 'el => el ? el.textContent : "NOT_FOUND"')
                print(f"    #{el_id}: {repr(val)}")
            except Exception as e:
                print(f"    #{el_id}: ERROR - {e}")

        print("\n[3] 检查 Socket.IO 连接状态...")
        socket_connected = await page.eval_on_selector('body', '''() => {
            // 检查是否有 socket 相关的全局变量
            return {
                hasSocket: typeof socket !== 'undefined',
                hasS: typeof S !== 'undefined' ? S : null,
            }
        }''')
        print(f"    {socket_connected}")

        print("\n[4] 加载推理组件 (sim-inference)...")
        try:
            # 查找 mssel 下拉框
            await page.select_option('#msel', 'sim-inference')
            print("    ✅ 已选择 sim-inference")
        except Exception as e:
            print(f"    ❌ 选择失败: {e}")

        await page.wait_for_timeout(500)

        # 点击加载按钮 (mlBtn)
        try:
            await page.click('#mlBtn')
            print("    ✅ 已点击 mlBtn (加载组件)")
        except Exception as e:
            print(f"    ❌ mlBtn 点击失败: {e}")

        await page.wait_for_timeout(2000)

        # 检查模型是否加载
        print("\n[5] 检查组件加载状态:")
        for el_id in ['msel', 'mlBtn', 'stat', 'cfg-component']:
            try:
                val = await page.eval_on_selector(f'#{el_id}', 'el => el ? (el.value || el.textContent || el.innerHTML).substring(0,80) : "NOT_FOUND"')
                print(f"    #{el_id}: {repr(val)}")
            except:
                print(f"    #{el_id}: NOT_FOUND")

        print("\n[6] 选择 pluto-repeater 设备...")
        try:
            await page.select_option('#deviceSel', 'pluto-repeater')
            print("    ✅ 已选择 pluto-repeater")
        except Exception as e:
            print(f"    ❌ 选择失败: {e}")
        await page.wait_for_timeout(500)

        print("\n[7] 点击启动采数 (btnS)...")
        try:
            await page.click('#btnS')
            print("    ✅ 已点击 btnS (启动采数)")
        except Exception as e:
            print(f"    ❌ btnS 点击失败: {e}")

        # 等待数据
        print("\n[8] 等待 6 秒采集数据...")
        await page.wait_for_timeout(6000)

        # 再次检查 DOM
        print("\n[9] 采数 6 秒后 DOM 状态:")
        for el_id in ['cnt', 'rtbody', 'rthead', 'buf-frames', 'buf-fps', 'buf-dropped',
                       'buf-fill', 'buf-val', 'buf-coll', 'cfg-device', 'crt', 'stat']:
            try:
                val = await page.eval_on_selector(f'#{el_id}', 'el => el ? el.textContent : "NOT_FOUND"')
                print(f"    #{el_id}: {repr(val)}")
            except Exception as e:
                print(f"    #{el_id}: ERROR - {e}")

        # 检查 S 全局变量
        print("\n[10] 全局变量 S 状态:")
        try:
            s_state = await page.eval_on_selector('body', '''() => {
                if (typeof S === 'undefined') return 'S is undefined';
                return {
                    session_id: S.session_id,
                    inf_count: S.inf_count,
                    results_len: S.results ? S.results.length : 0,
                    session_config: S.session_config ? Object.keys(S.session_config) : null,
                    collecting: S.collecting,
                    component_loaded: S.component_loaded,
                    collector_connected: S.collector_connected,
                    socket_connected: socket && socket.connected,
                }
            }''')
            print(f"    {json.dumps(s_state, indent=4)}")
        except Exception as e:
            print(f"    ERROR: {e}")

        # 打印 API 调用
        print(f"\n[11] API 调用 ({len(api_calls)} 次):")
        for call in api_calls:
            url_short = call.get('url', '').replace(API_BASE, '').replace(FRONTEND, '')
            print(f"    {call.get('method','?')} {url_short}")
            if 'body' in call:
                body_str = str(call['body'])[:200]
                print(f"        → {body_str}")

        # 打印拦截的 Socket.IO 事件
        print(f"\n[12] Socket.IO 拦截事件 ({len(intercepted_events)} 个):")
        for ev_name, ev_data in intercepted_events[:20]:
            print(f"    [{ev_name}] {str(ev_data)[:150]}")

        # 截图
        screenshot_path = "/tmp/frontend_diag.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        print(f"\n[13] 截图已保存: {screenshot_path}")

        await browser.close()

    return results

if __name__ == "__main__":
    asyncio.run(run())
