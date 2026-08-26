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
from pssim.io.opcua_path import (
    MAX_ELEMENTS,
    ValuePath,
    child_path,
    parse_path,
    resolve_value,
)
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

#: What OPC UA's `ValueRank` calls a scalar. Every other value is an array,
#: `0` (one or more dimensions) included.
_SCALAR_RANK: Final = -1

#: The variant type a struct's value arrives as. A row reporting this is a
#: struct, whatever its own type is called.
_EXTENSION_OBJECT: Final = "ExtensionObject"


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
    is_readable: bool = True
    """What the server's `UserAccessLevel` says this session may do with it.

    Both, because a variable may be bound in either direction and the node
    decides which are possible: a servo's actual position is read-only, a
    command word may be write-only. Readable defaults to `True` because that is
    the ordinary case and because a node whose access could not be read at all
    should not look forbidden.
    """

    has_children: bool = False
    """Whether it is worth offering an expander. Answered by the server while its
    children are being listed, so opening a leaf costs nothing."""

    path: str = ""
    """Where inside the node's value this row is: `Position.X`, `Limits[1]`.

    Empty for every node a server actually has. A **field is not a node** — the
    server has one node for the struct and nothing for the field inside it, so a
    field row carries its parent's `node_id` plus this. See `io/opcua_path.py`.
    """

    is_container: bool = False
    """A struct or an array: something to open, and not something to bind.

    Both halves matter. A struct's value is an `ExtensionObject` and an array's
    is a list, and handing either to a joint that wants a number is the mistake
    this flag exists to make impossible — while still letting the row be opened,
    which is the whole request.
    """

    @property
    def is_variable(self) -> bool:
        return self.kind is NodeKind.VARIABLE

    @property
    def is_field(self) -> bool:
        """Whether this is a place inside a value rather than a node of its own."""
        return bool(self.path)

    @property
    def is_numeric(self) -> bool:
        """Whether it can carry a position, an angle or a reading.

        The set is `opcua_browser.NUMERIC_TYPES` rather than a second copy of it:
        the one-shot browse and the live session must agree about which nodes are
        bindable, and two frozensets would eventually not.

        A container is never numeric even when its element type is: `Double[4]`
        reports `Double`, and binding the array itself would hand a joint a list.
        """
        return self.is_variable and not self.is_container and self.data_type in NUMERIC_TYPES

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
        "_structures_loaded",
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
        self._structures_loaded = False

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
        self._structures_loaded = False
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
        self,
        node_id: str = OBJECTS_NODE_ID,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        path: str = "",
    ) -> BrowseResult:
        """What is under one node, or under one place inside its value.

        Called from a worker thread, never from the one that draws. Reads only
        this node's children — that is the whole point of holding the session
        rather than walking the tree once.

        With a `path`, the answer comes from the node's **type** rather than from
        the address space: a struct's fields are read off its `DataType`, and an
        array's elements off its value. See `io/opcua_path.py` for why a field
        cannot be a node.
        """
        # Checked **before** the coroutine is built. Building it first and then
        # refusing leaves a coroutine nobody awaits, which Python reports as a
        # RuntimeWarning on a closed session.
        loop = self._require_open()
        return self._ask(loop, self._read_children(node_id, path), timeout_s)

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

    async def _read_children(self, node_id: str, path: str) -> BrowseResult:
        """One level, from whichever of the three places has it.

        The address space first, because that is the only one with real nodes in
        it: a PLC that exposes a struct's members as `HasComponent` children —
        which many do — needs nothing else, and it costs one request either way.
        Only when there are none does the type get asked.
        """
        client = self._client
        if client is None:  # pragma: no cover - closed between the check and here
            raise DataSourceError("the browse session is not open")

        node = client.get_node(node_id)
        if not path:
            described = await self._describe_all(await node.get_children())
            if described is not None:
                return described

        return await self._read_inside(node, path)

    async def _read_inside(self, node: Any, path: str) -> BrowseResult:
        """What is inside a value: a struct's fields, or an array's elements.

        Which of the two is decided by the **type**, walked from the node's own
        `DataType` along `path`. Only an array needs the value read, and only to
        learn how many elements there are.
        """
        steps = parse_path(path)
        try:
            described = await _walk_type(node, steps)
        except Exception as exc:
            logger.debug("a type would not describe itself", path=path, error=str(exc))
            return BrowseResult()

        if described is None:
            return BrowseResult()
        if described.is_array:
            await self._ensure_structures()
            return await _array_elements(node, path, steps, described, self._child_limit)
        return await _struct_fields_of(node, path, described)

    async def _ensure_structures(self) -> None:
        """Teach the client the server's struct types, once, before reading a value.

        Needed for an array **inside** a struct: the value arrives as one
        `ExtensionObject`, and without the generated classes there is nothing to
        walk the path through — so the element count cannot be found and the
        folder comes back empty. A bare array needs none of this, which is why
        the gap survived a passing test: the mock server had registered the same
        classes on the process-wide `ua` module from inside the test process.

        Once, and lazily: it is a round trip and a code generation, and a server
        of plain scalars should not pay for it just to open a folder. A failure is
        logged and carried past — the fields of a struct come from metadata and
        still work without it.
        """
        if self._structures_loaded:
            return
        self._structures_loaded = True
        client = self._client
        if client is None:  # pragma: no cover - closed underneath us
            return
        try:
            await client.load_data_type_definitions()
        except Exception as exc:
            logger.warning(
                "could not load the server's structure definitions",
                endpoint=self._endpoint,
                error=str(exc),
            )

    async def _describe_all(self, children: list[Any]) -> BrowseResult | None:
        """Describe real child nodes, or `None` when there are none to describe."""
        if not children:
            return None
        limited = children[: self._child_limit]
        described: list[BrowseNode] = []
        for child in limited:
            node = await _describe(child)
            if node is not None:
                described.append(node)
        return BrowseResult(nodes=tuple(described), is_truncated=len(children) > len(limited))


@dataclass(frozen=True, slots=True)
class _TypeAt:
    """What the type system says is at one place in a value."""

    name: str
    """The variant type's name, or the struct's own name."""

    is_struct: bool
    is_array: bool = False
    definition: Any = None
    """The `StructureDefinition`, when this is a struct."""

    @property
    def element(self) -> _TypeAt:
        """The same type without the array-ness — what one element is."""
        return _TypeAt(self.name, self.is_struct, False, self.definition)


async def _struct_fields_of(node: Any, path: str, described: _TypeAt) -> BrowseResult:
    """One row per field of a struct, each carrying the parent's node id.

    A field's own type name needs a read when the type is a custom one — the
    offline resolver cannot name it, and a Type column reading `ns=2;i=3` is no
    help in picking the right field. Those reads are gathered, so a struct of
    twenty struct fields costs one round trip's latency rather than twenty.
    """
    definition = described.definition
    if definition is None:
        return BrowseResult()

    fields = list(definition.Fields)
    names = await _custom_names(node.session, [field.DataType for field in fields])
    node_id = node.nodeid.to_string()
    return BrowseResult(nodes=tuple(_field_node(node_id, path, field, names) for field in fields))


async def _custom_names(session: Any, data_type_ids: list[Any]) -> dict[str, str]:
    """Browse names for the custom types among these, keyed by node id.

    Namespace 0 is skipped because it needs no read: those types have names the
    offline resolver already knows.
    """
    from asyncua.common.node import Node

    custom = {
        data_type_id.to_string(): data_type_id
        for data_type_id in data_type_ids
        if data_type_id.NamespaceIndex != 0
    }
    if not custom:
        return {}

    async def read(node_id: str, data_type_id: Any) -> tuple[str, str]:
        try:
            name = (await Node(session, data_type_id).read_browse_name()).Name or ""
        except Exception:
            name = ""
        return node_id, name

    answered = await asyncio.gather(
        *(read(node_id, data_type_id) for node_id, data_type_id in custom.items())
    )
    return {node_id: name for node_id, name in answered if name}


def _field_node(node_id: str, path: str, field: Any, names: dict[str, str]) -> BrowseNode:
    inner = _named_type(field.DataType)
    name = names.get(field.DataType.to_string(), inner.name)
    is_array = field.ValueRank is not None and field.ValueRank != _SCALAR_RANK
    return BrowseNode(
        node_id=node_id,
        browse_name=field.Name,
        display_name=field.Name,
        kind=NodeKind.VARIABLE,
        data_type=f"{name}[]" if is_array else name,
        # A field is never writable on its own: a write goes back as the whole
        # struct or not at all, and this project writes one node at a time (R19).
        is_writable=False,
        has_children=is_array or inner.is_struct,
        path=child_path(path, field.Name),
        is_container=is_array or inner.is_struct,
    )


async def _array_elements(
    node: Any, path: str, steps: ValuePath, described: _TypeAt, limit: int
) -> BrowseResult:
    """One row per element, as many as the value turned out to have.

    The count comes from the value because that is the only place it reliably is:
    `ArrayDimensions` is an optional attribute, and `ValueRank` says "one or more
    dimensions" without saying how many of anything.
    """
    try:
        value = resolve_value(await node.read_value(), steps)
    except Exception as exc:
        # A server that will not hand over the value cannot be asked how long it
        # is. An empty answer, and the tree says there is nothing to show.
        logger.debug("an array would not say how long it is", path=path, error=str(exc))
        return BrowseResult()

    if not isinstance(value, (list, tuple)):
        return BrowseResult()

    count = min(len(value), limit, MAX_ELEMENTS)
    inner = described.element
    # `described` came off the type walk, which already resolved a custom name.
    element_type = inner.name
    elements = tuple(
        BrowseNode(
            node_id=node.nodeid.to_string(),
            browse_name=f"[{index}]",
            display_name=f"[{index}]",
            kind=NodeKind.VARIABLE,
            data_type=element_type,
            has_children=inner.is_struct,
            path=child_path(path, index),
            is_container=inner.is_struct,
        )
        for index in range(count)
    )
    return BrowseResult(nodes=elements, is_truncated=count < len(value))


async def _walk_type(node: Any, steps: ValuePath) -> _TypeAt | None:
    """Follow a path through the type system, never through a value.

    Metadata only, so a struct can be opened before anything has ever been
    written into it — which is most of a freshly started PLC.
    """
    described = await _type_at(node.session, await node.read_data_type())
    described = _TypeAt(
        described.name,
        described.is_struct,
        is_array=await _is_array(node),
        definition=described.definition,
    )

    for step in steps:
        if isinstance(step, int):
            described = described.element
            continue
        field = _field_named(described.definition, step)
        if field is None:
            return None
        inner = await _type_at(node.session, field.DataType)
        described = _TypeAt(
            inner.name,
            inner.is_struct,
            is_array=field.ValueRank is not None and field.ValueRank != _SCALAR_RANK,
            definition=inner.definition,
        )
    return described


def _field_named(definition: Any, name: str) -> Any:
    if definition is None:
        return None
    return next((field for field in definition.Fields if field.Name == name), None)


async def _is_array(node: Any) -> bool:
    """Whether the node holds more than one of something.

    `ValueRank` and nothing else: `-1` is Scalar, and every other value —
    including `0`, which reads as "one or more dimensions" — means an array.
    Verified against a live server, where a four-element `Double` array reported
    rank `0` and left `ArrayDimensions` unset entirely.
    """
    from asyncua import ua

    try:
        rank = (await node.read_attribute(ua.AttributeIds.ValueRank)).Value.Value
    except Exception:
        return False
    return rank is not None and rank != _SCALAR_RANK


def _named_type(data_type_id: Any) -> _TypeAt:
    """A `DataType` node id described without asking the server.

    `ua.datatype_to_varianttype` is used for namespace 0 **only**, and that is
    not a preference: given a custom type it reads the numeric identifier and
    ignores the namespace, so `ns=2;i=1` came back as `VariantType.Boolean`. A
    custom type is therefore reported by its node id and treated as a struct
    until it is opened, at which point its own definition settles it.
    """
    from asyncua import ua

    if data_type_id.NamespaceIndex == 0:
        try:
            return _TypeAt(ua.datatype_to_varianttype(data_type_id).name, is_struct=False)
        except Exception:
            return _TypeAt(data_type_id.to_string(), is_struct=False)
    return _TypeAt(data_type_id.to_string(), is_struct=True)


async def _type_at(session: Any, data_type_id: Any) -> _TypeAt:
    """The same, but allowed to ask — for the node the walk is standing on.

    A custom type gets its browse name, which is what a reader recognises, and
    its definition, which is what the next step of the walk needs.

    The session comes from `node.session` — asyncua 2.x renamed that from
    `node.server`, and the old name fails at run time with an `AttributeError`
    that a `try` around a browse turns into an empty folder.
    """
    described = _named_type(data_type_id)
    if not described.is_struct:
        return described

    from asyncua.common.node import Node

    node = Node(session, data_type_id)
    try:
        name = (await node.read_browse_name()).Name or described.name
    except Exception:
        name = described.name

    definition = await _definition_of(node)
    return _TypeAt(name, is_struct=definition is not None, definition=definition)


async def _definition_of(node: Any) -> Any:
    """A node's `StructureDefinition`, or `None` when it has none.

    Both outcomes are normal and neither is an error: a `DataType` node for a
    plain number answers `None`, and one for an enumeration answers an
    `EnumDefinition`, which has no `Fields` and is not something to open.
    """
    try:
        definition = await node.read_data_type_definition()
    except Exception:
        return None
    return definition if hasattr(definition, "Fields") else None


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
    details = await _variable_details(node, kind, ua)
    return BrowseNode(
        node_id=node.nodeid.to_string(),
        browse_name=browse_name,
        display_name=details.display or browse_name,
        kind=kind,
        data_type=details.data_type,
        is_writable=details.is_writable,
        is_readable=details.is_readable,
        # An object may hold anything. A **variable** gets an expander when it is
        # a struct or an array: that is where the fields somebody wants are, and
        # it is also where a PLC that exposes its struct members as real child
        # nodes has them. A plain scalar does not get one even though it may
        # carry properties — an expander on every leaf makes a tree of leaves
        # look like a tree of folders.
        has_children=kind is NodeKind.OBJECT or details.is_container,
        is_container=details.is_container,
    )


def _kind_of(node_class: Any, ua: Any) -> NodeKind:
    if node_class == ua.NodeClass.Variable:
        return NodeKind.VARIABLE
    if node_class == ua.NodeClass.Object:
        return NodeKind.OBJECT
    return NodeKind.OTHER


@dataclass(frozen=True, slots=True)
class _Details:
    """What one row needs beyond its name."""

    display: str = ""
    data_type: str = ""
    is_writable: bool = False
    is_readable: bool = True
    is_container: bool = False


async def _variable_details(node: Any, kind: NodeKind, ua: Any) -> _Details:
    """The display name, and for a variable its type, access and shape.

    **One request**, not four: `read_attributes` takes a list of attribute ids
    and answers them together, which is what makes asking a fourth question
    (`ValueRank`, for whether it is an array) cheaper than the three separate
    reads this used to do.

    `read_data_type_as_variant_type` stays a second call and only for a variable.
    It resolves the type hierarchy properly — a `Double` subtype answers
    `Double` — where the offline `datatype_to_varianttype` reads the numeric
    identifier and ignores the namespace, so a custom type comes back as
    whatever ns-0 type happens to share its number.
    """
    attributes = [
        ua.AttributeIds.DisplayName,
        ua.AttributeIds.ValueRank,
        ua.AttributeIds.UserAccessLevel,
    ]
    try:
        display_value, rank_value, access_value = await node.read_attributes(attributes)
        display = getattr(display_value.Value.Value, "Text", "") or ""
    except Exception as exc:
        logger.debug("a node would not describe itself", error=str(exc))
        return _Details()

    if kind is not NodeKind.VARIABLE:
        return _Details(display=display)

    try:
        variant = (await node.read_data_type_as_variant_type()).name
    except Exception as exc:
        logger.debug("a variable would not name its type", error=str(exc))
        return _Details(display=display)

    rank = rank_value.Value.Value
    is_array = rank is not None and rank != _SCALAR_RANK
    access = ua.AccessLevel.parse_bitfield(access_value.Value.Value)
    return _Details(
        display=display,
        data_type=f"{variant}[]" if is_array else variant,
        is_writable=ua.AccessLevel.CurrentWrite in access,
        is_readable=ua.AccessLevel.CurrentRead in access,
        # A struct arrives as an `ExtensionObject` and an array as a list.
        # Neither is a number, and both have something inside worth picking.
        is_container=is_array or variant == _EXTENSION_OBJECT,
    )
