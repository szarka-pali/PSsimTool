"""The window listing one model's joints, for driving their live values.

Opened per model (double-click, or the tree's "Edit Variables…" action) —
separate from `ui/joint_dialog.py`, which defines a joint's *geometry* once;
this one is for driving an *already-defined* joint's value often, potentially
for the whole time the application is open.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from pssim.ui.joint_registry import JointEntry
from pssim.ui.joint_value_row import JointValueRow


class ModelValuesPanel(QDialog):
    """One `JointValueRow` per joint owned by one model."""

    value_edited = Signal(str, float)
    """Forwards whichever row was edited — `(joint_id, value)`, value in
    internal units. One signal for the window to connect to, rather than one
    per row."""

    def __init__(
        self,
        model_id: str,
        model_name: str,
        entries: tuple[JointEntry, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._model_id = model_id
        self.setWindowTitle(self.tr("{0} — Values").format(model_name))
        self.setModal(False)  # driving a value should not block the rest of the scene

        layout = QVBoxLayout(self)
        self._rows: dict[str, JointValueRow] = {}
        for entry in entries:
            row = JointValueRow(entry.joint_id, entry.joint, entry.value, self)
            row.value_edited.connect(self.value_edited.emit)
            layout.addWidget(row)
            self._rows[entry.joint_id] = row

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def joint_count(self) -> int:
        return len(self._rows)

    def row_for(self, joint_id: str) -> JointValueRow | None:
        """The row for a joint, or `None` if this panel has none — a
        read-only lookup, mainly for tests; normal use only ever needs
        `set_value_silently`."""
        return self._rows.get(joint_id)

    def set_value_silently(self, joint_id: str, value: float) -> None:
        """Refresh one row without it looking like a user edit — used when
        something outside this panel changes a joint it is showing (e.g. its
        limits, edited via `JointDialog` while this panel is also open)."""
        row = self._rows.get(joint_id)
        if row is not None:
            row.set_value_silently(value)
