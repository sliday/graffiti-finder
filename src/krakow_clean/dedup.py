"""SQLite dedup + queue store."""
from __future__ import annotations

import hashlib
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS detections (
    detection_id  TEXT PRIMARY KEY,
    image_id      TEXT NOT NULL,
    lat           REAL NOT NULL,
    lng           REAL NOT NULL,
    severity      TEXT,
    score         REAL,
    rodzaj        TEXT,
    miejsce       TEXT,
    crop_path     TEXT,
    crop_phash    TEXT,
    captured_at   TEXT,          -- ISO 8601, when the Mapillary frame was taken
    detected_at   TEXT NOT NULL, -- when we ran SAM3+CLIP
    submitted_at  TEXT,
    submit_status TEXT NOT NULL DEFAULT 'pending',
    instance_id   TEXT,
    response_blob TEXT
);
"""

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_loc ON detections(round(lat, 4), round(lng, 4));
CREATE INDEX IF NOT EXISTS idx_status ON detections(submit_status);
CREATE INDEX IF NOT EXISTS idx_phash ON detections(crop_phash);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns on existing DBs (best-effort, idempotent)."""
    for col, decl in [("crop_phash", "TEXT"), ("captured_at", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE detections ADD COLUMN {col} {decl}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column exists


@dataclass(frozen=True)
class StoredDetection:
    detection_id: str
    image_id: str
    lat: float
    lng: float
    severity: str
    score: float
    rodzaj: str
    miejsce: str
    crop_path: str
    detected_at: str
    submit_status: str


def open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    # WAL mode allows concurrent reads + a single writer with much less
    # blocking — the parallel walker fan-out hits the store from several
    # processes at once. The PRAGMA itself races when multiple workers
    # open the store simultaneously on a fresh DB; retry briefly.
    for _ in range(10):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            time.sleep(0.2)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")

    # Table/index DDL is also serialised under WAL; same retry.
    for _ in range(10):
        try:
            conn.executescript(TABLE_DDL)
            _migrate(conn)
            conn.executescript(INDEX_DDL)
            break
        except sqlite3.OperationalError:
            time.sleep(0.2)

    conn.row_factory = sqlite3.Row
    return conn


def _phash_hamming(a: str, b: str) -> int:
    """Hamming distance between two ImageHash hex strings."""
    if not a or not b or len(a) != len(b):
        return 64  # max distance for 64-bit pHash
    ia = int(a, 16)
    ib = int(b, 16)
    return bin(ia ^ ib).count("1")


def detection_id(image_id: str, mask_phash: str) -> str:
    return hashlib.sha256(f"{image_id}::{mask_phash}".encode()).hexdigest()[:32]


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def has_recent_neighbor(
    conn: sqlite3.Connection,
    lat: float,
    lng: float,
    *,
    radius_m: float = 30.0,
    days: int = 30,
) -> bool:
    """True if any *submitted* record within radius_m in the last `days`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT lat, lng FROM detections
        WHERE submit_status = 'submitted'
          AND submitted_at >= ?
          AND round(lat, 3) BETWEEN round(?, 3) - 0.001 AND round(?, 3) + 0.001
          AND round(lng, 3) BETWEEN round(?, 3) - 0.001 AND round(?, 3) + 0.001
        """,
        (cutoff, lat, lat, lng, lng),
    ).fetchall()
    return any(_haversine_m(lat, lng, r["lat"], r["lng"]) <= radius_m for r in rows)


def has_close_existing(
    conn: sqlite3.Connection,
    lat: float,
    lng: float,
    *,
    radius_m: float = 12.0,
) -> bool:
    """True if any record (pending OR submitted) within radius_m.

    Tight radius — same wall captured from different angles in a Mapillary
    sequence lands within ~12 m of itself even with GPS noise.
    """
    rows = conn.execute(
        """
        SELECT lat, lng FROM detections
        WHERE round(lat, 3) BETWEEN round(?, 3) - 0.001 AND round(?, 3) + 0.001
          AND round(lng, 3) BETWEEN round(?, 3) - 0.001 AND round(?, 3) + 0.001
        """,
        (lat, lat, lng, lng),
    ).fetchall()
    return any(_haversine_m(lat, lng, r["lat"], r["lng"]) <= radius_m for r in rows)


def has_similar_crop(
    conn: sqlite3.Connection,
    crop_phash: str,
    *,
    max_hamming: int = 10,
) -> bool:
    """True if any existing crop's pHash is within Hamming distance.

    pHash is computed on the crop bitmap, so near-duplicate graffiti shots
    from sequential Mapillary frames collide here even when GPS drifts.
    """
    if not crop_phash:
        return False
    rows = conn.execute(
        "SELECT crop_phash FROM detections WHERE crop_phash IS NOT NULL"
    ).fetchall()
    return any(_phash_hamming(crop_phash, r["crop_phash"]) <= max_hamming for r in rows)


def upsert_pending(
    conn: sqlite3.Connection,
    detection_id_: str,
    image_id: str,
    lat: float,
    lng: float,
    severity: str,
    score: float,
    rodzaj: str,
    miejsce: str,
    crop_path: Path,
    crop_phash: str | None = None,
    captured_at: str | None = None,
) -> bool:
    """Return True if newly inserted, False if it already existed."""
    detected_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO detections
        (detection_id, image_id, lat, lng, severity, score, rodzaj, miejsce,
         crop_path, crop_phash, captured_at, detected_at, submit_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            detection_id_,
            image_id,
            lat,
            lng,
            severity,
            score,
            rodzaj,
            miejsce,
            str(crop_path),
            crop_phash,
            captured_at,
            detected_at,
        ),
    )
    conn.commit()
    return cur.rowcount == 1


def prune_stale_at_spot(
    conn: sqlite3.Connection,
    *,
    bucket_degrees: float = 0.0001,  # ~11 m at Krakow latitude
) -> int:
    """For each spatial bucket, keep only detections from the most recent
    Mapillary frame (by captured_at). Older frames at the same wall are
    deleted on the assumption their graffiti may have been cleaned.

    Returns the number of rows deleted.
    """
    rows = conn.execute(
        """
        SELECT detection_id, image_id, lat, lng, captured_at
        FROM detections
        WHERE submit_status = 'pending'
          AND captured_at IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return 0
    freshest: dict[tuple[int, int], tuple[str, str]] = {}
    for r in rows:
        key = (round(r["lat"] / bucket_degrees), round(r["lng"] / bucket_degrees))
        prev = freshest.get(key)
        if prev is None or r["captured_at"] > prev[1]:
            freshest[key] = (r["image_id"], r["captured_at"])

    to_delete: list[str] = []
    for r in rows:
        key = (round(r["lat"] / bucket_degrees), round(r["lng"] / bucket_degrees))
        keeper_image, _ = freshest[key]
        if r["image_id"] != keeper_image:
            to_delete.append(r["detection_id"])

    if to_delete:
        placeholders = ",".join("?" * len(to_delete))
        conn.execute(
            f"DELETE FROM detections WHERE detection_id IN ({placeholders})",
            to_delete,
        )
        conn.commit()
    return len(to_delete)


def mark_submitted(
    conn: sqlite3.Connection,
    detection_id_: str,
    instance_id: str,
    response_blob: str,
    status: str = "submitted",
) -> None:
    conn.execute(
        """
        UPDATE detections SET submit_status = ?, submitted_at = ?,
            instance_id = ?, response_blob = ?
        WHERE detection_id = ?
        """,
        (
            status,
            datetime.now(timezone.utc).isoformat(),
            instance_id,
            response_blob,
            detection_id_,
        ),
    )
    conn.commit()


def list_pending(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM detections WHERE submit_status = 'pending' "
        "ORDER BY score DESC LIMIT ?",
        (limit,),
    ).fetchall()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT submit_status, COUNT(*) c FROM detections GROUP BY submit_status"
    ).fetchall()
    return {r["submit_status"]: r["c"] for r in rows}
