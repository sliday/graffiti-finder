#!/usr/bin/env python3
"""Match each pending detection to the nearest OSM heritage feature.

Graffiti on a monument is treated as a crime in Poland, not just a fine.
We pull every historic / heritage / religious-building / tourism-monument
feature in the walked Krakow bbox from OpenStreetMap Overpass, cache it,
then for each detection compute haversine distance to the closest feature.

Within 30 m we mark the detection as is_monument with the monument's name
and OSM id so the city sheet can sort criminal cases to the top.

Usage:
    uv run python scripts/check_monuments.py
    uv run python scripts/check_monuments.py --radius 50
    uv run python scripts/check_monuments.py --refresh
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "heritage" / "krakow.json"
DB = ROOT / "data" / "store.sqlite"

# Wide bbox: inner Krakow + Nowa Huta + Zabłocie + a little buffer.
BBOX = (50.020, 19.870, 50.115, 20.105)  # S, W, N, E
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
UA = "graffiti-finder/0.1 (+https://github.com/sliday/graffiti-finder)"


OVERPASS_QL = """
[out:json][timeout:90];
(
  nwr({s},{w},{n},{e})["historic"];
  nwr({s},{w},{n},{e})["heritage"];
  nwr({s},{w},{n},{e})["building"~"cathedral|chapel|church|monastery|synagogue|temple|castle"];
  nwr({s},{w},{n},{e})["tourism"~"monument|attraction|memorial"];
);
out tags center;
"""


def fetch_heritage(refresh: bool = False) -> list[dict]:
    if CACHE.exists() and not refresh:
        return json.loads(CACHE.read_text())["elements"]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    body = OVERPASS_QL.format(s=BBOX[0], w=BBOX[1], n=BBOX[2], e=BBOX[3])
    print(f"querying Overpass for bbox {BBOX}")
    with httpx.Client(timeout=120, headers={"User-Agent": UA}) as c:
        r = c.post(OVERPASS_URL, data={"data": body})
        r.raise_for_status()
    data = r.json()
    CACHE.write_text(json.dumps(data))
    print(f"  cached {len(data['elements'])} features to {CACHE.relative_to(ROOT)}")
    return data["elements"]


def _coords(element: dict) -> tuple[float, float] | None:
    if element["type"] == "node":
        return (element["lat"], element["lon"])
    centre = element.get("center")
    if centre:
        return (centre["lat"], centre["lon"])
    return None


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _kind(tags: dict) -> str:
    """Compact label for the popup."""
    if tags.get("historic"):
        return f"historic:{tags['historic']}"
    if tags.get("heritage"):
        return f"heritage:{tags['heritage']}"
    if tags.get("tourism") in ("monument", "memorial", "attraction"):
        return f"tourism:{tags['tourism']}"
    if tags.get("building") in (
        "cathedral",
        "chapel",
        "church",
        "monastery",
        "synagogue",
        "temple",
        "castle",
    ):
        return f"building:{tags['building']}"
    return "other"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(detections)")}
    for col, decl in [
        ("is_monument", "INTEGER DEFAULT 0"),
        ("monument_name", "TEXT"),
        ("monument_kind", "TEXT"),
        ("monument_distance_m", "REAL"),
        ("monument_osm_id", "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE detections ADD COLUMN {col} {decl}")
    conn.commit()


def annotate(radius_m: float, refresh: bool) -> None:
    elements = fetch_heritage(refresh=refresh)
    # Pre-compute (lat, lng, name, kind, type/id) for in-range candidates.
    features: list[tuple[float, float, str, str, str]] = []
    for el in elements:
        latlng = _coords(el)
        if latlng is None:
            continue
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:pl") or tags.get("alt_name") or "(unnamed)"
        kind = _kind(tags)
        osm_id = f"{el['type']}/{el['id']}"
        features.append((latlng[0], latlng[1], name, kind, osm_id))

    print(f"{len(features)} heritage features loaded")

    conn = sqlite3.connect(DB, timeout=15)
    _ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT detection_id, lat, lng FROM detections WHERE submit_status='pending'"
    ).fetchall()
    print(f"checking {len(rows)} pending detections within {radius_m} m")

    hits = 0
    for r in rows:
        best: tuple[float, str, str, str] | None = None
        for flat, flng, name, kind, osm_id in features:
            d = _haversine_m(r["lat"], r["lng"], flat, flng)
            if d <= radius_m and (best is None or d < best[0]):
                best = (d, name, kind, osm_id)
        if best is not None:
            conn.execute(
                "UPDATE detections SET is_monument=1, monument_name=?, "
                "monument_kind=?, monument_distance_m=?, monument_osm_id=? "
                "WHERE detection_id=?",
                (best[1], best[2], best[0], best[3], r["detection_id"]),
            )
            hits += 1
        else:
            conn.execute(
                "UPDATE detections SET is_monument=0, monument_name=NULL, "
                "monument_kind=NULL, monument_distance_m=NULL, monument_osm_id=NULL "
                "WHERE detection_id=?",
                (r["detection_id"],),
            )
    conn.commit()
    print(f"flagged {hits} of {len(rows)} as on-monument ({hits / len(rows):.1%})")

    if hits:
        print("\ntop 10 monument hits by name:")
        for row in conn.execute(
            "SELECT monument_name, monument_kind, COUNT(*) c FROM detections "
            "WHERE submit_status='pending' AND is_monument=1 "
            "GROUP BY monument_name ORDER BY c DESC LIMIT 10"
        ):
            print(f"  {row[2]:3d}  {row[0][:50]:50s} {row[1]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--radius", type=float, default=30.0, help="metres")
    parser.add_argument("--refresh", action="store_true", help="re-query Overpass")
    args = parser.parse_args()
    annotate(args.radius, args.refresh)


if __name__ == "__main__":
    main()
