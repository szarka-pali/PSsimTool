"""Hlavné okno aplikácie.

Zatiaľ len shell: menu, stavový riadok a miesto, kam neskôr príde 3D viewport
z `viz/`. Bez OPC UA, bez definície stroja.

Menu je zámerne rozdelené tak, ako bolo zadané — `Open` je **samostatná
položka v menu bare**, nie podpoložka `File`. Zvyklosť je mať `Open` pod `File`;
ak sa to má zmeniť, je to jednoriadková úprava v `_build_menu()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QWidget

from pssim.observability import get_logger

logger = get_logger(__name__)

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._current_file: Path | None = None

        self.setWindowTitle(APP_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)

        self._placeholder = QLabel("Žiadny súbor.\nOtvor 3D súbor cez menu Open.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(self._placeholder)

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
        return self.set_current_file(Path(filename))

    def set_current_file(self, path: Path) -> Path:
        """Zapamätá si vybraný súbor a ohlási to zvyšku aplikácie.

        Súbor sa zatiaľ **nenačítava** — import geometrie a zobrazenie pribudnú
        až s viewportom.
        """
        self._current_file = path
        self.setWindowTitle(f"{APP_TITLE} — {path.name}")
        self.statusBar().showMessage(str(path))
        self._placeholder.setText(
            f"{path.name}\n\n(načítanie geometrie zatiaľ nie je implementované)"
        )
        logger.info("vybraný súbor", file=str(path))
        self.file_opened.emit(path)
        return path


def run(argv: list[str] | None = None) -> int:
    """Spustí aplikáciu a vráti návratový kód. Blokuje do zatvorenia okna."""
    from PySide6.QtWidgets import QApplication

    application: Any = QApplication.instance() or QApplication(argv or [])
    application.setApplicationName(APP_TITLE)

    window = MainWindow()
    window.show()
    return int(application.exec())
