"""The scene camera and lighting.

Separated from `app.py` because it is the only part of the visualisation that has to know
the **scale of the scene**. Machines are in metres and run from 0.1 m (a single part) to
20 m (a line), while Panda3D has a default near clip of **1.0** and the camera at the
origin. On a small machine that means an empty window: the whole model is inside the near
plane.

Computing the camera distance is a pure function, so it can be tested without a window.
"""

from __future__ import annotations

import math
from typing import Any, Final

from pssim.observability import get_logger
from pssim.viz.orbit import DEFAULT_VIEW, STANDARD_VIEWS, standard_view

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_VIEW",
    "STANDARD_VIEWS",
    "clip_planes",
    "frame_distance",
    "scene_radius",
    "setup_camera",
    "setup_lights",
    "view_direction",
]

DEFAULT_FOV_DEG: Final = 40.0

#: How many times the scene radius we leave around the model. 1.0 = the model exactly
#: fills the view; more gives clearance, so movement of the axes away from the rest
#: position stays visible.
FRAMING_MARGIN: Final = 1.6

#: The fallback radius when the scene is empty or has no bounds (missing geometry).
FALLBACK_RADIUS_M: Final = 1.0


def frame_distance(radius_m: float, fov_deg: float = DEFAULT_FOV_DEG) -> float:
    """The camera distance at which a sphere of the given radius fills the view.

    A pure function — tested without Panda3D.
    """
    if radius_m <= 0.0:
        radius_m = FALLBACK_RADIUS_M
    half_fov = math.radians(max(fov_deg, 1.0)) / 2.0
    return radius_m * FRAMING_MARGIN / math.sin(half_fov)


def clip_planes(radius_m: float) -> tuple[float, float]:
    """The near and far planes, derived from the size of the scene.

    Fixed values do not work here: the default near of 1.0 clips a whole 0.2 m part,
    while a near of 0.001 on a 20 m line destroys the precision of the depth buffer.
    """
    if radius_m <= 0.0:
        radius_m = FALLBACK_RADIUS_M
    return (radius_m * 0.01, radius_m * 100.0)


def scene_radius(root: Any) -> tuple[Any, float]:
    """The centre and radius of the scene. An empty scene gives the origin and the fallback radius."""
    from panda3d.core import LPoint3

    bounds = root.getBounds()
    if bounds.isEmpty() or bounds.getRadius() <= 0.0:
        logger.warning("the scene has no dimensions - is the geometry missing?")
        return LPoint3(0.0, 0.0, 0.0), FALLBACK_RADIUS_M
    return bounds.getCenter(), float(bounds.getRadius())


def view_direction(view: str) -> tuple[float, float, float]:
    """The unit vector from the centre of the model towards the camera for a standard view.

    Derived from `viz.orbit.STANDARD_VIEWS`, which is the **single source of truth** —
    the definition of "what the front view is" must not exist in two places, or the
    interactive camera and `pssim screenshot` will drift apart over time.
    """
    azimuth, elevation = standard_view(view)
    horizontal = math.cos(elevation)
    return (
        horizontal * math.sin(azimuth),
        -horizontal * math.cos(azimuth),
        math.sin(elevation),
    )


def setup_camera(
    base: Any,
    root: Any,
    fov_deg: float = DEFAULT_FOV_DEG,
    view: str = DEFAULT_VIEW,
) -> None:
    """Set the camera so the whole machine is in view.

    The camera is set **directly** and the trackball is synchronised to it afterwards.
    The other order (setting only the trackball) works in a window but not when rendering
    to a file: the trackball feeds its transformation into the camera through the data
    graph, which `graphicsEngine.renderFrame()` does not traverse on its own, so the
    camera stays at the origin and the image comes out empty.
    """
    from panda3d.core import LPoint3, LVector3

    center, radius = scene_radius(root)
    near, far = clip_planes(radius)

    lens = base.camLens
    lens.setFov(fov_deg)
    lens.setNearFar(near, far)

    direction = LVector3(*view_direction(view))
    direction.normalize()
    eye = LPoint3(center) + direction * frame_distance(radius, fov_deg)
    base.camera.setPos(eye)
    base.camera.lookAt(LPoint3(center))

    _sync_trackball(base, center)

    logger.info(
        "camera set",
        radius_m=round(radius, 3),
        near_m=round(near, 4),
        far_m=round(far, 1),
        eye=(round(eye[0], 3), round(eye[1], 3), round(eye[2], 3)),
    )


def _sync_trackball(base: Any, center: Any) -> None:
    """Set the trackball so the mouse continues from the camera's current view.

    Without this, the first frame in the window would overwrite the camera from the
    trackball and the view would jump back to the origin. The trackball holds the
    **inverse** of the camera transformation.
    """
    from panda3d.core import LMatrix4, LPoint3

    trackball = getattr(base, "trackball", None)
    if trackball is None:  # an offscreen render has no mouse, and so no trackball
        return

    inverse = LMatrix4()
    inverse.invertAffineFrom(base.camera.getMat())
    trackball.node().setOrigin(LPoint3(center))
    trackball.node().setMat(inverse)


def setup_lights(target: Any) -> None:
    """Basic lighting attached to the given subtree.

    Without lights Panda3D renders a flat unlit colour — the model is visible, but as a
    silhouette with no shape. Two directional lights from different sides plus an ambient
    are enough to make the edges of the parts readable.

    The lights hang on the **machine root**, not on `render`. That way they disappear
    together with the scene; hanging on `render` they would accumulate across repeated
    renders in one process and the image would gradually wash out.
    """
    from panda3d.core import AmbientLight, DirectionalLight

    ambient = AmbientLight("ambient")
    ambient.setColor((0.35, 0.35, 0.38, 1.0))
    target.setLight(target.attachNewNode(ambient))

    key = DirectionalLight("key")
    key.setColor((0.8, 0.8, 0.78, 1.0))
    key_path = target.attachNewNode(key)
    key_path.setHpr(-30.0, -50.0, 0.0)
    target.setLight(key_path)

    fill = DirectionalLight("fill")
    fill.setColor((0.3, 0.32, 0.35, 1.0))
    fill_path = target.attachNewNode(fill)
    fill_path.setHpr(140.0, -20.0, 0.0)
    target.setLight(fill_path)
