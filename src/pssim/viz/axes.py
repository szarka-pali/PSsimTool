"""The cartesian cross at the origin of the coordinate system.

It shows where the model's zero is and how the scene is oriented — the same thing the
origin display does in SolidWorks. Without it, it is easy to lose track of which
direction is which while rotating, especially with symmetrical parts.

The colours are the convention most CAD tools use:
**X red, Y green, Z blue.**

The layout of the segments is a pure function — it can be tested without Panda3D.
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

#: The cross should be legible without shouting over the model. A quarter of its radius
#: is a compromise that works from a single part up to a whole production line.
DEFAULT_SCALE: Final = 0.25

#: Even in an empty scene the cross has to be visible.
MIN_LENGTH_M: Final = 0.01

#: How far past the end of an axis its label sits, as a fraction of the axis length.
LABEL_OFFSET: Final = 1.12

LINE_THICKNESS_PX: Final = 2.0


@dataclass(frozen=True, slots=True)
class AxisSegment:
    """One axis of the cross: a line from the origin and a label at its end."""

    name: str
    start: Vec3
    end: Vec3
    color: Rgba
    label_position: Vec3


def axis_length_for(scene_radius_m: float, scale: float = DEFAULT_SCALE) -> float:
    """The arm length of the cross for a scene of the given radius.

    The cross scales with the model rather than being fixed: on a 0.2 m part a
    metre-long axis would fill the view, and on a 20 m line a centimetre-long one would
    be invisible.
    """
    return max(scene_radius_m * scale, MIN_LENGTH_M)


def axis_segments(length_m: float) -> tuple[AxisSegment, ...]:
    """The three positive half-axes from the origin. A pure function.

    The negative half-axes are not drawn — SolidWorks does not draw them either, and the
    cross would otherwise look like six random lines in a dense assembly.
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


#: Colour of the selection outline. Deliberately not one of the axis colours,
#: so a highlighted model can never be mistaken for an axis.
HIGHLIGHT_COLOR: Final[Rgba] = (1.0, 0.72, 0.15, 1.0)

HIGHLIGHT_THICKNESS_PX: Final = 1.6

#: The twelve edges of a box, as index pairs into `box_corners()`.
BOX_EDGES: Final[tuple[tuple[int, int], ...]] = (
    (0, 1),
    (1, 3),
    (3, 2),
    (2, 0),  # bottom face
    (4, 5),
    (5, 7),
    (7, 6),
    (6, 4),  # top face
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),  # uprights
)


def box_corners(low: Vec3, high: Vec3) -> tuple[Vec3, ...]:
    """The eight corners of an axis-aligned box, ordered for `BOX_EDGES`.

    Pure function: the ordering is what makes the edge table correct, and that
    is worth a test.
    """
    return tuple(
        (
            high[0] if index & 1 else low[0],
            high[1] if index & 2 else low[1],
            high[2] if index & 4 else low[2],
        )
        for index in range(8)
    )


def make_highlight_box(node_path: Any) -> Any | None:
    """Wireframe box around a subtree, for showing which model is selected.

    Returns `None` when the subtree has no extent — an empty model has nothing
    to outline, and a zero-size box would render as a dot at the origin.

    The box is meant to be attached **as a child of `node_path`**, so that it
    follows the model when it is moved or rotated.

    That is why the bounds are asked for in the node's **own** coordinates.
    Bare `getTightBounds()` returns them relative to the *parent*, so they
    already include the node's transform; attaching such a box as a child would
    apply that transform a second time and the outline would sit at double the
    placement. (Measured: a model at x=0.4 got an outline at x=0.8.)
    """
    from panda3d.core import LineSegs, NodePath

    bounds = node_path.getTightBounds(node_path)
    if bounds is None:
        return None

    low, high = bounds
    corners = box_corners(
        (low[0], low[1], low[2]),
        (high[0], high[1], high[2]),
    )

    lines = LineSegs("highlight")
    lines.setThickness(HIGHLIGHT_THICKNESS_PX)
    lines.setColor(*HIGHLIGHT_COLOR)
    for start, end in BOX_EDGES:
        lines.moveTo(*corners[start])
        lines.drawTo(*corners[end])

    box = NodePath(lines.create())
    box.setName("selection-highlight")
    # An outline is a marker, not geometry — lighting would dim it from behind.
    box.setLightOff()
    return box


def make_axes_node(length_m: float, with_labels: bool = True) -> Any:
    """Build a `NodePath` with the cross. The caller attaches it wherever it is needed."""
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
    # The cross is an orientation aid, not geometry — lighting would change its colour
    # with the viewing angle and the red axis would be red one moment and brown the next.
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
        # The label always turns to face the viewer, otherwise it would be illegible
        # from the side.
        label.setBillboardPointEye()
        label.setLightOff()

    return root
