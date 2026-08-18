"""Icons drawn at run time, with no binary assets.

A view icon shows the **X/Y/Z axes as they are really seen from that view** — from the front
X is to the right and Z is up, from the top X is to the right and Y is up. The projection is
done by the same camera as the scene (`viz.orbit.OrbitCamera.project`), so an icon cannot
claim anything other than what happens after the click.

Drawing at run time has two advantages: there are no binaries in the repository, and the
icons adapt to the display's DPI.
"""

from __future__ import annotations

from typing import Any, Final

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from pssim.viz.axes import AXIS_COLORS, AXIS_DIRECTIONS
from pssim.viz.orbit import OrbitCamera

DEFAULT_ICON_PX: Final = 24

#: We render at a larger resolution and scale down — otherwise diagonal lines are jagged.
SUPERSAMPLE: Final = 2

#: The arm length as a fraction of half the icon.
ARM_RATIO: Final = 0.72

#: An axis shorter than this points (almost) into the screen and is drawn as a dot.
INTO_SCREEN_THRESHOLD: Final = 0.2

LINE_WIDTH_PX: Final = 2.0
DOT_RADIUS_PX: Final = 2.0


def view_icon(view: str, size_px: int = DEFAULT_ICON_PX) -> QIcon:
    """The icon of a standard view — the projected axes.

    An unknown view name is a `ValueError` (raised by `with_view`).
    """
    camera = OrbitCamera().with_view(view)
    scale = SUPERSAMPLE
    pixmap = QPixmap(size_px * scale, size_px * scale)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _draw_axes(painter, camera, size_px * scale)
    finally:
        painter.end()

    pixmap.setDevicePixelRatio(float(scale))
    return QIcon(pixmap)


def _draw_axes(painter: QPainter, camera: OrbitCamera, size_px: int) -> None:
    center = size_px / 2.0
    arm = center * ARM_RATIO

    # The axes pointing into the screen are drawn first, so the visible ones cover them.
    projected = {name: camera.project(direction) for name, direction in AXIS_DIRECTIONS.items()}
    order = sorted(projected, key=lambda name: _length(projected[name]))

    for name in order:
        screen_x, screen_y = projected[name]
        color = QColor.fromRgbF(*AXIS_COLORS[name])
        pen = QPen(color)
        pen.setWidthF(LINE_WIDTH_PX * SUPERSAMPLE)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        if _length((screen_x, screen_y)) < INTO_SCREEN_THRESHOLD:
            # The axis points into the screen — from this view it is a dot, not a line.
            painter.setBrush(color)
            painter.drawEllipse(
                QPointF(center, center),
                DOT_RADIUS_PX * SUPERSAMPLE,
                DOT_RADIUS_PX * SUPERSAMPLE,
            )
            continue

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(
            QPointF(center, center),
            # Qt has Y downwards, the scene upwards — hence the minus.
            QPointF(center + screen_x * arm, center - screen_y * arm),
        )


def _length(point: tuple[float, float]) -> float:
    return (point[0] ** 2 + point[1] ** 2) ** 0.5


def fit_icon(size_px: int = DEFAULT_ICON_PX) -> Any:
    """The icon for "show the whole model" — a square with corner marks."""
    scale = SUPERSAMPLE
    pixmap = QPixmap(size_px * scale, size_px * scale)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(90, 90, 95))
        pen.setWidthF(LINE_WIDTH_PX * scale)
        painter.setPen(pen)

        span = size_px * scale
        margin = span * 0.18
        corner = span * 0.22
        for x_sign, y_sign in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            corner_x = span / 2 + x_sign * (span / 2 - margin)
            corner_y = span / 2 + y_sign * (span / 2 - margin)
            painter.drawLine(
                QPointF(corner_x, corner_y), QPointF(corner_x - x_sign * corner, corner_y)
            )
            painter.drawLine(
                QPointF(corner_x, corner_y), QPointF(corner_x, corner_y - y_sign * corner)
            )
    finally:
        painter.end()

    pixmap.setDevicePixelRatio(float(scale))
    return QIcon(pixmap)
