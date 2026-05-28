"""Fan-out walker — spawn N walk_full_sam3 subprocesses in parallel.

Each subprocess handles one route. SAM3 model loads once per subprocess
(~30 s cold), then ~2 s per image. M-series GPU is shared, so 2-3 in
parallel is the sweet spot before GPU contention dominates.

WAL-mode SQLite (enabled in dedup.open_store) handles concurrent enqueues
from the subprocesses.

Usage:
    uv run python scripts/parallel_walk.py 3 florianska szewska grodzka ...
    uv run python scripts/parallel_walk.py 3 --preset wide-old-town
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from krakow_clean.routes import ROUTES, WIDE_OLD_TOWN_ROUTES

ROOT = Path(__file__).resolve().parents[1]


def _walk_one(route: str) -> tuple[str, int, str]:
    """Run a single walker. Returns (route, exit_code, last_lines_of_log)."""
    start = time.perf_counter()
    log_path = ROOT / "data" / "runs" / f"parallel-{route}-{int(start)}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as fp:
        proc = subprocess.run(
            ["uv", "run", "python", "scripts/walk_full_sam3.py", route],
            cwd=ROOT,
            stdout=fp,
            stderr=subprocess.STDOUT,
        )
    elapsed = time.perf_counter() - start
    tail = log_path.read_text().splitlines()[-3:]
    return route, proc.returncode, f"{elapsed:.0f}s · " + " | ".join(tail)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("concurrency", type=int, help="parallel processes (suggest 2-3 on M-series)")
    parser.add_argument("routes", nargs="*", help="route names to walk")
    parser.add_argument(
        "--preset",
        choices=["wide-old-town"],
        help="select a route set instead of listing names",
    )
    args = parser.parse_args()

    if args.preset == "wide-old-town":
        names = [name for name, _ in WIDE_OLD_TOWN_ROUTES]
    else:
        names = args.routes

    if not names:
        parser.error("supply route names or --preset")

    unknown = [n for n in names if n not in ROUTES]
    if unknown:
        parser.error(f"unknown routes: {unknown}. available: {sorted(ROUTES)}")

    print(f"[parallel-walk] {len(names)} routes, {args.concurrency} workers")
    print(f"               routes: {', '.join(names)}")
    started = time.perf_counter()

    with ProcessPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(_walk_one, route): route for route in names}
        for fut in as_completed(futures):
            route, code, tail = fut.result()
            tag = "[ok]" if code == 0 else f"[ERR {code}]"
            print(f"  {tag} {route}: {tail}")

    elapsed = time.perf_counter() - started
    print(f"\n[parallel-walk] all done in {elapsed:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
