#!/bin/bash
# RF-Drone-Platform 一键启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "RF-Drone-Platform 一键启动"
echo "=========================================="

# 检查依赖
check_deps() {
    if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
        echo "[ERROR] 缺少依赖，请先运行: pip install -e \".[dev]\""
        exit 1
    fi
}

# 启动 Collector Service
start_collector() {
    echo "[1/2] 启动 Collector Service (端口 5101)..."
    python -m collector.api --port 5101 &
    COLLECTOR_PID=$!
    echo "Collector PID: $COLLECTOR_PID"
}

# 启动 Platform Backend
start_platform() {
    echo "[2/2] 启动 Platform Backend (端口 5100)..."
    uvicorn backend.main:app --host 0.0.0.0 --port 5100 &
    PLATFORM_PID=$!
    echo "Platform PID: $PLATFORM_PID"
}

# 启动前端
start_frontend() {
    echo "[3/3] 启动 Frontend (端口 5102)..."
    cd frontend
    python -m http.server 5102 &
    FRONTEND_PID=$!
    echo "Frontend PID: $FRONTEND_PID"
    cd "$SCRIPT_DIR"
}

# 主流程
check_deps
start_collector
start_platform
start_frontend

echo ""
echo "=========================================="
echo "服务已全部启动"
echo "  - Collector: http://localhost:5101"
echo "  - Platform:  http://localhost:5100"
echo "  - Frontend:  http://localhost:5102"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo "=========================================="

# 等待退出
trap "kill $COLLECTOR_PID $PLATFORM_PID $FRONTEND_PID 2>/dev/null; echo '已停止所有服务'; exit 0" INT TERM
wait