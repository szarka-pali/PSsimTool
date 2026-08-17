"""Hlavné okno aplikácie.

Zatiaľ len shell: menu, stavový riadok a miesto, kam neskôr príde 3D viewport
z `viz/`. Bez OPC UA, bez definície stroja.

Menu je zámerne rozdelené tak, ako bolo zadané — `Open` je **samostatná
položka v menu bare**, nie podpoložka `File`. Zvyklosť je mať `Open` pod `File`;
ak sa to má zmeniť, je to jednoriadková úprava v `_build_menu()`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import QCoreApplication, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
    QToolButton,
    QWidget,
)

from pssim.domain.machine import Transform
from pssim.domain.placement import IDENTITY_PLACEMENT
from pssim.observability import get_logger
from pssim.ui.i18n import SOURCE_LANGUAGE, install_translator
from pssim.ui.icons import fit_icon, view_icon
from pssim.ui.labels import describe_assembly, describe_placement, missing_geometry_suffix
from pssim.ui.loader import StepImportThread
from pssim.ui.placement_dialog import PlacementDialog

logger = get_logger(__name__)

#: Ako sa vyrobí 3D plocha okna. Testy sem podstrčia obyčajný widget —
#: `ShowBase` smie v procese existovať len raz a v testoch ho nechceme vôbec.
ViewportFactory = Callable[[], QWidget]

APP_TITLE: Final = "PSsimTool"


def cad_file_filter() -> str:
    """Filter dialógu na výber súboru.

    Funkcia, nie konštanta: preklad musí prebehnúť až keď je nainštalovaný
    prekladač, nie pri importe modulu.

    STEP je zatiaľ jediný podporovaný formát — viď docs/architecture.md,
    IGES/JT/glTF sa dajú pridať v `cad/`.
    """
    return QCoreApplication.translate("MainWindow", "CAD files (*.step *.stp);;All files (*)")


DEFAULT_SIZE: Final = (1200, 800)
MINIMUM_SIZE: Final = (640, 480)

#: Položky menu pohľadov v poradí, v akom sa zobrazia. Kľúče musia existovať
#: v `viz.orbit.STANDARD_VIEWS` — je to jediný zdroj definície pohľadov.
VIEW_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("iso", "Isometric"),
    ("front", "Front"),
    ("back", "Back"),
    ("left", "Left"),
    ("right", "Right"),
    ("top", "Top"),
    ("bottom", "Bottom"),
)


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
        self._placement_dialog: PlacementDialog | None = None

        self.setWindowTitle(APP_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)

        self._viewport = (viewport_factory or _default_viewport)()
        self.setCentralWidget(self._viewport)

        self._build_menu()
        self._build_toolbar()
        self.statusBar().showMessage(self.tr("Ready"))

    # -- menu ---------------------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self.exit_action = QAction("E&xit", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.setStatusTip(self.tr("Quit the application"))
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        open_menu = menu_bar.addMenu("&Open")
        self.open_action = QAction("Open &3D file…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setStatusTip(self.tr("Open a CAD file (STEP)"))
        self.open_action.triggered.connect(self.open_file_dialog)
        open_menu.addAction(self.open_action)

        model_menu = menu_bar.addMenu("&Model")
        self.placement_action = QAction("&Placement…", self)
        self.placement_action.setShortcut(QKeySequence("Ctrl+M"))
        self.placement_action.setStatusTip(
            self.tr("Move and rotate the model relative to the origin")
        )
        self.placement_action.triggered.connect(self.open_placement_dialog)
        model_menu.addAction(self.placement_action)

    # -- lišta pohľadov -----------------------------------------------------

    def _build_toolbar(self) -> None:
        """Lišta s prepínaním pohľadov.

        Jedno tlačidlo s rozbaľovacím menu, nie sedem samostatných ikon —
        pohľad sa mení zriedka a sedem tlačidiel by zabralo lištu, do ktorej
        pribudnú dôležitejšie nástroje.
        """
        toolbar = QToolBar(self.tr("View"), self)
        toolbar.setObjectName("view-toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.view_menu = QMenu(self.tr("View"), self)
        self.view_actions: dict[str, QAction] = {}

        for index, (name, label) in enumerate(VIEW_LABELS, start=1):
            action = QAction(view_icon(name), label, self)
            action.setShortcut(QKeySequence(f"Ctrl+{index}"))
            action.setStatusTip(self.tr("View: {0}").format(label))
            action.triggered.connect(partial(self.set_view, name))
            self.view_menu.addAction(action)
            self.view_actions[name] = action

        self.view_button = QToolButton(self)
        self.view_button.setText(self.tr("View"))
        self.view_button.setIcon(view_icon("iso"))
        self.view_button.setMenu(self.view_menu)
        self.view_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addWidget(self.view_button)

        self.fit_action = QAction(fit_icon(), self.tr("Fit to view"), self)
        self.fit_action.setShortcut(QKeySequence("Ctrl+0"))
        self.fit_action.setStatusTip(self.tr("Center the camera on the whole model"))
        self.fit_action.triggered.connect(self.fit_view)
        toolbar.addAction(self.fit_action)

        self.toolbar = toolbar

    def set_view(self, name: str) -> None:
        """Prepne 3D pohľad. Priblíženie zostáva, mení sa len uhol."""
        set_view = getattr(self._viewport, "set_view", None)
        if set_view is None:
            logger.debug("viewport nepodporuje prepínanie pohľadov", view=name)
            return
        set_view(name)
        # Ikona tlačidla ukazuje, v akom pohľade sa práve nachádzame.
        self.view_button.setIcon(view_icon(name))
        self.statusBar().showMessage(self.tr("View: {0}").format(name))

    def fit_view(self) -> None:
        """Vycentruje kameru tak, aby bol celý model v zábere."""
        fit = getattr(self._viewport, "fit_view", None)
        if fit is None:
            return
        fit()
        self.statusBar().showMessage(self.tr("Whole model in view"))

    # -- umiestnenie modelu -------------------------------------------------

    def open_placement_dialog(self) -> PlacementDialog:
        """Otvorí dialóg na posun a otočenie modelu.

        Dialóg je **nemodálny** a mení scénu priebežne — inak by sa hodnoty
        zadávali naslepo. Druhé vyvolanie existujúci dialóg len vytiahne
        dopredu, nevytvorí ďalší.
        """
        if self._placement_dialog is not None:
            self._placement_dialog.raise_()
            self._placement_dialog.activateWindow()
            return self._placement_dialog

        dialog = PlacementDialog(self.placement, self)
        dialog.placement_changed.connect(self.apply_placement)
        dialog.finished.connect(self._on_placement_dialog_closed)
        self._placement_dialog = dialog
        dialog.show()
        return dialog

    @property
    def placement(self) -> Transform:
        """Aktuálne umiestnenie modelu, alebo identita ak viewport nič nevie."""
        placement = getattr(self._viewport, "placement", None)
        return placement if isinstance(placement, Transform) else IDENTITY_PLACEMENT

    def apply_placement(self, placement: Transform) -> None:
        """Premietne umiestnenie do scény. Qt slot dialógu."""
        set_placement = getattr(self._viewport, "set_placement", None)
        if set_placement is None:
            return
        set_placement(placement)
        self.statusBar().showMessage(describe_placement(placement))

    def _on_placement_dialog_closed(self, _result: int) -> None:
        self._placement_dialog = None

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
            self.tr("Open 3D file"),
            start_directory,
            cad_file_filter(),
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
        self.statusBar().showMessage(self.tr("Loading {0}…").format(path.name))

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
            self.statusBar().showMessage(describe_assembly(metadata.assembly))
            return

        missing = show_assembly(metadata.assembly, cache_dir)
        message = describe_assembly(metadata.assembly)
        if missing:
            message += missing_geometry_suffix(missing)
        self.statusBar().showMessage(message)

    def on_import_failed(self, message: str) -> None:
        """Qt slot: import zlyhal."""
        self.statusBar().showMessage(self.tr("Loading failed"))
        QMessageBox.warning(self, self.tr("Loading failed"), message)

    def on_import_finished(self) -> None:
        """Qt slot: vlákno skončilo, nech už dopadlo akokoľvek."""
        self._import_thread = None
        self.open_action.setEnabled(True)


def _default_viewport() -> QWidget:
    """Skutočný 3D viewport. Oddelené, aby sa dal v testoch nahradiť."""
    from pssim.ui.viewport import Panda3DViewport

    return Panda3DViewport()


def run(argv: list[str] | None = None, language: str = SOURCE_LANGUAGE) -> int:
    """Spustí aplikáciu a vráti návratový kód. Blokuje do zatvorenia okna.

    Prekladač sa inštaluje **pred** vytvorením okna — texty sa prekladajú
    v momente, keď sa widgety skladajú, nie priebežne.
    """
    from PySide6.QtWidgets import QApplication

    application: Any = QApplication.instance() or QApplication(argv or [])
    application.setApplicationName(APP_TITLE)
    install_translator(application, language)

    window = MainWindow()
    window.show()
    return int(application.exec())
