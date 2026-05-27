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

# Score threshold from GroundingDINO ranges 0-1; tuned for high precision.
BOX_THRESHOLD = 0.30
TEXT_THRESHOLD = 0.25
MIN_AREA_PX = 1500
MAX_AREA_FRAC = 0.40

MODEL_ID = "IDEA-Research/grounding-dino-base"

_MODEL = None
_PROCESSOR = None
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
        contrast = _bbox_contrast(np_image, bbox)
        severity = _severity(area / image_area, contrast)

        crop = image.crop(bbox)
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
                    "area_frac": area / image_area,
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
