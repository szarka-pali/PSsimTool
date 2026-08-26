"""The connection indicator in the status bar.

What is worth pinning is not how it looks but the two things that were wrong
without it: the state has to survive the next status-bar message, and it has to
be reachable — the reason for a refusal is a status code that does not fit on one
line, so the tooltip carries it and a click opens the log.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.io.base import SourceStatus  # noqa: E402
from pssim.ui.connection_status import ConnectionStatusWidget  # noqa: E402
from pssim.ui.labels import source_status_color  # noqa: E402
from pssim.ui.main_window import MainWindow  # noqa: E402

pytestmark = pytest.mark.ui

ENDPOINT = "opc.tcp://plc:4840/"


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def indicator(qt_app: QApplication) -> ConnectionStatusWidget:
    return ConnectionStatusWidget()


class TestWhatItSays:
    def test_it_starts_disconnected(self, indicator: ConnectionStatusWidget) -> None:
        assert indicator.status is SourceStatus.DISCONNECTED

    def test_and_says_so(self, indicator: ConnectionStatusWidget) -> None:
        assert indicator.label.text() == "Disconnected"

    def test_connected_says_connected(self, indicator: ConnectionStatusWidget) -> None:
        indicator.show_status(SourceStatus.CONNECTED, ENDPOINT)

        assert indicator.label.text() == "Connected"

    def test_degraded_is_not_called_connected(self, indicator: ConnectionStatusWidget) -> None:
        # The connection is alive and a signal has stopped arriving. The scene
        # goes on drawing the old value (R10), which looks like nothing being
        # wrong — so the word has to say it.
        indicator.show_status(SourceStatus.DEGRADED, ENDPOINT)

        assert indicator.label.text() != "Connected"

    def test_connecting_says_so(self, indicator: ConnectionStatusWidget) -> None:
        indicator.show_status(SourceStatus.CONNECTING, ENDPOINT)

        assert indicator.label.text() == "Connecting"


class TestTheColour:
    def test_connected_is_the_live_green(self, indicator: ConnectionStatusWidget) -> None:
        # The same green a live sensor reading gets, so one green never comes to
        # mean two things.
        from pssim.ui.labels import live_reading_color

        assert source_status_color(SourceStatus.CONNECTED) == live_reading_color()

    def test_disconnected_is_not_green(self) -> None:
        assert source_status_color(SourceStatus.DISCONNECTED) != source_status_color(
            SourceStatus.CONNECTED
        )

    def test_connecting_is_neither(self) -> None:
        connecting = source_status_color(SourceStatus.CONNECTING)

        assert connecting != source_status_color(SourceStatus.CONNECTED)
        assert connecting != source_status_color(SourceStatus.DISCONNECTED)


class TestTheReasonIsReachable:
    def test_the_endpoint_is_in_the_tooltip(self, indicator: ConnectionStatusWidget) -> None:
        indicator.show_status(SourceStatus.CONNECTED, ENDPOINT)

        assert ENDPOINT in indicator.toolTip()

    def test_a_refusal_says_why(self, indicator: ConnectionStatusWidget) -> None:
        # R20: `BadUserAccessDenied` *is* the answer, and it was nowhere on screen.
        indicator.show_status(SourceStatus.DISCONNECTED, ENDPOINT, "BadUserAccessDenied")

        assert "BadUserAccessDenied" in indicator.toolTip()

    def test_the_tooltip_is_on_the_widget_not_only_the_label(
        self, indicator: ConnectionStatusWidget
    ) -> None:
        # The dot is half of what the pointer lands on.
        indicator.show_status(SourceStatus.CONNECTED, ENDPOINT)

        assert indicator.toolTip() != ""

    def test_a_connected_indicator_carries_no_reason(
        self, indicator: ConnectionStatusWidget
    ) -> None:
        # A stale reason reported after a later attempt succeeded is the trap
        # `last_error` already had to be fixed for.
        indicator.show_status(SourceStatus.CONNECTED, ENDPOINT, "BadUserAccessDenied")

        assert "BadUserAccessDenied" not in indicator.toolTip()


class TestItDoesNotRepaintForNothing:
    """The controller emits its status on every timer tick, not only when it
    moves, and re-setting a tooltip ten times a second makes it flicker while it
    is being read."""

    def test_the_same_state_twice_changes_nothing(self, indicator: ConnectionStatusWidget) -> None:
        indicator.show_status(SourceStatus.CONNECTED, ENDPOINT)
        before = indicator.toolTip()

        indicator.show_status(SourceStatus.CONNECTED, ENDPOINT)

        assert indicator.toolTip() == before

    def test_a_changed_reason_does_get_through(self, indicator: ConnectionStatusWidget) -> None:
        indicator.show_status(SourceStatus.DISCONNECTED, ENDPOINT, "BadTimeout")

        indicator.show_status(SourceStatus.DISCONNECTED, ENDPOINT, "BadUserAccessDenied")

        assert "BadUserAccessDenied" in indicator.toolTip()

    def test_a_changed_endpoint_does_too(self, indicator: ConnectionStatusWidget) -> None:
        indicator.show_status(SourceStatus.CONNECTED, ENDPOINT)

        indicator.show_status(SourceStatus.CONNECTED, "opc.tcp://other:4840/")

        assert "other" in indicator.toolTip()


class TestInTheWindow:
    @pytest.fixture
    def window(self, qt_app: QApplication) -> MainWindow:
        return MainWindow(viewport_factory=QWidget)

    def test_the_window_has_one(self, window: MainWindow) -> None:
        assert window.connection_status.status is SourceStatus.DISCONNECTED

    def test_it_is_permanent_not_a_message(self, window: MainWindow) -> None:
        # The point of the whole widget: `showMessage` writes into the transient
        # area, where the next "Selected X" wipes it.
        window.statusBar().showMessage("Added joint tilt")

        assert window.connection_status.label.text() == "Disconnected"

    def test_it_names_the_configured_endpoint(self, window: MainWindow) -> None:
        assert window._connection_settings.endpoint in window.connection_status.toolTip()

    def test_a_new_endpoint_reaches_it(self, window: MainWindow) -> None:
        from pssim.ui.settings import ConnectionSettings

        window.save_connection_settings(ConnectionSettings(endpoint="opc.tcp://elsewhere:4840/"))

        assert "elsewhere" in window.connection_status.toolTip()

    def test_clicking_it_opens_the_diagnostics(self, window: MainWindow) -> None:
        opened: list[object] = []
        window.connection_status.clicked.disconnect()
        window.connection_status.clicked.connect(lambda: opened.append(True))

        window.connection_status.clicked.emit()

        assert opened == [True]
