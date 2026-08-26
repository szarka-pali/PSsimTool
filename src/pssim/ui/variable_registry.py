"""The project's variables, what each is bound to, and what it is worth now.

A joint and a sensor each carry a `variable` — a name the PLC knows the thing by
(R16). Until now the name went nowhere. This is the list of them, with the OPC UA
tag each is assigned and the last value that arrived.

Pure, like the other registries (R6): the Variables tab renders this and the
connection controller feeds it, but neither owns it. No Qt, no asyncua, so the
whole thing is testable without a window or a server.

The variables themselves are **derived**, not stored: they are whatever the scene
currently mentions. Adding an axis called `X` adds `X` to this list; renaming its
variable renames the entry and leaves the old tag behind, which is the honest
outcome — a tag was assigned to a name, and that name is gone.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from enum import StrEnum

from pssim.config.binding import BindingDirection, VariableBinding
from pssim.ui.settings import VariableTag


class VariableState(StrEnum):
    """What can be said about one variable right now."""

    UNBOUND = "unbound"
    """No tag assigned. Nothing is wrong; it simply has nowhere to read from."""

    OFFLINE = "offline"
    """Bound, but there is no connection to the server."""

    WAITING = "waiting"
    """Connected and subscribed, but nothing has arrived yet."""

    LIVE = "live"
    """A value arrived recently enough to believe."""

    STALE = "stale"
    """A value arrived, but not lately. The scene keeps showing the last one
    (R10) — this is what says so rather than letting it look current."""


@dataclass(frozen=True, slots=True)
class VariableSource:
    """Where a variable name came from, as the window reads it off the scene."""

    name: str
    direction: BindingDirection
    owner: str
    """`axis tilt`, `sensor gate` — shown so a variable can be traced back to the
    thing that named it, which matters when two of them read alike."""


@dataclass(frozen=True, slots=True)
class VariableEntry:
    """One variable, everything the tab shows about it."""

    name: str
    direction: BindingDirection
    owner: str
    tag: VariableTag | None = None
    value: float | None = None
    state: VariableState = VariableState.UNBOUND

    @property
    def is_bound(self) -> bool:
        return self.tag is not None

    def binding(self) -> VariableBinding | None:
        """What `io/` needs to subscribe to or publish this, or `None` if unbound."""
        if self.tag is None:
            return None
        return VariableBinding(
            variable=self.name,
            node_id=self.tag.node_id,
            scale=self.tag.scale,
            offset=self.tag.offset,
            direction=self.direction,
            path=self.tag.path,
        )


class VariableRegistry:
    """The variables of the current scene, in the order the scene mentions them.

    Order is joints first, then sensors, because that is the order the scene is
    built in and a stable order is what keeps a tab from reshuffling itself
    every time something moves.
    """

    __slots__ = ("_entries", "_tags", "_is_connected")

    def __init__(self) -> None:
        self._entries: dict[str, VariableEntry] = {}
        self._tags: dict[str, VariableTag] = {}
        self._is_connected = False

    # -- reading ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[VariableEntry]:
        return iter(self._entries.values())

    def __contains__(self, name: object) -> bool:
        return name in self._entries

    @property
    def entries(self) -> tuple[VariableEntry, ...]:
        return tuple(self._entries.values())

    @property
    def is_empty(self) -> bool:
        return not self._entries

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def get(self, name: str) -> VariableEntry | None:
        return self._entries.get(name)

    def bindings(self) -> tuple[VariableBinding, ...]:
        """Every bound variable, as `io/` wants them. Unbound ones are left out —
        subscribing to nothing is not an error, it is just nothing."""
        return tuple(
            binding for entry in self._entries.values() if (binding := entry.binding()) is not None
        )

    # -- writing ------------------------------------------------------------

    def set_sources(self, sources: Iterable[VariableSource]) -> bool:
        """Replace the variable list with whatever the scene now mentions.

        Returns whether anything changed, so a caller can skip a redraw. Values
        and states survive for names that are still there — a variable does not
        go blank because a sensor elsewhere was renamed.
        """
        rebuilt: dict[str, VariableEntry] = {}
        for source in sources:
            if not source.name or source.name in rebuilt:
                # A name used twice is one variable driven from two places, which
                # is legitimate: an axis and the sensor watching it can share it.
                continue
            previous = self._entries.get(source.name)
            rebuilt[source.name] = VariableEntry(
                name=source.name,
                direction=source.direction,
                owner=source.owner,
                tag=self._tags.get(source.name),
                value=previous.value if previous is not None else None,
                state=self._state_for(source.name, previous),
            )

        if rebuilt == self._entries:
            return False
        self._entries = rebuilt
        return True

    def set_tags(self, tags: dict[str, VariableTag]) -> None:
        """Apply the saved tag assignments. Kept whole rather than per entry, so
        a tag for a variable the scene does not have yet is not thrown away —
        loading a project after the settings is the normal order."""
        self._tags = dict(tags)
        for name, entry in self._entries.items():
            self._entries[name] = replace(
                entry, tag=self._tags.get(name), state=self._state_for(name, entry)
            )

    def set_value(self, name: str, value: float, is_stale: bool = False) -> bool:
        """Record what arrived. Returns whether the row now reads differently."""
        entry = self._entries.get(name)
        if entry is None:
            return False
        state = VariableState.STALE if is_stale else VariableState.LIVE
        updated = replace(entry, value=value, state=state)
        if updated == entry:
            return False
        self._entries[name] = updated
        return True

    def set_connected(self, is_connected: bool) -> None:
        """Connected or not, for every bound variable at once.

        The last value is **kept** when the connection drops — the scene goes on
        showing the last known state (R10), and a row that blanked would say
        something different from what the viewport is drawing.
        """
        self._is_connected = is_connected
        for name, entry in self._entries.items():
            self._entries[name] = replace(entry, state=self._state_for(name, entry))

    def clear(self) -> None:
        """Forget the variables. The tag assignments are settings and survive."""
        self._entries.clear()
        self._is_connected = False

    # -- state --------------------------------------------------------------

    def _state_for(self, name: str, previous: VariableEntry | None) -> VariableState:
        """What a variable's state becomes after its tag or the connection changed.

        A value already received keeps whatever it had — live or stale is decided
        by how old it is, which only `set_value` knows.
        """
        if name not in self._tags:
            return VariableState.UNBOUND
        if not self._is_connected:
            return VariableState.OFFLINE
        if previous is None or previous.value is None:
            return VariableState.WAITING
        return previous.state if previous.state in _VALUE_STATES else VariableState.WAITING


_VALUE_STATES = frozenset({VariableState.LIVE, VariableState.STALE})
