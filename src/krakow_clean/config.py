"""Runtime configuration loaded from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Config:
    mapillary_token: str
    mapillary_app_id: str
    survey_item_id: str
    survey_portal: str
    survey_share_url: str
    submit_action: str
    feature_item_id: str
    nominatim_ua: str

    data_dir: Path
    images_dir: Path
    samples_dir: Path
    runs_dir: Path
    store_path: Path
    form_dir: Path


def load_config() -> Config:
    data = ROOT / "data"
    return Config(
        mapillary_token=os.environ["MAPILLARY_ACCESS_TOKEN"],
        mapillary_app_id=os.environ["MAPILLARY_APP_ID"],
        survey_item_id=os.environ["SURVEY123_ITEM_ID"],
        survey_portal=os.environ["SURVEY123_PORTAL"],
        survey_share_url=os.environ["SURVEY123_SHARE_URL"],
        submit_action="https://bezpiecznie.um.krakow.pl/portal/sharing/rest/content/items/fc26d76f8cfb4b478ae6132446459f28",
        feature_item_id="fc26d76f8cfb4b478ae6132446459f28",
        nominatim_ua=os.environ.get("NOMINATIM_UA", "graffiti-finder/0.1"),
        data_dir=data,
        images_dir=data / "images",
        samples_dir=data / "samples",
        runs_dir=data / "runs",
        store_path=data / "store.sqlite",
        form_dir=data / "form" / "extracted" / "esriinfo",
    )
