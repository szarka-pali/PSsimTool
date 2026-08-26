"""The Apply column and the red value, in the table itself.

Separate from `test_ui_variables_drive.py`, which is about what the window does
with a value: this is about what the row looks like and what a click on it
reports.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.config.binding import BindingDirection  # noqa: E402
from pssim.ui.labels import out_of_range_color  # noqa: E402
from pssim.ui.variable_registry import (  # noqa: E402
    VariableRegistry,
    VariableSource,
    VariableState,
)
from pssim.ui.variable_tree import (  # noqa: E402
    COLUMN_APPLY,
    COLUMN_VALUE,
    DEFAULT_COLUMN_WIDTHS,
    VariableTree,
)

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def registry(*, with_sensor: bool = False) -> VariableRegistry:
    sources = [VariableSource("tilt", BindingDirection.READ, "axis tilt")]
    if with_sensor:
        sources.append(VariableSource("gate", BindingDirection.WRITE, "sensor gate"))
    built = VariableRegistry()
    built.set_sources(sources)
    return built


@pytest.fixture
def tree(qt_app: QApplication) -> VariableTree:
    return VariableTree()


class TestTheColumn:
    def test_it_is_the_first_one(self, tree: VariableTree) -> None:
        assert COLUMN_APPLY == 0

    def test_it_has_a_default_width(self, tree: VariableTree) -> None:
        # One per column, or R18 drops the whole saved layout as the wrong shape.
        assert len(DEFAULT_COLUMN_WIDTHS) == tree.columnCount()

    def test_it_is_narrow(self, tree: VariableTree) -> None:
        assert DEFAULT_COLUMN_WIDTHS[COLUMN_APPLY] < DEFAULT_COLUMN_WIDTHS[COLUMN_VALUE]

    def test_the_header_says_what_it_does(self, tree: VariableTree) -> None:
        assert "move" in tree.headerItem().toolTip(COLUMN_APPLY)


class TestTheCheckbox:
    def test_a_read_variable_has_one_and_it_is_on(self, tree: VariableTree) -> None:
        tree.refresh(registry())

        assert tree.topLevelItem(0).checkState(COLUMN_APPLY) == Qt.CheckState.Checked

    def test_it_is_clickable(self, tree: VariableTree) -> None:
        tree.refresh(registry())

        flags = tree.topLevelItem(0).flags()
        assert bool(flags & Qt.ItemFlag.ItemIsUserCheckable)

    def test_a_switched_off_variable_shows_it_cleared(self, tree: VariableTree) -> None:
        store = registry()
        store.set_applied("tilt", False)

        tree.refresh(store)

        assert tree.topLevelItem(0).checkState(COLUMN_APPLY) == Qt.CheckState.Unchecked

    def test_a_sensor_variable_has_no_box(self, tree: VariableTree) -> None:
        # It travels the other way (R19); there is nothing arriving to apply, and
        # a cleared box would suggest it could be turned on.
        tree.refresh(registry(with_sensor=True))

        flags = tree.topLevelItem(1).flags()
        assert not bool(flags & Qt.ItemFlag.ItemIsUserCheckable)

    def test_the_tooltip_says_what_clearing_it_does(self, tree: VariableTree) -> None:
        tree.refresh(registry())

        assert "hand" in tree.topLevelItem(0).toolTip(COLUMN_APPLY)


class TestClickingIt:
    def test_clearing_it_is_reported(self, tree: VariableTree) -> None:
        tree.refresh(registry())
        seen: list[tuple[str, bool]] = []
        tree.applied_changed.connect(lambda name, on: seen.append((name, on)))

        tree.topLevelItem(0).setCheckState(COLUMN_APPLY, Qt.CheckState.Unchecked)

        assert seen == [("tilt", False)]

    def test_and_so_is_setting_it_again(self, tree: VariableTree) -> None:
        store = registry()
        store.set_applied("tilt", False)
        tree.refresh(store)
        seen: list[tuple[str, bool]] = []
        tree.applied_changed.connect(lambda name, on: seen.append((name, on)))

        tree.topLevelItem(0).setCheckState(COLUMN_APPLY, Qt.CheckState.Checked)

        assert seen == [("tilt", True)]

    def test_a_redraw_reports_nothing(self, tree: VariableTree) -> None:
        # `refresh` blocks signals while it rebuilds. Without that, redrawing the
        # table would report every row as having just been switched — and the
        # table is redrawn on every notification.
        seen: list[tuple[str, bool]] = []
        tree.applied_changed.connect(lambda name, on: seen.append((name, on)))

        tree.refresh(registry())
        tree.refresh(registry())

        assert seen == []


class TestTheRedValue:
    def test_a_value_in_range_is_not_red(self, tree: VariableTree) -> None:
        store = registry()
        store.set_value("tilt", 0.5)

        tree.refresh(store)

        assert tree.topLevelItem(0).foreground(COLUMN_VALUE).color() != out_of_range_color()

    def test_one_out_of_range_is(self, tree: VariableTree) -> None:
        store = registry()
        store.set_value("tilt", 9.5)
        store.set_out_of_range("tilt", True)

        tree.refresh(store)

        assert tree.topLevelItem(0).foreground(COLUMN_VALUE).color() == out_of_range_color()

    def test_the_tooltip_says_the_joint_did_not_follow(self, tree: VariableTree) -> None:
        # The cell shows what arrived, so without this the row would name a value
        # the model is not at.
        store = registry()
        store.set_value("tilt", 9.5)
        store.set_out_of_range("tilt", True)

        tree.refresh(store)

        assert "limits" in tree.topLevelItem(0).toolTip(COLUMN_VALUE)

    def test_the_status_is_still_live(self, tree: VariableTree) -> None:
        # Out of range is a fault in the value, not in the connection: the
        # subscription is fine and the number arrived intact.
        store = registry()
        store.set_value("tilt", 9.5)
        store.set_out_of_range("tilt", True)

        entry = store.get("tilt")
        assert entry is not None
        assert entry.state is VariableState.LIVE


class TestTheRegistryKeepsIt:
    def test_a_switch_survives_the_scene_being_re_read(self) -> None:
        # The entries are rebuilt from scratch whenever anything is renamed.
        store = registry()
        store.set_applied("tilt", False)

        store.set_sources([VariableSource("tilt", BindingDirection.READ, "axis tilt")])

        entry = store.get("tilt")
        assert entry is not None
        assert entry.is_applied is False

    def test_a_switch_thrown_before_the_variable_exists_is_kept(self) -> None:
        # Loading a project after the settings is the normal order.
        store = VariableRegistry()
        store.set_applied("tilt", False)

        store.set_sources([VariableSource("tilt", BindingDirection.READ, "axis tilt")])

        entry = store.get("tilt")
        assert entry is not None
        assert entry.is_applied is False

    def test_setting_it_to_what_it_already_is_changes_nothing(self) -> None:
        store = registry()

        assert store.set_applied("tilt", True) is False

    def test_changing_it_reports_a_change(self) -> None:
        store = registry()

        assert store.set_applied("tilt", False) is True

    def test_out_of_range_starts_false(self) -> None:
        entry = registry().get("tilt")
        assert entry is not None
        assert entry.is_out_of_range is False

    def test_it_clears_again(self) -> None:
        store = registry()
        store.set_out_of_range("tilt", True)

        assert store.set_out_of_range("tilt", False) is True

    def test_an_unknown_variable_is_ignored(self) -> None:
        # Both setters are called from the window, which may be a redraw behind.
        store = registry()

        assert store.set_out_of_range("nothing", True) is False
