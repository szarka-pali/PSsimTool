"""Tests of the main window.

They run **headless** — `QT_QPA_PLATFORM=offscreen` is set before PySide6 is imported,
so no window opens and the tests work on a machine with no display.

Requires `uv sync --extra ui`. Run with: ``uv run pytest -m ui``
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# Must come before importing PySide6, or Qt picks its platform from the environment
# and dies on CI with no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QToolButton, QWidget  # noqa: E402

from pssim.cad.model import CadAssembly, CadNode  # noqa: E402
from pssim.domain.machine import Transform  # noqa: E402
from pssim.domain.placement import IDENTITY_PLACEMENT  # noqa: E402
from pssim.ui.main_window import APP_TITLE, MainWindow  # noqa: E402
from pssim.ui.model_registry import ModelEntry  # noqa: E402
from pssim.ui.placement_dialog import PlacementDialog  # noqa: E402
from pssim.viz.orbit import STANDARD_VIEWS  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    """One `QApplication` per module — Qt allows no more than one."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def window(qt_app: QApplication) -> Iterator[MainWindow]:
    """A fresh window for every test, closed afterwards.

    Instead of the real 3D viewport it gets a plain widget: `ShowBase` may exist only
    once per process, so in the tests it is never created at all. Displaying geometry
    is verified by `tests/integration/test_viz_scene.py` and by real runs.
    """
    instance = MainWindow(viewport_factory=QWidget)
    yield instance
    instance.close()


def _pick_file(path: Path | None) -> Callable[..., tuple[str, str]]:
    """Stand-in for `QFileDialog.getOpenFileName`.

    `None` means the user pressed Cancel — Qt then returns an empty string, not
    `None`.
    """

    def fake_dialog(*args: object, **kwargs: object) -> tuple[str, str]:
        return (str(path) if path is not None else "", "")

    return fake_dialog


def _record_loads(recorded: list[Path]) -> Callable[..., None]:
    """Stand-in for `MainWindow.load_file` that only records what should have loaded."""

    def fake_load(_self: object, path: Path) -> None:
        recorded.append(path)

    return fake_load


def _skip_loading(_self: object, _path: Path) -> None:
    """Stand-in for `MainWindow.load_file` that does nothing at all."""


class _StubThread(QObject):
    """Stand-in for `StepImportThread` that starts no import.

    A real thread over a non-existent file would end in an error, and that error would
    open a modal dialog with nobody in the test to close it.
    """

    started_count = 0

    succeeded = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, step_file: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.step_file = step_file

    def start(self) -> None:
        type(self).started_count += 1


def menu_titles(window: MainWindow) -> list[str]:
    """The menu bar entry names, without the keyboard accelerators (`&`)."""
    return [action.text().replace("&", "") for action in window.menuBar().actions()]


def menu_items(window: MainWindow, title: str) -> list[str]:
    for action in window.menuBar().actions():
        if action.text().replace("&", "") != title:
            continue
        # `QAction.menu()` is typed as QObject in the stubs, although it returns QMenu.
        submenu = action.menu()
        assert isinstance(submenu, QMenu), f"menu {title!r} has no sub-items"
        return [item.text().replace("&", "") for item in submenu.actions()]
    raise AssertionError(f"menu {title!r} does not exist; the menus are {menu_titles(window)}")


class TestWindow:
    def test_the_window_has_a_title(self, window: MainWindow) -> None:
        assert window.windowTitle() == APP_TITLE

    def test_ma_stavovy_riadok(self, window: MainWindow) -> None:
        assert window.statusBar() is not None

    def test_ma_centralny_widget(self, window: MainWindow) -> None:
        # The 3D viewport from viz/ goes here later.
        assert window.centralWidget() is not None

    def test_no_file_is_open_at_the_start(self, window: MainWindow) -> None:
        assert window.current_file is None


class TestMenu:
    def test_the_main_menus_are_in_order(self, window: MainWindow) -> None:
        assert menu_titles(window) == ["File", "Open", "Model"]

    def test_file_obsahuje_projektove_polozky(self, window: MainWindow) -> None:
        # Separators come through as empty strings; only the real entries matter.
        entries = [text for text in menu_items(window, "File") if text]

        assert entries == [
            "Open Project…",
            "Open Recent",
            "Save Project",
            "Save Project As…",
            "Close All",
            "Exit",
        ]

    def test_the_open_menu_offers_opening_a_3d_file(self, window: MainWindow) -> None:
        assert menu_items(window, "Open") == ["Open 3D file…"]

    def test_exit_ma_klavesovu_skratku(self, window: MainWindow) -> None:
        assert not window.exit_action.shortcut().isEmpty()

    def test_open_ma_klavesovu_skratku(self, window: MainWindow) -> None:
        assert not window.open_action.shortcut().isEmpty()


class TestExit:
    def test_exit_closes_the_window(self, window: MainWindow) -> None:
        window.show()

        window.exit_action.trigger()

        assert not window.isVisible()

    def test_exit_works_on_a_window_never_shown(self, window: MainWindow) -> None:
        # Must not crash if the user presses Ctrl+Q before the window is shown.
        window.exit_action.trigger()

        assert not window.isVisible()


class TestOpeningAFile:
    def test_the_chosen_file_is_remembered(self, window: MainWindow, tmp_path: Path) -> None:
        step_file = tmp_path / "stroj.step"

        window.set_current_file(step_file)

        assert window.current_file == step_file

    def test_the_window_title_shows_the_file(self, window: MainWindow, tmp_path: Path) -> None:
        window.set_current_file(tmp_path / "stroj.step")

        assert "stroj.step" in window.windowTitle()

    def test_stavovy_riadok_ukaze_cestu(self, window: MainWindow, tmp_path: Path) -> None:
        step_file = tmp_path / "stroj.step"

        window.set_current_file(step_file)

        assert window.statusBar().currentMessage() == str(step_file)

    def test_vyslany_signal_nesie_cestu(self, window: MainWindow, tmp_path: Path) -> None:
        step_file = tmp_path / "stroj.step"
        received: list[Path] = []
        window.file_opened.connect(received.append)

        window.set_current_file(step_file)

        assert received == [step_file]

    def test_the_dialog_sets_the_current_file(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        step_file = tmp_path / "vybrany.step"
        monkeypatch.setattr(
            "pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(step_file)
        )
        # A real import would start a thread over a non-existent file, and its error
        # would open a modal dialog with nobody in the test to close it.
        monkeypatch.setattr(MainWindow, "load_file", _skip_loading)

        assert window.open_file_dialog() == step_file

    def test_the_dialog_starts_the_load(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        step_file = tmp_path / "vybrany.step"
        loaded: list[Path] = []
        monkeypatch.setattr(
            "pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(step_file)
        )
        monkeypatch.setattr(MainWindow, "load_file", _record_loads(loaded))

        window.open_file_dialog()

        assert loaded == [step_file]

    def test_zruseny_dialog_nic_nezmeni(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An empty string is what Qt returns when Cancel is pressed.
        monkeypatch.setattr("pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(None))

        assert window.open_file_dialog() is None
        assert window.current_file is None

    def test_a_cancelled_dialog_keeps_the_window_title(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(None))

        window.open_file_dialog()

        assert window.windowTitle() == APP_TITLE

    def test_a_cancelled_dialog_starts_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loaded: list[Path] = []
        monkeypatch.setattr("pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(None))
        monkeypatch.setattr(MainWindow, "load_file", _record_loads(loaded))

        window.open_file_dialog()

        assert loaded == []


class _StubViewport(QWidget):
    """Viewport without Panda3D that records what the window asked of it."""

    def __init__(self) -> None:
        super().__init__()
        self.views: list[str] = []
        self.fit_calls: list[str | None] = []
        self.placements: dict[str, Transform] = {}
        self.highlighted: str | None = None
        self.added: list[str] = []
        self.removed: list[str] = []

    def set_view(self, name: str) -> None:
        self.views.append(name)

    def fit_view(self, model_id: str | None = None) -> None:
        self.fit_calls.append(model_id)

    def set_highlight(self, model_id: str | None) -> None:
        self.highlighted = model_id

    def add_model(self, model_id: str, assembly: object, cache_dir: Path) -> int:
        self.added.append(model_id)
        return 0

    def remove_model(self, model_id: str) -> None:
        self.removed.append(model_id)

    def placement(self, model_id: str) -> Transform:
        return self.placements.get(model_id, IDENTITY_PLACEMENT)

    def set_placement(self, model_id: str, placement: Transform) -> None:
        self.placements[model_id] = placement


def _assembly(parts: int = 2, triangles: int = 24) -> CadAssembly:
    """Smallest assembly the window will accept, with plausible counts."""
    nodes = tuple(
        CadNode(path=f"part{index}", mesh=f"{index}.npz", triangle_count=triangles // parts)
        for index in range(parts)
    )
    return CadAssembly(nodes=nodes, roots=(nodes[0].path,))


def _load(window: MainWindow, name: str = "gantry") -> ModelEntry:
    """Register a model as if an import had just finished."""
    return window.add_model(Path(f"C:/models/{name}.step"), _assembly(), Path("cache"))


def _type_name(name: str | None) -> Callable[..., tuple[str, bool]]:
    """Stand-in for `QInputDialog.getText`.

    `None` means the user pressed Cancel, which Qt reports as `accepted=False`;
    the text must then be ignored rather than trusted.
    """

    def fake_dialog(*args: object, **kwargs: object) -> tuple[str, bool]:
        return ("" if name is None else name, name is not None)

    return fake_dialog


def _menu_labels(menu: QMenu) -> list[str]:
    """Action texts with Qt keyboard accelerators stripped."""
    return [action.text().replace("&", "") for action in menu.actions() if not action.isSeparator()]


@pytest.fixture
def window_with_viewport(qt_app: QApplication) -> Iterator[tuple[MainWindow, _StubViewport]]:
    viewport = _StubViewport()
    instance = MainWindow(viewport_factory=lambda: viewport)
    yield instance, viewport
    instance.close()


class TestViewToolbar:
    def test_lista_existuje(self, window: MainWindow) -> None:
        assert window.toolbar is not None

    def test_the_menu_offers_every_standard_view(self, window: MainWindow) -> None:
        assert set(window.view_actions) == set(STANDARD_VIEWS)

    def test_poradie_zacina_izometriou(self, window: MainWindow) -> None:
        assert [action.text() for action in window.view_menu.actions()][0] == "Isometric"

    def test_the_menu_lists_the_specified_views(self, window: MainWindow) -> None:
        labels = [action.text() for action in window.view_menu.actions()]

        assert {"Top", "Bottom", "Left", "Right", "Back", "Front"} <= set(labels)

    def test_tlacidlo_ma_rozbalovacie_menu(self, window: MainWindow) -> None:
        assert window.view_button.menu() is window.view_menu

    def test_the_menu_opens_on_the_first_click(self, window: MainWindow) -> None:
        # Without InstantPopup the menu would appear only after holding the button.
        assert window.view_button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup

    def test_every_view_has_an_icon(self, window: MainWindow) -> None:
        assert all(not action.icon().isNull() for action in window.view_actions.values())

    def test_every_view_has_a_shortcut(self, window: MainWindow) -> None:
        assert all(not action.shortcut().isEmpty() for action in window.view_actions.values())

    def test_skratky_su_unikatne(self, window: MainWindow) -> None:
        shortcuts = [action.shortcut().toString() for action in window.view_actions.values()]

        assert len(set(shortcuts)) == len(shortcuts)

    def test_the_fit_button_has_an_icon(self, window: MainWindow) -> None:
        assert not window.fit_action.icon().isNull()


class TestSwitchingViews:
    def test_the_action_sends_the_view_to_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.view_actions["top"].trigger()

        assert viewport.views == ["top"]

    @pytest.mark.parametrize("name", sorted(STANDARD_VIEWS))
    def test_kazda_polozka_menu_funguje(
        self, window_with_viewport: tuple[MainWindow, _StubViewport], name: str
    ) -> None:
        window, viewport = window_with_viewport

        window.view_actions[name].trigger()

        assert viewport.views == [name]

    def test_the_status_bar_reports_the_view(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        window.set_view("front")

        assert "front" in window.statusBar().currentMessage()

    def test_fit_to_view_calls_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        _load(window)

        window.fit_action.trigger()

        assert viewport.fit_calls == [window.models.selected_id]

    def test_fit_to_view_frames_everything_with_no_selection(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        _load(window)
        window.select_model(None)

        window.fit_view()

        assert viewport.fit_calls == [None]

    def test_a_viewport_without_support_does_not_crash(self, window: MainWindow) -> None:
        # The substitute widget in the tests has no `set_view` — the window must survive.
        window.set_view("top")

        assert window.centralWidget() is not None


class TestModelTree:
    def test_tree_starts_empty(self, window: MainWindow) -> None:
        assert window.model_tree.topLevelItemCount() == 0

    def test_dock_is_on_the_left(self, window: MainWindow) -> None:
        assert window.dockWidgetArea(window.model_dock) == Qt.DockWidgetArea.LeftDockWidgetArea

    def test_loading_adds_a_row(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window)

        assert window.model_tree.topLevelItemCount() == 1

    def test_loading_twice_adds_two_rows(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window, "a")
        _load(window, "b")

        assert window.model_tree.topLevelItemCount() == 2

    def test_second_model_does_not_replace_the_first(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The whole point of the change: opening a file adds, it does not replace.
        window, viewport = window_with_viewport

        _load(window, "a")
        _load(window, "b")

        assert len(viewport.added) == 2
        assert len(window.models) == 2

    def test_same_file_twice_gets_distinct_names(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window, "bolt")
        _load(window, "bolt")

        assert window.models.names == ("bolt", "bolt (2)")

    def test_tree_shows_part_counts(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        item = window.model_tree.topLevelItem(0)

        assert item is not None
        assert item.text(1) == "2"

    def test_newly_loaded_model_is_selected(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        entry = _load(window)

        assert window.models.selected_id == entry.model_id


class TestSelection:
    def test_nothing_selected_at_start(self, window: MainWindow) -> None:
        assert window.selected_model is None

    def test_selection_highlights_in_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        first = _load(window, "a")
        _load(window, "b")

        window.select_model(first.model_id)

        assert viewport.highlighted == first.model_id

    def test_clearing_selection_clears_the_highlight(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        _load(window)

        window.select_model(None)

        assert viewport.highlighted is None

    def test_status_bar_names_the_selected_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        first = _load(window, "alpha")
        _load(window, "beta")

        window.select_model(first.model_id)

        assert "alpha" in window.statusBar().currentMessage()

    def test_code_selection_reaches_the_tree(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Selection can change from code too (a neighbour after removal). A row
        # highlighted in the tree must always be the one the app acts on.
        window, _ = window_with_viewport
        first = _load(window, "a")
        _load(window, "b")

        window.select_model(first.model_id)

        assert window.model_tree.selected_model_id == first.model_id

    def test_clearing_selection_clears_the_tree(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.select_model(None)

        assert window.model_tree.selected_model_id is None

    def test_removal_leaves_tree_and_registry_agreeing(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "a")
        _load(window, "b")

        window.remove_selected_model()

        assert window.model_tree.selected_model_id == window.models.selected_id

    def test_tree_selection_reaches_the_window(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        first = _load(window, "a")
        _load(window, "b")

        # `setCurrentItem` is what a click does; `setSelected` alone would not
        # deselect the other row, since single-selection is only enforced for
        # user interaction.
        first_item = window.model_tree.topLevelItem(0)
        assert first_item is not None
        window.model_tree.setCurrentItem(first_item)

        assert window.models.selected_id == first.model_id


class TestActionsFollowSelection:
    def test_placement_disabled_without_a_model(self, window: MainWindow) -> None:
        # No target means the action must be disabled, not silently do nothing.
        assert window.placement_action.isEnabled() is False

    def test_remove_disabled_without_a_model(self, window: MainWindow) -> None:
        assert window.remove_action.isEnabled() is False

    def test_fit_disabled_without_a_model(self, window: MainWindow) -> None:
        assert window.fit_action.isEnabled() is False

    def test_placement_enabled_once_a_model_is_selected(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window)

        assert window.placement_action.isEnabled() is True

    def test_remove_enabled_once_a_model_is_selected(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window)

        assert window.remove_action.isEnabled() is True

    def test_actions_disabled_again_when_selection_cleared(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.select_model(None)

        assert window.placement_action.isEnabled() is False
        assert window.remove_action.isEnabled() is False

    def test_dialog_not_opened_without_a_selection(self, window: MainWindow) -> None:
        assert window.open_placement_dialog() is None


class TestRemoving:
    def test_remove_drops_the_row(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.remove_action.trigger()

        assert window.model_tree.topLevelItemCount() == 0

    def test_remove_reaches_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window)

        window.remove_selected_model()

        assert viewport.removed == [entry.model_id]

    def test_remove_selects_a_neighbour(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "a")
        _load(window, "b")

        window.remove_selected_model()

        assert window.selected_model is not None

    def test_remove_without_selection_is_harmless(self, window: MainWindow) -> None:
        window.remove_selected_model()

        assert window.models.is_empty


class TestPlacing:
    def test_menu_model_existuje(self, window: MainWindow) -> None:
        assert "Model" in menu_titles(window)

    def test_menu_model_obsahuje_polozky(self, window: MainWindow) -> None:
        assert menu_items(window, "Model") == ["Placement…", "Rename…", "Remove"]

    def test_polozka_ma_skratku(self, window: MainWindow) -> None:
        assert not window.placement_action.shortcut().isEmpty()

    def test_the_dialog_opens(self, window_with_viewport: tuple[MainWindow, _StubViewport]) -> None:
        window, _ = window_with_viewport
        _load(window)

        dialog = window.open_placement_dialog()

        assert dialog is not None
        assert dialog.isVisible()
        dialog.close()

    def test_dialog_pojmenuje_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # With several models loaded the dialog must say which one it edits.
        window, _ = window_with_viewport
        _load(window, "gantry")

        dialog = window.open_placement_dialog()

        assert dialog is not None
        assert "gantry" in dialog.windowTitle()
        dialog.close()

    def test_a_second_call_creates_no_second_dialog(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)
        first = window.open_placement_dialog()

        second = window.open_placement_dialog()

        assert first is second
        assert first is not None
        first.close()

    def test_the_dialog_shows_the_current_placement(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window)
        window.models.set_placement(entry.model_id, Transform(xyz=(0.25, 0.0, 0.0)))

        dialog = window.open_placement_dialog()

        assert dialog is not None
        assert dialog.x_spin.value() == pytest.approx(250.0)
        dialog.close()

    def test_a_change_in_the_dialog_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window)
        dialog = window.open_placement_dialog()

        assert dialog is not None
        dialog.x_spin.setValue(500.0)

        assert viewport.placements[entry.model_id].xyz[0] == pytest.approx(0.5)
        dialog.close()

    def test_rotation_reaches_the_scene_in_radians(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window)
        dialog = window.open_placement_dialog()

        assert dialog is not None
        dialog.rotate_z_spin.setValue(90.0)

        assert viewport.placements[entry.model_id].rpy[2] == pytest.approx(math.pi / 2)
        dialog.close()

    def test_placement_is_per_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Moving one model must leave the other where it was.
        window, _ = window_with_viewport
        first = _load(window, "a")
        second = _load(window, "b")

        window.select_model(first.model_id)
        window.apply_placement(Transform(xyz=(1.0, 0.0, 0.0)))

        assert window.placement(first.model_id).xyz[0] == pytest.approx(1.0)
        assert window.placement(second.model_id).xyz[0] == pytest.approx(0.0)

    def test_zmena_vyberu_zavrie_dialog(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The dialog belongs to one model; leaving it open against another
        # selection would apply edits to the wrong one.
        window, _ = window_with_viewport
        first = _load(window, "a")
        second = _load(window, "b")
        window.select_model(first.model_id)
        dialog = window.open_placement_dialog()

        window.select_model(second.model_id)

        assert dialog is not None
        assert not dialog.isVisible()

    def test_the_status_bar_reports_the_placement_in_mm(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.apply_placement(Transform(xyz=(0.1, 0.0, 0.0)))

        assert "100" in window.statusBar().currentMessage()

    def test_a_viewport_without_support_does_not_crash(self, window: MainWindow) -> None:
        window.apply_placement(Transform(xyz=(1.0, 0.0, 0.0)))

        assert window.centralWidget() is not None


class TestLoading:
    def test_nothing_runs_at_the_start(self, window: MainWindow) -> None:
        assert window.is_loading is False

    def test_open_is_disabled_while_loading(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two imports at once would overwrite both the cache and the scene.
        monkeypatch.setattr("pssim.ui.main_window.StepImportThread", _StubThread)

        window.load_file(tmp_path / "stroj.step")

        assert window.open_action.isEnabled() is False

    def test_a_second_attempt_while_loading_is_ignored(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pssim.ui.main_window.StepImportThread", _StubThread)

        window.load_file(tmp_path / "prvy.step")
        started = _StubThread.started_count
        window.load_file(tmp_path / "druhy.step")

        assert _StubThread.started_count == started

    def test_open_is_enabled_again_when_finished(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pssim.ui.main_window.StepImportThread", _StubThread)
        window.load_file(tmp_path / "stroj.step")

        window.on_import_finished()  # stands in for the signal from the thread

        assert window.open_action.isEnabled() is True
        assert window.is_loading is False


class TestContextMenu:
    def test_menu_on_empty_space_offers_adding_a_model(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(on_model=False)

        assert _menu_labels(menu) == ["Add Model…"]

    def test_menu_on_empty_space_leaves_out_model_actions(self, window: MainWindow) -> None:
        # Left out rather than greyed out: the selection survives a click into
        # empty space, so a disabled Rename would contradict the toolbar.
        menu = window.model_tree.build_context_menu(on_model=False)

        assert "Rename…" not in _menu_labels(menu)

    def test_menu_on_a_model_offers_every_action(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(on_model=True)

        assert _menu_labels(menu) == ["Add Model…", "Rename…", "Placement…", "Remove"]

    def test_rename_is_reachable_by_f2(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(on_model=True)
        renames = [a for a in menu.actions() if a.text().replace("&", "") == "Rename…"]

        assert renames[0].shortcut() == QKeySequence(Qt.Key.Key_F2)

    def test_add_opens_the_file_dialog(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        window, _ = window_with_viewport
        step = tmp_path / "part.step"
        step.write_text("dummy")
        monkeypatch.setattr("pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(step))
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_loads(started))

        window.model_tree.add_requested.emit()

        assert started == [step]

    def test_placement_opens_the_dialog_for_the_selection(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")

        window.model_tree.placement_requested.emit()

        dialog = window.findChild(PlacementDialog)
        assert dialog is not None
        assert entry.name in dialog.windowTitle()

    def test_remove_drops_the_selection(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")

        window.model_tree.remove_requested.emit()

        assert window.models.is_empty is True

    def test_selecting_a_row_makes_it_the_target(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # What the right-click does before opening the menu. Acting on the old
        # selection instead of the clicked row moves the wrong part.
        window, _ = window_with_viewport
        first = _load(window, "a")
        _load(window, "b")
        tree = window.model_tree
        item = tree.topLevelItem(0)
        assert item is not None

        tree.setCurrentItem(item)

        assert window.models.selected_id == first.model_id


class TestRenaming:
    def test_rename_applies_the_typed_name(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("conveyor"))

        assert window.rename_selected_model() == "conveyor"

    def test_tree_shows_the_new_name(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("conveyor"))

        window.rename_selected_model()

        item = window.model_tree.topLevelItem(0)
        assert item is not None
        assert item.text(0) == "conveyor"

    def test_cancel_leaves_the_name_alone(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name(None))

        window.rename_selected_model()

        assert window.models.names == ("gantry",)

    def test_blank_name_is_refused(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("   "))

        assert window.rename_selected_model() is None
        assert window.models.names == ("gantry",)

    def test_taken_name_gets_a_counter(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        _load(window, "conveyor")
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("gantry"))

        assert window.rename_selected_model() == "gantry (2)"

    def test_counter_is_explained_in_the_status_bar(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The name in the tree is not the one that was typed, so it gets said.
        window, _ = window_with_viewport
        _load(window, "gantry")
        _load(window, "conveyor")
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("gantry"))

        window.rename_selected_model()

        assert "gantry (2)" in window.statusBar().currentMessage()

    def test_rename_keeps_the_placement(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.apply_placement(Transform(xyz=(0.3, 0.0, 0.0)))
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("conveyor"))

        window.rename_selected_model()

        renamed = window.selected_model
        assert renamed is not None
        assert renamed.placement.xyz[0] == pytest.approx(0.3)
        assert viewport.placements[entry.model_id].xyz[0] == pytest.approx(0.3)

    def test_renamed_placed_model_keeps_its_marker(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        window.apply_placement(Transform(xyz=(0.3, 0.0, 0.0)))
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("conveyor"))

        window.rename_selected_model()

        item = window.model_tree.topLevelItem(0)
        assert item is not None
        assert item.text(0) == "conveyor *"

    def test_rename_without_a_selection_does_nothing(self, window: MainWindow) -> None:
        assert window.rename_selected_model() is None

    def test_action_is_disabled_without_a_selection(self, window: MainWindow) -> None:
        assert window.rename_action.isEnabled() is False

    def test_action_is_enabled_with_a_selection(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window, "gantry")

        assert window.rename_action.isEnabled() is True

    def test_context_menu_request_reaches_the_dialog(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        monkeypatch.setattr("pssim.ui.main_window.QInputDialog.getText", _type_name("conveyor"))

        window.model_tree.rename_requested.emit()

        assert window.models.names == ("conveyor",)
