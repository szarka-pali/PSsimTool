"""Collision between objects: axis-aligned bounding boxes, nothing more.

**This is a warning marker, not a physics engine** (docs/architecture.md R14). The
consequences of using boxes are real and are not going to be hidden:

- A box is axis-aligned in the *world*, so a long part lying diagonally has a box
  far bigger than the part. Two such parts can be reported as colliding with a
  wide gap between them.
- A concave part's box is filled in, so something sitting inside the hollow of a
  C-shaped bracket reads as a collision.
- Conversely nothing is ever missed: a box always contains its part, so a real
  collision is always reported. The error is one-directional — false alarms, never
  false silence — which is the right direction for a warning.

The comparison is **per part**, not per model. One box around a whole assembly is
useless in practice: a carriage inside a frame overlaps the frame's box wherever it
stands, so the answer would be "colliding" forever. `Body` therefore carries both —
the overall box to rule a pair out cheaply, and the parts to decide the rest.

Refining this means real geometry: convex hulls from OpenCASCADE fed to
`panda3d.bullet`, which is part of Panda3D already. That is a deliberate later
step, not an oversight.

`AABB` and `boxes_overlap` live here rather than in `domain/sensors.py`, where they
started, because a sensor's proximity zone test *is* a box overlap — sensors are one
consumer of this geometry, collision is the second.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pssim.domain.machine import Vec3


@dataclass(frozen=True, slots=True)
class AABB:
    """An axis-aligned box, in metres. Viz-computed geometry (from
    `NodePath.getTightBounds()`), not user/file input — a plain `ValueError`,
    not `ConfigError`, mirrors `viz.orbit.OrbitCamera`'s own validation.
    """

    low: Vec3
    high: Vec3

    def __post_init__(self) -> None:
        for axis in range(3):
            if self.low[axis] > self.high[axis]:
                raise ValueError(f"AABB low {self.low} exceeds high {self.high} on axis {axis}")


def boxes_overlap(a: AABB, b: AABB) -> bool:
    """Whether two boxes share any volume, boundaries included — two boxes
    exactly touching count as overlapping, the same way `sensors.is_blocked`
    treats a beam exactly tangent to a face as blocked."""
    return all(a.low[axis] <= b.high[axis] and a.high[axis] >= b.low[axis] for axis in range(3))


def aabb_around(points: Iterable[Vec3]) -> AABB | None:
    """The smallest axis-aligned box containing every point. `None` if there are none.

    Used to turn a model's **local** box, whose corners have been pushed through
    its world transform, into a world box — see `viz.embed._world_box` for why
    that is done rather than asking Panda3D for world bounds every frame.

    A rotated box's corners give a box **larger** than the geometry's true tight
    world bounds. That is the safe direction: the result still contains the part,
    so a real collision is still never missed, and the extra slack can only cause
    a false alarm — the same one-directional error the module docstring describes.
    """
    ordered = list(points)
    if not ordered:
        return None
    return AABB(
        low=(
            min(point[0] for point in ordered),
            min(point[1] for point in ordered),
            min(point[2] for point in ordered),
        ),
        high=(
            max(point[0] for point in ordered),
            max(point[1] for point in ordered),
            max(point[2] for point in ordered),
        ),
    )


@dataclass(frozen=True, slots=True)
class Body:
    """One model as collision sees it, at two levels of detail.

    `box` is the whole model's box — one cheap test that rules most pairs out.
    `parts` are the individual parts' boxes, which is what makes the answer
    *useful*: a carriage sitting inside a frame overlaps the frame's overall box
    permanently, so a check at model level alone reports a collision that never
    goes away and tells you nothing.

    `parts` empty means "no finer detail available" and the overall box is taken
    as the answer.
    """

    box: AABB
    parts: tuple[AABB, ...] = ()


def parts_overlap(first: Sequence[AABB], second: Sequence[AABB]) -> bool:
    """Whether any box in `first` overlaps any box in `second`.

    A sweep along X rather than every-against-every: both sides are sorted by
    their low X edge and walked together, keeping only the boxes still open at
    the current edge. Measured on two 1052-part assemblies far apart — the worst
    case, since there is no early exit to take — 1 ms against 1036 ms for the
    naive double loop.

    Sorting happens here rather than being a precondition on the caller. It costs
    a fraction of a millisecond and a forgotten sort would not fail loudly, it
    would silently miss collisions.
    """
    left = sorted(first, key=lambda box: box.low[0])
    right = sorted(second, key=lambda box: box.low[0])

    index_left = index_right = 0
    open_left: list[AABB] = []
    open_right: list[AABB] = []

    while index_left < len(left) or index_right < len(right):
        take_left = index_right >= len(right) or (
            index_left < len(left) and left[index_left].low[0] <= right[index_right].low[0]
        )
        if take_left:
            box = left[index_left]
            index_left += 1
            open_right = [other for other in open_right if other.high[0] >= box.low[0]]
            if any(boxes_overlap(box, other) for other in open_right):
                return True
            open_left.append(box)
        else:
            box = right[index_right]
            index_right += 1
            open_left = [other for other in open_left if other.high[0] >= box.low[0]]
            if any(boxes_overlap(box, other) for other in open_left):
                return True
            open_right.append(box)

    return False


def bodies_collide(first: Body, second: Body) -> bool:
    """Whether two bodies touch: the cheap box test, then the per-part one."""
    if not boxes_overlap(first.box, second.box):
        return False
    if not first.parts or not second.parts:
        return True
    return parts_overlap(first.parts, second.parts)


def colliding_pairs(bodies: Mapping[str, Body]) -> frozenset[tuple[str, str]]:
    """Every pair of ids that touch.

    Each pair is ordered by id, so a pair appears once and the result compares
    equal across calls regardless of what order the mapping happened to iterate
    in. That matters upstream: the renderer decides whether to redraw anything by
    comparing this against the previous answer.

    **No pair is excluded on principle.** Two parts bolted to the same axis are
    reported as readily as two that genuinely crashed — from geometry alone the
    two are indistinguishable, and suppressing one class of overlap would mean
    choosing which real collisions never to mention. What *is* excluded is the
    pair whose parts do not actually meet, which is what `parts` is for.

    Pairs rather than a flat set of ids: the ids fall out of the pairs, but going
    the other way loses *what* hit what.
    """
    ids = sorted(bodies)
    return frozenset(
        (first, second)
        for index, first in enumerate(ids)
        for second in ids[index + 1 :]
        if bodies_collide(bodies[first], bodies[second])
    )


def colliding_ids(pairs: frozenset[tuple[str, str]]) -> frozenset[str]:
    """Everything involved in at least one collision — what needs marking."""
    return frozenset(item for pair in pairs for item in pair)
