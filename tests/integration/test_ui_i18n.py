"""Tests of the translation mechanism and of the English UI strings.

The source language is English, so with no translation installed the application must
show the strings as they are written in the code. That is also the point of these
tests: a missing or broken translation **must not** bring the application down.

They run headless through `QT_QPA_PLATFORM=offscreen`.
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
    """A viewport without Panda3D. It must have `set_view`, or the window sets no message."""

    def set_view(self, name: str) -> None:
        self.last_view = name


@pytest.fixture
def window(qt_app: QApplication) -> Iterator[MainWindow]:
    instance = MainWindow(viewport_factory=_StubViewport)
    yield instance
    instance.close()


class TestLanguageRegistry:
    def test_the_source_language_is_english(self) -> None:
        assert SOURCE_LANGUAGE == "en"

    def test_the_source_language_is_always_available(self) -> None:
        # Needs no `.qm` file — the strings are written in the code.
        assert SOURCE_LANGUAGE in available_languages()

    def test_jazyky_su_pomenovane_vo_svojom_jazyku(self) -> None:
        # A future menu should show "Slovenčina", not "Slovak".
        assert LANGUAGES["sk"] == "Slovenčina"

    def test_dostupne_su_podmnozinou_registrovanych(self) -> None:
        assert set(available_languages()) <= set(LANGUAGES)

    def test_a_language_without_a_translation_is_unavailable(self) -> None:
        # `sk` is registered, but no `.qm` exists for it yet.
        if not translation_file("sk").is_file():
            assert "sk" not in available_languages()

    def test_the_translation_file_name(self) -> None:
        assert translation_file("de").name == "pssim_de.qm"


class TestInstallingATranslation:
    def test_zdrojovy_jazyk_nic_neinstaluje(self, qt_app: QApplication) -> None:
        assert install_translator(qt_app, SOURCE_LANGUAGE) is False

    def test_neznamy_jazyk_nespadne(self, qt_app: QApplication) -> None:
        # A typo in a language code must not bring down application startup.
        assert install_translator(qt_app, "klingon") is False

    def test_a_missing_translation_does_not_crash(self, qt_app: QApplication) -> None:
        assert install_translator(qt_app, "sk") is (translation_file("sk").is_file())

    def test_a_damaged_translation_file_does_not_crash(
        self, qt_app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = tmp_path / "pssim_sk.qm"
        broken.write_bytes(b"toto nie je qm subor")
        monkeypatch.setattr("pssim.ui.i18n.TRANSLATIONS_DIR", tmp_path)

        assert install_translator(qt_app, "sk") is False


class TestEnglishWindowText:
    def test_the_status_bar_is_in_english(self, window: MainWindow) -> None:
        assert window.statusBar().currentMessage() == "Ready"

    def test_the_toolbar_has_an_english_name(self, window: MainWindow) -> None:
        assert window.toolbar.windowTitle() == "View"

    def test_the_view_button_is_in_english(self, window: MainWindow) -> None:
        assert window.view_button.text() == "View"

    def test_fit_to_view_is_in_english(self, window: MainWindow) -> None:
        assert window.fit_action.text() == "Fit to view"

    def test_the_exit_status_tip_is_in_english(self, window: MainWindow) -> None:
        assert window.exit_action.statusTip() == "Quit the application"

    def test_switching_the_view_reports_in_english(self, window: MainWindow) -> None:
        window.set_view("top")

        assert window.statusBar().currentMessage() == "View: top"

    def test_the_file_filter_is_in_english(self) -> None:
        assert "CAD files" in cad_file_filter()
        assert "All files" in cad_file_filter()


class TestEnglishDialogText:
    def test_the_dialog_title(self, qt_app: QApplication) -> None:
        dialog = PlacementDialog()

        assert dialog.windowTitle() == "Model Placement"
        dialog.close()

    def test_the_groups_have_english_names(self, qt_app: QApplication) -> None:
        from PySide6.QtWidgets import QGroupBox

        dialog = PlacementDialog()
        titles = {group.title() for group in dialog.findChildren(QGroupBox)}
        dialog.close()

        assert titles == {"Translation", "Rotation"}


class TestTranslatableMessages:
    def test_the_identity_is_described_in_english(self) -> None:
        assert describe_placement(Transform()) == "Model at origin, no rotation"

    def test_the_description_states_millimetres_not_metres(self) -> None:
        # If the description showed 0.1, the user would go looking for the missing 100.
        text = describe_placement(to_transform(PlacementDisplay(x_mm=100.0)))

        assert "100" in text
        assert "mm" in text

    def test_popis_uvadza_stupne(self) -> None:
        text = describe_placement(to_transform(PlacementDisplay(rotate_z_deg=45.0)))

        assert "45" in text
        assert "°" in text

    def test_the_description_is_in_english(self) -> None:
        text = describe_placement(to_transform(PlacementDisplay(x_mm=1.0)))

        assert "Moved" in text
        assert "rotated" in text

    def test_a_whole_degree_of_rotation_is_not_rounded_badly(self) -> None:
        text = describe_placement(Transform(rpy=(0.0, 0.0, math.pi)))

        assert "180" in text

    def test_describing_a_model_with_no_assembly(self) -> None:
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

    def test_the_missing_geometry_suffix(self) -> None:
        assert "3" in missing_geometry_suffix(3)
        assert "geometry missing" in missing_geometry_suffix(3)
