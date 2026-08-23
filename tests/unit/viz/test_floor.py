"""Tests of the floor grid layout.

Pure functions, no Panda3D — the drawing itself is covered by the integration
tests.
"""

from __future__ import annotations

import pytest

from pssim.viz.floor import (
    MAX_LINES_PER_AXIS,
    MIN_HALF_EXTENT_M,
    floor_grid_lines,
    floor_half_extent_for,
)


class TestHalfExtent:
    def test_grows_with_scene_radius(self) -> None:
        assert floor_half_extent_for(10.0) > floor_half_extent_for(1.0)

    def test_has_a_floor_for_a_tiny_scene(self) -> None:
        assert floor_half_extent_for(0.0) == MIN_HALF_EXTENT_M

    def test_matches_the_margin(self) -> None:
        assert floor_half_extent_for(5.0, margin=2.0) == pytest.approx(10.0)


class TestGridLines:
    def test_line_count_for_a_known_extent(self) -> None:
        # extent=0.5, spacing=0.5 -> index -1..1 -> 3 lines per direction, 6 total.
        assert len(floor_grid_lines(0.5, spacing_m=0.5)) == 6

    def test_every_line_stays_within_the_extent(self) -> None:
        extent = 2.0
        for line in floor_grid_lines(extent, spacing_m=0.5):
            for point in (line.start, line.end):
                assert abs(point[0]) <= extent + 1e-9
                assert abs(point[1]) <= extent + 1e-9
                assert point[2] == 0.0

    def test_a_line_passes_through_the_origin(self) -> None:
        lines = floor_grid_lines(1.0, spacing_m=0.5)
        origins = [line for line in lines if line.start[0] == 0.0 and line.start[1] == -1.0]
        assert origins, "expected a grid line through x=0"

    def test_spacing_widens_for_a_huge_extent(self) -> None:
        # Without widening, 1000 m / 0.1 m would be 10 000 lines per axis.
        lines = floor_grid_lines(1000.0, spacing_m=0.1)
        assert len(lines) <= 2 * (2 * MAX_LINES_PER_AXIS + 1)

    def test_extent_below_the_minimum_is_raised(self) -> None:
        small = floor_grid_lines(0.01, spacing_m=0.5)
        large = floor_grid_lines(MIN_HALF_EXTENT_M, spacing_m=0.5)
        assert small == large
