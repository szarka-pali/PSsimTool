"""Tests of the mouse-to-camera wiring in Panda3D.

The control maths is covered by `tests/unit/viz/test_orbit.py`. This is about whether
the events are actually **connected** — whether `wheel_up` really reaches the camera and
whether pressing a button selects the right action. That is the part a unit test cannot
cover, because it needs a live Panda3D.

The events are sent through `messenger`, so no real mouse is needed. No window is
opened — it runs over an offscreen buffer.

Requires `uv sync --extra viz`. Run with: ``uv run pytest -m viz``
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from pssim.viz.embed import offscreen_showbase
from pssim.viz.orbit import DragAction, OrbitCamera
from pssim.viz.orbit_control import OrbitController

pytestmark = pytest.mark.viz


@pytest.fixture(scope="module")
def base() -> Any:
    """An offscreen `ShowBase`. Panda3D allows one per process, hence module scope."""
    return offscreen_showbase((320, 240))


@pytest.fixture
def controller(base: Any) -> Iterator[OrbitController]:
    """The controller enabled, with a predictable initial camera."""
    instance = OrbitController(
        base,
        OrbitCamera(
            target=(0.0, 0.0, 0.0), distance_m=10.0, min_distance_m=0.1, max_distance_m=100.0
        ),
    )
    instance.enable()
    yield instance
    instance.disable()


def send(base: Any, event: str) -> None:
    """Send an event the way it would arrive from the mouse."""
    base.messenger.send(event)


class TestWheel:
    def test_wheel_up_priblizi(self, base: Any, controller: OrbitController) -> None:
        before = controller.camera.distance_m

        send(base, "wheel_up")

        assert controller.camera.distance_m < before

    def test_wheel_down_oddiali(self, base: Any, controller: OrbitController) -> None:
        before = controller.camera.distance_m

        send(base, "wheel_down")

        assert controller.camera.distance_m > before

    def test_the_wheel_moves_the_camera_in_the_scene(
        self, base: Any, controller: OrbitController
    ) -> None:
        # Changing the model is not enough — it has to reach the Panda3D camera too.
        before = base.camera.getPos().length()

        send(base, "wheel_up")

        assert base.camera.getPos().length() < before

    def test_koliesko_nemeni_ciel(self, base: Any, controller: OrbitController) -> None:
        send(base, "wheel_up")

        assert controller.camera.target == (0.0, 0.0, 0.0)


class TestButtons:
    def test_the_middle_button_starts_orbiting(
        self, base: Any, controller: OrbitController
    ) -> None:
        send(base, "mouse2")

        assert controller.action is DragAction.ORBIT

    def test_shift_with_the_middle_button_starts_panning(
        self, base: Any, controller: OrbitController
    ) -> None:
        send(base, "shift-mouse2")

        assert controller.action is DragAction.PAN

    def test_the_right_button_starts_panning(self, base: Any, controller: OrbitController) -> None:
        send(base, "mouse3")

        assert controller.action is DragAction.PAN

    def test_releasing_ends_the_drag(self, base: Any, controller: OrbitController) -> None:
        send(base, "mouse2")

        send(base, "mouse2-up")

        assert controller.action is DragAction.NONE


class TestReachingTheScene:
    def test_the_camera_stands_at_the_right_distance(
        self, controller: OrbitController, base: Any
    ) -> None:
        controller.set_camera(
            OrbitCamera(target=(1.0, 2.0, 3.0), distance_m=5.0, azimuth_rad=0.0, elevation_rad=0.0)
        )

        position = base.camera.getPos()

        assert (position[0], position[1], position[2]) == pytest.approx((1.0, -3.0, 3.0), abs=1e-4)

    def test_the_clip_planes_follow_the_scale(self, controller: OrbitController, base: Any) -> None:
        # A small model needs a small near plane, or all of it gets clipped.
        controller.set_camera(OrbitCamera(target=(0.0, 0.0, 0.0), distance_m=0.2))

        assert base.camLens.getNear() < 0.2

    def test_vypnutie_odpoji_udalosti(self, base: Any, controller: OrbitController) -> None:
        controller.disable()
        before = controller.camera.distance_m

        send(base, "wheel_up")

        assert controller.camera.distance_m == before
