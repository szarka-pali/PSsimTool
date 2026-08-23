"""Tests for `ModelTree`'s joint nesting: rows, selection resolution, and the
double-click that opens a model's value panel.

The context-menu three-way-target behaviour is covered alongside the rest of
`ModelTree`'s context menu in `test_ui_main_window.py::TestContextMenu`.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.ui.joint_registry import JointRegistry  # noqa: E402
from pssim.ui.model_registry import ModelRegistry  # noqa: E402
from pssim.ui.model_tree import ModelTree  # noqa: E402
from tests.factories import axis_joint, trajectory_joint  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def tree(qt_app: QApplication) -> Iterator[ModelTree]:
    instance = ModelTree()
    yield instance
    instance.deleteLater()


def populated(tree: ModelTree) -> tuple[ModelRegistry, JointRegistry]:
    """A rail carrying a head; `gantry` bound to the rail, `conveyor` loose.

    The shape the inversion produces: joints at the top, models hanging off
    them, and one model in the scene on its own.
    """
    models = ModelRegistry()
    joints = JointRegistry()
    gantry = models.add(Path("gantry.step"))
    models.add(Path("conveyor.step"))
    rail = joints.add(trajectory_joint(name="slide"))
    joints.add(axis_joint(name="tilt"), parent_joint_id=rail.joint_id)
    models.bind(gantry.model_id, rail.joint_id)
    tree.refresh(models, joints)
    return models, joints


class TestNesting:
    def test_a_joint_is_top_level(self, tree: ModelTree) -> None:
        populated(tree)

        first = tree.topLevelItem(0)
        assert first is not None
        assert first.text(0) == "slide (trajectory)"

    def test_a_joint_carries_its_bound_model_and_its_child_joint(self, tree: ModelTree) -> None:
        populated(tree)

        rail_item = tree.topLevelItem(0)
        assert rail_item is not None
        labels = [rail_item.child(i).text(0) for i in range(rail_item.childCount())]
        # Models first, then the next stage of the mechanism.
        assert labels == ["gantry", "tilt (axis)"]

    def test_a_model_bound_to_nothing_stays_top_level(self, tree: ModelTree) -> None:
        populated(tree)

        labels = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        assert "conveyor" in labels

    def test_a_bound_model_is_no_longer_top_level(self, tree: ModelTree) -> None:
        populated(tree)

        labels = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        assert "gantry" not in labels

    def test_a_child_joint_nests_arbitrarily_deep(self, tree: ModelTree) -> None:
        models = ModelRegistry()
        joints = JointRegistry()
        rail = joints.add(trajectory_joint(name="rail"))
        head = joints.add(axis_joint(name="head"), parent_joint_id=rail.joint_id)
        joints.add(axis_joint(name="wrist"), parent_joint_id=head.joint_id)

        tree.refresh(models, joints)

        rail_item = tree.topLevelItem(0)
        assert rail_item is not None
        head_item = rail_item.child(0)
        assert head_item is not None
        assert head_item.child(0).text(0) == "wrist (axis)"


class TestSelectionResolution:
    def test_selecting_a_joint_row_reports_its_id(self, tree: ModelTree) -> None:
        _models, joints = populated(tree)
        joint_id = joints.entries[0].joint_id

        tree.select_joint_id(joint_id)

        assert tree.selected_joint_id == joint_id

    def test_selecting_a_joint_row_resolves_no_model(self, tree: ModelTree) -> None:
        # A joint belongs to no model, so there is nothing to resolve up to —
        # the opposite of what this asserted before the inversion.
        _models, joints = populated(tree)

        tree.select_joint_id(joints.entries[0].joint_id)

        assert tree.selected_model_id is None

    def test_selecting_a_model_row_reports_no_joint(self, tree: ModelTree) -> None:
        models, _joints = populated(tree)

        tree.select_id(models.entries[0].model_id)

        assert tree.selected_joint_id is None

    def test_selecting_a_model_row_deselects_a_previously_selected_joint(
        self, tree: ModelTree
    ) -> None:
        models, joints = populated(tree)
        tree.select_joint_id(joints.entries[0].joint_id)

        tree.select_id(models.entries[1].model_id)

        assert tree.selected_joint_id is None
        assert tree.selected_model_id == models.entries[1].model_id

    def test_refresh_restores_a_joint_selection(self, tree: ModelTree) -> None:
        models, joints = populated(tree)
        joint_id = joints.entries[0].joint_id
        joints.select(joint_id)

        tree.refresh(models, joints)

        assert tree.selected_joint_id == joint_id

    def test_nothing_selected_reports_none_for_both(self, tree: ModelTree) -> None:
        populated(tree)
        tree.select_id(None)

        assert tree.selected_model_id is None
        assert tree.selected_joint_id is None


class TestDoubleClick:
    def test_double_clicking_a_model_emits_its_id(self, tree: ModelTree) -> None:
        # The bound model now hangs *under* the joint, so that is where its row
        # is — the nesting is the other way round from before.
        models, _joints = populated(tree)
        rail_item = tree.topLevelItem(0)
        assert rail_item is not None
        model_item = rail_item.child(0)
        assert model_item is not None
        received: list[object] = []
        tree.model_double_clicked.connect(received.append)

        tree.itemDoubleClicked.emit(model_item, 0)

        assert received == [models.entries[0].model_id]

    def test_double_clicking_a_joint_row_emits_nothing(self, tree: ModelTree) -> None:
        populated(tree)
        joint_item = tree.topLevelItem(0)
        assert joint_item is not None
        received: list[object] = []
        tree.model_double_clicked.connect(received.append)

        tree.itemDoubleClicked.emit(joint_item, 0)

        assert received == []
