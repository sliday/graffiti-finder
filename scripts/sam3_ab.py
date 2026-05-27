"""A/B comparison: mlx-sam3 (3.13 sidecar) vs current GroundingDINO+CLIP.

Renders overlays for both side-by-side on the five known-good Karmelicka /
Kazimierz images, plus a summary HTML.

Usage:
    uv run python scripts/sam3_ab.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from krakow_clean.config import load_config
from krakow_clean.vision import (
    BOX_THRESHOLD,
    detect_with_overlay as dino_clip_overlay,
)

TARGETS = [
    "3689129454680282",   # Bar Mleczny corner: GZ, JUMP8, scribbles
    "1237359060636431",   # same corner alt angle
    "453477123994712",    # Dajwór 19 Kazimierz
    "972146901074126",    # Dajwór 19 alt
    "441112288652120",    # Dajwór 14
]

# Lower bound for SAM3 — anything below is dropped.
SAM3_MIN_SCORE = 0.55


def run_sam3_sidecar(image_paths: list[Path]) -> dict[str, dict]:
    # Sidecar lives in vendor/mlx_sam3 with its own uv env. Pass absolute
    # paths so we don't need to fight pathlib's relative-anchor rules.
    cmd = ["uv", "run", "python", "sidecar.py"] + [str(p.resolve()) for p in image_paths]
    print(f"running mlx-sam3 sidecar on {len(image_paths)} images...")
    proc = subprocess.run(cmd, cwd="vendor/mlx_sam3", capture_output=True, text=True)
    if proc.returncode != 0:
        print("sidecar stderr:", proc.stderr[-1000:])
    results: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "error" in entry:
            print(f"  ERR {entry['image']}: {entry['error']}")
            continue
        stem = Path(entry["image"]).stem
        results[stem] = entry
        print(f"  {stem}: {entry['n']} sam3 dets, "
              f"{sum(1 for d in entry['detections'] if d['score'] >= SAM3_MIN_SCORE)} above {SAM3_MIN_SCORE}")
    return results


def draw_sam3_overlay(image: Image.Image, detections: list[dict], out_path: Path) -> None:
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    for d in detections:
        if d["score"] < SAM3_MIN_SCORE:
            continue
        x0, y0, x1, y1 = d["bbox"]
        draw.rectangle((x0, y0, x1, y1), outline=(60, 200, 255, 255), width=4)
        draw.text((x0 + 4, y0 + 4), f"{d['score']:.2f}", fill=(255, 255, 255))
    overlay.save(out_path, "JPEG", quality=88)


def main() -> int:
    cfg = load_config()
    image_paths = [cfg.images_dir / f"{stem}.jpg" for stem in TARGETS]
    image_paths = [p for p in image_paths if p.exists()]

    sam3_results = run_sam3_sidecar(image_paths)

    out_root = cfg.samples_dir / "sam3_ab"
    out_root.mkdir(parents=True, exist_ok=True)

    rows: list[str] = []
    summary: list[dict] = []
    for src in image_paths:
        stem = src.stem
        sub = out_root / stem
        sub.mkdir(exist_ok=True)
        image = Image.open(src).convert("RGB")

        dino_dets, dino_overlay = dino_clip_overlay(src, stem, sub)
        dino_dst = sub / "dino.jpg"
        dino_overlay.rename(dino_dst)

        sam3_entry = sam3_results.get(stem, {"detections": [], "elapsed_ms": 0, "n": 0})
        sam3_dst = sub / "sam3.jpg"
        draw_sam3_overlay(image, sam3_entry["detections"], sam3_dst)

        n_sam3_kept = sum(1 for d in sam3_entry["detections"] if d["score"] >= SAM3_MIN_SCORE)
        summary.append({
            "image_id": stem,
            "dino_n": len(dino_dets),
            "sam3_n_raw": sam3_entry.get("n", 0),
            "sam3_n_kept": n_sam3_kept,
            "sam3_ms": sam3_entry.get("elapsed_ms", 0),
            "top_sam3_scores": sorted(
                [round(d["score"], 2) for d in sam3_entry["detections"]],
                reverse=True,
            )[:6],
        })
        rows.append(f"""
<tr>
  <td>{stem}</td>
  <td><img src="sam3_ab/{stem}/dino.jpg"><br>
      <small>{len(dino_dets)} dets · threshold {BOX_THRESHOLD}</small></td>
  <td><img src="sam3_ab/{stem}/sam3.jpg"><br>
      <small>{n_sam3_kept} kept of {sam3_entry.get('n', 0)} (≥ {SAM3_MIN_SCORE}) · {sam3_entry.get('elapsed_ms', 0)}ms</small></td>
</tr>""")

    html = f"""<!doctype html><meta charset='utf-8'>
<title>mlx-sam3 vs GroundingDINO+CLIP</title>
<style>
body{{font:13px/1.4 -apple-system,system-ui,sans-serif;margin:18px;background:#0d0d10;color:#eee;max-width:1400px;margin-inline:auto}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}}
th,td{{border:1px solid #333;padding:8px;vertical-align:top}}
th{{color:#c9f76f;font-size:12px;text-align:left;text-transform:uppercase;letter-spacing:.05em}}
td:nth-child(1){{width:160px;font-family:ui-monospace;font-size:11px;word-break:break-all}}
td:nth-child(2),td:nth-child(3){{width:auto}}
img{{display:block;width:100%;border-radius:4px}}
small{{color:#9aa;font-family:ui-monospace}}
.legend{{margin:14px 0;color:#9aa}}
.legend span{{display:inline-block;margin-right:18px}}
.legend i{{display:inline-block;width:14px;height:14px;vertical-align:-2px;border-radius:2px;margin-right:6px}}
</style>
<h1>mlx-sam3 vs GroundingDINO+CLIP — A/B on Karmelicka + Kazimierz hotspots</h1>
<p class="legend">
<span><i style="background:#ff3c46"></i> GroundingDINO+CLIP (red boxes)</span>
<span><i style="background:#3cc8ff"></i> mlx-sam3 (cyan boxes, score ≥ {SAM3_MIN_SCORE})</span>
</p>
<table>
<tr><th>image</th><th>GroundingDINO + CLIP</th><th>mlx-sam3</th></tr>
{''.join(rows)}
</table>"""
    out_path = cfg.samples_dir / "sam3_ab.html"
    out_path.write_text(html)
    (cfg.samples_dir / "sam3_ab.json").write_text(json.dumps(summary, indent=2))

    print(f"\n=== Summary ===")
    for s in summary:
        print(f"  {s['image_id']}: dino={s['dino_n']} sam3_kept={s['sam3_n_kept']}/{s['sam3_n_raw']} "
              f"top={s['top_sam3_scores']} time={s['sam3_ms']}ms")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
