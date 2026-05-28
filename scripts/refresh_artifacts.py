"""Rebuild every viewable artifact from the current queue.

Run after any walk to refresh:
  - data/runs/<ts>/proposed.jsonl  (form-ready payloads with enrichment)
  - data/queue_gallery.html         (per-source-image overlays)
  - demo/map.html                   (Leaflet pins + heatmap + side panel)
  - Google Sheet                    (optional; prints URL)
  - index.html #materials counts    (rewrites a few lines via template)

Usage:
    uv run python scripts/refresh_artifacts.py
    uv run python scripts/refresh_artifacts.py --no-sheet
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd)
    if proc.returncode != 0:
        print(f"  ! exit {proc.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sheet", action="store_true", help="skip Google Sheet export")
    ap.add_argument("--no-viz", action="store_true", help="skip per-image overlay regen (slow)")
    args = ap.parse_args()

    _run(["uv", "run", "krakow-clean", "mock", "--limit", "500"])
    _run(["uv", "run", "python", "scripts/build_map.py"])

    if not args.no_viz:
        _run(["uv", "run", "python", "scripts/visualize_queue.py"])

    if not args.no_sheet:
        _run(["uv", "run", "python", "scripts/export_sheet.py"])

    print("\nrefresh done. Open:")
    print(f"  {ROOT/'demo/map.html'}")
    print(f"  {ROOT/'data/queue_gallery.html'}")
    print(f"  {ROOT/'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
