"""
test_start_services.py
验证三个服务组件启动时的日志输出无异常

测试场景：
1. 启动 Collector 服务，检查启动日志无 import 错误
2. 启动 Platform 服务，检查启动日志无 import 错误
3. 启动 Frontend 服务，检查启动日志无 port 冲突
4. 同时启动三个服务，检查无端口冲突
5. 健康检查：验证各服务 HTTP 端口响应

依赖: pytest, pytest-asyncio, httpx
运行: pytest tests/integration/test_start_services.py -v
"""

import subprocess
import time
import sys
import os

import pytest


BASE_DIR = "/repo"  # 容器内代码路径（与 docker-compose.yml working_dir 一致）
LOG_DIR = f"{BASE_DIR}/logs"


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def kill_process_on_port(port: int) -> None:
    """杀掉占用指定端口的进程（用于清理）"""
    try:
        # 查找占用端口的进程
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line:
                # 提取 PID
                parts = line.split()
                for p in parts:
                    if "/" in p:
                        pid = p.split("/")[0]
                        try:
                            subprocess.run(["kill", "-9", pid], timeout=5)
                            print(f"[cleanup] killed PID {pid} on port {port}")
                        except Exception:
                            pass
                        break
    except Exception:
        pass


def is_port_in_use(port: int) -> bool:
    """检查端口是否已被占用"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("localhost", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def wait_for_port(port: int, timeout: int = 10) -> bool:
    """等待端口变为可用（或超时）"""
    start = time.time()
    while time.time() - start < timeout:
        if is_port_in_use(port):
            time.sleep(0.5)
            return True
        time.sleep(0.2)
    return False


def check_log_for_errors(log_path: str) -> tuple[bool, list[str]]:
    """检查日志文件是否包含 ERROR/Exception/Traceback（不含 WARNING）"""
    if not os.path.exists(log_path):
        return False, [f"日志文件不存在: {log_path}"]

    errors = []
    with open(log_path, "r") as f:
        for line in f:
            # 排除 WARNING 和 info 级别的正常输出
            stripped = line.strip()
            if ("ERROR" in line or "Exception" in line or "Traceback" in line) \
                    and "WARNING" not in line \
                    and "werkzeug" not in line:  # werkzeug INFO 日志不算错误
                errors.append(stripped[:120])  # 截断过长的行

    return len(errors) == 0, errors


def start_service_background(cmd: list, log_file: str, cwd: str = BASE_DIR) -> int:
    """后台启动服务，返回 PID"""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, "w") as f:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=f,
            stderr=subprocess.STDOUT,
            text=True
        )
    return proc.pid


# ------------------------------------------------------------------
# 测试用例
# ------------------------------------------------------------------

class TestCollectorStart:
    """Collector 服务启动测试"""

    def test_collector_no_import_error(self):
        """验证 Collector 启动日志无 import 错误"""
        log_file = f"{LOG_DIR}/test_collector.log"
        cmd = [
            sys.executable, "-m", "collector.api",
            "--port", "5101"
        ]

        # 清理端口
        kill_process_on_port(5101)
        time.sleep(0.5)

        pid = start_service_background(cmd, log_file)
        time.sleep(3)

        # 检查进程是否存活
        try:
            os.kill(pid, 0)
        except OSError:
            pass  # 进程已退出，我们仍检查日志

        clean, errors = check_log_for_errors(log_file)
        if not clean:
            print(f"\n[Collector] 发现错误日志:")
            for e in errors:
                print(f"  {e}")

        assert clean, f"Collector 启动日志包含错误: {errors[:3]}"


class TestPlatformStart:
    """Platform 服务启动测试"""

    def test_platform_no_import_error(self):
        """验证 Platform 启动日志无 import 错误"""
        log_file = f"{LOG_DIR}/test_platform.log"
        cmd = [
            sys.executable, "-m", "uvicorn",
            "backend.main:app",
            "--host", "0.0.0.0",
            "--port", "5100",
            "--log-level", "info"
        ]

        # 清理端口
        kill_process_on_port(5100)
        time.sleep(0.5)

        pid = start_service_background(cmd, log_file)
        time.sleep(5)

        try:
            os.kill(pid, 0)
        except OSError:
            pass

        clean, errors = check_log_for_errors(log_file)
        if not clean:
            print(f"\n[Platform] 发现错误日志:")
            for e in errors:
                print(f"  {e}")

        assert clean, f"Platform 启动日志包含错误: {errors[:3]}"


class TestFrontendStart:
    """Frontend 服务启动测试"""

    def test_frontend_no_port_conflict(self):
        """验证 Frontend 启动无端口冲突"""
        log_file = f"{LOG_DIR}/test_frontend.log"
        frontend_dir = f"{BASE_DIR}/frontend"

        # 清理端口
        kill_process_on_port(5102)
        time.sleep(0.5)

        # 先确认端口空闲
        assert not is_port_in_use(5102), "端口 5102 未能成功释放"

        cmd = [sys.executable, "-m", "http.server", "5102"]
        pid = start_service_background(cmd, log_file, cwd=frontend_dir)
        time.sleep(2)

        # 检查进程存活
        alive = False
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            pass

        # 检查是否有 "Address already in use" 错误
        with open(log_file, "r") as f:
            content = f.read()

        has_port_conflict = "Address already in use" in content or "Errno 98" in content

        if has_port_conflict:
            print(f"\n[Frontend] 端口冲突日志:")
            print(content[-500:])

        # 如果进程已退出且有端口冲突，说明有问题
        if not alive and has_port_conflict:
            pytest.fail(f"Frontend 因端口冲突启动失败")

        # 如果进程存活，检查日志没有严重错误
        if alive:
            clean, errors = check_log_for_errors(log_file)
            # Frontend 的 http.server 可能输出 Info 级日志，不算错误
            # 主要检查是否有 Exception/ERROR（werkzeug 除外）
            real_errors = [e for e in errors if "werkzeug" not in e.lower()]
            assert len(real_errors) == 0, f"Frontend 启动日志包含错误: {real_errors[:3]}"


class TestAllServicesTogether:
    """三个服务同时启动测试"""

    def test_all_services_start_without_conflicts(self):
        """同时启动三个服务，检查无端口冲突，无 import 错误"""
        # 清理所有端口
        for port in [5100, 5101, 5102]:
            kill_process_on_port(port)
        time.sleep(1)

        log_collector = f"{LOG_DIR}/test_all_collector.log"
        log_platform = f"{LOG_DIR}/test_all_platform.log"
        log_frontend = f"{LOG_DIR}/test_all_frontend.log"

        # 启动 Collector
        pid_col = start_service_background(
            [sys.executable, "-m", "collector.api", "--port", "5101"],
            log_collector
        )
        time.sleep(2)

        # 启动 Platform
        pid_plt = start_service_background(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "5100", "--log-level", "info"],
            log_platform
        )
        time.sleep(4)

        # 启动 Frontend
        pid_fep = start_service_background(
            [sys.executable, "-m", "http.server", "5102"],
            log_frontend,
            cwd=f"{BASE_DIR}/frontend"
        )
        time.sleep(2)

        # 验证所有进程存活
        for name, pid in [("Collector", pid_col), ("Platform", pid_plt), ("Frontend", pid_fep)]:
            try:
                os.kill(pid, 0)
                print(f"[{name}] PID={pid} RUNNING ✓")
            except OSError:
                print(f"[{name}] PID={pid} DEAD ✗")

        # 检查各服务日志
        for name, log_path in [
            ("Collector", log_collector),
            ("Platform", log_platform),
            ("Frontend", log_frontend)
        ]:
            clean, errors = check_log_for_errors(log_path)
            real_errors = [e for e in errors if "werkzeug" not in e.lower()]
            if not clean:
                print(f"\n[{name}] 启动异常:")
                for e in real_errors[:5]:
                    print(f"  {e}")

            assert clean, f"{name} 启动日志包含错误: {real_errors[:3]}"

        # 验证端口都在监听
        for port, name in [(5101, "Collector"), (5100, "Platform"), (5102, "Frontend")]:
            assert is_port_in_use(port), f"{name} 端口 {port} 未监听"


class TestHealthChecks:
    """健康检查测试（在服务已启动后运行）"""

    def test_collector_health_endpoint(self):
        """验证 Collector /api/v1/collector/health 返回 200"""
        import urllib.request

        # 如果端口未就绪，跳过
        if not is_port_in_use(5101):
            pytest.skip("Collector 未运行，跳过健康检查")

        try:
            resp = urllib.request.urlopen(
                "http://localhost:5101/api/v1/collector/health",
                timeout=5
            )
            assert resp.status == 200, f"Collector 健康检查失败: {resp.status}"
            data = resp.read().decode()
            assert "code" in data, f"Collector 健康检查响应异常: {data}"
            print(f"[Collector] 健康检查通过: {data}")
        except Exception as e:
            pytest.fail(f"Collector 健康检查失败: {e}")

    def test_platform_health_endpoint(self):
        """验证 Platform /health 返回 200"""
        import urllib.request

        if not is_port_in_use(5100):
            pytest.skip("Platform 未运行，跳过健康检查")

        try:
            resp = urllib.request.urlopen(
                "http://localhost:5100/health",
                timeout=5
            )
            assert resp.status == 200, f"Platform 健康检查失败: {resp.status}"
            data = resp.read().decode()
            assert "status" in data, f"Platform 健康检查响应异常: {data}"
            print(f"[Platform] 健康检查通过: {data}")
        except Exception as e:
            pytest.fail(f"Platform 健康检查失败: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])