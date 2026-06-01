# clean-krakow — Process

How the pipeline runs end to end. For "why" see `FINDINGS.md`. For
"things that surprised us" see `LEARNED.md`.

---

## One-shot setup

```bash
cd graffiti-finder

# 1. Main env (Python 3.12, torch + transformers + playwright)
uv sync

# 2. Sidecar env for SAM3 (Python 3.13 + mlx)
bash scripts/install_sam3.sh
# (or manually:)
#   mkdir -p vendor
#   git clone --depth 1 https://github.com/Deekshith-Dade/mlx_sam3 vendor/mlx_sam3
#   cd vendor/mlx_sam3 && uv sync

# 3. .env with credentials (gitignored)
cat > .env <<EOF
MAPILLARY_APP_ID=...
MAPILLARY_ACCESS_TOKEN=MLY|...
SURVEY123_ITEM_ID=b996fb8c744f41a69c5d51729702c55b
SURVEY123_PORTAL=https://bezpiecznie.um.krakow.pl/portal
SURVEY123_SHARE_URL=https://survey123.arcgis.com/share/b996fb8c744f41a69c5d51729702c55b?portalUrl=https://bezpiecznie.um.krakow.pl/portal
HF_TOKEN=hf_...
HUGGING_FACE_HUB_TOKEN=hf_...
NOMINATIM_UA=graffiti-finder/0.1 (you@example)
EOF

# 4. HF auth for the SAM3 weights
hf auth login    # paste token
```

---

## Daily flow

```bash
# Find every Mapillary image on a route, download + detect + enqueue
uv run python scripts/walk_full_sam3.py karmelicka

# Or quick coverage check
uv run krakow-clean probe --route karmelicka

# Or smaller, faster walk
uv run krakow-clean walk --route karmelicka --max-images 30

# See the queue
uv run krakow-clean status

# Render proposed submissions (no POST)
uv run krakow-clean mock --limit 50

# Build local overlay gallery
uv run python scripts/visualize_queue.py

# Export the queue as a Google Sheet
uv run python scripts/export_sheet.py
```

---

## The pipeline, step by step

```
┌───────────────────────────┐
│ routes.py — waypoint list │   waypoint lists for Karmelicka, Królewska,
└──────────────┬────────────┘   Floriańska, Szewska, Grodzka, Krupnicza,
               │                Józefa, Estery, Kazimierz core, all.
               ▼
┌───────────────────────────┐
│ walker.search_corridor    │   bbox of envelope, tile to 0.009° squares
│ → Mapillary Graph API     │   (cap is 0.01°), GET /images. Retry on 500.
└──────────────┬────────────┘   Returns ImageRef list (id, lat, lng,
               │                captured_at, compass, thumb URL).
               ▼
┌───────────────────────────┐
│ walker.download_image     │   parallel downloads to data/images/<id>.jpg.
└──────────────┬────────────┘   Caches: skips if file already exists.
               │
               ▼
┌───────────────────────────┐
│ vision_sam3.detect_batch  │   spawns SAM3 sidecar (Python 3.13 + mlx)
│   → sidecar.py            │   in vendor/mlx_sam3 via subprocess. Sidecar
│   → SAM3 image model      │   emits one JSON line per image to stdout:
│   → returns boxes, scores │   {"image": "...", "elapsed_ms": N,
└──────────────┬────────────┘    "detections": [{"bbox": [..], "score": ..}]}
               │
               ▼
┌───────────────────────────┐
│ filter chain              │   area gates (min 2000 px², max 40% frame)
│   1. min_area_px          │   aspect ratio ≥ 0.45 (drops tall posts)
│   2. max_area_frac        │   colour std ≥ 16 (drops uniform pillars)
│   3. aspect_ratio         │
│   4. color_std            │
└──────────────┬────────────┘
               │
               ▼
┌───────────────────────────┐
│ CLIP zero-shot classifier │   crops each surviving bbox, classifies
│   "graffiti vs 9 others"  │   against 10 labels. Keep only if positive
│   → vision.classify_crop  │   index is argmax with prob ≥ 0.30.
└──────────────┬────────────┘
               │
               ▼
┌───────────────────────────┐
│ enrichment.enrich         │   parallel calls to Krakow GIS:
│   → Dzielnice             │   district, police region, building ID,
│   → Rejony_SM             │   parcel ID, reverse-geocoded address.
│   → EG_budynki_okrojona   │   Geocoder needs JSON geometry, not "x,y".
│   → EG_dzialki_okrojona   │
│   → Lokalizator_Krakow    │
└──────────────┬────────────┘
               │
               ▼
┌───────────────────────────┐
│ dedup                     │   has_recent_neighbor (30 m / 30 d for
│   has_similar_crop        │   submitted records — backstop)
│   has_recent_neighbor     │   has_similar_crop (pHash Hamming ≤ 10 —
│                           │   catches same tag in sequential frames)
└──────────────┬────────────┘
               │
               ▼
┌───────────────────────────┐
│ dedup.upsert_pending      │   SQLite store at data/store.sqlite.
│   → detections table      │   Columns: lat, lng, severity, score,
└──────────────┬────────────┘   crop_path, crop_phash, submit_status...
               │
               ▼  (manual gate)
┌───────────────────────────┐
│ submit.submit_via_*       │   Two paths:
│   ⚠ requires --live +     │     - submit_via_feature_service (default)
│     --confirm-token=WYSLIJ│     - submit_via_openrosa (legacy)
└───────────────────────────┘   Photo: addAttachment as a second POST.
                                EXIF stripped, JPEG re-encoded.
```

---

## Safety gates in the CLI

1. `walk` — never POSTs. Pure detect + enqueue.
2. `mock` — renders proposed submissions to JSONL for review. Never POSTs.
3. `submit --live --confirm-token=WYSLIJ` — both flags required. Limit
   defaults to 1 per invocation. Jitter 60–300 s between submissions.
4. The wire-format check we ran once landed an objectid=15232 at
   Vistula river coords; we then `updateFeatures`-marked it closed
   with a Polish note in `uwagi_sm`. Lesson recorded in
   `LEARNED.md` and in the memory store under `feedback-civic-submissions`.

---

## Repo layout

```
clean-krakow/
├── docs/
│   ├── FINDINGS.md          ← what we found
│   ├── PROCESS.md           ← this file
│   ├── LEARNED.md           ← non-obvious gotchas
│   ├── SAM3_ATTEMPTS.md     ← all the failed paths to SAM3
│   └── specs/2026-05-27-krakow-graffiti-reporter-design.md
├── data/
│   ├── form/extracted/      ← unpacked Survey123 form definition
│   ├── store.sqlite         ← queue + dedup state (gitignored)
│   ├── images/              ← Mapillary cache (gitignored)
│   ├── images/crops/        ← detection crops (gitignored)
│   ├── runs/<ts>/           ← per-run JSONL + browser screenshots
│   ├── samples/             ← visual A/Bs, tune sweeps
│   ├── queue_gallery.html   ← local overlay viewer
│   └── queue_viz/           ← overlay JPEGs per source image
├── src/krakow_clean/
│   ├── cli.py               ← typer entry points
│   ├── config.py            ← .env loader
│   ├── routes.py            ← Krakow corridor waypoints
│   ├── walker.py            ← Mapillary Graph API + downloads
│   ├── vision.py            ← GroundingDINO + filters + CLIP
│   ├── vision_sam3.py       ← SAM3 sidecar + CLIP (current default)
│   ├── enrichment.py        ← Krakow GIS spatial queries
│   ├── formspec.py          ← XForm builder (OpenRosa fallback)
│   ├── submit.py            ← addFeatures + addAttachment
│   ├── dedup.py             ← SQLite + pHash dedup
│   ├── pipeline.py          ← walk + detect + enqueue (DINO path)
│   └── browser_fill.py      ← Playwright dry-fill demo
├── scripts/
│   ├── install_sam3.sh           ← clone + install Meta SAM3 (vendored)
│   ├── sample_detect.py          ← detection demo on 8 sample images
│   ├── tune_detector.py          ← 5 prompts × 4 thresholds sweep
│   ├── visualize_queue.py        ← per-image overlays + gallery
│   ├── redetect_all_cached.py    ← re-detect on cached set (DINO)
│   ├── redetect_sam3_clip.py     ← re-detect with SAM3+CLIP
│   ├── walk_full_sam3.py         ← full-corridor SAM3+CLIP walk
│   ├── sam3_ab.py                ← SAM3 vs DINO A/B harness
│   └── export_sheet.py           ← form-ready list → Google Sheet
├── vendor/
│   ├── sam3/        (gitignored, Meta SAM3 cloned + patched)
│   └── mlx_sam3/    (gitignored, Apple MLX SAM3 + sidecar.py)
└── index.html       ← long-read landing page
```
