"""The thread-safe holder of the latest samples of all signals.

This is the **only** place where state is shared between threads. Do not add a second
one — if something else needs sharing, extend the store. See docs/architecture.md R10.

The threading model:

    thread B (asyncio, io/)  →  put()
    thread A (Panda3D, viz/) →  sample_all() / status_of()

What we share is the *latest value + a short ring buffer*, not a queue: a queue would
grow whenever rendering fell behind and old data would be displayed.
"""

from __future__ import annotations

import threading

from pssim.domain.interpolation import DEFAULT_CAPACITY, Sample, SignalBuffer


class StateStore:
    """Signal samples, safely shared between threads.

    The lock is held as briefly as possible: data is copied under the lock, nothing is
    computed under it. Interpolation runs outside the lock, over a copy.
    """

    __slots__ = ("_buffers", "_lock", "_capacity")

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._buffers: dict[str, SignalBuffer] = {}

    # -- writing (the data source's thread) ---------------------------------

    def put(self, signal: str, value: float, source_time_s: float) -> None:
        """Store a sample. Called from the data source's thread.

        `source_time_s` must already be converted onto the internal monotonic scale —
        the conversion from `SourceTimestamp` is done by the source, not by the store.
        """
        sample = Sample(source_time_s=source_time_s, value=value)
        with self._lock:
            buffer = self._buffers.get(signal)
            if buffer is None:
                buffer = SignalBuffer(capacity=self._capacity)
                self._buffers[signal] = buffer
            buffer.put(sample)

    def clear(self) -> None:
        """Delete every sample.

        **Do not use this when the connection drops** — the scene should stay on the
        last known state. This is for switching to a different machine or a different
        recording.
        """
        with self._lock:
            self._buffers.clear()

    # -- reading (the render thread) ----------------------------------------

    def sample_all(self, at_time_s: float) -> dict[str, float]:
        """The interpolated values of all known signals at `at_time_s`.

        Signals without a single sample are not in the result — the caller should use
        `rest_pose()` for the joints missing here.
        """
        with self._lock:
            snapshot = list(self._buffers.items())

        result: dict[str, float] = {}
        for signal, buffer in snapshot:
            value = buffer.sample_at(at_time_s)
            if value is not None:
                result[signal] = value
        return result

    def sample(self, signal: str, at_time_s: float) -> float | None:
        """The interpolated value of one signal, or `None` when it has no samples."""
        with self._lock:
            buffer = self._buffers.get(signal)
        return buffer.sample_at(at_time_s) if buffer is not None else None

    def stale_signals(self, at_time_s: float, stale_after_s: float) -> frozenset[str]:
        """The signals that have gone longer than `stale_after_s` without a new sample."""
        with self._lock:
            snapshot = list(self._buffers.items())
        return frozenset(
            signal for signal, buffer in snapshot if buffer.is_stale(at_time_s, stale_after_s)
        )

    def latest_time(self) -> float | None:
        """The time of the newest sample across all signals.

        Serves as the reference time for `sample_all()`: rendering has to be done
        against the time of the data, not against the local clock. Returns `None`
        until something has arrived.
        """
        with self._lock:
            times = [
                buffer.latest.source_time_s
                for buffer in self._buffers.values()
                if buffer.latest is not None
            ]
        return max(times) if times else None

    @property
    def signal_names(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._buffers)

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffers)
