"""Choosing which way a variable travels, limited by what the node allows.

A joint's variable may now be written as well as read — writing is this
application telling the PLC where its model is, which is what a
hardware-in-the-loop setup wants. A sensor's is still always a write, so the
choice is not offered for one.

The limit comes from the node's own `UserAccessLevel`: a servo's actual position
is read-only and a command word may be write-only, and offering the impossible
direction is how a binding gets made that the server then refuses.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.config.binding import BindingDirection  # noqa: E402
from pssim.domain.model_joints import ModelJoint, ModelJointKind  # noqa: E402
from pssim.domain.sensors import Sensor, SensorKind  # noqa: E402
from pssim.io.opcua_browse_session import BrowseNode, NodeKind  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.opcua_dialog import AssignTagDialog  # noqa: E402
from pssim.ui.settings import ConnectionSettings, VariableTag  # noqa: E402
from pssim.ui.variable_registry import (  # noqa: E402
    VariableRegistry,
    VariableSource,
)

pytestmark = pytest.mark.ui

NODE = "ns=2;s=Axes.X.ActPos"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def node(is_readable: bool = True, is_writable: bool = False, path: str = "") -> BrowseNode:
    return BrowseNode(
        node_id=NODE,
        browse_name="ActPos",
        display_name="ActPos",
        kind=NodeKind.VARIABLE,
        data_type="Double",
        is_writable=is_writable,
        is_readable=is_readable,
        path=path,
    )


def rail(variable: str = "rail_pos") -> ModelJoint:
    return ModelJoint(
        name="rail",
        kind=ModelJointKind.TRAJECTORY,
        origin=(0.0, 0.0, 0.0),
        target=(3.0, 0.0, 0.0),
        variable=variable,
    )


def beam(variable: str = "gate") -> Sensor:
    return Sensor(
        name="gate",
        kind=SensorKind.BEAM,
        origin=(0.0, 0.0, 0.0),
        direction=(1.0, 0.0, 0.0),
        range_m=1.0,
        variable=variable,
    )


@pytest.fixture
def dialog(qt_app: QApplication) -> AssignTagDialog:
    return AssignTagDialog("rail_pos", "opc.tcp://plc:4840/", unit="mm")


class TestTheChoice:
    def test_reading_is_the_default(self, dialog: AssignTagDialog) -> None:
        assert dialog.direction is BindingDirection.READ

    def test_writing_can_be_picked(self, dialog: AssignTagDialog) -> None:
        dialog.write_radio.setChecked(True)

        assert dialog.direction is BindingDirection.WRITE

    def test_it_reaches_the_tag(self, dialog: AssignTagDialog) -> None:
        dialog.node_edit.setText(NODE)
        dialog.write_radio.setChecked(True)

        tag = dialog.tag
        assert tag is not None
        assert tag.direction is BindingDirection.WRITE

    def test_an_existing_choice_is_shown(self, qt_app: QApplication) -> None:
        current = VariableTag(node_id=NODE, direction=BindingDirection.WRITE)

        shown = AssignTagDialog("rail_pos", "opc.tcp://plc:4840/", current, unit="mm")

        assert shown.write_radio.isChecked() is True

    def test_a_tag_that_never_chose_reads(self, qt_app: QApplication) -> None:
        shown = AssignTagDialog(
            "rail_pos", "opc.tcp://plc:4840/", VariableTag(node_id=NODE), unit="mm"
        )

        assert shown.read_radio.isChecked() is True


class TestTheNodeLimitsIt:
    def test_a_read_only_node_cannot_be_written(self, dialog: AssignTagDialog) -> None:
        dialog._on_node_selected(node(is_readable=True, is_writable=False))

        assert dialog.write_radio.isEnabled() is False

    def test_a_write_only_node_cannot_be_read(self, dialog: AssignTagDialog) -> None:
        dialog._on_node_selected(node(is_readable=False, is_writable=True))

        assert dialog.read_radio.isEnabled() is False

    def test_a_read_write_node_allows_both(self, dialog: AssignTagDialog) -> None:
        dialog._on_node_selected(node(is_readable=True, is_writable=True))

        assert dialog.read_radio.isEnabled()
        assert dialog.write_radio.isEnabled()

    def test_the_access_is_said_out_loud(self, dialog: AssignTagDialog) -> None:
        # Greyed rather than hidden, and explained rather than merely
        # unavailable.
        dialog._on_node_selected(node(is_readable=True, is_writable=False))

        assert "read-only" in dialog.access_label.text()

    def test_picking_a_read_only_node_moves_the_choice_back(self, dialog: AssignTagDialog) -> None:
        # Otherwise the tag is left saying it writes to a node that refuses to
        # be written, and the refusal only shows up on the server.
        dialog.write_radio.setChecked(True)

        dialog._on_node_selected(node(is_readable=True, is_writable=False))

        assert dialog.direction is BindingDirection.READ

    def test_picking_a_write_only_node_moves_it_forward(self, dialog: AssignTagDialog) -> None:
        dialog._on_node_selected(node(is_readable=False, is_writable=True))

        assert dialog.direction is BindingDirection.WRITE

    def test_a_struct_field_cannot_be_written(self, dialog: AssignTagDialog) -> None:
        # A write goes back as the whole struct or not at all, and this project
        # writes one node at a time (R19).
        dialog._on_node_selected(node(is_readable=True, is_writable=True, path="Position.X"))

        assert dialog.write_radio.isEnabled() is False


class TestASensorIsNotAsked:
    @pytest.fixture
    def sensor_dialog(self, qt_app: QApplication) -> AssignTagDialog:
        return AssignTagDialog("gate", "opc.tcp://plc:4840/", is_direction_fixed=True)

    def test_neither_radio_can_be_touched(self, sensor_dialog: AssignTagDialog) -> None:
        assert sensor_dialog.read_radio.isEnabled() is False
        assert sensor_dialog.write_radio.isEnabled() is False

    def test_it_writes(self, sensor_dialog: AssignTagDialog) -> None:
        assert sensor_dialog.direction is BindingDirection.WRITE

    def test_and_says_why(self, sensor_dialog: AssignTagDialog) -> None:
        assert "sensor" in sensor_dialog.access_label.text()

    def test_the_node_does_not_change_that(self, sensor_dialog: AssignTagDialog) -> None:
        sensor_dialog._on_node_selected(node(is_readable=True, is_writable=False))

        assert sensor_dialog.direction is BindingDirection.WRITE


class TestTheRegistryResolvesIt:
    def test_a_joint_reads_by_default(self) -> None:
        registry = VariableRegistry()
        registry.set_sources([VariableSource("rail_pos", BindingDirection.READ, "axis")])
        registry.set_tags({"rail_pos": VariableTag(node_id=NODE)})

        entry = registry.get("rail_pos")
        assert entry is not None
        assert entry.direction is BindingDirection.READ

    def test_a_joint_s_tag_can_make_it_write(self) -> None:
        registry = VariableRegistry()
        registry.set_sources([VariableSource("rail_pos", BindingDirection.READ, "axis")])
        registry.set_tags({"rail_pos": VariableTag(node_id=NODE, direction=BindingDirection.WRITE)})

        entry = registry.get("rail_pos")
        assert entry is not None
        assert entry.direction is BindingDirection.WRITE

    def test_a_sensor_s_tag_cannot_make_it_read(self) -> None:
        registry = VariableRegistry()
        registry.set_sources(
            [VariableSource("gate", BindingDirection.WRITE, "sensor", is_direction_fixed=True)]
        )
        registry.set_tags({"gate": VariableTag(node_id=NODE, direction=BindingDirection.READ)})

        entry = registry.get("gate")
        assert entry is not None
        assert entry.direction is BindingDirection.WRITE

    def test_a_tag_that_never_chose_leaves_the_default(self) -> None:
        # Which is what every tag stored before this field existed looks like.
        registry = VariableRegistry()
        registry.set_sources([VariableSource("gate", BindingDirection.WRITE, "sensor")])
        registry.set_tags({"gate": VariableTag(node_id=NODE)})

        entry = registry.get("gate")
        assert entry is not None
        assert entry.direction is BindingDirection.WRITE

    def test_the_order_the_scene_arrives_in_does_not_matter(self) -> None:
        # Settings load before a project does (R18), so the tag is often first.
        registry = VariableRegistry()
        registry.set_tags({"rail_pos": VariableTag(node_id=NODE, direction=BindingDirection.WRITE)})
        registry.set_sources([VariableSource("rail_pos", BindingDirection.READ, "axis")])

        entry = registry.get("rail_pos")
        assert entry is not None
        assert entry.direction is BindingDirection.WRITE

    def test_the_binding_carries_it(self) -> None:
        registry = VariableRegistry()
        registry.set_sources([VariableSource("rail_pos", BindingDirection.READ, "axis")])
        registry.set_tags({"rail_pos": VariableTag(node_id=NODE, direction=BindingDirection.WRITE)})

        assert registry.bindings()[0].direction is BindingDirection.WRITE


class TestInTheWindow:
    def test_a_joint_may_choose(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        window._joints.add(rail())
        window.refresh_variables()

        assert window._variable_direction_is_fixed("rail_pos") is False
        window.close()

    def test_a_sensor_may_not(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        window._sensors.add(beam())
        window.refresh_variables()

        assert window._variable_direction_is_fixed("gate") is True
        window.close()

    def test_a_write_bound_joint_is_not_driven(self, qt_app: QApplication) -> None:
        # It publishes instead; nothing arrives for it.
        window = MainWindow(viewport_factory=QWidget)
        joint_id = window._joints.add(rail()).joint_id
        window.save_connection_settings(
            ConnectionSettings().with_tag(
                "rail_pos", VariableTag(node_id=NODE, direction=BindingDirection.WRITE)
            )
        )
        window.refresh_variables()
        window._variables.set_connected(True)
        window._variables.set_value("rail_pos", 0.4)

        window._drive_joints_from_variables()

        entry = window._joints.get(joint_id)
        assert entry is not None
        assert entry.value == pytest.approx(0.0)
        window.close()

    def test_a_write_bound_joint_offers_its_value(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        joint_id = window._joints.add(rail()).joint_id
        window.save_connection_settings(
            ConnectionSettings().with_tag(
                "rail_pos", VariableTag(node_id=NODE, direction=BindingDirection.WRITE)
            )
        )
        window.refresh_variables()
        window.apply_joint_value(joint_id, 0.4)

        assert window._connection.store.pending_writes().get("rail_pos") == pytest.approx(0.4)
        window.close()

    def test_a_read_bound_joint_offers_nothing(self, qt_app: QApplication) -> None:
        window = MainWindow(viewport_factory=QWidget)
        joint_id = window._joints.add(rail()).joint_id
        window.save_connection_settings(
            ConnectionSettings().with_tag("rail_pos", VariableTag(node_id=NODE))
        )
        window.refresh_variables()
        window.apply_joint_value(joint_id, 0.4)

        assert "rail_pos" not in window._connection.store.pending_writes()
        window.close()
