"""Full-corridor walk using SAM3+CLIP.

Phase 1: download every Mapillary candidate to data/images/.
Phase 2: batch SAM3 sidecar on the downloaded set (single model load).
Phase 3: CLIP-filter each box, dedup, enqueue.

Usage:
    uv run python scripts/walk_full_sam3.py <route_name>
"""
from __future__ import annotations

import sys
import time
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
from krakow_clean.routes import ROUTES
from krakow_clean.vision_sam3 import detect_batch
from krakow_clean.walker import download_image, search_corridor

console = Console()


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: walk_full_sam3.py <route>")
        return 1
    route = argv[0]
    if route not in ROUTES:
        print(f"unknown route '{route}'. options: {list(ROUTES)}")
        return 1

    cfg = load_config()
    waypoints = ROUTES[route]
    conn = open_store(cfg.store_path)

    started = time.perf_counter()

    with httpx.Client(timeout=60, http2=True) as c:
        refs = search_corridor(cfg, waypoints, min_year=2022, client=c)
    console.print(f"[bold]Mapillary: {len(refs)} candidate images on '{route}'[/]")

    cfg.images_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    ref_by_path: dict[Path, object] = {}

    console.print("[cyan]Phase 1: download[/]")
    with httpx.Client(timeout=60, http2=True) as c:
        for i, ref in enumerate(refs, 1):
            dst = cfg.images_dir / f"{ref.id}.jpg"
            try:
                download_image(ref, dst, c)
                image_paths.append(dst)
                ref_by_path[dst] = ref
                if i % 25 == 0:
                    console.print(f"  downloaded {i}/{len(refs)}")
            except httpx.HTTPError as exc:
                console.print(f"  [red]dl fail {ref.id}: {exc}[/]")
    console.print(f"  ready: {len(image_paths)} images on disk")

    console.print("[cyan]Phase 2: SAM3 + CLIP batch[/]")
    crop_dir = cfg.images_dir / "crops"
    detections_by_id = detect_batch(image_paths, crop_dir)
    total = sum(len(v) for v in detections_by_id.values())
    console.print(f"  raw survivors after SAM3+CLIP: {total}")

    console.print("[cyan]Phase 3: dedup + enqueue[/]")
    kept = 0
    skipped_phash = 0
    for image_id, detections in detections_by_id.items():
        path_match = next((p for p in image_paths if p.stem == image_id), None)
        if path_match is None:
            continue
        ref = ref_by_path[path_match]
        for det in detections:
            phash = str(imagehash.phash(Image.open(det.crop_path)))
            did = detection_id(image_id, phash)
            if has_recent_neighbor(conn, ref.lat, ref.lng):
                continue
            if has_similar_crop(conn, phash):
                skipped_phash += 1
                continue
            if upsert_pending(
                conn, did, image_id, ref.lat, ref.lng,
                det.severity, det.sam3_score,
                RODZAJ["other"], MIEJSCE["building_wall"],
                det.crop_path, crop_phash=phash,
                captured_at=ref.captured_at.isoformat(),
            ):
                kept += 1

    elapsed = time.perf_counter() - started
    console.print(
        f"\n[bold green]done[/]: {kept} kept, {skipped_phash} pHash-deduped, "
        f"{total - kept - skipped_phash} cross-walk-dups skipped"
    )
    console.print(f"total wall time: {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
