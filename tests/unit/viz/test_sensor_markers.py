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
