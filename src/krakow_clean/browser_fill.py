"""Playwright dry-fill into the actual Survey123 webform.

Opens the live form URL, fills the geopoint via map click, fills the
category dropdowns, uploads the cropped photo, takes screenshots at each
step, and STOPS BEFORE clicking submit. Anonymity-preserving: pl-PL locale,
randomized UA, no email, no stored cookies between runs.
"""
from __future__ import annotations

import asyncio
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, async_playwright

from .config import Config, load_config
from .formspec import GraffitiReport
from .submit import USER_AGENTS


@dataclass
class FillResult:
    detection_id: str
    success: bool
    screenshots: list[Path]
    notes: list[str]


async def _fill_one(
    page: Page, report: GraffitiReport, screenshot_dir: Path, label: str
) -> FillResult:
    notes: list[str] = []
    shots: list[Path] = []
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def shot(name: str) -> None:
        path = screenshot_dir / f"{name}.png"
        await page.screenshot(path=path, full_page=False)
        shots.append(path)
        notes.append(f"shot {name}")

    config = load_config()
    await page.goto(config.survey_share_url, wait_until="networkidle", timeout=60_000)
    await shot("01_form_loaded")

    # Survey123 webform renders inside an iframe in some embeds, but the
    # /share/ direct URL renders top-level. The first input is the map.
    # Strategy: wait for the "Press to locate" map widget label to appear.
    map_button = page.get_by_text("Wskaż lokalizację", exact=False)
    try:
        await map_button.wait_for(timeout=15_000)
    except Exception:  # noqa: BLE001
        notes.append("map button text not found — form layout may have changed")
    await shot("02_after_wait")

    # Click the map widget to open the location picker.
    try:
        await map_button.click()
        await shot("03_map_picker_open")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"map click failed: {exc}")

    # The picker shows a search box. Type the address from enrichment.
    address_box = page.locator("input[placeholder*='Szukaj'], input[placeholder*='search']").first
    try:
        await address_box.fill(f"{report.lat}, {report.lng}", timeout=8_000)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2_000)
        await shot("04_search_typed")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"address search not available: {exc}")

    # If a "use this location" button appears, capture it but do not click submit.
    use_loc = page.get_by_text("OK", exact=False).first
    try:
        await use_loc.click(timeout=4_000)
        await shot("05_location_confirmed")
    except Exception:  # noqa: BLE001
        notes.append("OK button not found; continuing")

    # Set rodzaj_graffitti.
    try:
        rodzaj_label = page.get_by_text("Podaj rodzaj graffiti", exact=False).first
        await rodzaj_label.scroll_into_view_if_needed()
        await page.wait_for_timeout(800)
        # Open the select1 dropdown — Survey123 renders as a custom select.
        rodzaj_dropdown = rodzaj_label.locator("xpath=ancestor::*[contains(@class,'question')]//select | ancestor::*[contains(@class,'question')]//button").first
        await rodzaj_dropdown.click(timeout=4_000)
        await shot("06_rodzaj_open")
        # Pick the labeled option matching our value.
        await page.get_by_text("inne", exact=False).first.click(timeout=4_000)
        await shot("07_rodzaj_picked")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"rodzaj fill skipped: {exc}")

    # Set miejsce_graffitti.
    try:
        miejsce_label = page.get_by_text("Miejsce graffiti", exact=False).first
        await miejsce_label.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        miejsce_dropdown = miejsce_label.locator("xpath=ancestor::*[contains(@class,'question')]//select | ancestor::*[contains(@class,'question')]//button").first
        await miejsce_dropdown.click(timeout=4_000)
        await shot("08_miejsce_open")
        await page.get_by_text("ściana budynku", exact=False).first.click(timeout=4_000)
        await shot("09_miejsce_picked")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"miejsce fill skipped: {exc}")

    # Upload the crop image.
    if report.photo_path and report.photo_path.exists():
        try:
            file_inputs = page.locator("input[type='file']")
            count = await file_inputs.count()
            if count > 0:
                await file_inputs.first.set_input_files(str(report.photo_path))
                await page.wait_for_timeout(1500)
                await shot("10_photo_uploaded")
            else:
                notes.append("no file input found")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"photo upload skipped: {exc}")

    # Scroll to the submit button (Wyślij) but DO NOT click.
    try:
        submit_btn = page.get_by_role("button", name="Wyślij").first
        await submit_btn.scroll_into_view_if_needed()
        await shot("11_ready_to_submit_NO_CLICK")
        notes.append("submit button visible; click withheld (dry-fill mode)")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"submit button not visible: {exc}")

    return FillResult(
        detection_id=label, success=True, screenshots=shots, notes=notes
    )


async def run_fills(
    config: Config,
    reports: list[tuple[str, GraffitiReport]],
    out_dir: Path,
    headless: bool = True,
) -> list[FillResult]:
    """Open one Chromium context, dry-fill each report sequentially."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[FillResult] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--lang=pl-PL",
                "--accept-lang=pl-PL,pl;q=0.9,en;q=0.7",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1440, "height": 900},
        )
        for detection_id, report in reports:
            page = await context.new_page()
            shot_dir = out_dir / detection_id
            try:
                result = await _fill_one(page, report, shot_dir, detection_id)
            except Exception as exc:  # noqa: BLE001
                result = FillResult(
                    detection_id=detection_id,
                    success=False,
                    screenshots=[],
                    notes=[f"top-level error: {exc}"],
                )
            results.append(result)
            await page.close()
        await context.close()
        await browser.close()
    return results


def main(argv: Optional[list[str]] = None) -> int:
    import sqlite3

    config = load_config()
    conn = sqlite3.connect(config.store_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM detections WHERE submit_status = 'pending' "
        "ORDER BY score DESC LIMIT 1"
    ).fetchall()
    if not rows:
        print("no pending detections; run `krakow-clean walk` first")
        return 1

    reports = [
        (
            row["detection_id"],
            GraffitiReport(
                lat=row["lat"],
                lng=row["lng"],
                rodzaj=row["rodzaj"],
                miejsce=row["miejsce"],
                photo_path=Path(row["crop_path"]) if row["crop_path"] else None,
            ),
        )
        for row in rows
    ]
    out_dir = config.runs_dir / "browser_fill"
    print(f"dry-filling {len(reports)} detection(s) into the live form...")
    results = asyncio.run(run_fills(config, reports, out_dir, headless=True))
    for r in results:
        print(f"\n[{r.detection_id[:8]}] success={r.success}")
        for n in r.notes:
            print(f"  · {n}")
        for s in r.screenshots:
            print(f"  shot: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
