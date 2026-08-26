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

    unit_scale: float = 1.0
    """Millimetres or degrees into metres or radians, from the kind of joint that
    named it (`domain.model_joints.value_scale`).

    Carried on the **source** because only the scene knows it: the registry has
    no idea whether a name belongs to a rail or a rotary head, and that is what
    decides whether `354.21` from the PLC is a distance or an angle. `1.0` for a
    sensor's variable, which has no joint to ask (R16)."""

    is_direction_fixed: bool = False
    """Whether `direction` is the answer or merely the default.

    A sensor's variable is always a write - its reading is something this
    application produces (R19) - so there is nothing to choose. A joint's may go
    either way, and the tag says which."""


@dataclass(frozen=True, slots=True)
class VariableEntry:
    """One variable, everything the tab shows about it."""

    name: str
    direction: BindingDirection
    owner: str
    unit_scale: float = 1.0
    """What `VariableSource.unit_scale` said, kept so the binding can be built
    without going back to the scene."""

    tag: VariableTag | None = None
    value: float | None = None
    """The number that **arrived**, not the one the joint took. When a value is
    out of range those differ, and the one worth showing is what the PLC sent —
    that is the diagnostic; the clamped one says only that something was wrong."""

    state: VariableState = VariableState.UNBOUND

    is_applied: bool = True
    """Whether an arriving value moves the model.

    On by default: reading a machine and watching it move is the point of the
    application. Off leaves the value arriving and shown while the joint goes
    back to being set by hand, which is how a scene is posed while the PLC is
    connected.
    """

    is_out_of_range: bool = False
    """Whether the last value had to be clamped into the joint's limits.

    Not an error state of its own: the value arrived intact and the joint is at
    its limit. It is a fault in what was sent, and the row says so in red.
    """

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
            offset=self.tag.offset,
            direction=self.direction,
            path=self.tag.path,
            decimals=self.tag.decimals,
            unit_scale=self.unit_scale,
        )


class VariableRegistry:
    """The variables of the current scene, in the order the scene mentions them.

    Order is joints first, then sensors, because that is the order the scene is
    built in and a stable order is what keeps a tab from reshuffling itself
    every time something moves.
    """

    __slots__ = ("_entries", "_sources", "_tags", "_applied", "_is_connected")

    def __init__(self) -> None:
        self._entries: dict[str, VariableEntry] = {}
        # Kept because a direction has to be re-resolved when a tag arrives, and
        # only the source knows whether the choice was the tag's to make.
        self._sources: dict[str, VariableSource] = {}
        self._tags: dict[str, VariableTag] = {}
        # Kept by name outside the entries, like `_tags`: a variable switched off
        # must stay switched off when the scene is re-read, and the entries are
        # rebuilt from scratch every time anything is renamed.
        self._applied: dict[str, bool] = {}
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
            tag = self._tags.get(source.name)
            rebuilt[source.name] = VariableEntry(
                name=source.name,
                direction=self._direction_for(source, tag),
                owner=source.owner,
                unit_scale=source.unit_scale,
                tag=tag,
                value=previous.value if previous is not None else None,
                state=self._state_for(source.name, previous),
                is_applied=self._applied.get(source.name, True),
                is_out_of_range=(previous.is_out_of_range if previous is not None else False),
            )

        self._sources = {source.name: source for source in sources if source.name}
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
            tag = self._tags.get(name)
            self._entries[name] = replace(
                entry,
                tag=tag,
                direction=self._direction_for(self._sources[name], tag)
                if name in self._sources
                else entry.direction,
                state=self._state_for(name, entry),
            )

    def _direction_for(self, source: VariableSource, tag: VariableTag | None) -> BindingDirection:
        """Which way a variable travels: the scene's answer, or the tag's choice.

        The source wins where it says so, which is a sensor. Otherwise the tag
        decides, and with no tag there is nothing bound and the default stands.
        """
        if source.is_direction_fixed or tag is None or tag.direction is None:
            return source.direction
        return tag.direction

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

    def set_applied(self, name: str, is_applied: bool) -> bool:
        """Switch whether arriving values move the model. Returns whether it
        changed.

        Remembered by name even for a variable the scene does not have yet, for
        the reason `set_tags` gives: loading a project after the settings is the
        normal order, and a switch thrown before then must not be lost.
        """
        self._applied[name] = is_applied
        entry = self._entries.get(name)
        if entry is None or entry.is_applied == is_applied:
            return False
        self._entries[name] = replace(entry, is_applied=is_applied)
        return True

    def set_out_of_range(self, name: str, is_out_of_range: bool) -> bool:
        """Record whether the last value had to be clamped. Returns whether the
        row now reads differently.

        Separate from `set_value` because it is answered by the **joint**, not by
        the server: the same number is in range for one axis and not for another,
        and the registry has no idea what any of them can reach.
        """
        entry = self._entries.get(name)
        if entry is None or entry.is_out_of_range == is_out_of_range:
            return False
        self._entries[name] = replace(entry, is_out_of_range=is_out_of_range)
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
