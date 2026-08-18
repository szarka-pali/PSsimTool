"""Tests of the unit conversions.

This is the most likely silent bug in the whole project, which is why the numbers here
are concrete.
"""

from __future__ import annotations

import math

import pytest

from pssim.domain.units import encoder_increments_to_rad, length_scale_to_m


class TestLength:
    @pytest.mark.parametrize(
        ("unit", "expected"),
        [("m", 1.0), ("mm", 1e-3), ("um", 1e-6), ("in", 0.0254)],
    )
    def test_conversion_to_metres(self, unit: str, expected: float) -> None:
        assert length_scale_to_m(unit) == pytest.approx(expected, rel=1e-12)

    def test_1000_mm_is_1_metre(self) -> None:
        assert 1000.0 * length_scale_to_m("mm") == pytest.approx(1.0)

    def test_an_unknown_unit_lists_the_supported_ones(self) -> None:
        with pytest.raises(ValueError, match="supported: in, m, mm, um"):
            length_scale_to_m("stopa")


class TestEncoder:
    def test_4096_increments_is_a_full_turn(self) -> None:
        scale = encoder_increments_to_rad(4096)

        assert 4096 * scale == pytest.approx(2 * math.pi)

    def test_half_the_range_is_pi(self) -> None:
        assert 2048 * encoder_increments_to_rad(4096) == pytest.approx(math.pi)

    def test_zero_increments_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            encoder_increments_to_rad(0)
