"""Typer CLI for the krakow-clean reporter."""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .config import Config, load_config
from .dedup import counts, list_pending, mark_submitted, open_store
from .formspec import GraffitiReport
from .pipeline import run_id, walk_and_detect
from .routes import ROUTES
from .submit import jitter_sleep, submit_report
from .walker import Waypoint

app = typer.Typer(add_completion=False, help="Krakow graffiti auto-reporter")
console = Console()


def _resolve_route(route: str) -> list[Waypoint]:
    if route in ROUTES:
        return ROUTES[route]
    raise typer.BadParameter(f"unknown route '{route}'. options: {list(ROUTES)}")


@app.command()
def walk(
    route: str = typer.Option("karmelicka", help="route name from routes.py"),
    max_images: int = typer.Option(50, help="cap Mapillary images this run"),
    min_year: int = typer.Option(2023, help="ignore images older than this"),
) -> None:
    """Walk a route via Mapillary + run SAM3 detector, enqueue findings."""
    config = load_config()
    waypoints = _resolve_route(route)
    detections = walk_and_detect(
        config,
        waypoints,
        max_images=max_images,
        min_year=min_year,
    )
    console.print(f"\n[bold green]done[/]: {len(detections)} new detections enqueued")
    status()


@app.command()
def mock(
    limit: int = typer.Option(20, help="max payloads to render"),
) -> None:
    """Render proposed submissions to data/runs/<ts>/proposed.jsonl. No POST.

    Default behaviour for autonomous mode. Lets the user review the exact
    payload (lat/lng, attributes, enrichment, crop path) before any live POST.
    """
    config = load_config()
    conn = open_store(config.store_path)
    pending = list_pending(conn, limit=limit)
    if not pending:
        console.print("[yellow]no pending detections — run `walk` first[/]")
        return

    from datetime import datetime, timezone
    from .enrichment import enrich
    from .submit import _TO_MERCATOR  # type: ignore[attr-defined]

    out_dir = config.runs_dir / run_id()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "proposed.jsonl"

    import json as _json
    with httpx.Client(timeout=30) as client, out_path.open("w") as fp:
        for row in pending:
            enrichment = enrich(client, row["lng"], row["lat"])
            x, y = _TO_MERCATOR.transform(row["lng"], row["lat"])
            cols = set(row.keys())
            monument = None
            if "is_monument" in cols and row["is_monument"]:
                monument = {
                    "is_monument": True,
                    "name": row["monument_name"],
                    "kind": row["monument_kind"],
                    "distance_m": row["monument_distance_m"],
                    "osm_id": row["monument_osm_id"],
                }
            record = {
                "detection_id": row["detection_id"],
                "image_id": row["image_id"],
                "captured_at": row["captured_at"],
                "lat": row["lat"],
                "lng": row["lng"],
                "mercator_x": x,
                "mercator_y": y,
                "score": row["score"],
                "severity": row["severity"],
                "rodzaj": row["rodzaj"],
                "miejsce": row["miejsce"],
                "crop_path": row["crop_path"],
                "enrichment": enrichment.__dict__,
                "monument": monument,
                "rendered_at": datetime.now(timezone.utc).isoformat(),
            }
            fp.write(_json.dumps(record, ensure_ascii=False) + "\n")
            mon_tag = f" [red]⚠ {monument['name']}[/]" if monument else ""
            console.print(
                f"[blue]mock[/] {row['detection_id'][:8]} "
                f"({row['lat']:.5f},{row['lng']:.5f}) "
                f"adr={enrichment.adres!s}{mon_tag}"
            )
    console.print(f"\n[bold]wrote[/] {out_path}")


@app.command()
def submit(
    live: bool = typer.Option(
        False,
        "--live",
        help="REQUIRED to actually POST. Without this flag, runs as dry-run.",
    ),
    limit: int = typer.Option(1, help="max submissions this run (cap small!)"),
    min_delay: float = typer.Option(60.0, help="min seconds between submits"),
    max_delay: float = typer.Option(300.0, help="max seconds between submits"),
    confirm_token: str = typer.Option(
        "",
        help="must equal 'WYSLIJ' to authorise live submission",
    ),
) -> None:
    """Submit pending detections to the city Survey123 endpoint.

    Safety: by default this is a dry-run. To actually POST you must pass
    BOTH `--live` AND `--confirm-token=WYSLIJ`. This double-gate is to prevent
    accidental civic noise.
    """
    config = load_config()
    conn = open_store(config.store_path)
    pending = list_pending(conn, limit=limit)
    if not pending:
        console.print("[yellow]no pending detections[/]")
        return

    is_live = live and confirm_token == "WYSLIJ"
    if live and not is_live:
        console.print(
            "[red]--live requires --confirm-token=WYSLIJ. Aborting.[/]"
        )
        raise typer.Exit(code=2)

    log_dir = config.runs_dir / run_id()
    with httpx.Client(timeout=60) as client:
        for idx, row in enumerate(pending):
            report = GraffitiReport(
                lat=row["lat"],
                lng=row["lng"],
                rodzaj=row["rodzaj"],
                miejsce=row["miejsce"],
                photo_path=Path(row["crop_path"]) if row["crop_path"] else None,
            )
            console.print(
                f"[bold]→ {'LIVE' if is_live else 'dry'} submit[/] "
                f"{row['detection_id'][:8]} "
                f"({row['lat']:.5f},{row['lng']:.5f}) score={row['score']:.2f}"
            )
            if not is_live:
                console.print("[grey](dry-run; no POST — use `mock` for full payload)[/]")
                continue
            result = submit_report(config, report, client=client, log_dir=log_dir)
            status_label = "submitted" if result.status_code in (200, 201) else "failed"
            mark_submitted(
                conn,
                row["detection_id"],
                result.instance_id,
                response_blob=f"HTTP {result.status_code} :: {result.response_text[:500]}",
                status=status_label,
            )
            console.print(
                f"   HTTP {result.status_code} obj={result.object_id} "
                f"in {result.duration_ms}ms → {status_label}"
            )
            if idx < len(pending) - 1:
                jitter_sleep(min_delay, max_delay)
    status()


@app.command()
def status() -> None:
    """Show queue counts."""
    config = load_config()
    conn = open_store(config.store_path)
    data = counts(conn)
    table = Table(title="krakow-clean queue", show_header=True, header_style="bold")
    table.add_column("status")
    table.add_column("count", justify="right")
    for k in ("pending", "submitted", "failed", "skipped"):
        table.add_row(k, str(data.get(k, 0)))
    console.print(table)


@app.command()
def probe(
    route: str = typer.Option("karmelicka"),
    min_year: int = typer.Option(2023),
) -> None:
    """Mapillary recon only: count images on a route, no downloads, no detect."""
    config = load_config()
    waypoints = _resolve_route(route)
    from .walker import search_corridor

    refs = search_corridor(config, waypoints, min_year=min_year)
    console.print(f"[bold]{route}[/]: {len(refs)} images (year >= {min_year})")
    for ref in refs[:5]:
        console.print(
            f"  {ref.id}  {ref.captured_at.date()}  "
            f"({ref.lat:.5f},{ref.lng:.5f})  pano={ref.is_pano}"
        )


@app.command(name="form-sync")
def form_sync() -> None:
    """Refresh the cached Survey123 form definition."""
    config = load_config()
    url = (
        f"{config.survey_portal}/sharing/rest/content/items/"
        f"{config.survey_item_id}/data"
    )
    target = config.form_dir.parent.parent / "Gravvitti_v_1.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60) as client:
        response = client.get(url)
        response.raise_for_status()
        target.write_bytes(response.content)
    console.print(f"saved {target} ({len(response.content)} bytes)")


if __name__ == "__main__":
    app()
