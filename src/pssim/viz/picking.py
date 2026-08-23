"""Picking a point on a model by clicking in the viewport.

Coexists with `viz/orbit_control.py`'s claim on `mouse1`/`mouse1-up` by owning
its **own `DirectObject`** to accept events through, rather than calling
`base.accept(...)`. That distinction is the whole reason this works: Panda3D's
messenger keys handlers by *(event, receiving object)*, so two `accept` calls
for the same event **on the same object** — and `base` is one object —
silently replace one another, which would leave the camera unable to orbit.
Two different `DirectObject`s both receive the event. (Measured both ways; the
overwrite is real and cost a debugging session.)

The two controls are told apart by how far the mouse moved between press and
release — within a small pixel tolerance is a click (a pick), anything more was
a drag (orbiting already handled it).

**Deviation from the original plan, found by testing against the real Panda3D
API rather than assuming it**: `CollisionTraverser` does not resolve hits against
plain visible `GeomNode` geometry — only against explicit `CollisionSolid`s inside
a `CollisionNode`. (Verified: a ray with a matching collide mask against a bare
`GeomNode` finds zero entries; the identical ray against a `CollisionBox` finds
the expected hit. A `GeomNode`'s own `getIntoCollideMask()` exists for a different
purpose and does not make it collidable on its own.) Authoring per-triangle
collision polygons for a full CAD assembly would be exactly the new
collision-proxy pipeline the plan wanted to avoid, so instead this builds one
*temporary* `CollisionBox` from the model's own tight bounds — the same bounds
`viz.sensor_markers.aabb_of` already computes for sensors — for the duration of a
single pick, in the model's own local frame so it moves with the model
automatically and needs no upkeep between picks. The result is a point on the
model's bounding box, not its exact mesh surface: coarser than true polygon
precision, but consistent with this project's established preference for plain
AABB math over exact collision geometry (docs/architecture.md R14, and the sensor
feature's own reasoning).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Final

from pssim.domain.machine import Vec3
from pssim.observability import get_logger

logger = get_logger(__name__)

#: Press/release pixels within this distance of each other count as a click, not
#: a drag — matches ordinary mouse jitter without accepting a real orbit drag as
#: if it were a pick.
CLICK_TOLERANCE_PX: Final = 4.0

#: A `CollisionBox` cannot have a zero-thickness dimension (an assertion failure
#: inside Panda3D itself) — a model whose tight bounds are exactly flat on one
#: axis (a thin plate) is padded to at least this half-thickness on that axis.
MIN_HALF_THICKNESS_M: Final = 1e-3


def padded_box_bounds(low: Vec3, high: Vec3) -> tuple[Vec3, Vec3]:
    """Widen any axis narrower than `2 * MIN_HALF_THICKNESS_M` so a `CollisionBox`
    built from the result never has a degenerate dimension. Pure function,
    testable without Panda3D."""
    lx, ly, lz = low
    hx, hy, hz = high
    pairs = []
    for lo, hi in ((lx, hx), (ly, hy), (lz, hz)):
        if hi - lo < 2.0 * MIN_HALF_THICKNESS_M:
            center = (lo + hi) / 2.0
            lo, hi = center - MIN_HALF_THICKNESS_M, center + MIN_HALF_THICKNESS_M
        pairs.append((lo, hi))
    return (pairs[0][0], pairs[1][0], pairs[2][0]), (pairs[0][1], pairs[1][1], pairs[2][1])


def _pixel_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


class PointPicker:
    """Resolves a point on one model's bounding box from the next plain click."""

    def __init__(self, base: Any) -> None:
        self._base = base
        self._enabled = False
        self._press_pixel: tuple[float, float] | None = None
        self._root: Any | None = None
        self._on_point_picked: Callable[[Vec3], None] | None = None
        self._proxy: Any | None = None

        from direct.showbase.DirectObject import DirectObject
        from panda3d.core import (
            BoundingSphere,
            CollisionHandlerQueue,
            CollisionNode,
            CollisionRay,
            CollisionTraverser,
            Point3,
        )

        # Its own receiver, never `base` itself — see the module docstring: two
        # `accept`s of one event on one object overwrite each other, and the
        # orbit controller already accepts these same events on `base`.
        self._events = DirectObject()

        self._traverser = CollisionTraverser("pssim-picking")
        self._ray = CollisionRay()
        ray_node = CollisionNode("pssim-picker-ray")
        ray_node.addSolid(self._ray)
        # A `CollisionRay` is **infinite**, and this node hangs under the camera,
        # which is part of `render`. Left alone, that makes `render.getBounds()`
        # infinite, and `viz.camera.scene_radius`'s `getRadius()` then trips a
        # Panda3D assertion — which, raised from `_refresh_floor()` in
        # `EmbeddedRenderer.__init__`, killed the whole viewport and left nothing
        # on screen at all. Explicit finite bounds, marked final so Panda3D never
        # re-expands them from the solid inside, keep the ray out of every
        # scene-size computation while still colliding normally.
        ray_node.setBounds(BoundingSphere(Point3(0.0, 0.0, 0.0), 0.0))
        ray_node.setFinal(True)
        self._ray_node_path = base.camera.attachNewNode(ray_node)
        self._queue = CollisionHandlerQueue()
        self._traverser.addCollider(self._ray_node_path, self._queue)

    # -- lifecycle ------------------------------------------------------------

    def enable(self) -> None:
        if self._enabled:
            return
        self._events.accept("mouse1", self._on_press)
        self._events.accept("mouse1-up", self._on_release)
        self._enabled = True

    def disable(self) -> None:
        if not self._enabled:
            return
        self._events.ignoreAll()
        self._enabled = False
        self.cancel()

    # -- arming -----------------------------------------------------------------

    def begin(self, root: Any, on_point_picked: Callable[[Vec3], None]) -> None:
        """Arm picking, scoped to one model's root `NodePath`.

        The *next* plain click on it resolves a point (in `root`'s own local
        frame) and calls `on_point_picked`, then disarms itself. A click that
        misses the model leaves picking armed — a drag anywhere disarms nothing,
        it simply is not a click.
        """
        self.cancel()

        bounds = root.getTightBounds(root)
        if bounds is None:
            logger.warning("model has no extent, nothing to pick against")
            return

        low, high = bounds
        padded_low, padded_high = padded_box_bounds(
            (low[0], low[1], low[2]), (high[0], high[1], high[2])
        )

        from panda3d.core import CollisionBox, CollisionNode, Point3

        proxy_node = CollisionNode("pssim-pick-proxy")
        proxy_node.addSolid(CollisionBox(Point3(*padded_low), Point3(*padded_high)))
        self._proxy = root.attachNewNode(proxy_node)
        self._root = root
        self._on_point_picked = on_point_picked

    def cancel(self) -> None:
        """Disarm picking without resolving a point. Safe to call when not armed."""
        if self._proxy is not None:
            self._proxy.removeNode()
            self._proxy = None
        self._root = None
        self._on_point_picked = None
        self._press_pixel = None

    # -- events -------------------------------------------------------------

    def _on_press(self) -> None:
        if self._root is not None:
            self._press_pixel = self._mouse_pixel()

    def _on_release(self) -> None:
        if self._root is None or self._press_pixel is None:
            return
        press_pixel = self._press_pixel
        self._press_pixel = None

        release_pixel = self._mouse_pixel()
        if (
            release_pixel is None
            or _pixel_distance(press_pixel, release_pixel) > CLICK_TOLERANCE_PX
        ):
            return  # a drag - orbiting already handled it, picking does nothing

        point = self._resolve_point()
        if point is None:
            return  # a click that missed the model - stay armed, let them try again

        callback = self._on_point_picked
        self.cancel()
        if callback is not None:
            callback(point)

    def _resolve_point(self) -> Vec3 | None:
        watcher = self._base.mouseWatcherNode
        if watcher is None or not watcher.hasMouse() or self._root is None:
            return None

        self._ray.setFromLens(self._base.camNode, watcher.getMouseX(), watcher.getMouseY())
        self._queue.clearEntries()
        self._traverser.traverse(self._root)
        if self._queue.getNumEntries() == 0:
            return None

        self._queue.sortEntries()
        point = self._queue.getEntry(0).getSurfacePoint(self._root)
        return (point[0], point[1], point[2])

    def _mouse_pixel(self) -> tuple[float, float] | None:
        """Mirrors `OrbitController._mouse_pixel` — kept separate rather than
        shared, since the two controls are deliberately independent of each
        other.

        Reads the size straight off `base.win` via `getXSize()`/`getYSize()`
        rather than through `getProperties()` (as `OrbitController` does): the
        latter exists only on a real `GraphicsWindow`, not on the
        `GraphicsBuffer` an offscreen render target uses, and this method needs
        to work in both.
        """
        watcher = self._base.mouseWatcherNode
        if watcher is None or not watcher.hasMouse():
            return None
        window = self._base.win
        if window is None:
            return None
        width = max(window.getXSize(), 1)
        height = max(window.getYSize(), 1)
        return (watcher.getMouseX() * width / 2.0, watcher.getMouseY() * height / 2.0)
