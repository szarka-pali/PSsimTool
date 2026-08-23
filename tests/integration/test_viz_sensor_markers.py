"""Tests for sensor markers and the world-space bounding boxes they react to.

Mirrors `test_viz_highlight.py`'s pattern: nothing here needs a window, but real
Panda3D geometry is the only thing that can confirm shapes and transforms.

Requires `uv sync --extra viz`. Run with ``uv run pytest -m viz``.
"""

from __future__ import annotations

from typing import Any

import pytest

from pssim.viz.axes import BOX_EDGES, box_corners
from pssim.viz.sensor_markers import (
    aabb_of,
    make_beam_marker,
    make_proximity_marker,
    make_sensor_marker,
)
from tests.factories import beam_sensor, proximity_sensor

pytestmark = pytest.mark.viz


def scene_root() -> Any:
    """Stand-in for `render` — bounds must be asked relative to something, or
    "world" bounds collapse to node-local ones and comparisons hold trivially."""
    from panda3d.core import NodePath

    return NodePath("scene")


def box_node(parent: Any, low: tuple[float, float, float], high: tuple[float, float, float]) -> Any:
    """A `NodePath` whose geometry spans exactly `low` to `high` — enough to give
    `getTightBounds()` a known answer without needing real mesh data."""
    from panda3d.core import LineSegs, NodePath

    corners = box_corners(low, high)
    lines = LineSegs("box")
    for start, end in BOX_EDGES:
        lines.moveTo(*corners[start])
        lines.drawTo(*corners[end])

    node = NodePath(lines.create())
    node.reparentTo(parent)
    return node


class TestBeamMarker:
    def test_is_named_for_lookup(self) -> None:
        sensor = beam_sensor(name="gate-1")

        assert make_beam_marker(sensor, is_active=False).getName() == "sensor-beam-gate-1"

    def test_ignores_lighting(self) -> None:
        assert make_beam_marker(beam_sensor(), is_active=False).hasLightOff()

    def test_spans_origin_to_target(self) -> None:
        sensor = beam_sensor(origin=(0.0, 0.0, 0.0), target=(1.0, 2.0, 3.0))
        scene = scene_root()
        node = make_beam_marker(sensor, is_active=False)
        node.reparentTo(scene)

        low, high = node.getTightBounds(scene)

        assert (round(low[0], 6), round(low[1], 6), round(low[2], 6)) == (0.0, 0.0, 0.0)
        assert (round(high[0], 6), round(high[1], 6), round(high[2], 6)) == (1.0, 2.0, 3.0)


class TestProximityMarker:
    def test_is_named_for_lookup(self) -> None:
        sensor = proximity_sensor(name="zone-1")

        assert make_proximity_marker(sensor, is_active=False).getName() == "sensor-zone-zone-1"

    def test_ignores_lighting(self) -> None:
        assert make_proximity_marker(proximity_sensor(), is_active=False).hasLightOff()

    def test_spans_the_zone(self) -> None:
        sensor = proximity_sensor(origin=(1.0, 2.0, 3.0), half_extent_m=0.5)
        scene = scene_root()
        node = make_proximity_marker(sensor, is_active=False)
        node.reparentTo(scene)

        low, high = node.getTightBounds(scene)

        assert round(low[0], 6) == pytest.approx(0.5)
        assert round(high[0], 6) == pytest.approx(1.5)


class TestMakeSensorMarker:
    def test_dispatches_to_the_beam_shape(self) -> None:
        sensor = beam_sensor(name="gate-1")

        assert make_sensor_marker(sensor, is_active=False).getName() == "sensor-beam-gate-1"

    def test_dispatches_to_the_proximity_shape(self) -> None:
        sensor = proximity_sensor(name="zone-1")

        assert make_sensor_marker(sensor, is_active=False).getName() == "sensor-zone-zone-1"


class TestAabbOf:
    def test_matches_a_known_box(self) -> None:
        scene = scene_root()
        node = box_node(scene, (0.0, 0.0, 0.0), (1.0, 2.0, 3.0))

        box = aabb_of(node, scene)

        assert box is not None
        assert tuple(round(value, 6) for value in box.low) == (0.0, 0.0, 0.0)
        assert tuple(round(value, 6) for value in box.high) == (1.0, 2.0, 3.0)

    def test_reflects_a_moved_node(self) -> None:
        scene = scene_root()
        node = box_node(scene, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
        node.setPos(5.0, 0.0, 0.0)

        box = aabb_of(node, scene)

        assert box is not None
        assert round(box.low[0], 6) == pytest.approx(5.0)
        assert round(box.high[0], 6) == pytest.approx(6.0)

    def test_none_for_an_empty_node(self) -> None:
        from panda3d.core import NodePath

        assert aabb_of(NodePath("empty"), scene_root()) is None
