"""SQLite dedup + queue store."""
from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
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
    detected_at   TEXT NOT NULL,
    submitted_at  TEXT,
    submit_status TEXT NOT NULL DEFAULT 'pending',
    instance_id   TEXT,
    response_blob TEXT
);

CREATE INDEX IF NOT EXISTS idx_loc ON detections(round(lat, 4), round(lng, 4));
CREATE INDEX IF NOT EXISTS idx_status ON detections(submit_status);
"""


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
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


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
) -> bool:
    """Return True if newly inserted, False if it already existed."""
    detected_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO detections
        (detection_id, image_id, lat, lng, severity, score, rodzaj, miejsce,
         crop_path, detected_at, submit_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
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
            detected_at,
        ),
    )
    conn.commit()
    return cur.rowcount == 1


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
