"""Assigning a tag by decimal places, in the unit the joint moves in.

The complaint: the scale was the hard part. It made you work out the whole unit
conversion and type it as one factor — `machines/example.yaml` still shows where
that ends up, `scale: 1.7453292519943296e-05` with a comment explaining it is
`pi/180/1000`.

So the dialog asks the two things a PLC programmer knows instead: how many
decimal places the integer carries, and a zero-point offset in the PLC's own
unit. Which unit that is comes from the joint, and the dialog says so on every
field.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.domain.model_joints import ModelJoint, ModelJointKind  # noqa: E402
from pssim.domain.units import DEG_TO_RAD, MM_TO_M  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.opcua_dialog import AssignTagDialog  # noqa: E402
from pssim.ui.settings import MAX_DECIMALS, ConnectionSettings, VariableTag  # noqa: E402

pytestmark = pytest.mark.ui

NODE = "ns=2;s=Axes.X.ActPos"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def rail(variable: str = "rail_pos") -> ModelJoint:
    return ModelJoint(
        name="rail",
        kind=ModelJointKind.TRAJECTORY,
        origin=(0.0, 0.0, 0.0),
        target=(3.0, 0.0, 0.0),
        variable=variable,
    )


def head(variable: str = "head_angle") -> ModelJoint:
    return ModelJoint(
        name="head",
        kind=ModelJointKind.AXIS,
        origin=(0.0, 0.0, 0.0),
        direction=(0.0, 0.0, 1.0),
        variable=variable,
    )


@pytest.fixture
def dialog(qt_app: QApplication) -> AssignTagDialog:
    return AssignTagDialog("rail_pos", "opc.tcp://plc:4840/", unit="mm")


class TestTheFields:
    def test_decimal_places_start_at_none(self, dialog: AssignTagDialog) -> None:
        # Which is 1:1 — what a REAL wants, and the safe reading of silence.
        assert dialog.decimals_spin.value() == 0

    def test_they_cannot_be_negative(self, dialog: AssignTagDialog) -> None:
        # It would mean multiplying by ten, which is a different feature.
        assert dialog.decimals_spin.minimum() == 0

    def test_they_are_bounded(self, dialog: AssignTagDialog) -> None:
        assert dialog.decimals_spin.maximum() == MAX_DECIMALS

    def test_there_is_no_scale_field_any_more(self, dialog: AssignTagDialog) -> None:
        assert not hasattr(dialog, "scale_spin")

    def test_the_offset_is_labelled_in_the_joint_s_unit(self, dialog: AssignTagDialog) -> None:
        assert dialog.offset_spin.suffix().strip() == "mm"

    def test_an_axis_says_degrees_instead(self, qt_app: QApplication) -> None:
        rotary = AssignTagDialog("head_angle", "opc.tcp://plc:4840/", unit="°")

        assert rotary.offset_spin.suffix().strip() == "°"

    def test_a_variable_with_no_joint_says_units(self, qt_app: QApplication) -> None:
        # A sensor's: there is no joint kind to ask (R16), so it does not guess.
        plain = AssignTagDialog("gate", "opc.tcp://plc:4840/")

        assert plain.offset_spin.suffix().strip() == "units"


class TestTheWorkedExample:
    """A line of arithmetic rather than a rule to be trusted: a wrong decimal
    place is invisible in the fields and obvious here."""

    def test_none_is_one_to_one(self, dialog: AssignTagDialog) -> None:
        assert "652 mm" in dialog.conversion_label.text()

    def test_one_place_shifts_the_point(self, dialog: AssignTagDialog) -> None:
        dialog.decimals_spin.setValue(1)

        assert "65.2 mm" in dialog.conversion_label.text()

    def test_two_places(self, dialog: AssignTagDialog) -> None:
        dialog.decimals_spin.setValue(2)

        assert "6.52 mm" in dialog.conversion_label.text()

    def test_the_offset_is_included(self, dialog: AssignTagDialog) -> None:
        dialog.decimals_spin.setValue(1)
        dialog.offset_spin.setValue(10.0)

        assert "75.2 mm" in dialog.conversion_label.text()

    def test_it_speaks_the_joint_s_unit(self, qt_app: QApplication) -> None:
        rotary = AssignTagDialog("head_angle", "opc.tcp://plc:4840/", unit="°")

        assert "°" in rotary.conversion_label.text()


class TestTheTagItProduces:
    def test_the_decimals_reach_it(self, dialog: AssignTagDialog) -> None:
        dialog.node_edit.setText(NODE)
        dialog.decimals_spin.setValue(1)

        tag = dialog.tag
        assert tag is not None
        assert tag.decimals == 1

    def test_so_does_the_offset(self, dialog: AssignTagDialog) -> None:
        dialog.node_edit.setText(NODE)
        dialog.offset_spin.setValue(-100.0)

        tag = dialog.tag
        assert tag is not None
        assert tag.offset == pytest.approx(-100.0)

    def test_an_existing_tag_is_shown(self, qt_app: QApplication) -> None:
        current = VariableTag(node_id=NODE, decimals=2, offset=5.0)

        shown = AssignTagDialog("rail_pos", "opc.tcp://plc:4840/", current, unit="mm")

        assert shown.decimals_spin.value() == 2
        assert shown.offset_spin.value() == pytest.approx(5.0)


class TestTheWindowSuppliesTheUnit:
    """Only the scene knows whether a variable belongs to a rail or a rotary
    head, and that is what decides whether the PLC's 354.21 is a distance or an
    angle."""

    def test_a_trajectory_variable_converts_millimetres(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(rail())
        window.save_connection_settings(
            ConnectionSettings().with_tag("rail_pos", VariableTag(node_id=NODE))
        )
        window.refresh_variables()

        entry = window._variables.get("rail_pos")
        assert entry is not None
        assert entry.unit_scale == pytest.approx(MM_TO_M)
        window.close()

    def test_an_axis_variable_converts_degrees(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(head())
        window.save_connection_settings(
            ConnectionSettings().with_tag("head_angle", VariableTag(node_id=NODE))
        )
        window.refresh_variables()

        entry = window._variables.get("head_angle")
        assert entry is not None
        assert entry.unit_scale == pytest.approx(DEG_TO_RAD)
        window.close()

    def test_the_binding_then_reads_the_brief_s_example(self, qt_app: QApplication) -> None:
        # 354.21 from the PLC is 354.21 mm of travel.
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(rail())
        window.save_connection_settings(
            ConnectionSettings().with_tag("rail_pos", VariableTag(node_id=NODE))
        )
        window.refresh_variables()

        entry = window._variables.get("rail_pos")
        assert entry is not None
        binding = entry.binding()
        assert binding is not None
        assert binding.to_internal(354.21) == pytest.approx(0.35421)
        window.close()

    def test_and_the_same_number_as_an_angle(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(head())
        window.save_connection_settings(
            ConnectionSettings().with_tag("head_angle", VariableTag(node_id=NODE))
        )
        window.refresh_variables()

        entry = window._variables.get("head_angle")
        assert entry is not None
        binding = entry.binding()
        assert binding is not None
        assert binding.to_internal(354.21) == pytest.approx(354.21 * DEG_TO_RAD)
        window.close()

    def test_an_integer_axis_with_one_decimal(self, qt_app: QApplication) -> None:
        # The brief's other example: 652 becomes 65.2 degrees.
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(head())
        window.save_connection_settings(
            ConnectionSettings().with_tag("head_angle", VariableTag(node_id=NODE, decimals=1))
        )
        window.refresh_variables()

        entry = window._variables.get("head_angle")
        assert entry is not None
        binding = entry.binding()
        assert binding is not None
        assert binding.to_internal(652) == pytest.approx(65.2 * DEG_TO_RAD)
        window.close()

    def test_the_dialog_is_opened_with_the_joint_s_unit(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(head())
        window.refresh_variables()

        assert window._variable_unit("head_angle") == "°"
        window.close()

    def test_and_millimetres_for_a_rail(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(rail())
        window.refresh_variables()

        assert window._variable_unit("rail_pos") == "mm"
        window.close()

    def test_a_variable_no_joint_claims_has_no_unit(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)

        assert window._variable_unit("gate") == ""
        window.close()


class TestItActuallyMovesTheModel:
    """End to end through the registry, because the arithmetic being right and
    the model being in the right place are two different claims."""

    def _driven(self, joint: ModelJoint, decimals: int, value: float) -> float:
        window = MainWindow(viewport_factory=QWidget)
        joint_id = window._joints.add(joint).joint_id
        window.save_connection_settings(
            ConnectionSettings().with_tag(
                joint.variable, VariableTag(node_id=NODE, decimals=decimals)
            )
        )
        window.refresh_variables()

        window._variables.set_connected(True)
        window._variables.set_value(
            joint.variable,
            window._variables.get(joint.variable).binding().to_internal(value),  # type: ignore[union-attr]
        )
        window._drive_joints_from_variables()

        entry = window._joints.get(joint_id)
        assert entry is not None
        moved = entry.value
        window.close()
        return moved

    def test_a_float_moves_a_rail_by_millimetres(self) -> None:
        assert self._driven(rail(), 0, 354.21) == pytest.approx(0.35421)

    def test_an_integer_moves_it_by_the_decimals(self) -> None:
        assert self._driven(rail(), 1, 652) == pytest.approx(0.0652)

    def test_a_float_turns_a_head_by_degrees(self) -> None:
        assert self._driven(head(), 0, 90.0) == pytest.approx(math.pi / 2.0)

    def test_an_integer_turns_it_by_the_decimals(self) -> None:
        assert self._driven(head(), 1, 652) == pytest.approx(65.2 * DEG_TO_RAD)
