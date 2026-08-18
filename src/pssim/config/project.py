"""The project file — which models are loaded, where they sit, where the camera is.

Format is JSON. Chosen over YAML because the file is written by the application,
not by hand; JSON has one way to write things, so a diff between two saves shows
only what actually changed.

**Everything in the file is in millimetres and degrees.** One rule with no
exceptions, including the camera: the numbers in the file are the numbers the
user typed into the Placement dialog, so the file can be read and sanity-checked
without converting anything in your head. The translation to internal metres and
radians happens here, at the boundary, exactly as it does for `machines/*.yaml`
and for the OPC UA layer (see docs/architecture.md R3).

Model paths are stored **relative to the project file** when the model sits
inside the project's own folder, so a project plus its `models/` subfolder can be
moved or shared as a unit. Models kept anywhere else are stored absolute — see
`store_path`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Transform
from pssim.domain.placement import PlacementDisplay, from_transform, to_transform

#: Bumped only on a change that older readers could not understand. A file from
#: the future is refused rather than half-read.
PROJECT_FORMAT_VERSION: Final = 1

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


class ModelSpec(StrictModel):
    """One model in the project."""

    name: str = Field(min_length=1)
    """Display name. Restored so the tree reads the same after reloading."""

    file: str = Field(min_length=1)
    """Path to the CAD file, relative to the project file where possible."""

    placement: PlacementSpec = PlacementSpec()


class CameraSpec(StrictModel):
    """Where the camera was looking. Millimetres and degrees, as everywhere."""

    target_x_mm: float = 0.0
    target_y_mm: float = 0.0
    target_z_mm: float = 0.0
    distance_mm: float = Field(default=1000.0, gt=0.0)
    azimuth_deg: float = 0.0
    elevation_deg: float = 0.0


class ProjectSpec(StrictModel):
    """Root of the project file."""

    version: int = PROJECT_FORMAT_VERSION
    models: tuple[ModelSpec, ...] = ()
    selected: str | None = None
    """Name of the selected model, or `None`. Names, not ids: ids are generated
    per session and would mean nothing after a reload."""

    camera: CameraSpec | None = None


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


def build_project(
    models: tuple[tuple[str, Path, Transform], ...],
    project_path: Path,
    selected: str | None = None,
    camera: CameraSpec | None = None,
) -> ProjectSpec:
    """Assemble a project from `(name, cad_path, placement)` triples."""
    return ProjectSpec(
        version=PROJECT_FORMAT_VERSION,
        models=tuple(
            ModelSpec(
                name=name,
                file=store_path(path, project_path),
                placement=PlacementSpec.from_transform(placement),
            )
            for name, path, placement in models
        ),
        selected=selected,
        camera=camera,
    )
