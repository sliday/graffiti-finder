#!/usr/bin/env python3
"""Render a per-writer gallery HTML for visual verification.

For each candidate writer (cluster of ≥3 detections), show the first
N crops side by side so the human can decide whether the algorithm
grouped a real same-tagger run or just visually-similar noise.

Within each cluster, we dedupe by spatial proximity (default 5 m) so the
same wall captured from two adjacent Mapillary frames doesn't appear
twice in the gallery — keep the highest-score crop per bucket.
"""
from __future__ import annotations

import argparse
import base64
import math
import sqlite3
from collections import defaultdict
from pathlib import Path


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _dedupe_spatial(items: list, radius_m: float) -> list:
    """Within a writer cluster, collapse crops whose lat/lng are within
    radius_m of an already-kept crop. Caller order is preserved among
    kept items, so passing rows pre-sorted by score keeps the best."""
    kept: list = []
    for r in items:
        too_close = False
        for k in kept:
            if _haversine_m(r["lat"], r["lng"], k["lat"], k["lng"]) <= radius_m:
                too_close = True
                break
        if not too_close:
            kept.append(r)
    return kept

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "store.sqlite"
OUT = ROOT / "demo" / "writers.html"


def _b64(path: Path) -> str:
    if not path.exists():
        return ""
    return f"data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode()}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-cluster", type=int, default=3)
    parser.add_argument("--max-show", type=int, default=12, help="crops per writer")
    parser.add_argument(
        "--dedupe-m",
        type=float,
        default=5.0,
        help="collapse crops within this many metres of each other (same wall)",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT detection_id, writer_id, writer_cluster_size, crop_path, "
        "       monument_name, lat, lng, score "
        "FROM detections WHERE submit_status='pending' AND writer_id IS NOT NULL "
        "ORDER BY writer_id, score DESC"
    ).fetchall()

    by_writer: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_writer[r["writer_id"]].append(r)

    # Within each cluster: sort by score desc so dedup keeps the best, then
    # drop crops within args.dedupe_m of an already-kept one (same wall from
    # an adjacent Mapillary frame).
    deduped: dict[int, list[sqlite3.Row]] = {}
    for wid, items in by_writer.items():
        sorted_items = sorted(items, key=lambda r: -(r["score"] or 0))
        deduped[wid] = _dedupe_spatial(sorted_items, args.dedupe_m)
    drop_total = sum(len(v) - len(deduped[k]) for k, v in by_writer.items())
    print(f"spatial-deduped: dropped {drop_total} near-duplicate crops at ≤{args.dedupe_m} m")

    candidates = [
        (wid, items) for wid, items in deduped.items()
        if len(items) >= args.min_cluster
    ]
    candidates.sort(key=lambda kv: -len(kv[1]))

    print(f"writers ≥{args.min_cluster}: {len(candidates)}")

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>clean-krakow — writer clusters</title>",
        "<style>",
        "body{background:#0d0d10;color:#eee;font:14px/1.45 -apple-system,sans-serif;margin:24px;}",
        "h1{font-size:20px;margin:0 0 4px}",
        ".meta{color:#9aa;font-family:ui-monospace,monospace;font-size:12px;margin:0 0 24px}",
        ".w{border-top:1px solid #2a2c35;padding:16px 0;}",
        ".w h2{font-size:16px;margin:0 0 6px;font-family:-apple-system,sans-serif}",
        ".w .stats{color:#9aa;font-family:ui-monospace,monospace;font-size:12px;margin:0 0 12px}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}",
        ".grid div{background:#1c1d24;border-radius:4px;overflow:hidden;border:1px solid #2a2c35}",
        ".grid img{display:block;width:100%;height:140px;object-fit:cover}",
        ".grid .cap{padding:4px 6px;font-family:ui-monospace,monospace;font-size:10px;color:#7ad6ff}",
        ".grid .cap.mon{color:#ff6b6b}",
        "</style></head><body>",
        "<h1>clean-krakow — writer clusters</h1>",
        f"<p class='meta'>{len(candidates)} candidate writers · click any group to confirm or refute visually</p>",
    ]

    for wid, items in candidates:
        mon_count = sum(1 for x in items if x["monument_name"])
        parts.append("<div class='w'>")
        parts.append(f"<h2>Writer #{wid}</h2>")
        suffix = f" · {mon_count} on monuments" if mon_count else ""
        parts.append(
            f"<p class='stats'>cluster size {len(items)} · "
            f"showing top {min(args.max_show, len(items))} by score{suffix}</p>"
        )
        parts.append("<div class='grid'>")
        for r in items[: args.max_show]:
            b64 = _b64(Path(r["crop_path"]))
            cap_class = "cap mon" if r["monument_name"] else "cap"
            if r["monument_name"]:
                cap_label = r["monument_name"][:18] + "…"
            else:
                cap_label = f"{r['lat']:.4f},{r['lng']:.4f}"
            det_short = r["detection_id"][:8]
            parts.append(
                f"<div><img src='{b64}' alt='crop {det_short}'>"
                f"<div class='{cap_class}'>{cap_label}</div></div>"
            )
        parts.append("</div></div>")

    parts.append("</body></html>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
