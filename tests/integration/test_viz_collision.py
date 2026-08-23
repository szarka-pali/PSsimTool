"""Tests for the collision warning outline and the world boxes it is driven by.

The pure "which pairs overlap" logic lives in `tests/unit/domain/test_collision.py`.
What needs real Panda3D is the other half: that the outline is built where the
model actually is, that it is a marker rather than geometry, and that the world
boxes fed to the collision test follow a model when its **parent** moves — which
is how a model bound to a joint moves, and the case a node-local box would miss.

Requires `uv sync --extra viz`. Run with ``uv run pytest -m viz``.
"""

from __future__ import annotations

from typing import Any

import pytest

from pssim.domain.collision import (
    AABB,
    Body,
    aabb_around,
    colliding_ids,
    colliding_pairs,
    parts_overlap,
)
from pssim.viz.axes import BOX_EDGES, HIGHLIGHT_THICKNESS_PX, box_corners, make_highlight_box
from pssim.viz.collision_markers import (
    COLLISION_COLOR,
    COLLISION_THICKNESS_PX,
    make_collision_box,
)
from pssim.viz.embed import _part_bounds
from pssim.viz.sensor_markers import aabb_of

pytestmark = pytest.mark.viz

Triple = tuple[float, float, float]


def scene_root() -> Any:
    """Stand-in for `render` — bounds must be asked relative to something, or
    "world" bounds collapse to node-local ones and comparisons hold trivially."""
    from panda3d.core import NodePath

    return NodePath("scene")


def box_node(parent: Any, low: Triple, high: Triple) -> Any:
    """A `NodePath` whose geometry spans exactly `low` to `high`."""
    from panda3d.core import LineSegs, NodePath

    corners = box_corners(low, high)
    lines = LineSegs("box")
    for start, end in BOX_EDGES:
        lines.moveTo(*corners[start])
        lines.drawTo(*corners[end])

    node = NodePath(lines.create())
    node.reparentTo(parent)
    return node


def unit_cube(parent: Any) -> Any:
    return box_node(parent, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


def local_bounds(node: Any) -> tuple[Triple, Triple]:
    """The node's box in its own coordinates — what the renderer caches once."""
    low, high = node.getTightBounds(node)
    return ((low[0], low[1], low[2]), (high[0], high[1], high[2]))


def world_box_the_fast_way(node: Any, reference: Any) -> Any:
    """What `EmbeddedRenderer._world_box` does: push the cached local box's eight
    corners through the node's world matrix, instead of walking the subtree."""
    from panda3d.core import LPoint3

    low, high = local_bounds(node)
    matrix = node.getMat(reference)
    return aabb_around(
        tuple(matrix.xformPoint(LPoint3(*corner))) for corner in box_corners(low, high)
    )


UNIT_LOW: Triple = (0.0, 0.0, 0.0)
UNIT_HIGH: Triple = (1.0, 1.0, 1.0)


class TestCollisionBox:
    def test_is_named_for_lookup(self) -> None:
        node = make_collision_box(UNIT_LOW, UNIT_HIGH)

        assert node.getName() == "collision-outline"

    def test_ignores_lighting(self) -> None:
        assert make_collision_box(UNIT_LOW, UNIT_HIGH).hasLightOff()

    def test_is_never_what_a_pick_ray_hits(self) -> None:
        # A warning marker sitting on the model must not become the thing the
        # user picks instead of the model underneath it.
        node = make_collision_box(UNIT_LOW, UNIT_HIGH)

        assert node.node().getIntoCollideMask().isZero()

    def test_it_spans_the_box_it_was_given(self) -> None:
        node = make_collision_box((0.0, 0.0, 0.0), (2.0, 3.0, 4.0))

        low, high = node.getTightBounds(node)
        assert (high[0] - low[0], high[1] - low[1], high[2] - low[2]) == pytest.approx(
            (2.0, 3.0, 4.0)
        )

    def test_it_is_thicker_than_the_selection_outline(self) -> None:
        # So a model that is both selected and colliding shows both outlines on
        # the same edges without either being lost inside the other.
        assert COLLISION_THICKNESS_PX > HIGHLIGHT_THICKNESS_PX

    def test_it_is_a_different_colour_from_the_selection_outline(self) -> None:
        from pssim.viz.axes import HIGHLIGHT_COLOR

        assert COLLISION_COLOR != HIGHLIGHT_COLOR

    def test_it_lands_on_the_model_once_attached(self) -> None:
        # It is built from the model's **local** box and reparented onto it, so
        # the model's own transform must be applied exactly once. Building it
        # from bounds that already included that transform is the trap
        # `make_highlight_box` documents.
        scene = scene_root()
        model = unit_cube(scene)
        model.setPos(5.0, 0.0, 0.0)
        outline = make_collision_box(*local_bounds(model))

        outline.reparentTo(model)

        low, _ = outline.getTightBounds(scene)
        assert low[0] == pytest.approx(5.0, abs=1e-6)

    def test_the_selection_outline_still_works(self) -> None:
        # `make_highlight_box` now delegates to the shared builder; its own
        # behaviour must not have changed with the extraction.
        node = make_highlight_box(unit_cube(scene_root()))

        assert node is not None
        assert node.hasLightOff()


class TestWorldBoxesDriveTheAnswer:
    """`aabb_of` + `colliding_pairs` together — what the renderer actually runs."""

    def test_two_separated_models_do_not_collide(self) -> None:
        scene = scene_root()
        first = unit_cube(scene)
        second = unit_cube(scene)
        second.setPos(5.0, 0.0, 0.0)

        boxes = {"a": aabb_of(first, scene), "b": aabb_of(second, scene)}

        assert colliding_pairs({k: Body(v) for k, v in boxes.items() if v}) == frozenset()

    def test_a_model_driven_into_another_collides(self) -> None:
        scene = scene_root()
        first = unit_cube(scene)
        second = unit_cube(scene)
        second.setPos(0.5, 0.0, 0.0)

        boxes = {"a": aabb_of(first, scene), "b": aabb_of(second, scene)}

        assert colliding_pairs({k: Body(v) for k, v in boxes.items() if v}) == frozenset(
            {("a", "b")}
        )

    def test_a_model_moved_by_its_parent_collides(self) -> None:
        # The case node-local bounds would miss entirely: a bound model does not
        # move itself, the joint node above it does.
        from panda3d.core import NodePath

        scene = scene_root()
        still = unit_cube(scene)
        carrier = NodePath("joint-move")
        carrier.reparentTo(scene)
        riding = unit_cube(carrier)
        riding.setPos(5.0, 0.0, 0.0)

        carrier.setPos(-4.6, 0.0, 0.0)

        boxes = {"still": aabb_of(still, scene), "riding": aabb_of(riding, scene)}
        assert colliding_pairs({k: Body(v) for k, v in boxes.items() if v}) == frozenset(
            {("riding", "still")}
        )

    def test_both_models_are_marked(self) -> None:
        scene = scene_root()
        first = unit_cube(scene)
        second = unit_cube(scene)
        second.setPos(0.5, 0.0, 0.0)

        boxes = {"a": aabb_of(first, scene), "b": aabb_of(second, scene)}
        pairs = colliding_pairs({k: Body(v) for k, v in boxes.items() if v})

        assert colliding_ids(pairs) == frozenset({"a", "b"})

    def test_a_hidden_model_still_collides(self) -> None:
        # The settled rule: hiding is visual only.
        scene = scene_root()
        first = unit_cube(scene)
        second = unit_cube(scene)
        second.setPos(0.5, 0.0, 0.0)
        second.hide()

        boxes = {"a": aabb_of(first, scene), "b": aabb_of(second, scene)}

        assert colliding_pairs({k: Body(v) for k, v in boxes.items() if v}) == frozenset(
            {("a", "b")}
        )

    def test_an_outline_on_a_hidden_model_is_hidden_with_it(self) -> None:
        # The consequence of parenting the outline to the model: it goes away
        # with it, while the model it hit keeps showing one.
        scene = scene_root()
        model = unit_cube(scene)
        outline = make_collision_box(*local_bounds(model))
        outline.reparentTo(model)

        model.hide()

        assert outline.isHidden()


class TestTheFastWorldBox:
    """`EmbeddedRenderer._world_box`: the cached local box pushed through the
    world matrix, instead of `getTightBounds(render)` every frame.

    The reason is measured, not stylistic — walking a 1052-node STEP assembly
    cost ~154 ms per model per frame and froze the window at 0.5 fps. These tests
    pin that the cheap route gives the same answer where it must, and errs the
    safe way where it cannot.
    """

    def test_it_matches_the_subtree_walk_for_a_model_at_the_origin(self) -> None:
        scene = scene_root()
        model = unit_cube(scene)

        assert world_box_the_fast_way(model, scene) == aabb_of(model, scene)

    def test_it_matches_the_subtree_walk_for_a_moved_model(self) -> None:
        scene = scene_root()
        model = unit_cube(scene)
        model.setPos(5.0, -2.0, 0.5)

        assert world_box_the_fast_way(model, scene) == aabb_of(model, scene)

    def test_it_follows_a_moving_parent(self) -> None:
        # The case that matters for a bound model: it never moves itself, the
        # joint node above it does. The matrix picks that up with no bookkeeping.
        from panda3d.core import NodePath

        scene = scene_root()
        carrier = NodePath("joint-move")
        carrier.reparentTo(scene)
        model = unit_cube(carrier)

        carrier.setPos(3.0, 0.0, 0.0)

        box = world_box_the_fast_way(model, scene)
        assert box is not None
        assert box.low[0] == pytest.approx(3.0, abs=1e-6)

    def test_it_sees_a_change_with_no_cache_to_invalidate(self) -> None:
        scene = scene_root()
        model = unit_cube(scene)
        before = world_box_the_fast_way(model, scene)

        model.setPos(10.0, 0.0, 0.0)

        assert world_box_the_fast_way(model, scene) != before

    def test_a_rotated_model_is_contained_not_missed(self) -> None:
        # A rotated box's corners give a box at least as large as the true tight
        # bounds — never smaller. That is the safe direction: a real collision is
        # still never missed, only a false alarm is possible.
        scene = scene_root()
        model = unit_cube(scene)
        model.setHpr(37.0, 0.0, 0.0)

        fast = world_box_the_fast_way(model, scene)
        tight = aabb_of(model, scene)

        assert fast is not None
        assert tight is not None
        for axis in range(3):
            assert fast.low[axis] <= tight.low[axis] + 1e-6
            assert fast.high[axis] >= tight.high[axis] - 1e-6

    def test_a_rotated_model_still_collides_with_what_it_hits(self) -> None:
        scene = scene_root()
        first = unit_cube(scene)
        second = unit_cube(scene)
        second.setPos(0.5, 0.0, 0.0)
        second.setHpr(30.0, 0.0, 0.0)

        boxes = {
            "a": world_box_the_fast_way(first, scene),
            "b": world_box_the_fast_way(second, scene),
        }

        assert colliding_pairs({k: Body(v) for k, v in boxes.items() if v}) == frozenset(
            {("a", "b")}
        )

    def test_a_hidden_model_keeps_its_box(self) -> None:
        scene = scene_root()
        model = unit_cube(scene)
        before = world_box_the_fast_way(model, scene)

        model.hide()

        assert world_box_the_fast_way(model, scene) == before


class TestPartBoundsMeasureOwnGeometry:
    """The reported bug: the check behaved as if it compared the outlines.

    `_part_bounds` measured each node's whole **subtree**, and a STEP assembly's
    interior nodes are subassemblies — so one box spanned nearly the entire model
    (measured: 1 of 1052 covered more than half of it). That box overlapped any
    neighbour, making the answer "colliding" wherever the real parts were.

    Reproduced here with a nested `NodePath` tree rather than a real assembly:
    `models/` and `assets/cache/` are not in the repository, and the shape of the
    bug is the nesting, not the geometry.
    """

    def _built(self, parent_has_own_geometry: bool = True) -> Any:
        """A parent part at the origin with a child part 5 m away.

        Stands in for `viz.scene.BuiltScene` — `_part_bounds` reads only
        `node_paths` and `root`.
        """
        from panda3d.core import NodePath

        root = NodePath("model")
        parent = root.attachNewNode("subassembly")
        if parent_has_own_geometry:
            box_node(parent, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

        child = parent.attachNewNode("part")
        child.setPos(5.0, 0.0, 0.0)
        box_node(child, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))

        class Built:
            def __init__(self) -> None:
                self.root = root
                self.node_paths = {"subassembly": parent, "part": child}

        return Built()

    def test_no_box_spans_the_whole_subtree(self) -> None:
        # The parent's own geometry is 1 m wide; its subtree is 6 m wide. A 6 m
        # box is the bug.
        boxes = _part_bounds(self._built())

        widths = [box.high[0] - box.low[0] for box in boxes]
        assert max(widths) == pytest.approx(1.0, abs=1e-6)

    def test_each_part_with_geometry_gets_a_box(self) -> None:
        assert len(_part_bounds(self._built())) == 2

    def test_the_child_is_measured_where_it_sits(self) -> None:
        boxes = _part_bounds(self._built())

        assert max(box.high[0] for box in boxes) == pytest.approx(6.0, abs=1e-6)

    def test_a_node_with_no_geometry_of_its_own_contributes_nothing(self) -> None:
        # A pure grouping node has no `GeomNode` child, so it must add no box —
        # not even the box of what it contains.
        boxes = _part_bounds(self._built(parent_has_own_geometry=False))

        assert len(boxes) == 1
        assert boxes[0].low[0] == pytest.approx(5.0, abs=1e-6)

    def test_two_assemblies_side_by_side_do_not_touch(self) -> None:
        # The end-to-end consequence. The shift is 3 m on purpose: the parts sit
        # at 0-1 m and 5-6 m, so shifted copies land at 3-4 m and 8-9 m and no
        # part meets another — but the grouping node's subtree box spanned 0-6 m,
        # which does reach 3-9 m. This is the gap that used to read as a
        # collision.
        parts = _part_bounds(self._built())
        shifted = tuple(
            AABB(
                low=(box.low[0] + 3.0, box.low[1], box.low[2]),
                high=(box.high[0] + 3.0, box.high[1], box.high[2]),
            )
            for box in parts
        )

        assert parts_overlap(parts, shifted) is False
