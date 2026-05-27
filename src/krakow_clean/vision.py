"""Graffiti detection using IDEA-Research/grounding-dino-base.

GroundingDINO does zero-shot, text-prompted object detection. We prompt with
graffiti-flavoured phrases and accept bounding boxes whose score and area
clear thresholds. Bounding boxes (not masks) are returned — sufficient for
the city report payload.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

# Prompt — GroundingDINO accepts period-separated text phrases.
PROMPT = "graffiti. spray paint. wall tag. street art."

# Score threshold from GroundingDINO ranges 0-1. Tune sweep showed 0.30 misses
# clear graffiti (sample_04) while 0.10 over-fires. 0.20 catches obvious wall
# scribbles with manageable false positives; require ≥2000px to drop tiny
# spurious boxes.
BOX_THRESHOLD = 0.20
TEXT_THRESHOLD = 0.18
MIN_AREA_PX = 2000
MAX_AREA_FRAC = 0.40

# Street lamps, sign posts, and pillars are tall-narrow. Real graffiti is
# usually wider than tall (or roughly square). Reject boxes whose width/height
# ratio drops below this threshold.
MIN_ASPECT_RATIO = 0.45

# Lamp posts and dark sign poles have low color variance across their pixels.
# Graffiti tags use paint with strong color shifts. Drop low-variance boxes.
# Lowered from 22 → 16 after the GZ-on-pink-wall case: red-text on a uniform
# wall produces a bbox where most pixels are still wall colour, dropping the
# std into the high teens. CLIP second-stage catches the lamp-post escapees.
MIN_COLOR_STD = 16.0

# Skin and sky regions also trip the detector occasionally. They're nearly
# monochrome too — covered by the color-std gate.

MODEL_ID = "IDEA-Research/grounding-dino-base"
CLIP_ID = "openai/clip-vit-base-patch32"

# Crop-classifier labels — order matters only for stable index assignment.
# Index 0 is the positive class; everything else is a rejection category.
CLIP_LABELS = [
    "spray paint graffiti tag on a wall",      # 0  POSITIVE
    "a road sign or traffic sign",             # 1
    "a street lamp or lamp post",              # 2
    "a window of a building",                  # 3
    "a tree or vegetation",                    # 4
    "a parked car or vehicle",                 # 5
    "a person or pedestrian",                  # 6
    "a brick wall without graffiti",           # 7
    "a doorway or entrance",                   # 8
    "a billboard or commercial sign",          # 9
]
CLIP_MIN_PROB = 0.30  # positive must beat all negatives by softmax

_MODEL = None
_PROCESSOR = None
_CLIP_MODEL = None
_CLIP_PROCESSOR = None
_DEVICE: torch.device | None = None


@dataclass
class Detection:
    image_id: str
    image_path: Path
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1
    score: float
    area_px: int
    severity: str
    label: str
    crop_path: Path | None = None
    mask_phash: str | None = None
    extras: dict = field(default_factory=dict)


def _device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _load() -> tuple[object, object, torch.device]:
    global _MODEL, _PROCESSOR, _DEVICE
    if _MODEL is None:
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        _DEVICE = _device()
        _PROCESSOR = AutoProcessor.from_pretrained(MODEL_ID)
        _MODEL = (
            AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID)
            .to(_DEVICE)
            .eval()
        )
    return _MODEL, _PROCESSOR, _DEVICE


def _load_clip() -> tuple[object, object, torch.device]:
    global _CLIP_MODEL, _CLIP_PROCESSOR, _DEVICE
    if _CLIP_MODEL is None:
        from transformers import CLIPModel, CLIPProcessor

        _DEVICE = _DEVICE or _device()
        _CLIP_PROCESSOR = CLIPProcessor.from_pretrained(CLIP_ID)
        _CLIP_MODEL = CLIPModel.from_pretrained(CLIP_ID).to(_DEVICE).eval()
    return _CLIP_MODEL, _CLIP_PROCESSOR, _DEVICE


def classify_crop(crop_image: Image.Image) -> tuple[bool, str, float, dict]:
    """Returns (is_graffiti, top_label, top_prob, all_probs).

    The crop is "graffiti" iff index 0 is the argmax AND its softmax prob
    clears CLIP_MIN_PROB.
    """
    model, processor, device = _load_clip()
    inputs = processor(
        text=CLIP_LABELS, images=crop_image, return_tensors="pt", padding=True
    ).to(device)
    with torch.no_grad():
        outputs = model(**inputs)
    probs = outputs.logits_per_image.softmax(dim=-1)[0].cpu().tolist()
    top = int(np.argmax(probs))
    label = CLIP_LABELS[top]
    keep = top == 0 and probs[0] >= CLIP_MIN_PROB
    return keep, label, float(probs[top]), {l: round(p, 3) for l, p in zip(CLIP_LABELS, probs)}


def _severity(area_frac: float, contrast: float) -> str:
    if area_frac >= 0.08 or contrast >= 60:
        return "severe"
    if area_frac >= 0.025 or contrast >= 35:
        return "moderate"
    return "minor"


def _bbox_contrast(image_rgb: np.ndarray, bbox: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = bbox
    inside = image_rgb[y0:y1, x0:x1]
    if inside.size == 0:
        return 0.0
    mean_in = inside.reshape(-1, 3).mean(axis=0)
    h, w, _ = image_rgb.shape
    mask = np.ones((h, w), dtype=bool)
    mask[y0:y1, x0:x1] = False
    outside = image_rgb[mask]
    if outside.size == 0:
        return 0.0
    return float(np.abs(mean_in - outside.reshape(-1, 3).mean(axis=0)).mean())


def _bbox_color_std(image_rgb: np.ndarray, bbox: tuple[int, int, int, int]) -> float:
    """Average per-channel standard deviation inside a bbox. Lamp posts and
    uniform pillars score very low here; tags with multiple colors score
    much higher."""
    x0, y0, x1, y1 = bbox
    region = image_rgb[y0:y1, x0:x1]
    if region.size == 0:
        return 0.0
    return float(region.reshape(-1, 3).std(axis=0).mean())


def _passes_shape_gate(bbox: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = bbox
    w = x1 - x0
    h = y1 - y0
    if w <= 0 or h <= 0:
        return False
    return (w / h) >= MIN_ASPECT_RATIO


def detect(
    image_path: Path,
    image_id: str,
    crop_dir: Path,
    prompt: str = PROMPT,
) -> list[Detection]:
    model, processor, device = _load()
    image = Image.open(image_path).convert("RGB")
    w, h = image.size
    image_area = w * h
    np_image = np.array(image)

    inputs = processor(images=image, text=prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)

    results = processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        threshold=BOX_THRESHOLD,
        text_threshold=TEXT_THRESHOLD,
        target_sizes=[(h, w)],
    )[0]

    detections: list[Detection] = []
    for idx, (box, score, label) in enumerate(
        zip(results["boxes"], results["scores"], results.get("text_labels", results.get("labels", [])))
    ):
        x0, y0, x1, y1 = [int(v) for v in box.tolist()]
        area = max(0, (x1 - x0) * (y1 - y0))
        if area < MIN_AREA_PX:
            continue
        if area / image_area > MAX_AREA_FRAC:
            continue
        bbox = (x0, y0, x1, y1)
        if not _passes_shape_gate(bbox):
            continue
        color_std = _bbox_color_std(np_image, bbox)
        if color_std < MIN_COLOR_STD:
            continue
        contrast = _bbox_contrast(np_image, bbox)
        severity = _severity(area / image_area, contrast)

        crop = image.crop(bbox)
        # Second-stage CLIP check: reject if the crop's argmax label is not
        # "graffiti". Catches road signs, lamps, windows, etc. that the
        # geometric filters miss.
        is_graffiti, top_label, top_prob, all_probs = classify_crop(crop)
        if not is_graffiti:
            continue

        crop_path = crop_dir / f"{image_id}_{idx:02d}.jpg"
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(crop_path, format="JPEG", quality=88)

        detections.append(
            Detection(
                image_id=image_id,
                image_path=image_path,
                bbox=bbox,
                score=float(score),
                area_px=area,
                severity=severity,
                label=str(label),
                crop_path=crop_path,
                extras={
                    "contrast": contrast,
                    "color_std": color_std,
                    "aspect": (x1 - x0) / (y1 - y0),
                    "area_frac": area / image_area,
                    "clip_top_label": top_label,
                    "clip_prob": top_prob,
                    "clip_probs": all_probs,
                },
            )
        )
    return detections


def detect_with_overlay(
    image_path: Path,
    image_id: str,
    out_dir: Path,
    prompt: str = PROMPT,
) -> tuple[list[Detection], Path]:
    """Detect + render a visual overlay with bounding boxes drawn on."""
    detections = detect(image_path, image_id, out_dir, prompt=prompt)
    overlay = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(overlay, "RGBA")
    for det in detections:
        draw.rectangle(det.bbox, outline=(255, 60, 60, 255), width=4)
        draw.text(
            (det.bbox[0] + 6, det.bbox[1] + 6),
            f"{det.label} {det.score:.2f} · {det.severity}",
            fill=(255, 255, 255, 255),
        )
    overlay_path = out_dir / "overlay.jpg"
    overlay.save(overlay_path, format="JPEG", quality=88)
    return detections, overlay_path


def encode_clean_jpeg(crop_path: Path) -> bytes:
    """Re-encode JPEG without EXIF for upload. Quality 86, max edge 1600px."""
    image = Image.open(crop_path).convert("RGB")
    image.thumbnail((1600, 1600), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86, optimize=True, exif=b"")
    return buffer.getvalue()
