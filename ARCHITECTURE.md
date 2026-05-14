# RF-Drone-Platform 架构

> 版本 v1.0 — 2026-05-14

## 系统全貌

```
┌─────────────────────────────────────────────────────┐
│                   RF-Drone-Platform                  │
├──────────────┬──────────────┬──────────────────────┤
│   小采采      │    小边      │      小页            │
│  (采集模块)   │  (推理模块)  │    (Web UI)         │
├──────────────┴──────────────┴──────────────────────┤
│              inference/framework/                    │
│         (推理插件接口 + Runner)                      │
├─────────────────────────────────────────────────────┤
│         inference/plugins/stage1_yolo/               │
│         inference/plugins/stage2_resnet/           │
│              (两阶段模型插件)                        │
├─────────────────────────────────────────────────────┤
│              collector/collector_proxy.py             │
│              (采集接口标准化)                         │
├─────────────────────────────────────────────────────┤
│              Pluto SDR / 模拟采集器                   │
└─────────────────────────────────────────────────────┘
```

## Agent 职责

### 小采采 — 采集模块
- Pluto SDR 配置、采集、轮询
- CollectorProxy 接口标准化
- 模拟采集器管理

**目录**：`collector/`

### 小边 — 推理模块
- STFT 时频分析
- Preprocess / Postprocess
- ModelLoader / Runner
- 推理插件管理

**目录**：`inference/plugins/`, `inference/framework/`

### 小页 — Web UI
- 采集器选择器（Pluto/模拟）
- 实时推理结果展示
- WebSocket 推送（500ms间隔）

**目录**：`inference/ui/`

## 两阶段推理 Pipeline

```
Pluto 采集 IQ（60MHz, burst=200）
      ↓
STFT 生成频谱图（fs=60MHz, nperseg=1024）
      ↓
[Stage1: YOLOv5 检测器] → bbox列表（nc=1）
      ↓
每个 bbox 裁剪 → resize 224×224
      ↓
[Stage2: ResNet152 分类器] → 机型（nc=7）
      ↓
[机型, 置信度, 频率位置]
```

## 关键接口

### STFT 参数（训练/推理必须一致）
| 参数 | 值 |
|---|---|
| fs | 60MHz |
| nperseg | 1024 |
| hop | 512 |
| window | hamming |
| resize | 640×640（Stage1）/ 224×224（Stage2）|

### Pluto 参数
| 参数 | 值 |
|---|---|
| 采样率 | 60MHz |
| 增益 | 60dB |
| burst | 200 |
| 5.8G频点 | 5760/5775/5800/5825/5850 MHz |
| 2.4G频点 | 2450/2470 MHz |

## 模型

| 模型 | 文件 | mAP/Acc | 用途 |
|---|---|---|---|
| Stage1 | stage1_yolo_v2.onnx | mAP50=0.808 | 无人机检测 |
| Stage2 | stage2_resnet152.onnx | Acc=100% | 7机型分类 |

下载：http://yoyo-chat.cn:8000/

## Agent 持久化

各 Agent 上下文存放在 `.openclaw/workspace/agents/<name>/`：
- `SOUL.md` — 人格定义
- `CONTEXT.md` — 当前状态
- `HANDBOOK.md` — 职责手册（待补充）
