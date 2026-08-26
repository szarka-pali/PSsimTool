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

from pssim.config.binding import (
    BindingDirection,
    JointBinding,
    SignalBinding,
    SourceSettings,
)
from pssim.domain.errors import DataSourceError
from pssim.io.base import SourceStatus
from pssim.io.opcua_diagnostics import DiagnosticLog, DiagnosticStep
from pssim.io.opcua_path import is_numeric_value, parse_path, resolve_value
from pssim.io.opcua_security import Credentials, configure
from pssim.io.opcua_values import FALLBACK_TYPE, coerce_for, is_writable_type
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
    bindings: tuple[SignalBinding, ...]
    publishing_interval_ms: int = 50
    session_timeout_ms: int = 10_000

    credentials: Credentials | None = None
    """How to get in: policy, mode, user. `None` is the open, anonymous
    connection this could only ever make before."""

    allow_writing: bool = False
    """Whether the session runs a write pump at all.

    Off by default and checked here rather than only in the UI: with it off the
    pump is never created, so a value that reaches the store's outbox by mistake
    has nothing that could carry it to a server. See `.claude/rules/io-opcua.md`
    — writing is tested exclusively against `pssim mock-server`.
    """


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
        # A **list** per node, not one binding: `Position.X` and `Position.Y`
        # are two signals reading two places in one notification, so a dict
        # keyed by node id would silently keep only the last of them.
        self._node_to_bindings: dict[str, list[SignalBinding]] = {}
        for binding in config.bindings:
            if binding.direction is BindingDirection.READ:
                self._node_to_bindings.setdefault(binding.node_id, []).append(binding)
        self._outputs = {
            b.signal: b for b in config.bindings if b.direction is BindingDirection.WRITE
        }
        self._revised_interval_ms: int | None = None
        self._diagnostics = DiagnosticLog()
        # Each output node's variant type, read from the server once a session.
        # Empty until then, and `FALLBACK_TYPE` stands in for anything missing.
        self._write_types: dict[str, str] = {}

    # -- DataSource ---------------------------------------------------------

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def store(self) -> StateStore:
        return self._store

    @property
    def diagnostics(self) -> DiagnosticLog:
        """What the last attempt tried, and where it stopped.

        `status` says whether there is a connection; this says why there is not.
        A refused password and a server that is not there are the same status and
        very different problems.
        """
        return self._diagnostics

    @property
    def last_error(self) -> str | None:
        """One line about **why there is no connection**, or `None` while there is.

        Connected means no error, whatever the log still holds: the diagnostics
        are append-only now (a reconnect must not wipe the failure that explains
        the last attempt), so a failed first try would otherwise go on being
        reported after a later one succeeded.

        A refused *write* stays in the diagnostics for anyone reading them; it is
        not a reason to call a live connection broken.
        """
        if self._status is SourceStatus.CONNECTED:
            return None
        failure = self._diagnostics.last_failure
        if failure is None:
            return None
        return failure.status_code or failure.detail

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
                # The diagnostics already hold the step that failed; this is the
                # log, which is throttled because a reconnect loop runs for ever.
                if attempt == 1 or attempt % 10 == 0:
                    logger.warning("connection failed", error=str(exc), retry_in_s=delay)

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
        """One session: connect, subscribe, hold until it drops or a stop arrives.

        Every stage records what happened, so a failure leaves the step it failed
        on as the last line of the diagnostics — which is the difference between
        "disconnected" and "the server refused the password".
        """
        from asyncua import Client  # a heavy import - only when actually needed

        credentials = self._config.credentials or Credentials()
        self._diagnostics.start_attempt(credentials.describe())

        client = Client(url=self._config.endpoint)
        client.session_timeout = self._config.session_timeout_ms
        await self._apply_credentials(client, credentials)

        # `connect()` explicitly rather than `async with client`: the two do the
        # same thing, but this way the failure is caught where it happens and
        # recorded as the session step. Inside a context manager it escapes
        # unlabelled, and "Disconnected" with an empty log is what this whole
        # module exists to stop.
        try:
            await client.connect()
        except Exception as exc:
            self._diagnostics.failed(DiagnosticStep.SESSION, exc)
            raise
        self._diagnostics.ok(DiagnosticStep.SESSION, self._config.endpoint)
        await self._load_structures(client)
        await self._read_write_types(client)

        try:
            await self._hold_subscription(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diagnostics.failed(DiagnosticStep.SUBSCRIBE, exc)
            raise
        finally:
            await client.disconnect()

    async def _load_structures(self, client: Any) -> None:
        """Teach the client the server's own struct types, when one is read.

        Without this a struct arrives as an `ExtensionObject` of undecoded bytes
        and no path can be resolved out of it; with it, asyncua generates a
        dataclass per type and a notification carries the decoded object, nested
        structs included. Verified against a live server — a subscription
        delivers it decoded, not only a read.

        Called only when some binding actually has a path. It is a round trip and
        a code generation, and every setup that reads plain scalars — which is
        every setup that existed before this — should not start paying for it.

        A failure is logged and carried past. A server whose type dictionary
        cannot be read is still a server whose scalars can be subscribed to, and
        the paths that then fail to resolve fail one signal at a time.
        """
        if not any(binding.path for binding in self._config.bindings):
            return
        try:
            await client.load_data_type_definitions()
        except Exception as exc:
            logger.warning(
                "could not load the server's structure definitions",
                endpoint=self._config.endpoint,
                error=str(exc),
            )

    async def _read_write_types(self, client: Any) -> None:
        """Ask each output node what type it holds, once per session.

        The server is the authority on this and nothing else is: a node id typed
        by hand carries no type, and one picked from the browser may have been
        changed on the PLC since. One read per output at connect time, and every
        setup here has a handful of them.

        A node that will not say is left out and written as `Double`, which is
        what the path did unconditionally before this — an unreadable type must
        not turn a working configuration into a failing one.
        """
        if not self._config.allow_writing or not self._outputs:
            return
        for signal, binding in self._outputs.items():
            try:
                name = (
                    await client.get_node(binding.node_id).read_data_type_as_variant_type()
                ).name
            except Exception as exc:
                logger.warning(
                    "could not read an output node's type, writing it as a float",
                    signal=signal,
                    node=binding.node_id,
                    error=str(exc),
                )
                continue
            if not is_writable_type(name):
                # A String or a DateTime node: a number written there means
                # nothing, and refusing beats guessing.
                logger.warning(
                    "an output node holds something a number cannot drive",
                    signal=signal,
                    node=binding.node_id,
                    type=name,
                )
                continue
            self._write_types[signal] = name

    async def _apply_credentials(self, client: Any, credentials: Credentials) -> None:
        """Security and authentication onto the client, recorded either way.

        A policy that needs no certificate is recorded as **skipped** rather than
        left out: "there was nothing to do" and "it was not attempted" read the
        same in a log that only mentions what happened.
        """
        try:
            applied = await configure(client, credentials)
        except Exception as exc:
            self._diagnostics.failed(DiagnosticStep.CERTIFICATE, exc)
            raise
        if credentials.is_secure:
            self._diagnostics.ok(DiagnosticStep.CERTIFICATE, applied)
        else:
            self._diagnostics.skipped(DiagnosticStep.CERTIFICATE, "no security, none needed")

    async def _hold_subscription(self, client: Any) -> None:
        """Subscribe to every read binding and stay until told to stop."""
        subscription = await client.create_subscription(
            period=self._config.publishing_interval_ms,
            handler=_SubscriptionHandler(self),
        )
        nodes = [client.get_node(node_id) for node_id in self._node_to_bindings]
        await subscription.subscribe_data_change(nodes, queuesize=_QUEUE_SIZE)

        # The server may revise the interval - TwinCAT, for instance, ties it
        # to the task cycle.
        revised = getattr(subscription.parameters, "RequestedPublishingInterval", None)
        self._revised_interval_ms = int(revised) if revised else None

        self._status = SourceStatus.CONNECTED
        self._diagnostics.ok(
            DiagnosticStep.SUBSCRIBE,
            f"{len(nodes)} signals, {self._revised_interval_ms or '?'} ms revised",
        )
        logger.info(
            "subscription active",
            signals=len(nodes),
            revised_interval_ms=self._revised_interval_ms,
        )

        # Created only when writing is allowed: no pump, nothing that can
        # write, whatever ends up in the outbox.
        pump = (
            asyncio.create_task(self._pump_writes(client))
            if self._config.allow_writing and self._outputs
            else None
        )
        try:
            await self._wait_for_stop()
        finally:
            if pump is not None:
                pump.cancel()
            await subscription.delete()

    async def _pump_writes(self, client: Any) -> None:
        """Drain the store's outbox onto the server, once per publishing interval.

        Coalesced by the outbox itself, which is a dict keyed by signal: a value
        offered on every frame is written once, and only the newest one was ever
        going to matter.

        **Never raises.** A refused write — a node that turned read-only, a
        server that dropped — is logged and the pump carries on, exactly as the
        subscription handler does. An exception here would kill the session.
        """
        from asyncua import ua

        interval_s = self._config.publishing_interval_ms / 1000.0
        while True:
            await asyncio.sleep(interval_s)
            for signal, value in self._store.take_writes().items():
                binding = self._outputs.get(signal)
                if binding is None:
                    continue
                try:
                    # An explicit Variant rather than a bare float: verified
                    # against `pssim mock-server` that both work on a Double
                    # node, but a bare float only because the node already held
                    # one — this states the type instead of inheriting it.
                    #
                    # And **the node's own type**, not Double for everything: a
                    # 0/1 sensor bound to a Boolean, which is what a PLC
                    # programmer would use for one, was refused by the server.
                    type_name = self._write_types.get(signal, FALLBACK_TYPE)
                    await client.get_node(binding.node_id).write_value(
                        ua.Variant(
                            coerce_for(binding.to_plc(value), type_name),
                            getattr(ua.VariantType, type_name, ua.VariantType.Double),
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._diagnostics.failed(DiagnosticStep.WRITE, exc)
                    logger.warning(
                        "write refused, carrying on",
                        signal=signal,
                        node=binding.node_id,
                        error=str(exc),
                    )

    # -- callback zo subscription ------------------------------------------

    def handle_data_change(self, node_id: str, value: Any, source_time: datetime | None) -> None:
        """Handle one notification. **Never raises** — an exception here kills the loop.

        Public deliberately: it is the contract between the source and its
        subscription handler.

        One notification may feed several signals: a struct arrives whole, and
        each binding takes its own field out of it.
        """
        bindings = self._node_to_bindings.get(node_id)
        if not bindings:
            return

        if source_time is None:
            # Some versions of Codesys do not send SourceTimestamp at all.
            internal_time = time.monotonic()
        else:
            internal_time = self._timebase.to_internal(source_time, time.monotonic())

        for binding in bindings:
            self._store_one(binding, node_id, value, internal_time)

    def _store_one(
        self, binding: SignalBinding, node_id: str, value: Any, internal_time: float
    ) -> None:
        """Take one binding's number out of a notification and store it.

        A path that does not fit the value costs **this signal** and nothing
        else: the other fields of the same struct still arrive, and the
        subscription stays up. A field the server renamed is a configuration
        problem, not a reason to stop reading the machine.
        """
        try:
            found = resolve_value(value, parse_path(binding.path))
        except DataSourceError as exc:
            # Warned once per notification would be a flood at 20 Hz; the store
            # marking the signal stale is what the scene shows (R11).
            logger.debug(
                "a signal's path does not fit the value",
                signal=binding.signal,
                node=node_id,
                path=binding.path,
                error=str(exc),
            )
            return

        if not is_numeric_value(found):
            logger.warning(
                "signal is not numeric, ignoring",
                signal=binding.signal,
                node=node_id,
                path=binding.path,
                type=type(found).__name__,
            )
            return

        self._store.put(
            signal=binding.signal,
            value=binding.to_internal(float(found)),
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
