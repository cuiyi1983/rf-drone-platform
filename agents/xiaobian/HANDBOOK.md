# xiaobian/HANDBOOK.md

## 实现规范补充

### Config Manager 合并规则（已实现）

```
collector_capabilities:  # 从 POST /collector/discover 获取
  frequency:    range=[325M, 6G], default=5.805G
  buffer_size:  range=[1024, 1048576], default=524288
  gain:         range=[0, 60], default=20
  sample_rate:  fixed=60M  ← 不参与合并
  rf_bandwidth: fixed=56M  ← 不参与合并

collector_requirements:   # 从 manifest.collector_requirements 读取
  frequency: 5805M
  buffer_size: 524288
  gain: 20

合并逻辑:
  1. fixed 参数直接使用 fixed 值
  2. 建议值在范围内 → 直接采纳
  3. 建议值 < 下限 → 降级到下限 + WARNING
  4. 建议值 > 上限 → 降级到上限 + WARNING
  5. 缺失 → 用 capability default + WARNING
  6. scan/min_data_points 原样透传
```

### 接口规范遵守情况

| 接口文件 | 版本 | 状态 |
|---|---|---|
| `platform-frontend.yaml` | v1.1 | ✅ 已遵循 |
| `platform-collector.yaml` | v2.4 | ✅ 已遵循 |
| `component-manifest.yaml` | v2.3 | ✅ 已遵循 |

### 已验证的实现要点

1. **REST API 响应码**
   - 400: 参数无效/会话已停止/能力不足
   - 404: 组件不存在/会话不存在/设备不存在
   - 500: 组件初始化失败/系统错误
   - 会话不存在时 `get_session_status` 返回 `{"error": ...}`，API 层转 404

2. **Socket.IO 消息格式**（与 platform-frontend.yaml 一致）
   - `inference_result`: `{session_id, frame_id, timestamp, detections[], debug{}}`
   - `collector_stats`: `{session_id, frames_per_second, dropped_rate, buffer_level, total_frames, total_dropped}`
   - `device_status`: `{event, device_id, timestamp, detail}`
   - `error`: `{code, message, session_id?, timestamp}`

3. **Frame Queue 丢弃策略**
   - 队列满时丢弃 `frame_queue[0]`（最旧帧）
   - 丢弃时记录 reason 到 `dropped_reasons[]`
   - dropped_rate = frames_dropped / frames_received

4. **推理历史**
   - 内存缓存，每个会话最多 1000 条
   - `get_history` 支持 limit 参数（最大 1000）

### 已知限制

1. 组件加载使用 MockComponent（真实实现需等待组件注册表）
2. Collector 通信使用 mock fallback（真实集成需等待 Collector Service）
3. 设备列表使用 mock 设备（真实需等待 Collector `/collector/devices`）
4. 无持久化存储（会话状态仅内存）
5. 无认证/授权（生产环境需补充）