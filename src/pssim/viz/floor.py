"""A procedural ground plane, for orientation — not modelled geometry.

Gives the scene a sense of scale and a visible "down" without needing a CAD file
for it. Drawn as a grid of lines rather than a solid quad, for the same reasons
`viz/axes.py` uses lines for the origin cross: no material/lighting work, and no
z-fighting against a model whose base happens to sit exactly at the floor height.

The layout is a pure function — it can be tested without Panda3D.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from pssim.domain.machine import Vec3

Rgba = tuple[float, float, float, float]

FLOOR_COLOR: Final[Rgba] = (0.32, 0.34, 0.38, 1.0)
LINE_THICKNESS_PX: Final = 1.0

#: Below this the grid would be too small to read as a floor rather than a
#: postage stamp under the model.
MIN_HALF_EXTENT_M: Final = 0.5

#: How many times the scene radius the grid extends to each side.
FLOOR_MARGIN: Final = 2.0

#: Target spacing between grid lines. Widened automatically for a large extent —
#: see `floor_grid_lines` — so a 20 m line does not draw thousands of segments.
GRID_SPACING_M: Final = 0.1

#: Upper bound on lines per axis, regardless of extent or requested spacing.
MAX_LINES_PER_AXIS: Final = 60


@dataclass(frozen=True, slots=True)
class FloorState:
    """The floor's configuration as `EmbeddedRenderer` holds it: metres, internal
    units. Viewport-only, like `viz.orbit.OrbitCamera` — nothing outside viz/ui
    needs it, which is why its mm conversion lives in `ui/project_controller.py`,
    not here.
    """

    visible: bool = True
    z_m: float = 0.0


@dataclass(frozen=True, slots=True)
class FloorLine:
    """One grid line, from `start` to `end`, both in the floor's own z=0 plane."""

    start: Vec3
    end: Vec3


def floor_half_extent_for(scene_radius_m: float, margin: float = FLOOR_MARGIN) -> float:
    """The floor's half-extent for a scene of the given radius.

    Mirrors `axes.axis_length_for`: the floor grows with the scene rather than
    being fixed, and never shrinks below a size that still reads as a floor.
    """
    return max(scene_radius_m * margin, MIN_HALF_EXTENT_M)


def floor_grid_lines(
    half_extent_m: float, spacing_m: float = GRID_SPACING_M
) -> tuple[FloorLine, ...]:
    """A square grid of lines centred on the origin, in the floor's own z=0 plane.

    The spacing widens (doubling) until the line count per axis is within
    `MAX_LINES_PER_AXIS`, regardless of what was requested — a genuine cap, not
    just a default, so nothing can ask for a grid with thousands of segments.
    """
    extent = max(half_extent_m, MIN_HALF_EXTENT_M)
    spacing = spacing_m
    while extent / spacing > MAX_LINES_PER_AXIS:
        spacing *= 2.0

    lines: list[FloorLine] = []
    count = round(extent / spacing)
    for index in range(-count, count + 1):
        offset = index * spacing
        lines.append(FloorLine(start=(offset, -extent, 0.0), end=(offset, extent, 0.0)))
        lines.append(FloorLine(start=(-extent, offset, 0.0), end=(extent, offset, 0.0)))
    return tuple(lines)


def make_floor_node(half_extent_m: float) -> Any:
    """Build a `NodePath` with the grid. The caller sets its height with `setZ()`
    and reparents it wherever it is needed.

    Height is deliberately not baked into the geometry: a height-only edit must
    only move the node, never rebuild the grid.
    """
    from panda3d.core import BitMask32, LineSegs, NodePath

    lines = LineSegs("floor-grid")
    lines.setThickness(LINE_THICKNESS_PX)
    lines.setColor(*FLOOR_COLOR)
    for line in floor_grid_lines(half_extent_m):
        lines.moveTo(*line.start)
        lines.drawTo(*line.end)

    node = NodePath(lines.create())
    node.setName("floor-grid")
    # The floor is a reference aid, not geometry — lighting would dim it unevenly
    # and make it look like a shaded surface rather than a flat reference plane.
    node.setLightOff()
    node.node().setIntoCollideMask(BitMask32.allOff())
    return node
