@echo off
chcp 65001 >nul
echo ==========================================
echo RF-Drone-Platform 一键启动
echo ==========================================
echo.

cd /d %~dp0

REM 检查依赖
python -c "import fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo [错误] 缺少依赖，请先运行: pip install -e ".[dev]"
    pause
    exit /b 1
)

echo [1/3] 启动 Collector Service (端口 8081)...
start "Collector-Service" cmd /k "echo Collector Service 启动中... && python -m collector.api"

timeout /t 2 >nul

echo [2/3] 启动 Platform Backend (端口 8080)...
start "Platform-Backend" cmd /k "echo Platform Backend 启动中... && uvicorn backend.main:app --host 0.0.0.0 --port 8080"

timeout /t 2 >nul

echo [3/3] 启动 Frontend (端口 8082)...
start "Frontend" cmd /k "cd frontend && echo Frontend 启动中... && python -m http.server 8082"

timeout /t 1 >nul

echo.
echo ==========================================
echo 服务已全部启动
echo   - Collector:     http://localhost:8081
echo   - Platform:       http://localhost:8080
echo   - Frontend:       http://localhost:8082
echo.
echo 各服务在独立窗口中运行，可观察后台日志
echo.
echo 按任意键停止所有服务并退出...
echo ==========================================
pause >nul

REM 停止所有相关进程
taskkill /f /im python.exe 2>nul
echo 已停止所有服务