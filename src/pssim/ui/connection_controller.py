"""The lifetime of the OPC UA connection, and the pump that feeds the tab.

Split out of `MainWindow` for the same reason `ProjectLoader` was: it is a small
machine with states of its own — connect, hold, drop, reconnect — and a window
that owned it directly would grow another few hundred lines of something that is
not about windows.

**This is the thread boundary.** `OpcUaSource` runs its own thread with its own
asyncio loop and writes into a `StateStore` (R10). Nothing here ever touches
asyncua: a `QTimer` on the UI thread takes a snapshot of the store and copies it
into the registry, which is exactly the "read a snapshot, never call across"
arrangement the renderer already uses.

`poll` takes the time rather than reading the clock, so a test can drive it
without sleeping — the same rule interpolation and the store follow.
"""

from __future__ import annotations

import time
from typing import Final

from PySide6.QtCore import QObject, QTimer, Signal

from pssim.domain.errors import PSsimError
from pssim.io.base import DataSource, SourceStatus
from pssim.io.opcua_source import OpcUaConfig, OpcUaSource
from pssim.io.store import StateStore
from pssim.observability import get_logger
from pssim.ui.settings import ConnectionSettings
from pssim.ui.variable_registry import VariableRegistry

logger = get_logger(__name__)

#: How often the tab is refreshed from the store. Not the publishing interval:
#: this is a table a person reads, and ten times a second is already faster than
#: anyone can follow. The scene is driven separately and at frame rate.
POLL_INTERVAL_MS: Final = 100

#: A signal that has not arrived for this long is shown as stale rather than
#: current. The scene keeps drawing the last value either way (R10) — this is
#: what stops the table implying it is fresh.
DEFAULT_STALE_AFTER_S: Final = 1.0


class ConnectionController(QObject):
    """Owns the source, and copies what it collects into the variable registry."""

    status_changed = Signal(object)
    """Carries a `SourceStatus`. The window puts it in the status bar."""

    values_changed = Signal()
    """Something in the registry now reads differently — redraw the tab."""

    def __init__(
        self,
        variables: VariableRegistry,
        parent: QObject | None = None,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> None:
        super().__init__(parent)
        self._variables = variables
        self._stale_after_s = stale_after_s
        self._source: DataSource | None = None
        self._store = StateStore()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

    # -- reading ------------------------------------------------------------

    @property
    def store(self) -> StateStore:
        """The store the source writes into. The one thing shared across threads."""
        return self._store

    @property
    def source(self) -> DataSource | None:
        """The live source, or `None`. Observable state, not a secret.

        Typed as the `DataSource` protocol rather than as `OpcUaSource`: nothing
        here needs OPC UA in particular, and R12 exists so a replay or a
        different transport can take its place without this file changing.
        """
        return self._source

    def use_source(self, source: DataSource) -> None:
        """Take over an already-built source and start pumping from it.

        The seam R12 promises: a `ReplaySource` reproducing a fault from the
        field, or a stub in a test, goes here without `connect_to` having to
        know about either. The source must already share this controller's
        store, or nothing it collects will be seen.
        """
        self.disconnect_from_server()
        self._source = source
        self._timer.start()
        self.status_changed.emit(self.status)

    @property
    def status(self) -> SourceStatus:
        return self._source.status if self._source is not None else SourceStatus.DISCONNECTED

    @property
    def is_connected(self) -> bool:
        return self.status is SourceStatus.CONNECTED

    # -- the connection -----------------------------------------------------

    def connect_to(self, settings: ConnectionSettings) -> str | None:
        """Start a source for whatever is currently bound.

        Returns `None` on success, or a sentence saying why not. A message rather
        than an exception: "no tags assigned yet" is a normal state of a project
        that has not been wired up, not a fault.
        """
        self.disconnect_from_server()

        bindings = self._variables.bindings()
        if not bindings:
            return "No variable has a tag yet - assign one first"

        try:
            source = OpcUaSource(
                OpcUaConfig(
                    endpoint=settings.endpoint,
                    bindings=bindings,
                    publishing_interval_ms=settings.publishing_interval_ms,
                    allow_writing=settings.allow_writing,
                ),
                store=self._store,
            )
            source.start()
        except PSsimError as exc:
            logger.warning("could not start the source", error=str(exc))
            return str(exc)

        self._source = source
        self._timer.start()
        logger.info(
            "connecting",
            endpoint=settings.endpoint,
            signals=len(bindings),
            writing=settings.allow_writing,
        )
        self.status_changed.emit(self.status)
        return None

    def disconnect_from_server(self) -> None:
        """Stop the source. Idempotent, and safe to call on a window closing.

        Not called `disconnect`: `QObject` already has a static method of that
        name, and shadowing it would be a trap for anyone expecting Qt's.

        The store is **not** cleared: the scene goes on showing the last known
        state (R10), and the tab says `Disconnected` beside it rather than
        blanking the numbers the viewport is still drawing.
        """
        self._timer.stop()
        source = self._source
        self._source = None
        if source is not None:
            source.stop()
        self._variables.set_connected(False)
        self.status_changed.emit(SourceStatus.DISCONNECTED)
        self.values_changed.emit()

    # -- the pump -----------------------------------------------------------

    def poll(self, now_s: float) -> bool:
        """Copy one snapshot of the store into the registry.

        Returns whether anything now reads differently, so the caller can skip a
        redraw of an unchanged table. `now_s` is a parameter rather than a call
        to `time.monotonic()` so a test can drive staleness without sleeping.
        """
        was_connected = self._variables.is_connected
        is_connected = self.is_connected
        if is_connected != was_connected:
            self._variables.set_connected(is_connected)
        changed = is_connected != was_connected

        # Nothing is copied while disconnected. The store still holds the last
        # values — the scene goes on drawing them (R10) — but calling them live
        # would say the opposite of what the Status column just said.
        if not is_connected:
            return changed

        latest = self._store.latest_time()
        if latest is None:
            return changed

        stale = self._store.stale_signals(at_time_s=now_s, stale_after_s=self._stale_after_s)
        for signal, value in self._store.sample_all(at_time_s=latest).items():
            if self._variables.set_value(signal, value, is_stale=signal in stale):
                changed = True
        return changed

    def publish(self, variable: str, value: float) -> bool:
        """Offer a value for the source to write out. Returns whether the tab
        now reads differently.

        Queuing is not sending: whether it leaves this process is decided by the
        source, and only when writing was deliberately allowed. A variable with
        no output binding is simply never taken.

        The value is also recorded against the variable, because for an output
        it *is* the current value — nothing will ever arrive from the server to
        tell us what it is, and a row reading "waiting" for ever would be
        waiting for something that is never coming.
        """
        self._store.queue_write(variable, value)
        return self._variables.set_value(variable, value, is_stale=not self.is_connected)

    def _on_tick(self) -> None:
        """The timer's slot. The clock is read **here** and nowhere deeper."""
        if self.poll(time.monotonic()):
            self.values_changed.emit()
        self.status_changed.emit(self.status)
