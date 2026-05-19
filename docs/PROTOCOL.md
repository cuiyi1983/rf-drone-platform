# Socket.IO Protocol Specification

Platform 后端通过 Socket.IO 实时推送推理结果、采集统计、设备状态和错误信息。

---

## Connection

**Namespace:** `/`

**Auth:** 可选 `auth: { token: "..." }`

**Subscribe Event:** `subscribe`
```json
{ "session_id": "sess_abc123" }
```
Server responds:
```json
{
  "success": true,
  "subscribed_events": ["inference_result", "collector_stats", "device_status", "error"]
}
```

---

## Push Events

### `inference_result`

推理完成时推送，每个推理周期至少一次。

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | 会话 ID |
| `timestamp` | float | Unix timestamp (秒) |
| `class_name` | string | 检测类别，如 `"drone"`、`"jammer"` |
| `confidence` | float | 置信度 0~1 |
| `frequency` | float | 信号中心频率 (Hz)，可选 |
| `bandwidth` | float | 信号带宽 (Hz)，可选 |
| `duration_ms` | float | 推理耗时 (毫秒)，可选 |
| `frame_index` | int | 帧序号，可选 |

**Example:**
```json
{
  "session_id": "sess_abc123",
  "timestamp": 1716112345.123,
  "class_name": "drone",
  "confidence": 0.94,
  "frequency": 5805000000,
  "bandwidth": 56000000,
  "duration_ms": 8.3,
  "frame_index": 42
}
```

---

### `collector_stats`

采集统计信息，定期推送（约 1 秒一次）。

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | 会话 ID |
| `frames_per_second` | float | 推理帧率 FPS |
| `dropped_rate` | float | 丢帧率 0~1 |
| `buffer_level` | int | 帧队列当前深度 |
| `total_frames` | int | 累计采集帧数 |
| `total_dropped` | int | 累计丢帧数 |

**Example:**
```json
{
  "session_id": "sess_abc123",
  "frames_per_second": 7.2,
  "dropped_rate": 0.01,
  "buffer_level": 45,
  "total_frames": 1420,
  "total_dropped": 14
}
```

---

### `device_status`

设备连接状态变更时推送。

| Field | Type | Description |
|-------|------|-------------|
| `event` | string | 事件类型：`"connected"` / `"disconnected"` / `"error"` |
| `device_id` | string | 设备 ID |
| `timestamp` | float | Unix timestamp (秒) |
| `detail` | string | 详细信息，可选 |

**Example:**
```json
{
  "event": "connected",
  "device_id": "usb:2.6.5",
  "timestamp": 1716112300.000,
  "detail": ""
}
```

---

### `error`

平台或推理层发生错误时推送。

| Field | Type | Description |
|-------|------|-------------|
| `code` | int | 错误码 |
| `message` | string | 错误信息 |
| `timestamp` | float | Unix timestamp (秒) |
| `session_id` | string | 关联会话 ID（可选） |

**Error Codes:**

| Code | Meaning |
|------|---------|
| 2001 | 推理错误 |
| 3001 | Collector 连接失败 |
| 3002 | 设备未找到 |
| 3003 | 配置无效 |
| 4001 | 组件加载失败 |
| 4002 | 组件初始化失败 |

**Example:**
```json
{
  "code": 2001,
  "message": "推理超时",
  "timestamp": 1716112345.000,
  "session_id": "sess_abc123"
}
```

---

## Client Events

### `subscribe`

订阅会话推送，see above.

### `unsubscribe`

取消订阅。
```json
{ "session_id": "sess_abc123" }
```

### `get_history`

请求历史推理结果。
```json
{ "session_id": "sess_abc123", "limit": 100 }
```
Server responds:
```json
{
  "success": true,
  "results": [...],
  "total": 150,
  "returned": 100
}
```

---

## REST API Events (Polling Fallback)

部分客户端可能无法使用 WebSocket，可轮询以下端点：

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/session/status?session_id=xxx` | 查询会话状态 |
| GET | `/api/v1/session/status` | 列出所有会话 |
| GET | `/api/v1/session/{id}/history?limit=100` | 推理历史（未来扩展） |