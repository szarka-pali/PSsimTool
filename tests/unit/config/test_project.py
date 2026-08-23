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
from pydantic import ValidationError

from pssim.config.project import (
    PROJECT_FORMAT_VERSION,
    PROJECT_SUFFIX,
    AnchorSpec,
    CameraSpec,
    ColorSpec,
    FloorSpec,
    JointSave,
    JointSpec,
    ModelSave,
    ModelSpec,
    PlacementSpec,
    ProjectSpec,
    SceneSpec,
    SensorSpec,
    build_project,
    read_project,
    resolve_path,
    store_path,
    write_project,
)
from pssim.domain.machine import Transform
from pssim.domain.model_joints import Anchor, direction_of
from pssim.domain.placement import PlacementDisplay, to_transform
from pssim.domain.sensors import (
    ENCODER_KINDS,
    Sensor,
    SensorKind,
    unit_direction,
)
from tests.factories import axis_joint, beam_sensor, proximity_sensor, trajectory_joint


def sample_project(project_path: Path) -> ProjectSpec:
    return build_project(
        models=(
            ModelSave(
                name="gantry",
                path=project_path.parent / "models" / "gantry.step",
                placement=Transform(),
            ),
            ModelSave(
                name="head",
                path=project_path.parent / "models" / "head.step",
                placement=to_transform(PlacementDisplay(x_mm=250.0, rotate_z_deg=45.0)),
                bound_to_joint_name="rail",
                anchor=Anchor(point=(0.05, 0.0, 0.0), direction=(1.0, 0.0, 0.0)),
            ),
        ),
        project_path=project_path,
        scene=SceneSpec(
            selected="head",
            camera=CameraSpec(distance_mm=1500.0, azimuth_deg=-35.0, elevation_deg=25.0),
            floor=FloorSpec(visible=False, z_mm=-50.0),
            sensors=(
                SensorSpec.from_sensor(beam_sensor(name="gate")),
                SensorSpec.from_sensor(proximity_sensor(name="zone")),
            ),
            joints=(
                JointSpec.from_joint(
                    JointSave(
                        trajectory_joint(
                            name="rail", origin=(1.0, 0.0, 0.0), target=(5.0, 0.0, 0.0)
                        ),
                        value=0.5,
                    )
                ),
                JointSpec.from_joint(
                    JointSave(
                        axis_joint(name="turn", limits=(-math.pi / 2.0, math.pi / 2.0)),
                        value=math.pi / 4.0,
                    ),
                    parent_name="rail",
                ),
            ),
        ),
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

    def test_floor_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        floor = read_project(path).floor

        assert floor.visible is False
        assert floor.z_mm == pytest.approx(-50.0)

    def test_sensors_survive(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        sensors = read_project(path).sensors

        assert [sensor.name for sensor in sensors] == ["gate", "zone"]


class TestBackwardCompatibility:
    def test_a_version_one_file_with_no_floor_or_sensors_gets_the_defaults(
        self, tmp_path: Path
    ) -> None:
        # A file predating floor/sensors has neither key; the format bump must
        # not turn its absence into an error.
        path = tmp_path / "line.pssim"
        path.write_text(json.dumps({"version": 1, "models": []}), encoding="utf-8")

        project = read_project(path)

        assert project.floor == FloorSpec()
        assert project.sensors == ()


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

    def test_unknown_sensor_kind_is_refused(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(
            json.dumps({"version": 2, "sensors": [{"name": "x", "kind": "laser"}]}),
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


class TestSensorSpec:
    def test_round_trip_preserves_a_beam(self) -> None:
        original = beam_sensor(name="gate", origin=(1.0, 2.0, 3.0), target=(4.0, 5.0, 6.0))

        restored = SensorSpec.from_sensor(original).to_sensor()

        assert restored.name == original.name
        assert restored.kind == original.kind
        assert restored.origin == pytest.approx(original.origin)
        assert restored.direction == pytest.approx(original.direction)
        assert restored.range_m == pytest.approx(original.range_m)

    def test_round_trip_preserves_a_proximity_zone(self) -> None:
        original = proximity_sensor(name="zone", origin=(0.1, 0.0, 0.0), half_extent_m=0.25)

        restored = SensorSpec.from_sensor(original).to_sensor()

        assert restored.half_extent_m == pytest.approx(original.half_extent_m)

    def test_units_are_millimetres(self) -> None:
        spec = SensorSpec.from_sensor(beam_sensor(origin=(1.0, 0.0, 0.0), target=(2.0, 0.0, 0.0)))

        assert spec.origin_x_mm == pytest.approx(1000.0)


class TestAnchorSpec:
    def test_default_direction_is_z(self) -> None:
        assert AnchorSpec().to_anchor().direction == pytest.approx((0.0, 0.0, 1.0))

    def test_point_is_in_millimetres(self) -> None:
        spec = AnchorSpec.from_anchor(Anchor(point=(0.05, 0.0, 0.0)))

        assert spec.point_x_mm == pytest.approx(50.0)

    def test_direction_is_unitless(self) -> None:
        spec = AnchorSpec.from_anchor(Anchor(direction=(1.0, 0.0, 0.0)))

        assert (spec.direction_x, spec.direction_y, spec.direction_z) == pytest.approx(
            (1.0, 0.0, 0.0)
        )

    def test_round_trip_preserves_the_point(self) -> None:
        original = Anchor(point=(0.1, -0.2, 0.3), direction=(0.0, 1.0, 0.0))

        restored = AnchorSpec.from_anchor(original).to_anchor()

        assert restored.point == pytest.approx(original.point)


class TestJointSpec:
    def test_round_trip_preserves_a_trajectory(self) -> None:
        original = trajectory_joint(name="rail", origin=(1.0, 0.0, 0.0), target=(5.0, 0.0, 0.0))

        restored = JointSpec.from_joint(JointSave(original, value=0.0)).to_joint()

        assert restored.origin == pytest.approx(original.origin)
        assert restored.target == pytest.approx(original.target)

    def test_round_trip_preserves_the_kind(self) -> None:
        restored = JointSpec.from_joint(JointSave(axis_joint(), value=0.0)).to_joint()

        assert restored.kind is axis_joint().kind

    def test_round_trip_preserves_the_initial_frame(self) -> None:
        original = axis_joint(alignment=to_transform(PlacementDisplay(z_mm=250.0)))

        restored = JointSpec.from_joint(JointSave(original, value=0.0)).to_joint()

        assert restored.alignment.xyz == pytest.approx((0.0, 0.0, 0.25))

    def test_origin_is_in_millimetres(self) -> None:
        spec = JointSpec.from_joint(JointSave(trajectory_joint(origin=(1.5, 0.0, 0.0))))

        assert spec.origin_x_mm == pytest.approx(1500.0)

    def test_an_axis_value_is_in_degrees(self) -> None:
        spec = JointSpec.from_joint(JointSave(axis_joint(), value=math.pi / 2.0))

        assert spec.value == pytest.approx(90.0)

    def test_a_trajectory_value_is_in_millimetres(self) -> None:
        spec = JointSpec.from_joint(JointSave(trajectory_joint(), value=0.5))

        assert spec.value == pytest.approx(500.0)

    def test_an_axis_value_comes_back_in_radians(self) -> None:
        spec = JointSpec.from_joint(JointSave(axis_joint(), value=math.pi / 2.0))

        assert spec.to_value() == pytest.approx(math.pi / 2.0)

    def test_a_trajectory_value_comes_back_in_metres(self) -> None:
        spec = JointSpec.from_joint(JointSave(trajectory_joint(), value=0.5))

        assert spec.to_value() == pytest.approx(0.5)

    def test_axis_limits_are_in_degrees(self) -> None:
        spec = JointSpec.from_joint(JointSave(axis_joint(limits=(-math.pi, math.pi))))

        assert spec.lower_limit == pytest.approx(-180.0)

    def test_trajectory_limits_are_in_millimetres(self) -> None:
        spec = JointSpec.from_joint(JointSave(trajectory_joint(limits=(0.0, 2.0))))

        assert spec.upper_limit == pytest.approx(2000.0)

    def test_a_joint_without_limits_stays_without_them(self) -> None:
        restored = JointSpec.from_joint(JointSave(axis_joint(limits=None), value=0.0)).to_joint()

        assert restored.limits is None

    def test_the_parent_is_recorded_by_name(self) -> None:
        spec = JointSpec.from_joint(JointSave(axis_joint(), value=0.0), parent_name="rail")

        assert spec.parent == "rail"

    def test_a_top_level_joint_has_no_parent(self) -> None:
        assert JointSpec.from_joint(JointSave(axis_joint(), value=0.0)).parent is None


class TestJointsInTheFile:
    def test_joints_survive_the_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert [joint.name for joint in read_project(path).joints] == ["rail", "turn"]

    def test_the_carrying_joint_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).joints[1].parent == "rail"

    def test_a_live_value_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).joints[0].value == pytest.approx(500.0)

    def test_a_binding_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).models[1].bound_to == "rail"

    def test_an_anchor_survives(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).models[1].anchor.point_x_mm == pytest.approx(50.0)

    def test_an_unbound_model_has_no_binding(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        assert read_project(path).models[0].bound_to is None

    def test_units_in_the_file_are_millimetres(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        written = json.loads(path.read_text(encoding="utf-8"))

        assert written["joints"][0]["origin_x_mm"] == pytest.approx(1000.0)

    def test_an_axis_value_in_the_file_is_in_degrees(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        write_project(path, sample_project(path))

        written = json.loads(path.read_text(encoding="utf-8"))

        assert written["joints"][1]["value"] == pytest.approx(45.0)


class TestOlderFiles:
    """A version-2 file predates joints entirely and must still open."""

    def _version_2_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "old.pssim"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "models": [{"name": "gantry", "file": "models/gantry.step"}],
                    "selected": "gantry",
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_it_loads(self, tmp_path: Path) -> None:
        assert read_project(self._version_2_file(tmp_path)).selected == "gantry"

    def test_it_has_no_joints(self, tmp_path: Path) -> None:
        assert read_project(self._version_2_file(tmp_path)).joints == ()

    def test_its_models_are_unbound(self, tmp_path: Path) -> None:
        assert read_project(self._version_2_file(tmp_path)).models[0].bound_to is None

    def test_its_models_get_the_default_anchor(self, tmp_path: Path) -> None:
        anchor = read_project(self._version_2_file(tmp_path)).models[0].anchor.to_anchor()

        assert anchor.direction == pytest.approx((0.0, 0.0, 1.0))

    def test_an_unknown_joint_kind_is_refused(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(
            json.dumps(
                {"version": 2, "joints": [{"name": "x", "kind": "helical", "variable": "v"}]}
            ),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_a_trajectory_with_no_length_is_refused_when_read(self, tmp_path: Path) -> None:
        # Origin and target coincide, which defines no path at all. It has to be
        # caught here, at the boundary: the same check further in would fire
        # halfway through a load, with models already on screen.
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(
            json.dumps(
                {"version": 3, "joints": [{"name": "x", "kind": "trajectory", "variable": "v"}]}
            ),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_an_axis_with_a_zero_direction_is_refused_when_read(self, tmp_path: Path) -> None:
        # An axis has no second point any more, so this is its degenerate case.
        from pssim.domain.errors import ConfigError

        path = tmp_path / "line.pssim"
        path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "joints": [
                        {
                            "name": "x",
                            "kind": "axis",
                            "variable": "v",
                            "direction": {"x": 0.0, "y": 0.0, "z": 0.0},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_an_axis_without_the_field_keeps_the_direction_it_implied(self, tmp_path: Path) -> None:
        # A file written before the field existed described an axis by two
        # points. Falling back to the default +Z would silently turn every axis
        # that ran along X or Y — the kind of change nobody notices until a model
        # spins the wrong way.
        path = tmp_path / "older.pssim"
        path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "joints": [
                        {"name": "x", "kind": "axis", "variable": "v", "target_x_mm": 1000.0}
                    ],
                }
            ),
            encoding="utf-8",
        )

        joint = read_project(path).joints[0].to_joint()

        assert direction_of(joint) == pytest.approx((1.0, 0.0, 0.0))

    def test_an_axis_with_neither_falls_back_to_z(self, tmp_path: Path) -> None:
        path = tmp_path / "older.pssim"
        path.write_text(
            json.dumps({"version": 3, "joints": [{"name": "x", "kind": "axis", "variable": "v"}]}),
            encoding="utf-8",
        )

        joint = read_project(path).joints[0].to_joint()

        assert direction_of(joint) == pytest.approx((0.0, 0.0, 1.0))


class TestDisplayFlagsInTheFile:
    """Hiding a model or its cross is a deliberate act, so it survives a save."""

    def test_a_model_is_visible_by_default(self) -> None:
        assert ModelSpec(name="a", file="a.step").visible is True

    def test_a_models_cross_is_shown_by_default(self) -> None:
        assert ModelSpec(name="a", file="a.step").show_axes is True

    def test_a_joints_cross_is_shown_by_default(self) -> None:
        spec = JointSpec.from_joint(JointSave(axis_joint()))

        assert spec.show_axes is True

    def test_a_hidden_model_is_written(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        project = build_project(
            models=(
                ModelSave(
                    name="housing",
                    path=tmp_path / "housing.step",
                    placement=Transform(),
                    is_visible=False,
                ),
            ),
            project_path=path,
        )
        write_project(path, project)

        assert read_project(path).models[0].visible is False

    def test_a_hidden_cross_is_written(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        project = build_project(
            models=(
                ModelSave(
                    name="housing",
                    path=tmp_path / "housing.step",
                    placement=Transform(),
                    show_axes=False,
                ),
            ),
            project_path=path,
        )
        write_project(path, project)

        assert read_project(path).models[0].show_axes is False

    def test_a_joints_hidden_cross_is_written(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        project = build_project(
            models=(),
            project_path=path,
            scene=SceneSpec(
                joints=(JointSpec.from_joint(JointSave(axis_joint(), show_axes=False)),)
            ),
        )
        write_project(path, project)

        assert read_project(path).joints[0].show_axes is False

    def test_the_two_model_flags_are_independent(self, tmp_path: Path) -> None:
        path = tmp_path / "line.pssim"
        project = build_project(
            models=(
                ModelSave(
                    name="housing",
                    path=tmp_path / "housing.step",
                    placement=Transform(),
                    show_axes=False,
                ),
            ),
            project_path=path,
        )
        write_project(path, project)

        assert read_project(path).models[0].visible is True

    def test_a_version_2_file_gets_the_defaults(self, tmp_path: Path) -> None:
        # A file predating the flags must not open with everything hidden.
        path = tmp_path / "old.pssim"
        path.write_text(
            json.dumps({"version": 2, "models": [{"name": "a", "file": "a.step"}]}),
            encoding="utf-8",
        )

        model = read_project(path).models[0]

        assert (model.visible, model.show_axes) == (True, True)


class TestColorSpec:
    def test_the_default_is_opaque_white(self) -> None:
        assert ColorSpec().to_color() == (1.0, 1.0, 1.0, 1.0)

    def test_round_trip_preserves_the_colour(self) -> None:
        original = (0.25, 0.5, 0.75, 1.0)

        assert ColorSpec.from_color(original).to_color() == pytest.approx(original)

    def test_a_component_above_one_is_refused(self) -> None:
        # The file is written by the app, but a hand edit must not silently
        # produce a colour Panda3D would clamp differently.
        with pytest.raises(ValidationError):
            ColorSpec(red=1.5)

    def test_a_negative_component_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ColorSpec(green=-0.1)


class TestColorsAndNamesInTheFile:
    def _saved(self, tmp_path: Path) -> Path:
        path = tmp_path / "line.pssim"
        project = build_project(
            models=(
                ModelSave(
                    name="gantry",
                    path=tmp_path / "gantry.step",
                    placement=Transform(),
                    color=(1.0, 0.0, 0.0, 1.0),
                ),
            ),
            project_path=path,
            scene=SceneSpec(
                joints=(
                    JointSpec.from_joint(
                        JointSave(
                            axis_joint(name="turn"),
                            show_name=False,
                            color=(0.0, 1.0, 0.0, 1.0),
                        )
                    ),
                ),
                show_joint_names=False,
            ),
        )
        write_project(path, project)
        return path

    def test_a_models_colour_survives(self, tmp_path: Path) -> None:
        color = read_project(self._saved(tmp_path)).models[0].color

        assert color is not None
        assert color.to_color() == pytest.approx((1.0, 0.0, 0.0, 1.0))

    def test_a_joints_colour_survives(self, tmp_path: Path) -> None:
        color = read_project(self._saved(tmp_path)).joints[0].color

        assert color is not None
        assert color.to_color() == pytest.approx((0.0, 1.0, 0.0, 1.0))

    def test_a_hidden_joint_name_survives(self, tmp_path: Path) -> None:
        assert read_project(self._saved(tmp_path)).joints[0].show_name is False

    def test_the_scene_wide_switch_survives(self, tmp_path: Path) -> None:
        assert read_project(self._saved(tmp_path)).show_joint_names is False

    def test_no_override_stays_absent(self, tmp_path: Path) -> None:
        # Absent and "white" are different states; the file must not turn one
        # into the other, or a model would lose its CAD colours on a reload.
        path = tmp_path / "plain.pssim"
        write_project(
            path,
            build_project(
                models=(
                    ModelSave(name="gantry", path=tmp_path / "gantry.step", placement=Transform()),
                ),
                project_path=path,
            ),
        )

        assert read_project(path).models[0].color is None

    def test_the_colour_is_written_as_named_components(self, tmp_path: Path) -> None:
        written = json.loads(self._saved(tmp_path).read_text(encoding="utf-8"))

        assert written["models"][0]["color"]["red"] == pytest.approx(1.0)

    def test_a_version_2_file_gets_the_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "old.pssim"
        path.write_text(
            json.dumps({"version": 2, "models": [{"name": "a", "file": "a.step"}]}),
            encoding="utf-8",
        )

        project = read_project(path)

        assert project.models[0].color is None
        assert project.show_joint_names is True


class TestHighlightColourAndSizesInTheFile:
    def _saved(self, tmp_path: Path) -> Path:
        path = tmp_path / "line.pssim"
        write_project(
            path,
            build_project(
                models=(
                    ModelSave(
                        name="gantry",
                        path=tmp_path / "gantry.step",
                        placement=Transform(),
                        highlight_color=(0.0, 1.0, 1.0, 1.0),
                    ),
                ),
                project_path=path,
                scene=SceneSpec(text_size_mm=120.0, cross_size_mm=400.0),
            ),
        )
        return path

    def test_a_highlight_colour_survives(self, tmp_path: Path) -> None:
        color = read_project(self._saved(tmp_path)).models[0].highlight_color

        assert color is not None
        assert color.to_color() == pytest.approx((0.0, 1.0, 1.0, 1.0))

    def test_it_is_separate_from_the_body_colour(self, tmp_path: Path) -> None:
        assert read_project(self._saved(tmp_path)).models[0].color is None

    def test_the_text_size_survives(self, tmp_path: Path) -> None:
        assert read_project(self._saved(tmp_path)).text_size_mm == pytest.approx(120.0)

    def test_the_cross_size_survives(self, tmp_path: Path) -> None:
        assert read_project(self._saved(tmp_path)).cross_size_mm == pytest.approx(400.0)

    def test_the_sizes_are_in_millimetres_in_the_file(self, tmp_path: Path) -> None:
        written = json.loads(self._saved(tmp_path).read_text(encoding="utf-8"))

        assert written["text_size_mm"] == pytest.approx(120.0)
        assert written["cross_size_mm"] == pytest.approx(400.0)

    def test_an_older_files_label_size_becomes_the_text_size(self, tmp_path: Path) -> None:
        # `StrictModel` forbids unknown fields, so a project saved by the build
        # that had `label_size_mm` would refuse to open without this.
        path = tmp_path / "older.pssim"
        path.write_text(json.dumps({"version": 3, "label_size_mm": 80.0}), encoding="utf-8")

        project = read_project(path)

        assert project.text_size_mm == pytest.approx(80.0)
        assert project.cross_size_mm == pytest.approx(200.0)

    def test_a_zero_text_size_is_refused_when_read(self, tmp_path: Path) -> None:
        # A label scaled to nothing is invisible, and there would be no way to
        # tell that from "the names are broken". Checked on `ProjectSpec`, which
        # is the file boundary — `SceneSpec` is a plain dataclass handed in by
        # the ui layer, and validating the same thing twice is how the two drift.
        from pssim.domain.errors import ConfigError

        path = tmp_path / "bad.pssim"
        path.write_text(json.dumps({"version": 3, "text_size_mm": 0.0}), encoding="utf-8")

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_a_negative_cross_size_is_refused_when_read(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "bad.pssim"
        path.write_text(json.dumps({"version": 3, "cross_size_mm": -5.0}), encoding="utf-8")

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_a_version_2_file_gets_the_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "old.pssim"
        path.write_text(
            json.dumps({"version": 2, "models": [{"name": "a", "file": "a.step"}]}),
            encoding="utf-8",
        )

        project = read_project(path)

        assert project.models[0].highlight_color is None
        assert project.text_size_mm == pytest.approx(50.0)
        assert project.cross_size_mm == pytest.approx(200.0)


class TestAxisFieldsInTheFile:
    def _saved(self, tmp_path: Path) -> Path:
        path = tmp_path / "line.pssim"
        write_project(
            path,
            build_project(
                models=(),
                project_path=path,
                scene=SceneSpec(
                    joints=(
                        JointSpec.from_joint(
                            JointSave(
                                axis_joint(
                                    name="turn",
                                    direction=(0.0, 2.0, 0.0),
                                    initial_angle_rad=math.pi / 2.0,
                                )
                            )
                        ),
                    )
                ),
            ),
        )
        return path

    def test_the_direction_survives(self, tmp_path: Path) -> None:
        spec = read_project(self._saved(tmp_path)).joints[0]

        assert spec.direction is not None
        assert spec.direction.to_direction() == pytest.approx((0.0, 2.0, 0.0))

    def test_the_direction_is_unitless_in_the_file(self, tmp_path: Path) -> None:
        # No `_mm` suffix and no conversion: only the direction matters.
        written = json.loads(self._saved(tmp_path).read_text(encoding="utf-8"))

        assert written["joints"][0]["direction"] == {"x": 0.0, "y": 2.0, "z": 0.0}

    def test_the_init_rotation_is_in_degrees(self, tmp_path: Path) -> None:
        written = json.loads(self._saved(tmp_path).read_text(encoding="utf-8"))

        assert written["joints"][0]["initial_angle_deg"] == pytest.approx(90.0)

    def test_the_init_rotation_comes_back_in_radians(self, tmp_path: Path) -> None:
        joint = read_project(self._saved(tmp_path)).joints[0].to_joint()

        assert joint.initial_angle_rad == pytest.approx(math.pi / 2.0)

    def test_the_magnitude_is_preserved_not_normalised(self, tmp_path: Path) -> None:
        # Storing the number the user typed rather than a normalised one: the
        # file should read back as what was entered.
        joint = read_project(self._saved(tmp_path)).joints[0].to_joint()

        assert joint.direction == pytest.approx((0.0, 2.0, 0.0))


class TestOriginCrossInTheFile:
    def _saved(self, tmp_path: Path) -> Path:
        path = tmp_path / "line.pssim"
        write_project(
            path,
            build_project(
                models=(),
                project_path=path,
                scene=SceneSpec(origin_cross_size_mm=750.0, show_origin_cross=False),
            ),
        )
        return path

    def test_its_size_survives(self, tmp_path: Path) -> None:
        assert read_project(self._saved(tmp_path)).origin_cross_size_mm == pytest.approx(750.0)

    def test_its_switch_survives(self, tmp_path: Path) -> None:
        assert read_project(self._saved(tmp_path)).show_origin_cross is False

    def test_it_is_independent_of_the_item_cross_size(self, tmp_path: Path) -> None:
        # The point of giving it its own field.
        assert read_project(self._saved(tmp_path)).cross_size_mm == pytest.approx(200.0)

    def test_the_size_is_in_millimetres_in_the_file(self, tmp_path: Path) -> None:
        written = json.loads(self._saved(tmp_path).read_text(encoding="utf-8"))

        assert written["origin_cross_size_mm"] == pytest.approx(750.0)

    def test_a_zero_size_is_refused_when_read(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "bad.pssim"
        path.write_text(json.dumps({"version": 3, "origin_cross_size_mm": 0.0}), encoding="utf-8")

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_a_file_without_the_fields_gets_the_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "older.pssim"
        path.write_text(json.dumps({"version": 3}), encoding="utf-8")

        project = read_project(path)

        assert project.origin_cross_size_mm == pytest.approx(200.0)
        assert project.show_origin_cross is True


class TestSensorFieldsInTheFile:
    def _spec(self, sensor: Sensor, mounted_on: str | None = None) -> SensorSpec:
        return SensorSpec.from_sensor(sensor, mounted_on)

    def _saved(self, tmp_path: Path, spec: SensorSpec) -> Path:
        path = tmp_path / "line.pssim"
        write_project(
            path,
            build_project(models=(), project_path=path, scene=SceneSpec(sensors=(spec,))),
        )
        return path

    def test_the_variable_survives(self, tmp_path: Path) -> None:
        sensor = beam_sensor(name="gate", variable="gate_signal")

        path = self._saved(tmp_path, self._spec(sensor))

        assert read_project(path).sensors[0].variable == "gate_signal"

    def test_the_direction_survives(self, tmp_path: Path) -> None:
        sensor = beam_sensor(name="gate", direction=(0.0, 2.0, 0.0))

        restored = read_project(self._saved(tmp_path, self._spec(sensor))).sensors[0].to_sensor()

        assert restored.direction == pytest.approx((0.0, 2.0, 0.0))

    def test_the_range_survives_in_millimetres(self, tmp_path: Path) -> None:
        sensor = beam_sensor(name="gate", direction=(1.0, 0.0, 0.0), range_m=1.5)

        path = self._saved(tmp_path, self._spec(sensor))

        assert read_project(path).sensors[0].range_mm == pytest.approx(1500.0)
        assert read_project(path).sensors[0].to_sensor().range_m == pytest.approx(1.5)

    def test_every_kind_survives(self, tmp_path: Path) -> None:
        for kind in SensorKind:
            sensor = (
                Sensor(name="s", kind=kind, variable="v")
                if kind in ENCODER_KINDS or kind is SensorKind.PROXIMITY
                else beam_sensor(name="s", kind=kind, direction=(1.0, 0.0, 0.0))
            )
            path = self._saved(tmp_path, self._spec(sensor))

            assert read_project(path).sensors[0].to_sensor().kind is kind

    def test_the_resolution_survives(self, tmp_path: Path) -> None:
        sensor = Sensor(
            name="enc",
            kind=SensorKind.ENCODER_ABS,
            variable="enc",
            counts_per_revolution=4096,
        )

        path = self._saved(tmp_path, self._spec(sensor))

        assert read_project(path).sensors[0].to_sensor().counts_per_revolution == 4096

    def test_the_mount_is_recorded_by_name(self, tmp_path: Path) -> None:
        # Ids are per-session and would mean nothing after a reload (R7).
        sensor = beam_sensor(name="gate")

        path = self._saved(tmp_path, self._spec(sensor, mounted_on="carriage"))

        assert read_project(path).sensors[0].mounted_on == "carriage"

    def test_an_unmounted_sensor_records_no_mount(self, tmp_path: Path) -> None:
        path = self._saved(tmp_path, self._spec(beam_sensor(name="gate")))

        assert read_project(path).sensors[0].mounted_on is None

    def test_a_zero_range_is_refused_when_read(self, tmp_path: Path) -> None:
        from pssim.domain.errors import ConfigError

        path = tmp_path / "bad.pssim"
        path.write_text(
            json.dumps({"version": 3, "sensors": [{"name": "s", "kind": "beam", "range_mm": 0.0}]}),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="invalid project"):
            read_project(path)

    def test_an_older_two_point_beam_keeps_its_direction(self, tmp_path: Path) -> None:
        # A file written when a beam was two points described a direction with
        # them; the default +Z would silently turn every beam along X or Y.
        path = tmp_path / "older.pssim"
        path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "sensors": [{"name": "s", "kind": "beam", "target_x_mm": 500.0}],
                }
            ),
            encoding="utf-8",
        )

        sensor = read_project(path).sensors[0].to_sensor()

        assert unit_direction(sensor) == pytest.approx((1.0, 0.0, 0.0))

    def test_an_older_two_point_beam_keeps_its_reach(self, tmp_path: Path) -> None:
        path = tmp_path / "older.pssim"
        path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "sensors": [{"name": "s", "kind": "beam", "target_x_mm": 500.0}],
                }
            ),
            encoding="utf-8",
        )

        assert read_project(path).sensors[0].to_sensor().range_m == pytest.approx(0.5)
