"""Tests of `ui.labels`: the sentences the UI assembles out of numbers.

The sensor wording is the interesting part. "Clear" is only the honest word for a
sensor that detects presence — a rangefinder with nothing in front of it is *out
of range*, and an encoder detects nothing at all, so it has no state to report.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.domain.sensors import Sensor, SensorKind, SensorReading  # noqa: E402
from pssim.ui.labels import (  # noqa: E402
    NOT_APPLICABLE,
    describe_reading,
    describe_state,
    describe_state_tooltip,
    has_detection_state,
)
from pssim.ui.sensor_registry import SensorEntry  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def entry(
    kind: SensorKind,
    *,
    is_active: bool = False,
    value: float = 0.0,
    is_valid: bool = True,
    range_m: float = 2.5,
) -> SensorEntry:
    return SensorEntry(
        sensor_id="sensor-1",
        sensor=Sensor(name="s", kind=kind, direction=(1.0, 0.0, 0.0), range_m=range_m),
        is_active=is_active,
        reading=SensorReading(value=value, is_valid=is_valid),
    )


class TestPresenceState:
    @pytest.mark.parametrize("kind", [SensorKind.BEAM, SensorKind.INDUCTIVE, SensorKind.PROXIMITY])
    def test_something_there_reads_detected(self, qt_app: QApplication, kind: SensorKind) -> None:
        assert describe_state(entry(kind, is_active=True, value=1.0)) == "Detected"

    @pytest.mark.parametrize("kind", [SensorKind.BEAM, SensorKind.INDUCTIVE, SensorKind.PROXIMITY])
    def test_nothing_there_reads_clear(self, qt_app: QApplication, kind: SensorKind) -> None:
        assert describe_state(entry(kind)) == "Clear"

    def test_the_tooltip_says_what_clear_means(self, qt_app: QApplication) -> None:
        assert describe_state_tooltip(entry(SensorKind.BEAM)) == "Nothing is crossing the beam"

    def test_the_zone_gets_its_own_wording(self, qt_app: QApplication) -> None:
        assert describe_state_tooltip(entry(SensorKind.PROXIMITY)) == "The zone is empty"


class TestDistanceState:
    @pytest.mark.parametrize("kind", [SensorKind.TOF, SensorKind.LASER_DISTANCE])
    def test_a_measurement_reads_in_range(self, qt_app: QApplication, kind: SensorKind) -> None:
        assert describe_state(entry(kind, is_active=True, value=0.3)) == "In range"

    @pytest.mark.parametrize("kind", [SensorKind.TOF, SensorKind.LASER_DISTANCE])
    def test_nothing_in_front_is_not_clear(self, qt_app: QApplication, kind: SensorKind) -> None:
        # This is the reading that used to say "Clear", which reads as "nothing is
        # in the way" when it actually means the sensor cannot see that far.
        assert describe_state(entry(kind, value=2.5, is_valid=False)) == "Out of range"

    def test_the_tooltip_names_the_range(self, qt_app: QApplication) -> None:
        entry_out = entry(SensorKind.TOF, value=2.5, is_valid=False, range_m=2.5)

        assert describe_state_tooltip(entry_out) == "Nothing within the 2500 mm range"


class TestEncoderState:
    @pytest.mark.parametrize("kind", [SensorKind.ENCODER_INC, SensorKind.ENCODER_ABS])
    def test_an_encoder_has_no_detection_state(
        self, qt_app: QApplication, kind: SensorKind
    ) -> None:
        # `is_active` is False for every encoder, always — so any word for it
        # would claim the sensor is failing to see something it never looks for.
        assert describe_state(entry(kind, value=1234.0)) == NOT_APPLICABLE

    @pytest.mark.parametrize("kind", [SensorKind.ENCODER_INC, SensorKind.ENCODER_ABS])
    def test_an_encoder_is_not_a_detector(self, qt_app: QApplication, kind: SensorKind) -> None:
        assert has_detection_state(kind) is False

    @pytest.mark.parametrize(
        "kind",
        [
            SensorKind.BEAM,
            SensorKind.INDUCTIVE,
            SensorKind.PROXIMITY,
            SensorKind.TOF,
            SensorKind.LASER_DISTANCE,
        ],
    )
    def test_everything_else_is(self, qt_app: QApplication, kind: SensorKind) -> None:
        assert has_detection_state(kind) is True

    def test_the_tooltip_explains_why(self, qt_app: QApplication) -> None:
        tooltip = describe_state_tooltip(entry(SensorKind.ENCODER_ABS))

        assert tooltip == "An encoder detects nothing — it reports the angle of its axis"


class TestReading:
    def test_a_distance_is_shown_in_millimetres(self, qt_app: QApplication) -> None:
        assert describe_reading(entry(SensorKind.TOF, value=0.3)) == "300.0 mm"

    def test_an_unmeasurable_distance_is_a_dash(self, qt_app: QApplication) -> None:
        # Not the range: that number would look like a measurement.
        assert describe_reading(entry(SensorKind.TOF, value=2.5, is_valid=False)) == "—"

    def test_an_encoder_reads_counts(self, qt_app: QApplication) -> None:
        assert describe_reading(entry(SensorKind.ENCODER_INC, value=1234.0)) == "1234"

    def test_a_presence_sensor_reads_zero_or_one(self, qt_app: QApplication) -> None:
        assert describe_reading(entry(SensorKind.BEAM, is_active=True, value=1.0)) == "1"
