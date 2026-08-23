"""Tests for the selection outline.

The bug these exist for: `getTightBounds()` without an argument returns bounds
relative to the **parent**, so they already include the node's own transform.
Attaching such a box as a child applies the transform twice and the outline
lands at double the placement — a model at x=0.4 got an outline at x=0.8.

Nothing but real geometry with a real transform catches that, so these run
against Panda3D. No window is opened.

Requires `uv sync --extra viz`. Run with ``uv run pytest -m viz``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from pssim.cad.mesh import build_mesh
from pssim.viz.axes import make_highlight_box
from pssim.viz.mesh_loader import geom_node_from_mesh

pytestmark = pytest.mark.viz

#: A 200 x 100 x 80 mm block, the same order of magnitude as the test fixture.
BLOCK_SIZE = (0.2, 0.1, 0.08)


def scene_root() -> Any:
    """Stand-in for `render`.

    The model must hang off something, otherwise "world" bounds collapse to
    model-local ones and the assertions below would hold no matter what.
    """
    from panda3d.core import NodePath

    return NodePath("scene")


def block_node(parent: Any, name: str = "block") -> Any:
    """A NodePath holding one axis-aligned block from the origin outwards."""
    from panda3d.core import NodePath

    width, depth, height = BLOCK_SIZE
    vertices = np.array(
        [
            [0.0, 0.0, 0.0],
            [width, 0.0, 0.0],
            [width, depth, 0.0],
            [0.0, depth, 0.0],
            [0.0, 0.0, height],
            [width, 0.0, height],
            [width, depth, height],
            [0.0, depth, height],
        ],
        dtype=np.float32,
    )
    # Two triangles are enough to span the full extent in all three axes.
    indices = np.array([[0, 1, 6], [0, 6, 7]], dtype=np.uint32)

    root = NodePath(name)
    root.attachNewNode(geom_node_from_mesh(build_mesh(vertices, indices), name))
    root.reparentTo(parent)
    return root


def bounds_in(node: Any, reference: Any) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Tight bounds of `node` expressed in `reference` coordinates.

    Both the model and its outline are measured against the same reference, so
    a transform applied twice shows up as a mismatch.
    """
    bounds = node.getTightBounds(reference)
    assert bounds is not None, "node has no extent"
    low, high = bounds
    return (
        tuple(round(low[index], 6) for index in range(3)),
        tuple(round(high[index], 6) for index in range(3)),
    )


def attach_highlight(model: Any) -> Any:
    box = make_highlight_box(model)
    assert box is not None
    box.reparentTo(model)
    return box


class TestOutlineMatchesGeometry:
    def test_untransformed_model(self) -> None:
        scene = scene_root()
        model = block_node(scene)

        box = attach_highlight(model)

        assert bounds_in(box, scene) == bounds_in(model, scene)

    def test_moved_model(self) -> None:
        # The regression: with parent-relative bounds the outline landed at
        # double the offset.
        scene = scene_root()
        model = block_node(scene)
        model.setPos(0.4, 0.0, 0.0)

        box = attach_highlight(model)

        assert bounds_in(box, scene)[0][0] == pytest.approx(0.4, abs=1e-6)

    def test_moved_model_outline_has_the_right_size(self) -> None:
        scene = scene_root()
        model = block_node(scene)
        model.setPos(0.4, -0.2, 0.1)

        box = attach_highlight(model)
        low, high = bounds_in(box, scene)

        assert high[0] - low[0] == pytest.approx(BLOCK_SIZE[0], abs=1e-6)

    def test_moved_model_outline_covers_the_model(self) -> None:
        scene = scene_root()
        model = block_node(scene)
        model.setPos(1.5, 2.5, -0.5)

        box = attach_highlight(model)

        assert bounds_in(box, scene) == bounds_in(model, scene)

    def test_rotated_model(self) -> None:
        from panda3d.core import LQuaternion, LVector3

        scene = scene_root()
        model = block_node(scene)
        quat = LQuaternion()
        quat.setFromAxisAngleRad(math.pi / 2, LVector3(0.0, 0.0, 1.0))
        model.setQuat(quat)

        box = attach_highlight(model)

        assert bounds_in(box, scene) == bounds_in(model, scene)

    def test_moved_and_rotated_model(self) -> None:
        from panda3d.core import LQuaternion, LVector3

        scene = scene_root()
        model = block_node(scene)
        model.setPos(0.3, 0.0, 0.15)
        quat = LQuaternion()
        quat.setFromAxisAngleRad(math.pi / 4, LVector3(0.0, 0.0, 1.0))
        model.setQuat(quat)

        box = attach_highlight(model)

        assert bounds_in(box, scene) == bounds_in(model, scene)

    def test_outline_follows_a_later_move(self) -> None:
        # The outline is a child, so moving the model must carry it along.
        scene = scene_root()
        model = block_node(scene)
        box = attach_highlight(model)

        model.setPos(2.0, 0.0, 0.0)

        assert bounds_in(box, scene) == bounds_in(model, scene)
        assert bounds_in(box, scene)[0][0] == pytest.approx(2.0, abs=1e-6)


class TestOutlineDoesNotDistortBounds:
    def test_outline_does_not_inflate_the_model(self) -> None:
        # A wrong outline inflated the model's own bounds, which then fed the
        # axis sizing and `fit_view`.
        scene = scene_root()
        model = block_node(scene)
        model.setPos(0.4, 0.0, 0.0)
        before = bounds_in(model, scene)

        attach_highlight(model)

        assert bounds_in(model, scene) == before

    def test_width_stays_the_block_width(self) -> None:
        scene = scene_root()
        model = block_node(scene)
        model.setPos(0.4, 0.0, 0.0)

        attach_highlight(model)
        low, high = bounds_in(model, scene)

        assert high[0] - low[0] == pytest.approx(BLOCK_SIZE[0], abs=1e-6)


class TestEdgeCases:
    def test_empty_node_has_no_outline(self) -> None:
        from panda3d.core import NodePath

        assert make_highlight_box(NodePath("empty")) is None

    def test_outline_is_named_for_lookup(self) -> None:
        assert attach_highlight(block_node(scene_root())).getName() == "selection-highlight"

    def test_outline_ignores_lighting(self) -> None:
        # A marker must keep its colour regardless of where the lights are.
        box = attach_highlight(block_node(scene_root()))

        assert box.hasLightOff()


class TestHidingTakesTheMarkersWithIt:
    """`EmbeddedRenderer.set_model_visible` is a plain `hide()` on the model
    root, and both the selection box and any collision outline hang off that
    root. This pins the consequence the renderer's docstring claims: a marker is
    never left floating where an invisible model used to be."""

    def test_the_model_is_visible_to_start_with(self) -> None:
        assert not block_node(scene_root()).isHidden()

    def test_hiding_the_model_hides_its_outline(self) -> None:
        model = block_node(scene_root())
        box = attach_highlight(model)

        model.hide()

        assert box.isHidden()

    def test_showing_the_model_brings_the_outline_back(self) -> None:
        model = block_node(scene_root())
        box = attach_highlight(model)
        model.hide()

        model.show()

        assert not box.isHidden()

    def test_hiding_one_model_leaves_another_alone(self) -> None:
        scene = scene_root()
        hidden = block_node(scene, name="hidden")
        other = block_node(scene, name="other")

        hidden.hide()

        assert not other.isHidden()

    def test_a_hidden_model_keeps_its_world_bounds(self) -> None:
        # Why hiding stays purely visual: the geometry is still there, so the
        # model still blocks sensors and still collides.
        scene = scene_root()
        model = block_node(scene)
        before = bounds_in(model, scene)

        model.hide()

        assert bounds_in(model, scene) == before
