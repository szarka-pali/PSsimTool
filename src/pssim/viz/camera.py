"""Kamera a osvetlenie scény.

Oddelené od `app.py`, lebo je to jediná časť vizualizácie, ktorá musí poznať
**mierku scény**. Stroje sú v metroch a bývajú od 0,1 m (jeden diel) po 20 m
(linka), zatiaľ čo Panda3D má default near clip **1.0** a kameru v počiatku.
Pri malom stroji to znamená prázdne okno: celý model je vnútri near roviny.

Výpočet vzdialenosti kamery je čistá funkcia, aby sa dal otestovať bez okna.
"""

from __future__ import annotations

import math
from typing import Any, Final

from pssim.observability import get_logger

logger = get_logger(__name__)

DEFAULT_FOV_DEG: Final = 40.0

#: Koľkonásobok polomeru scény necháme okolo modelu. 1.0 = model presne vyplní
#: záber; viac dá odstup, aby bolo vidieť aj pohyb osí mimo kľudovej polohy.
FRAMING_MARGIN: Final = 1.6

#: Náhradný polomer, keď je scéna prázdna alebo bez hraníc (chýbajúca geometria).
FALLBACK_RADIUS_M: Final = 1.0


def frame_distance(radius_m: float, fov_deg: float = DEFAULT_FOV_DEG) -> float:
    """Vzdialenosť kamery, pri ktorej guľa daného polomeru vyplní záber.

    Čistá funkcia — testuje sa bez Panda3D.
    """
    if radius_m <= 0.0:
        radius_m = FALLBACK_RADIUS_M
    half_fov = math.radians(max(fov_deg, 1.0)) / 2.0
    return radius_m * FRAMING_MARGIN / math.sin(half_fov)


def clip_planes(radius_m: float) -> tuple[float, float]:
    """Near a far rovina odvodené od veľkosti scény.

    Fixné hodnoty tu nefungujú: default near 1.0 oreže celý 0,2 m diel,
    zatiaľ čo near 0.001 na 20 m linke rozbije presnosť depth bufferu.
    """
    if radius_m <= 0.0:
        radius_m = FALLBACK_RADIUS_M
    return (radius_m * 0.01, radius_m * 100.0)


def scene_radius(root: Any) -> tuple[Any, float]:
    """Stred a polomer scény. Prázdna scéna dá počiatok a náhradný polomer."""
    from panda3d.core import LPoint3

    bounds = root.getBounds()
    if bounds.isEmpty() or bounds.getRadius() <= 0.0:
        logger.warning("scéna nemá rozmery — chýba geometria?")
        return LPoint3(0.0, 0.0, 0.0), FALLBACK_RADIUS_M
    return bounds.getCenter(), float(bounds.getRadius())


#: Smery pohľadu. Kľúč `iso` je default — trojštvrťový záber zhora.
#: Vektory sa normalizujú, dôležitý je len ich pomer.
VIEW_DIRECTIONS: Final[dict[str, tuple[float, float, float]]] = {
    "iso": (0.65, -0.75, 0.45),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, -0.001, 1.0),  # nie čisto +Z: lookAt potrebuje smer mimo osi up
}

DEFAULT_VIEW: Final = "iso"


def view_direction(view: str) -> tuple[float, float, float]:
    """Smer pohľadu podľa názvu. Neznámy názov je `ValueError`."""
    try:
        return VIEW_DIRECTIONS[view]
    except KeyError:
        known = ", ".join(sorted(VIEW_DIRECTIONS))
        raise ValueError(f"neznámy pohľad {view!r}; podporované: {known}") from None


def setup_camera(
    base: Any,
    root: Any,
    fov_deg: float = DEFAULT_FOV_DEG,
    view: str = DEFAULT_VIEW,
) -> None:
    """Nastaví kameru tak, aby bol stroj celý v zábere.

    Kamera sa nastavuje **priamo** a trackball sa k nej až potom dosynchronizuje.
    Opačné poradie (nastaviť len trackball) funguje v okne, ale nie pri renderi
    do súboru: trackball prenáša transformáciu do kamery cez data graph, ktorý
    `graphicsEngine.renderFrame()` sám o sebe nepretraverzuje, takže kamera
    zostane v počiatku a obrázok vyjde prázdny.
    """
    from panda3d.core import LPoint3, LVector3

    center, radius = scene_radius(root)
    near, far = clip_planes(radius)

    lens = base.camLens
    lens.setFov(fov_deg)
    lens.setNearFar(near, far)

    direction = LVector3(*view_direction(view))
    direction.normalize()
    eye = LPoint3(center) + direction * frame_distance(radius, fov_deg)
    base.camera.setPos(eye)
    base.camera.lookAt(LPoint3(center))

    _sync_trackball(base, center)

    logger.info(
        "kamera nastavená",
        radius_m=round(radius, 3),
        near_m=round(near, 4),
        far_m=round(far, 1),
        eye=(round(eye[0], 3), round(eye[1], 3), round(eye[2], 3)),
    )


def _sync_trackball(base: Any, center: Any) -> None:
    """Nastaví trackball tak, aby myš pokračovala z aktuálneho pohľadu kamery.

    Bez tohto by prvý snímok v okne prepísal kameru trackballom a záber
    by sa vrátil do počiatku. Trackball drží **inverznú** transformáciu kamery.
    """
    from panda3d.core import LMatrix4, LPoint3

    trackball = getattr(base, "trackball", None)
    if trackball is None:  # offscreen render nemá myš, a teda ani trackball
        return

    inverse = LMatrix4()
    inverse.invertAffineFrom(base.camera.getMat())
    trackball.node().setOrigin(LPoint3(center))
    trackball.node().setMat(inverse)


def setup_lights(target: Any) -> None:
    """Základné osvetlenie pripojené na daný podstrom.

    Bez svetiel Panda3D renderuje plochú neosvetlenú farbu — model je vidieť,
    ale ako siluetu bez tvaru. Dve smerové svetlá z rôznych strán plus ambient
    stačia na to, aby boli hrany dielov čitateľné.

    Svetlá visia na **koreni stroja**, nie na `render`. Vďaka tomu zmiznú spolu
    so scénou; keby viseli na `render`, pri opakovanom renderi v jednom procese
    by sa hromadili a obraz by postupne vybielil.
    """
    from panda3d.core import AmbientLight, DirectionalLight

    ambient = AmbientLight("ambient")
    ambient.setColor((0.35, 0.35, 0.38, 1.0))
    target.setLight(target.attachNewNode(ambient))

    key = DirectionalLight("key")
    key.setColor((0.8, 0.8, 0.78, 1.0))
    key_path = target.attachNewNode(key)
    key_path.setHpr(-30.0, -50.0, 0.0)
    target.setLight(key_path)

    fill = DirectionalLight("fill")
    fill.setColor((0.3, 0.32, 0.35, 1.0))
    fill_path = target.attachNewNode(fill)
    fill_path.setHpr(140.0, -20.0, 0.0)
    target.setLight(fill_path)
