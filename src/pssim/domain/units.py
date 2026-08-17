"""Jednotky a ich prevody.

Vnútri systému platí **jedna sada jednotiek: metre a radiány**. Prevod sa deje
výhradne na hranici (`config/loader.py`, `cad/`, `io/`). Tento modul je jediné
miesto, kde smú byť prevodné konštanty napísané — inak sa niekde vynásobí dvakrát.
"""

from __future__ import annotations

import math
from typing import Final

MM_TO_M: Final = 1e-3
UM_TO_M: Final = 1e-6
INCH_TO_M: Final = 0.0254
DEG_TO_RAD: Final = math.pi / 180.0

#: Podporované jednotky dĺžky v `machines/*.yaml` (pole `units:`).
LENGTH_UNITS: Final[dict[str, float]] = {
    "m": 1.0,
    "mm": MM_TO_M,
    "um": UM_TO_M,
    "in": INCH_TO_M,
}


def length_scale_to_m(unit: str) -> float:
    """Vráti násobiteľ, ktorým sa dĺžka v `unit` prevedie na metre.

    Vyhadzuje `ValueError` pri neznámej jednotke — volajúci ju má preložiť
    na `ConfigError` s uvedením súboru a poľa.
    """
    try:
        return LENGTH_UNITS[unit]
    except KeyError:
        known = ", ".join(sorted(LENGTH_UNITS))
        raise ValueError(f"neznáma jednotka dĺžky {unit!r}; podporované: {known}") from None


def encoder_increments_to_rad(increments_per_revolution: int) -> float:
    """Vráti `scale` pre prevod inkrementov enkodéra na radiány.

    Typické servo posiela polohu ako celočíselné inkrementy. Pri 4096 inkrementoch
    na otáčku je scale ``2*pi/4096``.
    """
    if increments_per_revolution <= 0:
        raise ValueError("increments_per_revolution musí byť > 0")
    return 2.0 * math.pi / increments_per_revolution
