"""
socketio_server.py - Socket.IO real-time streaming server

Pushes IQ frames to connected Platform Backend clients via Socket.IO.
One room per collector session ({session_id}).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Socket.IO integration
# ------------------------------------------------------------------
class SocketIOServer:
    """
    Socket.IO server wrapper.

    Manages the Socket.IO server lifecycle and provides a clean API
    for emitting IQ frames to subscribed rooms.

    Usage:
        io_server = SocketIOServer()
        io_server.start(host, port)
        io_server.emit_frame(session_id, frame_dict)
        io_server.stop()
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8081):
        self.host = host
        self.port = port
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._io_loop = None  # asyncio event loop reference

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """
        Start the Socket.IO server on (host, port).

        Runs in a background thread so Flask can block on the API port.
        """
        if self._running:
            logger.warning("Socket.IO server already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_server, name="socketio-server", daemon=True)
        self._thread.start()
        logger.info("Socket.IO server started on %s:%s", self.host, self.port)

    def stop(self) -> None:
        """Stop the Socket.IO server."""
        if not self._running:
            return
        self._running = False
        if self._server:
            try:
                self._server.stop()
            except Exception as e:
                logger.warning("Socket.IO stop error: %s", e)
        self._thread = None
        logger.info("Socket.IO server stopped")

    # ------------------------------------------------------------------
    # Frame emission
    # ------------------------------------------------------------------
    def emit_frame(self, session_id: str, frame_dict: dict) -> None:
        """
        Emit an IQ frame to the room named after session_id.

        The frame dict must match the collector-api.yaml iq_frame schema:
        {
          "type": "iq_frame",
          "session_id": <session_id>,
          "frame": { <iq_frame fields> }
        }

        Silently skips if the room has no clients.
        """
        if not self._running or self._server is None:
            return

        event = {
            "type": "iq_frame",
            "session_id": session_id,
            "frame": frame_dict,
        }
        try:
            self._emit_via_socketio(session_id, event)
        except Exception as e:
            logger.debug("emit_frame skipped (no clients): %s", e)

    def emit_stats(self, session_id: str, stats: dict) -> None:
        """Emit session stats event."""
        if not self._running:
            return
        event = {"type": "collector_stats", "session_id": session_id, "stats": stats}
        try:
            self._emit_via_socketio(session_id, event)
        except Exception as e:
            logger.debug("emit_stats skipped: %s", e)

    def emit_error(self, session_id: str, error_msg: str) -> None:
        """Emit an error event."""
        if not self._running:
            return
        event = {"type": "error", "session_id": session_id, "message": error_msg}
        try:
            self._emit_via_socketio(session_id, event)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal server runner
    # ------------------------------------------------------------------
    def _run_server(self) -> None:
        """Background thread: runs the async Socket.IO event loop."""
        try:
            import socketio
        except ImportError:
            logger.error("python-socketio not installed – Socket.IO streaming disabled")
            return

        sio = socketio.Server(
            cors_allowed_origins="*",
            async_mode="threading",
            logger=False,
            engineio_logger=False,
        )

        @sio.on("connect", namespace="/")
        def on_connect(sid, environ):
            logger.info("Client connected: sid=%s", sid)

        @sio.on("disconnect", namespace="/")
        def on_disconnect(sid):
            logger.info("Client disconnected: sid=%s", sid)

        self._server = socketio.Server()
        # Transfer event handlers to the main server instance
        # (keep reference for emit calls)
        self._server = sio

        app = socketio.WSGIApp(sio)
        import werkzeug.serving as serving

        self._sio_app = app
        self._running = True

        try:
            serving.run_simple(
                self.host,
                self.port,
                app,
                threaded=True,
                use_reloader=False,
            )
        except Exception as e:
            logger.error("Socket.IO server error: %s", e)

    def _emit_via_socketio(self, room: str, event: dict) -> None:
        """Send event to room via the running server instance."""
        if self._server is None:
            return

        # Defer to the server's emit in the same async context
        def _emit():
            try:
                self._server.emit("message", event, room=room, namespace="/")
            except Exception:
                pass

        # If we have an async loop running, schedule; otherwise fire in a thread
        if self._io_loop is not None:
            import asyncio
            asyncio.get_event_loop().call_soon_threadsafe(_emit)
        else:
            t = threading.Thread(target=_emit, daemon=True)
            t.start()
            t.join(timeout=0.1)


# ------------------------------------------------------------------
# Singleton factory (one server per process)
# ------------------------------------------------------------------
_io_server_instance: Optional[SocketIOServer] = None
_io_lock = threading.Lock()


def get_socketio_server(host: str = "0.0.0.0", port: int = 8081) -> SocketIOServer:
    """Get or create the singleton SocketIOServer."""
    global _io_server_instance
    with _io_lock:
        if _io_server_instance is None:
            _io_server_instance = SocketIOServer(host=host, port=port)
        return _io_server_instance