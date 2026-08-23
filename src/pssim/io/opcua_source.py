"""The OPC UA client as a `DataSource`.

Runs in **its own thread with its own asyncio loop**. The Panda3D task manager
supports `async def` tasks, but it awaits Panda3D futures, not asyncio ones — asyncua
cannot run in it. See docs/architecture.md R10.

The rules for this layer are in `.claude/rules/io-opcua.md`.

**Status: written, not verified against a real PLC.** What is verified is only what the
tests in `tests/integration/` cover against `mock_server.py`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from pssim.config.binding import JointBinding, SourceSettings
from pssim.domain.errors import DataSourceError
from pssim.io.base import SourceStatus
from pssim.io.store import StateStore
from pssim.io.timebase import Timebase
from pssim.observability import get_logger

logger = get_logger(__name__)

_RECONNECT_INITIAL_S: Final = 0.5
_RECONNECT_MAX_S: Final = 30.0
_QUEUE_SIZE: Final = 4
"""queuesize > 1: when falling behind we still get the intermediate samples. With
queuesize=1, fast movement would keep only the end points and the interpolation would
cut corners."""


@dataclass(frozen=True, slots=True)
class OpcUaConfig:
    """The connection configuration.

    `endpoint` comes from the environment or from the CLI, not from `machines/*.yaml` —
    that file is versioned and a customer's PLC addresses do not belong in the
    repository.
    """

    endpoint: str
    bindings: tuple[JointBinding, ...]
    publishing_interval_ms: int = 50
    session_timeout_ms: int = 10_000


class OpcUaSource:
    """A flow of data from an OPC UA server through a subscription.

    Implements `pssim.io.base.DataSource`.
    """

    def __init__(self, config: OpcUaConfig, store: StateStore | None = None) -> None:
        if not config.endpoint:
            raise DataSourceError("endpoint must not be empty")
        if not config.bindings:
            raise DataSourceError("no signal bindings - there is nothing to subscribe to")

        self._config = config
        self._store = store if store is not None else StateStore()
        self._status = SourceStatus.DISCONNECTED
        self._timebase = Timebase()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        # Thread A signals through a threading.Event, thread B waits on an
        # asyncio.Event. The bridge between them is `call_soon_threadsafe` in `stop()`.
        self._async_stop: asyncio.Event | None = None
        self._node_to_joint = {b.node_id: b for b in config.bindings}
        self._revised_interval_ms: int | None = None

    # -- DataSource ---------------------------------------------------------

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def store(self) -> StateStore:
        return self._store

    @property
    def revised_interval_ms(self) -> int | None:
        """The interval the server actually granted. `None` until there is a subscription.

        Use this to compute `render_delay` — not the one we asked for.
        """
        return self._revised_interval_ms

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_thread,
            name="pssim-opcua",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        loop = self._loop
        async_stop = self._async_stop
        if loop is not None and async_stop is not None and loop.is_running():
            loop.call_soon_threadsafe(async_stop.set)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning("opcua thread did not stop within 5 s, carrying on")
        self._thread = None
        self._status = SourceStatus.DISCONNECTED

    # -- thread B -----------------------------------------------------------

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._connect_forever())
        except Exception:
            logger.exception("opcua thread died")
            self._status = SourceStatus.DISCONNECTED

    async def _connect_forever(self) -> None:
        """Reconnect with exponential backoff.

        Losing the connection is a normal state, not an exception. We log the first
        attempt and then every tenth — otherwise one unreachable PLC fills the log.
        """
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        delay = _RECONNECT_INITIAL_S
        attempt = 0

        while not self._stop_event.is_set():
            attempt += 1
            if attempt == 1 or attempt % 10 == 0:
                logger.info("connecting", endpoint=self._config.endpoint, attempt=attempt)

            self._status = SourceStatus.CONNECTING
            try:
                await self._session()
                delay = _RECONNECT_INITIAL_S  # a successful connection resets the backoff
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt == 1 or attempt % 10 == 0:
                    logger.warning("spojenie zlyhalo", error=str(exc), retry_in_s=delay)

            self._status = SourceStatus.DISCONNECTED
            if await self._wait_for_stop(timeout_s=delay):
                return
            delay = min(delay * 2.0, _RECONNECT_MAX_S)

    async def _wait_for_stop(self, timeout_s: float | None = None) -> bool:
        """Wait for the stop signal. Returns `True` if it came, `False` on timeout."""
        assert self._async_stop is not None
        if timeout_s is None:
            await self._async_stop.wait()
            return True
        try:
            await asyncio.wait_for(self._async_stop.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return True

    async def _session(self) -> None:
        """One session: connect, subscribe, hold until it drops or a stop arrives."""
        from asyncua import Client  # a heavy import - only when actually needed

        client = Client(url=self._config.endpoint)
        client.session_timeout = self._config.session_timeout_ms

        async with client:
            subscription = await client.create_subscription(
                period=self._config.publishing_interval_ms,
                handler=_SubscriptionHandler(self),
            )
            nodes = [client.get_node(node_id) for node_id in self._node_to_joint]
            await subscription.subscribe_data_change(nodes, queuesize=_QUEUE_SIZE)

            # The server may revise the interval - TwinCAT, for instance, ties it
            # to the task cycle.
            revised = getattr(subscription.parameters, "RequestedPublishingInterval", None)
            self._revised_interval_ms = int(revised) if revised else None

            self._status = SourceStatus.CONNECTED
            logger.info(
                "subscription active",
                signals=len(nodes),
                revised_interval_ms=self._revised_interval_ms,
            )

            try:
                await self._wait_for_stop()
            finally:
                await subscription.delete()

    # -- callback zo subscription ------------------------------------------

    def handle_data_change(self, node_id: str, value: Any, source_time: datetime | None) -> None:
        """Handle one notification. **Never raises** — an exception here kills the loop.

        Public deliberately: it is the contract between the source and its
        subscription handler.
        """
        binding = self._node_to_joint.get(node_id)
        if binding is None:
            return

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            logger.warning(
                "signal is not numeric, ignoring",
                node=node_id,
                type=type(value).__name__,
            )
            return

        if source_time is None:
            # Some versions of Codesys do not send SourceTimestamp at all.
            internal_time = time.monotonic()
        else:
            internal_time = self._timebase.to_internal(source_time, time.monotonic())

        self._store.put(
            signal=binding.joint_name,
            value=binding.to_internal(float(value)),
            source_time_s=internal_time,
        )


class _SubscriptionHandler:
    """The handler for asyncua. The `datachange_notification` method is an asyncua contract."""

    __slots__ = ("_source",)

    def __init__(self, source: OpcUaSource) -> None:
        self._source = source

    def datachange_notification(self, node: Any, val: Any, data: Any) -> None:
        """Called by asyncua from thread B. An exception from here would kill the whole loop."""
        try:
            source_time = getattr(
                getattr(getattr(data, "monitored_item", None), "Value", None),
                "SourceTimestamp",
                None,
            )
            self._source.handle_data_change(str(node.nodeid.to_string()), val, source_time)
        except Exception:
            logger.exception("error in the datachange handler, carrying on")


def build_source(
    settings: SourceSettings,
    bindings: tuple[JointBinding, ...],
    *,
    endpoint_override: str | None = None,
    store: StateStore | None = None,
) -> OpcUaSource:
    """Build a source from a machine configuration.

    `endpoint_override` takes precedence over `machines/*.yaml` — real endpoints are
    given on the CLI or through the environment, not in a versioned file.
    """
    return OpcUaSource(
        OpcUaConfig(
            endpoint=endpoint_override or settings.endpoint,
            bindings=bindings,
            publishing_interval_ms=settings.publishing_interval_ms,
        ),
        store=store,
    )
