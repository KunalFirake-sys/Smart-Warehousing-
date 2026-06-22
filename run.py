"""
run.py — Smart Warehouse Digital Twin entry point.

Workflow:
  1. Open camera, flush buffer, grab reference frame
  2. STEP 1 — Perspective corner calibration (4-click warp)
  3. STEP 2 — Shelf ROI drawing (A–G)
  4. STEP 3 — Launch FastAPI server in background (uvicorn)
  5. STEP 4 — Live debug window (reads backend.state.debug_frame)

Keys / controls documented inline.
"""

import cv2
import numpy as np
import threading
import time
import uvicorn
import os
import json
import sys

# ── Config (mirrors backend/config.py — kept here to avoid importing FastAPI
#    before the server thread starts) ─────────────────────────────────────
CAMERA_INDEX = 0
WARP_W       = 500
WARP_H       = 400
WARP_FILE    = "warp.npy"
ROI_FILE     = "shelf_rois.json"
FONT         = cv2.FONT_HERSHEY_SIMPLEX
SHELF_LABELS = ["A", "B", "C", "D", "E", "F", "G"]
COLOR_ROI    = (0, 220, 220)
COLOR_CORNER = (0, 255, 0)
COLOR_DRAW   = (0, 100, 255)


# =============================================================================
# UTILITY — open camera safely
# =======================================================================q======
def open_camera(index: int) -> cv2.VideoCapture:
    for backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            return cap
    return cv2.VideoCapture(index)


def grab_frame(index: int) -> np.ndarray | None:
    """Open camera, flush buffer, return one frame, release."""
    cap = open_camera(index)
    if not cap.isOpened():
        return None
    for _ in range(8):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


# =============================================================================
# STEP 1 — CORNER SELECTION + PERSPECTIVE WARP
# =============================================================================
def step1_corner_calibration(raw_frame: np.ndarray) -> np.ndarray | None:
    """
    Show raw_frame, let user click 4 corners.
    ENTER  → compute + save warp.npy, return warped frame
    ESC    → skip warp, delete old warp.npy, return raw_frame copy
    Returns the (warped or raw) frame to use for ROI drawing.
    """
    print("\n=== STEP 1: Corner Calibration ===")
    print("Click the 4 corners of the warehouse shelf area:")
    print("  1=Top-Left  2=Top-Right  3=Bottom-Right  4=Bottom-Left")
    print("  ENTER = confirm & save   ESC = skip warp\n")

    corners: list[tuple[int, int]] = []
    corner_lbls = ["TL", "TR", "BR", "BL"]

    def on_click(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
            corners.append((x, y))
            print(f"  Corner {len(corners)} ({corner_lbls[len(corners)-1]}): ({x},{y})")

    WIN = "Step 1: Click 4 corners  |  ENTER=confirm  ESC=skip"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 960, 640)
    cv2.setMouseCallback(WIN, on_click)

    warp_M = None

    while True:
        disp = raw_frame.copy()

        for i, pt in enumerate(corners):
            cv2.circle(disp, pt, 8, COLOR_CORNER, -1)
            cv2.putText(disp, corner_lbls[i], (pt[0] + 10, pt[1] - 8),
                        FONT, 0.7, COLOR_CORNER, 2)

        if len(corners) >= 2:
            for i in range(len(corners) - 1):
                cv2.line(disp, corners[i], corners[i + 1], COLOR_CORNER, 2)

        if len(corners) == 4:
            cv2.line(disp, corners[3], corners[0], COLOR_CORNER, 2)
            src = np.array(corners, dtype=np.float32)
            dst = np.array([[0, 0], [WARP_W, 0],
                            [WARP_W, WARP_H], [0, WARP_H]], dtype=np.float32)
            M       = cv2.getPerspectiveTransform(src, dst)
            preview = cv2.warpPerspective(raw_frame, M, (WARP_W, WARP_H))
            scale   = 200 / WARP_W
            small   = cv2.resize(preview, (200, int(WARP_H * scale)))
            sh, sw  = small.shape[:2]
            ox = disp.shape[1] - sw - 10
            oy = 50
            disp[oy:oy + sh, ox:ox + sw] = small
            cv2.rectangle(disp, (ox - 1, oy - 1), (ox + sw + 1, oy + sh + 1), COLOR_ROI, 1)
            cv2.putText(disp, "warp preview", (ox, oy - 5), FONT, 0.38, COLOR_ROI, 1)

        cv2.rectangle(disp, (0, 0), (disp.shape[1], 40), (0, 0, 0), -1)
        cv2.putText(disp, f"Corners: {len(corners)}/4  |  ENTER=confirm  ESC=skip",
                    (10, 26), FONT, 0.65, (0, 220, 120), 2)
        cv2.imshow(WIN, disp)

        key = cv2.waitKey(20) & 0xFF

        if key == 13 and len(corners) == 4:   # ENTER
            src    = np.array(corners, dtype=np.float32)
            dst    = np.array([[0, 0], [WARP_W, 0],
                               [WARP_W, WARP_H], [0, WARP_H]], dtype=np.float32)
            warp_M = cv2.getPerspectiveTransform(src, dst)
            np.save(WARP_FILE, warp_M)
            print(f"Warp matrix saved → {WARP_FILE}")
            warped = cv2.warpPerspective(raw_frame, warp_M, (WARP_W, WARP_H))
            cv2.destroyAllWindows()
            return warped

        if key == 27:                          # ESC
            print("Warp skipped — using raw frame.")
            if os.path.exists(WARP_FILE):
                os.remove(WARP_FILE)
                print(f"Removed old {WARP_FILE}")
            cv2.destroyAllWindows()
            return raw_frame.copy()


# =============================================================================
# STEP 2 — DRAW SHELF ROIs
# =============================================================================
def step2_roi_drawing(base_frame: np.ndarray) -> dict:
    """
    Let user drag boxes and press A–G to assign shelf labels.
    ENTER  → save shelf_rois.json, return regions dict
    ESC    → skip ROI step, return empty dict
    """
    print("\n=== STEP 2: Draw Shelf ROIs ===")
    print("  Drag to draw a box → press A–G to assign a shelf label")
    print("  Redraw any shelf by drawing again and pressing its letter")
    print("  ENTER = finish   ESC = skip\n")

    shelf_regions: dict[str, tuple] = {}
    s = {"drawing": False, "x0": 0, "y0": 0, "x1": 0, "y1": 0,
         "pending": None, "done": False}

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            s.update(drawing=True, x0=x, y0=y, x1=x, y1=y, pending=None)
        elif event == cv2.EVENT_MOUSEMOVE and s["drawing"]:
            s["x1"] = x
            s["y1"] = y
        elif event == cv2.EVENT_LBUTTONUP:
            s["drawing"] = False
            w = abs(s["x1"] - s["x0"])
            h = abs(s["y1"] - s["y0"])
            if w > 8 and h > 8:
                s["pending"] = (
                    min(s["x0"], s["x1"]), min(s["y0"], s["y1"]),
                    max(s["x0"], s["x1"]), max(s["y0"], s["y1"]),
                )
                print("  Box drawn — press A–G to assign shelf label")

    WIN = "Step 2: Draw shelf ROIs  |  drag=draw  A–G=assign  ENTER=done  ESC=skip"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 900, 700)
    cv2.setMouseCallback(WIN, on_mouse)

    while not s["done"]:
        disp = base_frame.copy()

        # Saved ROIs
        for lbl, (rx1, ry1, rx2, ry2) in shelf_regions.items():
            cv2.rectangle(disp, (rx1, ry1), (rx2, ry2), COLOR_ROI, 2)
            cv2.putText(disp, lbl, (rx1 + 4, ry1 + 18), FONT, 0.65, COLOR_ROI, 2)

        # Rubber-band / pending box
        if s["drawing"] or s["pending"]:
            bx0 = min(s["x0"], s["x1"])
            by0 = min(s["y0"], s["y1"])
            bx1 = max(s["x0"], s["x1"])
            by1 = max(s["y0"], s["y1"])
            col = COLOR_DRAW if s["drawing"] else (0, 60, 220)
            cv2.rectangle(disp, (bx0, by0), (bx1, by1), col, 2)
            if s["pending"]:
                cv2.putText(disp, "press A-G to assign",
                            (bx0, max(by0 - 6, 12)), FONT, 0.44, COLOR_DRAW, 1)

        # Status bar
        assigned = sorted(shelf_regions.keys())
        missing  = [l for l in SHELF_LABELS if l not in shelf_regions]
        cv2.rectangle(disp, (0, 0), (disp.shape[1], 42), (0, 0, 0), -1)
        cv2.putText(disp,
                    f"Done: {assigned}   Missing: {missing}   ENTER=finish",
                    (8, 27), FONT, 0.52, (0, 220, 120), 2)

        cv2.imshow(WIN, disp)
        key = cv2.waitKey(20) & 0xFF

        # A–G assignment
        for lbl in SHELF_LABELS:
            if key in (ord(lbl.lower()), ord(lbl.upper())):
                if s["pending"]:
                    shelf_regions[lbl] = s["pending"]
                    print(f"  Shelf {lbl} = {s['pending']}")
                    s["pending"] = None

        if key == 13:    # ENTER
            if not shelf_regions:
                print("  No shelves drawn — draw at least one.")
            else:
                s["done"] = True

        if key == 27:    # ESC
            print("  ROI step skipped.")
            s["done"] = True

    cv2.destroyAllWindows()

    if shelf_regions:
        with open(ROI_FILE, "w") as f:
            json.dump(shelf_regions, f, indent=2)
        print(f"\nROIs saved → {ROI_FILE}")
        for lbl in SHELF_LABELS:
            if lbl in shelf_regions:
                r = shelf_regions[lbl]
                print(f'  "{lbl}": {r}')
    else:
        print("No ROIs defined — backend will use hardcoded defaults.")

    return shelf_regions


# =============================================================================
# STEP 3 — START FASTAPI SERVER IN BACKGROUND
# =============================================================================
def start_server() -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(
        "backend.app:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    thread.start()
    return server, thread


# =============================================================================
# STEP 4 — LIVE DEBUG WINDOW (main thread)
# =============================================================================
def live_debug_window(server: uvicorn.Server, server_thread: threading.Thread):
    import backend.state as state  # safe to import now — server already started

    WIN = "Warehouse CV Live  |  Q=close window"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 800, 640)

    print("\nDebug window open.  Q=close window  Ctrl+C=stop server\n")

    while True:
        with state.debug_lock:
            frame = state.debug_frame.copy() if state.debug_frame is not None else None

        if frame is None:
            placeholder = np.full((WARP_H, WARP_W, 3), 18, dtype=np.uint8)
            cv2.putText(placeholder, "Waiting for inference...",
                        (40, WARP_H // 2), FONT, 0.7, (55, 55, 55), 2)
            cv2.imshow(WIN, placeholder)
        else:
            cv2.imshow(WIN, frame)

        key = cv2.waitKey(30) & 0xFF
        if key == ord('q'):
            print("Debug window closed. Server still running.")
            break
        if not server_thread.is_alive():
            print("ERROR: Server thread died unexpectedly.")
            break

    cv2.destroyAllWindows()


# =============================================================================
# MAIN
# =============================================================================
def main():
    print("=" * 60)
    print("  Smart Warehouse Digital Twin — Startup")
    print("=" * 60)

    # ── Open camera & grab reference frame ────────────────────────────────
    print(f"\nOpening camera {CAMERA_INDEX}...")
    raw_frame = grab_frame(CAMERA_INDEX)
    if raw_frame is None:
        print(f"ERROR: Cannot open camera {CAMERA_INDEX}. Exiting.")
        sys.exit(1)
    print(f"Camera OK — {raw_frame.shape[1]}x{raw_frame.shape[0]}")

    # ── Step 1: corner calibration ────────────────────────────────────────
    base_frame = step1_corner_calibration(raw_frame)

    # ── Step 2: ROI drawing ───────────────────────────────────────────────
    step2_roi_drawing(base_frame)

    # ── Step 3: start server ──────────────────────────────────────────────
    print("\nStarting FastAPI server on http://0.0.0.0:8000 ...")
    server, server_thread = start_server()

    print("Waiting for server to initialise (5 s)...")
    time.sleep(5)

    if not server_thread.is_alive():
        print("ERROR: Server failed to start. Check errors above.")
        sys.exit(1)

    print("Server ready at http://localhost:8000")
    print("  WebSocket : ws://localhost:8000/ws")
    print("  Status    : http://localhost:8000/status")

    # ── Step 4: live debug window (blocks until Q pressed) ────────────────
    live_debug_window(server, server_thread)

    # ── Keep alive until Ctrl+C ───────────────────────────────────────────
    print("\nPress Ctrl+C to stop the server.")
    try:
        while server_thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.should_exit = True
        server_thread.join(timeout=5)
        print("Done.")


if __name__ == "__main__":
    main()
