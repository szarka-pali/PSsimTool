"""Tests of converting a placement between the user's units and the scene's.

The user enters millimetres and degrees, the scene runs in metres and radians. Six
fields times two directions is plenty of opportunity for a typo, which is why there is
a test per axis — a mistake in one axis would otherwise pass.
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


class TestTranslation:
    def test_millimetres_convert_to_metres(self) -> None:
        transform = to_transform(PlacementDisplay(x_mm=1000.0))

        assert transform.xyz[0] == pytest.approx(1.0)

    def test_each_axis_goes_into_its_own_component(self) -> None:
        # Swapping axes is the classic mistake one summary test does not catch.
        transform = to_transform(PlacementDisplay(x_mm=100.0, y_mm=200.0, z_mm=300.0))

        assert transform.xyz == pytest.approx((0.1, 0.2, 0.3))

    def test_a_negative_offset_keeps_its_sign(self) -> None:
        transform = to_transform(PlacementDisplay(y_mm=-50.0))

        assert transform.xyz[1] == pytest.approx(-0.05)

    def test_zero_values_give_the_identity(self) -> None:
        assert to_transform(PlacementDisplay()) == IDENTITY_PLACEMENT


class TestRotating:
    def test_degrees_convert_to_radians(self) -> None:
        transform = to_transform(PlacementDisplay(rotate_z_deg=180.0))

        assert transform.rpy[2] == pytest.approx(math.pi)

    def test_each_axis_goes_into_its_own_component(self) -> None:
        transform = to_transform(PlacementDisplay(rotate_x_deg=90.0, rotate_y_deg=45.0))

        assert transform.rpy == pytest.approx((math.pi / 2, math.pi / 4, 0.0))

    def test_rotation_does_not_change_the_translation(self) -> None:
        transform = to_transform(PlacementDisplay(rotate_x_deg=33.0))

        assert transform.xyz == (0.0, 0.0, 0.0)

    def test_translation_does_not_change_the_rotation(self) -> None:
        transform = to_transform(PlacementDisplay(x_mm=1234.0))

        assert transform.rpy == (0.0, 0.0, 0.0)


class TestConversionBack:
    def test_a_round_trip_preserves_the_values(self) -> None:
        original = PlacementDisplay(1.5, -2.5, 3.5, 10.0, -20.0, 30.0)

        assert from_transform(to_transform(original)).as_tuple == pytest.approx(original.as_tuple)

    def test_metres_are_shown_as_millimetres(self) -> None:
        display = from_transform(Transform(xyz=(0.25, 0.0, 0.0)))

        assert display.x_mm == pytest.approx(250.0)

    def test_radians_are_shown_as_degrees(self) -> None:
        display = from_transform(Transform(rpy=(0.0, math.pi / 2, 0.0)))

        assert display.rotate_y_deg == pytest.approx(90.0)


class TestIdentity:
    def test_a_zero_transform_is_the_identity(self) -> None:
        assert is_identity(Transform()) is True

    def test_a_translation_is_not_the_identity(self) -> None:
        assert is_identity(Transform(xyz=(0.001, 0.0, 0.0))) is False

    def test_a_rotation_is_not_the_identity(self) -> None:
        assert is_identity(Transform(rpy=(0.0, 0.0, 0.001))) is False

    def test_a_negligible_deviation_counts_as_the_identity(self) -> None:
        # A rounding error from the round trip must not report "the model has moved".
        assert is_identity(Transform(xyz=(1e-15, 0.0, 0.0))) is True


class TestAngleNormalisation:
    @pytest.mark.parametrize(
        ("angle", "expected"),
        [(0.0, 0.0), (90.0, 90.0), (180.0, 180.0), (-90.0, -90.0)],
    )
    def test_angles_in_range_are_left_alone(self, angle: float, expected: float) -> None:
        assert normalize_degrees(angle) == pytest.approx(expected)

    def test_a_full_turn_is_zero(self) -> None:
        assert normalize_degrees(360.0) == pytest.approx(0.0)

    def test_several_turns_fold_down(self) -> None:
        assert normalize_degrees(720.0 + 45.0) == pytest.approx(45.0)

    def test_a_large_negative_angle_folds_down(self) -> None:
        assert normalize_degrees(-450.0) == pytest.approx(-90.0)

    def test_the_result_is_always_in_range(self) -> None:
        for angle in (-1000.0, -37.0, 0.0, 199.0, 5000.0):
            assert -180.0 < normalize_degrees(angle) <= 180.0
