"""Build demo/map.html — interactive Leaflet map of all detections.

Reads data/store.sqlite, emits a self-contained HTML that:
  - drops one pin per detection (OSM tiles via Leaflet)
  - shows popup with crop image, score, address, severity
  - toggles a heatmap layer (Leaflet.heat plugin)
  - lists per-address aggregation in a side panel
"""
from __future__ import annotations

import base64
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from krakow_clean.config import load_config
from krakow_clean.enrichment import enrich
import httpx


def _b64_thumb(path: Path, max_edge: int = 240) -> str | None:
    """Encode a small thumb as a data: URL so the HTML is self-contained."""
    if not path.exists():
        return None
    from PIL import Image
    import io
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main() -> None:
    cfg = load_config()
    conn = sqlite3.connect(cfg.store_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM detections WHERE submit_status = 'pending' "
        "ORDER BY image_id, detection_id"
    ).fetchall()

    if not rows:
        print("no detections; run a walk first")
        return

    # Enrich any rows whose address we haven't cached yet (rough cache by lat/lng).
    addr_cache: dict[tuple[float, float], dict[str, str | None]] = {}
    with httpx.Client(timeout=15) as c:
        for r in rows:
            key = (round(r["lat"], 5), round(r["lng"], 5))
            if key in addr_cache:
                continue
            e = enrich(c, r["lng"], r["lat"])
            addr_cache[key] = e.__dict__

    points: list[dict] = []
    by_address: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "severities": defaultdict(int), "max_score": 0, "lat": 0, "lng": 0, "image_ids": set()}
    )

    for r in rows:
        key = (round(r["lat"], 5), round(r["lng"], 5))
        enr = addr_cache.get(key, {})
        addr = enr.get("adres") or f"({r['lat']:.5f}, {r['lng']:.5f})"
        crop = Path(r["crop_path"]) if r["crop_path"] else None
        thumb = _b64_thumb(crop) if crop else None
        point = {
            "id": r["detection_id"][:8],
            "lat": r["lat"],
            "lng": r["lng"],
            "score": round(r["score"] or 0, 2),
            "severity": r["severity"] or "minor",
            "address": addr,
            "dzielnica": enr.get("dzielnica") or "",
            "rejon_sm": enr.get("rejon_sm") or "",
            "image_id": r["image_id"],
            "thumb": thumb,
        }
        points.append(point)
        agg = by_address[addr]
        agg["count"] += 1
        agg["severities"][r["severity"] or "minor"] += 1
        agg["max_score"] = max(agg["max_score"], r["score"] or 0)
        agg["lat"] = r["lat"]
        agg["lng"] = r["lng"]
        agg["image_ids"].add(r["image_id"])

    addresses = sorted(
        [
            {
                "address": addr,
                "count": v["count"],
                "max_score": round(v["max_score"], 2),
                "severities": dict(v["severities"]),
                "lat": v["lat"],
                "lng": v["lng"],
                "image_ids": sorted(v["image_ids"]),
            }
            for addr, v in by_address.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )

    html = build_html(points, addresses)
    out_path = Path("demo/map.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path}  ({len(points)} pins, {len(addresses)} unique addresses)")


# Subresource Integrity hashes pinned for the CDN assets. Without these, a
# compromise of unpkg lets an attacker swap the JS we run. Computed via
# `curl ... | openssl dgst -sha384 -binary | openssl base64 -A`.
SRI = {
    "leaflet_css": "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H",
    "leaflet_js":  "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH",
    "leaflet_heat": "sha384-mFKkGiGvT5vo1fEyGCD3hshDdKmW3wzXW/x+fWriYJArD0R3gawT6lMvLboM22c0",
}


def build_html(points: list[dict], addresses: list[dict]) -> str:
    points_json = json.dumps(points, ensure_ascii=False)
    addresses_json = json.dumps(addresses, ensure_ascii=False)
    if not points:
        center_lat, center_lng = 50.062, 19.937
    else:
        center_lat = sum(p["lat"] for p in points) / len(points)
        center_lng = sum(p["lng"] for p in points) / len(points)

    severity_colour = {"minor": "#7ad6ff", "moderate": "#ffd166", "severe": "#e63946"}
    severity_colour_json = json.dumps(severity_colour)

    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<title>clean-krakow — detection map</title>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<link rel='stylesheet'
      href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'
      integrity='{SRI["leaflet_css"]}'
      crossorigin='anonymous'>
<style>
body, html {{ margin: 0; height: 100%; font: 13px/1.4 -apple-system, system-ui, sans-serif; background:#0d0d10; color:#eee }}
#wrap {{ display: grid; grid-template-columns: 1fr 340px; height: 100vh }}
#map {{ height: 100% }}
#side {{ background: #15161b; border-left: 1px solid #23252c; overflow: auto; padding: 16px 18px }}
#side h1 {{ font-size: 16px; margin: 0 0 6px }}
#side .meta {{ color: #9aa; font-size: 12px; margin-bottom: 14px; font-family: ui-monospace, SF Mono, Menlo, monospace }}
.toggle {{ display: flex; gap: 8px; margin: 10px 0 16px }}
.toggle button {{ background: #1c1d24; color: #ccd; border: 1px solid #2a2c35; padding: 6px 10px; border-radius: 4px; font: 12px ui-monospace, monospace; cursor: pointer }}
.toggle button.on {{ background: #c9f76f; color: #0d0d10; border-color: #c9f76f }}
.addr {{ background: #1c1d24; border: 1px solid #23252c; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; cursor: pointer }}
.addr:hover {{ border-color: #7ad6ff }}
.addr h3 {{ margin: 0 0 4px; font-size: 13px; color: #fff; font-family: -apple-system, sans-serif }}
.addr .count {{ font-size: 11px; color: #c9f76f; font-family: ui-monospace, monospace; letter-spacing: .05em }}
.addr .sev {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font: 10px ui-monospace, monospace; margin-right: 4px; margin-top: 6px }}
.addr .sev.minor {{ background: #7ad6ff22; color: #7ad6ff }}
.addr .sev.moderate {{ background: #ffd16622; color: #ffd166 }}
.addr .sev.severe {{ background: #e6394622; color: #e63946 }}
.legend {{ position: absolute; bottom: 12px; left: 12px; background: rgba(13,13,16,.88); padding: 10px 12px; border-radius: 6px; font: 11px ui-monospace, monospace; z-index: 1000 }}
.legend i {{ display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; vertical-align: -2px }}
.leaflet-popup-content-wrapper {{ background: #1c1d24; color: #eee; border-radius: 6px }}
.leaflet-popup-content {{ margin: 10px 14px; font: 12px -apple-system, sans-serif }}
.leaflet-popup-content img {{ max-width: 220px; max-height: 180px; border-radius: 4px; margin: 6px 0 }}
.leaflet-popup-content .field {{ font-family: ui-monospace, monospace; color: #9aa; font-size: 11px }}
.leaflet-popup-tip {{ background: #1c1d24 }}
</style>
</head>
<body>
<div id='wrap'>
  <div style='position: relative; height: 100%'>
    <div id='map'></div>
    <div class='legend'>
      <div><i style='background:#7ad6ff'></i> minor</div>
      <div><i style='background:#ffd166'></i> moderate</div>
      <div><i style='background:#e63946'></i> severe</div>
    </div>
  </div>
  <div id='side'>
    <h1>clean-krakow — detections</h1>
    <div class='meta'>{len(points)} pins · {len(addresses)} addresses · generated {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    <div class='toggle'>
      <button id='pinBtn' class='on'>Pins</button>
      <button id='heatBtn'>Heatmap</button>
    </div>
    <div id='addresses'></div>
  </div>
</div>

<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
        integrity='{SRI["leaflet_js"]}'
        crossorigin='anonymous'></script>
<script src='https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js'
        integrity='{SRI["leaflet_heat"]}'
        crossorigin='anonymous'></script>
<script>
const points = {points_json};
const addresses = {addresses_json};
const sevColour = {severity_colour_json};

// All user-derived strings (addresses come from an external geocoder, image_ids
// from Mapillary) are inserted via textContent / DOM APIs only. No innerHTML
// from data fields. The thumb is a base64 data URL we generated locally —
// trusted source, but still set via element.src, not innerHTML.

const map = L.map('map').setView([{center_lat}, {center_lng}], 15);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '© OpenStreetMap, © CARTO', maxZoom: 19
}}).addTo(map);

function field(label, value) {{
  const el = document.createElement('span');
  el.className = 'field';
  el.textContent = `${{label}} ${{value}}`;
  return el;
}}

function popupNode(p) {{
  const root = document.createElement('div');
  const addr = document.createElement('b');
  addr.textContent = p.address;
  root.append(addr, document.createElement('br'),
              field('id:', `${{p.id}} · score: ${{p.score}} · sev: ${{p.severity}}`),
              document.createElement('br'),
              field('', `${{p.dzielnica}} · ${{p.rejon_sm}}`),
              document.createElement('br'));
  if (p.thumb) {{
    const img = document.createElement('img');
    img.alt = 'crop';
    img.src = p.thumb;
    root.appendChild(img);
  }}
  root.appendChild(field('img', p.image_id));
  return root;
}}

const pinLayer = L.layerGroup();
points.forEach(p => {{
  const marker = L.circleMarker([p.lat, p.lng], {{
    radius: 7,
    fillColor: sevColour[p.severity] || '#7ad6ff',
    color: '#0d0d10',
    weight: 2,
    fillOpacity: 0.92
  }});
  marker.bindPopup(popupNode(p));   // accepts HTMLElement; no innerHTML path.
  marker.addTo(pinLayer);
}});
pinLayer.addTo(map);

const heat = L.heatLayer(points.map(p => [p.lat, p.lng, 0.5 + p.score]), {{
  radius: 24, blur: 18, maxZoom: 17,
  gradient: {{0.2: '#7ad6ff', 0.5: '#ffd166', 0.8: '#e63946'}}
}});

document.getElementById('pinBtn').onclick = () => {{
  map.addLayer(pinLayer); map.removeLayer(heat);
  document.getElementById('pinBtn').classList.add('on');
  document.getElementById('heatBtn').classList.remove('on');
}};
document.getElementById('heatBtn').onclick = () => {{
  map.removeLayer(pinLayer); map.addLayer(heat);
  document.getElementById('heatBtn').classList.add('on');
  document.getElementById('pinBtn').classList.remove('on');
}};

function severityChip(sev, n) {{
  const span = document.createElement('span');
  span.className = `sev ${{sev}}`;        // class names from our own enum, not user data
  span.textContent = `${{sev}} ×${{n}}`;
  return span;
}}

const list = document.getElementById('addresses');
addresses.forEach(a => {{
  const el = document.createElement('div');
  el.className = 'addr';
  const h = document.createElement('h3');
  h.textContent = a.address;
  const count = document.createElement('span');
  count.className = 'count';
  count.textContent = `${{a.count}} detections · top ${{a.max_score}}`;
  el.append(h, count, document.createElement('br'));
  for (const [sev, n] of Object.entries(a.severities)) {{
    el.appendChild(severityChip(sev, n));
  }}
  el.onclick = () => map.flyTo([a.lat, a.lng], 18, {{duration: 0.6}});
  list.appendChild(el);
}});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
