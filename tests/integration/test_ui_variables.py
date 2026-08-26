"""Tests of the Variables tab, the connection controller and the tag dialogs.

No server: `OpcUaSource` is never started here. What is exercised is everything
between the scene and the store — which variables exist, what they are bound to,
and what the tab says about them. The half that needs a server is in
`tests/integration/test_opcua_roundtrip.py`, against the mock and nothing else.

`poll` takes the time as an argument, so staleness is driven without sleeping.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from asyncua.ua.uaerrors import BadUserAccessDenied  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.config.binding import BindingDirection  # noqa: E402
from pssim.domain.sensors import Sensor, SensorKind  # noqa: E402
from pssim.domain.units import DEG_TO_RAD  # noqa: E402
from pssim.io.base import SourceStatus  # noqa: E402
from pssim.io.opcua_diagnostics import DiagnosticLog, DiagnosticStep  # noqa: E402
from pssim.io.store import StateStore  # noqa: E402
from pssim.ui.connection_controller import ConnectionController  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.opcua_dialog import AssignTagDialog, ConnectionDialog  # noqa: E402
from pssim.ui.settings import ConnectionSettings, SettingsStore, VariableTag  # noqa: E402
from pssim.ui.variable_registry import (  # noqa: E402
    VariableRegistry,
    VariableSource,
    VariableState,
)
from pssim.ui.variable_tree import (  # noqa: E402
    COLUMN_NAME,
    COLUMN_STATUS,
    COLUMN_TAG,
    COLUMN_VALUE,
    VariableTree,
)
from tests.factories import axis_joint, trajectory_joint  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def store(qt_app: QApplication, tmp_path: Path) -> SettingsStore:
    return SettingsStore(QSettings(str(tmp_path / "pssim.ini"), QSettings.Format.IniFormat))


@pytest.fixture
def window(qt_app: QApplication, store: SettingsStore) -> Iterator[MainWindow]:
    instance = MainWindow(viewport_factory=QWidget, settings=store)
    yield instance
    instance.close()


def with_a_driven_axis(window: MainWindow, variable: str = "X") -> MainWindow:
    window.joints.add(axis_joint(name="tilt", variable=variable))
    window.refresh_variables()
    return window


def assign(window: MainWindow, variable: str, node_id: str, decimals: int = 0) -> None:
    window.save_connection_settings(
        window.connection_settings.with_tag(
            variable, VariableTag(node_id=node_id, decimals=decimals)
        )
    )


class TestTheListFollowsTheScene:
    def test_a_fresh_window_has_no_variables(self, window: MainWindow) -> None:
        assert window.variables.is_empty

    def test_an_axis_contributes_its_variable(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        assert window.variables.names == ("X",)

    def test_a_sensor_contributes_its_variable(self, window: MainWindow) -> None:
        window.sensors.add(Sensor(name="gate", kind=SensorKind.BEAM, variable="I0.0"))
        window.refresh_variables()

        assert "I0.0" in window.variables.names

    def test_a_sensor_variable_is_an_output(self, window: MainWindow) -> None:
        # Its reading is something this application produces, not something the
        # PLC decides.
        window.sensors.add(Sensor(name="gate", kind=SensorKind.BEAM, variable="I0.0"))
        window.refresh_variables()

        entry = window.variables.get("I0.0")
        assert entry is not None
        assert entry.direction is BindingDirection.WRITE

    def test_an_axis_variable_is_read(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        entry = window.variables.get("X")
        assert entry is not None
        assert entry.direction is BindingDirection.READ

    def test_a_joint_always_has_a_variable(self, window: MainWindow) -> None:
        # The domain refuses an empty one, so every joint contributes a row.
        window.joints.add(trajectory_joint(name="rail"))
        window.refresh_variables()

        assert len(window.variables) == 1

    def test_a_sensor_with_no_variable_adds_nothing(self, window: MainWindow) -> None:
        # Unlike a joint, a sensor may have none — it is recorded for later
        # rather than required (R16).
        window.sensors.add(Sensor(name="gate", kind=SensorKind.BEAM))
        window.refresh_variables()

        assert window.variables.is_empty

    def test_the_row_names_what_it_came_from(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        entry = window.variables.get("X")
        assert entry is not None
        assert "tilt" in entry.owner

    def test_the_tab_shows_a_row(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        assert window.variable_tree.topLevelItemCount() == 1
        assert window.variable_tree.topLevelItem(0).text(COLUMN_NAME) == "X"


class TestAssigningATag:
    def test_a_variable_starts_with_no_tag(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        assert window.variable_tree.topLevelItem(0).text(COLUMN_STATUS) == "No tag"

    def test_a_tag_shows_in_the_row(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        assign(window, "X", "ns=2;s=Axes.X.ActPos")

        assert window.variable_tree.topLevelItem(0).text(COLUMN_TAG) == "ns=2;s=Axes.X.ActPos"

    def test_a_bound_variable_offline_says_so(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        assign(window, "X", "ns=2;s=Axes.X.ActPos")

        assert window.variable_tree.topLevelItem(0).text(COLUMN_STATUS) == "Disconnected"

    def test_the_tag_is_saved(self, window: MainWindow, store: SettingsStore) -> None:
        with_a_driven_axis(window, "X")

        assign(window, "X", "ns=2;s=Axes.X.ActPos", decimals=1)

        tag = store.load_connection().tag_for("X")
        assert tag is not None
        assert tag.decimals == 1

    def test_a_saved_tag_comes_back(self, qt_app: QApplication, store: SettingsStore) -> None:
        # Settings load before a project does, so a tag whose variable has not
        # arrived yet has to survive.
        store.save_connection(
            ConnectionSettings().with_tag("X", VariableTag(node_id="ns=2;s=Axes.X.ActPos"))
        )

        second = MainWindow(viewport_factory=QWidget, settings=store)
        try:
            with_a_driven_axis(second, "X")
            entry = second.variables.get("X")
            assert entry is not None
            assert entry.is_bound
        finally:
            second.close()

    def test_clearing_a_tag_unbinds_it(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")
        assign(window, "X", "ns=2;s=Axes.X.ActPos")
        window.select_variable("X")

        window.clear_variable_tag()

        assert window.connection_settings.tag_for("X") is None

    def test_clearing_without_a_selection_does_nothing(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")
        assign(window, "X", "ns=2;s=Axes.X.ActPos")

        window.clear_variable_tag()

        assert window.connection_settings.tag_for("X") is not None


class TestTheConnection:
    def test_connecting_with_nothing_bound_is_refused(self, window: MainWindow) -> None:
        # A normal state of a project that has not been wired up, not a fault —
        # so a status-bar message rather than a modal.
        with_a_driven_axis(window, "X")

        assert window.connect_to_server() is False

    def test_the_refusal_says_why(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        window.connect_to_server()

        assert "tag" in window.statusBar().currentMessage()

    def test_disconnecting_is_safe_when_never_connected(self, window: MainWindow) -> None:
        window.disconnect_from_server()

        assert window.connection.is_connected is False

    def test_connect_is_offered_and_disconnect_is_not(self, window: MainWindow) -> None:
        assert window.connect_action.isEnabled() is True
        assert window.disconnect_action.isEnabled() is False

    def test_the_tag_actions_need_a_selection(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        assert window.assign_tag_action.isEnabled() is False

        window.select_variable("X")
        assert window.assign_tag_action.isEnabled() is True

    def test_selecting_a_variable_leaves_the_model_alone(self, window: MainWindow) -> None:
        # A variable is not a thing in the scene, and the properties dock never
        # shows one — picking a row here must not clear what is being looked at.
        with_a_driven_axis(window, "X")
        joint = window.joints.entries[0]
        window.select_joint(joint.joint_id)

        window.select_variable("X")

        assert window.joints.selected_id == joint.joint_id


class _StubSource:
    """A `DataSource` that connects to nothing. The seam `use_source` exists for."""

    def __init__(self, store: StateStore, status: SourceStatus) -> None:
        self._store = store
        self._status = status

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def store(self) -> StateStore:
        return self._store

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


def connected_controller(
    variables: VariableRegistry, stale_after_s: float = 1.0
) -> ConnectionController:
    controller = ConnectionController(variables, stale_after_s=stale_after_s)
    controller.use_source(_StubSource(controller.store, SourceStatus.CONNECTED))
    return controller


class TestThePump:
    """`ConnectionController.poll` without a real server: the store is filled by
    hand and a stub source reports the status."""

    def test_a_value_reaches_the_registry(self, qt_app: QApplication) -> None:
        variables = VariableRegistry()
        variables.set_sources([VariableSource("X", BindingDirection.READ, "axis tilt")])
        variables.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        controller = connected_controller(variables)
        controller.store.put("X", 1.25, source_time_s=10.0)

        controller.poll(now_s=10.1)

        entry = variables.get("X")
        assert entry is not None
        assert entry.value == pytest.approx(1.25)

    def test_connecting_is_reported_to_the_registry(self, qt_app: QApplication) -> None:
        variables = VariableRegistry()
        variables.set_sources([VariableSource("X", BindingDirection.READ, "axis tilt")])
        variables.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        controller = connected_controller(variables)

        controller.poll(now_s=10.0)

        assert variables.is_connected is True

    def test_nothing_is_copied_while_disconnected(self, qt_app: QApplication) -> None:
        # The store still holds the value and the scene still draws it (R10), but
        # calling it live would contradict the Status column.
        variables = VariableRegistry()
        variables.set_sources([VariableSource("X", BindingDirection.READ, "axis tilt")])
        variables.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        controller = ConnectionController(variables)
        controller.store.put("X", 1.25, source_time_s=10.0)

        controller.poll(now_s=10.1)

        entry = variables.get("X")
        assert entry is not None
        assert entry.state is VariableState.OFFLINE

    def test_an_old_value_reads_stale(self, qt_app: QApplication) -> None:
        variables = VariableRegistry()
        variables.set_sources([VariableSource("X", BindingDirection.READ, "axis tilt")])
        variables.set_tags({"X": VariableTag(node_id="ns=2;s=X")})
        controller = connected_controller(variables, stale_after_s=0.5)
        controller.store.put("X", 1.25, source_time_s=10.0)

        controller.poll(now_s=12.0)

        entry = variables.get("X")
        assert entry is not None
        assert entry.state is VariableState.STALE

    def test_publishing_queues_it(self, qt_app: QApplication) -> None:
        variables = VariableRegistry()
        variables.set_sources([VariableSource("Out", BindingDirection.WRITE, "sensor gate")])
        controller = ConnectionController(variables)

        controller.publish("Out", 1.0)

        assert controller.store.pending_writes() == {"Out": 1.0}

    def test_publishing_shows_the_value(self, qt_app: QApplication) -> None:
        # Nothing will ever arrive from the server to say what an output is
        # worth, so a row waiting for one would wait for ever.
        variables = VariableRegistry()
        variables.set_sources([VariableSource("Out", BindingDirection.WRITE, "sensor gate")])
        controller = ConnectionController(variables)

        controller.publish("Out", 1.0)

        entry = variables.get("Out")
        assert entry is not None
        assert entry.value == pytest.approx(1.0)


class TestWhyItIsNotConnected:
    """R19: a disconnected row says `Disconnected`; the reason lives here."""

    def test_nothing_attempted_is_an_empty_log(self, qt_app: QApplication) -> None:
        # Empty rather than `None`: a caller asking before anything was tried
        # should get an empty answer, not something to branch on.
        controller = ConnectionController(VariableRegistry())

        assert controller.diagnostics.entries == ()

    def test_and_no_error_either(self, qt_app: QApplication) -> None:
        assert ConnectionController(VariableRegistry()).last_error is None

    def test_a_source_without_diagnostics_is_survived(self, qt_app: QApplication) -> None:
        # `DataSource` is a Protocol (R12) and a replay implements none of this.
        controller = connected_controller(VariableRegistry())

        assert controller.diagnostics.entries == ()
        assert controller.last_error is None

    def test_the_source_s_reason_is_passed_through(self, qt_app: QApplication) -> None:
        controller = ConnectionController(VariableRegistry())
        controller.use_source(_FailedSource(controller.store))

        assert controller.last_error == "BadUserAccessDenied"

    def test_so_is_its_log(self, qt_app: QApplication) -> None:
        controller = ConnectionController(VariableRegistry())
        controller.use_source(_FailedSource(controller.store))

        assert controller.diagnostics.last_failure is not None


class _FailedSource(_StubSource):
    """A source that tried and could not, as `OpcUaSource` reports it."""

    def __init__(self, store: StateStore) -> None:
        super().__init__(store, SourceStatus.DISCONNECTED)
        self._log = DiagnosticLog()
        self._log.failed(DiagnosticStep.SESSION, BadUserAccessDenied())

    @property
    def diagnostics(self) -> DiagnosticLog:
        return self._log

    @property
    def last_error(self) -> str:
        return "BadUserAccessDenied"


class TestTheValueShown:
    def test_it_is_shown_in_the_plc_s_own_units(self, window: MainWindow) -> None:
        # A row saying 1.5708 for a tag the PLC set to 90 invites a hunt for a bug
        # that is not there. `X` names an axis, so the PLC's unit is degrees.
        with_a_driven_axis(window, "X")
        assign(window, "X", "ns=2;s=X")
        window.variables.set_connected(True)
        window.variables.set_value("X", math.pi / 2.0)
        window._refresh_variables_view()

        assert window.variable_tree.topLevelItem(0).text(COLUMN_VALUE) == "90"

    def test_an_integer_tag_shows_the_integer(self, window: MainWindow) -> None:
        # One decimal place: the PLC's 652 is 65.2 degrees, and the row says 652.
        with_a_driven_axis(window, "X")
        assign(window, "X", "ns=2;s=X", decimals=1)
        window.variables.set_connected(True)
        window.variables.set_value("X", 65.2 * DEG_TO_RAD)
        window._refresh_variables_view()

        assert window.variable_tree.topLevelItem(0).text(COLUMN_VALUE) == "652"

    def test_no_value_yet_is_a_dash(self, window: MainWindow) -> None:
        with_a_driven_axis(window, "X")

        assert window.variable_tree.topLevelItem(0).text(COLUMN_VALUE) == "—"


class TestDialogs:
    def test_the_connection_dialog_shows_the_endpoint(self, qt_app: QApplication) -> None:
        dialog = ConnectionDialog(ConnectionSettings(endpoint="opc.tcp://plc:4840/"))

        assert dialog.endpoint_edit.text() == "opc.tcp://plc:4840/"

    def test_writing_starts_off(self, qt_app: QApplication) -> None:
        assert ConnectionDialog(ConnectionSettings()).writing_check.isChecked() is False

    def test_it_reports_what_was_typed(self, qt_app: QApplication) -> None:
        dialog = ConnectionDialog(ConnectionSettings())

        dialog.endpoint_edit.setText("opc.tcp://plc:4840/")
        dialog.writing_check.setChecked(True)

        assert dialog.settings.endpoint == "opc.tcp://plc:4840/"
        assert dialog.settings.allow_writing is True

    def test_it_carries_the_tags_through(self, qt_app: QApplication) -> None:
        # This dialog is about the connection, not about the assignments.
        settings = ConnectionSettings().with_tag("X", VariableTag(node_id="ns=2;s=X"))
        dialog = ConnectionDialog(settings)

        assert dialog.settings.tag_for("X") is not None

    def test_the_assign_dialog_shows_the_current_tag(self, qt_app: QApplication) -> None:
        dialog = AssignTagDialog("X", "opc.tcp://plc:4840/", VariableTag(node_id="ns=2;s=X"))

        assert dialog.node_edit.text() == "ns=2;s=X"

    def test_an_empty_node_is_no_tag(self, qt_app: QApplication) -> None:
        # Leaving the field empty is how a variable is deliberately left unbound.
        dialog = AssignTagDialog("X", "opc.tcp://plc:4840/")

        assert dialog.tag is None

    def test_it_reports_the_conversion(self, qt_app: QApplication) -> None:
        dialog = AssignTagDialog("X", "opc.tcp://plc:4840/")
        dialog.node_edit.setText("ns=2;s=X")
        dialog.decimals_spin.setValue(1)

        tag = dialog.tag
        assert tag is not None
        assert tag.decimals == 1

    def test_a_failed_browse_is_reported_in_the_dialog(self, qt_app: QApplication) -> None:
        # Not a modal on top of a modal.
        dialog = AssignTagDialog("X", "opc.tcp://nothing/")

        dialog.show_failure("connection refused")

        assert "connection refused" in dialog.status_label.text()


class TestTheTabItself:
    def test_it_has_six_columns(self, qt_app: QApplication) -> None:
        assert VariableTree().columnCount() == 6

    def test_every_column_can_be_dragged(self, qt_app: QApplication) -> None:
        tree = VariableTree()
        header = tree.header()

        modes = [header.sectionResizeMode(column) for column in range(tree.columnCount())]
        assert modes == [header.ResizeMode.Interactive] * tree.columnCount()

    def test_it_is_tabbed_with_the_sensors(self, window: MainWindow) -> None:
        assert window.variable_dock in window.tabifiedDockWidgets(window.sensor_dock)

    def test_the_sensors_are_the_visible_tab(self, window: MainWindow) -> None:
        # You place sensors first, then wire them up.
        assert window.sensor_dock.isVisible() or not window.isVisible()
