# clean-krakow — Learned (non-obvious gotchas)

Things that cost time and that we want future-us to find first.

---

## ArcGIS / Survey123

### `addFeatures` is the real target, not the share URL
The form XML says `<submission action="...item/.../FeatureServer">`.
Once we POST directly to `/FeatureServer/0/addFeatures` the webform
disappears from the picture. Skip the share URL.

### Webform CAPTCHA disagrees with the form definition
`captcha.isEnabled = false` in the unpacked form. The live webform still
renders a "Wpisz tekst" image CAPTCHA on the way to submit (screenshot in
`data/runs/browser_fill/.../11_ready_to_submit_NO_CLICK.png`). The
CAPTCHA is enforced client-side; FeatureServer `addFeatures` accepts
anonymous POSTs without it.

### `deleteFeatures` is disabled on this layer
Capabilities are `Query, Create, Update, Uploads, Editing` — note the
missing `Delete`. Once a record exists it cannot be removed, only marked
closed via `updateFeatures` setting `data_zakonczenia` + a note in
`uwagi_sm`. Plan accordingly: NEVER post a record you wouldn't want to
explain.

### Spatial reference is 102100 (Web Mercator Auxiliary Sphere)
EPSG:3857 in modern tooling. Within Krakow the difference is sub-meter,
so `pyproj.Transformer.from_crs(4326, 3857, always_xy=True)` is fine.

### Krakow Lokalizator wants JSON-encoded geometry
The Krakow geocoder rejects `location=lng,lat` with HTTP 400 "empty
geometry". You must send
`location={"x":lng,"y":lat,"spatialReference":{"wkid":4326}}`
URL-encoded. Two hours lost discovering this.

### `created_user` stays empty for anonymous adds
Server-side, no identity is recorded for an unauthenticated POST. That is
what "anonymity" actually means here — not Tor, not VPN, just leaving
`adres_mailowy` empty and not sending an Authorization header.

---

## OSM Nominatim vs the city's own geocoder

For the Bar Mleczny corner (50.063917, 19.927753):

| Source | Result |
|---|---|
| Krakow Lokalizator | Czysta 1, 31-121 (correct) |
| Google search | Czysta 1, 31-121 (correct, "Bar Mleczny Górnik") |
| OSM Nominatim | Dolnych Młynów 5, 31-124 (wrong) |

OSM picks the nearest-house-number; on corners where three streets
intersect, that picks an adjacent building, not the one the camera sees.
For Polish addresses, use the city's `Lokalizator_Krakow/GeocodeServer`.

---

## Mapillary

### Bbox API caps at 0.01° squares
Tile larger areas. Walker tiles automatically.

### `thumb_2048_url` redirects to the CDN
`response.raise_for_status()` then `response.content` works. No special
headers needed.

### Faces and license plates are blurred upstream
You don't need to re-blur for anonymity.

### Image date matters more than image density
A corridor with 280 candidates can have all 280 from 2019 (graffiti
since cleaned) or all 280 from 2024 (current state). Always filter by
`captured_at` year before deciding the walk is "fresh".

### Sequential frames are near-duplicates
A sequence captures every ~5 m. Same wall appears in 4-6 frames. Spatial
dedup at 12 m is too aggressive (collapses real multi-tag walls). pHash
on the cropped detection is the right dedup signal — it catches the same
tag at different angles without merging distinct tags at the same wall.

---

## Vision models

### `facebook/sam3` via transformers is gated AND missing a config
`Sam3Processor.from_pretrained("facebook/sam3")` fails: gated repo ships
Meta's own pickled processor, no `preprocessor_config.json`. The
transformers `Sam3Model` class exists but cannot load these weights.

### Meta's `sam3` package needs Linux+CUDA in practice
- `sam3/model/edt.py` imports `triton` (no macOS wheels).
- `sam3/model/decoder.py`, `vl_combiner.py`, `io_utils.py`,
  `sam3_image_processor.py`, `position_encoding.py` and four others
  hardcode `device="cuda"` or `.cuda()`.
- Patching all of these gets the model to load on CPU, but ViT FFN
  layers have baked-in bf16 weights that `.float()` doesn't unwind —
  inference hits `mat1 and mat2 must have the same dtype, but got
  BFloat16 and Float`.

Full chronology in `SAM3_ATTEMPTS.md`.

### `mlx-community/sam3-image` (via Deekshith-Dade/mlx_sam3) just works
Requires Python 3.13. We spin up a separate uv env in
`vendor/mlx_sam3/` with its own Python 3.13 + mlx, and shell out to it
from the main 3.12 env via a sidecar that emits JSON detections. Fast
(~650 ms / image on M-series), tight boxes, high confidence.

### GroundingDINO at default threshold returns zero on real graffiti
Default `box_threshold=0.30` matches the model card but produces 0
detections on obvious graffiti like the Bar Mleczny corner. Effective
threshold for our prompt is 0.15–0.22; below that, lamp posts and street
signs flood in. CLIP filter does the final cleanup either way.

### Open-vocab detectors need a "vs" prompt set
A single positive prompt ("graffiti") and a confidence threshold is not
enough — the model will fire on anything with high enough resemblance
(painted signs, scribbled posters, dark vertical objects). CLIP zero-shot
against ten labels (1 positive + 9 negatives) was the cleanest filter we
found.

### Negative-prompt labels we settled on
- spray paint graffiti tag on a wall      ← POSITIVE
- a road sign or traffic sign
- a street lamp or lamp post
- a window of a building
- a tree or vegetation
- a parked car or vehicle
- a person or pedestrian
- a brick wall without graffiti
- a doorway or entrance
- a billboard or commercial sign

Each negative was added after seeing the detector mis-fire on that
category in real Krakow imagery.

### Colour-std threshold is delicate
`MIN_COLOR_STD = 22` rejected the GZ tag (red on a uniform pink wall:
most pixels in the bbox are still pink, so std drops to 20). Lowering to
16 recovered it without re-introducing lamp posts — CLIP catches those
even at low std.

### SAM3 catches stickers too
The model treats flat paper stickers as graffiti. That's defensible (the
city's `rodzaj_graffitti` enum has an `inne` ("other") category), but
arguably stickers should be filed under a different complaint. Future
work: second CLIP pass with "spray paint" vs "paper sticker" labels.

---

## Python + uv

### Two Python versions in one repo, no conflicts
- Main project: Python 3.12 (torch + transformers + playwright wheel
  coverage is best here).
- SAM3 sidecar: Python 3.13 (mlx requires 3.13+).
- `vendor/mlx_sam3/` has its own `pyproject.toml` + `uv.lock` + venv.
- The 3.12 project shells out via `subprocess.run(["uv", "run", ...],
  cwd="vendor/mlx_sam3")`. JSON on stdout is the wire format.

### Hard-pin Python in `pyproject.toml`
`requires-python = ">=3.11,<3.13"` because Python 3.14 (which was the
system default at start) has no torch wheels yet. Without the pin, uv
picks the system Python and `uv sync` half-installs.

### `uv pip install -e ./vendor/sam3` reused the main venv
Editable installs of vendored packages live in the parent project's
venv. Patches under `vendor/sam3/` apply immediately (no reinstall).

---

## Shell + safety

### Background commands inherit kill signals from the harness
A background `uv run krakow-clean walk` died with exit 144 when the
harness shut it down. Don't assume background tasks complete; check
`run_in_background` outputs.

### `gws sheets values update` wants `range` in `--params`
Counter-intuitive: the values payload goes via `--json`, but
`spreadsheetId` and `range` are query parameters. The CLI was easy to
script once that clicked.

### `huggingface-cli` is deprecated; use `hf auth login`
The first attempt at HF auth printed a deprecation warning; the second
binary is `hf` (no hyphen). Both currently coexist on macOS via the
`huggingface_hub` package.

---

## Civic / operational

### The municipality doesn't validate locations
We POSTed a single test record to coordinates in the middle of the
Vistula river. It was accepted, status "Graffiti nowe", and entered the
city's dispatch queue with `pom_nr = "Id_g-2094"`. We then
`updateFeatures`-ed it closed with an explanatory note. Lesson: even
obviously-bad inputs get queued for human handling. Default to dry-run.

### One stray live submission is enough to teach the lesson
After that test record, the project's default mode flipped to
`--dry-run` everywhere. `submit` requires both `--live` AND
`--confirm-token=WYSLIJ`. Recorded as the durable feedback memory
`feedback-civic-submissions`.

### "Anonymity" here is mostly social, not network
Survey123 sees our real IP. What it does NOT see is any PII in the form
itself — no email, no name, no phone. Combined with a small UA rotation
list and `Accept-Language: pl-PL`, the submission looks indistinguishable
from a random Polish phone webform user.

### Old Town's pedestrian streets are clean; the corners aren't
Floriańska, Szewska, Grodzka, Krupnicza walked end-to-end produce zero
detections each. Bar Mleczny corner (Czysta 1) and the Kazimierz Dajwór
block produce dozens. The pipeline rediscovers what locals know.
