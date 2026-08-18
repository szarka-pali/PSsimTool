"""The binding between a joint and an OPC UA node.

Deliberately separate from `domain.machine.Joint`: the domain knows nothing about
the PLC. This is the only place where a raw value from the PLC is converted into
internal units.
"""

from __future__ import annotations

from dataclasses import dataclass


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

    def to_internal(self, raw_value: float) -> float:
        """Convert a value from PLC units into metres / radians."""
        return raw_value * self.scale + self.offset


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
    """If `None`, computed as 2× the revised publishing interval. See R5."""

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
