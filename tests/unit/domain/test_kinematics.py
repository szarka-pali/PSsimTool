"""Testy kinematiky."""

from __future__ import annotations

import math

import pytest

from pssim.domain.kinematics import clamp_to_limits, joint_pose, rest_pose
from pssim.domain.machine import Transform
from tests.factories import fixed_joint, prismatic_joint, revolute_joint


class TestPrismatic:
    def test_it_translates_along_the_axis(self) -> None:
        joint = prismatic_joint(axis=(1.0, 0.0, 0.0))

        pose = joint_pose(joint, 1.5)

        assert pose.translation == pytest.approx((1.5, 0.0, 0.0), abs=1e-12)

    def test_nerotuje(self) -> None:
        pose = joint_pose(prismatic_joint(), 1.5)

        assert pose.rotation_angle_rad == 0.0

    def test_pripocita_pevny_offset(self) -> None:
        joint = prismatic_joint(origin=Transform(xyz=(0.0, 0.0, 0.15)))

        pose = joint_pose(joint, 1.0)

        assert pose.translation == pytest.approx((1.0, 0.0, 0.15), abs=1e-12)

    def test_zaporna_os_obrati_smer(self) -> None:
        joint = prismatic_joint(axis=(-1.0, 0.0, 0.0), limits=None)

        pose = joint_pose(joint, 2.0)

        assert pose.translation == pytest.approx((-2.0, 0.0, 0.0), abs=1e-12)


class TestRevolute:
    def test_it_rotates_by_the_given_angle(self) -> None:
        pose = joint_pose(revolute_joint(), math.pi / 4)

        assert pose.rotation_angle_rad == pytest.approx(math.pi / 4, abs=1e-12)

    def test_it_does_not_translate(self) -> None:
        pose = joint_pose(revolute_joint(), math.pi / 4)

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)


class TestFixed:
    def test_it_ignores_the_value(self) -> None:
        joint = fixed_joint()

        assert joint_pose(joint, 999.0).translation == joint_pose(joint, 0.0).translation

    def test_it_is_never_clamped(self) -> None:
        assert joint_pose(fixed_joint(), 999.0).is_clamped is False


class TestLimits:
    def test_a_value_in_range_passes_unchanged(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), 1.0)

        assert (value, is_clamped) == (1.0, False)

    @pytest.mark.parametrize("value", [0.0, 2.5])
    def test_a_value_exactly_on_the_limit_is_not_clamped(self, value: float) -> None:
        _, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), value)

        assert is_clamped is False

    def test_below_the_lower_limit_it_is_clamped(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), -1.0)

        assert (value, is_clamped) == (0.0, True)

    def test_above_the_upper_limit_it_is_clamped(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), 9.0)

        assert (value, is_clamped) == (2.5, True)

    def test_without_limits_anything_passes(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=None), 1e6)

        assert (value, is_clamped) == (1e6, False)

    def test_clamping_shows_in_the_pose(self) -> None:
        pose = joint_pose(prismatic_joint(limits=(0.0, 2.5)), 9.0)

        assert pose.is_clamped is True
        assert pose.translation == pytest.approx((2.5, 0.0, 0.0), abs=1e-12)


class TestRestPose:
    def test_without_limits_the_rest_pose_is_zero(self) -> None:
        pose = rest_pose(prismatic_joint(limits=None))

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)

    def test_limits_excluding_zero_move_to_the_nearest_limit(self) -> None:
        # Without this the part would end up outside the physically possible range until
        # the first value from the PLC arrives.
        pose = rest_pose(prismatic_joint(limits=(1.0, 2.0)))

        assert pose.translation == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)

    def test_a_value_above_the_upper_limit_is_clamped_in_the_rest_pose(self) -> None:
        pose = rest_pose(prismatic_joint(limits=(-2.0, -1.0)))

        assert pose.translation == pytest.approx((-1.0, 0.0, 0.0), abs=1e-12)
