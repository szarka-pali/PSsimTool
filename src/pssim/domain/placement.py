"""Umiestnenie modelu v scéne — posun a natočenie voči počiatku.

Slúži na to, aby sa dal model „posadiť" tam, kam patrí: CAD súbor má počiatok
tam, kde ho konštruktér nechal, a to nemusí byť bod, voči ktorému chceš merať.

**Prevod jednotiek je tu, nie v UI.** Používateľ zadáva milimetre a stupne
(tak, ako je zvyknutý z CAD), scéna beží v metroch a radiánoch. Konverzia sa
deje raz, na jednom mieste, a má testy — šesť polí krát dva smery je dosť
príležitostí na preklep.

Modul je čistý (len stdlib), takže sa dá otestovať bez Qt aj bez Panda3D.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from pssim.domain.machine import Transform
from pssim.domain.units import DEG_TO_RAD, MM_TO_M

#: Model bez posunu a bez natočenia — teda tak, ako prišiel z CAD.
IDENTITY_PLACEMENT: Final = Transform()

#: Pod touto hodnotou považujeme umiestnenie za nulové. Zodpovedá jednej
#: tisícine milimetra a tisícine stupňa — jemnejšie ako čokoľvek, čo má
#: pri stavaní stroja zmysel zadávať.
EPSILON: Final = 1e-9


@dataclass(frozen=True, slots=True)
class PlacementDisplay:
    """Umiestnenie v jednotkách, ktoré vidí používateľ: **milimetre a stupne**.

    Zámerne oddelené od `Transform` (metre, radiány), aby sa nedali zameniť.
    Typová kontrola tak zachytí, keby niekto poslal mm tam, kde patria metre.
    """

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    rotate_x_deg: float = 0.0
    rotate_y_deg: float = 0.0
    rotate_z_deg: float = 0.0

    @property
    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (
            self.x_mm,
            self.y_mm,
            self.z_mm,
            self.rotate_x_deg,
            self.rotate_y_deg,
            self.rotate_z_deg,
        )


def to_transform(display: PlacementDisplay) -> Transform:
    """Prevedie zadané hodnoty na internú transformáciu (metre, radiány)."""
    return Transform(
        xyz=(
            display.x_mm * MM_TO_M,
            display.y_mm * MM_TO_M,
            display.z_mm * MM_TO_M,
        ),
        rpy=(
            display.rotate_x_deg * DEG_TO_RAD,
            display.rotate_y_deg * DEG_TO_RAD,
            display.rotate_z_deg * DEG_TO_RAD,
        ),
    )


def from_transform(transform: Transform) -> PlacementDisplay:
    """Prevedie internú transformáciu späť na to, čo sa ukáže v dialógu."""
    return PlacementDisplay(
        x_mm=transform.xyz[0] / MM_TO_M,
        y_mm=transform.xyz[1] / MM_TO_M,
        z_mm=transform.xyz[2] / MM_TO_M,
        rotate_x_deg=transform.rpy[0] / DEG_TO_RAD,
        rotate_y_deg=transform.rpy[1] / DEG_TO_RAD,
        rotate_z_deg=transform.rpy[2] / DEG_TO_RAD,
    )


def is_identity(transform: Transform) -> bool:
    """Či umiestnenie nič nerobí. Používa sa na to, čo hlásiť používateľovi."""
    return all(abs(value) < EPSILON for value in (*transform.xyz, *transform.rpy))


def normalize_degrees(angle_deg: float) -> float:
    """Zloží uhol do rozsahu (-180, 180].

    Bez toho by sa po opakovanom otáčaní v dialógu hromadili čísla ako 720°,
    ktoré síce fungujú, ale nikto z nich nevyčíta, ako je model natočený.
    """
    wrapped = math.fmod(angle_deg + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0
