"""Tests of the pure spin/slider scale math (no Qt, no QApplication needed).

The widget wiring itself is covered by
`tests/integration/test_ui_joint_value_row.py`.
"""

from __future__ import annotations

import pytest

from pssim.ui.joint_value_row import SLIDER_STEPS, slider_to_value, value_to_slider


class TestValueToSlider:
    def test_the_low_end_maps_to_zero(self) -> None:
        assert value_to_slider(0.0, 0.0, 10.0) == 0

    def test_the_high_end_maps_to_the_last_step(self) -> None:
        assert value_to_slider(10.0, 0.0, 10.0) == SLIDER_STEPS

    def test_the_midpoint_maps_to_half(self) -> None:
        assert value_to_slider(5.0, 0.0, 10.0) == SLIDER_STEPS // 2

    def test_a_value_below_low_clamps_to_zero(self) -> None:
        assert value_to_slider(-5.0, 0.0, 10.0) == 0

    def test_a_value_above_high_clamps_to_the_last_step(self) -> None:
        assert value_to_slider(50.0, 0.0, 10.0) == SLIDER_STEPS

    def test_a_zero_width_range_maps_everything_to_zero(self) -> None:
        assert value_to_slider(3.0, 5.0, 5.0) == 0

    def test_a_negative_range_works_the_same_way(self) -> None:
        assert value_to_slider(-5.0, -10.0, 0.0) == SLIDER_STEPS // 2


class TestSliderToValue:
    def test_zero_maps_to_the_low_end(self) -> None:
        assert slider_to_value(0, 0.0, 10.0) == pytest.approx(0.0)

    def test_the_last_step_maps_to_the_high_end(self) -> None:
        assert slider_to_value(SLIDER_STEPS, 0.0, 10.0) == pytest.approx(10.0)

    def test_the_midpoint_maps_back_to_half(self) -> None:
        assert slider_to_value(SLIDER_STEPS // 2, 0.0, 10.0) == pytest.approx(5.0, abs=0.01)


class TestRoundTrip:
    @pytest.mark.parametrize("value", [0.0, 2.5, 5.0, 7.5, 10.0])
    def test_converting_both_ways_recovers_the_value(self, value: float) -> None:
        position = value_to_slider(value, 0.0, 10.0)

        assert slider_to_value(position, 0.0, 10.0) == pytest.approx(value, abs=0.02)
