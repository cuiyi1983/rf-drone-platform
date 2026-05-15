@echo off
chcp 65001 >nul
echo ==========================================
echo RF-Drone-Platform 一键启动 (Windows)
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

REM 启动 Collector Service (后台运行)
echo [1/3] 启动 Collector Service (端口 8081)...
start /b python -m collector.api >nul 2>&1
timeout /t 2 >nul

REM 启动 Platform Backend (后台运行)
echo [2/3] 启动 Platform Backend (端口 8080)...
start /b uvicorn backend.main:app --host 0.0.0.0 --port 8080 >nul 2>&1
timeout /t 2 >nul

REM 启动 Frontend (后台运行)
echo [3/3] 启动 Frontend (端口 8082)...
cd frontend
start /b python -m http.server 8082 >nul 2>&1
cd ..
timeout /t 1 >nul

echo.
echo ==========================================
echo 服务已全部启动
echo   - Collector: http://localhost:8081
echo   - Platform:  http://localhost:8080
echo   - Frontend:  http://localhost:8082
echo.
echo 按任意键停止所有服务并退出...
echo ==========================================
pause >nul

REM 停止所有相关进程
taskkill /f /im python.exe 2>nul
taskkill /f /im uvicorn.exe 2>nul
echo 已停止所有服务