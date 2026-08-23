"""Model joints: axes and trajectories attached to a whole loaded model.

Unlike `domain/machine.py` + `domain/kinematics.py` (a kinematic tree *inside* one CAD
assembly, driven by the PLC), this is a much smaller thing: an axis or a straight travel
path standing in the scene, carrying whole loaded models that are **bound** to it. A joint
may also be carried by another joint, which is what lets a rail carry a rotary head. The
two subsystems are deliberately not unified — see the accompanying plan for why.

The module is pure (stdlib only), the same treatment `domain/kinematics.py` and
`domain/sensors.py` get, so the geometry is testable without a window and without a PLC.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Transform, Vec3
from pssim.domain.placement import (
    IDENTITY_PLACEMENT,
    PlacementDisplay,
    from_transform,
    to_transform,
)
from pssim.domain.units import DEG_TO_RAD, MM_TO_M

_ZERO: Final[Vec3] = (0.0, 0.0, 0.0)

#: Below this, two unit vectors count as parallel (or antiparallel) and the
#: rotation between them has no well-defined axis. `1e-9` on a sine is about
#: 2e-5 degrees — far finer than anything enterable, and safely above the
#: float noise of a normalised cross product.
_ALIGNMENT_TOLERANCE: Final = 1e-9


class ModelJointKind(StrEnum):
    """The kind of movement a joint offers."""

    AXIS = "axis"
    """Rotation about the line through `origin` along `direction`."""

    TRAJECTORY = "trajectory"
    """Translation along the straight segment from `origin` to `target`."""


@dataclass(frozen=True, slots=True)
class ModelJoint:
    """One axis or trajectory, in the frame of whatever carries it — the scene for
    a top-level joint, the parent's moving frame for a carried one.

    `variable` is a forward-looking label only — nothing drives it yet; it exists so a
    future communication link (OPC UA/TCP-IP) has a name to bind to.

    The two kinds are defined differently, because they are different shapes.

    An **axis** is a *centre point* (`origin`) and a *direction* (`direction`), plus
    an `initial_angle_rad` fixing where value 0 points. Only the direction of
    `direction` matters — it is normalised on use, so `(0,0,1)` and `(0,0,100)`
    describe the same axis. That is deliberate: an axis has no length, and asking
    for a second *point* on it invited numbers that looked meaningful but were not.

    A **trajectory** is a segment from `origin` to `target`, and its direction is
    derived as `normalize(target - origin)`. Here the two points both mean
    something: the far end is where the travel stops.

    So `target` and `alignment` are read for a trajectory only, and `direction` and
    `initial_angle_rad` for an axis only — the same "fields only some kinds use"
    shape `domain.machine.Joint` and `domain.sensors.Sensor` already have.
    """

    name: str
    kind: ModelJointKind
    variable: str
    origin: Vec3 = _ZERO
    """The centre point for an `AXIS`, the start of the path for a `TRAJECTORY`."""

    target: Vec3 = _ZERO
    """The far end of the path. `TRAJECTORY` only."""

    direction: Vec3 = (0.0, 0.0, 1.0)
    """Which way the axis points, magnitude irrelevant. `AXIS` only."""

    initial_angle_rad: float = 0.0
    """The angle value 0 corresponds to. `AXIS` only.

    Part of the *motion*, not of the mounting frame: it shifts where zero is,
    which is a different thing from where a bound model sits. Limits clamp the
    value, never this offset — otherwise a joint limited to a few degrees could
    not sit at its own zero.
    """

    limits: tuple[float, float] | None = None
    """Radians (`AXIS`) or metres of travel (`TRAJECTORY`); `None` means unrestricted."""

    alignment: Transform = IDENTITY_PLACEMENT
    """The **initial coordinate system** a bound model aligns to, relative to
    the joint's own tangential frame. `TRAJECTORY` only — an axis uses
    `initial_angle_rad` instead, which is the one degree of freedom that
    actually needed naming there.

    Identity — the default — means "at the joint's origin, with `+Z` along the
    joint": for a trajectory, the start of the path pointing the way it runs;
    for an axis, on the axis line pointing along it. Offsetting or rotating from
    there decides exactly how a bound model sits, including the roll about the
    joint that the anchor alone leaves arbitrary.

    Its own axes are the *tangential frame's*, not the world's — so `z` moves
    along the joint, not upwards. That is what "relative to the frame the joint
    defines" means, and the UI has to label it so.
    """

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("a joint must have a non-empty name")

        if not self.variable:
            raise ConfigError(f"joint {self.name!r}: the variable name must not be empty")

        # Validated per kind, because the kinds are defined differently: an axis
        # has one point and a direction, a trajectory has two points. Neither
        # degenerate case is normalised away — silently picking a direction would
        # hide the mistake, the same reasoning domain.sensors.Sensor's beam uses.
        if self.kind is ModelJointKind.AXIS:
            if not any(self.direction):
                raise ConfigError(
                    f"joint {self.name!r}: the direction is zero "
                    "(an axis needs a direction to turn about)"
                )
        elif self.origin == self.target:
            raise ConfigError(
                f"joint {self.name!r}: origin and target are the same point "
                "(a trajectory needs two different points)"
            )

        if self.limits is not None:
            low, high = self.limits
            if low > high:
                raise ConfigError(
                    f"joint {self.name!r}: the lower limit {low} is greater than the upper {high}"
                )


@dataclass(frozen=True, slots=True)
class ModelJointPose:
    """The pose a joint's frame should have for a given value.

    Structurally identical to `domain.kinematics.JointPose` but a distinct type — the
    two joint subsystems are deliberately not entangled with each other.
    """

    translation: Vec3
    rotation_axis: Vec3
    rotation_angle_rad: float
    is_clamped: bool = False


def rotate_vec3(axis: Vec3, angle_rad: float, point: Vec3) -> Vec3:
    """Rotate `point` by `angle_rad` about the line through the local origin in
    direction `axis` (a unit vector) — Rodrigues' rotation formula.
    """
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    one_minus_cos = 1.0 - cos_a
    ax, ay, az = axis
    px, py, pz = point
    dot = ax * px + ay * py + az * pz
    cross_x = ay * pz - az * py
    cross_y = az * px - ax * pz
    cross_z = ax * py - ay * px
    return (
        px * cos_a + cross_x * sin_a + ax * dot * one_minus_cos,
        py * cos_a + cross_y * sin_a + ay * dot * one_minus_cos,
        pz * cos_a + cross_z * sin_a + az * dot * one_minus_cos,
    )


def perpendicular_to(direction: Vec3) -> Vec3:
    """Any unit vector perpendicular to `direction` (itself a unit vector).

    Crosses with whichever coordinate axis `direction` leans on least, so the
    cross product is never near-zero. Lives here rather than in `viz/` because
    `rotation_onto` needs it for the antiparallel case, and `domain/` cannot
    import `viz/`.
    """
    ax, ay, az = direction
    if abs(ax) <= abs(ay) and abs(ax) <= abs(az):
        reference: Vec3 = (1.0, 0.0, 0.0)
    elif abs(ay) <= abs(az):
        reference = (0.0, 1.0, 0.0)
    else:
        reference = (0.0, 0.0, 1.0)

    rx, ry, rz = reference
    cx, cy, cz = ay * rz - az * ry, az * rx - ax * rz, ax * ry - ay * rx
    length = math.sqrt(cx * cx + cy * cy + cz * cz)
    return (cx / length, cy / length, cz / length)


def rotation_onto(source: Vec3, target: Vec3) -> tuple[Vec3, float]:
    """The axis and angle of the shortest rotation carrying unit `source` onto
    unit `target`.

    `atan2(|source x target|, source . target)` rather than `acos(dot)`: the
    latter loses all precision near 0 and pi, which is exactly where an anchor
    that is nearly aligned with its joint sits.

    Both degenerate cases are real and handled: already aligned gives a zero
    angle about an arbitrary axis, and exactly opposite gives half a turn about
    *some* perpendicular — there is no unique choice there, and any of them is
    correct.
    """
    sx, sy, sz = source
    tx, ty, tz = target
    dot = sx * tx + sy * ty + sz * tz
    cx, cy, cz = sy * tz - sz * ty, sz * tx - sx * tz, sx * ty - sy * tx
    sine = math.sqrt(cx * cx + cy * cy + cz * cz)

    if sine < _ALIGNMENT_TOLERANCE:
        if dot > 0.0:
            return (0.0, 0.0, 1.0), 0.0
        return perpendicular_to(source), math.pi

    return (cx / sine, cy / sine, cz / sine), math.atan2(sine, dot)


def direction_of(joint: ModelJoint) -> Vec3:
    """The unit vector the joint moves along or turns about.

    An axis normalises its own `direction`; a trajectory derives it from its two
    points. Both are safe: `__post_init__` rejects a zero direction and a
    zero-length path respectively.
    """
    if joint.kind is ModelJointKind.AXIS:
        dx, dy, dz = joint.direction
    else:
        ox, oy, oz = joint.origin
        tx, ty, tz = joint.target
        dx, dy, dz = tx - ox, ty - oy, tz - oz
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    return (dx / length, dy / length, dz / length)


def _path_length(joint: ModelJoint) -> float:
    ox, oy, oz = joint.origin
    tx, ty, tz = joint.target
    return math.sqrt((tx - ox) ** 2 + (ty - oy) ** 2 + (tz - oz) ** 2)


def clamp(low: float, high: float, value: float) -> tuple[float, bool]:
    """Clamp `value` into `[low, high]`. Returns `(value, was_clamped)`.

    Same contract as `domain.kinematics.clamp_to_limits`, but takes the bounds
    directly rather than a joint — `ModelJoint` has no single type to hand it the
    way `Joint` does, since "unrestricted" means a different fallback range per
    kind (see `effective_limits`).
    """
    if value < low:
        return low, True
    if value > high:
        return high, True
    return value, False


def effective_limits(joint: ModelJoint) -> tuple[float, float]:
    """The range a value is clamped into.

    `joint.limits` if set; otherwise a full turn either way for `AXIS`, or the
    path's own length for `TRAJECTORY` — arc length beyond the path is undefined,
    so "unrestricted" cannot mean unbounded the way it does for a rotation.
    Used by the pose calculation, `rest_model_joint_pose`, and the value dialog's
    slider range alike — one function, not reimplemented per caller.
    """
    if joint.limits is not None:
        return joint.limits
    if joint.kind is ModelJointKind.AXIS:
        return (-math.pi, math.pi)
    return (0.0, _path_length(joint))


def _axis_pose(joint: ModelJoint, value: float) -> ModelJointPose:
    """Rotating a rigid body by `angle` about a line through `origin` moves its own
    local-origin point to `origin - R(axis, angle) @ origin` — the exact identity
    that makes an off-origin pivot representable as a plain (translation, axis,
    angle) pose, matching how `NodePath.setPos()`/`setQuat()` already compose.
    """
    low, high = effective_limits(joint)
    clamped, is_clamped = clamp(low, high, value)
    axis = direction_of(joint)
    rotated_origin = rotate_vec3(axis, clamped, joint.origin)
    ox, oy, oz = joint.origin
    rx, ry, rz = rotated_origin
    return ModelJointPose(
        translation=(ox - rx, oy - ry, oz - rz),
        rotation_axis=axis,
        rotation_angle_rad=clamped,
        is_clamped=is_clamped,
    )


def _trajectory_pose(joint: ModelJoint, value: float) -> ModelJointPose:
    low, high = effective_limits(joint)
    clamped, is_clamped = clamp(low, high, value)
    t = clamped / _path_length(joint)
    ox, oy, oz = joint.origin
    tx, ty, tz = joint.target
    return ModelJointPose(
        translation=(ox + t * (tx - ox), oy + t * (ty - oy), oz + t * (tz - oz)),
        rotation_axis=(0.0, 0.0, 1.0),
        rotation_angle_rad=0.0,
        is_clamped=is_clamped,
    )


def model_joint_pose(joint: ModelJoint, value: float) -> ModelJointPose:
    """Translate a live value into the joint's pose. Dispatches on `joint.kind`, so
    the evaluation loop in `viz/` never branches on kind itself — mirrors
    `domain.kinematics.joint_pose`'s single-entry-point shape."""
    if joint.kind is ModelJointKind.AXIS:
        return _axis_pose(joint, value)
    return _trajectory_pose(joint, value)


def joint_value_pose(joint: ModelJoint, value: float) -> ModelJointPose:
    """The joint's *motion* alone, for the moving half of its frame.

    The joint's own `origin` is deliberately **not** included: the frame this
    pose is applied to is already sitting at the origin, so a rotation here is
    simply about the joint's direction, and a translation simply along it. That
    is why this needs none of the pivot compensation `_axis_pose` does — the
    pivot *is* the frame origin.

    AXIS: rotation about `direction_of(joint)` by the clamped value, no
    translation. TRAJECTORY: translation `direction * clamped`, no rotation.
    """
    low, high = effective_limits(joint)
    clamped, is_clamped = clamp(low, high, value)
    axis = direction_of(joint)

    if joint.kind is ModelJointKind.AXIS:
        return ModelJointPose(
            translation=_ZERO,
            rotation_axis=axis,
            rotation_angle_rad=joint.initial_angle_rad + clamped,
            is_clamped=is_clamped,
        )

    return ModelJointPose(
        translation=(axis[0] * clamped, axis[1] * clamped, axis[2] * clamped),
        rotation_axis=axis,
        rotation_angle_rad=0.0,
        is_clamped=is_clamped,
    )


@dataclass(frozen=True, slots=True)
class Anchor:
    """A model's contact point and its direction, in the model's **own** local
    frame.

    This is what couples a model to a joint: the point is what sits on the
    trajectory or on the axis line, and the direction is what gets lined up
    with the joint's own. Without it a model could only ever be attached by its
    CAD origin, which is wherever the designer happened to leave it.
    """

    point: Vec3 = _ZERO
    direction: Vec3 = (0.0, 0.0, 1.0)

    def __post_init__(self) -> None:
        length_sq = sum(component * component for component in self.direction)
        if length_sq < _ALIGNMENT_TOLERANCE:
            raise ConfigError(
                f"anchor direction {self.direction} has no length; "
                "it must point somewhere for the model to be oriented by it"
            )


def anchor_pose(anchor: Anchor, joint_direction: Vec3 = (0.0, 0.0, 1.0)) -> ModelJointPose:
    """The transform that seats a model on a joint by its anchor.

    Puts `anchor.point` at the frame's origin and turns `anchor.direction` onto
    `joint_direction`. Rotating first moves the anchor point to `R · point`, so
    the translation that brings it back to the origin is `-R · point` — this is
    the one place `rotate_vec3` is still needed.

    The default `+Z` is what `viz/` passes: the joint's own tangential frame
    already points that way, so the anchor only has to line up with the frame it
    is being seated in rather than re-deriving the joint's direction.

    `joint_direction` need not be normalised by the caller; it is normalised
    here, since a direction read straight off two picked points rarely is.
    """
    length = math.sqrt(sum(component * component for component in joint_direction))
    unit_joint = (
        joint_direction[0] / length,
        joint_direction[1] / length,
        joint_direction[2] / length,
    )
    unit_anchor_length = math.sqrt(sum(component * component for component in anchor.direction))
    unit_anchor = (
        anchor.direction[0] / unit_anchor_length,
        anchor.direction[1] / unit_anchor_length,
        anchor.direction[2] / unit_anchor_length,
    )

    axis, angle = rotation_onto(unit_anchor, unit_joint)
    rotated = rotate_vec3(axis, angle, anchor.point)
    return ModelJointPose(
        translation=(-rotated[0], -rotated[1], -rotated[2]),
        rotation_axis=axis,
        rotation_angle_rad=angle,
    )


def rest_model_joint_pose(joint: ModelJoint) -> ModelJointPose:
    """The pose before any value has been set.

    Not zero: if the joint's limits do not contain zero, zero would place it
    outside the physically possible range — the nearest value within the limits is
    used instead, mirroring `domain.kinematics.rest_pose` exactly.
    """
    low, high = effective_limits(joint)
    initial = min(max(0.0, low), high)
    return model_joint_pose(joint, initial)


def value_scale(kind: ModelJointKind) -> float:
    """What multiplies a **display** number to get an internal one for `kind`.

    An axis is driven in degrees and stored in radians; a trajectory is driven in
    millimetres and stored in metres. The same rule governs a joint's limits and
    its live value, so it lives in one place rather than being spelled out at
    each of them.
    """
    return DEG_TO_RAD if kind is ModelJointKind.AXIS else MM_TO_M


@dataclass(frozen=True, slots=True)
class ModelJointDisplay:
    """A joint in the units the user sees: **millimetres and degrees**. Same role as
    `domain.sensors.SensorDisplay`.

    `lower_limit`/`upper_limit` are degrees for `AXIS`, millimetres for `TRAJECTORY`;
    either being `None` means unrestricted (a dialog gates both fields as a pair, so
    "one set, one not" is not a state that arises from normal use).
    """

    name: str = ""
    kind: ModelJointKind = ModelJointKind.AXIS
    variable: str = ""
    origin_mm: Vec3 = _ZERO
    target_mm: Vec3 = _ZERO
    direction: Vec3 = (0.0, 0.0, 1.0)
    """Unitless, unlike everything else here — it is a direction, and scaling it
    would mean nothing. Same exception `AnchorDisplay.direction` makes."""

    initial_angle_deg: float = 0.0
    lower_limit: float | None = None
    upper_limit: float | None = None

    alignment: PlacementDisplay = PlacementDisplay()
    """The initial coordinate system in the units the user types: millimetres
    and degrees. Reuses `PlacementDisplay` rather than six more fields — it is
    exactly a placement, just relative to the joint's tangential frame."""


def to_model_joint(display: ModelJointDisplay) -> ModelJoint:
    """Convert the entered values into an internal joint (metres, radians)."""
    limits = None
    if display.lower_limit is not None and display.upper_limit is not None:
        scale = value_scale(display.kind)
        limits = (display.lower_limit * scale, display.upper_limit * scale)

    ox, oy, oz = display.origin_mm
    tx, ty, tz = display.target_mm
    return ModelJoint(
        name=display.name,
        kind=display.kind,
        variable=display.variable,
        origin=(ox * MM_TO_M, oy * MM_TO_M, oz * MM_TO_M),
        target=(tx * MM_TO_M, ty * MM_TO_M, tz * MM_TO_M),
        direction=display.direction,
        initial_angle_rad=display.initial_angle_deg * DEG_TO_RAD,
        limits=limits,
        alignment=to_transform(display.alignment),
    )


def from_model_joint(joint: ModelJoint) -> ModelJointDisplay:
    """Convert an internal joint back into what a dialog shows."""
    lower_limit = upper_limit = None
    if joint.limits is not None:
        low, high = joint.limits
        scale = value_scale(joint.kind)
        lower_limit, upper_limit = low / scale, high / scale

    ox, oy, oz = joint.origin
    tx, ty, tz = joint.target
    return ModelJointDisplay(
        name=joint.name,
        kind=joint.kind,
        variable=joint.variable,
        origin_mm=(ox / MM_TO_M, oy / MM_TO_M, oz / MM_TO_M),
        target_mm=(tx / MM_TO_M, ty / MM_TO_M, tz / MM_TO_M),
        direction=joint.direction,
        initial_angle_deg=joint.initial_angle_rad / DEG_TO_RAD,
        lower_limit=lower_limit,
        upper_limit=upper_limit,
        alignment=from_transform(joint.alignment),
    )


@dataclass(frozen=True, slots=True)
class AnchorDisplay:
    """An anchor in the units the user sees: **millimetres** for the point.

    The direction stays unitless — it is a direction, and scaling it would mean
    nothing. Same boundary role as `ModelJointDisplay`.
    """

    point_mm: Vec3 = _ZERO
    direction: Vec3 = (0.0, 0.0, 1.0)


def to_anchor(display: AnchorDisplay) -> Anchor:
    """Convert the entered values into an internal anchor (metres)."""
    px, py, pz = display.point_mm
    return Anchor(
        point=(px * MM_TO_M, py * MM_TO_M, pz * MM_TO_M),
        direction=display.direction,
    )


def from_anchor(anchor: Anchor) -> AnchorDisplay:
    """Convert an internal anchor back into what a panel shows."""
    px, py, pz = anchor.point
    return AnchorDisplay(
        point_mm=(px / MM_TO_M, py / MM_TO_M, pz / MM_TO_M),
        direction=anchor.direction,
    )
