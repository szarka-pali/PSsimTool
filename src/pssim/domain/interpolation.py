"""Interpolation of signal samples.

OPC UA delivers data every 20–100 ms and we render at 60 fps. Without interpolation
the motion is jerky. See docs/architecture.md R5.

The key decision: **time is always an argument**, never read from a clock. That is
what makes the whole of the interpolation deterministically testable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Final

#: How many samples per signal we keep. At a 100 ms interval that is ~3 s of history —
#: enough for interpolation and for a short graph in the HUD, little enough not to
#: take up memory.
DEFAULT_CAPACITY: Final = 32


@dataclass(frozen=True, slots=True)
class Sample:
    """One signal value with the time it came into being in the PLC.

    `source_time_s` is on the internal monotonic scale in seconds — the conversion
    from `SourceTimestamp` happens in `io/`, not here.
    """

    source_time_s: float
    value: float


class SignalBuffer:
    """A ring buffer of one signal's samples, with linear interpolation.

    Not thread-safe. Concurrent access is handled by `io.store.StateStore` with a
    lock.
    """

    __slots__ = ("_samples", "_capacity")

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 2:
            raise ValueError(
                "capacity must be at least 2, otherwise there is nothing to interpolate between"
            )
        self._capacity = capacity
        self._samples: deque[Sample] = deque(maxlen=capacity)

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def is_empty(self) -> bool:
        return not self._samples

    @property
    def latest(self) -> Sample | None:
        return self._samples[-1] if self._samples else None

    def put(self, sample: Sample) -> None:
        """Add a sample.

        Samples are expected in increasing time order. A sample with a time
        **older** than the last one is dropped — on a reconnect, servers sometimes
        send an old value, which would otherwise cause a jump backwards.
        """
        latest = self.latest
        if latest is not None and sample.source_time_s < latest.source_time_s:
            return
        self._samples.append(sample)

    def clear(self) -> None:
        self._samples.clear()

    def sample_at(self, at_time_s: float) -> float | None:
        """The value of the signal at `at_time_s`, linearly interpolated.

        Returns `None` when the buffer is empty. Outside the range of the samples
        it **does not extrapolate** — it returns the boundary value. Extrapolation
        would send a part off to infinity whenever a signal got stuck.
        """
        if not self._samples:
            return None

        if len(self._samples) == 1:
            return self._samples[0].value

        first = self._samples[0]
        if at_time_s <= first.source_time_s:
            return first.value

        last = self._samples[-1]
        if at_time_s >= last.source_time_s:
            return last.value

        older, newer = self._bracket(at_time_s)
        span = newer.source_time_s - older.source_time_s
        if span <= 0.0:
            # Identical timestamps are common on the S7-1500, for instance, where
            # SourceTimestamp has the resolution of the OB cycle. Take the newer value.
            return newer.value

        ratio = (at_time_s - older.source_time_s) / span
        return older.value + (newer.value - older.value) * ratio

    def _bracket(self, at_time_s: float) -> tuple[Sample, Sample]:
        """The two samples `at_time_s` lies between.

        Only called when `at_time_s` is demonstrably inside the range, which is why
        there is no fallback here — if one ever returned, it would be a hidden bug.
        """
        previous = self._samples[0]
        for current in self._samples:
            if current.source_time_s >= at_time_s:
                return previous, current
            previous = current
        raise AssertionError("sample_at has already verified that at_time_s is inside the range")

    def is_stale(self, at_time_s: float, stale_after_s: float) -> bool:
        """Whether the signal has gone longer than `stale_after_s` without a new sample.

        An empty buffer is stale — nothing has ever arrived.
        """
        latest = self.latest
        if latest is None:
            return True
        return (at_time_s - latest.source_time_s) > stale_after_s
