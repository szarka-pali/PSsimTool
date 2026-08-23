"""Tests of sensor construction and the pure geometry that drives a reaction.

No physics, no Panda3D — a sensor's state is a function of other objects'
axis-aligned bounding boxes, computed with plain arithmetic.
"""

from __future__ import annotations

import math

import pytest

from pssim.domain.collision import AABB
from pssim.domain.errors import ConfigError
from pssim.domain.sensors import (
    Sensor,
    SensorDisplay,
    SensorKind,
    from_sensor,
    is_active,
    is_blocked,
    is_triggered,
    nearest_along_ray,
    ray_distance_to,
    read_sensor,
    to_sensor,
)
from tests.factories import beam_sensor, proximity_sensor


class TestSensorConstruction:
    def test_a_valid_beam_is_accepted(self) -> None:
        sensor = beam_sensor(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))

        assert sensor.kind is SensorKind.BEAM

    def test_a_valid_proximity_sensor_is_accepted(self) -> None:
        sensor = proximity_sensor(origin=(0.0, 0.0, 0.0), half_extent_m=0.1)

        assert sensor.kind is SensorKind.PROXIMITY

    def test_an_empty_name_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="non-empty name"):
            beam_sensor(name="")

    def test_a_ray_with_no_direction_is_rejected(self) -> None:
        # A ray is a point plus a direction now, so its degenerate case is a
        # zero direction rather than two coincident points.
        with pytest.raises(ConfigError, match="direction is zero"):
            beam_sensor(origin=(1.0, 2.0, 3.0), direction=(0.0, 0.0, 0.0))

    def test_a_zero_range_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="range_m"):
            beam_sensor(direction=(1.0, 0.0, 0.0), range_m=0.0)

    def test_a_zero_half_extent_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="half_extent_m"):
            proximity_sensor(half_extent_m=0.0)

    def test_a_negative_half_extent_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="half_extent_m"):
            proximity_sensor(half_extent_m=-0.1)


class TestIsBlocked:
    def test_a_box_fully_outside_the_beam_does_not_block_it(self) -> None:
        sensor = beam_sensor(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
        box = AABB(low=(5.0, 5.0, 5.0), high=(6.0, 6.0, 6.0))

        assert is_blocked(sensor, (box,)) is False

    def test_a_box_crossing_the_beam_blocks_it(self) -> None:
        sensor = beam_sensor(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
        box = AABB(low=(0.4, -0.1, -0.1), high=(0.6, 0.1, 0.1))

        assert is_blocked(sensor, (box,)) is True

    def test_a_box_exactly_tangent_to_the_beam_blocks_it(self) -> None:
        # Inclusive boundary: touching counts as blocked, not "not quite".
        sensor = beam_sensor(origin=(0.0, 0.5, 0.5), target=(1.0, 0.5, 0.5))
        box = AABB(low=(0.3, 0.5, 0.5), high=(0.7, 1.0, 1.0))

        assert is_blocked(sensor, (box,)) is True

    def test_a_zero_size_box_exactly_on_the_beam_blocks_it(self) -> None:
        sensor = beam_sensor(origin=(0.0, 5.0, 5.0), target=(10.0, 5.0, 5.0))
        box = AABB(low=(5.0, 5.0, 5.0), high=(5.0, 5.0, 5.0))

        assert is_blocked(sensor, (box,)) is True

    def test_a_beam_parallel_to_an_axis_outside_the_box_on_that_axis(self) -> None:
        # The beam runs along X at y=0; the box sits at y=5 — no overlap however
        # far the beam extends in X.
        sensor = beam_sensor(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
        box = AABB(low=(-100.0, 5.0, -1.0), high=(100.0, 6.0, 1.0))

        assert is_blocked(sensor, (box,)) is False

    def test_a_beam_parallel_to_an_axis_inside_the_box_on_that_axis(self) -> None:
        sensor = beam_sensor(origin=(0.0, 0.5, 0.5), target=(1.0, 0.5, 0.5))
        box = AABB(low=(0.4, 0.0, 0.0), high=(0.6, 1.0, 1.0))

        assert is_blocked(sensor, (box,)) is True

    def test_no_other_objects_never_blocks(self) -> None:
        sensor = beam_sensor(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))

        assert is_blocked(sensor, ()) is False


class TestIsTriggered:
    def test_an_object_outside_the_zone_does_not_trigger_it(self) -> None:
        sensor = proximity_sensor(origin=(0.0, 0.0, 0.0), half_extent_m=0.1)
        box = AABB(low=(1.0, 1.0, 1.0), high=(1.1, 1.1, 1.1))

        assert is_triggered(sensor, (box,)) is False

    def test_an_object_exactly_touching_the_zone_triggers_it(self) -> None:
        sensor = proximity_sensor(origin=(0.0, 0.0, 0.0), half_extent_m=0.1)
        box = AABB(low=(0.1, -0.05, -0.05), high=(0.2, 0.05, 0.05))

        assert is_triggered(sensor, (box,)) is True

    def test_an_object_inside_the_zone_triggers_it(self) -> None:
        sensor = proximity_sensor(origin=(0.0, 0.0, 0.0), half_extent_m=0.5)
        box = AABB(low=(-0.05, -0.05, -0.05), high=(0.05, 0.05, 0.05))

        assert is_triggered(sensor, (box,)) is True

    def test_a_zero_size_box_exactly_at_the_zone_edge_triggers_it(self) -> None:
        sensor = proximity_sensor(origin=(0.0, 0.0, 0.0), half_extent_m=0.1)
        box = AABB(low=(0.1, 0.0, 0.0), high=(0.1, 0.0, 0.0))

        assert is_triggered(sensor, (box,)) is True

    def test_no_other_objects_never_triggers(self) -> None:
        sensor = proximity_sensor(origin=(0.0, 0.0, 0.0), half_extent_m=0.1)

        assert is_triggered(sensor, ()) is False


class TestConversion:
    def test_millimetres_convert_to_metres(self) -> None:
        display = SensorDisplay(
            name="beam-1",
            kind=SensorKind.BEAM,
            origin_mm=(100.0, 200.0, 300.0),
            range_mm=1500.0,
        )

        sensor = to_sensor(display)

        assert sensor.origin == pytest.approx((0.1, 0.2, 0.3))
        assert sensor.range_m == pytest.approx(1.5)

    def test_the_direction_stays_unitless(self) -> None:
        # A direction has no length, so it is the one field with no conversion.
        display = SensorDisplay(name="beam-1", kind=SensorKind.BEAM, direction=(0.0, 2.0, 0.0))

        assert to_sensor(display).direction == pytest.approx((0.0, 2.0, 0.0))

    def test_half_extent_converts_to_metres(self) -> None:
        display = SensorDisplay(name="zone-1", kind=SensorKind.PROXIMITY, half_extent_mm=250.0)

        sensor = to_sensor(display)

        assert sensor.half_extent_m == pytest.approx(0.25)

    def test_metres_convert_back_to_millimetres(self) -> None:
        sensor = beam_sensor(
            name="beam-1", origin=(0.1, 0.2, 0.3), direction=(1.0, 0.0, 0.0), range_m=2.5
        )

        display = from_sensor(sensor)

        assert display.origin_mm == pytest.approx((100.0, 200.0, 300.0))
        assert display.range_mm == pytest.approx(2500.0)

    def test_round_trip_preserves_a_beam(self) -> None:
        original = beam_sensor(
            name="beam-1", origin=(0.1, -0.2, 0.3), direction=(0.3, 0.7, -0.9), range_m=1.2
        )

        restored = to_sensor(from_sensor(original))

        assert restored == original

    def test_round_trip_preserves_a_proximity_sensor(self) -> None:
        original = proximity_sensor(name="zone-1", origin=(0.1, 0.2, 0.3), half_extent_m=0.15)

        restored = to_sensor(from_sensor(original))

        assert restored == original


class TestRayDistance:
    """The ray maths every ray kind shares. Distances are real metres, because
    the direction handed in is a unit vector."""

    def _box(self, low: float, high: float) -> AABB:
        return AABB(low=(low, -0.5, -0.5), high=(high, 0.5, 0.5))

    def test_it_reports_the_entry_distance(self) -> None:
        distance = ray_distance_to((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), self._box(0.3, 0.5), 1.0)

        assert distance == pytest.approx(0.3)

    def test_a_box_beyond_the_range_is_not_seen(self) -> None:
        assert ray_distance_to((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), self._box(2.0, 2.5), 1.0) is None

    def test_a_box_exactly_at_the_range_is_seen(self) -> None:
        distance = ray_distance_to((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), self._box(1.0, 1.5), 1.0)

        assert distance == pytest.approx(1.0)

    def test_a_box_behind_the_sensor_is_not_seen(self) -> None:
        # A ray looks one way only; something behind it is not in front of it.
        assert ray_distance_to((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), self._box(-2.0, -1.0), 1.0) is None

    def test_a_sensor_inside_a_box_reads_zero(self) -> None:
        # The honest answer for a sensor buried in something.
        distance = ray_distance_to((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), self._box(-0.5, 0.5), 1.0)

        assert distance == pytest.approx(0.0)

    def test_a_box_off_to_the_side_is_missed(self) -> None:
        aside = AABB(low=(0.3, 5.0, -0.5), high=(0.5, 6.0, 0.5))

        assert ray_distance_to((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), aside, 1.0) is None

    def test_the_nearest_of_several_wins(self) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=2.0, kind=SensorKind.TOF)
        boxes = (self._box(1.0, 1.2), self._box(0.4, 0.6), self._box(1.5, 1.7))

        assert nearest_along_ray(sensor, boxes) == pytest.approx(0.4)

    def test_the_magnitude_of_the_direction_changes_nothing(self) -> None:
        unit = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.TOF)
        huge = beam_sensor(direction=(100.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.TOF)
        boxes = (self._box(0.3, 0.5),)

        assert nearest_along_ray(unit, boxes) == pytest.approx(nearest_along_ray(huge, boxes))


class TestPresenceKinds:
    """`BEAM` and `INDUCTIVE` read 0/1 and share their maths."""

    BLOCKER = AABB(low=(0.2, -0.1, -0.1), high=(0.4, 0.1, 0.1))

    @pytest.mark.parametrize("kind", [SensorKind.BEAM, SensorKind.INDUCTIVE])
    def test_something_in_the_way_reads_one(self, kind: SensorKind) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=kind)

        assert read_sensor(sensor, (self.BLOCKER,)).value == pytest.approx(1.0)

    @pytest.mark.parametrize("kind", [SensorKind.BEAM, SensorKind.INDUCTIVE])
    def test_a_clear_ray_reads_zero(self, kind: SensorKind) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=0.1, kind=kind)

        assert read_sensor(sensor, (self.BLOCKER,)).value == pytest.approx(0.0)

    @pytest.mark.parametrize("kind", [SensorKind.BEAM, SensorKind.INDUCTIVE])
    def test_a_presence_reading_is_always_valid(self, kind: SensorKind) -> None:
        # The valid flag is for the distance kinds; 0 is a real answer here.
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=0.1, kind=kind)

        assert read_sensor(sensor, ()).is_valid is True

    def test_the_two_kinds_read_the_same(self) -> None:
        laser = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.BEAM)
        inductive = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.INDUCTIVE)

        assert read_sensor(laser, (self.BLOCKER,)) == read_sensor(inductive, (self.BLOCKER,))


class TestDistanceKinds:
    """`TOF` and `LASER_DISTANCE` report a distance plus a valid flag."""

    TARGET = AABB(low=(0.3, -0.1, -0.1), high=(0.5, 0.1, 0.1))

    @pytest.mark.parametrize("kind", [SensorKind.TOF, SensorKind.LASER_DISTANCE])
    def test_it_reports_the_distance_in_metres(self, kind: SensorKind) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=kind)

        assert read_sensor(sensor, (self.TARGET,)).value == pytest.approx(0.3)

    @pytest.mark.parametrize("kind", [SensorKind.TOF, SensorKind.LASER_DISTANCE])
    def test_a_reading_in_range_is_valid(self, kind: SensorKind) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=kind)

        assert read_sensor(sensor, (self.TARGET,)).is_valid is True

    @pytest.mark.parametrize("kind", [SensorKind.TOF, SensorKind.LASER_DISTANCE])
    def test_nothing_in_range_reports_the_range_flagged_invalid(self, kind: SensorKind) -> None:
        # Not zero: zero would mean both "touching the sensor" and "nothing
        # there", which are opposite situations.
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=0.2, kind=kind)

        reading = read_sensor(sensor, (self.TARGET,))

        assert reading.value == pytest.approx(0.2)
        assert reading.is_valid is False

    def test_the_two_kinds_read_the_same(self) -> None:
        tof = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.TOF)
        laser = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.LASER_DISTANCE)

        assert read_sensor(tof, (self.TARGET,)) == read_sensor(laser, (self.TARGET,))

    def test_a_touching_target_reads_zero_and_valid(self) -> None:
        # The other half of why the flag exists.
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.TOF)
        touching = AABB(low=(-0.1, -0.1, -0.1), high=(0.1, 0.1, 0.1))

        reading = read_sensor(sensor, (touching,))

        assert reading.value == pytest.approx(0.0)
        assert reading.is_valid is True


class TestEncoders:
    def _encoder(self, kind: SensorKind, counts: int = 3600) -> Sensor:
        return Sensor(name="enc", kind=kind, counts_per_revolution=counts)

    def test_zero_reads_zero(self) -> None:
        assert read_sensor(
            self._encoder(SensorKind.ENCODER_ABS), angle_rad=0.0
        ).value == pytest.approx(0.0)

    def test_a_quarter_turn_is_a_quarter_of_the_counts(self) -> None:
        reading = read_sensor(
            self._encoder(SensorKind.ENCODER_ABS, counts=4000), angle_rad=math.pi / 2.0
        )

        assert reading.value == pytest.approx(1000.0)

    def test_the_resolution_scales_the_reading(self) -> None:
        coarse = read_sensor(self._encoder(SensorKind.ENCODER_ABS, counts=360), angle_rad=1.0)
        fine = read_sensor(self._encoder(SensorKind.ENCODER_ABS, counts=3600), angle_rad=1.0)

        assert fine.value == pytest.approx(coarse.value * 10.0)

    def test_an_absolute_encoder_wraps_at_one_turn(self) -> None:
        # 370 degrees reads as 10 degrees: that is what "absolute" means on a
        # single-turn encoder.
        reading = read_sensor(
            self._encoder(SensorKind.ENCODER_ABS, counts=360),
            angle_rad=math.radians(370.0),
        )

        assert reading.value == pytest.approx(10.0)

    def test_an_incremental_encoder_keeps_counting(self) -> None:
        reading = read_sensor(
            self._encoder(SensorKind.ENCODER_INC, counts=360),
            angle_rad=math.radians(370.0),
        )

        assert reading.value == pytest.approx(370.0)

    def test_an_incremental_encoder_goes_negative(self) -> None:
        reading = read_sensor(
            self._encoder(SensorKind.ENCODER_INC, counts=360),
            angle_rad=math.radians(-90.0),
        )

        assert reading.value == pytest.approx(-90.0)

    def test_an_absolute_encoder_never_goes_negative(self) -> None:
        reading = read_sensor(
            self._encoder(SensorKind.ENCODER_ABS, counts=360),
            angle_rad=math.radians(-90.0),
        )

        assert reading.value == pytest.approx(270.0)

    def test_an_encoder_ignores_the_geometry(self) -> None:
        # It reads an angle; nothing in the scene can change that.
        blocker = AABB(low=(-1.0, -1.0, -1.0), high=(1.0, 1.0, 1.0))
        sensor = self._encoder(SensorKind.ENCODER_ABS, counts=360)

        assert read_sensor(sensor, (blocker,), angle_rad=1.0) == read_sensor(
            sensor, (), angle_rad=1.0
        )

    def test_a_zero_resolution_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="counts_per_revolution"):
            Sensor(name="enc", kind=SensorKind.ENCODER_ABS, counts_per_revolution=0)

    def test_an_encoder_is_never_active(self) -> None:
        # It always has a reading, so colouring a marker by one would say nothing.
        assert is_active(self._encoder(SensorKind.ENCODER_ABS), (), 1.0) is False


class TestIsActive:
    TARGET = AABB(low=(0.3, -0.1, -0.1), high=(0.5, 0.1, 0.1))

    def test_a_blocked_beam_is_active(self) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0)

        assert is_active(sensor, (self.TARGET,)) is True

    def test_a_clear_beam_is_not(self) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=0.1)

        assert is_active(sensor, (self.TARGET,)) is False

    def test_a_distance_sensor_with_a_target_is_active(self) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=1.0, kind=SensorKind.TOF)

        assert is_active(sensor, (self.TARGET,)) is True

    def test_a_distance_sensor_with_nothing_in_range_is_not(self) -> None:
        sensor = beam_sensor(direction=(1.0, 0.0, 0.0), range_m=0.1, kind=SensorKind.TOF)

        assert is_active(sensor, (self.TARGET,)) is False

    def test_it_dispatches_to_the_ray_test_for_a_ray_kind(self) -> None:
        # A box that would trigger a same-sized proximity zone but does not cross
        # the ray - proves dispatch, not agreement between the two.
        sensor = beam_sensor(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
        box = AABB(low=(5.0, 5.0, 5.0), high=(5.2, 5.2, 5.2))

        assert is_active(sensor, (box,)) is False

    def test_it_dispatches_to_the_zone_test_for_a_proximity_sensor(self) -> None:
        sensor = proximity_sensor(origin=(5.0, 5.0, 5.0), half_extent_m=0.5)
        box = AABB(low=(5.0, 5.0, 5.0), high=(5.1, 5.1, 5.1))

        assert is_active(sensor, (box,)) is True
