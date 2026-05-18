"""
Devices API - 设备查询
"""
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])

_platform_ref: Optional[Any] = None


def inject_platform(platform: Any) -> None:
    global _platform_ref
    _platform_ref = platform


@router.get("")
async def list_devices() -> dict:
    """
    GET /api/v1/devices
    列出已发现设备（缓存，启动时不刷新）
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")
    return await _platform_ref.list_devices()


@router.post("/refresh")
async def refresh_devices() -> dict:
    """
    POST /api/v1/devices/refresh
    重新扫描 Collector 设备列表并返回
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info("API: POST /api/v1/devices/refresh 被调用")
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")
    result = await _platform_ref.refresh_devices()
    logger.info(f"API: refresh_devices 返回 {result}")
    return result


@router.get("/{device_id}/capabilities")
async def get_device_capabilities(device_id: str) -> dict:
    """
    GET /api/v1/devices/{id}/capabilities
    获取设备能力
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")
    result = await _platform_ref.get_device_capabilities(device_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result