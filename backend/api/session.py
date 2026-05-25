"""
Session API - 会话管理
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/session", tags=["session"])

# 运行时注入（由 main.py 注入）
_platform_ref: Optional[Any] = None


def inject_platform(platform: Any) -> None:
    global _platform_ref
    _platform_ref = platform


@router.post("/start")
async def start_session(request: dict) -> dict:
    """
    POST /api/v1/session/start
    启动会话
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")

    component_id = request.get("component_id")
    config = request.get("config", {})

    if not component_id:
        raise HTTPException(status_code=400, detail="component_id is required")

    result = await _platform_ref.start_session(component_id, config)
    if result.get("error"):
        code = result.get("code", 1002)
        raise HTTPException(status_code=500 if code >= 3000 else 400, detail=result["error"])

    return result


@router.post("/stop")
async def stop_session(request: dict) -> dict:
    """
    POST /api/v1/session/stop
    停止会话
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")

    session_id = request.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    result = await _platform_ref.stop_session(session_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])

    return result


@router.get("/status")
async def session_status(session_id: Optional[str] = None) -> dict:
    """
    GET /api/v1/session/status?session_id=xxx
    查询会话状态
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")

    result = await _platform_ref.get_session_status(session_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{session_id}/config")
async def get_session_config(session_id: str) -> dict:
    """
    GET /api/v1/session/{session_id}/config
    查询会话当前配置（推理组件配置 + 采集器配置）
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")

    result = await _platform_ref.get_session_config(session_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.patch("/{session_id}/config")
async def update_session_config(session_id: str, request: dict) -> dict:
    """
    PATCH /api/v1/session/{id}/config
    更新运行时配置
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")

    result = await _platform_ref.update_session_config(session_id, request)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/{session_id}/stats")
async def get_session_stats(session_id: str) -> dict:
    """
    GET /api/v1/session/{session_id}/stats
    获取会话统计信息
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")

    if session_id not in _platform_ref._sessions:
        raise HTTPException(status_code=404, detail="会话不存在")

    framework = _platform_ref._frameworks.get(session_id)
    stats = framework.get_stats() if framework else {}
    return {"session_id": session_id, **stats}


@router.get("/{session_id}/latest_result")
async def get_session_latest_result(session_id: str) -> dict:
    """
    GET /api/v1/session/{session_id}/latest_result
    返回最近一次推理结果（用于 HTTP 轮询替代 Socket.IO）
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")

    history = _platform_ref._inference_history.get(session_id, [])
    latest = history[-1] if history else None
    return {"session_id": session_id, "result": latest}