"""Pre-defined Krakow street routes for the walker."""
from __future__ import annotations

from .walker import Waypoint

# Karmelicka runs roughly NNW from the Old Town toward Aleje Trzech Wieszczów.
# Waypoints sampled at intersections + along the corridor. User-provided seeds
# included.
KARMELICKA: list[Waypoint] = [
    Waypoint(50.0623200, 19.9342600, "Karmelicka @ Dunajewskiego"),
    Waypoint(50.0635222, 19.9329175, "Karmelicka — user seed 1"),
    Waypoint(50.0641000, 19.9322000, "Karmelicka @ Garbarska"),
    Waypoint(50.0648500, 19.9314500, "Karmelicka @ Loretańska"),
    Waypoint(50.0657287, 19.9305152, "Karmelicka @ Caffe Avanti (seed 2)"),
    Waypoint(50.0668000, 19.9292000, "Karmelicka @ Krupnicza"),
    Waypoint(50.0678500, 19.9279500, "Karmelicka @ Rajska"),
]

# Królewska continues from Aleje Trzech Wieszczów westward into Łobzów.
KROLEWSKA: list[Waypoint] = [
    Waypoint(50.0697000, 19.9244000, "Królewska — east end"),
    Waypoint(50.0703500, 19.9226500, "Królewska @ Lea"),
    Waypoint(50.0710000, 19.9209000, "Królewska @ Wrocławska"),
    Waypoint(50.0716500, 19.9191500, "Królewska @ Piastowska"),
    Waypoint(50.0723000, 19.9174000, "Królewska — west end"),
]

ROUTES: dict[str, list[Waypoint]] = {
    "karmelicka": KARMELICKA,
    "krolewska": KROLEWSKA,
    "all": KARMELICKA + KROLEWSKA,
}
