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

from PySide6.QtCore import QObject, QSettings, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QToolButton, QWidget  # noqa: E402

from pssim.cad.model import CadAssembly, CadNode  # noqa: E402
from pssim.domain.machine import Rgba, Transform, Vec3  # noqa: E402
from pssim.domain.model_joints import Anchor, ModelJoint, ModelJointKind  # noqa: E402
from pssim.domain.placement import IDENTITY_PLACEMENT  # noqa: E402
from pssim.domain.sensors import Sensor, SensorKind, SensorReading, from_sensor  # noqa: E402
from pssim.ui.joint_dialog import BindDialog, JointDialog, PickTarget  # noqa: E402
from pssim.ui.labels import describe_reading  # noqa: E402
from pssim.ui.main_window import APP_TITLE, MainWindow  # noqa: E402
from pssim.ui.model_registry import ModelEntry  # noqa: E402
from pssim.ui.model_tree import COLUMN_NAME, RowState, TreeTarget  # noqa: E402
from pssim.ui.model_values_panel import ModelValuesPanel  # noqa: E402
from pssim.ui.placement_dialog import PlacementDialog  # noqa: E402
from pssim.ui.sensor_dialog import SensorDialog  # noqa: E402
from pssim.ui.sensor_fields import kind_index  # noqa: E402
from pssim.ui.settings import SettingsStore  # noqa: E402
from pssim.ui.sizes_dialog import Sizes, SizesDialog  # noqa: E402
from pssim.viz.embed import (  # noqa: E402
    DEFAULT_CROSS_SIZE_M,
    DEFAULT_ORIGIN_CROSS_SIZE_M,
    DEFAULT_TEXT_SIZE_M,
)
from pssim.viz.orbit import STANDARD_VIEWS  # noqa: E402
from tests.factories import (
    axis_joint,
    beam_sensor,
    proximity_sensor,
    trajectory_joint,
)

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    """One `QApplication` per module — Qt allows no more than one."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def settings_store(tmp_path: Path) -> SettingsStore:
    """Column widths and connection settings in a throwaway ini file.

    Injected rather than defaulted, for the same reason `RecentProjects` takes
    its `QSettings`: the real one is the user's own store, and closing a window
    saves the layout into it.
    """
    return SettingsStore(QSettings(str(tmp_path / "pssim.ini"), QSettings.Format.IniFormat))


@pytest.fixture
def window(qt_app: QApplication, settings_store: SettingsStore) -> Iterator[MainWindow]:
    """A fresh window for every test, closed afterwards.

    Instead of the real 3D viewport it gets a plain widget: `ShowBase` may exist only
    once per process, so in the tests it is never created at all. Displaying geometry
    is verified by `tests/integration/test_viz_scene.py` and by real runs.
    """
    instance = MainWindow(viewport_factory=QWidget, settings=settings_store)
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


def submenu_items(window: MainWindow, title: str, group: str) -> list[str]:
    """The entries of one submenu, e.g. `Scene → Crosses and Labels`."""
    for action in window.menuBar().actions():
        if action.text().replace("&", "") != title:
            continue
        menu = action.menu()
        assert isinstance(menu, QMenu), f"menu {title!r} has no sub-items"
        for item in menu.actions():
            if item.text().replace("&", "") != group:
                continue
            nested = item.menu()
            assert isinstance(nested, QMenu), f"{title!r} → {group!r} is not a submenu"
            return [entry.text().replace("&", "") for entry in nested.actions()]
        raise AssertionError(f"{title!r} has no {group!r}; it has {menu_items(window, title)}")
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
        # Split by subject: what you are working on, not what you are doing to it.
        assert menu_titles(window) == ["File", "Models", "Geometry", "Sensors", "Scene"]

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

    def test_adding_a_3d_model_lives_in_the_models_menu(self, window: MainWindow) -> None:
        # It adds a model rather than replacing the scene, so it belongs with the
        # other model actions and not in a menu of its own.
        assert menu_items(window, "Models")[0] == "Add 3D Model…"

    def test_every_creation_entry_starts_with_add(self, window: MainWindow) -> None:
        created = [
            menu_items(window, "Models")[0],
            *menu_items(window, "Geometry")[:2],
            menu_items(window, "Sensors")[0],
        ]

        assert [text.split()[0] for text in created] == ["Add"] * 4

    def test_the_geometry_menu_holds_the_axes_and_trajectories(self, window: MainWindow) -> None:
        # They are neither a model nor a sensor, which is why they had nowhere to
        # go and ended up reachable only from the tree.
        entries = [text for text in menu_items(window, "Geometry") if text]

        assert entries == ["Add Axis…", "Add Trajectory…", "Edit…", "Carried By…", "Remove"]

    def test_the_sensors_have_their_own_menu(self, window: MainWindow) -> None:
        # Below the separator the menu title already says what the subject is,
        # so the leaves drop the noun.
        entries = [text for text in menu_items(window, "Sensors") if text]

        assert entries == ["Add Sensor…", "Edit…", "Mount On…", "Remove"]

    def test_the_scene_menu_groups_what_it_offers(self, window: MainWindow) -> None:
        # A leaf should say what it does from its path alone — `Sizes…` on its
        # own does not say what it sizes.
        entries = [text for text in menu_items(window, "Scene") if text]

        assert entries == ["Floor", "Crosses and Labels", "Check Collisions"]

    def test_exit_ma_klavesovu_skratku(self, window: MainWindow) -> None:
        assert not window.exit_action.shortcut().isEmpty()

    def test_open_ma_klavesovu_skratku(self, window: MainWindow) -> None:
        assert not window.insert_model_action.shortcut().isEmpty()


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
        self.floor_visible = True
        self.floor_z_m = 0.0
        self.sensors_added: dict[str, Sensor] = {}
        self.sensor_mounts: dict[str, str | None] = {}
        self.sensor_readings: dict[str, SensorReading] = {}
        self.sensors_updated: dict[str, Sensor] = {}
        self.sensors_removed: list[str] = []
        self.joints_added: dict[str, tuple[str | None, ModelJoint]] = {}
        self.joints_updated: dict[str, ModelJoint] = {}
        self.joints_removed: list[str] = []
        self.joint_values: dict[str, float] = {}
        self.bindings: dict[str, str | None] = {}
        self.joint_parents: dict[str, str | None] = {}
        self.anchors: dict[str, Anchor] = {}
        self.pick_frames: list[tuple[str, str | None]] = []
        self.joint_previews: list[tuple[str, ModelJoint]] = []
        self.joint_preview_cleared = 0
        self.pick_cancelled = 0
        self.model_visibility: dict[str, bool] = {}
        self.axes_visibility: dict[str, bool] = {}
        self.model_colors: dict[str, Rgba | None] = {}
        self.highlight_colors: dict[str, Rgba | None] = {}
        self.collision_checks = 0
        self.collision_result: frozenset[tuple[str, str]] = frozenset()
        self.cross_size_m = 0.2
        self.text_size_m = 0.05
        self.origin_cross_size_m = 0.2
        self.origin_cross_visible = True
        self.joint_colors: dict[str, Rgba | None] = {}
        self.joint_names: dict[str, bool] = {}
        self.names_visible = True
        self._pick_callback: Callable[[Vec3], None] | None = None

    def set_view(self, name: str) -> None:
        self.views.append(name)

    def fit_view(self, model_id: str | None = None) -> None:
        self.fit_calls.append(model_id)

    def set_highlight(self, model_id: str | None) -> None:
        self.highlighted = model_id

    def set_model_visible(self, model_id: str, is_visible: bool) -> None:
        self.model_visibility[model_id] = is_visible

    def set_axes_visible(self, item_id: str, show_axes: bool) -> None:
        self.axes_visibility[item_id] = show_axes

    def set_model_color(self, model_id: str, color: Rgba | None) -> None:
        self.model_colors[model_id] = color

    def set_highlight_color(self, model_id: str, color: Rgba | None) -> None:
        self.highlight_colors[model_id] = color

    def check_collisions(self) -> frozenset[tuple[str, str]]:
        self.collision_checks += 1
        return self.collision_result

    def set_cross_size(self, size_m: float) -> None:
        self.cross_size_m = size_m

    def set_text_size(self, size_m: float) -> None:
        self.text_size_m = size_m

    def set_origin_cross_size(self, size_m: float) -> None:
        self.origin_cross_size_m = size_m

    def set_origin_cross_visible(self, visible: bool) -> None:
        self.origin_cross_visible = visible

    def set_joint_color(self, joint_id: str, color: Rgba | None) -> None:
        self.joint_colors[joint_id] = color

    def set_joint_name_visible(self, joint_id: str, show_name: bool) -> None:
        self.joint_names[joint_id] = show_name

    def set_names_visible(self, show_names: bool) -> None:
        self.names_visible = show_names

    def add_model(self, model_id: str, assembly: object, cache_dir: Path) -> int:
        self.added.append(model_id)
        return 0

    def remove_model(self, model_id: str) -> None:
        self.removed.append(model_id)

    def placement(self, model_id: str) -> Transform:
        return self.placements.get(model_id, IDENTITY_PLACEMENT)

    def set_placement(self, model_id: str, placement: Transform) -> None:
        self.placements[model_id] = placement

    def set_floor_visible(self, visible: bool) -> None:
        self.floor_visible = visible

    def set_floor_z(self, z_m: float) -> None:
        self.floor_z_m = z_m

    def add_sensor(self, sensor_id: str, sensor: Sensor, mounted_on: str | None = None) -> None:
        self.sensors_added[sensor_id] = sensor
        self.sensor_mounts[sensor_id] = mounted_on

    def set_sensor_mount(self, sensor_id: str, mounted_on: str | None) -> None:
        self.sensor_mounts[sensor_id] = mounted_on

    def sensor_reading(self, sensor_id: str) -> SensorReading | None:
        return self.sensor_readings.get(sensor_id)

    def update_sensor(self, sensor_id: str, sensor: Sensor) -> None:
        self.sensors_updated[sensor_id] = sensor

    def remove_sensor(self, sensor_id: str) -> None:
        self.sensors_removed.append(sensor_id)
        self.sensors_added.pop(sensor_id, None)

    def add_joint(
        self, joint_id: str, joint: ModelJoint, parent_joint_id: str | None = None
    ) -> None:
        self.joints_added[joint_id] = (parent_joint_id, joint)

    def remove_joint(self, joint_id: str) -> None:
        self.joints_removed.append(joint_id)
        self.joints_added.pop(joint_id, None)

    def update_joint(self, joint_id: str, joint: ModelJoint) -> None:
        self.joints_updated[joint_id] = joint

    def set_joint_value(self, joint_id: str, value: float) -> None:
        self.joint_values[joint_id] = value

    def bind_model(self, model_id: str, joint_id: str | None) -> None:
        self.bindings[model_id] = joint_id

    def set_joint_parent(self, joint_id: str, parent_joint_id: str | None) -> None:
        self.joint_parents[joint_id] = parent_joint_id

    def set_anchor(self, model_id: str, anchor: Anchor) -> None:
        self.anchors[model_id] = anchor

    def anchor(self, model_id: str) -> Anchor:
        return self.anchors.get(model_id, Anchor())

    def preview_joint(self, joint: ModelJoint, parent_joint_id: str | None = None) -> None:
        self.joint_previews.append((parent_joint_id, joint))

    def clear_joint_preview(self) -> None:
        self.joint_preview_cleared += 1

    def begin_pick(self, model_id: str, on_point_picked: Callable[[Vec3], None]) -> None:
        self._pick_callback = on_point_picked

    def begin_pick_in_joint_frame(
        self,
        model_id: str,
        parent_joint_id: str | None,
        on_point_picked: Callable[[Vec3], None],
    ) -> None:
        self.pick_frames.append((model_id, parent_joint_id))
        self._pick_callback = on_point_picked

    def cancel_pick(self) -> None:
        self.pick_cancelled += 1
        self._pick_callback = None

    def resolve_pick(self, point: Vec3) -> None:
        """Test helper: simulate a click resolving, as `viz.picking.PointPicker`
        would, by invoking whatever callback `begin_pick` was last given."""
        if self._pick_callback is not None:
            self._pick_callback(point)


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


def _submenu_labels(menu: QMenu, title: str) -> list[str]:
    """The entries of one submenu, with the accelerators stripped."""
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None and action.text().replace("&", "") == title:
            return _menu_labels(submenu)
    raise AssertionError(f"no {title!r} submenu in {_menu_labels(menu)}")


@pytest.fixture
def window_with_viewport(
    qt_app: QApplication, settings_store: SettingsStore
) -> Iterator[tuple[MainWindow, _StubViewport]]:
    viewport = _StubViewport()
    instance = MainWindow(viewport_factory=lambda: viewport, settings=settings_store)
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
    def test_the_models_menu_exists(self, window: MainWindow) -> None:
        assert "Models" in menu_titles(window)

    def test_the_models_menu_lists_the_model_actions(self, window: MainWindow) -> None:
        entries = [text for text in menu_items(window, "Models") if text]

        assert entries == [
            "Add 3D Model…",
            "Placement…",
            "Rename…",
            "Bind To…",
            "Variables…",
            "Remove",
        ]

    def test_only_one_remove_carries_the_delete_shortcut(self, window: MainWindow) -> None:
        # Three of them bound to Delete would be ambiguous and Qt would fire none.
        assert not window.remove_action.shortcut().isEmpty()
        assert window.remove_joint_action.shortcut().isEmpty()
        assert window.remove_sensor_action.shortcut().isEmpty()

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

        assert window.insert_model_action.isEnabled() is False

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

        assert window.insert_model_action.isEnabled() is True
        assert window.is_loading is False


class TestContextMenu:
    def test_menu_on_empty_space_offers_adding_a_model(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(TreeTarget.EMPTY)

        # Adding a joint needs no selection any more, so empty space offers it.
        assert _menu_labels(menu) == ["Add Model…", "Add Axis…", "Add Trajectory…"]

    def test_menu_on_empty_space_leaves_out_model_actions(self, window: MainWindow) -> None:
        # Left out rather than greyed out: the selection survives a click into
        # empty space, so a disabled Rename would contradict the toolbar.
        menu = window.model_tree.build_context_menu(TreeTarget.EMPTY)

        assert "Rename…" not in _menu_labels(menu)

    def test_menu_on_a_model_offers_every_action(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(TreeTarget.MODEL)

        # No "Add Axis" here any more: a joint belongs to the scene, not to a
        # model, so a model's own menu offers attaching *to* one instead.
        assert _menu_labels(menu) == [
            "Add Model…",
            "Rename…",
            "Placement…",
            "Bind to…",
            "Visible",
            "Show Coordinate Cross",
            "Colour",
            "Remove",
        ]

    def test_menu_on_a_driven_model_also_offers_editing_variables(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(TreeTarget.MODEL, RowState(is_driven=True))

        assert "Edit Variables…" in _menu_labels(menu)

    def test_menu_on_a_joint_offers_edit_and_remove(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(TreeTarget.JOINT)

        # A joint row can also grow a child joint — that is how a chain is
        # built (a rail carrying a rotation axis).
        assert _menu_labels(menu) == [
            "Add Model…",
            "Edit…",
            "Carried By…",
            "Show Coordinate Cross",
            "Show Name",
            "Colour…",
            "Add Child Axis…",
            "Add Child Trajectory…",
            "Remove",
        ]

    def test_rename_is_reachable_by_f2(self, window: MainWindow) -> None:
        menu = window.model_tree.build_context_menu(TreeTarget.MODEL)
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


class TestFloor:
    def test_scene_menu_exists(self, window: MainWindow) -> None:
        assert "Scene" in menu_titles(window)

    def test_the_menu_offers_the_floor_items(self, window: MainWindow) -> None:
        assert submenu_items(window, "Scene", "Floor") == ["Show Floor", "Position…"]

    def test_floor_is_visible_by_default(self, window: MainWindow) -> None:
        assert window.floor_visible_action.isChecked() is True

    def test_toggling_the_action_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.floor_visible_action.setChecked(False)

        assert viewport.floor_visible is False

    def test_a_toggle_without_support_does_not_crash(self, window: MainWindow) -> None:
        window.floor_visible_action.setChecked(False)

        assert window.centralWidget() is not None

    def test_opening_the_dialog(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        dialog = window.open_floor_dialog()

        assert dialog is not None
        assert dialog.isVisible()
        dialog.close()

    def test_a_second_call_creates_no_second_dialog(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        first = window.open_floor_dialog()

        second = window.open_floor_dialog()

        assert first is second
        assert first is not None
        first.close()

    def test_a_change_in_the_dialog_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        dialog = window.open_floor_dialog()

        assert dialog is not None
        dialog.z_spin.setValue(500.0)

        assert viewport.floor_z_m == pytest.approx(0.5)
        dialog.close()

    def test_a_new_dialog_can_open_after_the_old_one_closed(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        first = window.open_floor_dialog()
        assert first is not None
        first.close()

        second = window.open_floor_dialog()

        assert second is not None
        assert second is not first
        second.close()

    def test_a_viewport_without_support_does_not_crash(self, window: MainWindow) -> None:
        window.apply_floor_z(1.0)

        assert window.centralWidget() is not None


def _accept_sensor(sensor: Sensor) -> Callable[[SensorDialog], int]:
    """Stand-in for `SensorDialog.exec` that fills the dialog from `sensor` and
    reports Accepted, without running a real modal event loop — nobody in the
    test is there to click OK."""

    def fake_exec(self: SensorDialog) -> int:
        self.set_display(from_sensor(sensor))
        return QDialog.DialogCode.Accepted

    return fake_exec


def _cancel_sensor(_self: SensorDialog) -> int:
    """Stand-in for `SensorDialog.exec` reporting Cancel."""
    return QDialog.DialogCode.Rejected


class TestSensors:
    def test_sensor_dock_exists(self, window: MainWindow) -> None:
        assert window.sensor_tree is not None

    def test_tree_starts_empty(self, window: MainWindow) -> None:
        assert window.sensor_tree.topLevelItemCount() == 0

    def test_add_opens_the_dialog_and_registers_the_sensor(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(beam_sensor(name="gate")))

        window.open_sensor_dialog()

        assert window.sensors.names == ("gate",)

    def test_a_cancelled_dialog_adds_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SensorDialog, "exec", _cancel_sensor)

        window.open_sensor_dialog()

        assert window.sensors.is_empty is True

    def test_adding_reaches_the_scene(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        sensor = beam_sensor(name="gate")
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(sensor))

        window.open_sensor_dialog()

        entry = window.sensors.selected
        assert entry is not None
        assert viewport.sensors_added[entry.sensor_id].name == "gate"

    def test_adding_selects_and_refreshes_the_tree(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(proximity_sensor(name="zone")))

        window.open_sensor_dialog()

        assert window.sensor_tree.topLevelItemCount() == 1
        assert window.sensor_tree.selected_sensor_id == window.sensors.selected_id

    def test_edit_without_a_selection_does_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(beam_sensor()))

        window.edit_selected_sensor()

        assert window.sensors.is_empty is True

    def test_edit_replaces_the_sensor_and_reaches_the_scene(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(beam_sensor(name="gate")))
        window.open_sensor_dialog()
        entry = window.sensors.selected
        assert entry is not None

        edited = beam_sensor(name="gate", origin=(1.0, 0.0, 0.0), target=(2.0, 0.0, 0.0))
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(edited))
        window.edit_selected_sensor()

        updated = window.sensors.get(entry.sensor_id)
        assert updated is not None
        assert updated.sensor.origin == (1.0, 0.0, 0.0)
        assert viewport.sensors_updated[entry.sensor_id].origin == (1.0, 0.0, 0.0)

    def test_remove_without_a_selection_does_nothing(self, window: MainWindow) -> None:
        window.remove_selected_sensor()

        assert window.sensors.is_empty is True

    def test_remove_drops_the_sensor_and_reaches_the_scene(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(beam_sensor(name="gate")))
        window.open_sensor_dialog()
        entry = window.sensors.selected
        assert entry is not None

        window.remove_selected_sensor()

        assert window.sensors.is_empty is True
        assert entry.sensor_id in viewport.sensors_removed

    def test_edit_action_is_disabled_without_a_selection(self, window: MainWindow) -> None:
        assert window.edit_sensor_action.isEnabled() is False

    def test_remove_action_is_disabled_without_a_selection(self, window: MainWindow) -> None:
        assert window.remove_sensor_action.isEnabled() is False

    def test_edit_action_is_enabled_once_a_sensor_exists(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(beam_sensor()))

        window.open_sensor_dialog()

        assert window.edit_sensor_action.isEnabled() is True
        assert window.remove_sensor_action.isEnabled() is True

    def test_selecting_a_row_makes_it_the_target(self, window: MainWindow) -> None:
        window.sensors.add(beam_sensor(name="a"))
        second = window.sensors.add(beam_sensor(name="b"))
        window.sensor_tree.refresh(window.sensors)
        first_id = next(e.sensor_id for e in window.sensors if e.sensor.name == "a")

        window.select_sensor(first_id)

        assert window.selected_sensor is not None
        assert window.selected_sensor.sensor_id == first_id
        assert window.selected_sensor.sensor_id != second.sensor_id

    def test_close_all_models_also_clears_the_sensors(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(beam_sensor(name="gate")))
        window.open_sensor_dialog()

        window.close_all_models()

        assert window.sensors.is_empty is True
        assert window.sensor_tree.topLevelItemCount() == 0

    def test_a_viewport_without_support_does_not_crash(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SensorDialog, "exec", _accept_sensor(beam_sensor(name="gate")))
        window.open_sensor_dialog()

        window.edit_selected_sensor()
        window.remove_selected_sensor()

        assert window.centralWidget() is not None


class TestModelJoints:
    def test_add_opens_a_dialog_and_registers_the_joint(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
        assert dialog is not None
        dialog.name_edit.setText("tilt")
        dialog.accept()

        assert window.joints.names == ("tilt",)
        joint_id = window.joints.entries[0].joint_id
        assert joint_id in viewport.joints_added
        # It lands in the scene, not on the model: nothing owns it.
        assert viewport.joints_added[joint_id][0] is None

    def test_add_pre_sets_the_requested_kind(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        dialog = window.open_add_joint_dialog(ModelJointKind.TRAJECTORY)

        assert dialog is not None
        assert dialog.kind is ModelJointKind.TRAJECTORY
        dialog.close()

    def test_add_needs_no_selected_model(self, window: MainWindow) -> None:
        # A joint stands in the scene on its own, so nothing has to be selected
        # to create one.
        dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)

        assert dialog is not None
        dialog.close()

    def test_adding_with_a_joint_selected_creates_a_child(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # How a chain gets built: the new joint is carried by the selected one.
        window, viewport = window_with_viewport
        rail = window.joints.add(axis_joint(name="rail"))
        window.select_joint(rail.joint_id)

        dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
        dialog.name_edit.setText("head")
        dialog.accept()

        head = window.joints.entries[-1]
        assert head.parent_joint_id == rail.joint_id
        assert viewport.joints_added[head.joint_id][0] == rail.joint_id

    def test_a_field_change_reaches_the_live_preview(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
        assert dialog is not None
        before = len(viewport.joint_previews)

        dialog.origin_spins[0].setValue(50.0)

        assert len(viewport.joint_previews) == before + 1
        # Scoped to the parent joint now (the scene, here) rather than a model.
        assert viewport.joint_previews[-1][0] is None
        dialog.close()

    def test_a_pick_request_arms_the_viewport_and_the_result_reaches_the_dialog(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
        assert dialog is not None

        dialog.pick_requested.emit(PickTarget.ORIGIN)
        viewport.resolve_pick((0.25, 0.0, 0.0))

        assert dialog.origin_spins[0].value() == pytest.approx(250.0)
        dialog.close()

    def test_closing_without_accepting_clears_the_preview_and_the_pick(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
        assert dialog is not None
        dialog.pick_requested.emit(PickTarget.ORIGIN)

        dialog.reject()

        assert viewport.joint_preview_cleared >= 1
        assert viewport.pick_cancelled >= 1
        assert window.joints.is_empty is True

    def test_edit_opens_pre_filled_and_updates_the_joint(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        add_dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
        assert add_dialog is not None
        add_dialog.name_edit.setText("tilt")
        add_dialog.accept()
        joint_id = window.joints.entries[0].joint_id
        window.select_joint(joint_id)

        edit_dialog = window.open_edit_joint_dialog()
        assert edit_dialog is not None
        assert edit_dialog.name_edit.text() == "tilt"
        edit_dialog.origin_spins[0].setValue(10.0)
        edit_dialog.accept()

        updated = window.joints.get(joint_id)
        assert updated is not None
        assert updated.joint.origin[0] == pytest.approx(0.01)
        assert viewport.joints_updated[joint_id].origin[0] == pytest.approx(0.01)

    def test_edit_without_a_selection_does_nothing(self, window: MainWindow) -> None:
        assert window.open_edit_joint_dialog() is None

    def test_remove_drops_the_joint_and_reaches_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
        assert dialog is not None
        dialog.accept()
        joint_id = window.joints.entries[0].joint_id
        window.select_joint(joint_id)

        window.remove_selected_joint()

        assert window.joints.is_empty is True
        assert joint_id in viewport.joints_removed

    def test_remove_without_a_selection_does_nothing(self, window: MainWindow) -> None:
        window.remove_selected_joint()

        assert window.joints.is_empty is True

    def test_selecting_a_joint_selects_no_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # A joint belongs to no model, so there is nothing to select on its
        # behalf — the opposite of what this asserted before the inversion.
        window, _ = window_with_viewport
        entry = _load(window, "a")
        window.select_model(entry.model_id)
        joint_id = window.joints.add(axis_joint(name="tilt")).joint_id
        window.model_tree.refresh(window.models, window.joints)
        window.joints.select(None)

        window.select_joint(joint_id)

        assert window.selected_joint is not None
        assert window.selected_joint.joint_id == joint_id

    def test_selecting_a_model_clears_the_joint_selection(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        window.joints.add(axis_joint(name="tilt"))
        window.model_tree.refresh(window.models, window.joints)
        window.select_joint(window.joints.entries[0].joint_id)

        window.select_model(entry.model_id)

        assert window.selected_joint is None

    def test_removing_a_model_keeps_the_joint_that_carried_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The inversion's whole point: the joint is the higher-level thing, so
        # deleting a model must not take it down. This asserted the opposite
        # before, when a model owned its joints.
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = window.joints.add(axis_joint(name="tilt")).joint_id
        window.apply_binding(entry.model_id, joint_id)
        window.select_model(entry.model_id)

        window.remove_selected_model()

        assert window.joints.get(joint_id) is not None
        assert entry.model_id in viewport.removed

    def test_removing_a_joint_releases_the_models_it_carried(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The cascade now runs the other way: a model owns no joints, so it is
        # removing the *joint* that has to release what rode it.
        window, _viewport = window_with_viewport
        model = _load(window, "arm")
        joint_id = window.joints.add(axis_joint(name="pivot")).joint_id
        window.apply_binding(model.model_id, joint_id)

        window.select_joint(joint_id)
        window.remove_selected_joint()

        updated = window.models.get(model.model_id)
        assert updated is not None
        assert updated.bound_to_joint_id is None
        assert window.models.get(model.model_id) is not None

    def test_close_all_models_also_clears_the_joints(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        window.joints.add(axis_joint(name="tilt"))

        window.close_all_models()

        assert window.joints.is_empty is True

    def test_tree_signals_open_the_right_dialogs(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.add_trajectory_requested.emit()

        dialogs = window.findChildren(JointDialog)
        assert len(dialogs) == 1
        assert dialogs[0].kind is ModelJointKind.TRAJECTORY


def _accept_bind(label: str) -> Callable[[BindDialog], int]:
    """Stand-in for `BindDialog.exec` that picks a joint by its label and
    reports Accepted, without running a real modal loop."""

    def fake_exec(self: BindDialog) -> int:
        index = self.joint_combo.findText(label)
        if index != -1:
            self.joint_combo.setCurrentIndex(index)
        return QDialog.DialogCode.Accepted

    return fake_exec


def _cancel_bind(_self: BindDialog) -> int:
    """Stand-in for `BindDialog.exec` reporting Cancel."""
    return QDialog.DialogCode.Rejected


class TestBinding:
    def test_binding_updates_the_registry_and_the_viewport(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        model = _load(window, "arm")
        joint_id = window.joints.add(axis_joint(name="pivot")).joint_id
        monkeypatch.setattr(BindDialog, "exec", _accept_bind("pivot"))

        window.select_model(model.model_id)
        window.open_bind_dialog()

        updated = window.models.get(model.model_id)
        assert updated is not None
        assert updated.bound_to_joint_id == joint_id
        assert viewport.bindings[model.model_id] == joint_id

    def test_cancelling_changes_nothing(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window, "arm")
        window.joints.add(axis_joint(name="pivot"))
        monkeypatch.setattr(BindDialog, "exec", _cancel_bind)

        window.select_model(model.model_id)
        window.open_bind_dialog()

        updated = window.models.get(model.model_id)
        assert updated is not None
        assert updated.bound_to_joint_id is None

    def test_choosing_none_releases_the_model(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        model = _load(window, "arm")
        joint_id = window.joints.add(axis_joint(name="pivot")).joint_id
        window.apply_binding(model.model_id, joint_id)
        monkeypatch.setattr(BindDialog, "exec", _accept_bind(BindDialog.NONE_LABEL))

        window.select_model(model.model_id)
        window.open_bind_dialog()

        updated = window.models.get(model.model_id)
        assert updated is not None
        assert updated.bound_to_joint_id is None
        assert viewport.bindings[model.model_id] is None

    def test_with_no_joints_it_reports_status_and_opens_nothing(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # A joint has to exist before anything can be bound to one.
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        assert window.open_bind_dialog() is None

    def test_without_a_selected_model_does_nothing(self, window: MainWindow) -> None:
        assert window.open_bind_dialog() is None

    def test_a_joint_can_be_hung_under_another(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The whole point of the inversion: a rail carrying a rotation axis.
        window, viewport = window_with_viewport
        rail = window.joints.add(axis_joint(name="rail"))
        head = window.joints.add(axis_joint(name="head"))
        monkeypatch.setattr(BindDialog, "exec", _accept_bind("rail"))

        window.select_joint(head.joint_id)
        window.open_joint_parent_dialog()

        updated = window.joints.get(head.joint_id)
        assert updated is not None
        assert updated.parent_joint_id == rail.joint_id
        assert viewport.joint_parents[head.joint_id] == rail.joint_id

    def test_a_joints_own_descendants_are_not_offered_as_its_parent(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, _ = window_with_viewport
        rail = window.joints.add(axis_joint(name="rail"))
        window.joints.add(axis_joint(name="head"), parent_joint_id=rail.joint_id)
        monkeypatch.setattr(BindDialog, "exec", _cancel_bind)

        window.select_joint(rail.joint_id)
        dialog = window.open_joint_parent_dialog()

        # Hanging the rail under its own head would close a loop, so the head
        # must not even appear as a choice.
        assert dialog is not None
        assert dialog.joint_combo.findText("rail / head") == -1


class TestValuesPanel:
    def test_opens_with_one_row_per_driving_joint(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # A model is bound to one joint, but the whole chain above it moves it,
        # so a rail carrying a head gives the model two sliders.
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        rail = window.joints.add(trajectory_joint(name="slide"))
        head = window.joints.add(axis_joint(name="tilt"), parent_joint_id=rail.joint_id)
        window.apply_binding(entry.model_id, head.joint_id)

        panel = window.open_values_panel(entry.model_id)

        assert panel is not None
        assert panel.joint_count == 2

    def test_a_model_nothing_drives_opens_nothing(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        # A joint exists, but this model is not bound to it, so nothing moves it.
        window.joints.add(axis_joint(name="tilt"))

        assert window.open_values_panel(entry.model_id) is None

    def test_reopening_the_same_model_reuses_the_panel(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = window.joints.add(axis_joint(name="tilt")).joint_id
        window.apply_binding(entry.model_id, joint_id)

        first = window.open_values_panel(entry.model_id)
        second = window.open_values_panel(entry.model_id)

        assert first is not None
        assert first is second

    def test_double_click_opens_the_panel_for_that_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = window.joints.add(axis_joint(name="tilt")).joint_id
        window.apply_binding(entry.model_id, joint_id)

        window.model_tree.model_double_clicked.emit(entry.model_id)

        panels = window.findChildren(ModelValuesPanel)
        assert len(panels) == 1
        assert panels[0].model_id == entry.model_id

    def test_editing_a_row_updates_the_registry_and_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = window.joints.add(axis_joint(name="tilt")).joint_id
        window.apply_binding(entry.model_id, joint_id)
        panel = window.open_values_panel(entry.model_id)
        assert panel is not None
        row = panel.row_for(joint_id)
        assert row is not None

        row.value_spin.setValue(30.0)

        updated = window.joints.get(joint_id)
        assert updated is not None
        assert updated.value == pytest.approx(math.radians(30.0))
        assert viewport.joint_values[joint_id] == pytest.approx(math.radians(30.0))


def _add_axis_joint(window: MainWindow, model_id: str, name: str = "tilt") -> str:
    """Add an axis joint through the real dialog flow, bind `model_id` to it,
    and return its id.

    Goes through `open_add_joint_dialog`/`apply_binding` rather than poking the
    registries, so the window does its own refreshing exactly as it does in the
    application. Binding is part of the setup now: a joint no longer belongs to
    a model, so the model has to be attached for it to be driven at all.
    """
    dialog = window.open_add_joint_dialog(ModelJointKind.AXIS)
    dialog.name_edit.setText(name)
    dialog.accept()
    joint_id = window.joints.entries[-1].joint_id
    window.apply_binding(model_id, joint_id)
    window.select_model(model_id)
    return joint_id


class TestPropertiesDock:
    def test_the_dock_is_on_the_right(self, window: MainWindow) -> None:
        assert (
            window.dockWidgetArea(window.properties_dock) == Qt.DockWidgetArea.RightDockWidgetArea
        )

    def test_it_is_empty_with_nothing_selected(self, window: MainWindow) -> None:
        assert window.properties_panel.model_id is None

    def test_selecting_a_model_shows_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")

        window.select_model(entry.model_id)

        assert window.properties_panel.model_id == entry.model_id
        assert window.properties_panel.name_edit.text() == "gantry"

    def test_removing_the_last_model_empties_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")

        window.remove_selected_model()

        assert window.properties_panel.model_id is None

    def test_selecting_a_joint_row_shows_the_joint_not_the_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Clicking a trajectory asks about the trajectory. The tree also selects
        # the owning model (so Placement/Rename keep a target), so the panel has
        # to prefer the joint rather than trust the model selection alone.
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id)

        window.select_joint(joint_id)

        assert window.properties_panel.joint_id == joint_id
        assert window.properties_panel.model_id is None

    def test_the_joint_view_names_what_carries_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # A joint is carried by another joint or by the scene — never by a
        # model, which is what this asserted before the inversion.
        window, _ = window_with_viewport
        rail = window.joints.add(axis_joint(name="rail"))
        head = window.joints.add(axis_joint(name="head"), parent_joint_id=rail.joint_id)
        # Adding already selected it, so clear first or `select_joint` no-ops.
        window.joints.select(None)

        window.select_joint(head.joint_id)

        assert window.properties_panel.joint_parent_label.text() == "rail"

    def test_a_top_level_joint_reports_nothing_carrying_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        rail = window.joints.add(axis_joint(name="rail"))
        window.joints.select(None)

        window.select_joint(rail.joint_id)

        assert window.properties_panel.joint_parent_label.text() == "—"

    def test_the_joint_view_shows_the_joints_own_geometry(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        dialog = window.open_add_joint_dialog(ModelJointKind.TRAJECTORY)
        assert dialog is not None
        dialog.name_edit.setText("slide")
        dialog.target_spins[0].setValue(400.0)
        dialog.accept()
        joint_id = window.joints.entries[-1].joint_id

        window.select_joint(joint_id)

        assert window.properties_panel.joint_target_x_spin.value() == pytest.approx(400.0)

    def test_selecting_a_model_again_leaves_the_joint_view(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id)
        window.select_joint(joint_id)

        window.select_model(entry.model_id)

        assert window.properties_panel.joint_id is None
        assert window.properties_panel.model_id == entry.model_id

    def test_editing_the_joint_in_the_panel_redraws_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id)
        window.select_joint(joint_id)

        window.properties_panel.joint_origin_x_spin.setValue(250.0)

        updated = window.joints.get(joint_id)
        assert updated is not None
        assert updated.joint.origin[0] == pytest.approx(0.25)
        assert viewport.joints_updated[joint_id].origin[0] == pytest.approx(0.25)

    def test_renaming_the_joint_in_the_panel_reaches_the_tree(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id, name="tilt")
        window.select_joint(joint_id)

        window.properties_panel.joint_name_edit.setText("swivel")
        window.properties_panel.joint_name_edit.editingFinished.emit()

        assert window.joints.names == ("swivel",)

    def test_driving_the_value_from_the_joint_view_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id)
        window.select_joint(joint_id)
        row = window.properties_panel.row_for(joint_id)
        assert row is not None

        row.value_spin.setValue(30.0)

        assert viewport.joint_values[joint_id] == pytest.approx(math.radians(30.0))

    def test_a_joint_gets_a_row_in_the_panel(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id)

        assert window.properties_panel.row_for(joint_id) is not None

    def test_driving_a_row_moves_the_model_in_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The whole point of the panel: a slider here reaches the viewport.
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id)
        row = window.properties_panel.row_for(joint_id)
        assert row is not None

        row.value_spin.setValue(30.0)

        assert viewport.joint_values[joint_id] == pytest.approx(math.radians(30.0))

    def test_editing_the_placement_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.properties_panel.x_spin.setValue(500.0)

        assert viewport.placements[entry.model_id].xyz[0] == pytest.approx(0.5)

    def test_editing_the_name_renames_the_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.properties_panel.name_edit.setText("conveyor")
        window.properties_panel.name_edit.editingFinished.emit()

        assert window.models.names == ("conveyor",)

    def test_a_taken_name_is_corrected_in_the_field(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "gantry")
        second = _load(window, "conveyor")
        window.select_model(second.model_id)

        window.properties_panel.name_edit.setText("gantry")
        window.properties_panel.name_edit.editingFinished.emit()

        # The registry uniquified it; the field must not keep claiming "gantry".
        assert window.properties_panel.name_edit.text() == "gantry (2)"
        assert window.models.names == ("gantry", "gantry (2)")

    def test_the_placement_dialog_and_the_panel_stay_in_step(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        dialog = window.open_placement_dialog()
        assert dialog is not None

        dialog.x_spin.setValue(250.0)

        assert window.properties_panel.x_spin.value() == pytest.approx(250.0)
        dialog.close()

    def test_the_panel_and_the_values_window_stay_in_step(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        joint_id = _add_axis_joint(window, entry.model_id)
        values = window.open_values_panel(entry.model_id)
        assert values is not None
        panel_row = window.properties_panel.row_for(joint_id)
        assert panel_row is not None

        panel_row.value_spin.setValue(30.0)

        window_row = values.row_for(joint_id)
        assert window_row is not None
        assert window_row.value_spin.value() == pytest.approx(30.0)


class TestHidingModels:
    def test_a_model_starts_visible(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        assert _load(window, "gantry").is_visible is True

    def test_hiding_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.visibility_toggled.emit(False)

        assert viewport.model_visibility[entry.model_id] is False

    def test_hiding_is_remembered_in_the_registry(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.visibility_toggled.emit(False)

        stored = window.models.get(entry.model_id)
        assert stored is not None
        assert stored.is_visible is False

    def test_showing_it_again_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        window.model_tree.visibility_toggled.emit(False)

        window.model_tree.visibility_toggled.emit(True)

        assert viewport.model_visibility[entry.model_id] is True

    def test_the_status_bar_names_what_was_hidden(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.visibility_toggled.emit(False)

        assert "gantry" in window.statusBar().currentMessage()

    def test_only_the_selected_model_is_hidden(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        first = _load(window, "gantry")
        second = _load(window, "head")
        window.select_model(second.model_id)

        window.model_tree.visibility_toggled.emit(False)

        assert first.model_id not in viewport.model_visibility

    def test_hiding_with_nothing_selected_is_harmless(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.model_tree.visibility_toggled.emit(False)

        assert viewport.model_visibility == {}

    def test_a_hidden_row_is_dimmed(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The only cue that a model is hidden, so it is worth pinning.
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        before = window.model_tree.topLevelItem(0).foreground(COLUMN_NAME).color()

        window.model_tree.visibility_toggled.emit(False)

        after = window.model_tree.topLevelItem(0).foreground(COLUMN_NAME).color()
        assert after != before

    def test_a_hidden_row_says_so_in_its_tooltip(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.visibility_toggled.emit(False)

        assert "Hidden" in window.model_tree.topLevelItem(0).toolTip(COLUMN_NAME)

    def test_a_hidden_row_is_still_selectable(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Otherwise there would be no way to unhide it.
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        window.model_tree.visibility_toggled.emit(False)

        item = window.model_tree.topLevelItem(0)

        assert item.flags() & Qt.ItemFlag.ItemIsSelectable

    def test_the_menu_shows_the_current_state(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        menu = window.model_tree.build_context_menu(TreeTarget.MODEL, RowState(is_visible=False))

        visible = [a for a in menu.actions() if a.text().replace("&", "") == "Visible"]

        assert visible[0].isChecked() is False


class TestHidingTheCoordinateCross:
    def test_the_cross_starts_shown(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        assert _load(window, "gantry").show_axes is True

    def test_hiding_a_models_cross_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.axes_visibility_toggled.emit(False)

        assert viewport.axes_visibility[entry.model_id] is False

    def test_hiding_a_models_cross_is_remembered(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.axes_visibility_toggled.emit(False)

        stored = window.models.get(entry.model_id)
        assert stored is not None
        assert stored.show_axes is False

    def test_hiding_a_models_cross_leaves_the_model_visible(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.axes_visibility_toggled.emit(False)

        assert entry.model_id not in viewport.model_visibility

    def test_hiding_a_joints_cross_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = window.joints.add(axis_joint(name="turn"))
        window.select_joint(entry.joint_id)

        window.model_tree.axes_visibility_toggled.emit(False)

        assert viewport.axes_visibility[entry.joint_id] is False

    def test_hiding_a_joints_cross_is_remembered(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.joints.add(axis_joint(name="turn"))
        window.select_joint(entry.joint_id)

        window.model_tree.axes_visibility_toggled.emit(False)

        stored = window.joints.get(entry.joint_id)
        assert stored is not None
        assert stored.show_axes is False

    def test_a_selected_joint_wins_over_a_selected_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # One menu entry serves both rows, so the toggle has to land on the row
        # that was actually clicked — and selecting a joint row is what clears
        # the model selection.
        window, viewport = window_with_viewport
        model = _load(window, "gantry")
        joint = window.joints.add(axis_joint(name="turn"))
        window.select_joint(joint.joint_id)

        window.model_tree.axes_visibility_toggled.emit(False)

        assert model.model_id not in viewport.axes_visibility

    def test_toggling_with_nothing_selected_is_harmless(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.model_tree.axes_visibility_toggled.emit(False)

        assert viewport.axes_visibility == {}

    def test_the_menu_shows_the_current_state(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        menu = window.model_tree.build_context_menu(TreeTarget.JOINT, RowState(show_axes=False))

        crosses = [
            a for a in menu.actions() if a.text().replace("&", "") == "Show Coordinate Cross"
        ]

        assert crosses[0].isChecked() is False


class TestColouringItems:
    def test_a_models_colour_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.apply_color(entry.model_id, (1.0, 0.0, 0.0, 1.0))

        assert viewport.model_colors[entry.model_id] == (1.0, 0.0, 0.0, 1.0)

    def test_a_models_colour_is_remembered(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")

        window.apply_color(entry.model_id, (1.0, 0.0, 0.0, 1.0))

        stored = window.models.get(entry.model_id)
        assert stored is not None
        assert stored.color == (1.0, 0.0, 0.0, 1.0)

    def test_a_joints_colour_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = window.joints.add(axis_joint(name="turn"))

        window.apply_color(entry.joint_id, (0.0, 1.0, 0.0, 1.0))

        assert viewport.joint_colors[entry.joint_id] == (0.0, 1.0, 0.0, 1.0)

    def test_a_joints_colour_is_remembered(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.joints.add(axis_joint(name="turn"))

        window.apply_color(entry.joint_id, (0.0, 1.0, 0.0, 1.0))

        stored = window.joints.get(entry.joint_id)
        assert stored is not None
        assert stored.color == (0.0, 1.0, 0.0, 1.0)

    def test_one_id_never_lands_on_both_registries(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # One entry point serves both kinds, so it must pick exactly one.
        window, viewport = window_with_viewport
        model = _load(window, "gantry")
        joint = window.joints.add(axis_joint(name="turn"))

        window.apply_color(joint.joint_id, (0.0, 1.0, 0.0, 1.0))

        assert model.model_id not in viewport.model_colors

    def test_an_unknown_id_is_harmless(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.apply_color("nonsense", (1.0, 0.0, 0.0, 1.0))

        assert viewport.model_colors == {}
        assert viewport.joint_colors == {}

    def test_resetting_clears_the_override_in_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # `None` is what brings the CAD colours back, so it has to travel.
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        window.apply_color(entry.model_id, (1.0, 0.0, 0.0, 1.0))

        window.model_tree.color_reset_requested.emit()

        assert viewport.model_colors[entry.model_id] is None

    def test_resetting_clears_the_override_in_the_registry(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        window.apply_color(entry.model_id, (1.0, 0.0, 0.0, 1.0))

        window.model_tree.color_reset_requested.emit()

        stored = window.models.get(entry.model_id)
        assert stored is not None
        assert stored.color is None

    def test_a_selected_joint_wins_over_a_selected_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        model = _load(window, "gantry")
        joint = window.joints.add(axis_joint(name="turn"))
        window.apply_color(joint.joint_id, (0.0, 1.0, 0.0, 1.0))
        window.select_joint(joint.joint_id)

        window.model_tree.color_reset_requested.emit()

        assert viewport.joint_colors[joint.joint_id] is None
        assert model.model_id not in viewport.model_colors

    def test_resetting_with_nothing_selected_is_harmless(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.model_tree.color_reset_requested.emit()

        assert viewport.model_colors == {}

    def test_the_dialog_does_nothing_when_cancelled(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Cancel gives back an invalid QColor, which must not be applied.
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        monkeypatch.setattr("pssim.ui.main_window.QColorDialog.getColor", lambda *a, **k: QColor())

        assert window.open_color_dialog() is None
        assert viewport.model_colors == {}

    def test_the_dialog_applies_what_was_picked(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        monkeypatch.setattr(
            "pssim.ui.main_window.QColorDialog.getColor",
            lambda *a, **k: QColor(255, 0, 0),
        )

        window.open_color_dialog()

        assert viewport.model_colors[entry.model_id] == pytest.approx((1.0, 0.0, 0.0, 1.0))

    def test_the_picked_colour_is_forced_opaque(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A half-transparent part reads as a rendering fault, and the outlines
        # are drawn assuming solid geometry behind them.
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        monkeypatch.setattr(
            "pssim.ui.main_window.QColorDialog.getColor",
            lambda *a, **k: QColor(255, 0, 0, 10),
        )

        window.open_color_dialog()

        assert viewport.model_colors[entry.model_id][3] == pytest.approx(1.0)

    def test_reset_is_offered_only_when_there_is_an_override(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        plain = _submenu_labels(
            window.model_tree.build_context_menu(TreeTarget.MODEL, RowState()), "Colour"
        )
        coloured = _submenu_labels(
            window.model_tree.build_context_menu(TreeTarget.MODEL, RowState(has_color=True)),
            "Colour",
        )

        assert "Reset Model Colour" not in plain
        assert "Reset Model Colour" in coloured

    def test_the_highlight_reset_is_separate_from_the_model_one(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Two overrides, two resets: clearing one must not offer to clear both.
        window, _ = window_with_viewport

        labels = _submenu_labels(
            window.model_tree.build_context_menu(
                TreeTarget.MODEL, RowState(has_highlight_color=True)
            ),
            "Colour",
        )

        assert "Reset Highlight Colour" in labels
        assert "Reset Model Colour" not in labels

    def test_the_submenu_offers_both_colours(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        labels = _submenu_labels(
            window.model_tree.build_context_menu(TreeTarget.MODEL, RowState()), "Colour"
        )

        assert labels == ["Model…", "Highlight…"]


class TestJointNames:
    def test_a_name_reaches_the_scene_when_hidden(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = window.joints.add(axis_joint(name="turn"))
        window.select_joint(entry.joint_id)

        window.model_tree.name_visibility_toggled.emit(False)

        assert viewport.joint_names[entry.joint_id] is False

    def test_hiding_a_name_is_remembered(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.joints.add(axis_joint(name="turn"))
        window.select_joint(entry.joint_id)

        window.model_tree.name_visibility_toggled.emit(False)

        stored = window.joints.get(entry.joint_id)
        assert stored is not None
        assert stored.show_name is False

    def test_toggling_with_no_joint_selected_is_harmless(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.model_tree.name_visibility_toggled.emit(False)

        assert viewport.joint_names == {}

    def test_names_are_shown_by_default(self, window: MainWindow) -> None:
        assert window.joint_names_action.isChecked() is True

    def test_the_global_switch_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.joint_names_action.setChecked(False)

        assert viewport.names_visible is False

    def test_the_global_switch_says_what_it_did(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        window.joint_names_action.setChecked(False)

        assert "names" in window.statusBar().currentMessage().lower()

    def test_the_global_switch_leaves_the_per_joint_flag_alone(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Turning them all off and on again must not resurrect a name the user
        # had silenced on its own.
        window, _ = window_with_viewport
        entry = window.joints.add(axis_joint(name="turn"))
        window.select_joint(entry.joint_id)
        window.model_tree.name_visibility_toggled.emit(False)

        window.joint_names_action.setChecked(False)
        window.joint_names_action.setChecked(True)

        stored = window.joints.get(entry.joint_id)
        assert stored is not None
        assert stored.show_name is False


class TestHighlightColour:
    def test_it_starts_with_no_override(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        assert _load(window, "gantry").highlight_color is None

    def test_it_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")

        window.apply_highlight_color(entry.model_id, (0.0, 1.0, 1.0, 1.0))

        assert viewport.highlight_colors[entry.model_id] == (0.0, 1.0, 1.0, 1.0)

    def test_it_is_remembered(self, window_with_viewport: tuple[MainWindow, _StubViewport]) -> None:
        window, _ = window_with_viewport
        entry = _load(window, "gantry")

        window.apply_highlight_color(entry.model_id, (0.0, 1.0, 1.0, 1.0))

        stored = window.models.get(entry.model_id)
        assert stored is not None
        assert stored.highlight_color == (0.0, 1.0, 1.0, 1.0)

    def test_it_is_separate_from_the_body_colour(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Two colours per model: the part and the marker drawn around it.
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")

        window.apply_highlight_color(entry.model_id, (0.0, 1.0, 1.0, 1.0))

        assert entry.model_id not in viewport.model_colors

    def test_resetting_clears_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        window.apply_highlight_color(entry.model_id, (0.0, 1.0, 1.0, 1.0))

        window.model_tree.highlight_color_reset_requested.emit()

        assert viewport.highlight_colors[entry.model_id] is None

    def test_resetting_with_nothing_selected_is_harmless(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.model_tree.highlight_color_reset_requested.emit()

        assert viewport.highlight_colors == {}

    def test_the_dialog_applies_what_was_picked(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        monkeypatch.setattr(
            "pssim.ui.main_window.QColorDialog.getColor", lambda *a, **k: QColor(0, 255, 0)
        )

        window.open_highlight_color_dialog()

        assert viewport.highlight_colors[entry.model_id] == pytest.approx((0.0, 1.0, 0.0, 1.0))

    def test_cancelling_the_dialog_changes_nothing(
        self,
        window_with_viewport: tuple[MainWindow, _StubViewport],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)
        monkeypatch.setattr("pssim.ui.main_window.QColorDialog.getColor", lambda *a, **k: QColor())

        assert window.open_highlight_color_dialog() is None
        assert viewport.highlight_colors == {}


class TestCollisionButton:
    def test_the_menu_offers_it(self, window: MainWindow) -> None:
        assert "Check Collisions" in menu_items(window, "Scene")

    def test_it_asks_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.check_collisions_action.trigger()

        assert viewport.collision_checks == 1

    def test_it_runs_only_when_asked(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The whole point of the change: no timer, no per-frame work.
        window, viewport = window_with_viewport
        _load(window, "gantry")
        _load(window, "head")

        assert viewport.collision_checks == 0

    def test_a_clear_scene_says_so(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # With a button, "nothing found" is a result; silence would read as
        # "the button did nothing".
        window, viewport = window_with_viewport
        viewport.collision_result = frozenset()

        window.check_collisions()

        assert window.statusBar().currentMessage() == "No collisions"

    def test_a_collision_is_named(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        first = _load(window, "gantry")
        second = _load(window, "head")
        viewport.collision_result = frozenset({(first.model_id, second.model_id)})

        window.check_collisions()

        message = window.statusBar().currentMessage()
        assert "gantry" in message
        assert "head" in message

    def test_it_returns_what_it_found(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        viewport.collision_result = frozenset({("a", "b")})

        assert window.check_collisions() == frozenset({("a", "b")})


class TestSizes:
    """One size for the item crosses, one for all 3D text, and the origin cross
    on its own.

    The reported bug was that the text setting did nothing: `make_joint_label`
    treated its argument as a span to scale by 0.09 and clamped it at 50 mm, so
    10, 25 and 50 mm all drew 4.5 mm of text.
    """

    def test_the_menu_offers_it(self, window: MainWindow) -> None:
        # Under `Crosses and Labels`, so the path says what the sizes are for.
        assert "Sizes…" in submenu_items(window, "Scene", "Crosses and Labels")

    def test_they_start_at_the_defaults(self, window: MainWindow) -> None:
        assert window.cross_size_m == pytest.approx(DEFAULT_CROSS_SIZE_M)
        assert window.text_size_m == pytest.approx(DEFAULT_TEXT_SIZE_M)
        assert window.origin_cross_size_m == pytest.approx(DEFAULT_ORIGIN_CROSS_SIZE_M)

    def test_applying_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.apply_sizes(0.5, 0.1, 0.8)

        assert viewport.cross_size_m == pytest.approx(0.5)
        assert viewport.text_size_m == pytest.approx(0.1)
        assert viewport.origin_cross_size_m == pytest.approx(0.8)

    def test_applying_is_remembered(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        window.apply_sizes(0.5, 0.1, 0.8)

        assert window.sizes == Sizes(
            cross_size_m=pytest.approx(0.5),
            text_size_m=pytest.approx(0.1),
            origin_cross_size_m=pytest.approx(0.8),
        )

    def test_the_origin_cross_is_independent_of_the_item_crosses(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The whole point of giving it its own setting.
        window, viewport = window_with_viewport

        window.apply_sizes(DEFAULT_CROSS_SIZE_M, DEFAULT_TEXT_SIZE_M, 1.5)

        assert viewport.cross_size_m == pytest.approx(DEFAULT_CROSS_SIZE_M)
        assert viewport.origin_cross_size_m == pytest.approx(1.5)

    def test_the_dialog_shows_all_three_in_millimetres(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        window.apply_sizes(0.5, 0.075, 0.9)

        dialog = SizesDialog(window.sizes)
        try:
            assert dialog.cross_spin.value() == pytest.approx(500.0)
            assert dialog.text_spin.value() == pytest.approx(75.0)
            assert dialog.origin_spin.value() == pytest.approx(900.0)
        finally:
            dialog.close()

    def test_the_dialog_reports_metres(self) -> None:
        dialog = SizesDialog(Sizes(0.2, 0.05, 0.2))
        try:
            dialog.cross_spin.setValue(1000.0)
            dialog.text_spin.setValue(100.0)
            dialog.origin_spin.setValue(2000.0)

            assert dialog.cross_size_m == pytest.approx(1.0)
            assert dialog.text_size_m == pytest.approx(0.1)
            assert dialog.origin_cross_size_m == pytest.approx(2.0)
        finally:
            dialog.close()

    def test_the_dialog_previews_as_it_moves(self) -> None:
        dialog = SizesDialog(Sizes(0.2, 0.05, 0.2))
        received: list[tuple[float, float, float]] = []
        dialog.sizes_changed.connect(
            lambda cross, text, origin: received.append((cross, text, origin))
        )
        try:
            dialog.text_spin.setValue(100.0)

            assert received[-1][1] == pytest.approx(0.1)
        finally:
            dialog.close()

    def test_cancelling_puts_every_previous_size_back(self) -> None:
        # The preview has already changed the scene, so cancel has to undo it.
        dialog = SizesDialog(Sizes(0.2, 0.05, 0.3))
        received: list[tuple[float, float, float]] = []
        dialog.sizes_changed.connect(
            lambda cross, text, origin: received.append((cross, text, origin))
        )
        dialog.cross_spin.setValue(900.0)
        dialog.text_spin.setValue(300.0)
        dialog.origin_spin.setValue(1200.0)

        dialog.reject()

        assert received[-1] == pytest.approx((0.2, 0.05, 0.3))


class TestOriginCrossSwitch:
    def test_the_menu_offers_it(self, window: MainWindow) -> None:
        assert "Origin Cross" in submenu_items(window, "Scene", "Crosses and Labels")

    def test_it_starts_shown(self, window: MainWindow) -> None:
        assert window.origin_cross_action.isChecked() is True

    def test_hiding_it_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.origin_cross_action.setChecked(False)

        assert viewport.origin_cross_visible is False

    def test_hiding_it_says_so(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        window.origin_cross_action.setChecked(False)

        assert "origin cross" in window.statusBar().currentMessage().lower()

    def test_it_leaves_the_item_crosses_alone(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # A selected model's cross follows its own `show_axes`; this switch is
        # only about the one at the scene origin.
        window, viewport = window_with_viewport
        entry = _load(window, "gantry")
        window.select_model(entry.model_id)

        window.origin_cross_action.setChecked(False)

        assert entry.model_id not in viewport.axes_visibility


class TestSensorMounting:
    """A sensor's point and direction are in its mount's frame, so mounting is
    what makes one on a carriage ride the carriage."""

    def test_a_new_sensor_starts_unmounted(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        assert window.sensors.add(beam_sensor(name="gate")).mounted_on is None

    def test_mounting_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        model = _load(window, "gantry")
        entry = window.sensors.add(beam_sensor(name="gate"))

        window.apply_sensor_mount(entry.sensor_id, model.model_id)

        assert viewport.sensor_mounts[entry.sensor_id] == model.model_id

    def test_mounting_is_remembered(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window, "gantry")
        entry = window.sensors.add(beam_sensor(name="gate"))

        window.apply_sensor_mount(entry.sensor_id, model.model_id)

        stored = window.sensors.get(entry.sensor_id)
        assert stored is not None
        assert stored.mounted_on == model.model_id

    def test_an_encoder_can_be_mounted_on_a_joint(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The one kind that wants a joint rather than a model.
        window, viewport = window_with_viewport
        joint = window.joints.add(axis_joint(name="turn"))
        entry = window.sensors.add(Sensor(name="enc", kind=SensorKind.ENCODER_ABS, variable="enc"))

        window.apply_sensor_mount(entry.sensor_id, joint.joint_id)

        assert viewport.sensor_mounts[entry.sensor_id] == joint.joint_id

    def test_taking_it_off_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        model = _load(window, "gantry")
        entry = window.sensors.add(beam_sensor(name="gate"))
        window.apply_sensor_mount(entry.sensor_id, model.model_id)

        window.apply_sensor_mount(entry.sensor_id, None)

        assert viewport.sensor_mounts[entry.sensor_id] is None

    def test_the_chooser_offers_models_and_joints(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window, "gantry")
        joint = window.joints.add(axis_joint(name="turn"))

        offered = [item_id for item_id, _label in window._mount_choices()]

        assert model.model_id in offered
        assert joint.joint_id in offered

    def test_the_menu_offers_mounting(self, window: MainWindow) -> None:
        assert "Mount On…" in menu_items(window, "Sensors")


class TestSensorReadings:
    def test_a_reading_is_pulled_into_the_registry(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"))
        viewport.sensor_readings[entry.sensor_id] = SensorReading(value=0.42)

        window.refresh_sensor_readings()

        stored = window.sensors.get(entry.sensor_id)
        assert stored is not None
        assert stored.reading.value == pytest.approx(0.42)

    def test_driving_a_joint_refreshes_the_readings(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # A moved joint can change every reading, and it is the only thing that
        # moves the scene until a PLC drives it.
        window, viewport = window_with_viewport
        joint = window.joints.add(axis_joint(name="turn"))
        entry = window.sensors.add(beam_sensor(name="gate"))
        viewport.sensor_readings[entry.sensor_id] = SensorReading(value=1.0)

        window.apply_joint_value(joint.joint_id, 0.5)

        stored = window.sensors.get(entry.sensor_id)
        assert stored is not None
        assert stored.reading.value == pytest.approx(1.0)

    def test_a_sensor_the_scene_knows_nothing_about_is_left_alone(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"))

        window.refresh_sensor_readings()

        stored = window.sensors.get(entry.sensor_id)
        assert stored is not None
        assert stored.reading.value == pytest.approx(0.0)

    def test_the_tree_shows_a_distance_in_millimetres(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.sensors.add(
            beam_sensor(name="tof", kind=SensorKind.TOF, direction=(1.0, 0.0, 0.0))
        )
        window.sensors.set_reading(entry.sensor_id, SensorReading(value=0.3))

        assert describe_reading(window.sensors.entries[0]) == "300.0 mm"

    def test_the_tree_shows_a_dash_for_nothing_in_range(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The number would be the range and would look like a measurement.
        window, _ = window_with_viewport
        entry = window.sensors.add(
            beam_sensor(name="tof", kind=SensorKind.TOF, direction=(1.0, 0.0, 0.0))
        )
        window.sensors.set_reading(entry.sensor_id, SensorReading(value=1.0, is_valid=False))

        assert describe_reading(window.sensors.entries[0]) == "—"

    def test_the_tree_shows_counts_for_an_encoder(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.sensors.add(Sensor(name="enc", kind=SensorKind.ENCODER_INC, variable="enc"))
        window.sensors.set_reading(entry.sensor_id, SensorReading(value=1234.0))

        assert describe_reading(window.sensors.entries[0]) == "1234"


class TestSensorInTheProperties:
    """A sensor is the third thing the properties dock can show, and the three
    are exclusive — the panel has one subject.
    """

    def test_selecting_a_sensor_shows_it(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)

        window.select_sensor(entry.sensor_id)

        assert window.properties_panel.sensor_id == entry.sensor_id

    def test_selecting_a_sensor_clears_the_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)

        window.select_sensor(entry.sensor_id)

        assert window.models.selected is None

    def test_selecting_a_sensor_takes_the_highlight_off(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Otherwise a wireframe box stays round a model nothing considers
        # selected any more (R6).
        window, viewport = window_with_viewport
        _load(window)
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)

        window.select_sensor(entry.sensor_id)

        assert viewport.highlighted is None

    def test_selecting_a_model_clears_the_sensor(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window)
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.select_model(model.model_id)

        assert window.sensors.selected is None
        assert window.properties_panel.sensor_id is None

    def test_selecting_a_joint_clears_the_sensor(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        joint = window.joints.add(axis_joint(name="tilt"))
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.select_joint(joint.joint_id)

        assert window.sensors.selected is None

    def test_the_panel_is_told_what_the_sensor_is_mounted_on(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window, "conveyor")
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.apply_sensor_mount(entry.sensor_id, model.model_id)

        window.select_sensor(entry.sensor_id)

        assert window.properties_panel.sensor_mount_combo.currentText() == "conveyor"


class TestEditingASensorInThePanel:
    def test_an_edit_reaches_the_registry(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.properties_panel.sensor_fields.origin_x_spin.setValue(250.0)

        updated = window.sensors.get(entry.sensor_id)
        assert updated is not None
        assert updated.sensor.origin[0] == pytest.approx(0.25)

    def test_an_edit_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.properties_panel.sensor_fields.range_spin.setValue(2500.0)

        assert viewport.sensors_updated[entry.sensor_id].range_m == pytest.approx(2.5)

    def test_an_edit_refreshes_the_tree(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.properties_panel.sensor_fields.name_edit.setText("barrier")
        window.properties_panel.sensor_fields.name_edit.editingFinished.emit()

        assert window.sensor_tree.topLevelItem(0).text(0) == "barrier"

    def test_an_edit_does_not_re_fill_the_fields(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The refresh an edit provokes must not move the caret in the box being
        # typed into — the same rule the joint panel already follows.
        window, _ = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.properties_panel.sensor_fields.origin_y_spin.setValue(400.0)
        window._refresh_properties()

        assert window.properties_panel.sensor_fields.origin_y_spin.value() == pytest.approx(400.0)

    def test_choosing_a_mount_in_the_panel_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        model = _load(window, "conveyor")
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.properties_panel.sensor_mount_combo.setCurrentIndex(1)

        assert viewport.sensor_mounts[entry.sensor_id] == model.model_id

    def test_a_kind_change_reaches_the_scene(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)

        window.properties_panel.sensor_fields.kind_combo.setCurrentIndex(
            kind_index(SensorKind.INDUCTIVE)
        )

        assert viewport.sensors_updated[entry.sensor_id].kind is SensorKind.INDUCTIVE


class TestReadingsReachThePanel:
    def test_a_new_reading_shows_in_the_panel(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        window.select_sensor(entry.sensor_id)
        viewport.sensor_readings[entry.sensor_id] = SensorReading(value=1.0)

        window.refresh_sensor_readings()

        assert window.properties_panel.sensor_reading_label.text() == "1"

    def test_moving_a_model_re_reads_the_sensors(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # A placement moves geometry, and a sensor reads geometry — R16 says the
        # readings follow anything that can move.
        window, viewport = window_with_viewport
        model = _load(window)
        entry = window.sensors.add(beam_sensor(name="gate"), select=False)
        viewport.sensor_readings[entry.sensor_id] = SensorReading(value=1.0)

        window.apply_model_placement(model.model_id, Transform(xyz=(0.5, 0.0, 0.0)))

        updated = window.sensors.get(entry.sensor_id)
        assert updated is not None
        assert updated.reading.value == pytest.approx(1.0)


class TestGeometryMenuEnablement:
    """Greyed out rather than left to fail: an entry that reports "select
    something first" after the click has told the user too late.
    """

    def test_the_joint_actions_start_disabled(self, window: MainWindow) -> None:
        assert window.edit_joint_action.isEnabled() is False
        assert window.joint_parent_action.isEnabled() is False
        assert window.remove_joint_action.isEnabled() is False

    def test_adding_is_always_available(self, window: MainWindow) -> None:
        # A first axis has to be addable with nothing selected at all.
        assert window.add_axis_action.isEnabled() is True
        assert window.add_trajectory_action.isEnabled() is True

    def test_selecting_a_joint_enables_them(self, window: MainWindow) -> None:
        entry = window.joints.add(axis_joint(name="tilt"), select=False)

        window.select_joint(entry.joint_id)

        assert window.edit_joint_action.isEnabled() is True
        assert window.remove_joint_action.isEnabled() is True

    def test_binding_needs_something_to_bind_to(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window)
        window.select_model(model.model_id)

        assert window.bind_action.isEnabled() is False

    def test_binding_is_offered_once_a_joint_exists(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window)
        joint = window.joints.add(axis_joint(name="tilt"), select=False)
        # Through the selection, the way a click would: adding to the registry
        # by hand skips the refresh that re-evaluates what is possible.
        window.select_joint(joint.joint_id)
        window.select_model(model.model_id)

        assert window.bind_action.isEnabled() is True

    def test_variables_needs_the_model_to_be_driven(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window)
        window.select_model(model.model_id)

        assert window.values_action.isEnabled() is False

    def test_variables_is_offered_once_it_is_bound(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        model = _load(window)
        joint = window.joints.add(axis_joint(name="tilt"), select=False)
        window.apply_binding(model.model_id, joint.joint_id)
        window.select_model(model.model_id)

        assert window.values_action.isEnabled() is True
