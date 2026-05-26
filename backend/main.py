"""
RF-Drone-Platform Backend
FastAPI 入口 + 核心 Platform 协调器
"""
import asyncio
import logging
import uuid
import yaml
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
from .socketio.server import SocketIOServer, PlatformNamespace
from .collector_io_client import CollectorIOClient

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
        self._collector_io_client: dict[str, CollectorIOClient] = {}  # session_id → client

        # 会话存储
        self._sessions: dict[str, dict] = {}

        # 推理框架实例（每个会话一个）
        self._frameworks: dict[str, InferenceFramework] = {}

        # 定期 stats 推送任务（session_id → task）
        self._stats_tasks: dict[str, asyncio.Task] = {}

        # 推理历史（内存缓存）
        self._inference_history: dict[str, list] = {}

        # asyncio event loop（从主线程获取，供给推理线程的 run_coroutine_threadsafe 使用）
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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
        self._discover_components()

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

    def _discover_components(self) -> None:
        """自动扫描 components/ 目录，注册所有有效组件"""
        import sys, os, importlib.util
        import yaml

        components_base = os.path.join(os.path.dirname(__file__), '..', 'components')
        if not os.path.isdir(components_base):
            logger.warning(f"Platform: components/ 目录不存在 ({components_base})，跳过组件扫描")
            self._components = {}
            return

        discovered = {}
        # 扫描外部组件目录
        for entry_name in os.listdir(components_base):
            comp_dir = os.path.join(components_base, entry_name)
            manifest_path = os.path.join(comp_dir, 'manifest.yaml')
            component_py = os.path.join(comp_dir, 'component.py')
            if not (os.path.isdir(comp_dir) and os.path.exists(manifest_path) and os.path.exists(component_py)):
                continue

            # sim-inference 是内置组件，走 backend.components.sim_component
            if entry_name == 'sim-inference':
                try:
                    from backend.components.sim_component import COMPONENT_ENTRY as _se
                    manifest_comp = _se['manifest'].get('component', _se['manifest'])
                    discovered[_se['id']] = {
                        'id': _se['id'],
                        'name': _se.get('name', _se['id']),
                        'version': _se.get('version', '1.0.0'),
                        'type': manifest_comp.get('component_type', 'inference'),
                        'config_schema': _se['manifest'].get('config_schema', {}),
                        'collector_requirements': _se['manifest'].get('collector_requirements', {}),
                        'io': _se['manifest'].get('io', {}),
                        '_component_class': _se.get('component_class'),
                    }
                    logger.info(f"Platform: 发现内置组件 sim-inference")
                except Exception as e:
                    logger.error(f"Platform: 加载内置 sim-inference 失败: {e}")
                continue

            try:
                # 加载 manifest.yaml
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = yaml.safe_load(f)

                # 动态加载 component.py
                # 需要临时把 backend/ 的父目录加入 sys.path 以便 "from backend.xxx" 可用
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)

                if comp_dir not in sys.path:
                    sys.path.insert(0, comp_dir)
                spec = importlib.util.spec_from_file_location("_comp", component_py)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                if not hasattr(mod, 'COMPONENT_ENTRY'):
                    logger.warning(f"Platform: {entry_name}/component.py 未导出 COMPONENT_ENTRY，跳过")
                    for _p in [comp_dir, project_root]:
                        if _p in sys.path:
                            sys.path.remove(_p)
                    continue

                entry = mod.COMPONENT_ENTRY
                comp_id = entry['id']
                manifest_comp = manifest.get('component', manifest)

                discovered[comp_id] = {
                    'id': comp_id,
                    'name': entry.get('name', comp_id),
                    'version': entry.get('version', '1.0.0'),
                    'type': manifest_comp.get('component_type', 'inference'),
                    'config_schema': manifest.get('config_schema', {}),
                    'collector_requirements': manifest.get('collector_requirements', {}),
                    'io': manifest.get('io', {}),
                    '_component_class': entry.get('component_class'),
                }
                logger.info(f"Platform: 发现组件 {comp_id} (path={entry_name})")

                for _p in [comp_dir, project_root]:
                    if _p in sys.path:
                        sys.path.remove(_p)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    f"Platform: 加载组件 {entry_name} 失败: {type(e).__name__} {e}", exc_info=True
                )
                continue

        self._components = discovered
        logger.info(f"Platform: 组件扫描完成，共注册 {len(discovered)} 个组件: {list(discovered.keys())}")

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

        # 组件实例：从已注册的组件中取 _component_class 动态实例化
        comp_info = self._components.get(component_id, {})
        comp_cls = comp_info.get('_component_class')
        if not comp_cls:
            return {"error": f"组件 {component_id} 未找到或不支持实例化", "code": 1003}
        component_instance = comp_cls()
        device = "cpu"  # 实际通过 ONNX Runtime 检测

        if not framework.load_component(component_id, component_instance, config, device):
            return {"error": "组件初始化失败", "code": 1002}

        framework.start()

        # 从 http://localhost:5101 提取 host（TCP 连接目标）
        collector_host = self._collector_base_url.replace("http://", "").split(":")[0] or "localhost"

        # 启动采集（通知 Collector 开始）— 必须先于 TCP 连接，确保 Collector 侧 TCP server 已就绪
        conn_result = await self._collector_start(session_id, {**merged_config, **config})
        if conn_result.get("status") != "success":
            return {"error": f"Collector 启动失败: {conn_result.get('detail', 'Unknown error')}", "code": 1004}

        # 保存会话（collector 启动成功后才创建）
        self._sessions[session_id] = {
            "session_id": session_id,
            "collector_session_id": conn_result.get("collector_session_id", session_id),
            "status": "running",
            "component_id": component_id,
            "current_config": {**merged_config, **config},
            "started_at": datetime.now(timezone.utc).isoformat(),
            "warnings": warnings,
            "device_info": self._get_device_info()
        }

        # 建立数据通道（全部 UDP，删除 TCP）
        # UDP 无连接，无队列积压，无发送超时问题
        collector_type = "udp"
        collector_io = CollectorIOClient(
            collector_host=collector_host,
            tcp_port=6103,
            udp_port=6104,
            collector_type=collector_type,
        )
        connected = await collector_io.connect(framework, session_id)
        if connected:
            self._collector_io_client[session_id] = collector_io
            logger.info(f"Platform: CollectorIOClient 已连接 (session={session_id})")
        else:
            logger.warning(f"Platform: CollectorIOClient 连接失败 (session={session_id})")

        self._frameworks[session_id] = framework
        self._inference_history[session_id] = []

        # 启动定期 stats 推送（每秒一次，不管有没有帧进来）
        task = asyncio.create_task(self._run_stats_loop(session_id))
        self._stats_tasks[session_id] = task

        # 构建 config 响应结构（与前端 updateConfigDisplay 期望一致）
        current_cfg = self._sessions[session_id]["current_config"]
        cfg_schema = component.get("config_schema", {})
        # pluto-repeater 模式下 uri 取 device_id（固定为 "pluto-repeater"），否则从 config 取
        collector_type = current_cfg.get("collector_type", "")
        _uri = current_cfg.get("uri") or ("pluto-repeater" if collector_type == "pluto-repeater" else None)
        inference_config = {
            "component_id": component_id,
            **{k: v for k, v in current_cfg.items() if k in cfg_schema}
        }
        collector_config = {
            "center_freq_hz": current_cfg.get("frequency"),
            "sample_rate_hz": current_cfg.get("sample_rate"),
            "gain_db": current_cfg.get("gain"),
            "bandwidth_hz": current_cfg.get("bandwidth") or current_cfg.get("buffer_size"),
            "uri": _uri,
            **{k: v for k, v in current_cfg.items() if k not in cfg_schema and k not in ("frequency", "sample_rate", "gain", "bandwidth", "buffer_size", "uri")}
        }
        return {
            "session_id": session_id,
            "status": "running",
            "config": {
                "component_id": component_id,
                "inference_config": inference_config,
                "collector_config": collector_config,
            },
            "warnings": warnings,
            "collector_connection": conn_result["status"],
            **({"collector_error": conn_result["detail"]} if conn_result["status"] == "failed" else {})
        }

    async def stop_session(self, session_id: str) -> dict:
        """停止会话"""
        if session_id not in self._sessions:
            return {"error": "会话不存在", "code": 1003}

        # 停止定期 stats 推送
        task = self._stats_tasks.pop(session_id, None)
        if task:
            task.cancel()

        # 先从 _frameworks 取出 framework，再获取 stats（顺序重要）
        framework = self._frameworks.pop(session_id, None)
        stats = framework.get_stats() if framework else {}
        if framework:
            framework.stop()

        # 断开 Collector Socket.IO 客户端
        client = self._collector_io_client.pop(session_id, None)
        if client:
            await client.disconnect()

        session = self._sessions[session_id]
        session["status"] = "stopped"
        session["stopped_at"] = datetime.now(timezone.utc).isoformat()

        await self._collector_stop(session_id)

        return {
            "status": "stopped",
            "stats": {
                "frames_received": stats.get("frames_received", 0),
                "frames_dropped": stats.get("frames_dropped", 0),
                "duration_seconds": self._session_duration(session),
                "inference_count": stats.get("inference_count", 0),
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
            "component_id": component_id,
            **{k: v for k, v in user_config.items()
               if k in component.get("config_schema", {})},
        }
        # 补充组件声明的 default 值（用户未指定时）
        for k, schema in component.get("config_schema", {}).items():
            if k not in inference_config and "default" in schema:
                inference_config[k] = schema["default"]

        # 采集器配置（字段名映射：内部名 → 前端期望名）
        # pluto-repeater 模式下 uri 固定为 "pluto-repeater"
        _uri = user_config.get("uri") or ("pluto-repeater" if user_config.get("collector_type") == "pluto-repeater" else None)
        collector_config = {
            "center_freq_hz": user_config.get("frequency"),
            "sample_rate_hz": user_config.get("sample_rate"),
            "gain_db": user_config.get("gain"),
            "bandwidth_hz": user_config.get("bandwidth") or user_config.get("buffer_size"),
            "uri": _uri,
            "device_uri": _uri,
            **{k: v for k, v in user_config.items()
               if k not in ("frequency", "sample_rate", "gain", "bandwidth", "buffer_size", "uri")
               and k not in component.get("config_schema", {})},
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
        comp = self._components[component_id].copy()
        comp.pop("_component_class", None)  # 移除不可序列化的类对象
        return comp

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
            # collector_type 显式指定模式 > iq_file_path 隐式推断
            collector_type = config.get("collector_type", "")
            if collector_type == "pluto-repeater":
                collector_mode = "repeater"
            elif config.get("iq_file_path"):
                collector_mode = "repeater"
            else:
                collector_mode = "pluto"
            # 全部使用 UDP（删除 TCP 路径）
            config = {**config, "collector_type": "udp"}

            resp = await asyncio.to_thread(self._requests.post, f"{self._collector_base_url}/api/v1/collector/start", json={
                "session_id": session_id,
                "mode": collector_mode,
                "config": config,
                "force": True
            }, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    # 提取 Collector 分配的 session_id（UUID）
                    collector_session_id = data.get("session_id", session_id)
                    return {"status": "success", "collector_session_id": collector_session_id}
                return {"status": "failed", "detail": data.get("message", "Unknown error")}
            if resp.status_code == 409:
                # 冲突：先 reset 再重试
                logger.warning(f"Collector start conflict (409) for session {session_id}, attempting reset...")
                try:
                    await asyncio.to_thread(
                        self._requests.post,
                        f"{self._collector_base_url}/api/v1/collector/reset",
                        timeout=5
                    )
                except Exception as reset_err:
                    logger.warning(f"Collector reset call failed: {reset_err}")
                # 重试 start
                resp = await asyncio.to_thread(self._requests.post, f"{self._collector_base_url}/api/v1/collector/start", json={
                    "session_id": session_id,
                    "mode": collector_mode,
                    "config": config,
                    "force": True
                }, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        collector_session_id = data.get("session_id", session_id)
                        return {"status": "success", "collector_session_id": collector_session_id}
                    return {"status": "failed", "detail": data.get("message", "Unknown error")}
            return {"status": "failed", "detail": f"HTTP {resp.status_code}: {resp.text[:100]}"}
        except Exception as e:
            return {"status": "failed", "detail": f"{type(e).__name__}: {e}"}

    async def _collector_stop(self, session_id: str) -> None:
        """通知 Collector 停止采集"""
        try:
            # 必须用 Collector 分配的 session_id（UUID），不能用 Platform 的 sess_xxx
            collector_session_id = self._sessions.get(session_id, {}).get("collector_session_id", session_id)
            await asyncio.to_thread(self._requests.post, f"{self._collector_base_url}/api/v1/collector/stop", json={"session_id": collector_session_id}, timeout=10)
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
        """推理结果回调（从框架线程调用）"""
        # 统一前端期望的字段格式
        self._format_result(result)

        # 保存历史
        if session_id in self._inference_history:
            history = self._inference_history[session_id]
            history.append(result)
            if len(history) > 1000:
                history[:] = history[-1000:]

        # 推送 Socket.IO（跨线程调度）
        loop = self._loop
        if loop is None:
            logger.error("[_on_result] no event loop available")
            return
        asyncio.run_coroutine_threadsafe(
            self.socketio_server.emit_inference_result(session_id, {
                "session_id": session_id,
                **result
            }),
            loop
        )

    def _format_result(self, result: dict) -> None:
        """
        统一推理结果字段格式，适配前端 renderResultsTable 期望的字段。
        - rfuav-two-stage: 有 center_freq_mhz 或 detections 含 stage2_conf
        - sim-inference: 有 detections.frequency (MHz) 和 power_dbm
        """
        detections = result.get('detections', [])
        is_rfuav = result.get('center_freq_mhz') is not None or (
            detections and any(d.get('stage2_conf') is not None for d in detections)
        )

        if is_rfuav:
            # rfuav-two-stage: 从 detections 转换
            if detections:
                best = max(detections, key=lambda d: d.get('stage2_conf', 0))
                result['is_drone'] = True
                result['drone_prob'] = best.get('stage2_conf', 0.0)
                result['noise_prob'] = 1.0 - best.get('stage2_conf', 0.0)
            else:
                result['is_drone'] = False
                result['drone_prob'] = 0.0
                result['noise_prob'] = 1.0
            # rfuav 字段映射
            result['freq_mhz'] = result.get('center_freq_mhz')
            # power_db 直接透传
            result['process_time_ms'] = (result.get('debug', {}).get('inference_time_ms') or 0)
        else:
            # sim-inference: 透传已有字段，补充前端期望的字段名
            if detections:
                best_det = detections[0]
                result['freq_mhz'] = best_det.get('frequency')
                result['power_db'] = best_det.get('power_dbm')
                result['is_drone'] = True
                result['drone_prob'] = best_det.get('confidence')
                result['noise_prob'] = 1.0 - best_det.get('confidence', 0.0)
            else:
                result['is_drone'] = False
                result['drone_prob'] = 0.0
                result['noise_prob'] = 1.0
            result['process_time_ms'] = (result.get('debug', {}).get('inference_time_ms') or 0)

    async def _run_stats_loop(self, session_id: str) -> None:
        """每秒推送一次 collector_stats，不管有没有帧进来"""
        while True:
            await asyncio.sleep(1.0)
            framework = self._frameworks.get(session_id)
            if not framework:
                break
            try:
                await self._on_stats(session_id, {})
            except Exception as e:
                logger.error(f"Platform: stats loop error for {session_id}: {e}")

    async def _on_stats(self, session_id: str, stats: dict) -> None:
        """统计回调"""
        framework = self._frameworks.get(session_id)
        if not framework:
            return
        qstats = framework.get_stats()
        await self.socketio_server.emit_collector_stats(session_id, {
            "session_id": session_id,
            "frames_per_second": round(qstats.get("inference_count", 0) / max(1, qstats.get("frames_received", 1)), 2),
            "dropped_rate": qstats.get("dropped_rate", 0),
            "buffer_level": qstats.get("buffer_level", 0),
            "total_frames": qstats.get("frames_received", 0),
            "total_dropped": qstats.get("frames_dropped", 0)
        })

    def _on_error(self, session_id: str, message: str) -> None:
        """错误回调（从框架线程调用）"""
        loop = self._loop
        if loop is None:
            logger.error("[_on_error] no event loop available")
            return
        asyncio.run_coroutine_threadsafe(
            self.socketio_server.emit_error(2001, message, session_id),
            loop
        )

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
    platform._loop = asyncio.get_running_loop()
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
from .api import session, components, devices, collector_proxy

app.include_router(session.router)
app.include_router(components.router)
app.include_router(devices.router)
app.include_router(collector_proxy.router)

# ---- Collector stats REST API（替代 Socket.IO 推送）------------------
@app.get("/api/v1/collector/stats")
async def get_collector_stats():
    """
    GET /api/v1/collector/stats
    返回当前采集器状态（buffer_level、fps、frames 等），
    数据格式与之前 Socket.IO 的 collector_stats 事件一致。
    """
    # 找到最近一个活跃的 session
    session_id = None
    if platform._sessions and platform._frameworks:
        for sid in platform._sessions:
            framework = platform._frameworks.get(sid)
            if framework is not None:
                session_id = sid
                break

    if not session_id:
        return {"status": "ok", "stats": {
            "frames_per_second": 0,
            "dropped_rate": 0,
            "buffer_level": 0,
            "total_frames": 0,
            "total_dropped": 0,
        }}

    framework = platform._frameworks.get(session_id)
    qstats = framework.get_stats() if framework else {}
    return {
        "status": "ok",
        "stats": {
            "session_id": session_id,
            "frames_per_second": round(qstats.get("inference_count", 0) / max(1, qstats.get("frames_received", 1)), 2),
            "dropped_rate": qstats.get("dropped_rate", 0),
            "buffer_level": qstats.get("buffer_level", 0),
            "total_frames": qstats.get("frames_received", 0),
            "total_dropped": qstats.get("frames_dropped", 0),
        }
    }

# 挂载 Socket.IO 到 /socket.io 路径（必须在路由注册后）
def create_socketio_app(fastapi_app: Any) -> Any:
    """
    创建 Socket.IO ASGI 应用并挂载到 FastAPI 的 /socket.io 路径。
    这样 FastAPI 处理所有其他路由（/api/*），Socket.IO 处理 /socket.io/*
    """
    import socketio
    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )
    namespace = PlatformNamespace("/", platform)
    sio.register_namespace(namespace)
    platform.socketio_server._sio = sio
    platform.socketio_server._namespace = namespace

    # 将 FastAPI 作为 other_asgi_app，非 Socket.IO 流量由 FastAPI 处理
    sio_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

    # 挂载到 FastAPI 的 /socket.io 路径
    fastapi_app.mount('/socket.io', sio_app, name="socketio")

    logger.info("Socket.IO 已挂载到 /socket.io")
    return sio


create_socketio_app(app)


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