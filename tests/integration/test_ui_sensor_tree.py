"""Tests of `SensorTree`: the rows it renders and what its cells claim.

The colouring is the interesting part. Green sits behind the **Reading**, not
behind the State: the word already says what the state is, whereas a number on
its own does not say whether it is a live measurement or the sensor idling. An
encoder never gets it — it has no in-range notion to report.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QBrush  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.domain.sensors import Sensor, SensorKind, SensorReading  # noqa: E402
from pssim.ui.sensor_registry import SensorRegistry  # noqa: E402
from pssim.ui.sensor_tree import (  # noqa: E402
    COLUMN_KIND,
    COLUMN_NAME,
    COLUMN_READING,
    COLUMN_STATE,
    SensorTree,
)
from pssim.viz.sensor_markers import ACTIVE_COLOR, CLEAR_COLOR  # noqa: E402
from tests.factories import beam_sensor  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def tree(qt_app: QApplication) -> SensorTree:
    return SensorTree()


def registry_with(
    sensor: Sensor, *, is_active: bool = False, reading: SensorReading | None = None
) -> SensorRegistry:
    registry = SensorRegistry()
    entry = registry.add(sensor)
    if reading is not None:
        registry.set_reading(entry.sensor_id, reading)
    registry.set_active(entry.sensor_id, is_active)
    return registry


class TestColumns:
    def test_there_are_four_columns(self, tree: SensorTree) -> None:
        assert tree.columnCount() == 4

    def test_the_headers_name_them(self, tree: SensorTree) -> None:
        header = tree.headerItem()

        assert [header.text(index) for index in range(4)] == [
            "Sensor",
            "Kind",
            "State",
            "Reading",
        ]


class TestRows:
    def test_a_sensor_gets_a_row(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(name="gate")))

        assert tree.topLevelItemCount() == 1

    def test_the_row_shows_the_name(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(name="gate")))

        assert tree.topLevelItem(0).text(COLUMN_NAME) == "gate"

    def test_the_row_shows_the_kind(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(kind=SensorKind.INDUCTIVE)))

        assert tree.topLevelItem(0).text(COLUMN_KIND) == "Inductive"


class TestState:
    def test_a_blocked_beam_reads_detected(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(), is_active=True))

        assert tree.topLevelItem(0).text(COLUMN_STATE) == "Detected"

    def test_an_unblocked_beam_reads_clear(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor()))

        assert tree.topLevelItem(0).text(COLUMN_STATE) == "Clear"

    def test_a_rangefinder_with_nothing_in_front_reads_out_of_range(self, tree: SensorTree) -> None:
        # It used to read "Clear", which says "nothing is in the way" when it
        # means "the sensor cannot see that far".
        tree.refresh(
            registry_with(
                beam_sensor(kind=SensorKind.TOF, range_m=2.5),
                reading=SensorReading(value=2.5, is_valid=False),
            )
        )

        assert tree.topLevelItem(0).text(COLUMN_STATE) == "Out of range"

    def test_an_encoder_reads_a_dash(self, tree: SensorTree) -> None:
        # It used to read "Clear" for ever, because `is_active` is False for
        # every encoder — they look for nothing.
        tree.refresh(
            registry_with(
                Sensor(name="turns", kind=SensorKind.ENCODER_INC),
                reading=SensorReading(value=1234.0),
            )
        )

        assert tree.topLevelItem(0).text(COLUMN_STATE) == "—"

    def test_the_cell_explains_itself(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor()))

        assert tree.topLevelItem(0).toolTip(COLUMN_STATE) == "Nothing is crossing the beam"


#: QColor stores 8 bits per channel, so a float that went through it comes back
#: within one 255th of itself — not to 1e-9, the tolerance the rest of the suite uses.
_CHANNEL_STEP = 1.0 / 255.0


class TestStateIsNotColoured:
    """The State cell carries no background at all any more. The word is the
    statement; a colour behind it added nothing and competed with the reading."""

    def test_a_detecting_sensor_has_no_state_background(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(), is_active=True))

        assert tree.topLevelItem(0).background(COLUMN_STATE) == QBrush()

    def test_a_clear_sensor_has_no_state_background(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor()))

        assert tree.topLevelItem(0).background(COLUMN_STATE) == QBrush()


class TestReadingColour:
    def test_a_detecting_sensor_has_a_green_reading(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(), is_active=True))

        background = tree.topLevelItem(0).background(COLUMN_READING)
        assert background.color().getRgbF()[:3] == pytest.approx(
            ACTIVE_COLOR[:3], abs=_CHANNEL_STEP
        )

    def test_a_measurement_in_range_is_green(self, tree: SensorTree) -> None:
        tree.refresh(
            registry_with(
                beam_sensor(kind=SensorKind.TOF, range_m=2.5),
                is_active=True,
                reading=SensorReading(value=0.3),
            )
        )

        background = tree.topLevelItem(0).background(COLUMN_READING)
        assert background.color().getRgbF()[:3] == pytest.approx(
            ACTIVE_COLOR[:3], abs=_CHANNEL_STEP
        )

    def test_a_clear_sensor_has_no_reading_background(self, tree: SensorTree) -> None:
        # No red in a table: a red cell reads as an error, and the scene already
        # shows "not seeing anything" with a whole marker.
        tree.refresh(registry_with(beam_sensor()))

        assert tree.topLevelItem(0).background(COLUMN_READING) == QBrush()

    def test_nothing_in_range_has_no_reading_background(self, tree: SensorTree) -> None:
        tree.refresh(
            registry_with(
                beam_sensor(kind=SensorKind.TOF, range_m=2.5),
                reading=SensorReading(value=2.5, is_valid=False),
            )
        )

        assert tree.topLevelItem(0).background(COLUMN_READING) == QBrush()

    def test_an_encoder_is_never_coloured(self, tree: SensorTree) -> None:
        # It has no in-range notion, so green would be claiming one.
        tree.refresh(
            registry_with(
                Sensor(name="turns", kind=SensorKind.ENCODER_ABS),
                reading=SensorReading(value=512.0),
            )
        )

        assert tree.topLevelItem(0).background(COLUMN_READING) == QBrush()

    def test_the_red_never_reaches_the_table(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor()))

        item = tree.topLevelItem(0)
        for column in (COLUMN_STATE, COLUMN_READING):
            assert item.background(column).color().getRgbF()[:3] != pytest.approx(
                CLEAR_COLOR[:3], abs=_CHANNEL_STEP
            )


class TestReading:
    def test_a_distance_is_shown_in_millimetres(self, tree: SensorTree) -> None:
        tree.refresh(
            registry_with(
                beam_sensor(kind=SensorKind.LASER_DISTANCE),
                reading=SensorReading(value=0.3),
            )
        )

        assert tree.topLevelItem(0).text(COLUMN_READING) == "300.0 mm"

    def test_an_encoder_shows_its_counts(self, tree: SensorTree) -> None:
        tree.refresh(
            registry_with(
                Sensor(name="turns", kind=SensorKind.ENCODER_ABS),
                reading=SensorReading(value=512.0),
            )
        )

        assert tree.topLevelItem(0).text(COLUMN_READING) == "512"


class TestIcons:
    def test_a_row_carries_its_kind_icon(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(kind=SensorKind.PROXIMITY)))

        assert tree.topLevelItem(0).icon(COLUMN_NAME).isNull() is False

    def test_two_kinds_carry_different_icons(self, tree: SensorTree) -> None:
        tree.refresh(registry_with(beam_sensor(kind=SensorKind.BEAM)))
        beam = tree.topLevelItem(0).icon(COLUMN_NAME).pixmap(24, 24).toImage()

        tree.refresh(registry_with(Sensor(name="turns", kind=SensorKind.ENCODER_ABS)))
        encoder = tree.topLevelItem(0).icon(COLUMN_NAME).pixmap(24, 24).toImage()

        assert beam != encoder
