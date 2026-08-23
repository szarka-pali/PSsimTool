"""The outline drawn around a model that is currently colliding with another.

Red, and thicker than the selection outline, so the two are distinguishable when
a model is both selected and colliding — which is the normal case while someone
is driving a joint to find out where it starts to touch.

Same builder as the selection outline (`viz.axes.make_outline_box`), so it
inherits both of that function's non-obvious properties: bounds taken in the
node's own coordinates, so the marker can be reparented onto the model and
follow it, and a cleared collide mask, so a marker never becomes what a pick ray
hits.

What counts as a collision is decided in `domain/collision.py`, which is pure and
knows nothing about Panda3D. This module only draws the answer.
"""

from __future__ import annotations

from typing import Any, Final

from pssim.domain.machine import Vec3
from pssim.viz.axes import Rgba, make_box_outline

#: Red rather than the selection's amber: a warning should not be mistaken for a
#: selection, and red over grey CAD geometry reads at a glance.
COLLISION_COLOR: Final[Rgba] = (1.0, 0.20, 0.18, 1.0)

#: Thicker than `axes.HIGHLIGHT_THICKNESS_PX`, so that when a selected model is
#: also colliding the two outlines sit on the same edges without either being
#: lost inside the other.
COLLISION_THICKNESS_PX: Final = 3.0


def make_collision_box(low: Vec3, high: Vec3) -> Any:
    """Wireframe warning box, to be attached as a child of the model it marks.

    Takes the box rather than the node, unlike `make_highlight_box`: the renderer
    already knows every model's local bounds (it measures them once, at
    `add_model`), and re-measuring here would put a ~154 ms `getTightBounds` walk
    on the frame where contact starts or stops — a visible stutter every time a
    joint is driven into or out of a collision.

    The bounds must be the model's **own** coordinates, since the marker becomes
    its child.
    """
    box = make_box_outline(low, high, COLLISION_COLOR, COLLISION_THICKNESS_PX)
    box.setName("collision-outline")
    return box
