"""Prevod časových známok z PLC na internú monotónnu škálu.

Hodinky PLC nie sú synchronizované s lokálnymi a môžu skákať (NTP, ručné
nastavenie, prechod na letný čas). Interne preto používame **monotónnu** škálu
v sekundách a offset medzi ňou a časom PLC určíme **raz**, pri prvej vzorke.

Keby sa offset prepočítaval priebežne, každý skok hodín PLC by sa premietol
do interpolácie a diely by v scéne poskakovali.

Modul je čistý (čas prichádza ako argument), takže sa testuje deterministicky.
"""

from __future__ import annotations

from datetime import UTC, datetime


class Timebase:
    """Prevádza čas PLC na internú monotónnu škálu s pevným offsetom.

    Použitie::

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
        """Offset `internal = plc_epoch + offset`. `None` kým neprišla prvá vzorka."""
        return self._offset_s

    @property
    def drift_exceeded(self) -> bool:
        """Či sa čas PLC odchýlil od očakávania viac ako `max_drift_s`.

        Zdroj to má **zalogovať raz** a pokračovať — nie resetovať offset.
        Typicky to znamená, že sa PLC preNTPovalo alebo že hodinky idú inak.
        """
        return self._drift_exceeded

    def to_internal(self, plc_time: datetime, now_monotonic_s: float) -> float:
        """Prevedie časovú známku z PLC na internú škálu v sekundách.

        `plc_time` musí byť tz-aware. Naivný `datetime` je chyba — v OPC UA je
        `SourceTimestamp` vždy UTC a naivná hodnota znamená, že sa niekde stratila
        informácia o zóne.
        """
        if plc_time.tzinfo is None:
            raise ValueError("plc_time musí byť tz-aware; naivný datetime znamená stratenú zónu")

        plc_epoch = plc_time.astimezone(UTC).timestamp()

        if self._offset_s is None:
            self._offset_s = now_monotonic_s - plc_epoch
            return now_monotonic_s

        internal = plc_epoch + self._offset_s
        if abs(internal - now_monotonic_s) > self._max_drift_s:
            self._drift_exceeded = True
        return internal
