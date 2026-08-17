"""Thread-safe držiteľ posledných vzoriek všetkých signálov.

Toto je **jediné** miesto, kde sa zdieľa stav medzi vláknami. Nepridávaj druhé —
ak treba zdieľať niečo ďalšie, rozšír store. Viď docs/architecture.md R4.

Vláknový model:

    vlákno B (asyncio, io/)  →  put()
    vlákno A (Panda3D, viz/) →  sample_all() / status_of()

Zdieľame *latest value + krátky ring buffer*, nie queue: queue by pri zaostávaní
renderu rástla a zobrazovali by sa staré dáta.
"""

from __future__ import annotations

import threading

from pssim.domain.interpolation import DEFAULT_CAPACITY, Sample, SignalBuffer


class StateStore:
    """Vzorky signálov, bezpečne zdieľané medzi vláknami.

    Lock sa drží čo najkratšie: pod lockom sa kopírujú dáta, nepočíta sa.
    Interpolácia beží mimo lock, nad kópiou.
    """

    __slots__ = ("_buffers", "_lock", "_capacity")

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._buffers: dict[str, SignalBuffer] = {}

    # -- zápis (vlákno zdroja dát) -----------------------------------------

    def put(self, signal: str, value: float, source_time_s: float) -> None:
        """Zapíše vzorku. Volá sa z vlákna zdroja dát.

        `source_time_s` musí byť už prevedený na internú monotónnu škálu —
        prevod z `SourceTimestamp` robí zdroj, nie store.
        """
        sample = Sample(source_time_s=source_time_s, value=value)
        with self._lock:
            buffer = self._buffers.get(signal)
            if buffer is None:
                buffer = SignalBuffer(capacity=self._capacity)
                self._buffers[signal] = buffer
            buffer.put(sample)

    def clear(self) -> None:
        """Zmaže všetky vzorky.

        **Nepoužívaj pri odpadnutí spojenia** — scéna má zostať stáť na poslednom
        známom stave. Slúži na prepnutie na iný stroj alebo iný záznam.
        """
        with self._lock:
            self._buffers.clear()

    # -- čítanie (render vlákno) -------------------------------------------

    def sample_all(self, at_time_s: float) -> dict[str, float]:
        """Interpolované hodnoty všetkých známych signálov v čase `at_time_s`.

        Signály bez jedinej vzorky vo výsledku nie sú — volajúci má použiť
        `rest_pose()` pre kĺby, ktoré tu chýbajú.
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
        """Interpolovaná hodnota jedného signálu, alebo `None` ak nemá vzorky."""
        with self._lock:
            buffer = self._buffers.get(signal)
        return buffer.sample_at(at_time_s) if buffer is not None else None

    def stale_signals(self, at_time_s: float, stale_after_s: float) -> frozenset[str]:
        """Signály, ktoré nedostali novú vzorku dlhšie ako `stale_after_s`."""
        with self._lock:
            snapshot = list(self._buffers.items())
        return frozenset(
            signal for signal, buffer in snapshot if buffer.is_stale(at_time_s, stale_after_s)
        )

    def latest_time(self) -> float | None:
        """Čas najnovšej vzorky spomedzi všetkých signálov.

        Slúži ako referenčný čas pre `sample_all()`: renderovať treba voči času
        dát, nie voči lokálnym hodinám. Vracia `None`, kým nič neprišlo.
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
