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
│         动态表单 / 推理结果 / 调试信息 / Socket.IO         │
└────────────────┬────────────────────────────────────────┘
                   │ Socket.IO（自动降级WebSocket）
                   ↓
┌─────────────────────────────────────────────────────────┐
│              Platform Backend (python-socketio)             │
│  ┌─────────────────┐                                   │
│  │  Plugin Manager │  ← 加载 .zip 推理组件             │
│  │  (动态加载)     │    读取 manifest                  │
│  └────────┬────────┘                                   │
│  ┌────────▼────────┐  ┌─────────────────┐              │
│  │  Component      │  │  Config Manager │              │
│  │  Executor       │  │  (外部配置)     │              │
│  │  (异步/多线程) │  └─────────────────┘              │
│  └────────┬────────┘                                   │
│  ┌────────▼────────┐                                   │
│  │  Device        │  ← NPU → GPU → CPU 自动选择       │
│  │  Selector      │                                   │
│  └─────────────────┘                                   │
└────────────────┬────────────────────────────────────────┘
                   │ HTTP
                   ↓
┌─────────────────────────────────────────────────────────┐
│           Collector Service (独立服务器部署)                │
│  ┌───────────────┐  ┌────────────────┐               │
│  │  Pluto        │  │  Scan Strategy  │               │
│  │  Manager      │  │  Manager        │               │
│  └───────────────┘  └────────────────┘               │
│  ┌───────────────┐                                    │
│  │  IQ Simulator │  ← 支持导入已有 IQ 数据              │
│  └───────────────┘                                    │
└─────────────────────────────────────────────────────────┘
```

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
  async_inference: true

collector_requirements:
  min_sample_rate: 60e6
  frequency_range: [5.7e9, 6.0e9]
  min_burst_count: 200

collector_config_template:
  frequencies: [5760, 5775, 5800, 5825, 5850]
  sample_rate: 60e6
  burst_count: 200
```

### 设备选择

```
优先级: NPU → GPU → CPU
- 组件声明支持 NPU/GPU/CPU
- 平台按优先级尝试验证
- 首个验证成功者被选中
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

## 接口协议

所有跨模块接口定义在 `interfaces/` 目录：

| 文件 | 用途 |
|---|---|
| `interfaces/component-manifest.yaml` | 推理组件自描述协议 |
| `interfaces/collector-api.yaml` | 采集模块 API |
| `interfaces/platform-collector.yaml` | 平台↔采集协同协议 |

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
├── inference/          # 推理模块（待实现）
│   ├── framework/       # 推理框架核心
│   └── plugins/         # 推理组件（动态加载）
├── ui/                 # Web 前端
└── tests/              # 集成测试
```

---

## Agent 团队

| Agent | 职责 |
|---|---|
| **技术总监** | 架构守护、接口审批、原则管理 |
| **小边** | 推理模块实现 |
| **小采采** | 采集模块实现 |
| **小页** | Web UI 实现 |
| **小崔崔** | 训练模块（在 RF-Training 仓库）|
