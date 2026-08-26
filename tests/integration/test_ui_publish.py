"""What the application offers to the server, and when.

The bug: a sensor's value reached the outbox only on the frame its reading
*changed*. The publish loop sat below `if not changed: return`, which is an
early exit meant for the table redraw. So connecting and then leaving the scene
alone — which is most of the time — wrote nothing at all, and the node on the
server never moved.

Offering is not sending: whether anything leaves the process is the source's
decision and only when writing was deliberately allowed (R19). These tests check
the outbox, which is the boundary the window is responsible for.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.config.binding import BindingDirection  # noqa: E402
from pssim.domain.model_joints import ModelJoint, ModelJointKind  # noqa: E402
from pssim.domain.sensors import Sensor, SensorKind, SensorReading  # noqa: E402
from pssim.io.base import SourceStatus  # noqa: E402
from pssim.io.store import StateStore  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.settings import ConnectionSettings, VariableTag  # noqa: E402

pytestmark = pytest.mark.ui

FLAG_NODE = "ns=2;s=Sim.Flag1"
AXIS_NODE = "ns=2;s=Axes.X.ActPos"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


class FakeViewport(QWidget):
    """A viewport that answers `sensor_reading`.

    A bare `QWidget` does not, and `refresh_sensor_readings` returns at its first
    line for one — so a test using a bare widget would prove nothing about what
    happens further down.
    """

    def __init__(self) -> None:
        super().__init__()
        self.blocked = False

    def sensor_reading(self, _sensor_id: str) -> SensorReading:
        return SensorReading(value=1.0 if self.blocked else 0.0, is_valid=True)

    def set_joint_value(self, _joint_id: str, _value: float) -> None:
        return None


class _ConnectedSource:
    """A connected `DataSource` that carries nothing (R12)."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.CONNECTED

    @property
    def store(self) -> StateStore:
        return self._store

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


def beam(variable: str = "gate_state") -> Sensor:
    return Sensor(
        name="gate",
        kind=SensorKind.BEAM,
        origin=(0.0, 0.0, 0.0),
        direction=(1.0, 0.0, 0.0),
        range_m=1.0,
        variable=variable,
    )


def rail(variable: str = "rail_pos") -> ModelJoint:
    return ModelJoint(
        name="rail",
        kind=ModelJointKind.TRAJECTORY,
        origin=(0.0, 0.0, 0.0),
        target=(1.0, 0.0, 0.0),
        variable=variable,
    )


class Scene:
    """A window with a sensor bound to a writable node, and a connection."""

    def __init__(self, tags: dict[str, VariableTag]) -> None:
        self.viewport = FakeViewport()
        self.window = MainWindow(viewport_factory=lambda: self.viewport)

    def connect(self) -> None:
        controller = self.window._connection
        controller.use_source(_ConnectedSource(controller.store))
        self.window.on_connected()

    @property
    def outbox(self) -> dict[str, float]:
        return self.window._connection.store.pending_writes()

    def close(self) -> None:
        self.window.close()


def scene_with_sensor(qt_app: QApplication) -> Scene:
    built = Scene({})
    built.window._sensors.add(beam())
    built.window.save_connection_settings(
        ConnectionSettings(allow_writing=True).with_tag(
            "gate_state", VariableTag(node_id=FLAG_NODE)
        )
    )
    built.window.refresh_variables()
    return built


class TestASensorIsOfferedOnConnect:
    """Its state is true the moment there is a connection; waiting for it to
    change means a machine that is simply not moving is never told about."""

    def test_the_reading_reaches_the_outbox(self, qt_app: QApplication) -> None:
        scene = scene_with_sensor(qt_app)

        scene.connect()

        assert "gate_state" in scene.outbox
        scene.close()

    def test_and_it_is_the_current_value(self, qt_app: QApplication) -> None:
        scene = scene_with_sensor(qt_app)
        scene.viewport.blocked = True

        scene.connect()

        assert scene.outbox["gate_state"] == pytest.approx(1.0)
        scene.close()

    def test_a_clear_beam_is_offered_too(self, qt_app: QApplication) -> None:
        # Zero is a value, not the absence of one: a PLC watching this node has
        # to be told the beam is clear as much as that it is blocked.
        scene = scene_with_sensor(qt_app)

        scene.connect()

        assert scene.outbox["gate_state"] == pytest.approx(0.0)
        scene.close()


class TestASteadyReadingIsStillOffered:
    """The defect: the publish sat below an early return meant for the redraw,
    so a reading that had not changed since the last frame was never sent."""

    def test_a_refresh_that_changes_nothing_still_offers(self, qt_app: QApplication) -> None:
        scene = scene_with_sensor(qt_app)
        scene.connect()
        scene.window._connection.store.take_writes()

        scene.window.refresh_sensor_readings()

        assert "gate_state" in scene.outbox
        scene.close()

    def test_a_changed_reading_is_offered(self, qt_app: QApplication) -> None:
        scene = scene_with_sensor(qt_app)
        scene.connect()
        scene.window._connection.store.take_writes()
        scene.viewport.blocked = True

        scene.window.refresh_sensor_readings()

        assert scene.outbox["gate_state"] == pytest.approx(1.0)
        scene.close()

    def test_moving_a_joint_offers_it_too(self, qt_app: QApplication) -> None:
        scene = scene_with_sensor(qt_app)
        joint_id = scene.window._joints.add(rail()).joint_id
        scene.window.refresh_variables()
        scene.connect()
        scene.window._connection.store.take_writes()

        scene.window.apply_joint_value(joint_id, 0.5)

        assert "gate_state" in scene.outbox
        scene.close()


class TestAWriteBoundJointToo:
    def test_it_is_offered_on_connect(self, qt_app: QApplication) -> None:
        scene = Scene({})
        joint_id = scene.window._joints.add(rail()).joint_id
        scene.window.save_connection_settings(
            ConnectionSettings(allow_writing=True).with_tag(
                "rail_pos",
                VariableTag(node_id=AXIS_NODE, direction=BindingDirection.WRITE),
            )
        )
        scene.window.refresh_variables()
        scene.window.apply_joint_value(joint_id, 0.4)
        scene.window._connection.store.take_writes()

        scene.connect()

        assert scene.outbox["rail_pos"] == pytest.approx(0.4)
        scene.close()

    def test_a_read_bound_joint_is_not(self, qt_app: QApplication) -> None:
        scene = Scene({})
        scene.window._joints.add(rail())
        scene.window.save_connection_settings(
            ConnectionSettings(allow_writing=True).with_tag(
                "rail_pos", VariableTag(node_id=AXIS_NODE)
            )
        )
        scene.window.refresh_variables()

        scene.connect()

        assert "rail_pos" not in scene.outbox
        scene.close()
