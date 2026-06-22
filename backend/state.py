"""
state.py — Thread-safe shared state for the entire backend.
"""

import asyncio
import threading
import numpy as np
from typing import Optional

_state_lock = threading.Lock()

latest_inventory: dict = {}
alert_state:      dict = {}

# Latest reading from POST /sensor
# {"sensor_id": "1", "weight_kg": 0.35, "ir_blocked": False, "timestamp": "14:02:11"}
latest_sensor: dict = {}

debug_frame: Optional[np.ndarray] = None
debug_lock  = threading.Lock()

main_loop: Optional[asyncio.AbstractEventLoop] = None
model      = None
warp_matrix: Optional[np.ndarray] = None


def get_inventory() -> dict:
    with _state_lock:
        return dict(latest_inventory)


def set_inventory(inv: dict) -> bool:
    global latest_inventory
    with _state_lock:
        changed = inv != latest_inventory
        if changed:
            latest_inventory = inv
    return changed


def get_alert_state(shelf: str) -> int:
    with _state_lock:
        return alert_state.get(shelf, -1)


def set_alert_state(shelf: str, count: int):
    with _state_lock:
        alert_state[shelf] = count


def set_sensor(data: dict):
    global latest_sensor
    with _state_lock:
        latest_sensor = data


def get_sensor() -> dict:
    with _state_lock:
        return dict(latest_sensor)
