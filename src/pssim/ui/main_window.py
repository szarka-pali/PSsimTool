"""Hlavné okno aplikácie.

Zatiaľ len shell: menu, stavový riadok a miesto, kam neskôr príde 3D viewport
z `viz/`. Bez OPC UA, bez definície stroja.

Menu je zámerne rozdelené tak, ako bolo zadané — `Open` je **samostatná
položka v menu bare**, nie podpoložka `File`. Zvyklosť je mať `Open` pod `File`;
ak sa to má zmeniť, je to jednoriadková úprava v `_build_menu()`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QWidget

from pssim.observability import get_logger
from pssim.ui.loader import StepImportThread, summarize

logger = get_logger(__name__)

#: Ako sa vyrobí 3D plocha okna. Testy sem podstrčia obyčajný widget —
#: `ShowBase` smie v procese existovať len raz a v testoch ho nechceme vôbec.
ViewportFactory = Callable[[], QWidget]

APP_TITLE: Final = "PSsimTool"

#: Filter dialógu. STEP je zatiaľ jediný podporovaný formát — viď
#: docs/architecture.md, IGES/JT/glTF sa dajú pridať v `cad/`.
CAD_FILE_FILTER: Final = "CAD súbory (*.step *.stp);;Všetky súbory (*)"

DEFAULT_SIZE: Final = (1200, 800)
MINIMUM_SIZE: Final = (640, 480)


class MainWindow(QMainWindow):
    """Okno s hlavným menu.

    Otvorenie súboru sa hlási signálom `file_opened`, nie priamym volaním —
    keď pribudne viewport, pripojí sa naň bez zmeny tohto okna.
    """

    file_opened = Signal(object)
    """Vyslaný po výbere súboru. Nesie `pathlib.Path`."""

    def __init__(
        self,
        parent: QWidget | None = None,
        viewport_factory: ViewportFactory | None = None,
    ) -> None:
        super().__init__(parent)

        self._current_file: Path | None = None
        self._import_thread: StepImportThread | None = None

        self.setWindowTitle(APP_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)

        self._viewport = (viewport_factory or _default_viewport)()
        self.setCentralWidget(self._viewport)

        self._build_menu()
        self.statusBar().showMessage("Pripravené")

    # -- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self.exit_action = QAction("E&xit", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.setStatusTip("Ukončí aplikáciu")
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        open_menu = menu_bar.addMenu("&Open")
        self.open_action = QAction("Open &3D file…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setStatusTip("Otvorí CAD súbor (STEP)")
        self.open_action.triggered.connect(self.open_file_dialog)
        open_menu.addAction(self.open_action)

    # -- otvorenie súboru ---------------------------------------------------

    @property
    def current_file(self) -> Path | None:
        """Naposledy otvorený súbor, alebo `None`."""
        return self._current_file

    def open_file_dialog(self) -> Path | None:
        """Zobrazí dialóg na výber súboru. Vracia vybranú cestu, alebo `None`.

        Oddelené od `set_current_file()`, aby sa dala logika po výbere testovať
        bez otvárania modálneho dialógu.
        """
        start_directory = str(self._current_file.parent) if self._current_file else ""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Otvoriť 3D súbor",
            start_directory,
            CAD_FILE_FILTER,
        )
        if not filename:
            logger.debug("výber súboru zrušený")
            return None
        return self.open_path(Path(filename))

    def open_path(self, path: Path) -> Path:
        """Otvorí súbor: zapamätá si ho a spustí načítanie geometrie."""
        self.set_current_file(path)
        self.load_file(path)
        return path

    def set_current_file(self, path: Path) -> Path:
        """Zapamätá si vybraný súbor a ohlási to zvyšku aplikácie.

        Načítanie **nespúšťa** — to je `load_file()`. Oddelené preto, že
        „ktorý súbor je otvorený" a „prebieha import" sú dva nezávislé stavy.
        """
        self._current_file = path
        self.setWindowTitle(f"{APP_TITLE} — {path.name}")
        self.statusBar().showMessage(str(path))
        logger.info("vybraný súbor", file=str(path))
        self.file_opened.emit(path)
        return path

    # -- načítanie geometrie ------------------------------------------------

    @property
    def is_loading(self) -> bool:
        return self._import_thread is not None

    def load_file(self, path: Path) -> None:
        """Spustí import STEP súboru na pozadí.

        Počas importu je `Open` zakázané — druhý súbeh by si prepísal cache
        aj scénu. Veľká zostava sa importuje minúty, preto sa to nedá robiť
        v hlavnom vlákne.
        """
        if self._import_thread is not None:
            logger.debug("import už beží, ignorujem", file=str(path))
            return

        self.open_action.setEnabled(False)
        self.statusBar().showMessage(f"Načítavam {path.name}…")

        thread = StepImportThread(path, parent=self)
        thread.succeeded.connect(self.on_import_succeeded)
        thread.failed.connect(self.on_import_failed)
        thread.finished.connect(self.on_import_finished)
        self._import_thread = thread
        thread.start()

    def on_import_succeeded(self, metadata: Any, cache_dir: Path) -> None:
        """Qt slot. Beží už v hlavnom vlákne — scénu smie stavať len ono."""
        show_assembly = getattr(self._viewport, "show_assembly", None)
        if show_assembly is None:
            self.statusBar().showMessage(summarize(metadata))
            return

        missing = show_assembly(metadata.assembly, cache_dir)
        message = summarize(metadata)
        if missing:
            message = f"{message} — {missing} dielom chýba geometria"
        self.statusBar().showMessage(message)

    def on_import_failed(self, message: str) -> None:
        """Qt slot: import zlyhal."""
        self.statusBar().showMessage("Načítanie zlyhalo")
        QMessageBox.warning(self, "Načítanie zlyhalo", message)

    def on_import_finished(self) -> None:
        """Qt slot: vlákno skončilo, nech už dopadlo akokoľvek."""
        self._import_thread = None
        self.open_action.setEnabled(True)


def _default_viewport() -> QWidget:
    """Skutočný 3D viewport. Oddelené, aby sa dal v testoch nahradiť."""
    from pssim.ui.viewport import Panda3DViewport

    return Panda3DViewport()


def run(argv: list[str] | None = None) -> int:
    """Spustí aplikáciu a vráti návratový kód. Blokuje do zatvorenia okna."""
    from PySide6.QtWidgets import QApplication

    application: Any = QApplication.instance() or QApplication(argv or [])
    application.setApplicationName(APP_TITLE)

    window = MainWindow()
    window.show()
    return int(application.exec())
