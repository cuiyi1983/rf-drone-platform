#!/bin/bash
# ===================================================================
# start_all_services.sh
# 启动 RF-Drone-Platform 三个服务组件：
#   - Collector (Flask)  : 5101
#   - Platform (FastAPI) : 5100
#   - Frontend (static)  : 5102
#
# 修复说明（2026-05-20）：
#   - Base dir 从 /home/ubuntu/rf-drone-platform-test 改为 /repo
#     （容器内代码挂载在 /repo，与 docker-compose.yml 一致）
#   - 依赖安装提前到 PYTHON_PACKAGES 变量中
# ===================================================================
set -e

# 容器内代码路径（与 docker-compose.yml working_dir 一致）
BASE_DIR="/repo"
LOG_DIR="${BASE_DIR}/logs"
mkdir -p "${LOG_DIR}"

# 必需 Python 依赖（确保pip install已执行）
PYTHON_PACKAGES="requests"

echo "=========================================="
echo "[$(date)] RF-Drone-Platform 启动脚本"
echo "=========================================="

# 进入代码目录
cd "${BASE_DIR}"

# 检查并安装缺失的依赖
for pkg in ${PYTHON_PACKAGES}; do
    python -c "import ${pkg}" 2>/dev/null || {
        echo "[依赖] 安装 ${pkg}..."
        pip install ${pkg} -q 2>/dev/null
    }
done

# 检查是否已有进程占用端口
check_port() {
    local port=$1
    python -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(('localhost', ${port}))
    print('IN_USE')
except:
    print('FREE')
finally:
    s.close()
" 2>/dev/null | grep -q "IN_USE" && {
        echo "[WARN] 端口 ${port} 已被占用，将跳过启动"
        return 1
    }
    return 0
}

# ------------------------------------------------------------------
# 1. 启动 Collector（--mock-devices）
# ------------------------------------------------------------------
echo "[$(date)] [Collector] 启动中 on port 5101..."
check_port 5101 && {
    cd "${BASE_DIR}"
    python -m collector.api \
        --mock-devices \
        --port 5101 \
        > "${LOG_DIR}/collector.log" 2>&1 &
    COL_PID=$!
    echo "[Collector] PID=${COL_PID}, 日志=${LOG_DIR}/collector.log"
    echo $COL_PID > "${BASE_DIR}/collector.pid"
} || echo "[Collector] 跳过（端口已占用）"

# 等待 Collector 完全启动
sleep 2

# ------------------------------------------------------------------
# 2. 启动 Platform (FastAPI + Uvicorn)
# ------------------------------------------------------------------
echo "[$(date)] [Platform] 启动中 on port 5100..."
check_port 5100 && {
    cd "${BASE_DIR}"
    python -m uvicorn \
        backend.main:app \
        --host 0.0.0.0 \
        --port 5100 \
        --log-level info \
        > "${LOG_DIR}/platform.log" 2>&1 &
    PLT_PID=$!
    echo "[Platform] PID=${PLT_PID}, 日志=${LOG_DIR}/platform.log"
    echo $PLT_PID > "${BASE_DIR}/platform.pid"
} || echo "[Platform] 跳过（端口已占用）"

# 等待 Platform 完全启动
sleep 3

# ------------------------------------------------------------------
# 3. 启动 Frontend (Python http.server)
# ------------------------------------------------------------------
echo "[$(date)] [Frontend] 启动中 on port 5102..."
check_port 5102 && {
    cd "${BASE_DIR}/frontend"
    python -m http.server 5102 \
        > "${LOG_DIR}/frontend.log" 2>&1 &
    FEP_PID=$!
    echo "[Frontend] PID=${FEP_PID}, 日志=${LOG_DIR}/frontend.log"
    echo $FEP_PID > "${BASE_DIR}/frontend.pid"
} || echo "[Frontend] 跳过（端口已占用）"

sleep 1

# ------------------------------------------------------------------
# 验证所有服务健康状态
# ------------------------------------------------------------------
echo ""
echo "[$(date)] === 健康检查 ==="

check_health() {
    local name=$1
    local url=$2
    python -c "
import urllib.request, urllib.error
try:
    r = urllib.request.urlopen('${url}', timeout=5)
    print(f'[${name}] HTTP {r.status} ✓')
except urllib.error.HTTPError as e:
    print(f'[${name}] HTTP {e.code} ✓')
except Exception as e:
    print(f'[${name}] HTTP ERROR: {e}')
" 2>/dev/null || echo "[${name}] 健康检查失败（无可用工具）"
}

ALL_OK=true

check_health "Collector" "http://localhost:5101/api/v1/collector/health" || ALL_OK=false
check_health "Platform"  "http://localhost:5100/health"               || ALL_OK=false

# Frontend 是静态服务，检查进程
python -c "
import socket
s = socket.socket()
try:
    s.connect(('localhost', 5102))
    print('[Frontend] 端口 5102 监听中 ✓')
except:
    print('[Frontend] 端口 5102 未监听 ✗')
finally:
    s.close()
" 2>/dev/null || echo "[Frontend] 端口检查失败"

echo ""
if [ "${ALL_OK}" = "true" ]; then
    echo "[$(date)] ✓ 所有服务启动成功"
else
    echo "[$(date)] ⚠ 部分服务启动异常，请检查日志"
fi

echo ""
echo "=== 进程状态 ==="
for svc in collector platform frontend; do
    pidf="${BASE_DIR}/${svc}.pid"
    if [ -f "${pidf}" ]; then
        pid=$(cat "${pidf}")
        if kill -0 "${pid}" 2>/dev/null; then
            echo "  ${svc}: PID=${pid} RUNNING"
        else
            echo "  ${svc}: PID=${pid} DEAD (见日志)"
        fi
    else
        echo "  ${svc}: 无 PID 文件（跳过或已失败）"
    fi
done

echo ""
echo "=== 日志文件 ==="
for f in collector platform frontend; do
    log="${LOG_DIR}/${f}.log"
    if [ -f "${log}" ] && [ -s "${log}" ]; then
        echo "  ${f}: ${log}"
        echo "  --- last 10 lines of ${f}.log ---"
        tail -10 "${log}"
        echo ""
    fi
done

echo "=========================================="
echo "[$(date)] 启动完成"
echo "=========================================="
