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

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.cad.model import CadAssembly, CadNode  # noqa: E402
from pssim.config.project import PROJECT_SUFFIX, read_project  # noqa: E402
from pssim.domain.machine import Transform  # noqa: E402
from pssim.domain.placement import (  # noqa: E402
    IDENTITY_PLACEMENT,
    PlacementDisplay,
    to_transform,
)
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.model_registry import ModelEntry  # noqa: E402
from pssim.ui.project_controller import spec_to_camera  # noqa: E402
from pssim.ui.recent_files import RecentProjects  # noqa: E402
from pssim.viz.orbit import OrbitCamera  # noqa: E402

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
        first = MainWindow(viewport_factory=QWidget, recent=RecentProjects(settings))
        first.save_project_to(tmp_path / "line.pssim")
        first.close()

        second = MainWindow(viewport_factory=QWidget, recent=RecentProjects(settings))
        try:
            assert len(second.recent_projects.paths) == 1
        finally:
            second.close()
