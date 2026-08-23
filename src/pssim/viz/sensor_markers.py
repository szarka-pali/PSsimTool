"""Visual markers for sensors — a line for a beam, a wireframe box for a zone.

Colour is the only reaction this version has: green when clear, red when active.
No status-bar or logged readout yet — see the accompanying plan for why that is
deliberately future work, not missing.
"""

from __future__ import annotations

from typing import Any, Final

from pssim.domain.collision import AABB
from pssim.domain.sensors import RAY_KINDS, Sensor, ray_end
from pssim.viz.axes import BOX_EDGES, box_corners

Rgba = tuple[float, float, float, float]

CLEAR_COLOR: Final[Rgba] = (0.30, 0.80, 0.35, 1.0)
ACTIVE_COLOR: Final[Rgba] = (0.90, 0.25, 0.25, 1.0)

LINE_THICKNESS_PX: Final = 2.0


def sensor_color(is_active: bool) -> Rgba:
    """Green when clear, red when active — the one source of truth for the
    colour convention, shared by the 3D marker and (later) any dock cell."""
    return ACTIVE_COLOR if is_active else CLEAR_COLOR


def make_beam_marker(sensor: Sensor, is_active: bool) -> Any:
    """The ray from `origin` out to its range, coloured by whether it sees anything.

    `ray_end` is what the reading uses too, so the line that is drawn and the
    line that is tested cannot drift apart.
    """
    from panda3d.core import BitMask32, LineSegs, NodePath

    lines = LineSegs(f"sensor-beam-{sensor.name}")
    lines.setThickness(LINE_THICKNESS_PX)
    lines.setColor(*sensor_color(is_active))
    lines.moveTo(*sensor.origin)
    lines.drawTo(*ray_end(sensor))

    node = NodePath(lines.create())
    node.setName(f"sensor-beam-{sensor.name}")
    # A marker, not geometry — lighting would dim it unevenly and hide the state colour.
    node.setLightOff()
    # A marker must never be what a pick ray hits instead of the model underneath it.
    node.node().setIntoCollideMask(BitMask32.allOff())
    return node


def make_proximity_marker(sensor: Sensor, is_active: bool) -> Any:
    """A wireframe box around `origin`, reusing `viz.axes.box_corners`/`BOX_EDGES`
    — the same primitives the selection outline draws with — instead of new
    geometry."""
    from panda3d.core import BitMask32, LineSegs, NodePath

    half = sensor.half_extent_m
    ox, oy, oz = sensor.origin
    corners = box_corners((ox - half, oy - half, oz - half), (ox + half, oy + half, oz + half))

    lines = LineSegs(f"sensor-zone-{sensor.name}")
    lines.setThickness(LINE_THICKNESS_PX)
    lines.setColor(*sensor_color(is_active))
    for start, end in BOX_EDGES:
        lines.moveTo(*corners[start])
        lines.drawTo(*corners[end])

    node = NodePath(lines.create())
    node.setName(f"sensor-zone-{sensor.name}")
    node.setLightOff()
    node.node().setIntoCollideMask(BitMask32.allOff())
    return node


def make_sensor_marker(sensor: Sensor, is_active: bool) -> Any:
    """Build the right marker for `sensor.kind`.

    Every ray kind draws the same line — a photoelectric beam, an inductive
    sensor and a rangefinder all look along one. The kinds are separate because
    the *machine* distinguishes them, not because the picture does.

    An encoder gets no marker of its own: it senses no geometry, it reads the
    angle of the joint it is bolted to, and that joint already draws itself.
    """
    if sensor.kind in RAY_KINDS:
        return make_beam_marker(sensor, is_active)
    return make_proximity_marker(sensor, is_active)


def aabb_of(node_path: Any, reference: Any) -> AABB | None:
    """The bounding box of `node_path`, expressed in `reference`'s coordinates.

    `None` when the subtree has no extent — an empty model has nothing to react
    to. `reference` is normally the scene root: every sensor needs the boxes in
    one shared frame to compare against, which is why this asks for bounds
    relative to it rather than to the node's own coordinates (contrast
    `viz.axes.make_highlight_box`, which deliberately does the opposite because
    its box is reparented as a *child* of the node it outlines).
    """
    bounds = node_path.getTightBounds(reference)
    if bounds is None:
        return None
    low, high = bounds
    return AABB(low=(low[0], low[1], low[2]), high=(high[0], high[1], high[2]))
