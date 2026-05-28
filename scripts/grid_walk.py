"""Grid-tile walker — bbox a wide area, tile into 0.009° squares, walk each
tile in parallel with per-tile cap on Mapillary candidates.

Used for whole-district sweeps where defining named-route waypoints is
impractical. The walker still relies on walk_full_sam3.py per tile; we
just generate synthetic Waypoints at the tile centre.

Usage:
    uv run python scripts/grid_walk.py inner-krakow --workers 3 --tile-cap 25
    uv run python scripts/grid_walk.py 50.030,19.900 50.095,19.990
"""
from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx

from krakow_clean.config import load_config
from krakow_clean.dedup import (
    detection_id, has_recent_neighbor, has_similar_crop,
    open_store, upsert_pending,
)
from krakow_clean.formspec import MIEJSCE, RODZAJ
from krakow_clean.vision_sam3 import detect_batch
from krakow_clean.walker import (
    GRAPH_BASE, FIELDS, _parse_image, download_image, _fetch_with_retry,
)

# Pre-defined bboxes for convenient names.
BBOXES = {
    # SW corner, NE corner
    "inner-krakow":   ((50.030, 19.900), (50.095, 19.990)),
    "kazimierz":      ((50.048, 19.940), (50.060, 19.952)),
    "podgorze":       ((50.038, 19.940), (50.060, 19.965)),
    "train-station":  ((50.066, 19.943), (50.080, 19.960)),
    # East district: Nowa Huta — Plac Centralny + housing estates +
    # ArcelorMittal industrial zone (graffiti-heavy).
    "nowa-huta":      ((50.060, 20.000), (50.100, 20.090)),
    # Industrial / post-factory rail corridor on the south bank between
    # Most Kotlarski and Płaszów. Includes Schindler Factory area.
    "zablocie":       ((50.040, 19.945), (50.060, 19.985)),
}

TILE_DEG = 0.009  # Mapillary bbox cap is 0.01°, leave slack
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Tile:
    idx: int
    min_lat: float; min_lng: float
    max_lat: float; max_lng: float

    @property
    def label(self) -> str:
        return f"tile-{self.idx:03d}"


def tile_bbox(sw: tuple[float, float], ne: tuple[float, float]) -> list[Tile]:
    min_lat, min_lng = sw
    max_lat, max_lng = ne
    n_lat = math.ceil((max_lat - min_lat) / TILE_DEG)
    n_lng = math.ceil((max_lng - min_lng) / TILE_DEG)
    d_lat = (max_lat - min_lat) / n_lat
    d_lng = (max_lng - min_lng) / n_lng
    out = []
    idx = 0
    for i in range(n_lat):
        for j in range(n_lng):
            out.append(Tile(
                idx=idx,
                min_lat=min_lat + i * d_lat,
                min_lng=min_lng + j * d_lng,
                max_lat=min_lat + (i + 1) * d_lat,
                max_lng=min_lng + (j + 1) * d_lng,
            ))
            idx += 1
    return out


def _process_tile(args: tuple[Tile, int, int]) -> tuple[Tile, int, int, str]:
    tile, tile_cap, min_year = args
    started = time.perf_counter()
    log_path = ROOT / "data" / "runs" / "grid" / f"{tile.label}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fp = log_path.open("w")

    def log(msg: str) -> None:
        log_fp.write(msg + "\n"); log_fp.flush()

    cfg = load_config()
    conn = open_store(cfg.store_path)

    # Direct Mapillary search for this tile.
    with httpx.Client(timeout=30, http2=True) as c:
        params = {
            "access_token": cfg.mapillary_token,
            "fields": FIELDS,
            "bbox": f"{tile.min_lng:.6f},{tile.min_lat:.6f},{tile.max_lng:.6f},{tile.max_lat:.6f}",
            "limit": 500,
        }
        try:
            data = _fetch_with_retry(c, f"{GRAPH_BASE}/images", params)
        except Exception as exc:
            log(f"mapillary search failed: {exc}")
            log_fp.close()
            return tile, 0, 0, f"search-fail: {exc}"

        refs = []
        for raw in data.get("data", []):
            ref = _parse_image(raw)
            if ref is None: continue
            if ref.captured_at.year < min_year: continue
            if ref.is_pano: continue
            refs.append(ref)
        refs.sort(key=lambda r: r.captured_at, reverse=True)
        refs = refs[:tile_cap]
        log(f"{tile.label}: {len(refs)} refs after filter/cap")

        if not refs:
            log_fp.close()
            return tile, 0, 0, "empty"

        # Download.
        cfg.images_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        ref_by_path: dict[Path, object] = {}
        for ref in refs:
            dst = cfg.images_dir / f"{ref.id}.jpg"
            try:
                download_image(ref, dst, c)
                downloaded.append(dst)
                ref_by_path[dst] = ref
            except httpx.HTTPError:
                pass
        log(f"{tile.label}: {len(downloaded)} downloaded")

    # SAM3 + CLIP.
    crop_dir = cfg.images_dir / "crops"
    try:
        results = detect_batch(downloaded, crop_dir)
    except Exception as exc:
        log(f"detect_batch failed: {exc}")
        log_fp.close()
        return tile, len(downloaded), 0, f"detect-fail: {exc}"

    import imagehash
    from PIL import Image as PILImage

    kept = 0
    for image_id, dets in results.items():
        path_match = next((p for p in downloaded if p.stem == image_id), None)
        if path_match is None: continue
        ref = ref_by_path[path_match]
        for det in dets:
            phash = str(imagehash.phash(PILImage.open(det.crop_path)))
            did = detection_id(image_id, phash)
            if has_recent_neighbor(conn, ref.lat, ref.lng): continue
            if has_similar_crop(conn, phash): continue
            if upsert_pending(
                conn, did, image_id, ref.lat, ref.lng,
                det.severity, det.sam3_score,
                RODZAJ["other"], MIEJSCE["building_wall"],
                det.crop_path, crop_phash=phash,
                captured_at=ref.captured_at.isoformat(),
            ):
                kept += 1
    elapsed = time.perf_counter() - started
    log(f"{tile.label}: kept {kept} in {elapsed:.0f}s")
    log_fp.close()
    return tile, len(downloaded), kept, f"{elapsed:.0f}s · {kept}/{len(downloaded)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("preset_or_sw", help="bbox preset name OR 'lat,lng' SW corner")
    ap.add_argument("ne", nargs="?", help="'lat,lng' NE corner if SW given")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--tile-cap", type=int, default=25)
    ap.add_argument("--min-year", type=int, default=2023)
    args = ap.parse_args()

    if args.preset_or_sw in BBOXES:
        sw, ne = BBOXES[args.preset_or_sw]
    else:
        sw = tuple(float(x) for x in args.preset_or_sw.split(","))
        ne = tuple(float(x) for x in args.ne.split(","))

    tiles = tile_bbox(sw, ne)
    print(f"[grid-walk] {len(tiles)} tiles · {args.workers} workers · cap {args.tile_cap}")
    print(f"            bbox SW={sw} NE={ne}")
    started = time.perf_counter()

    total_kept = 0
    total_dl = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_process_tile, (t, args.tile_cap, args.min_year)): t for t in tiles}
        for fut in as_completed(futures):
            tile, n_dl, n_kept, note = fut.result()
            total_dl += n_dl
            total_kept += n_kept
            print(f"  {tile.label}  ({tile.min_lat:.4f},{tile.min_lng:.4f}→{tile.max_lat:.4f},{tile.max_lng:.4f})  {note}")

    elapsed = time.perf_counter() - started
    print(f"\n[grid-walk] done in {elapsed:.0f}s · downloaded {total_dl} · kept {total_kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
