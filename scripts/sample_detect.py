"""Run the GroundingDINO detector against the 8 sample images.

Usage:
    uv run python scripts/sample_detect.py

Output:
    data/samples/detections/<sample>/crop_NN.jpg + overlay.jpg
    data/samples/detection_gallery.html
"""
from __future__ import annotations

import json
from pathlib import Path

from krakow_clean.config import load_config
from krakow_clean.vision import (
    BOX_THRESHOLD,
    MIN_AREA_PX,
    PROMPT,
    detect_with_overlay,
)


def main() -> None:
    cfg = load_config()
    samples = sorted(cfg.samples_dir.glob("sample_*.jpg"))
    print(f"running GroundingDINO on {len(samples)} samples...")
    summary: list[dict] = []
    base_out = cfg.samples_dir / "detections"
    base_out.mkdir(parents=True, exist_ok=True)

    for sample in samples:
        image_id = sample.stem
        out_dir = base_out / image_id
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            detections, overlay = detect_with_overlay(sample, image_id, out_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sample.name}: ERROR {exc}")
            summary.append({"sample": sample.name, "error": str(exc)})
            continue
        print(f"  {sample.name}: {len(detections)} detections")
        summary.append(
            {
                "sample": sample.name,
                "detections": [
                    {
                        "idx": i,
                        "score": d.score,
                        "label": d.label,
                        "severity": d.severity,
                        "bbox": d.bbox,
                        "area_px": d.area_px,
                        "extras": d.extras,
                    }
                    for i, d in enumerate(detections)
                ],
                "overlay": str(overlay.relative_to(cfg.samples_dir)),
            }
        )

    (cfg.samples_dir / "detection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )

    rows = []
    for entry in summary:
        if "error" in entry:
            rows.append(
                f"<tr><td>{entry['sample']}</td><td colspan=3>ERROR: {entry['error']}</td></tr>"
            )
            continue
        dets = entry["detections"]
        if not dets:
            rows.append(
                f"<tr><td>{entry['sample']}</td>"
                f"<td><img src='{entry['overlay']}'></td>"
                f"<td colspan=2><i>no detections</i></td></tr>"
            )
            continue
        for d in dets:
            crop_rel = f"detections/{Path(entry['sample']).stem}/crop_{d['idx']:02d}.jpg"
            rows.append(
                f"<tr><td>{entry['sample']}</td>"
                f"<td><img src='{entry['overlay']}'></td>"
                f"<td><img src='{crop_rel}' style='max-width:240px'></td>"
                f"<td>label=<b>{d['label']}</b><br>"
                f"score={d['score']:.2f}<br>sev={d['severity']}<br>"
                f"area={d['area_px']}px<br>"
                f"contrast={d['extras']['contrast']:.1f}</td></tr>"
            )

    html = f"""<!doctype html><meta charset='utf-8'>
<title>krakow-clean — GroundingDINO detections</title>
<style>
body{{font:13px/1.4 -apple-system,system-ui,sans-serif;margin:18px;background:#101013;color:#eee}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}}
td,th{{border:1px solid #333;padding:8px;vertical-align:top}}
td:nth-child(1){{width:180px;font-family:ui-monospace,SF Mono,Menlo,monospace;font-size:11px}}
td:nth-child(2) img{{max-width:340px;border-radius:4px}}
td:nth-child(3) img{{max-width:240px;border-radius:4px}}
td:nth-child(4){{width:170px;font-family:ui-monospace,SF Mono,Menlo,monospace;font-size:11px}}
</style>
<h1>GroundingDINO detections on Karmelicka samples</h1>
<p>Prompt: <code>{PROMPT}</code> · box_threshold={BOX_THRESHOLD} · min_area={MIN_AREA_PX}px</p>
<table>
<tr><th>sample</th><th>overlay</th><th>crop</th><th>meta</th></tr>
{''.join(rows)}
</table>
"""
    out_path = cfg.samples_dir / "detection_gallery.html"
    out_path.write_text(html)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
