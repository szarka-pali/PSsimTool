"""Ikony kreslené za behu, bez binárnych assetov.

Ikona pohľadu ukazuje **osi X/Y/Z tak, ako ich z toho pohľadu naozaj vidno** —
z čelného pohľadu je X doprava a Z hore, zhora je X doprava a Y hore. Premietanie
robí tá istá kamera ako scéna (`viz.orbit.OrbitCamera.project`), takže ikona
nemôže tvrdiť niečo iné než to, čo sa po kliknutí stane.

Kreslenie za behu má dve výhody: v repozitári nie sú binárky a ikony sa
prispôsobia DPI displeja.
"""

from __future__ import annotations

from typing import Any, Final

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from pssim.viz.axes import AXIS_COLORS, AXIS_DIRECTIONS
from pssim.viz.orbit import OrbitCamera

DEFAULT_ICON_PX: Final = 24

#: Vykresľujeme vo väčšom rozlíšení a zmenšíme — inak sú šikmé čiary zubaté.
SUPERSAMPLE: Final = 2

#: Dĺžka ramena ako podiel polovice ikony.
ARM_RATIO: Final = 0.72

#: Os kratšia než toto mieri (skoro) do obrazovky a kreslí sa ako bodka.
INTO_SCREEN_THRESHOLD: Final = 0.2

LINE_WIDTH_PX: Final = 2.0
DOT_RADIUS_PX: Final = 2.0


def view_icon(view: str, size_px: int = DEFAULT_ICON_PX) -> QIcon:
    """Ikona štandardného pohľadu — premietnuté osi.

    Neznámy názov pohľadu je `ValueError` (vyhodí ho `with_view`).
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

    # Osi mieriace do obrazovky kreslíme prvé, aby ich tie viditeľné prekryli.
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
            # Os mieri do obrazovky — z tohto pohľadu je to bod, nie čiara.
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
            # Qt má Y nadol, scéna nahor — preto mínus.
            QPointF(center + screen_x * arm, center - screen_y * arm),
        )


def _length(point: tuple[float, float]) -> float:
    return (point[0] ** 2 + point[1] ** 2) ** 0.5


def fit_icon(size_px: int = DEFAULT_ICON_PX) -> Any:
    """Ikona pre „zobraz celý model" — štvorec s rohovými značkami."""
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
