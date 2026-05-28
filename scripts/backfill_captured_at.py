"""Backfill captured_at on existing rows from Mapillary entity API.

Existing queue rows were enqueued before the schema added captured_at.
Look up each unique image_id via Mapillary, write the ISO timestamp
into every row sharing that image_id.
"""
from __future__ import annotations

import sqlite3

import httpx
from rich.console import Console

from krakow_clean.config import load_config
from krakow_clean.walker import fetch_geometry

console = Console()


def main() -> None:
    cfg = load_config()
    conn = sqlite3.connect(cfg.store_path, timeout=30)
    conn.row_factory = sqlite3.Row

    needs = conn.execute(
        "SELECT DISTINCT image_id FROM detections "
        "WHERE submit_status = 'pending' AND captured_at IS NULL"
    ).fetchall()
    image_ids = [r["image_id"] for r in needs]
    console.print(f"backfilling captured_at for {len(image_ids)} image_ids")

    with httpx.Client(timeout=20, http2=True) as c:
        refs = fetch_geometry(cfg, image_ids, client=c)
    console.print(f"  geometry recovered for {len(refs)}/{len(image_ids)}")

    updated = 0
    for image_id, ref in refs.items():
        iso = ref.captured_at.isoformat()
        n = conn.execute(
            "UPDATE detections SET captured_at = ? "
            "WHERE image_id = ? AND captured_at IS NULL",
            (iso, image_id),
        ).rowcount
        updated += n
    conn.commit()
    console.print(f"[bold green]updated {updated} rows[/]")


if __name__ == "__main__":
    main()
