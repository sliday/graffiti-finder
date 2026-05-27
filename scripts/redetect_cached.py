"""Re-run the detector on cached Mapillary images.

Faster than full walk: skips Mapillary search + downloads. Wipes pending
queue entries and re-enqueues survivors under the updated vision filters.
Useful after tuning thresholds / adding new gates.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import imagehash
from PIL import Image
from rich.console import Console

from krakow_clean.config import load_config
from krakow_clean.dedup import (
    detection_id,
    has_close_existing,
    has_recent_neighbor,
    has_similar_crop,
    open_store,
    upsert_pending,
)
from krakow_clean.formspec import MIEJSCE, RODZAJ
from krakow_clean.vision import detect

console = Console()


def main() -> None:
    cfg = load_config()
    conn = open_store(cfg.store_path)
    # Capture original lat/lng per cached image from existing rows BEFORE
    # wiping the queue; we need them to re-enqueue with location info.
    image_meta = {
        row["image_id"]: (row["lat"], row["lng"])
        for row in conn.execute(
            "SELECT DISTINCT image_id, lat, lng FROM detections"
        ).fetchall()
    }
    # Clear pending entries; preserve submitted ones (we have none yet, but
    # the policy is conservative).
    deleted = conn.execute(
        "DELETE FROM detections WHERE submit_status = 'pending'"
    ).rowcount
    conn.commit()
    console.print(f"[yellow]cleared {deleted} pending entries[/]")

    crop_dir = cfg.images_dir / "crops"
    cached = sorted(cfg.images_dir.glob("*.jpg"))
    cached = [p for p in cached if p.stem in image_meta]
    console.print(f"re-detecting on {len(cached)} cached images...")

    kept = 0
    for src in cached:
        image_id = src.stem
        lat, lng = image_meta[image_id]
        detections = detect(src, image_id, crop_dir)
        for det in detections:
            if det.crop_path is None:
                continue
            phash = str(imagehash.phash(Image.open(det.crop_path)))
            did = detection_id(image_id, phash)
            if has_recent_neighbor(conn, lat, lng):
                continue
            if has_similar_crop(conn, phash):
                console.print(f"[grey]~ near-dup pHash {image_id}[/]")
                continue
            if upsert_pending(
                conn, did, image_id, lat, lng,
                det.severity, det.score,
                RODZAJ["other"], MIEJSCE["building_wall"],
                det.crop_path, crop_phash=phash,
            ):
                kept += 1
                console.print(
                    f"[green]+[/] {image_id} score={det.score:.2f} "
                    f"sev={det.severity} aspect={det.extras['aspect']:.2f} "
                    f"std={det.extras['color_std']:.0f}"
                )
    console.print(f"\n[bold]kept {kept} detections after filters[/]")


if __name__ == "__main__":
    main()
