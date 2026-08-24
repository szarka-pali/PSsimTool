"""Browsing an OPC UA server for the variables it offers.

Typing a NodeId by hand means reading it off someone else's screen and hoping;
this is how a tag gets picked from what the server actually has.

Separate from `opcua_source.py` on purpose. A browse is a **one-off question**
with an answer — connect, walk, disconnect — where a source is a long-lived
subscription that reconnects for ever. They share nothing but the endpoint, and
folding a request/response call into the reconnect loop would complicate the one
piece of this project that must never get stuck.

`browse_variables` is synchronous and runs its own asyncio loop, so a caller in
another thread needs to know nothing about asyncio. The UI runs it in a worker
thread the way `ui/loader.StepImportThread` runs a STEP import — never on the
thread that draws (R10).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Final

from pssim.domain.errors import DataSourceError
from pssim.observability import get_logger

logger = get_logger(__name__)

#: How deep to walk. Deeper than any sensible PLC address space and shallow
#: enough that a server with a cycle in it cannot hold the dialog open for ever.
DEFAULT_MAX_DEPTH: Final = 8

#: How long a whole browse may take. A server that answers slowly is a server the
#: user is still waiting on, so this is generous — but it is not unbounded.
DEFAULT_TIMEOUT_S: Final = 20.0

#: Namespace 0 is the OPC UA standard address space: server diagnostics, type
#: definitions, aliases. Skipped entirely — a browse that includes it buries the
#: three nodes somebody wants under a hundred they do not.
_STANDARD_NAMESPACE: Final = 0


@dataclass(frozen=True, slots=True)
class OpcUaNode:
    """One variable on a server, as a chooser needs to show it."""

    node_id: str
    """The NodeId in text form — what a binding is made of."""

    browse_path: str
    """Where it sits, as `Axes / Axes.X.ActPos`. For reading, never for keying:
    two servers can disagree about a path and agree about a NodeId."""

    display_name: str
    data_type: str
    """The variant type's name, e.g. `Double`. Shown because a tag that is not a
    number is not something this application can drive a joint with."""

    is_writable: bool
    """Whether the server admits a write. A sensor's output needs one of these;
    an axis position never is."""

    @property
    def is_numeric(self) -> bool:
        """Whether it can carry a position, an angle or a reading.

        A `String` or a `DateTime` node is perfectly real and perfectly useless
        here — the chooser greys them out rather than hiding them, so it is
        obvious the tag was found and rejected rather than missing.
        """
        return self.data_type in _NUMERIC_TYPES


#: The variant types worth binding to. Verified against `pssim mock-server`:
#: `read_data_type_as_variant_type()` returns a `ua.VariantType` whose `.name` is
#: exactly one of these spellings.
_NUMERIC_TYPES: Final[frozenset[str]] = frozenset(
    {
        "Boolean",
        "SByte",
        "Byte",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
        "Float",
        "Double",
    }
)


def browse_variables(
    endpoint: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> tuple[OpcUaNode, ...]:
    """Every variable the server offers, outside the standard namespace.

    Blocks until the server answers or `timeout_s` passes. Raises
    `DataSourceError` when the server cannot be reached at all — that is a
    question with no answer, unlike a subscription dropping, which is a normal
    state to be retried (R12).
    """
    if not endpoint:
        raise DataSourceError("endpoint must not be empty")
    try:
        return asyncio.run(_browse(endpoint, timeout_s=timeout_s, max_depth=max_depth))
    except DataSourceError:
        raise
    except TimeoutError as exc:
        raise DataSourceError(f"{endpoint} did not answer within {timeout_s:g} s") from exc
    except Exception as exc:  # asyncua raises a wide family of its own
        raise DataSourceError(f"could not browse {endpoint}: {exc}") from exc


async def _browse(endpoint: str, *, timeout_s: float, max_depth: int) -> tuple[OpcUaNode, ...]:
    """Connect, walk, disconnect — the whole of it under one deadline.

    The timeout has to cover the **connection**, not only the walk. A host that
    accepts a TCP connection and then says nothing is exactly the case a browse
    has to survive, and asyncua's own connect timeout is not this one.
    """
    return await asyncio.wait_for(_connect_and_walk(endpoint, max_depth), timeout=timeout_s)


async def _connect_and_walk(endpoint: str, max_depth: int) -> tuple[OpcUaNode, ...]:
    from asyncua import Client

    found: list[OpcUaNode] = []
    client = Client(url=endpoint)
    async with client:
        await _walk(client.nodes.objects, (), found, max_depth)
    # Sorted by where they sit, so a chooser lists a folder's nodes together
    # whatever order the server happened to answer in.
    return tuple(sorted(found, key=lambda node: (node.browse_path, node.node_id)))


async def _walk(node: Any, path: tuple[str, ...], found: list[OpcUaNode], depth: int) -> None:
    """Collect the variables under `node`, recursing into objects and folders."""
    from asyncua import ua

    if depth <= 0:
        return

    for child in await node.get_children():
        if child.nodeid.NamespaceIndex == _STANDARD_NAMESPACE:
            continue
        try:
            node_class = await child.read_node_class()
            name = (await child.read_browse_name()).Name
        except Exception as exc:
            # One unreadable node must not lose the rest of the tree.
            logger.debug("skipping an unreadable node", error=str(exc))
            continue

        if node_class == ua.NodeClass.Variable:
            described = await _describe(child, (*path, name))
            if described is not None:
                found.append(described)
        elif node_class == ua.NodeClass.Object:
            await _walk(child, (*path, name), found, depth - 1)


async def _describe(node: Any, path: tuple[str, ...]) -> OpcUaNode | None:
    """One variable, or `None` when the server will not say what it is."""
    from asyncua import ua

    try:
        variant_type = await node.read_data_type_as_variant_type()
        display = (await node.read_display_name()).Text
        access = await node.get_user_access_level()
    except Exception as exc:
        logger.debug("skipping a variable that would not describe itself", error=str(exc))
        return None

    return OpcUaNode(
        node_id=node.nodeid.to_string(),
        browse_path=" / ".join(path),
        display_name=display or path[-1],
        data_type=variant_type.name,
        is_writable=ua.AccessLevel.CurrentWrite in access,
    )
