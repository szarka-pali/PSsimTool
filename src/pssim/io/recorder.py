"""Recording the data stream into JSONL.

Without recording and replay there is no developing without hardware and no
reproducing a fault that happened once, at a customer's machine. See
docs/architecture.md R7.

The format is one JSON object per line, so a recording can be read with `head` and so
that an interrupted write does not destroy the whole file:

    {"t": 12.345, "signal": "os_x", "value": 1.2345}

`t` is the internal monotonic scale in seconds. A recording is therefore **relative** —
there is no way to tell from it when exactly it ran. That is deliberate: recordings from
real machines may contain customer data, and the fewer metadata the better.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import TextIO

from pssim.domain.errors import DataSourceError
from pssim.io.store import StateStore


class RecordingStore(StateStore):
    """A `StateStore` that also writes every put into a JSONL file.

    Used instead of the plain store — the data source knows nothing about the
    recording and does not need to.

    It is not a `DataSource`; it is a store. That is what makes recording work with any
    source, including a future ADS or S7.
    """

    def __init__(self, path: str | Path, capacity: int = 32) -> None:
        super().__init__(capacity=capacity)
        self._path = Path(path)
        self._file: TextIO | None = None
        self._count = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def sample_count(self) -> int:
        return self._count

    def open(self) -> None:
        if self._file is not None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._path.open("w", encoding="utf-8", buffering=1)
        except OSError as exc:
            raise DataSourceError(f"the recording cannot be opened: {self._path}: {exc}") from exc

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def put(self, signal: str, value: float, source_time_s: float) -> None:
        super().put(signal, value, source_time_s)
        if self._file is None:
            return
        # We write straight through, with no extra buffer: buffering=1 (line buffered)
        # is enough, and if the process dies the recording stays usable up to the last
        # complete line.
        line = json.dumps(
            {"t": round(source_time_s, 6), "signal": signal, "value": value},
            ensure_ascii=False,
        )
        self._file.write(line + "\n")
        self._count += 1

    def __enter__(self) -> RecordingStore:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
