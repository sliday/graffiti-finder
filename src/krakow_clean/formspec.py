"""Survey123 XForm payload builder.

Reads the Gravvitti_v_1.xml schema, lets callers fill values, and emits the
multipart form-data body required by the OpenRosa endpoint.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Values for the rodzaj_graffitti choice list (kind of graffiti).
RODZAJ = {
    "none": "brak",
    "scribble": "bazgroly",
    "hate_speech": "mowa nienawisci",
    "mural": "mural",
    "other": "inne",
}

# Values for the miejsce_graffitti choice list (location surface).
MIEJSCE = {
    "none": "brak",
    "building_wall": "sciana budynku",
    "fence": "ogrodzenie",
    "bridge_pillar": "filar mostu",
    "trash_shelter": "wiata",
    "garage": "garaz",
    "other": "inne",
}

# These public_url_* constants live in the published XForm; we mirror them
# verbatim so the server-side calculate() expressions can run.
PUBLIC_URL_DZIEL = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/Dzielnice/FeatureServer/0"
PUBLIC_URL_SM = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/Rejony_SM/FeatureServer/0"
PUBLIC_URL_BUD = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/EG_budynki_okrojona/FeatureServer/0"
PUBLIC_URL_DZIAL = "https://bezpiecznie.um.krakow.pl/server/rest/services/Bezpieczenstwo/EG_dzialki_okrojona/FeatureServer/0"


@dataclass(frozen=True)
class GraffitiReport:
    lat: float
    lng: float
    rodzaj: str = RODZAJ["other"]
    miejsce: str = MIEJSCE["building_wall"]
    photo_path: Path | None = None
    photo_field_name: str = "Graffitti_v1_image.jpg"


def _now_iso() -> str:
    """Server expects an ISO timestamp matching the XForm dateTime type."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_xform_xml(report: GraffitiReport) -> tuple[str, str]:
    """Return (xml_payload, instance_id). instance_id is the UUID used in the
    XForm meta element; also used for client-side dedup logging."""
    instance_id = f"uuid:{uuid.uuid4()}"
    geopoint = f"{report.lat:.7f} {report.lng:.7f} 0 0"
    submitted_at = _now_iso()

    photo_ref = (
        f"<Graffitti_v1_image>{report.photo_field_name}</Graffitti_v1_image>"
        if report.photo_path is not None
        else "<Graffitti_v1_image/>"
    )

    xml = f"""<?xml version="1.0"?>
<Gravvitti_v_1 id="Graffitti_v1_2">
  <Graffitti_v1_point>{geopoint}</Graffitti_v1_point>
  <data_zgloszenia>{submitted_at}</data_zgloszenia>
  {photo_ref}
  <rodzaj_graffitti>{report.rodzaj}</rodzaj_graffitti>
  <miejsce_graffitti>{report.miejsce}</miejsce_graffitti>
  <dzielnica/>
  <rejon_sm/>
  <identyfikator_budynku/>
  <identyfikator_dzialki/>
  <wsp_x>{report.lng:.7f}</wsp_x>
  <wsp_y>{report.lat:.7f}</wsp_y>
  <data_weryfikacji/>
  <data_zakonczenia/>
  <nazwa_jednostki>osoba fizyczna</nazwa_jednostki>
  <adres_mailowy/>
  <public_url_dziel>{PUBLIC_URL_DZIEL}</public_url_dziel>
  <public_url_sm>{PUBLIC_URL_SM}</public_url_sm>
  <public_url_bud>{PUBLIC_URL_BUD}</public_url_bud>
  <public_url_dzial>{PUBLIC_URL_DZIAL}</public_url_dzial>
  <public_url_slownik/>
  <adres/>
  <generated_note_form_title/>
  <generated_note_form_submit_text/>
  <generated_note_form_footer/>
  <generated_note_prompt_submitted/>
  <generated_note_prompt_captcha/>
  <meta>
    <instanceID>{instance_id}</instanceID>
    <instanceName/>
  </meta>
</Gravvitti_v_1>
""".strip()
    return xml, instance_id
