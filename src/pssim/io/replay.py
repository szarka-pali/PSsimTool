"""Replaying a recorded data stream.

Implements the same `DataSource` contract as `OpcUaSource`, so the scene cannot tell
the difference. This is the main tool for reproducing faults from the field and for
developing without a PLC. See docs/architecture.md R7.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pssim.domain.errors import DataSourceError
from pssim.domain.interpolation import Sample
from pssim.io.base import SourceStatus
from pssim.io.store import StateStore
from pssim.observability import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RecordedSample:
    """One line from a recording."""

    signal: str
    sample: Sample


def read_recording(path: str | Path) -> tuple[RecordedSample, ...]:
    """Read a JSONL recording. A pure function — tested without threads.

    Damaged lines are skipped with a warning. An interrupted write (a process dying,
    for instance) leaves the last line incomplete, and that must not invalidate the
    whole recording.
    """
    file_path = Path(path)
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DataSourceError(f"záznam sa nedá prečítať: {file_path}: {exc}") from exc

    samples: list[RecordedSample] = []
    skipped = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            samples.append(
                RecordedSample(
                    signal=str(row["signal"]),
                    sample=Sample(source_time_s=float(row["t"]), value=float(row["value"])),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            skipped += 1
            logger.debug("skipping a damaged line", file=str(file_path), line=line_number)

    if not samples:
        raise DataSourceError(f"záznam {file_path} neobsahuje ani jednu použiteľnú vzorku")
    if skipped:
        logger.warning(
            "the recording contained damaged lines", file=str(file_path), skipped=skipped
        )

    return tuple(sorted(samples, key=lambda item: item.sample.source_time_s))


class ReplaySource:
    """Replay a recording into a `StateStore` with its original timing.

    Implements `pssim.io.base.DataSource`.
    """

    def __init__(
        self,
        path: str | Path,
        store: StateStore | None = None,
        *,
        speed: float = 1.0,
        loop: bool = False,
    ) -> None:
        if speed <= 0.0:
            raise DataSourceError("speed must be > 0")
        self._samples = read_recording(path)
        self._store = store if store is not None else StateStore()
        self._speed = speed
        self._loop = loop
        self._status = SourceStatus.DISCONNECTED
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def store(self) -> StateStore:
        return self._store

    @property
    def duration_s(self) -> float:
        return self._samples[-1].sample.source_time_s - self._samples[0].sample.source_time_s

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="pssim-replay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
        self._thread = None
        self._status = SourceStatus.DISCONNECTED

    def _run(self) -> None:
        self._status = SourceStatus.CONNECTED
        try:
            while not self._stop_event.is_set():
                self._play_once()
                if not self._loop or self._stop_event.is_set():
                    break
                self._store.clear()
        finally:
            self._status = SourceStatus.DISCONNECTED

    def _play_once(self) -> None:
        """Replay the recording once.

        Timing: `sleep()` here is the exception to the rule "no sleep in production
        code" — replay is deliberately meant to reproduce the original timing.
        """
        first_recorded = self._samples[0].sample.source_time_s
        wall_start = time.monotonic()

        for item in self._samples:
            if self._stop_event.is_set():
                return

            elapsed_recorded = (item.sample.source_time_s - first_recorded) / self._speed
            wait = wall_start + elapsed_recorded - time.monotonic()
            if wait > 0 and self._stop_event.wait(timeout=wait):
                return

            # Times are recomputed onto the current monotonic scale, so that
            # interpolation and stale detection behave as they do on a live connection.
            self._store.put(
                signal=item.signal,
                value=item.sample.value,
                source_time_s=wall_start + elapsed_recorded,
            )
