"""Testy prevodov jednotiek.

Toto je najpravdepodobnejší tichý bug v celom projekte, preto sú tu konkrétne čísla.
"""

from __future__ import annotations

import math

import pytest

from pssim.domain.units import encoder_increments_to_rad, length_scale_to_m


class TestDlzka:
    @pytest.mark.parametrize(
        ("unit", "expected"),
        [("m", 1.0), ("mm", 1e-3), ("um", 1e-6), ("in", 0.0254)],
    )
    def test_prevod_na_metre(self, unit: str, expected: float) -> None:
        assert length_scale_to_m(unit) == pytest.approx(expected, rel=1e-12)

    def test_1000_mm_je_1_meter(self) -> None:
        assert 1000.0 * length_scale_to_m("mm") == pytest.approx(1.0)

    def test_neznama_jednotka_vypise_podporovane(self) -> None:
        with pytest.raises(ValueError, match="supported: in, m, mm, um"):
            length_scale_to_m("stopa")


class TestEnkoder:
    def test_4096_inkrementov_je_plna_otacka(self) -> None:
        scale = encoder_increments_to_rad(4096)

        assert 4096 * scale == pytest.approx(2 * math.pi)

    def test_polovica_rozsahu_je_pi(self) -> None:
        assert 2048 * encoder_increments_to_rad(4096) == pytest.approx(math.pi)

    def test_nula_inkrementov_je_chyba(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            encoder_increments_to_rad(0)
