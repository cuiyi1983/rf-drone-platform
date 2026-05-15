# RF-Drone-Platform

> 无人机探测平台 — 组件化射频推理与IQ采数系统

## 系统定位

**核心能力**：
- 推理能力：动态加载推理组件，支持 NPU/GPU/CPU 自适应
- 采数能力：Pluto 设备管理 + IQ 数据模拟 + 扫频策略
- 组件化架构：训练方案变更不需要修改平台代码

**不属于平台职责**：
- 训练过程（归 RF-Training 仓库）
- 推理组件内部实现（归组件自己）
- Pipeline 编排逻辑（由组件自行决定）

---

## 架构原则【技术总监守护】

| 优先级 | 原则 | 说明 |
|---|---|---|
| 【最高】| 接口不变性 | 已发布接口禁止破坏性变更 |
| 【最高】| 单向数据流 | 采集→推理→展示，不允许反向依赖 |
| 【高】| 模块自治 | 各模块内部实现自主，故障隔离 |
| 【高】| 接口协议化 | 所有跨模块交互通过显式接口 |
| 【中】| 可降级性 | 单组件故障时系统能降级运行 |
| 【中】| 配置外部化 | 业务参数禁止硬编码 |

详见：[ARCHITECTURE.md](ARCHITECTURE.md)

---

## 核心架构

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Web UI)                      │
│         Socket.IO（推理结果/调试信息/状态变更）             │
└────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│              Platform Backend (python-socketio)          │
│  ┌─────────────────┐  ┌──────────────────────────┐   │
│  │  Config Manager  │  │   Inference Framework     │   │
│  │  (外部配置)      │  │  ┌──────────────────────┐ │   │
│  └─────────────────┘  │  │  │   Frame Queue        │ │   │
│                       │  │  │   (环形缓冲, 3-5秒) │ │   │
│                       │  │  └──────────┬───────────┘ │   │
│                       │  │  ┌──────────▼───────────┐ │   │
│                       │  │  │  Component Lifecycle│ │   │
│                       │  │  │  (加载/初始化/崩溃恢复)│ │   │
│                       │  │  └──────────┬───────────┘ │   │
│                       │  │  ┌──────────▼───────────┐ │   │
│                       │  │  │   Device Selector    │ │   │
│                       │  │  │  (NPU→GPU→CPU自动)  │ │   │
│                       │  │  └──────────┬───────────┘ │   │
│                       │  │  ┌──────────▼───────────┐ │   │
│                       │  │  │     Component       │ │   │
│                       │  │  │  ┌──────────────┐   │ │   │
│                       │  │  │  │Model Adapter │   │ │   │
│                       │  │  │  │ - 模型加载   │   │ │   │
│                       │  │  │  │ - STFT       │   │ │   │
│                       │  │  │  │ - 推理执行   │   │ │   │
│                       │  │  │  └──────────────┘   │ │   │
│                       │  │  └────────────────────┘ │   │
│                       │  └──────────────────────────┘   │
└───────────────────────┬─────────────────────────────────┘
                        │ IQ原始数据帧
                        ▼
┌─────────────────────────────────────────────────────────┐
│           Collector Service (独立服务器部署)             │
│  ┌───────────────┐  ┌────────────────┐               │
│  │  Pluto        │  │  Burst Buffer   │               │
│  │  Manager      │  │  (帧封装)       │               │
│  └───────────────┘  └────────────────┘               │
│  ┌───────────────┐  ┌────────────────┐               │
│  │  IQ Simulator │  │  Scan Strategy  │               │
│  │  (模拟数据)   │  │  (频点轮询)    │               │
│  └───────────────┘  └────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

---

## Inference Framework（推理框架）

**职责**：
- 帧队列管理（消费不了就扔，不背压）
- 组件生命周期（加载、初始化、崩溃感知与恢复）
- 设备选择（NPU → GPU → CPU）
- 调试信息统一格式化 + 日志保存

**关键约束**：
- 不感知组件内部逻辑
- 不感知 Pipeline
- 消费不了就扔（不需要背压）

---

## 推理组件

### 组件自描述

每个推理组件必须包含 `manifest.yaml`：

```yaml
component:
  id: "rfuav-two-stage"
  name: "RFUAV 两阶段推理"
  version: "2.0.0"
  type: "inference"

capability:
  device_support: [npu, gpu, cpu]

collector_requirements:
  min_data_points: 60000            # 模型需要的数据点数

io:
  input:
    - name: iq_frame
      type: dict
  output:
    - name: detections
    - name: debug      # 统一格式，平台原样展示

config_schema:
  confidence_threshold:
    type: number
    default: 0.5
```

### 设备选择

```
优先级: NPU → GPU → CPU
- 推理框架（ONNX Runtime）自动检测可用设备
- 组件 manifest.device_support 仅作为声明
```

### 组件打包格式

组件打包为 `.zip`，包含：
```
my_component.zip/
├── manifest.yaml         # 必选：组件自描述
├── model.onnx          # 模型文件
├── inference.py        # 推理实现
└── requirements.txt   # 依赖（如有）
```

---

## 数据流

```
Collector ──IQ帧──→ Platform ──转发──→ Inference Framework
                                              │
                                              ▼
                                    Frame Queue（环形缓冲）
                                    消费不了就扔
                                              │
                                              ▼
                                    Component.infer(iq_frame)
                                              │
                                              ▼
                                    result + debug
                                              │
                                              ▼
                                    Socket.IO → Frontend
```

---

## 接口协议

所有跨模块接口定义在 `interfaces/` 目录：

| 文件 | 版本 | 用途 |
|---|---|---|
| `interfaces/component-manifest.yaml` | v2.0 | 推理组件自描述协议 |
| `interfaces/collector-api.yaml` | v2.0 | 采集模块 API |
| `interfaces/platform-collector.yaml` | v2.0 | 平台↔采集协同协议 |

---

## 模型

训练产出见 [RF-Training](https://github.com/cuiyi1983/RF-Training) 仓库。

| 模型 | 下载 | 用途 |
|---|---|---|
| stage1_yolo_v2.onnx | http://yoyo-chat.cn:8000/stage1_yolo_v2.onnx | 无人机检测 |
| stage2_resnet152.onnx | http://yoyo-chat.cn:8000/stage2_resnet152.onnx | 7机型分类 |

---

## 目录结构

```
rf-drone-platform/
├── interfaces/           # 接口协议定义（技术总监维护）
├── collector/          # 采集模块（Pluto + Simulator）
├── inference/          # 推理模块
│   ├── framework/     # Inference Framework（框架）
│   └── plugins/        # 推理组件（动态加载）
├── ui/                 # Web 前端
└── tests/              # 集成测试
```

---

## Agent 团队

| Agent | 职责 |
|---|---|
| **技术总监** | 架构守护、接口审批、约束跟踪 |
| **小边** | Model Adapter + Inference Framework |
| **小采采** | Collector Service + Pluto 管理 |
| **小页** | Web UI + Socket.IO 前端 |
| **小崔崔** | 训练模块（在 RF-Training 仓库）|

---

## 快速启动

### 环境准备

```bash
# 安装依赖（Python 3.10+）
pip install -e ".[dev]
```

### 启动服务

**Collector Service（端口 8081）**

```bash
# 方式1：直接运行
python -m collector.api

# 方式2：指定端口
COLLECTOR_HTTP_PORT=8081 python -m collector.api
```

**Platform Backend（端口 8080）**

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

### 前端

直接用浏览器打开 `frontend/index.html` 即可。

离线模式下（后端未启动）会自动切换到 Mock 数据流，可验证 UI 功能。

### 端口约定

| 服务 | 端口 | 说明 |
|---|---|---|
| Platform Backend | 8080 | REST API + Socket.IO |
| Collector Service | 8081 | 采集模块 API（独立部署）|