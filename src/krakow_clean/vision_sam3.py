"""SAM3 + CLIP hybrid detector.

Architecture:
  1. Batch a list of images to the mlx-sam3 sidecar (Python 3.13 env) —
     single model load, one inference per image, JSON detections back.
  2. For each surviving box, crop the source image and feed the crop into
     the CLIP classifier from vision.py (10-label zero-shot).
  3. Keep only crops whose top CLIP label is the positive class.

This combines SAM3's tighter localisation and higher recall with the
CLIP filter that already rejects road signs / lamp posts / windows.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw

from .vision import (
    CLIP_LABELS,
    MAX_AREA_FRAC,
    MIN_AREA_PX,
    _bbox_color_std,
    _bbox_contrast,
    _severity,
    classify_crop,
)

SAM3_MIN_SCORE = 0.50
SAM3_PROMPT = "graffiti"

# Sticker / leaflet labels — SAM3 can flag flat paper as graffiti. Treat as
# a soft reject: not graffiti for city-form purposes.
# (CLIP already covers most of this with its label set.)

SIDECAR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "mlx_sam3"
SIDECAR_SCRIPT = SIDECAR_DIR / "sidecar.py"


@dataclass
class HybridDetection:
    image_id: str
    image_path: Path
    bbox: tuple[int, int, int, int]
    sam3_score: float
    area_px: int
    severity: str
    crop_path: Path
    mask_phash: str | None = None
    extras: dict = field(default_factory=dict)


def _call_sidecar(image_paths: list[Path]) -> list[dict]:
    """Run the SAM3 sidecar in its 3.13 env. Returns parsed JSON per image."""
    if not SIDECAR_SCRIPT.exists():
        raise RuntimeError(f"sidecar missing at {SIDECAR_SCRIPT}; run uv sync in vendor/mlx_sam3")
    cmd = ["uv", "run", "python", "sidecar.py"] + [str(p.resolve()) for p in image_paths]
    proc = subprocess.run(cmd, cwd=SIDECAR_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"sidecar failed: {proc.stderr[-1000:]}")
    out: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def detect_batch(
    image_paths: list[Path],
    crop_dir: Path,
    *,
    sam3_min_score: float = SAM3_MIN_SCORE,
    image_id_for_path: callable | None = None,
) -> dict[str, list[HybridDetection]]:
    """Run SAM3 on each image, filter with the geometric + CLIP gates."""
    crop_dir.mkdir(parents=True, exist_ok=True)
    sidecar_out = _call_sidecar(image_paths)

    by_path: dict[str, dict] = {entry["image"]: entry for entry in sidecar_out}
    results: dict[str, list[HybridDetection]] = {}

    for image_path in image_paths:
        path_str = str(image_path.resolve())
        entry = by_path.get(path_str)
        if entry is None or "error" in entry:
            results[image_path.stem] = []
            continue
        image = Image.open(image_path).convert("RGB")
        w, h = image.size
        image_area = w * h
        np_image = np.array(image)
        image_id = image_id_for_path(image_path) if image_id_for_path else image_path.stem

        detections: list[HybridDetection] = []
        for idx, raw in enumerate(entry.get("detections", [])):
            if raw["score"] < sam3_min_score:
                continue
            x0, y0, x1, y1 = raw["bbox"]
            x0 = max(0, x0); y0 = max(0, y0)
            x1 = min(w, x1); y1 = min(h, y1)
            area = max(0, (x1 - x0) * (y1 - y0))
            if area < MIN_AREA_PX:
                continue
            if area / image_area > MAX_AREA_FRAC:
                continue
            bbox = (x0, y0, x1, y1)

            crop = image.crop(bbox)
            is_graffiti, top_label, top_prob, all_probs = classify_crop(crop)
            if not is_graffiti:
                continue

            contrast = _bbox_contrast(np_image, bbox)
            color_std = _bbox_color_std(np_image, bbox)
            severity = _severity(area / image_area, contrast)

            crop_path = crop_dir / f"{image_id}_{idx:02d}.jpg"
            crop.save(crop_path, format="JPEG", quality=88)

            detections.append(
                HybridDetection(
                    image_id=image_id,
                    image_path=image_path,
                    bbox=bbox,
                    sam3_score=float(raw["score"]),
                    area_px=area,
                    severity=severity,
                    crop_path=crop_path,
                    extras={
                        "contrast": contrast,
                        "color_std": color_std,
                        "area_frac": area / image_area,
                        "clip_top_label": top_label,
                        "clip_prob": top_prob,
                        "sidecar_elapsed_ms": entry.get("elapsed_ms"),
                    },
                )
            )
        results[image_id] = detections
    return results


def detect_with_overlay(
    image_path: Path,
    image_id: str,
    out_dir: Path,
    *,
    sam3_min_score: float = SAM3_MIN_SCORE,
) -> tuple[list[HybridDetection], Path]:
    """Convenience for visualization: detect on a single image + draw boxes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    detections_by_id = detect_batch([image_path], out_dir, sam3_min_score=sam3_min_score)
    detections = detections_by_id.get(image_id, [])

    image = Image.open(image_path).convert("RGB")
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    for det in detections:
        draw.rectangle(det.bbox, outline=(60, 200, 255, 255), width=4)
        draw.text(
            (det.bbox[0] + 4, det.bbox[1] + 4),
            f"sam3 {det.sam3_score:.2f} · clip {det.extras['clip_prob']:.2f}",
            fill=(255, 255, 255),
        )
    overlay_path = out_dir / "overlay.jpg"
    overlay.save(overlay_path, "JPEG", quality=88)
    return detections, overlay_path
