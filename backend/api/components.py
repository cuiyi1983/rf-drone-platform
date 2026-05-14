"""
Components API - 组件查询
"""
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/components", tags=["components"])

_platform_ref: Optional[Any] = None


def inject_platform(platform: Any) -> None:
    global _platform_ref
    _platform_ref = platform


@router.get("")
async def list_components() -> dict:
    """
    GET /api/v1/components
    列出可用组件
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")
    return await _platform_ref.list_components()


@router.get("/{component_id}")
async def get_component(component_id: str) -> dict:
    """
    GET /api/v1/components/{id}
    获取组件详情
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")
    result = await _platform_ref.get_component_detail(component_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{component_id}/config-schema")
async def get_component_config_schema(component_id: str) -> dict:
    """
    GET /api/v1/components/{id}/config-schema
    获取组件配置Schema
    """
    if _platform_ref is None:
        raise HTTPException(status_code=500, detail="Platform not initialized")
    result = await _platform_ref.get_component_config_schema(component_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result