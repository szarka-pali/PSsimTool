"""Tests of the joint (axis/trajectory) geometry dialog and the joint chooser.

The unit conversion and validation are covered by
`tests/unit/domain/test_model_joints.py`. This is about the Qt side: whether
the fields match the values, whether the live preview emits changes, whether
picking writes back cleanly, and whether an invalid joint is caught before it
ever reaches the registry.

They run headless through `QT_QPA_PLATFORM=offscreen`.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from pssim.domain.model_joints import (  # noqa: E402
    ModelJoint,
    ModelJointKind,
    direction_of,
    from_model_joint,
)
from pssim.ui.joint_dialog import BindDialog, JointDialog, PickTarget  # noqa: E402
from tests.factories import axis_joint  # noqa: E402

pytestmark = pytest.mark.ui


def _silence_warning(*args: object, **kwargs: object) -> None:
    """Stand-in for `QMessageBox.warning`: a modal has nobody to close it here."""


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def dialog(qt_app: QApplication) -> Iterator[JointDialog]:
    instance = JointDialog()
    yield instance
    instance.close()


class TestFields:
    def test_the_dialog_defaults_to_a_valid_axis(self, dialog: JointDialog) -> None:
        assert dialog.kind is ModelJointKind.AXIS
        assert dialog.origin_spins[0].value() != dialog.target_spins[0].value() or (
            dialog.origin_spins[2].value() != dialog.target_spins[2].value()
        )

    def test_origin_is_in_millimetres(self, dialog: JointDialog) -> None:
        assert dialog.origin_spins[0].suffix().strip() == "mm"

    def test_target_is_in_millimetres(self, dialog: JointDialog) -> None:
        assert dialog.target_spins[0].suffix().strip() == "mm"

    def test_a_new_dialog_can_be_opened_pre_set_to_trajectory(self, qt_app: QApplication) -> None:
        instance = JointDialog(initial_kind=ModelJointKind.TRAJECTORY)

        assert instance.kind is ModelJointKind.TRAJECTORY
        instance.close()

    def test_limits_start_disabled(self, dialog: JointDialog) -> None:
        assert dialog.lower_limit_spin.isEnabled() is False
        assert dialog.upper_limit_spin.isEnabled() is False


class TestLoadingValues:
    def test_the_dialog_shows_the_given_joint(self, qt_app: QApplication) -> None:
        joint = axis_joint(name="tilt", variable="tilt_angle", origin=(0.0, 0.0, 0.0))
        instance = JointDialog(joint)

        assert instance.name_edit.text() == "tilt"
        assert instance.variable_edit.text() == "tilt_angle"
        instance.close()

    def test_metres_are_shown_as_millimetres(self, qt_app: QApplication) -> None:
        joint = axis_joint(origin=(0.5, 0.0, 0.0), target=(0.5, 0.0, 1.0))
        instance = JointDialog(joint)

        assert instance.origin_spins[0].value() == pytest.approx(500.0)
        instance.close()

    def test_limits_populate_and_enable_the_checkbox(self, qt_app: QApplication) -> None:
        import math

        joint = axis_joint(limits=(0.0, math.pi))
        instance = JointDialog(joint)

        assert instance.limit_checkbox.isChecked() is True
        assert instance.upper_limit_spin.value() == pytest.approx(180.0)
        instance.close()


class TestLivePreview:
    def test_a_field_change_emits_a_joint(self, dialog: JointDialog) -> None:
        received: list[ModelJoint] = []
        dialog.joint_previewed.connect(received.append)

        dialog.origin_spins[0].setValue(50.0)

        assert len(received) == 1

    def test_the_emitted_joint_reflects_the_change(self, dialog: JointDialog) -> None:
        received: list[ModelJoint] = []
        dialog.joint_previewed.connect(received.append)

        dialog.origin_spins[0].setValue(50.0)

        assert received[-1].origin[0] == pytest.approx(0.05)

    def test_an_in_progress_degenerate_axis_emits_nothing(self, dialog: JointDialog) -> None:
        # Zeroing the direction one field at a time must not crash, even though
        # the intermediate joint is invalid. An axis has no second point any
        # more, so this is what "degenerate" means for one.
        received: list[ModelJoint] = []
        dialog.joint_previewed.connect(received.append)
        before = len(received)

        for spin in dialog.direction_spins:
            spin.setValue(0.0)

        assert len(received) == before  # the degenerate state emitted nothing

    def test_an_in_progress_degenerate_trajectory_emits_nothing(self, dialog: JointDialog) -> None:
        dialog.kind_combo.setCurrentIndex(1)
        received: list[ModelJoint] = []
        dialog.joint_previewed.connect(received.append)
        before = len(received)

        for index in range(3):
            dialog.target_spins[index].setValue(dialog.origin_spins[index].value())

        assert len(received) == before

    def test_set_display_does_not_flicker(self, dialog: JointDialog) -> None:
        # Several fields would otherwise emit as many intermediate joints.
        received: list[ModelJoint] = []
        dialog.joint_previewed.connect(received.append)

        dialog.set_display(dialog.display)

        assert len(received) == 1

    def test_opening_with_a_joint_emits_exactly_one_preview_worth_of_state(
        self, qt_app: QApplication
    ) -> None:
        instance = JointDialog(axis_joint(limits=(0.0, 1.0)))
        received: list[ModelJoint] = []
        instance.joint_previewed.connect(received.append)

        instance.set_display(instance.display)

        assert len(received) == 1
        instance.close()

    def test_switching_kind_re_emits_the_preview(self, dialog: JointDialog) -> None:
        received: list[ModelJoint] = []
        dialog.joint_previewed.connect(received.append)

        dialog.kind_combo.setCurrentIndex(1)

        assert received[-1].kind is ModelJointKind.TRAJECTORY

    def test_switching_kind_changes_the_limit_unit(self, dialog: JointDialog) -> None:
        dialog.kind_combo.setCurrentIndex(1)  # trajectory

        assert dialog.lower_limit_spin.suffix().strip() == "mm"


class TestPicking:
    def test_the_origin_pick_button_requests_origin(self, dialog: JointDialog) -> None:
        from PySide6.QtWidgets import QPushButton

        received: list[object] = []
        dialog.pick_requested.connect(received.append)
        button = dialog.origin_group.findChild(QPushButton)
        assert button is not None

        button.click()

        assert received == [PickTarget.ORIGIN]

    def test_set_point_updates_the_matching_spins(self, dialog: JointDialog) -> None:
        dialog.set_point(PickTarget.TARGET, (0.25, 0.0, 0.0))

        assert dialog.target_spins[0].value() == pytest.approx(250.0)

    def test_set_point_emits_exactly_one_preview(self, dialog: JointDialog) -> None:
        received: list[ModelJoint] = []
        dialog.joint_previewed.connect(received.append)

        dialog.set_point(PickTarget.ORIGIN, (0.1, 0.2, 0.3))

        assert len(received) == 1
        assert received[0].origin == pytest.approx((0.1, 0.2, 0.3))


class TestValidation:
    def test_a_valid_joint_accepts(self, dialog: JointDialog) -> None:
        dialog.accept()

        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_an_axis_with_no_direction_is_refused(
        self, dialog: JointDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(QMessageBox, "warning", _silence_warning)
        for spin in dialog.direction_spins:
            spin.setValue(0.0)

        dialog.accept()

        assert dialog.result() != QDialog.DialogCode.Accepted

    def test_a_trajectory_with_no_length_is_refused(
        self, dialog: JointDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(QMessageBox, "warning", _silence_warning)
        dialog.kind_combo.setCurrentIndex(1)
        for index in range(3):
            dialog.target_spins[index].setValue(dialog.origin_spins[index].value())

        dialog.accept()

        assert dialog.result() != QDialog.DialogCode.Accepted

    def test_a_blank_name_is_refused(
        self, dialog: JointDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(QMessageBox, "warning", _silence_warning)
        dialog.name_edit.setText("   ")

        dialog.accept()

        assert dialog.result() != QDialog.DialogCode.Accepted


class TestBindDialog:
    def test_it_offers_a_none_entry_first(self, qt_app: QApplication) -> None:
        # Releasing has to be as reachable as binding, so "none" is an ordinary
        # entry rather than a separate button.
        dialog = BindDialog((("joint-1", "rail"),))

        assert dialog.joint_combo.count() == 2
        assert dialog.selected_joint_id is None

    def test_every_choice_is_listed(self, qt_app: QApplication) -> None:
        dialog = BindDialog((("joint-1", "rail"), ("joint-2", "rail / head")))

        labels = [dialog.joint_combo.itemText(i) for i in range(dialog.joint_combo.count())]
        assert "rail" in labels
        assert "rail / head" in labels

    def test_choosing_a_joint_reports_its_id(self, qt_app: QApplication) -> None:
        dialog = BindDialog((("joint-1", "rail"), ("joint-2", "rail / head")))

        dialog.joint_combo.setCurrentIndex(2)

        assert dialog.selected_joint_id == "joint-2"

    def test_the_current_binding_starts_selected(self, qt_app: QApplication) -> None:
        dialog = BindDialog((("joint-1", "rail"), ("joint-2", "head")), current_joint_id="joint-2")

        assert dialog.selected_joint_id == "joint-2"

    def test_an_unbound_model_starts_on_none(self, qt_app: QApplication) -> None:
        dialog = BindDialog((("joint-1", "rail"),), current_joint_id=None)

        assert dialog.selected_joint_id is None

    def test_an_empty_list_still_offers_none(self, qt_app: QApplication) -> None:
        dialog = BindDialog(())

        assert dialog.joint_combo.count() == 1
        assert dialog.selected_joint_id is None


class TestAxisFields:
    """An axis is a centre point, a direction and an init rotation."""

    def test_the_axis_group_shows_for_an_axis(self, dialog: JointDialog) -> None:
        assert dialog.axis_group.isVisibleTo(dialog)

    def test_the_target_group_hides_for_an_axis(self, dialog: JointDialog) -> None:
        # An axis has no second point, so offering one would invite numbers that
        # look meaningful and are not.
        assert not dialog.target_group.isVisibleTo(dialog)

    def test_switching_to_a_trajectory_swaps_the_groups(self, dialog: JointDialog) -> None:
        dialog.kind_combo.setCurrentIndex(1)

        assert dialog.target_group.isVisibleTo(dialog)
        assert not dialog.axis_group.isVisibleTo(dialog)

    def test_the_origin_group_is_called_a_centre_point_for_an_axis(
        self, dialog: JointDialog
    ) -> None:
        assert dialog.origin_group.title() == "Centre point"

    def test_the_direction_reaches_the_joint(self, dialog: JointDialog) -> None:
        dialog.direction_spins[0].setValue(1.0)
        dialog.direction_spins[2].setValue(0.0)

        assert direction_of(dialog.joint) == pytest.approx((1.0, 0.0, 0.0))

    def test_the_magnitude_makes_no_difference(self, dialog: JointDialog) -> None:
        dialog.direction_spins[2].setValue(1.0)
        unit = direction_of(dialog.joint)
        dialog.direction_spins[2].setValue(100.0)

        assert direction_of(dialog.joint) == pytest.approx(unit)

    def test_the_init_rotation_reaches_the_joint_in_radians(self, dialog: JointDialog) -> None:
        dialog.initial_angle_spin.setValue(90.0)

        assert dialog.joint.initial_angle_rad == pytest.approx(math.pi / 2.0)

    def test_editing_an_axis_shows_its_direction_back(self, dialog: JointDialog) -> None:
        dialog.set_display(
            from_model_joint(axis_joint(direction=(0.0, 2.0, 0.0), initial_angle_rad=0.5))
        )

        assert [spin.value() for spin in dialog.direction_spins] == pytest.approx([0.0, 2.0, 0.0])

    def test_editing_an_axis_shows_its_init_rotation_back(self, dialog: JointDialog) -> None:
        dialog.set_display(from_model_joint(axis_joint(initial_angle_rad=math.pi / 2.0)))

        assert dialog.initial_angle_spin.value() == pytest.approx(90.0)
