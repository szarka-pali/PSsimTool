"""Tests of the drawn item icons.

The failure a hand-drawn icon actually has is being blank, or being the same
picture as its neighbour. Both are checked here for every kind; what the drawing
*looks* like is not something a test can judge.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.domain.model_joints import ModelJointKind  # noqa: E402
from pssim.domain.sensors import SensorKind  # noqa: E402
from pssim.ui.icons import (  # noqa: E402
    DEFAULT_ICON_PX,
    fit_icon,
    joint_icon,
    model_icon,
    sensor_icon,
    view_icon,
)

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


def painted_pixels(icon: object, size_px: int = DEFAULT_ICON_PX) -> int:
    """How many pixels the drawing actually touched."""
    image = icon.pixmap(size_px, size_px).toImage()  # type: ignore[attr-defined]
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    )


def fingerprint(icon: object, size_px: int = DEFAULT_ICON_PX) -> bytes:
    image = icon.pixmap(size_px, size_px).toImage()  # type: ignore[attr-defined]
    return bytes(image.constBits())


class TestSomethingIsDrawn:
    def test_the_model_icon_is_not_blank(self, qt_app: QApplication) -> None:
        assert painted_pixels(model_icon()) > 0

    @pytest.mark.parametrize("kind", list(ModelJointKind))
    def test_every_joint_kind_is_drawn(self, qt_app: QApplication, kind: ModelJointKind) -> None:
        assert painted_pixels(joint_icon(kind)) > 0

    @pytest.mark.parametrize("kind", list(SensorKind))
    def test_every_sensor_kind_is_drawn(self, qt_app: QApplication, kind: SensorKind) -> None:
        assert painted_pixels(sensor_icon(kind)) > 0

    def test_the_toolbar_icons_still_draw(self, qt_app: QApplication) -> None:
        assert painted_pixels(fit_icon()) > 0
        assert painted_pixels(view_icon("iso")) > 0


class TestEveryKindLooksDifferent:
    def test_no_two_sensor_kinds_share_a_picture(self, qt_app: QApplication) -> None:
        # Including the pairs whose maths is identical — the icon is the shortest
        # way to read which part the machine actually has (R16).
        seen = {kind: fingerprint(sensor_icon(kind)) for kind in SensorKind}

        assert len(set(seen.values())) == len(SensorKind)

    def test_an_axis_and_a_trajectory_differ(self, qt_app: QApplication) -> None:
        axis = fingerprint(joint_icon(ModelJointKind.AXIS))
        trajectory = fingerprint(joint_icon(ModelJointKind.TRAJECTORY))

        assert axis != trajectory

    def test_a_model_is_not_a_joint(self, qt_app: QApplication) -> None:
        assert fingerprint(model_icon()) != fingerprint(joint_icon(ModelJointKind.AXIS))


class TestCaching:
    def test_the_same_icon_is_handed_back(self, qt_app: QApplication) -> None:
        # A tree row asks on every refresh; repainting per row per refresh is
        # work with no result.
        assert model_icon() is model_icon()

    def test_a_different_size_is_a_different_icon(self, qt_app: QApplication) -> None:
        assert model_icon(16) is not model_icon(32)

    def test_the_requested_size_is_honoured(self, qt_app: QApplication) -> None:
        pixmap = sensor_icon(SensorKind.BEAM, 32).pixmap(32, 32)

        assert pixmap.width() == 32
