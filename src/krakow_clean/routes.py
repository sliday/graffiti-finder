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

# Floriańska — main pedestrian axis from Rynek Główny north to Brama
# Floriańska / Barbakan. Heavy foot traffic, lots of facades.
FLORIANSKA: list[Waypoint] = [
    Waypoint(50.0620000, 19.9389000, "Floriańska @ Rynek"),
    Waypoint(50.0626500, 19.9391500, "Floriańska @ św. Tomasza"),
    Waypoint(50.0633500, 19.9394500, "Floriańska mid"),
    Waypoint(50.0640500, 19.9397500, "Floriańska @ św. Marka"),
    Waypoint(50.0647000, 19.9400000, "Floriańska @ Pijarska"),
    Waypoint(50.0653500, 19.9403000, "Floriańska @ Brama Floriańska"),
]

# Szewska — south end of Karmelicka, runs west from Rynek to Planty.
SZEWSKA: list[Waypoint] = [
    Waypoint(50.0617500, 19.9376000, "Szewska @ Rynek"),
    Waypoint(50.0620500, 19.9366500, "Szewska mid-east"),
    Waypoint(50.0623500, 19.9357000, "Szewska mid"),
    Waypoint(50.0626500, 19.9347500, "Szewska @ Jagiellońska"),
    Waypoint(50.0629500, 19.9338000, "Szewska @ Planty"),
]

# Grodzka — main south axis from Rynek to Wawel. Tourist-dense.
GRODZKA: list[Waypoint] = [
    Waypoint(50.0608000, 19.9379000, "Grodzka @ Rynek"),
    Waypoint(50.0601000, 19.9380000, "Grodzka @ Mariacki"),
    Waypoint(50.0594000, 19.9381000, "Grodzka mid"),
    Waypoint(50.0587000, 19.9382000, "Grodzka @ Dominikański"),
    Waypoint(50.0580000, 19.9383000, "Grodzka @ Senacka"),
    Waypoint(50.0573000, 19.9384000, "Grodzka @ Poselska"),
    Waypoint(50.0566000, 19.9385000, "Grodzka @ Wawel"),
]

# Krupnicza — crosses Karmelicka, runs through quieter west Old Town.
KRUPNICZA: list[Waypoint] = [
    Waypoint(50.0660000, 19.9290000, "Krupnicza @ Karmelicka"),
    Waypoint(50.0664000, 19.9280000, "Krupnicza @ Studencka"),
    Waypoint(50.0668000, 19.9270000, "Krupnicza mid"),
    Waypoint(50.0672000, 19.9260000, "Krupnicza @ Skarbowa"),
    Waypoint(50.0676000, 19.9250000, "Krupnicza @ Loretańska"),
]

# Kazimierz — Old Jewish quarter, the city's street-art / graffiti hotspot.
# These routes cover the densest tag areas: Józefa, Estery, Plac Nowy,
# Bożego Ciała.
JOZEFA: list[Waypoint] = [
    Waypoint(50.0511000, 19.9445000, "Józefa @ Plac Wolnica"),
    Waypoint(50.0512000, 19.9456000, "Józefa @ Bożego Ciała"),
    Waypoint(50.0513500, 19.9468000, "Józefa @ Jakuba"),
    Waypoint(50.0515000, 19.9480000, "Józefa @ Estery"),
    Waypoint(50.0516500, 19.9492000, "Józefa @ Izaaka"),
]

ESTERY: list[Waypoint] = [
    Waypoint(50.0512000, 19.9483000, "Estery @ Józefa"),
    Waypoint(50.0517000, 19.9484000, "Estery @ Plac Nowy"),
    Waypoint(50.0522000, 19.9485000, "Estery @ Meiselsa"),
]

KAZIMIERZ_CORE: list[Waypoint] = JOZEFA + ESTERY

ROUTES: dict[str, list[Waypoint]] = {
    "karmelicka": KARMELICKA,
    "krolewska": KROLEWSKA,
    "florianska": FLORIANSKA,
    "szewska": SZEWSKA,
    "grodzka": GRODZKA,
    "krupnicza": KRUPNICZA,
    "old-town": FLORIANSKA + SZEWSKA + GRODZKA,
    "jozefa": JOZEFA,
    "estery": ESTERY,
    "kazimierz": KAZIMIERZ_CORE,
    "all": (
        KARMELICKA + KROLEWSKA + FLORIANSKA + SZEWSKA + GRODZKA
        + KRUPNICZA + KAZIMIERZ_CORE
    ),
}
