"""Hranica k vonkajšiemu svetu.

Referenčný modul projektu pre definíciu hraníc: `Protocol`, nie abstraktná trieda.
Konzument (`viz/`) nemusí importovať žiadnu konkrétnu implementáciu.

Každý zdroj dát — OPC UA, replay, mock — implementuje `DataSource` a nič viac.
Nový transport (ADS, S7) sa pridá bez zásahu do `viz/` a `domain/`. Viď R6.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pssim.io.store import StateStore


class SourceStatus(StrEnum):
    """Stav zdroja dát.

    Prechody sú popísané v `.claude/skills/domenovy-kontext/SKILL.md`. Každý nový
    stav pridaj **aj tam**, inak sa HUD a DEGRADED logika obídu.
    """

    DISCONNECTED = "disconnected"
    """Bez spojenia. Dáta v StateStore zostávajú — scéna stojí na poslednom stave."""

    CONNECTING = "connecting"
    """Prebieha pokus o spojenie alebo reconnect."""

    CONNECTED = "connected"
    """Spojenie žije a všetky signály sú svieže."""

    DEGRADED = "degraded"
    """Spojenie žije, ale aspoň jeden signál je zastaraný. Renderuje sa ďalej."""


@runtime_checkable
class DataSource(Protocol):
    """Zdroj hodnôt signálov.

    Kontrakt:

    - `start()` je neblokujúce. Ak zdroj potrebuje vlákno alebo asyncio loop,
      vytvorí si ho sám a `start()` sa vráti okamžite.
    - Zdroj zapisuje **výhradne** do `StateStore`, ktorý dostal v konštruktore.
      Nikdy sa nedotkne Panda3D ani scény.
    - `stop()` je idempotentné a musí sa vrátiť do niekoľkých sekúnd.
    - Odpadnutie spojenia **nie je výnimka** — zdroj prejde do `CONNECTING`
      a skúša znovu. Výnimku vyhoď len z `start()`, keď sa nedá ani začať.
    """

    @property
    def status(self) -> SourceStatus: ...

    @property
    def store(self) -> StateStore: ...

    def start(self) -> None:
        """Spustí prívod dát. Neblokuje.

        Vyhadzuje `DataSourceError`, ak je konfigurácia neplatná alebo sa zdroj
        nedá otvoriť vôbec.
        """
        ...

    def stop(self) -> None:
        """Zastaví prívod dát. Idempotentné."""
        ...
