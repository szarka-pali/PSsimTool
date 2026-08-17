"""Interpolácia vzoriek signálu.

OPC UA doručuje dáta každých 20–100 ms, renderujeme 60 fps. Bez interpolácie je
pohyb trhaný. Viď docs/architecture.md R5.

Kľúčové rozhodnutie: **čas je vždy argument**, nikdy sa nečíta z hodín. Vďaka tomu
sa celá interpolácia testuje deterministicky.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Final

#: Koľko vzoriek na signál držíme. Pri 100 ms intervale to je ~3 s histórie —
#: dosť na interpoláciu aj na krátky graf v HUD, málo na to, aby to zaberalo pamäť.
DEFAULT_CAPACITY: Final = 32


@dataclass(frozen=True, slots=True)
class Sample:
    """Jedna hodnota signálu s časom, kedy vznikla v PLC.

    `source_time_s` je v internej monotónnej škále v sekundách — prevod
    z `SourceTimestamp` sa deje v `io/`, nie tu.
    """

    source_time_s: float
    value: float


class SignalBuffer:
    """Ring buffer vzoriek jedného signálu s lineárnou interpoláciou.

    Nie je thread-safe. Súbežný prístup rieši `io.store.StateStore` lockom.
    """

    __slots__ = ("_samples", "_capacity")

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 2:
            raise ValueError("capacity musí byť aspoň 2, inak sa nedá interpolovať")
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
        """Pridá vzorku.

        Vzorky sa očakávajú v rastúcom čase. Vzorku so **starším** časom, než má
        posledná, zahodíme — servery pri reconnecte občas pošlú starú hodnotu
        a tá by inak spôsobila skok dozadu.
        """
        latest = self.latest
        if latest is not None and sample.source_time_s < latest.source_time_s:
            return
        self._samples.append(sample)

    def clear(self) -> None:
        self._samples.clear()

    def sample_at(self, at_time_s: float) -> float | None:
        """Hodnota signálu v čase `at_time_s`, lineárne interpolovaná.

        Vracia `None`, ak buffer je prázdny. Mimo rozsahu vzoriek **neextrapoluje** —
        vráti krajnú hodnotu. Extrapolácia by pri zaseknutom signáli poslala diel
        do nekonečna.
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
            # Rovnaké časové známky sú bežné napr. pri S7-1500, kde má
            # SourceTimestamp rozlíšenie cyklu OB. Vezmi novšiu hodnotu.
            return newer.value

        ratio = (at_time_s - older.source_time_s) / span
        return older.value + (newer.value - older.value) * ratio

    def _bracket(self, at_time_s: float) -> tuple[Sample, Sample]:
        """Dve vzorky, medzi ktorými `at_time_s` leží.

        Volá sa len keď je `at_time_s` preukázateľne vnútri rozsahu, preto tu
        nie je fallback — ak by sa vrátil, bola by to skrytá chyba.
        """
        previous = self._samples[0]
        for current in self._samples:
            if current.source_time_s >= at_time_s:
                return previous, current
            previous = current
        raise AssertionError("sample_at už overil, že at_time_s je vnútri rozsahu")

    def is_stale(self, at_time_s: float, stale_after_s: float) -> bool:
        """Či signál nedostal novú vzorku dlhšie ako `stale_after_s`.

        Prázdny buffer je zastaraný — ešte nikdy nič neprišlo.
        """
        latest = self.latest
        if latest is None:
            return True
        return (at_time_s - latest.source_time_s) > stale_after_s
