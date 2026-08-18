"""The application's main window.

A shell for now: the menus, the status bar and the place the 3D viewport from `viz/` goes
later. No OPC UA, no machine definition.

The menus are deliberately split the way they were specified — `Open` is a **separate entry
in the menu bar**, not a sub-item of `File`. The convention is to have `Open` under `File`;
if that is to change, it is a one-line edit in `_build_menu()`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import QCoreApplication, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
    QToolButton,
    QWidget,
)

from pssim.cad.model import CadAssembly
from pssim.config.project import PROJECT_FILE_FILTER, PROJECT_SUFFIX
from pssim.domain.errors import PSsimError
from pssim.domain.machine import Transform
from pssim.domain.placement import IDENTITY_PLACEMENT
from pssim.observability import get_logger
from pssim.ui.i18n import SOURCE_LANGUAGE, install_translator
from pssim.ui.icons import fit_icon, view_icon
from pssim.ui.labels import describe_assembly, describe_placement, missing_geometry_suffix
from pssim.ui.loader import StepImportThread
from pssim.ui.model_registry import ModelEntry, ModelRegistry
from pssim.ui.model_tree import ModelTree
from pssim.ui.placement_dialog import PlacementDialog
from pssim.ui.project_controller import (
    LoadPlan,
    ProjectLoader,
    load_plan_from_file,
    save_project,
    spec_to_camera,
)
from pssim.ui.recent_files import RecentProjects, shorten

logger = get_logger(__name__)

#: How the 3D area of the window is created. The tests substitute a plain widget —
#: `ShowBase` may exist only once per process and in the tests we do not want it at all.
ViewportFactory = Callable[[], QWidget]

APP_TITLE: Final = "PSsimTool"


def cad_file_filter() -> str:
    """The filter for the file dialog.

    A function, not a constant: the translation must happen once the translator is
    installed, not when the module is imported.

    STEP is the only supported format so far — see docs/architecture.md; IGES/JT/glTF can
    be added in `cad/`.
    """
    return QCoreApplication.translate("MainWindow", "CAD files (*.step *.stp);;All files (*)")


DEFAULT_SIZE: Final = (1200, 800)
MINIMUM_SIZE: Final = (640, 480)

#: The view menu entries in the order they appear. The keys must exist in
#: `viz.orbit.STANDARD_VIEWS` — that is the single source of the view definitions.
VIEW_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("iso", "Isometric"),
    ("front", "Front"),
    ("back", "Back"),
    ("left", "Left"),
    ("right", "Right"),
    ("top", "Top"),
    ("bottom", "Bottom"),
)


class MainWindow(QMainWindow):
    """The window with the main menu.

    Opening a file is announced through the `file_opened` signal rather than by a direct
    call — when the viewport arrives, it connects to it without this window changing.
    """

    file_opened = Signal(object)
    """Emitted after a file is picked. Carries a `pathlib.Path`."""

    def __init__(
        self,
        parent: QWidget | None = None,
        viewport_factory: ViewportFactory | None = None,
        recent: RecentProjects | None = None,
    ) -> None:
        super().__init__(parent)

        self._current_file: Path | None = None
        self._import_thread: StepImportThread | None = None
        self._placement_dialog: PlacementDialog | None = None
        self._placement_model_id: str | None = None
        self._models = ModelRegistry()
        self._project_path: Path | None = None
        self._recent = recent or RecentProjects()
        self._loader = ProjectLoader(self.load_file)

        self.setWindowTitle(APP_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)

        self._viewport = (viewport_factory or _default_viewport)()
        self.setCentralWidget(self._viewport)

        self._build_model_dock()
        self._build_menu()
        self._build_toolbar()
        self._update_actions()
        self.statusBar().showMessage(self.tr("Ready"))

    # -- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")

        self.open_project_action = QAction(self.tr("&Open Project…"), self)
        self.open_project_action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.open_project_action.setStatusTip(self.tr("Open a saved scene"))
        self.open_project_action.triggered.connect(self.open_project_dialog)
        file_menu.addAction(self.open_project_action)

        self.recent_menu = file_menu.addMenu(self.tr("Open &Recent"))
        self.recent_menu.aboutToShow.connect(self.refresh_recent_menu)

        file_menu.addSeparator()

        self.save_project_action = QAction(self.tr("&Save Project"), self)
        self.save_project_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_project_action.setStatusTip(self.tr("Save the scene and all its settings"))
        self.save_project_action.triggered.connect(self.save_project)
        file_menu.addAction(self.save_project_action)

        self.save_project_as_action = QAction(self.tr("Save Project &As…"), self)
        self.save_project_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.save_project_as_action.setStatusTip(self.tr("Save the scene under a new name"))
        self.save_project_as_action.triggered.connect(self.save_project_as)
        file_menu.addAction(self.save_project_as_action)

        file_menu.addSeparator()

        self.close_all_action = QAction(self.tr("&Close All"), self)
        self.close_all_action.setStatusTip(self.tr("Remove every model from the scene"))
        self.close_all_action.triggered.connect(self.close_all_models)
        file_menu.addAction(self.close_all_action)

        file_menu.addSeparator()

        self.exit_action = QAction("E&xit", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.setStatusTip(self.tr("Quit the application"))
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        open_menu = menu_bar.addMenu("&Open")
        self.open_action = QAction("Open &3D file…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setStatusTip(self.tr("Open a CAD file (STEP)"))
        self.open_action.triggered.connect(self.open_file_dialog)
        open_menu.addAction(self.open_action)

        model_menu = menu_bar.addMenu("&Model")
        self.placement_action = QAction("&Placement…", self)
        self.placement_action.setShortcut(QKeySequence("Ctrl+M"))
        self.placement_action.setStatusTip(
            self.tr("Move and rotate the model relative to the origin")
        )
        self.placement_action.triggered.connect(self.open_placement_dialog)
        model_menu.addAction(self.placement_action)

        self.rename_action = QAction(self.tr("Re&name…"), self)
        self.rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        self.rename_action.setStatusTip(self.tr("Give the selected model a different name"))
        self.rename_action.triggered.connect(self.rename_selected_model)
        model_menu.addAction(self.rename_action)

        self.remove_action = QAction(self.tr("&Remove"), self)
        self.remove_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.remove_action.setStatusTip(self.tr("Remove the selected model"))
        self.remove_action.triggered.connect(self.remove_selected_model)
        model_menu.addAction(self.remove_action)

    # -- projects -----------------------------------------------------------

    @property
    def project_path(self) -> Path | None:
        """The project file the scene came from, or `None` if never saved."""
        return self._project_path

    @property
    def recent_projects(self) -> RecentProjects:
        return self._recent

    def save_project(self) -> Path | None:
        """Save to the current project file, asking for a name the first time."""
        if self._project_path is None:
            return self.save_project_as()
        return self.save_project_to(self._project_path)

    def save_project_as(self) -> Path | None:
        """Ask for a file name and save there."""
        start = str(self._project_path or Path.home() / f"scene{PROJECT_SUFFIX}")
        filename, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save Project"), start, PROJECT_FILE_FILTER
        )
        if not filename:
            return None
        return self.save_project_to(Path(filename))

    @property
    def project_loader(self) -> ProjectLoader:
        """The queue driving a project load. Observable state, not a secret.

        A progress indicator would read this; tests step it deterministically.
        """
        return self._loader

    def save_project_to(self, path: Path) -> Path | None:
        """Write the scene to `path`. Returns the path written, or `None` on failure."""
        models = tuple((entry.name, entry.path, entry.placement) for entry in self._models)
        camera = getattr(self._viewport, "camera_state", None)
        try:
            written = save_project(path, models, self._models.selected_name, camera)
        except PSsimError as exc:
            self.statusBar().showMessage(self.tr("Save failed"))
            QMessageBox.warning(self, self.tr("Save failed"), str(exc))
            return None

        self._set_project_path(written)
        self.statusBar().showMessage(
            self.tr("Saved {0} ({1} models)").format(written.name, len(models))
        )
        return written

    def open_project_dialog(self) -> Path | None:
        """Ask for a project file and load it."""
        start = str(self._project_path.parent) if self._project_path else ""
        filename, _ = QFileDialog.getOpenFileName(
            self, self.tr("Open Project"), start, PROJECT_FILE_FILTER
        )
        if not filename:
            return None
        return self.load_project(Path(filename))

    def load_project(self, path: Path) -> Path | None:
        """Replace the scene with the contents of a project file.

        Models are imported **one at a time** — the importer writes into a shared
        cache, so two at once would race. `ProjectLoader` holds the queue and
        `on_import_finished` drives it forward.
        """
        try:
            plan = load_plan_from_file(path)
        except PSsimError as exc:
            self.statusBar().showMessage(self.tr("Project could not be opened"))
            QMessageBox.warning(self, self.tr("Project could not be opened"), str(exc))
            self._recent.remove(path)
            return None

        self.close_all_models()
        self._set_project_path(path)

        if plan.missing:
            # Reported once, not once per file: five moved files would otherwise
            # mean five modal dialogs before anything appears.
            QMessageBox.warning(
                self,
                self.tr("Missing files"),
                self.tr("{0} of the files this project refers to were not found:\n\n{1}").format(
                    len(plan.missing),
                    "\n".join(str(item) for item in plan.missing),
                ),
            )

        if not plan.has_work:
            self.statusBar().showMessage(self.tr("Project has no models to load"))
            self._loader.finish()
            self._finish_project_load(plan)
            return path

        self.statusBar().showMessage(
            self.tr("Loading project {0} ({1} models)…").format(path.name, len(plan.pending))
        )
        self._loader.begin(plan)
        return path

    def _finish_project_load(self, plan: LoadPlan | None) -> None:
        """Last steps once every model of a project is in: selection, camera."""
        if plan is None:
            return

        if plan.selected_name is not None:
            for entry in self._models:
                if entry.name == plan.selected_name:
                    self.select_model(entry.model_id)
                    break

        if plan.camera is not None:
            set_camera = getattr(self._viewport, "set_camera_state", None)
            if set_camera is not None:
                set_camera(spec_to_camera(plan.camera, self._scene_radius_hint()))

        self.statusBar().showMessage(
            self.tr("Project loaded: {0} models").format(len(self._models))
        )

    def _scene_radius_hint(self) -> float:
        """Rough scene size, used to rebuild the camera's zoom limits.

        Limits are not stored in the project: they follow from how big the scene
        is, and that is decided by whatever was just loaded.
        """
        camera = getattr(self._viewport, "camera_state", None)
        distance = getattr(camera, "distance_m", None)
        return float(distance) if distance else 1.0

    def close_all_models(self) -> None:
        """Empty the scene. Does not touch the current project file name."""
        if self._placement_dialog is not None:
            self._placement_dialog.close()

        clear = getattr(self._viewport, "clear", None)
        if clear is not None:
            clear()
        self._models.clear()
        self._refresh_models()

    def _set_project_path(self, path: Path) -> None:
        self._project_path = path
        self._recent.add(path)
        self.setWindowTitle(f"{APP_TITLE} - {path.name}")

    def refresh_recent_menu(self) -> None:
        """Rebuild the recent-projects submenu.

        Built on `aboutToShow` rather than kept in sync: the list changes rarely
        and rebuilding on demand cannot go stale.
        """
        self.recent_menu.clear()
        paths = self._recent.paths
        if not paths:
            empty = self.recent_menu.addAction(self.tr("(none)"))
            empty.setEnabled(False)
            return

        for path in paths:
            action = self.recent_menu.addAction(shorten(str(path)))
            action.setStatusTip(str(path))
            action.triggered.connect(partial(self.load_project, path))

        self.recent_menu.addSeparator()
        clear_action = self.recent_menu.addAction(self.tr("Clear List"))
        clear_action.triggered.connect(self._recent.clear)

    # -- model tree ---------------------------------------------------------

    def _build_model_dock(self) -> None:
        """Dock the model tree on the left.

        A dock rather than a splitter: a CAD-like tool grows more panels
        (properties, signals) and docks can be rearranged and hidden without
        rebuilding the layout.
        """
        self.model_tree = ModelTree(self)
        self.model_tree.model_selected.connect(self.select_model)

        # The context menu asks, the window decides. The tree has already selected
        # the row the menu was opened on, so none of these carry an id.
        self.model_tree.add_requested.connect(self.open_file_dialog)
        self.model_tree.rename_requested.connect(self.rename_selected_model)
        self.model_tree.placement_requested.connect(self.open_placement_dialog)
        self.model_tree.remove_requested.connect(self.remove_selected_model)

        dock = QDockWidget(self.tr("Models"), self)
        dock.setObjectName("model-dock")
        dock.setWidget(self.model_tree)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.model_dock = dock

    @property
    def models(self) -> ModelRegistry:
        """The loaded models. The tree renders this; nothing else owns it."""
        return self._models

    @property
    def selected_model(self) -> ModelEntry | None:
        """The model every action applies to, or `None` when nothing is picked."""
        return self._models.selected

    def select_model(self, model_id: str | None) -> None:
        """Make a model the active one. Qt slot of the tree.

        Highlights it in the viewport and re-evaluates which actions make sense.
        """
        if not self._models.select(model_id):
            return

        selected = self._models.selected_id
        # Keep the tree in step: selection can also change from code, and a row
        # highlighted in the tree must always be the one the app acts on.
        self.model_tree.select_id(selected)

        set_highlight = getattr(self._viewport, "set_highlight", None)
        if set_highlight is not None:
            set_highlight(selected)

        # A placement dialog belongs to the model it was opened for; keeping it
        # open against a different selection would apply edits to the wrong one.
        if self._placement_dialog is not None and self._placement_model_id != selected:
            self._placement_dialog.close()

        self._update_actions()
        entry = self._models.selected
        if entry is not None:
            self.statusBar().showMessage(self.tr("Selected {0}").format(entry.name))

    def rename_selected_model(self) -> str | None:
        """Ask for a new name for the selected model. Returns the name applied.

        `None` when there is nothing selected, the dialog was cancelled, or the
        name was left as it was. A blank name is refused by the registry, so the
        dialog closing on an empty field changes nothing.
        """
        entry = self._models.selected
        if entry is None:
            return None

        name, accepted = QInputDialog.getText(
            self,
            self.tr("Rename Model"),
            self.tr("Name:"),
            QLineEdit.EchoMode.Normal,
            entry.name,
        )
        if not accepted:
            return None

        updated = self._models.rename(entry.model_id, name)
        if updated is None or updated.name == entry.name:
            return None

        self._refresh_models()
        if updated.name != name.strip():
            # The counter suffix is not what the user typed, so it gets said out
            # loud rather than appearing in the tree unexplained.
            self.statusBar().showMessage(
                self.tr("Name {0} is taken, using {1}").format(name.strip(), updated.name)
            )
        else:
            self.statusBar().showMessage(
                self.tr("Renamed {0} to {1}").format(entry.name, updated.name)
            )
        return updated.name

    def remove_selected_model(self) -> None:
        """Drop the selected model from the scene and the tree."""
        entry = self._models.selected
        if entry is None:
            return

        remove_model = getattr(self._viewport, "remove_model", None)
        if remove_model is not None:
            remove_model(entry.model_id)

        self._models.remove(entry.model_id)
        if self._placement_model_id == entry.model_id and self._placement_dialog is not None:
            self._placement_dialog.close()

        self._refresh_models()
        self.statusBar().showMessage(self.tr("Removed {0}").format(entry.name))

    def _refresh_models(self) -> None:
        """Re-render the tree and re-evaluate actions after any change."""
        self.model_tree.refresh(self._models)
        set_highlight = getattr(self._viewport, "set_highlight", None)
        if set_highlight is not None:
            set_highlight(self._models.selected_id)
        self._update_actions()

    def _update_actions(self) -> None:
        """Enable only what makes sense right now.

        With nothing selected there is no target for placement or removal, so
        those actions are disabled rather than silently doing nothing.
        """
        has_selection = self._models.selected is not None
        self.placement_action.setEnabled(has_selection)
        self.rename_action.setEnabled(has_selection)
        self.remove_action.setEnabled(has_selection)
        self.fit_action.setEnabled(not self._models.is_empty)

    # -- the view toolbar ---------------------------------------------------

    def _build_toolbar(self) -> None:
        """The toolbar for switching views.

        One button with a dropdown menu, not seven separate icons — the view changes rarely
        and seven buttons would take up a toolbar that more important tools are going to
        arrive in.
        """
        toolbar = QToolBar(self.tr("View"), self)
        toolbar.setObjectName("view-toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.view_menu = QMenu(self.tr("View"), self)
        self.view_actions: dict[str, QAction] = {}

        for index, (name, label) in enumerate(VIEW_LABELS, start=1):
            action = QAction(view_icon(name), label, self)
            action.setShortcut(QKeySequence(f"Ctrl+{index}"))
            action.setStatusTip(self.tr("View: {0}").format(label))
            action.triggered.connect(partial(self.set_view, name))
            self.view_menu.addAction(action)
            self.view_actions[name] = action

        self.view_button = QToolButton(self)
        self.view_button.setText(self.tr("View"))
        self.view_button.setIcon(view_icon("iso"))
        self.view_button.setMenu(self.view_menu)
        self.view_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addWidget(self.view_button)

        self.fit_action = QAction(fit_icon(), self.tr("Fit to view"), self)
        self.fit_action.setShortcut(QKeySequence("Ctrl+0"))
        self.fit_action.setStatusTip(self.tr("Center the camera on the whole model"))
        self.fit_action.triggered.connect(self.fit_view)
        toolbar.addAction(self.fit_action)

        self.toolbar = toolbar

    def set_view(self, name: str) -> None:
        """Switch the 3D view. The zoom stays, only the angle changes."""
        set_view = getattr(self._viewport, "set_view", None)
        if set_view is None:
            logger.debug("viewport does not support switching views", view=name)
            return
        set_view(name)
        # The button icon shows which view we are currently in.
        self.view_button.setIcon(view_icon(name))
        self.statusBar().showMessage(self.tr("View: {0}").format(name))

    def fit_view(self) -> None:
        """Frame the selected model, or everything when nothing is selected."""
        fit = getattr(self._viewport, "fit_view", None)
        if fit is None:
            return

        entry = self._models.selected
        fit(entry.model_id if entry is not None else None)
        if entry is None:
            self.statusBar().showMessage(self.tr("All models in view"))
        else:
            self.statusBar().showMessage(self.tr("{0} in view").format(entry.name))

    # -- umiestnenie modelu -------------------------------------------------

    def open_placement_dialog(self) -> PlacementDialog | None:
        """Open the placement dialog for the selected model.

        The dialog is **modeless** and edits the scene as you type — the numbers
        are impossible to judge blind. It is bound to one model and names it in
        the title, so a later selection change cannot redirect the edits.

        Returns `None` when nothing is selected. The action is disabled in that
        state, so this only guards against a direct call.
        """
        entry = self._models.selected
        if entry is None:
            logger.debug("no model selected, placement dialog not opened")
            return None

        if self._placement_dialog is not None and self._placement_model_id == entry.model_id:
            self._placement_dialog.raise_()
            self._placement_dialog.activateWindow()
            return self._placement_dialog

        if self._placement_dialog is not None:
            self._placement_dialog.close()

        dialog = PlacementDialog(entry.placement, self)
        dialog.setWindowTitle(f"{dialog.windowTitle()} - {entry.name}")
        dialog.placement_changed.connect(self.apply_placement)
        dialog.finished.connect(self._on_placement_dialog_closed)
        self._placement_dialog = dialog
        self._placement_model_id = entry.model_id
        dialog.show()
        return dialog

    def placement(self, model_id: str | None = None) -> Transform:
        """Placement of a model, or of the selected one when `model_id` is `None`."""
        resolved = model_id or self._models.selected_id
        if resolved is None:
            return IDENTITY_PLACEMENT
        entry = self._models.get(resolved)
        return entry.placement if entry is not None else IDENTITY_PLACEMENT

    def apply_placement(self, placement: Transform) -> None:
        """Push a placement onto the model the dialog belongs to. Qt slot.

        Uses the id the dialog was opened with, not the current selection: the
        two can differ for an instant while the selection is changing.
        """
        model_id = self._placement_model_id or self._models.selected_id
        if model_id is None:
            return

        set_placement = getattr(self._viewport, "set_placement", None)
        if set_placement is not None:
            set_placement(model_id, placement)

        self._models.set_placement(model_id, placement)
        self.model_tree.refresh(self._models)
        self.statusBar().showMessage(describe_placement(placement))

    def _on_placement_dialog_closed(self, _result: int) -> None:
        self._placement_dialog = None
        self._placement_model_id = None

    # -- opening a file -----------------------------------------------------

    @property
    def current_file(self) -> Path | None:
        """The file opened most recently, or `None`."""
        return self._current_file

    def open_file_dialog(self) -> Path | None:
        """Show the file dialog. Returns the chosen path, or `None`.

        Separated from `set_current_file()` so the logic after the choice can be tested
        without opening a modal dialog.
        """
        start_directory = str(self._current_file.parent) if self._current_file else ""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Open 3D file"),
            start_directory,
            cad_file_filter(),
        )
        if not filename:
            logger.debug("file selection cancelled")
            return None
        return self.open_path(Path(filename))

    def open_path(self, path: Path) -> Path:
        """Open a file: remember it and start loading the geometry."""
        self.set_current_file(path)
        self.load_file(path)
        return path

    def set_current_file(self, path: Path) -> Path:
        """Remember the chosen file and announce it to the rest of the application.

        Does **not** start loading — that is `load_file()`. Kept apart because
        "which file was picked" and "an import is running" are separate states.

        The title shows the file opened most recently, not the selected model:
        with several models loaded there is no single current file.
        """
        self._current_file = path
        self.setWindowTitle(f"{APP_TITLE} - {path.name}")
        self.statusBar().showMessage(str(path))
        logger.info("file chosen", file=str(path))
        self.file_opened.emit(path)
        return path

    # -- loading the geometry -----------------------------------------------

    @property
    def is_loading(self) -> bool:
        return self._import_thread is not None

    def load_file(self, path: Path) -> None:
        """Start importing a STEP file in the background.

        `Open` is disabled during the import — a second one running at the same time would
        overwrite both the cache and the scene. A large assembly takes minutes to import,
        which is why it cannot be done in the main thread.
        """
        if self._import_thread is not None:
            logger.debug("an import is already running, ignoring", file=str(path))
            return

        self.open_action.setEnabled(False)
        self.statusBar().showMessage(self.tr("Loading {0}…").format(path.name))

        thread = StepImportThread(path, parent=self)
        thread.succeeded.connect(self.on_import_succeeded)
        thread.failed.connect(self.on_import_failed)
        thread.finished.connect(self.on_import_finished)
        self._import_thread = thread
        thread.start()

    def add_model(self, path: Path, assembly: CadAssembly, cache_dir: Path) -> ModelEntry:
        """Register an already-imported model, show it and select it.

        Separate from `on_import_succeeded` so that anything holding a finished
        assembly can add it — a future "restore session" would, and tests do.
        """
        entry = self._models.add(
            path,
            node_count=len(assembly.nodes),
            triangle_count=assembly.triangle_count,
        )

        add_to_scene = getattr(self._viewport, "add_model", None)
        missing = 0 if add_to_scene is None else add_to_scene(entry.model_id, assembly, cache_dir)

        self._refresh_models()

        message = f"{entry.name}: {describe_assembly(assembly)}"
        if missing:
            message += missing_geometry_suffix(missing)
        self.statusBar().showMessage(message)
        return entry

    def on_import_succeeded(self, metadata: Any, cache_dir: Path) -> None:
        """Qt slot. Already on the main thread — only it may build the scene.

        Every import **adds** a model, it does not replace what is loaded. The
        same file may be opened repeatedly, which is why the registry hands out
        ids rather than keying on the path.
        """
        path = self._import_thread.step_file if self._import_thread is not None else Path()
        entry = self.add_model(path, metadata.assembly, cache_dir)

        # A model that came from a project brings its saved placement with it.
        pending = self._loader.current
        if pending is not None:
            self._models.set_placement(entry.model_id, pending.placement)
            set_placement = getattr(self._viewport, "set_placement", None)
            if set_placement is not None:
                set_placement(entry.model_id, pending.placement)
            self._refresh_models()

    def on_import_failed(self, message: str) -> None:
        """Qt slot: import zlyhal."""
        self.statusBar().showMessage(self.tr("Loading failed"))
        QMessageBox.warning(self, self.tr("Loading failed"), message)

    def on_import_finished(self) -> None:
        """Qt slot: the thread ended, however it went.

        This is also what drives a project load forward: the next model starts
        only once the previous import has released the cache.
        """
        self._import_thread = None
        self.open_action.setEnabled(True)

        if self._loader.is_loading and self._loader.start_next():
            return
        if self._loader.plan is not None:
            self._finish_project_load(self._loader.finish())


def _default_viewport() -> QWidget:
    """The real 3D viewport. Separated so it can be replaced in tests."""
    from pssim.ui.viewport import Panda3DViewport

    return Panda3DViewport()


def run(argv: list[str] | None = None, language: str = SOURCE_LANGUAGE) -> int:
    """Run the application and return its exit code. Blocks until the window is closed.

    The translator is installed **before** the window is created — the strings are
    translated at the moment the widgets are assembled, not continuously.
    """
    from PySide6.QtWidgets import QApplication

    application: Any = QApplication.instance() or QApplication(argv or [])
    application.setApplicationName(APP_TITLE)
    install_translator(application, language)

    window = MainWindow()
    window.show()
    return int(application.exec())
