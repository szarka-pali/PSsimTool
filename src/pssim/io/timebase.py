"""Converting PLC timestamps onto the internal monotonic scale.

The PLC clock is not synchronised with the local one and may jump (NTP, a manual
setting, the change to summer time). Internally we therefore use a **monotonic** scale
in seconds and determine the offset between it and PLC time **once**, on the first
sample.

If the offset were recomputed continuously, every jump of the PLC clock would feed
through into the interpolation and parts would hop about in the scene.

The module is pure (time arrives as an argument), so it is tested deterministically.
"""

from __future__ import annotations

from datetime import UTC, datetime


class Timebase:
    """Converts PLC time onto the internal monotonic scale with a fixed offset.

    Usage::

        timebase = Timebase()
        internal = timebase.to_internal(source_timestamp, now_monotonic_s)
    """

    __slots__ = ("_offset_s", "_max_drift_s", "_drift_exceeded")

    def __init__(self, max_drift_s: float = 5.0) -> None:
        self._offset_s: float | None = None
        self._max_drift_s = max_drift_s
        self._drift_exceeded = False

    @property
    def is_initialized(self) -> bool:
        return self._offset_s is not None

    @property
    def offset_s(self) -> float | None:
        """The offset `internal = plc_epoch + offset`. `None` until the first sample."""
        return self._offset_s

    @property
    def drift_exceeded(self) -> bool:
        """Whether PLC time has drifted from expectation by more than `max_drift_s`.

        The source should **log this once** and carry on — not reset the offset.
        Typically it means the PLC was re-NTP'd or that its clock runs differently.
        """
        return self._drift_exceeded

    def to_internal(self, plc_time: datetime, now_monotonic_s: float) -> float:
        """Convert a timestamp from the PLC onto the internal scale in seconds.

        `plc_time` must be tz-aware. A naive `datetime` is a bug — in OPC UA the
        `SourceTimestamp` is always UTC, and a naive value means the zone information
        was lost somewhere.
        """
        if plc_time.tzinfo is None:
            raise ValueError("plc_time must be tz-aware; a naive datetime means the zone was lost")

        plc_epoch = plc_time.astimezone(UTC).timestamp()

        if self._offset_s is None:
            self._offset_s = now_monotonic_s - plc_epoch
            return now_monotonic_s

        internal = plc_epoch + self._offset_s
        if abs(internal - now_monotonic_s) > self._max_drift_s:
            self._drift_exceeded = True
        return internal
