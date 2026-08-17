"""Texty pre používateľa, ktoré sa skladajú z čísel.

Formátovanie hlášok je **UI záležitosť, nie doménová** — potrebuje preklad
a doména nemá ako vedieť, v akom jazyku appka práve beží. Preto tu, nie
v `domain/`.

Všetky texty idú cez `QCoreApplication.translate()`, aby sa dali vyextrahovať
do `.ts` súboru. Viď `ui/translations/README.md`.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QCoreApplication

from pssim.cad.model import CadAssembly
from pssim.domain.machine import Transform
from pssim.domain.placement import from_transform, is_identity

#: Kontext pre `lupdate`. Musí byť konštantný, inak sa preklady rozsypú.
CONTEXT: Final = "labels"


def _tr(text: str) -> str:
    return QCoreApplication.translate(CONTEXT, text)


def describe_placement(transform: Transform) -> str:
    """Jednoveta o umiestnení modelu pre stavový riadok.

    Uvádza jednotky, ktoré používateľ zadával (mm, stupne), nie tie interné —
    inak by po zadaní „100 mm" videl „0.1" a hľadal by, kde sa to stratilo.
    """
    if is_identity(transform):
        return _tr("Model at origin, no rotation")

    display = from_transform(transform)
    # Zástupné znaky, nie zlepovanie viet — v inom jazyku môže byť poradie iné.
    return _tr("Moved {0}, {1}, {2} mm; rotated {3}, {4}, {5}°").format(
        f"{display.x_mm:g}",
        f"{display.y_mm:g}",
        f"{display.z_mm:g}",
        f"{display.rotate_x_deg:g}",
        f"{display.rotate_y_deg:g}",
        f"{display.rotate_z_deg:g}",
    )


def describe_assembly(assembly: CadAssembly | None) -> str:
    """Jednoveta o naimportovanom modeli pre stavový riadok."""
    if assembly is None:
        return _tr("Model loaded")
    return _tr("{0} parts, {1} triangles").format(len(assembly.nodes), assembly.triangle_count)


def missing_geometry_suffix(missing: int) -> str:
    """Doplnok hlášky, keď časti modelu chýba geometria v cache."""
    return _tr(" — geometry missing for {0} part(s)").format(missing)
