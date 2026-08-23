"""Tests of the `JointValueRow` widget wiring: unit conversion, and the spin/
slider mutual sync that keeps them from feeding back into each other.

The pure scale/unscale math is covered by
`tests/unit/ui/test_joint_value_row.py`.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.ui.joint_value_row import SLIDER_STEPS, JointValueRow  # noqa: E402
from tests.factories import axis_joint, trajectory_joint  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def row(qt_app: QApplication) -> Iterator[JointValueRow]:
    joint = axis_joint(limits=(-math.pi / 2, math.pi / 2))
    instance = JointValueRow("joint-1", joint, 0.0)
    yield instance
    instance.deleteLater()


class TestUnitsAndRange:
    def test_an_axis_row_shows_degrees(self, row: JointValueRow) -> None:
        assert row.value_spin.suffix().strip() == "°"

    def test_a_trajectory_row_shows_millimetres(self, qt_app: QApplication) -> None:
        joint = trajectory_joint(origin=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
        instance = JointValueRow("joint-1", joint, 0.0)

        assert instance.value_spin.suffix().strip() == "mm"
        instance.deleteLater()

    def test_the_spin_range_matches_the_joints_limits_in_degrees(self, row: JointValueRow) -> None:
        assert row.value_spin.minimum() == pytest.approx(-90.0)
        assert row.value_spin.maximum() == pytest.approx(90.0)

    def test_the_initial_value_is_shown_in_display_units(self, qt_app: QApplication) -> None:
        joint = axis_joint(limits=(-math.pi, math.pi))
        instance = JointValueRow("joint-1", joint, math.pi / 2)

        assert instance.value_spin.value() == pytest.approx(90.0)
        instance.deleteLater()


class TestSpinSliderSync:
    def test_moving_the_spin_moves_the_slider(self, row: JointValueRow) -> None:
        row.value_spin.setValue(45.0)  # halfway from -90 to 90

        assert row.slider.value() == pytest.approx(SLIDER_STEPS * 3 // 4, abs=2)

    def test_moving_the_slider_moves_the_spin(self, row: JointValueRow) -> None:
        row.slider.setValue(SLIDER_STEPS)  # the top of the range

        assert row.value_spin.value() == pytest.approx(90.0, abs=0.1)

    def test_the_spin_does_not_re_trigger_the_slider_signal_loop(self, row: JointValueRow) -> None:
        # If the guard failed, setting the spin would bounce back through the
        # slider and re-emit - counted here via how many edits are reported.
        received: list[tuple[str, float]] = []
        row.value_edited.connect(lambda joint_id, value: received.append((joint_id, value)))

        row.value_spin.setValue(30.0)

        assert len(received) == 1


class TestValueEdited:
    def test_a_spin_edit_emits_the_joint_id(self, row: JointValueRow) -> None:
        received: list[str] = []
        row.value_edited.connect(lambda joint_id, _value: received.append(joint_id))

        row.value_spin.setValue(10.0)

        assert received == ["joint-1"]

    def test_the_emitted_value_is_in_internal_units(self, row: JointValueRow) -> None:
        received: list[float] = []
        row.value_edited.connect(lambda _joint_id, value: received.append(value))

        row.value_spin.setValue(90.0)

        assert received[-1] == pytest.approx(math.pi / 2)

    def test_set_value_silently_emits_nothing(self, row: JointValueRow) -> None:
        received: list[float] = []
        row.value_edited.connect(lambda _joint_id, value: received.append(value))

        row.set_value_silently(math.pi / 4)

        assert received == []

    def test_set_value_silently_still_updates_the_widgets(self, row: JointValueRow) -> None:
        row.set_value_silently(math.pi / 4)

        assert row.value_spin.value() == pytest.approx(45.0)
