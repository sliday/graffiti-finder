"""Mapillary Graph API walker.

Given a route (list of waypoints), returns image candidates along the corridor.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx

from .config import Config

GRAPH_BASE = "https://graph.mapillary.com"
BBOX_MAX_DEG = 0.009  # Mapillary cap is 0.01°; keep margin.
FIELDS = "id,geometry,captured_at,compass_angle,sequence,thumb_2048_url,is_pano"


@dataclass(frozen=True)
class Waypoint:
    lat: float
    lng: float
    label: str = ""


@dataclass(frozen=True)
class ImageRef:
    id: str
    lat: float
    lng: float
    captured_at: datetime
    compass_angle: float | None
    sequence: str
    thumb_url: str
    is_pano: bool


def _envelope(waypoints: list[Waypoint], buffer_m: float) -> tuple[float, float, float, float]:
    lats = [w.lat for w in waypoints]
    lngs = [w.lng for w in waypoints]
    lat_buf = buffer_m / 111_111
    # 1 degree longitude ≈ 111_111 * cos(lat) meters; use mid-lat.
    mid_lat = (min(lats) + max(lats)) / 2
    lng_buf = buffer_m / (111_111 * max(math.cos(math.radians(mid_lat)), 0.01))
    return (
        min(lngs) - lng_buf,
        min(lats) - lat_buf,
        max(lngs) + lng_buf,
        max(lats) + lat_buf,
    )


def _tile_bbox(bbox: tuple[float, float, float, float], max_deg: float = BBOX_MAX_DEG) -> Iterator[tuple[float, float, float, float]]:
    min_lng, min_lat, max_lng, max_lat = bbox
    lat_steps = max(1, math.ceil((max_lat - min_lat) / max_deg))
    lng_steps = max(1, math.ceil((max_lng - min_lng) / max_deg))
    d_lat = (max_lat - min_lat) / lat_steps
    d_lng = (max_lng - min_lng) / lng_steps
    for i in range(lat_steps):
        for j in range(lng_steps):
            yield (
                min_lng + j * d_lng,
                min_lat + i * d_lat,
                min_lng + (j + 1) * d_lng,
                min_lat + (i + 1) * d_lat,
            )


def _parse_image(item: dict) -> ImageRef | None:
    try:
        coords = item["geometry"]["coordinates"]
        captured = datetime.fromtimestamp(item["captured_at"] / 1000, tz=timezone.utc)
        return ImageRef(
            id=item["id"],
            lng=coords[0],
            lat=coords[1],
            captured_at=captured,
            compass_angle=item.get("compass_angle"),
            sequence=item.get("sequence", ""),
            thumb_url=item.get("thumb_2048_url", ""),
            is_pano=bool(item.get("is_pano", False)),
        )
    except (KeyError, TypeError):
        return None


def _fetch_with_retry(
    client: httpx.Client, url: str, params: dict, attempts: int = 4
) -> dict:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.get(url, params=params, timeout=30)
            if response.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                time.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
    if last_exc:
        raise last_exc
    return {}


def search_corridor(
    config: Config,
    waypoints: list[Waypoint],
    *,
    buffer_m: float = 25.0,
    min_year: int = 2023,
    drop_panos: bool = True,
    per_tile_limit: int = 500,
    client: httpx.Client | None = None,
) -> list[ImageRef]:
    """Fetch image refs across the route envelope. Tile if bbox exceeds cap."""
    owned_client = client is None
    if owned_client:
        client = httpx.Client(timeout=30, http2=True)
    try:
        bbox = _envelope(waypoints, buffer_m)
        results: dict[str, ImageRef] = {}
        for tile in _tile_bbox(bbox):
            params = {
                "access_token": config.mapillary_token,
                "fields": FIELDS,
                "bbox": ",".join(f"{v:.7f}" for v in tile),
                "limit": per_tile_limit,
            }
            data = _fetch_with_retry(client, f"{GRAPH_BASE}/images", params)
            for raw in data.get("data", []):
                ref = _parse_image(raw)
                if ref is None:
                    continue
                if ref.captured_at.year < min_year:
                    continue
                if drop_panos and ref.is_pano:
                    continue
                results[ref.id] = ref
        return sorted(results.values(), key=lambda r: r.captured_at, reverse=True)
    finally:
        if owned_client:
            client.close()


def fetch_geometry(
    config: Config, image_ids: list[str], client: httpx.Client | None = None
) -> dict[str, ImageRef]:
    """Look up Mapillary geometry + capture time for a list of image_ids.

    Used by tooling that operates on the on-disk image cache without re-running
    a full bbox search. One entity call per image_id; rate-limit is generous
    (60k/min).
    """
    owned = client is None
    if owned:
        client = httpx.Client(timeout=20, http2=True)
    refs: dict[str, ImageRef] = {}
    try:
        for image_id in image_ids:
            params = {
                "access_token": config.mapillary_token,
                "fields": FIELDS,
            }
            try:
                data = _fetch_with_retry(client, f"{GRAPH_BASE}/{image_id}", params, attempts=3)
            except httpx.HTTPError:
                continue
            ref = _parse_image(data)
            if ref is not None:
                refs[image_id] = ref
        return refs
    finally:
        if owned:
            client.close()


def download_image(ref: ImageRef, out_path: Path, client: httpx.Client) -> Path:
    if out_path.exists():
        return out_path
    response = client.get(ref.thumb_url, timeout=60)
    response.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(response.content)
    return out_path
