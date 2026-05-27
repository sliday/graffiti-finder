"""Graffiti detection using facebook/sam3 (HuggingFace transformers).

Text-prompted concept segmentation. We use "graffiti" + variants. Returns
mask metadata and saves cropped JPEGs of detections to disk.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import torch
from PIL import Image

# Lazy imports for transformers — SAM3 weights are several GB; keep the import
# cost off the CLI critical path until a detector is actually requested.
_MODEL = None
_PROCESSOR = None
_DEVICE = None


PROMPT = "graffiti on a wall"
SCORE_THRESHOLD = 0.55
MIN_AREA_PX = 1500
MAX_AREA_FRAC = 0.4


@dataclass
class Detection:
    image_id: str
    image_path: Path
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1
    score: float
    area_px: int
    severity: str
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
        from transformers import Sam3Model, Sam3Processor  # type: ignore[attr-defined]

        _DEVICE = _device()
        _MODEL = Sam3Model.from_pretrained("facebook/sam3").to(_DEVICE).eval()
        _PROCESSOR = Sam3Processor.from_pretrained("facebook/sam3")
    return _MODEL, _PROCESSOR, _DEVICE


@lru_cache(maxsize=1)
def _warm() -> None:
    """Trigger model load eagerly when caller wants startup cost up front."""
    _load()


def _severity(area_frac: float, contrast: float) -> str:
    if area_frac >= 0.08 or contrast >= 60:
        return "severe"
    if area_frac >= 0.025 or contrast >= 35:
        return "moderate"
    return "minor"


def _contrast(image_rgb: np.ndarray, mask: np.ndarray) -> float:
    if mask.sum() == 0:
        return 0.0
    inside = image_rgb[mask]
    outside = image_rgb[~mask]
    if outside.size == 0:
        return 0.0
    return float(abs(inside.mean() - outside.mean()))


def detect(
    image_path: Path,
    image_id: str,
    crop_dir: Path,
    prompt: str = PROMPT,
) -> list[Detection]:
    """Run SAM3 on a single image and return surviving detections.

    Detections survive when: score >= SCORE_THRESHOLD AND
    mask_area >= MIN_AREA_PX AND mask_area / image_area <= MAX_AREA_FRAC.
    """
    model, processor, device = _load()
    image = Image.open(image_path).convert("RGB")
    w, h = image.size
    image_area = w * h
    np_image = np.array(image)

    inputs = processor(images=image, text=prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)

    target_size = inputs.get("original_sizes")
    if target_size is not None:
        target_size = target_size.tolist()
    results = processor.post_process_instance_segmentation(
        outputs,
        threshold=SCORE_THRESHOLD,
        mask_threshold=0.5,
        target_sizes=target_size,
    )[0]

    detections: list[Detection] = []
    masks = results.get("masks", [])
    scores = results.get("scores", [])
    for idx, (mask_tensor, score) in enumerate(zip(masks, scores)):
        mask = mask_tensor.cpu().numpy().astype(bool)
        area = int(mask.sum())
        if area < MIN_AREA_PX:
            continue
        if area / image_area > MAX_AREA_FRAC:
            continue
        ys, xs = np.where(mask)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        contrast = _contrast(np_image, mask)
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
                crop_path=crop_path,
                extras={"contrast": contrast, "area_frac": area / image_area},
            )
        )
    return detections


def encode_clean_jpeg(crop_path: Path) -> bytes:
    """Re-encode JPEG without EXIF for upload. Quality 86, max edge 1600px."""
    image = Image.open(crop_path).convert("RGB")
    image.thumbnail((1600, 1600), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86, optimize=True, exif=b"")
    return buffer.getvalue()
