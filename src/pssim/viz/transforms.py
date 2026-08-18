"""Converting domain transformations into what Panda3D wants.

The module is deliberately **pure** (numpy, no Panda3D), so the rotation convention can be
checked against a rotation matrix in `tests/unit/`. Conventions are the part of 3D code
where a mistake shows up as "the part is turned somehow oddly" and is the worst to debug.

The convention of `domain.machine.Transform.rpy`: **intrinsic XYZ** — first a rotation
about X, then about the new Y, finally about the new Z. That is what
`gp_Trsf.GetRotation().GetEulerAngles(gp_Intrinsic_XYZ)` gives when importing STEP.
"""

from __future__ import annotations

import numpy as np

from pssim.domain.machine import Vec3

#: A quaternion as (w, x, y, z) — the same order as the `LQuaternion` constructor.
Quaternion = tuple[float, float, float, float]

IDENTITY_QUAT: Quaternion = (1.0, 0.0, 0.0, 0.0)


def axis_angle_to_quat(axis: Vec3, angle_rad: float) -> Quaternion:
    """The quaternion of a rotation about an axis. The axis is normalised.

    A zero axis gives the identity — a joint with a zero axis is a `ConfigError` at load
    time, but the scene must not fall over even if such a value somehow gets here.
    """
    vector = np.asarray(axis, dtype=np.float64)
    length = float(np.linalg.norm(vector))
    if length == 0.0:
        return IDENTITY_QUAT

    unit = vector / length
    half = angle_rad / 2.0
    sin_half = float(np.sin(half))
    return (
        float(np.cos(half)),
        float(unit[0] * sin_half),
        float(unit[1] * sin_half),
        float(unit[2] * sin_half),
    )


def multiply_quat(first: Quaternion, second: Quaternion) -> Quaternion:
    """The Hamilton product. The result matches the matrix `R(first) @ R(second)`."""
    w1, x1, y1, z1 = first
    w2, x2, y2, z2 = second
    return (
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    )


def rpy_to_quat(rpy: Vec3) -> Quaternion:
    """Convert intrinsic XYZ angles into a quaternion.

    Matches the matrix ``Rx(roll) @ Ry(pitch) @ Rz(yaw)``.
    """
    roll, pitch, yaw = rpy
    return multiply_quat(
        multiply_quat(
            axis_angle_to_quat((1.0, 0.0, 0.0), roll),
            axis_angle_to_quat((0.0, 1.0, 0.0), pitch),
        ),
        axis_angle_to_quat((0.0, 0.0, 1.0), yaw),
    )


def quat_to_matrix(quat: Quaternion) -> np.ndarray:
    """The `3x3` rotation matrix for the column-vector convention (`v' = R @ v`).

    Used for checking the conventions in tests and for diagnosis.
    """
    w, x, y, z = quat
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def rotate_point(quat: Quaternion, point: Vec3) -> Vec3:
    """Rotate a point by a quaternion. The reference implementation for tests."""
    rotated = quat_to_matrix(quat) @ np.asarray(point, dtype=np.float64)
    return (float(rotated[0]), float(rotated[1]), float(rotated[2]))
