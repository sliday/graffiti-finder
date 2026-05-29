"""Export the form-ready proposed.jsonl to a Google Sheet via gws CLI.

Usage:
    uv run python scripts/export_sheet.py [path/to/proposed.jsonl]

If no path given, picks the most recent data/runs/<ts>/proposed.jsonl.

Output: prints the new spreadsheet URL.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from krakow_clean.config import load_config

COLUMNS = [
    "detection_id",
    "captured_at",     # when the Mapillary frame was taken
    "is_monument",     # 1 = on protected building (criminal in PL), 0 = misdemeanour
    "monument_name",
    "monument_kind",
    "monument_distance_m",
    "writer_id",       # CLIP-style cluster — 1 = largest group; sort to find serial offenders
    "writer_cluster_size",
    "lat",
    "lng",
    "address",
    "dzielnica",
    "rejon_sm",
    "identyfikator_budynku",
    "identyfikator_dzialki",
    "severity",
    "score",
    "clip_prob",
    "rodzaj",
    "miejsce",
    "crop_path",
    "image_id",
    "rendered_at",     # when we ran SAM3+CLIP
]


def _gws(args: list[str], json_body: dict | None = None, params: dict | None = None) -> dict:
    cmd = ["gws", *args]
    if params is not None:
        cmd += ["--params", json.dumps(params)]
    if json_body is not None:
        cmd += ["--json", json.dumps(json_body)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gws failed: {result.stderr or result.stdout}")
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def _latest_proposed(runs_dir: Path) -> Path | None:
    candidates = sorted(runs_dir.glob("*/proposed.jsonl"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _row(entry: dict) -> list:
    enr = entry.get("enrichment", {}) or {}
    captured = entry.get("captured_at") or ""
    if captured:
        captured = captured[:10]  # YYYY-MM-DD slice
    mon = entry.get("monument") or {}
    wr = entry.get("writer") or {}
    return [
        entry.get("detection_id", ""),
        captured,
        1 if mon.get("is_monument") else 0,
        mon.get("name") or "",
        mon.get("kind") or "",
        round(mon["distance_m"], 1) if mon.get("distance_m") is not None else "",
        wr.get("writer_id") or "",
        wr.get("cluster_size") or "",
        entry.get("lat"),
        entry.get("lng"),
        enr.get("adres") or "",
        enr.get("dzielnica") or "",
        enr.get("rejon_sm") or "",
        enr.get("identyfikator_budynku") or "",
        enr.get("identyfikator_dzialki") or "",
        entry.get("severity", ""),
        round(entry.get("score", 0.0), 3),
        "",  # clip_prob not in proposed.jsonl currently; reserved
        entry.get("rodzaj", ""),
        entry.get("miejsce", ""),
        entry.get("crop_path", ""),
        entry.get("image_id", ""),
        entry.get("rendered_at", ""),
    ]


def main() -> int:
    cfg = load_config()
    if len(sys.argv) > 1:
        jsonl = Path(sys.argv[1])
    else:
        jsonl = _latest_proposed(cfg.runs_dir)
        if jsonl is None:
            print("no proposed.jsonl found; run `krakow-clean mock` first")
            return 1

    rows = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    print(f"loaded {len(rows)} entries from {jsonl}")

    title = f"krakow-clean — Karmelicka graffiti list — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    create = _gws(
        ["sheets", "spreadsheets", "create"],
        json_body={"properties": {"title": title}},
    )
    sheet_id = create["spreadsheetId"]
    sheet_url = create["spreadsheetUrl"]
    print(f"created sheet: {sheet_url}")

    values = [COLUMNS] + [_row(r) for r in rows]
    _gws(
        [
            "sheets",
            "spreadsheets",
            "values",
            "update",
            "--params",
            json.dumps({"spreadsheetId": sheet_id, "range": "A1", "valueInputOption": "RAW"}),
        ],
        json_body={"values": values},
    )
    print(f"wrote {len(values) - 1} rows (+ header)")
    print(f"\n→ {sheet_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
