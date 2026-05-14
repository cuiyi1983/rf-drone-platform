# RF-Drone-Platform

> 无人机探测平台 — 射频指纹识别系统

## 系统架构

```
Pluto SDR（采集）
    ↓
STFT（时频分析）
    ↓
[Stage1: YOLOv5 检测器] → 检测所有无人机区域
    ↓
[Stage2: ResNet152 分类器] → 识别具体机型
    ↓
Web UI（结果展示）
```

## Agent 团队

| Agent | 职责 |
|---|---|
| **小边** | 推理模块（STFT/Runner/Web UI）|
| **小采采** | 采集模块（Pluto + CollectorProxy）|
| **小页** | Web UI（采集器选择器 + 界面）|

## 模型

- **Stage1（检测）**：http://yoyo-chat.cn:8000/stage1_yolo_v2.onnx
- **Stage2（分类）**：http://yoyo-chat.cn:8000/stage2_resnet152.onnx

### 7机型分类标签

| Index | 机型 |
|---|---|
| 0 | DAUTEL EVO NANO |
| 1 | DEVENTION DEVO |
| 2 | DJI AVATA2 |
| 3 | DJI FPV COMBO |
| 4 | DJI MAVIC3 PRO |
| 5 | DJI MINI3.1 |
| 6 | DJI MINI4 PRO |

## 推理参数

- **采样率**：60MHz
- **STFT**：nperseg=1024, hop=512, window=hamming
- **burst数**：200（等效窗口~3.4ms）
- **频点**：5760/5775/5800/5825/5850 MHz（5.8G轮询）

## 目录结构

```
inference/
  plugins/          — 推理插件
  ui/              — Web界面
  framework/        — 推理框架核心
collector/         — 采集模块（Pluto + 模拟器）
tests/             — 集成测试
```

## 训练模块

训练代码见 [RF-Training](https://github.com/cuiyi1983/RF-Training) 仓库。
