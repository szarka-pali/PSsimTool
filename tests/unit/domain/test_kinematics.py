"""Testy kinematiky."""

from __future__ import annotations

import math

import pytest

from pssim.domain.kinematics import clamp_to_limits, joint_pose, rest_pose
from pssim.domain.machine import Transform
from tests.factories import fixed_joint, prismatic_joint, revolute_joint


class TestPrismatic:
    def test_posunie_po_osi(self) -> None:
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
    def test_otoci_o_zadany_uhol(self) -> None:
        pose = joint_pose(revolute_joint(), math.pi / 4)

        assert pose.rotation_angle_rad == pytest.approx(math.pi / 4, abs=1e-12)

    def test_neposunie(self) -> None:
        pose = joint_pose(revolute_joint(), math.pi / 4)

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)


class TestFixed:
    def test_ignoruje_hodnotu(self) -> None:
        joint = fixed_joint()

        assert joint_pose(joint, 999.0).translation == joint_pose(joint, 0.0).translation

    def test_nie_je_nikdy_clamped(self) -> None:
        assert joint_pose(fixed_joint(), 999.0).is_clamped is False


class TestLimity:
    def test_hodnota_v_rozsahu_prejde_nezmenena(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), 1.0)

        assert (value, is_clamped) == (1.0, False)

    @pytest.mark.parametrize("value", [0.0, 2.5])
    def test_hodnota_presne_na_limite_nie_je_clamped(self, value: float) -> None:
        _, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), value)

        assert is_clamped is False

    def test_pod_dolnym_limitom_sa_orezhe(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), -1.0)

        assert (value, is_clamped) == (0.0, True)

    def test_nad_hornym_limitom_sa_orezhe(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=(0.0, 2.5)), 9.0)

        assert (value, is_clamped) == (2.5, True)

    def test_bez_limitov_prejde_cokolvek(self) -> None:
        value, is_clamped = clamp_to_limits(prismatic_joint(limits=None), 1e6)

        assert (value, is_clamped) == (1e6, False)

    def test_clamp_sa_premietne_do_pozy(self) -> None:
        pose = joint_pose(prismatic_joint(limits=(0.0, 2.5)), 9.0)

        assert pose.is_clamped is True
        assert pose.translation == pytest.approx((2.5, 0.0, 0.0), abs=1e-12)


class TestRestPose:
    def test_bez_limitov_je_v_nule(self) -> None:
        pose = rest_pose(prismatic_joint(limits=None))

        assert pose.translation == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)

    def test_limity_neobsahujuce_nulu_posunu_na_najblizsi_limit(self) -> None:
        # Bez tohto by diel skončil mimo fyzicky možný rozsah, kým nepríde
        # prvá hodnota z PLC.
        pose = rest_pose(prismatic_joint(limits=(1.0, 2.0)))

        assert pose.translation == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)

    def test_hodnota_nad_hornym_limitom_sa_orezhe_aj_v_rest_poze(self) -> None:
        pose = rest_pose(prismatic_joint(limits=(-2.0, -1.0)))

        assert pose.translation == pytest.approx((-1.0, 0.0, 0.0), abs=1e-12)
