# UI 重构 — 观测页面 & 配置页面 + 后台任务清单

> 来源：与崔老板讨论（2026-05-19）
> 目标：参考 detector 仓 UI，重构 platform 仓前端 + 补全后台接口

---

## 一、观测页面

### 1.1 顶部 statusbar（参考 detector: `statusbar`）
| 元素 | 状态 | 说明 |
|---|---|---|
| Pluto 连接状态（dot + 文字） | ✅ 保留 | 绿点/红灯 + 连接状态文字 |
| 模型加载状况 | ✅ 保留 | 显示已加载模型名称 |
| socket 状态 | ❌ 去除 | detector 中 `dbg` 元素 |

### 1.2 实时推理统计表（替代原"设备状态" card）
- 表格形式，每行 = 一次推理，保留**最近 10 条**
- 列 = 推理组件动态上报的字段
- 支持勾选要显示的参数（动态表头）
- 参考 detector 的 `rtable` 样式

### 1.3 去除的区域
- ~~检测结果面板~~（右上大图标 + 概率条）
- ~~推理历史~~（左下时间线列表）
- ~~采集帧率 / 丢帧率 / 总帧数~~（移入缓冲区状态监控）

### 1.4 新增展示区
| 区块 | 内容来源 | 状态 |
|---|---|---|
| 模型当前配置参数 | 推理组件上报 | 动态 |
| 采数当前配置参数 | 采集组件上报 | 动态 |
| Platform 缓冲区状态监控 | Platform 管理，定期刷新 | 预留内容 |

### 1.5 控制面板
| 元素 | 状态 |
|---|---|
| 启动采数 | ✅ 保留 |
| 停止采数 | ✅ 保留 |
| 单次推理 | ❌ 去除 |
| IQ 录制勾选框 | ✅ 勾选 ✅ 置灰不可选 |

---

## 二、配置页面（参考 detector: `pg-cfg`，重构如下）

### 2.1 推理组件配置（替代原"模型配置"）
| 元素 | 说明 |
|---|---|
| 推理组件下拉框 | 选择要加载的推理组件（来自 `/api/v1/components`） |
| 组件参数清单 | 选中组件后**动态加载**该组件的 `config_schema`，呈现可配置参数 |
| 参数预填值 | 使用组件 schema 中的 `default`（推荐配置）作为默认值 |
| 确认加载按钮 | 点击后调用 `/api/v1/session/start` 加载组件 |
| 就近成功提示 | 加载成功后直接在按钮附近显示 ✅ |

### 2.2 采集器配置（替代原"采集器配置"）
| 元素 | 说明 |
|---|---|
| 采集器下拉框 | 展示 collector 上报的设备列表（来自 collector 扫描结果） |
| 扫描设备按钮 | 点击触发 collector 设备扫描（调用 `/api/v1/devices/refresh`） |
| 采集器参数清单 | 选中某采集器后**动态加载**该采集器提供的可配置参数（频率/采样率/增益/带宽等） |
| 参数预填匹配逻辑 | 参数值优先使用**当前已加载模型的推荐配置**；若模型无该参数，则使用 collector 上报的默认值 |
| 确认连接按钮 | 点击后建立连接，显示连接结果（成功/失败） |

### 2.3 去除的内容
- ~~调试信息区块~~（detector 原底部可折叠调试面板）
- ~~单独的配置 Tab 页~~（配置不再以 Tab 呈现）

### 2.4 页面结构
```
配置页面（单页，无 Tab）
├── 推理组件配置区
│   ├── 组件下拉框
│   ├── 动态参数表单
│   └── 确认加载 + 提示
└── 采集器配置区
    ├── 采集器下拉框 + 扫描按钮
    ├── 动态参数表单（自动匹配模型推荐值）
    └── 确认连接 + 提示
```

---

## 三、后台接口任务清单

### 3.1 Collector 模块（归小采采）

| # | 任务 | 说明 | 影响接口 |
|---|---|---|---|
| C-1 | 新增设备连接接口 | 支持设备 URI 选择，在 start 前先建立连接 | `POST /collector/connect`（新增） |
| C-2 | start 接口改造 | 仅负责启动采集，设备未连接时自动连接 | `POST /collector/start`（修改） |
| C-3 | disconnect 接口 | 断开设备连接 | `POST /collector/disconnect`（新增） |

### 3.2 Platform Backend 模块（主 Agent）

| # | 任务 | 说明 | 影响接口 |
|---|---|---|---|
| P-1 | 会话状态返回模型信息 | `GET /api/v1/session/status` 增加当前组件名称/版本 | 修改现有接口 |
| P-2 | 新增当前配置查询接口 | 返回当前会话的推理组件配置 + 采集器配置 | `GET /api/v1/session/{id}/config`（新增） |
| P-3 | 连接结果返回 | collector connect 接口结果透传至前端 | 修改 session start 响应 |
| P-4 | Socket.IO 事件规范补充 | 规范 inference_result 字段，前端动态解析表头 | 协议文档 |

### 3.3 模拟推理组件（归主 Agent）

| # | 任务 | 说明 |
|---|---|---|
| M-1 | 实现模拟推理组件 | `backend/components/mock_component.py`，实现 `IInferenceComponent` 接口，推理过程跳过，随机输出结果，与真实组件无区别 |

---

## 四、任务汇总

### 前端（UI 重构）
- [ ] 观测页面：顶部 statusbar（Pluto状态 + 模型状况，去 socket）
- [ ] 观测页面：实时推理统计表（动态列，最近10条）
- [ ] 观测页面：去除检测结果面板 + 推理历史
- [ ] 观测页面：新增模型配置参数展示区
- [ ] 观测页面：新增采数配置参数展示区
- [ ] 观测页面：新增 Platform 缓冲区状态监控
- [ ] 观测页面：控制面板（保留启动/停止，去单次推理，IQ录制置灰）
- [ ] 配置页面：推理组件下拉框 + 动态参数清单 + 确认加载 + 成功提示
- [ ] 配置页面：采集器下拉框 + 扫描按钮 + 动态参数清单 + 确认连接 + 结果提示
- [ ] 配置页面：去除调试信息
- [ ] 配置页面：参数值优先回填模型推荐配置，无则用 collector 默认值
- [ ] 全局：统一风格（参考 detector 深色主题）

### Collector（归小采采）
- [ ] C-1：新增 `POST /collector/connect` 设备连接接口
- [ ] C-2：改造 `POST /collector/start` 支持设备未连接场景
- [ ] C-3：新增 `POST /collector/disconnect` 断开设备接口

### Platform Backend（归主 Agent）
- [ ] P-1：会话状态返回组件名称/版本
- [ ] P-2：新增 `GET /api/v1/session/{id}/config` 当前配置查询
- [ ] P-3：session start 返回连接结果
- [ ] P-4：Socket.IO inference_result 字段规范文档
- [ ] M-1：实现模拟推理组件 `backend/components/mock_component.py`

---

## 五、参考来源

- **Detector UI 模板**：`/projects/low-altitude-monitoring/inference/ui/templates/index.html`
- **Detector UI JS**：`/projects/low-altitude-monitoring/inference/ui/static/js/main.js`
- **Detector 样式**：Bootstrap 5 + 自定义 CSS（深色主题，色值：--bg=#0f0f23, --card=#1a1a2e, --acc=#e94560, --acc2=#00d9ff）