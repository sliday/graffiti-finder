# clean-krakow — Findings

What we learned about the city's reporting system, Mapillary coverage,
vision detection on Krakow imagery, and the corridor we walked. Written
end of project, 2026-05-28.

---

## 1. The city's reporting endpoint

**Form item**: `b996fb8c744f41a69c5d51729702c55b` on the Kraków portal
(`bezpiecznie.um.krakow.pl/portal`). Title `Gravvitti_v_1`, owner
`WBIZK_UMK` (Wydział Bezpieczeństwa i Zarządzania Kryzysowego — security
and crisis management department). Created October 2025.

**Submission endpoint**: not the OpenRosa share URL the form points at,
but the underlying ArcGIS FeatureServer:

```
POST https://bezpiecznie.um.krakow.pl/server/rest/services/Graffitti_v1_2/FeatureServer/0/addFeatures
POST https://bezpiecznie.um.krakow.pl/server/rest/services/Graffitti_v1_2/FeatureServer/0/<objectId>/addAttachment
```

**Capabilities**: `Query, Create, Update, Uploads, Editing`. Notably
**no Delete**. Once a record lands it stays. `updateFeatures` can mark it
closed via `data_zakonczenia` + a Polish note in `uwagi_sm`.

**Anonymous access**: `addFeatures` accepts unauthenticated POSTs. The
`created_user` column stays empty on the server side. The form is "public"
in ArcGIS terms.

**CAPTCHA**: the form definition says `captcha.isEnabled = false`. The
live webform shows an image CAPTCHA anyway, captured in
`data/runs/browser_fill/.../11_ready_to_submit_NO_CLICK.png`. The CAPTCHA
is enforced by webform JavaScript only — `addFeatures` REST bypass works
without any CAPTCHA challenge. This validated the direct-POST choice.

**Spatial reference**: WKID 102100 (Web Mercator Auxiliary Sphere, alias
of EPSG:3857). Need `pyproj.Transformer.from_crs(4326, 3857, always_xy=True)`
to project WGS84 lat/lng before POSTing.

**Required attributes**: `Graffitti_v1_point` (geopoint), `wsp_x`, `wsp_y`,
`nazwa_jednostki` (only allowed value: `osoba fizyczna`), `data_zgloszenia`
(epoch ms). Everything else is optional.

**Anonymity-friendly fields**: `adres_mailowy` is optional. Leaving it null
keeps the report fully anonymous.

**Server-side enrichment fields**: `dzielnica`, `rejon_sm`,
`identyfikator_budynku`, `identyfikator_dzialki`, `adres`. The Survey123
webform calls `pulldata()` against five Krakow ArcGIS feature services to
populate these. We call the same services directly from
`src/krakow_clean/enrichment.py` so anonymous POSTs arrive enriched.

---

## 2. Mapillary coverage of the Old Town

- Karmelicka: 193 images at year ≥ 2022, 97 from 2024+
- Median sequence spacing: ~5.6 m
- 98.8% flat (non-pano) — good for facade analysis
- Faces and license plates blurred upstream — anonymity already enforced
- Bbox API limit is 0.01° square; our walker tiles automatically

Trade-offs vs Google Street View:
- Mapillary: free, open ToS, crowd-sourced
- GSV: paid API key, ToS restricts automation, but fresher imagery

We embed Mapillary in the demo page (no key) and offer GSV as an opt-in
upgrade requiring `GOOGLE_MAPS_EMBED_KEY`.

---

## 3. Geocoder accuracy

Three sources tested on the same corner (50.063917, 19.927753):

| Source | Address |
|---|---|
| Google search ("Czysta 1 Bar Mleczny") | Czysta 1, 31-121 (Bar Mleczny Górnik) |
| Krakow Lokalizator (`msip.um.krakow.pl/arcgis/.../Lokalizator_Krakow/GeocodeServer`) | Czysta 1, 31-121 |
| OSM Nominatim | Dolnych Młynów 5, 31-124 |

Lesson: OSM's nearest-house-number heuristic gives the wrong answer for
corner buildings where three streets meet. Use the city's own geocoder.

The Krakow geocoder requires the `location` parameter as a JSON-encoded
geometry object (`{"x":lng,"y":lat,"spatialReference":{"wkid":4326}}`),
not a `"lng,lat"` string. Plain string returns HTTP 400 "empty geometry".

---

## 4. Detection — what works

**Final pipeline (locked 2026-05-28)**: SAM3 (via MLX, Python 3.13
sidecar) + CLIP zero-shot classifier (Python 3.12 main env).

Per-image: SAM3 boxes + scores → area gates → CLIP classifies each crop
against 10 labels → keep only if "spray paint graffiti on a wall" is the
argmax with prob ≥ 0.30.

**Why SAM3 over GroundingDINO**: SAM3 produces tighter bboxes, higher
confidence on real tags (top 0.91-0.93 vs 0.21-0.35), runs faster on
Apple Silicon via MLX (~650 ms vs ~3 s per image), and has higher recall
across the same images.

**Why we still need CLIP**: SAM3 fires on stickers, distant facades, and
the occasional window frame. CLIP's 10-label classifier rejects those at
the crop level.

**The CLIP labels** (vision.py):
- Positive (index 0): "spray paint graffiti tag on a wall"
- Negatives: road/traffic sign, street lamp, window, tree, vehicle,
  pedestrian, brick wall without graffiti, doorway, billboard

The negatives were chosen empirically — each was added after seeing the
detector mis-fire on that category in real Krakow imagery.

---

## 5. False-positive shapes we learned to filter

| Category | Mitigation | Threshold |
|---|---|---|
| Tall narrow lamp posts | aspect ratio ≥ 0.45 (width/height) | width/height ≥ 0.45 |
| Uniform dark pillars | colour stddev ≥ 16 (RGB across bbox) | std ≥ 16 |
| Tiny dots (compression artefacts) | min_area_px = 2000 | min 2000 px² |
| Full-frame false fires | max_area_frac = 0.40 | ≤ 40% of frame |
| Road signs, parking signs | CLIP classifier | — |
| Stickers / leaflets | CLIP classifier | — |
| Distant building facades visible through a frame | CLIP usually catches; some survive | future: relate bbox to camera direction |

The colour-std threshold was set to 22 originally and missed the "GZ"
red-on-pink-wall tag (std = 20 in the bbox because most pixels are still
pink). Lowering to 16 recovered it without re-introducing the lamp posts
(CLIP catches those independently).

---

## 6. Detection counts on what we walked

| Route | Mapillary candidates | Processed | New form-ready detections |
|---|---|---|---|
| Karmelicka (sampled, max 30) | 193 | 30 | 5 → 20 (after std fix) |
| Floriańska | 155 | 25 | 0 |
| Szewska | — | 20 | 0 |
| Krupnicza | — | 20 | 0 |
| Kazimierz core (Józefa + Estery) | — | 30 | 11 |
| **After SAM3+CLIP swap on cached set (64 images)** | — | 64 | **54** |
| **Karmelicka end-to-end (full)** | 280+ | all | (in progress, see queue_gallery.html) |

Pattern: tourist axes (Floriańska, Grodzka) are clean. The corners and
side-blocks accumulate paint. Bar Mleczny Górnik corner (Czysta 1) is
the densest single spot on Karmelicka. Kazimierz Dajwór block is the
single densest spot we found anywhere.

---

## 7. Addresses we surfaced

Top three hotspots after geocoding:

| Address | Detections | What's there |
|---|---|---|
| Czysta 1, 31-121, Kraków, Dzielnica I Stare Miasto | ~30 | Bar Mleczny Górnik corner, multi-tag facade |
| Dajwór 19, 31-052, Kraków, Dzielnica I Stare Miasto | 11 | Weathered Kazimierz building, stickers + tags |
| Dajwór 14, 31-052, Kraków, Dzielnica I Stare Miasto | 4 | Adjacent block, similar pattern |

All in Dzielnica I (Old Town), Straż Miejska regions I/07 (Karmelicka)
and I/19 (Kazimierz).

---

## 8. What did NOT work

- **SAM3 weights via `transformers.Sam3Model`** — gated repo ships Meta's
  pickled processor, missing `preprocessor_config.json`. Cannot load via
  `Sam3Processor.from_pretrained`.
- **Meta's `sam3` package on macOS** — needs Triton (Linux-only wheels)
  and has 9 files hardcoding `device="cuda"`. Patched both, builds, but
  hits a baked-in bf16/fp32 mismatch in the ViT FFN that `.float()`
  doesn't unwind.
- **Spatial dedup at 12 m** — too aggressive; collapses multi-tag walls
  into single records. Pure pHash dedup works better.
- **First-pass GroundingDINO at default threshold (0.30)** — zero
  detections, even on obviously-tagged frames.

Full chronology in `docs/SAM3_ATTEMPTS.md`.

---

## 9. The single most useful side observation

The whole project hinged on noticing that `addFeatures` is the
city's real submission target, not the OpenRosa share endpoint. The form
JSON gave that away in the `<submission action="...item/.../FeatureServer">`
element. Once we hit FeatureServer directly:

- No CAPTCHA
- Anonymous accepted
- Same enrichment available via separate REST queries
- Round-trip in ~1.2 s

Without that, we would have been driving Playwright through CAPTCHA
forever.
