"""Importing a STEP file in the background.

Tessellating an assembly of thousands of parts takes tens of seconds to minutes. If it ran
in the main thread, the window would be unresponsive for all of it and Windows would mark it
as frozen.

The division of work follows from what is safe outside the main thread:

- **the worker** — reading the STEP, tessellating, writing into the cache (pure Python and OCC)
- **the main thread** — building the scene from the cache (the Panda3D scene graph)
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from pssim.domain.errors import PSsimError
from pssim.observability import get_logger

logger = get_logger(__name__)

DEFAULT_CACHE_ROOT: Final = Path("assets/cache")

#: The units to assume when there is nowhere to get them from. Most STEP files from
#: mechanical CAD are in millimetres. Once a machine definition is present, `units:` from
#: `machines/*.yaml` takes precedence.
ASSUMED_UNITS: Final = "mm"


class StepImportThread(QThread):
    """Import a STEP into the cache outside the main thread.

    The signals are delivered back into the main thread, so a UI update can safely be
    connected to them.
    """

    succeeded = Signal(object, object)
    """`(CacheMetadata, Path)` — the metadata and the cache directory with the geometry."""

    failed = Signal(str)
    """The error text intended for the user."""

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
        """Runs in the worker thread. Must not touch anything from Qt or from Panda3D."""
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
        except Exception as exc:  # unexpected - the user has to see something
            logger.exception("import spadol", file=str(self._step_file))
            self.failed.emit(
                QCoreApplication.translate(
                    "StepImportThread", "Unexpected error during import: {0}"
                ).format(exc)
            )
            return

        self.succeeded.emit(metadata, cache_dir)
