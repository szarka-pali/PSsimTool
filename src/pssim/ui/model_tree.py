"""Tree of loaded models, docked on the left.

A `QTreeWidget` rather than a `QListWidget`: models will grow children (the
assembly hierarchy from the STEP file) and switching container types later would
mean rewriting every caller.

The widget owns no state. It renders a `ModelRegistry` and reports what the user
picked; the window decides what that means.
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from pssim.observability import get_logger
from pssim.ui.model_registry import ModelEntry, ModelRegistry

logger = get_logger(__name__)

#: Role under which the model id is stored on a tree item. Qt needs an int above
#: `UserRole`; the id must not be read back from the visible text, which is a
#: display name and can repeat.
MODEL_ID_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1

COLUMN_NAME: Final = 0
COLUMN_PARTS: Final = 1


class ModelTree(QTreeWidget):
    """Lists loaded models and reports which one is selected."""

    model_selected = Signal(object)
    """Emitted on selection change. Carries the model id, or `None` for nothing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setColumnCount(2)
        self.setHeaderLabels([self.tr("Model"), self.tr("Parts")])
        self.setRootIsDecorated(False)  # no children yet — arrows would be noise
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.setUniformRowHeights(True)
        self.setMinimumWidth(220)

        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COLUMN_NAME, header.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_PARTS, header.ResizeMode.ResizeToContents)

        self.itemSelectionChanged.connect(self._on_selection_changed)

    # -- rendering ----------------------------------------------------------

    def refresh(self, registry: ModelRegistry) -> None:
        """Rebuild the tree from the registry and restore the selection.

        Rebuilding wholesale rather than diffing: the list is short, and a diff
        would be a second source of truth about what is on screen.

        Selection signals are suppressed while rebuilding — otherwise clearing
        the tree would report "nothing selected" and the window would drop the
        real selection.
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

    def select_id(self, model_id: str | None) -> None:
        """Move the visual selection without reporting it back.

        Needed because selection can also change from code — picking a neighbour
        after a removal, or a programmatic `select_model()`. Without this the
        tree would keep highlighting a row the rest of the app no longer
        considers selected.

        Signals stay blocked: the caller already knows, and re-emitting would
        bounce the change back through the window.
        """
        blocked = self.blockSignals(True)
        try:
            self._apply_selection(model_id)
        finally:
            self.blockSignals(blocked)

    def _apply_selection(self, model_id: str | None) -> None:
        if model_id is None:
            self.clearSelection()
            # Clearing the current item too, so no stale focus rectangle is left
            # on a row that is no longer selected. An invalid index is how Qt
            # expresses "no current item"; `setCurrentItem(None)` is not typed.
            self.setCurrentIndex(QModelIndex())
            return
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if item is not None and item.data(COLUMN_NAME, MODEL_ID_ROLE) == model_id:
                # `setCurrentItem` is what enforces single selection; plain
                # `setSelected` would leave the previous row highlighted too.
                self.setCurrentItem(item)
                item.setSelected(True)
                return
        self.clearSelection()

    # -- reading ------------------------------------------------------------

    @property
    def selected_model_id(self) -> str | None:
        """Id of the selected model, or `None`."""
        items = self.selectedItems()
        if not items:
            return None
        model_id = items[0].data(COLUMN_NAME, MODEL_ID_ROLE)
        return str(model_id) if model_id is not None else None

    def _on_selection_changed(self) -> None:
        self.model_selected.emit(self.selected_model_id)


def _make_item(entry: ModelEntry) -> QTreeWidgetItem:
    """One row. The id travels in item data, never in the visible text."""
    item = QTreeWidgetItem([entry.name, str(entry.node_count)])
    item.setData(COLUMN_NAME, MODEL_ID_ROLE, entry.model_id)
    item.setTextAlignment(COLUMN_PARTS, Qt.AlignmentFlag.AlignRight)
    item.setToolTip(COLUMN_NAME, str(entry.path))
    if entry.is_placed:
        # A moved or rotated model is worth spotting in the list — otherwise
        # "why is it not where I expect" costs a trip through the dialog.
        item.setText(COLUMN_NAME, f"{entry.name} *")
        item.setToolTip(COLUMN_NAME, f"{entry.path}\n{_placement_tooltip(entry)}")
    return item


def _placement_tooltip(entry: ModelEntry) -> str:
    from pssim.ui.labels import describe_placement

    return describe_placement(entry.placement)
