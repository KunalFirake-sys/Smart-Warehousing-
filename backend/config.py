"""
config.py — Single source of truth for all constants.
"""

# ── Model ──────────────────────────────────────────────────────────────────
MODEL_PATH     = r"C:\Codes\warehouse\best.pt"
IMGSZ          = 512
CONF           = 0.25   # detection threshold — all boxes drawn on debug window
DASHBOARD_CONF = 0.70   # minimum confidence to count into inventory + dashboard
DEVICE         = "cpu"  # swap to 0 for GPU

# ── Camera ─────────────────────────────────────────────────────────────────
CAMERA_INDEX = 0
INFER_EVERY  = 1.0      # seconds between inference passes

# ── Perspective warp ───────────────────────────────────────────────────────
WARP_W    = 500
WARP_H    = 400
WARP_FILE = "warp.npy"

# ── Shelf ROI ──────────────────────────────────────────────────────────────
ROI_FILE = "shelf_rois.json"

DEFAULT_REGIONS: dict[str, tuple[int, int, int, int]] = {
    "A": (21,  183, 125, 346),
    "B": (144, 189, 244, 341),
    "C": (150, 21,  247, 172),
    "D": (268, 190, 368, 341),
    "E": (270, 23,  371, 172),
    "F": (390, 192, 490, 335),
    "G": (391, 22,  491, 171),
}

SHELF_TO_SLOT: dict[str, tuple[int, int]] = {
    "A": (0, 0),
    "B": (1, 0),
    "C": (1, 1),
    "D": (2, 0),
    "E": (2, 1),
    "F": (3, 0),
    "G": (3, 1),
}

# ── Class names (exact order from data.yaml, nc: 7) ────────────────────────
CLASS_NAMES: dict[int, str] = {
    0: "bottle",
    1: "chips",
    2: "cubes",
    3: "cups",
    4: "cylinders",
    5: "small_cups",
    6: "tin can",
}

# ── Class display colours (BGR for OpenCV) ─────────────────────────────────
CLASS_BASE: dict[str, str] = {
    "cubes":      "cube",
    "cylinders":  "cylinder",
    "chips":      "cube",
    "bottle":     "cylinder",
    "cups":       "cylinder",
    "small_cups": "cylinder",
    "tin can":    "cylinder",
}

CLASS_COLORS: dict[str, tuple[int, int, int]] = {
    "bottle":     (50,  187,  68),
    "chips":      (0,   204, 255),
    "cubes":      (220, 100,  30),
    "cups":       (100, 134, 200),
    "cylinders":  (30,  220, 140),
    "small_cups": (240, 240, 240),
    "tin can":    (200, 187, 170),
}

COLOR_ROI = (0, 220, 220)
FONT      = 0   # cv2.FONT_HERSHEY_SIMPLEX
