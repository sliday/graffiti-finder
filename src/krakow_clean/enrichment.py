"""Civic enrichment via Krakow ArcGIS feature services.

The Survey123 form runs `pulldata()` expressions client-side to populate the
district (`dzielnica`), municipal police region (`rejon_sm`), building ID,
parcel ID, and street address from a clicked point. We mirror that lookup so
our autonomous submissions arrive enriched (cleanup crews dispatch faster).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

DZIEL = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/Dzielnice/FeatureServer/0/query"
SM = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/Rejony_SM/FeatureServer/0/query"
BUD = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/EG_budynki_okrojona/FeatureServer/0/query"
DZIAL = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/EG_dzialki_okrojona/FeatureServer/0/query"
GEOCODER = "https://msip.um.krakow.pl/arcgis/rest/services/epl/Lokalizator_Krakow/GeocodeServer/reverseGeocode"


@dataclass(frozen=True)
class Enrichment:
    dzielnica: Optional[str]
    rejon_sm: Optional[str]
    identyfikator_budynku: Optional[str]
    identyfikator_dzialki: Optional[str]
    adres: Optional[str]


def _spatial_query(
    client: httpx.Client,
    service_url: str,
    lng: float,
    lat: float,
    out_field: str,
) -> Optional[str]:
    params = {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_field,
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        response = client.get(service_url, params=params, timeout=15)
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            return None
        attrs = features[0].get("attributes", {})
        value = attrs.get(out_field)
        return None if value in ("", None) else str(value)
    except (httpx.HTTPError, ValueError):
        return None


def _reverse_geocode(client: httpx.Client, lng: float, lat: float) -> Optional[str]:
    """Krakow's geocoder needs a JSON-encoded geometry, not a comma-separated
    pair. The plain `x,y` form returns 400 'empty geometry'."""
    import json as _json

    params = {
        "location": _json.dumps(
            {"x": lng, "y": lat, "spatialReference": {"wkid": 4326}}
        ),
        "outSR": "4326",
        "langCode": "pol",
        "f": "json",
    }
    try:
        response = client.get(GEOCODER, params=params, timeout=15)
        response.raise_for_status()
        address = response.json().get("address", {})
        return address.get("Match_addr") or address.get("Address")
    except (httpx.HTTPError, ValueError):
        return None


def enrich(client: httpx.Client, lng: float, lat: float) -> Enrichment:
    return Enrichment(
        dzielnica=_spatial_query(client, DZIEL, lng, lat, "nazwa"),
        rejon_sm=_spatial_query(client, SM, lng, lat, "nr_rejonu"),
        identyfikator_budynku=_spatial_query(client, BUD, lng, lat, "ident_iip_"),
        identyfikator_dzialki=_spatial_query(client, DZIAL, lng, lat, "ident_iip_"),
        adres=_reverse_geocode(client, lng, lat),
    )
