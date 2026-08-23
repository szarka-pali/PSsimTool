"""Tree of loaded models, docked on the left.

A `QTreeWidget` rather than a `QListWidget`: models were always expected to grow
children eventually, and joints (axes/trajectories) are the first thing to
nest under a model's row — the assembly hierarchy from the STEP file may be a
second, later.

The widget owns no state. It renders a `ModelRegistry` (models) and a
`JointRegistry` (their joints) and reports what the user picked; the window
decides what that means.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from PySide6.QtCore import QCoreApplication, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QKeySequence, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidget, QTreeWidgetItem, QWidget

from pssim.domain.model_joints import ModelJointKind
from pssim.observability import get_logger
from pssim.ui.joint_registry import JointEntry, JointRegistry
from pssim.ui.model_registry import ModelEntry, ModelRegistry

logger = get_logger(__name__)

#: Role under which the model id is stored on a tree item. Qt needs an int above
#: `UserRole`; the id must not be read back from the visible text, which is a
#: display name and can repeat.
MODEL_ID_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1

#: Role under which the joint id is stored — only set on joint (child) rows, so
#: its presence is also how a row is told apart from a model row.
JOINT_ID_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 2

#: Roles carrying the row's own display settings. Kept on the item rather than in
#: a field on the widget so this stays what its docstring says it is — a
#: renderer of the registries, owning no state of its own. The context menu needs
#: them to show the right check marks, and the item is already where the id
#: lives.
VISIBLE_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 3
SHOW_AXES_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 4
SHOW_NAME_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 5
HAS_COLOR_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 6
HAS_HIGHLIGHT_COLOR_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 7

COLUMN_NAME: Final = 0
COLUMN_PARTS: Final = 1

#: Appended to a hidden row's tooltip. Not `tr()`-wrapped at module level with
#: `QCoreApplication.translate` like a dialog string would be, because it is
#: built into a tooltip by a module-level function with no `QObject` to hand —
#: see `translate` usage in `ui/labels.py` for the same situation.
_HIDDEN_TOOLTIP: Final = QCoreApplication.translate("ModelTree", "Hidden")

_KIND_LABELS: Final[dict[ModelJointKind, str]] = {
    ModelJointKind.AXIS: "axis",
    ModelJointKind.TRAJECTORY: "trajectory",
}


@dataclass(frozen=True, slots=True)
class RowState:
    """What the context menu needs to know about the row it opened on.

    A dataclass rather than three more arguments: `build_context_menu` was
    already at two, and code-style.md caps a signature at four. Defaults chosen
    so a caller that does not care — every test that only checks which entries
    exist — can leave it out entirely.
    """

    is_driven: bool = False
    """The row is a model carried by a joint, so it has a value to drive."""

    is_visible: bool = True
    show_axes: bool = True
    show_name: bool = True
    has_color: bool = False
    """There is a colour override to reset. Only then is `Reset Colour` offered —
    an entry that would do nothing is worse than no entry."""

    has_highlight_color: bool = False


#: A frozen, side-effect-free default shared by every caller that does not care
#: about the row's settings — nothing about it is ever mutated. Same idiom as
#: `ui.project_controller._EMPTY_SCENE`.
_DEFAULT_ROW: Final = RowState()


class TreeTarget(StrEnum):
    """What a context menu was opened on. A plain bool stopped being enough
    once a joint row became a third possibility alongside a model row and
    empty space."""

    EMPTY = "empty"
    MODEL = "model"
    JOINT = "joint"


class ModelTree(QTreeWidget):
    """Lists loaded models and their joints, and reports what is selected."""

    model_selected = Signal(object)
    """Emitted on selection change. Carries the model id, or `None` — including
    when a joint row is selected: a joint belongs to no model, so there is no
    owning model to fall back to any more."""

    joint_selected = Signal(object)
    """Emitted alongside `model_selected` from the same selection change.
    Carries the joint id, or `None` when the current row is not a joint."""

    model_double_clicked = Signal(object)
    """A model row was double-clicked. Carries its id. Never fired for a joint
    row - double-clicking one just expands/collapses it, the default Qt
    behaviour."""

    add_requested = Signal()
    """The user asked for another model in the assembly."""

    rename_requested = Signal()
    placement_requested = Signal()
    remove_requested = Signal()
    """These three carry nothing on purpose: the tree selects the row the menu
    was opened on before showing it, so the target is always the selection."""

    add_axis_requested = Signal()
    add_trajectory_requested = Signal()
    """Add a joint. Where it lands depends on the current row: in the scene from
    empty space, or under the selected joint from a joint row."""

    bind_requested = Signal()
    edit_values_requested = Signal()
    """On a model row: attach it to a joint, or drive the values that move it."""

    visibility_toggled = Signal(bool)
    """A model row's `Visible` was ticked or unticked."""

    axes_visibility_toggled = Signal(bool)
    """A model or joint row's coordinate cross was turned on or off."""

    name_visibility_toggled = Signal(bool)
    """A joint row's name label was turned on or off."""

    color_requested = Signal()
    color_reset_requested = Signal()
    """Pick a colour for the selected row, or go back to its original one."""

    highlight_color_requested = Signal()
    highlight_color_reset_requested = Signal()
    """The same pair for the colour a model is outlined in when selected."""

    edit_joint_requested = Signal()
    set_joint_parent_requested = Signal()
    remove_joint_requested = Signal()
    """On a joint row. Same "carries nothing" convention — the tree has already
    selected the row the menu was opened on."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setColumnCount(2)
        self.setHeaderLabels([self.tr("Model"), self.tr("Parts")])
        self.setRootIsDecorated(True)  # a model with joints needs the expand arrow
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(220)

        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COLUMN_NAME, header.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_PARTS, header.ResizeMode.ResizeToContents)

        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # -- context menu -------------------------------------------------------

    def _on_context_menu(self, position: QPoint) -> None:
        """Right-click: act on the row under the cursor, not on the old selection.

        Selecting first is what makes the menu unambiguous — right-clicking one
        model and having the action land on another is the classic way to move
        the wrong part.
        """
        item = self.itemAt(position)
        if item is not None:
            self.setCurrentItem(item)

        menu = self.build_context_menu(self._target_of(item), self._row_state(item))
        menu.exec(self.viewport().mapToGlobal(position))

    def _row_state(self, item: QTreeWidgetItem | None) -> RowState:
        """Gather the row's display settings, so the menu can tick the right
        boxes. Absent roles read as the default, which is what an item built
        before these roles existed would have."""
        if item is None:
            return RowState()
        visible = item.data(COLUMN_NAME, VISIBLE_ROLE)
        show_axes = item.data(COLUMN_NAME, SHOW_AXES_ROLE)
        show_name = item.data(COLUMN_NAME, SHOW_NAME_ROLE)
        return RowState(
            is_driven=self._is_driven(item),
            is_visible=True if visible is None else bool(visible),
            show_axes=True if show_axes is None else bool(show_axes),
            show_name=True if show_name is None else bool(show_name),
            has_color=bool(item.data(COLUMN_NAME, HAS_COLOR_ROLE)),
            has_highlight_color=bool(item.data(COLUMN_NAME, HAS_HIGHLIGHT_COLOR_ROLE)),
        )

    def _is_driven(self, item: QTreeWidgetItem | None) -> bool:
        """Whether a model row is carried by a joint — i.e. whether it has any
        value to drive.

        Read from the row's **parent**, not from its child count: a model's
        children used to be its joints, but now a model is a leaf and it is the
        joint above it that moves it.
        """
        if item is None or item.data(COLUMN_NAME, JOINT_ID_ROLE) is not None:
            return False
        parent = item.parent()
        # The PySide6 stub claims `parent()` always returns an item, but a
        # top-level model row genuinely has none.
        if parent is None:  # type: ignore[reportUnnecessaryComparison]
            return False
        return parent.data(COLUMN_NAME, JOINT_ID_ROLE) is not None

    def _target_of(self, item: QTreeWidgetItem | None) -> TreeTarget:
        if item is None:
            return TreeTarget.EMPTY
        if item.data(COLUMN_NAME, JOINT_ID_ROLE) is not None:
            return TreeTarget.JOINT
        return TreeTarget.MODEL

    def build_context_menu(self, target: TreeTarget, state: RowState = _DEFAULT_ROW) -> QMenu:
        """The menu for a model row, a joint row, or empty space.

        Empty space offers what needs no target: adding a model, and adding an
        axis or trajectory to the scene — a joint no longer belongs to a model,
        so there is nothing to select first.

        A joint row's menu can add a *child* joint, which is how a chain is
        built: a rail carrying a rotation axis.

        Actions with no target are **left out**, not greyed out: the selection
        survives a click into the void, so a disabled `Rename` would be claiming
        there is no model when the toolbar still says there is one.

        Public so the actions can be inspected and triggered without a cursor —
        `exec()` blocks on a real popup, which no test can drive.
        """
        menu = QMenu(self)

        add_action = menu.addAction(self.tr("&Add Model…"))
        add_action.setStatusTip(self.tr("Add another model to the assembly"))
        add_action.triggered.connect(self.add_requested.emit)

        if target is TreeTarget.EMPTY:
            menu.addSeparator()
            self._add_joint_actions(menu, in_scene=True)
            return menu

        if target is TreeTarget.JOINT:
            menu.addSeparator()
            edit_action = menu.addAction(self.tr("&Edit…"))
            edit_action.triggered.connect(self.edit_joint_requested.emit)
            parent_action = menu.addAction(self.tr("Set &Parent…"))
            parent_action.setStatusTip(self.tr("Choose which joint carries this one"))
            parent_action.triggered.connect(self.set_joint_parent_requested.emit)
            menu.addSeparator()
            self._add_cross_action(menu, state.show_axes)
            name_action = menu.addAction(self.tr("Show &Name"))
            name_action.setCheckable(True)
            name_action.setChecked(state.show_name)
            name_action.setStatusTip(self.tr("Draw this joint's name in the scene"))
            name_action.toggled.connect(self.name_visibility_toggled.emit)
            self._add_joint_color_actions(menu, state.has_color)
            menu.addSeparator()
            self._add_joint_actions(menu, in_scene=False)
            menu.addSeparator()
            remove_joint_action = menu.addAction(self.tr("&Remove"))
            remove_joint_action.triggered.connect(self.remove_joint_requested.emit)
            return menu

        menu.addSeparator()

        rename_action = menu.addAction(self.tr("Re&name…"))
        rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        rename_action.triggered.connect(self.rename_requested.emit)

        placement_action = menu.addAction(self.tr("&Placement…"))
        placement_action.triggered.connect(self.placement_requested.emit)

        menu.addSeparator()

        bind_action = menu.addAction(self.tr("&Bind to…"))
        bind_action.setStatusTip(self.tr("Attach this model to an axis or trajectory"))
        bind_action.triggered.connect(self.bind_requested.emit)

        if state.is_driven:
            values_action = menu.addAction(self.tr("Edit &Variables…"))
            values_action.triggered.connect(self.edit_values_requested.emit)

        menu.addSeparator()

        visible_action = menu.addAction(self.tr("&Visible"))
        visible_action.setCheckable(True)
        visible_action.setChecked(state.is_visible)
        visible_action.setStatusTip(self.tr("Draw this model, or hide it to see behind it"))
        visible_action.toggled.connect(self.visibility_toggled.emit)

        self._add_cross_action(menu, state.show_axes)
        self._add_color_actions(menu, state)

        menu.addSeparator()

        remove_action = menu.addAction(self.tr("&Remove"))
        remove_action.triggered.connect(self.remove_requested.emit)

        return menu

    def _add_cross_action(self, menu: QMenu, show_axes: bool) -> None:
        """The cross toggle, worded the same for a model and for a joint —
        it means the same thing on both, and one wording is one string to
        translate."""
        action = menu.addAction(self.tr("Show Coordinate &Cross"))
        action.setCheckable(True)
        action.setChecked(show_axes)
        action.setStatusTip(self.tr("Draw the coordinate cross while this is selected"))
        action.toggled.connect(self.axes_visibility_toggled.emit)

    def _add_color_actions(self, menu: QMenu, state: RowState) -> None:
        """The colour entries for a model: its own colour and its outline colour.

        A submenu rather than four more flat entries — the model menu was already
        long, and the two colours plus their resets read as one group.

        A `Reset` appears only when there is something to reset. For the model
        colour that means going back to the colours the STEP file carries, which
        no colour value can express.
        """
        submenu = menu.addMenu(self.tr("Colou&r"))

        model_action = submenu.addAction(self.tr("&Model…"))
        model_action.setStatusTip(self.tr("Pick a colour for this model"))
        model_action.triggered.connect(self.color_requested.emit)

        highlight_action = submenu.addAction(self.tr("&Highlight…"))
        highlight_action.setStatusTip(
            self.tr("Pick the colour this model is outlined in when selected")
        )
        highlight_action.triggered.connect(self.highlight_color_requested.emit)

        if not (state.has_color or state.has_highlight_color):
            return
        submenu.addSeparator()
        if state.has_color:
            reset = submenu.addAction(self.tr("Reset M&odel Colour"))
            reset.setStatusTip(self.tr("Go back to the colours from the CAD file"))
            reset.triggered.connect(self.color_reset_requested.emit)
        if state.has_highlight_color:
            reset_highlight = submenu.addAction(self.tr("Reset H&ighlight Colour"))
            reset_highlight.triggered.connect(self.highlight_color_reset_requested.emit)

    def _add_joint_color_actions(self, menu: QMenu, has_color: bool) -> None:
        """A joint has one colour, so it stays a flat pair."""
        color_action = menu.addAction(self.tr("&Colour…"))
        color_action.setStatusTip(self.tr("Pick a colour for this item"))
        color_action.triggered.connect(self.color_requested.emit)

        if not has_color:
            return
        reset_action = menu.addAction(self.tr("Reset Colou&r"))
        reset_action.setStatusTip(self.tr("Go back to the original colour"))
        reset_action.triggered.connect(self.color_reset_requested.emit)

    def _add_joint_actions(self, menu: QMenu, in_scene: bool) -> None:
        """The two "add a joint" entries. Worded for where it will land, so a
        joint row's menu does not read as if it would add another top-level one.
        """
        axis_label = self.tr("Add &Axis…") if in_scene else self.tr("Add Child &Axis…")
        trajectory_label = (
            self.tr("Add &Trajectory…") if in_scene else self.tr("Add Child &Trajectory…")
        )
        axis_action = menu.addAction(axis_label)
        axis_action.triggered.connect(self.add_axis_requested.emit)
        trajectory_action = menu.addAction(trajectory_label)
        trajectory_action.triggered.connect(self.add_trajectory_requested.emit)

    # -- rendering ----------------------------------------------------------

    def refresh(self, models: ModelRegistry, joints: JointRegistry) -> None:
        """Rebuild the tree from both registries and restore the selection.

        The shape follows the scene: joints at the top, each carrying the models
        bound to it and the joints hanging off it, recursively. Models bound to
        nothing sit at the top level too. This is the inversion of what it used
        to be — joints nested under their owning model — and it is why the
        builder recurses instead of running two fixed loops.

        Rebuilding wholesale rather than diffing: the list is short, and a diff
        would be a second source of truth about what is on screen.

        Selection signals are suppressed while rebuilding — otherwise clearing
        the tree would report "nothing selected" and the window would drop the
        real selection.
        """
        previous_model = models.selected_id
        previous_joint = joints.selected_id
        blocked = self.blockSignals(True)
        try:
            self.clear()
            for joint_entry in joints.children_of(None):
                self.addTopLevelItem(_build_joint_branch(joint_entry, models, joints))
            for entry in models:
                if entry.bound_to_joint_id is None:
                    self.addTopLevelItem(_make_item(entry))
            self._apply_selection(previous_joint, previous_model)
        finally:
            self.blockSignals(blocked)

    def select_id(self, model_id: str | None) -> None:
        """Move the visual selection onto a model row, without reporting it back.

        Needed because selection can also change from code — picking a neighbour
        after a removal, or a programmatic `select_model()`. Without this the
        tree would keep highlighting a row the rest of the app no longer
        considers selected.

        Signals stay blocked: the caller already knows, and re-emitting would
        bounce the change back through the window.
        """
        blocked = self.blockSignals(True)
        try:
            self._apply_selection(None, model_id)
        finally:
            self.blockSignals(blocked)

    def select_joint_id(self, joint_id: str | None) -> None:
        """The joint-row counterpart of `select_id`."""
        blocked = self.blockSignals(True)
        try:
            self._apply_selection(joint_id, None)
        finally:
            self.blockSignals(blocked)

    def _apply_selection(self, joint_id: str | None, model_id: str | None) -> None:
        if joint_id is not None:
            item = self._find_joint_item(joint_id)
            if item is not None:
                # `setCurrentItem` is what enforces single selection; plain
                # `setSelected` would leave the previous row highlighted too.
                self.setCurrentItem(item)
                item.setSelected(True)
                return

        if model_id is not None:
            item = self._find_model_item(model_id)
            if item is not None:
                self.setCurrentItem(item)
                item.setSelected(True)
                return

        self.clearSelection()
        # Clearing the current item too, so no stale focus rectangle is left on
        # a row that is no longer selected. An invalid index is how Qt expresses
        # "no current item"; `setCurrentItem(None)` is not typed.
        self.setCurrentIndex(QModelIndex())

    def _find_model_item(self, model_id: str) -> QTreeWidgetItem | None:
        return self._find_by_role(MODEL_ID_ROLE, model_id)

    def _find_joint_item(self, joint_id: str) -> QTreeWidgetItem | None:
        return self._find_by_role(JOINT_ID_ROLE, joint_id)

    def _find_by_role(self, role: int, value: str) -> QTreeWidgetItem | None:
        """Depth-first search for the row carrying `value` under `role`.

        Recursive rather than the two fixed levels this used to scan: a chain of
        joints can nest arbitrarily deep now.
        """
        frontier = [self.topLevelItem(index) for index in range(self.topLevelItemCount())]
        while frontier:
            item = frontier.pop()
            if item is None:
                continue
            if item.data(COLUMN_NAME, role) == value:
                return item
            frontier.extend(item.child(index) for index in range(item.childCount()))
        return None

    # -- reading ------------------------------------------------------------

    @property
    def selected_model_id(self) -> str | None:
        """Id of the selected model, or `None` — including when the current row
        is a joint.

        No parent walk any more: a joint's parent is another joint or the scene,
        never a model, so there is nothing to resolve up to. A joint row means
        "no model selected", which is exactly what the properties panel wants in
        order to show the joint instead.
        """
        items = self.selectedItems()
        if not items:
            return None
        model_id = items[0].data(COLUMN_NAME, MODEL_ID_ROLE)
        return str(model_id) if model_id is not None else None

    @property
    def selected_joint_id(self) -> str | None:
        """Id of the selected joint, or `None` when the current row is not one."""
        items = self.selectedItems()
        if not items:
            return None
        joint_id = items[0].data(COLUMN_NAME, JOINT_ID_ROLE)
        return str(joint_id) if joint_id is not None else None

    def _on_selection_changed(self) -> None:
        self.model_selected.emit(self.selected_model_id)
        self.joint_selected.emit(self.selected_joint_id)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(COLUMN_NAME, JOINT_ID_ROLE) is not None:
            return
        model_id = item.data(COLUMN_NAME, MODEL_ID_ROLE)
        if model_id is not None:
            self.model_double_clicked.emit(str(model_id))


def _make_item(entry: ModelEntry) -> QTreeWidgetItem:
    """One model row. The id travels in item data, never in the visible text."""
    item = QTreeWidgetItem([entry.name, str(entry.node_count)])
    item.setData(COLUMN_NAME, MODEL_ID_ROLE, entry.model_id)
    item.setData(COLUMN_NAME, VISIBLE_ROLE, entry.is_visible)
    item.setData(COLUMN_NAME, SHOW_AXES_ROLE, entry.show_axes)
    item.setData(COLUMN_NAME, HAS_COLOR_ROLE, entry.color is not None)
    item.setData(COLUMN_NAME, HAS_HIGHLIGHT_COLOR_ROLE, entry.highlight_color is not None)
    item.setTextAlignment(COLUMN_PARTS, Qt.AlignmentFlag.AlignRight)
    tooltip = str(entry.path)
    if entry.is_placed:
        # A moved or rotated model is worth spotting in the list — otherwise
        # "why is it not where I expect" costs a trip through the dialog.
        item.setText(COLUMN_NAME, f"{entry.name} *")
        tooltip = f"{entry.path}\n{_placement_tooltip(entry)}"
    if not entry.is_visible:
        # Dimmed rather than marked with a glyph: a hidden model is still
        # selectable — that is how you get it back — and `setDisabled` would
        # take that away along with the grey.
        _dim(item)
        tooltip = f"{tooltip}\n{_HIDDEN_TOOLTIP}"
    item.setToolTip(COLUMN_NAME, tooltip)
    return item


def _dim(item: QTreeWidgetItem) -> None:
    """Grey a row out using the palette's own disabled colour, so it stays
    legible in a light theme and in a dark one alike. A hardcoded grey would be
    invisible in one of the two."""
    palette = QApplication.palette()
    brush = QBrush(palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText))
    for column in (COLUMN_NAME, COLUMN_PARTS):
        item.setForeground(column, brush)


def _make_joint_item(entry: JointEntry) -> QTreeWidgetItem:
    """One joint row. The id travels in item data, never in the visible text."""
    label = f"{entry.joint.name} ({_KIND_LABELS[entry.joint.kind]})"
    item = QTreeWidgetItem([label, ""])
    item.setData(COLUMN_NAME, JOINT_ID_ROLE, entry.joint_id)
    item.setData(COLUMN_NAME, SHOW_AXES_ROLE, entry.show_axes)
    item.setData(COLUMN_NAME, SHOW_NAME_ROLE, entry.show_name)
    item.setData(COLUMN_NAME, HAS_COLOR_ROLE, entry.color is not None)
    return item


def _build_joint_branch(
    entry: JointEntry, models: ModelRegistry, joints: JointRegistry
) -> QTreeWidgetItem:
    """A joint row with everything it carries: the models bound to it first,
    then the joints hanging off it, recursively.

    Models before child joints so the thing being moved reads before the next
    stage of the mechanism. Recursion is safe because
    `ui.joint_registry.would_cycle` refuses to create a loop in the first place.
    """
    item = _make_joint_item(entry)
    for model_entry in models:
        if model_entry.bound_to_joint_id == entry.joint_id:
            item.addChild(_make_item(model_entry))
    for child in joints.children_of(entry.joint_id):
        item.addChild(_build_joint_branch(child, models, joints))
    return item


def _placement_tooltip(entry: ModelEntry) -> str:
    from pssim.ui.labels import describe_placement

    return describe_placement(entry.placement)
