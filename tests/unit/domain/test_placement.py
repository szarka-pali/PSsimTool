"""Testy prevodu umiestnenia medzi jednotkami používateľa a scény.

Používateľ zadáva milimetre a stupne, scéna beží v metroch a radiánoch.
Šesť polí krát dva smery je dosť príležitostí na preklep, preto sú tu testy
na každú os zvlášť — chyba v jednej osi by inak prešla.
"""

from __future__ import annotations

import math

import pytest

from pssim.domain.machine import Transform
from pssim.domain.placement import (
    IDENTITY_PLACEMENT,
    PlacementDisplay,
    from_transform,
    is_identity,
    normalize_degrees,
    to_transform,
)


class TestPosun:
    def test_milimetre_sa_prevedu_na_metre(self) -> None:
        transform = to_transform(PlacementDisplay(x_mm=1000.0))

        assert transform.xyz[0] == pytest.approx(1.0)

    def test_kazda_os_ide_do_svojej_zlozky(self) -> None:
        # Zámena osí je klasická chyba, ktorú jeden súhrnný test neodhalí.
        transform = to_transform(PlacementDisplay(x_mm=100.0, y_mm=200.0, z_mm=300.0))

        assert transform.xyz == pytest.approx((0.1, 0.2, 0.3))

    def test_zaporny_posun_zachova_znamienko(self) -> None:
        transform = to_transform(PlacementDisplay(y_mm=-50.0))

        assert transform.xyz[1] == pytest.approx(-0.05)

    def test_nulove_hodnoty_daju_identitu(self) -> None:
        assert to_transform(PlacementDisplay()) == IDENTITY_PLACEMENT


class TestOtocenie:
    def test_stupne_sa_prevedu_na_radiany(self) -> None:
        transform = to_transform(PlacementDisplay(rotate_z_deg=180.0))

        assert transform.rpy[2] == pytest.approx(math.pi)

    def test_kazda_os_ide_do_svojej_zlozky(self) -> None:
        transform = to_transform(PlacementDisplay(rotate_x_deg=90.0, rotate_y_deg=45.0))

        assert transform.rpy == pytest.approx((math.pi / 2, math.pi / 4, 0.0))

    def test_otocenie_nemeni_posun(self) -> None:
        transform = to_transform(PlacementDisplay(rotate_x_deg=33.0))

        assert transform.xyz == (0.0, 0.0, 0.0)

    def test_posun_nemeni_otocenie(self) -> None:
        transform = to_transform(PlacementDisplay(x_mm=1234.0))

        assert transform.rpy == (0.0, 0.0, 0.0)


class TestSpatnyPrevod:
    def test_roundtrip_zachova_hodnoty(self) -> None:
        original = PlacementDisplay(1.5, -2.5, 3.5, 10.0, -20.0, 30.0)

        assert from_transform(to_transform(original)).as_tuple == pytest.approx(original.as_tuple)

    def test_metre_sa_ukazu_ako_milimetre(self) -> None:
        display = from_transform(Transform(xyz=(0.25, 0.0, 0.0)))

        assert display.x_mm == pytest.approx(250.0)

    def test_radiany_sa_ukazu_ako_stupne(self) -> None:
        display = from_transform(Transform(rpy=(0.0, math.pi / 2, 0.0)))

        assert display.rotate_y_deg == pytest.approx(90.0)


class TestIdentita:
    def test_nulova_transformacia_je_identita(self) -> None:
        assert is_identity(Transform()) is True

    def test_posun_nie_je_identita(self) -> None:
        assert is_identity(Transform(xyz=(0.001, 0.0, 0.0))) is False

    def test_otocenie_nie_je_identita(self) -> None:
        assert is_identity(Transform(rpy=(0.0, 0.0, 0.001))) is False

    def test_zanedbatelna_odchylka_je_identita(self) -> None:
        # Zaokrúhľovacia chyba z roundtripu nesmie hlásiť „model je posunutý".
        assert is_identity(Transform(xyz=(1e-15, 0.0, 0.0))) is True


class TestNormalizaciaUhla:
    @pytest.mark.parametrize(
        ("angle", "expected"),
        [(0.0, 0.0), (90.0, 90.0), (180.0, 180.0), (-90.0, -90.0)],
    )
    def test_uhly_v_rozsahu_zostanu(self, angle: float, expected: float) -> None:
        assert normalize_degrees(angle) == pytest.approx(expected)

    def test_plna_otacka_je_nula(self) -> None:
        assert normalize_degrees(360.0) == pytest.approx(0.0)

    def test_viac_otaciek_sa_zlozi(self) -> None:
        assert normalize_degrees(720.0 + 45.0) == pytest.approx(45.0)

    def test_zaporny_velky_uhol_sa_zlozi(self) -> None:
        assert normalize_degrees(-450.0) == pytest.approx(-90.0)

    def test_vysledok_je_vzdy_v_rozsahu(self) -> None:
        for angle in (-1000.0, -37.0, 0.0, 199.0, 5000.0):
            assert -180.0 < normalize_degrees(angle) <= 180.0
