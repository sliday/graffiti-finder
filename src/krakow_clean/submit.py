"""Submission to the Krakow Survey123-backed feature service.

Two paths supported:

* **addFeatures (default)** — direct ArcGIS REST call to the feature service.
  Cleaner, faster, verified anonymous. Photo attaches via a follow-up
  `addAttachment` POST against the returned objectId.
* **openrosa** — POST OpenRosa multipart XForm XML to the Survey123 share item.
  Kept for parity with the webform.

Both run anonymously over plain HTTP. Anonymity is preserved by leaving the
email field empty, stripping EXIF from photos, jittering inter-submit delays,
and rotating user-agent strings.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from pyproj import Transformer

from .config import Config
from .enrichment import Enrichment, enrich
from .formspec import GraffitiReport, build_xform_xml
from .vision import encode_clean_jpeg

FEATURE_SERVER = (
    "https://bezpiecznie.um.krakow.pl/server/rest/services/Graffitti_v1_2/FeatureServer/0"
)
ADD_FEATURES = f"{FEATURE_SERVER}/addFeatures"
DELETE_FEATURES = f"{FEATURE_SERVER}/deleteFeatures"

# ArcGIS uses Web Mercator Auxiliary Sphere (wkid 102100), aliased by EPSG:3857.
# Within Krakow bbox the difference vs strict 3857 is sub-meter — acceptable.
_TO_MERCATOR = Transformer.from_crs(4326, 3857, always_xy=True)


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
]


@dataclass(frozen=True)
class SubmitResult:
    instance_id: str
    status_code: int
    object_id: Optional[int]
    global_id: Optional[str]
    duration_ms: int
    response_text: str
    submitted_at: datetime
    attachment_id: Optional[int] = None


def _headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, */*",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7",
        "Origin": "https://survey123.arcgis.com",
        "Referer": "https://survey123.arcgis.com/",
    }


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _to_attributes(
    report: GraffitiReport,
    enrichment: Enrichment | None,
    submitted_at: datetime,
) -> dict[str, object]:
    attrs: dict[str, object] = {
        "data_zgloszenia": _epoch_ms(submitted_at),
        "rodzaj_graffitti": report.rodzaj,
        "miejsce_graffitti": report.miejsce,
        "wsp_x": report.lng,
        "wsp_y": report.lat,
        "nazwa_jednostki": "osoba fizyczna",
        "adres_mailowy": None,
    }
    if enrichment is not None:
        attrs.update(
            {
                "dzielnica": enrichment.dzielnica,
                "rejon_sm": enrichment.rejon_sm,
                "identyfikator_budynku": enrichment.identyfikator_budynku,
                "identyfikator_dzialki": enrichment.identyfikator_dzialki,
                "adres": enrichment.adres,
            }
        )
    return attrs


def submit_via_feature_service(
    config: Config,
    report: GraffitiReport,
    *,
    client: httpx.Client | None = None,
    log_dir: Path | None = None,
    do_enrich: bool = True,
) -> SubmitResult:
    owned = client is None
    if owned:
        client = httpx.Client(timeout=60)
    started = time.perf_counter()
    submitted_at = datetime.now(timezone.utc)

    enrichment = enrich(client, report.lng, report.lat) if do_enrich else None
    x, y = _TO_MERCATOR.transform(report.lng, report.lat)
    feature = {
        "geometry": {
            "x": x,
            "y": y,
            "spatialReference": {"wkid": 102100},
        },
        "attributes": _to_attributes(report, enrichment, submitted_at),
    }
    payload = {"f": "json", "features": json.dumps([feature], ensure_ascii=False)}

    try:
        response = client.post(ADD_FEATURES, data=payload, headers=_headers())
    finally:
        pass
    duration_ms = int((time.perf_counter() - started) * 1000)

    object_id: Optional[int] = None
    global_id: Optional[str] = None
    try:
        data = response.json()
        add_results = data.get("addResults", [])
        if add_results and add_results[0].get("success"):
            object_id = add_results[0].get("objectId")
            global_id = add_results[0].get("globalId")
    except ValueError:
        data = {"raw": response.text[:500]}

    attachment_id: Optional[int] = None
    if (
        object_id is not None
        and report.photo_path is not None
        and report.photo_path.exists()
    ):
        try:
            attachment_id = _add_attachment(
                client, object_id, report.photo_path, report.photo_field_name
            )
        except httpx.HTTPError as exc:
            data["attachment_error"] = str(exc)

    if owned:
        client.close()

    result = SubmitResult(
        instance_id=global_id or "no-global-id",
        status_code=response.status_code,
        object_id=object_id,
        global_id=global_id,
        duration_ms=duration_ms,
        response_text=response.text[:4000],
        submitted_at=submitted_at,
        attachment_id=attachment_id,
    )
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "object_id": object_id,
            "global_id": global_id,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "submitted_at": submitted_at.isoformat(),
            "lat": report.lat,
            "lng": report.lng,
            "rodzaj": report.rodzaj,
            "miejsce": report.miejsce,
            "photo": str(report.photo_path) if report.photo_path else None,
            "enrichment": enrichment.__dict__ if enrichment else None,
            "response_text": response.text[:1500],
            "attachment_id": attachment_id,
        }
        with (log_dir / "submissions.jsonl").open("a") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result


def _add_attachment(
    client: httpx.Client, object_id: int, photo_path: Path, field_name: str
) -> Optional[int]:
    url = f"{FEATURE_SERVER}/{object_id}/addAttachment"
    jpeg_bytes = encode_clean_jpeg(photo_path)
    files = {"attachment": (field_name, jpeg_bytes, "image/jpeg")}
    response = client.post(url, files=files, data={"f": "json"}, headers=_headers())
    try:
        data = response.json()
        result = data.get("addAttachmentResult", {})
        if result.get("success"):
            return result.get("objectId")
    except ValueError:
        pass
    return None


def delete_feature(
    config: Config,
    object_id: int,
    *,
    client: httpx.Client | None = None,
) -> bool:
    """Test-helper. Removes a feature we created for sanity-check submissions."""
    owned = client is None
    if owned:
        client = httpx.Client(timeout=30)
    try:
        response = client.post(
            DELETE_FEATURES,
            data={"f": "json", "objectIds": str(object_id)},
            headers=_headers(),
        )
        data = response.json()
        return bool(data.get("deleteResults", [{}])[0].get("success"))
    finally:
        if owned:
            client.close()


def submit_via_openrosa(
    config: Config,
    report: GraffitiReport,
    *,
    client: httpx.Client | None = None,
    log_dir: Path | None = None,
) -> SubmitResult:
    """Legacy path: OpenRosa form-data POST to the Survey123 item endpoint.

    Kept for fallback when the FeatureServer path returns errors.
    """
    xml, instance_id = build_xform_xml(report)
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        (
            "xml_submission_file",
            ("xml_submission_file", xml.encode("utf-8"), "application/xml"),
        ),
    ]
    if report.photo_path is not None and report.photo_path.exists():
        files.append(
            (
                report.photo_field_name,
                (report.photo_field_name, encode_clean_jpeg(report.photo_path), "image/jpeg"),
            )
        )

    owned = client is None
    if owned:
        client = httpx.Client(timeout=60)
    started = time.perf_counter()
    submitted_at = datetime.now(timezone.utc)
    try:
        response = client.post(config.submit_action, files=files, headers=_headers())
    finally:
        if owned:
            client.close()
    duration_ms = int((time.perf_counter() - started) * 1000)

    result = SubmitResult(
        instance_id=instance_id,
        status_code=response.status_code,
        object_id=None,
        global_id=None,
        duration_ms=duration_ms,
        response_text=response.text[:4000],
        submitted_at=submitted_at,
    )
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "instance_id": instance_id,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "submitted_at": submitted_at.isoformat(),
            "lat": report.lat,
            "lng": report.lng,
            "rodzaj": report.rodzaj,
            "miejsce": report.miejsce,
            "photo": str(report.photo_path) if report.photo_path else None,
            "response_text": response.text[:1500],
            "path": "openrosa",
        }
        with (log_dir / "submissions.jsonl").open("a") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result


def jitter_sleep(low: float = 60.0, high: float = 300.0) -> None:
    time.sleep(random.uniform(low, high))


# Public façade — default to the FeatureServer path.
submit_report = submit_via_feature_service
