"""End-to-end walk → detect → enqueue pipeline."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import httpx
import imagehash
from PIL import Image
from rich.console import Console

from .config import Config
from .dedup import (
    detection_id,
    has_close_existing,
    has_recent_neighbor,
    has_similar_crop,
    open_store,
    upsert_pending,
)
from .formspec import MIEJSCE, RODZAJ
from .vision import Detection, detect
from .walker import ImageRef, Waypoint, download_image, search_corridor

console = Console()


def _phash(crop_path: Path) -> str:
    return str(imagehash.phash(Image.open(crop_path).convert("RGB")))


def walk_and_detect(
    config: Config,
    waypoints: list[Waypoint],
    *,
    max_images: int | None = None,
    min_year: int = 2023,
) -> list[Detection]:
    """Walk Mapillary along the corridor, run SAM3, persist surviving
    detections to the queue. Returns the list of new detections enqueued."""
    config.images_dir.mkdir(parents=True, exist_ok=True)
    crop_dir = config.images_dir / "crops"

    conn = open_store(config.store_path)

    with httpx.Client(timeout=60, http2=True) as client:
        refs = search_corridor(config, waypoints, min_year=min_year, client=client)
        console.print(f"[bold]Mapillary[/]: {len(refs)} candidate images")
        if max_images is not None:
            refs = refs[:max_images]

        new_detections: list[Detection] = []
        for ref in refs:
            try:
                local = download_image(
                    ref,
                    config.images_dir / f"{ref.id}.jpg",
                    client,
                )
            except httpx.HTTPError as exc:
                console.print(f"[red]download fail {ref.id}: {exc}[/]")
                continue

            try:
                detections = detect(local, ref.id, crop_dir)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]detect fail {ref.id}: {exc}[/]")
                continue

            for det in detections:
                if det.crop_path is None:
                    continue
                phash = _phash(det.crop_path)
                did = detection_id(ref.id, phash)
                # pHash dedup catches the same tag captured from sequential
                # Mapillary frames. Different tags at the same wall produce
                # distinct pHashes and survive. Spatial dedup is kept only
                # for the cross-run "we already submitted this" check.
                if has_recent_neighbor(conn, ref.lat, ref.lng):
                    continue
                if has_similar_crop(conn, phash):
                    console.print(f"[grey]~ near-dup pHash {ref.id}[/]")
                    continue
                inserted = upsert_pending(
                    conn,
                    did,
                    ref.id,
                    ref.lat,
                    ref.lng,
                    det.severity,
                    det.score,
                    RODZAJ["other"],
                    MIEJSCE["building_wall"],
                    det.crop_path,
                    crop_phash=phash,
                )
                if inserted:
                    det.mask_phash = phash
                    new_detections.append(det)
                    console.print(
                        f"[green]+[/] {ref.id} score={det.score:.2f} "
                        f"sev={det.severity} ({ref.lat:.5f},{ref.lng:.5f})"
                    )
        return new_detections


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
