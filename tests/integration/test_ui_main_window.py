"""Testy hlavného okna.

Bežia **headless** — `QT_QPA_PLATFORM=offscreen` sa nastaví skôr, než sa
importuje PySide6, takže sa neotvorí žiadne okno a testy fungujú aj na stroji
bez displeja.

Vyžaduje `uv sync --extra ui`. Spustenie: ``uv run pytest -m ui``
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# Musí byť pred importom PySide6, inak si Qt vyberie platformu podľa prostredia
# a na CI bez displeja spadne.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from pssim.ui.main_window import APP_TITLE, MainWindow  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    """Jediná `QApplication` na modul — Qt viac ako jednu nedovolí."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def window(qt_app: QApplication) -> Iterator[MainWindow]:
    """Čerstvé okno pre každý test. Po teste sa zavrie."""
    instance = MainWindow()
    yield instance
    instance.close()


def _pick_file(path: Path | None) -> Callable[..., tuple[str, str]]:
    """Náhrada `QFileDialog.getOpenFileName`.

    `None` znamená, že používateľ stlačil Zrušiť — Qt v tom prípade vracia
    prázdny reťazec, nie `None`.
    """

    def fake_dialog(*args: object, **kwargs: object) -> tuple[str, str]:
        return (str(path) if path is not None else "", "")

    return fake_dialog


def menu_titles(window: MainWindow) -> list[str]:
    """Názvy položiek v menu bare, bez klávesových akcelerátorov (`&`)."""
    return [action.text().replace("&", "") for action in window.menuBar().actions()]


def menu_items(window: MainWindow, title: str) -> list[str]:
    for action in window.menuBar().actions():
        if action.text().replace("&", "") != title:
            continue
        # `QAction.menu()` je v stuboch typované ako QObject, hoci vracia QMenu.
        submenu = action.menu()
        assert isinstance(submenu, QMenu), f"menu {title!r} nemá podpoložky"
        return [item.text().replace("&", "") for item in submenu.actions()]
    raise AssertionError(f"menu {title!r} neexistuje; sú tam {menu_titles(window)}")


class TestOkno:
    def test_ma_nazov(self, window: MainWindow) -> None:
        assert window.windowTitle() == APP_TITLE

    def test_ma_stavovy_riadok(self, window: MainWindow) -> None:
        assert window.statusBar() is not None

    def test_ma_centralny_widget(self, window: MainWindow) -> None:
        # Sem neskôr príde 3D viewport z viz/.
        assert window.centralWidget() is not None

    def test_na_zaciatku_nie_je_otvoreny_subor(self, window: MainWindow) -> None:
        assert window.current_file is None


class TestMenu:
    def test_ma_prave_dve_hlavne_polozky(self, window: MainWindow) -> None:
        assert menu_titles(window) == ["File", "Open"]

    def test_file_obsahuje_exit(self, window: MainWindow) -> None:
        assert menu_items(window, "File") == ["Exit"]

    def test_open_obsahuje_otvorenie_3d_suboru(self, window: MainWindow) -> None:
        assert menu_items(window, "Open") == ["Open 3D file…"]

    def test_exit_ma_klavesovu_skratku(self, window: MainWindow) -> None:
        assert not window.exit_action.shortcut().isEmpty()

    def test_open_ma_klavesovu_skratku(self, window: MainWindow) -> None:
        assert not window.open_action.shortcut().isEmpty()


class TestExit:
    def test_exit_zavrie_okno(self, window: MainWindow) -> None:
        window.show()

        window.exit_action.trigger()

        assert not window.isVisible()

    def test_exit_sa_da_zavolat_aj_na_neotvorenom_okne(self, window: MainWindow) -> None:
        # Nesmie spadnúť, ak používateľ stlačí Ctrl+Q skôr, než sa okno zobrazí.
        window.exit_action.trigger()

        assert not window.isVisible()


class TestOtvorenieSuboru:
    def test_vybrany_subor_sa_zapamata(self, window: MainWindow, tmp_path: Path) -> None:
        step_file = tmp_path / "stroj.step"

        window.set_current_file(step_file)

        assert window.current_file == step_file

    def test_nazov_okna_ukaze_subor(self, window: MainWindow, tmp_path: Path) -> None:
        window.set_current_file(tmp_path / "stroj.step")

        assert "stroj.step" in window.windowTitle()

    def test_stavovy_riadok_ukaze_cestu(self, window: MainWindow, tmp_path: Path) -> None:
        step_file = tmp_path / "stroj.step"

        window.set_current_file(step_file)

        assert window.statusBar().currentMessage() == str(step_file)

    def test_vyslany_signal_nesie_cestu(self, window: MainWindow, tmp_path: Path) -> None:
        step_file = tmp_path / "stroj.step"
        received: list[Path] = []
        window.file_opened.connect(received.append)

        window.set_current_file(step_file)

        assert received == [step_file]

    def test_dialog_s_vyberom_nastavi_subor(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        step_file = tmp_path / "vybrany.step"
        monkeypatch.setattr(
            "pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(step_file)
        )

        assert window.open_file_dialog() == step_file

    def test_zruseny_dialog_nic_nezmeni(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Prázdny reťazec je to, čo Qt vráti pri stlačení Zrušiť.
        monkeypatch.setattr("pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(None))

        assert window.open_file_dialog() is None
        assert window.current_file is None

    def test_zruseny_dialog_nechá_povodny_nazov_okna(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(None))

        window.open_file_dialog()

        assert window.windowTitle() == APP_TITLE
