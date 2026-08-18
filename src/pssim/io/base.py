"""The boundary to the outside world.

The project's reference module for defining boundaries: a `Protocol`, not an abstract
base class. The consumer (`viz/`) does not have to import any concrete implementation.

Every data source — OPC UA, replay, mock — implements `DataSource` and nothing more.
A new transport (ADS, S7) can be added without touching `viz/` or `domain/`. See R6.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pssim.io.store import StateStore


class SourceStatus(StrEnum):
    """The state of a data source.

    The transitions are described in `.claude/skills/domenovy-kontext/SKILL.md`. Add
    every new state **there too**, or the HUD and the DEGRADED logic get bypassed.
    """

    DISCONNECTED = "disconnected"
    """No connection. The data in StateStore stays — the scene holds the last state."""

    CONNECTING = "connecting"
    """Prebieha pokus o spojenie alebo reconnect."""

    CONNECTED = "connected"
    """The connection is alive and every signal is fresh."""

    DEGRADED = "degraded"
    """The connection is alive but at least one signal is stale. Rendering continues."""


@runtime_checkable
class DataSource(Protocol):
    """A source of signal values.

    The contract:

    - `start()` is non-blocking. If the source needs a thread or an asyncio loop, it
      creates one itself and `start()` returns immediately.
    - The source writes **exclusively** into the `StateStore` it was given in the
      constructor. It never touches Panda3D or the scene.
    - `stop()` is idempotent and must return within a few seconds.
    - Losing the connection **is not an exception** — the source goes to `CONNECTING`
      and tries again. Raise an exception only from `start()`, when it cannot even begin.
    """

    @property
    def status(self) -> SourceStatus: ...

    @property
    def store(self) -> StateStore: ...

    def start(self) -> None:
        """Start the flow of data. Does not block.

        Raises `DataSourceError` if the configuration is invalid or the source cannot
        be opened at all.
        """
        ...

    def stop(self) -> None:
        """Stop the flow of data. Idempotent."""
        ...
