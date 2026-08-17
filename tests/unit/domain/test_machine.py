"""Testy validácie modelu stroja."""

from __future__ import annotations

import pytest

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Joint, JointType, Machine
from tests.factories import fixed_joint, machine, prismatic_joint, revolute_joint


class TestJoint:
    def test_neznormalizovana_os_je_chyba(self) -> None:
        # Dĺžka vektora by inak nenápadne škálovala pohyb.
        with pytest.raises(ConfigError, match="jednotkový vektor"):
            Joint(name="a", parent="p", child="c", type=JointType.PRISMATIC, axis=(0.0, 0.0, 2.0))

    def test_fixed_klb_os_neriesi(self) -> None:
        joint = Joint(name="a", parent="p", child="c", type=JointType.FIXED, axis=(0.0, 0.0, 5.0))

        assert joint.type is JointType.FIXED

    def test_prehodene_limity_su_chyba(self) -> None:
        with pytest.raises(ConfigError, match="väčší ako horný"):
            prismatic_joint(limits=(2.0, 1.0))

    def test_prazdny_nazov_je_chyba(self) -> None:
        with pytest.raises(ConfigError, match="neprázdny názov"):
            prismatic_joint(name="")


class TestMachine:
    def test_duplicitny_nazov_klbu_je_chyba(self) -> None:
        with pytest.raises(ConfigError, match="duplicitný názov"):
            machine(prismatic_joint(name="a"), prismatic_joint(name="a", child="iny"))

    def test_uzol_s_dvoma_rodicmi_je_chyba(self) -> None:
        with pytest.raises(ConfigError, match="musí byť strom"):
            machine(
                prismatic_joint(name="a", parent="p1", child="spolocny"),
                prismatic_joint(name="b", parent="p2", child="spolocny"),
            )

    def test_cyklus_je_chyba(self) -> None:
        with pytest.raises(ConfigError, match="cyklus"):
            machine(
                prismatic_joint(name="a", parent="x", child="y"),
                prismatic_joint(name="b", parent="y", child="x"),
            )

    def test_prazdny_nazov_stroja_je_chyba(self) -> None:
        with pytest.raises(ConfigError, match="neprázdny názov"):
            Machine(name="", joints=(prismatic_joint(),))


class TestDotazy:
    def test_moving_joints_vynecha_fixed(self) -> None:
        result = machine(prismatic_joint(name="a"), fixed_joint(name="b"))

        assert result.moving_joints == (result.joint("a"),)

    def test_neznamy_klb_vyhodi_chybu_so_zoznamom(self) -> None:
        with pytest.raises(ConfigError, match="dostupné: os_x"):
            machine(prismatic_joint(name="os_x")).joint("neexistuje")

    def test_chain_to_root_vrati_klby_od_uzla_ku_korenu(self) -> None:
        result = machine(
            prismatic_joint(name="x", parent="base", child="portal"),
            revolute_joint(name="c", parent="portal", child="hlava"),
        )

        chain = result.chain_to_root("hlava")

        assert tuple(joint.name for joint in chain) == ("c", "x")

    def test_chain_to_root_korena_je_prazdny(self) -> None:
        assert machine(prismatic_joint()).chain_to_root("base") == ()
