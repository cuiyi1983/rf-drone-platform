"""
Collector Proxy API - 代理前端对 Collector 的调用
将 /api/v1/collector/* 请求转发给 Collector（5101）
"""
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/collector", tags=["collector"])

_collector_base_url: str = "http://localhost:5101"


def inject_collector_base_url(url: str) -> None:
    global _collector_base_url
    _collector_base_url = url


@router.post("/connect")
async def connect_device(body: dict) -> dict:
    """
    POST /api/v1/collector/connect
    Body: { "device_uri": "usb:2.6.5" }
    """
    import requests
    try:
        resp = requests.post(
            f"{_collector_base_url}/api/v1/collector/connect",
            json=body,
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        return {"code": 1, "message": str(e)}


@router.post("/disconnect")
async def disconnect_device(body: dict = {}) -> dict:
    """
    POST /api/v1/collector/disconnect
    """
    import requests
    try:
        resp = requests.post(
            f"{_collector_base_url}/api/v1/collector/disconnect",
            json=body,
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        return {"code": 1, "message": str(e)}


@router.get("/devices")
async def list_devices() -> dict:
    """
    GET /api/v1/collector/devices
    """
    import requests
    try:
        resp = requests.get(f"{_collector_base_url}/api/v1/collector/devices", timeout=10)
        return resp.json()
    except Exception as e:
        return {"code": 1, "message": str(e)}


@router.post("/discover")
async def discover_collector() -> dict:
    """
    POST /api/v1/collector/discover
    """
    import requests
    try:
        resp = requests.post(f"{_collector_base_url}/api/v1/collector/discover", timeout=10)
        return resp.json()
    except Exception as e:
        return {"code": 1, "message": str(e)}


@router.post("/apply_component_config")
async def apply_component_config(body: dict) -> dict:
    """
    POST /api/v1/collector/apply_component_config
    """
    import requests
    try:
        resp = requests.post(
            f"{_collector_base_url}/api/v1/collector/apply_component_config",
            json=body,
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        return {"code": 1, "message": str(e)}


@router.get("/health")
async def health_check() -> dict:
    """
    GET /api/v1/collector/health
    """
    import requests
    try:
        resp = requests.get(f"{_collector_base_url}/api/v1/collector/health", timeout=10)
        return resp.json()
    except Exception as e:
        return {"code": 1, "message": str(e)}
