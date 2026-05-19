"""
RF-Drone-Platform Backend
FastAPI 入口 + 核心 Platform 协调器
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from .api.components import inject_platform as inject_components
from .api.devices import inject_platform as inject_devices
from .api.session import inject_platform as inject_session
from .config_manager import ConfigManager
from .inference.framework import InferenceFramework
from .socketio.server import SocketIOServer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Platform:
    """
    Platform Backend 核心协调器
    负责会话管理、组件管理、设备管理、配置合并
    """

    def __init__(self, collector_base_url: str = "http://localhost:5101"):
        self._collector_base_url = collector_base_url

        # 子模块
        self.config_manager = ConfigManager()
        self.socketio_server = SocketIOServer()

        # 会话存储
        self._sessions: dict[str, dict] = {}

        # 推理框架实例（每个会话一个）
        self._frameworks: dict[str, InferenceFramework] = {}

        # 推理历史（内存缓存）
        self._inference_history: dict[str, list] = {}

        # 组件注册表（mock）
        self._components: dict[str, dict] = {}

        # 设备注册表（mock）
        self._devices: dict[str, dict] = {}

        # HTTP 客户端
        self._requests: Optional[Any] = None

    # ── 初始化 ───────────────────────────────────────────────────

    async def startup(self, app: FastAPI) -> None:
        """启动时调用"""
        logger.info("Platform: 启动中...")

        # 初始化 HTTP 客户端
        import requests
        self._requests = requests.Session()
        self._requests.headers.update({"User-Agent": "RF-Drone-Platform/1.0"})

        # 注册模拟组件
        self._register_sim_components()

        # 注册模拟设备
        await self._discover_devices()

        # 注入 platform ref 到各 API router
        inject_session(self)
        inject_components(self)
        inject_devices(self)

        # 从 Collector 获取能力并缓存
        await self._load_collector_capabilities()

        logger.info("Platform: 启动完成")

    async def shutdown(self) -> None:
        """关闭时调用"""
        # 停止所有会话
        for session_id in list(self._sessions.keys()):
            await self.stop_session(session_id)
        if self._requests:
            self._requests.close()
        logger.info("Platform: 已关闭")

    # ── 模拟数据 ─────────────────────────────────────────────────

    def _register_sim_components(self) -> None:
        """注册内置模拟组件（与真实 .zip 组件同等对待）"""
        from backend.components.sim_component import COMPONENT_ENTRY
        self._components = {
            COMPONENT_ENTRY["id"]: {
                **COMPONENT_ENTRY["manifest"]["component"],
                "version": COMPONENT_ENTRY["version"],
                "type": COMPONENT_ENTRY["manifest"]["component"].get("type", "inference"),
                **COMPONENT_ENTRY["manifest"]  # 展开 capability / collector_requirements / io / config_schema
            }
        }

    async def _discover_devices(self) -> None:
        """从 Collector 发现设备"""
        logger.info(f"Platform: _discover_devices 开始，目标是 {self._collector_base_url}/api/v1/collector/devices")
        try:
            resp = await asyncio.to_thread(self._requests.get, f"{self._collector_base_url}/api/v1/collector/devices", timeout=10)
            logger.info(f"Platform: Collector 响应 status={resp.status_code} body={resp.text[:200]}")
            if resp.status_code == 200:
                data = resp.json()
                for dev in data.get("devices", []):
                    self._devices[dev["id"]] = dev
                    logger.info(f"Platform: 发现设备 {dev['id']}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Platform: 无法从 Collector 发现设备: {type(e).__name__} {e}", exc_info=True)
            # 禁止 mock 降级，设备列表保持为空

    async def _load_collector_capabilities(self) -> None:
        """从 Collector 获取能力范围"""
        try:
            resp = await asyncio.to_thread(self._requests.get, f"{self._collector_base_url}/api/v1/collector/discover", timeout=10)
            if resp.status_code == 200:
                caps = resp.json().get("capabilities", {})
                self.config_manager.set_collector_capabilities(caps)
                logger.info("Platform: Collector capabilities 已加载")
                return
        except Exception as e:
            logger.warning(f"Platform: 无法获取 Collector capabilities: {e}")

        # 禁止 mock 降级，使用 Pluto 硬件默认值
        default_caps = {
            "frequency": {"type": "int", "range": [325_000_000, 6_000_000_000], "default": 5_805_000_000},
            "buffer_size": {"type": "int", "range": [1024, 1_048_576], "default": 524_288},
            "gain": {"type": "float", "range": [0.0, 60.0], "default": 20.0},
            "sample_rate": {"type": "int", "fixed": 60_000_000},
            "rf_bandwidth": {"type": "int", "fixed": 56_000_000}
        }
        self.config_manager.set_collector_capabilities(default_caps)

    # ── 会话管理 API ───────────────────────────────────────────────

    async def start_session(self, component_id: str, config: dict) -> dict:
        """启动会话"""
        if component_id not in self._components:
            return {"error": "组件不存在或初始化失败", "code": 1001}

        component = self._components[component_id]
        requirements = component.get("collector_requirements", {})

        # 合并配置
        merged_config, warnings = self.config_manager.merge(requirements, None)
        logger.info(f"ConfigManager 合并结果: {merged_config}, warnings={warnings}")

        # 生成 session_id
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        # 创建推理框架
        framework = InferenceFramework(
            buffer_capacity=100,
            stats_callback=lambda stats: self._on_stats(session_id, stats),
            result_callback=lambda result, qstats: self._on_result(session_id, result, qstats),
            error_callback=lambda err: self._on_error(session_id, err)
        )

        # 组件实例：统一加载逻辑，平台不感知是 sim 组件还是真实组件
        # TODO: 真实组件从 .zip 包加载实现后，此处统一处理
        from backend.components.sim_component import SimComponent
        component_instance = SimComponent()
        device = "cpu"  # 实际通过 ONNX Runtime 检测

        if not framework.load_component(component_id, component_instance, config, device):
            return {"error": "组件初始化失败", "code": 1002}

        framework.start()

        # 保存会话
        self._sessions[session_id] = {
            "session_id": session_id,
            "status": "running",
            "component_id": component_id,
            "current_config": {**merged_config, **config},
            "started_at": datetime.now(timezone.utc).isoformat(),
            "warnings": warnings,
            "device_info": self._get_device_info()
        }

        self._frameworks[session_id] = framework
        self._inference_history[session_id] = []

        # 启动采集（通知 Collector）
        conn_result = await self._collector_start(session_id, merged_config)

        return {
            "session_id": session_id,
            "status": "running",
            "warnings": warnings,
            "collector_connection": conn_result["status"],
            **({"collector_error": conn_result["detail"]} if conn_result["status"] == "failed" else {})
        }

    async def stop_session(self, session_id: str) -> dict:
        """停止会话"""
        if session_id not in self._sessions:
            return {"error": "会话不存在", "code": 1003}

        framework = self._frameworks.get(session_id)
        if framework:
            framework.stop()
            del self._frameworks[session_id]

        session = self._sessions[session_id]
        session["status"] = "stopped"
        session["stopped_at"] = datetime.now(timezone.utc).isoformat()

        stats = framework.get_stats() if framework else {}
        await self._collector_stop(session_id)

        return {
            "status": "stopped",
            "stats": {
                "frames_received": stats.get("frames_received", 0),
                "frames_dropped": stats.get("frames_dropped", 0),
                "duration_seconds": self._session_duration(session),
                "detections_count": len(self._inference_history.get(session_id, []))
            }
        }

    async def get_session_status(self, session_id: Optional[str]) -> dict:
        """查询会话状态"""
        if session_id:
            if session_id not in self._sessions:
                return {"error": "会话不存在", "code": 1003}
            session = self._sessions[session_id].copy()
            # 补充组件名称和版本
            component_id = session.get("component_id")
            if component_id and component_id in self._components:
                comp = self._components[component_id]
                session["component_name"] = comp.get("name", "")
                session["component_version"] = comp.get("version", "")
            return session
        else:
            return {"sessions": [s.copy() for s in self._sessions.values()]}

    async def get_session_config(self, session_id: str) -> dict:
        """查询会话当前配置（推理组件配置 + 采集器配置）"""
        if session_id not in self._sessions:
            return {"error": "会话不存在", "code": 1003}

        session = self._sessions[session_id]
        component_id = session["component_id"]
        component = self._components.get(component_id, {})

        # 推理组件配置：来源为用户调用 start 时传入的 config 合并组件 schema defaults
        user_config = session.get("current_config", {})
        inference_config = {
            k: v for k, v in user_config.items()
            if k in component.get("config_schema", {})
        }
        # 补充组件声明的 default 值（用户未指定时）
        for k, schema in component.get("config_schema", {}).items():
            if k not in inference_config and "default" in schema:
                inference_config[k] = schema["default"]

        # 采集器配置
        collector_config = {
            k: v for k, v in user_config.items()
            if k not in component.get("config_schema", {})
        }

        # 设备信息
        device_info = session.get("device_info", {})

        return {
            "session_id": session_id,
            "component_id": component_id,
            "component_name": component.get("name", ""),
            "component_version": component.get("version", ""),
            "inference_config": inference_config,
            "collector_config": collector_config,
            "device_info": device_info,
        }

    async def update_session_config(self, session_id: str, config: dict) -> dict:
        """更新会话配置"""
        if session_id not in self._sessions:
            return {"error": "会话不存在", "code": 1003}
        if self._sessions[session_id]["status"] != "running":
            return {"error": "会话已停止", "code": 1004}

        merged_config, warnings = self.config_manager.merge(
            config, self.config_manager.get_collector_capabilities()
        )

        self._sessions[session_id]["current_config"].update(merged_config)
        self._sessions[session_id]["warnings"].extend(warnings)

        framework = self._frameworks.get(session_id)
        if framework:
            framework.update_config(merged_config)

        component_id = self._sessions[session_id]["component_id"]
        await self._collector_apply_config(component_id, merged_config)

        return {"session_id": session_id, "updated_config": merged_config, "warnings": warnings}

    # ── 组件 API ─────────────────────────────────────────────────

    async def list_components(self) -> dict:
        return {
            "components": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "version": c["version"],
                    "config_schema": c.get("config_schema", {})
                }
                for c in self._components.values()
            ]
        }

    async def get_component_detail(self, component_id: str) -> dict:
        if component_id not in self._components:
            return {"error": "组件不存在", "code": 1001}
        return self._components[component_id].copy()

    async def get_component_config_schema(self, component_id: str) -> dict:
        if component_id not in self._components:
            return {"error": "组件不存在", "code": 1001}
        return {"config_schema": self._components[component_id].get("config_schema", {})}

    # ── 设备 API ─────────────────────────────────────────────────

    async def list_devices(self) -> dict:
        return {"devices": list(self._devices.values())}

    async def get_device_capabilities(self, device_id: str) -> dict:
        if device_id not in self._devices:
            return {"error": "设备不存在或未连接", "code": 1006}
        caps = self.config_manager.get_collector_capabilities()
        return {"capabilities": caps}

    async def refresh_devices(self) -> dict:
        """重新扫描 Collector 设备列表并更新缓存"""
        self._devices.clear()
        logger.info(f"Platform: 开始刷新设备，目标是 {self._collector_base_url}/api/v1/collector/devices")
        try:
            resp = await asyncio.to_thread(self._requests.get, f"{self._collector_base_url}/api/v1/collector/devices", timeout=10)
            logger.info(f"Platform: Collector 响应 status={resp.status_code} body={resp.text[:200]}")
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"Platform: 解析到 devices 列表: {data.get('devices', [])}")
                for dev in data.get("devices", []):
                    self._devices[dev["id"]] = dev
                    logger.info(f"Platform: 刷新设备 {dev['id']}")
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Platform: 刷新设备失败: {type(e).__name__} {e}", exc_info=True)
        return {"devices": list(self._devices.values())}

    # ── Collector 通信 ───────────────────────────────────────────

    async def _collector_start(self, session_id: str, config: dict) -> dict:
        """通知 Collector 开始采集，返回连接结果"""
        try:
            resp = await asyncio.to_thread(self._requests.post, f"{self._collector_base_url}/api/v1/collector/start", json={
                "session_id": session_id,
                "config": config
            }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    return {"status": "success"}
                return {"status": "failed", "detail": data.get("message", "Unknown error")}
            return {"status": "failed", "detail": f"HTTP {resp.status_code}: {resp.text[:100]}"}
        except Exception as e:
            return {"status": "failed", "detail": f"{type(e).__name__}: {e}"}

    async def _collector_stop(self, session_id: str) -> None:
        """通知 Collector 停止采集"""
        try:
            await asyncio.to_thread(self._requests.post, f"{self._collector_base_url}/api/v1/collector/stop", json={"session_id": session_id}, timeout=10)
        except Exception as e:
            logger.warning(f"Collector stop failed: {e}")

    async def _collector_apply_config(self, component_id: str, config: dict) -> None:
        """通知 Collector 应用组件配置（运行时配置更新）"""
        try:
            await asyncio.to_thread(self._requests.post, f"{self._collector_base_url}/api/v1/collector/apply_component_config", json={
                "source": "component",
                "component_id": component_id,
                "requirements": {},
                "config": config
            }, timeout=10)
        except Exception as e:
            logger.warning(f"Collector apply_config failed: {e}")

    # ── Socket.IO 回调 ───────────────────────────────────────────

    def _on_result(self, session_id: str, result: dict, qstats: Any) -> None:
        """推理结果回调"""
        # 保存历史
        if session_id in self._inference_history:
            history = self._inference_history[session_id]
            history.append(result)
            if len(history) > 1000:
                history[:] = history[-1000:]

        # 推送 Socket.IO
        self.socketio_server.emit_inference_result(session_id, {
            "session_id": session_id,
            **result
        })

    def _on_stats(self, session_id: str, stats: dict) -> None:
        """统计回调"""
        framework = self._frameworks.get(session_id)
        if not framework:
            return
        qstats = framework.get_stats()
        self.socketio_server.emit_collector_stats(session_id, {
            "session_id": session_id,
            "frames_per_second": round(qstats.get("inference_count", 0) / max(1, qstats.get("frames_received", 1)), 2),
            "dropped_rate": qstats.get("dropped_rate", 0),
            "buffer_level": qstats.get("buffer_level", 0),
            "total_frames": qstats.get("frames_received", 0),
            "total_dropped": qstats.get("frames_dropped", 0)
        })

    def _on_error(self, session_id: str, message: str) -> None:
        """错误回调"""
        self.socketio_server.emit_error(2001, message, session_id)

    # ── 历史查询 ─────────────────────────────────────────────────

    def get_inference_history(self, session_id: str, limit: int) -> list:
        return (self._inference_history.get(session_id, []))[-limit:]

    # ── 辅助方法 ─────────────────────────────────────────────────

    def _get_device_info(self) -> dict:
        dev = next(iter(self._devices.values()), {})
        caps = self.config_manager.get_collector_capabilities()
        return {
            "type": dev.get("type", "Unknown"),
            "uri": dev.get("uri", ""),
            "center_freq": caps.get("frequency", {}).get("default", 5_805_000_000),
            "sample_rate": caps.get("sample_rate", {}).get("fixed", 60_000_000)
        }

    @staticmethod
    def _session_duration(session: dict) -> int:
        try:
            started = datetime.fromisoformat(session["started_at"].replace("Z", "+00:00"))
            stopped = datetime.fromisoformat(session.get("stopped_at", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
            return int((stopped - started).total_seconds())
        except Exception:
            return 0




# ── FastAPI App ──────────────────────────────────────────────────────────────

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="RF-Drone-Platform Backend", version="1.0.0")

# Serve frontend static files
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (must be after app creation)


# ── 405 例外处理：OPTIONS 预检请求直接返回 200 ──────────────────────────────

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """拦截 405 并将 OPTIONS 转为 200（支持 CORS 预检）"""
    if exc.status_code == 405 and request.method == 'OPTIONS':
        return Response(status_code=200, headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Allow-Credentials': 'true',
        })
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
platform = Platform()


@app.on_event("startup")
async def startup():
    await platform.startup(app)
    # Mount frontend static files
    import os
    frontend_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend')
    if os.path.isdir(frontend_dir):
        app.mount('/static', StaticFiles(directory=frontend_dir), name='static')
    # Redirect root to frontend
    @app.get('/')
    async def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url='/static/index.html')


@app.on_event("shutdown")
async def shutdown():
    await platform.shutdown()


# 注册路由
from .api import session, components, devices

app.include_router(session.router)
app.include_router(components.router)
app.include_router(devices.router)

# 挂载 Socket.IO
platform.socketio_server.init_app(app, platform)


# ── 健康检查 ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Simulator 端点 ───────────────────────────────────────────────────────────

@app.get("/api/v1/simulator/metadata")
async def simulator_metadata():
    """
    获取模拟器元数据（平台内部模拟，无需调用 Collector）
    """
    return {
        "simulator": {
            "enabled": True,
            "type": "pluto_simulator",
            "description": "Pluto SDR 模拟器（用于开发和测试）"
        },
        "supported_modes": ["fixed_freq", "scan"],
        "max_buffer_size": 1_048_576
    }