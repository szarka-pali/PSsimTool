"""Testy hlavného okna.

Bežia **headless** — `QT_QPA_PLATFORM=offscreen` sa nastaví skôr, než sa
importuje PySide6, takže sa neotvorí žiadne okno a testy fungujú aj na stroji
bez displeja.

Vyžaduje `uv sync --extra ui`. Spustenie: ``uv run pytest -m ui``
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# Musí byť pred importom PySide6, inak si Qt vyberie platformu podľa prostredia
# a na CI bez displeja spadne.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QToolButton, QWidget  # noqa: E402

from pssim.cad.model import CadAssembly, CadNode  # noqa: E402
from pssim.domain.machine import Transform  # noqa: E402
from pssim.domain.placement import IDENTITY_PLACEMENT  # noqa: E402
from pssim.ui.main_window import APP_TITLE, MainWindow  # noqa: E402
from pssim.ui.model_registry import ModelEntry  # noqa: E402
from pssim.viz.orbit import STANDARD_VIEWS  # noqa: E402

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
    """Čerstvé okno pre každý test. Po teste sa zavrie.

    Namiesto skutočného 3D viewportu dostane obyčajný widget: `ShowBase` smie
    v procese existovať len raz, takže sa v testoch nevytvára vôbec. Zobrazenie
    geometrie overuje `tests/integration/test_viz_scene.py` a reálne spustenie.
    """
    instance = MainWindow(viewport_factory=QWidget)
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


def _record_loads(recorded: list[Path]) -> Callable[..., None]:
    """Náhrada `MainWindow.load_file`, ktorá si len zapíše, čo sa malo načítať."""

    def fake_load(_self: object, path: Path) -> None:
        recorded.append(path)

    return fake_load


def _skip_loading(_self: object, _path: Path) -> None:
    """Náhrada `MainWindow.load_file`, ktorá nerobí nič."""


class _StubThread(QObject):
    """Náhrada `StepImportThread`, ktorá nespustí žiadny import.

    Skutočné vlákno by nad neexistujúcim súborom skončilo chybou a tá by
    otvorila modálny dialóg — ten by v teste nemal kto zavrieť.
    """

    started_count = 0

    succeeded = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, step_file: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.step_file = step_file

    def start(self) -> None:
        type(self).started_count += 1


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
    def test_hlavne_polozky_su_v_poradi(self, window: MainWindow) -> None:
        assert menu_titles(window) == ["File", "Open", "Model"]

    def test_file_obsahuje_projektove_polozky(self, window: MainWindow) -> None:
        # Separators come through as empty strings; only the real entries matter.
        entries = [text for text in menu_items(window, "File") if text]

        assert entries == [
            "Open Project…",
            "Open Recent",
            "Save Project",
            "Save Project As…",
            "Close All",
            "Exit",
        ]

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
        # Skutočný import by tu spustil vlákno nad neexistujúcim súborom
        # a jeho chyba by otvorila modálny dialóg, ktorý test nemá kto zavrieť.
        monkeypatch.setattr(MainWindow, "load_file", _skip_loading)

        assert window.open_file_dialog() == step_file

    def test_dialog_spusti_nacitanie(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        step_file = tmp_path / "vybrany.step"
        loaded: list[Path] = []
        monkeypatch.setattr(
            "pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(step_file)
        )
        monkeypatch.setattr(MainWindow, "load_file", _record_loads(loaded))

        window.open_file_dialog()

        assert loaded == [step_file]

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

    def test_zruseny_dialog_nespusti_nacitanie(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loaded: list[Path] = []
        monkeypatch.setattr("pssim.ui.main_window.QFileDialog.getOpenFileName", _pick_file(None))
        monkeypatch.setattr(MainWindow, "load_file", _record_loads(loaded))

        window.open_file_dialog()

        assert loaded == []


class _StubViewport(QWidget):
    """Viewport without Panda3D that records what the window asked of it."""

    def __init__(self) -> None:
        super().__init__()
        self.views: list[str] = []
        self.fit_calls: list[str | None] = []
        self.placements: dict[str, Transform] = {}
        self.highlighted: str | None = None
        self.added: list[str] = []
        self.removed: list[str] = []

    def set_view(self, name: str) -> None:
        self.views.append(name)

    def fit_view(self, model_id: str | None = None) -> None:
        self.fit_calls.append(model_id)

    def set_highlight(self, model_id: str | None) -> None:
        self.highlighted = model_id

    def add_model(self, model_id: str, assembly: object, cache_dir: Path) -> int:
        self.added.append(model_id)
        return 0

    def remove_model(self, model_id: str) -> None:
        self.removed.append(model_id)

    def placement(self, model_id: str) -> Transform:
        return self.placements.get(model_id, IDENTITY_PLACEMENT)

    def set_placement(self, model_id: str, placement: Transform) -> None:
        self.placements[model_id] = placement


def _assembly(parts: int = 2, triangles: int = 24) -> CadAssembly:
    """Smallest assembly the window will accept, with plausible counts."""
    nodes = tuple(
        CadNode(path=f"part{index}", mesh=f"{index}.npz", triangle_count=triangles // parts)
        for index in range(parts)
    )
    return CadAssembly(nodes=nodes, roots=(nodes[0].path,))


def _load(window: MainWindow, name: str = "gantry") -> ModelEntry:
    """Register a model as if an import had just finished."""
    return window.add_model(Path(f"C:/models/{name}.step"), _assembly(), Path("cache"))


@pytest.fixture
def window_with_viewport(qt_app: QApplication) -> Iterator[tuple[MainWindow, _StubViewport]]:
    viewport = _StubViewport()
    instance = MainWindow(viewport_factory=lambda: viewport)
    yield instance, viewport
    instance.close()


class TestListaPohladov:
    def test_lista_existuje(self, window: MainWindow) -> None:
        assert window.toolbar is not None

    def test_menu_ponuka_vsetky_standardne_pohlady(self, window: MainWindow) -> None:
        assert set(window.view_actions) == set(STANDARD_VIEWS)

    def test_poradie_zacina_izometriou(self, window: MainWindow) -> None:
        assert [action.text() for action in window.view_menu.actions()][0] == "Isometric"

    def test_menu_obsahuje_zadane_pohlady(self, window: MainWindow) -> None:
        labels = [action.text() for action in window.view_menu.actions()]

        assert {"Top", "Bottom", "Left", "Right", "Back", "Front"} <= set(labels)

    def test_tlacidlo_ma_rozbalovacie_menu(self, window: MainWindow) -> None:
        assert window.view_button.menu() is window.view_menu

    def test_menu_sa_rozbali_hned_po_kliknuti(self, window: MainWindow) -> None:
        # Bez InstantPopup by sa menu ukázalo až po podržaní tlačidla.
        assert window.view_button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup

    def test_kazdy_pohlad_ma_ikonu(self, window: MainWindow) -> None:
        assert all(not action.icon().isNull() for action in window.view_actions.values())

    def test_kazdy_pohlad_ma_skratku(self, window: MainWindow) -> None:
        assert all(not action.shortcut().isEmpty() for action in window.view_actions.values())

    def test_skratky_su_unikatne(self, window: MainWindow) -> None:
        shortcuts = [action.shortcut().toString() for action in window.view_actions.values()]

        assert len(set(shortcuts)) == len(shortcuts)

    def test_tlacidlo_zobraz_cele_ma_ikonu(self, window: MainWindow) -> None:
        assert not window.fit_action.icon().isNull()


class TestPrepnutiePohladu:
    def test_akcia_posle_pohlad_do_viewportu(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport

        window.view_actions["top"].trigger()

        assert viewport.views == ["top"]

    @pytest.mark.parametrize("name", sorted(STANDARD_VIEWS))
    def test_kazda_polozka_menu_funguje(
        self, window_with_viewport: tuple[MainWindow, _StubViewport], name: str
    ) -> None:
        window, viewport = window_with_viewport

        window.view_actions[name].trigger()

        assert viewport.views == [name]

    def test_stavovy_riadok_hlasi_pohlad(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        window.set_view("front")

        assert "front" in window.statusBar().currentMessage()

    def test_zobraz_cele_zavola_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        _load(window)

        window.fit_action.trigger()

        assert viewport.fit_calls == [window.models.selected_id]

    def test_zobraz_cele_bez_vyberu_rammuje_vsetko(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        _load(window)
        window.select_model(None)

        window.fit_view()

        assert viewport.fit_calls == [None]

    def test_viewport_bez_podpory_nespadne(self, window: MainWindow) -> None:
        # Náhradný widget v testoch `set_view` nemá — okno to musí prežiť.
        window.set_view("top")

        assert window.centralWidget() is not None


class TestModelTree:
    def test_tree_starts_empty(self, window: MainWindow) -> None:
        assert window.model_tree.topLevelItemCount() == 0

    def test_dock_is_on_the_left(self, window: MainWindow) -> None:
        assert window.dockWidgetArea(window.model_dock) == Qt.DockWidgetArea.LeftDockWidgetArea

    def test_loading_adds_a_row(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window)

        assert window.model_tree.topLevelItemCount() == 1

    def test_loading_twice_adds_two_rows(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window, "a")
        _load(window, "b")

        assert window.model_tree.topLevelItemCount() == 2

    def test_second_model_does_not_replace_the_first(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The whole point of the change: opening a file adds, it does not replace.
        window, viewport = window_with_viewport

        _load(window, "a")
        _load(window, "b")

        assert len(viewport.added) == 2
        assert len(window.models) == 2

    def test_same_file_twice_gets_distinct_names(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window, "bolt")
        _load(window, "bolt")

        assert window.models.names == ("bolt", "bolt (2)")

    def test_tree_shows_part_counts(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        item = window.model_tree.topLevelItem(0)

        assert item is not None
        assert item.text(1) == "2"

    def test_newly_loaded_model_is_selected(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        entry = _load(window)

        assert window.models.selected_id == entry.model_id


class TestSelection:
    def test_nothing_selected_at_start(self, window: MainWindow) -> None:
        assert window.selected_model is None

    def test_selection_highlights_in_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        first = _load(window, "a")
        _load(window, "b")

        window.select_model(first.model_id)

        assert viewport.highlighted == first.model_id

    def test_clearing_selection_clears_the_highlight(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        _load(window)

        window.select_model(None)

        assert viewport.highlighted is None

    def test_status_bar_names_the_selected_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        first = _load(window, "alpha")
        _load(window, "beta")

        window.select_model(first.model_id)

        assert "alpha" in window.statusBar().currentMessage()

    def test_code_selection_reaches_the_tree(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Selection can change from code too (a neighbour after removal). A row
        # highlighted in the tree must always be the one the app acts on.
        window, _ = window_with_viewport
        first = _load(window, "a")
        _load(window, "b")

        window.select_model(first.model_id)

        assert window.model_tree.selected_model_id == first.model_id

    def test_clearing_selection_clears_the_tree(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.select_model(None)

        assert window.model_tree.selected_model_id is None

    def test_removal_leaves_tree_and_registry_agreeing(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "a")
        _load(window, "b")

        window.remove_selected_model()

        assert window.model_tree.selected_model_id == window.models.selected_id

    def test_tree_selection_reaches_the_window(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        first = _load(window, "a")
        _load(window, "b")

        # `setCurrentItem` is what a click does; `setSelected` alone would not
        # deselect the other row, since single-selection is only enforced for
        # user interaction.
        first_item = window.model_tree.topLevelItem(0)
        assert first_item is not None
        window.model_tree.setCurrentItem(first_item)

        assert window.models.selected_id == first.model_id


class TestActionsFollowSelection:
    def test_placement_disabled_without_a_model(self, window: MainWindow) -> None:
        # No target means the action must be disabled, not silently do nothing.
        assert window.placement_action.isEnabled() is False

    def test_remove_disabled_without_a_model(self, window: MainWindow) -> None:
        assert window.remove_action.isEnabled() is False

    def test_fit_disabled_without_a_model(self, window: MainWindow) -> None:
        assert window.fit_action.isEnabled() is False

    def test_placement_enabled_once_a_model_is_selected(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window)

        assert window.placement_action.isEnabled() is True

    def test_remove_enabled_once_a_model_is_selected(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport

        _load(window)

        assert window.remove_action.isEnabled() is True

    def test_actions_disabled_again_when_selection_cleared(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.select_model(None)

        assert window.placement_action.isEnabled() is False
        assert window.remove_action.isEnabled() is False

    def test_dialog_not_opened_without_a_selection(self, window: MainWindow) -> None:
        assert window.open_placement_dialog() is None


class TestRemoving:
    def test_remove_drops_the_row(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.remove_action.trigger()

        assert window.model_tree.topLevelItemCount() == 0

    def test_remove_reaches_the_viewport(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window)

        window.remove_selected_model()

        assert viewport.removed == [entry.model_id]

    def test_remove_selects_a_neighbour(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window, "a")
        _load(window, "b")

        window.remove_selected_model()

        assert window.selected_model is not None

    def test_remove_without_selection_is_harmless(self, window: MainWindow) -> None:
        window.remove_selected_model()

        assert window.models.is_empty


class TestUmiestnenie:
    def test_menu_model_existuje(self, window: MainWindow) -> None:
        assert "Model" in menu_titles(window)

    def test_menu_model_obsahuje_polozky(self, window: MainWindow) -> None:
        assert menu_items(window, "Model") == ["Placement…", "Remove"]

    def test_polozka_ma_skratku(self, window: MainWindow) -> None:
        assert not window.placement_action.shortcut().isEmpty()

    def test_dialog_sa_otvori(self, window_with_viewport: tuple[MainWindow, _StubViewport]) -> None:
        window, _ = window_with_viewport
        _load(window)

        dialog = window.open_placement_dialog()

        assert dialog is not None
        assert dialog.isVisible()
        dialog.close()

    def test_dialog_pojmenuje_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # With several models loaded the dialog must say which one it edits.
        window, _ = window_with_viewport
        _load(window, "gantry")

        dialog = window.open_placement_dialog()

        assert dialog is not None
        assert "gantry" in dialog.windowTitle()
        dialog.close()

    def test_druhe_vyvolanie_nevytvori_dalsi_dialog(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)
        first = window.open_placement_dialog()

        second = window.open_placement_dialog()

        assert first is second
        assert first is not None
        first.close()

    def test_dialog_ukaze_aktualne_umiestnenie(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        entry = _load(window)
        window.models.set_placement(entry.model_id, Transform(xyz=(0.25, 0.0, 0.0)))

        dialog = window.open_placement_dialog()

        assert dialog is not None
        assert dialog.x_spin.value() == pytest.approx(250.0)
        dialog.close()

    def test_zmena_v_dialogu_dorazi_do_sceny(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window)
        dialog = window.open_placement_dialog()

        assert dialog is not None
        dialog.x_spin.setValue(500.0)

        assert viewport.placements[entry.model_id].xyz[0] == pytest.approx(0.5)
        dialog.close()

    def test_otocenie_dorazi_do_sceny_v_radianoch(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, viewport = window_with_viewport
        entry = _load(window)
        dialog = window.open_placement_dialog()

        assert dialog is not None
        dialog.rotate_z_spin.setValue(90.0)

        assert viewport.placements[entry.model_id].rpy[2] == pytest.approx(math.pi / 2)
        dialog.close()

    def test_umiestnenie_je_per_model(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # Moving one model must leave the other where it was.
        window, _ = window_with_viewport
        first = _load(window, "a")
        second = _load(window, "b")

        window.select_model(first.model_id)
        window.apply_placement(Transform(xyz=(1.0, 0.0, 0.0)))

        assert window.placement(first.model_id).xyz[0] == pytest.approx(1.0)
        assert window.placement(second.model_id).xyz[0] == pytest.approx(0.0)

    def test_zmena_vyberu_zavrie_dialog(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        # The dialog belongs to one model; leaving it open against another
        # selection would apply edits to the wrong one.
        window, _ = window_with_viewport
        first = _load(window, "a")
        second = _load(window, "b")
        window.select_model(first.model_id)
        dialog = window.open_placement_dialog()

        window.select_model(second.model_id)

        assert dialog is not None
        assert not dialog.isVisible()

    def test_stavovy_riadok_hlasi_umiestnenie_v_milimetroch(
        self, window_with_viewport: tuple[MainWindow, _StubViewport]
    ) -> None:
        window, _ = window_with_viewport
        _load(window)

        window.apply_placement(Transform(xyz=(0.1, 0.0, 0.0)))

        assert "100" in window.statusBar().currentMessage()

    def test_viewport_bez_podpory_nespadne(self, window: MainWindow) -> None:
        window.apply_placement(Transform(xyz=(1.0, 0.0, 0.0)))

        assert window.centralWidget() is not None


class TestNacitanie:
    def test_na_zaciatku_nic_nebezi(self, window: MainWindow) -> None:
        assert window.is_loading is False

    def test_pocas_nacitania_je_open_zakazane(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Súbeh dvoch importov by si prepísal cache aj scénu.
        monkeypatch.setattr("pssim.ui.main_window.StepImportThread", _StubThread)

        window.load_file(tmp_path / "stroj.step")

        assert window.open_action.isEnabled() is False

    def test_druhy_pokus_pocas_nacitania_sa_ignoruje(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pssim.ui.main_window.StepImportThread", _StubThread)

        window.load_file(tmp_path / "prvy.step")
        started = _StubThread.started_count
        window.load_file(tmp_path / "druhy.step")

        assert _StubThread.started_count == started

    def test_po_dokonceni_je_open_znovu_povolene(
        self, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("pssim.ui.main_window.StepImportThread", _StubThread)
        window.load_file(tmp_path / "stroj.step")

        window.on_import_finished()  # simuluje signál z vlákna

        assert window.open_action.isEnabled() is True
        assert window.is_loading is False
