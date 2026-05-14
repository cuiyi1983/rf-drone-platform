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
    列出已发现设备
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")
    return await _platform_ref.list_devices()


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