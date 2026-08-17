"""Testy kartézskeho kríža.

Geometria kríža je čistá funkcia, takže sa dá overiť bez Panda3D. Kreslenie
samotné pokrývajú integračné testy.
"""

from __future__ import annotations

import math

import pytest

from pssim.viz.axes import (
    AXIS_COLORS,
    AXIS_DIRECTIONS,
    DEFAULT_SCALE,
    MIN_LENGTH_M,
    axis_length_for,
    axis_segments,
)


class TestDlzkaOsi:
    def test_skaluje_s_velkostou_sceny(self) -> None:
        # Fixná dĺžka nefunguje: na 0,2 m dieli by meter dlhá os zaplnila obraz.
        assert axis_length_for(10.0) > axis_length_for(1.0)

    def test_je_podielom_polomeru(self) -> None:
        assert axis_length_for(4.0) == pytest.approx(4.0 * DEFAULT_SCALE)

    def test_kriz_je_mensi_ako_model(self) -> None:
        # Inak by orientačná pomôcka prekričala to, čo si má používateľ pozrieť.
        assert axis_length_for(2.0) < 2.0

    @pytest.mark.parametrize("radius", [0.0, -1.0, 1e-9])
    def test_prazdna_scena_dostane_minimalnu_dlzku(self, radius: float) -> None:
        assert axis_length_for(radius) == pytest.approx(MIN_LENGTH_M)


class TestSegmenty:
    def test_su_tri_osi(self) -> None:
        assert len(axis_segments(1.0)) == 3

    def test_pomenovanie_je_xyz(self) -> None:
        assert {segment.name for segment in axis_segments(1.0)} == {"X", "Y", "Z"}

    def test_vsetky_zacinaju_v_pociatku(self) -> None:
        # Kríž ukazuje nulu modelu — keby nezačínal v počiatku, klamal by.
        assert all(segment.start == (0.0, 0.0, 0.0) for segment in axis_segments(2.0))

    def test_dlzka_ramena_sedi(self) -> None:
        for segment in axis_segments(3.0):
            assert math.dist(segment.start, segment.end) == pytest.approx(3.0, abs=1e-9)

    def test_osi_mieria_do_kladnych_smerov(self) -> None:
        by_name = {segment.name: segment for segment in axis_segments(1.0)}

        assert by_name["X"].end == pytest.approx((1.0, 0.0, 0.0))
        assert by_name["Y"].end == pytest.approx((0.0, 1.0, 0.0))
        assert by_name["Z"].end == pytest.approx((0.0, 0.0, 1.0))

    def test_farby_su_cad_konvencia(self) -> None:
        # X červená, Y zelená, Z modrá — to čaká každý, kto videl CAD.
        assert AXIS_COLORS["X"][0] > AXIS_COLORS["X"][1]
        assert AXIS_COLORS["Y"][1] > AXIS_COLORS["Y"][0]
        assert AXIS_COLORS["Z"][2] > AXIS_COLORS["Z"][0]

    def test_kazda_os_ma_svoju_farbu(self) -> None:
        colors = {segment.color for segment in axis_segments(1.0)}

        assert len(colors) == 3

    def test_popisok_je_za_koncom_osi(self) -> None:
        # Keby sedel presne na konci, prekrýval by čiaru.
        for segment in axis_segments(1.0):
            assert math.dist(segment.start, segment.label_position) > 1.0

    def test_nulova_dlzka_nespadne(self) -> None:
        segments = axis_segments(0.0)

        assert math.dist(segments[0].start, segments[0].end) == pytest.approx(MIN_LENGTH_M)

    def test_smery_zodpovedaju_tabulke(self) -> None:
        by_name = {segment.name: segment for segment in axis_segments(5.0)}

        for name, direction in AXIS_DIRECTIONS.items():
            expected = tuple(component * 5.0 for component in direction)
            assert by_name[name].end == pytest.approx(expected)
