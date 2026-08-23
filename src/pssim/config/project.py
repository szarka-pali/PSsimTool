"""The project file — which models are loaded, where they sit, where the camera is.

Format is JSON. Chosen over YAML because the file is written by the application,
not by hand; JSON has one way to write things, so a diff between two saves shows
only what actually changed.

**Everything in the file is in millimetres and degrees.** One rule with no
exceptions, including the camera: the numbers in the file are the numbers the
user typed into the Placement dialog, so the file can be read and sanity-checked
without converting anything in your head. The translation to internal metres and
radians happens here, at the boundary, exactly as it does for `machines/*.yaml`
and for the OPC UA layer (see docs/architecture.md R8).

Model paths are stored **relative to the project file** when the model sits
inside the project's own folder, so a project plus its `models/` subfolder can be
moved or shared as a unit. Models kept anywhere else are stored absolute — see
`store_path`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Rgba, Transform, Vec3
from pssim.domain.model_joints import (
    Anchor,
    AnchorDisplay,
    ModelJoint,
    ModelJointDisplay,
    ModelJointKind,
    from_anchor,
    from_model_joint,
    to_anchor,
    to_model_joint,
    value_scale,
)
from pssim.domain.placement import PlacementDisplay, from_transform, to_transform
from pssim.domain.sensors import (
    DEFAULT_COUNTS_PER_REVOLUTION,
    Sensor,
    SensorDisplay,
    SensorKind,
    from_sensor,
    to_sensor,
)

#: Bumped only on a change that older readers could not understand. A file from
#: the future is refused rather than half-read.
#: 2: added `floor` and `sensors` — bumped once for both, not twice, since one
#: release gets one format boundary regardless of how many fields it adds.
#: 3: added `joints`, per-model `bound_to`/`anchor`, and the per-item display
#: flags (`visible`, `show_axes`), so the whole joint system and how it is being
#: looked at both survive a save. One bump for the set, same reasoning — and the
#: flags joined 3 rather than making a 4 because 3 had not been released yet, so
#: no file on anyone's disk has ever crossed that boundary.
PROJECT_FORMAT_VERSION: Final = 3

PROJECT_SUFFIX: Final = ".pssim"

#: What `QFileDialog` shows. Kept here so the format and its filter cannot drift.
PROJECT_FILE_FILTER: Final = "PSsimTool project (*.pssim);;All files (*)"


class StrictModel(BaseModel):
    """Unknown fields are an error, not silently dropped.

    A typo in a hand-edited project would otherwise be invisible: the file loads,
    the setting is ignored, and nobody finds out why it had no effect.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class PlacementSpec(StrictModel):
    """Placement as it appears in the file: millimetres and degrees."""

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    rotate_x_deg: float = 0.0
    rotate_y_deg: float = 0.0
    rotate_z_deg: float = 0.0

    @classmethod
    def from_transform(cls, transform: Transform) -> PlacementSpec:
        display = from_transform(transform)
        return cls(
            x_mm=display.x_mm,
            y_mm=display.y_mm,
            z_mm=display.z_mm,
            rotate_x_deg=display.rotate_x_deg,
            rotate_y_deg=display.rotate_y_deg,
            rotate_z_deg=display.rotate_z_deg,
        )

    def to_placement_display(self) -> PlacementDisplay:
        """The same six numbers as the display dataclass the domain converters
        take. Used where a spec nests a placement rather than owning one."""
        return PlacementDisplay(
            x_mm=self.x_mm,
            y_mm=self.y_mm,
            z_mm=self.z_mm,
            rotate_x_deg=self.rotate_x_deg,
            rotate_y_deg=self.rotate_y_deg,
            rotate_z_deg=self.rotate_z_deg,
        )

    def to_transform(self) -> Transform:
        return to_transform(
            PlacementDisplay(
                x_mm=self.x_mm,
                y_mm=self.y_mm,
                z_mm=self.z_mm,
                rotate_x_deg=self.rotate_x_deg,
                rotate_y_deg=self.rotate_y_deg,
                rotate_z_deg=self.rotate_z_deg,
            )
        )


class AnchorSpec(StrictModel):
    """A model's contact point, in the units the file uses: millimetres.

    The direction stays unitless — it is a direction, and scaling it would mean
    nothing. The only field in the whole format without a unit suffix, for that
    reason.
    """

    point_x_mm: float = 0.0
    point_y_mm: float = 0.0
    point_z_mm: float = 0.0
    direction_x: float = 0.0
    direction_y: float = 0.0
    direction_z: float = 1.0

    @classmethod
    def from_anchor(cls, anchor: Anchor) -> AnchorSpec:
        display = from_anchor(anchor)
        return cls(
            point_x_mm=display.point_mm[0],
            point_y_mm=display.point_mm[1],
            point_z_mm=display.point_mm[2],
            direction_x=display.direction[0],
            direction_y=display.direction[1],
            direction_z=display.direction[2],
        )

    def to_anchor(self) -> Anchor:
        return to_anchor(
            AnchorDisplay(
                point_mm=(self.point_x_mm, self.point_y_mm, self.point_z_mm),
                direction=(self.direction_x, self.direction_y, self.direction_z),
            )
        )


class ColorSpec(StrictModel):
    """A colour in the file: four components, each 0..1.

    No unit suffix, for the same reason `AnchorSpec.direction_*` has none — a
    colour has no unit. Named fields rather than a list, matching how every other
    spec here is written, so a hand-read of the file needs no index counting.

    `alpha` is stored but always written opaque today; see
    `ui.main_window._from_qcolor` for why the dialog does not offer anything else.
    """

    red: float = Field(default=1.0, ge=0.0, le=1.0)
    green: float = Field(default=1.0, ge=0.0, le=1.0)
    blue: float = Field(default=1.0, ge=0.0, le=1.0)
    alpha: float = Field(default=1.0, ge=0.0, le=1.0)

    @classmethod
    def from_color(cls, color: Rgba) -> ColorSpec:
        return cls(red=color[0], green=color[1], blue=color[2], alpha=color[3])

    def to_color(self) -> Rgba:
        return (self.red, self.green, self.blue, self.alpha)


class DirectionSpec(StrictModel):
    """Which way an axis points. Unitless — only the direction matters, so the
    fields carry no `_mm` suffix and the magnitude is never read."""

    x: float = 0.0
    y: float = 0.0
    z: float = 1.0

    @classmethod
    def from_direction(cls, direction: Vec3) -> DirectionSpec:
        return cls(x=direction[0], y=direction[1], z=direction[2])

    def to_direction(self) -> Vec3:
        return (self.x, self.y, self.z)


class JointSpec(StrictModel):
    """One axis or trajectory. Millimetres and degrees, as everywhere.

    `parent` and a model's `bound_to` reference a joint by **name**, not by id:
    ids are generated per session and would mean nothing after a reload, the
    same reasoning `selected` uses for models. Joint names are unique across the
    whole registry, not just among siblings, so a name is unambiguous.

    `lower_limit`/`upper_limit`/`value` are degrees for an `AXIS` and millimetres
    for a `TRAJECTORY` — the kind decides, which is what `value_scale` encodes.
    """

    name: str = Field(min_length=1)
    kind: ModelJointKind
    variable: str = Field(min_length=1)
    origin_x_mm: float = 0.0
    origin_y_mm: float = 0.0
    origin_z_mm: float = 0.0
    target_x_mm: float = 0.0
    target_y_mm: float = 0.0
    target_z_mm: float = 0.0
    direction: DirectionSpec | None = None
    """Which way an `AXIS` turns. Absent means the file predates the field, and
    the direction is recovered from `origin` -> `target` — see `to_joint`."""

    initial_angle_deg: float = 0.0
    """The angle value 0 corresponds to, for an `AXIS`."""

    lower_limit: float | None = None
    upper_limit: float | None = None
    alignment: PlacementSpec = PlacementSpec()
    """The initial coordinate system a bound model aligns to, relative to the
    joint's own tangential frame."""

    parent: str | None = None
    """The name of the joint carrying this one, or `None` for one in the scene."""

    value: float = 0.0
    """Where the joint currently stands. Saved so a scene opens as it was left."""

    show_axes: bool = True
    """Whether the cross on its initial coordinate system is drawn while it is
    selected."""

    show_name: bool = True
    """Whether its name is drawn beside it in the scene."""

    color: ColorSpec | None = None
    """An override for its marker and label, or absent for the default colour."""

    @classmethod
    def from_joint(cls, entry: JointSave, parent_name: str | None = None) -> JointSpec:
        joint = entry.joint
        display = from_model_joint(joint)
        return cls(
            name=display.name,
            kind=display.kind,
            variable=display.variable,
            origin_x_mm=display.origin_mm[0],
            origin_y_mm=display.origin_mm[1],
            origin_z_mm=display.origin_mm[2],
            target_x_mm=display.target_mm[0],
            target_y_mm=display.target_mm[1],
            target_z_mm=display.target_mm[2],
            direction=DirectionSpec.from_direction(joint.direction),
            initial_angle_deg=display.initial_angle_deg,
            lower_limit=display.lower_limit,
            upper_limit=display.upper_limit,
            alignment=PlacementSpec.from_transform(joint.alignment),
            parent=parent_name,
            value=entry.value / value_scale(joint.kind),
            show_axes=entry.show_axes,
            show_name=entry.show_name,
            color=None if entry.color is None else ColorSpec.from_color(entry.color),
        )

    def to_joint(self) -> ModelJoint:
        return to_model_joint(
            ModelJointDisplay(
                name=self.name,
                kind=self.kind,
                variable=self.variable,
                origin_mm=(self.origin_x_mm, self.origin_y_mm, self.origin_z_mm),
                target_mm=(self.target_x_mm, self.target_y_mm, self.target_z_mm),
                direction=self._direction(),
                initial_angle_deg=self.initial_angle_deg,
                lower_limit=self.lower_limit,
                upper_limit=self.upper_limit,
                alignment=self.alignment.to_placement_display(),
            )
        )

    def _direction(self) -> Vec3:
        """Which way an axis turns, recovering it for a file written before the
        field existed.

        Such a file described an axis by two points, so the direction it meant is
        `target - origin`. Falling back to the default `+Z` instead would silently
        turn every axis that ran along X or Y, which is the kind of change nobody
        notices until a model spins the wrong way.

        The magnitude is irrelevant, so the millimetre values go through as they
        are — no conversion to metres, and none needed.
        """
        if self.direction is not None:
            return self.direction.to_direction()

        implied = (
            self.target_x_mm - self.origin_x_mm,
            self.target_y_mm - self.origin_y_mm,
            self.target_z_mm - self.origin_z_mm,
        )
        return implied if any(implied) else (0.0, 0.0, 1.0)

    def to_value(self) -> float:
        """The saved value in internal units (radians or metres)."""
        return self.value * value_scale(self.kind)

    @model_validator(mode="after")
    def _joint_is_buildable(self) -> JointSpec:
        """Refuse a joint the domain would refuse — here, at the boundary.

        Without this the check still happens, but in `to_joint()`, which the
        window calls **after** every model of the project has been imported. The
        user would get a raw failure halfway through a load with part of the
        scene already on screen, instead of "this file is invalid" before
        anything was touched.
        """
        try:
            _ = self.to_joint()
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ModelSpec(StrictModel):
    """One model in the project."""

    name: str = Field(min_length=1)
    """Display name. Restored so the tree reads the same after reloading."""

    file: str = Field(min_length=1)
    """Path to the CAD file, relative to the project file where possible."""

    placement: PlacementSpec = PlacementSpec()

    bound_to: str | None = None
    """Name of the joint whose motion carries this model, or `None`."""

    anchor: AnchorSpec = AnchorSpec()
    """The contact point this model couples to that joint by."""

    visible: bool = True
    """Whether the model is drawn. Stored because hiding a housing to see what
    is behind it is a deliberate act, not an accident of the last session."""

    show_axes: bool = True
    """Whether its coordinate cross is drawn while it is selected."""

    color: ColorSpec | None = None
    """An override for the whole model, or absent for the colours the STEP file
    carries. Absent and "white" are different states — see
    `ui.model_registry.ModelEntry.color`."""

    highlight_color: ColorSpec | None = None
    """The colour it is outlined in when selected, or absent for the default."""


class CameraSpec(StrictModel):
    """Where the camera was looking. Millimetres and degrees, as everywhere."""

    target_x_mm: float = 0.0
    target_y_mm: float = 0.0
    target_z_mm: float = 0.0
    distance_mm: float = Field(default=1000.0, gt=0.0)
    azimuth_deg: float = 0.0
    elevation_deg: float = 0.0


class FloorSpec(StrictModel):
    """The floor's visibility and height. Millimetres, as everywhere in this file.

    Viewport-only state, mirroring `CameraSpec` — the mm/m conversion happens in
    `ui/project_controller.py`, not here, the same boundary `camera` already uses.
    """

    visible: bool = True
    z_mm: float = 0.0


class SensorSpec(StrictModel):
    """One placed sensor. Millimetres, as everywhere in this file.

    Domain-meaningful (unlike `FloorSpec`), so this model converts directly
    against `domain.sensors`, the same way `PlacementSpec` converts against
    `domain.placement`.
    """

    name: str = Field(min_length=1)
    kind: SensorKind
    variable: str = ""
    origin_x_mm: float = 0.0
    origin_y_mm: float = 0.0
    origin_z_mm: float = 0.0
    direction: DirectionSpec | None = None
    """Which way a ray sensor looks. Absent means the file predates the field and
    the direction is recovered from `origin` -> `target` — see `to_sensor`."""

    target_x_mm: float = 0.0
    target_y_mm: float = 0.0
    target_z_mm: float = 0.0
    """Kept only to read a file written when a beam was two points. Never
    written any more; `direction` and `range_mm` replace it."""

    range_mm: float = Field(default=1000.0, gt=0.0)
    half_extent_mm: float = Field(default=100.0, gt=0.0)
    counts_per_revolution: int = Field(default=DEFAULT_COUNTS_PER_REVOLUTION, gt=0)

    mounted_on: str | None = None
    """The name of the model or joint carrying this sensor, or `None` for one
    sitting in the scene. A name, not an id (R7)."""

    @classmethod
    def from_sensor(cls, sensor: Sensor, mounted_on: str | None = None) -> SensorSpec:
        display = from_sensor(sensor)
        return cls(
            name=display.name,
            kind=display.kind,
            variable=display.variable,
            origin_x_mm=display.origin_mm[0],
            origin_y_mm=display.origin_mm[1],
            origin_z_mm=display.origin_mm[2],
            direction=DirectionSpec.from_direction(display.direction),
            range_mm=display.range_mm,
            half_extent_mm=display.half_extent_mm,
            counts_per_revolution=display.counts_per_revolution,
            mounted_on=mounted_on,
        )

    def to_sensor(self) -> Sensor:
        return to_sensor(
            SensorDisplay(
                name=self.name,
                kind=self.kind,
                variable=self.variable,
                origin_mm=(self.origin_x_mm, self.origin_y_mm, self.origin_z_mm),
                direction=self._direction(),
                range_mm=self._range_mm(),
                half_extent_mm=self.half_extent_mm,
                counts_per_revolution=self.counts_per_revolution,
            )
        )

    def _direction(self) -> Vec3:
        """Which way the ray looks, recovering it for a file that described a
        beam by two points.

        Falling back to the default `+Z` would silently turn every beam that ran
        along X or Y — the same trap `JointSpec._direction` avoids.
        """
        if self.direction is not None:
            return self.direction.to_direction()
        implied = (
            self.target_x_mm - self.origin_x_mm,
            self.target_y_mm - self.origin_y_mm,
            self.target_z_mm - self.origin_z_mm,
        )
        return implied if any(implied) else (0.0, 0.0, 1.0)

    def _range_mm(self) -> float:
        """How far the ray reaches.

        For a two-point file that is the distance between the points, so a beam
        keeps the reach it had rather than silently gaining or losing some.
        """
        if self.direction is not None:
            return self.range_mm
        implied = self._direction()
        length = math.sqrt(sum(component * component for component in implied))
        return length if length > 0.0 else self.range_mm


class ProjectSpec(StrictModel):
    """Root of the project file."""

    version: int = PROJECT_FORMAT_VERSION
    models: tuple[ModelSpec, ...] = ()
    selected: str | None = None
    """Name of the selected model, or `None`. Names, not ids: ids are generated
    per session and would mean nothing after a reload."""

    camera: CameraSpec | None = None
    floor: FloorSpec = FloorSpec()
    sensors: tuple[SensorSpec, ...] = ()
    joints: tuple[JointSpec, ...] = ()
    show_joint_names: bool = True
    """The scene-wide name switch. Beside `floor` because it is the same kind of
    thing: how the scene is being looked at, not what it contains."""

    cross_size_mm: float = Field(default=200.0, gt=0.0)
    """The arm length of every coordinate cross. One size for the whole scene —
    see `viz.embed.DEFAULT_CROSS_SIZE_M`."""

    text_size_mm: float = Field(default=50.0, gt=0.0)
    """The height of every piece of 3D text: the X/Y/Z glyphs and the joint
    names alike."""

    origin_cross_size_mm: float = Field(default=200.0, gt=0.0)
    """The origin cross has its own size — it is the scene's reference rather
    than an annotation on one item."""

    show_origin_cross: bool = True

    @model_validator(mode="before")
    @classmethod
    def _accept_the_old_label_size(cls, data: Any) -> Any:
        """A file written before the sizes were split carried `label_size_mm`,
        which meant what `text_size_mm` means now.

        Renamed here rather than simply dropped, because `StrictModel` forbids
        unknown fields — a project saved by the previous build would otherwise
        stop opening, and refusing a file over a renamed key is not a trade worth
        making.
        """
        if not isinstance(data, dict) or "label_size_mm" not in data:
            return data
        migrated = dict(data)
        migrated.setdefault("text_size_mm", migrated.pop("label_size_mm"))
        migrated.pop("label_size_mm", None)
        return migrated


def write_project(path: Path, project: ProjectSpec) -> Path:
    """Write a project file. Returns the path actually written.

    The suffix is added when missing, so a user who types `line1` in the save
    dialog still gets a file the open dialog will show.

    Writing is atomic: a crash halfway through must not leave a file that exists
    but cannot be read — that is worse than no file, because the user believes
    their work is saved.
    """
    target = path if path.suffix else path.with_suffix(PROJECT_SUFFIX)
    payload = json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False)

    temporary = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(target)
    except OSError as exc:
        raise ConfigError(f"{target}: project cannot be written: {exc}") from exc
    return target


def read_project(path: Path) -> ProjectSpec:
    """Read and validate a project file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: project cannot be read: {exc}") from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: not a valid project file: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected an object at the top level")

    version = raw.get("version")
    if isinstance(version, int) and version > PROJECT_FORMAT_VERSION:
        raise ConfigError(
            f"{path}: project was written by a newer version of PSsimTool "
            f"(format {version}, this build understands {PROJECT_FORMAT_VERSION})"
        )

    try:
        return ProjectSpec.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{path}: invalid project file:\n{_format_errors(exc)}") from exc


def _format_errors(exc: ValidationError) -> str:
    """Pydantic errors in a form readable without knowing pydantic."""
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)


def store_path(model_path: Path, project_path: Path) -> str:
    """How a model path should be written into the project file.

    Relative **only when the model lives inside the project's own folder**, so
    that folder can be moved or handed to a colleague as a unit. Anything else
    is absolute: a model somewhere else does not travel with the project, and a
    chain of `..` segments back out to it breaks the moment the project file
    alone is moved, where an absolute path still opens.
    """
    model = model_path.resolve()
    project_dir = project_path.resolve().parent
    if model.drive.lower() != project_dir.drive.lower():
        return str(model)
    if not model.is_relative_to(project_dir):
        return str(model)
    return model.relative_to(project_dir).as_posix()


def resolve_path(stored: str, project_path: Path) -> Path:
    """Turn a stored path back into something openable."""
    candidate = Path(stored)
    if candidate.is_absolute():
        return candidate
    return (project_path.resolve().parent / candidate).resolve()


@dataclass(frozen=True, slots=True)
class ModelSave:
    """One model as the scene hands it over for saving.

    Was a bare `(name, path, placement)` triple; grew a binding and an anchor,
    at which point five positional values stopped being readable at the call
    sites.
    """

    name: str
    path: Path
    placement: Transform
    bound_to_joint_name: str | None = None
    anchor: Anchor = Anchor()
    is_visible: bool = True
    show_axes: bool = True
    color: Rgba | None = None
    highlight_color: Rgba | None = None


@dataclass(frozen=True, slots=True)
class JointSave:
    """One joint as the scene hands it over for saving.

    Same reason as `ModelSave`: `from_joint` took a joint plus a value, and a
    third loose display flag is where that stops reading. `ui.joint_registry`'s
    `JointEntry` is not used directly because it carries a `parent_joint_id`,
    and only `ui/` can turn that into the parent's *name* — this layer stores
    names (R7).
    """

    joint: ModelJoint
    value: float = 0.0
    show_axes: bool = True
    show_name: bool = True
    color: Rgba | None = None


@dataclass(frozen=True, slots=True)
class SceneSpec:
    """Everything a project remembers besides its models: selection, camera,
    floor, sensors. Bundled because `camera` alone already left `build_project`
    at the code-style.md 4-argument limit; `floor` and `sensors` would have
    pushed it past it.
    """

    selected: str | None = None
    camera: CameraSpec | None = None
    floor: FloorSpec = FloorSpec()
    sensors: tuple[SensorSpec, ...] = ()
    """Already converted, like `joints`: a sensor's mount has to be named rather
    than identified, and only `ui/` holds the id-to-name map."""

    joints: tuple[JointSpec, ...] = ()
    """Already converted, unlike `sensors`: turning a joint's parent id into a
    parent *name* needs the registry, which is a `ui/` concern. This layer only
    stores what it is handed."""

    show_joint_names: bool = True
    cross_size_mm: float = 200.0
    text_size_mm: float = 50.0
    origin_cross_size_mm: float = 200.0
    show_origin_cross: bool = True


#: A frozen, side-effect-free default — read, never mutated, so sharing one
#: instance across every call with no `scene` argument is safe.
_EMPTY_SCENE: Final = SceneSpec()


def build_project(
    models: tuple[ModelSave, ...],
    project_path: Path,
    scene: SceneSpec = _EMPTY_SCENE,
) -> ProjectSpec:
    """Assemble a project from what the scene holds plus its scene-wide state."""
    return ProjectSpec(
        version=PROJECT_FORMAT_VERSION,
        models=tuple(
            ModelSpec(
                name=save.name,
                file=store_path(save.path, project_path),
                placement=PlacementSpec.from_transform(save.placement),
                bound_to=save.bound_to_joint_name,
                anchor=AnchorSpec.from_anchor(save.anchor),
                visible=save.is_visible,
                show_axes=save.show_axes,
                color=None if save.color is None else ColorSpec.from_color(save.color),
                highlight_color=(
                    None
                    if save.highlight_color is None
                    else ColorSpec.from_color(save.highlight_color)
                ),
            )
            for save in models
        ),
        selected=scene.selected,
        camera=scene.camera,
        floor=scene.floor,
        sensors=scene.sensors,
        joints=scene.joints,
        show_joint_names=scene.show_joint_names,
        cross_size_mm=scene.cross_size_mm,
        text_size_mm=scene.text_size_mm,
        origin_cross_size_mm=scene.origin_cross_size_mm,
        show_origin_cross=scene.show_origin_cross,
    )
