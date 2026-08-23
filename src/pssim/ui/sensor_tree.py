"""Tree of placed sensors, docked below the model tree.

A separate dock rather than folding into the Models dock: a sensor's columns
(Name / Kind / State / Reading) are not a model's (Name / Parts), and the
Reading cell needs a coloured background a model row never has.

The wording of the State and Reading cells lives in `ui/labels.py`, not here —
the properties panel says the same two things about the selected sensor, and one
of them saying it differently is how a reader ends up doubting both.

The widget owns no state. It renders a `SensorRegistry` and reports what the
user picked; the window decides what that means. Mirrors `ui/model_tree.py`.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem, QWidget

from pssim.domain.sensors import SensorKind
from pssim.observability import get_logger
from pssim.ui.labels import (
    describe_reading,
    describe_state,
    describe_state_tooltip,
    is_reading_live,
    live_reading_color,
)
from pssim.ui.sensor_registry import SensorEntry, SensorRegistry

logger = get_logger(__name__)

#: Role under which the sensor id is stored on a tree item. Qt needs an int above
#: `UserRole`; the id must not be read back from the visible text, which is a
#: display name and can repeat.
SENSOR_ID_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1

COLUMN_NAME: Final = 0
COLUMN_KIND: Final = 1
COLUMN_STATE: Final = 2
COLUMN_READING: Final = 3

#: Short labels for the tree's Kind column. Every kind is listed: a missing one
#: would raise on the row rather than degrade, and a scene with an unlabelled
#: sensor is not worth crashing the tree over.
_KIND_LABELS: Final[dict[SensorKind, str]] = {
    SensorKind.BEAM: "Laser",
    SensorKind.INDUCTIVE: "Inductive",
    SensorKind.TOF: "ToF",
    SensorKind.LASER_DISTANCE: "Laser dist.",
    SensorKind.ENCODER_INC: "Encoder INC",
    SensorKind.ENCODER_ABS: "Encoder ABS",
    SensorKind.PROXIMITY: "Proximity",
}


class SensorTree(QTreeWidget):
    """Lists placed sensors and reports which one is selected."""

    sensor_selected = Signal(object)
    """Emitted on selection change. Carries the sensor id, or `None` for nothing."""

    add_requested = Signal()
    """The user asked to place another sensor."""

    edit_requested = Signal()
    remove_requested = Signal()
    """These two carry nothing on purpose: the tree selects the row the menu was
    opened on before showing it, so the target is always the selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # No `setColumnCount` — `setHeaderLabels` sets it from the list, and a
        # second number here would only be a chance to disagree with it.
        self.setHeaderLabels(
            [self.tr("Sensor"), self.tr("Kind"), self.tr("State"), self.tr("Reading")]
        )
        self.setRootIsDecorated(False)  # no children — arrows would be noise
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(220)

        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COLUMN_NAME, header.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_KIND, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_STATE, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_READING, header.ResizeMode.ResizeToContents)

        self.itemSelectionChanged.connect(self._on_selection_changed)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # -- context menu ---------------------------------------------------------

    def _on_context_menu(self, position: QPoint) -> None:
        """Right-click: act on the row under the cursor, not the old selection —
        mirrors `ModelTree`'s own reasoning."""
        item = self.itemAt(position)
        if item is not None:
            self.setCurrentItem(item)

        menu = self.build_context_menu(on_sensor=item is not None)
        menu.exec(self.viewport().mapToGlobal(position))

    def build_context_menu(self, on_sensor: bool) -> QMenu:
        """The menu for a row (`on_sensor`) or for empty space.

        On empty space the sensor actions are **left out**, not greyed out —
        the same reasoning `ModelTree.build_context_menu` uses for models.
        """
        menu = QMenu(self)

        add_action = menu.addAction(self.tr("&Add Sensor…"))
        add_action.setStatusTip(self.tr("Place a beam or proximity sensor"))
        add_action.triggered.connect(self.add_requested.emit)

        if not on_sensor:
            return menu

        menu.addSeparator()

        edit_action = menu.addAction(self.tr("&Edit…"))
        edit_action.triggered.connect(self.edit_requested.emit)

        remove_action = menu.addAction(self.tr("&Remove"))
        remove_action.triggered.connect(self.remove_requested.emit)

        return menu

    # -- rendering --------------------------------------------------------------

    def refresh(self, registry: SensorRegistry) -> None:
        """Rebuild the tree from the registry and restore the selection.

        Rebuilding wholesale rather than diffing — the same choice
        `ModelTree.refresh` makes, for the same reason: the list is short, and a
        diff would be a second source of truth about what is on screen.
        """
        previous = registry.selected_id
        blocked = self.blockSignals(True)
        try:
            self.clear()
            for entry in registry:
                self.addTopLevelItem(_make_item(entry))
            self._apply_selection(previous)
        finally:
            self.blockSignals(blocked)

    def select_id(self, sensor_id: str | None) -> None:
        """Move the visual selection without reporting it back — needed because
        the selection can also change from code, mirroring `ModelTree.select_id`."""
        blocked = self.blockSignals(True)
        try:
            self._apply_selection(sensor_id)
        finally:
            self.blockSignals(blocked)

    def _apply_selection(self, sensor_id: str | None) -> None:
        if sensor_id is None:
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
            return
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if item is not None and item.data(COLUMN_NAME, SENSOR_ID_ROLE) == sensor_id:
                self.setCurrentItem(item)
                item.setSelected(True)
                return
        self.clearSelection()

    # -- reading ----------------------------------------------------------------

    @property
    def selected_sensor_id(self) -> str | None:
        """Id of the selected sensor, or `None`."""
        items = self.selectedItems()
        if not items:
            return None
        sensor_id = items[0].data(COLUMN_NAME, SENSOR_ID_ROLE)
        return str(sensor_id) if sensor_id is not None else None

    def _on_selection_changed(self) -> None:
        self.sensor_selected.emit(self.selected_sensor_id)


def _make_item(entry: SensorEntry) -> QTreeWidgetItem:
    """One row. The id travels in item data, never in the visible text."""
    item = QTreeWidgetItem(
        [
            entry.sensor.name,
            _KIND_LABELS.get(entry.sensor.kind, entry.sensor.kind.value),
            describe_state(entry),
            describe_reading(entry),
        ]
    )
    item.setData(COLUMN_NAME, SENSOR_ID_ROLE, entry.sensor_id)
    # The colour goes behind the **number**, not behind the word: the word
    # already says what the state is, while a number on its own does not say
    # whether it is a live measurement or the sensor idling. An encoder never
    # gets it — it has no in-range notion to report.
    if is_reading_live(entry):
        item.setBackground(COLUMN_READING, live_reading_color())
    item.setToolTip(COLUMN_STATE, describe_state_tooltip(entry))
    if entry.mounted_on is not None:
        item.setToolTip(COLUMN_NAME, f"{entry.sensor.name} (mounted)")
    return item
