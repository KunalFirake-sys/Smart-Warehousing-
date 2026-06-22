"""
inventory.py — Inventory construction and payload conversion.

Responsibilities:
  - Build a blank inventory structure
  - Assign a detected object (cx, cy) to the correct shelf ROI
  - Convert internal inventory dict to the frontend slot payload
"""

import os
import json
import logging
from backend.config import (
    ROI_FILE,
    DEFAULT_REGIONS,
    SHELF_TO_SLOT,
    CLASS_NAMES,
)

logger = logging.getLogger("warehouse.inventory")


# ── ROI loader ────────────────────────────────────────────────────────────

def load_shelf_regions() -> dict[str, tuple[int, int, int, int]]:
    """
    Load shelf ROIs from shelf_rois.json.
    Falls back to DEFAULT_REGIONS when the file is absent.
    """
    if os.path.exists(ROI_FILE):
        with open(ROI_FILE) as f:
            raw: dict = json.load(f)
        regions = {k: tuple(v) for k, v in raw.items()}
        logger.info(f"Loaded {len(regions)} ROIs from {ROI_FILE}: {sorted(regions)}")
        return regions

    logger.warning(f"{ROI_FILE} not found — using hard-coded default ROIs.")
    return dict(DEFAULT_REGIONS)


# Loaded once at import time; refreshed via reload_regions() if needed
SHELF_REGIONS: dict[str, tuple[int, int, int, int]] = load_shelf_regions()


def reload_regions():
    """Hot-reload ROIs without restarting the server."""
    global SHELF_REGIONS
    SHELF_REGIONS = load_shelf_regions()


# ── Blank inventory ────────────────────────────────────────────────────────

def blank_inventory() -> dict:
    """Return a zero-filled inventory for all known shelves and classes."""
    return {
        shelf: {cls: 0 for cls in CLASS_NAMES.values()}
        for shelf in SHELF_REGIONS
    }


# ── Shelf assignment ───────────────────────────────────────────────────────

def shelf_for_point(cx: float, cy: float) -> str | None:
    """
    Return the shelf label whose ROI contains (cx, cy), or None.
    If multiple ROIs overlap, the first match wins (dict insertion order).
    """
    for shelf, (sx1, sy1, sx2, sy2) in SHELF_REGIONS.items():
        if sx1 <= cx <= sx2 and sy1 <= cy <= sy2:
            return shelf
    return None


# ── Payload builder ────────────────────────────────────────────────────────

def build_payload(inventory: dict) -> list[dict]:
    """
    Convert:
        {"A": {"cubes": 2, "chips": 1, ...}, ...}
    To:
        [{"col": 0, "row": 0, "items": ["cubes", "cubes", "chips"]}, ...]

    Only shelves present in SHELF_TO_SLOT are included.
    All shelves including C are passed through — YOLO may update any shelf.
    Sensor messages are blocked from shelf C at the /sensor route level.
    """
    updates = []
    for shelf, counts in inventory.items():
        if shelf not in SHELF_TO_SLOT:
            continue
        col, row = SHELF_TO_SLOT[shelf]
        items: list[str] = []
        for cls_name, count in counts.items():
            items.extend([cls_name] * count)
        updates.append({"col": col, "row": row, "items": items})
    return updates


# ── Total count helper ────────────────────────────────────────────────────

def shelf_total(shelf_counts: dict) -> int:
    return sum(shelf_counts.values())
