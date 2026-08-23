"""Tests of `ModelValuesPanel`: one row per joint, forwarding edits, and
`set_value_silently` reaching the right row without it looking like an edit.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.ui.joint_registry import JointRegistry  # noqa: E402
from pssim.ui.model_values_panel import ModelValuesPanel  # noqa: E402
from tests.factories import axis_joint, trajectory_joint  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def joints() -> JointRegistry:
    registry = JointRegistry()
    registry.add(axis_joint(name="tilt"))
    registry.add(trajectory_joint(name="slide"))
    return registry


@pytest.fixture
def panel(qt_app: QApplication, joints: JointRegistry) -> Iterator[ModelValuesPanel]:
    instance = ModelValuesPanel("model-1", "gantry", joints.entries)
    yield instance
    instance.close()


def _record_edit(received: list[tuple[str, float]]) -> object:
    def handler(joint_id: str, value: float) -> None:
        received.append((joint_id, value))

    return handler


class TestRows:
    def test_one_row_per_joint(self, panel: ModelValuesPanel) -> None:
        assert panel.joint_count == 2

    def test_the_model_id_is_kept(self, panel: ModelValuesPanel) -> None:
        assert panel.model_id == "model-1"

    def test_the_title_names_the_model(self, panel: ModelValuesPanel) -> None:
        assert "gantry" in panel.windowTitle()

    def test_a_model_with_no_joints_gets_no_rows(self, qt_app: QApplication) -> None:
        instance = ModelValuesPanel("model-2", "conveyor", ())

        assert instance.joint_count == 0
        instance.close()


class TestForwarding:
    def test_a_rows_edit_is_forwarded_with_its_joint_id(
        self, panel: ModelValuesPanel, joints: JointRegistry
    ) -> None:
        entry = joints.entries[0]
        row = panel.row_for(entry.joint_id)
        assert row is not None
        received: list[tuple[str, float]] = []
        panel.value_edited.connect(_record_edit(received))

        row.value_spin.setValue(10.0)

        assert received[0][0] == entry.joint_id


class TestSetValueSilently:
    def test_updates_the_matching_row(self, panel: ModelValuesPanel, joints: JointRegistry) -> None:
        import math

        entry = joints.entries[0]  # an axis joint - degrees on screen

        panel.set_value_silently(entry.joint_id, math.pi / 2)

        row = panel.row_for(entry.joint_id)
        assert row is not None
        assert row.value_spin.value() == pytest.approx(90.0)

    def test_does_not_emit_value_edited(
        self, panel: ModelValuesPanel, joints: JointRegistry
    ) -> None:
        entry = joints.entries[0]
        received: list[object] = []
        panel.value_edited.connect(received.append)

        panel.set_value_silently(entry.joint_id, 0.5)

        assert received == []

    def test_an_unknown_joint_id_is_harmless(self, panel: ModelValuesPanel) -> None:
        panel.set_value_silently("joint-99", 1.0)
