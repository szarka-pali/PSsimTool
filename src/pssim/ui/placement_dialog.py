"""Dialóg na umiestnenie modelu — posun a natočenie voči počiatku.

Zadáva sa v **milimetroch a stupňoch**, teda tak, ako je to zvykom v CAD.
Prevod na interné metre a radiány robí `domain.placement`, nie tento dialóg —
tu je len Qt.

Zmena sa premietne do scény **okamžite** (živý náhľad). `Zrušiť` vráti stav,
aký bol pri otvorení: bez toho by sa umiestnenie hľadalo po slepu, lebo
z čísel sa výsledok predstaviť nedá.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)

from pssim.domain.machine import Transform
from pssim.domain.placement import (
    IDENTITY_PLACEMENT,
    PlacementDisplay,
    from_transform,
    to_transform,
)

#: Rozsah posunu. 100 m na každú stranu pokryje aj veľkú linku a zároveň
#: zabráni tomu, aby preklep poslal model tak daleko, že zmizne.
TRANSLATION_LIMIT_MM: Final = 100_000.0
TRANSLATION_DECIMALS: Final = 3
TRANSLATION_STEP_MM: Final = 10.0

ROTATION_LIMIT_DEG: Final = 360.0
ROTATION_DECIMALS: Final = 2
ROTATION_STEP_DEG: Final = 15.0


class PlacementDialog(QDialog):
    """Šesť polí: posun v X/Y/Z a otočenie okolo X/Y/Z."""

    placement_changed = Signal(object)
    """Vyslaný pri každej zmene. Nesie `domain.machine.Transform`."""

    def __init__(
        self,
        current: Transform = IDENTITY_PLACEMENT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Model Placement"))
        self.setModal(False)  # živý náhľad chce, aby sa dalo hýbať aj scénou

        self._original = current
        self._emitting = True

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_translation_group())
        layout.addWidget(self._build_rotation_group())
        layout.addWidget(self._build_buttons())

        self.set_placement(current)

    # -- zloženie -----------------------------------------------------------

    def _build_translation_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("Translation"), self)
        form = QFormLayout(group)

        self.x_spin = self._translation_spin()
        self.y_spin = self._translation_spin()
        self.z_spin = self._translation_spin()

        form.addRow(self.tr("X:"), self.x_spin)
        form.addRow(self.tr("Y:"), self.y_spin)
        form.addRow(self.tr("Z:"), self.z_spin)
        return group

    def _build_rotation_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("Rotation"), self)
        form = QFormLayout(group)

        self.rotate_x_spin = self._rotation_spin()
        self.rotate_y_spin = self._rotation_spin()
        self.rotate_z_spin = self._rotation_spin()

        form.addRow(self.tr("about X:"), self.rotate_x_spin)
        form.addRow(self.tr("about Y:"), self.rotate_y_spin)
        form.addRow(self.tr("about Z:"), self.rotate_z_spin)
        return group

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # `OK`, `Cancel` a `Reset` prekladá Qt samo podľa nainštalovaného
        # prekladu, takže sa tu nepremenovávajú — inak by pri prepnutí jazyka
        # zostali v angličtine, kým zvyšok dialógu by sa preložil.
        reset = buttons.button(QDialogButtonBox.StandardButton.Reset)
        reset.clicked.connect(self.reset_placement)

        self.button_box = buttons
        return buttons

    def _translation_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
        spin.setDecimals(TRANSLATION_DECIMALS)
        spin.setSingleStep(TRANSLATION_STEP_MM)
        spin.setSuffix(" mm")
        spin.valueChanged.connect(self._on_value_changed)
        return spin

    def _rotation_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(-ROTATION_LIMIT_DEG, ROTATION_LIMIT_DEG)
        spin.setDecimals(ROTATION_DECIMALS)
        spin.setSingleStep(ROTATION_STEP_DEG)
        spin.setSuffix(" °")
        # Otočenie je cyklické — po 360° má nasledovať -360°, nie zaseknutie.
        spin.setWrapping(True)
        spin.valueChanged.connect(self._on_value_changed)
        return spin

    # -- hodnoty ------------------------------------------------------------

    @property
    def display(self) -> PlacementDisplay:
        """Aktuálne hodnoty v jednotkách, ktoré vidí používateľ."""
        return PlacementDisplay(
            x_mm=self.x_spin.value(),
            y_mm=self.y_spin.value(),
            z_mm=self.z_spin.value(),
            rotate_x_deg=self.rotate_x_spin.value(),
            rotate_y_deg=self.rotate_y_spin.value(),
            rotate_z_deg=self.rotate_z_spin.value(),
        )

    @property
    def placement(self) -> Transform:
        """Aktuálne umiestnenie v interných jednotkách."""
        return to_transform(self.display)

    def set_placement(self, placement: Transform) -> None:
        """Nastaví polia. Signál sa počas plnenia **nevysiela**.

        Bez potlačenia by šesť polí vyslalo šesť medzistavov a scéna by pri
        každom otvorení dialógu preblikla cez nezmyselné polohy.
        """
        display = from_transform(placement)
        self._emitting = False
        try:
            self.x_spin.setValue(display.x_mm)
            self.y_spin.setValue(display.y_mm)
            self.z_spin.setValue(display.z_mm)
            self.rotate_x_spin.setValue(display.rotate_x_deg)
            self.rotate_y_spin.setValue(display.rotate_y_deg)
            self.rotate_z_spin.setValue(display.rotate_z_deg)
        finally:
            self._emitting = True
        self.placement_changed.emit(self.placement)

    def reset_placement(self) -> None:
        """Vráti model do počiatku bez natočenia."""
        self.set_placement(IDENTITY_PLACEMENT)

    # -- udalosti -----------------------------------------------------------

    def _on_value_changed(self, _value: float) -> None:
        if self._emitting:
            self.placement_changed.emit(self.placement)

    def reject(self) -> None:
        """Zrušenie vráti stav, aký bol pri otvorení dialógu."""
        self.placement_changed.emit(self._original)
        super().reject()
