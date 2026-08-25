"""A session held open so the address space can be walked a folder at a time.

`opcua_browser.browse_variables` reads everything and disconnects. That is right
for `pssim probe` and for a small server, and useless for a PLC: a real address
space runs to thousands of nodes, and reading all of them to show the contents of
one folder is not something a window can do.

This is the other shape — UaExpert's. Connect once, keep the session, answer
"what is under this node" as each one is opened, disconnect when the dialog
closes. Nothing is read until something asks for it.

**Its own thread with its own asyncio loop**, for the reason R10 gives for the
data source: the Qt event loop is not an asyncio one and asyncua cannot live in
it. The bridge is `asyncio.run_coroutine_threadsafe`, which keeps every asyncua
call on the one loop that owns the connection, and hands the caller a future to
wait on. Callers wait from a worker thread, never from the thread that draws —
one expansion is usually quick, but "quick on my LAN" is not something to build
a window on.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from pssim.domain.errors import DataSourceError
from pssim.io.opcua_browser import NUMERIC_TYPES
from pssim.io.opcua_diagnostics import DiagnosticLog, DiagnosticStep
from pssim.io.opcua_security import Credentials, configure
from pssim.observability import get_logger

logger = get_logger(__name__)

#: The Objects folder — where a server's own address space starts. Everything
#: above it is the standard type system, which is not what anybody is looking for.
OBJECTS_NODE_ID: Final = "i=85"

#: How long one request may take before the caller is told the server is not
#: answering. A folder is usually milliseconds; this is the point at which
#: waiting longer tells nobody anything new.
DEFAULT_REQUEST_TIMEOUT_S: Final = 20.0

#: How long opening the session may take, connection included.
DEFAULT_OPEN_TIMEOUT_S: Final = 20.0

#: A guard against a folder that a server reports as having a million children.
#: Hit means truncated, and `BrowseResult.is_truncated` says so rather than
#: quietly showing part of it.
DEFAULT_CHILD_LIMIT: Final = 5000


class NodeKind(StrEnum):
    """What a node is, as far as anything here cares."""

    OBJECT = "object"
    """A folder or a device. Something to open."""

    VARIABLE = "variable"
    """A value. Something to bind to."""

    OTHER = "other"
    """A method, a type, a view. Shown so the tree matches the server, but not
    something this application does anything with."""


@dataclass(frozen=True, slots=True)
class BrowseNode:
    """One node of the address space, as a tree needs to show it."""

    node_id: str
    browse_name: str
    display_name: str
    kind: NodeKind
    data_type: str = ""
    """Only a variable has one. Shown because a tag that is not a number cannot
    drive a joint."""

    is_writable: bool = False
    has_children: bool = False
    """Whether it is worth offering an expander. Answered by the server while its
    children are being listed, so opening a leaf costs nothing."""

    @property
    def is_variable(self) -> bool:
        return self.kind is NodeKind.VARIABLE

    @property
    def is_numeric(self) -> bool:
        """Whether it can carry a position, an angle or a reading.

        The set is `opcua_browser.NUMERIC_TYPES` rather than a second copy of it:
        the one-shot browse and the live session must agree about which nodes are
        bindable, and two frozensets would eventually not.
        """
        return self.is_variable and self.data_type in NUMERIC_TYPES

    @property
    def label(self) -> str:
        """What the row reads. The display name, falling back to the browse name —
        some servers leave one of the two empty."""
        return self.display_name or self.browse_name or self.node_id


@dataclass(frozen=True, slots=True)
class BrowseResult:
    """The children of one node, and whether that is all of them."""

    nodes: tuple[BrowseNode, ...] = ()
    is_truncated: bool = False

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Any:
        return iter(self.nodes)


class OpcUaBrowseSession:
    """A connection kept open for browsing, on a thread of its own.

    Not a `DataSource` (R12): it subscribes to nothing and writes into no store.
    It answers questions, which is a different job from carrying a stream, and
    folding the two together would put a request/response call inside the
    reconnect loop that must never get stuck.
    """

    __slots__ = (
        "_endpoint",
        "_credentials",
        "_child_limit",
        "_thread",
        "_loop",
        "_client",
        "_ready",
        "_diagnostics",
        "_open_error",
    )

    def __init__(
        self,
        endpoint: str,
        credentials: Credentials | None = None,
        child_limit: int = DEFAULT_CHILD_LIMIT,
    ) -> None:
        if not endpoint:
            raise DataSourceError("endpoint must not be empty")
        self._endpoint = endpoint
        self._credentials = credentials or Credentials()
        self._child_limit = child_limit
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._ready = threading.Event()
        self._diagnostics = DiagnosticLog()
        self._open_error: BaseException | None = None

    # -- reading ------------------------------------------------------------

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def diagnostics(self) -> DiagnosticLog:
        """What opening the session tried, and where it stopped."""
        return self._diagnostics

    @property
    def is_open(self) -> bool:
        return self._client is not None

    # -- lifetime -----------------------------------------------------------

    def open(self, timeout_s: float = DEFAULT_OPEN_TIMEOUT_S) -> DiagnosticLog:
        """Connect and hold. Blocks until the session is up or the attempt fails.

        Raises `DataSourceError` when it could not connect, with the diagnostics
        already recording which step it stopped on.
        """
        if self._thread is not None:
            return self._diagnostics

        self._diagnostics.start_attempt(self._credentials.describe())
        self._ready.clear()
        self._open_error = None
        self._thread = threading.Thread(target=self._run, name="pssim-opcua-browse", daemon=True)
        self._thread.start()

        if not self._ready.wait(timeout=timeout_s):
            self.close()
            raise DataSourceError(f"{self._endpoint} did not answer within {timeout_s:g} s")
        if self._open_error is not None:
            error = self._open_error
            self.close()
            raise DataSourceError(f"could not open a session on {self._endpoint}: {error}")
        return self._diagnostics

    def close(self) -> None:
        """Let the session go. Idempotent, and safe on a dialog closing."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning("browse thread did not stop within 5 s, carrying on")
        self._thread = None
        self._loop = None
        self._client = None
        self._ready.clear()

    def __enter__(self) -> OpcUaBrowseSession:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- questions ----------------------------------------------------------

    def children_of(
        self, node_id: str = OBJECTS_NODE_ID, timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    ) -> BrowseResult:
        """What is under one node. Blocks until the server answers.

        Called from a worker thread, never from the one that draws. Reads only
        this node's children — that is the whole point of holding the session
        rather than walking the tree once.
        """
        # Checked **before** the coroutine is built. Building it first and then
        # refusing leaves a coroutine nobody awaits, which Python reports as a
        # RuntimeWarning on a closed session.
        loop = self._require_open()
        return self._ask(loop, self._read_children(node_id), timeout_s)

    def _require_open(self) -> asyncio.AbstractEventLoop:
        """The session's loop, or a typed error saying there is not one."""
        loop = self._loop
        if loop is None or self._client is None:
            raise DataSourceError("the browse session is not open")
        return loop

    def _ask(self, loop: asyncio.AbstractEventLoop, work: Any, timeout_s: float) -> BrowseResult:
        """Run one coroutine on the session's own loop and wait for the answer."""
        future: Future[BrowseResult] = asyncio.run_coroutine_threadsafe(work, loop)
        try:
            return future.result(timeout=timeout_s)
        except TimeoutError as exc:
            future.cancel()
            raise DataSourceError(f"the server did not answer within {timeout_s:g} s") from exc
        except Exception as exc:
            raise DataSourceError(f"could not browse: {exc}") from exc

    # -- the session's own thread -------------------------------------------

    def _run(self) -> None:
        """Own loop, own client, from open to close."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect())
            if self._open_error is None:
                # Nothing scheduled: the loop now exists only to run the
                # coroutines `children_of` hands it, until `close` stops it.
                loop.run_forever()
        except Exception as exc:  # pragma: no cover - the loop itself failing
            self._open_error = exc
            logger.exception("browse session thread died")
        finally:
            self._ready.set()
            self._shut_down(loop)

    async def _connect(self) -> None:
        """Open the session, recording each step. Never raises out of the thread."""
        from asyncua import Client

        client = Client(url=self._endpoint)
        try:
            applied = await configure(client, self._credentials)
        except Exception as exc:
            self._diagnostics.failed(DiagnosticStep.CERTIFICATE, exc)
            self._open_error = exc
            return
        if self._credentials.is_secure:
            self._diagnostics.ok(DiagnosticStep.CERTIFICATE, applied)
        else:
            self._diagnostics.skipped(DiagnosticStep.CERTIFICATE, "no security, none needed")

        try:
            await client.connect()
        except Exception as exc:
            self._diagnostics.failed(DiagnosticStep.SESSION, exc)
            self._open_error = exc
            return

        self._client = client
        self._diagnostics.ok(DiagnosticStep.SESSION, self._endpoint)
        # Set *after* the client is in place, so a caller released by this can
        # rely on `children_of` having something to ask.
        self._ready.set()

    def _shut_down(self, loop: asyncio.AbstractEventLoop) -> None:
        """Disconnect and close the loop, whatever happened."""
        client = self._client
        self._client = None
        try:
            if client is not None:
                loop.run_until_complete(client.disconnect())
        except Exception as exc:
            # A session being torn down cannot fail in a way worth raising: the
            # server will time it out regardless.
            logger.debug("browse session did not disconnect cleanly", error=str(exc))
        finally:
            loop.close()

    async def _read_children(self, node_id: str) -> BrowseResult:
        """One level of the tree, described. Only reached on an open session."""
        client = self._client
        if client is None:  # pragma: no cover - closed between the check and here
            raise DataSourceError("the browse session is not open")

        children = await client.get_node(node_id).get_children()
        limited = children[: self._child_limit]
        described: list[BrowseNode] = []
        for child in limited:
            node = await _describe(child)
            if node is not None:
                described.append(node)
        return BrowseResult(nodes=tuple(described), is_truncated=len(children) > len(limited))


async def _describe(node: Any) -> BrowseNode | None:
    """One node, or `None` when the server will not say what it is.

    A single unreadable node must not lose the rest of the folder — a server with
    one node it refuses to describe is common, and hiding the other forty because
    of it would be worse than skipping the one.
    """
    from asyncua import ua

    try:
        node_class = await node.read_node_class()
        browse_name = (await node.read_browse_name()).Name or ""
    except Exception as exc:
        logger.debug("skipping an unreadable node", error=str(exc))
        return None

    kind = _kind_of(node_class, ua)
    display, data_type, is_writable = await _variable_details(node, kind, ua)
    return BrowseNode(
        node_id=node.nodeid.to_string(),
        browse_name=browse_name,
        display_name=display or browse_name,
        kind=kind,
        data_type=data_type,
        is_writable=is_writable,
        # An object may hold anything; a variable may still have properties
        # under it, but offering an expander on every one of them makes a tree
        # of leaves look like a tree of folders.
        has_children=kind is NodeKind.OBJECT,
    )


def _kind_of(node_class: Any, ua: Any) -> NodeKind:
    if node_class == ua.NodeClass.Variable:
        return NodeKind.VARIABLE
    if node_class == ua.NodeClass.Object:
        return NodeKind.OBJECT
    return NodeKind.OTHER


async def _variable_details(node: Any, kind: NodeKind, ua: Any) -> tuple[str, str, bool]:
    """The display name, and for a variable its type and whether it is writable.

    Three reads, and only for a variable: doing them for every object in a
    thousand-node folder is what makes a browse feel slow.
    """
    try:
        display = (await node.read_display_name()).Text or ""
    except Exception:
        display = ""

    if kind is not NodeKind.VARIABLE:
        return display, "", False

    try:
        data_type = (await node.read_data_type_as_variant_type()).name
        access = await node.get_user_access_level()
    except Exception as exc:
        logger.debug("a variable would not describe itself", error=str(exc))
        return display, "", False
    return display, data_type, ua.AccessLevel.CurrentWrite in access
