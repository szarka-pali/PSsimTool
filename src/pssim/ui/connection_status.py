"""The connection indicator in the status bar.

A **permanent** widget rather than a `showMessage` call, and that is the whole
reason this exists as a widget at all: `showMessage` writes into the status bar's
transient area, where the next "Selected X" or "Added joint Y" wipes it. The
connection state is not a message about something that just happened — it is
where the application currently stands, so it has to keep its place on the right
while messages come and go on the left.

Clicking it opens the diagnostics. *Why* it is not connected is a status code
(R20) and too long for one line of status bar, so the label says the state, the
tooltip says the reason, and the click is the shortest path to the whole log.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from pssim.io.base import SourceStatus
from pssim.ui.labels import (
    describe_source_status,
    describe_source_status_tooltip,
    source_status_color,
)

#: The dot's diameter, in points, so it scales with the display like the text
#: beside it rather than staying 8 physical pixels on a 4K screen.
DOT_SIZE_PT = 9


class _StatusDot(QWidget):
    """A filled circle. Drawn rather than a coloured character.

    A bullet glyph is a different size in every font the platforms ship, and on
    Windows it lands noticeably above the text's centre line.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = source_status_color(SourceStatus.DISCONNECTED)
        size = round(DOT_SIZE_PT * self.logicalDpiX() / 72.0)
        self.setFixedSize(size, size)

    def set_status(self, status: SourceStatus) -> None:
        self._color = source_status_color(status)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt's name
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class ConnectionStatusWidget(QWidget):
    """A dot and a word, saying where the connection stands.

    Told what to show rather than reading the controller itself: the window
    already receives `status_changed` and owns the endpoint and the settings, and
    a second thing polling for the same answer is how two parts of one window
    come to disagree.
    """

    clicked = Signal()
    """The user asked for the whole story. The window opens the diagnostics."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = SourceStatus.DISCONNECTED
        self._shown: tuple[SourceStatus, str, str] | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 6, 0)
        layout.setSpacing(6)

        self.dot = _StatusDot(self)
        layout.addWidget(self.dot)

        self.label = QLabel(self)
        layout.addWidget(self.label)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_status(SourceStatus.DISCONNECTED, "", "")

    @property
    def status(self) -> SourceStatus:
        return self._status

    def show_status(self, status: SourceStatus, endpoint: str, reason: str = "") -> None:
        """Render one state.

        Nothing is touched when nothing changed. The controller emits its status
        on every timer tick, not only when it moves, and re-setting a tooltip
        ten times a second makes it flicker while it is being read.
        """
        self._status = status
        if self._shown == (status, endpoint, reason):
            return
        self._shown = (status, endpoint, reason)

        self.dot.set_status(status)
        self.label.setText(describe_source_status(status))

        tooltip = describe_source_status_tooltip(status, endpoint, reason)
        # On the whole widget, not only the label: the dot is half of what the
        # pointer is likely to land on.
        self.setToolTip(tooltip)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt's name
        # On release rather than press, so dragging off it cancels — the same way
        # every other clickable thing in the window behaves.
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)
