"""Re-detect across every cached Mapillary image on disk.

Different from redetect_cached.py: this does not rely on existing DB rows
for the image list, only for lat/lng lookup. If lat/lng is unknown it
queries Mapillary's entity endpoint to fetch geometry.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import imagehash
from PIL import Image
from rich.console import Console

from krakow_clean.config import load_config
from krakow_clean.dedup import (
    detection_id,
    has_recent_neighbor,
    has_similar_crop,
    open_store,
    upsert_pending,
)
from krakow_clean.formspec import MIEJSCE, RODZAJ
from krakow_clean.vision import detect
from krakow_clean.walker import fetch_geometry

console = Console()


def main() -> None:
    cfg = load_config()
    conn = open_store(cfg.store_path)
    conn.execute("DELETE FROM detections")
    conn.commit()
    console.print("[yellow]queue cleared[/]")

    cached = sorted(cfg.images_dir.glob("*.jpg"))
    console.print(f"found {len(cached)} cached images")

    # Lookup lat/lng for each image_id from Mapillary entity API.
    image_ids = [p.stem for p in cached]
    with httpx.Client(timeout=20, http2=True) as c:
        refs = fetch_geometry(cfg, image_ids, client=c)
    console.print(f"geometry recovered for {len(refs)} of {len(image_ids)}")

    crop_dir = cfg.images_dir / "crops"
    kept = 0
    for src in cached:
        image_id = src.stem
        ref = refs.get(image_id)
        if ref is None:
            console.print(f"[grey]skip {image_id} (no geometry)[/]")
            continue

        detections = detect(src, image_id, crop_dir)
        for det in detections:
            if det.crop_path is None:
                continue
            phash = str(imagehash.phash(Image.open(det.crop_path)))
            did = detection_id(image_id, phash)
            if has_recent_neighbor(conn, ref.lat, ref.lng):
                continue
            if has_similar_crop(conn, phash):
                console.print(f"[grey]~ near-dup pHash {image_id}[/]")
                continue
            if upsert_pending(
                conn, did, image_id, ref.lat, ref.lng,
                det.severity, det.score,
                RODZAJ["other"], MIEJSCE["building_wall"],
                det.crop_path, crop_phash=phash,
            ):
                kept += 1
                console.print(
                    f"[green]+[/] {image_id} score={det.score:.2f} "
                    f"clip={det.extras['clip_top_label'][:30]!r} "
                    f"p={det.extras['clip_prob']:.2f} "
                    f"({ref.lat:.5f},{ref.lng:.5f})"
                )
    console.print(f"\n[bold green]kept {kept} detections[/]")


if __name__ == "__main__":
    main()
