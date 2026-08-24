"""Tests of the drawn item icons.

The failure a hand-drawn icon actually has is being blank, or being the same
picture as its neighbour. Both are checked here for every kind; what the drawing
*looks* like is not something a test can judge.

Runs headless. Requires `uv sync --extra ui`. Run with ``uv run pytest -m ui``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pssim.domain.errors import ConfigError  # noqa: E402
from pssim.domain.model_joints import ModelJointKind  # noqa: E402
from pssim.domain.sensors import SensorKind  # noqa: E402
from pssim.ui.icons import (  # noqa: E402
    APP_ICON_SIZES,
    DEFAULT_ICON_PX,
    app_icon,
    fit_icon,
    joint_icon,
    model_icon,
    sensor_icon,
    view_icon,
    write_app_icon,
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


def saturation(icon: object, size_px: int = DEFAULT_ICON_PX) -> int:
    """The strongest colour cast among the painted pixels.

    Zero for a drawing made only of the palette's ink, because ink is grey and
    grey has equal channels. Anything above a few counts is a real accent rather
    than an anti-aliasing artefact.
    """
    image = icon.pixmap(size_px, size_px).toImage()  # type: ignore[attr-defined]
    strongest = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() == 0:
                continue
            channels = (pixel.red(), pixel.green(), pixel.blue())
            strongest = max(strongest, max(channels) - min(channels))
    return strongest


class TestColour:
    """Iteration 2: structure in the palette's ink, identity in colour."""

    def test_the_model_icon_is_coloured(self, qt_app: QApplication) -> None:
        assert saturation(model_icon()) > 40

    @pytest.mark.parametrize("kind", list(SensorKind))
    def test_every_sensor_kind_is_coloured(self, qt_app: QApplication, kind: SensorKind) -> None:
        assert saturation(sensor_icon(kind)) > 40

    @pytest.mark.parametrize("kind", list(ModelJointKind))
    def test_every_joint_kind_is_coloured(self, qt_app: QApplication, kind: ModelJointKind) -> None:
        assert saturation(joint_icon(kind)) > 40

    def test_the_toolbar_icons_stay_in_ink(self, qt_app: QApplication) -> None:
        # `fit_icon` is a control, not an item: it has no identity to signal, and
        # a coloured one would compete with the rows beside it.
        assert saturation(fit_icon()) < 40


class TestApplicationIcon:
    def test_it_offers_every_size(self, qt_app: QApplication) -> None:
        # Each is drawn at its own size rather than scaled from one bitmap: a
        # 16 px icon downsampled from 256 is mud, and 16 px is what a taskbar
        # uses most.
        available = {size.width() for size in app_icon().availableSizes()}

        assert available == set(APP_ICON_SIZES)

    def test_the_small_size_is_not_blank(self, qt_app: QApplication) -> None:
        assert painted_pixels(app_icon(), 16) > 0

    def test_it_is_coloured(self, qt_app: QApplication) -> None:
        assert saturation(app_icon(), 64) > 40

    def test_it_is_opaque(self, qt_app: QApplication) -> None:
        # Unlike the item icons, which are transparent line drawings: an
        # application icon owns its own ground, because it is seen on a taskbar
        # rather than on this application's own background.
        image = app_icon().pixmap(64, 64).toImage()

        assert image.pixelColor(32, 32).alpha() == 255

    def test_it_can_be_written_to_a_file(self, qt_app: QApplication, tmp_path: Path) -> None:
        # No binary is committed (R17); this is how a packaging step gets one
        # that cannot drift from the drawing.
        written = write_app_icon(tmp_path / "pssim.ico")

        assert written.exists() and written.stat().st_size > 0

    def test_the_written_icon_is_the_largest_size(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        write_app_icon(tmp_path / "pssim.png")

        assert QImage(str(tmp_path / "pssim.png")).width() == max(APP_ICON_SIZES)

    def test_an_unwritable_path_is_a_typed_error(
        self, qt_app: QApplication, tmp_path: Path
    ) -> None:
        with pytest.raises(ConfigError):
            write_app_icon(tmp_path / "missing" / "pssim.ico")
