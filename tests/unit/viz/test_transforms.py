"""Tests of the rotation conventions.

The point is to check the quaternions against an **independently built rotation
matrix**. Conventions are the part of 3D code where a mistake shows up as "the part is
turned somehow oddly" and is the worst to track down.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from pssim.viz.transforms import (
    IDENTITY_QUAT,
    axis_angle_to_quat,
    multiply_quat,
    quat_to_matrix,
    rotate_point,
    rpy_to_quat,
)


def rotation_x(angle: float) -> np.ndarray:
    cos, sin = math.cos(angle), math.sin(angle)
    return np.array([[1, 0, 0], [0, cos, -sin], [0, sin, cos]], dtype=np.float64)


def rotation_y(angle: float) -> np.ndarray:
    cos, sin = math.cos(angle), math.sin(angle)
    return np.array([[cos, 0, sin], [0, 1, 0], [-sin, 0, cos]], dtype=np.float64)


def rotation_z(angle: float) -> np.ndarray:
    cos, sin = math.cos(angle), math.sin(angle)
    return np.array([[cos, -sin, 0], [sin, cos, 0], [0, 0, 1]], dtype=np.float64)


class TestAxisAngle:
    def test_a_zero_angle_is_the_identity(self) -> None:
        assert axis_angle_to_quat((0.0, 0.0, 1.0), 0.0) == pytest.approx(IDENTITY_QUAT, abs=1e-12)

    def test_a_zero_axis_is_the_identity(self) -> None:
        # Should not happen (the loader rejects it), but the scene must not crash.
        assert axis_angle_to_quat((0.0, 0.0, 0.0), 1.0) == pytest.approx(IDENTITY_QUAT, abs=1e-12)

    def test_the_axis_is_normalised(self) -> None:
        long_axis = axis_angle_to_quat((0.0, 0.0, 5.0), math.pi / 3)
        unit_axis = axis_angle_to_quat((0.0, 0.0, 1.0), math.pi / 3)

        assert long_axis == pytest.approx(unit_axis, abs=1e-12)

    def test_a_90_degree_rotation_about_z(self) -> None:
        quat = axis_angle_to_quat((0.0, 0.0, 1.0), math.pi / 2)

        assert rotate_point(quat, (1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)

    def test_a_90_degree_rotation_about_x(self) -> None:
        quat = axis_angle_to_quat((1.0, 0.0, 0.0), math.pi / 2)

        assert rotate_point(quat, (0.0, 1.0, 0.0)) == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)

    def test_a_negative_angle_turns_the_other_way(self) -> None:
        quat = axis_angle_to_quat((0.0, 0.0, 1.0), -math.pi / 2)

        assert rotate_point(quat, (1.0, 0.0, 0.0)) == pytest.approx((0.0, -1.0, 0.0), abs=1e-9)

    def test_the_matrix_is_orthogonal(self) -> None:
        matrix = quat_to_matrix(axis_angle_to_quat((1.0, 2.0, 3.0), 0.7))

        assert matrix @ matrix.T == pytest.approx(np.eye(3), abs=1e-9)


class TestProduct:
    def test_identita_nic_nemeni(self) -> None:
        quat = axis_angle_to_quat((0.0, 1.0, 0.0), 0.4)

        assert multiply_quat(IDENTITY_QUAT, quat) == pytest.approx(quat, abs=1e-12)

    def test_sucin_zodpoveda_sucinu_matic(self) -> None:
        first = axis_angle_to_quat((1.0, 0.0, 0.0), 0.3)
        second = axis_angle_to_quat((0.0, 0.0, 1.0), 0.7)

        product = quat_to_matrix(multiply_quat(first, second))

        assert product == pytest.approx(quat_to_matrix(first) @ quat_to_matrix(second), abs=1e-9)

    def test_poradie_zalezi(self) -> None:
        first = axis_angle_to_quat((1.0, 0.0, 0.0), 0.9)
        second = axis_angle_to_quat((0.0, 0.0, 1.0), 0.9)

        assert multiply_quat(first, second) != pytest.approx(multiply_quat(second, first), abs=1e-6)


class TestRpy:
    def test_nulove_uhly_su_identita(self) -> None:
        assert rpy_to_quat((0.0, 0.0, 0.0)) == pytest.approx(IDENTITY_QUAT, abs=1e-12)

    @pytest.mark.parametrize(
        "rpy",
        [
            (0.3, 0.0, 0.0),
            (0.0, 0.4, 0.0),
            (0.0, 0.0, 0.5),
            (0.3, 0.4, 0.5),
            (-1.1, 0.7, 2.2),
        ],
    )
    def test_zodpoveda_intrinsic_xyz_matici(self, rpy: tuple[float, float, float]) -> None:
        # This is the test that holds the convention: gp_Intrinsic_XYZ from OCC means
        # Rx @ Ry @ Rz. Change the order and every machines/*.yaml falls apart.
        roll, pitch, yaw = rpy
        expected = rotation_x(roll) @ rotation_y(pitch) @ rotation_z(yaw)

        assert quat_to_matrix(rpy_to_quat(rpy)) == pytest.approx(expected, abs=1e-9)

    def test_a_90_degree_rotation_about_z_as_in_the_fixture(self) -> None:
        # `head` in fixture.step carries exactly this rotation.
        quat = rpy_to_quat((0.0, 0.0, math.pi / 2))

        assert rotate_point(quat, (1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)
