"""Values arriving from the PLC drive the joints.

The bug: values were read, the registry held them, the table showed them, and
nothing moved. `_refresh_variables_view` redrew the table and there was no path
from a variable to the joint carrying its name.

No server: `ConnectionController.poll` takes the store and the time, so a value
can be put in by hand at a known instant. What is exercised is the window's own
wiring, which is where it was missing.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.domain.model_joints import ModelJoint, ModelJointKind  # noqa: E402
from pssim.io.base import SourceStatus  # noqa: E402
from pssim.io.store import StateStore  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402
from pssim.ui.settings import ConnectionSettings, VariableTag  # noqa: E402

pytestmark = pytest.mark.ui

VARIABLE = "tilt_pos"
NODE = "ns=2;s=Axes.X.ActPos"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def trajectory() -> ModelJoint:
    """A straight path a metre long, so a value is a distance in metres."""
    return ModelJoint(
        name="rail",
        kind=ModelJointKind.TRAJECTORY,
        origin=(0.0, 0.0, 0.0),
        target=(1.0, 0.0, 0.0),
        variable=VARIABLE,
    )


class _ConnectedSource:
    """A `DataSource` that is connected and does nothing else (R12)."""

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


class Window:
    """A window with one axis whose variable is bound to a tag."""

    def __init__(self, joint: ModelJoint) -> None:
        self.window = MainWindow(viewport_factory=QWidget)
        self.joint_id = self.window._joints.add(joint).joint_id
        self.window.save_connection_settings(
            ConnectionSettings().with_tag(VARIABLE, VariableTag(node_id=NODE))
        )
        self.window.refresh_variables()

    def deliver(self, value: float, at_s: float = 10.0) -> None:
        """One value, as if it had arrived from the server.

        A stub source, because `poll` copies nothing while disconnected — the
        store still holds the last values and calling them live would say the
        opposite of what the Status column just said.
        """
        controller = self.window._connection
        if controller.source is None:
            controller.use_source(_ConnectedSource(controller.store))
        controller.store.put(VARIABLE, value, source_time_s=at_s)
        if controller.poll(now_s=at_s):
            # The controller's own signal, so what is exercised is the window's
            # wiring to it rather than a method chosen by the test.
            controller.values_changed.emit()

    @property
    def joint_value(self) -> float:
        entry = self.window._joints.get(self.joint_id)
        assert entry is not None
        return entry.value

    @property
    def shown(self) -> float | None:
        entry = self.window._variables.get(VARIABLE)
        assert entry is not None
        return entry.value

    def close(self) -> None:
        self.window.close()


@pytest.fixture
def scene(qt_app: QApplication) -> Window:
    return Window(trajectory())


class TestAValueMovesTheModel:
    """The bug: values were read, the table showed them, and nothing moved."""

    def test_the_joint_takes_the_value(self, scene: Window) -> None:
        scene.deliver(0.4)

        assert scene.joint_value == pytest.approx(0.4)

    def test_a_joint_starts_at_zero(self, scene: Window) -> None:
        assert scene.joint_value == pytest.approx(0.0)

    def test_a_later_value_replaces_it(self, scene: Window) -> None:
        scene.deliver(0.4, at_s=10.0)
        scene.deliver(0.9, at_s=11.0)

        assert scene.joint_value == pytest.approx(0.9)

    def test_the_table_shows_it_too(self, scene: Window) -> None:
        scene.deliver(0.4)

        assert scene.shown == pytest.approx(0.4)

    def test_a_variable_no_joint_owns_moves_nothing(self, qt_app: QApplication) -> None:
        # A sensor's variable, or one whose axis was renamed: it still arrives
        # and is still shown, and there is nothing for it to drive.
        window = MainWindow(viewport_factory=QWidget)
        window.save_connection_settings(
            ConnectionSettings().with_tag("orphan", VariableTag(node_id=NODE))
        )
        window.refresh_variables()

        window._connection.use_source(_ConnectedSource(window._connection.store))
        window._connection.store.put("orphan", 1.0, source_time_s=10.0)
        window._connection.poll(now_s=10.0)

        assert window._variables.get("orphan") is None
        window.close()
