"""Záznam dátového toku do JSONL.

Bez záznamu a replay sa nedá vyvíjať bez hardware ani reprodukovať chybu, ktorá
sa stala raz na stroji u zákazníka. Viď docs/architecture.md R7.

Formát je jeden JSON objekt na riadok, aby sa dal záznam čítať aj `head`om
a aby prerušený zápis nezničil celý súbor:

    {"t": 12.345, "signal": "os_x", "value": 1.2345}

`t` je interná monotónna škála v sekundách. Záznam je preto **relatívny** —
nedá sa z neho zistiť, kedy presne bežal. To je zámer: záznamy z reálnych strojov
môžu obsahovať údaje zákazníka a čím menej metadát, tým lepšie.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import TextIO

from pssim.domain.errors import DataSourceError
from pssim.io.store import StateStore


class RecordingStore(StateStore):
    """`StateStore`, ktorý každý zápis zároveň zapíše do JSONL súboru.

    Použije sa namiesto obyčajného store — zdroj dát o zázname nevie a nemusí.

    Nie je to `DataSource`; je to store. Vďaka tomu funguje záznam s ľubovoľným
    zdrojom vrátane budúceho ADS alebo S7.
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
            raise DataSourceError(f"záznam sa nedá otvoriť: {self._path}: {exc}") from exc

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def put(self, signal: str, value: float, source_time_s: float) -> None:
        super().put(signal, value, source_time_s)
        if self._file is None:
            return
        # Zapisujeme priamo, bez bufferu naviac: buffering=1 (line buffered) stačí
        # a pri páde procesu zostane záznam použiteľný po posledný celý riadok.
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
