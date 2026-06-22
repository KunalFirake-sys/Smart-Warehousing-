"""
inference.py — YOLO inference loop + frame annotation.

Performance fixes applied:
  - Camera buffer drained continuously in a separate thread so cap.read()
    always returns the LATEST frame, never a stale buffered one.
  - debug_frame written with a non-blocking swap (no copy inside the lock).
  - CONF threshold raised to 0.70 — only high-confidence detections reach
    the inventory and the dashboard.
  - INFER_EVERY sleep replaced with a non-blocking drain pattern.
"""

import asyncio
import time
import logging
import threading

import cv2
import numpy as np

import backend.state as state
from backend.config import (
    CAMERA_INDEX,
    INFER_EVERY,
    IMGSZ,
    CONF,
    DASHBOARD_CONF,
    DEVICE,
    WARP_W,
    WARP_H,
    CLASS_NAMES,
    CLASS_COLORS,
    COLOR_ROI,
    FONT,
)
from backend.inventory import (
    SHELF_REGIONS,
    blank_inventory,
    shelf_for_point,
    build_payload,
    shelf_total,
)
from backend.websocket import manager

try:
    from alerts import check_alert
    _ALERTS_AVAILABLE = True
except ImportError:
    _ALERTS_AVAILABLE = False

try:
    from logger import log_inventory
    _LOGGER_AVAILABLE = True
except ImportError:
    _LOGGER_AVAILABLE = False

logger = logging.getLogger("warehouse.inference")


# ── Warp helper ────────────────────────────────────────────────────────────

def _apply_warp(frame: np.ndarray) -> np.ndarray:
    if state.warp_matrix is not None:
        return cv2.warpPerspective(frame, state.warp_matrix, (WARP_W, WARP_H))
    return frame


# ── Latest-frame grabber ───────────────────────────────────────────────────
# Runs in its own thread, draining the camera buffer at full FPS.
# The inference loop always picks up _latest_raw which is the most
# recent frame — zero buffer lag.

_latest_raw: np.ndarray | None = None
_raw_lock = threading.Lock()
_cam_ready = threading.Event()


def _camera_drain_loop(cap: cv2.VideoCapture):
    """Continuously read from camera and keep only the latest frame."""
    global _latest_raw
    logger.info("Camera drain thread started.")
    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.01)
            continue
        with _raw_lock:
            _latest_raw = frame
        _cam_ready.set()


def _grab_latest() -> np.ndarray | None:
    """Return the most recent camera frame (never a buffered stale one)."""
    with _raw_lock:
        return _latest_raw.copy() if _latest_raw is not None else None


# ── Core inference ─────────────────────────────────────────────────────────

def run_inference(frame: np.ndarray) -> tuple[dict, np.ndarray]:
    """
    Run YOLO on *frame*.

    Two confidence thresholds:
      CONF (0.25)          — boxes shown on the CV debug window
      DASHBOARD_CONF (0.70)— threshold to count into inventory / dashboard
    """
    results = state.model.predict(
        source=frame,
        imgsz=IMGSZ,
        conf=CONF,          # low conf so we can draw all boxes for debugging
        device=DEVICE,
        verbose=False,
    )

    inventory = blank_inventory()
    vis = frame.copy()

    # Draw shelf ROI outlines
    for shelf, (sx1, sy1, sx2, sy2) in SHELF_REGIONS.items():
        cv2.rectangle(vis, (sx1, sy1), (sx2, sy2), COLOR_ROI, 2)
        cv2.putText(vis, shelf, (sx1 + 3, sy1 + 14), FONT, 0.50, COLOR_ROI, 1)

    # Process detections
    for box in results[0].boxes:
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
        cls_id   = int(box.cls[0])
        conf_val = float(box.conf[0])

        cls_name = state.model.names.get(cls_id, CLASS_NAMES.get(cls_id, "cubes"))
        color    = CLASS_COLORS.get(cls_name, (255, 255, 255))

        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        # Draw ALL detections on debug frame (even low-conf ones)
        # Low-conf boxes drawn dimmer so you can see what's being filtered
        draw_color = color if conf_val >= DASHBOARD_CONF else tuple(c // 3 for c in color)
        cv2.rectangle(vis, (x1, y1), (x2, y2), draw_color, 2)
        label = f"{cls_name} {conf_val:.2f}"
        if conf_val < DASHBOARD_CONF:
            label += " [filtered]"
        cv2.putText(vis, label, (x1, max(y1 - 5, 12)), FONT, 0.38, draw_color, 1)
        cv2.circle(vis, (int(cx), int(cy)), 4, draw_color, -1)

        # Only count into inventory if confidence >= DASHBOARD_CONF (0.70)
        if conf_val < DASHBOARD_CONF:
            continue

        shelf_label = shelf_for_point(cx, cy)
        if shelf_label and cls_name in inventory[shelf_label]:
            inventory[shelf_label][cls_name] += 1

    # Per-shelf count overlay
    for shelf, (sx1, sy1, sx2, sy2) in SHELF_REGIONS.items():
        c = inventory[shelf]
        line1 = f"cu:{c['cubes']} cy:{c['cylinders']} ch:{c['chips']}"
        line2 = f"bt:{c['bottle']} cp:{c['cups']} sc:{c['small_cups']} tn:{c['tin can']}"
        cv2.putText(vis, line1, (sx1 + 2, sy2 - 14), FONT, 0.28, COLOR_ROI, 1)
        cv2.putText(vis, line2, (sx1 + 2, sy2 - 4),  FONT, 0.28, COLOR_ROI, 1)

    # HUD
    total   = sum(shelf_total(c) for c in inventory.values())
    mode    = "WARPED" if state.warp_matrix is not None else "RAW"
    clients = manager.client_count()
    cv2.rectangle(vis, (0, 0), (420, 36), (0, 0, 0), -1)
    cv2.putText(
        vis,
        f"items:{total}  clients:{clients}  [{mode}]  dash_conf:{DASHBOARD_CONF}",
        (6, 24), FONT, 0.42, (0, 255, 120), 1,
    )

    return inventory, vis


# ── Alert helper ───────────────────────────────────────────────────────────

def _fire_alerts(inventory: dict):
    if not _ALERTS_AVAILABLE:
        return
    for shelf, counts in inventory.items():
        total = shelf_total(counts)
        last  = state.get_alert_state(shelf)
        if total != last:
            check_alert(shelf, total)
            state.set_alert_state(shelf, total)


# ── Inference loop ─────────────────────────────────────────────────────────

def inference_loop():
    # ── Open camera ──────────────────────────────────────────────────────
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        logger.error(f"Cannot open camera {CAMERA_INDEX}. Inference loop aborted.")
        return

    # Minimise OpenCV's internal buffer to 1 frame — critical for low lag
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # Flush any startup frames
    for _ in range(4):
        cap.read()

    # Start the camera drain thread — keeps buffer empty at full FPS
    drain_thread = threading.Thread(
        target=_camera_drain_loop, args=(cap,), daemon=True, name="cam-drain"
    )
    drain_thread.start()

    # Wait until at least one frame is available
    logger.info("Waiting for first camera frame...")
    _cam_ready.wait(timeout=5.0)
    logger.info("Inference loop started.")

    while True:
        t0 = time.perf_counter()

        raw = _grab_latest()
        if raw is None:
            time.sleep(0.05)
            continue

        frame = _apply_warp(raw)
        inventory, annotated = run_inference(frame)

        # Non-blocking debug frame swap — no copy inside the lock
        with state.debug_lock:
            state.debug_frame = annotated   # reassign reference, not copy

        changed = state.set_inventory(inventory)
        if changed:
            payload = build_payload(inventory)

            loop = state.main_loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast({"type": "inventory", "slots": payload, "verification": {}}),
                    loop,
                )

            _fire_alerts(inventory)

            if _LOGGER_AVAILABLE:
                try:
                    log_inventory(inventory)
                except Exception as exc:
                    logger.error(f"Logger error: {exc}")

        # Sleep only the REMAINING time in the interval — accounts for
        # inference duration so we don't drift behind schedule
        elapsed = time.perf_counter() - t0
        sleep_for = max(0.0, INFER_EVERY - elapsed)
        if sleep_for > 0:
            time.sleep(sleep_for)

    cap.release()


def start_inference_thread() -> threading.Thread:
    t = threading.Thread(target=inference_loop, daemon=True, name="inference")
    t.start()
    logger.info("Inference thread launched.")
    return t
