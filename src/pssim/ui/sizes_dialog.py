"""The dialog for how big the scene's markers and text are drawn.

Two numbers, both scene-wide: the arm length of every coordinate cross, and the
height of every piece of 3D text — the X/Y/Z glyphs on those crosses and the
joint name labels alike.

They are settings rather than rules because deriving them is what went wrong.
Each used to come from something different (the scene radius, a model's own
radius, a joint's span), so the origin cross ended up with 100 mm letters beside
a joint cross with 3.1 mm ones. See `viz.embed.DEFAULT_CROSS_SIZE_M`.

Mirrors `ui/floor_dialog.FloorDialog`: live preview as the numbers change, and
the previous values put back on cancel.
"""

from __future__ import annotations

from dataclasses import dataclass

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

#: Bounds for both spin boxes, in millimetres. Below a millimetre a marker is a
#: smudge; above ten metres it covers any machine it is meant to annotate.
MIN_SIZE_MM = 1.0
MAX_SIZE_MM = 10000.0


@dataclass(frozen=True, slots=True)
class Sizes:
    """The scene's three sizes, in metres.

    Bundled because three loose floats in a constructor and a signal is where the
    order stops being obvious — `code-style.md` caps a signature at four
    arguments for the same reason.
    """

    cross_size_m: float
    text_size_m: float
    origin_cross_size_m: float


class SizesDialog(QDialog):
    """Sets the cross size and the text size for the whole scene."""

    sizes_changed = Signal(float, float, float)
    """`(cross size, text size, origin cross size)` in **metres**, as any spin box
    moves. Metres because everything past this boundary is in metres; the
    millimetres stop here, the same rule `FloorDialog` follows."""

    def __init__(self, sizes: Sizes, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Sizes"))
        self._initial = sizes

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_form(sizes))
        layout.addWidget(self._build_buttons())

    def _build_form(self, sizes: Sizes) -> QFormLayout:
        form = QFormLayout()

        self.cross_spin = self._size_spin(sizes.cross_size_m)
        self.cross_spin.setToolTip(
            self.tr("The arm length of a selected model's cross and a joint's frame.")
        )
        form.addRow(self.tr("Item crosses:"), self.cross_spin)

        self.origin_spin = self._size_spin(sizes.origin_cross_size_m)
        self.origin_spin.setToolTip(
            self.tr("The arm length of the cross at the scene origin, on its own.")
        )
        form.addRow(self.tr("Origin cross:"), self.origin_spin)

        self.text_spin = self._size_spin(sizes.text_size_m)
        self.text_spin.setToolTip(
            self.tr("The height of the X/Y/Z letters and of every joint name.")
        )
        form.addRow(self.tr("Text height:"), self.text_spin)
        return form

    def _size_spin(self, size_m: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(MIN_SIZE_MM, MAX_SIZE_MM)
        spin.setDecimals(1)
        spin.setSingleStep(5.0)
        spin.setSuffix(self.tr(" mm"))
        spin.setValue(size_m / MM_TO_M)
        spin.valueChanged.connect(self._on_value_changed)
        return spin

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        return buttons

    @property
    def cross_size_m(self) -> float:
        return self.cross_spin.value() * MM_TO_M

    @property
    def text_size_m(self) -> float:
        return self.text_spin.value() * MM_TO_M

    @property
    def origin_cross_size_m(self) -> float:
        return self.origin_spin.value() * MM_TO_M

    @property
    def sizes(self) -> Sizes:
        return Sizes(
            cross_size_m=self.cross_size_m,
            text_size_m=self.text_size_m,
            origin_cross_size_m=self.origin_cross_size_m,
        )

    def _on_value_changed(self, _value: float) -> None:
        current = self.sizes
        self.sizes_changed.emit(
            current.cross_size_m, current.text_size_m, current.origin_cross_size_m
        )

    def reject(self) -> None:
        """Cancel puts every previous size back.

        The preview has already changed the scene by now, so cancelling has to
        undo it — otherwise "Cancel" would leave the change applied, which is the
        one thing a cancel must never do.
        """
        self.sizes_changed.emit(
            self._initial.cross_size_m,
            self._initial.text_size_m,
            self._initial.origin_cross_size_m,
        )
        super().reject()
