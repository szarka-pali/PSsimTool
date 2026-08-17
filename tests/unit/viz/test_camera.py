"""Testy rámovania kamery.

Čisté funkcie, bez Panda3D. Kritické je, že sa všetko odvodzuje od **veľkosti
scény** — fixné hodnoty tu nefungujú a práve ony spôsobili prvý prázdny render.
"""

from __future__ import annotations

import math

import pytest

from pssim.viz.camera import (
    DEFAULT_FOV_DEG,
    FALLBACK_RADIUS_M,
    STANDARD_VIEWS,
    clip_planes,
    frame_distance,
    view_direction,
)


class TestVzdialenostKamery:
    def test_vacsia_scena_potrebuje_vacsiu_vzdialenost(self) -> None:
        assert frame_distance(1.0) > frame_distance(0.1)

    def test_skaluje_linearne_s_polomerom(self) -> None:
        assert frame_distance(2.0) == pytest.approx(2 * frame_distance(1.0), rel=1e-9)

    def test_sirsi_zorny_uhol_dovoli_ist_blizsie(self) -> None:
        assert frame_distance(1.0, fov_deg=90.0) < frame_distance(1.0, fov_deg=30.0)

    def test_cely_model_sa_vojde_do_zaberu(self) -> None:
        # Polovičný zorný uhol musí „obsiahnuť" polomer scény zo vzdialenosti d.
        radius = 0.5
        distance = frame_distance(radius, DEFAULT_FOV_DEG)

        max_visible_radius = distance * math.sin(math.radians(DEFAULT_FOV_DEG) / 2)

        assert max_visible_radius >= radius

    @pytest.mark.parametrize("radius", [0.0, -1.0])
    def test_nezmyselny_polomer_pouzije_nahradny(self, radius: float) -> None:
        assert frame_distance(radius) == pytest.approx(frame_distance(FALLBACK_RADIUS_M))

    def test_nulovy_zorny_uhol_nespadne(self) -> None:
        assert frame_distance(1.0, fov_deg=0.0) > 0.0


class TestOrezoveRoviny:
    def test_near_je_pod_velkostou_sceny(self) -> None:
        # Toto je presne tá chyba, čo spôsobila prázdne okno: default near 1.0
        # pri 0,1 m stroji oreže úplne všetko.
        near, _ = clip_planes(0.113)

        assert near < 0.113

    def test_far_je_nad_velkostou_sceny(self) -> None:
        _, far = clip_planes(0.113)

        assert far > 0.113

    def test_roviny_skaluju_so_scenou(self) -> None:
        small_near, small_far = clip_planes(0.1)
        big_near, big_far = clip_planes(10.0)

        assert big_near > small_near
        assert big_far > small_far

    def test_near_je_vzdy_kladne(self) -> None:
        near, _ = clip_planes(0.001)

        assert near > 0.0

    def test_pomer_far_near_je_rozumny_pre_depth_buffer(self) -> None:
        # Príliš veľký pomer rozbije presnosť depth bufferu a plochy začnú blikať.
        near, far = clip_planes(5.0)

        assert far / near <= 100_000

    @pytest.mark.parametrize("radius", [0.0, -1.0])
    def test_nezmyselny_polomer_pouzije_nahradny(self, radius: float) -> None:
        assert clip_planes(radius) == clip_planes(FALLBACK_RADIUS_M)


class TestSmeryPohladu:
    def test_default_je_znamy(self) -> None:
        assert "iso" in STANDARD_VIEWS

    @pytest.mark.parametrize("name", sorted(STANDARD_VIEWS))
    def test_ziadny_smer_nie_je_nulovy(self, name: str) -> None:
        # Nulový vektor by pri normalizácii dal NaN a scéna by zmizla.
        assert any(component != 0.0 for component in view_direction(name))

    def test_front_a_back_su_opacne(self) -> None:
        front = view_direction("front")
        back = view_direction("back")

        assert front == pytest.approx(tuple(-component for component in back))

    def test_top_nie_je_presne_v_osi_up(self) -> None:
        # Čisto +Z by rozbilo lookAt — smer pohľadu by bol rovnobežný s osou up.
        assert view_direction("top")[1] != 0.0

    def test_smery_su_jednotkove(self) -> None:
        for name in STANDARD_VIEWS:
            length = math.sqrt(sum(component**2 for component in view_direction(name)))
            assert length == pytest.approx(1.0, abs=1e-9), name

    def test_lavy_a_pravy_su_opacne(self) -> None:
        left = view_direction("left")
        right = view_direction("right")

        assert left == pytest.approx(tuple(-component for component in right), abs=1e-9)

    def test_top_a_bottom_su_opacne_vo_vyske(self) -> None:
        assert view_direction("top")[2] == pytest.approx(-view_direction("bottom")[2], abs=1e-9)

    def test_celny_pohlad_je_na_zapornej_y(self) -> None:
        assert view_direction("front") == pytest.approx((0.0, -1.0, 0.0), abs=1e-9)

    def test_neznamy_smer_vypise_podporovane(self) -> None:
        with pytest.raises(ValueError, match="podporované:"):
            view_direction("zozadu-zhora")
