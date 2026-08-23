"""The spin box + slider pair for driving one joint's live value.

The first `QSlider` anywhere in this codebase — every numeric input until now
has been a bare `QDoubleSpinBox`. The pure scale/unscale math is kept as free
functions (no Qt objects in their signature) so it is unit-testable without a
`QApplication`, the same trick `ui/model_registry.py`'s own logic already
relies on despite living in `ui/`.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QSlider, QWidget

from pssim.domain.model_joints import ModelJoint, ModelJointKind, effective_limits
from pssim.domain.units import DEG_TO_RAD, MM_TO_M
from pssim.ui.placement_dialog import ROTATION_DECIMALS, TRANSLATION_DECIMALS

#: The slider's own resolution — independent of the spin box's float range, and
#: fine enough that dragging it does not visibly jump between values.
SLIDER_STEPS: Final = 1000


def value_to_slider(value: float, low: float, high: float) -> int:
    """Map `value` in `[low, high]` onto a slider position in `[0, SLIDER_STEPS]`.

    `high <= low` (a zero-width range) maps everything to `0` rather than
    dividing by zero — a joint whose limits collapse to a single point has
    nothing for the slider to express anyway.
    """
    if high <= low:
        return 0
    fraction = max(0.0, min(1.0, (value - low) / (high - low)))
    return round(fraction * SLIDER_STEPS)


def slider_to_value(position: int, low: float, high: float) -> float:
    """The inverse of `value_to_slider`."""
    return low + (position / SLIDER_STEPS) * (high - low)


class JointValueRow(QWidget):
    """A name label, a spin box and a slider, all showing/editing one joint's
    live value in the units the user sees (degrees for `AXIS`, millimetres for
    `TRAJECTORY`).
    """

    value_edited = Signal(str, float)
    """Emitted only on a genuine user edit, from either widget. Carries
    `(joint_id, value)` — `value` in **internal** units (radians or metres),
    matching what `viewport.set_joint_value` expects, not what the spin box
    displays."""

    def __init__(
        self,
        joint_id: str,
        joint: ModelJoint,
        value: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._joint_id = joint_id
        self._is_axis = joint.kind is ModelJointKind.AXIS
        self._scale = DEG_TO_RAD if self._is_axis else MM_TO_M
        low, high = effective_limits(joint)
        self._low_display = low / self._scale
        self._high_display = high / self._scale
        self._emitting = True

        layout = QHBoxLayout(self)
        self.name_label = QLabel(joint.name, self)

        self.value_spin = QDoubleSpinBox(self)
        self.value_spin.setRange(self._low_display, self._high_display)
        self.value_spin.setDecimals(ROTATION_DECIMALS if self._is_axis else TRANSLATION_DECIMALS)
        self.value_spin.setSuffix(" °" if self._is_axis else " mm")

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, SLIDER_STEPS)

        layout.addWidget(self.name_label)
        layout.addWidget(self.value_spin)
        layout.addWidget(self.slider)

        self.set_value_silently(value)

        self.value_spin.valueChanged.connect(self._on_spin_changed)
        self.slider.valueChanged.connect(self._on_slider_changed)

    @property
    def joint_id(self) -> str:
        return self._joint_id

    def set_value_silently(self, value: float) -> None:
        """Set both widgets from an internal-units value without emitting
        `value_edited` — a programmatic refresh, not a user edit."""
        display_value = value / self._scale
        self._emitting = False
        try:
            self.value_spin.setValue(display_value)
            self.slider.setValue(
                value_to_slider(display_value, self._low_display, self._high_display)
            )
        finally:
            self._emitting = True

    # -- events -------------------------------------------------------------

    def _on_spin_changed(self, display_value: float) -> None:
        if not self._emitting:
            return
        self._set_slider_silently(display_value)
        self.value_edited.emit(self._joint_id, display_value * self._scale)

    def _on_slider_changed(self, position: int) -> None:
        if not self._emitting:
            return
        display_value = slider_to_value(position, self._low_display, self._high_display)
        self._set_spin_silently(display_value)
        self.value_edited.emit(self._joint_id, display_value * self._scale)

    def _set_slider_silently(self, display_value: float) -> None:
        self._emitting = False
        try:
            self.slider.setValue(
                value_to_slider(display_value, self._low_display, self._high_display)
            )
        finally:
            self._emitting = True

    def _set_spin_silently(self, display_value: float) -> None:
        self._emitting = False
        try:
            self.value_spin.setValue(display_value)
        finally:
            self._emitting = True
