"""Kinematics: a signal value → the pose of a joint.

The project's reference module. A pure function: no state, no memory of the previous
frame, no dependency outside the stdlib. If you are writing new domain logic,
imitate this.

Matrices are deliberately **not** computed here. The result is a `JointPose`
(translation + axis and angle), which `viz/` translates into `NodePath.setPos()` /
`setQuat()`. That way the domain needs neither numpy nor linear algebra, and is
tested by comparing numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from pssim.domain.machine import Joint, JointType, Vec3

_ZERO: Vec3 = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class JointPose:
    """The pose of a joint relative to its parent, including the fixed `joint.origin` offset.

    `rotation_angle_rad` is 0 for translational joints; `translation` is the zero
    vector for rotational ones. `is_clamped` says the input value was outside the
    limits — the scene should show that, not ignore it.
    """

    translation: Vec3
    rotation_axis: Vec3
    rotation_angle_rad: float
    is_clamped: bool = False


def clamp_to_limits(joint: Joint, value: float) -> tuple[float, bool]:
    """Clamp a value to the joint's limits. Returns `(value, was_clamped)`.

    A value outside the limits is not an error — the PLC may send anything and the
    scene must not fall over because of it. But it is information the user wants to
    see.
    """
    if joint.limits is None:
        return value, False

    low, high = joint.limits
    if value < low:
        return low, True
    if value > high:
        return high, True
    return value, False


def joint_pose(joint: Joint, value: float) -> JointPose:
    """Translate a signal value into the pose of a joint relative to its parent.

    `value` is already in internal units (metres / radians) — the conversion from
    PLC units happens in `config.binding`, not here.
    """
    if joint.type is JointType.FIXED:
        return JointPose(
            translation=joint.origin.xyz,
            rotation_axis=joint.axis,
            rotation_angle_rad=0.0,
        )

    clamped, is_clamped = clamp_to_limits(joint, value)

    if joint.type is JointType.PRISMATIC:
        offset_x, offset_y, offset_z = joint.origin.xyz
        axis_x, axis_y, axis_z = joint.axis
        return JointPose(
            translation=(
                offset_x + axis_x * clamped,
                offset_y + axis_y * clamped,
                offset_z + axis_z * clamped,
            ),
            rotation_axis=joint.axis,
            rotation_angle_rad=0.0,
            is_clamped=is_clamped,
        )

    return JointPose(
        translation=joint.origin.xyz,
        rotation_axis=joint.axis,
        rotation_angle_rad=clamped,
        is_clamped=is_clamped,
    )


def rest_pose(joint: Joint) -> JointPose:
    """The pose of a joint when no value has arrived for it yet.

    Not zero: if the joint has limits that do not contain zero, zero would place the
    part outside the physically possible range. The nearest value within the limits
    is used.
    """
    if joint.type is JointType.FIXED or joint.limits is None:
        return joint_pose(joint, 0.0)

    low, high = joint.limits
    initial = min(max(0.0, low), high)
    return joint_pose(joint, initial)


def identity_pose() -> JointPose:
    """The neutral pose — usable as a fallback when the joint is not known."""
    return JointPose(translation=_ZERO, rotation_axis=(0.0, 0.0, 1.0), rotation_angle_rad=0.0)
