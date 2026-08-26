"""Values arriving from the PLC drive the joints, under control.

Three things at once, because they are one behaviour:

- a value that arrives **moves the model** — the registry holding it and the
  table showing it is not the point of reading it;
- a variable can be switched off, so the joint goes back to being set by hand;
- a value outside the joint's limits is clamped and shown red, because a PLC
  sending 3000 mm to an axis that stops at 2450 is a fault worth seeing rather
  than silently flattening.

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


def trajectory(limits: tuple[float, float] | None = None) -> ModelJoint:
    """A straight path a metre long, so a value is a distance in metres."""
    return ModelJoint(
        name="rail",
        kind=ModelJointKind.TRAJECTORY,
        origin=(0.0, 0.0, 0.0),
        target=(1.0, 0.0, 0.0),
        variable=VARIABLE,
        limits=limits,
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


class TestOutOfRange:
    """A PLC sending 3000 mm to an axis that stops at 2450 is a fault worth
    seeing, not something to flatten silently."""

    @pytest.fixture
    def limited(self, qt_app: QApplication) -> Window:
        return Window(trajectory(limits=(0.2, 0.8)))

    def test_a_value_above_the_limit_is_clamped(self, limited: Window) -> None:
        limited.deliver(0.95)

        assert limited.joint_value == pytest.approx(0.8)

    def test_a_value_below_it_too(self, limited: Window) -> None:
        # Moved off the lower limit first: a joint with limits does not rest at
        # zero (R9), so this would pass with nothing driving it at all.
        limited.deliver(0.5, at_s=10.0)

        limited.deliver(0.05, at_s=11.0)

        assert limited.joint_value == pytest.approx(0.2)

    def test_a_value_inside_is_untouched(self, limited: Window) -> None:
        limited.deliver(0.5)

        assert limited.joint_value == pytest.approx(0.5)

    def test_the_row_says_it_was_out_of_range(self, limited: Window) -> None:
        limited.deliver(0.95)

        entry = limited.window._variables.get(VARIABLE)
        assert entry is not None
        assert entry.is_out_of_range is True

    def test_and_stops_saying_so_once_it_is_back(self, limited: Window) -> None:
        limited.deliver(0.95, at_s=10.0)
        limited.deliver(0.5, at_s=11.0)

        entry = limited.window._variables.get(VARIABLE)
        assert entry is not None
        assert entry.is_out_of_range is False

    def test_the_value_shown_is_the_one_that_arrived(self, limited: Window) -> None:
        # Not the clamped one: seeing 0.95 in red says what the PLC sent, which
        # is the whole diagnostic. The joint took 0.8.
        limited.deliver(0.95)

        assert limited.shown == pytest.approx(0.95)


class TestSwitchingItOff:
    def test_it_is_on_by_default(self, scene: Window) -> None:
        entry = scene.window._variables.get(VARIABLE)
        assert entry is not None
        assert entry.is_applied is True

    def test_switched_off_the_joint_stops_following(self, scene: Window) -> None:
        scene.window.set_variable_applied(VARIABLE, False)

        scene.deliver(0.4)

        assert scene.joint_value == pytest.approx(0.0)

    def test_but_the_value_still_arrives(self, scene: Window) -> None:
        # Off means "do not move the model", not "stop reading".
        scene.window.set_variable_applied(VARIABLE, False)

        scene.deliver(0.4)

        assert scene.shown == pytest.approx(0.4)

    def test_switched_off_the_joint_can_be_set_by_hand(self, scene: Window) -> None:
        scene.window.set_variable_applied(VARIABLE, False)
        scene.deliver(0.4)

        scene.window.apply_joint_value(scene.joint_id, 0.7)

        assert scene.joint_value == pytest.approx(0.7)

    def test_switching_it_back_on_takes_the_next_value(self, scene: Window) -> None:
        scene.window.set_variable_applied(VARIABLE, False)
        scene.deliver(0.4, at_s=10.0)

        scene.window.set_variable_applied(VARIABLE, True)
        scene.deliver(0.9, at_s=11.0)

        assert scene.joint_value == pytest.approx(0.9)

    def test_a_value_out_of_range_is_not_flagged_while_it_is_off(
        self, qt_app: QApplication
    ) -> None:
        # Nothing is being clamped, so nothing is being refused.
        scene = Window(trajectory(limits=(0.2, 0.8)))
        scene.window.set_variable_applied(VARIABLE, False)

        scene.deliver(0.95)

        entry = scene.window._variables.get(VARIABLE)
        assert entry is not None
        assert entry.is_out_of_range is False
        scene.close()
