"""Orbitálna kamera — čistý model bez Panda3D.

Stav kamery je popísaný sféricky: bod, okolo ktorého sa točí (`target`),
vzdialenosť od neho a dva uhly. Vďaka tomu je orbitovanie triviálne a nikdy
sa nestratí „hore" — to je problém, ktorý má voľná kamera s kvaterniónom.

Modul je zámerne bez Panda3D, aby sa dala celá matematika ovládania otestovať
v `tests/unit/` bez okna. Panda3D časť (čítanie myši, aplikácia na kameru)
je v `viz/orbit_control.py`.

Súradnice zodpovedajú Panda3D: **Z je hore, +Y dopredu**. Pri `azimuth = 0`
je kamera na `-Y` a pozerá na `+Y` — teda čelný pohľad.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from pssim.domain.machine import Vec3

#: Elevácia sa nesmie dostať presne na pól — v zenite stráca `lookAt` referenciu
#: „hore" a obraz sa preklopí. Necháme malú rezervu.
MAX_ELEVATION_RAD: Final = math.pi / 2 - 1e-3

#: Medze priblíženia ako násobok polomeru scény. Bez nich sa dá dozoomovať
#: dovnútra dielu alebo odletieť tak ďaleko, že model zmizne v jednom pixeli.
MIN_DISTANCE_FACTOR: Final = 0.05
MAX_DISTANCE_FACTOR: Final = 50.0

DEFAULT_FOV_DEG: Final = 40.0

#: Východiskový trojštvrťový pohľad.
DEFAULT_AZIMUTH_RAD: Final = math.radians(-35.0)
DEFAULT_ELEVATION_RAD: Final = math.radians(25.0)

#: Štandardné pohľady ako `(azimut, elevácia)` v radiánoch. **Jediný zdroj pravdy** —
#: `viz/camera.py` si z nich odvodí smerový vektor, UI z nich robí položky menu.
#:
#: Pri `azimuth = 0` je kamera na `-Y` a pozerá na `+Y`, čo je čelný pohľad.
#: `top` a `bottom` používajú orezanú eleváciu, nie presne `±pi/2` — v zenite
#: stráca `lookAt` referenciu „hore" a obraz sa preklopí.
STANDARD_VIEWS: Final[dict[str, tuple[float, float]]] = {
    "iso": (DEFAULT_AZIMUTH_RAD, DEFAULT_ELEVATION_RAD),
    "front": (0.0, 0.0),
    "back": (math.pi, 0.0),
    "right": (math.pi / 2.0, 0.0),
    "left": (-math.pi / 2.0, 0.0),
    "top": (0.0, MAX_ELEVATION_RAD),
    "bottom": (0.0, -MAX_ELEVATION_RAD),
}

DEFAULT_VIEW: Final = "iso"


def standard_view(name: str) -> tuple[float, float]:
    """Uhly štandardného pohľadu. Neznámy názov je `ValueError`."""
    try:
        return STANDARD_VIEWS[name]
    except KeyError:
        known = ", ".join(STANDARD_VIEWS)
        raise ValueError(f"neznámy pohľad {name!r}; podporované: {known}") from None


class DragAction(StrEnum):
    """Čo robí ťahanie myšou."""

    NONE = "none"
    ORBIT = "orbit"
    PAN = "pan"


def drag_action(button: int, *, shift: bool = False) -> DragAction:
    """Mapovanie tlačidla myši na akciu.

    Konvencia je prevzatá z CAD nástrojov (SolidWorks, Fusion, Inventor):

    - **stredné tlačidlo** — otáčanie
    - **Shift + stredné** — posun
    - **pravé tlačidlo** — posun (skratka, aby sa dalo bez klávesnice)
    - **ľavé tlačidlo** — otáčanie (bežné vo viewer-och; výber dielu zatiaľ nie je)

    Celá konvencia je v tejto jedinej funkcii — zmena väzieb je zmena tu.
    """
    if button == 2:  # stredné
        return DragAction.PAN if shift else DragAction.ORBIT
    if button == 3:  # pravé
        return DragAction.PAN
    if button == 1:  # ľavé
        return DragAction.ORBIT
    return DragAction.NONE


@dataclass(frozen=True, slots=True)
class OrbitCamera:
    """Poloha kamery okolo bodu záujmu.

    Nemenná — každá operácia vracia nový stav. Vďaka tomu sa dá porovnávať
    „pred a po" a nedá sa omylom zmeniť stav uprostred výpočtu.
    """

    target: Vec3 = (0.0, 0.0, 0.0)
    distance_m: float = 1.0
    azimuth_rad: float = DEFAULT_AZIMUTH_RAD
    elevation_rad: float = DEFAULT_ELEVATION_RAD
    min_distance_m: float = 0.01
    max_distance_m: float = 1000.0

    def __post_init__(self) -> None:
        if self.distance_m <= 0.0:
            raise ValueError("distance_m musí byť > 0")
        if self.min_distance_m > self.max_distance_m:
            raise ValueError("min_distance_m nesmie byť väčšie ako max_distance_m")

    # -- odvodené vektory ---------------------------------------------------

    @property
    def eye(self) -> Vec3:
        """Poloha kamery v scéne."""
        horizontal = self.distance_m * math.cos(self.elevation_rad)
        return (
            self.target[0] + horizontal * math.sin(self.azimuth_rad),
            self.target[1] - horizontal * math.cos(self.azimuth_rad),
            self.target[2] + self.distance_m * math.sin(self.elevation_rad),
        )

    @property
    def forward(self) -> Vec3:
        """Jednotkový vektor od kamery k cieľu."""
        eye = self.eye
        direction = (
            self.target[0] - eye[0],
            self.target[1] - eye[1],
            self.target[2] - eye[2],
        )
        return _normalized(direction)

    @property
    def right(self) -> Vec3:
        """Jednotkový vektor doprava po obrazovke.

        Leží vždy v rovine XY — kamera sa nenakláňa nabok (žiadny roll),
        čo je pri prehliadaní strojov to, čo používateľ čaká.
        """
        return (math.cos(self.azimuth_rad), math.sin(self.azimuth_rad), 0.0)

    @property
    def up(self) -> Vec3:
        """Jednotkový vektor nahor po obrazovke."""
        return _normalized(_cross(self.right, self.forward))

    # -- operácie -----------------------------------------------------------

    def orbit(self, delta_azimuth_rad: float, delta_elevation_rad: float) -> OrbitCamera:
        """Otočí kameru okolo cieľa. Elevácia sa oreže pred pólmi."""
        return replace(
            self,
            azimuth_rad=_wrap_angle(self.azimuth_rad + delta_azimuth_rad),
            elevation_rad=_clamp(
                self.elevation_rad + delta_elevation_rad,
                -MAX_ELEVATION_RAD,
                MAX_ELEVATION_RAD,
            ),
        )

    def zoom(self, factor: float) -> OrbitCamera:
        """Priblíži (`factor < 1`) alebo oddiali (`factor > 1`).

        Násobenie, nie pripočítanie: pri odzoomovanom pohľade má krok kolieska
        posunúť kameru o veľa, pri priblíženom o málo. Sčítanie by v detaile
        preskakovalo cez celý model.
        """
        if factor <= 0.0:
            raise ValueError("factor musí byť > 0")
        return replace(
            self,
            distance_m=_clamp(self.distance_m * factor, self.min_distance_m, self.max_distance_m),
        )

    def pan(self, right_m: float, up_m: float) -> OrbitCamera:
        """Posunie cieľ (a s ním kameru) v rovine obrazovky."""
        right = self.right
        up = self.up
        return replace(
            self,
            target=(
                self.target[0] + right[0] * right_m + up[0] * up_m,
                self.target[1] + right[1] * right_m + up[1] * up_m,
                self.target[2] + right[2] * right_m + up[2] * up_m,
            ),
        )

    def pan_pixels(
        self,
        delta_x_px: float,
        delta_y_px: float,
        viewport_height_px: int,
        fov_deg: float = DEFAULT_FOV_DEG,
    ) -> OrbitCamera:
        """Posun podľa pohybu myši v pixeloch.

        Mierka závisí od vzdialenosti, takže bod pod kurzorom zostáva pod ním
        bez ohľadu na priblíženie. Bez toho by posun pri odzoomovanom pohľade
        pôsobil pomaly a pri priblíženom by preletel cez celý model.
        """
        if viewport_height_px <= 0:
            return self
        world_per_pixel = (
            2.0 * self.distance_m * math.tan(math.radians(fov_deg) / 2.0) / viewport_height_px
        )
        # Ťahanie doprava posúva model doprava, teda cieľ doľava.
        return self.pan(-delta_x_px * world_per_pixel, delta_y_px * world_per_pixel)

    def looking_at(self, target: Vec3) -> OrbitCamera:
        return replace(self, target=target)

    def with_view(self, name: str) -> OrbitCamera:
        """Prepne na štandardný pohľad.

        Cieľ **ani vzdialenosť sa nemenia** — používateľ chce zmeniť uhol
        pohľadu, nie stratiť priblíženie, ktoré si nastavil.
        """
        azimuth, elevation = standard_view(name)
        return replace(self, azimuth_rad=azimuth, elevation_rad=elevation)

    def project(self, vector: Vec3) -> tuple[float, float]:
        """Premietne svetový vektor do roviny obrazovky ako `(doprava, hore)`.

        Slúži na kreslenie orientačných ikoniek v UI: os, ktorá mieri do
        obrazovky, vyjde blízko `(0, 0)`.
        """
        right, up = self.right, self.up
        return (
            sum(a * b for a, b in zip(vector, right, strict=True)),
            sum(a * b for a, b in zip(vector, up, strict=True)),
        )

    # -- rámovanie ----------------------------------------------------------

    @classmethod
    def framing(
        cls,
        center: Vec3,
        radius_m: float,
        fov_deg: float = DEFAULT_FOV_DEG,
        margin: float = 1.3,
    ) -> OrbitCamera:
        """Kamera, ktorá má guľu `(center, radius)` celú v zábere.

        Toto je „vycentrovanie na model" — volá sa po načítaní súboru.
        """
        safe_radius = radius_m if radius_m > 0.0 else 1.0
        distance = safe_radius * margin / math.sin(math.radians(max(fov_deg, 1.0)) / 2.0)
        return cls(
            target=center,
            distance_m=distance,
            min_distance_m=safe_radius * MIN_DISTANCE_FACTOR,
            max_distance_m=safe_radius * MAX_DISTANCE_FACTOR,
        )

    def clip_planes(self) -> tuple[float, float]:
        """Near a far rovina pre aktuálnu vzdialenosť.

        Odvodené od vzdialenosti, nie fixné: default near 1.0 v Panda3D oreže
        celý 0,2 m diel, zatiaľ čo 0.001 na 20 m linke rozbije depth buffer.
        """
        near = max(self.distance_m * 0.001, 1e-4)
        far = self.distance_m * 100.0 + self.max_distance_m
        return near, far


#: Koľko sa kamera otočí na jeden pixel ťahania. Pri 0,4°/px prejde otočenie
#: o 180° zhruba cez polovicu obrazovky, čo je bežné tempo v CAD nástrojoch.
ORBIT_RAD_PER_PIXEL: Final = math.radians(0.4)

#: Násobok vzdialenosti na jeden zub kolieska.
ZOOM_STEP: Final = 1.15


def apply_drag(
    camera: OrbitCamera,
    action: DragAction,
    delta_x_px: float,
    delta_y_px: float,
    viewport_height_px: int,
    fov_deg: float = DEFAULT_FOV_DEG,
) -> OrbitCamera:
    """Premietne ťahanie myšou na nový stav kamery.

    Čistá funkcia — Panda3D časť (`viz/orbit_control.py`) len dodá čísla.
    Znamienka sú zvolené tak, aby to pôsobilo ako „chytím model a otáčam ním":
    ťah doprava otočí model doprava, ťah nadol ho nakloní k pozorovateľovi.
    """
    if action is DragAction.ORBIT:
        return camera.orbit(
            -delta_x_px * ORBIT_RAD_PER_PIXEL,
            -delta_y_px * ORBIT_RAD_PER_PIXEL,
        )
    if action is DragAction.PAN:
        return camera.pan_pixels(delta_x_px, delta_y_px, viewport_height_px, fov_deg)
    return camera


def apply_wheel(camera: OrbitCamera, steps: int) -> OrbitCamera:
    """Priblíži alebo oddiali o zadaný počet zubov kolieska.

    Kladné `steps` priblížia — to je smer, ktorý čaká väčšina používateľov
    („koliesko od seba = bližšie k modelu").
    """
    if steps == 0:
        return camera
    return camera.zoom(ZOOM_STEP ** (-steps))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _wrap_angle(angle_rad: float) -> float:
    """Zloží uhol do (-pi, pi], aby po mnohých otáčkach nerástol donekonečna."""
    return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


def _cross(first: Vec3, second: Vec3) -> Vec3:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _normalized(vector: Vec3) -> Vec3:
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    if length == 0.0:
        return (0.0, 1.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)
