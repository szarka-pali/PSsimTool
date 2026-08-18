"""Tests of the orbit camera.

The whole of the control maths is pure, so it can be checked without a window. That is
the point: "the model spins strangely" is otherwise a bug you can only debug by eye.
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


class TestCameraPosition:
    def test_the_default_view_is_from_the_front(self) -> None:
        # azimuth = 0 means the camera at -Y, looking at +Y.
        assert camera().eye == pytest.approx((0.0, -10.0, 0.0), abs=1e-9)

    def test_an_azimuth_of_90_degrees_gives_a_side_view(self) -> None:
        eye = camera(azimuth_rad=math.pi / 2).eye

        assert eye == pytest.approx((10.0, 0.0, 0.0), abs=1e-9)

    def test_elevation_lifts_the_camera(self) -> None:
        eye = camera(elevation_rad=math.pi / 4).eye

        assert eye[2] == pytest.approx(10.0 * math.sin(math.pi / 4), abs=1e-9)

    def test_vzdialenost_od_ciela_sedi(self) -> None:
        instance = camera(azimuth_rad=0.7, elevation_rad=0.3)
        eye = instance.eye

        assert math.dist(eye, instance.target) == pytest.approx(10.0, abs=1e-9)

    def test_a_moved_target_moves_the_camera(self) -> None:
        eye = camera(target=(5.0, 5.0, 5.0)).eye

        assert eye == pytest.approx((5.0, -5.0, 5.0), abs=1e-9)

    def test_forward_points_at_the_target(self) -> None:
        assert camera().forward == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)

    def test_right_is_perpendicular_to_forward(self) -> None:
        instance = camera(azimuth_rad=0.9, elevation_rad=0.4)
        right, forward = instance.right, instance.forward

        dot = sum(a * b for a, b in zip(right, forward, strict=True))

        assert dot == pytest.approx(0.0, abs=1e-9)

    def test_the_camera_does_not_roll_sideways(self) -> None:
        # `right` has to stay horizontal, or the image tilts and that is unpleasant.
        assert camera(elevation_rad=1.2).right[2] == pytest.approx(0.0, abs=1e-12)

    def test_up_is_unit_length(self) -> None:
        up = camera(azimuth_rad=0.3, elevation_rad=-0.8).up

        assert math.sqrt(sum(component**2 for component in up)) == pytest.approx(1.0, abs=1e-9)


class TestOrbiting:
    def test_orbit_zmeni_azimut(self) -> None:
        turned = camera().orbit(0.5, 0.0)

        assert turned.azimuth_rad == pytest.approx(0.5, abs=1e-12)

    def test_orbit_nemeni_vzdialenost(self) -> None:
        turned = camera().orbit(1.0, 0.5)

        assert turned.distance_m == pytest.approx(10.0, abs=1e-12)

    def test_orbit_nemeni_ciel(self) -> None:
        turned = camera(target=(1.0, 2.0, 3.0)).orbit(1.0, 0.5)

        assert turned.target == (1.0, 2.0, 3.0)

    def test_elevation_is_clamped_below_the_pole(self) -> None:
        # Exactly at the zenith, lookAt loses its "up" reference and the image flips.
        turned = camera().orbit(0.0, 10.0)

        assert turned.elevation_rad == pytest.approx(MAX_ELEVATION_RAD, abs=1e-12)

    def test_elevation_is_clamped_at_the_bottom_too(self) -> None:
        turned = camera().orbit(0.0, -10.0)

        assert turned.elevation_rad == pytest.approx(-MAX_ELEVATION_RAD, abs=1e-12)

    def test_azimuth_wraps_around(self) -> None:
        # After many turns the number would otherwise grow without bound.
        turned = camera().orbit(20.0 * math.pi + 0.3, 0.0)

        assert abs(turned.azimuth_rad) <= math.pi

    def test_a_full_turn_returns_the_same_view(self) -> None:
        turned = camera().orbit(2.0 * math.pi, 0.0)

        assert turned.eye == pytest.approx(camera().eye, abs=1e-9)

    def test_the_original_camera_is_left_unchanged(self) -> None:
        original = camera()

        original.orbit(1.0, 1.0)

        assert original.azimuth_rad == 0.0


class TestZooming:
    def test_faktor_pod_jedna_priblizi(self) -> None:
        assert camera().zoom(0.5).distance_m == pytest.approx(5.0, abs=1e-12)

    def test_faktor_nad_jedna_oddiali(self) -> None:
        assert camera().zoom(2.0).distance_m == pytest.approx(20.0, abs=1e-12)

    def test_zoom_is_multiplicative(self) -> None:
        # Two equal steps must give the same result as one of twice the size.
        twice = camera().zoom(0.8).zoom(0.8)

        assert twice.distance_m == pytest.approx(camera().zoom(0.64).distance_m, abs=1e-12)

    def test_zooming_in_hits_the_lower_limit(self) -> None:
        assert camera().zoom(1e-9).distance_m == pytest.approx(0.1, abs=1e-12)

    def test_zooming_out_hits_the_upper_limit(self) -> None:
        assert camera().zoom(1e9).distance_m == pytest.approx(100.0, abs=1e-12)

    def test_zoom_does_not_change_the_viewing_direction(self) -> None:
        zoomed = camera(azimuth_rad=0.6, elevation_rad=0.2).zoom(0.3)

        assert zoomed.forward == pytest.approx(camera(azimuth_rad=0.6, elevation_rad=0.2).forward)

    def test_a_zero_factor_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="factor"):
            camera().zoom(0.0)


class TestPanning:
    def test_pan_moves_the_target_right(self) -> None:
        moved = camera().pan(2.0, 0.0)

        assert moved.target == pytest.approx((2.0, 0.0, 0.0), abs=1e-9)

    def test_pan_moves_the_target_up(self) -> None:
        moved = camera().pan(0.0, 3.0)

        assert moved.target == pytest.approx((0.0, 0.0, 3.0), abs=1e-9)

    def test_pan_nemeni_vzdialenost_ani_uhly(self) -> None:
        moved = camera(azimuth_rad=0.4, elevation_rad=0.2).pan(1.0, 2.0)

        assert moved.distance_m == pytest.approx(10.0)
        assert moved.azimuth_rad == pytest.approx(0.4)

    def test_pan_follows_the_viewing_direction(self) -> None:
        # Seen from the side, "right across the screen" must be a different world direction.
        moved = camera(azimuth_rad=math.pi / 2).pan(2.0, 0.0)

        assert moved.target == pytest.approx((0.0, 2.0, 0.0), abs=1e-9)

    def test_pan_in_pixels_scales_with_the_distance(self) -> None:
        # The point under the cursor should stay under the cursor regardless of the zoom.
        near = camera(distance_m=1.0).pan_pixels(100.0, 0.0, viewport_height_px=800)
        far = camera(distance_m=10.0).pan_pixels(100.0, 0.0, viewport_height_px=800)

        assert abs(far.target[0]) == pytest.approx(10.0 * abs(near.target[0]), rel=1e-9)

    def test_dragging_right_moves_the_model_right(self) -> None:
        # So the target goes left — that is the "grab the model and drag it" convention.
        moved = camera().pan_pixels(50.0, 0.0, viewport_height_px=600)

        assert moved.target[0] < 0.0

    def test_nulova_vyska_viewportu_nespadne(self) -> None:
        # Happens when the window is minimised.
        moved = camera().pan_pixels(10.0, 10.0, viewport_height_px=0)

        assert moved.target == camera().target


class TestFraming:
    def test_the_target_is_the_centre_of_the_model(self) -> None:
        framed = OrbitCamera.framing(center=(1.0, 2.0, 3.0), radius_m=0.5)

        assert framed.target == (1.0, 2.0, 3.0)

    def test_the_whole_model_fits_in_the_frame(self) -> None:
        radius, fov = 0.5, 40.0
        framed = OrbitCamera.framing(center=(0.0, 0.0, 0.0), radius_m=radius, fov_deg=fov)

        visible_radius = framed.distance_m * math.sin(math.radians(fov) / 2)

        assert visible_radius >= radius

    def test_vacsi_model_znamena_vacsiu_vzdialenost(self) -> None:
        small = OrbitCamera.framing((0.0, 0.0, 0.0), 0.1)
        big = OrbitCamera.framing((0.0, 0.0, 0.0), 10.0)

        assert big.distance_m > small.distance_m

    def test_the_zoom_limits_follow_the_size(self) -> None:
        framed = OrbitCamera.framing((0.0, 0.0, 0.0), 2.0)

        assert framed.min_distance_m < framed.distance_m < framed.max_distance_m

    def test_nulovy_polomer_nespadne(self) -> None:
        # Happens with an empty scene, or with a single point.
        framed = OrbitCamera.framing((0.0, 0.0, 0.0), 0.0)

        assert framed.distance_m > 0.0


class TestClipPlanes:
    def test_near_is_below_the_distance(self) -> None:
        near, _ = camera(distance_m=0.2).clip_planes()

        assert 0.0 < near < 0.2

    def test_far_is_beyond_the_model(self) -> None:
        _, far = camera(distance_m=0.2).clip_planes()

        assert far > 0.2

    def test_roviny_skaluju_so_vzdialenostou(self) -> None:
        blizko, _ = camera(distance_m=0.1).clip_planes()
        daleko, _ = camera(distance_m=100.0).clip_planes()

        assert daleko > blizko


class TestMouseDragging:
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

    def test_pan_moves_the_target_in_the_scene(self) -> None:
        dragged = apply_drag(camera(), DragAction.PAN, 100.0, 0.0, viewport_height_px=800)

        assert dragged.target != camera().target

    def test_ziadna_akcia_nic_nezmeni(self) -> None:
        dragged = apply_drag(camera(), DragAction.NONE, 100.0, 50.0, viewport_height_px=800)

        assert dragged == camera()

    def test_the_opposite_drag_brings_the_camera_back(self) -> None:
        start = camera()

        there = apply_drag(start, DragAction.ORBIT, 60.0, 30.0, viewport_height_px=800)
        back = apply_drag(there, DragAction.ORBIT, -60.0, -30.0, viewport_height_px=800)

        assert back.azimuth_rad == pytest.approx(start.azimuth_rad, abs=1e-9)
        assert back.elevation_rad == pytest.approx(start.elevation_rad, abs=1e-9)

    def test_a_longer_drag_turns_further(self) -> None:
        small = apply_drag(camera(), DragAction.ORBIT, 10.0, 0.0, viewport_height_px=800)
        big = apply_drag(camera(), DragAction.ORBIT, 100.0, 0.0, viewport_height_px=800)

        assert abs(big.azimuth_rad) > abs(small.azimuth_rad)


class TestWheel:
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


class TestStandardViews:
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

    def test_the_front_view_is_on_negative_y(self) -> None:
        assert camera().with_view("front").eye == pytest.approx((0.0, -10.0, 0.0), abs=1e-9)

    def test_the_back_view_is_on_positive_y(self) -> None:
        assert camera().with_view("back").eye == pytest.approx((0.0, 10.0, 0.0), abs=1e-9)

    def test_the_right_view_is_on_positive_x(self) -> None:
        assert camera().with_view("right").eye == pytest.approx((10.0, 0.0, 0.0), abs=1e-9)

    def test_the_left_view_is_on_negative_x(self) -> None:
        assert camera().with_view("left").eye == pytest.approx((-10.0, 0.0, 0.0), abs=1e-9)

    def test_the_top_view_is_above_the_model(self) -> None:
        assert camera().with_view("top").eye[2] > 9.9

    def test_the_bottom_view_is_below_the_model(self) -> None:
        assert camera().with_view("bottom").eye[2] < -9.9

    def test_top_is_not_exactly_at_the_zenith(self) -> None:
        # Exactly at the pole, lookAt loses its "up" reference and the image flips.
        assert abs(camera().with_view("top").elevation_rad) < math.pi / 2

    def test_prepnutie_zachova_priblizenie(self) -> None:
        # The user wants to change the angle, not lose the zoom they set.
        zoomed = camera().zoom(0.3)

        assert zoomed.with_view("top").distance_m == pytest.approx(zoomed.distance_m)

    def test_prepnutie_zachova_ciel(self) -> None:
        moved = camera(target=(1.0, 2.0, 3.0))

        assert moved.with_view("front").target == (1.0, 2.0, 3.0)

    def test_an_unknown_view_lists_the_supported_ones(self) -> None:
        with pytest.raises(ValueError, match="supported:"):
            camera().with_view("zozadu-zhora")

    @pytest.mark.parametrize("name", sorted(STANDARD_VIEWS))
    def test_every_view_gives_a_valid_camera(self, name: str) -> None:
        instance = camera().with_view(name)

        assert math.dist(instance.eye, instance.target) == pytest.approx(10.0, abs=1e-9)


class TestAxisProjection:
    def test_zpredu_ide_x_doprava(self) -> None:
        screen_x, screen_y = camera().with_view("front").project((1.0, 0.0, 0.0))

        assert screen_x == pytest.approx(1.0, abs=1e-9)
        assert screen_y == pytest.approx(0.0, abs=1e-9)

    def test_from_the_front_z_points_up(self) -> None:
        assert camera().with_view("front").project((0.0, 0.0, 1.0)) == pytest.approx(
            (0.0, 1.0), abs=1e-9
        )

    def test_from_the_front_y_points_into_the_screen(self) -> None:
        # An axis parallel to the viewing direction projects to a point.
        screen = camera().with_view("front").project((0.0, 1.0, 0.0))

        assert screen == pytest.approx((0.0, 0.0), abs=1e-9)

    def test_zhora_ide_y_hore(self) -> None:
        _, screen_y = camera().with_view("top").project((0.0, 1.0, 0.0))

        assert screen_y > 0.9

    def test_from_the_top_z_points_into_the_screen(self) -> None:
        screen = camera().with_view("top").project((0.0, 0.0, 1.0))

        assert abs(screen[0]) < 0.05
        assert abs(screen[1]) < 0.05

    def test_zprava_ide_y_doprava(self) -> None:
        screen_x, _ = camera().with_view("right").project((0.0, 1.0, 0.0))

        assert screen_x == pytest.approx(1.0, abs=1e-9)

    def test_a_projection_never_exceeds_one(self) -> None:
        # The projection of a unit vector cannot be longer than one.
        instance = camera().with_view("iso")
        for direction in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            screen = instance.project(direction)
            assert math.hypot(*screen) <= 1.0 + 1e-9


class TestMouseBindings:
    def test_stredne_tlacidlo_otaca(self) -> None:
        assert drag_action(2) is DragAction.ORBIT

    def test_shift_so_strednym_posuva(self) -> None:
        assert drag_action(2, shift=True) is DragAction.PAN

    def test_prave_tlacidlo_posuva(self) -> None:
        assert drag_action(3) is DragAction.PAN

    def test_lave_tlacidlo_otaca(self) -> None:
        assert drag_action(1) is DragAction.ORBIT

    def test_an_unknown_button_does_nothing(self) -> None:
        assert drag_action(9) is DragAction.NONE
