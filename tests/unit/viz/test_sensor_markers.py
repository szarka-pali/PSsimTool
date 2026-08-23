"""Tests of the sensor marker colour convention.

The one pure function here — everything else needs real Panda3D geometry, and is
covered by `tests/integration/test_viz_sensor_markers.py`.
"""

from __future__ import annotations

from pssim.viz.sensor_markers import ACTIVE_COLOR, CLEAR_COLOR, sensor_color


class TestSensorColor:
    def test_active_is_the_active_color(self) -> None:
        assert sensor_color(True) == ACTIVE_COLOR

    def test_clear_is_the_clear_color(self) -> None:
        assert sensor_color(False) == CLEAR_COLOR

    def test_the_two_colors_differ(self) -> None:
        assert ACTIVE_COLOR != CLEAR_COLOR


class TestWhichColorIsGreen:
    """Pinned by channel, not symbolically.

    The tests above compare `sensor_color(True)` against `ACTIVE_COLOR` and pass
    whichever hue that constant holds — which is exactly how the convention
    ended up inverted without a test noticing.
    """

    def test_a_sensor_that_sees_something_is_green(self) -> None:
        red, green, blue, _alpha = sensor_color(True)

        assert green > red and green > blue

    def test_a_sensor_that_sees_nothing_is_red(self) -> None:
        red, green, blue, _alpha = sensor_color(False)

        assert red > green and red > blue
