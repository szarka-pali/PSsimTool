"""The binding between something in the scene and an OPC UA node.

Deliberately separate from `domain.machine.Joint`: the domain knows nothing about
the PLC. This is the only place where a raw value from the PLC is converted into
internal units, and the only place the inverse happens on the way back out.

Two bindings, one protocol:

- `JointBinding` comes from `machines/*.yaml` and names a **joint**. It is always
  read: the PLC decides where the machine is.
- `VariableBinding` comes from the application's settings and names a **variable**
  — the string a joint or a sensor carries in a project (R16). It can go either
  way, because a sensor's reading is something the simulation produces.

The two state their conversion differently, and deliberately. `JointBinding` keeps
a single `scale` the machine definition spells out; `VariableBinding` takes
**decimal places** plus the unit of the joint it drives, because that is how a PLC
programmer knows the number — a `REAL` is 1:1 and a `DINT` has an implied decimal
point. Working the whole factor out by hand is what produced
`scale: 1.7453292519943296e-05` in `machines/example.yaml`.

`SignalBinding` is what `io/` consumes, so a data source never has to know which
of the two it was handed. A `Protocol`, not a base class — the same boundary rule
`io/base.py` follows (R12).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pssim.domain.errors import ConfigError


class BindingDirection(StrEnum):
    """Which way a value travels."""

    READ = "read"
    """From the PLC into the scene. Everything a joint does."""

    WRITE = "write"
    """From the scene out to the PLC. Only ever a sensor, and only when writing
    has been deliberately allowed — see `.claude/rules/io-opcua.md`."""


@runtime_checkable
class SignalBinding(Protocol):
    """What `io/` needs of a binding, whatever kind it is.

    `signal` is the key the value is stored under, which is a joint's name for
    one kind of binding and a variable's for the other. Naming it once here is
    what lets `OpcUaSource` take both without a branch.

    `path` is where inside the node's value the number is — `Position.X`,
    `Limits[1]` — and empty for the node's own value, which is what every
    binding written before structures existed means. It stays **text** here on
    purpose: parsing and walking it is `io/opcua_path.py`, and `config/` may not
    import `io/` (the layer test pins that). The boundary resolves it, in the one
    place the scaling already happens (R8).
    """

    @property
    def signal(self) -> str: ...

    @property
    def node_id(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def direction(self) -> BindingDirection: ...

    def to_internal(self, raw_value: float) -> float: ...

    def to_plc(self, internal_value: float) -> float: ...


def to_internal(raw_value: float, scale: float, offset: float) -> float:
    """PLC units into metres / radians. `raw * scale + offset`, in that order.

    The order is fixed: changing it would silently break every existing
    `machines/*.yaml`.
    """
    return raw_value * scale + offset


def from_plc(raw_value: float, decimals: int, offset: float, unit_scale: float) -> float:
    """A PLC number into metres or radians, the way a PLC programmer states it.

    ``(raw / 10**decimals + offset) * unit_scale``, in that order:

    - `decimals` is how many decimal places an integer carries. `0` for a `REAL`
      or `FLOAT`, which then means `354.21` is 354.21 of whatever unit the thing
      moves in. `1` makes `652` read `65.2`.
    - `offset` is in that same unit — millimetres or degrees — because every
      number typed for a tag is, and an offset in metres beside a value in
      millimetres is exactly the confusion this replaced.
    - `unit_scale` is millimetres or degrees into metres or radians, and it comes
      from the joint's kind (`domain.model_joints.value_scale`) rather than being
      typed. That is what makes `354.21` mean 354.21 mm on a rail and 354.21° on
      a rotary head without the tag having to know which it is bound to.

    What this replaces is a single `scale` the user had to work out themselves:
    `machines/example.yaml` still carries `scale: 1.7453292519943296e-05` with a
    comment explaining it is `pi/180/1000`.
    """
    if decimals < 0:
        # A multiplier rather than a divisor is a different feature and one
        # nobody asked for; accepting it silently would scale by ten instead.
        raise ConfigError(f"decimals must not be negative, got {decimals}")
    return (raw_value / (10.0**decimals) + offset) * unit_scale


def to_plc_units(internal_value: float, decimals: int, offset: float, unit_scale: float) -> float:
    """The exact inverse of `from_plc`, so a value that goes out and comes back
    is the value that went out.

    A zero `unit_scale` has no inverse. It cannot arise from a joint - both kinds
    give a positive factor - so it means a binding assembled by hand, and
    refusing is better than dividing by zero on the way out.
    """
    if decimals < 0:
        raise ConfigError(f"decimals must not be negative, got {decimals}")
    if unit_scale == 0.0:
        raise ConfigError("a binding with unit_scale 0 cannot be written back")
    return (internal_value / unit_scale - offset) * (10.0**decimals)


def to_plc(internal_value: float, scale: float, offset: float) -> float:
    """The exact inverse of `to_internal`, so a value that goes out and comes
    back is the value that went out.

    A zero scale has no inverse — it maps everything onto `offset`. That is a
    usable *read* (a signal pinned to a constant) and an impossible write, so it
    is refused here rather than at construction, where it would turn an existing
    machine definition into an error.
    """
    if scale == 0.0:
        raise ConfigError("a binding with scale 0 cannot be written back")
    return (internal_value - offset) / scale


@dataclass(frozen=True, slots=True)
class JointBinding:
    """The `joint ↔ OPC UA node` mapping, including the unit conversion.

    `node_id` is an OPC UA NodeId in text form (`"ns=2;s=Axes.X.ActPos"`).
    The conversion is always `raw * scale + offset` — this order is fixed, and
    changing it would silently break existing `machines/*.yaml`.
    """

    joint_name: str
    node_id: str
    scale: float = 1.0
    offset: float = 0.0
    path: str = ""
    """Where inside the node's value to read, for a node holding a structure or
    an array. Empty means the value itself — which is what every machine
    definition written before this said, so nothing existing has to change."""

    @property
    def signal(self) -> str:
        """The key the store holds this under. A joint's name, here.

        A property rather than a renamed field: `joint_name` is what
        `machines/*.yaml` and `config/loader.py` already use, and renaming it
        would change a versioned format for nothing.
        """
        return self.joint_name

    @property
    def direction(self) -> BindingDirection:
        """Always read. The PLC decides where the machine is; this application
        is an OPC UA *client* that displays it (see CLAUDE.md)."""
        return BindingDirection.READ

    def to_internal(self, raw_value: float) -> float:
        """Convert a value from PLC units into metres / radians."""
        return to_internal(raw_value, self.scale, self.offset)

    def to_plc(self, internal_value: float) -> float:
        """Present only to satisfy `SignalBinding`; a joint is never written."""
        return to_plc(internal_value, self.scale, self.offset)


@dataclass(frozen=True, slots=True)
class VariableBinding:
    """A project **variable** bound to a node, in either direction.

    Where `JointBinding` comes from a versioned machine definition, this one
    comes from the application's settings — the endpoint and the tag mapping are
    deliberately not in the project file, so a `*.pssim` carries no addresses.
    """

    variable: str
    node_id: str
    offset: float = 0.0
    """Added after the decimals, **in the PLC's own unit** - millimetres or
    degrees, not metres or radians. A zero point: the encoder reads 100 mm where
    the machine is at zero."""

    direction: BindingDirection = BindingDirection.READ
    path: str = ""
    """Where inside the node's value to read or write. See `JointBinding.path`."""

    decimals: int = 0
    """How many decimal places an integer value carries. `0` for a `REAL` or a
    `FLOAT`, which is then 1:1."""

    unit_scale: float = 1.0
    """Millimetres or degrees into metres or radians, from the joint's kind.

    `1.0` for a variable with no joint to ask - a sensor's, which travels the
    other way and reports metres or counts (R16). Its value then passes through
    unchanged, which is what it did before any of this.
    """

    @property
    def signal(self) -> str:
        return self.variable

    def to_internal(self, raw_value: float) -> float:
        return from_plc(raw_value, self.decimals, self.offset, self.unit_scale)

    def to_plc(self, internal_value: float) -> float:
        return to_plc_units(internal_value, self.decimals, self.offset, self.unit_scale)


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """Connection and timing settings of a data source.

    `endpoint` is present here only for a local mock. Real endpoints belong in
    the environment (`PSSIM_OPCUA_ENDPOINT`) — `machines/*.yaml` is versioned.
    """

    endpoint: str
    publishing_interval_ms: int = 50
    stale_after_s: float = 1.0
    render_delay_ms: int | None = None
    """If `None`, computed as 2× the revised publishing interval. See R11."""

    def effective_render_delay_s(self, revised_interval_ms: int | None = None) -> float:
        """The sampling delay in seconds.

        When not given explicitly, 2× the interval the server actually granted is
        used (not the one we asked for). Twice, so that it interpolates between
        two known points rather than extrapolating.
        """
        if self.render_delay_ms is not None:
            return self.render_delay_ms / 1000.0
        interval = revised_interval_ms or self.publishing_interval_ms
        return 2.0 * interval / 1000.0
