"""The dialog for placing or editing a sensor.

Unlike `ui/placement_dialog.py`, there is nothing to live-preview — the sensor
does not exist in the scene until confirmed — so this is a plain modal dialog:
fill in the fields, OK to confirm or Cancel to discard everything typed.

The fields themselves are `ui/sensor_fields.SensorFields`, shared with the
`Sensor` group in the properties panel. This dialog is what wraps them in a
modal with an OK button; the panel is what edits them live. `fields_changed` is
deliberately ignored here — nothing should reach the scene before OK.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from pssim.domain.errors import ConfigError
from pssim.domain.sensors import Sensor, SensorDisplay, SensorKind, from_sensor
from pssim.ui.sensor_fields import DEFAULT_NAME, SensorFields


class SensorDialog(QDialog):
    """Name, kind, and the fields that kind needs, behind OK and Cancel."""

    def __init__(
        self,
        current: Sensor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Sensor"))
        self.setModal(True)

        self.fields = SensorFields(self)

        layout = QVBoxLayout(self)
        layout.addWidget(self.fields)
        layout.addWidget(self._build_buttons())

        initial = SensorDisplay(name=DEFAULT_NAME) if current is None else from_sensor(current)
        self.set_display(initial)

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        return buttons

    # -- values -----------------------------------------------------------------

    @property
    def kind(self) -> SensorKind:
        return self.fields.kind

    @property
    def display(self) -> SensorDisplay:
        """The current fields, in the units the user sees."""
        return self.fields.display

    @property
    def sensor(self) -> Sensor:
        """The current fields as a domain `Sensor`.

        May raise `ConfigError` (an empty name, or a degenerate beam) — `accept()`
        is what catches it. Call this directly only once the fields are already
        known to be valid.
        """
        return self.fields.sensor

    def set_display(self, display: SensorDisplay) -> None:
        """Fill every field from `display`, in the units the user sees."""
        self.fields.set_display(display)

    # -- events -------------------------------------------------------------

    def accept(self) -> None:
        """OK builds the sensor before closing.

        A ray with no direction, or a blank name, are the cases the spin boxes and
        a plain text field cannot prevent by range alone — caught here and
        reported, instead of raising out of a dialog.
        """
        try:
            _ = self.sensor
        except ConfigError as exc:
            QMessageBox.warning(self, self.tr("Invalid sensor"), str(exc))
            return
        super().accept()
