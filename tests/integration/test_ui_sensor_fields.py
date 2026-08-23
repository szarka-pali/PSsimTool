"""Tests of `SensorFields`, the sensor fields shared by the dialog and the panel.

Two things matter beyond what the dialog already covered: `fields_changed` fires
on a user edit and stays quiet while a caller fills the widget in, and
`sensor_if_valid()` reports a half-typed definition rather than raising out of a
slot.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.domain.sensors import SensorDisplay, SensorKind, from_sensor  # noqa: E402
from pssim.ui.sensor_fields import SensorFields, kind_index  # noqa: E402
from tests.factories import beam_sensor  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def fields(qt_app: QApplication) -> SensorFields:
    return SensorFields()


def count_signals(widget: SensorFields) -> list[int]:
    """A one-element list the connected slot increments — a plain int would be
    rebound inside the closure rather than shared with the test."""
    seen = [0]
    widget.fields_changed.connect(lambda: seen.__setitem__(0, seen[0] + 1))
    return seen


class TestReporting:
    def test_a_spin_box_edit_is_reported(self, fields: SensorFields) -> None:
        seen = count_signals(fields)

        fields.origin_x_spin.setValue(120.0)

        assert seen[0] == 1

    def test_a_kind_change_is_reported(self, fields: SensorFields) -> None:
        seen = count_signals(fields)

        fields.kind_combo.setCurrentIndex(kind_index(SensorKind.PROXIMITY))

        assert seen[0] == 1

    def test_filling_the_widget_in_is_silent(self, fields: SensorFields) -> None:
        # Otherwise the panel would get its own value echoed straight back at it
        # and drive a round trip through the registry on every refresh.
        seen = count_signals(fields)

        fields.set_display(from_sensor(beam_sensor(name="gate", range_m=2.0)))

        assert seen[0] == 0


class TestPerKindGroups:
    def test_a_beam_shows_the_ray_group(self, fields: SensorFields) -> None:
        fields.set_display(SensorDisplay(name="s", kind=SensorKind.BEAM))

        assert fields.ray_group.isVisibleTo(fields) is True

    def test_a_beam_hides_the_zone_group(self, fields: SensorFields) -> None:
        fields.set_display(SensorDisplay(name="s", kind=SensorKind.BEAM))

        assert fields.zone_group.isVisibleTo(fields) is False

    def test_a_zone_shows_its_own_group(self, fields: SensorFields) -> None:
        fields.set_display(SensorDisplay(name="s", kind=SensorKind.PROXIMITY))

        assert fields.zone_group.isVisibleTo(fields) is True

    def test_an_encoder_shows_the_counts(self, fields: SensorFields) -> None:
        fields.set_display(SensorDisplay(name="s", kind=SensorKind.ENCODER_ABS))

        assert fields.encoder_group.isVisibleTo(fields) is True

    def test_an_encoder_has_no_position(self, fields: SensorFields) -> None:
        # It reads the joint it is bolted to; a point in space would mean nothing.
        fields.set_display(SensorDisplay(name="s", kind=SensorKind.ENCODER_INC))

        assert fields.origin_group.isVisibleTo(fields) is False

    def test_a_hidden_group_keeps_what_was_typed(self, fields: SensorFields) -> None:
        fields.set_display(SensorDisplay(name="s", kind=SensorKind.PROXIMITY, half_extent_mm=250.0))

        fields.kind_combo.setCurrentIndex(kind_index(SensorKind.BEAM))

        assert fields.display.half_extent_mm == pytest.approx(250.0)


class TestValidity:
    def test_a_finished_definition_reads_back(self, fields: SensorFields) -> None:
        fields.set_display(from_sensor(beam_sensor(name="gate", range_m=2.0)))

        sensor = fields.sensor_if_valid()

        assert sensor is not None
        assert sensor.name == "gate"

    def test_an_empty_name_is_not_a_sensor(self, fields: SensorFields) -> None:
        fields.set_display(from_sensor(beam_sensor(name="gate")))
        fields.name_edit.setText("   ")

        assert fields.sensor_if_valid() is None

    def test_a_ray_with_no_direction_is_not_a_sensor(self, fields: SensorFields) -> None:
        # Passing through zero while retyping a direction is normal; it must not
        # throw out of the slot that is watching the spin box.
        fields.set_display(from_sensor(beam_sensor(name="gate")))
        for spin in fields.direction_spins:
            spin.setValue(0.0)

        assert fields.sensor_if_valid() is None

    def test_units_are_converted_on_the_way_out(self, fields: SensorFields) -> None:
        fields.set_display(SensorDisplay(name="s", kind=SensorKind.BEAM, origin_mm=(300.0, 0, 0)))

        sensor = fields.sensor_if_valid()

        assert sensor is not None
        assert sensor.origin[0] == pytest.approx(0.3)
