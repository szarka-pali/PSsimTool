"""Tests for the floor grid's `NodePath`.

The layout itself is covered by `tests/unit/viz/test_floor.py` (pure). What only
real Panda3D can confirm: the node is named for lookup, ignores lighting like the
other scene helpers, and `setZ()` actually moves it without touching its shape.

Requires `uv sync --extra viz`. Run with ``uv run pytest -m viz``.
"""

from __future__ import annotations

import pytest

from pssim.viz.floor import make_floor_node

pytestmark = pytest.mark.viz


class TestFloorNode:
    def test_is_named_for_lookup(self) -> None:
        assert make_floor_node(1.0).getName() == "floor-grid"

    def test_ignores_lighting(self) -> None:
        # A reference plane must keep its colour regardless of where the lights are.
        assert make_floor_node(1.0).hasLightOff()

    def test_has_extent(self) -> None:
        from panda3d.core import NodePath

        scene = NodePath("scene")
        node = make_floor_node(1.0)
        node.reparentTo(scene)

        bounds = node.getTightBounds(scene)

        assert bounds is not None

    def test_set_z_moves_it_without_rebuilding(self) -> None:
        from panda3d.core import NodePath

        scene = NodePath("scene")
        node = make_floor_node(1.0)
        node.reparentTo(scene)
        before_low, before_high = node.getTightBounds(node)

        node.setZ(2.5)

        after_low, after_high = node.getTightBounds(scene)
        assert after_low[2] == pytest.approx(before_low[2] + 2.5, abs=1e-6)
        assert after_high[2] == pytest.approx(before_high[2] + 2.5, abs=1e-6)
