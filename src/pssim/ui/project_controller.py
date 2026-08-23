"""Saving and loading projects on behalf of the main window.

Split out of `MainWindow` because loading is not a single action: a project
names several CAD files, each needs its own background import, and they have to
run **one at a time** — the importer writes into a shared cache and two of them
at once would race.

So this holds a small queue and steps through it, applying each model's saved
placement as its import finishes. The window keeps the menu and the dialogs; this
keeps the sequence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pssim.config.project import (
    AnchorSpec,
    CameraSpec,
    FloorSpec,
    JointSpec,
    ModelSave,
    ProjectSpec,
    SceneSpec,
    SensorSpec,
    build_project,
    read_project,
    resolve_path,
    write_project,
)
from pssim.domain.machine import Rgba, Transform
from pssim.observability import get_logger
from pssim.viz.floor import FloorState
from pssim.viz.orbit import MAX_DISTANCE_FACTOR, MIN_DISTANCE_FACTOR, OrbitCamera

logger = get_logger(__name__)

#: Degrees per radian, spelled out because the project file is in degrees and
#: the camera is in radians. Same boundary rule as everywhere else.
_DEG_PER_RAD: Final = 57.29577951308232
_MM_PER_M: Final = 1000.0


@dataclass(frozen=True, slots=True)
class PendingModel:
    """A model named by a project that has not been imported yet."""

    name: str
    path: Path
    placement: Transform
    is_visible: bool = True
    show_axes: bool = True
    color: Rgba | None = None
    highlight_color: Rgba | None = None
    """The model's own display settings. Here rather than beside `bindings`
    because they need nothing but the model itself — they go back on as its
    import finishes, exactly like its placement."""


@dataclass(frozen=True, slots=True)
class LoadPlan:
    """What a project asked for, split into what can and cannot be done."""

    pending: tuple[PendingModel, ...]
    missing: tuple[Path, ...]
    """Files the project references that are not on disk."""

    selected_name: str | None = None
    camera: CameraSpec | None = None
    floor: FloorSpec = FloorSpec()
    sensors: tuple[SensorSpec, ...] = ()
    joints: tuple[JointSpec, ...] = ()
    """Unconverted, like `sensors`: the joints are rebuilt once every model is
    in, since a binding needs both to exist."""

    show_joint_names: bool = True
    cross_size_mm: float = 200.0
    text_size_mm: float = 50.0
    origin_cross_size_mm: float = 200.0
    show_origin_cross: bool = True

    bindings: tuple[tuple[str, str, AnchorSpec], ...] = ()
    """`(model name, joint name, anchor)` for every model the file binds. Kept
    beside the models rather than inside `PendingModel`, because a binding can
    only be applied after the joints exist — which is long after the model's own
    import finished."""

    @property
    def has_work(self) -> bool:
        return bool(self.pending)


def plan_load(project: ProjectSpec, project_path: Path) -> LoadPlan:
    """Work out what loading this project involves. Pure function.

    Missing files are separated out rather than being allowed to fail one by one
    during import: a project referencing five moved files would otherwise raise
    five modal dialogs. They are reported once and the rest still loads.
    """
    pending: list[PendingModel] = []
    missing: list[Path] = []

    for spec in project.models:
        path = resolve_path(spec.file, project_path)
        if path.is_file():
            pending.append(
                PendingModel(
                    name=spec.name,
                    path=path,
                    placement=spec.placement.to_transform(),
                    is_visible=spec.visible,
                    show_axes=spec.show_axes,
                    color=None if spec.color is None else spec.color.to_color(),
                    highlight_color=(
                        None if spec.highlight_color is None else spec.highlight_color.to_color()
                    ),
                )
            )
        else:
            missing.append(path)

    return LoadPlan(
        pending=tuple(pending),
        missing=tuple(missing),
        selected_name=project.selected,
        camera=project.camera,
        floor=project.floor,
        sensors=project.sensors,
        joints=project.joints,
        show_joint_names=project.show_joint_names,
        cross_size_mm=project.cross_size_mm,
        text_size_mm=project.text_size_mm,
        origin_cross_size_mm=project.origin_cross_size_mm,
        show_origin_cross=project.show_origin_cross,
        bindings=tuple(
            (spec.name, spec.bound_to, spec.anchor)
            for spec in project.models
            if spec.bound_to is not None
        ),
    )


def camera_to_spec(camera: OrbitCamera | None) -> CameraSpec | None:
    """Convert a camera into file units: millimetres and degrees.

    `None` in, `None` out: a window with no renderer has no camera to save, and
    a project without a camera section simply keeps whatever the view happens to
    be on load.
    """
    if camera is None or not camera.distance_m:
        return None

    return CameraSpec(
        target_x_mm=camera.target[0] * _MM_PER_M,
        target_y_mm=camera.target[1] * _MM_PER_M,
        target_z_mm=camera.target[2] * _MM_PER_M,
        distance_mm=camera.distance_m * _MM_PER_M,
        azimuth_deg=camera.azimuth_rad * _DEG_PER_RAD,
        elevation_deg=camera.elevation_rad * _DEG_PER_RAD,
    )


def spec_to_camera(spec: CameraSpec, radius_hint_m: float = 1.0) -> OrbitCamera:
    """Rebuild a camera from file units.

    `radius_hint_m` sets the zoom limits, which are not stored: they follow from
    the size of the scene, and the scene is whatever was just loaded.
    """
    safe_radius = radius_hint_m if radius_hint_m > 0.0 else 1.0
    return OrbitCamera(
        target=(
            spec.target_x_mm / _MM_PER_M,
            spec.target_y_mm / _MM_PER_M,
            spec.target_z_mm / _MM_PER_M,
        ),
        distance_m=spec.distance_mm / _MM_PER_M,
        azimuth_rad=spec.azimuth_deg / _DEG_PER_RAD,
        elevation_rad=spec.elevation_deg / _DEG_PER_RAD,
        min_distance_m=safe_radius * MIN_DISTANCE_FACTOR,
        max_distance_m=safe_radius * MAX_DISTANCE_FACTOR,
    )


def floor_to_spec(floor: FloorState) -> FloorSpec:
    """Convert the floor into file units: millimetres."""
    return FloorSpec(visible=floor.visible, z_mm=floor.z_m * _MM_PER_M)


def spec_to_floor(spec: FloorSpec) -> FloorState:
    """Rebuild the floor from file units."""
    return FloorState(visible=spec.visible, z_m=spec.z_mm / _MM_PER_M)


class ProjectLoader:
    """Steps through the models of a project, one import at a time."""

    def __init__(self, start_import: Callable[[Path], None]) -> None:
        self._start_import = start_import
        self._queue: deque[PendingModel] = deque()
        self._current: PendingModel | None = None
        self._plan: LoadPlan | None = None

    # -- state --------------------------------------------------------------

    @property
    def is_loading(self) -> bool:
        return self._current is not None or bool(self._queue)

    @property
    def current(self) -> PendingModel | None:
        """The model being imported right now, so its placement can be applied."""
        return self._current

    @property
    def plan(self) -> LoadPlan | None:
        return self._plan

    @property
    def remaining(self) -> int:
        return len(self._queue)

    # -- driving ------------------------------------------------------------

    def begin(self, plan: LoadPlan) -> bool:
        """Queue a plan and start the first import. `False` if there was nothing."""
        self._plan = plan
        self._queue = deque(plan.pending)
        self._current = None
        return self.start_next()

    def start_next(self) -> bool:
        """Start the next import. `False` when the queue is empty."""
        if not self._queue:
            self._current = None
            return False
        self._current = self._queue.popleft()
        logger.info("loading project model", name=self._current.name, remaining=len(self._queue))
        self._start_import(self._current.path)
        return True

    def finish(self) -> LoadPlan | None:
        """Called once the queue is empty. Returns the plan for final steps."""
        plan = self._plan
        self._plan = None
        self._current = None
        return plan


@dataclass(frozen=True, slots=True)
class SceneState:
    """Everything about the scene besides its models, in the types the viewport
    and registries hand around. The ui-layer counterpart of `config.project.SceneSpec`,
    which holds the same information already converted to file units.

    Bundled for the same reason as `SceneSpec`: `camera` alone already left
    `project_from_models`/`save_project` at the code-style.md 4-argument limit.
    """

    selected_name: str | None = None
    camera: OrbitCamera | None = None
    floor: FloorState = FloorState()
    sensors: tuple[SensorSpec, ...] = ()
    joints: tuple[JointSpec, ...] = ()
    """Already file-shaped, unlike `sensors`: a joint's parent has to be named
    rather than identified, and only this layer holds the id-to-name map."""

    show_joint_names: bool = True
    cross_size_mm: float = 200.0
    text_size_mm: float = 50.0
    origin_cross_size_mm: float = 200.0
    show_origin_cross: bool = True


#: A frozen, side-effect-free default, shared across every call with no `scene`
#: argument — nothing about it is ever mutated.
_EMPTY_SCENE: Final = SceneState()


def project_from_models(
    models: tuple[ModelSave, ...],
    project_path: Path,
    scene: SceneState = _EMPTY_SCENE,
) -> ProjectSpec:
    """Assemble a project file from the current scene."""
    return build_project(
        models=models,
        project_path=project_path,
        scene=SceneSpec(
            selected=scene.selected_name,
            camera=camera_to_spec(scene.camera),
            floor=floor_to_spec(scene.floor),
            sensors=scene.sensors,
            joints=scene.joints,
            show_joint_names=scene.show_joint_names,
            cross_size_mm=scene.cross_size_mm,
            text_size_mm=scene.text_size_mm,
            origin_cross_size_mm=scene.origin_cross_size_mm,
            show_origin_cross=scene.show_origin_cross,
        ),
    )


def save_project(
    path: Path,
    models: tuple[ModelSave, ...],
    scene: SceneState = _EMPTY_SCENE,
) -> Path:
    """Write the current scene to a project file. Returns the path written."""
    project = project_from_models(models, path, scene)
    written = write_project(path, project)
    logger.info("project saved", file=str(written), models=len(project.models))
    return written


def load_plan_from_file(path: Path) -> LoadPlan:
    """Read a project file and work out what loading it involves."""
    project = read_project(path)
    plan = plan_load(project, path)
    logger.info(
        "project read",
        file=str(path),
        models=len(plan.pending),
        missing=len(plan.missing),
        joints=len(plan.joints),
    )
    return plan
