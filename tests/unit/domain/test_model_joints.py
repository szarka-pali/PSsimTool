"""Tests for model joints: axes and trajectories attached to a whole loaded model.

Mirrors test_kinematics.py's shape (pure pose math) and test_sensors.py's rigor for
edge cases (degenerate geometry, construction validation).
"""

from __future__ import annotations

import math

import pytest

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Transform
from pssim.domain.model_joints import (
    Anchor,
    AnchorDisplay,
    ModelJoint,
    ModelJointDisplay,
    ModelJointKind,
    anchor_pose,
    clamp,
    direction_of,
    effective_limits,
    from_anchor,
    from_model_joint,
    joint_value_pose,
    model_joint_pose,
    perpendicular_to,
    rest_model_joint_pose,
    rotate_vec3,
    rotation_onto,
    to_anchor,
    to_model_joint,
    value_scale,
)
from pssim.domain.placement import IDENTITY_PLACEMENT
from pssim.domain.units import DEG_TO_RAD, MM_TO_M
from tests.factories import axis_joint, trajectory_joint


class TestConstruction:
    def test_a_valid_axis_is_accepted(self) -> None:
        axis_joint()

    def test_a_valid_trajectory_is_accepted(self) -> None:
        trajectory_joint()

    def test_an_empty_name_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="non-empty name"):
            axis_joint(name="")

    def test_an_empty_variable_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="variable"):
            axis_joint(variable="")

    def test_a_degenerate_axis_is_refused(self) -> None:
        # An axis has no second point any more, so its degenerate case is a zero
        # direction rather than two coincident points.
        with pytest.raises(ConfigError, match="direction is zero"):
            axis_joint(origin=(1.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))

    def test_a_degenerate_trajectory_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="same point"):
            trajectory_joint(origin=(1.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))

    def test_a_lower_limit_above_the_upper_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="lower limit"):
            axis_joint(limits=(2.0, 1.0))


class TestRotateVec3:
    def test_a_quarter_turn_about_z_of_the_x_axis_point(self) -> None:
        # Hand-verified case from the plan: axis=Z, point=(1,0,0), angle=90 deg.
        rotated = rotate_vec3((0.0, 0.0, 1.0), math.pi / 2, (1.0, 0.0, 0.0))

        assert rotated == pytest.approx((0.0, 1.0, 0.0), abs=1e-12)

    def test_a_full_turn_returns_to_the_start(self) -> None:
        rotated = rotate_vec3((0.0, 0.0, 1.0), 2.0 * math.pi, (1.0, 2.0, 3.0))

        assert rotated == pytest.approx((1.0, 2.0, 3.0), abs=1e-9)

    def test_a_point_on_the_axis_does_not_move(self) -> None:
        rotated = rotate_vec3((0.0, 0.0, 1.0), math.pi / 2, (0.0, 0.0, 5.0))

        assert rotated == pytest.approx((0.0, 0.0, 5.0), abs=1e-12)

    def test_zero_angle_is_the_identity(self) -> None:
        rotated = rotate_vec3((1.0, 0.0, 0.0), 0.0, (3.0, -2.0, 7.0))

        assert rotated == pytest.approx((3.0, -2.0, 7.0), abs=1e-12)


class TestAxisPose:
    def test_rotating_about_the_local_origin_does_not_translate(self) -> None:
        joint = axis_joint(origin=(0.0, 0.0, 0.0), target=(0.0, 0.0, 1.0))

        pose = model_joint_pose(joint, math.pi / 2)

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)
        assert pose.rotation_angle_rad == pytest.approx(math.pi / 2, abs=1e-12)

    def test_rotating_about_an_off_origin_pivot_compensates_the_translation(self) -> None:
        # The plan's hand-verified case: axis=Z through (1,0,0), a quarter turn.
        # The local origin must land at (1,-1,0); the pivot itself must map to itself.
        joint = axis_joint(origin=(1.0, 0.0, 0.0), target=(1.0, 0.0, 1.0))

        pose = model_joint_pose(joint, math.pi / 2)

        assert pose.translation == pytest.approx((1.0, -1.0, 0.0), abs=1e-9)
        rotated_pivot = rotate_vec3(pose.rotation_axis, pose.rotation_angle_rad, joint.origin)
        pivot_world = tuple(r + t for r, t in zip(rotated_pivot, pose.translation, strict=True))
        assert pivot_world == pytest.approx(joint.origin, abs=1e-9)

    def test_the_direction_is_derived_from_the_two_points(self) -> None:
        joint = axis_joint(origin=(0.0, 0.0, 0.0), target=(0.0, 0.0, 5.0))

        pose = model_joint_pose(joint, 0.0)

        assert pose.rotation_axis == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)

    def test_without_limits_a_value_is_clamped_to_one_full_turn(self) -> None:
        joint = axis_joint(limits=None)

        pose = model_joint_pose(joint, 10.0)

        assert pose.is_clamped is True
        assert pose.rotation_angle_rad == pytest.approx(math.pi, abs=1e-12)

    def test_explicit_limits_clamp_below(self) -> None:
        joint = axis_joint(limits=(0.0, math.pi / 2))

        pose = model_joint_pose(joint, -1.0)

        assert pose.rotation_angle_rad == pytest.approx(0.0, abs=1e-12)
        assert pose.is_clamped is True

    def test_explicit_limits_clamp_above(self) -> None:
        joint = axis_joint(limits=(0.0, math.pi / 2))

        pose = model_joint_pose(joint, 10.0)

        assert pose.rotation_angle_rad == pytest.approx(math.pi / 2, abs=1e-12)
        assert pose.is_clamped is True

    def test_a_value_in_range_is_not_clamped(self) -> None:
        joint = axis_joint(limits=(0.0, math.pi))

        pose = model_joint_pose(joint, math.pi / 4)

        assert pose.is_clamped is False


class TestTrajectoryPose:
    def test_the_origin_value_stays_at_the_origin(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))

        pose = model_joint_pose(joint, 0.0)

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)
        assert pose.rotation_angle_rad == 0.0

    def test_the_full_length_reaches_the_target(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(2.0, 0.0, 0.0))

        pose = model_joint_pose(joint, 2.0)

        assert pose.translation == pytest.approx((2.0, 0.0, 0.0), abs=1e-12)

    def test_the_midpoint_is_halfway(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(4.0, 0.0, 0.0))

        pose = model_joint_pose(joint, 2.0)

        assert pose.translation == pytest.approx((2.0, 0.0, 0.0), abs=1e-12)

    def test_a_diagonal_path_interpolates_every_axis(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(3.0, 4.0, 0.0))  # length 5

        pose = model_joint_pose(joint, 2.5)

        assert pose.translation == pytest.approx((1.5, 2.0, 0.0), abs=1e-9)

    def test_without_limits_the_value_is_clamped_to_the_path_length(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))

        pose = model_joint_pose(joint, 5.0)

        assert pose.is_clamped is True
        assert pose.translation == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)

    def test_a_negative_value_is_clamped_to_the_origin(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))

        pose = model_joint_pose(joint, -5.0)

        assert pose.is_clamped is True
        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)

    def test_it_never_rotates(self) -> None:
        pose = model_joint_pose(trajectory_joint(), 0.5)

        assert pose.rotation_angle_rad == 0.0


class TestEffectiveLimits:
    def test_explicit_limits_are_used_as_given(self) -> None:
        joint = axis_joint(limits=(-1.0, 1.0))

        assert effective_limits(joint) == (-1.0, 1.0)

    def test_an_axis_without_limits_defaults_to_one_full_turn(self) -> None:
        low, high = effective_limits(axis_joint(limits=None))

        assert (low, high) == pytest.approx((-math.pi, math.pi))

    def test_a_trajectory_without_limits_defaults_to_its_own_length(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(3.0, 0.0, 0.0), limits=None)

        low, high = effective_limits(joint)

        assert (low, high) == pytest.approx((0.0, 3.0))


class TestClamp:
    def test_a_value_in_range_passes_unchanged(self) -> None:
        assert clamp(0.0, 1.0, 0.5) == (0.5, False)

    def test_below_the_lower_limit_it_is_clamped(self) -> None:
        assert clamp(0.0, 1.0, -1.0) == (0.0, True)

    def test_above_the_upper_limit_it_is_clamped(self) -> None:
        assert clamp(0.0, 1.0, 2.0) == (1.0, True)

    @pytest.mark.parametrize("value", [0.0, 1.0])
    def test_a_value_exactly_on_the_limit_is_not_clamped(self, value: float) -> None:
        assert clamp(0.0, 1.0, value)[1] is False


class TestRestPose:
    def test_zero_within_limits_is_the_rest_value(self) -> None:
        joint = axis_joint(limits=(-1.0, 1.0))

        pose = rest_model_joint_pose(joint)

        assert pose.rotation_angle_rad == pytest.approx(0.0, abs=1e-12)

    def test_limits_excluding_zero_move_to_the_nearest_limit(self) -> None:
        joint = axis_joint(limits=(0.5, 1.0))

        pose = rest_model_joint_pose(joint)

        assert pose.rotation_angle_rad == pytest.approx(0.5, abs=1e-12)

    def test_limits_both_negative_move_to_the_upper(self) -> None:
        joint = axis_joint(limits=(-2.0, -1.0))

        pose = rest_model_joint_pose(joint)

        assert pose.rotation_angle_rad == pytest.approx(-1.0, abs=1e-12)


class TestConversion:
    def test_round_trip_preserves_an_axis(self) -> None:
        original = axis_joint(
            name="gate",
            variable="gate_angle",
            origin=(1.0, 2.0, 3.0),
            target=(1.0, 2.0, 4.0),
            limits=(0.0, math.pi / 2),
        )

        restored = to_model_joint(from_model_joint(original))

        assert restored.name == original.name
        assert restored.variable == original.variable
        assert restored.origin == pytest.approx(original.origin)
        assert restored.target == pytest.approx(original.target)
        assert restored.limits == pytest.approx(original.limits)

    def test_round_trip_preserves_a_trajectory(self) -> None:
        original = trajectory_joint(
            name="belt",
            variable="belt_position",
            origin=(0.0, 0.0, 0.0),
            target=(2.0, 0.0, 0.0),
            limits=(0.0, 1.5),
        )

        restored = to_model_joint(from_model_joint(original))

        assert restored.target == pytest.approx(original.target)
        assert restored.limits == pytest.approx(original.limits)

    def test_axis_limits_convert_through_degrees(self) -> None:
        display = from_model_joint(axis_joint(limits=(0.0, math.pi)))

        assert display.upper_limit == pytest.approx(180.0)

    def test_trajectory_limits_convert_through_millimetres(self) -> None:
        display = from_model_joint(trajectory_joint(limits=(0.0, 1.5)))

        assert display.upper_limit == pytest.approx(1500.0)

    def test_origin_converts_through_millimetres(self) -> None:
        display = from_model_joint(axis_joint(origin=(0.5, 0.0, 0.0)))

        assert display.origin_mm[0] == pytest.approx(500.0)

    def test_a_display_with_no_limits_converts_to_none(self) -> None:
        display = ModelJointDisplay(
            kind=ModelJointKind.AXIS,
            name="a",
            variable="a",
            target_mm=(0.0, 0.0, 1.0),
        )

        joint = to_model_joint(display)

        assert joint.limits is None


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
        assert _dot(perpendicular_to(direction), direction) == pytest.approx(0.0, abs=1e-6)

    def test_the_result_is_a_unit_vector(self) -> None:
        assert _length(perpendicular_to((0.0, 0.0, 1.0))) == pytest.approx(1.0, abs=1e-9)


class TestRotationOnto:
    @pytest.mark.parametrize(
        ("source", "target"),
        [
            ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
            ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
            ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            ((0.5773503, 0.5773503, 0.5773503), (0.0, 0.0, 1.0)),
        ],
    )
    def test_the_rotation_carries_source_onto_target(
        self, source: tuple[float, float, float], target: tuple[float, float, float]
    ) -> None:
        # Applied with the project's own Rodrigues helper, so this checks the
        # axis/angle pair really is the rotation it claims to be.
        axis, angle = rotation_onto(source, target)

        assert rotate_vec3(axis, angle, source) == pytest.approx(target, abs=1e-6)

    def test_an_already_aligned_pair_needs_no_rotation(self) -> None:
        _axis, angle = rotation_onto((0.0, 0.0, 1.0), (0.0, 0.0, 1.0))

        assert angle == pytest.approx(0.0, abs=1e-12)

    def test_an_opposite_pair_is_half_a_turn_about_a_perpendicular(self) -> None:
        source = (0.0, 0.0, 1.0)

        axis, angle = rotation_onto(source, (0.0, 0.0, -1.0))

        assert angle == pytest.approx(math.pi, abs=1e-12)
        assert _dot(axis, source) == pytest.approx(0.0, abs=1e-9)
        assert rotate_vec3(axis, angle, source) == pytest.approx((0.0, 0.0, -1.0), abs=1e-9)

    def test_the_axis_is_perpendicular_to_both(self) -> None:
        source, target = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)

        axis, _angle = rotation_onto(source, target)

        assert _dot(axis, source) == pytest.approx(0.0, abs=1e-9)
        assert _dot(axis, target) == pytest.approx(0.0, abs=1e-9)


class TestDirectionOf:
    def test_it_is_the_normalised_span(self) -> None:
        joint = trajectory_joint(origin=(1.0, 0.0, 0.0), target=(1.0, 0.0, 4.0))

        assert direction_of(joint) == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)

    def test_it_is_a_unit_vector_for_a_diagonal(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(3.0, 4.0, 0.0))

        assert _length(direction_of(joint)) == pytest.approx(1.0, abs=1e-12)


class TestJointValuePose:
    def test_an_axis_rotates_about_its_direction_without_translating(self) -> None:
        # The pivot is the frame origin, so unlike the old model-local pose
        # there is no compensating translation at all.
        joint = axis_joint(origin=(5.0, 0.0, 0.0), target=(5.0, 0.0, 1.0))

        pose = joint_value_pose(joint, math.pi / 2)

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)
        assert pose.rotation_axis == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)
        assert pose.rotation_angle_rad == pytest.approx(math.pi / 2, abs=1e-12)

    def test_a_trajectory_translates_along_its_direction_without_rotating(self) -> None:
        joint = trajectory_joint(origin=(5.0, 0.0, 0.0), target=(5.0, 0.0, 2.0))

        pose = joint_value_pose(joint, 1.5)

        assert pose.translation == pytest.approx((0.0, 0.0, 1.5), abs=1e-12)
        assert pose.rotation_angle_rad == 0.0

    def test_the_joints_own_origin_is_excluded(self) -> None:
        # The base frame already sits at the origin; including it here would
        # apply it twice.
        near = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(0.0, 0.0, 2.0))
        far = trajectory_joint(origin=(9.0, 9.0, 9.0), target=(9.0, 9.0, 11.0))

        assert joint_value_pose(near, 1.0).translation == pytest.approx(
            joint_value_pose(far, 1.0).translation, abs=1e-12
        )

    def test_a_diagonal_trajectory_moves_by_the_arc_length(self) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(3.0, 4.0, 0.0))  # length 5

        pose = joint_value_pose(joint, 2.5)

        assert pose.translation == pytest.approx((1.5, 2.0, 0.0), abs=1e-9)

    def test_clamping_still_applies(self) -> None:
        joint = axis_joint(limits=(0.0, math.pi / 2))

        pose = joint_value_pose(joint, 10.0)

        assert pose.rotation_angle_rad == pytest.approx(math.pi / 2, abs=1e-12)
        assert pose.is_clamped is True


class TestAnchor:
    def test_a_zero_direction_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="no length"):
            Anchor(direction=(0.0, 0.0, 0.0))

    def test_the_default_anchor_is_the_model_origin(self) -> None:
        assert Anchor().point == (0.0, 0.0, 0.0)


def _seated(anchor: Anchor, joint_direction: tuple[float, float, float]) -> tuple[float, ...]:
    """Where the anchor point ends up once its own pose is applied — it must be
    the joint frame's origin."""
    pose = anchor_pose(anchor, joint_direction)
    rotated = rotate_vec3(pose.rotation_axis, pose.rotation_angle_rad, anchor.point)
    return tuple(r + t for r, t in zip(rotated, pose.translation, strict=True))


class TestAnchorPose:
    def test_an_aligned_anchor_at_the_origin_is_the_identity(self) -> None:
        pose = anchor_pose(Anchor(), (0.0, 0.0, 1.0))

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)
        assert pose.rotation_angle_rad == pytest.approx(0.0, abs=1e-12)

    def test_the_anchor_point_lands_on_the_frame_origin(self) -> None:
        # The whole purpose: whatever point the user picked ends up ON the joint.
        anchor = Anchor(point=(0.0, 0.0, 0.5), direction=(0.0, 0.0, 1.0))

        assert _seated(anchor, (0.0, 0.0, 1.0)) == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)

    def test_it_lands_on_the_origin_even_when_the_model_must_turn(self) -> None:
        anchor = Anchor(point=(0.3, 0.0, 0.5), direction=(1.0, 0.0, 0.0))

        assert _seated(anchor, (0.0, 0.0, 1.0)) == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)

    def test_the_anchor_direction_ends_up_along_the_joints(self) -> None:
        anchor = Anchor(point=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0))
        joint_direction = (0.0, 1.0, 0.0)

        pose = anchor_pose(anchor, joint_direction)

        turned = rotate_vec3(pose.rotation_axis, pose.rotation_angle_rad, anchor.direction)
        assert turned == pytest.approx(joint_direction, abs=1e-9)

    def test_an_unnormalised_joint_direction_is_accepted(self) -> None:
        # A direction read off two picked points is rarely unit length.
        pose = anchor_pose(Anchor(direction=(1.0, 0.0, 0.0)), (0.0, 0.0, 7.0))

        turned = rotate_vec3(pose.rotation_axis, pose.rotation_angle_rad, (1.0, 0.0, 0.0))
        assert turned == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)

    def test_an_unnormalised_anchor_direction_is_accepted(self) -> None:
        pose = anchor_pose(Anchor(direction=(4.0, 0.0, 0.0)), (0.0, 0.0, 1.0))

        turned = rotate_vec3(pose.rotation_axis, pose.rotation_angle_rad, (1.0, 0.0, 0.0))
        assert turned == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)


class TestAnchorConversion:
    def test_the_point_converts_through_millimetres(self) -> None:
        display = from_anchor(Anchor(point=(0.25, 0.0, 0.0)))

        assert display.point_mm[0] == pytest.approx(250.0)

    def test_the_direction_is_left_unscaled(self) -> None:
        display = from_anchor(Anchor(direction=(0.0, 1.0, 0.0)))

        assert display.direction == (0.0, 1.0, 0.0)

    def test_round_trip_preserves_an_anchor(self) -> None:
        original = Anchor(point=(0.1, -0.2, 0.3), direction=(0.0, 1.0, 0.0))

        restored = to_anchor(from_anchor(original))

        assert restored.point == pytest.approx(original.point)
        assert restored.direction == pytest.approx(original.direction)

    def test_the_display_default_is_the_internal_default(self) -> None:
        assert to_anchor(AnchorDisplay()) == Anchor()


class TestValueScale:
    def test_an_axis_is_driven_in_degrees(self) -> None:
        assert value_scale(ModelJointKind.AXIS) == pytest.approx(DEG_TO_RAD)

    def test_a_trajectory_is_driven_in_millimetres(self) -> None:
        assert value_scale(ModelJointKind.TRAJECTORY) == pytest.approx(MM_TO_M)

    def test_it_is_the_scale_the_limits_already_use(self) -> None:
        # One rule, three users (limits both ways, and the saved value) — this
        # pins that they agree rather than drifting apart.
        display = from_model_joint(axis_joint(limits=(0.0, math.pi)))

        assert display.upper_limit == pytest.approx(math.pi / value_scale(ModelJointKind.AXIS))


class TestAlignment:
    def test_a_joint_starts_tangential_at_its_origin(self) -> None:
        # Identity is the default the user asked for: the start of the
        # trajectory, pointing the way it runs.
        assert axis_joint().alignment == IDENTITY_PLACEMENT

    def test_an_alignment_can_be_given(self) -> None:
        joint = axis_joint(alignment=Transform(xyz=(0.0, 0.0, 0.25)))

        assert joint.alignment.xyz == (0.0, 0.0, 0.25)

    def test_it_round_trips_through_the_display_boundary(self) -> None:
        original = trajectory_joint(
            alignment=Transform(xyz=(0.1, -0.2, 0.3), rpy=(0.0, 0.0, math.pi / 2))
        )

        restored = to_model_joint(from_model_joint(original))

        assert restored.alignment.xyz == pytest.approx(original.alignment.xyz)
        assert restored.alignment.rpy == pytest.approx(original.alignment.rpy)

    def test_the_display_shows_it_in_millimetres_and_degrees(self) -> None:
        display = from_model_joint(
            trajectory_joint(alignment=Transform(xyz=(0.25, 0.0, 0.0), rpy=(0.0, 0.0, math.pi / 2)))
        )

        assert display.alignment.x_mm == pytest.approx(250.0)
        assert display.alignment.rotate_z_deg == pytest.approx(90.0)

    def test_a_default_display_converts_to_an_identity_alignment(self) -> None:
        joint = to_model_joint(
            ModelJointDisplay(
                name="a",
                variable="a",
                kind=ModelJointKind.AXIS,
                target_mm=(0.0, 0.0, 1000.0),
            )
        )

        assert joint.alignment == IDENTITY_PLACEMENT

    def test_the_alignment_does_not_disturb_the_motion(self) -> None:
        # The value pose is the joint's motion alone; the frame is a separate
        # node, so an alignment must not leak into it.
        plain = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(0.0, 0.0, 2.0))
        aligned = trajectory_joint(
            origin=(0.0, 0.0, 0.0),
            target=(0.0, 0.0, 2.0),
            alignment=Transform(xyz=(9.0, 9.0, 9.0), rpy=(1.0, 1.0, 1.0)),
        )

        assert joint_value_pose(plain, 1.0).translation == pytest.approx(
            joint_value_pose(aligned, 1.0).translation
        )


class TestAnchorPoseDefault:
    def test_it_aligns_onto_plus_z_by_default(self) -> None:
        # What viz passes: the tangential frame already points along the joint,
        # so the anchor only lines up with the frame it sits in.
        anchor = Anchor(direction=(1.0, 0.0, 0.0))

        pose = anchor_pose(anchor)

        turned = rotate_vec3(pose.rotation_axis, pose.rotation_angle_rad, anchor.direction)
        assert turned == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)

    def test_the_default_matches_passing_plus_z_explicitly(self) -> None:
        anchor = Anchor(point=(0.3, 0.0, 0.1), direction=(0.0, 1.0, 0.0))

        implicit = anchor_pose(anchor)
        explicit = anchor_pose(anchor, (0.0, 0.0, 1.0))

        assert implicit.translation == pytest.approx(explicit.translation)
        assert implicit.rotation_angle_rad == pytest.approx(explicit.rotation_angle_rad)


class TestAxisByDirection:
    """An axis is a centre point plus a direction, not two points.

    Only the direction matters: `(0,0,1)` and `(0,0,100)` describe the same axis,
    which is what makes the field typeable by hand.
    """

    def test_the_default_direction_is_z(self) -> None:
        joint = ModelJoint(name="turn", kind=ModelJointKind.AXIS, variable="v")

        assert direction_of(joint) == pytest.approx((0.0, 0.0, 1.0))

    def test_the_direction_is_normalised(self) -> None:
        joint = ModelJoint(
            name="turn", kind=ModelJointKind.AXIS, variable="v", direction=(0.0, 0.0, 100.0)
        )

        assert direction_of(joint) == pytest.approx((0.0, 0.0, 1.0))

    def test_magnitude_makes_no_difference(self) -> None:
        small = ModelJoint(
            name="a", kind=ModelJointKind.AXIS, variable="v", direction=(1.0, 1.0, 1.0)
        )
        large = ModelJoint(
            name="b", kind=ModelJointKind.AXIS, variable="v", direction=(100.0, 100.0, 100.0)
        )

        assert direction_of(small) == pytest.approx(direction_of(large))

    def test_the_same_pose_comes_out_regardless_of_magnitude(self) -> None:
        small = ModelJoint(
            name="a", kind=ModelJointKind.AXIS, variable="v", direction=(1.0, 1.0, 1.0)
        )
        large = ModelJoint(
            name="b", kind=ModelJointKind.AXIS, variable="v", direction=(100.0, 100.0, 100.0)
        )

        first = joint_value_pose(small, 0.5)
        second = joint_value_pose(large, 0.5)

        assert first.rotation_axis == pytest.approx(second.rotation_axis)
        assert first.rotation_angle_rad == pytest.approx(second.rotation_angle_rad)

    def test_the_centre_point_is_the_origin(self) -> None:
        joint = ModelJoint(
            name="turn", kind=ModelJointKind.AXIS, variable="v", origin=(1.0, 2.0, 3.0)
        )

        assert joint.origin == (1.0, 2.0, 3.0)

    def test_an_axis_needs_no_second_point(self) -> None:
        # The old rule rejected origin == target, which an axis defined this way
        # leaves equal by default. It must not raise any more.
        joint = ModelJoint(name="turn", kind=ModelJointKind.AXIS, variable="v")

        assert joint.origin == joint.target

    def test_a_zero_direction_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="direction"):
            ModelJoint(
                name="turn", kind=ModelJointKind.AXIS, variable="v", direction=(0.0, 0.0, 0.0)
            )

    def test_a_trajectory_still_needs_two_points(self) -> None:
        with pytest.raises(ConfigError, match="same point"):
            ModelJoint(
                name="rail",
                kind=ModelJointKind.TRAJECTORY,
                variable="v",
                origin=(1.0, 0.0, 0.0),
                target=(1.0, 0.0, 0.0),
            )

    def test_a_trajectory_still_derives_its_direction_from_its_points(self) -> None:
        joint = ModelJoint(
            name="rail",
            kind=ModelJointKind.TRAJECTORY,
            variable="v",
            origin=(1.0, 0.0, 0.0),
            target=(5.0, 0.0, 0.0),
        )

        assert direction_of(joint) == pytest.approx((1.0, 0.0, 0.0))

    def test_a_trajectory_ignores_the_direction_field(self) -> None:
        joint = ModelJoint(
            name="rail",
            kind=ModelJointKind.TRAJECTORY,
            variable="v",
            origin=(0.0, 0.0, 0.0),
            target=(1.0, 0.0, 0.0),
            direction=(0.0, 1.0, 0.0),
        )

        assert direction_of(joint) == pytest.approx((1.0, 0.0, 0.0))


class TestInitialAngle:
    def test_it_defaults_to_zero(self) -> None:
        joint = ModelJoint(name="turn", kind=ModelJointKind.AXIS, variable="v")

        assert joint_value_pose(joint, 0.0).rotation_angle_rad == pytest.approx(0.0)

    def test_it_offsets_the_value(self) -> None:
        joint = ModelJoint(
            name="turn",
            kind=ModelJointKind.AXIS,
            variable="v",
            initial_angle_rad=math.pi / 2.0,
        )

        assert joint_value_pose(joint, 0.0).rotation_angle_rad == pytest.approx(math.pi / 2.0)

    def test_value_zero_lands_where_the_old_value_used_to(self) -> None:
        # An init rotation of 90 degrees means "value 0 points where value 90
        # pointed before" — that is what "the angle that defines 0" means.
        plain = ModelJoint(name="a", kind=ModelJointKind.AXIS, variable="v")
        offset = ModelJoint(
            name="b",
            kind=ModelJointKind.AXIS,
            variable="v",
            initial_angle_rad=math.pi / 2.0,
        )

        assert joint_value_pose(offset, 0.0).rotation_angle_rad == pytest.approx(
            joint_value_pose(plain, math.pi / 2.0).rotation_angle_rad
        )

    def test_it_adds_to_a_driven_value(self) -> None:
        joint = ModelJoint(
            name="turn",
            kind=ModelJointKind.AXIS,
            variable="v",
            initial_angle_rad=math.pi / 2.0,
        )

        assert joint_value_pose(joint, math.pi / 2.0).rotation_angle_rad == pytest.approx(math.pi)

    def test_limits_still_clamp_the_value_not_the_offset(self) -> None:
        # The offset is where zero *is*; it must not be squeezed by the limits,
        # or a joint limited to +/-10 degrees could never sit at its own zero.
        joint = ModelJoint(
            name="turn",
            kind=ModelJointKind.AXIS,
            variable="v",
            limits=(0.0, 0.1),
            initial_angle_rad=math.pi / 2.0,
        )

        pose = joint_value_pose(joint, 5.0)

        assert pose.is_clamped is True
        assert pose.rotation_angle_rad == pytest.approx(math.pi / 2.0 + 0.1)

    def test_a_trajectory_is_unaffected(self) -> None:
        joint = ModelJoint(
            name="rail",
            kind=ModelJointKind.TRAJECTORY,
            variable="v",
            origin=(0.0, 0.0, 0.0),
            target=(1.0, 0.0, 0.0),
            initial_angle_rad=math.pi / 2.0,
        )

        assert joint_value_pose(joint, 0.5).rotation_angle_rad == pytest.approx(0.0)


class TestAxisDisplayConversion:
    def test_the_direction_is_unitless_in_both_directions(self) -> None:
        display = ModelJointDisplay(
            name="turn",
            kind=ModelJointKind.AXIS,
            variable="v",
            direction=(0.0, 0.0, 2.0),
        )

        assert to_model_joint(display).direction == pytest.approx((0.0, 0.0, 2.0))

    def test_the_initial_angle_is_in_degrees(self) -> None:
        display = ModelJointDisplay(
            name="turn", kind=ModelJointKind.AXIS, variable="v", initial_angle_deg=90.0
        )

        assert to_model_joint(display).initial_angle_rad == pytest.approx(math.pi / 2.0)

    def test_the_initial_angle_comes_back_in_degrees(self) -> None:
        joint = ModelJoint(
            name="turn",
            kind=ModelJointKind.AXIS,
            variable="v",
            initial_angle_rad=math.pi / 2.0,
        )

        assert from_model_joint(joint).initial_angle_deg == pytest.approx(90.0)

    def test_the_direction_survives_a_round_trip(self) -> None:
        joint = ModelJoint(
            name="turn", kind=ModelJointKind.AXIS, variable="v", direction=(1.0, 2.0, 3.0)
        )

        restored = to_model_joint(from_model_joint(joint))

        assert restored.direction == pytest.approx((1.0, 2.0, 3.0))

    def test_the_centre_point_is_still_in_millimetres(self) -> None:
        joint = ModelJoint(
            name="turn", kind=ModelJointKind.AXIS, variable="v", origin=(1.5, 0.0, 0.0)
        )

        assert from_model_joint(joint).origin_mm[0] == pytest.approx(1500.0)
