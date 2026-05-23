# 测试覆盖规范 v1.0

> 版本 v1.0 — 2026-05-18
> 状态：生效

---

## 1. 覆盖原则

### 1.1 客户可见接口必须全量覆盖
每个在 `interfaces/platform-frontend.yaml` 中定义的 REST 接口，必须有对应的测试用例。禁止有接口无测试。

### 1.2 CORS preflight 必须覆盖
每个前端调用的 REST 接口（GET/POST/PATCH/DELETE），必须有一条 `OPTIONS` 测试验证 200 返回和 CORS headers。不允许只覆盖实际 method 而跳过 preflight。

### 1.3 内部组件通信接口必须覆盖
Platform→Collector 的 HTTP 调用链属于集成测试范畴，必须验证方法正确性（GET/POST 与接口文档一致）。

### 1.4 刷新/重新执行类接口必须覆盖
重新触发动作的接口（如 `/devices/refresh`）不能仅用读接口（GET devices）代替测试。

---

## 2. 测试分层要求

### 单元测试（tests/unit/）
- 测试每个 API 接口的完整方法（GET + POST + OPTIONS + 错误路径）
- 不得用 TestClient 绕过真实 HTTP 流来模拟 preflight
- 必须在测试中显式断言 response headers 包含 CORS 字段

### 集成测试（tests/integration/）
- 启动真实 Collector 进程
- 验证 Platform→Collector 通信链路的 HTTP 方法与接口文档一致
- 不得在测试中 mock HTTP response，必须用真实进程

---

## 3. 新增接口规范

新增接口时必须同时完成：
1. 在 `interfaces/platform-frontend.yaml` 中记录接口（路径、方法、Request/Response）
2. 在 `tests/unit/test_api_*.py` 中添加对应的测试用例
3. 在 `tests/integration/test_platform_flow.py` 中添加集成测试（如涉及组件间调用）

---

## 4. 禁止规则

| 规则 | 说明 |
|---|---|
| 禁止 TestClient 绕过 CORS | 单元测试的 ASGITransport 无法触发真实的浏览器 preflight 行为，必须显式测试 OPTIONS |
| 禁止用读接口替代刷新接口 | `GET /devices` 不能替代 `POST /devices/refresh` 的测试 |
| 禁止跳过 preflight 测试 | 每一个前端调用的接口都必须有 OPTIONS 测试用例 |
| 禁止内部接口无测试 | Platform→Collector 的 HTTP 调用链必须验证，不能只在 Platform 外部行为测试 |

---

---

## 6. WebUI 仿真测试原则（2026-05-20）

### 规则 6：WebUI 仿真测试原则
- **测试输入**：仅允许模拟前台 WebUI 行为
- **调用范围**：仅允许调用 WebUI 可访问的接口（`http://localhost:5100`，Platform）
- **禁止**：直接调用 `http://localhost:5101`（Collector 内部 API，WebUI 不可见）
- **链路验证**：通过 5100 的响应验证链路，不直接探 Collector
- **禁止**：ASGITransport / AsyncClient 在集成/e2e 测试中绕过真实 HTTP
- **setup 健康检查例外**：`requests.get("http://localhost:5101/...")` 仅用于环境就绪检测，不可达时 `pytest.skip`

---

## 7. 测试分层定义

| 层级 | 目录 | 调用目标 | Transport |
|---|---|---|---|
| 单元测试 | `tests/unit/` | `http://localhost:5100` + TestClient（FastAPI） | ASGITransport 允许（仅单元） |
| 集成测试 | `tests/integration/` | `http://localhost:5100` + 真实 subprocess | requests only |
| E2E 测试 | `tests/e2e/` | `http://localhost:5100` + subprocess 启动真实服务 | requests only |

> **关键**：集成测试和 E2E 测试禁止使用 ASGITransport/AsyncClient 绕过真实 HTTP 层，必须通过真实的网络调用验证完整链路。

---

## 5. 测试用例模板

```python
# REST 接口测试模板
@pytest.mark.asyncio
async def test_<method>_<path>_success(client):
    """<接口功能>"""
    resp = await client.<method>("/api/v1/<path>")
    assert resp.status_code == 200
    data = resp.json()
    assert "..." in data

@pytest.mark.asyncio
async def test_options_<path>_cors_preflight(client):
    """CORS preflight: OPTIONS /api/v1/<path> 返回 200"""
    resp = await client.options("/api/v1/<path>")
    assert resp.status_code == 200, f"CORS preflight failed: {resp.status_code}"
    assert "access-control-allow-origin" in resp.headers
    assert "access-control-allow-methods" in resp.headers
```
---

## 7. 集成测试禁止 Mock 规则（2026-05-20 新增）

**【集成测试禁止 Mock 规则】**

集成测试中，**被测对象的 Mock 模式必须关闭**：
- pluto-repeater（IQ 文件循环回放）是**软件功能**，不是硬件 Mock，必须在真实模式下也可用
- 测试禁止通过 mock 设备绕过真实的设备发现链路

> **背景**：2026-05-20 发现 pluto-repeater 设备被硬编码在 `--mock-devices` 分支中，导致真实模式下前端无法选择该设备（已修复，commit 229f22a）。`--mock-devices` 参数已删除。
