"""Icons drawn at run time, with no binary assets.

A view icon shows the **X/Y/Z axes as they are really seen from that view** — from the front
X is to the right and Z is up, from the top X is to the right and Y is up. The projection is
done by the same camera as the scene (`viz.orbit.OrbitCamera.project`), so an icon cannot
claim anything other than what happens after the click.

The item icons — a model, an axis, a trajectory, one per sensor kind — are line drawings of
the same kind. They are what the menus and both trees put beside a name, so a row can be
recognised without reading it.

They are drawn to one rule: **structure in the palette's ink, identity in colour.** The outline
of a shape takes `WindowText`, so it is legible on a light theme and on a dark one alike; the
one part that says *which* kind it is takes an accent from `_ACCENTS`, mid-toned so it reads
against white and against near-black without a second palette. Colour alone would fail on one
of the two themes; ink alone is what iteration 1 was, and seven sensor kinds in grey are hard
to tell apart at 24 px.

Drawing at run time rather than shipping files has three advantages: there are no binaries in
the repository to license or to keep in step with the code, the icons adapt to the display's
DPI, and the pen colour comes from the running palette — a fixed grey is invisible on one
theme or the other.

Icons are **cached** per kind and size. A tree row asks for its icon on every refresh, and
repainting a pixmap per row per refresh is work with no result.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cache
from typing import Any, Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from pssim.domain.model_joints import ModelJointKind
from pssim.domain.sensors import SensorKind
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

#: The accents. Mid-toned on purpose: a colour light enough to glow on a dark theme washes out
#: on a light one, and the reverse. These sit near the middle and read on both.
MODEL_BLUE: Final = QColor(70, 130, 200)
LASER_RED: Final = QColor(214, 64, 64)
COIL_COPPER: Final = QColor(198, 124, 48)
PULSE_CYAN: Final = QColor(38, 166, 178)
ENCODER_VIOLET: Final = QColor(146, 96, 200)
ZONE_GREEN: Final = QColor(72, 160, 88)


def _axis_color(name: str) -> QColor:
    """One of the scene's own axis colours, so an icon and the cross it stands
    for are recognisably the same thing."""
    red, green, blue, _alpha = AXIS_COLORS[name]
    return QColor.fromRgbF(red, green, blue)


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


def _ink() -> QColor:
    """The pen colour, taken from the running palette.

    Not a fixed grey: the same value cannot be legible on a light theme and on a
    dark one, and an icon nobody can see is worse than no icon.
    """
    return QApplication.palette().color(QPalette.ColorRole.WindowText)


def _drawn_icon(size_px: int, draw: Callable[[QPainter, float], None]) -> QIcon:
    """Run `draw` on a transparent, supersampled pixmap and wrap it as an icon.

    `draw` is handed the painter and the **span** it may use — the supersampled
    edge length — so every drawing below is written in fractions of its own box
    and needs no knowledge of the scale factor.
    """
    scale = SUPERSAMPLE
    span = size_px * scale
    pixmap = QPixmap(span, span)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(_ink())
        pen.setWidthF(LINE_WIDTH_PX * scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        draw(painter, float(span))
    finally:
        painter.end()

    pixmap.setDevicePixelRatio(float(scale))
    return QIcon(pixmap)


def _dot(painter: QPainter, center: QPointF, radius: float, color: QColor | None = None) -> None:
    """A filled dot, in the current pen colour unless another is given."""
    painter.save()
    filled = color if color is not None else painter.pen().color()
    painter.setPen(QPen(filled, painter.pen().widthF()))
    painter.setBrush(QBrush(filled))
    painter.drawEllipse(center, radius, radius)
    painter.restore()


def _accent(painter: QPainter, color: QColor) -> QPen:
    """The current pen recoloured. Width, cap and dash are kept, so an accented
    stroke sits at the same weight as the ink around it."""
    pen = QPen(painter.pen())
    pen.setColor(color)
    return pen


def _dashed(painter: QPainter) -> QPen:
    """A dashed copy of the current pen — for a beam, which is light, not metal."""
    pen = QPen(painter.pen())
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern([2.0, 2.0])
    return pen


def _arrow_head(painter: QPainter, tip: QPointF, angle_rad: float, size: float) -> None:
    """A two-stroke arrowhead at `tip`, pointing along `angle_rad`."""
    for spread in (2.5, -2.5):
        painter.drawLine(
            tip,
            QPointF(
                tip.x() + size * math.cos(angle_rad + spread),
                tip.y() + size * math.sin(angle_rad + spread),
            ),
        )


def fit_icon(size_px: int = DEFAULT_ICON_PX) -> Any:
    """The icon for "show the whole model" — a square with corner marks."""

    def draw(painter: QPainter, span: float) -> None:
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

    return _drawn_icon(size_px, draw)


# -- the items ---------------------------------------------------------------


@cache
def model_icon(size_px: int = DEFAULT_ICON_PX) -> QIcon:
    """A model: an isometric cube — a hexagon with its three visible edges.

    A cube rather than a file glyph: what the tree lists is geometry in the
    scene, not the STEP file it came out of.
    """

    def draw(painter: QPainter, span: float) -> None:
        center = QPointF(span / 2, span / 2)
        radius = span * 0.36
        corners = [
            QPointF(
                center.x() + radius * math.cos(math.radians(30 + 60 * step)),
                center.y() + radius * math.sin(math.radians(30 + 60 * step)),
            )
            for step in range(6)
        ]
        # One face filled, so the cube reads as a solid at 16 px where three
        # inner lines alone are just a busy hexagon.
        painter.save()
        painter.setBrush(QBrush(MODEL_BLUE))
        painter.setPen(_accent(painter, MODEL_BLUE))
        painter.drawPolygon([center, corners[5], corners[0], corners[1]])
        painter.restore()

        for index, corner in enumerate(corners):
            painter.drawLine(corner, corners[(index + 1) % 6])
        # The three edges meeting at the middle are what makes it read as solid
        # rather than as a flat hexagon.
        for index in (1, 3, 5):
            painter.drawLine(center, corners[index])

    return _drawn_icon(size_px, draw)


@cache
def joint_icon(kind: ModelJointKind, size_px: int = DEFAULT_ICON_PX) -> QIcon:
    """An axis or a trajectory — the two are drawn as the two motions they are."""
    if kind is ModelJointKind.AXIS:
        return _axis_icon(size_px)
    return _trajectory_icon(size_px)


def _axis_icon(size_px: int) -> QIcon:
    """Rotation: a shaft with a turn around it.

    The shaft takes the scene's Z colour and the turn takes X's, because that is
    what an axis marker looks like in the viewport — the icon and the thing it
    stands for should be recognisably each other.
    """

    def draw(painter: QPainter, span: float) -> None:
        center = span / 2
        painter.save()
        painter.setPen(_accent(painter, _axis_color("Z")))
        painter.drawLine(QPointF(center, span * 0.12), QPointF(center, span * 0.88))
        painter.restore()

        painter.save()
        painter.setPen(_accent(painter, _axis_color("X")))
        # A flattened ellipse is a circle seen at an angle — the turn the shaft makes.
        radius_x = span * 0.32
        radius_y = span * 0.14
        painter.drawArc(
            QRectF(center - radius_x, center - radius_y, radius_x * 2, radius_y * 2),
            30 * 16,
            300 * 16,
        )
        _arrow_head(
            painter, QPointF(center + radius_x * 0.86, center - radius_y * 0.5), 1.9, span * 0.16
        )
        painter.restore()

    return _drawn_icon(size_px, draw)


def _trajectory_icon(size_px: int) -> QIcon:
    """Travel: a path from a start to a far end, with both ends marked.

    The path takes the scene's X colour — the direction of travel — and the end
    stops stay ink, because they are structure rather than identity.
    """

    def draw(painter: QPainter, span: float) -> None:
        center = span / 2
        start = span * 0.16
        end = span * 0.84
        for x in (start, end):
            painter.drawLine(QPointF(x, center - span * 0.18), QPointF(x, center + span * 0.18))

        painter.save()
        painter.setPen(_accent(painter, _axis_color("X")))
        painter.drawLine(QPointF(start, center), QPointF(end, center))
        _arrow_head(painter, QPointF(end, center), 0.0, span * 0.18)
        painter.restore()

    return _drawn_icon(size_px, draw)


@cache
def sensor_icon(kind: SensorKind, size_px: int = DEFAULT_ICON_PX) -> QIcon:
    """One drawing per sensor kind.

    Every kind gets its own, including the pairs whose maths is identical
    (`BEAM`/`INDUCTIVE`, `TOF`/`LASER_DISTANCE`): the kind is how the machine is
    documented (R16), and the icon is the shortest way to read which part it is.
    """
    return _SENSOR_DRAWINGS[kind](size_px)


def _beam_icon(size_px: int) -> QIcon:
    """A photoelectric beam: an emitter and light crossing a gap to a receiver."""

    def draw(painter: QPainter, span: float) -> None:
        center = span / 2
        _dot(painter, QPointF(span * 0.18, center), span * 0.09)
        painter.drawLine(QPointF(span * 0.82, span * 0.22), QPointF(span * 0.82, span * 0.78))
        # The emitter and the receiver are hardware and stay ink; the light
        # between them is the part that is red.
        painter.save()
        painter.setPen(_accent(painter, LASER_RED))
        painter.setPen(_dashed(painter))
        painter.drawLine(QPointF(span * 0.30, center), QPointF(span * 0.78, center))
        painter.restore()

    return _drawn_icon(size_px, draw)


def _inductive_icon(size_px: int) -> QIcon:
    """An inductive probe: a coil behind its sensing face."""

    def draw(painter: QPainter, span: float) -> None:
        center = span / 2
        body = QRectF(span * 0.16, span * 0.28, span * 0.34, span * 0.44)
        painter.drawRect(body)
        painter.save()
        painter.setPen(_accent(painter, COIL_COPPER))
        # The windings, which is what makes it a coil rather than a box — and
        # copper, which is what makes it inductive rather than photoelectric.
        for fraction in (0.35, 0.5, 0.65):
            y = span * fraction
            painter.drawLine(QPointF(body.left(), y), QPointF(body.right(), y))
        for radius in (span * 0.16, span * 0.26):
            painter.drawArc(
                QRectF(span * 0.5 - radius, center - radius, radius * 2, radius * 2),
                -55 * 16,
                110 * 16,
            )
        painter.restore()

    return _drawn_icon(size_px, draw)


def _tof_icon(size_px: int) -> QIcon:
    """Time of flight: a pulse going out and a surface it comes back from."""

    def draw(painter: QPainter, span: float) -> None:
        center = span / 2
        _dot(painter, QPointF(span * 0.16, center), span * 0.09)
        painter.drawLine(QPointF(span * 0.86, span * 0.20), QPointF(span * 0.86, span * 0.80))
        painter.save()
        painter.setPen(_accent(painter, PULSE_CYAN))
        for radius in (span * 0.20, span * 0.32, span * 0.44):
            painter.drawArc(
                QRectF(span * 0.16 - radius, center - radius, radius * 2, radius * 2),
                -50 * 16,
                100 * 16,
            )
        painter.restore()

    return _drawn_icon(size_px, draw)


def _laser_distance_icon(size_px: int) -> QIcon:
    """A rangefinder: a straight ray with the distance it reports dimensioned."""

    def draw(painter: QPainter, span: float) -> None:
        ray_y = span * 0.36
        _dot(painter, QPointF(span * 0.16, ray_y), span * 0.08, LASER_RED)
        painter.drawLine(QPointF(span * 0.86, span * 0.18), QPointF(span * 0.86, span * 0.82))
        painter.save()
        painter.setPen(_accent(painter, LASER_RED))
        painter.drawLine(QPointF(span * 0.22, ray_y), QPointF(span * 0.84, ray_y))
        painter.restore()

        # The dimension line underneath is what says "it reports how far", rather
        # than only "there is a beam".
        measure_y = span * 0.72
        painter.drawLine(QPointF(span * 0.20, measure_y), QPointF(span * 0.82, measure_y))
        _arrow_head(painter, QPointF(span * 0.20, measure_y), math.pi, span * 0.14)
        _arrow_head(painter, QPointF(span * 0.82, measure_y), 0.0, span * 0.14)

    return _drawn_icon(size_px, draw)


def _encoder_icon(size_px: int, has_index: bool) -> QIcon:
    """A rotary encoder: a disc of counts. The absolute one has an index mark.

    That mark is the whole difference between the two devices — an absolute
    encoder knows where zero is, an incremental one only counts from wherever it
    started.
    """

    def draw(painter: QPainter, span: float) -> None:
        center = QPointF(span / 2, span / 2)
        radius = span * 0.30
        painter.save()
        painter.setPen(_accent(painter, ENCODER_VIOLET))
        painter.drawEllipse(center, radius, radius)
        painter.restore()
        for step in range(8):
            angle = math.radians(45 * step)
            painter.drawLine(
                QPointF(
                    center.x() + radius * math.cos(angle),
                    center.y() + radius * math.sin(angle),
                ),
                QPointF(
                    center.x() + radius * 1.36 * math.cos(angle),
                    center.y() + radius * 1.36 * math.sin(angle),
                ),
            )
        if has_index:
            _dot(
                painter,
                QPointF(center.x(), center.y() - radius * 0.55),
                span * 0.07,
                ENCODER_VIOLET,
            )

    return _drawn_icon(size_px, draw)


def _proximity_icon(size_px: int) -> QIcon:
    """A zone: a box that something either is or is not inside."""

    def draw(painter: QPainter, span: float) -> None:
        painter.save()
        painter.setPen(_accent(painter, ZONE_GREEN))
        painter.setPen(_dashed(painter))
        painter.drawRect(QRectF(span * 0.18, span * 0.18, span * 0.64, span * 0.64))
        painter.restore()
        # The dot is what is *in* the zone, so it stays ink — the zone is the
        # part that is green.
        _dot(painter, QPointF(span / 2, span / 2), span * 0.10)

    return _drawn_icon(size_px, draw)


#: One drawing per kind. Every kind is listed on purpose — a missing one would be
#: a `KeyError` on a tree row, and a lookup with a fallback would quietly give
#: two kinds the same picture.
_SENSOR_DRAWINGS: Final[dict[SensorKind, Callable[[int], QIcon]]] = {
    SensorKind.BEAM: _beam_icon,
    SensorKind.INDUCTIVE: _inductive_icon,
    SensorKind.TOF: _tof_icon,
    SensorKind.LASER_DISTANCE: _laser_distance_icon,
    SensorKind.ENCODER_INC: lambda size_px: _encoder_icon(size_px, has_index=False),
    SensorKind.ENCODER_ABS: lambda size_px: _encoder_icon(size_px, has_index=True),
    SensorKind.PROXIMITY: _proximity_icon,
}
