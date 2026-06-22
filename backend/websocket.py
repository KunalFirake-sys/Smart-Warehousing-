"""
websocket.py — WebSocket connection manager.

Responsibilities:
  - Track all live client connections
  - Broadcast JSON messages to all clients
  - Silently clean up dead connections
"""

import json
import asyncio
import logging
from fastapi import WebSocket

logger = logging.getLogger("warehouse.ws")


class ConnectionManager:
    def __init__(self):
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)
        logger.info(f"[WS] Client connected  — total: {len(self._clients)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)
        logger.info(f"[WS] Client disconnected — total: {len(self._clients)}")

    async def broadcast(self, payload: dict):
        """Send payload (as JSON) to every connected client."""
        if not self._clients:
            return

        message = json.dumps(payload)
        dead: list[WebSocket] = []

        async with self._lock:
            targets = list(self._clients)

        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._clients:
                        self._clients.remove(ws)
            logger.warning(f"[WS] Pruned {len(dead)} dead connection(s).")

    def client_count(self) -> int:
        return len(self._clients)


# Singleton used by the whole application
manager = ConnectionManager()
