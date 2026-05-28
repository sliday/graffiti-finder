"""Re-detect every cached image with the SAM3+CLIP hybrid.

Replaces queue contents with the hybrid output. Use after switching the
default detector.
"""
from __future__ import annotations

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
from krakow_clean.vision_sam3 import detect_batch
from krakow_clean.walker import fetch_geometry

console = Console()


def main() -> None:
    cfg = load_config()
    conn = open_store(cfg.store_path)
    conn.execute("DELETE FROM detections")
    conn.commit()
    console.print("[yellow]queue cleared[/]")

    cached = sorted(cfg.images_dir.glob("*.jpg"))
    console.print(f"found {len(cached)} cached images; calling SAM3 sidecar...")

    image_ids = [p.stem for p in cached]
    with httpx.Client(timeout=20, http2=True) as c:
        refs = fetch_geometry(cfg, image_ids, client=c)
    console.print(f"geometry: {len(refs)}/{len(image_ids)} resolved")

    crop_dir = cfg.images_dir / "crops"
    detections_by_id = detect_batch(cached, crop_dir)

    kept = 0
    for image_id, detections in detections_by_id.items():
        ref = refs.get(image_id)
        if ref is None:
            continue
        for det in detections:
            phash = str(imagehash.phash(Image.open(det.crop_path)))
            did = detection_id(image_id, phash)
            if has_recent_neighbor(conn, ref.lat, ref.lng):
                continue
            if has_similar_crop(conn, phash):
                console.print(f"[grey]~ near-dup pHash {image_id}[/]")
                continue
            if upsert_pending(
                conn, did, image_id, ref.lat, ref.lng,
                det.severity, det.sam3_score,
                RODZAJ["other"], MIEJSCE["building_wall"],
                det.crop_path, crop_phash=phash,
            ):
                kept += 1
                console.print(
                    f"[green]+[/] {image_id} sam3={det.sam3_score:.2f} "
                    f"clip={det.extras['clip_prob']:.2f} sev={det.severity} "
                    f"({ref.lat:.5f},{ref.lng:.5f})"
                )

    console.print(f"\n[bold green]kept {kept} detections (SAM3+CLIP hybrid)[/]")


if __name__ == "__main__":
    main()
