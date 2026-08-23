"""The fields that describe a sensor, as one widget.

Two things need them: the modal `ui/sensor_dialog.py` used to place a sensor, and
the `Sensor` group in `ui/properties_panel.py` used to edit one already in the
scene. They are the same seven kinds with the same per-kind groups, so they are
built once here rather than twice — two copies would be two chances to disagree
about what a sensor's fields are, and the one that drifted would be the one
nobody was looking at.

The two consumers differ only in *when* they read the fields. The dialog reads
them once, on OK; the panel follows `fields_changed` and pushes every edit
straight into the scene. Hence both a `sensor` property that raises on a
half-finished definition and a `sensor_if_valid()` that reports `None` for it —
typing through an invalid state is normal, and must not throw out of a slot.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pssim.domain.errors import ConfigError
from pssim.domain.sensors import (
    ENCODER_KINDS,
    RAY_KINDS,
    Sensor,
    SensorDisplay,
    SensorKind,
    to_sensor,
)
from pssim.observability import get_logger
from pssim.ui.placement_dialog import (
    TRANSLATION_DECIMALS,
    TRANSLATION_LIMIT_MM,
    TRANSLATION_STEP_MM,
)

logger = get_logger(__name__)

#: The zone's smallest usable half-extent. Kept out of reach in the spin box
#: itself, rather than only in `Sensor.__post_init__` — the UI should not let a
#: value through that the domain would just reject.
MIN_HALF_EXTENT_MM: Final = 1.0

#: The smallest usable range for a ray sensor, for the same reason.
MIN_RANGE_MM: Final = 1.0

#: A direction is a ratio, not a length — the same bounds `joint_dialog` uses.
DIRECTION_LIMIT: Final = 1000.0
DIRECTION_DECIMALS: Final = 3

#: Encoder resolutions in real hardware run from a handful of counts to hundreds
#: of thousands; the spin box only has to cover that.
MAX_COUNTS_PER_REVOLUTION: Final = 1_000_000

#: (kind, label) in the order the combo box offers them. Laser and inductive read
#: the same, as do the two rangefinders — the label is what tells the reader which
#: part the machine actually has.
KINDS: Final[tuple[tuple[SensorKind, str], ...]] = (
    (SensorKind.BEAM, "Laser beam"),
    (SensorKind.INDUCTIVE, "Inductive"),
    (SensorKind.TOF, "Time of flight"),
    (SensorKind.LASER_DISTANCE, "Laser distance"),
    (SensorKind.ENCODER_INC, "Rotary encoder (incremental)"),
    (SensorKind.ENCODER_ABS, "Rotary encoder (absolute)"),
    (SensorKind.PROXIMITY, "Proximity zone"),
)

#: What a freshly opened "add" dialog starts with — a name is required, and this
#: is plain data (like a file stem becoming a model's name), not UI text.
DEFAULT_NAME: Final = "sensor"


def kind_index(kind: SensorKind) -> int:
    """Where `kind` sits in the combo box. Looked up rather than assumed: with
    seven kinds, an index arithmetic mistake would silently show the wrong one."""
    for index, (candidate, _label) in enumerate(KINDS):
        if candidate is kind:
            return index
    return 0


class SensorFields(QWidget):
    """Name, kind, and the fields that kind needs.

    Every field group stays built and filled underneath, even while hidden —
    flipping the kind combo never loses what was typed into the one not
    currently shown.
    """

    fields_changed = Signal()
    """Emitted on a genuine user edit of any field, including the kind. Not
    emitted by `set_display`, which is how a caller fills the widget in."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._emitting = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._build_name_row())
        layout.addWidget(self._build_origin_group())
        layout.addWidget(self._build_ray_group())
        layout.addWidget(self._build_zone_group())
        layout.addWidget(self._build_encoder_group())

        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        self.set_display(SensorDisplay(name=DEFAULT_NAME))

    # -- assembly -------------------------------------------------------------

    def _build_name_row(self) -> QFormLayout:
        form = QFormLayout()
        self.name_edit = QLineEdit(self)
        # `editingFinished`, not `textChanged`: a rename per keystroke would put
        # half-typed names through the registry's uniquifier and settle on
        # `ga (2)` before the user finished typing `gate`.
        self.name_edit.editingFinished.connect(self._report)
        form.addRow(self.tr("Name:"), self.name_edit)

        self.variable_edit = QLineEdit(self)
        self.variable_edit.setToolTip(
            self.tr("A label for the reading - not connected to anything yet.")
        )
        self.variable_edit.editingFinished.connect(self._report)
        form.addRow(self.tr("Variable:"), self.variable_edit)

        self.kind_combo = QComboBox(self)
        for _kind, label in KINDS:
            self.kind_combo.addItem(self.tr(label))
        self.kind_combo.currentIndexChanged.connect(self._report_index)
        form.addRow(self.tr("Kind:"), self.kind_combo)
        return form

    def _build_origin_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("Position (relative to what it is mounted on)"), self)
        self.origin_group = group
        form = QFormLayout(group)
        self.origin_x_spin = self._translation_spin()
        self.origin_y_spin = self._translation_spin()
        self.origin_z_spin = self._translation_spin()
        form.addRow(self.tr("X:"), self.origin_x_spin)
        form.addRow(self.tr("Y:"), self.origin_y_spin)
        form.addRow(self.tr("Z:"), self.origin_z_spin)
        return group

    def _build_ray_group(self) -> QGroupBox:
        """Which way the ray looks and how far it reaches.

        A direction rather than a second point: a ray has no length of its own,
        so a second point invited coordinates that looked meaningful and were
        not. The reach is a range, which is what the datasheet calls it.
        """
        self.ray_group = QGroupBox(self.tr("Ray"), self)
        form = QFormLayout(self.ray_group)

        self.direction_spins = tuple(self._direction_spin() for _ in range(3))
        form.addRow(self.tr("Direction X:"), self.direction_spins[0])
        form.addRow(self.tr("Direction Y:"), self.direction_spins[1])
        form.addRow(self.tr("Direction Z:"), self.direction_spins[2])

        self.range_spin = QDoubleSpinBox(self)
        self.range_spin.setRange(MIN_RANGE_MM, TRANSLATION_LIMIT_MM)
        self.range_spin.setDecimals(TRANSLATION_DECIMALS)
        self.range_spin.setSingleStep(TRANSLATION_STEP_MM)
        self.range_spin.setSuffix(" mm")
        self.range_spin.valueChanged.connect(self._report_value)
        form.addRow(self.tr("Range:"), self.range_spin)
        return self.ray_group

    def _build_encoder_group(self) -> QGroupBox:
        self.encoder_group = QGroupBox(self.tr("Encoder"), self)
        form = QFormLayout(self.encoder_group)
        self.counts_spin = QSpinBox(self)
        self.counts_spin.setRange(1, MAX_COUNTS_PER_REVOLUTION)
        self.counts_spin.setToolTip(
            self.tr("How many counts the encoder reports per full revolution.")
        )
        self.counts_spin.valueChanged.connect(self._report_index)
        form.addRow(self.tr("Counts per revolution:"), self.counts_spin)
        return self.encoder_group

    def _direction_spin(self) -> QDoubleSpinBox:
        """A direction component: no unit, because only the direction matters."""
        spin = QDoubleSpinBox(self)
        spin.setRange(-DIRECTION_LIMIT, DIRECTION_LIMIT)
        spin.setDecimals(DIRECTION_DECIMALS)
        spin.setSingleStep(1.0)
        spin.valueChanged.connect(self._report_value)
        return spin

    def _build_zone_group(self) -> QGroupBox:
        self.zone_group = QGroupBox(self.tr("Zone (proximity)"), self)
        form = QFormLayout(self.zone_group)
        self.half_extent_spin = QDoubleSpinBox(self)
        self.half_extent_spin.setRange(MIN_HALF_EXTENT_MM, TRANSLATION_LIMIT_MM)
        self.half_extent_spin.setDecimals(TRANSLATION_DECIMALS)
        self.half_extent_spin.setSingleStep(TRANSLATION_STEP_MM)
        self.half_extent_spin.setSuffix(" mm")
        self.half_extent_spin.valueChanged.connect(self._report_value)
        form.addRow(self.tr("Half-extent:"), self.half_extent_spin)
        return self.zone_group

    def _translation_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
        spin.setDecimals(TRANSLATION_DECIMALS)
        spin.setSingleStep(TRANSLATION_STEP_MM)
        spin.setSuffix(" mm")
        spin.valueChanged.connect(self._report_value)
        return spin

    # -- values -----------------------------------------------------------------

    @property
    def kind(self) -> SensorKind:
        return KINDS[self.kind_combo.currentIndex()][0]

    @property
    def display(self) -> SensorDisplay:
        """The current fields, in the units the user sees."""
        dx, dy, dz = (spin.value() for spin in self.direction_spins)
        return SensorDisplay(
            name=self.name_edit.text().strip(),
            kind=self.kind,
            variable=self.variable_edit.text().strip(),
            origin_mm=(
                self.origin_x_spin.value(),
                self.origin_y_spin.value(),
                self.origin_z_spin.value(),
            ),
            direction=(dx, dy, dz),
            range_mm=self.range_spin.value(),
            half_extent_mm=self.half_extent_spin.value(),
            counts_per_revolution=self.counts_spin.value(),
        )

    @property
    def sensor(self) -> Sensor:
        """The current fields as a domain `Sensor`.

        May raise `ConfigError` (an empty name, or a degenerate beam) — the
        dialog's `accept()` is what catches it. Anything editing live should use
        `sensor_if_valid()` instead.
        """
        return to_sensor(self.display)

    def sensor_if_valid(self) -> Sensor | None:
        """The current fields, or `None` when they do not form a valid sensor.

        A blank name or a zero-length direction is a state the fields can
        legitimately pass through while being edited — reported as `None` rather
        than raised, so typing never throws out of a slot. Mirrors
        `PropertiesPanel.joint()`.
        """
        try:
            return self.sensor
        except ConfigError as exc:
            logger.debug("sensor fields do not form a valid sensor yet", reason=str(exc))
            return None

    def set_display(self, display: SensorDisplay) -> None:
        """Fill every field from `display`, in the units the user sees.

        Silent: filling the widget in is not a user edit, so `fields_changed`
        stays quiet and a caller cannot get its own value echoed back at it.
        """
        self._emitting = False
        try:
            self.name_edit.setText(display.name)
            self.variable_edit.setText(display.variable)
            self.kind_combo.setCurrentIndex(kind_index(display.kind))
            self.origin_x_spin.setValue(display.origin_mm[0])
            self.origin_y_spin.setValue(display.origin_mm[1])
            self.origin_z_spin.setValue(display.origin_mm[2])
            for spin, value in zip(self.direction_spins, display.direction, strict=True):
                spin.setValue(value)
            self.range_spin.setValue(display.range_mm)
            self.half_extent_spin.setValue(display.half_extent_mm)
            self.counts_spin.setValue(display.counts_per_revolution)
        finally:
            self._emitting = True
        self._on_kind_changed()

    # -- events -------------------------------------------------------------

    def _on_kind_changed(self) -> None:
        """Show only the fields the current kind uses.

        Every group stays built and filled underneath, so flipping the combo
        never loses what was typed into one that is momentarily hidden.
        """
        kind = self.kind
        self.ray_group.setVisible(kind in RAY_KINDS)
        self.zone_group.setVisible(kind is SensorKind.PROXIMITY)
        self.encoder_group.setVisible(kind in ENCODER_KINDS)
        # An encoder reads the joint it is bolted to; it has no point in space of
        # its own, so its origin would be a field with nothing to mean.
        self.origin_group.setVisible(kind not in ENCODER_KINDS)

    def _report(self) -> None:
        if self._emitting:
            self.fields_changed.emit()

    def _report_value(self, _value: float) -> None:
        """`valueChanged` hands over the number; the signal carries none."""
        self._report()

    def _report_index(self, _index: int) -> None:
        self._report()
