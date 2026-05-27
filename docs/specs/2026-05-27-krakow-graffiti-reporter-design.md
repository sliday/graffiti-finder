# Krakow Graffiti Auto-Reporter — Design

**Date:** 2026-05-27
**Author:** stas + claude
**Status:** Draft → executing

## Goal

Walk Karmelicka and Królewska streets in Krakow via Mapillary imagery,
detect graffiti on walls with a local vision model, generate a Polish-language
report per detection, and submit each report to the Krakow city Survey123 form
autonomously. No PII, fingerprint-randomized browser, idempotent submissions.

## Seed waypoints

- Start: `50.0635222, 19.9329175` (Karmelicka, heading 337°)
- A/B test: `50.0657287, 19.9305152` (Caffe Avanti area, heading 315°)

Route is the line through both points plus extensions north (Królewska) and
south (Karmelicka toward Old Town). Bounding box drives Mapillary search.

## Decisions (locked by user)

| Decision        | Choice                                  |
|-----------------|------------------------------------------|
| Submission mode | Fully autonomous                         |
| Imagery source  | Mapillary Graph API                      |
| Anonymity stack | Plain Playwright + fingerprint mitigation (no Tor/VPN) |
| Vision pipeline | facebook/sam3 via HF transformers        |

Autonomous + no network proxy is the user's call. Mitigation:
- High confidence threshold for detection (mask area + score gates)
- Dedup store prevents re-submission of same wall
- Randomized inter-submission delay (60-300s)
- EXIF stripped from any uploaded image
- No name, no email, no phone in form fields
- Playwright with pl-PL locale, Europe/Warsaw TZ, residential UA string

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  CLI (krakow-clean walk|submit|status)                           │
└──────────────────────────────────────────────────────────────────┘
            │
            ▼
┌───────────────────┐    ┌────────────────┐    ┌──────────────────┐
│  Route Planner    │───▶│  Mapillary     │───▶│  Image Cache     │
│  (waypoints→bbox) │    │  Fetcher       │    │  (PNG + meta)    │
└───────────────────┘    └────────────────┘    └──────────────────┘
                                                        │
                                                        ▼
                                              ┌──────────────────┐
                                              │  SAM3 Detector   │
                                              │  prompt=graffiti │
                                              └──────────────────┘
                                                        │
                                              ┌─────────┴─────────┐
                                              ▼                   ▼
                                    ┌──────────────────┐  ┌─────────────┐
                                    │  Confidence gate │  │  Discard    │
                                    └──────────────────┘  └─────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │  Reverse geocoder│ (Nominatim)
                                    │  lat,lng→ulica   │
                                    └──────────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │  Dedup store     │ (SQLite)
                                    │  (image+mask hash│
                                    └──────────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │  Playwright      │
                                    │  form submitter  │
                                    └──────────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │  Survey123 API   │
                                    │  (city portal)   │
                                    └──────────────────┘
```

## Modules

### `walker.py` — Route planner + Mapillary fetcher

- Input: list of waypoints, search radius (default 25m), bbox computed from
  envelope.
- Mapillary search: `GET https://graph.mapillary.com/images?bbox=...&fields=id,
  geometry,captured_at,compass_angle,sequence,thumb_2048_url,is_pano`.
- Bbox capped at 0.01° square per Mapillary limit; route is tiled into
  sub-boxes if larger.
- Filter: prefer images captured within last 24 months, prefer non-pano
  side-facing shots, dedupe by sequence proximity (1 image per ~5m along path).
- Output: iterator of `ImageRef(id, lat, lng, captured_at, compass, url)`.

### `vision.py` — SAM3 detector

- `Sam3Model.from_pretrained("facebook/sam3")` loaded once, MPS device on
  Apple Silicon.
- For each image: download via httpx, open with PIL, run
  `processor(images=img, text="graffiti on wall", return_tensors="pt")`.
- Post-process: `processor.post_process_instance_segmentation(outputs,
  threshold=0.5, mask_threshold=0.5, target_sizes=...)`.
- Heuristic gate: keep masks with `score >= 0.55` AND
  `mask_area >= 1500px` AND `mask_area / image_area <= 0.4` (drops false
  positives that mask the whole image like billboards).
- Compute severity score from masked-region contrast variance (paint contrast
  vs surrounding wall) — three buckets: minor / moderate / severe.
- Output: list of `Detection(image_id, bbox, mask_png_bytes, score, severity)`.

### `geo.py` — Reverse geocoder

- Nominatim public endpoint with custom User-Agent `clean-krakow/0.1
  (stas@variant.net)` per ToS.
- Rate-limited to 1 req/sec, retries with backoff.
- Returns `Address(street, house_number, neighborhood, postcode,
  full_label_pl)`.
- Local cache by 6-decimal-rounded lat,lng (≈11cm) in SQLite.

### `formspec.py` — Form schema loader

- On first run: download Survey123 form binary from
  `https://bezpiecznie.um.krakow.pl/portal/sharing/rest/content/items/
  b996fb8c744f41a69c5d51729702c55b/data` to `data/form/Gravvitti_v_1.zip`.
- Unzip → parse `.xml` (XForm) for `<bind nodeset>` and required attrs, and
  `.webform/.json` for the field order + labels.
- Persist parsed schema as `data/form/schema.json`.
- Reused by submitter to map our `Detection + Address` payload to form fields.

### `submit.py` — Playwright submitter

- Headless Chromium, args: `--lang=pl-PL`,
  `--timezone-id=Europe/Warsaw`, `--accept-lang=pl-PL,pl;q=0.9,en;q=0.6`.
- UA randomized from a small allowlist (recent Chrome/Firefox on macOS+Win).
- Viewport randomized within {1366x768, 1440x900, 1536x864, 1920x1080}.
- Sequence per submission:
  1. New context (fresh storage + cookies).
  2. Navigate to form share URL with `portalUrl` param.
  3. Wait for form ready.
  4. Click map / type address into location field.
  5. Fill description (Polish), select severity, select category.
  6. Upload EXIF-stripped JPEG (cropped to detection bbox + 30% padding).
  7. Submit, wait for confirmation, capture response.
  8. Close context.
- Inter-submission jitter: `uniform(60, 300)` seconds.
- All API/network responses logged to `data/runs/<ts>/submissions.jsonl`.

### `dedup.py` — Idempotency store

SQLite schema:
```sql
CREATE TABLE detections (
  detection_id     TEXT PRIMARY KEY,        -- sha256(image_id + mask_phash)
  mapillary_image  TEXT NOT NULL,
  lat              REAL NOT NULL,
  lng              REAL NOT NULL,
  address          TEXT,
  severity         TEXT,
  score            REAL,
  detected_at      DATETIME NOT NULL,
  submitted_at     DATETIME,
  submit_status    TEXT,                    -- pending|submitted|failed|skipped
  response_blob    TEXT
);
CREATE INDEX idx_loc ON detections(round(lat,4), round(lng,4));
```
Dedup rules:
- Same `detection_id` → skip.
- Within 30m of any submitted detection in last 30 days → skip (city already
  notified).

### `cli.py` — Orchestrator

```
krakow-clean walk      --route karmelicka --dry-run    # detect only
krakow-clean walk      --route karmelicka --submit     # detect + submit
krakow-clean submit    --pending                       # submit queued
krakow-clean status                                    # show counts
krakow-clean form sync                                 # refresh form schema
```

## Data flow

1. `walk` → bbox → Mapillary search → image refs.
2. Per image: download → SAM3 → gate → dedup check.
3. Survivors: reverse geocode → write to SQLite as `pending`.
4. `submit` reads `pending`, opens Playwright, posts each, marks `submitted`
   or `failed`.

## Project layout

```
clean-krakow/
├── docs/specs/2026-05-27-krakow-graffiti-reporter-design.md
├── pyproject.toml
├── .env                       (gitignored, mapillary token)
├── .gitignore
├── data/
│   ├── form/Gravvitti_v_1.zip
│   ├── form/schema.json
│   ├── store.sqlite
│   ├── images/<image_id>.jpg
│   └── runs/<ts>/submissions.jsonl
└── src/krakow_clean/
    ├── __init__.py
    ├── cli.py
    ├── walker.py
    ├── vision.py
    ├── geo.py
    ├── formspec.py
    ├── submit.py
    ├── dedup.py
    └── routes/karmelicka.json   (waypoint list)
```

## Dependencies

- `httpx[http2]`, `pillow`, `piexif`, `pydantic`
- `torch`, `transformers` (for SAM3 — MPS device on macOS Apple Silicon)
- `playwright` (Chromium)
- `pyproj` for distance calcs, `geojson` for shapes
- `typer` for CLI

## Risks + mitigations

| Risk                                        | Mitigation                                 |
|---------------------------------------------|--------------------------------------------|
| SAM3 false positive submits noise           | Threshold + dedup + mask-area gate         |
| Survey123 detects automation                | Stealth flags, jitter, fresh context       |
| Mapillary photo too old (graffiti gone)     | Filter by `captured_at` ≤ 24 months        |
| Submit to wrong location                    | Nominatim cross-check + drop low-precision GPS |
| Rate-limit ban from city portal             | Default cap 20 submissions per run         |
| Photo upload reveals user metadata          | EXIF strip + re-encode JPEG before upload  |
| Repeat submissions for same wall            | Dedup key on hash + 30m / 30d radius rule  |

## Form schema (resolved 2026-05-27)

Form unpacked from `data/form/extracted/esriinfo/`:

- **Survey title:** `Gravvitti_v_1` (id `Graffitti_v1_2`)
- **CAPTCHA:** disabled
- **Submission endpoint:** `POST https://bezpiecznie.um.krakow.pl/portal/sharing/rest/content/items/fc26d76f8cfb4b478ae6132446459f28`
- **Submission format:** OpenRosa `form-data-post` (multipart XForm XML + binary attachments)
- **Server-side enrichment:** `dzielnica`, `rejon_sm`, `identyfikator_budynku`,
  `identyfikator_dzialki`, `adres` are computed server-side from `Graffitti_v1_point`
  via ArcGIS feature-service `pulldata`. Our payload sends them empty.

**Fields:**

| Field | Type | Required | Choices |
|-------|------|----------|---------|
| `Graffitti_v1_point` | geopoint | YES | "lat lng 0 0" |
| `data_zgloszenia` | dateTime | YES | now() |
| `Graffitti_v1_image` | binary | no | JPEG ≤ 10MB |
| `rodzaj_graffitti` | select1 | no | brak/bazgroly/mowa nienawisci/mural/inne |
| `miejsce_graffitti` | select1 | no | brak/sciana budynku/ogrodzenie/filar mostu/wiata/garaz/inne |
| `wsp_x`, `wsp_y` | decimal | YES | calculated from geopoint |
| `nazwa_jednostki` | select1 | YES | "osoba fizyczna" (only option) |
| `adres_mailowy` | string | no | optional email (we leave empty) |
| `meta/instanceID` | uuid | auto | `uuid:<v4>` |

Anonymity preserved: email is the only PII field and it's optional.

**Pipeline simplification:** direct POST replaces Playwright. The submitter
becomes an `httpx`-based OpenRosa client. Playwright dependency dropped from
the runtime; kept only as a debugging tool for manual flows.

## Open questions to resolve during impl

1. Does the FeatureServer accept anonymous direct POST? (recon agent running)
2. Mapillary coverage age + density on the corridor? (recon agent running)
3. SAM3 weight size + MPS support on this Mac? (recon agent running)

## Success criteria

- Detection precision verified by manual review of first 10 detections
  (>= 8/10 true graffiti).
- One live submission completes successfully with confirmation page.
- Full Karmelicka walk produces between 5 and 50 deduped detections.
- No re-submission for the same wall across two runs.
