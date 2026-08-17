"""Testy konvencií otočení.

Zmyslom je overiť kvaternióny proti **nezávisle zostavenej rotačnej matici**.
Konvencie sú tá časť 3D kódu, kde sa chyba prejaví ako „diel je otočený nejako
divne" a hľadá sa najhoršie.
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
    def test_nulovy_uhol_je_identita(self) -> None:
        assert axis_angle_to_quat((0.0, 0.0, 1.0), 0.0) == pytest.approx(IDENTITY_QUAT, abs=1e-12)

    def test_nulova_os_je_identita(self) -> None:
        # Nemá sa stať (loader to odmietne), ale scéna nesmie spadnúť.
        assert axis_angle_to_quat((0.0, 0.0, 0.0), 1.0) == pytest.approx(IDENTITY_QUAT, abs=1e-12)

    def test_os_sa_normalizuje(self) -> None:
        long_axis = axis_angle_to_quat((0.0, 0.0, 5.0), math.pi / 3)
        unit_axis = axis_angle_to_quat((0.0, 0.0, 1.0), math.pi / 3)

        assert long_axis == pytest.approx(unit_axis, abs=1e-12)

    def test_otocenie_o_90_okolo_z(self) -> None:
        quat = axis_angle_to_quat((0.0, 0.0, 1.0), math.pi / 2)

        assert rotate_point(quat, (1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)

    def test_otocenie_o_90_okolo_x(self) -> None:
        quat = axis_angle_to_quat((1.0, 0.0, 0.0), math.pi / 2)

        assert rotate_point(quat, (0.0, 1.0, 0.0)) == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)

    def test_zaporny_uhol_otoci_opacne(self) -> None:
        quat = axis_angle_to_quat((0.0, 0.0, 1.0), -math.pi / 2)

        assert rotate_point(quat, (1.0, 0.0, 0.0)) == pytest.approx((0.0, -1.0, 0.0), abs=1e-9)

    def test_matica_je_ortogonalna(self) -> None:
        matrix = quat_to_matrix(axis_angle_to_quat((1.0, 2.0, 3.0), 0.7))

        assert matrix @ matrix.T == pytest.approx(np.eye(3), abs=1e-9)


class TestSucin:
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
        # Toto je ten test, ktorý drží konvenciu: gp_Intrinsic_XYZ z OCC
        # znamená Rx @ Ry @ Rz. Ak sa poradie zmení, rozsypú sa všetky machines/*.yaml.
        roll, pitch, yaw = rpy
        expected = rotation_x(roll) @ rotation_y(pitch) @ rotation_z(yaw)

        assert quat_to_matrix(rpy_to_quat(rpy)) == pytest.approx(expected, abs=1e-9)

    def test_otocenie_o_90_okolo_z_ako_vo_fixture(self) -> None:
        # `hlava` vo fixture.step má presne toto otočenie.
        quat = rpy_to_quat((0.0, 0.0, math.pi / 2))

        assert rotate_point(quat, (1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)
