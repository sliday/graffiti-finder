"""Render the pending queue: overlay boxes on source images + gallery HTML.

Reads detection_id, image_id, bbox via re-detect (no bbox stored in queue —
mvp choice — re-run vision on each unique image_id is acceptable for ≤20).
Generates data/queue_gallery.html with one row per source image, source on
left, all detections boxed in red, crops on right.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

from krakow_clean.config import load_config
from krakow_clean.vision import detect_with_overlay


def main() -> None:
    cfg = load_config()
    conn = sqlite3.connect(cfg.store_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT detection_id, image_id, lat, lng, severity, score, "
        "crop_path FROM detections WHERE submit_status = 'pending'"
    ).fetchall()
    print(f"pending: {len(rows)} detections across "
          f"{len({r['image_id'] for r in rows})} source images")

    by_image: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_image[r["image_id"]].append(r)

    out_root = cfg.data_dir / "queue_viz"
    out_root.mkdir(parents=True, exist_ok=True)
    gallery_rows: list[str] = []

    for image_id, dets in by_image.items():
        src = cfg.images_dir / f"{image_id}.jpg"
        if not src.exists():
            print(f"  ! missing source {src}")
            continue
        # Re-run detector to recover boxes (queue stores hashes, not bboxes).
        overlay_dir = out_root / image_id
        overlay_dir.mkdir(exist_ok=True)
        detections, overlay_path = detect_with_overlay(src, image_id, overlay_dir)
        print(f"  {image_id}: {len(detections)} boxes drawn")

        crops_html = "".join(
            f'<img src="../images/{image_id}.jpg" hidden>'
            f'<a href="../images/{image_id}.jpg"><img class="crop" src="queue_viz/{image_id}/{d.crop_path.name}" alt="crop {i}"></a>'
            for i, d in enumerate(detections)
        )
        first = dets[0]
        gallery_rows.append(
            f'<article>'
            f'<header>'
            f'<h3>{image_id}</h3>'
            f'<p class="meta">lat <b>{first["lat"]:.5f}</b> · '
            f'lng <b>{first["lng"]:.5f}</b> · '
            f'{len(detections)} detections · '
            f'top score <b>{max((d.score for d in detections), default=0):.2f}</b></p>'
            f'</header>'
            f'<div class="row">'
            f'<a href="queue_viz/{image_id}/overlay.jpg" class="overlay-link">'
            f'<img class="overlay" src="queue_viz/{image_id}/overlay.jpg"></a>'
            f'<div class="crops">{crops_html}</div>'
            f'</div>'
            f'</article>'
        )

    html = f"""<!doctype html><meta charset='utf-8'>
<title>krakow-clean — pending queue ({len(rows)} detections)</title>
<style>
body{{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:24px;background:#0d0d10;color:#eee;max-width:1200px;margin-inline:auto}}
h1{{font-size:24px;margin:0 0 4px}}
.lede{{color:#9aa;margin:0 0 32px}}
article{{background:#15161b;border:1px solid #23252c;border-radius:10px;margin:20px 0;padding:18px 22px}}
article header{{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #23252c;padding-bottom:10px;margin-bottom:14px}}
article h3{{font-family:ui-monospace,SF Mono,Menlo,monospace;font-size:13px;margin:0;color:#7ad6ff}}
.meta{{margin:0;color:#9aa;font-size:12px;font-family:ui-monospace}}
.meta b{{color:#fff}}
.row{{display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start}}
.overlay{{width:100%;display:block;border-radius:6px;border:1px solid #23252c}}
.crops{{display:flex;flex-wrap:wrap;gap:8px}}
.crop{{max-width:120px;max-height:120px;border-radius:4px;border:1px solid #23252c}}
.crop:hover{{outline:2px solid #c9f76f}}
</style>
<h1>krakow-clean — pending queue</h1>
<p class="lede">{len(rows)} detections from {len(by_image)} source images, GroundingDINO at box_threshold=0.20. Hover/click a crop to inspect.</p>
{''.join(gallery_rows)}
"""
    out_path = cfg.data_dir / "queue_gallery.html"
    out_path.write_text(html)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
