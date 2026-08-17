"""Testy mechanizmu prekladov a anglických textov UI.

Zdrojový jazyk je angličtina, takže bez nainštalovaného prekladu musí appka
ukazovať texty tak, ako sú napísané v kóde. To je zároveň zmysel týchto testov:
chýbajúci alebo pokazený preklad **nesmie** appku zhodiť.

Bežia headless cez `QT_QPA_PLATFORM=offscreen`.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pssim.cad.model import CadAssembly, CadNode  # noqa: E402
from pssim.domain.machine import Transform  # noqa: E402
from pssim.domain.placement import PlacementDisplay, to_transform  # noqa: E402
from pssim.ui.i18n import (  # noqa: E402
    LANGUAGES,
    SOURCE_LANGUAGE,
    available_languages,
    install_translator,
    translation_file,
)
from pssim.ui.labels import (  # noqa: E402
    describe_assembly,
    describe_placement,
    missing_geometry_suffix,
)
from pssim.ui.main_window import MainWindow, cad_file_filter  # noqa: E402
from pssim.ui.placement_dialog import PlacementDialog  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


class _StubViewport(QWidget):
    """Viewport bez Panda3D. Musí mať `set_view`, inak okno hlášku nenastaví."""

    def set_view(self, name: str) -> None:
        self.last_view = name


@pytest.fixture
def window(qt_app: QApplication) -> Iterator[MainWindow]:
    instance = MainWindow(viewport_factory=_StubViewport)
    yield instance
    instance.close()


class TestRegisterJazykov:
    def test_zdrojovy_jazyk_je_anglictina(self) -> None:
        assert SOURCE_LANGUAGE == "en"

    def test_zdrojovy_jazyk_je_vzdy_dostupny(self) -> None:
        # Nepotrebuje `.qm` súbor — texty sú v ňom napísané v kóde.
        assert SOURCE_LANGUAGE in available_languages()

    def test_jazyky_su_pomenovane_vo_svojom_jazyku(self) -> None:
        # Budúce menu má zobrazovať „Slovenčina", nie „Slovak".
        assert LANGUAGES["sk"] == "Slovenčina"

    def test_dostupne_su_podmnozinou_registrovanych(self) -> None:
        assert set(available_languages()) <= set(LANGUAGES)

    def test_jazyk_bez_prekladu_nie_je_dostupny(self) -> None:
        # `sk` je registrovaný, ale `.qm` preň zatiaľ neexistuje.
        if not translation_file("sk").is_file():
            assert "sk" not in available_languages()

    def test_nazov_suboru_prekladu(self) -> None:
        assert translation_file("de").name == "pssim_de.qm"


class TestInstalaciaPrekladu:
    def test_zdrojovy_jazyk_nic_neinstaluje(self, qt_app: QApplication) -> None:
        assert install_translator(qt_app, SOURCE_LANGUAGE) is False

    def test_neznamy_jazyk_nespadne(self, qt_app: QApplication) -> None:
        # Preklep v kóde jazyka nesmie zhodiť štart aplikácie.
        assert install_translator(qt_app, "klingon") is False

    def test_chybajuci_preklad_nespadne(self, qt_app: QApplication) -> None:
        assert install_translator(qt_app, "sk") is (translation_file("sk").is_file())

    def test_poskodeny_subor_prekladu_nespadne(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = tmp_path / "pssim_sk.qm"
        broken.write_bytes(b"toto nie je qm subor")
        monkeypatch.setattr("pssim.ui.i18n.TRANSLATIONS_DIR", tmp_path)

        assert install_translator(qt_app, "sk") is False


class TestAnglickeTextyOkna:
    def test_stavovy_riadok_je_po_anglicky(self, window: MainWindow) -> None:
        assert window.statusBar().currentMessage() == "Ready"

    def test_lista_ma_anglicky_nazov(self, window: MainWindow) -> None:
        assert window.toolbar.windowTitle() == "View"

    def test_tlacidlo_pohladu_je_po_anglicky(self, window: MainWindow) -> None:
        assert window.view_button.text() == "View"

    def test_zobraz_cele_je_po_anglicky(self, window: MainWindow) -> None:
        assert window.fit_action.text() == "Fit to view"

    def test_popis_akcie_exit_je_po_anglicky(self, window: MainWindow) -> None:
        assert window.exit_action.statusTip() == "Quit the application"

    def test_prepnutie_pohladu_hlasi_po_anglicky(self, window: MainWindow) -> None:
        window.set_view("top")

        assert window.statusBar().currentMessage() == "View: top"

    def test_filter_suborov_je_po_anglicky(self) -> None:
        assert "CAD files" in cad_file_filter()
        assert "All files" in cad_file_filter()


class TestAnglickeTextyDialogu:
    def test_nazov_dialogu(self, qt_app: QApplication) -> None:
        dialog = PlacementDialog()

        assert dialog.windowTitle() == "Model Placement"
        dialog.close()

    def test_skupiny_maju_anglicke_nazvy(self, qt_app: QApplication) -> None:
        from PySide6.QtWidgets import QGroupBox

        dialog = PlacementDialog()
        titles = {group.title() for group in dialog.findChildren(QGroupBox)}
        dialog.close()

        assert titles == {"Translation", "Rotation"}


class TestPrelozitelneHlasky:
    def test_identita_sa_popise_po_anglicky(self) -> None:
        assert describe_placement(Transform()) == "Model at origin, no rotation"

    def test_popis_uvadza_milimetre_nie_metre(self) -> None:
        # Keby popis ukázal 0.1, používateľ by hľadal, kde sa mu stratilo 100.
        text = describe_placement(to_transform(PlacementDisplay(x_mm=100.0)))

        assert "100" in text
        assert "mm" in text

    def test_popis_uvadza_stupne(self) -> None:
        text = describe_placement(to_transform(PlacementDisplay(rotate_z_deg=45.0)))

        assert "45" in text
        assert "°" in text

    def test_popis_je_po_anglicky(self) -> None:
        text = describe_placement(to_transform(PlacementDisplay(x_mm=1.0)))

        assert "Moved" in text
        assert "rotated" in text

    def test_otocenie_o_ciely_stupen_sa_nezaokruhli_zle(self) -> None:
        text = describe_placement(Transform(rpy=(0.0, 0.0, math.pi)))

        assert "180" in text

    def test_popis_modelu_bez_assembly(self) -> None:
        assert describe_assembly(None) == "Model loaded"

    def test_popis_modelu_uvadza_pocty(self) -> None:
        assembly = CadAssembly(
            nodes=(
                CadNode(path="base", triangle_count=0),
                CadNode(path="base/diel", mesh="a.npz", triangle_count=12),
            ),
            roots=("base",),
        )

        text = describe_assembly(assembly)

        assert "2 parts" in text
        assert "12 triangles" in text

    def test_doplnok_o_chybajucej_geometrii(self) -> None:
        assert "3" in missing_geometry_suffix(3)
        assert "geometry missing" in missing_geometry_suffix(3)
