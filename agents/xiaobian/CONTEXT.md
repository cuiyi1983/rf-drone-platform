# xiaobian/CONTEXT.md

## 当前进度

### 已完成 ✅

**第一阶段交付（全部完成）**

1. **Config Manager** (`backend/config_manager.py`)
   - 配置合并：Capability Range ∩ Component 建议 → 最终配置
   - 越界降级到边界 + WARNING
   - 缺失用 capability default 补齐
   - 验证接口 `validate_final_config()`
   - 14 个单元测试，全部通过

2. **Frame Queue** (`backend/inference/frame_queue.py`)
   - 环形缓冲，固定容量 100 帧
   - 队列满丢弃最旧帧（drop oldest）
   - 线程安全（threading.Condition）
   - `dropped_rate` 统计
   - 9 个单元测试，全部通过

3. **Inference Framework** (`backend/inference/framework.py`)
   - 组件生命周期（load/unload/update_config）
   - 推理循环（独立线程）
   - 回调机制（result/stats/error）
   - 健康检查 `health_check()`
   - MockComponent 实现（实际组件从 .zip 加载）

4. **REST API** (`backend/api/*.py`)
   - `POST /api/v1/session/start` - 启动会话
   - `POST /api/v1/session/stop` - 停止会话
   - `GET  /api/v1/session/status` - 查询状态（单/全部）
   - `PATCH /api/v1/session/{id}/config` - 更新配置
   - `GET  /api/v1/components` - 组件列表
   - `GET  /api/v1/components/{id}` - 组件详情
   - `GET  /api/v1/components/{id}/config-schema` - 配置Schema
   - `GET  /api/v1/devices` - 设备列表
   - `GET  /api/v1/devices/{id}/capabilities` - 设备能力
   - `GET  /api/v1/simulator/metadata` - 模拟器状态
   - 17 个单元测试，全部通过

5. **Socket.IO Server** (`backend/socketio/server.py`)
   - 命名空间 `/`
   - 推送：`inference_result`, `collector_stats`, `device_status`, `error`
   - 前台→后台：`subscribe`, `unsubscribe`, `get_history`
   - 房间管理（按 session_id 分组）
   - 6 个单元测试，全部通过

6. **FastAPI 入口** (`backend/main.py`)
   - Platform 协调器（会话/组件/设备管理）
   - 与 Collector HTTP 通信
   - MockComponent + Mock设备列表
   - 启动时自动探测 Collector capabilities
   - 推理历史缓存（内存，1000条）

7. **单元测试** (`tests/unit/`)
   - 94 个测试，全部通过

### 决策记录

1. **ConfigManager 默认能力** - 当无法从 Collector 获取时，使用 ARCHITECTURE.md 中的 Pluto SDR 实测值作为默认值
2. **会话状态 404** - `get_session_status` 返回 `{"error": ...}` 时 API 层转 404 而非 200
3. **Socket.IO `emit`** - python-socketio 5.x 中 `emit` 是 sio 实例方法，不从 socketio 模块导入

### 待处理

- 第二阶段：真实组件加载（.zip）、Collector 真实集成、持久化存储
- 技术评审：待技术总监审视架构遵从度
- 代码检视：专家代码检视

### 技术债务

- FastAPI `@app.on_event("startup")` 已标 deprecated，应改用 lifespan handler
- MockComponent 应替换为真实组件加载器