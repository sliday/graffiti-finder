# clean-krakow

Autonomous graffiti detector and reporter for the Krakow Survey123 form
(`Gravvitti_v_1`, owned by WBIZK_UMK). Pipeline:

```
Mapillary corridor walk ──▶ download imagery ──▶ SAM3 graffiti detection
   ──▶ Krakow GIS enrichment (district, police region, building, parcel, address)
   ──▶ SQLite dedup queue ──▶ ArcGIS FeatureServer addFeatures + addAttachment
```

## Safety guardrails

Every live submission has municipality consequences (cleanup crews
dispatch). Defaults are **gradual / dry-run / mock first**.

- `walk` only detects and queues. It never POSTs.
- `mock` renders proposed payloads to `data/runs/<ts>/proposed.jsonl` for
  review. No POST.
- `submit` requires **both** `--live` AND `--confirm-token=WYSLIJ`. Without
  the token it stays dry-run.
- `deleteFeatures` is disabled by the city endpoint — once a record is
  submitted it stays. Plan accordingly.

## Setup

```bash
uv sync                # installs deps; pinned to Python 3.12
uv run playwright install chromium    # only if using fallback OpenRosa flow
```

`.env` (gitignored) needs:

```
MAPILLARY_APP_ID=...
MAPILLARY_ACCESS_TOKEN=MLY|...
SURVEY123_ITEM_ID=b996fb8c744f41a69c5d51729702c55b
SURVEY123_PORTAL=https://bezpiecznie.um.krakow.pl/portal
SURVEY123_SHARE_URL=...
```

## CLI

```
# 1. quick coverage check — no downloads, no detect
uv run krakow-clean probe --route karmelicka

# 2. walk a route — downloads images, runs SAM3, queues detections
uv run krakow-clean walk --route karmelicka --max-images 30

# 3. render mock payloads for review (NO POST)
uv run krakow-clean mock --limit 5

# 4. inspect queue
uv run krakow-clean status

# 5. submit — dry-run by default, even with --live unless token passes
uv run krakow-clean submit --live --confirm-token=WYSLIJ --limit 1
```

## Architecture

| Module          | Role                                                  |
|-----------------|-------------------------------------------------------|
| `config`        | Loads `.env`, holds constants                         |
| `walker`        | Mapillary Graph API, route → image refs              |
| `vision`        | facebook/sam3 (HF transformers), MPS-aware            |
| `enrichment`    | Krakow GIS spatial queries + Lokalizator geocoder    |
| `formspec`      | XForm payload builder (legacy OpenRosa path)          |
| `submit`        | FeatureServer `addFeatures` + `addAttachment`         |
| `dedup`         | SQLite queue + spatial dedup                          |
| `pipeline`      | walk → detect → enqueue                                |
| `cli`           | Typer entry points                                    |

## Known constraints

- Mapillary thumbs are face- and license-plate-blurred (privacy upstream).
- Building-polygon ID is missing when the camera point sits on the road;
  needs a wall-direction offset to populate `identyfikator_budynku`.
- City endpoint capabilities: `Query,Create,Update,Uploads,Editing` — **not
  Delete**. Test records can only be neutralised via `updateFeatures` (set
  `data_zakonczenia`, write `uwagi_sm`).
- Survey is currently open. If the city closes it (`openStatusInfo.status`),
  the FeatureServer may continue accepting writes; check `/0/addFeatures`
  capability listing before each batch.

## Spec

See `docs/specs/2026-05-27-krakow-graffiti-reporter-design.md`.
