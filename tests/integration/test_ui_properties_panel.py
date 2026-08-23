"""Tests of `PropertiesPanel`: what it shows for a selection, what it reports
when edited, and — the part most easily got wrong — that refreshing it does not
destroy a row the user is dragging.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.domain.machine import Transform  # noqa: E402
from pssim.domain.model_joints import ModelJoint  # noqa: E402
from pssim.domain.sensors import Sensor, SensorKind, SensorReading  # noqa: E402
from pssim.ui.joint_registry import JointRegistry  # noqa: E402
from pssim.ui.model_registry import ModelEntry  # noqa: E402
from pssim.ui.properties_panel import PropertiesPanel  # noqa: E402
from pssim.ui.sensor_registry import SensorEntry  # noqa: E402
from pssim.viz.sensor_markers import ACTIVE_COLOR  # noqa: E402
from tests.factories import axis_joint, beam_sensor, trajectory_joint  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def panel(qt_app: QApplication) -> PropertiesPanel:
    return PropertiesPanel()


def entry(
    model_id: str = "model-1",
    name: str = "gantry",
    placement: Transform | None = None,
) -> ModelEntry:
    return ModelEntry(
        model_id=model_id,
        name=name,
        path=Path("C:/models/gantry.step"),
        placement=placement or Transform(),
        node_count=7,
        triangle_count=1234,
    )


def sensor_entry(
    sensor: Sensor | None = None,
    sensor_id: str = "sensor-1",
    *,
    is_active: bool = False,
    mounted_on: str | None = None,
    reading: SensorReading | None = None,
) -> SensorEntry:
    return SensorEntry(
        sensor_id=sensor_id,
        sensor=sensor if sensor is not None else beam_sensor(name="gate"),
        is_active=is_active,
        mounted_on=mounted_on,
        reading=reading if reading is not None else SensorReading(value=0.0),
    )


def joints_for(model_id: str = "model-1") -> JointRegistry:
    registry = JointRegistry()
    registry.add(axis_joint(name="tilt"))
    registry.add(trajectory_joint(name="slide"))
    return registry


class TestEmptyState:
    def test_nothing_is_shown_at_the_start(self, panel: PropertiesPanel) -> None:
        assert panel.model_id is None

    def test_clear_forgets_the_model(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())

        panel.clear()

        assert panel.model_id is None


class TestShowingAModel:
    def test_it_remembers_which_model_it_shows(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(model_id="model-9"), ())

        assert panel.model_id == "model-9"

    def test_the_name_is_shown(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(name="conveyor"), ())

        assert panel.name_edit.text() == "conveyor"

    def test_read_only_counts_are_shown(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())

        assert panel.parts_label.text() == "7"
        assert "1" in panel.triangles_label.text()

    def test_the_full_path_is_in_the_tooltip(self, panel: PropertiesPanel) -> None:
        # The label shows only the file name so a long CAD path cannot force
        # the dock wider than the window.
        panel.show_model(entry(), ())

        assert panel.file_label.text() == "gantry.step"
        assert "models" in panel.file_label.toolTip()

    def test_placement_is_shown_in_millimetres_and_degrees(self, panel: PropertiesPanel) -> None:
        placement = Transform(xyz=(0.25, 0.0, 0.0), rpy=(0.0, 0.0, math.pi / 2))

        panel.show_model(entry(placement=placement), ())

        assert panel.x_spin.value() == pytest.approx(250.0)
        assert panel.rotate_z_spin.value() == pytest.approx(90.0)

    def test_a_model_in_the_scene_reports_no_binding(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())

        assert panel.bound_to_label.text() == "—"

    def test_the_binding_target_is_shown_when_given(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), (), bound_to_name="arm / tilt")

        assert panel.bound_to_label.text() == "arm / tilt"

    def test_one_row_per_joint(self, panel: PropertiesPanel) -> None:
        registry = joints_for()

        panel.show_model(entry(), registry.entries)

        assert panel.row_for(registry.entries[0].joint_id) is not None
        assert panel.row_for(registry.entries[1].joint_id) is not None

    def test_a_model_with_no_joints_has_no_rows(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())

        assert panel.row_for("joint-1") is None


class TestReportingEdits:
    def test_editing_the_name_reports_it(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())
        seen: list[tuple[str, str]] = []

        def record(model_id: str, name: str) -> None:
            seen.append((model_id, name))

        panel.name_edited.connect(record)

        panel.name_edit.setText("conveyor")
        panel.name_edit.editingFinished.emit()

        assert seen == [("model-1", "conveyor")]

    def test_a_blank_name_is_not_reported(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())
        seen: list[tuple[str, str]] = []

        def record(model_id: str, name: str) -> None:
            seen.append((model_id, name))

        panel.name_edited.connect(record)

        panel.name_edit.setText("   ")
        panel.name_edit.editingFinished.emit()

        assert seen == []

    def test_editing_the_placement_reports_internal_units(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())
        seen: list[tuple[str, Transform]] = []

        def record(model_id: str, placement: Transform) -> None:
            seen.append((model_id, placement))

        panel.placement_edited.connect(record)

        panel.x_spin.setValue(500.0)

        assert seen
        model_id, placement = seen[-1]
        assert model_id == "model-1"
        assert placement.xyz[0] == pytest.approx(0.5)

    def test_editing_a_rotation_reports_radians(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())
        seen: list[Transform] = []

        def record(_model_id: str, placement: Transform) -> None:
            seen.append(placement)

        panel.placement_edited.connect(record)

        panel.rotate_z_spin.setValue(90.0)

        assert seen[-1].rpy[2] == pytest.approx(math.pi / 2)

    def test_a_joint_row_edit_is_forwarded(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_model(entry(), registry.entries)
        joint_id = registry.entries[0].joint_id
        seen: list[tuple[str, float]] = []

        def record(joint_id: str, value: float) -> None:
            seen.append((joint_id, value))

        panel.joint_value_edited.connect(record)
        row = panel.row_for(joint_id)
        assert row is not None

        row.value_spin.setValue(45.0)  # an AXIS row, so degrees in, radians out

        assert seen
        assert seen[-1][0] == joint_id
        assert seen[-1][1] == pytest.approx(math.pi / 4)

    def test_showing_a_model_does_not_report_anything(self, panel: PropertiesPanel) -> None:
        # Filling the fields is not a user edit; reporting it would write the
        # values straight back and could fight whatever set them.
        seen: list[object] = []

        def record(*args: object) -> None:
            seen.append(args)

        panel.placement_edited.connect(record)
        panel.name_edited.connect(record)

        panel.show_model(entry(placement=Transform(xyz=(1.0, 2.0, 3.0)), name="x"), ())

        assert seen == []


class TestSilentUpdates:
    def test_a_silent_placement_update_does_not_report(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())
        seen: list[object] = []

        def record(*args: object) -> None:
            seen.append(args)

        panel.placement_edited.connect(record)

        panel.set_placement_silently(Transform(xyz=(0.75, 0.0, 0.0)))

        assert panel.x_spin.value() == pytest.approx(750.0)
        assert seen == []

    def test_a_silent_name_update_does_not_report(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())
        seen: list[object] = []

        def record(*args: object) -> None:
            seen.append(args)

        panel.name_edited.connect(record)

        panel.set_name_silently("gantry (2)")

        assert panel.name_edit.text() == "gantry (2)"
        assert seen == []

    def test_a_silent_joint_value_update_does_not_report(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_model(entry(), registry.entries)
        joint_id = registry.entries[0].joint_id
        seen: list[object] = []

        def record(*args: object) -> None:
            seen.append(args)

        panel.joint_value_edited.connect(record)

        panel.set_joint_value_silently(joint_id, math.pi / 4)

        row = panel.row_for(joint_id)
        assert row is not None
        assert row.value_spin.value() == pytest.approx(45.0)
        assert seen == []


class TestRebuildingRows:
    def test_showing_the_same_model_keeps_the_same_row_widgets(
        self, panel: PropertiesPanel
    ) -> None:
        # The one that matters: a refresh must not destroy the slider being
        # dragged, or the drag is swallowed mid-gesture.
        registry = joints_for()
        panel.show_model(entry(), registry.entries)
        joint_id = registry.entries[0].joint_id
        row_before = panel.row_for(joint_id)

        panel.show_model(entry(), registry.entries)

        assert panel.row_for(joint_id) is row_before

    def test_rows_are_rebuilt_when_the_joint_set_changes(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_model(entry(), registry.entries)
        first_id = registry.entries[0].joint_id
        row_before = panel.row_for(first_id)

        panel.show_model(entry(), registry.entries[:1])

        assert panel.row_for(first_id) is not row_before
        assert panel.row_for(registry.entries[1].joint_id) is None

    def test_switching_model_replaces_the_rows(self, panel: PropertiesPanel) -> None:
        # Two separate registries hand out the same ids (`joint-1`, ...), which
        # is exactly the case that must still rebuild: a row captures its
        # joint's kind and limits when built, so keeping one across a model
        # switch would show another model's units and range.
        first = joints_for("model-1")
        panel.show_model(entry(model_id="model-1"), first.entries)
        row_before = panel.row_for(first.entries[0].joint_id)
        assert row_before is not None

        second = joints_for("model-2")
        panel.show_model(entry(model_id="model-2"), second.entries)

        assert panel.row_for(second.entries[0].joint_id) is not row_before

    def test_showing_the_same_model_refreshes_row_values(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_model(entry(), registry.entries)
        joint_id = registry.entries[0].joint_id
        registry.set_value(joint_id, math.pi / 2)

        panel.show_model(entry(), registry.entries)

        row = panel.row_for(joint_id)
        assert row is not None
        assert row.value_spin.value() == pytest.approx(90.0)


class TestJointMode:
    def test_showing_a_joint_hides_the_model_fields(self, panel: PropertiesPanel) -> None:
        registry = joints_for()

        panel.show_joint(registry.entries[0], carried_by="gantry")

        assert panel.joint_id == registry.entries[0].joint_id
        assert panel.model_id is None

    def test_the_joints_own_name_and_variable_are_shown(self, panel: PropertiesPanel) -> None:
        registry = joints_for()

        panel.show_joint(registry.entries[0], carried_by="gantry")

        assert panel.joint_name_edit.text() == "tilt"
        assert panel.joint_variable_edit.text() == "axis-1"

    def test_what_carries_the_joint_is_named(self, panel: PropertiesPanel) -> None:
        registry = joints_for()

        panel.show_joint(registry.entries[0], carried_by="gantry")

        assert panel.joint_parent_label.text() == "gantry"

    def test_the_two_points_are_shown_in_millimetres(self, panel: PropertiesPanel) -> None:
        registry = JointRegistry()
        registry.add(trajectory_joint(name="slide", target=(0.4, 0.0, 0.0)))

        panel.show_joint(registry.entries[0])

        assert panel.joint_target_x_spin.value() == pytest.approx(400.0)

    def test_axis_limits_are_in_degrees(self, panel: PropertiesPanel) -> None:
        registry = JointRegistry()
        registry.add(axis_joint(name="tilt", limits=(0.0, math.pi / 2)))

        panel.show_joint(registry.entries[0])

        assert panel.joint_limit_checkbox.isChecked() is True
        assert panel.joint_upper_limit_spin.value() == pytest.approx(90.0)
        assert panel.joint_upper_limit_spin.suffix() == " °"

    def test_trajectory_limits_are_in_millimetres(self, panel: PropertiesPanel) -> None:
        registry = JointRegistry()
        registry.add(trajectory_joint(name="slide", limits=(0.0, 0.25)))

        panel.show_joint(registry.entries[0])

        assert panel.joint_upper_limit_spin.value() == pytest.approx(250.0)
        assert panel.joint_upper_limit_spin.suffix() == " mm"

    def test_an_unlimited_joint_leaves_the_checkbox_clear(self, panel: PropertiesPanel) -> None:
        registry = joints_for()

        panel.show_joint(registry.entries[0])

        assert panel.joint_limit_checkbox.isChecked() is False
        assert panel.joint_lower_limit_spin.isEnabled() is False

    def test_the_joint_gets_a_value_row(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        joint_id = registry.entries[0].joint_id

        panel.show_joint(registry.entries[0])

        assert panel.row_for(joint_id) is not None

    def test_showing_a_joint_reports_nothing(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        seen: list[object] = []

        def record(*args: object) -> None:
            seen.append(args)

        panel.joint_edited.connect(record)

        panel.show_joint(registry.entries[0])

        assert seen == []

    def test_editing_a_point_reports_internal_units(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_joint(registry.entries[0])
        seen: list[tuple[str, object]] = []

        def record(joint_id: str, joint: object) -> None:
            seen.append((joint_id, joint))

        panel.joint_edited.connect(record)

        panel.joint_origin_x_spin.setValue(250.0)

        assert seen
        reported_id, reported = seen[-1]
        assert reported_id == registry.entries[0].joint_id
        assert isinstance(reported, ModelJoint)
        assert reported.origin[0] == pytest.approx(0.25)

    def test_editing_the_name_reports_the_joint(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_joint(registry.entries[0])
        seen: list[ModelJoint] = []

        def record(_joint_id: str, joint: ModelJoint) -> None:
            seen.append(joint)

        panel.joint_edited.connect(record)

        panel.joint_name_edit.setText("swivel")
        panel.joint_name_edit.editingFinished.emit()

        assert seen[-1].name == "swivel"

    def test_a_degenerate_edit_reports_nothing_instead_of_raising(
        self, panel: PropertiesPanel
    ) -> None:
        # Both points equal is a state the fields pass through while being
        # edited; it must not throw out of a Qt slot.
        registry = JointRegistry()
        registry.add(trajectory_joint(name="slide", target=(0.4, 0.0, 0.0)))
        panel.show_joint(registry.entries[0])
        seen: list[object] = []

        def record(*args: object) -> None:
            seen.append(args)

        panel.joint_edited.connect(record)

        panel.joint_target_x_spin.setValue(0.0)  # now equal to the origin

        assert seen == []
        assert panel.joint() is None

    def test_switching_from_joint_back_to_model(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_joint(registry.entries[0])

        panel.show_model(entry(), registry.entries)

        assert panel.joint_id is None
        assert panel.model_id == "model-1"

    def test_a_silent_value_update_reaches_the_joint_view_row(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        joint_id = registry.entries[0].joint_id
        panel.show_joint(registry.entries[0])

        panel.set_joint_value_silently(joint_id, math.pi / 4)

        row = panel.row_for(joint_id)
        assert row is not None
        assert row.value_spin.value() == pytest.approx(45.0)

    def test_the_value_row_survives_a_repeated_show(self, panel: PropertiesPanel) -> None:
        # Same reasoning as the model view: re-showing must not destroy the
        # slider being dragged.
        registry = joints_for()
        panel.show_joint(registry.entries[0])
        row_before = panel.row_for(registry.entries[0].joint_id)

        panel.show_joint(registry.entries[0])

        assert panel.row_for(registry.entries[0].joint_id) is row_before

    def test_the_value_row_is_rebuilt_when_the_limits_change(self, panel: PropertiesPanel) -> None:
        # The row captures its range when built, so an edited limit has to
        # replace it or the slider would keep the old span.
        registry = JointRegistry()
        entry_before = registry.add(axis_joint(name="tilt"))
        panel.show_joint(entry_before)
        row_before = panel.row_for(entry_before.joint_id)

        edited = registry.replace_joint(
            entry_before.joint_id, axis_joint(name="tilt", limits=(0.0, math.pi / 2))
        )
        assert edited is not None
        panel.show_joint(edited)

        assert panel.row_for(entry_before.joint_id) is not row_before


class TestInitialFrame:
    def test_a_default_joint_shows_a_zero_frame(self, panel: PropertiesPanel) -> None:
        registry = joints_for()

        panel.show_joint(registry.entries[0])

        assert panel.frame_z_spin.value() == pytest.approx(0.0)
        assert panel.frame_rotate_z_spin.value() == pytest.approx(0.0)

    def test_it_shows_the_frame_in_millimetres_and_degrees(self, panel: PropertiesPanel) -> None:
        registry = JointRegistry()
        registry.add(
            trajectory_joint(
                name="slide",
                alignment=Transform(xyz=(0.25, 0.0, 0.0), rpy=(0.0, 0.0, math.pi / 2)),
            )
        )

        panel.show_joint(registry.entries[0])

        assert panel.frame_x_spin.value() == pytest.approx(250.0)
        assert panel.frame_rotate_z_spin.value() == pytest.approx(90.0)

    def test_editing_the_frame_reports_internal_units(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_joint(registry.entries[0])
        seen: list[ModelJoint] = []

        def record(_joint_id: str, joint: ModelJoint) -> None:
            seen.append(joint)

        panel.joint_edited.connect(record)

        panel.frame_z_spin.setValue(500.0)

        assert seen
        assert seen[-1].alignment.xyz[2] == pytest.approx(0.5)

    def test_editing_the_roll_reports_radians(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        panel.show_joint(registry.entries[0])
        seen: list[ModelJoint] = []

        def record(_joint_id: str, joint: ModelJoint) -> None:
            seen.append(joint)

        panel.joint_edited.connect(record)

        panel.frame_rotate_z_spin.setValue(90.0)

        assert seen[-1].alignment.rpy[2] == pytest.approx(math.pi / 2)

    def test_showing_a_joint_does_not_report_the_frame(self, panel: PropertiesPanel) -> None:
        registry = joints_for()
        seen: list[object] = []

        def record(*args: object) -> None:
            seen.append(args)

        panel.joint_edited.connect(record)

        panel.show_joint(registry.entries[0])

        assert seen == []

    def test_the_frame_survives_a_round_trip_through_the_panel(
        self, panel: PropertiesPanel
    ) -> None:
        registry = JointRegistry()
        registry.add(trajectory_joint(name="slide", alignment=Transform(xyz=(0.1, -0.2, 0.3))))
        panel.show_joint(registry.entries[0])

        rebuilt = panel.joint()

        assert rebuilt is not None
        assert rebuilt.alignment.xyz == pytest.approx((0.1, -0.2, 0.3))


class TestSensorMode:
    """The third mode. A sensor is editable in place, like a model and a joint,
    and its live reading is shown beside the fields without disturbing them.
    """

    def test_showing_a_sensor_reveals_its_group(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry())

        assert panel.sensor_id == "sensor-1"

    def test_it_hides_the_model_fields(self, panel: PropertiesPanel) -> None:
        panel.show_model(entry(), ())

        panel.show_sensor(sensor_entry())

        assert panel.model_id is None

    def test_showing_a_model_hides_the_sensor_fields(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry())

        panel.show_model(entry(), ())

        assert panel.sensor_id is None

    def test_clearing_forgets_the_sensor(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry())

        panel.clear()

        assert panel.sensor_id is None

    def test_the_fields_are_filled_from_the_sensor(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(beam_sensor(name="gate", origin=(0.3, 0.0, 0.0))))

        assert panel.sensor_fields.origin_x_spin.value() == pytest.approx(300.0)

    def test_editing_a_field_reports_the_sensor(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(beam_sensor(name="gate")))
        seen: list[tuple[str, Sensor]] = []
        panel.sensor_edited.connect(lambda sensor_id, sensor: seen.append((sensor_id, sensor)))

        panel.sensor_fields.origin_x_spin.setValue(250.0)

        assert seen[-1][0] == "sensor-1"
        assert seen[-1][1].origin[0] == pytest.approx(0.25)

    def test_showing_a_sensor_reports_nothing(self, panel: PropertiesPanel) -> None:
        seen: list[object] = []
        panel.sensor_edited.connect(lambda *args: seen.append(args))

        panel.show_sensor(sensor_entry())

        assert seen == []

    def test_a_half_typed_definition_is_not_reported(self, panel: PropertiesPanel) -> None:
        # Retyping a direction passes through zero, which is not a sensor. It
        # must not throw out of the slot, and must not reach the scene either.
        panel.show_sensor(sensor_entry(beam_sensor(name="gate")))
        seen: list[object] = []
        panel.sensor_edited.connect(lambda *args: seen.append(args))

        for spin in panel.sensor_fields.direction_spins:
            spin.setValue(0.0)

        assert seen == []


class TestSensorMount:
    def test_nothing_is_the_first_choice(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(), (("model-1", "gantry"),))

        assert panel.sensor_mount_combo.itemText(0) == "Nothing (scene origin)"

    def test_the_choices_are_offered(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(), (("model-1", "gantry"), ("joint-1", "rail")))

        offered = [panel.sensor_mount_combo.itemText(i) for i in range(1, 3)]
        assert offered == ["gantry", "rail"]

    def test_the_current_mount_is_selected(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(
            sensor_entry(mounted_on="joint-1"), (("model-1", "gantry"), ("joint-1", "rail"))
        )

        assert panel.sensor_mount_combo.currentText() == "rail"

    def test_a_mount_the_scene_lost_is_still_offered(self, panel: PropertiesPanel) -> None:
        # Otherwise selecting the sensor would silently reseat it on nothing.
        panel.show_sensor(sensor_entry(mounted_on="model-9"), (), mount_name="conveyor")

        assert panel.sensor_mount_combo.currentText() == "conveyor"

    def test_choosing_a_mount_is_reported(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(), (("model-1", "gantry"),))
        seen: list[tuple[str, object]] = []
        panel.sensor_mount_edited.connect(lambda sensor_id, mount: seen.append((sensor_id, mount)))

        panel.sensor_mount_combo.setCurrentIndex(1)

        assert seen == [("sensor-1", "model-1")]

    def test_choosing_nothing_reports_none(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(mounted_on="model-1"), (("model-1", "gantry"),))
        seen: list[tuple[str, object]] = []
        panel.sensor_mount_edited.connect(lambda sensor_id, mount: seen.append((sensor_id, mount)))

        panel.sensor_mount_combo.setCurrentIndex(0)

        assert seen == [("sensor-1", None)]

    def test_filling_the_combo_in_reports_nothing(self, panel: PropertiesPanel) -> None:
        seen: list[object] = []
        panel.sensor_mount_edited.connect(lambda *args: seen.append(args))

        panel.show_sensor(sensor_entry(mounted_on="model-1"), (("model-1", "gantry"),))

        assert seen == []


class TestSensorReading:
    def test_the_state_is_shown(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(is_active=True, reading=SensorReading(value=1.0)))

        assert panel.sensor_state_label.text() == "Detected"

    def test_an_encoder_has_no_state(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(
            sensor_entry(Sensor(name="turns", kind=SensorKind.ENCODER_ABS)),
        )

        assert panel.sensor_state_label.text() == "—"

    def test_the_reading_is_shown_in_millimetres(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(
            sensor_entry(
                beam_sensor(name="dist", kind=SensorKind.TOF),
                reading=SensorReading(value=0.3),
            )
        )

        assert panel.sensor_reading_label.text() == "300.0 mm"

    def test_a_new_reading_refreshes_the_rows(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(beam_sensor(name="gate")))

        panel.set_sensor_reading_silently(
            sensor_entry(beam_sensor(name="gate"), is_active=True, reading=SensorReading(value=1.0))
        )

        assert panel.sensor_state_label.text() == "Detected"

    def test_a_new_reading_leaves_the_fields_alone(self, panel: PropertiesPanel) -> None:
        # The scene re-reads every sensor every frame; if that touched the fields
        # the panel would be unusable.
        panel.show_sensor(sensor_entry(beam_sensor(name="gate")))
        panel.sensor_fields.origin_x_spin.setValue(250.0)

        panel.set_sensor_reading_silently(
            sensor_entry(beam_sensor(name="gate"), is_active=True, reading=SensorReading(value=1.0))
        )

        assert panel.sensor_fields.origin_x_spin.value() == pytest.approx(250.0)

    def test_another_sensors_reading_is_ignored(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(sensor_entry(beam_sensor(name="gate")))

        panel.set_sensor_reading_silently(
            sensor_entry(
                beam_sensor(name="other"),
                sensor_id="sensor-2",
                is_active=True,
                reading=SensorReading(value=1.0),
            )
        )

        assert panel.sensor_state_label.text() == "Clear"

    def test_a_live_reading_gets_the_green_background(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(
            sensor_entry(beam_sensor(name="gate"), is_active=True, reading=SensorReading(value=1.0))
        )

        background = panel.sensor_reading_label.palette().color(QPalette.ColorRole.Window)
        assert background.getRgbF()[:3] == pytest.approx(ACTIVE_COLOR[:3], abs=1.0 / 255.0)

    def test_an_idle_reading_is_not_coloured(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(
            sensor_entry(beam_sensor(name="gate"), is_active=True, reading=SensorReading(value=1.0))
        )

        panel.set_sensor_reading_silently(sensor_entry(beam_sensor(name="gate")))

        background = panel.sensor_reading_label.palette().color(QPalette.ColorRole.Window)
        assert background.getRgbF()[:3] != pytest.approx(ACTIVE_COLOR[:3], abs=1.0 / 255.0)

    def test_an_encoder_is_never_coloured(self, panel: PropertiesPanel) -> None:
        panel.show_sensor(
            sensor_entry(
                Sensor(name="turns", kind=SensorKind.ENCODER_ABS),
                reading=SensorReading(value=512.0),
            )
        )

        background = panel.sensor_reading_label.palette().color(QPalette.ColorRole.Window)
        assert background.getRgbF()[:3] != pytest.approx(ACTIVE_COLOR[:3], abs=1.0 / 255.0)
