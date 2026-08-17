"""Tests for the project file format.

The things worth covering are the ones that lose work when wrong: the round trip
must be exact, a file from a newer version must be refused rather than
half-read, and paths must survive the project folder being moved.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from pssim.config.project import (
    PROJECT_FORMAT_VERSION,
    PROJECT_SUFFIX,
    CameraSpec,
    ModelSpec,
    PlacementSpec,
    ProjectSpec,
    build_project,
    read_project,
    resolve_path,
    store_path,
    write_project,
)
from pssim.domain.machine import Transform
from pssim.domain.placement import PlacementDisplay, to_transform


def sample_project(project_path: Path) -> ProjectSpec:
    return build_project(
        models=(
            ("gantry", project_path.parent / "models" / "gantry.step", Transform()),
            (
                "head",
                project_path.parent / "models" / "head.step",
                to_transform(PlacementDisplay(x_mm=250.0, rotate_z_deg=45.0)),
            ),
        ),
        project_path=project_path,
        selected="head",
        camera=CameraSpec(distance_mm=1500.0, azimuth_deg=-35.0, elevation_deg=25.0),
    )


class TestRoundTrip:
    def test_written_file_can_be_read(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert len(read_project(path).models) == 2

    def test_names_survive(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert [model.name for model in read_project(path).models] == ["gantry", "head"]

    def test_order_survives(self, tmp_path: Path) -> None:
        # The tree lists models in project order, so order is part of the data.
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).models[0].name == "gantry"

    def test_placement_survives_exactly(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        placement = read_project(path).models[1].placement

        assert placement.x_mm == pytest.approx(250.0)
        assert placement.rotate_z_deg == pytest.approx(45.0)

    def test_placement_converts_back_to_internal_units(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        transform = read_project(path).models[1].placement.to_transform()

        assert transform.xyz[0] == pytest.approx(0.25)
        assert transform.rpy[2] == pytest.approx(math.pi / 4)

    def test_selection_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).selected == "head"

    def test_camera_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        camera = read_project(path).camera

        assert camera is not None
        assert camera.distance_mm == pytest.approx(1500.0)
        assert camera.elevation_deg == pytest.approx(25.0)

    def test_empty_project_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.pssim"
        write_project(path, ProjectSpec())

        assert read_project(path).models == ()


class TestFileOnDisk:
    def test_file_is_json(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_units_in_the_file_are_millimetres(self, tmp_path: Path) -> None:
        # Someone reading the file must see the numbers they typed, not metres.
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        raw = json.loads(path.read_text(encoding="utf-8"))

        assert raw["models"][1]["placement"]["x_mm"] == pytest.approx(250.0)

    def test_version_is_recorded(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).version == PROJECT_FORMAT_VERSION

    def test_missing_suffix_is_added(self, tmp_path: Path) -> None:
        # A user typing "line1" in the save dialog must still get a file the
        # open dialog will show.
        written = write_project(tmp_path / "line1", ProjectSpec())

        assert written.suffix == PROJECT_SUFFIX

    def test_existing_suffix_is_kept(self, tmp_path: Path) -> None:
        written = write_project(tmp_path / "line1.pssim", ProjectSpec())

        assert written.name == "line1.pssim"

    def test_no_temporary_file_is_left_behind(self, tmp_path: Path) -> None:
        write_project(tmp_path / "line.pssim", ProjectSpec())

        assert list(tmp_path.glob("*.tmp")) == []

    def test_parent_directory_is_created(self, tmp_path: Path) -> None:
        written = write_project(tmp_path / "deeper" / "line.pssim", ProjectSpec())

        assert written.is_file()


class TestPaths:
    def test_model_beside_the_project_is_relative(self, tmp_path: Path) -> None:
        # A project plus its models folder must survive being moved or shared.
        project = tmp_path / "line.pssim"
        model = tmp_path / "models" / "gantry.step"

        assert store_path(model, project) == "models/gantry.step"

    def test_model_outside_the_project_folder_is_absolute(self, tmp_path: Path) -> None:
        # It does not travel with the project, so `..` steps back out to it would
        # only break once the project file alone is moved.
        project = tmp_path / "projects" / "line.pssim"
        model = tmp_path / "models" / "gantry.step"

        assert store_path(model, project) == str(model.resolve())

    def test_model_outside_the_project_folder_still_resolves(self, tmp_path: Path) -> None:
        project = tmp_path / "projects" / "line.pssim"
        model = tmp_path / "models" / "gantry.step"

        assert resolve_path(store_path(model, project), project) == model.resolve()

    def test_absolute_model_survives_moving_the_project_file(self, tmp_path: Path) -> None:
        # The other half of the rule: a model kept elsewhere stays findable even
        # when the project file is moved on its own.
        stored = store_path(tmp_path / "shared" / "gantry.step", tmp_path / "a" / "line.pssim")

        assert (
            resolve_path(stored, tmp_path / "b" / "c" / "line.pssim")
            == (tmp_path / "shared" / "gantry.step").resolve()
        )

    def test_nested_model_folder_stays_relative(self, tmp_path: Path) -> None:
        project = tmp_path / "line.pssim"
        model = tmp_path / "cad" / "station" / "gantry.step"

        assert store_path(model, project) == "cad/station/gantry.step"

    def test_relative_path_resolves_back(self, tmp_path: Path) -> None:
        project = tmp_path / "line.pssim"
        model = tmp_path / "models" / "gantry.step"

        assert resolve_path(store_path(model, project), project) == model.resolve()

    def test_absolute_path_is_left_alone(self, tmp_path: Path) -> None:
        absolute = Path("C:/elsewhere/gantry.step") if Path("C:/").exists() else Path("/g.step")

        assert resolve_path(str(absolute), tmp_path / "line.pssim") == absolute

    def test_moving_the_project_folder_keeps_models_findable(self, tmp_path: Path) -> None:
        # The whole reason paths are relative.
        original = tmp_path / "a" / "line.pssim"
        moved = tmp_path / "b" / "line.pssim"
        stored = store_path(tmp_path / "a" / "models" / "gantry.step", original)

        assert resolve_path(stored, moved) == (tmp_path / "b" / "models" / "gantry.step").resolve()


class TestRejectedFiles:
    def test_missing_file(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        with pytest.raises(ConfigError, match="cannot be read"):
            read_project(tmp_path / "nope.pssim")

    def test_not_json(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text("this is not a project", encoding="utf-8")

        with pytest.raises(ConfigError, match="not a valid project"):
            read_project(path)

    def test_json_but_not_an_object(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(ConfigError, match="object at the top level"):
            read_project(path)

    def test_newer_format_is_refused(self, tmp_path: Path) -> None:
        # Half-reading a newer file would silently drop settings.
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(
            json.dumps({"version": PROJECT_FORMAT_VERSION + 1, "models": []}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="newer version"):
            read_project(path)

    def test_unknown_field_is_refused(self, tmp_path: Path) -> None:
        # A typo in a hand-edited project must not be silently ignored.
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(json.dumps({"version": 1, "modles": []}), encoding="utf-8")

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_model_without_a_name_is_refused(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(
            json.dumps({"version": 1, "models": [{"name": "", "file": "a.step"}]}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_zero_camera_distance_is_refused(self, tmp_path: Path) -> None:
        # Distance zero would put the camera inside the model and divide by zero
        # in the framing maths.
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(
            json.dumps({"version": 1, "camera": {"distance_mm": 0.0}}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)


class TestPlacementSpec:
    def test_default_is_identity(self) -> None:
        assert PlacementSpec().to_transform() == Transform()

    def test_from_transform_uses_display_units(self) -> None:
        spec = PlacementSpec.from_transform(Transform(xyz=(0.1, 0.0, 0.0)))

        assert spec.x_mm == pytest.approx(100.0)

    def test_round_trip_through_transform(self) -> None:
        original = PlacementSpec(x_mm=1.5, rotate_y_deg=-30.0)

        assert PlacementSpec.from_transform(original.to_transform()).x_mm == pytest.approx(1.5)


class TestModelSpec:
    def test_placement_defaults_to_identity(self) -> None:
        spec = ModelSpec(name="a", file="a.step")

        assert spec.placement.to_transform() == Transform()
