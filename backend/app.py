"""
app.py — FastAPI application, lifespan, routes.

Sensor integration:
  POST /sensor  — receive ESP32 readings (2 load cells + 2 IR sensors).
                  Stores readings in state and broadcasts directly to dashboard.
                  No cross-verification with YOLO — sensors are display-only.
"""

import asyncio
import json
import logging
import os
import io
from datetime import datetime

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel
from ultralytics import YOLO
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import StreamingResponse, HTMLResponse
import backend.state as state
from backend.config import (
    MODEL_PATH,
    WARP_FILE,
    WARP_W,
    WARP_H,
    CLASS_NAMES,
    SHELF_TO_SLOT,
)
from backend.websocket import manager
from backend.inference import run_inference, start_inference_thread
from backend.inventory import (
    build_payload,
    reload_regions,
    SHELF_REGIONS,
    blank_inventory,
    shelf_total,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("warehouse.app")


# ── Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    state.main_loop = asyncio.get_event_loop()

    if os.path.exists(WARP_FILE):
        state.warp_matrix = np.load(WARP_FILE)
        logger.info(f"Warp matrix loaded from {WARP_FILE}")
    else:
        logger.info("No warp.npy — running on raw frames.")

    logger.info(f"Loading YOLO model: {MODEL_PATH}")
    state.model = YOLO(MODEL_PATH)
    logger.info(f"Model ready. Classes: {list(state.model.names.values())}")

    start_inference_thread()
    yield
    logger.info("Server shutting down.")


app = FastAPI(title="Warehouse Digital Twin", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
# ── WebSocket ──────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    # Push current inventory immediately on connect (shelf C excluded via build_payload)
    inv = state.get_inventory()
    if inv:
        payload = build_payload(inv)
        await websocket.send_text(json.dumps({
            "type":  "inventory",
            "slots": payload,
        }))

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


# ── POST /sensor ───────────────────────────────────────────────────────────

class SensorPayload(BaseModel):
    sensor_id:  str    # "1" or "2"
    weight_kg:  float  # load cell reading
    ir_blocked: bool   # IR proximity — True = object detected

@app.post("/sensor", summary="Receive ESP32 sensor reading (load cell + IR)")
async def sensor_input(payload: SensorPayload):
    sid = str(payload.sensor_id)
    if sid not in ("1", "2"):
        raise HTTPException(status_code=400, detail=f"Unknown sensor_id: {sid}. Must be '1' or '2'.")

    reading = {
        "sensor_id":  sid,
        "weight_kg":  round(payload.weight_kg, 4),
        "ir_blocked": payload.ir_blocked,
        "timestamp":  datetime.now().strftime("%H:%M:%S"),
    }

    # Store in shared state so /status can return it
    state.set_sensor(reading)

    # Map sensor_id to shelf letter for the dashboard.
    # Shelf C (sensor_id "3" if ever added) is intentionally excluded —
    # Shelf C is YOLO-only; its sensor card is static on the frontend.
    SENSOR_TO_SHELF = {"1": "A", "2": "B"}
    shelf_letter = SENSOR_TO_SHELF.get(sid)
    if shelf_letter is None:
        # sensor_id maps to a shelf that must not receive sensor updates
        return reading

    # Broadcast directly to dashboard — no gating, no cross-check
    await manager.broadcast({
        "type":       "sensor",
        "shelf":      shelf_letter,
        "weight_kg":  reading["weight_kg"],
        "ir_blocked": reading["ir_blocked"],
    })

    logger.info(
        f"[SENSOR] id={sid}  shelf={shelf_letter}  weight={reading['weight_kg']} kg  "
        f"ir={'BLOCKED' if reading['ir_blocked'] else 'CLEAR'}"
    )
    return reading


# ── GET /status ────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    inv = state.get_inventory()
    return {
        "status":        "ok",
        "clients":       manager.client_count(),
        "warped":        state.warp_matrix is not None,
        "inventory":     inv,
        "total_objects": sum(shelf_total(c) for c in inv.values()),
        "classes":       list(CLASS_NAMES.values()),
        "shelves":       list(SHELF_REGIONS.keys()),
        "sensor":        state.get_sensor(),
    }


# ── POST /set-slot ─────────────────────────────────────────────────────────

class SetSlotRequest(BaseModel):
    col:   int
    row:   int
    items: list[str]

@app.post("/set-slot")
async def set_slot(req: SetSlotRequest):
    valid    = set(CLASS_NAMES.values())
    filtered = [i for i in req.items if i in valid]
    payload  = [{"col": req.col, "row": req.row, "items": filtered}]
    await manager.broadcast({"type": "inventory", "slots": payload})
    return {"status": "sent", "payload": payload}


# ── POST /infer-image ──────────────────────────────────────────────────────

class InferImageRequest(BaseModel):
    path: str

@app.post("/infer-image")
async def infer_image(req: InferImageRequest):
    frame = cv2.imread(req.path)
    if frame is None:
        raise HTTPException(status_code=400, detail=f"Cannot read: {req.path}")
    if state.warp_matrix is not None:
        frame = cv2.warpPerspective(frame, state.warp_matrix, (WARP_W, WARP_H))
    inventory, _ = run_inference(frame)
    payload = build_payload(inventory)
    await manager.broadcast({"type": "inventory", "slots": payload})
    return {"inventory": inventory, "payload": payload}


# ── POST /simulate ─────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    inventory: dict

@app.post("/simulate")
async def simulate(req: SimulateRequest):
    base = blank_inventory()
    for shelf, counts in req.inventory.items():
        if shelf not in base:
            raise HTTPException(status_code=400, detail=f"Unknown shelf: {shelf}")
        for cls, cnt in counts.items():
            if cls not in base[shelf]:
                raise HTTPException(status_code=400, detail=f"Unknown class: {cls}")
            base[shelf][cls] = int(cnt)
    payload = build_payload(base)
    await manager.broadcast({"type": "inventory", "slots": payload})
    return {"status": "simulated", "payload": payload}


# ── GET /debug-frame ───────────────────────────────────────────────────────

@app.get("/debug-frame")
async def debug_frame_endpoint():
    with state.debug_lock:
        frame = state.debug_frame.copy() if state.debug_frame is not None else None
    if frame is None:
        raise HTTPException(status_code=503, detail="No frame available yet.")
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return StreamingResponse(io.BytesIO(buf.tobytes()), media_type="image/jpeg")


# ── GET /inventory ─────────────────────────────────────────────────────────

@app.get("/inventory")
async def get_inventory_current():
    """Return the latest in-memory inventory (as tracked by YOLO inference)."""
    inv = state.get_inventory()
    return {"shelves": inv, "total_shelves": len(inv)}


# ── GET /history ────────────────────────────────────────────────────────────

@app.get("/history")
async def get_history(limit: int = 100, shelf: str = None):
    """Return recent log entries from the Excel logger (in-memory log)."""
    try:
        from logger import get_log
        log = get_log()
    except ImportError:
        return {"count": 0, "history": []}

    if shelf:
        log = [r for r in log if r.get("Shelf", "").upper() == shelf.upper()]

    log = list(reversed(log))[:limit]
    return {"count": len(log), "history": log}


# ── POST /reload-rois ──────────────────────────────────────────────────────

@app.post("/reload-rois")
async def reload_rois():
    reload_regions()
    return {"status": "reloaded", "shelves": list(SHELF_REGIONS.keys())}
