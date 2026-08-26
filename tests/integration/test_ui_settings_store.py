"""Tests of `SettingsStore` and of the window remembering its column layout.

The dataclasses are covered without Qt in `tests/unit/ui/test_settings.py`; what
is left here is that they survive a trip through `QSettings`, and that closing a
window is what writes them.

Every test points the store at a temp ini file. The real one is the user's own
store, and a test that wrote into it would change their application.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.io.opcua_security import TokenType  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.model_tree import COLUMN_NAME, ModelTree  # noqa: E402
from pssim.ui.model_tree import TABLE_NAME as MODEL_TABLE  # noqa: E402
from pssim.ui.sensor_tree import TABLE_NAME as SENSOR_TABLE  # noqa: E402
from pssim.ui.sensor_tree import SensorTree  # noqa: E402
from pssim.ui.settings import (  # noqa: E402
    ConnectionSettings,
    SettingsStore,
    VariableTag,
    ViewSettings,
)

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def ini(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "pssim.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def store(qt_app: QApplication, ini: QSettings) -> SettingsStore:
    return SettingsStore(ini)


@pytest.fixture
def window(qt_app: QApplication, store: SettingsStore) -> Iterator[MainWindow]:
    instance = MainWindow(viewport_factory=QWidget, settings=store)
    yield instance
    instance.close()


class TestStore:
    def test_a_fresh_store_has_defaults(self, store: SettingsStore) -> None:
        assert store.load_view() == ViewSettings()

    def test_the_view_survives_a_round_trip(self, store: SettingsStore, ini: QSettings) -> None:
        store.save_view(ViewSettings().with_widths(MODEL_TABLE, (240, 80)))

        assert SettingsStore(ini).load_view().widths_for(MODEL_TABLE) == (240, 80)

    def test_the_connection_survives_a_round_trip(
        self, store: SettingsStore, ini: QSettings
    ) -> None:
        settings = ConnectionSettings(endpoint="opc.tcp://plc:4840/").with_tag(
            "X", VariableTag(node_id="ns=2;s=Axes.X.ActPos", decimals=1)
        )

        store.save_connection(settings)

        assert SettingsStore(ini).load_connection() == settings

    def test_a_damaged_entry_does_not_crash(self, store: SettingsStore, ini: QSettings) -> None:
        # Someone editing the ini by hand should cost defaults, not a crash on
        # startup — and the next save clears the mess.
        ini.setValue("view/columns", "{not json")

        assert store.load_view() == ViewSettings()


class TestThePasswordNeverReachesTheFile:
    """R19, proved against the actual file rather than against `to_dict`.

    The dialog is where a password is typed and the window is what saves the
    settings afterwards; the check is that the bytes on disk hold neither the
    secret nor a key that looks like somewhere to put one.
    """

    def test_there_is_no_key_to_put_one_in(
        self, store: SettingsStore, ini: QSettings, tmp_path: Path
    ) -> None:
        store.save_connection(
            ConnectionSettings(token_type=TokenType.USERNAME, username="operator")
        )
        ini.sync()

        assert "password" not in _ini_text(tmp_path).lower()

    def test_the_window_saves_the_settings_without_it(
        self, window: MainWindow, ini: QSettings, tmp_path: Path
    ) -> None:
        # The whole path: a password typed into the dialog is held for the
        # session, and what gets saved is everything except it.
        window._session_password = "s3cret"
        window.save_connection_settings(
            ConnectionSettings(token_type=TokenType.USERNAME, username="operator")
        )
        ini.sync()

        written = _ini_text(tmp_path)
        assert "s3cret" not in written
        # In the same breath, or the absence proves only that nothing was saved.
        assert "operator" in written

    def test_the_user_name_is_there_though(
        self, store: SettingsStore, ini: QSettings, tmp_path: Path
    ) -> None:
        # The counter-check: absence proves nothing if nothing was written.
        store.save_connection(
            ConnectionSettings(token_type=TokenType.USERNAME, username="operator")
        )
        ini.sync()

        assert "operator" in _ini_text(tmp_path)


def _ini_text(tmp_path: Path) -> str:
    """Every byte of the settings file, whatever encoding Qt chose for it."""
    return (tmp_path / "pssim.ini").read_bytes().decode("utf-8", errors="replace")


class TestTheWindowRemembers:
    def test_a_saved_layout_comes_back(self, qt_app: QApplication, store: SettingsStore) -> None:
        first = MainWindow(viewport_factory=QWidget, settings=store)
        first.model_tree.setColumnWidth(COLUMN_NAME, 321)
        first.close()

        second = MainWindow(viewport_factory=QWidget, settings=store)
        try:
            assert second.model_tree.columnWidth(COLUMN_NAME) == 321
        finally:
            second.close()

    def test_closing_is_what_writes_it(self, window: MainWindow, store: SettingsStore) -> None:
        # Not every drag: a width changes continuously while the mouse is down.
        window.model_tree.setColumnWidth(COLUMN_NAME, 300)

        assert store.load_view().widths_for(MODEL_TABLE) == ()

        window.save_view_settings()
        assert store.load_view().widths_for(MODEL_TABLE)[COLUMN_NAME] == 300

    def test_both_tables_are_saved(self, window: MainWindow, store: SettingsStore) -> None:
        window.save_view_settings()

        view = store.load_view()
        assert view.widths_for(MODEL_TABLE) and view.widths_for(SENSOR_TABLE)

    def test_nothing_saved_leaves_the_defaults(self, window: MainWindow) -> None:
        assert window.model_tree.columnWidth(COLUMN_NAME) > 0


class TestColumnsCanBeDragged:
    def test_every_model_column_is_interactive(self, qt_app: QApplication) -> None:
        # Stretch and ResizeToContents both compute the width themselves, and
        # take the drag handle away with it.
        tree = ModelTree()
        header = tree.header()

        modes = [header.sectionResizeMode(column) for column in range(tree.columnCount())]
        assert modes == [header.ResizeMode.Interactive] * tree.columnCount()

    def test_every_sensor_column_is_interactive(self, qt_app: QApplication) -> None:
        tree = SensorTree()
        header = tree.header()

        modes = [header.sectionResizeMode(column) for column in range(tree.columnCount())]
        assert modes == [header.ResizeMode.Interactive] * tree.columnCount()

    def test_widths_read_back(self, qt_app: QApplication) -> None:
        tree = SensorTree()

        tree.set_column_widths((60, 70, 80, 90))

        assert tree.column_widths() == (60, 70, 80, 90)

    def test_qt_will_not_collapse_a_column(self, qt_app: QApplication) -> None:
        # A saved width below the header's own minimum comes back as the
        # minimum, so a column can never be restored to nothing.
        tree = SensorTree()

        tree.set_column_widths((1, 1, 1, 1))

        assert min(tree.column_widths()) >= tree.header().minimumSectionSize()

    def test_a_layout_of_the_wrong_shape_is_ignored(self, qt_app: QApplication) -> None:
        # A saved layout from a build with different columns would otherwise put
        # each width against the wrong column.
        tree = SensorTree()
        before = tree.column_widths()

        tree.set_column_widths((60, 70))

        assert tree.column_widths() == before
