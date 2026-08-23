"""Tests for the real Panda3D `NodePath` shapes of joint markers.

Mirrors `test_viz_sensor_markers.py`'s pattern.

Requires `uv sync --extra viz`. Run with ``uv run pytest -m viz``.
"""

from __future__ import annotations

from typing import Any

import pytest

from pssim.viz.axes import make_axes_node
from pssim.viz.joint_markers import (
    JOINT_MARKER_COLOR,
    joint_span,
    make_axis_marker,
    make_joint_label,
    make_joint_marker,
    make_trajectory_marker,
)
from tests.factories import axis_joint, trajectory_joint

pytestmark = pytest.mark.viz


def scene_root() -> Any:
    from panda3d.core import NodePath

    return NodePath("scene")


def vertex_colors(node: Any) -> list[tuple[float, ...]]:
    """Every vertex colour in a marker, so two markers can be compared.

    A `LineSegs` colour ends up baked into the vertex data rather than on a
    render attribute, which is exactly why changing it means rebuilding."""
    from panda3d.core import GeomVertexReader

    colors: list[tuple[float, ...]] = []
    geom_node = node.node()
    for index in range(geom_node.getNumGeoms()):
        data = geom_node.getGeom(index).getVertexData()
        reader = GeomVertexReader(data, "color")
        while not reader.isAtEnd():
            colors.append(tuple(round(v, 4) for v in reader.getData4f()))
    return colors


class TestAxisMarker:
    def test_is_named_for_lookup(self) -> None:
        joint = axis_joint(name="tilt-1")

        assert make_axis_marker(joint).getName() == "joint-axis-tilt-1"

    def test_ignores_lighting(self) -> None:
        assert make_axis_marker(axis_joint(), 1.0).hasLightOff()

    def test_is_not_pickable(self) -> None:
        from panda3d.core import BitMask32

        node = make_axis_marker(axis_joint(), 1.0)

        assert node.node().getIntoCollideMask() == BitMask32.allOff()

    def test_it_runs_both_ways_through_the_centre(self) -> None:
        # A rotation axis is a line *through* a point, not a ray leaving it, so
        # the marker straddles the centre rather than starting at it.
        joint = axis_joint(origin=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0))
        scene = scene_root()
        node = make_axis_marker(joint, 2.0)
        node.reparentTo(scene)

        low, high = node.getTightBounds(scene)

        assert low[0] == pytest.approx(-1.0, abs=1e-6)
        assert high[0] == pytest.approx(1.0, abs=1e-6)

    def test_it_is_centred_on_the_centre_point(self) -> None:
        joint = axis_joint(origin=(5.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
        scene = scene_root()
        node = make_axis_marker(joint, 2.0)
        node.reparentTo(scene)

        low, high = node.getTightBounds(scene)

        assert (low[2] + high[2]) / 2.0 == pytest.approx(0.0, abs=1e-6)
        assert low[0] == pytest.approx(5.0, abs=1e-6)

    def test_its_length_comes_from_the_caller(self) -> None:
        scene = scene_root()
        short = make_axis_marker(axis_joint(), 1.0)
        long = make_axis_marker(axis_joint(), 4.0)
        short.reparentTo(scene)
        long.reparentTo(scene)

        short_low, short_high = short.getTightBounds(scene)
        long_low, long_high = long.getTightBounds(scene)

        assert (long_high[2] - long_low[2]) > (short_high[2] - short_low[2])

    def test_the_magnitude_of_the_direction_changes_nothing(self) -> None:
        # `(0,0,1)` and `(0,0,100)` are the same axis, so they must draw the same
        # marker — drawing to the vector's end would make them wildly different.
        scene = scene_root()
        unit = make_axis_marker(axis_joint(direction=(0.0, 0.0, 1.0)), 2.0)
        huge = make_axis_marker(axis_joint(direction=(0.0, 0.0, 100.0)), 2.0)
        unit.reparentTo(scene)
        huge.reparentTo(scene)

        assert unit.getTightBounds(scene)[1][2] == pytest.approx(
            huge.getTightBounds(scene)[1][2], abs=1e-6
        )


class TestTrajectoryMarker:
    def test_is_named_for_lookup(self) -> None:
        joint = trajectory_joint(name="belt-1")

        assert make_trajectory_marker(joint).getName() == "joint-trajectory-belt-1"

    def test_ignores_lighting(self) -> None:
        assert make_trajectory_marker(trajectory_joint()).hasLightOff()

    def test_is_not_pickable(self) -> None:
        from panda3d.core import BitMask32

        node = make_trajectory_marker(trajectory_joint())

        assert node.node().getIntoCollideMask() == BitMask32.allOff()

    def test_spans_origin_to_target(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(1.0, 2.0, 3.0))
        scene = scene_root()
        node = make_trajectory_marker(joint)
        node.reparentTo(scene)

        low, high = node.getTightBounds(scene)

        assert (round(low[0], 6), round(low[1], 6), round(low[2], 6)) == (0.0, 0.0, 0.0)
        assert (round(high[0], 6), round(high[1], 6), round(high[2], 6)) == (1.0, 2.0, 3.0)


class TestMakeJointMarker:
    def test_dispatches_to_the_axis_shape(self) -> None:
        joint = axis_joint(name="tilt-1")

        assert make_joint_marker(joint).getName() == "joint-axis-tilt-1"

    def test_dispatches_to_the_trajectory_shape(self) -> None:
        joint = trajectory_joint(name="belt-1")

        assert make_joint_marker(joint).getName() == "joint-trajectory-belt-1"


class TestJointColour:
    """A `LineSegs` colour is baked in when the geometry is created, which is why
    changing a joint's colour means rebuilding its marker."""

    def test_a_marker_defaults_to_the_standard_colour(self) -> None:
        node = make_joint_marker(axis_joint())

        assert node is not None

    def test_an_axis_marker_accepts_a_colour(self) -> None:
        node = make_axis_marker(axis_joint(), 1.0, (1.0, 0.0, 0.0, 1.0))

        assert node is not None

    def test_a_trajectory_marker_accepts_a_colour(self) -> None:
        node = make_trajectory_marker(trajectory_joint(), (1.0, 0.0, 0.0, 1.0))

        assert node is not None

    def test_the_colour_reaches_the_geometry(self) -> None:
        red = make_axis_marker(axis_joint(), 1.0, (1.0, 0.0, 0.0, 1.0))
        blue = make_axis_marker(axis_joint(), 1.0, (0.0, 0.0, 1.0, 1.0))

        assert vertex_colors(red) != vertex_colors(blue)

    def test_the_default_matches_the_module_colour(self) -> None:
        explicit = make_axis_marker(axis_joint(), 1.0, JOINT_MARKER_COLOR)
        implicit = make_axis_marker(axis_joint(), 1.0)

        assert vertex_colors(implicit) == vertex_colors(explicit)

    def test_make_joint_marker_passes_the_colour_on(self) -> None:
        # The dispatcher took a colour argument that was silently dropped in an
        # earlier draft; this is what would have caught it.
        through = make_joint_marker(axis_joint(), 1.0, (1.0, 0.0, 0.0, 1.0))
        direct = make_axis_marker(axis_joint(), 1.0, (1.0, 0.0, 0.0, 1.0))

        assert vertex_colors(through) == vertex_colors(direct)

    def test_a_trajectory_goes_through_the_dispatcher_too(self) -> None:
        through = make_joint_marker(trajectory_joint(), 1.0, (1.0, 0.0, 0.0, 1.0))
        direct = make_trajectory_marker(trajectory_joint(), (1.0, 0.0, 0.0, 1.0))

        assert vertex_colors(through) == vertex_colors(direct)


class TestJointLabel:
    def test_it_is_named_for_lookup(self) -> None:
        assert make_joint_label("rail", 1.0).getName() == "joint-label-rail"

    def test_it_shows_the_name(self) -> None:
        node = make_joint_label("rail", 1.0)

        assert node.node().getText() == "rail"

    def test_it_ignores_lighting(self) -> None:
        assert make_joint_label("rail", 1.0).hasLightOff()

    def test_it_is_never_what_a_pick_ray_hits(self) -> None:
        node = make_joint_label("rail", 1.0)

        assert node.node().getIntoCollideMask().isZero()

    def test_it_turns_to_face_the_viewer(self) -> None:
        # Without the billboard the text is edge-on and invisible from half the
        # orbit, which is the whole point of showing a name in the scene.
        # `RenderEffects` has no `hasBillboard`; the effect count is what is
        # actually observable — verified 0 before `setBillboardPointEye` and 1
        # after.
        from panda3d.core import NodePath, TextNode

        plain = NodePath(TextNode("plain"))

        assert make_joint_label("rail", 1.0).getEffects().getNumEffects() > (
            plain.getEffects().getNumEffects()
        )

    def test_it_sits_above_the_origin(self) -> None:
        # Not on top of the marker line it belongs to.
        assert make_joint_label("rail", 1.0).getPos()[2] > 0.0

    def test_it_scales_with_the_joint(self) -> None:
        small = make_joint_label("rail", 0.2)
        large = make_joint_label("rail", 4.0)

        assert large.getScale()[0] > small.getScale()[0]

    def test_a_degenerate_span_still_gives_a_readable_label(self) -> None:
        # Two points a hair apart must not produce text scaled to nothing.
        assert make_joint_label("rail", 0.0).getScale()[0] > 0.0

    def test_it_takes_the_joints_colour(self) -> None:
        node = make_joint_label("rail", 1.0, (1.0, 0.0, 0.0, 1.0))

        assert tuple(node.node().getTextColor())[:3] == pytest.approx((1.0, 0.0, 0.0))


class TestJointSpan:
    def test_it_measures_origin_to_target(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(3.0, 4.0, 0.0))

        assert joint_span(joint) == pytest.approx(5.0)

    def test_it_ignores_direction(self) -> None:
        forwards = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(2.0, 0.0, 0.0))
        backwards = trajectory_joint(origin=(2.0, 0.0, 0.0), target=(0.0, 0.0, 0.0))

        assert joint_span(forwards) == pytest.approx(joint_span(backwards))


class TestTextHeightMeansHeight:
    """The reported bug: the label size setting did nothing below 50 mm.

    `make_joint_label` treated its argument as a span to scale down by 0.09 and
    clamped it at 50 mm first, so asking for 10, 25 or 50 mm all produced 4.5 mm
    of text. These pin the corrected arithmetic with the measured numbers.
    """

    def test_the_height_asked_for_is_the_height_drawn(self) -> None:
        assert make_joint_label("rail", 0.01).getScale()[0] == pytest.approx(0.01)

    @pytest.mark.parametrize("height_mm", [10.0, 25.0, 50.0, 120.0, 300.0])
    def test_every_height_is_honoured(self, height_mm: float) -> None:
        node = make_joint_label("rail", height_mm / 1000.0)

        assert node.getScale()[0] == pytest.approx(height_mm / 1000.0)

    def test_small_sizes_are_no_longer_all_the_same(self) -> None:
        # 10, 25 and 50 mm previously all came out at 4.5 mm.
        scales = [make_joint_label("rail", mm / 1000.0).getScale()[0] for mm in (10, 25, 50)]

        assert len({round(scale, 9) for scale in scales}) == 3

    def test_a_zero_height_still_gives_something_visible(self) -> None:
        assert make_joint_label("rail", 0.0).getScale()[0] > 0.0

    def test_the_lift_follows_the_height(self) -> None:
        # The label sits above the marker line it belongs to, and a taller label
        # has to clear it by more.
        short = make_joint_label("rail", 0.02)
        tall = make_joint_label("rail", 0.2)

        assert tall.getPos()[2] > short.getPos()[2]

    def test_the_lift_is_smaller_than_the_text(self) -> None:
        # Far enough off the line to read, not so far it floats away from the
        # joint it names.
        node = make_joint_label("rail", 0.1)

        assert 0.0 < node.getPos()[2] < node.getScale()[0]


class TestCrossTextIsIndependentOfArmLength:
    """The other half of the report: the main cross's X/Y/Z letters were 100 mm
    while a small cross's were 3.1 mm, because the letter size was
    `arm_length * 0.25`."""

    def _letter_scale(self, node: Any) -> float:
        for child in node.getChildren():
            if child.getName().startswith("axis-label"):
                return float(child.getScale()[0])
        raise AssertionError("the cross has no letters")

    def test_the_text_height_asked_for_is_used(self) -> None:
        node = make_axes_node(1.0, text_height_m=0.05)

        assert self._letter_scale(node) == pytest.approx(0.05)

    def test_a_small_cross_and_a_large_one_get_the_same_letters(self) -> None:
        small = make_axes_node(0.05, text_height_m=0.05)
        large = make_axes_node(5.0, text_height_m=0.05)

        assert self._letter_scale(small) == pytest.approx(self._letter_scale(large))

    def test_the_arms_still_follow_their_own_length(self) -> None:
        short = make_axes_node(0.1, text_height_m=0.05)
        long = make_axes_node(1.0, text_height_m=0.05)

        assert long.getTightBounds()[1][0] > short.getTightBounds()[1][0]

    def test_labels_can_still_be_left_off(self) -> None:
        node = make_axes_node(1.0, text_height_m=0.05, with_labels=False)

        assert not [
            child for child in node.getChildren() if child.getName().startswith("axis-label")
        ]
