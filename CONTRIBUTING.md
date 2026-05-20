# Contributing to rf-drone-platform

## 开发规范

### 1. 前端 JS 提交规范
- 所有 `frontend/app.js` 修改后，**上库前必须执行语法检查**：
  ```bash
  node --check frontend/app.js
  ```
  不通过不准 push。
- 多文件批量改动时，需对**每个文件**做语法/逻辑完整性验证，不能只依赖 diff 行内变更判断。

### 2. 组件目录结构

每个推理组件放在 `components/<component-id>/` 下，包含：

```
components/<component-id>/
├── manifest.yaml       # 组件描述（名称/版本/IO/配置Schema）
├── component.py        # 组件实现，必须导出 COMPONENT_ENTRY
└── models/             # 模型文件目录（可选）
```

**`component.py` 必须导出**：
```python
COMPONENT_ENTRY = {
    "id": "<component-id>",
    "name": "<显示名称>",
    "version": "<版本>",
    "component_class": <你的组件类>,
    "manifest": <manifest字典>
}
```

### 3. 组件自动扫描机制
Platform 启动时自动扫描 `components/` 目录（项目根目录），发现包含 `manifest.yaml` + `component.py` 的目录即自动注册。

内置组件（`sim-inference`）也迁移至 `components/sim-inference/` 目录，与外部组件享受同等待遇。

### 4. 集成测试规范
- 集成测试必须使用真实 HTTP 请求（pytest TestClient 禁止绕过）
- 必须覆盖所有客户可见接口（包括 OPTIONS 预检请求）
- 禁止使用 Mock 设备，必须使用真实 Pluto 硬件
- 禁止使用 pytest mock 机制

### 5. API 方法契约
- 接口路由的 HTTP 方法（GET/POST/OPTIONS）必须严格遵守接口文档
- 刷新类接口不得用 GET 替代 POST
- 跨域预检请求（OPTIONS）必须返回 200 并携带正确 CORS 头
