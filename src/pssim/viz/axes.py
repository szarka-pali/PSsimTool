"""Kartézsky kríž v počiatku súradníc.

Ukazuje, kde je nula modelu a ako je scéna natočená — to isté, čo robí zobrazenie
počiatku v SolidWorks. Bez neho sa pri otáčaní ľahko stratí prehľad, ktorý smer
je ktorý, hlavne pri symetrických dieloch.

Farby sú konvencia, ktorú používa väčšina CAD nástrojov:
**X červená, Y zelená, Z modrá.**

Rozloženie segmentov je čistá funkcia — dá sa otestovať bez Panda3D.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from pssim.domain.machine import Vec3

Rgba = tuple[float, float, float, float]

AXIS_COLORS: Final[dict[str, Rgba]] = {
    "X": (0.90, 0.25, 0.25, 1.0),
    "Y": (0.35, 0.75, 0.30, 1.0),
    "Z": (0.30, 0.50, 0.95, 1.0),
}

AXIS_DIRECTIONS: Final[dict[str, Vec3]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}

#: Kríž má byť čitateľný, ale nesmie prekričať model. Štvrtina jeho polomeru
#: je kompromis, ktorý funguje od jedného dielu po celú linku.
DEFAULT_SCALE: Final = 0.25

#: Aj pri prázdnej scéne musí byť kríž niečo vidieť.
MIN_LENGTH_M: Final = 0.01

#: Ako ďaleko za koncom osi sedí popisok, ako podiel dĺžky osi.
LABEL_OFFSET: Final = 1.12

LINE_THICKNESS_PX: Final = 2.0


@dataclass(frozen=True, slots=True)
class AxisSegment:
    """Jedna os kríža: úsečka z počiatku a popisok na jej konci."""

    name: str
    start: Vec3
    end: Vec3
    color: Rgba
    label_position: Vec3


def axis_length_for(scene_radius_m: float, scale: float = DEFAULT_SCALE) -> float:
    """Dĺžka ramena kríža pre scénu daného polomeru.

    Kríž sa škáluje s modelom, nie je fixný: na 0,2 m dieli by meter dlhá os
    zaplnila obraz, na 20 m linke by centimetrová nebola vidieť.
    """
    return max(scene_radius_m * scale, MIN_LENGTH_M)


def axis_segments(length_m: float) -> tuple[AxisSegment, ...]:
    """Tri kladné polosi z počiatku. Čistá funkcia.

    Záporné polosi sa nekreslia — SolidWorks ich tiež nekreslí a kríž by inak
    v hustej zostave pôsobil ako šesť náhodných čiar.
    """
    safe_length = max(length_m, MIN_LENGTH_M)
    segments: list[AxisSegment] = []
    for name, direction in AXIS_DIRECTIONS.items():
        end = tuple(component * safe_length for component in direction)
        label = tuple(component * safe_length * LABEL_OFFSET for component in direction)
        segments.append(
            AxisSegment(
                name=name,
                start=(0.0, 0.0, 0.0),
                end=(end[0], end[1], end[2]),
                color=AXIS_COLORS[name],
                label_position=(label[0], label[1], label[2]),
            )
        )
    return tuple(segments)


def make_axes_node(length_m: float, with_labels: bool = True) -> Any:
    """Postaví `NodePath` s krížom. Volajúci si ho pripojí, kam potrebuje."""
    from panda3d.core import LineSegs, NodePath, TextNode

    segments = axis_segments(length_m)

    lines = LineSegs("axes")
    lines.setThickness(LINE_THICKNESS_PX)
    for segment in segments:
        lines.setColor(*segment.color)
        lines.moveTo(*segment.start)
        lines.drawTo(*segment.end)

    root = NodePath(lines.create())
    root.setName("origin-axes")
    # Kríž je orientačná pomôcka, nie geometria — osvetlenie by mu menilo farbu
    # podľa natočenia a červená os by raz bola červená a inokedy hnedá.
    root.setLightOff()

    if not with_labels:
        return root

    for segment in segments:
        text = TextNode(f"axis-label-{segment.name}")
        text.setText(segment.name)
        text.setTextColor(*segment.color)
        text.setAlign(TextNode.ACenter)
        label = root.attachNewNode(text)
        label.setPos(*segment.label_position)
        label.setScale(max(length_m, MIN_LENGTH_M) * 0.25)
        # Popisok sa vždy otáča k pozorovateľovi, inak by bol z boku nečitateľný.
        label.setBillboardPointEye()
        label.setLightOff()

    return root
