"""Tests for saving and loading projects from the window.

The interesting part is the load sequence: a project names several CAD files and
each needs its own background import, run one at a time. Here the import is
stubbed, so the queue can be stepped deterministically without touching
OpenCASCADE.

`QSettings` is pointed at a temporary ini file — a test must never write into the
real user settings.

Runs headless. Requires `uv sync --extra ui`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.cad.model import CadAssembly, CadNode  # noqa: E402
from pssim.config.project import (  # noqa: E402
    PROJECT_FORMAT_VERSION,
    PROJECT_SUFFIX,
    read_project,
)
from pssim.domain.machine import Transform  # noqa: E402
from pssim.domain.model_joints import Anchor, ModelJoint  # noqa: E402
from pssim.domain.placement import (  # noqa: E402
    IDENTITY_PLACEMENT,
    PlacementDisplay,
    to_transform,
)
from pssim.domain.sensors import Sensor, SensorKind, SensorReading  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.model_registry import ModelEntry  # noqa: E402
from pssim.ui.project_controller import spec_to_camera  # noqa: E402
from pssim.ui.recent_files import RecentProjects  # noqa: E402
from pssim.ui.settings import SettingsStore  # noqa: E402
from pssim.viz.orbit import OrbitCamera  # noqa: E402
from tests.factories import axis_joint, beam_sensor, trajectory_joint  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


class _StubViewport(QWidget):
    """Viewport without Panda3D, recording what the window asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.placements: dict[str, Transform] = {}
        self.added: list[str] = []
        self.cleared = 0
        # A real OrbitCamera, so the millimetre/degree conversion is exercised
        # in both directions rather than skipped for want of a camera.
        self.camera_state: object = OrbitCamera(
            target=(0.1, 0.2, 0.3), distance_m=1.5, azimuth_rad=-0.6, elevation_rad=0.4
        )
        self.restored_cameras: list[object] = []
        self.floor_visible = True
        self.floor_z_m = 0.0
        self.sensors_added: dict[str, Sensor] = {}
        self.sensor_mounts: dict[str, str | None] = {}
        self.sensor_readings: dict[str, SensorReading] = {}
        self.joints_added: dict[str, ModelJoint] = {}
        self.model_visibility: dict[str, bool] = {}
        self.axes_visibility: dict[str, bool] = {}
        self.model_colors: dict[str, object] = {}
        self.highlight_colors: dict[str, object] = {}
        self.cross_size_m = 0.2
        self.text_size_m = 0.05
        self.origin_cross_size_m = 0.2
        self.origin_cross_visible = True
        self.joint_colors: dict[str, object] = {}
        self.joint_names: dict[str, bool] = {}
        self.names_visible = True
        self.joint_parents: dict[str, str | None] = {}
        self.joint_values: dict[str, float] = {}
        self.bindings: dict[str, str | None] = {}
        self.anchors: dict[str, Anchor] = {}

    def add_model(self, model_id: str, assembly: object, cache_dir: Path) -> int:
        self.added.append(model_id)
        return 0

    def remove_model(self, model_id: str) -> None:
        self.placements.pop(model_id, None)

    def set_highlight(self, model_id: str | None) -> None: ...

    def fit_view(self, model_id: str | None = None) -> None: ...

    def set_placement(self, model_id: str, placement: Transform) -> None:
        self.placements[model_id] = placement

    def placement(self, model_id: str) -> Transform:
        return self.placements.get(model_id, IDENTITY_PLACEMENT)

    def set_camera_state(self, camera: object) -> None:
        self.restored_cameras.append(camera)

    def clear(self) -> None:
        self.cleared += 1
        self.placements.clear()

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

    def add_joint(
        self, joint_id: str, joint: ModelJoint, parent_joint_id: str | None = None
    ) -> None:
        self.joints_added[joint_id] = joint
        self.joint_parents[joint_id] = parent_joint_id

    def set_joint_parent(self, joint_id: str, parent_joint_id: str | None) -> None:
        self.joint_parents[joint_id] = parent_joint_id

    def set_joint_value(self, joint_id: str, value: float) -> None:
        self.joint_values[joint_id] = value

    def bind_model(self, model_id: str, joint_id: str | None) -> None:
        self.bindings[model_id] = joint_id

    def set_anchor(self, model_id: str, anchor: Anchor) -> None:
        self.anchors[model_id] = anchor

    def set_model_visible(self, model_id: str, is_visible: bool) -> None:
        self.model_visibility[model_id] = is_visible

    def set_axes_visible(self, item_id: str, show_axes: bool) -> None:
        self.axes_visibility[item_id] = show_axes

    def set_model_color(self, model_id: str, color: object) -> None:
        self.model_colors[model_id] = color

    def set_highlight_color(self, model_id: str, color: object) -> None:
        self.highlight_colors[model_id] = color

    def set_cross_size(self, size_m: float) -> None:
        self.cross_size_m = size_m

    def set_text_size(self, size_m: float) -> None:
        self.text_size_m = size_m

    def set_origin_cross_size(self, size_m: float) -> None:
        self.origin_cross_size_m = size_m

    def set_origin_cross_visible(self, visible: bool) -> None:
        self.origin_cross_visible = visible

    def set_joint_color(self, joint_id: str, color: object) -> None:
        self.joint_colors[joint_id] = color

    def set_joint_name_visible(self, joint_id: str, show_name: bool) -> None:
        self.joint_names[joint_id] = show_name

    def set_names_visible(self, show_names: bool) -> None:
        self.names_visible = show_names


def assembly(parts: int = 2, triangles: int = 24) -> CadAssembly:
    nodes = tuple(
        CadNode(path=f"part{index}", mesh=f"{index}.npz", triangle_count=triangles // parts)
        for index in range(parts)
    )
    return CadAssembly(nodes=nodes, roots=(nodes[0].path,))


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    """Recent-files storage in a throwaway ini file."""
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(qt_app: QApplication, settings: QSettings) -> Iterator[tuple[MainWindow, _StubViewport]]:
    viewport = _StubViewport()
    instance = MainWindow(
        viewport_factory=lambda: viewport,
        recent=RecentProjects(settings),
        settings=SettingsStore(settings),
    )
    yield instance, viewport
    instance.close()


def _silence_warning(*args: object, **kwargs: object) -> None:
    """Stand-in for `QMessageBox.warning`: a modal has nobody to close it here."""


def _record_imports(recorded: list[Path]) -> Callable[..., None]:
    """Stand-in for `MainWindow.load_file`.

    Without this, loading a project would start a real OpenCASCADE import on a
    dummy file; the failure raises a modal that no test can close, and the run
    hangs rather than fails.
    """

    def fake_load(_self: object, path: Path) -> None:
        recorded.append(path)

    return fake_load


class _Metadata:
    """What `StepImportThread.succeeded` carries: an assembly and nothing else."""

    def __init__(self, cad_assembly: CadAssembly) -> None:
        self.assembly = cad_assembly


def finish_import(window: MainWindow) -> None:
    """Let the import that is currently queued succeed, then step the queue.

    The loading tests stub `load_file`, so nothing would otherwise reach
    `on_import_succeeded` — which is where a project's saved name and placement are
    put back onto the freshly added model.
    """
    window.on_import_succeeded(_Metadata(assembly()), Path("cache"))
    window.on_import_finished()


def load(window: MainWindow, name: str, step_dir: Path) -> ModelEntry:
    """Add a model as if an import had finished, with a real file on disk."""
    step = step_dir / f"{name}.step"
    step.write_text("dummy", encoding="utf-8")
    return window.add_model(step, assembly(), Path("cache"))


class TestSaving:
    def test_save_writes_a_file(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        load(instance, "gantry", tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert written.is_file()

    def test_saved_project_lists_the_models(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        load(instance, "gantry", tmp_path)
        load(instance, "head", tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert [model.name for model in read_project(written).models] == ["gantry", "head"]

    def test_saved_project_keeps_placements(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        entry = load(instance, "gantry", tmp_path)
        instance.select_model(entry.model_id)
        instance.apply_placement(to_transform(PlacementDisplay(x_mm=250.0)))

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert read_project(written).models[0].placement.x_mm == pytest.approx(250.0)

    def test_saved_project_keeps_the_selection(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        load(instance, "gantry", tmp_path)
        second = load(instance, "head", tmp_path)
        instance.select_model(second.model_id)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert read_project(written).selected == "head"

    def test_saving_remembers_the_project_path(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        load(instance, "gantry", tmp_path)

        instance.save_project_to(tmp_path / "line.pssim")

        assert instance.project_path is not None
        assert instance.project_path.name == f"line{PROJECT_SUFFIX}"

    def test_saving_adds_to_recent(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        load(instance, "gantry", tmp_path)

        instance.save_project_to(tmp_path / "line.pssim")

        assert len(instance.recent_projects.paths) == 1

    def test_saved_camera_round_trips(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # The camera is stored in millimetres and degrees like everything else,
        # so a round trip has to survive two unit conversions.
        instance, _ = window
        load(instance, "gantry", tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        camera = read_project(written).camera
        assert camera is not None
        assert camera.distance_mm == pytest.approx(1500.0)
        assert camera.target_x_mm == pytest.approx(100.0)
        restored = spec_to_camera(camera)
        assert restored.distance_m == pytest.approx(1.5)
        assert restored.azimuth_rad == pytest.approx(-0.6)

    def test_saving_an_empty_scene_is_allowed(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = instance.save_project_to(tmp_path / "empty.pssim")

        assert written is not None
        assert read_project(written).models == ()


class TestLoading:
    @pytest.fixture(autouse=True)
    def _no_real_imports(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        """Every test in this class steps the queue instead of importing."""
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_imports(started))
        return started

    def _saved_project(self, instance: MainWindow, tmp_path: Path, count: int = 2) -> Path:
        for index in range(count):
            entry = load(instance, f"model{index}", tmp_path)
            instance.select_model(entry.model_id)
            instance.apply_placement(to_transform(PlacementDisplay(x_mm=100.0 * (index + 1))))
        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def _project_with_renamed_models(self, instance: MainWindow, tmp_path: Path) -> Path:
        """Two models from one file, both given names of their own, then saved."""
        for name in ("main frame", "carriage"):
            entry = load(instance, "fixture", tmp_path)
            instance.select_model(entry.model_id)
            renamed = instance.models.rename(entry.model_id, name)
            assert renamed is not None
        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def test_renamed_models_are_saved_under_their_new_names(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        project = self._project_with_renamed_models(instance, tmp_path)

        assert [model.name for model in read_project(project).models] == [
            "main frame",
            "carriage",
        ]

    def test_renamed_models_come_back_under_their_new_names(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # The bug: the tree showed the file stem plus a counter, not the saved name.
        instance, _ = window
        project = self._project_with_renamed_models(instance, tmp_path)

        instance.load_project(project)
        finish_import(instance)
        finish_import(instance)

        assert instance.models.names == ("main frame", "carriage")

    def test_a_renamed_selection_is_restored(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # A project records its selection by name (R13), so losing the names lost
        # the selection with them.
        instance, _ = window
        project = self._project_with_renamed_models(instance, tmp_path)

        instance.load_project(project)
        finish_import(instance)
        finish_import(instance)

        selected = instance.selected_model
        assert selected is not None
        assert selected.name == "carriage"

    def test_reloaded_names_stay_unique(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._project_with_renamed_models(instance, tmp_path)

        instance.load_project(project)
        finish_import(instance)
        finish_import(instance)

        assert len(set(instance.models.names)) == 2

    def test_reloaded_names_show_in_the_tree(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._project_with_renamed_models(instance, tmp_path)

        instance.load_project(project)
        finish_import(instance)
        finish_import(instance)

        item = instance.model_tree.topLevelItem(0)
        assert item is not None
        assert item.text(0) == "main frame"

    def test_saved_placement_still_survives_the_load(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # The name is restored next to the placement, so this guards the pair.
        instance, viewport = window
        project = self._saved_project(instance, tmp_path)

        instance.load_project(project)
        finish_import(instance)
        finish_import(instance)

        entries = instance.models.entries
        assert entries[0].placement.xyz[0] == pytest.approx(0.1)
        assert viewport.placements[entries[1].model_id].xyz[0] == pytest.approx(0.2)

    def test_names_from_a_project_of_one_model_survive(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        entry = load(instance, "fixture", tmp_path)
        instance.models.rename(entry.model_id, "gantry")
        written = instance.save_project_to(tmp_path / "one.pssim")
        assert written is not None

        instance.load_project(written)
        finish_import(instance)

        assert instance.models.names == ("gantry",)

    def test_loading_clears_the_previous_scene(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved_project(instance, tmp_path)

        instance.load_project(project)

        assert viewport.cleared >= 1

    def test_loading_queues_every_model(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved_project(instance, tmp_path)

        instance.load_project(project)

        # One import is running, the rest wait: the importer writes into a
        # shared cache, so two at once would race.
        assert instance.project_loader.remaining == 1
        assert instance.project_loader.current is not None

    def test_loading_starts_with_the_first_model(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved_project(instance, tmp_path)

        instance.load_project(project)

        current = instance.project_loader.current
        assert current is not None
        assert current.name == "model0"

    def test_pending_model_carries_its_placement(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved_project(instance, tmp_path)

        instance.load_project(project)

        current = instance.project_loader.current
        assert current is not None
        assert current.placement.xyz[0] == pytest.approx(0.1)

    def test_loading_remembers_the_project_path(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved_project(instance, tmp_path)

        instance.load_project(project)

        assert instance.project_path == project

    def test_loading_adds_to_recent(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved_project(instance, tmp_path)
        instance.recent_projects.clear()

        instance.load_project(project)

        assert instance.recent_projects.paths[0] == project.resolve()

    def test_project_with_no_models_finishes_immediately(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        empty = instance.save_project_to(tmp_path / "empty.pssim")
        assert empty is not None

        instance.load_project(empty)

        assert instance.project_loader.is_loading is False

    def test_missing_model_file_is_skipped_not_queued(
        self,
        window: tuple[MainWindow, _StubViewport],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A moved CAD file must not stall the load or raise one dialog per file.
        instance, _ = window
        project = self._saved_project(instance, tmp_path, count=2)
        (tmp_path / "model1.step").unlink()
        monkeypatch.setattr("pssim.ui.main_window.QMessageBox.warning", _silence_warning)

        instance.load_project(project)

        assert instance.project_loader.remaining == 0

    def test_queue_advances_to_the_next_model(
        self,
        window: tuple[MainWindow, _StubViewport],
        tmp_path: Path,
        _no_real_imports: list[Path],
    ) -> None:
        # What `on_import_finished` does for a project: start the next model
        # only once the previous import has released the shared cache.
        instance, _ = window
        project = self._saved_project(instance, tmp_path)
        instance.load_project(project)

        instance.on_import_finished()

        current = instance.project_loader.current
        assert current is not None
        assert current.name == "model1"

    def test_queue_finishes_after_the_last_model(
        self,
        window: tuple[MainWindow, _StubViewport],
        tmp_path: Path,
        _no_real_imports: list[Path],
    ) -> None:
        instance, _ = window
        project = self._saved_project(instance, tmp_path)
        instance.load_project(project)

        instance.on_import_finished()
        instance.on_import_finished()

        assert instance.project_loader.is_loading is False

    def test_camera_is_restored_when_the_load_ends(
        self,
        window: tuple[MainWindow, _StubViewport],
        tmp_path: Path,
        _no_real_imports: list[Path],
    ) -> None:
        instance, viewport = window
        project = self._saved_project(instance, tmp_path)
        instance.load_project(project)

        instance.on_import_finished()
        instance.on_import_finished()

        assert viewport.restored_cameras

    def test_every_model_is_started_exactly_once(
        self,
        window: tuple[MainWindow, _StubViewport],
        tmp_path: Path,
        _no_real_imports: list[Path],
    ) -> None:
        instance, _ = window
        project = self._saved_project(instance, tmp_path)
        _no_real_imports.clear()

        instance.load_project(project)
        instance.on_import_finished()
        instance.on_import_finished()

        assert len(_no_real_imports) == 2

    def test_unreadable_project_reports_and_forgets_it(
        self,
        window: tuple[MainWindow, _StubViewport],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        instance, _ = window
        broken = tmp_path / "broken.pssim"
        broken.write_text("not a project", encoding="utf-8")
        instance.recent_projects.add(broken)
        monkeypatch.setattr("pssim.ui.main_window.QMessageBox.warning", _silence_warning)

        assert instance.load_project(broken) is None
        assert broken.resolve() not in instance.recent_projects.paths


class TestFloorAndSensorPersistence:
    def test_floor_state_is_saved(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        viewport.floor_visible = False
        viewport.floor_z_m = -0.075

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        floor = read_project(written).floor
        assert floor.visible is False
        assert floor.z_mm == pytest.approx(-75.0)

    def test_sensors_are_saved(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        instance.sensors.add(beam_sensor(name="gate"))

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert [sensor.name for sensor in read_project(written).sensors] == ["gate"]

    def test_reloaded_project_restores_the_floor(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        viewport.floor_visible = False
        viewport.floor_z_m = -0.05
        written = instance.save_project_to(tmp_path / "empty.pssim")
        assert written is not None
        # Simulate a fresh scene before the reload, so the assertion below can
        # only pass if the load actually pushed the saved state back onto it.
        viewport.floor_visible = True
        viewport.floor_z_m = 0.0

        instance.load_project(written)

        assert viewport.floor_visible is False
        assert viewport.floor_z_m == pytest.approx(-0.05)
        assert instance.floor_visible_action.isChecked() is False

    def test_a_default_floor_still_reaches_the_viewport_on_reload(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # The tricky case: the saved floor equals the checkbox's already-checked
        # default, so `setChecked` alone would not fire `toggled` and the
        # viewport would never be told, even though nothing is actually wrong.
        instance, viewport = window
        written = instance.save_project_to(tmp_path / "empty.pssim")
        assert written is not None
        viewport.floor_visible = False

        instance.load_project(written)

        assert viewport.floor_visible is True

    def test_reloaded_project_restores_sensors(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        instance.sensors.add(
            beam_sensor(name="gate", origin=(1.0, 0.0, 0.0), target=(2.0, 0.0, 0.0))
        )
        written = instance.save_project_to(tmp_path / "empty.pssim")
        assert written is not None

        instance.load_project(written)

        assert instance.sensors.names == ("gate",)
        entry = next(iter(instance.sensors))
        assert entry.sensor.origin == pytest.approx((1.0, 0.0, 0.0))
        assert viewport.sensors_added[entry.sensor_id].name == "gate"

    def test_close_all_clears_sensors_before_a_reload_can_leak_them(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        empty = instance.save_project_to(tmp_path / "empty.pssim")
        assert empty is not None
        # Added only after saving: a stand-in for leftover state from before
        # the reload, not part of what the file says.
        instance.sensors.add(beam_sensor(name="stale"))

        instance.load_project(empty)

        assert instance.sensors.is_empty is True


class TestCloseAll:
    def test_close_all_empties_the_registry(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        load(instance, "gantry", tmp_path)

        instance.close_all_models()

        assert instance.models.is_empty

    def test_close_all_clears_the_viewport(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        load(instance, "gantry", tmp_path)

        instance.close_all_action.trigger()

        assert viewport.cleared == 1

    def test_close_all_disables_model_actions(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        load(instance, "gantry", tmp_path)

        instance.close_all_models()

        assert instance.placement_action.isEnabled() is False


class TestRecentMenu:
    def test_empty_menu_says_so(self, window: tuple[MainWindow, _StubViewport]) -> None:
        instance, _ = window

        instance.refresh_recent_menu()
        entries = instance.recent_menu.actions()

        assert len(entries) == 1
        assert entries[0].isEnabled() is False

    def test_saved_project_appears(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        instance.save_project_to(tmp_path / "line.pssim")

        instance.refresh_recent_menu()

        assert any("line" in action.text() for action in instance.recent_menu.actions())

    def test_menu_has_a_clear_entry(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        instance.save_project_to(tmp_path / "line.pssim")

        instance.refresh_recent_menu()

        assert any(action.text() == "Clear List" for action in instance.recent_menu.actions())

    def test_full_path_is_in_the_status_tip(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # The label is shortened, so the full path has to live somewhere.
        instance, _ = window
        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None

        instance.refresh_recent_menu()
        tips = [action.statusTip() for action in instance.recent_menu.actions()]

        assert str(written.resolve()) in tips

    def test_most_recent_comes_first(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        instance.save_project_to(tmp_path / "first.pssim")
        instance.save_project_to(tmp_path / "second.pssim")

        instance.refresh_recent_menu()

        assert "second" in instance.recent_menu.actions()[0].text()

    def test_recent_survives_a_new_window(
        self, qt_app: QApplication, settings: QSettings, tmp_path: Path
    ) -> None:
        # The point of persisting: the list is still there after a restart.
        first = MainWindow(
            viewport_factory=QWidget,
            recent=RecentProjects(settings),
            settings=SettingsStore(settings),
        )
        first.save_project_to(tmp_path / "line.pssim")
        first.close()

        second = MainWindow(
            viewport_factory=QWidget,
            recent=RecentProjects(settings),
            settings=SettingsStore(settings),
        )
        try:
            assert len(second.recent_projects.paths) == 1
        finally:
            second.close()


class TestSavingJoints:
    """What the file has to hold for a joint scene to come back at all."""

    def _scene(self, instance: MainWindow, tmp_path: Path) -> tuple[str, str, str]:
        """A rail carrying an axis, with a model bound to the rail. Returns ids."""
        rail = instance.joints.add(
            trajectory_joint(name="rail", origin=(1.0, 0.0, 0.0), target=(5.0, 0.0, 0.0)),
            select=False,
        )
        turn = instance.joints.add(axis_joint(name="turn"), select=False)
        instance.joints.set_parent(turn.joint_id, rail.joint_id)

        model = load(instance, "head", tmp_path)
        instance.models.set_anchor(model.model_id, Anchor(point=(0.05, 0.0, 0.0)))
        instance.apply_binding(model.model_id, rail.joint_id)
        instance.apply_joint_value(rail.joint_id, 2.0)

        return rail.joint_id, turn.joint_id, model.model_id

    def test_the_joints_are_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        self._scene(instance, tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert [joint.name for joint in read_project(written).joints] == ["rail", "turn"]

    def test_the_carrying_joint_is_written_by_name(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # Ids are per-session (R13), so a file that stored one would be useless.
        instance, _ = window
        self._scene(instance, tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert read_project(written).joints[1].parent == "rail"

    def test_the_binding_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        self._scene(instance, tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert read_project(written).models[0].bound_to == "rail"

    def test_the_anchor_is_written_in_millimetres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        self._scene(instance, tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert read_project(written).models[0].anchor.point_x_mm == pytest.approx(50.0)

    def test_the_live_value_is_written_in_millimetres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        self._scene(instance, tmp_path)

        written = instance.save_project_to(tmp_path / "line.pssim")

        assert written is not None
        assert read_project(written).joints[0].value == pytest.approx(2000.0)


class TestRestoringJoints:
    """The load side. Restore order matters: joints, then parents, then bindings.

    These cannot use the model-free shortcut the floor tests take — a binding
    needs a real model, so the import queue has to be stepped.
    """

    @pytest.fixture(autouse=True)
    def _no_real_imports(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_imports(started))
        return started

    def _saved(self, instance: MainWindow, tmp_path: Path) -> Path:
        rail = instance.joints.add(
            trajectory_joint(name="rail", origin=(1.0, 0.0, 0.0), target=(5.0, 0.0, 0.0)),
            select=False,
        )
        turn = instance.joints.add(axis_joint(name="turn"), select=False)
        instance.joints.set_parent(turn.joint_id, rail.joint_id)

        model = load(instance, "head", tmp_path)
        instance.models.set_anchor(model.model_id, Anchor(point=(0.05, 0.0, 0.0)))
        instance.apply_binding(model.model_id, rail.joint_id)
        instance.apply_joint_value(rail.joint_id, 2.0)
        instance.apply_joint_value(turn.joint_id, 0.5)

        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def _reload(self, instance: MainWindow, project: Path) -> None:
        instance.load_project(project)
        finish_import(instance)

    def test_the_joints_come_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert [entry.joint.name for entry in instance.joints] == ["rail", "turn"]

    def test_the_chain_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        entries = {entry.joint.name: entry for entry in instance.joints}
        assert entries["turn"].parent_joint_id == entries["rail"].joint_id

    def test_the_geometry_comes_back_in_metres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        rail = next(entry for entry in instance.joints if entry.joint.name == "rail")
        assert rail.joint.origin == pytest.approx((1.0, 0.0, 0.0))

    def test_the_binding_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        rail = next(entry for entry in instance.joints if entry.joint.name == "rail")
        assert [entry.bound_to_joint_id for entry in instance.models] == [rail.joint_id]

    def test_the_anchor_comes_back_in_metres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.models)[0].anchor.point == pytest.approx((0.05, 0.0, 0.0))

    def test_a_trajectory_value_comes_back_in_metres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        rail = next(entry for entry in instance.joints if entry.joint.name == "rail")
        assert rail.value == pytest.approx(2.0)

    def test_an_axis_value_comes_back_in_radians(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        turn = next(entry for entry in instance.joints if entry.joint.name == "turn")
        assert turn.value == pytest.approx(0.5)

    def test_the_scene_is_told_about_the_joints(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        restored = {entry.joint_id for entry in instance.joints}
        assert restored <= set(viewport.joints_added)

    def test_the_scene_is_told_about_the_binding(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        rail = next(entry for entry in instance.joints if entry.joint.name == "rail")
        model_id = list(instance.models)[0].model_id
        assert viewport.bindings[model_id] == rail.joint_id

    def test_a_reload_does_not_duplicate_the_joints(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # `close_all_models` clears the joint registry; loading over a scene
        # must not leave the previous one behind.
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)
        self._reload(instance, project)

        assert len(list(instance.joints)) == 2

    def test_a_parent_naming_nothing_is_dropped_not_raised(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # A hand-edited or half-copied file still opens; the rest of the scene
        # is worth more than the missing link.
        instance, _ = window
        project = tmp_path / "broken.pssim"
        project.write_text(
            json.dumps(
                {
                    "version": PROJECT_FORMAT_VERSION,
                    "joints": [
                        {
                            "name": "turn",
                            "kind": "axis",
                            "variable": "turn",
                            "target_z_mm": 1000.0,
                            "parent": "a joint that was never saved",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        instance.load_project(project)

        assert list(instance.joints)[0].parent_joint_id is None

    def test_a_binding_naming_nothing_is_dropped_not_raised(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        step = tmp_path / "head.step"
        step.write_text("dummy", encoding="utf-8")
        project = tmp_path / "broken.pssim"
        project.write_text(
            json.dumps(
                {
                    "version": PROJECT_FORMAT_VERSION,
                    "models": [{"name": "head", "file": "head.step", "bound_to": "gone"}],
                }
            ),
            encoding="utf-8",
        )

        instance.load_project(project)
        finish_import(instance)

        assert list(instance.models)[0].bound_to_joint_id is None


class TestSavingDisplayFlags:
    """Hiding survives a save. Uses the loading harness, since restoring a
    model's flags happens as its import finishes."""

    @pytest.fixture(autouse=True)
    def _no_real_imports(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_imports(started))
        return started

    def _saved(self, instance: MainWindow, tmp_path: Path) -> Path:
        model = load(instance, "housing", tmp_path)
        instance.models.set_visible(model.model_id, False)
        instance.models.set_axes_visible(model.model_id, False)

        joint = instance.joints.add(axis_joint(name="turn"), select=False)
        instance.joints.set_axes_visible(joint.joint_id, False)

        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def _reload(self, instance: MainWindow, project: Path) -> None:
        instance.load_project(project)
        finish_import(instance)

    def test_a_hidden_model_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        assert read_project(written).models[0].visible is False

    def test_a_hidden_cross_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        assert read_project(written).models[0].show_axes is False

    def test_a_joints_hidden_cross_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        assert read_project(written).joints[0].show_axes is False

    def test_the_model_comes_back_hidden(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.models)[0].is_visible is False

    def test_the_models_cross_comes_back_hidden(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.models)[0].show_axes is False

    def test_the_joints_cross_comes_back_hidden(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.joints)[0].show_axes is False

    def test_the_scene_is_told_the_model_is_hidden(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        model_id = list(instance.models)[0].model_id
        assert viewport.model_visibility[model_id] is False

    def test_the_scene_is_told_the_crosses_are_hidden(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        model_id = list(instance.models)[0].model_id
        joint_id = list(instance.joints)[0].joint_id
        assert viewport.axes_visibility[model_id] is False
        assert viewport.axes_visibility[joint_id] is False

    def test_a_visible_model_stays_visible(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # The default has to survive too — a saved scene must not come back
        # with everything hidden.
        instance, _ = window
        load(instance, "gantry", tmp_path)
        written = instance.save_project_to(tmp_path / "plain.pssim")
        assert written is not None

        self._reload(instance, written)

        assert list(instance.models)[0].is_visible is True


class TestSavingColorsAndNames:
    """A round-trip check caught the save side silently dropping a joint's
    colour and name flag while the model's survived. These pin both halves."""

    @pytest.fixture(autouse=True)
    def _no_real_imports(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_imports(started))
        return started

    def _saved(self, instance: MainWindow, tmp_path: Path) -> Path:
        joint = instance.joints.add(axis_joint(name="turn"), select=False)
        instance.apply_color(joint.joint_id, (1.0, 0.4, 0.0, 1.0))
        instance.joints.set_name_visible(joint.joint_id, False)
        instance.joint_names_action.setChecked(False)

        model = load(instance, "housing", tmp_path)
        instance.apply_color(model.model_id, (0.2, 0.6, 1.0, 1.0))

        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def _reload(self, instance: MainWindow, project: Path) -> None:
        instance.load_project(project)
        finish_import(instance)

    def test_a_joints_colour_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        color = read_project(written).joints[0].color
        assert color is not None
        assert color.to_color() == pytest.approx((1.0, 0.4, 0.0, 1.0))

    def test_a_hidden_joint_name_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        assert read_project(written).joints[0].show_name is False

    def test_a_models_colour_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        color = read_project(written).models[0].color
        assert color is not None
        assert color.to_color() == pytest.approx((0.2, 0.6, 1.0, 1.0))

    def test_the_scene_wide_switch_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        assert read_project(written).show_joint_names is False

    def test_a_joints_colour_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.joints)[0].color == pytest.approx((1.0, 0.4, 0.0, 1.0))

    def test_a_hidden_joint_name_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.joints)[0].show_name is False

    def test_a_models_colour_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.models)[0].color == pytest.approx((0.2, 0.6, 1.0, 1.0))

    def test_the_scene_wide_switch_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert instance.joint_names_action.isChecked() is False

    def test_the_scene_is_told_about_the_colours(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        joint_id = list(instance.joints)[0].joint_id
        model_id = list(instance.models)[0].model_id
        assert viewport.joint_colors[joint_id] == pytest.approx((1.0, 0.4, 0.0, 1.0))
        assert viewport.model_colors[model_id] == pytest.approx((0.2, 0.6, 1.0, 1.0))

    def test_a_model_with_no_override_stays_without_one(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # Absent must not come back as white, or a reload would wipe the CAD
        # colours of every model nobody had recoloured.
        instance, _ = window
        load(instance, "carriage", tmp_path)
        written = instance.save_project_to(tmp_path / "plain.pssim")
        assert written is not None

        self._reload(instance, written)

        assert list(instance.models)[0].color is None


class TestSavingHighlightAndSizes:
    @pytest.fixture(autouse=True)
    def _no_real_imports(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_imports(started))
        return started

    def _saved(self, instance: MainWindow, tmp_path: Path) -> Path:
        model = load(instance, "gantry", tmp_path)
        instance.apply_highlight_color(model.model_id, (0.0, 1.0, 1.0, 1.0))
        instance.apply_sizes(0.4, 0.12, 0.7)

        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def _reload(self, instance: MainWindow, project: Path) -> None:
        instance.load_project(project)
        finish_import(instance)

    def test_the_highlight_colour_is_written(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        color = read_project(written).models[0].highlight_color
        assert color is not None
        assert color.to_color() == pytest.approx((0.0, 1.0, 1.0, 1.0))

    def test_the_sizes_are_written_in_millimetres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window

        written = self._saved(instance, tmp_path)

        assert read_project(written).text_size_mm == pytest.approx(120.0)
        assert read_project(written).cross_size_mm == pytest.approx(400.0)
        assert read_project(written).origin_cross_size_mm == pytest.approx(700.0)

    def test_the_highlight_colour_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert list(instance.models)[0].highlight_color == pytest.approx((0.0, 1.0, 1.0, 1.0))

    def test_the_sizes_come_back_in_metres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert instance.text_size_m == pytest.approx(0.12)
        assert instance.cross_size_m == pytest.approx(0.4)
        assert instance.origin_cross_size_m == pytest.approx(0.7)

    def test_the_scene_is_told_both(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        model_id = list(instance.models)[0].model_id
        assert viewport.highlight_colors[model_id] == pytest.approx((0.0, 1.0, 1.0, 1.0))
        assert viewport.text_size_m == pytest.approx(0.12)
        assert viewport.cross_size_m == pytest.approx(0.4)
        assert viewport.origin_cross_size_m == pytest.approx(0.7)


class TestSavingAnAxis:
    """The axis definition changed shape, so both halves need pinning."""

    @pytest.fixture(autouse=True)
    def _no_real_imports(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_imports(started))
        return started

    def _saved(self, instance: MainWindow, tmp_path: Path) -> Path:
        instance.joints.add(
            axis_joint(name="turn", direction=(0.0, 3.0, 0.0), initial_angle_rad=0.75),
            select=False,
        )
        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def test_the_direction_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        instance.load_project(project)

        assert list(instance.joints)[0].joint.direction == pytest.approx((0.0, 3.0, 0.0))

    def test_the_init_rotation_comes_back(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        instance.load_project(project)

        assert list(instance.joints)[0].joint.initial_angle_rad == pytest.approx(0.75)

    def test_the_centre_point_comes_back_in_metres(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        instance.joints.add(
            axis_joint(name="turn", origin=(1.5, 0.0, 0.0), direction=(0.0, 0.0, 1.0)),
            select=False,
        )
        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None

        instance.load_project(written)

        assert list(instance.joints)[0].joint.origin == pytest.approx((1.5, 0.0, 0.0))


class TestRestoringSensorMounts:
    """A round-trip check caught encoders losing their mount on reload while the
    model-mounted sensors looked fine: the sensors were restored before the
    joints existed, so a joint name resolved to nothing."""

    @pytest.fixture(autouse=True)
    def _no_real_imports(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        started: list[Path] = []
        monkeypatch.setattr(MainWindow, "load_file", _record_imports(started))
        return started

    def _saved(self, instance: MainWindow, tmp_path: Path) -> Path:
        joint = instance.joints.add(axis_joint(name="turn"), select=False)
        model = load(instance, "carriage", tmp_path)

        on_joint = instance.sensors.add(
            Sensor(name="enc", kind=SensorKind.ENCODER_ABS, variable="enc"), select=False
        )
        instance.apply_sensor_mount(on_joint.sensor_id, joint.joint_id)

        on_model = instance.sensors.add(beam_sensor(name="gate"), select=False)
        instance.apply_sensor_mount(on_model.sensor_id, model.model_id)

        written = instance.save_project_to(tmp_path / "line.pssim")
        assert written is not None
        return written

    def _reload(self, instance: MainWindow, project: Path) -> None:
        instance.load_project(project)
        finish_import(instance)

    def _mount_name(self, instance: MainWindow, sensor_name: str) -> str | None:
        entry = next(e for e in instance.sensors if e.sensor.name == sensor_name)
        return instance._mount_name(entry.mounted_on)

    def test_a_sensor_on_a_joint_comes_back_mounted(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert self._mount_name(instance, "enc") == "turn"

    def test_a_sensor_on_a_model_comes_back_mounted(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, _ = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        assert self._mount_name(instance, "gate") == "carriage"

    def test_the_scene_is_told_about_both_mounts(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        instance, viewport = window
        project = self._saved(instance, tmp_path)

        self._reload(instance, project)

        # Scoped to the reloaded sensors: the stub records every call, and the
        # ids from before the save are still in it.
        reloaded = [entry.sensor_id for entry in instance.sensors]
        assert all(viewport.sensor_mounts[sensor_id] is not None for sensor_id in reloaded)

    def test_a_mount_naming_nothing_leaves_the_sensor_in_the_scene(
        self, window: tuple[MainWindow, _StubViewport], tmp_path: Path
    ) -> None:
        # A hand-edited or half-copied file still opens.
        instance, _ = window
        project = tmp_path / "broken.pssim"
        project.write_text(
            json.dumps(
                {
                    "version": PROJECT_FORMAT_VERSION,
                    "sensors": [{"name": "gate", "kind": "beam", "mounted_on": "never saved"}],
                }
            ),
            encoding="utf-8",
        )

        instance.load_project(project)

        assert list(instance.sensors)[0].mounted_on is None
