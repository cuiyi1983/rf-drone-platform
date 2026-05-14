# RF-Drone-Platform 架构

> 版本 v2.4 — 2026-05-14
> 状态：已确立核心决策

---

## 系统全貌

```
┌──────────────────────────────────────────────────────────────────┐
│                         Frontend (Web UI)                         │
│                                                                   │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │  Socket.IO Client  │  REST API Client                      │   │
│   │  - inference_result│  - POST /api/v1/session/start         │   │
│   │  - collector_stats │  - GET  /api/v1/session/status        │   │
│   │  - device_status   │  - GET  /api/v1/components            │   │
│   │  - error           │  - GET  /api/v1/devices               │   │
│   └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                       Platform Backend                            │
│   REST API (控制面)           │  Socket.IO Server (数据面推送)      │
│   ─────────────────────────  │  ───────────────────────────────  │
│   POST /session/start         │  → inference_result (帧)           │
│   POST /session/stop         │  → collector_stats (1s/次)         │
│   GET  /session/status       │  → device_status (实时)            │
│   GET  /components           │  → error (实时)                    │
│   GET  /devices              │                                    │
│                                                                   │
│  ┌────────────────┐         ┌────────────────────────────────┐  │
│  │  Config Manager │         │     Inference Framework        │  │
│  │  (外部配置)     │         │  ┌──────────────────────────┐  │  │
│  └────────────────┘         │  │    Frame Queue           │  │  │
│                             │  │    (环形缓冲, 3-5秒)     │  │  │
│                             │  └────────────┬───────────────┘  │  │
│                             │  ┌────────────▼───────────────┐  │  │
│                             │  │   Component Lifecycle      │  │  │
│                             │  │   (加载/初始化/崩溃恢复)  │  │  │
│                             │  └────────────┬───────────────┘  │  │
│                             │  ┌────────────▼───────────────┐  │  │
│                             │  │   Device Selector          │  │  │
│                             │  │   (NPU→GPU→CPU自动选择)   │  │  │
│                             │  └────────────┬───────────────┘  │  │
│                             │                │                   │  │
│                             │  ┌────────────▼───────────────┐  │  │
│                             │  │      Component            │  │  │
│                             │  │   .infer(iq_frame)         │  │  │
│                             │  │  ┌──────────────────────┐  │  │  │
│                             │  │  │  Model Adapter       │  │  │  │
│                             │  │  │  - 模型加载          │  │  │  │
│                             │  │  │  - STFT              │  │  │  │
│                             │  │  │  - 推理执行          │  │  │  │
│                             │  │  │  - 后处理            │  │  │  │
│                             │  │  └──────────────────────┘  │  │  │
│                             │  └──────────────────────────┘  │  │
│                             └────────────────────────────────┘  │
└────────────────────────────┬─────────────────────────────────────┘
                             │ IQ原始数据帧
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Collector Service                            │
│  ┌─────────────────┐    ┌─────────────────┐                      │
│  │  Pluto Manager  │───→│  Burst Buffer   │                      │
│  │  (设备管理)      │    │  (帧封装)       │                      │
│  └─────────────────┘    └─────────────────┘                      │
│  ┌─────────────────┐    ┌─────────────────┐                      │
│  │  IQ Simulator   │    │  Scan Strategy  │                      │
│  │  (模拟数据)     │    │  (频点轮询)     │                      │
│  └─────────────────┘    └─────────────────┘                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 核心解耦设计

### Config Manager（配置管理器）

> v2.3 新增

**职责**：
- 缓存 Collector 硬件能力范围（从 /collector/discover 获取）
- 接收 Component 推荐配置（从 manifest.collector_requirements 读取）
- 合并生成最终采集配置（capabilities ∩ requirements → final config）
- 冲突检测与降级告警

**配置分层模型**：

| 层 | 定义者 | 内容 | 来源 |
|---|---|---|---|
| **能力范围** | Collector 硬件 | min/max/type/fixed | POST /collector/discover |
| **建议配置** | Component | 推荐值 | manifest.collector_requirements |
| **最终配置** | Config Manager | 合并结果 | 建议值 ∩ 能力范围 |

**合并规则**：
1. `fixed` 参数（采样率、带宽）不参与合并，固定使用
2. 可配置参数：component 建议值 → 裁剪到能力范围内
3. 越界：降级到边界 + WARNING
4. 缺失：用 capability default 补齐

### 三层职责

| 层级 | 名称 | 职责 | 变更频率 |
|---|---|---|---|
| **工程层** | Inference Framework | 帧缓冲（消费不了就扔）、组件生命周期管理、设备选择 | 低 |
| **适配层** | Model Adapter | 模型加载、STFT、推理、后处理 | 高（换模型） |
| **封装层** | Component | Model Adapter + manifest.yaml | 高（换模型） |

### Inference Framework（推理框架）

**职责**：
- 帧队列管理（简单环形缓冲，3-5秒，队列满丢弃最旧帧）
- 组件生命周期（加载、初始化、崩溃感知与恢复）
- 设备选择（NPU → GPU → CPU 自动优先级）
- 调试信息统一格式化 + 日志保存

**关键约束**：
- 不感知组件内部逻辑
- 不感知 Pipeline
- 消费不了就扔（不需要背压）

### Model Adapter

**职责**：
- 加载 ONNX 模型（通过 ONNX Runtime）
- 执行模型推理（自动选择可用设备）
- 单次推理耗时记录到 debug 信息

**设备选择**：
- NPU/GPU/CPU 由推理框架（ONNX Runtime）自动选择
- 训练决定模型结构，推理决定运行设备

---

## 数据流

### IQ 帧格式

```yaml
iq_frame:
  frame_id: 1234              # 递增帧序号
  burst_id: 100              # burst 序号
  timestamp: 1715678900.123   # Unix 时间戳
  center_freq: 5800e6        # 中心频率（Hz）
  sample_rate: 60e6          # 采样率（Hz）
  iq_data: complex[]          # 原始 IQ 数据
  metadata:
    rx_buffer_size: 8192
```

### 推理结果格式

```yaml
result:
  frame_id: 1234              # 对应 IQ 帧
  timestamp: 1715678900.123
  detections:
    - model: "DJI_MAVIC3_PRO"
      confidence: 0.95
      frequency: 5800e6
  debug:                      # 统一格式
    inference_time_ms: 12.5      # 单次推理总耗时
    stft_time_ms: 3.2            # STFT 耗时（组件内部记录）
    model_time_ms: 8.1            # 模型推理耗时
    frames_received: 1000          # 累计收到帧数
    frames_dropped: 50             # 累计丢弃帧数
    device: "npu"               # 当前使用设备
    memory_mb: 256               # 内存占用
    log_path: "/var/log/inference/debug.log"  # 日志保存路径
```

---

## 帧缓冲策略

```
Collector ──IQ帧──→ Platform ──转发──→ Inference Framework
                                              │
                                              ▼
                                    ┌───────────────────┐
                                    │ Frame Queue       │  ← 环形缓冲，默认100帧(~3-5秒)
                                    │ 消费不了就扔      │
                                    │ 丢弃记录日志      │
                                    └───────┬───────────┘
                                            │
                                            ▼
                                    ┌───────────────────┐
                                    │ Component          │
                                    │ .infer(iq_frame)  │  ← 单次推理耗时记录到debug
                                    └───────────────────┘
```

**策略**：
- 环形缓冲，默认缓冲 100 帧（约 3-5 秒，可配置）
- 队列满时丢弃最旧的帧（drop oldest）
- 丢弃的帧记录到日志：`dropped_frames`, `drop_reason`
- **不需要背压**（消费不了就扔）

---

## 组件生命周期

```python
class InferenceFramework:
    def load_component(self, component_id: str, config: dict) -> bool:
        """加载并初始化组件
        感知成功/失败，结果通知 Platform
        """
        try:
            component = self._load_zip(component_id)
            component.initialize(config, self.device)
            self.component = component
            return True
        except Exception as e:
            self.platform.notify_error(f"组件初始化失败: {e}")
            return False

    def run(self):
        """组件运行 + 崩溃恢复"""
        while self.running:
            try:
                frame = self.frame_queue.get()
                result = self.component.infer(frame)
                self.platform.emit_result(result)
            except Exception as e:
                logger.error(f"组件异常: {e}")
                self._reset_component()  # 重置

    def health_check(self) -> bool:
        """Framework 感知组件健康状态"""
        return self.component is not None and self.running
```

---

## Socket.IO 推送策略

| 消息类型 | 频率 | 说明 |
|---|---|---|
| 推理结果 | 1-2秒/次 | 批量推送最近推理结果 |
| 调试信息 | 5秒/次 | 统一格式，组件返回的 debug 信息 |
| 错误告警 | 实时 | 组件或 Collector 异常 |
| 状态变更 | 实时 | 采集开始/停止/设备连接断开 |

---

## 组件 Manifest

```yaml
component:
  id: "rfuav-two-stage"
  name: "RFUAV 两阶段推理"
  version: "2.0.0"
  type: "inference"

capability:
  device_support: [npu, gpu, cpu]  # 框架自动选择

collector_requirements:
  min_data_points: 600000            # 模型需要的数据点数（60MHz×10ms = 600k）

io:
  input:
    - name: iq_frame
      type: dict
  output:
    - name: detections
    - name: debug          # 统一格式，平台原样展示

config_schema:
  confidence_threshold:
    type: number
    default: 0.5
```

---

## 组件接口

```python
class IInferenceComponent(ABC):
    def infer(self, iq_frame: dict) -> dict:
        """单帧推理（look once）
        组件自行决定处理逻辑（丢包/缓冲/STFT/推理）
        返回结果 + debug 信息（含单次推理耗时）
        """
        pass

    def get_manifest(self) -> ComponentManifest:
        pass

    def initialize(self, config: dict, device: str) -> None:
        """Framework 感知初始化成功/失败"""
        pass

    def release(self) -> None:
        pass

    def health_check(self) -> bool:
        """Framework 感知组件健康状态"""
        pass
```

---

## Pluto 采数约束（技术总监跟踪）

| 约束ID | 内容 | 优先级 | 状态 |
|---|---|---|---|
| PLUTO-001 | 采样率固定 60 MHz | 最高 | ✅ 已确立 |
| PLUTO-002 | rx_buffer_size 优先增大（已实测：524288 平衡窗口和检测频率）| 高 | ✅ 已验证 |
| PLUTO-003 | 单次大 buffer 优于 burst 拼接（已确认：Pluto 可稳定支持 1,048,576）| 高 | ✅ 已验证 |
| PLUTO-004 | 增益 20 dB | 最高 | ✅ 已确立 |
| PLUTO-005 | 能力探测仅首次/SN变化时 | 中 | ✅ 已确立 |
| PLUTO-006 | buffer 间时间间隙（已知，不影响检测）| 已知 | ✅ 确认 |
| PLUTO-007 | 5.8 GHz 频段已破解，支持 325-3800 MHz + 5.8 GHz | 高 | ✅ 已破解 |

---

## Agent 职责

| Agent | 职责 |
|---|---|
| 技术总监 | 架构守护、接口审批、约束跟踪 |
| 小崔崔 | 训练模块（RF-Training 仓库）|
| 小边 | Model Adapter + Inference Framework |
| 小采采 | Collector Service + Pluto 管理 |
| 小页 | Web UI + Socket.IO 前端 |

---

## 变更日志

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-05-14 | v2.4 | 新增 Config Manager 配置分层设计；更新 Pluto 约束状态；min_data_points 修正 |
| 2026-05-14 | v2.3 | 新增前后台接口文档 platform-frontend.yaml；更新系统全貌图（前端层 + REST API + Socket.IO）|
| 2026-05-14 | v2.2 | 完善帧缓冲策略（消费不了就扔）、组件生命周期、调试格式统一 |
| 2026-05-14 | v2.0 | 新增 Inference Framework 解耦设计 |
| 2026-05-14 | v1.0 | 初始架构 |