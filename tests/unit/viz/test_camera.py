"""Tests of camera framing.

Pure functions, no Panda3D. What matters is that everything is derived from the **size
of the scene** — fixed values do not work here, and they are exactly what caused the
first empty render.
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


class TestCameraDistance:
    def test_a_larger_scene_needs_a_larger_distance(self) -> None:
        assert frame_distance(1.0) > frame_distance(0.1)

    def test_skaluje_linearne_s_polomerom(self) -> None:
        assert frame_distance(2.0) == pytest.approx(2 * frame_distance(1.0), rel=1e-9)

    def test_sirsi_zorny_uhol_dovoli_ist_blizsie(self) -> None:
        assert frame_distance(1.0, fov_deg=90.0) < frame_distance(1.0, fov_deg=30.0)

    def test_the_whole_model_fits_in_the_frame(self) -> None:
        # Half the field of view has to "cover" the scene radius from distance d.
        radius = 0.5
        distance = frame_distance(radius, DEFAULT_FOV_DEG)

        max_visible_radius = distance * math.sin(math.radians(DEFAULT_FOV_DEG) / 2)

        assert max_visible_radius >= radius

    @pytest.mark.parametrize("radius", [0.0, -1.0])
    def test_a_nonsense_radius_uses_the_fallback(self, radius: float) -> None:
        assert frame_distance(radius) == pytest.approx(frame_distance(FALLBACK_RADIUS_M))

    def test_nulovy_zorny_uhol_nespadne(self) -> None:
        assert frame_distance(1.0, fov_deg=0.0) > 0.0


class TestClipPlanes:
    def test_near_is_below_the_scene_size(self) -> None:
        # This is exactly the fault that caused the empty window: a default near of 1.0
        # clips absolutely everything on a 0.1 m machine.
        near, _ = clip_planes(0.113)

        assert near < 0.113

    def test_far_is_above_the_scene_size(self) -> None:
        _, far = clip_planes(0.113)

        assert far > 0.113

    def test_the_planes_scale_with_the_scene(self) -> None:
        small_near, small_far = clip_planes(0.1)
        big_near, big_far = clip_planes(10.0)

        assert big_near > small_near
        assert big_far > small_far

    def test_near_is_always_positive(self) -> None:
        near, _ = clip_planes(0.001)

        assert near > 0.0

    def test_the_far_near_ratio_is_sane_for_the_depth_buffer(self) -> None:
        # Too large a ratio destroys depth buffer precision and the faces start flickering.
        near, far = clip_planes(5.0)

        assert far / near <= 100_000

    @pytest.mark.parametrize("radius", [0.0, -1.0])
    def test_a_nonsense_radius_uses_the_fallback(self, radius: float) -> None:
        assert clip_planes(radius) == clip_planes(FALLBACK_RADIUS_M)


class TestViewDirections:
    def test_the_default_view_is_known(self) -> None:
        assert "iso" in STANDARD_VIEWS

    @pytest.mark.parametrize("name", sorted(STANDARD_VIEWS))
    def test_no_direction_is_the_zero_vector(self, name: str) -> None:
        # A zero vector would give NaN when normalised and the scene would disappear.
        assert any(component != 0.0 for component in view_direction(name))

    def test_front_a_back_su_opacne(self) -> None:
        front = view_direction("front")
        back = view_direction("back")

        assert front == pytest.approx(tuple(-component for component in back))

    def test_top_is_not_exactly_on_the_up_axis(self) -> None:
        # Pure +Z would break lookAt — the viewing direction would be parallel to up.
        assert view_direction("top")[1] != 0.0

    def test_the_directions_are_unit_length(self) -> None:
        for name in STANDARD_VIEWS:
            length = math.sqrt(sum(component**2 for component in view_direction(name)))
            assert length == pytest.approx(1.0, abs=1e-9), name

    def test_lavy_a_pravy_su_opacne(self) -> None:
        left = view_direction("left")
        right = view_direction("right")

        assert left == pytest.approx(tuple(-component for component in right), abs=1e-9)

    def test_top_a_bottom_su_opacne_vo_vyske(self) -> None:
        assert view_direction("top")[2] == pytest.approx(-view_direction("bottom")[2], abs=1e-9)

    def test_the_front_view_is_on_negative_y(self) -> None:
        assert view_direction("front") == pytest.approx((0.0, -1.0, 0.0), abs=1e-9)

    def test_neznamy_smer_vypise_podporovane(self) -> None:
        with pytest.raises(ValueError, match="supported:"):
            view_direction("zozadu-zhora")
