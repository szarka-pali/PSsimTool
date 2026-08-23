"""Visual markers for model joints: a shaft with a small fork for an axis, a plain
line for a trajectory.

Both show the joint's *defined* geometry (fixed once the joint's own value changes —
rotating about a line leaves the line itself in place, and a trajectory's extent does
not depend on where the model currently is along it). Reused/extended from
`viz/axes.py` and `viz/sensor_markers.py` rather than introducing a new drawing style:
there is no arrow/cone primitive anywhere in `viz/`, so the fork is a couple of extra
`LineSegs` strokes, not a new geometry-authoring pipeline.
"""

from __future__ import annotations

import math
from typing import Any, Final

from pssim.domain.machine import Rgba, Vec3
from pssim.domain.model_joints import (
    ModelJoint,
    ModelJointKind,
    direction_of,
    perpendicular_to,
)

#: Distinct from the axis-cross colours (X/Y/Z) and the sensor colours (green/red),
#: so a joint marker is never mistaken for either.
JOINT_MARKER_COLOR: Final[Rgba] = (0.55, 0.55, 0.95, 1.0)

#: A joint defined between two points a hair apart still needs a readable label.
MIN_SPAN_M: Final = 0.05

#: How long an axis marker is when nobody says. An axis has no length of its own,
#: so the renderer normally passes one derived from the scene; this is only the
#: fallback for a caller that has no scene to ask.
DEFAULT_AXIS_LENGTH_M: Final = 0.2

LINE_THICKNESS_PX: Final = 2.0

#: How far back from the tip the fork's wings sit, as a fraction of the shaft's own
#: length — short shafts get a proportionally short fork instead of one that overruns
#: the whole line.
FORK_LENGTH_FRACTION: Final = 0.15

#: ...but never longer than this in absolute terms, so a very long axis does not grow
#: an oversized fork.
FORK_LENGTH_MAX_M: Final = 0.05

#: How far the wings spread sideways, relative to the fork's own length.
FORK_SPREAD_FRACTION: Final = 0.6


def arrow_fork_points(origin: Vec3, target: Vec3) -> tuple[Vec3, Vec3]:
    """Two points behind `target` (towards `origin`), spread sideways — a chevron
    that makes the shaft's direction visible at a glance without an arrowhead
    geometry."""
    ox, oy, oz = origin
    tx, ty, tz = target
    dx, dy, dz = tx - ox, ty - oy, tz - oz
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    direction: Vec3 = (dx / length, dy / length, dz / length)

    fork_length = min(length * FORK_LENGTH_FRACTION, FORK_LENGTH_MAX_M)
    side_x, side_y, side_z = perpendicular_to(direction)

    back_x = tx - direction[0] * fork_length
    back_y = ty - direction[1] * fork_length
    back_z = tz - direction[2] * fork_length
    spread_x = side_x * fork_length * FORK_SPREAD_FRACTION
    spread_y = side_y * fork_length * FORK_SPREAD_FRACTION
    spread_z = side_z * fork_length * FORK_SPREAD_FRACTION

    left: Vec3 = (back_x + spread_x, back_y + spread_y, back_z + spread_z)
    right: Vec3 = (back_x - spread_x, back_y - spread_y, back_z - spread_z)
    return left, right


def make_axis_marker(
    joint: ModelJoint, length_m: float = DEFAULT_AXIS_LENGTH_M, color: Rgba = JOINT_MARKER_COLOR
) -> Any:
    """A shaft through the centre point along the axis, with a fork at the far end.

    The **length comes from the caller**, not from the joint. An axis is a centre
    plus a direction now, and only the direction of that vector means anything —
    `(0,0,1)` and `(0,0,100)` are the same axis, so drawing to the vector's end
    would make two identical axes look wildly different. The renderer passes a
    length derived from the scene, exactly as the origin cross is sized.

    The shaft runs **both ways** from the centre, because that is what a rotation
    axis is: a line through a point, not a ray leaving it.
    """
    from panda3d.core import BitMask32, LineSegs, NodePath

    direction = direction_of(joint)
    half = max(length_m, MIN_SPAN_M) / 2.0
    ox, oy, oz = joint.origin
    start: Vec3 = (
        ox - direction[0] * half,
        oy - direction[1] * half,
        oz - direction[2] * half,
    )
    end: Vec3 = (
        ox + direction[0] * half,
        oy + direction[1] * half,
        oz + direction[2] * half,
    )
    left, right = arrow_fork_points(start, end)

    lines = LineSegs(f"joint-axis-{joint.name}")
    lines.setThickness(LINE_THICKNESS_PX)
    lines.setColor(*color)
    lines.moveTo(*start)
    lines.drawTo(*end)
    lines.moveTo(*end)
    lines.drawTo(*left)
    lines.moveTo(*end)
    lines.drawTo(*right)

    node = NodePath(lines.create())
    node.setName(f"joint-axis-{joint.name}")
    # A marker, not geometry — lighting would dim it unevenly.
    node.setLightOff()
    # A marker must never be what a pick ray hits instead of the model underneath it.
    node.node().setIntoCollideMask(BitMask32.allOff())
    return node


def make_trajectory_marker(joint: ModelJoint, color: Rgba = JOINT_MARKER_COLOR) -> Any:
    """A plain straight line from `origin` to `target` — the path's extent."""
    from panda3d.core import BitMask32, LineSegs, NodePath

    lines = LineSegs(f"joint-trajectory-{joint.name}")
    lines.setThickness(LINE_THICKNESS_PX)
    lines.setColor(*color)
    lines.moveTo(*joint.origin)
    lines.drawTo(*joint.target)

    node = NodePath(lines.create())
    node.setName(f"joint-trajectory-{joint.name}")
    node.setLightOff()
    node.node().setIntoCollideMask(BitMask32.allOff())
    return node


def make_joint_marker(
    joint: ModelJoint,
    axis_length_m: float = DEFAULT_AXIS_LENGTH_M,
    color: Rgba = JOINT_MARKER_COLOR,
) -> Any:
    """Build the right marker for `joint.kind`.

    `axis_length_m` is used by an axis only — a trajectory has a real length of
    its own, from its two points. `color` defaults to `JOINT_MARKER_COLOR`, so a
    joint with no colour of its own looks exactly as it always did.
    """
    if joint.kind is ModelJointKind.AXIS:
        return make_axis_marker(joint, axis_length_m, color)
    return make_trajectory_marker(joint, color)


#: How far the name sits above the joint's origin, as a fraction of the **text
#: height**, so the label clears the marker line it belongs to by the same
#: relative amount whatever size it is set to.
LABEL_LIFT: Final = 0.6

#: A text height of zero would be an invisible node rather than an obvious
#: mistake, so anything smaller than this is treated as this.
MIN_TEXT_HEIGHT_M: Final = 0.001


def make_joint_label(name: str, height_m: float, color: Rgba = JOINT_MARKER_COLOR) -> Any:
    """The joint's name, as a billboarded text node to hang off its base.

    `height_m` **is the height of the text**. It used to be a span that got
    multiplied by 0.09 and clamped at 50 mm first, which meant every requested
    size from 1 to 50 mm produced the same 4.5 mm of text — the setting appeared
    to do nothing. One number, one meaning.

    Sizing is not derived from the joint any more either. Per-joint sizing is
    exactly what made the labels inconsistent; the scene sets one height for all
    of them (see `viz.embed.DEFAULT_TEXT_SIZE_M`).

    `setBillboardPointEye` is what keeps it legible from every angle — the same
    call `viz.axes.make_axes_node` uses for the X/Y/Z glyphs, and without it the
    text is edge-on and invisible from half the orbit.
    """
    from panda3d.core import BitMask32, NodePath, TextNode

    text = TextNode(f"joint-label-{name}")
    text.setText(name)
    text.setTextColor(*color)
    text.setAlign(TextNode.ACenter)

    node = NodePath(text)
    node.setName(f"joint-label-{name}")
    height = max(height_m, MIN_TEXT_HEIGHT_M)
    node.setPos(0.0, 0.0, height * LABEL_LIFT)
    node.setScale(height)
    node.setBillboardPointEye()
    node.setLightOff()
    # A label must never be what a pick ray hits instead of the model under it.
    node.node().setIntoCollideMask(BitMask32.allOff())
    return node


def joint_span(joint: ModelJoint) -> float:
    """Distance from a joint's origin to its target.

    Meaningful for a **trajectory** only: an axis has no length, so a caller that
    needs a size for one has to supply it (see `make_axis_marker`).
    """
    ox, oy, oz = joint.origin
    tx, ty, tz = joint.target
    return math.sqrt((tx - ox) ** 2 + (ty - oy) ** 2 + (tz - oz) ** 2)
