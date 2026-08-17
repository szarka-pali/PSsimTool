"""Testy orbitálnej kamery.

Celá matematika ovládania je čistá, takže sa dá overiť bez okna. To je zámer:
„model sa točí divne" je inak chyba, ktorá sa ladí len očami.
"""

from __future__ import annotations

import math

import pytest

from pssim.viz.orbit import (
    MAX_ELEVATION_RAD,
    STANDARD_VIEWS,
    DragAction,
    OrbitCamera,
    apply_drag,
    apply_wheel,
    drag_action,
)


def camera(**kwargs: object) -> OrbitCamera:
    defaults: dict[str, object] = {
        "target": (0.0, 0.0, 0.0),
        "distance_m": 10.0,
        "azimuth_rad": 0.0,
        "elevation_rad": 0.0,
        "min_distance_m": 0.1,
        "max_distance_m": 100.0,
    }
    defaults.update(kwargs)
    return OrbitCamera(**defaults)  # type: ignore[arg-type]


class TestPolohaKamery:
    def test_zakladny_pohlad_je_zpredu(self) -> None:
        # azimuth = 0 znamená kameru na -Y, pozerajúcu na +Y.
        assert camera().eye == pytest.approx((0.0, -10.0, 0.0), abs=1e-9)

    def test_azimut_90_stupnov_da_pohlad_zboku(self) -> None:
        eye = camera(azimuth_rad=math.pi / 2).eye

        assert eye == pytest.approx((10.0, 0.0, 0.0), abs=1e-9)

    def test_elevacia_zdvihne_kameru(self) -> None:
        eye = camera(elevation_rad=math.pi / 4).eye

        assert eye[2] == pytest.approx(10.0 * math.sin(math.pi / 4), abs=1e-9)

    def test_vzdialenost_od_ciela_sedi(self) -> None:
        instance = camera(azimuth_rad=0.7, elevation_rad=0.3)
        eye = instance.eye

        assert math.dist(eye, instance.target) == pytest.approx(10.0, abs=1e-9)

    def test_posunuty_ciel_posunie_kameru(self) -> None:
        eye = camera(target=(5.0, 5.0, 5.0)).eye

        assert eye == pytest.approx((5.0, -5.0, 5.0), abs=1e-9)

    def test_forward_mieri_na_ciel(self) -> None:
        assert camera().forward == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)

    def test_right_je_kolmy_na_forward(self) -> None:
        instance = camera(azimuth_rad=0.9, elevation_rad=0.4)
        right, forward = instance.right, instance.forward

        dot = sum(a * b for a, b in zip(right, forward, strict=True))

        assert dot == pytest.approx(0.0, abs=1e-9)

    def test_kamera_sa_nenaklana_nabok(self) -> None:
        # `right` musí zostať vodorovný, inak sa obraz nakláňa a je to nepríjemné.
        assert camera(elevation_rad=1.2).right[2] == pytest.approx(0.0, abs=1e-12)

    def test_up_je_jednotkovy(self) -> None:
        up = camera(azimuth_rad=0.3, elevation_rad=-0.8).up

        assert math.sqrt(sum(component**2 for component in up)) == pytest.approx(1.0, abs=1e-9)


class TestOtacanie:
    def test_orbit_zmeni_azimut(self) -> None:
        turned = camera().orbit(0.5, 0.0)

        assert turned.azimuth_rad == pytest.approx(0.5, abs=1e-12)

    def test_orbit_nemeni_vzdialenost(self) -> None:
        turned = camera().orbit(1.0, 0.5)

        assert turned.distance_m == pytest.approx(10.0, abs=1e-12)

    def test_orbit_nemeni_ciel(self) -> None:
        turned = camera(target=(1.0, 2.0, 3.0)).orbit(1.0, 0.5)

        assert turned.target == (1.0, 2.0, 3.0)

    def test_elevacia_sa_oreze_pod_polom(self) -> None:
        # Presne v zenite stráca lookAt referenciu „hore" a obraz sa preklopí.
        turned = camera().orbit(0.0, 10.0)

        assert turned.elevation_rad == pytest.approx(MAX_ELEVATION_RAD, abs=1e-12)

    def test_elevacia_sa_oreze_aj_dole(self) -> None:
        turned = camera().orbit(0.0, -10.0)

        assert turned.elevation_rad == pytest.approx(-MAX_ELEVATION_RAD, abs=1e-12)

    def test_azimut_sa_zabaluje(self) -> None:
        # Po mnohých otáčkach by inak číslo rástlo donekonečna.
        turned = camera().orbit(20.0 * math.pi + 0.3, 0.0)

        assert abs(turned.azimuth_rad) <= math.pi

    def test_plna_otacka_vrati_rovnaky_pohlad(self) -> None:
        turned = camera().orbit(2.0 * math.pi, 0.0)

        assert turned.eye == pytest.approx(camera().eye, abs=1e-9)

    def test_povodna_kamera_zostane_nezmenena(self) -> None:
        original = camera()

        original.orbit(1.0, 1.0)

        assert original.azimuth_rad == 0.0


class TestPriblizovanie:
    def test_faktor_pod_jedna_priblizi(self) -> None:
        assert camera().zoom(0.5).distance_m == pytest.approx(5.0, abs=1e-12)

    def test_faktor_nad_jedna_oddiali(self) -> None:
        assert camera().zoom(2.0).distance_m == pytest.approx(20.0, abs=1e-12)

    def test_zoom_je_multiplikativny(self) -> None:
        # Dva rovnaké kroky musia dať ten istý výsledok ako jeden dvojnásobný.
        twice = camera().zoom(0.8).zoom(0.8)

        assert twice.distance_m == pytest.approx(camera().zoom(0.64).distance_m, abs=1e-12)

    def test_priblizenie_naraza_na_dolnu_medzu(self) -> None:
        assert camera().zoom(1e-9).distance_m == pytest.approx(0.1, abs=1e-12)

    def test_oddialenie_naraza_na_hornu_medzu(self) -> None:
        assert camera().zoom(1e9).distance_m == pytest.approx(100.0, abs=1e-12)

    def test_zoom_nemeni_smer_pohladu(self) -> None:
        zoomed = camera(azimuth_rad=0.6, elevation_rad=0.2).zoom(0.3)

        assert zoomed.forward == pytest.approx(camera(azimuth_rad=0.6, elevation_rad=0.2).forward)

    def test_nulovy_faktor_je_chyba(self) -> None:
        with pytest.raises(ValueError, match="factor"):
            camera().zoom(0.0)


class TestPosuvanie:
    def test_pan_posunie_ciel_doprava(self) -> None:
        moved = camera().pan(2.0, 0.0)

        assert moved.target == pytest.approx((2.0, 0.0, 0.0), abs=1e-9)

    def test_pan_posunie_ciel_hore(self) -> None:
        moved = camera().pan(0.0, 3.0)

        assert moved.target == pytest.approx((0.0, 0.0, 3.0), abs=1e-9)

    def test_pan_nemeni_vzdialenost_ani_uhly(self) -> None:
        moved = camera(azimuth_rad=0.4, elevation_rad=0.2).pan(1.0, 2.0)

        assert moved.distance_m == pytest.approx(10.0)
        assert moved.azimuth_rad == pytest.approx(0.4)

    def test_pan_ide_po_smere_pohladu(self) -> None:
        # Pri pohľade zboku musí „doprava po obrazovke" byť iný svetový smer.
        moved = camera(azimuth_rad=math.pi / 2).pan(2.0, 0.0)

        assert moved.target == pytest.approx((0.0, 2.0, 0.0), abs=1e-9)

    def test_pan_v_pixeloch_skaluje_so_vzdialenostou(self) -> None:
        # Bod pod kurzorom má zostať pod kurzorom bez ohľadu na priblíženie.
        near = camera(distance_m=1.0).pan_pixels(100.0, 0.0, viewport_height_px=800)
        far = camera(distance_m=10.0).pan_pixels(100.0, 0.0, viewport_height_px=800)

        assert abs(far.target[0]) == pytest.approx(10.0 * abs(near.target[0]), rel=1e-9)

    def test_tahanie_doprava_posunie_model_doprava(self) -> None:
        # Teda cieľ ide doľava — to je konvencia „chytím model a ťahám ho".
        moved = camera().pan_pixels(50.0, 0.0, viewport_height_px=600)

        assert moved.target[0] < 0.0

    def test_nulova_vyska_viewportu_nespadne(self) -> None:
        # Stáva sa pri minimalizovanom okne.
        moved = camera().pan_pixels(10.0, 10.0, viewport_height_px=0)

        assert moved.target == camera().target


class TestRamovanie:
    def test_ciel_je_stred_modelu(self) -> None:
        framed = OrbitCamera.framing(center=(1.0, 2.0, 3.0), radius_m=0.5)

        assert framed.target == (1.0, 2.0, 3.0)

    def test_cely_model_sa_vojde_do_zaberu(self) -> None:
        radius, fov = 0.5, 40.0
        framed = OrbitCamera.framing(center=(0.0, 0.0, 0.0), radius_m=radius, fov_deg=fov)

        visible_radius = framed.distance_m * math.sin(math.radians(fov) / 2)

        assert visible_radius >= radius

    def test_vacsi_model_znamena_vacsiu_vzdialenost(self) -> None:
        small = OrbitCamera.framing((0.0, 0.0, 0.0), 0.1)
        big = OrbitCamera.framing((0.0, 0.0, 0.0), 10.0)

        assert big.distance_m > small.distance_m

    def test_medze_zoomu_sa_odvodia_od_velkosti(self) -> None:
        framed = OrbitCamera.framing((0.0, 0.0, 0.0), 2.0)

        assert framed.min_distance_m < framed.distance_m < framed.max_distance_m

    def test_nulovy_polomer_nespadne(self) -> None:
        # Stáva sa pri prázdnej scéne alebo pri jedinom bode.
        framed = OrbitCamera.framing((0.0, 0.0, 0.0), 0.0)

        assert framed.distance_m > 0.0


class TestOrezoveRoviny:
    def test_near_je_pod_vzdialenostou(self) -> None:
        near, _ = camera(distance_m=0.2).clip_planes()

        assert 0.0 < near < 0.2

    def test_far_je_za_modelom(self) -> None:
        _, far = camera(distance_m=0.2).clip_planes()

        assert far > 0.2

    def test_roviny_skaluju_so_vzdialenostou(self) -> None:
        blizko, _ = camera(distance_m=0.1).clip_planes()
        daleko, _ = camera(distance_m=100.0).clip_planes()

        assert daleko > blizko


class TestTahanieMysou:
    def test_orbit_zmeni_uhly(self) -> None:
        dragged = apply_drag(camera(), DragAction.ORBIT, 100.0, 0.0, viewport_height_px=800)

        assert dragged.azimuth_rad != camera().azimuth_rad

    def test_orbit_nemeni_ciel(self) -> None:
        dragged = apply_drag(camera(), DragAction.ORBIT, 100.0, 50.0, viewport_height_px=800)

        assert dragged.target == camera().target

    def test_pan_nemeni_uhly(self) -> None:
        dragged = apply_drag(camera(), DragAction.PAN, 100.0, 50.0, viewport_height_px=800)

        assert dragged.azimuth_rad == camera().azimuth_rad
        assert dragged.distance_m == camera().distance_m

    def test_pan_posunie_ciel(self) -> None:
        dragged = apply_drag(camera(), DragAction.PAN, 100.0, 0.0, viewport_height_px=800)

        assert dragged.target != camera().target

    def test_ziadna_akcia_nic_nezmeni(self) -> None:
        dragged = apply_drag(camera(), DragAction.NONE, 100.0, 50.0, viewport_height_px=800)

        assert dragged == camera()

    def test_opacny_tah_vrati_kameru_spat(self) -> None:
        start = camera()

        there = apply_drag(start, DragAction.ORBIT, 60.0, 30.0, viewport_height_px=800)
        back = apply_drag(there, DragAction.ORBIT, -60.0, -30.0, viewport_height_px=800)

        assert back.azimuth_rad == pytest.approx(start.azimuth_rad, abs=1e-9)
        assert back.elevation_rad == pytest.approx(start.elevation_rad, abs=1e-9)

    def test_vacsi_tah_otoci_viac(self) -> None:
        small = apply_drag(camera(), DragAction.ORBIT, 10.0, 0.0, viewport_height_px=800)
        big = apply_drag(camera(), DragAction.ORBIT, 100.0, 0.0, viewport_height_px=800)

        assert abs(big.azimuth_rad) > abs(small.azimuth_rad)


class TestKoliesko:
    def test_kladny_krok_priblizi(self) -> None:
        assert apply_wheel(camera(), 1).distance_m < camera().distance_m

    def test_zaporny_krok_oddiali(self) -> None:
        assert apply_wheel(camera(), -1).distance_m > camera().distance_m

    def test_nulovy_krok_nic_nezmeni(self) -> None:
        assert apply_wheel(camera(), 0) == camera()

    def test_krok_tam_a_spat_vrati_povodnu_vzdialenost(self) -> None:
        there_and_back = apply_wheel(apply_wheel(camera(), 3), -3)

        assert there_and_back.distance_m == pytest.approx(camera().distance_m, rel=1e-12)

    def test_koliesko_respektuje_medze(self) -> None:
        assert apply_wheel(camera(), 500).distance_m == pytest.approx(0.1, abs=1e-12)


class TestStandardnePohlady:
    def test_vsetky_ocakavane_su_definovane(self) -> None:
        assert set(STANDARD_VIEWS) == {
            "iso",
            "front",
            "back",
            "left",
            "right",
            "top",
            "bottom",
        }

    def test_celny_pohlad_je_na_zapornej_y(self) -> None:
        assert camera().with_view("front").eye == pytest.approx((0.0, -10.0, 0.0), abs=1e-9)

    def test_zadny_pohlad_je_na_kladnej_y(self) -> None:
        assert camera().with_view("back").eye == pytest.approx((0.0, 10.0, 0.0), abs=1e-9)

    def test_pravy_pohlad_je_na_kladnej_x(self) -> None:
        assert camera().with_view("right").eye == pytest.approx((10.0, 0.0, 0.0), abs=1e-9)

    def test_lavy_pohlad_je_na_zapornej_x(self) -> None:
        assert camera().with_view("left").eye == pytest.approx((-10.0, 0.0, 0.0), abs=1e-9)

    def test_pohlad_zhora_je_nad_modelom(self) -> None:
        assert camera().with_view("top").eye[2] > 9.9

    def test_pohlad_zdola_je_pod_modelom(self) -> None:
        assert camera().with_view("bottom").eye[2] < -9.9

    def test_zhora_nie_je_presne_v_zenite(self) -> None:
        # Presne v póle stráca lookAt referenciu „hore" a obraz sa preklopí.
        assert abs(camera().with_view("top").elevation_rad) < math.pi / 2

    def test_prepnutie_zachova_priblizenie(self) -> None:
        # Používateľ chce zmeniť uhol, nie stratiť zoom, ktorý si nastavil.
        zoomed = camera().zoom(0.3)

        assert zoomed.with_view("top").distance_m == pytest.approx(zoomed.distance_m)

    def test_prepnutie_zachova_ciel(self) -> None:
        moved = camera(target=(1.0, 2.0, 3.0))

        assert moved.with_view("front").target == (1.0, 2.0, 3.0)

    def test_neznamy_pohlad_vypise_podporovane(self) -> None:
        with pytest.raises(ValueError, match="podporované:"):
            camera().with_view("zozadu-zhora")

    @pytest.mark.parametrize("name", sorted(STANDARD_VIEWS))
    def test_kazdy_pohlad_da_platnu_kameru(self, name: str) -> None:
        instance = camera().with_view(name)

        assert math.dist(instance.eye, instance.target) == pytest.approx(10.0, abs=1e-9)


class TestPremietanieOsi:
    def test_zpredu_ide_x_doprava(self) -> None:
        screen_x, screen_y = camera().with_view("front").project((1.0, 0.0, 0.0))

        assert screen_x == pytest.approx(1.0, abs=1e-9)
        assert screen_y == pytest.approx(0.0, abs=1e-9)

    def test_zpredu_ide_z_hore(self) -> None:
        assert camera().with_view("front").project((0.0, 0.0, 1.0)) == pytest.approx(
            (0.0, 1.0), abs=1e-9
        )

    def test_zpredu_mieri_y_do_obrazovky(self) -> None:
        # Os rovnobežná so smerom pohľadu sa premietne do bodu.
        screen = camera().with_view("front").project((0.0, 1.0, 0.0))

        assert screen == pytest.approx((0.0, 0.0), abs=1e-9)

    def test_zhora_ide_y_hore(self) -> None:
        _, screen_y = camera().with_view("top").project((0.0, 1.0, 0.0))

        assert screen_y > 0.9

    def test_zhora_mieri_z_do_obrazovky(self) -> None:
        screen = camera().with_view("top").project((0.0, 0.0, 1.0))

        assert abs(screen[0]) < 0.05
        assert abs(screen[1]) < 0.05

    def test_zprava_ide_y_doprava(self) -> None:
        screen_x, _ = camera().with_view("right").project((0.0, 1.0, 0.0))

        assert screen_x == pytest.approx(1.0, abs=1e-9)

    def test_premietnutie_nepresiahne_jednotku(self) -> None:
        # Priemet jednotkového vektora nemôže byť dlhší než jednotka.
        instance = camera().with_view("iso")
        for direction in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            screen = instance.project(direction)
            assert math.hypot(*screen) <= 1.0 + 1e-9


class TestVazbyMysi:
    def test_stredne_tlacidlo_otaca(self) -> None:
        assert drag_action(2) is DragAction.ORBIT

    def test_shift_so_strednym_posuva(self) -> None:
        assert drag_action(2, shift=True) is DragAction.PAN

    def test_prave_tlacidlo_posuva(self) -> None:
        assert drag_action(3) is DragAction.PAN

    def test_lave_tlacidlo_otaca(self) -> None:
        assert drag_action(1) is DragAction.ORBIT

    def test_neznáme_tlacidlo_nerobi_nic(self) -> None:
        assert drag_action(9) is DragAction.NONE
