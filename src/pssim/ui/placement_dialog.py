"""The dialog for placing a model — translation and rotation relative to the origin.

Values are entered in **millimetres and degrees**, as is usual in CAD. The conversion into
internal metres and radians is done by `domain.placement`, not by this dialog — there is
only Qt here.

A change is reflected in the scene **immediately** (a live preview). `Cancel` restores the
state as it was on opening: without that, finding the right placement would be blind
guessing, because the result cannot be pictured from the numbers.
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

#: The range of the translation. 100 m each way covers even a large line and at the same
#: time stops a typo from sending the model so far away that it disappears.
TRANSLATION_LIMIT_MM: Final = 100_000.0
TRANSLATION_DECIMALS: Final = 3
TRANSLATION_STEP_MM: Final = 10.0

ROTATION_LIMIT_DEG: Final = 360.0
ROTATION_DECIMALS: Final = 2
ROTATION_STEP_DEG: Final = 15.0


class PlacementDialog(QDialog):
    """Six fields: translation in X/Y/Z and rotation about X/Y/Z."""

    placement_changed = Signal(object)
    """Emitted on every change. Carries a `domain.machine.Transform`."""

    def __init__(
        self,
        current: Transform = IDENTITY_PLACEMENT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Model Placement"))
        self.setModal(False)  # the live preview wants the scene to stay movable

        self._original = current
        self._emitting = True

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_translation_group())
        layout.addWidget(self._build_rotation_group())
        layout.addWidget(self._build_buttons())

        self.set_placement(current)

    # -- assembly -----------------------------------------------------------

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

        # `OK`, `Cancel` and `Reset` are translated by Qt itself according to the installed
        # translation, so they are not renamed here — otherwise they would stay in English
        # when the language is switched while the rest of the dialog got translated.
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
        # Rotation is cyclic — 360° should be followed by -360°, not by getting stuck.
        spin.setWrapping(True)
        spin.valueChanged.connect(self._on_value_changed)
        return spin

    # -- hodnoty ------------------------------------------------------------

    @property
    def display(self) -> PlacementDisplay:
        """The current values in the units the user sees."""
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
        """The current placement in internal units."""
        return to_transform(self.display)

    def set_placement(self, placement: Transform) -> None:
        """Set the fields. The signal is **not emitted** while they are being filled.

        Without suppressing it, six fields would emit six intermediate states and the scene
        would flicker through meaningless positions every time the dialog opened.
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
        """Return the model to the origin with no rotation."""
        self.set_placement(IDENTITY_PLACEMENT)

    # -- udalosti -----------------------------------------------------------

    def _on_value_changed(self, _value: float) -> None:
        if self._emitting:
            self.placement_changed.emit(self.placement)

    def reject(self) -> None:
        """Cancelling restores the state as it was when the dialog opened."""
        self.placement_changed.emit(self._original)
        super().reject()
