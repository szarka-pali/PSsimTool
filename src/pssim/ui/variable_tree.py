"""The Variables tab: every variable the scene mentions, and where it stands.

Tabbed with the Sensors dock rather than given a slot of its own. The two are
read at different moments — you place sensors, then you wire them up — and a
third permanent panel in a window that already has four would cost more than it
gives.

The widget owns no state. It renders a `VariableRegistry` and reports what the
user picked; `ui/connection_controller.py` decides what that means. Mirrors
`ui/sensor_tree.py`.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem, QWidget

from pssim.observability import get_logger
from pssim.ui.labels import (
    NOT_APPLICABLE,
    describe_direction,
    describe_direction_tooltip,
    describe_variable_state,
    describe_variable_state_tooltip,
    describe_variable_value,
    describe_variable_value_tooltip,
    live_reading_color,
)
from pssim.ui.variable_registry import VariableEntry, VariableRegistry, VariableState

logger = get_logger(__name__)

#: Role under which the variable's name is stored. Qt needs an int above
#: `UserRole`; the name must not be read back from the visible text, which is
#: the same string here but need not stay that way.
VARIABLE_NAME_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1

COLUMN_NAME: Final = 0
COLUMN_DIRECTION: Final = 1
COLUMN_TAG: Final = 2
COLUMN_VALUE: Final = 3
COLUMN_STATUS: Final = 4

#: How this table is named in the saved view settings.
TABLE_NAME: Final = "variables"

#: What the columns start at before anyone has dragged them.
DEFAULT_COLUMN_WIDTHS: Final[tuple[int, ...]] = (110, 60, 200, 90, 100)


class VariableTree(QTreeWidget):
    """Lists the project's variables and reports which one is selected."""

    variable_selected = Signal(object)
    """Emitted on selection change. Carries the variable name, or `None`."""

    assign_requested = Signal()
    clear_requested = Signal()
    """Neither carries anything: the tree selects the row the menu was opened on
    before showing it, so the target is always the selection."""

    connect_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setHeaderLabels(
            [
                self.tr("Variable"),
                self.tr("Way"),
                self.tr("OPC UA tag"),
                self.tr("Value"),
                self.tr("Status"),
            ]
        )
        self.setRootIsDecorated(False)  # no children — arrows would be noise
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(260)

        header = self.header()
        header.setStretchLastSection(False)
        for column in range(self.columnCount()):
            header.setSectionResizeMode(column, header.ResizeMode.Interactive)
        self.set_column_widths(DEFAULT_COLUMN_WIDTHS)

        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.itemDoubleClicked.connect(self._on_double_clicked)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _on_double_clicked(self, _item: QTreeWidgetItem, _column: int) -> None:
        """A double-click is the quickest way to reach the tag chooser, which is
        the one thing a row in this table is usually opened for."""
        self.assign_requested.emit()

    # -- columns ------------------------------------------------------------

    def column_widths(self) -> tuple[int, ...]:
        """The current width of every column, for saving."""
        return tuple(self.columnWidth(column) for column in range(self.columnCount()))

    def set_column_widths(self, widths: tuple[int, ...]) -> None:
        """Apply saved widths. A list of the wrong length is ignored entirely —
        it would put each width against the wrong column."""
        if len(widths) != self.columnCount():
            return
        for column, width in enumerate(widths):
            self.setColumnWidth(column, width)

    # -- context menu -------------------------------------------------------

    def _on_context_menu(self, position: QPoint) -> None:
        item = self.itemAt(position)
        if item is not None:
            self.setCurrentItem(item)

        menu = self.build_context_menu(on_variable=item is not None)
        menu.exec(self.viewport().mapToGlobal(position))

    def build_context_menu(self, on_variable: bool) -> QMenu:
        """The menu for a row, or for empty space.

        On empty space the per-variable entries are **left out**, not greyed —
        the same reasoning `ModelTree.build_context_menu` uses.
        """
        menu = QMenu(self)

        connect_action = menu.addAction(self.tr("&Connect"))
        connect_action.setStatusTip(self.tr("Connect to the OPC UA server"))
        connect_action.triggered.connect(self.connect_requested.emit)

        settings_action = menu.addAction(self.tr("Connection &Settings…"))
        settings_action.triggered.connect(self.settings_requested.emit)

        if not on_variable:
            return menu

        menu.addSeparator()

        assign_action = menu.addAction(self.tr("&Assign Tag…"))
        assign_action.setStatusTip(self.tr("Pick the node this variable reads from"))
        assign_action.triggered.connect(self.assign_requested.emit)

        clear_action = menu.addAction(self.tr("&Clear Tag"))
        clear_action.setStatusTip(self.tr("Leave this variable bound to nothing"))
        clear_action.triggered.connect(self.clear_requested.emit)

        return menu

    # -- rendering ----------------------------------------------------------

    def refresh(self, registry: VariableRegistry) -> None:
        """Rebuild from the registry and restore the selection.

        Wholesale rather than diffing — the same choice the other two trees make,
        for the same reason: the list is short, and a diff would be a second
        source of truth about what is on screen.
        """
        previous = self.selected_variable
        blocked = self.blockSignals(True)
        try:
            self.clear()
            for entry in registry:
                self.addTopLevelItem(_make_item(entry))
            self._apply_selection(previous)
        finally:
            self.blockSignals(blocked)

    def select_variable(self, name: str | None) -> None:
        """Move the visual selection without reporting it back."""
        blocked = self.blockSignals(True)
        try:
            self._apply_selection(name)
        finally:
            self.blockSignals(blocked)

    def _apply_selection(self, name: str | None) -> None:
        if name is None:
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
            return
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if item is not None and item.data(COLUMN_NAME, VARIABLE_NAME_ROLE) == name:
                self.setCurrentItem(item)
                item.setSelected(True)
                return
        self.clearSelection()

    # -- reading ------------------------------------------------------------

    @property
    def selected_variable(self) -> str | None:
        """The selected variable's name, or `None`."""
        items = self.selectedItems()
        if not items:
            return None
        name = items[0].data(COLUMN_NAME, VARIABLE_NAME_ROLE)
        return str(name) if name is not None else None

    def _on_selection_changed(self) -> None:
        self.variable_selected.emit(self.selected_variable)


def _make_item(entry: VariableEntry) -> QTreeWidgetItem:
    """One row. The name travels in item data, never read back from the text."""
    item = QTreeWidgetItem(
        [
            entry.name,
            describe_direction(entry.direction),
            entry.tag.node_id if entry.tag is not None else NOT_APPLICABLE,
            describe_variable_value(entry),
            describe_variable_state(entry),
        ]
    )
    item.setData(COLUMN_NAME, VARIABLE_NAME_ROLE, entry.name)
    item.setToolTip(COLUMN_NAME, entry.owner)
    item.setToolTip(COLUMN_VALUE, describe_variable_value_tooltip(entry))
    item.setToolTip(COLUMN_STATUS, describe_variable_state_tooltip(entry))
    item.setTextAlignment(COLUMN_VALUE, Qt.AlignmentFlag.AlignRight)

    # The same green a live sensor reading gets, and for the same reason: it
    # marks the rows that are actually carrying data right now. Nothing is
    # painted red — a red cell in a table reads as an error, and being
    # disconnected is a state, not a fault.
    if entry.state is VariableState.LIVE:
        item.setBackground(COLUMN_STATUS, live_reading_color())
    item.setToolTip(COLUMN_DIRECTION, describe_direction_tooltip(entry.direction))
    return item


def state_color(entry: VariableEntry) -> QColor | None:
    """The Status cell's background, or `None` for the states that get none.

    Exposed so a test can ask about it without reaching into a tree item.
    """
    return live_reading_color() if entry.state is VariableState.LIVE else None
