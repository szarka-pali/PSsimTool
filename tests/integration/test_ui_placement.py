"""Tests of the model placement dialog.

The unit conversion is covered by `tests/unit/domain/test_placement.py`. This is about
the Qt side: whether the fields match the values, whether the live preview emits
changes, and whether `Cancel` really restores the original state.

They run headless through `QT_QPA_PLATFORM=offscreen`.
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


class TestFields:
    def test_there_are_three_translation_fields(self, dialog: PlacementDialog) -> None:
        assert (dialog.x_spin, dialog.y_spin, dialog.z_spin) != (None, None, None)

    def test_there_are_three_rotation_fields(self, dialog: PlacementDialog) -> None:
        assert (dialog.rotate_x_spin, dialog.rotate_y_spin, dialog.rotate_z_spin) != (
            None,
            None,
            None,
        )

    def test_translation_is_in_millimetres(self, dialog: PlacementDialog) -> None:
        # The user enters mm, not metres — otherwise they would type 0.001 for a millimetre.
        assert dialog.x_spin.suffix().strip() == "mm"

    def test_rotation_is_in_degrees(self, dialog: PlacementDialog) -> None:
        assert dialog.rotate_x_spin.suffix().strip() == "°"

    def test_translation_allows_negative_values(self, dialog: PlacementDialog) -> None:
        assert dialog.x_spin.minimum() < 0.0

    def test_rotation_wraps_around(self, dialog: PlacementDialog) -> None:
        # 360° should be followed by -360°, not by getting stuck at the maximum.
        assert dialog.rotate_z_spin.wrapping() is True

    def test_the_initial_state_is_zero(self, dialog: PlacementDialog) -> None:
        assert dialog.display.as_tuple == pytest.approx((0.0,) * 6)


class TestLoadingValues:
    def test_the_dialog_shows_the_given_placement(self, qt_app: QApplication) -> None:
        instance = PlacementDialog(to_transform(PlacementDisplay(x_mm=250.0)))

        assert instance.x_spin.value() == pytest.approx(250.0)
        instance.close()

    def test_metres_are_shown_as_millimetres(self, qt_app: QApplication) -> None:
        instance = PlacementDialog(Transform(xyz=(0.5, 0.0, 0.0)))

        assert instance.x_spin.value() == pytest.approx(500.0)
        instance.close()

    def test_radians_are_shown_as_degrees(self, qt_app: QApplication) -> None:
        instance = PlacementDialog(Transform(rpy=(0.0, 0.0, math.pi / 2)))

        assert instance.rotate_z_spin.value() == pytest.approx(90.0)
        instance.close()


class TestLivePreview:
    def test_zmena_pola_vysle_signal(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.x_spin.setValue(100.0)

        assert len(received) == 1

    def test_the_emitted_value_is_in_metres(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.x_spin.setValue(100.0)

        assert received[-1].xyz[0] == pytest.approx(0.1)

    def test_the_emitted_rotation_is_in_radians(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.rotate_y_spin.setValue(90.0)

        assert received[-1].rpy[1] == pytest.approx(math.pi / 2)

    def test_setting_all_fields_does_not_flicker(self, dialog: PlacementDialog) -> None:
        # Six fields would otherwise emit six signals and the scene would flicker
        # through meaningless positions.
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.set_placement(to_transform(PlacementDisplay(1.0, 2.0, 3.0, 4.0, 5.0, 6.0)))

        assert len(received) == 1

    def test_setting_all_fields_emits_the_result(self, dialog: PlacementDialog) -> None:
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)
        target = to_transform(PlacementDisplay(x_mm=10.0, rotate_z_deg=45.0))

        dialog.set_placement(target)

        assert received[-1].xyz == pytest.approx(target.xyz)
        assert received[-1].rpy == pytest.approx(target.rpy)


class TestButtons:
    def test_reset_returns_the_fields_to_zero(self, dialog: PlacementDialog) -> None:
        dialog.x_spin.setValue(123.0)
        dialog.rotate_z_spin.setValue(45.0)

        dialog.reset_placement()

        assert dialog.display.as_tuple == pytest.approx((0.0,) * 6)

    def test_reset_announces_the_change(self, dialog: PlacementDialog) -> None:
        dialog.x_spin.setValue(123.0)
        received: list[Transform] = []
        dialog.placement_changed.connect(received.append)

        dialog.reset_placement()

        assert received[-1].xyz == pytest.approx((0.0, 0.0, 0.0))

    def test_cancel_restores_the_original_state(self, qt_app: QApplication) -> None:
        original = to_transform(PlacementDisplay(x_mm=42.0))
        instance = PlacementDialog(original)
        received: list[Transform] = []
        instance.placement_changed.connect(received.append)

        instance.x_spin.setValue(999.0)
        instance.reject()

        assert received[-1].xyz == pytest.approx(original.xyz)

    def test_ok_keeps_the_last_value(self, qt_app: QApplication) -> None:
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
        # The `Cancel` and `Reset` strings are translated by Qt itself according to the
        # installed translation. Setting them by hand would leave them in English when
        # the language is switched while the rest of the dialog got translated.
        box = dialog.button_box

        assert box.button(QDialogButtonBox.StandardButton.Cancel).text() == "Cancel"
        assert box.button(QDialogButtonBox.StandardButton.Reset).text() == "Reset"
