"""The dialog for the floor's height above or below the scene origin.

One field, otherwise the same pattern as `ui/placement_dialog.py`: millimetres in,
a live preview, `Cancel` restores the height as it was when the dialog opened.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from pssim.domain.units import MM_TO_M
from pssim.ui.placement_dialog import (
    TRANSLATION_DECIMALS,
    TRANSLATION_LIMIT_MM,
    TRANSLATION_STEP_MM,
)


class FloorDialog(QDialog):
    """One field: the floor's height above the scene origin."""

    z_changed = Signal(float)
    """Emitted on every change. Carries the height in metres."""

    def __init__(
        self,
        current_z_m: float = 0.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Floor Position"))
        self.setModal(False)  # the live preview wants the scene to stay movable

        self._original_z_m = current_z_m
        self._emitting = True

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_form())
        layout.addWidget(self._build_buttons())

        self.set_z_m(current_z_m)

    # -- assembly -------------------------------------------------------------

    def _build_form(self) -> QFormLayout:
        form = QFormLayout()
        self.z_spin = QDoubleSpinBox(self)
        self.z_spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
        self.z_spin.setDecimals(TRANSLATION_DECIMALS)
        self.z_spin.setSingleStep(TRANSLATION_STEP_MM)
        self.z_spin.setSuffix(" mm")
        self.z_spin.valueChanged.connect(self._on_value_changed)
        form.addRow(self.tr("Z:"), self.z_spin)
        return form

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.button_box = buttons
        return buttons

    # -- values -----------------------------------------------------------------

    @property
    def z_m(self) -> float:
        """The current height in internal units."""
        return self.z_spin.value() * MM_TO_M

    def set_z_m(self, z_m: float) -> None:
        """Set the field. The signal is **not emitted** while it is being filled."""
        self._emitting = False
        try:
            self.z_spin.setValue(z_m / MM_TO_M)
        finally:
            self._emitting = True
        self.z_changed.emit(self.z_m)

    # -- events -----------------------------------------------------------------

    def _on_value_changed(self, _value: float) -> None:
        if self._emitting:
            self.z_changed.emit(self.z_m)

    def reject(self) -> None:
        """Cancelling restores the height as it was when the dialog opened."""
        self.z_changed.emit(self._original_z_m)
        super().reject()
