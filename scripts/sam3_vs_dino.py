"""Side-by-side A/B comparison: SAM3 vs current GroundingDINO+CLIP.

Runs both detectors on the same six cached Mapillary frames and emits a
visual report at data/samples/sam3_vs_dino.html — overlay images, mask /
box counts, scores, timing.

Usage:
    bash scripts/install_sam3.sh    # one-time
    uv run python scripts/sam3_vs_dino.py
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from krakow_clean.config import load_config
from krakow_clean.vision import detect_with_overlay as dino_clip_overlay

TARGETS = [
    "3689129454680282",   # Bar Mleczny — multi-tag corner
    "1237359060636431",   # same corner, different angle
    "463288660073931",    # close-up at same intersection
    "1014099427133730",
    "1073321477919159",   # had road-sign false positive (now rejected)
    "9656162564405381",   # had lamp-post false positive (now rejected)
]

PROMPT = "graffiti on a wall"


@dataclass
class RunStats:
    image_id: str
    detector: str
    n_detections: int
    inference_ms: int
    overlay_path: Path


def _draw_overlay(image: Image.Image, masks_or_boxes, scores, kind: str) -> Image.Image:
    out = image.copy()
    draw = ImageDraw.Draw(out, "RGBA")
    for item, score in zip(masks_or_boxes, scores):
        if kind == "box":
            x0, y0, x1, y1 = item
            draw.rectangle((x0, y0, x1, y1), outline=(255, 60, 60, 255), width=4)
            draw.text((x0 + 4, y0 + 4), f"{float(score):.2f}", fill=(255, 255, 255))
        else:  # mask
            mask = item.astype(bool)
            ys, xs = np.where(mask)
            if not len(xs):
                continue
            x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
            # Cyan overlay for mask, plus bbox.
            arr = np.array(out)
            arr[mask] = (arr[mask] * 0.5 + np.array([60, 200, 255]) * 0.5).astype(np.uint8)
            out = Image.fromarray(arr)
            draw = ImageDraw.Draw(out, "RGBA")
            draw.rectangle((x0, y0, x1, y1), outline=(60, 200, 255, 255), width=3)
            draw.text((x0 + 4, y0 + 4), f"{float(score):.2f}", fill=(255, 255, 255))
    return out


def run_sam3(image_path: Path, out_dir: Path) -> RunStats:
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    model = build_sam3_image_model()
    processor = Sam3Processor(model)
    image = Image.open(image_path).convert("RGB")
    started = time.perf_counter()
    state = processor.set_image(image)
    output = processor.set_text_prompt(state=state, prompt=PROMPT)
    elapsed = int((time.perf_counter() - started) * 1000)
    masks = output["masks"]
    boxes = output["boxes"]
    scores = output["scores"]
    overlay = _draw_overlay(image, masks, scores, kind="mask")
    out_path = out_dir / "sam3.jpg"
    overlay.save(out_path, "JPEG", quality=88)
    return RunStats(
        image_id=image_path.stem,
        detector="sam3",
        n_detections=len(scores),
        inference_ms=elapsed,
        overlay_path=out_path,
    )


def run_dino(image_path: Path, out_dir: Path) -> RunStats:
    started = time.perf_counter()
    detections, overlay_path = dino_clip_overlay(image_path, image_path.stem, out_dir)
    elapsed = int((time.perf_counter() - started) * 1000)
    # Move the overlay to a sensible name.
    final = out_dir / "dino_clip.jpg"
    overlay_path.rename(final)
    return RunStats(
        image_id=image_path.stem,
        detector="dino+clip",
        n_detections=len(detections),
        inference_ms=elapsed,
        overlay_path=final,
    )


def main() -> None:
    cfg = load_config()
    out_root = cfg.samples_dir / "sam3_vs_dino"
    out_root.mkdir(parents=True, exist_ok=True)

    summary = []
    for image_id in TARGETS:
        src = cfg.images_dir / f"{image_id}.jpg"
        if not src.exists():
            print(f"  skip {image_id} (not cached)")
            continue
        out_dir = out_root / image_id
        out_dir.mkdir(exist_ok=True)
        print(f"\n=== {image_id} ===")
        try:
            dino_stats = run_dino(src, out_dir)
            print(f"  dino+clip: {dino_stats.n_detections} dets in {dino_stats.inference_ms}ms")
        except Exception as exc:
            print(f"  dino+clip FAIL: {exc}")
            dino_stats = None
        try:
            sam3_stats = run_sam3(src, out_dir)
            print(f"  sam3     : {sam3_stats.n_detections} dets in {sam3_stats.inference_ms}ms")
        except Exception as exc:
            print(f"  sam3 FAIL: {exc}")
            sam3_stats = None
        summary.append({
            "image_id": image_id,
            "dino": dino_stats.__dict__ if dino_stats else None,
            "sam3": sam3_stats.__dict__ if sam3_stats else None,
        })

    # Emit HTML
    rows = []
    for s in summary:
        rows.append(
            f"<tr><td>{s['image_id']}</td>"
            f"<td><img src='sam3_vs_dino/{s['image_id']}/dino_clip.jpg' style='max-width:480px'>"
            f"<br><span>{s['dino']['n_detections'] if s['dino'] else 'fail'} dets · "
            f"{s['dino']['inference_ms'] if s['dino'] else '-'}ms</span></td>"
            f"<td><img src='sam3_vs_dino/{s['image_id']}/sam3.jpg' style='max-width:480px'>"
            f"<br><span>{s['sam3']['n_detections'] if s['sam3'] else 'fail'} dets · "
            f"{s['sam3']['inference_ms'] if s['sam3'] else '-'}ms</span></td></tr>"
        )

    html = """<!doctype html><meta charset='utf-8'>
<title>SAM3 vs GroundingDINO+CLIP</title>
<style>
body{font:13px/1.4 -apple-system,system-ui,sans-serif;margin:20px;background:#0d0d10;color:#eee}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #333;padding:8px;vertical-align:top}
th{color:#c9f76f;font-size:12px;text-align:left;text-transform:uppercase}
span{display:block;color:#9aa;margin-top:6px;font-family:ui-monospace}
img{display:block;border-radius:4px}
</style>
<h1>SAM3 vs GroundingDINO+CLIP — head-to-head</h1>
<p>Same prompt: <code>""" + PROMPT + """</code>. Same cached images.</p>
<table><tr><th>image</th><th>GroundingDINO+CLIP (boxes)</th><th>SAM3 (masks)</th></tr>
""" + "".join(rows) + """
</table>"""
    out_path = cfg.samples_dir / "sam3_vs_dino.html"
    out_path.write_text(html)
    (cfg.samples_dir / "sam3_vs_dino.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
