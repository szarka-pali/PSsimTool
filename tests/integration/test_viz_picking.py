"""Tests of the click-to-pick wiring in Panda3D.

`PointPicker` needs a real `CollisionTraverser`/`CollisionRay`, so this runs
against a real (offscreen) `ShowBase`, mirroring `test_viz_orbit_control.py`.
Button events can be sent through the messenger the same way, but an offscreen
buffer has no real pointer device — mouse *position* cannot be simulated that
way, so a small fake stands in for `base.mouseWatcherNode` for the duration of
each test.

Requires `uv sync --extra viz`. Run with ``uv run pytest -m viz``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from pssim.viz.axes import BOX_EDGES, box_corners
from pssim.viz.camera import scene_radius
from pssim.viz.embed import offscreen_showbase
from pssim.viz.orbit import OrbitCamera
from pssim.viz.orbit_control import OrbitController
from pssim.viz.picking import PointPicker

pytestmark = pytest.mark.viz


@pytest.fixture(scope="module")
def base() -> Any:
    """An offscreen `ShowBase`. Panda3D allows one per process, hence module scope."""
    return offscreen_showbase((320, 240))


class _FakeMouseWatcher:
    """Stands in for `base.mouseWatcherNode` — an offscreen buffer has no real
    pointer device to move."""

    def __init__(self, position: tuple[float, float] | None) -> None:
        self._position = position

    def hasMouse(self) -> bool:  # noqa: N802 - Panda3D naming convention
        return self._position is not None

    def getMouseX(self) -> float:  # noqa: N802
        assert self._position is not None
        return self._position[0]

    def getMouseY(self) -> float:  # noqa: N802
        assert self._position is not None
        return self._position[1]


@pytest.fixture
def mouse_at(base: Any) -> Iterator[Any]:
    """`mouse_at(x, y)` points the (faked) mouse at normalized coordinates for
    the rest of the test; the real watcher is restored afterwards."""
    original = base.mouseWatcherNode

    def set_position(position: tuple[float, float] | None) -> None:
        base.mouseWatcherNode = _FakeMouseWatcher(position)

    yield set_position
    base.mouseWatcherNode = original


@pytest.fixture
def picker(base: Any) -> Iterator[PointPicker]:
    instance = PointPicker(base)
    instance.enable()
    yield instance
    instance.disable()


def box_node(parent: Any, low: tuple[float, float, float], high: tuple[float, float, float]) -> Any:
    from panda3d.core import LineSegs, NodePath

    corners = box_corners(low, high)
    lines = LineSegs("box")
    for start, end in BOX_EDGES:
        lines.moveTo(*corners[start])
        lines.drawTo(*corners[end])

    node = NodePath(lines.create())
    node.reparentTo(parent)
    return node


def model_in_front_of_camera(base: Any) -> Any:
    """A box 10 units down the Y axis, with the camera looking straight at it —
    normalized screen centre (0, 0) always lands on the box."""
    model = box_node(base.render, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    model.setPos(0.0, 10.0, 0.0)
    base.camera.setPos(0.0, 0.0, 0.0)
    base.camera.lookAt(0.0, 10.0, 0.0)
    return model


def click(base: Any, mouse_at: Any, x: float, y: float) -> None:
    """Press and release at a fixed position — a plain click, not a drag."""
    mouse_at((x, y))
    base.messenger.send("mouse1")
    base.messenger.send("mouse1-up")


class TestClickResolvesAPoint:
    def test_a_click_on_the_model_resolves_a_point(
        self, base: Any, mouse_at: Any, picker: PointPicker
    ) -> None:
        model = model_in_front_of_camera(base)
        picked: list[tuple[float, float, float]] = []
        picker.begin(model, picked.append)

        click(base, mouse_at, 0.0, 0.0)

        assert len(picked) == 1

    def test_the_point_is_expressed_in_the_models_own_frame(
        self, base: Any, mouse_at: Any, picker: PointPicker
    ) -> None:
        model = model_in_front_of_camera(base)
        picked: list[tuple[float, float, float]] = []
        picker.begin(model, picked.append)

        click(base, mouse_at, 0.0, 0.0)

        # The camera looks along +Y at the box's near face (local y = 0), from
        # outside the model entirely - the model's own local frame, not world.
        assert picked[0][1] == pytest.approx(0.0, abs=1e-4)

    def test_a_click_that_misses_resolves_nothing(
        self, base: Any, mouse_at: Any, picker: PointPicker
    ) -> None:
        model = model_in_front_of_camera(base)
        picked: list[tuple[float, float, float]] = []
        picker.begin(model, picked.append)

        click(base, mouse_at, 0.9, 0.9)  # aimed well away from the box

        assert picked == []

    def test_a_miss_leaves_picking_armed_for_another_try(
        self, base: Any, mouse_at: Any, picker: PointPicker
    ) -> None:
        model = model_in_front_of_camera(base)
        picked: list[tuple[float, float, float]] = []
        picker.begin(model, picked.append)

        click(base, mouse_at, 0.9, 0.9)  # misses
        click(base, mouse_at, 0.0, 0.0)  # hits

        assert len(picked) == 1

    def test_a_drag_does_not_resolve_a_point(
        self, base: Any, mouse_at: Any, picker: PointPicker
    ) -> None:
        model = model_in_front_of_camera(base)
        picked: list[tuple[float, float, float]] = []
        picker.begin(model, picked.append)

        mouse_at((0.0, 0.0))
        base.messenger.send("mouse1")
        mouse_at((0.5, 0.5))  # moved far before releasing - a drag, not a click
        base.messenger.send("mouse1-up")

        assert picked == []

    def test_cancel_disarms_picking(self, base: Any, mouse_at: Any, picker: PointPicker) -> None:
        model = model_in_front_of_camera(base)
        picked: list[tuple[float, float, float]] = []
        picker.begin(model, picked.append)

        picker.cancel()
        click(base, mouse_at, 0.0, 0.0)

        assert picked == []

    def test_without_arming_a_click_does_nothing(
        self, base: Any, mouse_at: Any, picker: PointPicker
    ) -> None:
        model_in_front_of_camera(base)

        click(base, mouse_at, 0.0, 0.0)  # never armed with begin()

        # Must not raise, and there is nothing to assert beyond that.


class TestCoexistsWithTheRestOfTheScene:
    """The two regressions that shipped with the first version of picking, and
    that no other test covered: an infinite pick ray breaking every scene-size
    computation, and this class stealing the camera's mouse events."""

    def test_the_pick_ray_does_not_corrupt_the_scene_radius(
        self, base: Any, picker: PointPicker
    ) -> None:
        # A CollisionRay is infinite. Attached under the camera - which lives in
        # `render` - it made `render.getBounds().getRadius()` trip a Panda3D
        # assertion, which killed EmbeddedRenderer.__init__ and left the whole
        # viewport empty.
        model = model_in_front_of_camera(base)
        model.setPos(0.0, 10.0, 0.0)

        _center, radius = scene_radius(base.render)

        assert radius > 0.0
        assert radius < 1e6  # finite and plausible, not infinite

    def test_scene_radius_falls_back_instead_of_raising_on_infinite_bounds(self) -> None:
        """The second line of defence: even if something unbounded does end up
        in the tree, the viewport must degrade to the fallback radius rather
        than take the window down with a Panda3D assertion."""
        from panda3d.core import NodePath, OmniBoundingVolume

        root = NodePath("unbounded")
        root.node().setBounds(OmniBoundingVolume())
        root.node().setFinal(True)

        _center, radius = scene_radius(root)

        assert radius > 0.0

    def test_the_camera_still_receives_mouse_events(self, base: Any) -> None:
        # `base.accept()` keys handlers by (event, object). Both controls
        # accepting "mouse1" on `base` itself silently replaced one another and
        # left-drag orbiting stopped working; `PointPicker` therefore owns its
        # own DirectObject.
        controller = OrbitController(
            base,
            OrbitCamera(
                target=(0.0, 0.0, 0.0), distance_m=10.0, min_distance_m=0.1, max_distance_m=100.0
            ),
        )
        controller.enable()
        instance = PointPicker(base)
        instance.enable()
        try:
            base.messenger.send("mouse1")

            # The orbit controller records a drag action on press; if picking
            # had overwritten its handler, this would still be NONE.
            from pssim.viz.orbit import DragAction

            assert controller.action is not DragAction.NONE
        finally:
            instance.disable()
            controller.disable()

    def test_disabling_picking_leaves_the_cameras_handlers_alone(self, base: Any) -> None:
        from pssim.viz.orbit import DragAction

        controller = OrbitController(base)
        controller.enable()
        instance = PointPicker(base)
        instance.enable()
        instance.disable()
        try:
            base.messenger.send("mouse1")

            assert controller.action is not DragAction.NONE
        finally:
            controller.disable()
