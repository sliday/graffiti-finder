"""Sweep GroundingDINO thresholds + prompts over the 8 samples.

Goal: find the (prompt, thresholds) combo that catches the obvious wall
scribbles in sample_04 without spamming false positives on bicycles,
windows, traffic signs, etc.

Output: data/samples/tune_report.html with one row per (prompt × threshold)
combination, showing detection count + sample overlays.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

from krakow_clean.config import load_config

PROMPTS = [
    "graffiti. spray paint. wall tag. street art.",
    "graffiti.",
    "spray paint marks on wall. scribbles on wall. wall tag.",
    "vandalism. wall scribbles. spray paint.",
    "graffiti tag. spray paint. street art. wall vandalism.",
]
THRESHOLDS = [(0.30, 0.25), (0.20, 0.18), (0.15, 0.12), (0.10, 0.10)]


@dataclass
class Run:
    prompt: str
    box_thr: float
    text_thr: float
    per_sample: dict[str, list[dict]]


def main() -> None:
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    cfg = load_config()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained("IDEA-Research/grounding-dino-base")
    model = (
        AutoModelForZeroShotObjectDetection.from_pretrained(
            "IDEA-Research/grounding-dino-base"
        )
        .to(device)
        .eval()
    )

    samples = sorted(cfg.samples_dir.glob("sample_*.jpg"))
    out_root = cfg.samples_dir / "tune"
    out_root.mkdir(parents=True, exist_ok=True)

    runs: list[Run] = []
    for prompt in PROMPTS:
        for box_thr, text_thr in THRESHOLDS:
            print(f"\n=== prompt={prompt!r} box={box_thr} text={text_thr} ===")
            per_sample: dict[str, list[dict]] = {}
            run_id = f"p{PROMPTS.index(prompt)}_b{int(box_thr*100):02d}"
            run_dir = out_root / run_id
            run_dir.mkdir(exist_ok=True)
            for sample in samples:
                image = Image.open(sample).convert("RGB")
                w, h = image.size
                inputs = processor(images=image, text=prompt, return_tensors="pt").to(
                    device
                )
                with torch.no_grad():
                    outputs = model(**inputs)
                results = processor.post_process_grounded_object_detection(
                    outputs,
                    inputs.input_ids,
                    threshold=box_thr,
                    text_threshold=text_thr,
                    target_sizes=[(h, w)],
                )[0]
                rows = []
                overlay = image.copy()
                draw = ImageDraw.Draw(overlay, "RGBA")
                for box, score, label in zip(
                    results["boxes"],
                    results["scores"],
                    results.get("text_labels", results.get("labels", [])),
                ):
                    x0, y0, x1, y1 = [int(v) for v in box.tolist()]
                    if (x1 - x0) * (y1 - y0) < 500:
                        continue
                    draw.rectangle((x0, y0, x1, y1), outline=(255, 60, 60, 255), width=3)
                    draw.text(
                        (x0 + 4, y0 + 4),
                        f"{label} {float(score):.2f}",
                        fill=(255, 255, 255, 255),
                    )
                    rows.append(
                        {
                            "label": str(label),
                            "score": float(score),
                            "bbox": [x0, y0, x1, y1],
                            "area": (x1 - x0) * (y1 - y0),
                        }
                    )
                overlay_path = run_dir / f"{sample.stem}.jpg"
                overlay.save(overlay_path, "JPEG", quality=82)
                per_sample[sample.name] = rows
                print(f"  {sample.name}: {len(rows)} detections")
            runs.append(Run(prompt, box_thr, text_thr, per_sample))

    # Emit HTML report
    rows_html = []
    for run in runs:
        run_id = f"p{PROMPTS.index(run.prompt)}_b{int(run.box_thr*100):02d}"
        total = sum(len(v) for v in run.per_sample.values())
        rows_html.append(
            f"<h2>{run.prompt} · box={run.box_thr} text={run.text_thr} · "
            f"<b>{total}</b> detections total</h2>"
        )
        rows_html.append('<div class="grid">')
        for sample_name, dets in run.per_sample.items():
            stem = Path(sample_name).stem
            rows_html.append(
                f'<figure><img src="tune/{run_id}/{stem}.jpg">'
                f'<figcaption>{sample_name} → {len(dets)} det'
                f'{" · top:" + str(round(max((d["score"] for d in dets), default=0), 2)) if dets else ""}'
                f'</figcaption></figure>'
            )
        rows_html.append("</div>")

    html = f"""<!doctype html><meta charset='utf-8'>
<title>krakow-clean detector tuning</title>
<style>
body{{font:13px/1.4 -apple-system,sans-serif;margin:18px;background:#101013;color:#eee}}
h2{{font-size:14px;margin:24px 0 8px;color:#fff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px}}
figure{{margin:0;background:#1c1c20;border-radius:4px;overflow:hidden}}
figure img{{display:block;width:100%}}
figcaption{{padding:4px 8px;font-size:11px;color:#aac;font-family:ui-monospace}}
</style>
<h1>GroundingDINO threshold + prompt sweep ({len(samples)} samples × {len(PROMPTS)}×{len(THRESHOLDS)} = {len(samples)*len(PROMPTS)*len(THRESHOLDS)} inferences)</h1>
{''.join(rows_html)}
"""
    out_path = cfg.samples_dir / "tune_report.html"
    out_path.write_text(html)

    summary = {
        f"p{PROMPTS.index(r.prompt)}_b{int(r.box_thr*100):02d}": {
            "prompt": r.prompt,
            "box": r.box_thr,
            "text": r.text_thr,
            "totals": {k: len(v) for k, v in r.per_sample.items()},
            "grand_total": sum(len(v) for v in r.per_sample.values()),
        }
        for r in runs
    }
    (cfg.samples_dir / "tune_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
