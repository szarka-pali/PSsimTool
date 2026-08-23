"""Tests of the pure joint-marker geometry (no Panda3D).

Real `NodePath` shapes are covered by `tests/integration/test_viz_joint_markers.py`.
"""

from __future__ import annotations

import math

import pytest

from pssim.domain.model_joints import perpendicular_to
from pssim.viz.joint_markers import FORK_LENGTH_MAX_M, arrow_fork_points


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(v: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(v, v))


class TestPerpendicularTo:
    @pytest.mark.parametrize(
        "direction",
        [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.5773503, 0.5773503, 0.5773503)],
    )
    def test_the_result_is_perpendicular(self, direction: tuple[float, float, float]) -> None:
        side = perpendicular_to(direction)

        assert _dot(side, direction) == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.parametrize("direction", [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])
    def test_the_result_is_a_unit_vector(self, direction: tuple[float, float, float]) -> None:
        side = perpendicular_to(direction)

        assert _length(side) == pytest.approx(1.0, abs=1e-9)


class TestArrowForkPoints:
    def test_both_points_sit_the_same_distance_from_the_tip(self) -> None:
        left, right = arrow_fork_points((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        tx, ty, tz = 1.0, 0.0, 0.0

        left_distance = _length((tx - left[0], ty - left[1], tz - left[2]))
        right_distance = _length((tx - right[0], ty - right[1], tz - right[2]))

        assert left_distance == pytest.approx(right_distance, abs=1e-9)

    def test_the_points_are_distinct_and_behind_the_tip(self) -> None:
        left, right = arrow_fork_points((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))

        assert left != right
        assert left[0] < 1.0
        assert right[0] < 1.0

    def test_the_fork_never_exceeds_the_maximum_length_on_a_long_shaft(self) -> None:
        left, _ = arrow_fork_points((0.0, 0.0, 0.0), (100.0, 0.0, 0.0))

        # The fork's own length is bounded, so its points cannot be farther behind
        # the tip than the maximum, regardless of the shaft's total length.
        assert (100.0 - left[0]) <= FORK_LENGTH_MAX_M + 1e-9

    def test_a_short_shaft_gets_a_proportionally_short_fork(self) -> None:
        left, _ = arrow_fork_points((0.0, 0.0, 0.0), (0.02, 0.0, 0.0))

        assert (0.02 - left[0]) < FORK_LENGTH_MAX_M
