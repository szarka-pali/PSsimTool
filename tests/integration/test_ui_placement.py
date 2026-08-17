"""Testy dialógu umiestnenia modelu.

Prevod jednotiek pokrýva `tests/unit/domain/test_placement.py`. Tu ide o Qt
stránku: či polia sedia s hodnotami, či živý náhľad vysiela zmeny a či
`Zrušiť` naozaj vráti pôvodný stav.

Bežia headless cez `QT_QPA_PLATFORM=offscreen`.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from pssim.domain.machine import Transform  # noqa: E402
from pssim.domain.placement import PlacementDisplay, to_transform  # noqa: E402
from pssim.ui.placement_dialog import PlacementDialog  # noqa: E402

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


@pytest.fixture
def dialog(qt_app: QApplication) -> Iterator[PlacementDialog]:
    instance = PlacementDialog()
    yield instance
    instance.close()


class TestPolia:
    def test_ma_tri_polia_posunu(self, dialog: PlacementDialog) -> None:
        assert (dialog.x_spin, dialog.y_spin, dialog.z_spin) != (None, None, None)

    def test_ma_tri_polia_otocenia(self, dialog: PlacementDialog) -> None:
        assert (dialog.rotate_x_spin, dialog.rotate_y_spin, dialog.rotate_z_spin) != (
            None,
            None,
            None,
        )

    def test_posun_je_v_milimetroch(self, dialog: PlacementDialog) -> None:
        # Používateľ zadáva mm, nie metre — inak by písal 0.001 pre milimeter.
        assert dialog.x_spin.suffix().strip() == "mm"

    def test_otocenie_je_v_stupnoch(self, dialog: PlacementDialog) -> None:
        assert dialog.rotate_x_spin.suffix().strip() == "°"

    def test_posun_dovoluje_zaporne_hodnoty(self, dialog: PlacementDialog) -> None:
        assert dialog.x_spin.minimum() < 0.0

    def test_otocenie_sa_zabaluje(self, dialog: PlacementDialog) -> None:
        # Po 360° má nasledovať -360°, nie zaseknutie na maxime.
        assert dialog.rotate_z_spin.wrapping() is True

    def test_zaciatocny_stav_je_nulovy(self, dialog: PlacementDialog) -> None:
        assert dialog.display.as_tuple == pytest.approx((0.0,) * 6)


class TestNacitanieHodnot:
    def test_dialog_ukaze_zadane_umiestnenie(self, qt_app: QApplication) -> None:
        instance = PlacementDialog(to_transform(PlacementDisplay(x_mm=250.0)))

        assert instance.x_spin.value() == pytest.approx(250.0)
        instance.close()

    def test_metre_sa_ukazu_ako_milimetre(self, qt_app: QApplication) -> None:
        instance = PlacementDialog(Transform(xyz=(0.5, 0.0, 0.0)))

        assert instance.x_spin.value() == pytest.approx(500.0)
        instance.close()

    def test_radiany_sa_ukazu_ako_stupne(self, qt_app: QApplication) -> None:
        instance = PlacementDialog(Transform(rpy=(0.0, 0.0, math.pi / 2)))

        assert instance.rotate_z_spin.value() == pytest.approx(90.0)
        instance.close()


class TestZivyNahlad:
    def test_zmena_pola_vysle_signal(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.x_spin.setValue(100.0)

        assert len(received) == 1

    def test_vyslana_hodnota_je_v_metroch(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.x_spin.setValue(100.0)

        assert received[-1].xyz[0] == pytest.approx(0.1)

    def test_vyslane_otocenie_je_v_radianoch(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.rotate_y_spin.setValue(90.0)

        assert received[-1].rpy[1] == pytest.approx(math.pi / 2)

    def test_hromadne_nastavenie_nepreblikne_medzistavmi(self, dialog: PlacementDialog) -> None:
        # Šesť polí by inak vyslalo šesť signálov a scéna by preblikla
        # cez nezmyselné polohy.
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.set_placement(to_transform(PlacementDisplay(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)))

        assert len(received) == 1

    def test_hromadne_nastavenie_vysle_vysledok(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)
        target = to_transform(PlacementDisplay(x_mm=10.0, rotate_z_deg=45.0))

        dialog.set_placement(target)

        assert received[-1].xyz == pytest.approx(target.xyz)
        assert received[-1].rpy == pytest.approx(target.rpy)


class TestTlacidla:
    def test_vynulovanie_vrati_polia_na_nulu(self, dialog: PlacementDialog) -> None:
        dialog.x_spin.setValue(123.0)
        dialog.rotate_z_spin.setValue(45.0)

        dialog.reset_placement()

        assert dialog.display.as_tuple == pytest.approx((0.0,) * 6)

    def test_vynulovanie_ohlasi_zmenu(self, dialog: PlacementDialog) -> None:
        dialog.x_spin.setValue(123.0)
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.reset_placement()

        assert received[-1].xyz == pytest.approx((0.0, 0.0, 0.0))

    def test_zrusenie_vrati_povodny_stav(self, qt_app: QApplication) -> None:
        original = to_transform(PlacementDisplay(x_mm=42.0))
        instance = PlacementDialog(original)
        received: list[Transform] = []
        instance.placement_changed.connect(received.append)

        instance.x_spin.setValue(999.0)
        instance.reject()

        assert received[-1].xyz == pytest.approx(original.xyz)

    def test_potvrdenie_nechá_poslednu_hodnotu(self, qt_app: QApplication) -> None:
        instance = PlacementDialog()
        instance.x_spin.setValue(77.0)

        instance.accept()

        assert instance.placement.xyz[0] == pytest.approx(0.077)

    def test_ma_tlacidla_ok_zrusit_vynulovat(self, dialog: PlacementDialog) -> None:
        box = dialog.button_box

        assert box.button(QDialogButtonBox.StandardButton.Ok) is not None
        assert box.button(QDialogButtonBox.StandardButton.Cancel) is not None
        assert box.button(QDialogButtonBox.StandardButton.Reset) is not None

    def test_standardne_tlacidla_neprepisujeme(self, dialog: PlacementDialog) -> None:
        # Texty `Cancel` a `Reset` prekladá Qt samo podľa nainštalovaného
        # prekladu. Keby sme ich nastavovali natvrdo, pri prepnutí jazyka by
        # zostali v angličtine, kým zvyšok dialógu by sa preložil.
        box = dialog.button_box

        assert box.button(QDialogButtonBox.StandardButton.Cancel).text() == "Cancel"
        assert box.button(QDialogButtonBox.StandardButton.Reset).text() == "Reset"
