# graffiti-finder

A small civic robot that walks a city through open street imagery, finds graffiti
on walls with a local vision model, and produces a form-ready list of locations
+ addresses + crops that a human can review before any submission to the city.

Built for Kraków against the city's Survey123 form
([`Gravvitti_v_1`](https://survey123.arcgis.com/share/b996fb8c744f41a69c5d51729702c55b?portalUrl=https://bezpiecznie.um.krakow.pl/portal),
owned by Wydział Bezpieczeństwa i Zarządzania Kryzysowego UMK), but every layer
is parameterised — point it at any city with Mapillary coverage and a public
ArcGIS / open-form endpoint.

**Live demo:** <https://krakow-graffiti.pages.dev/>
([interactive map](https://krakow-graffiti.pages.dev/map) ·
[writer clusters](https://krakow-graffiti.pages.dev/writers.html))

![status](https://img.shields.io/badge/status-working-c9f76f)
![python](https://img.shields.io/badge/python-3.12-blue)
![runs on](https://img.shields.io/badge/runs%20on-Apple%20Silicon%20%2B%20MLX-black)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## What it does, in one diagram

```
Mapillary Graph API  ──▶  download every image in bbox (tile-by-tile)
                          │
                          ▼
              SAM3 (Apple-MLX port)            ──▶  text-prompted boxes
                          │
                          ▼
              CLIP zero-shot 2nd-stage         ──▶  rejects road signs, lamps,
                          │                          windows, billboards, trees
                          ▼
              geometric gates                  ──▶  aspect ratio, area, std
                          │
                          ▼
              spatial + pHash dedup            ──▶  one row per wall
                          │
                          ▼
              Krakow GIS enrichment           ──▶  district, police region,
                          │                          parcel + building IDs,
                          │                          street + house no.
                          ▼
              OSM Overpass heritage match     ──▶  flag if on monument
                          │                          (criminal in PL, not just
                          │                          a misdemeanour)
                          ▼
              CLIP image-embedding cluster    ──▶  candidate writer_id —
                          │                          spot serial offenders
                          ▼
              SQLite queue + Google Sheet + interactive Leaflet map
                          │
                          ▼
              (optional, double-gated) anonymous POST
              to ArcGIS FeatureServer addFeatures + addAttachment
```

## What it produced (Kraków, May 2026)

| | |
|---|---|
| Tiles walked (0.009° each) | 80 |
| Mapillary images processed | 1 800+ |
| Districts covered | **12** (Stare Miasto, Krowodrza, Grzegórzki, Podgórze, Prądnik Czerwony, Nowa Huta, Zwierzyniec, Dębniki, Bieńczyce, Wzgórza Krzesławickie, Prądnik Biały, Czyżyny) |
| Form-ready detections after freshest-per-wall prune | **622** |
| Unique geocoded addresses | 184 |
| On monuments (criminal-grade) | **77** — Pałac Popielów (12), Kamienica Mennica (8), Kamienica Dawidowska (5)… |
| Candidate writers (≥3 visually-similar crops) | 33 |
| Live submissions to the city form | **0** |

The pipeline runs end-to-end on a single Apple Silicon Mac, no GPU server, no
cloud inference. The full city-wide sweep takes about an hour wall-time.

## Why "no live submissions"

Every report dispatches a real cleanup crew. We default to **dry-run + human
review** because (a) false positives have real cost to the city, (b) the
`deleteFeatures` operation is disabled on the city endpoint — once a row goes
in, it stays. The CLI requires *both* `--live` AND `--confirm-token=WYSLIJ` to
actually POST. Anything short of that is dry-run.

## Architecture by module

```
src/krakow_clean/
├── walker.py        Mapillary Graph API search + retry-on-500
├── vision.py        GroundingDINO + CLIP fallback (open weights, no auth)
├── vision_sam3.py   SAM3 + CLIP hybrid via Python 3.13 subprocess sidecar
├── enrichment.py    Kraków GIS: district, police region, parcel, geocoder
├── formspec.py      XForm payload builder for the OpenRosa fallback path
├── submit.py        FeatureServer addFeatures + addAttachment (double-gated)
├── browser_fill.py  Playwright dry-fill demonstration (never clicks Wyślij)
├── dedup.py         SQLite WAL store + pHash + spatial dedup
├── pipeline.py      Single-route walk-and-detect orchestration
├── routes.py        Named waypoint lists + city bboxes
├── config.py        .env loader, Survey123 + Mapillary endpoints
└── cli.py           Typer entry points: walk, mock, status, submit, probe

scripts/
├── walk_full_sam3.py        Full pipeline on a route (download + SAM3 + CLIP)
├── grid_walk.py             bbox tiler, walks every tile in parallel
├── parallel_walk.py         ProcessPool fan-out, N workers
├── prune_stale.py           collapse a wall's older captures, keep freshest
├── prune_close.py           drop near-duplicate crops in tight buckets
├── check_monuments.py       OSM Overpass → mark on-monument detections
├── cluster_writers.py       CLIP image embeddings → agglomerative cluster
├── build_writer_gallery.py  per-cluster crop wall for visual confirmation
├── build_map.py             interactive Leaflet map with monument toggle
├── build_public.sh          Cloudflare Pages deploy directory builder
├── export_sheet.py          Google Sheet via gws CLI
└── (and ~10 more recon / visualisation utilities)
```

## How to run it yourself

### 0. Prerequisites

- macOS with Apple Silicon (M1 / M2 / M3 / M4) — MLX SAM3 needs Metal.
  CPU fallback works on Linux but is much slower.
- Python 3.12 (project) + Python 3.13 (sidecar for mlx-sam3, installs side-by-side via `uv`).
- ~12 GB free disk (model weights ~7 GB + Mapillary cache scales with tiles walked).
- A Mapillary account + access token (free, sign up at
  [mapillary.com/dashboard/developers](https://www.mapillary.com/dashboard/developers)).

### 1. Clone + install

```bash
git clone https://github.com/sliday/graffiti-finder.git
cd graffiti-finder
uv sync                                 # 3.12 env with torch / transformers / etc
cp .env.example .env                    # then fill in your Mapillary token
```

### 2. Clone vendored submodules

Two upstream models live outside this repo:

```bash
mkdir -p vendor && cd vendor
git clone --depth 1 https://github.com/facebookresearch/sam3.git
git clone --depth 1 https://github.com/Deekshith-Dade/mlx_sam3.git
cd mlx_sam3 && uv sync                  # creates the parallel Python 3.13 env
cd ../..
```

The pipeline shells out to `vendor/mlx_sam3/sidecar.py` for SAM3 inference;
the main project never has to load that runtime in-process. See
[`docs/SAM3_ATTEMPTS.md`](docs/SAM3_ATTEMPTS.md) for the full chronology of
why we ended up with this two-env arrangement.

### 3. Quick smoke test

```bash
# Mapillary coverage on a Kraków corridor, no detection
uv run krakow-clean probe --route karmelicka --min-year 2024

# Full pipeline on one route (~7 min, downloads ~150 images)
uv run python scripts/walk_full_sam3.py karmelicka

# Render the proposed list as JSONL + the local map
uv run krakow-clean mock --limit 1000
uv run python scripts/build_map.py
open demo/map.html
```

### 4. Wider sweep

```bash
# Inner Kraków, 80 tiles, 3 workers, ~30 min
uv run python scripts/grid_walk.py inner-krakow --workers 3 --tile-cap 25

# Then drop duplicate captures of the same wall (keep freshest)
uv run python scripts/prune_stale.py

# Heritage matching: 1,400+ OSM features → flag criminal-grade detections
uv run python scripts/check_monuments.py

# Writer clustering: CLIP embeddings → agglomerative cluster
uv run python scripts/cluster_writers.py --threshold 0.10

# Rebuild map + Sheet + landing page
uv run python scripts/refresh_artifacts.py
uv run python scripts/build_writer_gallery.py
```

### 5. (Optional) deploy

```bash
bash scripts/build_public.sh
wrangler pages deploy public --project-name=<your-project> --branch=main
```

## Privacy + safety

Everything runs locally. The pipeline never sends imagery to a third party for
analysis. Outbound calls and their payloads are documented in
[`docs/PRIVACY.md`](docs/PRIVACY.md). Quick summary:

| Outbound | What goes there | Why |
|---|---|---|
| Mapillary Graph API | bbox + access token | the imagery source |
| HuggingFace Hub | weight downloads (cached) | one-time SAM3 + CLIP fetch |
| Kraków GIS endpoints | lat/lng (in imagery already) | civic enrichment lookups |
| OSM Overpass | one bbox query, ~5s | heritage feature catalogue |
| Google Sheets API | the form-ready table | optional, only when you export |
| Cloudflare CDN tiles | nothing about detections | map tile rendering, browser-side |
| **City form endpoint** | the report payload + crop | **only if you authorise `--live`** |

No detection metadata, no per-user identifier, no analytics — nothing about
you or your detections leaves the laptop unless you explicitly call the submit
command with both gates set.

## Key findings (engineering notes)

If you're cloning this to build something similar, the non-obvious gotchas
that took the longest to find:

- **Krakow's geocoder** needs `location={"x":lng,"y":lat,"spatialReference":{"wkid":4326}}` —
  the `x,y` shorthand returns "empty geometry". Two hours.
- **The Survey123 form definition lies about CAPTCHA.** XForm metadata says
  `captcha.isEnabled=false`, the live webform shows an image CAPTCHA mid-fill.
  The FeatureServer REST endpoint bypasses it — that's the only programmable
  submission path.
- **`deleteFeatures` is disabled** on `Graffitti_v1_2/FeatureServer/0` even
  though `updateFeatures` works. Plan submissions as immutable; use `update`
  to retract.
- **OSM Nominatim returns wrong addresses at corner buildings.** Three streets
  meet at the Bar Mleczny corner; OSM picks the nearest house number, which
  isn't the one on the visible facade. Kraków's own Lokalizator agrees with
  Google. Use the city geocoder, not OSM, for civic-facing reports.
- **SAM3's official PyTorch path won't run on Apple Silicon** without invasive
  patching (triton + 9 files of hardcoded `cuda` + bf16 mismatch). The
  `mlx-community/sam3-image` Python 3.13 port works out of the box.
- **`replace_all` on bare numbers corrupts image filenames.** A queue-count
  bump from 471 to 622 silently rewrote `sample_02_792471472888873.jpg` to
  `sample_02_792622472888873.jpg`. Anchor every numeric replace to its
  surrounding context.

## Tech stack

- **Vision:** [SAM3](https://github.com/facebookresearch/sam3) text-prompted
  segmentation (via [Deekshith-Dade/mlx_sam3](https://github.com/Deekshith-Dade/mlx_sam3) for Apple Silicon)
  + [openai/clip-vit-base-patch32](https://huggingface.co/openai/clip-vit-base-patch32)
  second-stage classifier
- **Imagery:** [Mapillary](https://www.mapillary.com/developer/api-documentation) Graph API
- **Civic data:** Kraków GIS (`bezpiecznie.um.krakow.pl/portal`,
  `msip.um.krakow.pl/arcgis`) + [OpenStreetMap Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API)
- **Form submission (research-only):** ArcGIS Survey123 + FeatureServer REST
- **Browser dry-fill demo:** [Playwright](https://playwright.dev)
- **Map UI:** [Leaflet](https://leafletjs.com) + [leaflet.heat](https://github.com/Leaflet/Leaflet.heat),
  CARTO dark tiles
- **CLI:** [Typer](https://typer.tiangolo.com) + [Rich](https://github.com/Textualize/rich)
- **Storage:** SQLite (WAL mode for parallel walkers)
- **Hosting:** Cloudflare Pages
- **Spreadsheet export:** [`gws`](https://github.com/googleworkspace/cli) Google Workspace CLI

## License

MIT. See [LICENSE](LICENSE).

## Author

Stas Kulesh · [stas@sliday.com](mailto:stas@sliday.com) · [@sliday](https://github.com/sliday)

Built with [Claude Code](https://claude.com/claude-code) for the Kraków city
graffiti-reporting form. The full session transcript and design specs live in
[`docs/`](docs/).
