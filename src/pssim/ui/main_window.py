"""The application's main window.

A shell for now: the menus, the status bar and the place the 3D viewport from `viz/` goes
later. No OPC UA, no machine definition.

The menu bar is split by **what you are working on**: `Models`, `Geometry` (the axes and
trajectories), `Sensors`, `Scene`. `File` is the project as a document — the one place
something gets opened in place of the current scene; `Models → Add 3D Model` adds to what is
already loaded, which is why it is not there.

Every entry that creates something starts **Add**, and keeps its noun so it reads from the
menu bar: `Add 3D Model…`, `Add Axis…`, `Add Trajectory…`, `Add Sensor…`. Below the separator
the menu title already says what the subject is, so `Sensors → Edit…` beats
`Sensors → Edit Sensor…`.

`Scene` groups into submenus rather than listing everything flat, for the same reason: a leaf
should say what it does from its path alone. `Sizes…` on its own does not say what it sizes,
while `Scene → Crosses and Labels → Sizes…` does.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import QCoreApplication, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QKeySequence
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
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
from pssim.config.binding import BindingDirection
from pssim.config.project import (
    PROJECT_FILE_FILTER,
    PROJECT_SUFFIX,
    JointSave,
    JointSpec,
    ModelSave,
    SensorSpec,
)
from pssim.domain.errors import PSsimError
from pssim.domain.machine import Rgba, Transform, Vec3
from pssim.domain.model_joints import ModelJoint, ModelJointKind, clamp, effective_limits
from pssim.domain.placement import IDENTITY_PLACEMENT
from pssim.domain.sensors import Sensor, SensorKind
from pssim.domain.units import MM_TO_M
from pssim.observability import get_logger
from pssim.ui.connection_controller import ConnectionController
from pssim.ui.connection_status import ConnectionStatusWidget
from pssim.ui.floor_dialog import FloorDialog
from pssim.ui.i18n import SOURCE_LANGUAGE, install_translator
from pssim.ui.icons import (
    app_icon,
    fit_icon,
    joint_icon,
    model_icon,
    sensor_icon,
    view_icon,
)
from pssim.ui.joint_dialog import BindDialog, JointChoices, JointDialog, PickTarget
from pssim.ui.joint_registry import JointEntry, JointRegistry, descendants_of, would_cycle
from pssim.ui.labels import describe_assembly, describe_placement, missing_geometry_suffix
from pssim.ui.loader import StepImportThread
from pssim.ui.model_registry import ModelEntry, ModelRegistry
from pssim.ui.model_tree import TABLE_NAME as MODEL_TABLE
from pssim.ui.model_tree import ModelTree
from pssim.ui.model_values_panel import ModelValuesPanel
from pssim.ui.opcua_dialog import AssignTagDialog, ConnectionDialog, DiagnosticsDialog
from pssim.ui.placement_dialog import PlacementDialog
from pssim.ui.project_controller import (
    LoadPlan,
    PendingModel,
    ProjectLoader,
    SceneState,
    load_plan_from_file,
    save_project,
    spec_to_camera,
    spec_to_floor,
)
from pssim.ui.properties_panel import PropertiesPanel
from pssim.ui.recent_files import RecentProjects, shorten
from pssim.ui.sensor_dialog import SensorDialog
from pssim.ui.sensor_registry import SensorEntry, SensorRegistry
from pssim.ui.sensor_tree import TABLE_NAME as SENSOR_TABLE
from pssim.ui.sensor_tree import SensorTree
from pssim.ui.settings import ConnectionSettings, SettingsStore, ViewSettings
from pssim.ui.sizes_dialog import Sizes, SizesDialog
from pssim.ui.variable_registry import VariableRegistry, VariableSource
from pssim.ui.variable_tree import TABLE_NAME as VARIABLE_TABLE
from pssim.ui.variable_tree import VariableTree
from pssim.viz.axes import HIGHLIGHT_COLOR
from pssim.viz.embed import (
    DEFAULT_CROSS_SIZE_M,
    DEFAULT_ORIGIN_CROSS_SIZE_M,
    DEFAULT_TEXT_SIZE_M,
)
from pssim.viz.floor import FloorState

logger = get_logger(__name__)

#: How the 3D area of the window is created. The tests substitute a plain widget —
#: `ShowBase` may exist only once per process and in the tests we do not want it at all.
ViewportFactory = Callable[[], QWidget]

APP_TITLE: Final = "PSsimTool"

#: Only ever used to give `QSettings` a stable place of its own on disk. Nothing
#: is fetched from it and nothing needs to resolve.
APP_DOMAIN: Final = "pssim.local"


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


def _to_qcolor(color: Rgba) -> QColor:
    """Internal 0.0–1.0 floats to Qt's 0–255 integers."""
    return QColor.fromRgbF(color[0], color[1], color[2], color[3])


def _from_qcolor(color: QColor) -> Rgba:
    """Qt's colour to the internal floats.

    Alpha is forced opaque: a half-transparent part reads as a rendering fault in
    a CAD view, and the selection and collision outlines are drawn assuming they
    sit against solid geometry.
    """
    return (color.redF(), color.greenF(), color.blueF(), 1.0)


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
        settings: SettingsStore | None = None,
    ) -> None:
        super().__init__(parent)

        # Injectable so a test can point it at a temp file: the real one writes
        # into the user's own store, which no test may touch.
        self._settings = settings if settings is not None else SettingsStore()

        self._current_file: Path | None = None
        self._import_thread: StepImportThread | None = None
        self._placement_dialog: PlacementDialog | None = None
        self._placement_model_id: str | None = None
        self._floor_dialog: FloorDialog | None = None
        self._joint_dialog: JointDialog | None = None
        self._joint_dialog_joint_id: str | None = None
        self._joint_dialog_parent_id: str | None = None
        self._values_panel: ModelValuesPanel | None = None
        self._selected_variable: str | None = None
        self._session_password = ""
        """Typed into the connection dialog and held for this session only. It is
        deliberately not in `ConnectionSettings`, which is what gets written to
        disk — see `ui/settings.py`."""
        self._models = ModelRegistry()
        self._sensors = SensorRegistry()
        self._joints = JointRegistry()
        self._variables = VariableRegistry()
        self._connection_settings = self._settings.load_connection()
        self._connection = ConnectionController(self._variables, self)
        self._cross_size_m = DEFAULT_CROSS_SIZE_M
        self._text_size_m = DEFAULT_TEXT_SIZE_M
        self._origin_cross_size_m = DEFAULT_ORIGIN_CROSS_SIZE_M
        """How big markers and text are drawn. Scene-wide, so they live here
        rather than on any one item — the whole point is that they all match."""
        self._project_path: Path | None = None
        self._recent = recent or RecentProjects()
        self._loader = ProjectLoader(self.load_file)

        self.setWindowTitle(APP_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)

        self._viewport = (viewport_factory or _default_viewport)()
        self.setCentralWidget(self._viewport)

        self._build_model_dock()
        self._build_sensor_dock()
        self._build_variable_dock()
        self._build_properties_dock()
        self._build_menu()
        self._build_toolbar()
        self._build_connection_indicator()
        self._connection.status_changed.connect(self._on_connection_status)
        self._connection.values_changed.connect(self._on_values_changed)
        self._update_actions()
        self.refresh_variables()
        self.restore_view_settings()
        self.statusBar().showMessage(self.tr("Ready"))

    def _build_connection_indicator(self) -> None:
        """The connection state, permanently on the right of the status bar.

        `addPermanentWidget` rather than `showMessage`: a message is wiped by the
        next one, and where the connection stands is not a message.
        """
        self.connection_status = ConnectionStatusWidget(self)
        self.connection_status.clicked.connect(self.open_diagnostics_dialog)
        self.statusBar().addPermanentWidget(self.connection_status)
        self._refresh_connection_indicator()

    def _refresh_connection_indicator(self) -> None:
        """Point the indicator at whatever the controller currently says."""
        self.connection_status.show_status(
            self._connection.status,
            self._connection_settings.endpoint,
            self._connection.last_error or "",
        )

    # -- settings -----------------------------------------------------------

    @property
    def settings(self) -> SettingsStore:
        """Where anything durable-but-not-scene is kept. Observable, not a secret."""
        return self._settings

    def restore_view_settings(self) -> None:
        """Put the saved column widths back, if there are any.

        A table with nothing saved keeps the widths it was built with — the
        absence of a setting is not a reason to collapse a column.
        """
        view = self._settings.load_view()
        for table, tree in self._tables().items():
            widths = view.widths_for(table)
            if widths:
                tree.set_column_widths(widths)

    def save_view_settings(self) -> None:
        """Record where the columns currently are.

        Called on close rather than on every drag: a width changes continuously
        while the mouse is down, and writing the store per pixel would be a lot
        of I/O for a value nobody reads until the next launch.
        """
        view = ViewSettings()
        for table, tree in self._tables().items():
            view = view.with_widths(table, tree.column_widths())
        self._settings.save_view(view)

    def _tables(self) -> dict[str, ModelTree | SensorTree | VariableTree]:
        """Every table whose layout is saved, by the name it is saved under."""
        return {
            MODEL_TABLE: self.model_tree,
            SENSOR_TABLE: self.sensor_tree,
            VARIABLE_TABLE: self.variable_tree,
        }

    @property
    def connection_settings(self) -> ConnectionSettings:
        """Where the server is and what each variable is bound to."""
        return self._connection_settings

    def save_connection_settings(self, settings: ConnectionSettings) -> None:
        """Store the connection and push the tag mapping into the registry."""
        self._connection_settings = settings
        self._settings.save_connection(settings)
        self._variables.set_tags(dict(settings.tags))
        self._refresh_variables_view()
        # The endpoint may have changed, and the indicator names it.
        self._refresh_connection_indicator()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt's own name
        """Qt calls this as the window goes away.

        The source is stopped **first**: it owns a thread, and a window that
        vanished while it was still publishing would be writing to a server
        nobody is watching.
        """
        self._connection.disconnect_from_server()
        self.save_view_settings()
        super().closeEvent(event)

    # -- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        """The menu bar, one helper per menu.

        Split by subject rather than by verb: everything you can do to a model is
        under `Models`, to an axis or a trajectory under `Geometry`, to a sensor
        under `Sensors`, and to the scene as a whole under `Scene`. An `Edit`
        menu holding all of it would be fifteen unrelated entries with no way to
        tell which applied to what is selected.
        """
        menu_bar = self.menuBar()
        self._build_file_menu(menu_bar.addMenu("&File"))
        self._build_models_menu(menu_bar.addMenu(self.tr("&Models")))
        self._build_geometry_menu(menu_bar.addMenu(self.tr("&Geometry")))
        self._build_sensors_menu(menu_bar.addMenu(self.tr("Se&nsors")))
        self._build_scene_menu(menu_bar.addMenu(self.tr("&Scene")))
        self._build_communication_menu(menu_bar.addMenu(self.tr("Co&mmunication")))

    def _build_file_menu(self, file_menu: QMenu) -> None:
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

    def _build_models_menu(self, model_menu: QMenu) -> None:
        # Adding geometry is a model operation, not a document one: it adds to
        # whatever is already loaded rather than opening a file in place of it
        # (`File → Open Project` is the one that replaces the scene).
        self.insert_model_action = QAction(model_icon(), self.tr("&Add 3D Model…"), self)
        self.insert_model_action.setShortcut(QKeySequence.StandardKey.Open)
        self.insert_model_action.setStatusTip(self.tr("Add a CAD file (STEP) to the scene"))
        self.insert_model_action.triggered.connect(self.open_file_dialog)
        model_menu.addAction(self.insert_model_action)

        model_menu.addSeparator()

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

        # Binding and driving are model operations even though what they name is
        # a joint: you attach *this model* to an axis, and the values window
        # belongs to the model whose chain is being driven.
        self.bind_action = QAction(self.tr("&Bind To…"), self)
        self.bind_action.setStatusTip(self.tr("Attach the model to an axis or a trajectory"))
        self.bind_action.triggered.connect(self.open_bind_dialog)
        model_menu.addAction(self.bind_action)

        # `Values…`, not `Variables…`: the Communication tab now owns that word,
        # and it means something else there — a name bound to an OPC UA tag,
        # rather than the sliders that drive this model's joints by hand.
        self.values_action = QAction(self.tr("&Values…"), self)
        self.values_action.setStatusTip(self.tr("Drive the joints that move this model"))
        self.values_action.triggered.connect(self.open_values_panel)
        model_menu.addAction(self.values_action)

        model_menu.addSeparator()

        # The only Remove with a shortcut. Three of them all bound to Delete
        # would be ambiguous and Qt would fire none of them.
        self.remove_action = QAction(self.tr("&Remove"), self)
        self.remove_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.remove_action.setStatusTip(self.tr("Remove the selected model"))
        self.remove_action.triggered.connect(self.remove_selected_model)
        model_menu.addAction(self.remove_action)

    def _build_geometry_menu(self, geometry_menu: QMenu) -> None:
        """The axes and trajectories — the kinematic skeleton models hang off.

        They were reachable only from the model tree's context menu, because
        they are neither a model nor a sensor and there was nowhere to put them.
        """
        self.add_axis_action = QAction(joint_icon(ModelJointKind.AXIS), self.tr("Add &Axis…"), self)
        self.add_axis_action.setStatusTip(
            self.tr("Add a rotation axis, under the selected one if there is one")
        )
        self.add_axis_action.triggered.connect(
            partial(self.open_add_joint_dialog, ModelJointKind.AXIS)
        )
        geometry_menu.addAction(self.add_axis_action)

        self.add_trajectory_action = QAction(
            joint_icon(ModelJointKind.TRAJECTORY), self.tr("Add &Trajectory…"), self
        )
        self.add_trajectory_action.setStatusTip(
            self.tr("Add a travel path, under the selected joint if there is one")
        )
        self.add_trajectory_action.triggered.connect(
            partial(self.open_add_joint_dialog, ModelJointKind.TRAJECTORY)
        )
        geometry_menu.addAction(self.add_trajectory_action)

        geometry_menu.addSeparator()

        self.edit_joint_action = QAction(self.tr("&Edit…"), self)
        self.edit_joint_action.setStatusTip(
            self.tr("Change the selected axis or trajectory's own geometry")
        )
        self.edit_joint_action.triggered.connect(self.open_edit_joint_dialog)
        geometry_menu.addAction(self.edit_joint_action)

        self.joint_parent_action = QAction(self.tr("&Carried By…"), self)
        self.joint_parent_action.setStatusTip(self.tr("Choose which joint carries this one"))
        self.joint_parent_action.triggered.connect(self.open_joint_parent_dialog)
        geometry_menu.addAction(self.joint_parent_action)

        geometry_menu.addSeparator()

        self.remove_joint_action = QAction(self.tr("&Remove"), self)
        self.remove_joint_action.setStatusTip(
            self.tr("Remove the selected axis or trajectory; anything bound to it is released")
        )
        self.remove_joint_action.triggered.connect(self.remove_selected_joint)
        geometry_menu.addAction(self.remove_joint_action)

    def _build_sensors_menu(self, sensor_menu: QMenu) -> None:
        # The beam icon rather than a generic one: it is the kind the dialog
        # opens on, so the picture matches what appears.
        self.add_sensor_action = QAction(
            sensor_icon(SensorKind.BEAM), self.tr("&Add Sensor…"), self
        )
        self.add_sensor_action.setStatusTip(
            self.tr("Place a laser, inductive, distance or encoder sensor")
        )
        self.add_sensor_action.triggered.connect(self.open_sensor_dialog)
        sensor_menu.addAction(self.add_sensor_action)

        sensor_menu.addSeparator()

        self.edit_sensor_action = QAction(self.tr("&Edit…"), self)
        self.edit_sensor_action.setStatusTip(self.tr("Edit the selected sensor"))
        self.edit_sensor_action.triggered.connect(self.edit_selected_sensor)
        sensor_menu.addAction(self.edit_sensor_action)

        self.mount_sensor_action = QAction(self.tr("&Mount On…"), self)
        self.mount_sensor_action.setStatusTip(
            self.tr("Choose the model or axis that carries the selected sensor")
        )
        self.mount_sensor_action.triggered.connect(self.open_sensor_mount_dialog)
        sensor_menu.addAction(self.mount_sensor_action)

        sensor_menu.addSeparator()

        self.remove_sensor_action = QAction(self.tr("&Remove"), self)
        self.remove_sensor_action.setStatusTip(self.tr("Remove the selected sensor"))
        self.remove_sensor_action.triggered.connect(self.remove_selected_sensor)
        sensor_menu.addAction(self.remove_sensor_action)

    def _build_communication_menu(self, menu: QMenu) -> None:
        """Talking to the PLC. Its own menu because it is its own subject (R17)
        — it is about neither the models nor the scene, but about what drives
        them."""
        self.connect_action = QAction(self.tr("&Connect"), self)
        self.connect_action.setShortcut(QKeySequence(Qt.Key.Key_F5))
        self.connect_action.setStatusTip(self.tr("Subscribe to every variable that has a tag"))
        self.connect_action.triggered.connect(self.connect_to_server)
        menu.addAction(self.connect_action)

        self.disconnect_action = QAction(self.tr("&Disconnect"), self)
        self.disconnect_action.setStatusTip(self.tr("Stop reading from the server"))
        self.disconnect_action.triggered.connect(self.disconnect_from_server)
        menu.addAction(self.disconnect_action)

        menu.addSeparator()

        self.connection_action = QAction(self.tr("Connection &Settings…"), self)
        self.connection_action.setStatusTip(
            self.tr("Where the server is, and whether writing is allowed")
        )
        self.connection_action.triggered.connect(self.open_connection_dialog)
        menu.addAction(self.connection_action)

        menu.addSeparator()

        self.assign_tag_action = QAction(self.tr("&Assign Tag…"), self)
        self.assign_tag_action.setStatusTip(
            self.tr("Pick the node the selected variable reads from")
        )
        self.assign_tag_action.triggered.connect(self.open_assign_tag_dialog)
        menu.addAction(self.assign_tag_action)

        self.clear_tag_action = QAction(self.tr("C&lear Tag"), self)
        self.clear_tag_action.setStatusTip(self.tr("Leave the selected variable bound to nothing"))
        self.clear_tag_action.triggered.connect(self.clear_variable_tag)
        menu.addAction(self.clear_tag_action)

        menu.addSeparator()

        self.diagnostics_action = QAction(self.tr("&Diagnostics…"), self)
        self.diagnostics_action.setStatusTip(
            self.tr("What the last connection attempt tried, and where it stopped")
        )
        self.diagnostics_action.triggered.connect(self.open_diagnostics_dialog)
        menu.addAction(self.diagnostics_action)

    def _build_scene_menu(self, scene_menu: QMenu) -> None:
        """Everything that applies to the scene rather than to one item in it.

        Two submenus, because a leaf should say what it does from its path: on
        its own `Sizes…` does not say what it sizes, and neither `Floor` nor
        `Origin Cross` says whether it is a switch or a dialog until it is read.
        """
        self.floor_menu = scene_menu.addMenu(self.tr("&Floor"))

        self.floor_visible_action = QAction(self.tr("&Show Floor"), self)
        self.floor_visible_action.setCheckable(True)
        self.floor_visible_action.setChecked(True)
        self.floor_visible_action.setStatusTip(self.tr("Show or hide the floor grid"))
        self.floor_visible_action.toggled.connect(self.set_floor_visible)
        self.floor_menu.addAction(self.floor_visible_action)

        self.floor_position_action = QAction(self.tr("&Position…"), self)
        self.floor_position_action.setStatusTip(self.tr("Move the floor up or down"))
        self.floor_position_action.triggered.connect(self.open_floor_dialog)
        self.floor_menu.addAction(self.floor_position_action)

        self.annotations_menu = scene_menu.addMenu(self.tr("&Crosses and Labels"))

        self.origin_cross_action = QAction(self.tr("&Origin Cross"), self)
        self.origin_cross_action.setCheckable(True)
        self.origin_cross_action.setChecked(True)
        self.origin_cross_action.setStatusTip(
            self.tr("Show or hide the coordinate cross at the scene origin")
        )
        self.origin_cross_action.toggled.connect(self.set_origin_cross_visible)
        self.annotations_menu.addAction(self.origin_cross_action)

        self.joint_names_action = QAction(self.tr("Joint &Names"), self)
        self.joint_names_action.setCheckable(True)
        self.joint_names_action.setChecked(True)
        self.joint_names_action.setStatusTip(
            self.tr("Show or hide the names of every axis and trajectory")
        )
        self.joint_names_action.toggled.connect(self.set_names_visible)
        self.annotations_menu.addAction(self.joint_names_action)

        self.annotations_menu.addSeparator()

        self.sizes_action = QAction(self.tr("&Sizes…"), self)
        self.sizes_action.setStatusTip(self.tr("How big coordinate crosses and text are drawn"))
        self.sizes_action.triggered.connect(self.open_sizes_dialog)
        self.annotations_menu.addAction(self.sizes_action)

        scene_menu.addSeparator()

        self.check_collisions_action = QAction(self.tr("&Check Collisions"), self)
        self.check_collisions_action.setStatusTip(
            self.tr("Look for models whose parts overlap right now")
        )
        self.check_collisions_action.triggered.connect(self.check_collisions)
        scene_menu.addAction(self.check_collisions_action)

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
        models = tuple(
            ModelSave(
                name=entry.name,
                path=entry.path,
                placement=entry.placement,
                bound_to_joint_name=self._joint_name(entry.bound_to_joint_id),
                anchor=entry.anchor,
                is_visible=entry.is_visible,
                show_axes=entry.show_axes,
                color=entry.color,
                highlight_color=entry.highlight_color,
            )
            for entry in self._models
        )
        scene = SceneState(
            selected_name=self._models.selected_name,
            camera=getattr(self._viewport, "camera_state", None),
            floor=FloorState(
                visible=getattr(self._viewport, "floor_visible", True),
                z_m=getattr(self._viewport, "floor_z_m", 0.0),
            ),
            sensors=tuple(
                SensorSpec.from_sensor(entry.sensor, self._mount_name(entry.mounted_on))
                for entry in self._sensors
            ),
            show_joint_names=self.joint_names_action.isChecked(),
            cross_size_mm=self._cross_size_m / MM_TO_M,
            text_size_mm=self._text_size_m / MM_TO_M,
            origin_cross_size_mm=self._origin_cross_size_m / MM_TO_M,
            show_origin_cross=self.origin_cross_action.isChecked(),
            joints=tuple(
                JointSpec.from_joint(
                    JointSave(
                        joint=entry.joint,
                        value=entry.value,
                        show_axes=entry.show_axes,
                        show_name=entry.show_name,
                        color=entry.color,
                    ),
                    self._joint_name(entry.parent_joint_id),
                )
                for entry in self._joints
            ),
        )
        try:
            written = save_project(path, models, scene)
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
        """Last steps once every model of a project is in: selection, camera,
        floor, sensors, then the joints with their bindings and values.

        The joints come last because a binding needs both its model and its
        joint to exist, and the models only finish arriving here.
        """
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

        floor = spec_to_floor(plan.floor)
        # The tick is set without emitting: the scene is told directly on the
        # next line, and letting the action fire as well would say it twice.
        self.floor_visible_action.blockSignals(True)
        self.floor_visible_action.setChecked(floor.visible)
        self.floor_visible_action.blockSignals(False)
        self.set_floor_visible(floor.visible)
        self.apply_floor_z(floor.z_m)

        # Mounts are collected, not applied: an encoder names a joint, and
        # `_mount_id` can only find one that `_restore_joints` has already put
        # in the registry. Applied below, once it has.
        sensor_mounts: list[tuple[str, str]] = []
        for spec in plan.sensors:
            entry = self._sensors.add(spec.to_sensor(), select=False)
            if spec.mounted_on is not None:
                sensor_mounts.append((entry.sensor_id, spec.mounted_on))
            add_sensor = getattr(self._viewport, "add_sensor", None)
            if add_sensor is not None:
                add_sensor(entry.sensor_id, entry.sensor, None)
        self._refresh_sensors()

        self.joint_names_action.setChecked(plan.show_joint_names)
        self.set_names_visible(plan.show_joint_names)
        self.apply_sizes(
            plan.cross_size_mm * MM_TO_M,
            plan.text_size_mm * MM_TO_M,
            plan.origin_cross_size_mm * MM_TO_M,
        )
        self.origin_cross_action.setChecked(plan.show_origin_cross)
        self.set_origin_cross_visible(plan.show_origin_cross)

        self._restore_joints(plan)

        for sensor_id, mount_name in sensor_mounts:
            self.apply_sensor_mount(sensor_id, self._mount_id(mount_name))

        self._refresh_models()
        self.statusBar().showMessage(
            self.tr("Project loaded: {0} models").format(len(self._models))
        )

    # -- sensor mounting ----------------------------------------------------

    def _mount_name(self, mount_id: str | None) -> str | None:
        """A sensor mount's display name, for a file that references by name.

        A mount is a model or a joint, so both registries are asked. Ids are
        generated per session and would mean nothing after a reload (R7).
        """
        if mount_id is None:
            return None
        model = self._models.get(mount_id)
        if model is not None:
            return model.name
        joint = self._joints.get(mount_id)
        return joint.joint.name if joint is not None else None

    def _mount_id(self, name: str | None) -> str | None:
        """The other direction: a saved mount name back to an id.

        `None` when the name is not in the scene — a model whose CAD file moved,
        say. The sensor then simply sits in the scene rather than refusing to
        load, the same posture `plan_load` takes for a missing file.
        """
        if name is None:
            return None
        for model in self._models:
            if model.name == name:
                return model.model_id
        for joint in self._joints:
            if joint.joint.name == name:
                return joint.joint_id
        logger.warning("sensor mount not in the project", mount=name)
        return None

    def _mount_choices(self) -> JointChoices:
        """Everything a sensor can be mounted on: every model, then every joint.

        Models first because a geometric sensor is normally bolted to one; the
        encoders are the ones that want a joint, and they are the minority.
        """
        return tuple(
            [(entry.model_id, entry.name) for entry in self._models]
            + [(entry.joint_id, self._joint_path_label(entry.joint_id)) for entry in self._joints]
        )

    def open_sensor_mount_dialog(self) -> BindDialog | None:
        """Choose what carries the selected sensor."""
        entry = self._sensors.selected
        if entry is None:
            return None

        dialog = BindDialog(self._mount_choices(), entry.mounted_on, parent=self)
        dialog.setWindowTitle(self.tr("Mount Sensor On…"))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return dialog

        self.apply_sensor_mount(entry.sensor_id, dialog.selected_joint_id)
        return dialog

    def apply_sensor_definition(self, sensor_id: str, sensor: Sensor) -> None:
        """Store a sensor edited in the properties panel. Qt slot.

        Redraws the marker through the viewport, since the geometry it shows has
        changed. The tree is refreshed for the name and the kind; the panel is
        not, because the fields it would re-fill are the ones being typed into —
        the same reasoning as `apply_joint_definition`.
        """
        updated = self._sensors.replace_sensor(sensor_id, sensor)
        if updated is None:
            return

        update_sensor = getattr(self._viewport, "update_sensor", None)
        if update_sensor is not None:
            update_sensor(sensor_id, updated.sensor)

        self.sensor_tree.refresh(self._sensors)
        self._update_actions()
        if updated.sensor.name != sensor.name:
            self.statusBar().showMessage(
                self.tr("Name {0} is taken, using {1}").format(sensor.name, updated.sensor.name)
            )

    def apply_sensor_mount(self, sensor_id: str, mount_id: str | None) -> None:
        """Mount a sensor on a model or joint, or take it off onto the scene."""
        if not self._sensors.set_mount(sensor_id, mount_id):
            return

        set_mount = getattr(self._viewport, "set_sensor_mount", None)
        if set_mount is not None:
            set_mount(sensor_id, mount_id)
        self._refresh_sensors()

    def refresh_sensor_readings(self) -> None:
        """Copy what the scene last read into the registry, so the tree shows it.

        Pulled rather than pushed: the renderer re-reads every frame, and a
        signal per sensor per frame would be traffic for no gain. Called wherever
        something can move — which today is a joint value or a placement, since
        nothing else drives the scene yet.
        """
        reading_of = getattr(self._viewport, "sensor_reading", None)
        if reading_of is None:
            return

        changed = False
        for entry in self._sensors:
            reading = reading_of(entry.sensor_id)
            if reading is None:
                continue
            if self._sensors.set_reading(entry.sensor_id, reading):
                changed = True

        if not changed:
            return

        self.sensor_tree.refresh(self._sensors)
        # The panel's two read-only rows follow as well, but nothing above them
        # does: a reading arrives far more often than a sensor is edited.
        shown = self._sensors.get(self.properties_panel.sensor_id or "")
        if shown is not None:
            self.properties_panel.set_sensor_reading_silently(shown)

        # Offered to the connection, not sent: whether anything leaves this
        # process is the source's decision, and only when writing was allowed.
        published = False
        for entry in self._sensors:
            if entry.sensor.variable and self._connection.publish(
                entry.sensor.variable, entry.reading.value
            ):
                published = True
        if published:
            self._refresh_variables_view()

    # -- restoring the joints -----------------------------------------------

    def _joint_name(self, joint_id: str | None) -> str | None:
        """A joint's display name, for a file that references joints by name.

        Ids are generated per session and would mean nothing after a reload —
        the same reason `selected` records a model by name (R7).
        """
        if joint_id is None:
            return None
        entry = self._joints.get(joint_id)
        return entry.joint.name if entry is not None else None

    def _restore_joints(self, plan: LoadPlan) -> None:
        """Rebuild the joints, their hierarchy, the bindings and the values.

        Two passes over the joints: every one is created first, then the parents
        are applied. That way the file's joint order need not be topological — a
        parent may legitimately be written after its child.

        A `parent` or `bound_to` naming something the file does not contain is
        logged and skipped, not raised: the rest of the scene is still worth
        opening, the same posture `plan_load` takes for a moved CAD file.
        """
        add_joint = getattr(self._viewport, "add_joint", None)
        set_parent = getattr(self._viewport, "set_joint_parent", None)

        by_name: dict[str, str] = {}
        set_axes_visible = getattr(self._viewport, "set_axes_visible", None)
        set_name_visible = getattr(self._viewport, "set_joint_name_visible", None)
        set_joint_color = getattr(self._viewport, "set_joint_color", None)

        for spec in plan.joints:
            entry = self._joints.add(spec.to_joint(), select=False)
            by_name[spec.name] = entry.joint_id
            if add_joint is not None:
                add_joint(entry.joint_id, entry.joint, None)

            self._joints.set_axes_visible(entry.joint_id, spec.show_axes)
            if set_axes_visible is not None:
                set_axes_visible(entry.joint_id, spec.show_axes)

            self._joints.set_name_visible(entry.joint_id, spec.show_name)
            if set_name_visible is not None:
                set_name_visible(entry.joint_id, spec.show_name)

            color = None if spec.color is None else spec.color.to_color()
            self._joints.set_color(entry.joint_id, color)
            if set_joint_color is not None:
                set_joint_color(entry.joint_id, color)

        for spec in plan.joints:
            if spec.parent is None:
                continue
            joint_id = by_name.get(spec.name)
            parent_id = by_name.get(spec.parent)
            if joint_id is None or parent_id is None:
                logger.warning(
                    "joint parent not in the project",
                    joint=spec.name,
                    parent=spec.parent,
                )
                continue
            self._joints.set_parent(joint_id, parent_id)
            if set_parent is not None:
                set_parent(joint_id, parent_id)

        self._restore_bindings(plan, by_name)

        # Values last: a value moves a joint along the frame its parent gives it,
        # so the hierarchy has to be standing before any of them are applied.
        for spec in plan.joints:
            joint_id = by_name.get(spec.name)
            if joint_id is None:
                continue
            self.apply_joint_value(joint_id, spec.to_value())

    def _restore_bindings(self, plan: LoadPlan, by_name: dict[str, str]) -> None:
        """Bind each model the file bound, and put its anchor back.

        A model whose CAD file has moved is absent from the registry, so its
        binding is simply skipped — `plan_load` already dropped it.
        """
        set_anchor = getattr(self._viewport, "set_anchor", None)
        by_model_name = {entry.name: entry.model_id for entry in self._models}

        for model_name, joint_name, anchor_spec in plan.bindings:
            model_id = by_model_name.get(model_name)
            joint_id = by_name.get(joint_name)
            if model_id is None or joint_id is None:
                logger.warning("binding target missing", model=model_name, joint=joint_name)
                continue

            anchor = anchor_spec.to_anchor()
            self._models.set_anchor(model_id, anchor)
            if set_anchor is not None:
                set_anchor(model_id, anchor)
            self.apply_binding(model_id, joint_id)

    def _scene_radius_hint(self) -> float:
        """Rough scene size, used to rebuild the camera's zoom limits.

        Limits are not stored in the project: they follow from how big the scene
        is, and that is decided by whatever was just loaded.
        """
        camera = getattr(self._viewport, "camera_state", None)
        distance = getattr(camera, "distance_m", None)
        return float(distance) if distance else 1.0

    def close_all_models(self) -> None:
        """Empty the scene. Does not touch the current project file name.

        Also drops every sensor and joint: `viewport.clear()` already removes
        their markers (neither has anything to react to, or attach onto, once
        every model is gone), so leaving them in a dock would make it disagree
        with the scene.
        """
        if self._placement_dialog is not None:
            self._placement_dialog.close()
        if self._joint_dialog is not None:
            self._joint_dialog.close()
        if self._values_panel is not None:
            self._values_panel.close()

        clear = getattr(self._viewport, "clear", None)
        if clear is not None:
            clear()
        self._models.clear()
        self._sensors.clear()
        self._joints.clear()
        self._refresh_models()
        self._refresh_sensors()

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
        self.model_tree = ModelTree(self)
        self.model_tree.model_selected.connect(self.select_model)
        self.model_tree.joint_selected.connect(self.select_joint)
        self.model_tree.model_double_clicked.connect(self.open_values_panel)
        self.model_tree.add_requested.connect(self.open_file_dialog)
        self.model_tree.rename_requested.connect(self.rename_selected_model)
        self.model_tree.placement_requested.connect(self.open_placement_dialog)
        self.model_tree.remove_requested.connect(self.remove_selected_model)
        # `partial` rather than a lambda: a lambda closing over the loop-free
        # constant reads the same, but these two differ only by their argument
        # and naming the argument at the call site is clearer than two bodies.
        self.model_tree.add_axis_requested.connect(
            partial(self.open_add_joint_dialog, ModelJointKind.AXIS)
        )
        self.model_tree.add_trajectory_requested.connect(
            partial(self.open_add_joint_dialog, ModelJointKind.TRAJECTORY)
        )
        self.model_tree.bind_requested.connect(self.open_bind_dialog)
        self.model_tree.set_joint_parent_requested.connect(self.open_joint_parent_dialog)
        self.model_tree.edit_values_requested.connect(self.open_values_panel)
        self.model_tree.edit_joint_requested.connect(self.open_edit_joint_dialog)
        self.model_tree.remove_joint_requested.connect(self.remove_selected_joint)
        self.model_tree.visibility_toggled.connect(self.apply_visibility)
        self.model_tree.axes_visibility_toggled.connect(self.apply_axes_visibility)
        self.model_tree.name_visibility_toggled.connect(self.apply_name_visibility)
        self.model_tree.color_requested.connect(self.open_color_dialog)
        self.model_tree.color_reset_requested.connect(self.reset_color)
        self.model_tree.highlight_color_requested.connect(self.open_highlight_color_dialog)
        self.model_tree.highlight_color_reset_requested.connect(self.reset_highlight_color)

        dock = QDockWidget(self.tr("Models"), self)
        dock.setObjectName("model-dock")
        dock.setWidget(self.model_tree)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.model_dock = dock

    def _build_sensor_dock(self) -> None:
        self.sensor_tree = SensorTree(self)
        self.sensor_tree.sensor_selected.connect(self.select_sensor)
        self.sensor_tree.add_requested.connect(self.open_sensor_dialog)
        self.sensor_tree.edit_requested.connect(self.edit_selected_sensor)
        self.sensor_tree.remove_requested.connect(self.remove_selected_sensor)

        dock = QDockWidget(self.tr("Sensors"), self)
        dock.setObjectName("sensor-dock")
        dock.setWidget(self.sensor_tree)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        # Under the models rather than tabbed with them: a sensor reacts to where
        # the models are, so both lists want to be readable at once.
        self.splitDockWidget(self.model_dock, dock, Qt.Orientation.Vertical)
        self.sensor_dock = dock

    def _build_variable_dock(self) -> None:
        self.variable_tree = VariableTree(self)
        self.variable_tree.variable_selected.connect(self.select_variable)
        self.variable_tree.assign_requested.connect(self.open_assign_tag_dialog)
        self.variable_tree.clear_requested.connect(self.clear_variable_tag)
        self.variable_tree.connect_requested.connect(self.connect_to_server)
        self.variable_tree.settings_requested.connect(self.open_connection_dialog)
        self.variable_tree.applied_changed.connect(self.set_variable_applied)

        dock = QDockWidget(self.tr("Variables"), self)
        dock.setObjectName("variable-dock")
        dock.setWidget(self.variable_tree)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        # Tabbed with the sensors rather than given a slot of its own: the two
        # are read at different moments — you place sensors, then you wire them
        # up — and a third permanent panel would cost more than it gives.
        self.tabifyDockWidget(self.sensor_dock, dock)
        self.sensor_dock.raise_()
        self.variable_dock = dock

    def _build_properties_dock(self) -> None:
        self.properties_panel = PropertiesPanel(self)
        self.properties_panel.name_edited.connect(self.apply_model_name)
        self.properties_panel.placement_edited.connect(self.apply_model_placement)
        self.properties_panel.joint_value_edited.connect(self.apply_joint_value)
        self.properties_panel.joint_edited.connect(self.apply_joint_definition)
        self.properties_panel.sensor_edited.connect(self.apply_sensor_definition)
        self.properties_panel.sensor_mount_edited.connect(self.apply_sensor_mount)

        dock = QDockWidget(self.tr("Properties"), self)
        dock.setObjectName("properties-dock")
        dock.setWidget(self.properties_panel)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.properties_dock = dock

    def _refresh_properties(self) -> None:
        """Show whatever is selected: a joint, a sensor, a model, or nothing.

        The three registries hold at most one selection between them — selecting
        in any of the trees clears the other two — so the order here only decides
        what happens in the instant one is being handed over to the next.
        """
        joint_entry = self._joints.selected
        if joint_entry is not None:
            parent = joint_entry.parent_joint_id
            self.properties_panel.show_joint(
                joint_entry, None if parent is None else self._joint_path_label(parent)
            )
            return

        sensor_entry = self._sensors.selected
        if sensor_entry is not None:
            self.properties_panel.show_sensor(
                sensor_entry,
                self._mount_choices(),
                self._mount_name(sensor_entry.mounted_on),
            )
            return

        entry = self._models.selected
        if entry is None:
            self.properties_panel.clear()
            return

        self.properties_panel.show_model(
            entry,
            self._driving_joints(entry),
            self._bound_target_name(entry),
        )

    def _driving_joints(self, entry: ModelEntry) -> tuple[JointEntry, ...]:
        """The joints that move this model, outermost first.

        `ancestors_of` already includes the joint the model is bound to and
        returns the chain nearest-first; reversed, it reads the way the machine
        is built — the rail, then what it carries, then the tool.
        """
        if entry.bound_to_joint_id is None:
            return ()
        return tuple(reversed(self._joints.ancestors_of(entry.bound_to_joint_id)))

    def _bound_target_name(self, entry: ModelEntry) -> str | None:
        """What the model is bound to, as the panel shows it.

        The full path (`rail / head`) rather than the bare name, so two joints
        called the same under different parents can be told apart.
        """
        if entry.bound_to_joint_id is None:
            return None
        if self._joints.get(entry.bound_to_joint_id) is None:
            return None
        return self._joint_path_label(entry.bound_to_joint_id)

    @property
    def models(self) -> ModelRegistry:
        """The loaded models. The tree renders this; nothing else owns it."""
        return self._models

    @property
    def selected_model(self) -> ModelEntry | None:
        """The model every action applies to, or `None` when nothing is picked."""
        return self._models.selected

    def select_model(self, model_id: str | None) -> None:
        """Set the selection, highlight it in the scene and report it.

        Selecting a model clears the joint and sensor selections: the properties
        panel has one subject, and a model, a joint and a sensor are three
        different ones. All three registries are asked to change so that none is
        left claiming a selection another has taken.
        """
        model_changed = self._models.select(model_id)
        joint_was_selected = self._joints.select(None)
        sensor_was_selected = self._deselect_sensor()
        if not (model_changed or joint_was_selected or sensor_was_selected):
            return

        selected = self._models.selected_id
        # Back into the tree as well: the selection also changes from code — the
        # neighbour after a removal, or a programmatic select — and a row left
        # highlighted that nothing else considers selected is worse than none.
        self.model_tree.select_id(selected)

        set_highlight = getattr(self._viewport, "set_highlight", None)
        if set_highlight is not None:
            set_highlight(selected)
        self._mark_joint(None)

        # A placement dialog belongs to the model it was opened for.
        if self._placement_dialog is not None and self._placement_model_id != selected:
            self._placement_dialog.close()

        self._update_actions()
        self._refresh_properties()

        entry = self._models.selected
        if entry is not None:
            self.statusBar().showMessage(self.tr("Selected {0}").format(entry.name))

    def rename_selected_model(self) -> str | None:
        """Ask for a new name for the selected model. Returns the name used.

        The registry may hand back a different name than the one typed — names
        are made unique — so the status bar says which one it settled on.
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
            self.statusBar().showMessage(
                self.tr("Name {0} is taken, using {1}").format(name.strip(), updated.name)
            )
            return updated.name

        self.statusBar().showMessage(self.tr("Renamed {0} to {1}").format(entry.name, updated.name))
        return updated.name

    def remove_selected_model(self) -> None:
        """Remove the selected model from the scene and the tree."""
        entry = self._models.selected
        if entry is None:
            return

        remove_model = getattr(self._viewport, "remove_model", None)
        if remove_model is not None:
            remove_model(entry.model_id)

        # A values window belongs to the model it was opened for.
        if self._values_panel is not None and self._values_panel.model_id == entry.model_id:
            self._values_panel.close()

        self._models.remove(entry.model_id)

        if self._placement_model_id == entry.model_id and self._placement_dialog is not None:
            self._placement_dialog.close()

        self._refresh_models()
        self.statusBar().showMessage(self.tr("Removed {0}").format(entry.name))

    def _refresh_models(self) -> None:
        """Re-render the tree and re-apply what depends on the selection."""
        self.model_tree.refresh(self._models, self._joints)

        set_highlight = getattr(self._viewport, "set_highlight", None)
        if set_highlight is not None:
            set_highlight(self._models.selected_id)
        self._mark_joint(self._joints.selected_id)

        self._update_actions()
        self._refresh_properties()
        self.refresh_variables()

    def _mark_joint(self, joint_id: str | None) -> None:
        """Put the cross on the selected joint's initial frame, or clear it."""
        mark = getattr(self._viewport, "set_joint_highlight", None)
        if mark is not None:
            mark(joint_id)

    def _update_actions(self) -> None:
        """Enable only what the current selection makes possible.

        Greyed out rather than left to fail: an entry that reports "select a
        model first" after the click has already told the user too late.
        """
        model = self._models.selected
        has_selection = model is not None
        self.placement_action.setEnabled(has_selection)
        self.rename_action.setEnabled(has_selection)
        self.remove_action.setEnabled(has_selection)
        self.fit_action.setEnabled(not self._models.is_empty)

        # Binding needs something to bind *to*; driving needs the model to
        # already have something moving it.
        self.bind_action.setEnabled(has_selection and not self._joints.is_empty)
        self.values_action.setEnabled(model is not None and bool(self._driving_joints(model)))

        has_joint_selection = self._joints.selected is not None
        self.edit_joint_action.setEnabled(has_joint_selection)
        self.joint_parent_action.setEnabled(has_joint_selection)
        self.remove_joint_action.setEnabled(has_joint_selection)

        has_sensor_selection = self._sensors.selected is not None
        self.edit_sensor_action.setEnabled(has_sensor_selection)
        self.mount_sensor_action.setEnabled(has_sensor_selection)
        self.remove_sensor_action.setEnabled(has_sensor_selection)

        has_variable = self._selected_variable is not None
        self.assign_tag_action.setEnabled(has_variable)
        self.clear_tag_action.setEnabled(has_variable)
        self.connect_action.setEnabled(not self._connection.is_connected)
        self.disconnect_action.setEnabled(self._connection.is_connected)

    # -- variables ------------------------------------------------------------

    @property
    def variables(self) -> VariableRegistry:
        """The project's variables. The tab renders this; nothing else owns it."""
        return self._variables

    @property
    def connection(self) -> ConnectionController:
        """The OPC UA connection. Observable state, not a secret."""
        return self._connection

    @property
    def selected_variable(self) -> str | None:
        return self._selected_variable

    def select_variable(self, name: str | None) -> None:
        """Set the variable selection. Independent of the other three.

        Not exclusive with the models, joints and sensors: a variable is not a
        thing in the scene, and the properties dock never shows one — selecting
        a row here should not clear what is being looked at over there.
        """
        if name == self._selected_variable:
            return
        self._selected_variable = name
        self.variable_tree.select_variable(name)
        self._update_actions()

    def refresh_variables(self) -> None:
        """Rebuild the variable list from what the scene now mentions.

        Derived rather than stored: adding an axis called `X` adds `X`, and
        renaming its variable leaves the old tag behind — which is honest, since
        the tag was assigned to a name and that name is gone.
        """
        self._variables.set_tags(dict(self._connection_settings.tags))
        self._variables.set_sources(self._variable_sources())
        self._refresh_variables_view()

    def _variable_sources(self) -> tuple[VariableSource, ...]:
        """Every variable the scene names, joints first then sensors.

        A joint reads — the PLC decides where the machine is. A sensor writes:
        its reading is something this application produces.
        """
        sources: list[VariableSource] = []
        for joint in self._joints:
            if joint.joint.variable:
                sources.append(
                    VariableSource(
                        name=joint.joint.variable,
                        direction=BindingDirection.READ,
                        owner=self.tr("axis or trajectory {0}").format(joint.joint.name),
                    )
                )
        for sensor in self._sensors:
            if sensor.sensor.variable:
                sources.append(
                    VariableSource(
                        name=sensor.sensor.variable,
                        direction=BindingDirection.WRITE,
                        owner=self.tr("sensor {0}").format(sensor.sensor.name),
                    )
                )
        return tuple(sources)

    def _on_values_changed(self) -> None:
        """New values arrived. Move what they drive, then redraw the table.

        In that order, and this is the whole point of reading them: a value in
        the registry that the scene does not follow is a number in a table.
        """
        self._drive_joints_from_variables()
        self._refresh_variables_view()

    def _refresh_variables_view(self) -> None:
        self.variable_tree.refresh(self._variables)
        self._update_actions()

    def _drive_joints_from_variables(self) -> None:
        """Move every joint whose variable has a value it is allowed to follow.

        The sensors are re-read **once** at the end rather than per joint: every
        sensor is evaluated against every model (R16), and doing that per axis on
        every notification is the same work several times over.
        """
        moved = False
        for entry in self._variables:
            if entry.direction is not BindingDirection.READ or entry.value is None:
                continue
            if not entry.is_applied:
                continue
            joint_ids = self._joint_ids_for_variable(entry.name)
            if not joint_ids:
                # A sensor's variable, or one whose joint was renamed since. It
                # still arrives and is still shown; there is nothing to drive.
                continue
            # Every joint naming it, not the first: one variable driving two
            # joints is legitimate and `VariableRegistry.set_sources` already
            # says so — two axes moving together off one PLC value.
            out_of_range = False
            for joint_id in joint_ids:
                out_of_range = self._drive_one_joint(joint_id, entry.value) or out_of_range
            self._variables.set_out_of_range(entry.name, out_of_range)
            moved = True

        if moved:
            self.refresh_sensor_readings()

    def _drive_one_joint(self, joint_id: str, value: float) -> bool:
        """One joint, clamped into what it can actually reach. Returns whether it
        had to be clamped.

        A value outside the limits is **not** dropped and not passed through: the
        joint goes to its limit, which is where the real machine would be, and
        the caller marks the row so the number that arrived can be seen in red.
        Silently flattening it would hide a PLC sending millimetres where metres
        were expected, which is the most likely cause of it.
        """
        entry = self._joints.get(joint_id)
        if entry is None:  # pragma: no cover - the registry was just asked
            return False
        low, high = effective_limits(entry.joint)
        clamped, was_clamped = clamp(low, high, value)
        self._set_joint_value(joint_id, clamped)
        return was_clamped

    def _joint_ids_for_variable(self, variable: str) -> tuple[str, ...]:
        """Every joint this variable drives. Empty when no joint claims it.

        Searched rather than indexed: a scene has a handful of joints, and an
        index would have to be rebuilt everywhere a joint is added, renamed or
        removed — the one that got forgotten would leave an axis not following
        its own variable, which is exactly the fault this method exists to fix.
        """
        return tuple(entry.joint_id for entry in self._joints if entry.joint.variable == variable)

    def set_variable_applied(self, variable: str, is_applied: bool) -> None:
        """Switch whether arriving values move the model. Qt slot for the table.

        Turning it back on does not replay the last value: the next notification
        carries it, and reaching backwards for one that has already been declined
        would make the switch mean two different things.
        """
        if not self._variables.set_applied(variable, is_applied):
            return
        self._refresh_variables_view()

    # -- the connection ---------------------------------------------------------

    def connect_to_server(self) -> bool:
        """Subscribe to every variable that has a tag. Returns whether it started.

        A refusal is a status-bar message, not a modal: "nothing has a tag yet"
        is a normal state of a project that has not been wired up.
        """
        refusal = self._connection.connect_to(self._connection_settings, self._session_password)
        if refusal is not None:
            self.statusBar().showMessage(refusal)
            self._update_actions()
            return False

        self.statusBar().showMessage(
            self.tr("Connecting to {0}…").format(self._connection_settings.endpoint)
        )
        self._update_actions()
        return True

    def disconnect_from_server(self) -> None:
        """Stop reading. The scene keeps the last state it had (R10)."""
        self._connection.disconnect_from_server()
        self.statusBar().showMessage(self.tr("Disconnected"))
        self._update_actions()

    def _on_connection_status(self, status: object) -> None:
        """The controller's status changed. Only the actions follow it — a status
        bar rewritten ten times a second is unreadable.

        The reason is not spelled out in the message area either: it belongs in
        `Communication → Diagnostics…`, where it can be read rather than
        glimpsed before the next message overwrites it. The indicator carries it
        as a tooltip and opens that log when clicked.
        """
        _ = status
        self._refresh_connection_indicator()
        self._update_actions()

    def open_diagnostics_dialog(self) -> DiagnosticsDialog:
        """What the last attempt tried, and where it stopped.

        Reachable without reopening the connection dialog, because the question
        "why am I not connected" comes up long after that dialog was closed.
        """
        dialog = DiagnosticsDialog(self._connection.diagnostics, parent=self)
        dialog.exec()
        return dialog

    def open_connection_dialog(self) -> ConnectionDialog:
        """Where the server is, how to get in, and what it turned out to hold."""
        dialog = ConnectionDialog(self._connection_settings, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return dialog

        # The password is kept **here**, apart from the settings that get saved.
        self._session_password = dialog.password
        self.save_connection_settings(dialog.settings)
        self.statusBar().showMessage(
            self.tr("Server: {0}").format(self._connection_settings.describe())
        )
        return dialog

    def open_assign_tag_dialog(self) -> AssignTagDialog | None:
        """Pick the node the selected variable reads from."""
        name = self._selected_variable
        if name is None:
            return None

        dialog = AssignTagDialog(
            name,
            self._connection_settings.endpoint,
            self._connection_settings.tag_for(name),
            self._connection_settings.credentials(self._session_password),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return dialog

        self.save_connection_settings(self._connection_settings.with_tag(name, dialog.tag))
        self.statusBar().showMessage(
            self.tr("{0} is unbound").format(name)
            if dialog.tag is None
            else self.tr("{0} reads {1}").format(name, dialog.tag.node_id)
        )
        return dialog

    def clear_variable_tag(self) -> None:
        """Leave the selected variable bound to nothing."""
        name = self._selected_variable
        if name is None or self._connection_settings.tag_for(name) is None:
            return
        self.save_connection_settings(self._connection_settings.with_tag(name, None))
        self.statusBar().showMessage(self.tr("{0} is unbound").format(name))

    # -- sensors --------------------------------------------------------------

    @property
    def sensors(self) -> SensorRegistry:
        """The placed sensors. The tree renders this; nothing else owns it."""
        return self._sensors

    @property
    def selected_sensor(self) -> SensorEntry | None:
        return self._sensors.selected

    def select_sensor(self, sensor_id: str | None) -> None:
        """Set the sensor selection, reflect it back into the tree, show it.

        Clears the model and joint selections — the properties panel shows one
        thing, and a sensor picked in its own dock is now that thing.
        """
        sensor_changed = self._sensors.select(sensor_id)
        others_were_selected = False
        if sensor_id is not None:
            others_were_selected = self._models.select(None) or self._joints.select(None)
            if others_were_selected:
                self._reflect_cleared_selection()
        if not (sensor_changed or others_were_selected):
            return
        self.sensor_tree.select_id(self._sensors.selected_id)
        self._update_actions()
        self._refresh_properties()

    def _deselect_sensor(self) -> bool:
        """Drop any sensor selection and take the highlight out of its tree.

        Returns whether there was one, so the caller can tell a no-op selection
        change from a real one.
        """
        if not self._sensors.select(None):
            return False
        self.sensor_tree.select_id(None)
        return True

    def _reflect_cleared_selection(self) -> None:
        """Take the model highlight and the joint cross out of the scene.

        Called when a sensor takes the selection over: the registries have
        already been cleared, and the scene has to be told, or a wireframe box
        stays round a model nothing considers selected any more (R6).
        """
        self.model_tree.select_id(None)
        self.model_tree.select_joint_id(None)
        set_highlight = getattr(self._viewport, "set_highlight", None)
        if set_highlight is not None:
            set_highlight(None)
        self._mark_joint(None)

    def open_sensor_dialog(self) -> None:
        """Place a new sensor. Modal, unlike the placement dialog: it does not
        exist in the scene yet, so there is nothing to preview.
        """
        dialog = SensorDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        entry = self._sensors.add(dialog.sensor)
        add_sensor = getattr(self._viewport, "add_sensor", None)
        if add_sensor is not None:
            add_sensor(entry.sensor_id, entry.sensor, entry.mounted_on)

        self._refresh_sensors()
        self.statusBar().showMessage(self.tr("Added sensor {0}").format(entry.sensor.name))

    def edit_selected_sensor(self) -> None:
        """Open the dialog pre-filled with the selected sensor's fields."""
        entry = self._sensors.selected
        if entry is None:
            return

        dialog = SensorDialog(entry.sensor, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        updated = self._sensors.replace_sensor(entry.sensor_id, dialog.sensor)
        if updated is None:
            return

        update_sensor = getattr(self._viewport, "update_sensor", None)
        if update_sensor is not None:
            update_sensor(entry.sensor_id, updated.sensor)

        self._refresh_sensors()
        self.statusBar().showMessage(self.tr("Updated sensor {0}").format(updated.sensor.name))

    def remove_selected_sensor(self) -> None:
        """Remove the selected sensor from the scene and the tree."""
        entry = self._sensors.selected
        if entry is None:
            return

        remove_sensor = getattr(self._viewport, "remove_sensor", None)
        if remove_sensor is not None:
            remove_sensor(entry.sensor_id)

        self._sensors.remove(entry.sensor_id)
        self._refresh_sensors()
        self.statusBar().showMessage(self.tr("Removed sensor {0}").format(entry.sensor.name))

    def _refresh_sensors(self) -> None:
        """Re-render the sensor tree and re-apply what depends on the selection.

        Includes the properties panel, the way `_refresh_models` does: adding a
        sensor selects it, and a selection nothing showed would leave the dock
        claiming the scene is empty while the tree says otherwise.
        """
        self.sensor_tree.refresh(self._sensors)
        self._update_actions()
        self._refresh_properties()
        self.refresh_variables()

    # -- joints ---------------------------------------------------------------

    @property
    def joints(self) -> JointRegistry:
        """The placed joints. The tree renders this; nothing else owns it."""
        return self._joints

    @property
    def selected_joint(self) -> JointEntry | None:
        return self._joints.selected

    def select_joint(self, joint_id: str | None) -> None:
        """Set the joint selection, mark its frame and show its properties.

        Clears any selected sensor for the same reason `select_model` does: the
        properties panel shows one thing.
        """
        joint_changed = self._joints.select(joint_id)
        sensor_was_selected = self._deselect_sensor()
        if not (joint_changed or sensor_was_selected):
            return
        self.model_tree.select_joint_id(self._joints.selected_id)
        self._mark_joint(self._joints.selected_id)
        self._update_actions()
        self._refresh_properties()

    def open_add_joint_dialog(self, kind: ModelJointKind) -> JointDialog | None:
        """Add an axis or a trajectory, under the selected joint if there is one.

        Modeless, so the scene stays movable while the numbers are typed and the
        live preview is worth watching — the same reason the placement dialog is.
        """
        if self._joint_dialog is not None:
            self._joint_dialog.close()

        parent_entry = self._joints.selected
        parent_joint_id = None if parent_entry is None else parent_entry.joint_id

        dialog = JointDialog(initial_kind=kind, parent=self)
        if parent_entry is not None:
            dialog.setWindowTitle(f"{dialog.windowTitle()} - under {parent_entry.joint.name}")
        self._show_joint_dialog(dialog, None, parent_joint_id)
        return dialog

    def open_edit_joint_dialog(self) -> JointDialog | None:
        """Open the dialog pre-filled with the selected joint's own geometry."""
        entry = self._joints.selected
        if entry is None:
            return None

        if self._joint_dialog is not None:
            self._joint_dialog.close()

        dialog = JointDialog(entry.joint, parent=self)
        dialog.setWindowTitle(f"{dialog.windowTitle()} - {entry.joint.name}")
        self._show_joint_dialog(dialog, entry.joint_id, entry.parent_joint_id)
        return dialog

    def _show_joint_dialog(
        self, dialog: JointDialog, joint_id: str | None, parent_joint_id: str | None
    ) -> None:
        """Wire one joint dialog up and show it.

        `joint_id` is `None` when adding — that is what tells `accepted` whether
        to create a joint or replace one.
        """
        self._joint_dialog = dialog
        self._joint_dialog_joint_id = joint_id
        self._joint_dialog_parent_id = parent_joint_id
        dialog.joint_previewed.connect(self._on_joint_previewed)
        dialog.pick_requested.connect(self._on_joint_pick_requested)
        dialog.accepted.connect(self._on_joint_dialog_accepted)
        dialog.finished.connect(self._on_joint_dialog_closed)
        dialog.show()

    def _on_joint_previewed(self, joint: ModelJoint) -> None:
        """Draw the joint being typed, in the frame it will end up in."""
        preview = getattr(self._viewport, "preview_joint", None)
        if preview is not None:
            preview(joint, self._joint_dialog_parent_id)

    def _on_joint_pick_requested(self, field: PickTarget) -> None:
        """Let the user click a point on the selected model for one of the fields.

        The point comes back in the **joint's** frame, not the model's: that is
        what the dialog's numbers mean, so converting here saves the user doing
        it in their head.
        """
        if self._joint_dialog is None:
            return

        entry = self._models.selected
        if entry is None:
            self.statusBar().showMessage(self.tr("Select a model first to pick a point on it"))
            return

        begin_pick = getattr(self._viewport, "begin_pick_in_joint_frame", None)
        if begin_pick is None:
            return

        dialog = self._joint_dialog

        def on_point_picked(point: Vec3) -> None:
            dialog.set_point(field, point)

        begin_pick(entry.model_id, self._joint_dialog_parent_id, on_point_picked)

    def _on_joint_dialog_accepted(self) -> None:
        """OK on the joint dialog: add a new joint, or replace the edited one."""
        if self._joint_dialog is None:
            return

        joint = self._joint_dialog.joint
        if self._joint_dialog_joint_id is None:
            entry = self._joints.add(joint, self._joint_dialog_parent_id)
            add_joint = getattr(self._viewport, "add_joint", None)
            if add_joint is not None:
                add_joint(entry.joint_id, entry.joint, entry.parent_joint_id)
            self.statusBar().showMessage(self.tr("Added joint {0}").format(entry.joint.name))
        else:
            updated = self._joints.replace_joint(self._joint_dialog_joint_id, joint)
            if updated is None:
                return
            update_joint = getattr(self._viewport, "update_joint", None)
            if update_joint is not None:
                update_joint(updated.joint_id, updated.joint)
            self.statusBar().showMessage(self.tr("Updated joint {0}").format(updated.joint.name))

        self._refresh_models()

    def _on_joint_dialog_closed(self, _result: int) -> None:
        """Drop the preview and any armed pick when the dialog goes away.

        Unconditional: the dialog may close by OK, by Cancel or by the window
        button, and a marker or an armed ray left behind outlives what asked
        for it.
        """
        clear_preview = getattr(self._viewport, "clear_joint_preview", None)
        if clear_preview is not None:
            clear_preview()
        cancel_pick = getattr(self._viewport, "cancel_pick", None)
        if cancel_pick is not None:
            cancel_pick()

        self._joint_dialog = None
        self._joint_dialog_joint_id = None
        self._joint_dialog_parent_id = None

    def remove_selected_joint(self) -> None:
        """Remove the selected joint. Anything bound to it is released."""
        entry = self._joints.selected
        if entry is None:
            return

        remove_joint = getattr(self._viewport, "remove_joint", None)
        if remove_joint is not None:
            remove_joint(entry.joint_id)

        # Released rather than removed with it: the model is still a model, it
        # just no longer has anything moving it.
        for other in self._models:
            if other.bound_to_joint_id == entry.joint_id:
                self._models.bind(other.model_id, None)

        self._joints.remove(entry.joint_id)
        self._refresh_models()
        self.statusBar().showMessage(self.tr("Removed joint {0}").format(entry.joint.name))

    def open_bind_dialog(self) -> BindDialog | None:
        """Attach the selected model to a joint, or release it."""
        entry = self._models.selected
        if entry is None:
            return None

        if self._joints.is_empty:
            self.statusBar().showMessage(self.tr("Add an axis or a trajectory first"))
            return None

        dialog = BindDialog(self._joint_choices(), entry.bound_to_joint_id, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return dialog

        self.apply_binding(entry.model_id, dialog.selected_joint_id)
        return dialog

    # -- what is drawn ------------------------------------------------------

    def apply_visibility(self, is_visible: bool) -> None:
        """Show or hide the selected model. Qt slot for the tree's `Visible` tick.

        Only a model can be hidden, so a joint row's toggle has nothing to act
        on — the menu does not offer it there in the first place.
        """
        model_id = self._models.selected_id
        if model_id is None:
            return
        if not self._models.set_visible(model_id, is_visible):
            return

        set_visible = getattr(self._viewport, "set_model_visible", None)
        if set_visible is not None:
            set_visible(model_id, is_visible)

        entry = self._models.get(model_id)
        name = entry.name if entry is not None else model_id
        self._refresh_models()
        self.statusBar().showMessage(
            self.tr("Showing {0}").format(name)
            if is_visible
            else self.tr("Hiding {0}").format(name)
        )

    def apply_axes_visibility(self, show_axes: bool) -> None:
        """Turn the selection cross on the selected row on or off.

        One slot for both kinds because the menu entry is one entry: the joint
        row's selection and the model row's are mutually exclusive in a
        single-selection tree, so whichever is set is the one that was clicked.
        """
        joint_id = self._joints.selected_id
        model_id = self._models.selected_id
        item_id = joint_id if joint_id is not None else model_id
        if item_id is None:
            return

        if joint_id is not None:
            self._joints.set_axes_visible(joint_id, show_axes)
        else:
            self._models.set_axes_visible(item_id, show_axes)

        set_axes_visible = getattr(self._viewport, "set_axes_visible", None)
        if set_axes_visible is not None:
            set_axes_visible(item_id, show_axes)
        self._refresh_models()

    def apply_name_visibility(self, show_name: bool) -> None:
        """Show or hide the selected joint's name. Qt slot for the tree.

        Only a joint has a name label, so a model row's menu does not offer it.
        """
        joint_id = self._joints.selected_id
        if joint_id is None:
            return
        if not self._joints.set_name_visible(joint_id, show_name):
            return

        set_name_visible = getattr(self._viewport, "set_joint_name_visible", None)
        if set_name_visible is not None:
            set_name_visible(joint_id, show_name)
        self._refresh_models()

    def set_names_visible(self, show_names: bool) -> None:
        """The scene-wide name switch. Qt slot for the Scene menu.

        Each joint keeps its own flag, so turning everything back on restores
        exactly what was showing before rather than every label at once.
        """
        set_names_visible = getattr(self._viewport, "set_names_visible", None)
        if set_names_visible is not None:
            set_names_visible(show_names)

        self.statusBar().showMessage(
            self.tr("Showing joint names") if show_names else self.tr("Hiding joint names")
        )

    def open_color_dialog(self) -> QColor | None:
        """Pick a colour for the selected row. Returns what was chosen, or
        `None` if the dialog was cancelled.

        Returned rather than kept private so a test can see the choice without
        driving a modal — the same reason `build_context_menu` is public.
        """
        target = self._color_target()
        if target is None:
            return None

        item_id, current = target
        initial = _to_qcolor(current) if current is not None else QColor(255, 255, 255)
        chosen = QColorDialog.getColor(initial, self, self.tr("Pick a Colour"))
        if not chosen.isValid():
            return None

        self.apply_color(item_id, _from_qcolor(chosen))
        return chosen

    def reset_color(self) -> None:
        """Drop the selected row's colour override. Qt slot for the tree."""
        target = self._color_target()
        if target is None:
            return
        self.apply_color(target[0], None)

    def apply_color(self, item_id: str, color: Rgba | None) -> None:
        """Recolour one model or joint, or clear its override with `None`.

        One entry point for both kinds: the ids come from different registries
        and never collide, and whichever one holds this id is the one to change.
        """
        if self._joints.get(item_id) is not None:
            self._joints.set_color(item_id, color)
            set_joint_color = getattr(self._viewport, "set_joint_color", None)
            if set_joint_color is not None:
                set_joint_color(item_id, color)
        elif self._models.get(item_id) is not None:
            self._models.set_color(item_id, color)
            set_model_color = getattr(self._viewport, "set_model_color", None)
            if set_model_color is not None:
                set_model_color(item_id, color)

        self._refresh_models()

    def _color_target(self) -> tuple[str, Rgba | None] | None:
        """The row a colour action applies to, with its current override.

        A selected joint wins over a selected model for the same reason the
        cross toggle does: selecting a joint row is what clears the model
        selection, so whichever is set is the row that was clicked.
        """
        joint = self._joints.selected
        if joint is not None:
            return joint.joint_id, joint.color
        model = self._models.selected
        if model is not None:
            return model.model_id, model.color
        return None

    def check_collisions(self) -> frozenset[tuple[str, str]]:
        """Look for overlapping models now. Qt slot for the Scene menu.

        On demand rather than on a timer: the answer is only interesting at a
        moment the user picked, and it goes stale by itself as soon as anything
        moves — the renderer drops it then, so a red outline never describes a
        scene that has changed underneath it.

        The status bar says what was found either way. With a button, "no
        collisions" is a result, and silence would read as "nothing happened".
        """
        check = getattr(self._viewport, "check_collisions", None)
        pairs = frozenset() if check is None else check()

        if not pairs:
            self.statusBar().showMessage(self.tr("No collisions"))
            return pairs

        names = {entry.model_id: entry.name for entry in self._models}
        described = ", ".join(sorted(f"{names.get(a, a)} + {names.get(b, b)}" for a, b in pairs))
        self.statusBar().showMessage(self.tr("Colliding: {0}").format(described))
        return pairs

    def open_sizes_dialog(self) -> SizesDialog:
        """Set how big crosses and text are drawn."""
        dialog = SizesDialog(self.sizes, parent=self)
        dialog.sizes_changed.connect(self.apply_sizes)
        dialog.exec()
        return dialog

    @property
    def sizes(self) -> Sizes:
        return Sizes(
            cross_size_m=self._cross_size_m,
            text_size_m=self._text_size_m,
            origin_cross_size_m=self._origin_cross_size_m,
        )

    def apply_sizes(
        self, cross_size_m: float, text_size_m: float, origin_cross_size_m: float
    ) -> None:
        """Qt slot for the sizes dialog, live as any spin box moves."""
        self._cross_size_m = cross_size_m
        self._text_size_m = text_size_m
        self._origin_cross_size_m = origin_cross_size_m

        set_cross_size = getattr(self._viewport, "set_cross_size", None)
        if set_cross_size is not None:
            set_cross_size(cross_size_m)
        set_text_size = getattr(self._viewport, "set_text_size", None)
        if set_text_size is not None:
            set_text_size(text_size_m)
        set_origin_size = getattr(self._viewport, "set_origin_cross_size", None)
        if set_origin_size is not None:
            set_origin_size(origin_cross_size_m)

    def set_origin_cross_visible(self, visible: bool) -> None:
        """Show or hide the origin cross. Qt slot for the Scene menu.

        Only the origin cross: a selected model's and a joint's follow their own
        `show_axes` flags, which is what makes this one separate.
        """
        set_visible = getattr(self._viewport, "set_origin_cross_visible", None)
        if set_visible is not None:
            set_visible(visible)

        self.statusBar().showMessage(
            self.tr("Showing the origin cross") if visible else self.tr("Hiding the origin cross")
        )

    @property
    def cross_size_m(self) -> float:
        return self._cross_size_m

    @property
    def text_size_m(self) -> float:
        return self._text_size_m

    @property
    def origin_cross_size_m(self) -> float:
        return self._origin_cross_size_m

    def open_highlight_color_dialog(self) -> QColor | None:
        """Pick the colour the selected model is outlined in when selected."""
        entry = self._models.selected
        if entry is None:
            return None

        initial = (
            _to_qcolor(entry.highlight_color)
            if entry.highlight_color is not None
            else _to_qcolor(HIGHLIGHT_COLOR)
        )
        chosen = QColorDialog.getColor(initial, self, self.tr("Pick a Highlight Colour"))
        if not chosen.isValid():
            return None

        self.apply_highlight_color(entry.model_id, _from_qcolor(chosen))
        return chosen

    def reset_highlight_color(self) -> None:
        """Drop the selected model's highlight override. Qt slot for the tree."""
        entry = self._models.selected
        if entry is None:
            return
        self.apply_highlight_color(entry.model_id, None)

    def apply_highlight_color(self, model_id: str, color: Rgba | None) -> None:
        """Set the colour a model is outlined in, or clear the override."""
        if not self._models.set_highlight_color(model_id, color):
            return

        set_highlight_color = getattr(self._viewport, "set_highlight_color", None)
        if set_highlight_color is not None:
            set_highlight_color(model_id, color)
        self._refresh_models()

    def apply_binding(self, model_id: str, joint_id: str | None) -> None:
        """Bind a model to a joint, or release it with `None`."""
        entry = self._models.get(model_id)
        if entry is None:
            return

        self._models.bind(model_id, joint_id)
        bind_model = getattr(self._viewport, "bind_model", None)
        if bind_model is not None:
            bind_model(model_id, joint_id)

        self._refresh_models()
        if joint_id is None:
            self.statusBar().showMessage(self.tr("Released {0}").format(entry.name))
            return

        joint_entry = self._joints.get(joint_id)
        name = joint_entry.joint.name if joint_entry is not None else joint_id
        self.statusBar().showMessage(self.tr("Bound {0} to {1}").format(entry.name, name))

    def open_joint_parent_dialog(self) -> BindDialog | None:
        """Choose which joint carries the selected joint.

        Its own descendants are left out of the list rather than offered and
        then refused — that would be exactly the cycle `would_cycle` rejects.
        """
        entry = self._joints.selected
        if entry is None:
            return None

        excluded = descendants_of(self._joints, entry.joint_id) | {entry.joint_id}
        dialog = BindDialog(self._joint_choices(excluded), entry.parent_joint_id, parent=self)
        dialog.setWindowTitle(self.tr("Carried By…"))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return dialog

        parent_joint_id = dialog.selected_joint_id
        if would_cycle(self._joints, entry.joint_id, parent_joint_id):
            self.statusBar().showMessage(self.tr("That would create a loop"))
            return dialog

        if self._joints.set_parent(entry.joint_id, parent_joint_id):
            set_parent = getattr(self._viewport, "set_joint_parent", None)
            if set_parent is not None:
                set_parent(entry.joint_id, parent_joint_id)
            self._refresh_models()
        return dialog

    def _joint_choices(self, excluded: frozenset[str] = frozenset()) -> JointChoices:
        """`(joint_id, label)` pairs for a chooser, deepest path first so a
        chain reads as one thing.
        """
        return tuple(
            (entry.joint_id, self._joint_path_label(entry.joint_id))
            for entry in self._joints
            if entry.joint_id not in excluded
        )

    def _joint_path_label(self, joint_id: str) -> str:
        """`rail / head` — the chain down to this joint, so two joints with
        similar names are still tellable apart in a combo box.
        """
        chain = self._joints.ancestors_of(joint_id)
        return " / ".join(entry.joint.name for entry in reversed(chain))

    def open_values_panel(self, model_id: str | None = None) -> ModelValuesPanel | None:
        """Open the live-value panel for a model. Uses the current selection
        when `model_id` is not given — the tree's context-menu action carries
        nothing, but a double-click carries the row's own id directly.

        `None` when nothing drives the model - there is nothing to show.
        """
        resolved_id = model_id if model_id is not None else self._models.selected_id
        if resolved_id is None:
            return None
        entry = self._models.get(resolved_id)
        if entry is None:
            return None

        joint_entries = self._driving_joints(entry)
        if not joint_entries:
            return None

        if self._values_panel is not None and self._values_panel.model_id == resolved_id:
            self._values_panel.raise_()
            self._values_panel.activateWindow()
            return self._values_panel

        if self._values_panel is not None:
            self._values_panel.close()

        panel = ModelValuesPanel(resolved_id, entry.name, joint_entries, parent=self)
        panel.value_edited.connect(self.apply_joint_value)
        panel.finished.connect(self._on_values_panel_closed)
        self._values_panel = panel
        panel.show()
        return panel

    def apply_joint_value(self, joint_id: str, value: float) -> None:
        """Drive one joint's live value. Qt slot for both the properties panel
        and the floating values window.

        Both views of a joint are refreshed afterwards, including whichever one
        raised the edit — it gets the same number back, so that is a no-op, and
        not having to know the source keeps this to one code path.
        """
        self._set_joint_value(joint_id, value)
        self.refresh_sensor_readings()

    def _set_joint_value(self, joint_id: str, value: float) -> None:
        """Everything `apply_joint_value` does except re-reading the sensors.

        Split out for the one caller that moves several joints at once: the
        sensors are evaluated against every model, so once at the end beats once
        per axis on every notification from the PLC.
        """
        self._joints.set_value(joint_id, value)

        set_joint_value = getattr(self._viewport, "set_joint_value", None)
        if set_joint_value is not None:
            set_joint_value(joint_id, value)

        self.properties_panel.set_joint_value_silently(joint_id, value)
        if self._values_panel is not None:
            self._values_panel.set_value_silently(joint_id, value)

    def apply_joint_definition(self, joint_id: str, joint: ModelJoint) -> None:
        """Store an axis or trajectory edited in the properties panel. Qt slot.

        Redraws the marker through the viewport, since the geometry it shows has
        changed. The tree is refreshed for the name; the panel is not, because
        the fields it would rebuild are the ones being typed into.
        """
        updated = self._joints.replace_joint(joint_id, joint)
        if updated is None:
            return

        update_joint = getattr(self._viewport, "update_joint", None)
        if update_joint is not None:
            update_joint(joint_id, updated.joint)

        self.model_tree.refresh(self._models, self._joints)
        if updated.joint.name != joint.name:
            self.statusBar().showMessage(
                self.tr("Name {0} is taken, using {1}").format(joint.name, updated.joint.name)
            )

    def apply_model_name(self, model_id: str, name: str) -> None:
        """Rename a model from the properties panel. Qt slot.

        The registry may hand back a different name than was typed (a counter
        suffix when it collides); the field is corrected to whatever was
        actually applied, so the panel never shows a name the model does not
        have.
        """
        entry = self._models.get(model_id)
        if entry is None or name == entry.name:
            return

        updated = self._models.rename(model_id, name)
        if updated is None:
            return

        self.model_tree.refresh(self._models, self._joints)
        if updated.name != name:
            self.properties_panel.set_name_silently(updated.name)
            self.statusBar().showMessage(
                self.tr("Name {0} is taken, using {1}").format(name, updated.name)
            )
            return

        self.statusBar().showMessage(self.tr("Renamed {0} to {1}").format(entry.name, updated.name))

    def apply_model_placement(self, model_id: str, placement: Transform) -> None:
        """Move and rotate a model from the properties panel. Qt slot.

        Carries the model id explicitly rather than using the selection: the
        panel knows which model its fields belong to, and that is the one to
        move even if the selection is mid-change.
        """
        if self._models.get(model_id) is None:
            return

        set_placement = getattr(self._viewport, "set_placement", None)
        if set_placement is not None:
            set_placement(model_id, placement)

        self._models.set_placement(model_id, placement)
        self.model_tree.refresh(self._models, self._joints)

        if self._placement_dialog is not None and self._placement_model_id == model_id:
            self._placement_dialog.set_placement(placement)

        self.refresh_sensor_readings()
        self.statusBar().showMessage(describe_placement(placement))

    def _on_values_panel_closed(self, _result: int) -> None:
        self._values_panel = None

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

    # -- placing a model ----------------------------------------------------

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
        self.model_tree.refresh(self._models, self._joints)
        if self.properties_panel.model_id == model_id:
            self.properties_panel.set_placement_silently(placement)
        self.refresh_sensor_readings()
        self.statusBar().showMessage(describe_placement(placement))

    def _on_placement_dialog_closed(self, _result: int) -> None:
        self._placement_dialog = None
        self._placement_model_id = None

    # -- the floor ----------------------------------------------------------

    def set_floor_visible(self, visible: bool) -> None:
        """Show or hide the floor grid. Qt slot for the Scene menu."""
        set_visible = getattr(self._viewport, "set_floor_visible", None)
        if set_visible is not None:
            set_visible(visible)

    def open_floor_dialog(self) -> FloorDialog:
        """Move the floor up or down.

        Modeless, like the placement dialog and for the same reason: the number
        only means something against the model it is being lined up with.
        """
        if self._floor_dialog is not None:
            self._floor_dialog.raise_()
            self._floor_dialog.activateWindow()
            return self._floor_dialog

        current_z_m = getattr(self._viewport, "floor_z_m", 0.0)
        dialog = FloorDialog(current_z_m, self)
        dialog.z_changed.connect(self.apply_floor_z)
        dialog.finished.connect(self._on_floor_dialog_closed)
        self._floor_dialog = dialog
        dialog.show()
        return dialog

    def apply_floor_z(self, z_m: float) -> None:
        """Qt slot for the floor dialog, live as the spin box moves."""
        set_z = getattr(self._viewport, "set_floor_z", None)
        if set_z is not None:
            set_z(z_m)

    def _on_floor_dialog_closed(self, _result: int) -> None:
        self._floor_dialog = None

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

        `Insert 3D Model` is disabled during the import — a second one running at the same
        time would overwrite both the cache and the scene. A large assembly takes minutes
        to import, which is why it cannot be done in the main thread.
        """
        if self._import_thread is not None:
            logger.debug("an import is already running, ignoring", file=str(path))
            return

        self.insert_model_action.setEnabled(False)
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
        pending = self._loader.current
        entry = self.add_model(self._imported_file(pending), metadata.assembly, cache_dir)

        if pending is not None:
            self._restore_from_project(entry, pending)

    def _imported_file(self, pending: PendingModel | None) -> Path:
        """Which file the finished import was for.

        During a project load the queue is the authoritative answer — it is the
        same file the thread was given, and it is still known after the thread has
        gone.
        """
        if pending is not None:
            return pending.path
        return self._import_thread.step_file if self._import_thread is not None else Path()

    def _restore_from_project(self, entry: ModelEntry, pending: PendingModel) -> None:
        """Put back what the project recorded about a model: its name and placement.

        The name is not cosmetic. A project stores its selection by name (R13), so
        while the models came back as `fixture`, `fixture (2)` — the file stem plus a
        counter, freshly generated by the registry — a renamed selection could not be
        matched either.
        """
        self._models.rename(entry.model_id, pending.name)
        self._models.set_placement(entry.model_id, pending.placement)
        self._models.set_visible(entry.model_id, pending.is_visible)
        self._models.set_axes_visible(entry.model_id, pending.show_axes)
        self._models.set_color(entry.model_id, pending.color)
        self._models.set_highlight_color(entry.model_id, pending.highlight_color)

        set_placement = getattr(self._viewport, "set_placement", None)
        if set_placement is not None:
            set_placement(entry.model_id, pending.placement)
        set_visible = getattr(self._viewport, "set_model_visible", None)
        if set_visible is not None:
            set_visible(entry.model_id, pending.is_visible)
        set_axes_visible = getattr(self._viewport, "set_axes_visible", None)
        if set_axes_visible is not None:
            set_axes_visible(entry.model_id, pending.show_axes)
        set_model_color = getattr(self._viewport, "set_model_color", None)
        if set_model_color is not None:
            set_model_color(entry.model_id, pending.color)
        set_highlight_color = getattr(self._viewport, "set_highlight_color", None)
        if set_highlight_color is not None:
            set_highlight_color(entry.model_id, pending.highlight_color)

        self._refresh_models()

    def on_import_failed(self, message: str) -> None:
        """Qt slot: the import failed."""
        self.statusBar().showMessage(self.tr("Loading failed"))
        QMessageBox.warning(self, self.tr("Loading failed"), message)

    def on_import_finished(self) -> None:
        """Qt slot: the thread ended, however it went.

        This is also what drives a project load forward: the next model starts
        only once the previous import has released the cache.
        """
        self._import_thread = None
        self.insert_model_action.setEnabled(True)

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
    # Without both names `QSettings` has nowhere of its own to write, and the
    # column widths would be saved into a location that changes between runs.
    application.setOrganizationName(APP_TITLE)
    application.setOrganizationDomain(APP_DOMAIN)
    # Set on the application, not per window: it is what the taskbar and the
    # alt-tab list show, and every dialog inherits it.
    application.setWindowIcon(app_icon())
    install_translator(application, language)

    window = MainWindow()
    window.show()
    return int(application.exec())
