"""The dialog for defining one axis or trajectory's geometry.

Modeless with a live preview, like `ui/placement_dialog.py` — seeing the marker
move as you type or pick a point is exactly as valuable here as it is for
placement. Unlike `ui/sensor_dialog.py`'s two different field groups (a beam's
target vs. a zone's half-extent), an axis and a trajectory are both just "two
points" — `origin` and `target` mean slightly different things per kind (a
point on the rotation line, vs. the far end of a straight path), but the same
six spin boxes serve both, so there is nothing to show or hide by kind, only
the target group's caption and the limit fields' unit.

Also holds `MountDialog`, a much smaller companion for attaching one model onto
another's joint — small enough that a separate file would be more ceremony
than the thing itself.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Vec3
from pssim.domain.model_joints import (
    ModelJoint,
    ModelJointDisplay,
    ModelJointKind,
    from_model_joint,
    to_model_joint,
)
from pssim.domain.units import MM_TO_M
from pssim.ui.placement_dialog import (
    ROTATION_DECIMALS,
    ROTATION_LIMIT_DEG,
    ROTATION_STEP_DEG,
    TRANSLATION_DECIMALS,
    TRANSLATION_LIMIT_MM,
    TRANSLATION_STEP_MM,
)

#: What a freshly opened "add" dialog starts with. Plain data (like a file stem
#: becoming a model's name), not UI text.
_DEFAULT_NAME: Final = "joint"

#: A direction is a ratio, not a length, so the spin boxes only need enough
#: range to express one comfortably. Public because the properties panel shows
#: the same field and the two must not drift, the same reason
#: `placement_dialog` exports its own limits.
DIRECTION_LIMIT: Final = 1000.0
DIRECTION_DECIMALS: Final = 3

#: A non-degenerate default target, 100 mm along Z from the default origin —
#: opening the dialog must not immediately be an invalid joint.
_DEFAULT_TARGET_MM: Final[Vec3] = (0.0, 0.0, 100.0)

_KIND_LABELS: Final[tuple[tuple[ModelJointKind, str], ...]] = (
    (ModelJointKind.AXIS, "Axis"),
    (ModelJointKind.TRAJECTORY, "Trajectory"),
)


class PickTarget(StrEnum):
    """Which point group a "Pick from view" button belongs to."""

    ORIGIN = "origin"
    TARGET = "target"


class JointDialog(QDialog):
    """Name, kind, the two defining points, and an optional limit."""

    joint_previewed = Signal(object)
    """Emitted on every change. Carries a `domain.model_joints.ModelJoint`, or
    nothing at all while the fields momentarily describe an invalid one (e.g.
    origin and target typed to the same point mid-edit)."""

    pick_requested = Signal(object)
    """A "Pick from view" button was clicked. Carries a `PickTarget`. This
    dialog has no reference to the viewport - whoever opened it arms picking
    and calls `set_point()` with the result."""

    def __init__(
        self,
        current: ModelJoint | None = None,
        initial_kind: ModelJointKind = ModelJointKind.AXIS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Joint"))
        self.setModal(False)  # the live preview wants the scene to stay movable

        self._emitting = True

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_name_row())
        layout.addWidget(self._build_point_group("origin"))
        layout.addWidget(self._build_point_group("target"))
        layout.addWidget(self._build_axis_group())
        layout.addWidget(self._build_limit_group())
        layout.addWidget(self._build_buttons())

        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        if current is None:
            initial = ModelJointDisplay(
                name=_DEFAULT_NAME,
                kind=initial_kind,
                variable=_DEFAULT_NAME,
                target_mm=_DEFAULT_TARGET_MM,
            )
        else:
            initial = from_model_joint(current)
        self.set_display(initial)

    # -- assembly -------------------------------------------------------------

    def _build_name_row(self) -> QFormLayout:
        form = QFormLayout()
        self.name_edit = QLineEdit(self)
        form.addRow(self.tr("Name:"), self.name_edit)

        self.variable_edit = QLineEdit(self)
        self.variable_edit.setToolTip(
            self.tr("A label for the live value - not connected to anything yet.")
        )
        form.addRow(self.tr("Variable:"), self.variable_edit)

        self.kind_combo = QComboBox(self)
        for _kind, label in _KIND_LABELS:
            self.kind_combo.addItem(self.tr(label))
        form.addRow(self.tr("Kind:"), self.kind_combo)
        return form

    def _build_point_group(self, field: str) -> QGroupBox:
        group = QGroupBox(self)
        form = QFormLayout()
        x_spin, y_spin, z_spin = (self._translation_spin() for _ in range(3))
        form.addRow(self.tr("X:"), x_spin)
        form.addRow(self.tr("Y:"), y_spin)
        form.addRow(self.tr("Z:"), z_spin)

        pick_button = QPushButton(self.tr("Pick from view…"), self)
        target = PickTarget.ORIGIN if field == "origin" else PickTarget.TARGET
        pick_button.clicked.connect(lambda: self.pick_requested.emit(target))

        outer = QVBoxLayout(group)
        outer.addLayout(form)
        outer.addWidget(pick_button)

        if field == "origin":
            self.origin_group = group
            self.origin_spins = (x_spin, y_spin, z_spin)
        else:
            self.target_group = group
            self.target_spins = (x_spin, y_spin, z_spin)
        return group

    def _build_axis_group(self) -> QGroupBox:
        """The fields only an axis has: which way it points, and where zero is.

        A separate group rather than re-labelling the target one, because the
        fields are genuinely different: a direction has no unit and no pick
        button (there is no point in the view to pick), and the init rotation is
        an angle rather than a coordinate. `_on_kind_changed` shows this group or
        the target group, never both.
        """
        group = QGroupBox(self.tr("Axis"), self)
        form = QFormLayout()

        self.direction_spins = tuple(self._direction_spin() for _ in range(3))
        form.addRow(self.tr("Direction X:"), self.direction_spins[0])
        form.addRow(self.tr("Direction Y:"), self.direction_spins[1])
        form.addRow(self.tr("Direction Z:"), self.direction_spins[2])

        self.initial_angle_spin = QDoubleSpinBox(self)
        self.initial_angle_spin.setRange(-ROTATION_LIMIT_DEG, ROTATION_LIMIT_DEG)
        self.initial_angle_spin.setDecimals(ROTATION_DECIMALS)
        self.initial_angle_spin.setSingleStep(ROTATION_STEP_DEG)
        self.initial_angle_spin.setSuffix(self.tr(" °"))
        self.initial_angle_spin.setToolTip(self.tr("The angle that counts as zero for this axis."))
        self.initial_angle_spin.valueChanged.connect(self._on_value_changed)
        form.addRow(self.tr("Init rotation:"), self.initial_angle_spin)

        outer = QVBoxLayout(group)
        outer.addLayout(form)
        self.axis_group = group
        return group

    def _direction_spin(self) -> QDoubleSpinBox:
        """A direction component: no unit, because only the direction matters.

        `(0,0,1)` and `(0,0,100)` are the same axis, so the numbers are read as
        a ratio and never as a length — which is also why there is no `mm`
        suffix to suggest otherwise.
        """
        spin = QDoubleSpinBox(self)
        spin.setRange(-DIRECTION_LIMIT, DIRECTION_LIMIT)
        spin.setDecimals(DIRECTION_DECIMALS)
        spin.setSingleStep(1.0)
        spin.valueChanged.connect(self._on_value_changed)
        return spin

    def _build_limit_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("Limit"), self)
        self.limit_checkbox = QCheckBox(self.tr("Restrict the range"), self)
        self.limit_checkbox.toggled.connect(self._on_limit_toggled)

        self.lower_limit_spin = QDoubleSpinBox(self)
        self.upper_limit_spin = QDoubleSpinBox(self)
        self.lower_limit_spin.setEnabled(False)
        self.upper_limit_spin.setEnabled(False)

        form = QFormLayout()
        form.addRow(self.tr("Lower:"), self.lower_limit_spin)
        form.addRow(self.tr("Upper:"), self.upper_limit_spin)

        outer = QVBoxLayout(group)
        outer.addWidget(self.limit_checkbox)
        outer.addLayout(form)
        return group

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        return buttons

    def _translation_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
        spin.setDecimals(TRANSLATION_DECIMALS)
        spin.setSingleStep(TRANSLATION_STEP_MM)
        spin.setSuffix(" mm")
        spin.valueChanged.connect(self._on_value_changed)
        return spin

    # -- values -----------------------------------------------------------------

    @property
    def kind(self) -> ModelJointKind:
        return _KIND_LABELS[self.kind_combo.currentIndex()][0]

    @property
    def display(self) -> ModelJointDisplay:
        """The current fields, in the units the user sees."""
        has_limit = self.limit_checkbox.isChecked()
        ox, oy, oz = (spin.value() for spin in self.origin_spins)
        tx, ty, tz = (spin.value() for spin in self.target_spins)
        dx, dy, dz = (spin.value() for spin in self.direction_spins)
        return ModelJointDisplay(
            name=self.name_edit.text().strip(),
            kind=self.kind,
            variable=self.variable_edit.text().strip(),
            origin_mm=(ox, oy, oz),
            target_mm=(tx, ty, tz),
            direction=(dx, dy, dz),
            initial_angle_deg=self.initial_angle_spin.value(),
            lower_limit=self.lower_limit_spin.value() if has_limit else None,
            upper_limit=self.upper_limit_spin.value() if has_limit else None,
        )

    @property
    def joint(self) -> ModelJoint:
        """The current fields as a domain `ModelJoint`.

        May raise `ConfigError` (an empty name/variable, or origin == target) —
        `accept()` is what catches it. Call this directly only once the fields
        are already known to be valid.
        """
        return to_model_joint(self.display)

    def set_display(self, display: ModelJointDisplay) -> None:
        """Fill every field from `display`, in the units the user sees.

        The signal is **not emitted** while the fields are being filled — one
        clean preview at the end, not one per field (six spin boxes would
        otherwise flicker through as many meaningless intermediate joints).
        """
        self._emitting = False
        try:
            self.name_edit.setText(display.name)
            self.variable_edit.setText(display.variable)
            self.kind_combo.setCurrentIndex(0 if display.kind is ModelJointKind.AXIS else 1)
            # Explicit, not relied on via the combo's own signal:
            # setCurrentIndex() above is a no-op (fires nothing) when the combo
            # is already sitting on that index, and the limit spins'
            # range/unit must be correct for this kind *before* their values
            # are set below, or a value like 180 degrees gets silently
            # clamped to Qt's default spin-box range of 0..99.99.
            self._on_kind_changed()

            for spin, value in zip(self.origin_spins, display.origin_mm, strict=True):
                spin.setValue(value)
            for spin, value in zip(self.target_spins, display.target_mm, strict=True):
                spin.setValue(value)
            for spin, value in zip(self.direction_spins, display.direction, strict=True):
                spin.setValue(value)
            self.initial_angle_spin.setValue(display.initial_angle_deg)

            has_limit = display.lower_limit is not None and display.upper_limit is not None
            self.limit_checkbox.setChecked(has_limit)
            if has_limit:
                assert display.lower_limit is not None
                assert display.upper_limit is not None
                self.lower_limit_spin.setValue(display.lower_limit)
                self.upper_limit_spin.setValue(display.upper_limit)
        finally:
            self._emitting = True
        self._emit_preview()

    def set_point(self, field: PickTarget, point_m: Vec3) -> None:
        """Write a picked point (metres, the model's own local frame) into the
        matching spin boxes as one clean change, not three intermediate ones."""
        x_mm, y_mm, z_mm = (component / MM_TO_M for component in point_m)
        spins = self.origin_spins if field is PickTarget.ORIGIN else self.target_spins
        self._emitting = False
        try:
            spins[0].setValue(x_mm)
            spins[1].setValue(y_mm)
            spins[2].setValue(z_mm)
        finally:
            self._emitting = True
        self._emit_preview()

    # -- events -------------------------------------------------------------

    def _on_kind_changed(self) -> None:
        """Switch the limit fields' unit and the target group's caption to
        match the current kind — the fields themselves stay the same six spin
        boxes either way."""
        is_axis = self.kind is ModelJointKind.AXIS
        suffix = self.tr(" °") if is_axis else self.tr(" mm")
        limit = ROTATION_LIMIT_DEG if is_axis else TRANSLATION_LIMIT_MM
        decimals = ROTATION_DECIMALS if is_axis else TRANSLATION_DECIMALS
        step = ROTATION_STEP_DEG if is_axis else TRANSLATION_STEP_MM
        for spin in (self.lower_limit_spin, self.upper_limit_spin):
            spin.setSuffix(suffix)
            spin.setRange(-limit, limit)
            spin.setDecimals(decimals)
            spin.setSingleStep(step)

        # An axis is a centre and a direction; a trajectory is two points. The
        # groups swap rather than the labels changing, because the fields
        # themselves differ — see `_build_axis_group`.
        self.origin_group.setTitle(self.tr("Centre point") if is_axis else self.tr("Origin"))
        self.target_group.setVisible(not is_axis)
        self.target_group.setTitle(self.tr("Target (end of the path)"))
        self.axis_group.setVisible(is_axis)
        self._emit_preview()

    def _on_limit_toggled(self, checked: bool) -> None:
        self.lower_limit_spin.setEnabled(checked)
        self.upper_limit_spin.setEnabled(checked)

    def _on_value_changed(self, _value: float) -> None:
        self._emit_preview()

    def _emit_preview(self) -> None:
        if not self._emitting:
            return
        try:
            joint = self.joint
        except ConfigError:
            return  # an in-progress edit has nothing valid to preview yet
        self.joint_previewed.emit(joint)

    def accept(self) -> None:
        """OK builds the joint before closing.

        A degenerate joint (a zero direction, or a trajectory whose two points
        coincide) or a blank name
        are the cases the spin boxes and a plain text field cannot prevent by
        range alone — caught here and reported, instead of raising out of a
        dialog.
        """
        try:
            _ = self.joint
        except ConfigError as exc:
            QMessageBox.warning(self, self.tr("Invalid joint"), str(exc))
            return
        super().accept()


#: `(joint_id, label)` pairs offered by a chooser. The label is the whole chain
#: down to that joint (`rail / head`), so two similarly named joints on
#: different branches are still tellable apart.
JointChoices = tuple[tuple[str, str], ...]


class BindDialog(QDialog):
    """Pick one joint from a flat list, or none.

    Used for both questions that amount to "which joint?": binding a model onto
    one, and choosing which joint carries another. One combo box rather than the
    two-step model-then-joint cascade this replaces — a joint no longer belongs
    to a model, so there is no first step left to make.
    """

    #: What the "not attached to anything" entry says. First in the list, so
    #: releasing is as reachable as binding.
    NONE_LABEL: Final = "— none —"

    def __init__(
        self,
        choices: JointChoices,
        current_joint_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """`choices` already excludes anything illegal — the caller filters out
        a joint's own descendants rather than having them offered and refused
        (see `ui.joint_registry.descendants_of`).
        """
        super().__init__(parent)
        self.setWindowTitle(self.tr("Bind To…"))
        self.setModal(True)

        self._joint_ids: tuple[str | None, ...] = (None, *(joint_id for joint_id, _ in choices))

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.joint_combo = QComboBox(self)
        self.joint_combo.addItem(self.tr(self.NONE_LABEL))
        for _joint_id, label in choices:
            self.joint_combo.addItem(label)
        form.addRow(self.tr("Joint:"), self.joint_combo)
        layout.addLayout(form)

        if current_joint_id in self._joint_ids:
            self.joint_combo.setCurrentIndex(self._joint_ids.index(current_joint_id))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_joint_id(self) -> str | None:
        """The chosen joint, or `None` for the "none" entry."""
        index = self.joint_combo.currentIndex()
        if 0 <= index < len(self._joint_ids):
            return self._joint_ids[index]
        return None
