#!/usr/bin/env python3
"""Drop near-duplicate detections in tight spatial buckets, keeping best.

prune_stale_at_spot collapses by *freshest captured_at* in an 11 m bucket
— good for "same wall, different week" duplicates. This is the
complement: within a *tight* bucket (default 8 m, ≈ one wall section),
keep the single highest-score detection and drop the others, regardless
of capture date.

Use when you notice the same tag captured from 3-4 adjacent Mapillary
frames inflating the queue with near-duplicates.

Usage:
    uv run python scripts/prune_close.py                # 8 m, dry-run
    uv run python scripts/prune_close.py --commit       # actually delete
    uv run python scripts/prune_close.py --radius 5 --commit
"""
from __future__ import annotations

import argparse
import math
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "store.sqlite"


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def prune(radius_m: float, commit: bool) -> None:
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT detection_id, lat, lng, score FROM detections "
        "WHERE submit_status='pending' ORDER BY score DESC"
    ).fetchall()
    print(f"considering {len(rows)} pending detections at radius {radius_m} m")

    kept: list[sqlite3.Row] = []
    drop_ids: list[str] = []
    for r in rows:
        close_to = None
        for k in kept:
            if _haversine_m(r["lat"], r["lng"], k["lat"], k["lng"]) <= radius_m:
                close_to = k
                break
        if close_to is None:
            kept.append(r)
        else:
            drop_ids.append(r["detection_id"])

    print(f"would keep {len(kept)} · would drop {len(drop_ids)}")
    if not commit:
        print("dry-run — pass --commit to actually delete")
        return

    cur = conn.cursor()
    cur.executemany(
        "DELETE FROM detections WHERE detection_id=?",
        [(d,) for d in drop_ids],
    )
    conn.commit()
    print(f"deleted {cur.rowcount} rows")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--radius", type=float, default=8.0)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    prune(args.radius, args.commit)


if __name__ == "__main__":
    main()
