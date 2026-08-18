"""Tests of the cartesian cross.

The geometry of the cross is a pure function, so it can be checked without Panda3D.
The drawing itself is covered by the integration tests.
"""

from __future__ import annotations

import math

import pytest

from pssim.viz.axes import (
    AXIS_COLORS,
    AXIS_DIRECTIONS,
    DEFAULT_SCALE,
    MIN_LENGTH_M,
    axis_length_for,
    axis_segments,
)


class TestAxisLength:
    def test_it_scales_with_the_scene_size(self) -> None:
        # A fixed length does not work: on a 0.2 m part a metre-long axis would fill the view.
        assert axis_length_for(10.0) > axis_length_for(1.0)

    def test_it_is_a_fraction_of_the_radius(self) -> None:
        assert axis_length_for(4.0) == pytest.approx(4.0 * DEFAULT_SCALE)

    def test_the_cross_is_smaller_than_the_model(self) -> None:
        # Otherwise the orientation aid would shout over what the user came to look at.
        assert axis_length_for(2.0) < 2.0

    @pytest.mark.parametrize("radius", [0.0, -1.0, 1e-9])
    def test_an_empty_scene_gets_the_minimum_length(self, radius: float) -> None:
        assert axis_length_for(radius) == pytest.approx(MIN_LENGTH_M)


class TestSegments:
    def test_su_tri_osi(self) -> None:
        assert len(axis_segments(1.0)) == 3

    def test_the_axes_are_named_xyz(self) -> None:
        assert {segment.name for segment in axis_segments(1.0)} == {"X", "Y", "Z"}

    def test_all_of_them_start_at_the_origin(self) -> None:
        # The cross shows the model's zero — if it did not start at the origin, it would lie.
        assert all(segment.start == (0.0, 0.0, 0.0) for segment in axis_segments(2.0))

    def test_dlzka_ramena_sedi(self) -> None:
        for segment in axis_segments(3.0):
            assert math.dist(segment.start, segment.end) == pytest.approx(3.0, abs=1e-9)

    def test_the_axes_point_in_the_positive_directions(self) -> None:
        by_name = {segment.name: segment for segment in axis_segments(1.0)}

        assert by_name["X"].end == pytest.approx((1.0, 0.0, 0.0))
        assert by_name["Y"].end == pytest.approx((0.0, 1.0, 0.0))
        assert by_name["Z"].end == pytest.approx((0.0, 0.0, 1.0))

    def test_the_colours_follow_the_cad_convention(self) -> None:
        # X red, Y green, Z blue — what anyone who has seen CAD expects.
        assert AXIS_COLORS["X"][0] > AXIS_COLORS["X"][1]
        assert AXIS_COLORS["Y"][1] > AXIS_COLORS["Y"][0]
        assert AXIS_COLORS["Z"][2] > AXIS_COLORS["Z"][0]

    def test_every_axis_has_its_own_colour(self) -> None:
        colors = {segment.color for segment in axis_segments(1.0)}

        assert len(colors) == 3

    def test_the_label_sits_past_the_end_of_the_axis(self) -> None:
        # Sitting exactly on the end, it would cover the line.
        for segment in axis_segments(1.0):
            assert math.dist(segment.start, segment.label_position) > 1.0

    def test_nulova_dlzka_nespadne(self) -> None:
        segments = axis_segments(0.0)

        assert math.dist(segments[0].start, segments[0].end) == pytest.approx(MIN_LENGTH_M)

    def test_smery_zodpovedaju_tabulke(self) -> None:
        by_name = {segment.name: segment for segment in axis_segments(5.0)}

        for name, direction in AXIS_DIRECTIONS.items():
            expected = tuple(component * 5.0 for component in direction)
            assert by_name[name].end == pytest.approx(expected)
