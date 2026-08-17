"""Import STEP súboru na pozadí.

Tesselácia zostavy s tisíckami dielov trvá desiatky sekúnd až minúty. Keby
bežala v hlavnom vlákne, okno by celý ten čas nereagovalo a Windows by ho
označil ako zamrznuté.

Deľba práce je daná tým, čo je bezpečné mimo hlavného vlákna:

- **worker** — čítanie STEP, tesselácia, zápis do cache (čistý Python a OCC)
- **hlavné vlákno** — stavba scény z cache (Panda3D scene graph)
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, QThread, Signal

from pssim.domain.errors import PSsimError
from pssim.observability import get_logger

logger = get_logger(__name__)

DEFAULT_CACHE_ROOT: Final = Path("assets/cache")

#: Jednotky, keď ich nemáme odkiaľ zistiť. Väčšina STEP súborov zo strojárskeho
#: CAD je v milimetroch. Keď pribudne definícia stroja, prednosť dostane `units:`
#: z `machines/*.yaml`.
ASSUMED_UNITS: Final = "mm"


class StepImportThread(QThread):
    """Naimportuje STEP do cache mimo hlavného vlákna.

    Signály sa doručujú späť do hlavného vlákna, takže sa na ne dá bezpečne
    napojiť aktualizácia UI.
    """

    succeeded = Signal(object, object)
    """`(CacheMetadata, Path)` — metadáta a adresár cache s geometriou."""

    failed = Signal(str)
    """Text chyby určený používateľovi."""

    def __init__(
        self,
        step_file: Path,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._step_file = step_file
        self._cache_root = cache_root

    @property
    def step_file(self) -> Path:
        return self._step_file

    def run(self) -> None:
        """Beží vo worker vlákne. Nesmie sa dotknúť ničoho z Qt ani z Panda3D."""
        from pssim.cad.step_import import ImportSettings, cache_key_for, import_step
        from pssim.domain.units import length_scale_to_m

        try:
            settings = ImportSettings(
                step_file=self._step_file,
                scale_to_m=length_scale_to_m(ASSUMED_UNITS),
                units=ASSUMED_UNITS,
            )
            metadata = import_step(settings, self._cache_root)
            cache_dir = self._cache_root / cache_key_for(settings).digest
        except PSsimError as exc:
            logger.warning("import zlyhal", file=str(self._step_file), error=str(exc))
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # neočakávané — používateľ musí niečo vidieť
            logger.exception("import spadol", file=str(self._step_file))
            self.failed.emit(f"Neočakávaná chyba pri importe: {exc}")
            return

        self.succeeded.emit(metadata, cache_dir)


def summarize(metadata: object) -> str:
    """Jednoveta o naimportovanom modeli pre stavový riadok.

    Čistá funkcia, aby sa dala otestovať bez Qt aj bez OpenCASCADE.
    """
    assembly = getattr(metadata, "assembly", None)
    if assembly is None:
        return "model načítaný"

    nodes = len(assembly.nodes)
    triangles = assembly.triangle_count
    return f"{nodes} dielov, {triangles} trojuholníkov"
