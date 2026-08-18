"""Units and their conversions.

Inside the system **one set of units holds: metres and radians**. Conversion happens
exclusively at the boundary (`config/loader.py`, `cad/`, `io/`). This module is the
only place where conversion constants may be written down — otherwise something
somewhere gets multiplied twice.
"""

from __future__ import annotations

import math
from typing import Final

MM_TO_M: Final = 1e-3
UM_TO_M: Final = 1e-6
INCH_TO_M: Final = 0.0254
DEG_TO_RAD: Final = math.pi / 180.0

#: The length units supported in `machines/*.yaml` (the `units:` field).
LENGTH_UNITS: Final[dict[str, float]] = {
    "m": 1.0,
    "mm": MM_TO_M,
    "um": UM_TO_M,
    "in": INCH_TO_M,
}


def length_scale_to_m(unit: str) -> float:
    """Return the multiplier that converts a length in `unit` into metres.

    Raises `ValueError` for an unknown unit — the caller should translate that into
    a `ConfigError` naming the file and the field.
    """
    try:
        return LENGTH_UNITS[unit]
    except KeyError:
        known = ", ".join(sorted(LENGTH_UNITS))
        raise ValueError(f"neznáma jednotka dĺžky {unit!r}; podporované: {known}") from None


def encoder_increments_to_rad(increments_per_revolution: int) -> float:
    """Return the `scale` for converting encoder increments into radians.

    A typical servo sends its position as integer increments. At 4096 increments per
    revolution the scale is ``2*pi/4096``.
    """
    if increments_per_revolution <= 0:
        raise ValueError("increments_per_revolution must be > 0")
    return 2.0 * math.pi / increments_per_revolution
