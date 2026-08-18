"""Rendering tests — they verify that the machine can actually BE SEEN.

This is the one class of tests that would catch "an empty window opens". Tests of node
positions pass even when the camera points elsewhere, sits at the origin, or the whole
scene is behind the near plane.

The render goes to a file through an offscreen buffer; no window opens.

Requires `uv sync --extra viz --extra cad`. Run with: ``uv run pytest -m viz``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pssim.cad.step_import import ImportSettings, import_step
from pssim.config.loader import load_machine
from pssim.io.store import StateStore
from pssim.viz.app import MachineViewer, ViewerConfig

pytestmark = [pytest.mark.viz, pytest.mark.cad]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_MACHINE = PROJECT_ROOT / "machines" / "demo.yaml"
FIXTURE = PROJECT_ROOT / "tests" / "data" / "fixture.step"

RENDER_SIZE = (320, 240)


class StubSource:
    """A source with no data — rendering needs no PLC."""

    def __init__(self) -> None:
        self._store = StateStore()

    @property
    def status(self) -> object:
        from pssim.io.base import SourceStatus

        return SourceStatus.DISCONNECTED

    @property
    def store(self) -> StateStore:
        return self._store

    def start(self) -> None: ...

    def stop(self) -> None: ...


def render(tmp_path: Path, *, view: str = "back", values: dict[str, float] | None = None) -> Path:
    """Import the fixture, build the scene and render it into a PNG."""
    cache_root = tmp_path / "cache"
    loaded = load_machine(DEMO_MACHINE, project_root=PROJECT_ROOT)
    settings = ImportSettings(
        step_file=FIXTURE,
        scale_to_m=loaded.scale_to_m,
        units=loaded.units,
        linear_deflection_mm=loaded.linear_deflection_mm,
        angular_deflection_rad=loaded.angular_deflection_rad,
    )
    metadata = import_step(settings, cache_root)

    viewer = MachineViewer(
        loaded,
        metadata.assembly,
        StubSource(),  # type: ignore[arg-type]
        cache_root / metadata.key.digest,
        ViewerConfig(background=(0.0, 0.0, 0.0)),
    )
    return viewer.render_screenshot(
        tmp_path / "render.png", size=RENDER_SIZE, view=view, values=values
    )


def pixel_stats(path: Path) -> tuple[int, int]:
    """Return `(the number of non-background pixels, the total)`.

    The background is black, so "non-background" means any lighter pixel.
    """
    from panda3d.core import Filename, PNMImage

    image = PNMImage()
    assert image.read(Filename.fromOsSpecific(str(path))), f"the PNG cannot be read: {path}"

    lit = 0
    total = image.getXSize() * image.getYSize()
    for x in range(image.getXSize()):
        for y in range(image.getYSize()):
            if max(image.getXel(x, y)) > 0.05:
                lit += 1
    return lit, total


class TestRenderIsNotEmpty:
    def test_the_file_is_created(self, tmp_path: Path) -> None:
        assert render(tmp_path).is_file()

    def test_obrazok_ma_rozmery(self, tmp_path: Path) -> None:
        # The exact dimensions are not checked: Panda3D allows one ShowBase per process,
        # so the buffer size is decided by the first render in the whole test run.
        from panda3d.core import Filename, PNMImage

        image = PNMImage()
        image.read(Filename.fromOsSpecific(str(render(tmp_path))))

        assert image.getXSize() > 0
        assert image.getYSize() > 0

    def test_the_machine_is_visible(self, tmp_path: Path) -> None:
        # THIS is the test. An empty window = 0 non-background pixels.
        lit, total = pixel_stats(render(tmp_path))

        assert lit > 0, (
            "the render is empty - the camera points elsewhere or the scene is behind the near plane"
        )
        assert lit > total * 0.01, f"only {lit}/{total} pixels are lit, the machine is out of frame"

    def test_the_machine_does_not_fill_the_whole_frame(self, tmp_path: Path) -> None:
        # If the near plane were too close and the camera inside the model, it would
        # fill the whole image and nothing would be recognisable.
        lit, total = pixel_stats(render(tmp_path))

        assert lit < total * 0.95, "the machine fills the whole frame - the camera is too close"

    @pytest.mark.parametrize("view", ["iso", "front", "back", "left", "right", "top"])
    def test_every_view_shows_something(self, tmp_path: Path, view: str) -> None:
        lit, _ = pixel_stats(render(tmp_path, view=view))

        assert lit > 0, f"the {view!r} view is empty"


class TestMovementIsVisible:
    def test_iny_stav_osi_da_iny_obrazok(self, tmp_path: Path) -> None:
        # If joint values did not reach the scene, the images would be identical.
        rest = pixel_stats(render(tmp_path / "a"))
        moved = pixel_stats(render(tmp_path / "b", values={"axis_x": 1.5, "axis_z": 0.5}))

        assert rest[0] != moved[0]
