"""Testy plánu scény.

Rozdelenie na statickú a pohyblivú geometriu je čistá logika — testuje sa bez
Panda3D a bez otvorenia okna.
"""

from __future__ import annotations

import pytest

from pssim.domain.errors import ConfigError
from pssim.viz.scene_builder import plan_scene
from tests.factories import assembly, fixed_joint, machine, prismatic_joint, revolute_joint


class TestRozdelenie:
    def test_potomok_pohybliveho_klbu_je_pohyblivy(self) -> None:
        # Potomkovia sa hýbu spolu s dielom, takže sa nesmú flattenovať.
        plan = plan_scene(
            machine(prismatic_joint(parent="base", child="base/portal")),
            assembly("base", "base/portal", "base/portal/vozik"),
        )

        assert plan.moving_nodes == ("base/portal", "base/portal/vozik")

    def test_uzol_mimo_pohybliveho_podstromu_je_staticky(self) -> None:
        plan = plan_scene(
            machine(prismatic_joint(parent="base", child="base/portal")),
            assembly("base", "base/portal", "base/kryt"),
        )

        assert plan.static_nodes == ("base", "base/kryt")

    def test_fixed_klb_nerobi_uzol_pohyblivym(self) -> None:
        plan = plan_scene(
            machine(fixed_joint(parent="base", child="base/kryt")),
            assembly("base", "base/kryt"),
        )

        assert plan.moving_nodes == ()

    def test_prefix_nesmie_matchovat_ciastocne(self) -> None:
        # `base/portal2` nie je potomkom `base/portal`.
        plan = plan_scene(
            machine(prismatic_joint(parent="base", child="base/portal")),
            assembly("base", "base/portal", "base/portal2"),
        )

        assert "base/portal2" in plan.static_nodes

    def test_viac_klbov_v_retazci(self) -> None:
        plan = plan_scene(
            machine(
                prismatic_joint(name="x", parent="base", child="base/portal"),
                revolute_joint(name="c", parent="base/portal", child="base/portal/hlava"),
            ),
            assembly("base", "base/portal", "base/portal/hlava"),
        )

        assert plan.moving_nodes == ("base/portal", "base/portal/hlava")


class TestMapovanie:
    def test_klb_mieri_na_svojho_potomka(self) -> None:
        plan = plan_scene(
            machine(prismatic_joint(name="os_x", parent="base", child="base/portal")),
            assembly("base", "base/portal"),
        )

        assert plan.joint_to_node["os_x"] == "base/portal"


class TestChybneUzly:
    def test_neexistujuci_child_je_chyba(self) -> None:
        with pytest.raises(ConfigError, match="does not exist"):
            plan_scene(
                machine(prismatic_joint(parent="base", child="base/neexistuje")),
                assembly("base", "base/portal"),
            )

    def test_neexistujuci_parent_je_chyba(self) -> None:
        with pytest.raises(ConfigError, match="parent"):
            plan_scene(
                machine(prismatic_joint(parent="nieje", child="base")),
                assembly("base"),
            )

    def test_chyba_ponukne_podobne_cesty(self) -> None:
        # Assembly má tisíc uzlov — bez nápovedy sa chyba nedá vyriešiť.
        with pytest.raises(ConfigError, match="Similar paths"):
            plan_scene(
                machine(prismatic_joint(parent="base", child="portal")),
                assembly("base", "base/portal"),
            )
