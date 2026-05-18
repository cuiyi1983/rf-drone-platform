"""
api.py - REST API control interface

Exposes the Collector Service HTTP endpoints as defined in
collector-api.yaml v2.5.

All responses follow the standard {code, message, ...} envelope.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

from flask import Blueprint, Flask, jsonify, request

from collector.collector import Collector, CollectorConfig, CollectorState, IQFrame
from collector.devices import DeviceCapabilities, discover_devices
from collector.simulator import IQSimulator
from collector.socketio_server import get_socketio_server

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------
# Mock devices mode (set via --mock-devices CLI flag)
_mock_devices_mode = False


def create_app(mock_devices: bool = False) -> Flask:
    global _mock_devices_mode
    _mock_devices_mode = mock_devices
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    api = CollectorAPI(mock_devices=mock_devices)
    api.register_routes(app)

    return app


# ------------------------------------------------------------------
# Collector API
# ------------------------------------------------------------------
@dataclass
class APIResponse:
    """Standard response envelope."""

    code: int
    message: str
    extra: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.extra:
            d.update(self.extra)
        return d


def _json(code: int, message: str, **kwargs) -> tuple[dict, int]:
    # Map business code to HTTP status.  0 → 200 OK; 4xx/5xx codes used as HTTP status.
    http_status = 200 if code == 0 else abs(code)
    return {"code": code, "message": message, **kwargs}, http_status


class CollectorAPI:
    """
    Collector Service REST API.

    Usage:
        api = CollectorAPI()
        app = Flask(__name__)
        api.register_routes(app)
    """

    def __init__(self, mock_devices: bool = False):
        self._collector = Collector()
        self._simulator = IQSimulator()
        self._socketio_started = False
        self._mock_devices = mock_devices
        if mock_devices:
            logger.info("CollectorAPI: mock_devices 模式启用")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _start_socketio_once(self, host: str, port: int) -> None:
        """Start the Socket.IO server once (lazy)."""
        if not self._socketio_started:
            io_srv = get_socketio_server(host=host, port=port)
            io_srv.start()
            self._socketio_started = True
            # Wire up frame emission
            self._collector.on_iq_frame(self._make_frame_emitter())

    def _make_frame_emitter(self):
        """Return a callback that emits IQ frames via Socket.IO."""
        io_srv_ref = [None]  # nonlocal hack for Python 3

        def emit_frame(frame: IQFrame):
            io_srv = io_srv_ref[0]
            if io_srv is None:
                # Lookup now (lazy – avoid circular import at init time)
                io_srv = get_socketio_server()
                io_srv_ref[0] = io_srv
            session_id = self._collector._session_id or "unknown"
            frame_dict = {
                "frame_id": frame.frame_id,
                "burst_id": frame.burst_id,
                "timestamp": frame.timestamp,
                "center_freq": frame.center_freq,
                "sample_rate": frame.sample_rate,
                "iq_data": self._complex_to_list(frame.iq_data),
                "metadata": frame.metadata,
            }
            io_srv.emit_frame(session_id, frame_dict)

        return emit_frame

    @staticmethod
    def _complex_to_list(iq: "np.ndarray") -> list:
        """Convert complex64 numpy array to [[real, imag], ...] list."""
        import numpy as np

        if iq.size == 0:
            return []
        real = iq.real.astype(np.float32)
        imag = iq.imag.astype(np.float32)
        result = np.stack([real, imag], axis=1)
        return result.tolist()

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------
    def register_routes(self, app: Flask) -> None:
        """Register all collector-api.yaml endpoints on the Flask app."""

        @app.route("/api/v1/collector/start", methods=["POST"])
        def start_collector():
            """
            POST /api/v1/collector/start

            Body: { mode: "pluto"|"simulator", config: {...} }
            Returns: { code, message, session_id }
            """
            body = request.get_json(force=True) or {}
            mode = body.get("mode", "pluto")
            if mode not in ("pluto", "simulator"):
                return _json(400, f"Invalid mode: {mode}")

            raw_config = body.get("config", {})
            # Normalise frequencies – accept ints or float strings
            raw_freqs = raw_config.get("frequencies", [5_805_000_000])
            frequencies = [int(f) for f in raw_freqs]

            config = CollectorConfig(
                frequencies=frequencies,
                sample_rate=int(raw_config.get("sample_rate", 60_000_000)),
                buffer_size=int(raw_config.get("buffer_size", 524_288)),
                gain=float(raw_config.get("gain", 20.0)),
                hop_interval_ms=int(raw_config.get("hop_interval_ms", 100)),
            )

            try:
                self._start_socketio_once("0.0.0.0", 5101)
                session_id = self._collector.start(mode=mode, config=config)
                return _json(0, "采集已开始", session_id=session_id)
            except RuntimeError as e:
                logger.error("start failed: %s", e)
                return _json(409, str(e))

        @app.route("/api/v1/collector/stop", methods=["POST"])
        def stop_collector():
            """
            POST /api/v1/collector/stop

            Body: { session_id }
            Returns: { code, message, stats }
            """
            body = request.get_json(force=True) or {}
            session_id = body.get("session_id")
            if not session_id:
                return _json(400, "session_id required")

            try:
                stats = self._collector.stop(session_id)
                return _json(
                    0,
                    "采集已停止",
                    stats={
                        "total_frames": stats.total_frames,
                        "dropped_frames": stats.dropped_frames,
                        "duration_ms": round(stats.duration_ms, 1),
                    },
                )
            except ValueError as e:
                return _json(404, str(e))
            except RuntimeError as e:
                return _json(500, str(e))

        @app.route("/api/v1/collector/status", methods=["GET"])
        def collector_status():
            """
            GET /api/v1/collector/status
            """
            status = self._collector.get_status()
            return {"code": 0, "message": "ok", **status}, 200

        @app.route("/api/v1/collector/devices", methods=["GET"])
        def list_devices():
            """
            GET /api/v1/collector/devices
            """
            logger.info("Collector: HTTP GET /api/v1/collector/devices 被调用, mock=%s", self._mock_devices)
            if self._mock_devices:
                mock_devs = [
                    {"id": "sim:pluto_2.6.5", "type": "pluto", "name": "ADALM PLUTO (mock)", "connected": True, "fw_version": "v0.34"},
                    {"id": "sim:pluto_2.10.5", "type": "pluto", "name": "ADALM PLUTO (mock)", "connected": True, "fw_version": "v0.34"},
                ]
                logger.info("Collector: 返回 mock 设备列表")
                return {"code": 0, "message": "ok", "devices": mock_devs}, 200
            logger.info("Collector: 调用 self._collector.get_devices()")
            devs = self._collector.get_devices()
            logger.info("Collector: get_devices() 返回 %d 个设备: %s", len(devs), devs)
            return {"code": 0, "message": "ok", "devices": devs}, 200

        @app.route("/api/v1/collector/discover", methods=["GET", "POST"])
        def discover_capabilities():
            """
            GET /api/v1/collector/discover
            POST /api/v1/collector/discover
            """
            caps = self._collector.get_capabilities()
            return {"code": 0, "message": "ok", "capabilities": caps}, 200

        @app.route("/api/v1/collector/simulator/load", methods=["POST"])
        def load_simulator():
            """
            POST /api/v1/collector/simulator/load

            Body: { file_path }
            Returns: { code, message, metadata }
            """
            body = request.get_json(force=True) or {}
            file_path = body.get("file_path")
            if not file_path:
                return _json(400, "file_path required")

            try:
                metadata = self._simulator.load(file_path)
                return _json(
                    0,
                    "模拟数据已加载",
                    metadata={
                        "sample_count": metadata.sample_count,
                        "sample_rate": metadata.sample_rate,
                        "duration_ms": metadata.duration_ms,
                    },
                )
            except FileNotFoundError as e:
                return _json(404, str(e))
            except ValueError as e:
                return _json(400, str(e))
            except Exception as e:
                logger.error("simulator load error: %s", e)
                return _json(500, f"加载失败: {e}")

        @app.route("/api/v1/collector/apply_component_config", methods=["POST"])
        def apply_component_config():
            """
            POST /api/v1/collector/apply_component_config

            Body: { source, component_id, requirements, config }
            Returns: { code, message }
            """
            body = request.get_json(force=True) or {}
            source = body.get("source", "component")
            component_id = body.get("component_id", "")
            requirements = body.get("requirements", {})
            config = body.get("config", {})
            logger.info(f"apply_component_config: source={source}, component_id={component_id}")
            return _json(0, "配置已更新")

        # Health check endpoint
        @app.route("/api/v1/collector/health", methods=["GET"])
        def health():
            return {"code": 0, "message": "ok"}, 200


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------
if __name__ == "__main__":
    import os
    import argparse

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Collector Service")
    parser.add_argument("--mock-devices", action="store_true", help="使用模拟 Pluto 设备（用于测试）")
    parser.add_argument("--port", type=int, default=5101, help="HTTP 端口（默认 5101）")
    args = parser.parse_args()

    app = create_app(mock_devices=args.mock_devices)
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)