"""Everything about the selected item, in one editable panel.

A model, an axis or trajectory, or a sensor — whichever of the three is selected.
The three are exclusive: the panel has one subject, and selecting in one tree is
what clears the selection in the other.

Docked on the right, the conventional place for properties in a CAD-like tool,
so the tree on the left and the properties stay visible at the same time.

The panel is the primary way to drive a joint's live value — a slider here moves
the model in the scene directly. `ui/model_values_panel.py` offers the same
control in a floating window (it can be dragged to a second monitor); both exist
deliberately, and the window keeps them in step through the `*_silently` setters
below so an edit in one never fights the other.

**Fixed widgets are built once and never destroyed** — only the per-joint rows
are rebuilt, and only when the set of joints actually changes. Rebuilding a spin
box or slider while the user is dragging or typing in it would swallow the edit,
which is exactly what a properties panel refreshed on every change would
otherwise do.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pssim.domain.errors import ConfigError
from pssim.domain.machine import Transform
from pssim.domain.model_joints import (
    ModelJoint,
    ModelJointDisplay,
    ModelJointKind,
    from_model_joint,
    to_model_joint,
)
from pssim.domain.placement import PlacementDisplay, from_transform, to_transform
from pssim.domain.sensors import from_sensor
from pssim.observability import get_logger
from pssim.ui.joint_dialog import DIRECTION_DECIMALS, DIRECTION_LIMIT
from pssim.ui.joint_registry import JointEntry
from pssim.ui.joint_value_row import JointValueRow
from pssim.ui.labels import (
    describe_reading,
    describe_state,
    describe_state_tooltip,
    is_reading_live,
    live_reading_color,
)
from pssim.ui.model_registry import ModelEntry
from pssim.ui.placement_dialog import (
    ROTATION_DECIMALS,
    ROTATION_LIMIT_DEG,
    ROTATION_STEP_DEG,
    TRANSLATION_DECIMALS,
    TRANSLATION_LIMIT_MM,
    TRANSLATION_STEP_MM,
)
from pssim.ui.sensor_fields import SensorFields
from pssim.ui.sensor_registry import SensorEntry

logger = get_logger(__name__)

#: Shown in a read-only field that has nothing to report.
_NONE_TEXT: Final = "—"

_KIND_LABELS: Final[dict[ModelJointKind, str]] = {
    ModelJointKind.AXIS: "Axis (rotation)",
    ModelJointKind.TRAJECTORY: "Trajectory (travel)",
}


#: `(item_id, label)` pairs a chooser offers. Same shape as `ui.joint_dialog`'s
#: `JointChoices`, and for the same reason: the label is for reading, the id is
#: what anything is keyed by.
MountChoices = tuple[tuple[str, str], ...]


class PropertiesPanel(QWidget):
    """The selected model, joint or sensor: what it is, and what can be changed."""

    name_edited = Signal(str, str)
    """`(model_id, new_name)` — on a genuine user edit only."""

    placement_edited = Signal(str, object)
    """`(model_id, Transform)` — carries internal units (metres, radians)."""

    joint_value_edited = Signal(str, float)
    """`(joint_id, value)` — internal units, matching `JointValueRow`'s own
    signal so the window can treat both sources identically."""

    joint_edited = Signal(str, object)
    """`(joint_id, ModelJoint)` — the joint's own definition (name, variable,
    the two points, limits) after an edit in joint mode. Separate from
    `joint_value_edited`: that one drives an existing joint, this one redefines
    it."""

    sensor_edited = Signal(str, object)
    """`(sensor_id, Sensor)` — the sensor's own definition after an edit in
    sensor mode, in internal units. Live, like `joint_edited`."""

    sensor_mount_edited = Signal(str, object)
    """`(sensor_id, mount_id | None)` — what carries the sensor. Separate from
    `sensor_edited` because a mount is not part of the sensor: it is a model or
    joint id, which `domain.sensors.Sensor` knows nothing about."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._model_id: str | None = None
        self._joint_id: str | None = None
        self._sensor_id: str | None = None
        """Exactly one of the three is set. Which one decides which groups are
        visible; a click in any of the trees clears the other two."""
        self._shown_sensor: tuple[str, object] | None = None
        """Which sensor the fields were last filled from. Compared before
        re-filling, so a refresh caused by something else does not overwrite
        what is being typed — the same guard `_set_rows` uses for the rows."""
        self._mount_ids: tuple[str | None, ...] = (None,)
        self._rows_model_id: str | None = None
        """Which model the current rows were built for. Kept separately from
        `_model_id` so the rebuild check can see a model *change*, not just a
        change of joint ids — a row captures its joint's kind and limits when
        it is built, so a row from another model would show the wrong units
        and range even if the ids happened to line up."""
        self._shown_joints: tuple[tuple[str, ModelJoint], ...] = ()
        self._rows: dict[str, JointValueRow] = {}
        self._joint_row: JointValueRow | None = None
        self._joint_row_key: tuple[str, ModelJoint] | None = None
        self._emitting = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        self._content = QWidget(scroll)
        self._layout = QVBoxLayout(self._content)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)

        self._placeholder = QLabel(self.tr("Nothing selected"), self._content)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setEnabled(False)
        self._layout.addWidget(self._placeholder)

        self._identity_group = self._build_identity_group()
        self._layout.addWidget(self._identity_group)

        self._placement_group = self._build_placement_group()
        self._layout.addWidget(self._placement_group)

        self._variables_group = self._build_variables_group()
        self._layout.addWidget(self._variables_group)

        self._joint_group = self._build_joint_group()
        self._layout.addWidget(self._joint_group)

        self._sensor_group = self._build_sensor_group()
        self._layout.addWidget(self._sensor_group)

        self.setMinimumWidth(260)
        self.clear()

    # -- assembly -------------------------------------------------------------

    def _build_identity_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("Model"), self._content)
        form = QFormLayout(group)

        self.name_edit = QLineEdit(group)
        self.name_edit.editingFinished.connect(self._on_name_finished)
        form.addRow(self.tr("Name:"), self.name_edit)

        self.file_label = QLabel(group)
        # A CAD path is far wider than the dock; the full one lives in the
        # tooltip so the column never forces the dock wider.
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(self.tr("File:"), self.file_label)

        self.parts_label = QLabel(group)
        form.addRow(self.tr("Parts:"), self.parts_label)

        self.triangles_label = QLabel(group)
        form.addRow(self.tr("Triangles:"), self.triangles_label)

        self.bound_to_label = QLabel(group)
        form.addRow(self.tr("Bound to:"), self.bound_to_label)
        return group

    def _build_placement_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("Placement"), self._content)
        form = QFormLayout(group)

        self.x_spin = self._translation_spin(group)
        self.y_spin = self._translation_spin(group)
        self.z_spin = self._translation_spin(group)
        form.addRow(self.tr("X:"), self.x_spin)
        form.addRow(self.tr("Y:"), self.y_spin)
        form.addRow(self.tr("Z:"), self.z_spin)

        self.rotate_x_spin = self._rotation_spin(group)
        self.rotate_y_spin = self._rotation_spin(group)
        self.rotate_z_spin = self._rotation_spin(group)
        form.addRow(self.tr("Rotate X:"), self.rotate_x_spin)
        form.addRow(self.tr("Rotate Y:"), self.rotate_y_spin)
        form.addRow(self.tr("Rotate Z:"), self.rotate_z_spin)
        return group

    def _build_variables_group(self) -> QGroupBox:
        group = QGroupBox(self.tr("Variables"), self._content)
        self._variables_layout = QVBoxLayout(group)
        self._no_variables_label = QLabel(self.tr("No axes or trajectories defined"), group)
        self._no_variables_label.setEnabled(False)
        self._variables_layout.addWidget(self._no_variables_label)
        return group

    def _build_joint_group(self) -> QGroupBox:
        """The fields shown when a joint row — an axis or a trajectory — is
        what is selected, rather than a model."""
        group = QGroupBox(self.tr("Axis / Trajectory"), self._content)
        outer = QVBoxLayout(group)

        form = QFormLayout()
        self.joint_name_edit = QLineEdit(group)
        self.joint_name_edit.editingFinished.connect(self._on_joint_definition_finished)
        form.addRow(self.tr("Name:"), self.joint_name_edit)

        self.joint_kind_label = QLabel(group)
        form.addRow(self.tr("Kind:"), self.joint_kind_label)

        self.joint_variable_edit = QLineEdit(group)
        self.joint_variable_edit.editingFinished.connect(self._on_joint_definition_finished)
        form.addRow(self.tr("Variable:"), self.joint_variable_edit)

        self.joint_parent_label = QLabel(group)
        form.addRow(self.tr("Carried by:"), self.joint_parent_label)
        outer.addLayout(form)

        self.joint_origin_group = QGroupBox(self.tr("Origin"), group)
        origin_form = QFormLayout(self.joint_origin_group)
        self.joint_origin_x_spin = self._joint_point_spin(self.joint_origin_group)
        self.joint_origin_y_spin = self._joint_point_spin(self.joint_origin_group)
        self.joint_origin_z_spin = self._joint_point_spin(self.joint_origin_group)
        origin_form.addRow(self.tr("X:"), self.joint_origin_x_spin)
        origin_form.addRow(self.tr("Y:"), self.joint_origin_y_spin)
        origin_form.addRow(self.tr("Z:"), self.joint_origin_z_spin)
        outer.addWidget(self.joint_origin_group)

        self.joint_target_group = QGroupBox(self.tr("Target"), group)
        target_form = QFormLayout(self.joint_target_group)
        self.joint_target_x_spin = self._joint_point_spin(self.joint_target_group)
        self.joint_target_y_spin = self._joint_point_spin(self.joint_target_group)
        self.joint_target_z_spin = self._joint_point_spin(self.joint_target_group)
        target_form.addRow(self.tr("X:"), self.joint_target_x_spin)
        target_form.addRow(self.tr("Y:"), self.joint_target_y_spin)
        target_form.addRow(self.tr("Z:"), self.joint_target_z_spin)
        outer.addWidget(self.joint_target_group)
        outer.addWidget(self._build_axis_group(group))

        self.joint_limit_checkbox = QCheckBox(self.tr("Restrict the range"), group)
        self.joint_limit_checkbox.toggled.connect(self._on_joint_limit_toggled)
        outer.addWidget(self.joint_limit_checkbox)

        limit_form = QFormLayout()
        self.joint_lower_limit_spin = QDoubleSpinBox(group)
        self.joint_upper_limit_spin = QDoubleSpinBox(group)
        for spin in (self.joint_lower_limit_spin, self.joint_upper_limit_spin):
            spin.setEnabled(False)
            spin.valueChanged.connect(self._on_joint_definition_changed)
        limit_form.addRow(self.tr("Lower:"), self.joint_lower_limit_spin)
        limit_form.addRow(self.tr("Upper:"), self.joint_upper_limit_spin)
        outer.addLayout(limit_form)
        self.frame_group = self._build_initial_frame_group(group)
        outer.addWidget(self.frame_group)

        self._joint_value_layout = QVBoxLayout()
        outer.addLayout(self._joint_value_layout)
        return group

    def _build_sensor_group(self) -> QGroupBox:
        """The fields shown when a sensor is what is selected.

        The editable half is `ui/sensor_fields.SensorFields`, the same widget the
        `Sensor` dialog is built from — one definition of what a sensor's fields
        are, so the two cannot drift apart.
        """
        group = QGroupBox(self.tr("Sensor"), self._content)
        outer = QVBoxLayout(group)

        self.sensor_fields = SensorFields(group)
        self.sensor_fields.fields_changed.connect(self._on_sensor_changed)
        outer.addWidget(self.sensor_fields)

        form = QFormLayout()
        self.sensor_mount_combo = QComboBox(group)
        self.sensor_mount_combo.setToolTip(
            self.tr(
                "The model or axis carrying the sensor. Its position and direction "
                "are in that thing's frame, so it rides along when the mount moves."
            )
        )
        self.sensor_mount_combo.currentIndexChanged.connect(self._on_sensor_mount_changed)
        form.addRow(self.tr("Mounted on:"), self.sensor_mount_combo)

        # Read-only: these are what the scene last measured, not something to type.
        self.sensor_state_label = QLabel(group)
        form.addRow(self.tr("State:"), self.sensor_state_label)

        self.sensor_reading_label = QLabel(group)
        # Filled so a background can be painted at all; the colour itself is set
        # per reading, and cleared again when the sensor stops measuring.
        self.sensor_reading_label.setAutoFillBackground(True)
        form.addRow(self.tr("Reading:"), self.sensor_reading_label)
        outer.addLayout(form)
        return group

    def _build_axis_group(self, parent: QWidget) -> QGroupBox:
        """The fields only an axis has: which way it turns, and where zero is.

        The direction carries no unit because only its direction matters —
        `(0,0,1)` and `(0,0,100)` are the same axis. Shown instead of the target
        group, never alongside it.
        """
        group = QGroupBox(self.tr("Axis"), parent)
        form = QFormLayout(group)

        self.joint_direction_x_spin = self._direction_spin(group)
        self.joint_direction_y_spin = self._direction_spin(group)
        self.joint_direction_z_spin = self._direction_spin(group)
        form.addRow(self.tr("Direction X:"), self.joint_direction_x_spin)
        form.addRow(self.tr("Direction Y:"), self.joint_direction_y_spin)
        form.addRow(self.tr("Direction Z:"), self.joint_direction_z_spin)

        self.joint_initial_angle_spin = self._frame_rotation_spin(group)
        self.joint_initial_angle_spin.setToolTip(
            self.tr("The angle that counts as zero for this axis.")
        )
        form.addRow(self.tr("Init rotation:"), self.joint_initial_angle_spin)

        self.joint_axis_group = group
        return group

    def _direction_spin(self, parent: QWidget) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(-DIRECTION_LIMIT, DIRECTION_LIMIT)
        spin.setDecimals(DIRECTION_DECIMALS)
        spin.setSingleStep(1.0)
        spin.valueChanged.connect(self._on_joint_definition_changed)
        return spin

    def _build_initial_frame_group(self, parent: QWidget) -> QGroupBox:
        """The joint's **initial coordinate system** — what a bound model aligns to.

        Its axes are the joint's own, not the world's: `+Z` runs along the
        trajectory (or along the rotation axis), so the labels say so. Left at
        zero it means "at the start, pointing the way the joint goes", which is
        the default a trajectory should have.
        """
        group = QGroupBox(self.tr("Initial frame (a bound model aligns to this)"), parent)
        form = QFormLayout(group)

        self.frame_x_spin = self._frame_translation_spin(group)
        self.frame_y_spin = self._frame_translation_spin(group)
        self.frame_z_spin = self._frame_translation_spin(group)
        form.addRow(self.tr("X:"), self.frame_x_spin)
        form.addRow(self.tr("Y:"), self.frame_y_spin)
        form.addRow(self.tr("Z (along the joint):"), self.frame_z_spin)

        self.frame_rotate_x_spin = self._frame_rotation_spin(group)
        self.frame_rotate_y_spin = self._frame_rotation_spin(group)
        self.frame_rotate_z_spin = self._frame_rotation_spin(group)
        form.addRow(self.tr("Rotate X:"), self.frame_rotate_x_spin)
        form.addRow(self.tr("Rotate Y:"), self.frame_rotate_y_spin)
        form.addRow(self.tr("Roll (about the joint):"), self.frame_rotate_z_spin)
        return group

    def _frame_translation_spin(self, parent: QWidget) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
        spin.setDecimals(TRANSLATION_DECIMALS)
        spin.setSingleStep(TRANSLATION_STEP_MM)
        spin.setSuffix(" mm")
        spin.valueChanged.connect(self._on_joint_definition_changed)
        return spin

    def _frame_rotation_spin(self, parent: QWidget) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(-ROTATION_LIMIT_DEG, ROTATION_LIMIT_DEG)
        spin.setDecimals(ROTATION_DECIMALS)
        spin.setSingleStep(ROTATION_STEP_DEG)
        spin.setSuffix(" °")
        spin.setWrapping(True)
        spin.valueChanged.connect(self._on_joint_definition_changed)
        return spin

    def _joint_point_spin(self, parent: QWidget) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
        spin.setDecimals(TRANSLATION_DECIMALS)
        spin.setSingleStep(TRANSLATION_STEP_MM)
        spin.setSuffix(" mm")
        spin.valueChanged.connect(self._on_joint_definition_changed)
        return spin

    def _translation_spin(self, parent: QWidget) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
        spin.setDecimals(TRANSLATION_DECIMALS)
        spin.setSingleStep(TRANSLATION_STEP_MM)
        spin.setSuffix(" mm")
        spin.valueChanged.connect(self._on_placement_changed)
        return spin

    def _rotation_spin(self, parent: QWidget) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(-ROTATION_LIMIT_DEG, ROTATION_LIMIT_DEG)
        spin.setDecimals(ROTATION_DECIMALS)
        spin.setSingleStep(ROTATION_STEP_DEG)
        spin.setSuffix(" °")
        spin.setWrapping(True)
        spin.valueChanged.connect(self._on_placement_changed)
        return spin

    # -- what is shown ----------------------------------------------------------

    @property
    def model_id(self) -> str | None:
        """The model currently shown, or `None` in joint mode or when empty."""
        return self._model_id

    @property
    def joint_id(self) -> str | None:
        """The joint currently shown, or `None` in another mode or when empty."""
        return self._joint_id

    @property
    def sensor_id(self) -> str | None:
        """The sensor currently shown, or `None` in another mode or when empty."""
        return self._sensor_id

    def clear(self) -> None:
        """Show the placeholder — nothing is selected."""
        self._model_id = None
        self._joint_id = None
        self._sensor_id = None
        self._shown_sensor = None
        self._set_rows(None, ())
        self._placeholder.setVisible(True)
        self._identity_group.setVisible(False)
        self._placement_group.setVisible(False)
        self._variables_group.setVisible(False)
        self._joint_group.setVisible(False)
        self._sensor_group.setVisible(False)

    def show_model(
        self,
        entry: ModelEntry,
        joint_entries: tuple[JointEntry, ...],
        bound_to_name: str | None = None,
    ) -> None:
        """Render one model's properties.

        Safe to call repeatedly for the same model: the fixed fields are updated
        in place and the joint rows are only rebuilt when the set of joints
        actually differs, so an in-progress drag or edit survives.
        """
        self._model_id = entry.model_id
        self._joint_id = None
        self._sensor_id = None
        self._placeholder.setVisible(False)
        self._identity_group.setVisible(True)
        self._placement_group.setVisible(True)
        self._variables_group.setVisible(True)
        self._joint_group.setVisible(False)
        self._sensor_group.setVisible(False)

        self._emitting = False
        try:
            self.name_edit.setText(entry.name)
            self.file_label.setText(entry.path.name)
            self.file_label.setToolTip(str(entry.path))
            self.parts_label.setText(str(entry.node_count))
            self.triangles_label.setText(f"{entry.triangle_count:,}".replace(",", " "))
            self.bound_to_label.setText(bound_to_name or _NONE_TEXT)
            self._apply_placement_fields(entry.placement)
        finally:
            self._emitting = True

        self._set_rows(entry.model_id, joint_entries)

    def show_joint(self, entry: JointEntry, carried_by: str | None = None) -> None:
        """Render one axis or trajectory's own properties.

        Replaces the model view rather than adding to it: clicking a trajectory
        asks about the trajectory, and leaving the model's fields on screen was
        the thing that made it look like the click had not registered.
        """
        self._model_id = None
        self._joint_id = entry.joint_id
        self._sensor_id = None
        self._placeholder.setVisible(False)
        self._identity_group.setVisible(False)
        self._placement_group.setVisible(False)
        self._variables_group.setVisible(False)
        self._joint_group.setVisible(True)
        self._sensor_group.setVisible(False)

        joint = entry.joint
        display = from_model_joint(joint)
        is_axis = joint.kind is ModelJointKind.AXIS

        self._emitting = False
        try:
            self.joint_name_edit.setText(joint.name)
            self.joint_kind_label.setText(self.tr(_KIND_LABELS[joint.kind]))
            self.joint_variable_edit.setText(joint.variable)
            self.joint_parent_label.setText(carried_by or _NONE_TEXT)

            # An axis is a centre plus a direction, a trajectory two points, so
            # the groups swap rather than one caption changing. An axis has no
            # initial frame either — its `Init rotation` is the one degree of
            # freedom that needed naming there.
            self.joint_origin_group.setTitle(
                self.tr("Centre point") if is_axis else self.tr("Origin")
            )
            self.joint_target_group.setTitle(self.tr("End of the path"))
            self.joint_target_group.setVisible(not is_axis)
            self.joint_axis_group.setVisible(is_axis)
            self.frame_group.setVisible(not is_axis)

            self.joint_origin_x_spin.setValue(display.origin_mm[0])
            self.joint_origin_y_spin.setValue(display.origin_mm[1])
            self.joint_origin_z_spin.setValue(display.origin_mm[2])
            self.joint_target_x_spin.setValue(display.target_mm[0])
            self.joint_target_y_spin.setValue(display.target_mm[1])
            self.joint_target_z_spin.setValue(display.target_mm[2])
            self.joint_direction_x_spin.setValue(display.direction[0])
            self.joint_direction_y_spin.setValue(display.direction[1])
            self.joint_direction_z_spin.setValue(display.direction[2])
            self.joint_initial_angle_spin.setValue(display.initial_angle_deg)

            self.frame_x_spin.setValue(display.alignment.x_mm)
            self.frame_y_spin.setValue(display.alignment.y_mm)
            self.frame_z_spin.setValue(display.alignment.z_mm)
            self.frame_rotate_x_spin.setValue(display.alignment.rotate_x_deg)
            self.frame_rotate_y_spin.setValue(display.alignment.rotate_y_deg)
            self.frame_rotate_z_spin.setValue(display.alignment.rotate_z_deg)

            self._configure_limit_spins(is_axis)
            has_limit = display.lower_limit is not None and display.upper_limit is not None
            self.joint_limit_checkbox.setChecked(has_limit)
            self.joint_lower_limit_spin.setEnabled(has_limit)
            self.joint_upper_limit_spin.setEnabled(has_limit)
            if has_limit:
                assert display.lower_limit is not None
                assert display.upper_limit is not None
                self.joint_lower_limit_spin.setValue(display.lower_limit)
                self.joint_upper_limit_spin.setValue(display.upper_limit)
        finally:
            self._emitting = True

        self._set_joint_row(entry)

    def show_sensor(
        self,
        entry: SensorEntry,
        mount_choices: MountChoices = (),
        mount_name: str | None = None,
    ) -> None:
        """Render one sensor's properties, editable in place.

        Safe to call repeatedly for the same sensor: the fields are only re-filled
        when the sensor's own definition differs, so a refresh caused by something
        else — a reading changing, say — never overwrites what is being typed.
        """
        self._model_id = None
        self._joint_id = None
        self._sensor_id = entry.sensor_id
        self._placeholder.setVisible(False)
        self._identity_group.setVisible(False)
        self._placement_group.setVisible(False)
        self._variables_group.setVisible(False)
        self._joint_group.setVisible(False)
        self._sensor_group.setVisible(True)

        key = (entry.sensor_id, entry.sensor)
        if key != self._shown_sensor:
            self._shown_sensor = key
            self.sensor_fields.set_display(from_sensor(entry.sensor))

        self._set_mount_choices(mount_choices, entry.mounted_on, mount_name)
        self._apply_sensor_reading(entry)

    def _set_mount_choices(
        self,
        choices: MountChoices,
        mounted_on: str | None,
        mount_name: str | None,
    ) -> None:
        """Fill the mount combo, `None` first for a sensor sitting in the scene.

        A mount the scene no longer has — a model whose file moved — is offered
        anyway under the name the project recorded, so selecting the sensor does
        not silently reseat it on nothing.
        """
        offered: list[tuple[str | None, str]] = [(None, self.tr("Nothing (scene origin)"))]
        offered.extend(choices)
        if mounted_on is not None and mounted_on not in {item_id for item_id, _ in choices}:
            offered.append((mounted_on, mount_name or mounted_on))

        self._emitting = False
        try:
            self.sensor_mount_combo.clear()
            for _item_id, label in offered:
                self.sensor_mount_combo.addItem(label)
            self._mount_ids = tuple(item_id for item_id, _label in offered)
            self.sensor_mount_combo.setCurrentIndex(self._mount_ids.index(mounted_on))
        finally:
            self._emitting = True

    def _apply_sensor_reading(self, entry: SensorEntry) -> None:
        """The two read-only rows. Split out because a reading changes far more
        often than the definition above it, and only these two follow it.

        The green goes behind the number, from the same helper the dock uses, so
        the panel and the tree cannot disagree about what it means.
        """
        self.sensor_state_label.setText(describe_state(entry))
        self.sensor_state_label.setToolTip(describe_state_tooltip(entry))
        self.sensor_reading_label.setText(describe_reading(entry))

        palette = self.sensor_reading_label.palette()
        if is_reading_live(entry):
            palette.setColor(QPalette.ColorRole.Window, live_reading_color())
        else:
            # Back to the widget's own colour rather than a guessed grey — a
            # hardcoded one would be wrong in a light theme or in a dark one.
            palette.setColor(
                QPalette.ColorRole.Window,
                QApplication.palette().color(QPalette.ColorRole.Window),
            )
        self.sensor_reading_label.setPalette(palette)

    def set_sensor_reading_silently(self, entry: SensorEntry) -> None:
        """Reflect a reading measured by the scene, without touching the fields.

        Called every time the scene is re-read, which is far more often than a
        sensor is edited — so it must not go anywhere near the editable half.
        """
        if entry.sensor_id == self._sensor_id:
            self._apply_sensor_reading(entry)

    def _configure_limit_spins(self, is_axis: bool) -> None:
        """Limits are degrees for an axis and millimetres for a trajectory —
        the same value would otherwise mean two very different things."""
        for spin in (self.joint_lower_limit_spin, self.joint_upper_limit_spin):
            if is_axis:
                spin.setRange(-ROTATION_LIMIT_DEG, ROTATION_LIMIT_DEG)
                spin.setDecimals(ROTATION_DECIMALS)
                spin.setSingleStep(ROTATION_STEP_DEG)
                spin.setSuffix(" °")
            else:
                spin.setRange(-TRANSLATION_LIMIT_MM, TRANSLATION_LIMIT_MM)
                spin.setDecimals(TRANSLATION_DECIMALS)
                spin.setSingleStep(TRANSLATION_STEP_MM)
                spin.setSuffix(" mm")

    def _set_joint_row(self, entry: JointEntry) -> None:
        """The value slider shown in joint mode. Rebuilt only when the joint's
        own definition changes — a row captures its kind and limits, so editing
        those has to replace it, but driving the value must not."""
        key = (entry.joint_id, entry.joint)
        if key == self._joint_row_key and self._joint_row is not None:
            self._joint_row.set_value_silently(entry.value)
            return

        if self._joint_row is not None:
            self._joint_value_layout.removeWidget(self._joint_row)
            self._joint_row.deleteLater()

        row = JointValueRow(entry.joint_id, entry.joint, entry.value, self._joint_group)
        row.value_edited.connect(self.joint_value_edited.emit)
        self._joint_value_layout.addWidget(row)
        self._joint_row = row
        self._joint_row_key = key

    def _apply_placement_fields(self, placement: Transform) -> None:
        display = from_transform(placement)
        self.x_spin.setValue(display.x_mm)
        self.y_spin.setValue(display.y_mm)
        self.z_spin.setValue(display.z_mm)
        self.rotate_x_spin.setValue(display.rotate_x_deg)
        self.rotate_y_spin.setValue(display.rotate_y_deg)
        self.rotate_z_spin.setValue(display.rotate_z_deg)

    def _set_rows(self, model_id: str | None, joint_entries: tuple[JointEntry, ...]) -> None:
        """Rebuild the per-joint rows — but only when the model or the set of
        joints actually changed, so driving a value never destroys the widget
        being dragged."""
        # Compared by *definition*, not just id: a row captures its joint's
        # kind and limits when built, so editing those must rebuild it — while
        # merely driving the value (which changes neither) must not.
        shown = tuple((entry.joint_id, entry.joint) for entry in joint_entries)
        if model_id == self._rows_model_id and shown == self._shown_joints:
            for entry in joint_entries:
                self.set_joint_value_silently(entry.joint_id, entry.value)
            return

        for row in self._rows.values():
            self._variables_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._shown_joints = shown
        self._rows_model_id = model_id

        for entry in joint_entries:
            row = JointValueRow(entry.joint_id, entry.joint, entry.value, self._variables_group)
            row.value_edited.connect(self.joint_value_edited.emit)
            self._variables_layout.addWidget(row)
            self._rows[entry.joint_id] = row

        self._no_variables_label.setVisible(not joint_entries)

    # -- programmatic updates -------------------------------------------------

    def set_joint_value_silently(self, joint_id: str, value: float) -> None:
        """Reflect a value changed elsewhere, without re-reporting it.

        Reaches the row in either mode — the model view's per-joint list and
        the joint view's single slider both show the same number.
        """
        row = self._rows.get(joint_id)
        if row is not None:
            row.set_value_silently(value)
        if self._joint_row is not None and self._joint_row.joint_id == joint_id:
            self._joint_row.set_value_silently(value)

    def set_placement_silently(self, placement: Transform) -> None:
        """Reflect a placement changed elsewhere (the Placement dialog), without
        re-reporting it."""
        self._emitting = False
        try:
            self._apply_placement_fields(placement)
        finally:
            self._emitting = True

    def set_name_silently(self, name: str) -> None:
        self._emitting = False
        try:
            self.name_edit.setText(name)
        finally:
            self._emitting = True

    def row_for(self, joint_id: str) -> JointValueRow | None:
        """The row driving one joint, or `None`. Read-only lookup, mainly for tests."""
        row = self._rows.get(joint_id)
        if row is not None:
            return row
        if self._joint_row is not None and self._joint_row.joint_id == joint_id:
            return self._joint_row
        return None

    # -- events -------------------------------------------------------------

    def _on_name_finished(self) -> None:
        """`editingFinished` rather than `textChanged`: a rename per keystroke
        would put half-typed names through the registry's uniquifier and end up
        with `gan (2)` before the user finished typing `gantry`."""
        if not self._emitting or self._model_id is None:
            return
        name = self.name_edit.text().strip()
        if name:
            self.name_edited.emit(self._model_id, name)

    def _on_placement_changed(self, _value: float) -> None:
        if not self._emitting or self._model_id is None:
            return
        self.placement_edited.emit(self._model_id, self.placement())

    def _on_joint_limit_toggled(self, checked: bool) -> None:
        self.joint_lower_limit_spin.setEnabled(checked)
        self.joint_upper_limit_spin.setEnabled(checked)
        self._on_joint_definition_changed(0.0)

    def _on_joint_definition_finished(self) -> None:
        """The text fields report on `editingFinished`, not per keystroke — a
        half-typed name would otherwise go through the registry's uniquifier
        and come back as `til (2)` before `tilt` was finished."""
        self._on_joint_definition_changed(0.0)

    def _on_joint_definition_changed(self, _value: float) -> None:
        if not self._emitting or self._joint_id is None:
            return
        joint = self.joint()
        if joint is not None:
            self.joint_edited.emit(self._joint_id, joint)

    def _on_sensor_changed(self) -> None:
        """A field edited by hand. Live, so the marker follows what is typed."""
        if not self._emitting or self._sensor_id is None:
            return
        sensor = self.sensor_fields.sensor_if_valid()
        if sensor is None:
            return
        # Remembered as shown, or the refresh this edit provokes would re-fill
        # the fields from the registry and move the caret while it is being used.
        self._shown_sensor = (self._sensor_id, sensor)
        self.sensor_edited.emit(self._sensor_id, sensor)

    def _on_sensor_mount_changed(self, index: int) -> None:
        if not self._emitting or self._sensor_id is None:
            return
        if not 0 <= index < len(self._mount_ids):
            return
        self.sensor_mount_edited.emit(self._sensor_id, self._mount_ids[index])

    def joint(self) -> ModelJoint | None:
        """The joint currently in the fields, or `None` when they do not form a
        valid one.

        A degenerate definition (the two points equal, an empty name) is a state
        the fields can legitimately pass through while being edited — reported
        as `None` rather than raised, so typing never throws out of a slot.
        """
        has_limit = self.joint_limit_checkbox.isChecked()
        kind = (
            ModelJointKind.AXIS
            if self.joint_kind_label.text() == self.tr(_KIND_LABELS[ModelJointKind.AXIS])
            else ModelJointKind.TRAJECTORY
        )
        display = ModelJointDisplay(
            name=self.joint_name_edit.text().strip(),
            kind=kind,
            variable=self.joint_variable_edit.text().strip(),
            origin_mm=(
                self.joint_origin_x_spin.value(),
                self.joint_origin_y_spin.value(),
                self.joint_origin_z_spin.value(),
            ),
            target_mm=(
                self.joint_target_x_spin.value(),
                self.joint_target_y_spin.value(),
                self.joint_target_z_spin.value(),
            ),
            direction=(
                self.joint_direction_x_spin.value(),
                self.joint_direction_y_spin.value(),
                self.joint_direction_z_spin.value(),
            ),
            initial_angle_deg=self.joint_initial_angle_spin.value(),
            lower_limit=self.joint_lower_limit_spin.value() if has_limit else None,
            upper_limit=self.joint_upper_limit_spin.value() if has_limit else None,
            alignment=PlacementDisplay(
                x_mm=self.frame_x_spin.value(),
                y_mm=self.frame_y_spin.value(),
                z_mm=self.frame_z_spin.value(),
                rotate_x_deg=self.frame_rotate_x_spin.value(),
                rotate_y_deg=self.frame_rotate_y_spin.value(),
                rotate_z_deg=self.frame_rotate_z_spin.value(),
            ),
        )
        try:
            return to_model_joint(display)
        except ConfigError as exc:
            logger.debug("joint fields do not form a valid joint yet", reason=str(exc))
            return None

    def placement(self) -> Transform:
        """The placement currently in the fields, in internal units."""
        return to_transform(
            PlacementDisplay(
                x_mm=self.x_spin.value(),
                y_mm=self.y_spin.value(),
                z_mm=self.z_spin.value(),
                rotate_x_deg=self.rotate_x_spin.value(),
                rotate_y_deg=self.rotate_y_spin.value(),
                rotate_z_deg=self.rotate_z_spin.value(),
            )
        )
