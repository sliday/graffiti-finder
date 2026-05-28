"""Drop detections that are not from the most recent Mapillary frame at
each spot. Run after a walk.

Usage:
    uv run python scripts/prune_stale.py
    uv run python scripts/prune_stale.py --bucket-meters 8
"""
from __future__ import annotations

import argparse
import sqlite3

from rich.console import Console

from krakow_clean.config import load_config
from krakow_clean.dedup import open_store, prune_stale_at_spot

console = Console()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket-meters", type=float, default=11.0,
                    help="spatial bucket size; same wall captured from "
                    "frames within this radius is treated as one spot")
    args = ap.parse_args()

    bucket_deg = args.bucket_meters / 111_111  # latitude approximation

    cfg = load_config()
    conn = open_store(cfg.store_path)

    before = conn.execute(
        "SELECT COUNT(*) FROM detections WHERE submit_status='pending'"
    ).fetchone()[0]

    deleted = prune_stale_at_spot(conn, bucket_degrees=bucket_deg)

    after = conn.execute(
        "SELECT COUNT(*) FROM detections WHERE submit_status='pending'"
    ).fetchone()[0]

    console.print(
        f"pruned {deleted} stale detections "
        f"(bucket = {args.bucket_meters:.1f} m); "
        f"queue {before} → {after}"
    )

    # Report what we kept by capture date.
    rows = conn.execute(
        "SELECT substr(captured_at, 1, 7) AS month, COUNT(*) c "
        "FROM detections WHERE submit_status='pending' "
        "GROUP BY month ORDER BY month DESC"
    ).fetchall()
    console.print("\nKept detections by capture month:")
    for r in rows:
        console.print(f"  {r['month'] or '(unknown)':<10}  {r['c']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
